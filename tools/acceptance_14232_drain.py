#!/usr/bin/env python
"""Drain N session captures and grade #14232's acceptance criteria.

WHAT THIS MEASURES, AND WHY THOSE FOUR THINGS
---------------------------------------------
#14232's done-when is four observations on ONE run, not four separate claims:

1. **ack p95** — `POST /v1/sessions` answers inside the transport wait bound
   (`#3834`, 10 s, and it must NOT be raised). The target is p95 <= 1 s, which is
   the whole point of moving extraction off the request: a caller should not pay
   for the model.
2. **zero 504s** — the bound's refusal is the symptom this change exists to
   remove. A single 504 fails the run BY ITSELF: it is the production defect,
   reproduced.
3. **every session reaches a TERMINAL state on its own** — the ack is only
   honest if completion follows without the client doing anything. "Terminal" is
   read from the SAME projection the product serves (`capture_ok` present and the
   in-flight phase cleared), so the check cannot agree with a state no client can
   see.
4. **extraction demonstrably ran** — points, entities and edges for a drained
   session. Without this, 1-3 are all satisfiable by a capture that stores turns
   and never extracts, i.e. by the bug. Beside it the tool REPORTS, without
   grading, the share of acks that came back with extraction still in flight
   (`deferred_at_ack`): a run that defers on 0 of 50 is a run that was fast
   enough, not a failed split, so making the share a pass/fail would grade the
   host's load rather than the change.

    uv run python tools/acceptance_14232_drain.py --n 50
    uv run python tools/acceptance_14232_drain.py --n 50 --json

TWO MODES, AND THE DIFFERENCE MATTERS
-------------------------------------
* Against a DEPLOYMENT (`--base-url https://app.premiselabs.co` +
  `--api-key`/`TORTOISE_API_KEY`) this is the real acceptance run: the spooled
  corpus, the real database, the real provider, the real wait-bound middleware.
* With no `--base-url` it runs **in process** against `hosted_api.app` and a
  local embedded graph. That is a REHEARSAL, and the report says so on its first
  line: the criteria are the same code path, but the latency is this box's, not
  production's, and the extractor is the offline `MockModel` seam unless a
  provider key is configured.

The corpus: `--sessions-dir` (default `$TORTOISE_CAPTURE_SPOOL_DIR`, else
`~/.tortoise/capture-spool`) is read for spooled captures when it exists, because
the acceptance run is meant to be the 50 sessions that were parked — re-deriving
a synthetic corpus would test a shape no user produces. When the directory is
absent (the normal case off the deployment box) the run SYNTHESISES captures of
the same shape and says so; it never silently substitutes one for the other.

Exit codes — THREE-WAY, and the middle one is not a pass:

* `0` — every GRADED criterion met, and the verdict line says `PASS`;
* `0` — `INCONCLUSIVE`: every host-independent criterion met, but the absolute
  ack-p95 target was NOT graded because the box is oversubscribed
  (`load_1m > cpu_count`). The change is not what failed, and the printed banner
  and the JSON `verdict` both say INCONCLUSIVE — never PASS;
* `1` — a criterion that RAN failed: refusals, terminal state, or extraction
  evidence, **or a graded ack p95 over the target**, the report names which;
* `2` — usage/precondition (no app, no auth, bad corpus, an unusable
  `--base-url`), **or the DEPLOYMENT being unreachable/unreadable**: a transport
  failure on any of the three network calls reports `error: <label> unreachable
  — <Type>: <msg>` and exits 2. It is never a verdict about the change — one API
  blip must not print a criterion FAIL — and it is not "inconclusive" either: the
  run did not happen, so it says so.

⛔ A GRADED FAILURE IS NEVER `INCONCLUSIVE`, and this is the one shape the tool
is easy to get wrong in both directions: the p95 may not produce PASS on a box
that declined to grade it (it might otherwise have run at all), and it must not
produce `INCONCLUSIVE`/exit 0 on a box that DID grade it and saw a breach — that
would print `[FAIL] ack p95` and exit 0 in the same run, leaving the issue's
headline criterion unfalsifiable in the very artifact the issue is accepted on
(review round 5, F1). `_verdict()` is the one place that decision lives.

The deferral share (`deferred_at_ack`) is REPORTED and never graded: a fast host
completes extraction inside the window on every session, which is the split
working, not the split failing.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import time
from pathlib import Path

#: The ack budget this issue is graded on (p95, seconds).
DEFAULT_MAX_ACK_P95_S = 1.0
#: How long a drained session may take to reach a terminal state before the run
#: calls it stuck. Generous on purpose: the criterion is "on its own", not "fast".
DEFAULT_TERMINAL_TIMEOUT_S = 300.0
#: Poll interval while waiting for a session to finish extracting.
POLL_S = 0.25

_SPOOL_DIR_ENV = "TORTOISE_CAPTURE_SPOOL_DIR"
_DEFAULT_SPOOL_DIR = "~/.tortoise/capture-spool"


def _p(ok: bool, label: str, detail: str) -> str:
    return f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}"


# ── corpus ────────────────────────────────────────────────────────────────

def _synthetic_corpus(n: int) -> list[dict]:
    """Captures shaped like a real agent session: alternating turns, a claim.

    Deliberately NOT one-turn stubs: a one-turn capture cannot show that the ack
    is independent of the number of points an extraction has to mint, which is the
    quantity #14232 is about.
    """
    out = []
    for i in range(n):
        out.append({
            "session_id": f"acc14232-{i:03d}",
            # A REAL harness member: the boundary validates it against a closed
            # set, and an invented name is a 422 that would make the run grade
            # nothing at all (measured). `source` carries the acceptance mark.
            "harness": "pi",
            "source": "acceptance-14232",
            "conversation": [
                {"role": "user",
                 "content": (f"Acceptance capture {i}: the ingest worker must "
                             "acknowledge before it extracts anything.")},
                {"role": "assistant",
                 "content": ("Agreed — the receipt says accepted and the status "
                             f"surface names the phase for capture {i}.")},
                {"role": "user",
                 "content": (f"Capture {i} also asserts that extraction runs "
                             "behind the ack, on a worker.")},
            ],
        })
    return out


def _spooled_corpus(spool_dir: Path, n: int) -> list[dict]:
    """The first `n` captures the spool holds, in spool order (oldest first).

    The spool is the source of truth for "what was parked": each entry is a JSON
    object with the captured conversation. An entry this tool cannot read is
    SKIPPED and counted, never silently dropped — a corpus that shrank without
    saying so would make the run's denominator a fiction.
    """
    files = sorted(p for p in spool_dir.rglob("*.json") if p.is_file())
    out: list[dict] = []
    skipped = 0
    for path in files:
        if len(out) >= n:
            break
        try:
            entry = json.loads(path.read_text())
        except (OSError, ValueError):
            skipped += 1
            continue
        conv = entry.get("conversation") or entry.get("turns")
        sid = (entry.get("session_id") or entry.get("sessionId")
               or path.stem)
        if not conv:
            skipped += 1
            continue
        out.append({"session_id": sid, "harness": entry.get("harness") or "pi",
                    "source": "acceptance-14232", "conversation": conv})
    if skipped:
        print(f"  corpus: skipped {skipped} unreadable/empty spool entries")
    return out


# ── transports ────────────────────────────────────────────────────────────

class _HttpTransport:
    """Real HTTP against a deployment — the acceptance run proper."""

    label = "deployment"

    def __init__(self, base_url: str, api_key: str) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=120.0,
        )

    def post_capture(self, body: dict):
        return self._client.post("/v1/sessions", json=body)

    def get_session(self, session_id: str):
        return self._client.get(f"/v1/sessions/{session_id}")

    def extraction_evidence(self, session_id: str) -> dict:
        """Points/entities/edges FROM THE SERVED SURFACE only.

        A deployment probed over HTTP cannot be asked for a graph query, and
        inventing one for this tool would mean a new endpoint (a surface change).
        The detail body carries the extraction block and the point count it is
        derived from; entities and edges are reported as NOT MEASURED rather than
        assumed present.
        """
        r = self.get_session(session_id)
        if r.status_code != 200:
            return {"measured": False, "why": f"detail read {r.status_code}"}
        try:
            body = r.json()
        except ValueError as exc:  # a garbled body is the WIRE, not the extraction
            raise _UnreadableResponse(f"{type(exc).__name__}: {exc}") from exc
        # ⛔ A SUCCESSFUL DETAIL READ **IS** THE MEASUREMENT (review round 8, P1).
        # This arm used to hard-code `"measured": False` with the note below as the
        # reason — which made `ok_extract` unsatisfiable by construction, so a
        # DEPLOYMENT run could never PASS: a perfect deployment (200 ACK inside the
        # window, terminal on its own, one extracted point) still printed
        # `[FAIL] extraction demonstrably ran` and exited 1. The docstring's own
        # claim ("the detail body carries the extraction block and the point count
        # it is derived from") is exactly the evidence criterion 4 needs; what the
        # HTTP surface genuinely cannot answer is entities/edges, and those stay
        # unmeasured and stay governed by `--require-entities`.
        # ⛔ THE SERVED KEY IS `extracted` / `extracted_points`, NOT `points`
        # (review round 9, P1 — measured: `GET /v1/sessions/{id}` serves exactly
        # `SESSION_DETAIL_FIELDS`, and `points` is not one of them, so
        # `body.get("points")` was ALWAYS empty and the arm still could not PASS:
        # flagging `measured` alone moved the same failure one line along). The
        # count criterion 4 asks for is `extracted`; `extracted_points` is the
        # list it counts. Reading either from a key the surface never publishes is
        # how this tool certified a fiction in the first place.
        return {
            "measured": True,
            "why": "served detail surface (`extracted` / `extracted_points` from "
                   "the body; entities/edges are NOT measured over HTTP)",
            "points": body.get("extracted") or len(
                body.get("extracted_points") or []),
            "extraction": body.get("extraction"),
        }


class _InProcessTransport:
    """`hosted_api.app` in process, local embedded graph — a REHEARSAL."""

    label = "in-process rehearsal"

    def __init__(self, org_id: str) -> None:
        import tempfile

        from fastapi.testclient import TestClient

        import tortoise.hosted_api as ha

        self._ha = ha
        self._org_id = org_id
        # A private DB per run: an acceptance drain must not read or write the
        # state a previous run left, or "terminal on its own" could be satisfied
        # by work an earlier run did.
        self._tmp = tempfile.TemporaryDirectory(prefix="acc14232-")
        self._db_path = os.path.join(self._tmp.name, "acc.db")
        # The SDK resolves its embedded graph from `TORTOISE_DB_PATH`, so the pin
        # IS the private DB. Restored in `close()`.
        self._prev_db_path = os.environ.get("TORTOISE_DB_PATH")
        os.environ["TORTOISE_DB_PATH"] = self._db_path
        self._client = TestClient(ha.app, raise_server_exceptions=False)
        self._client.__enter__()

    def authenticate(self, org_id: str) -> None:
        """Mint a real API key and let the REAL auth dependency resolve the org.

        ⛔ Not a `dependency_overrides` stub. The capture lane's quota gate reads
        its limits straight off the authenticated org dict (`#683`), so a stub
        that carries only an id makes every capture a fail-closed 500
        ("team limits missing max_sessions") — measured, three attempts — and the
        run would then grade the quota gate's refusal as if it were the ack
        defect. Minting a key exercises the same door production uses, including
        the limits resolution.

        The two seeds a real tenant already has, and that the rehearsal must
        therefore create: the `:Team` row (`apply_limits` is a `MATCH … SET`), its
        tier limits (which also encode `#4010`'s unlimited sessions as NULL), and
        `session_recording=True` (without it every capture is a 403).
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
            params={"id": org_id, "st": "{}"})
        apply_limits(_make_sdk(namespace="registry"), org_id, "free")
        _update_onboarding_state(org_id, session_recording=True)
        state = _get_onboarding_state(org_id)
        if state.get("session_recording") is not True:  # pragma: no cover
            raise SystemExit(
                f"rehearsal consent seed did not persist: {state} — every "
                "capture would 403 and the run would grade an empty corpus")
        key = _make_sdk(namespace="registry")._apikey_create(
            org_id, "00000000-0000-4000-8000-000000000142")
        self._client.headers["Authorization"] = f"Bearer {key['api_key']}"

    def post_capture(self, body: dict):
        return self._client.post("/v1/sessions", json=body)

    def get_session(self, session_id: str):
        return self._client.get(f"/v1/sessions/{session_id}")

    def extraction_evidence(self, session_id: str) -> dict:
        """GRAPH evidence: extracted points, entity links and edges.

        This is the arm that can answer criterion 4 properly. It reads the same
        projection the read surfaces serve, and counts the three things the issue
        names — separately, because 'points exist' has been true before while
        nothing was WIRED to them.
        """
        ha = self._ha
        try:
            # ⛔ The ORG's data graph, not `registry`: extracted points live in
            # the tenant namespace, and a registry-namespace read reports 0 for a
            # capture that extracted fine (measured — it made the evidence check
            # fail on a PASSing run).
            proj = ha._make_sdk(namespace=self._org_id)._get_proj()
        except Exception as exc:  # pragma: no cover — rehearsal-only path
            return {"measured": False, "why": f"no graph handle: {exc}"}
        q = proj.g.query
        points = q(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
            "WHERE p.pointKind IS NULL OR p.pointKind <> 'event' "
            "RETURN count(p)", params={"sid": session_id}).result_set
        entities = q(
            "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) "
            "MATCH (p)-[r]->(e:Entity) "
            "RETURN count(DISTINCT e), count(r)",
            params={"sid": session_id}).result_set
        return {
            "measured": True,
            "points": int(points[0][0]) if points else 0,
            "entities": int(entities[0][0]) if entities else 0,
            "edges": int(entities[0][1]) if entities else 0,
        }

    def close(self) -> None:
        self._client.__exit__(None, None, None)
        if self._prev_db_path is None:
            os.environ.pop("TORTOISE_DB_PATH", None)
        else:
            os.environ["TORTOISE_DB_PATH"] = self._prev_db_path
        self._tmp.cleanup()


# ── the run ───────────────────────────────────────────────────────────────

def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    # Nearest-rank: with n=50 a p95 must be an OBSERVED value, never a spline
    # through two of them — the claim "p95 ack" has to be a request the run made.
    # `round()` already returns an int in Python 3, so no `int()` wrapper
    # (ruff RUF046): the cast was redundant and read as if a float could arrive.
    idx = min(len(ordered) - 1, max(0, round(pct / 100.0 * len(ordered)) - 1))
    return ordered[idx]


def _is_terminal(detail: dict) -> tuple[bool, str]:
    """Terminal = an outcome is known and no in-flight phase is published."""
    ex = detail.get("extraction") or {}
    if ex.get("phase"):
        return False, f"phase={ex['phase']}"
    if ex.get("outcome_known") is True:
        return True, f"status={ex.get('status')}"
    return False, f"status={ex.get('status')} outcome_known={ex.get('outcome_known')}"


def _verdict(*, ok_refusal: bool, ok_terminal: bool, ok_extract: bool,
             ack_graded: bool, ok_ack: bool) -> str:
    """Grade one run: `PASS` / `INCONCLUSIVE` / `FAIL` — the whole decision, once.

    Kept as a pure function because the ONE way this is easy to get wrong is the
    asymmetry between its two arms, and prose could not pin either:

    * a criterion this run DECLINED to grade (`ack_graded` False) may not produce
      `PASS` — measured on the first cut, which printed `NOT GRADED` beside
      `VERDICT: PASS` at load 21.29 on 16 cpus;
    * a criterion that WAS graded and FAILED may not produce `INCONCLUSIVE`/
      exit 0 — the symmetric hole, measured by review round 5: the `[FAIL] ack
      p95` banner and a `0` exit code in the same run.

    The host-INDEPENDENT criteria are graded on every host, so they short-circuit
    everything: `FAIL` first, then the p95 question, then `PASS`.
    """
    if not all((ok_refusal, ok_terminal, ok_extract)):
        return "FAIL"
    if not ack_graded:
        return "INCONCLUSIVE"
    return "PASS" if ok_ack else "FAIL"


def _is_transport_error(exc: BaseException) -> bool:
    """Is `exc` the NETWORK failing, rather than a criterion failing?

    ⛔ `isinstance` against httpx's own base class — not a name/substring test
    (review note 1 on `2285bebc1e`, comment 7074468018). The first cut tested
    `type(exc).__name__ == "HTTPError" or "httpx" in type(exc).__module__`: the
    name arm is false for EVERY concrete httpx error (`ConnectError`,
    `ReadTimeout`, `RemoteProtocolError`), so the guard matched only by accident
    of the module substring, and it would refuse a subclass raised from a
    differently-named module.

    ⛔ NO DUCK-TYPED FALLBACK (review round M, P3). The first version kept the old
    name/substring predicate under `except ImportError` for "an offline run with
    no httpx" — but httpx is a DIRECT dependency (`pyproject.toml`) and the
    rehearsal reaches `httpx` through `starlette.testclient` anyway, so that arm
    was unreachable in every mode, and had it ever run it would have reinstated
    the exact defect this function exists to remove (measured: an
    `httpx.HTTPError` subclass from `vendor.transport` classified False under it).
    A missing httpx is an ENVIRONMENT defect and must raise, not silently degrade
    the classifier.
    """
    import httpx
    return isinstance(exc, httpx.HTTPError)


def _is_unreadable_body(exc: BaseException) -> bool:
    """A 200 whose BODY cannot be decoded — a broken wire, not a criterion.

    ⛔ A DEDICATED TYPE, never a bare `ValueError` (measured: widening the check to
    `isinstance(exc, ValueError)` made a criterion bug that raises `ValueError`
    exit 2 — the exact laundering this tool refuses, reintroduced by the fix for
    it). Only the two places that DECODE a body raise this, and they raise it
    around the `.json()` call alone, so a `ValueError` from anywhere else in a
    transport method still propagates.
    """
    return isinstance(exc, _UnreadableResponse)


class _UnreadableResponse(Exception):
    """A 200 whose body will not decode. Raised ONLY by a decode site."""


def _close(transport) -> None:
    """Release the rehearsal's graph handle on the normal path and on every
    transport-failure path.

    The tail of `run()` used to close the transport itself, which the early
    `return 2` paths would skip.

    ⛔ NOT "every exit path" (review round M, P3): a NON-transport exception still
    propagates without a close, deliberately — the process is already failing and
    the handle dies with it. ⛔ And a close that RAISES would replace the exit code
    it was called to preserve, turning a clean "unreachable, exit 2" into a
    traceback at 1 (measured), so the close is guarded here, once, for every
    caller.
    """
    if isinstance(transport, _InProcessTransport):
        # SIM105: `suppress` is the same guard, and a close must never change the
        # verdict it was called to preserve.
        with contextlib.suppress(Exception):
            transport.close()


def _exit_code(verdict: str) -> int:
    """The shell contract: `PASS` and `INCONCLUSIVE` exit 0, `FAIL` exits 1.

    `INCONCLUSIVE` at 0 is deliberate (the change is not what failed) and only
    correct because `_verdict` never returns it for a criterion that RAN and
    failed. This is its own function so the mapping cannot drift from the
    verdict, and so a test can assert it rather than restate it.
    """
    return 0 if verdict in ("PASS", "INCONCLUSIVE") else 1


def run(args) -> int:
    corpus: list[dict]
    if args.sessions_dir:
        spool = Path(os.path.expanduser(args.sessions_dir))
        if not spool.is_dir():
            print(f"error: --sessions-dir {spool} is not a directory",
                  file=sys.stderr)
            return 2
        corpus = _spooled_corpus(spool, args.n)
        corpus_label = f"spooled ({spool})"
    else:
        corpus = _synthetic_corpus(args.n)
        corpus_label = "synthesised (no spool on this box)"
    if not corpus:
        print("error: the corpus is empty — nothing to drain", file=sys.stderr)
        return 2

    if args.base_url:
        api_key = args.api_key or os.environ.get("TORTOISE_API_KEY")
        if not api_key:
            print("error: --base-url needs --api-key or $TORTOISE_API_KEY",
                  file=sys.stderr)
            return 2
        try:
            transport = _HttpTransport(args.base_url, api_key)
        except Exception as exc:  # a URL that cannot build a client is a USAGE error
            print(f"error: --base-url {args.base_url!r} is not usable — "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    else:
        if not os.environ.get("TORTOISE_SESSION_LLM_MOCK") and not (
                os.environ.get("DEEPSEEK_API_KEY")
                or os.environ.get("OPENROUTER_API_KEY")):
            # Keyless in-process would 503 the extraction and the run would then
            # grade a capture that never extracted — the exact confusion
            # criterion 4 exists to catch. Refuse instead.
            print("error: no provider key and TORTOISE_SESSION_LLM_MOCK unset — "
                  "the rehearsal would grade a keyless capture. Set "
                  "TORTOISE_SESSION_LLM_MOCK=1 for the offline seam, or export "
                  "a provider key.", file=sys.stderr)
            return 2
        transport = _InProcessTransport(args.org)

    if isinstance(transport, _InProcessTransport):
        transport.authenticate(args.org)

    # ⛔ `--json` MUST BE PARSEABLE JSON. The preamble is human output, so under
    # `--json` it goes to stderr; every line below keeps its single destination.
    _out = sys.stderr if args.json else sys.stdout

    def _say(*a, **kw):
        print(*a, file=_out, **kw)

    def _unreachable(exc: BaseException) -> int:
        """A transport failure is a USAGE error (exit 2), never a verdict.

        ⛔ ALL THREE network calls report through here (review note 1, comment
        7074468018) — `post_capture`, the criterion-3 terminal poll, and the read
        inside `extraction_evidence` — and every one of the three widens its
        guard to a 200 whose BODY will not decode (`_is_unreadable_body`), because
        a garbled response is a broken wire, not a criterion. Only `post_capture`
        used to be wrapped, so a `ConnectError` on a detail read surfaced as a
        traceback and exited 1, which this tool's contract reads as "a criterion
        RAN and FAILED": a FAIL of the CHANGE rather than of the deployment, and a
        red wall of one API blip's making.
        """
        print(f"error: {transport.label} unreachable — "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        _close(transport)
        return 2

    def _unreadable(exc: BaseException) -> int:
        """A 200 with an undecodable body: the wire, not the change (exit 2)."""
        print(f"error: {transport.label} returned an unreadable response — "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        _close(transport)
        return 2

    def _wire_failure(exc: BaseException) -> int | None:
        """Route a caught exception: 2 for a broken wire, None to re-raise it."""
        if _is_transport_error(exc):
            return _unreachable(exc)
        if _is_unreadable_body(exc):
            return _unreadable(exc)
        return None

    _say(f"#14232 acceptance drain — {transport.label}")
    _say(f"  corpus: {corpus_label}, n={len(corpus)}")
    if not args.base_url:
        load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
        _say("  ⚠ rehearsal: latency and extractor are THIS box's, not "
             "production's" + (f" (load {load:.1f} on {os.cpu_count()} cpus)"
                               if load is not None else ""))

    acks: list[float] = []
    refusals: list[dict] = []
    outcomes: list[dict] = []

    for cap in corpus:
        started = time.monotonic()
        try:
            r = transport.post_capture(cap)
            # The ack's clock stops at the RESPONSE, before any decode: the p95
            # this tool grades must not pay for parsing our own body.
            elapsed = time.monotonic() - started
            body = None
            try:
                body = r.json()
            except ValueError:
                body = None
        except Exception as exc:  # a transport failure is a USAGE error, not a verdict
            _rc = _wire_failure(exc)
            if _rc is not None:
                return _rc
            raise
        acks.append(elapsed)
        if r.status_code == 504:
            refusals.append({"session_id": cap["session_id"],
                             "stage": (body or {}).get("stage"),
                             "spent_ms": (body or {}).get("stages_ms")})
        elif r.status_code != 200:
            refusals.append({"session_id": cap["session_id"],
                             "status": r.status_code,
                             "detail": (body or {}) if isinstance(body, dict) else str(r.text)[:200]})
        phase_at_ack = (((body or {}).get("extraction") or {}).get("phase")
                        if isinstance(body, dict) else None)
        ack_status = (body or {}).get("status") if isinstance(body, dict) else None
        outcomes.append({
            "session_id": cap["session_id"],
            "status_code": r.status_code,
            "ack_ms": round(elapsed * 1000.0, 1),
            "ack_status": ack_status,
            "phase_at_ack": phase_at_ack,
            # ⛔ AN UNREADABLE ACK BODY IS NOT A MEASUREMENT OF "0 DEFERRED"
            # (review round N, P3). The tolerant decode above is deliberate — a
            # garbled ack must not fail the run — but without this flag the
            # report said "extraction finished inside the budget on every
            # session" about a body it never read: a claim with nothing behind
            # it, in the one line that describes the split itself.
            "ack_body_readable": isinstance(body, dict),
            # "the ack came back before extraction finished" — the property this
            # tool MEASURES. It is deliberately NOT a criterion: a run where
            # extraction happens to finish inside the budget is a legitimate run,
            # so grading it would be grading the machine's load. Recorded per ack
            # so the share can be reported beside the criteria.
            "deferred_at_ack": bool(phase_at_ack) or ack_status == "accepted",
        })

    # ── criterion 3: every session reaches a terminal state, on its own ──
    stuck: list[dict] = []
    terminals: list[dict] = []
    for cap in corpus:
        if any(o["session_id"] == cap["session_id"] and o["status_code"] != 200
               for o in outcomes):
            continue
        deadline = time.monotonic() + args.terminal_timeout
        last = "not read"
        while time.monotonic() < deadline:
            try:
                r = transport.get_session(cap["session_id"])
            except Exception as exc:  # network down ≠ "every session got stuck"
                _rc = _wire_failure(exc)
                if _rc is not None:
                    return _rc
                raise
            detail = None
            if r.status_code == 200:
                try:
                    detail = r.json()
                except ValueError as exc:  # a garbled body is the WIRE
                    return _unreadable(_UnreadableResponse(
                        f"{type(exc).__name__}: {exc}"))
            if detail is not None:
                done, last = _is_terminal(detail)
                if done:
                    terminals.append({"session_id": cap["session_id"],
                                      "status": last})
                    break
            else:
                last = f"detail read {r.status_code}"
            time.sleep(POLL_S)
        else:
            stuck.append({"session_id": cap["session_id"], "last": last})

    # ── criterion 4: extraction demonstrably ran, on ONE drained session ──
    evidence = {"measured": False, "why": "no session drained"}
    if terminals:
        try:
            evidence = transport.extraction_evidence(terminals[0]["session_id"])
        except Exception as exc:  # a blip here reads as "extraction did not run"
            _rc = _wire_failure(exc)
            if _rc is not None:
                return _rc
            raise
        evidence["session_id"] = terminals[0]["session_id"]

    p95 = _percentile(acks, 95)
    p50 = statistics.median(acks) if acks else 0.0
    # ⛔ THE ABSOLUTE p95 IS A DEPLOYMENT CLAIM, AND AN OVERSUBSCRIBED HOST CANNOT
    # GRADE IT (#5049 rule 1: a deadline expiry is evidence about the machine, not
    # about the property under test). Measured here at load 21.7 on 16 cpus the
    # same tree gives p95 1 089 ms; at load 14.5 it gives 924 ms, against a 1 s
    # target — i.e. the criterion flips with the box, not with the diff. So it is
    # GRADED only where the host is not oversubscribed, and REPORTED INCONCLUSIVE
    # everywhere else — never PASS, never a FAIL that blames the change for the
    # scheduler. The host-INDEPENDENT criteria below stay hard either way.
    load1 = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    cpus = os.cpu_count() or 1
    oversubscribed = load1 is not None and load1 > cpus
    ok_ack = p95 <= args.max_ack_p95
    ack_graded = not oversubscribed
    ok_refusal = not refusals
    accepted = sum(1 for o in outcomes if o["status_code"] == 200)
    deferred = sum(1 for o in outcomes
                   if o["status_code"] == 200 and o["deferred_at_ack"])
    #: Acks whose body would not decode. The COUNT of deferrals means nothing for
    #: those, so the reporting line says so instead of reading their `False` as
    #: "extraction finished inside the budget" (review round N, P3).
    unreadable_acks = sum(1 for o in outcomes if not o["ack_body_readable"])
    ok_terminal = (not stuck) and accepted > 0 and len(terminals) == accepted
    # ⛔ DEFERRAL IS A MEASUREMENT, NOT A CRITERION (review round 5). An earlier
    # cut made `deferred > 0` a hard failure and called it "host-independent",
    # which is exactly backwards: a FAST host completes the extraction inside the
    # window on every session, so `deferred == 0` there is evidence the split
    # works — measured with the deadline forced to 30 s: 0/3 deferred, all four
    # criteria PASS, and the tool said FAIL. It is reported, and never graded.
    # Points are the non-negotiable half: without an extracted point the capture
    # stored turns and never extracted, which is the defect. Entities and edges
    # are asserted only where the extractor emits them — the offline `MockModel`
    # seam emits a bare claim with NO entity, measured, so requiring them there
    # would fail a healthy run. A deployment run passes `--require-entities`,
    # because a real extractor that produces no entity is a finding.
    ok_points = (evidence.get("points") or 0) >= 1
    ok_entities = (not args.require_entities) or (
        (evidence.get("entities") or 0) >= 1
        and (evidence.get("edges") or 0) >= 1)
    ok_extract = bool(evidence.get("measured") and ok_points and ok_entities)
    if not evidence.get("measured"):
        # An unmeasured claim is not a PASS: report it as not-met.
        ok_extract = False

    load = os.getloadavg() if hasattr(os, "getloadavg") else (None, None, None)
    report = {
        "mode": transport.label,
        "host_load_1m": round(load[0], 2) if load[0] is not None else None,
        "cpu_count": os.cpu_count(),
        "corpus": corpus_label,
        "n": len(corpus),
        "ack_ms": {"p50": round(p50 * 1000.0, 1),
                   "p95": round(p95 * 1000.0, 1),
                   "max": round((max(acks) if acks else 0.0) * 1000.0, 1)},
        "refusals": refusals,
        "accepted": accepted,
        "terminal": len(terminals),
        "deferred_at_ack": {"n": deferred, "of": accepted,
                            "unreadable": unreadable_acks,
                            "measurable": bool(accepted) and not unreadable_acks},
        "stuck": stuck,
        "extraction_evidence": evidence,
        "outcomes": outcomes,
        "criteria": {
            # `None` = the run declined to grade it (host oversubscribed); the
            # `verdict` is then INCONCLUSIVE, never PASS.
            "ack_p95_within_budget": ok_ack if ack_graded else None,
            "zero_refusals": ok_refusal,
            "all_terminal": ok_terminal,
            "extraction_demonstrably_ran": ok_extract,
        },
        "host": {"load_1m": round(load1, 2) if load1 is not None else None,
                 "cpu_count": cpus,
                 "ack_p95_graded": ack_graded,
                 "why_not_graded": (None if ack_graded else
                                    "host oversubscribed (load > cpu_count): the "
                                    "absolute p95 would grade the scheduler")},
        # ⛔ A PASS REQUIRES A GRADED p95: `ok_ack` is still *computed* when the
        # host is oversubscribed (so the number is printed either way), but it
        # must not be able to produce PASS on a criterion this run declined to
        # grade — measured on the first cut of this change, which printed
        # "NOT GRADED" beside a `VERDICT: PASS` at load 21.29 on 16 cpus.
        # The deferral share is deliberately ABSENT from both lists: it is the
        # measurement, not a criterion (see the note above).
        # ⛔ `_verdict` owns the decision (and with it the two fail-open arms — see
        # its docstring); this call site must not restate the conjunction.
        "verdict": _verdict(ok_refusal=ok_refusal, ok_terminal=ok_terminal,
                            ok_extract=ok_extract, ack_graded=ack_graded,
                            ok_ack=ok_ack),
    }

    if args.json:
        print(json.dumps(report, indent=2))
        # ⛔ THE MACHINE REPORT MUST CARRY THE HONESTY TOO (review round O, P2):
        # `--json` is the documented acceptance mode, and the unreadable branch used
        # to live only in the human block below — so the JSON said
        # `deferred_at_ack: {n: 0, of: 1}` about an ack body it never read. The key
        # is in the report; this line keeps it visible to a reader of the log as
        # well, on the same stream the preamble uses.
        if unreadable_acks:
            _say(f"  · deferred at ack: NOT MEASURABLE — {unreadable_acks} ack "
                 f"body/bodies would not decode, so this run cannot say whether "
                 f"the split deferred")
    else:
        print()
        if ack_graded:
            print(_p(ok_ack, "ack p95",
                     f"{report['ack_ms']['p95']} ms <= {args.max_ack_p95 * 1000:.0f} ms "
                     f"(p50 {report['ack_ms']['p50']} ms, max {report['ack_ms']['max']} ms)"))
        else:
            # `·`, never `[PASS]`/`[FAIL]`: this criterion produced NO verdict.
            print(f"  · ack p95: {report['ack_ms']['p95']} ms measured against "
                  f"{args.max_ack_p95 * 1000:.0f} ms — NOT GRADED on this host "
                  f"(load {report['host']['load_1m']} on {cpus} cpus; the "
                  f"deployment run grades it)")
        print(_p(ok_refusal, "zero refusals",
                 "none" if ok_refusal
                 else f"{len(refusals)} refusal(s): {refusals[:3]}"))
        print(_p(ok_terminal, "terminal on its own",
                 f"{len(terminals)}/{accepted} accepted"
                 + ("" if ok_terminal else f" — stuck: {stuck[:3]}")))

        print(_p(ok_extract, "extraction demonstrably ran",
                 json.dumps(evidence, default=str)))
        # A MEASUREMENT, not a criterion — printed with `·` so it can never be
        # read as a PASS/FAIL. It answers "did the split actually defer?" on
        # this run; grading it would red a run that was merely fast enough.
        if unreadable_acks:
            print(f"  · deferred at ack: NOT MEASURABLE — {unreadable_acks} ack "
                  f"body/bodies would not decode, so this run cannot say whether "
                  f"the split deferred")
        elif not accepted:
            # ⛔ ZERO ACCEPTED ACKS IS NOT "every session finished inside the
            # budget" (review round O, P3): on a refusal-only run — the 504 run
            # this tool exists to detect — that sentence described a population of
            # zero, and the same class of claim the unreadable arm above refuses.
            print("  · deferred at ack: NOT MEASURABLE — no accepted ack "
                  "(0/0), so there is no deferral share to report")
        else:
            print(f"  · deferred at ack: {deferred}/{accepted} "
                  f"({'the ack returned before extraction finished' if deferred
                     else 'extraction finished inside the budget on every session'}; "
                  f"measured, not graded)")
        print()
        print(f"VERDICT: {report['verdict']}")

    _close(transport)
    # ⛔ THREE-WAY, and the middle one is not a pass: an INCONCLUSIVE run graded
    # every host-INDEPENDENT criterion and could not grade the absolute one, so it
    # exits 0 (the change is not what failed) while the printed and JSON verdict
    # say INCONCLUSIVE — never PASS. A criterion that RAN and failed — the
    # host-independent three OR a graded p95 breach — is a FAIL (1); `_verdict`
    # decides that, including the breach, which used to exit 0 beside its own
    # `[FAIL]` banner.
    return _exit_code(report["verdict"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="#14232 acceptance: drain N captures, grade ack p95, "
                    "zero 504s, terminal state, and extraction evidence.")
    ap.add_argument("--n", type=int, default=50,
                    help="how many sessions to drain (default 50 — the parked corpus)")
    ap.add_argument("--base-url", default=None,
                    help="deployment URL; omit for the in-process rehearsal")
    ap.add_argument("--api-key", default=None,
                    help="API key for --base-url (else $TORTOISE_API_KEY)")
    ap.add_argument("--sessions-dir", default=None,
                    help="spool directory to draw the corpus from (default: "
                         f"${_SPOOL_DIR_ENV} or {_DEFAULT_SPOOL_DIR} when it "
                         "exists; otherwise a synthesised corpus, and the report "
                         "says which)")
    ap.add_argument("--org", default=os.environ.get("TORTOISE_ACCEPTANCE_ORG",
                                                   "acc14232"),
                    help="org id for the in-process rehearsal")
    ap.add_argument("--max-ack-p95", type=float, default=DEFAULT_MAX_ACK_P95_S,
                    help=f"p95 ack budget, seconds (default {DEFAULT_MAX_ACK_P95_S})")
    ap.add_argument("--terminal-timeout", type=float,
                    default=DEFAULT_TERMINAL_TIMEOUT_S,
                    help="how long a session may take to finish extracting "
                         f"(default {DEFAULT_TERMINAL_TIMEOUT_S:.0f}s)")
    ap.add_argument("--require-entities", action="store_true",
                    help="also require extracted entities AND edges (pass this "
                         "against a deployment with a real extractor; the "
                         "offline MockModel seam emits none)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)

    if args.n <= 0:
        print("error: --n must be positive", file=sys.stderr)
        return 2
    if args.sessions_dir is None:
        # A MISSING DEFAULT is not an error — most boxes have no spool, and the
        # synthesised corpus is the documented fallback. Only an EXPLICIT
        # `--sessions-dir` that does not exist is a usage error, because that is a
        # caller who asked for a specific corpus and would otherwise be silently
        # graded on a different one.
        for candidate in (os.environ.get(_SPOOL_DIR_ENV), _DEFAULT_SPOOL_DIR):
            if candidate and Path(os.path.expanduser(candidate)).is_dir():
                args.sessions_dir = candidate
                break
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
