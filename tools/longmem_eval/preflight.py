"""M2 pre-flight API gate (#1523, epic #1509): billing probe + 4xx fail-fast.

One realistic completion per model BEFORE the 500-Q loop starts — judge key
presence (config-only, fastest fail), extractor billing probe (S1-sized, so a
mid-run 402 is provably a BALANCE problem rather than a per-request cap),
reader ping, judge ping. Any fatal-class result aborts the run with an
aggregated, actionable message (E2E-2: a run cannot silently degrade).

Error-class taxonomy: CONSUMED from P2 (#1530) — ``tortoise/model_adapters.py``
is the single source (the M2 plan's provisional-copy hedge is obsolete since
P2 landed). Contract pinned here per the plan's Step 1:
    classify_llm_error(exc) -> LlmErrorClass; fatal = 401/402/403 (urllib
    HTTPError status / requests response.status_code) + config-4xx (400/404/
    unknown 4xx → FATAL_CONFIG); transient = 429/5xx/Timeout/URLError/
    connection. ``is_fatal`` = FATAL + FATAL_CONFIG (abort, never retry);
    ``is_transient`` = TRANSIENT + UNKNOWN (retry / failover-eligible).
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any

# M2 imports the taxonomy module directly — no provisional copy (D1 hedge
# obsolete; P2 #1530 landed the production module).
from tortoise.ingest import _PROVIDERS
from tortoise.model_adapters import (
    LlmErrorClass,
    classify_llm_error,
)

# ── S1-shaped billing probe (D3) ──────────────────────────────────────────
# The extractor ping IS the billing probe: one completion shaped like a real
# S1 story-digest call (mirrors S1_TMPL's digest instruction at ~1/10 scale
# — see tortoise/extractor_v2.py) through the extractor model's provider.
# If the probe succeeds, a mid-run 402 is a BALANCE problem (fatal, abort);
# if the provider rejects S1-shaped requests (per-request cap, content
# filter, wrong model id), the probe fails with a config error distinct from
# 402 — the operator sees which before spending a run. Token budgets are
# asserted by test_billing_probe_is_s1_shaped (system <= 2000 / user <= 500).
# The PROBE_SYSTEM constant is PRODUCT-owned now (#1987 Task 1 — moved to
# tortoise/reader.py); this module re-exports it so the preflight gate and
# LLMReader.ping stay on the exact shipped text.
from tortoise.reader import PROBE_SYSTEM

from .reader import _parse_model_spec

PROBE_USER = (
    "user: I think we should go with the apartment near the park — it's "
    "closer to the office and the rent fits the budget.\n"
    "assistant: That sounds reasonable. Let's also check the weekday "
    "commute time.\n"
    "user: Yes, and if the commute is under 30 minutes, I'm ready to sign."
)


class PreflightError(Exception):
    """The pre-flight gate failed — one or more fatal-class checks.

    ``checks`` carries the full per-check dict list (aggregated, so the
    operator fixes keys once, not iteratively); the message lists every
    failing check with its detail.
    """

    def __init__(self, checks: list[dict[str, Any]]):
        self.checks = checks
        self.failures = [c for c in checks if c.get("status") == "fatal"]
        lines = [f"  - {c.get('what', '?')}: {c.get('detail') or c.get('status')}"
                 for c in self.failures]
        super().__init__("pre-flight failed:\n" + "\n".join(lines))


class FatalProviderError(PreflightError):
    """A mid-run LLM call hit a fatal-class error (401/402/403 or config-4xx).

    Raised by the run-loop guard in ``run_evaluation`` — the run ABORTS
    instead of recording a per-question failure and silently degrading for
    the rest of the 500-Q run (E2E-2). ``run_main`` catches it for a clean
    non-zero exit.
    """

    def __init__(self, *, where: str, exc: BaseException,
                 qid: str | None = None):
        self.where = where
        self.exc = exc
        self.qid = qid
        msg = f"fatal provider error in {where}"
        if qid:
            msg += f" (question {qid})"
        msg += f": {type(exc).__name__}: {exc}"
        super().__init__([{"what": where, "status": "fatal", "detail": msg}])


def _adapter_meta(model: Any, attr: str) -> Any:
    """Resolve an identity attribute through a routing wrapper (the extractor
    model may be a RoutingModel whose adapters carry id/provider/key_env) and
    the reader/judge wrappers (which expose ``model_id`` rather than ``id``)."""
    v = getattr(model, attr, None)
    if v is None:
        v = getattr(getattr(model, "primary", None), attr, None)
    if v is None and attr == "id":
        v = getattr(model, "model_id", None)
    return v


def ping_model(model: Any, *, what: str) -> dict[str, Any]:
    """One realistic completion via the model's public interface.

    Dispatch (D2): reader/judge wrappers expose ``ping(probe)``; the
    extractor adapter's ``complete(system=, user=)`` IS the ping interface.
    Returns ``{"what", "model_id", "provider", "key_env", "status",
    "latency_ms", "detail"}`` with ``status`` in ok | fatal | transient.
    Fatal-class results (401/402/403 + config-4xx, per the P2 taxonomy) are
    included in the gate's aggregate failure list; transient results are
    recorded but do NOT fail the gate (the run's per-question backoff
    handles transients later).

    Exported for P2's capture-path warm-up wiring (D5): on provider
    selection before capture extraction, P2 calls
    ``ping_model(provider_adapter, what="capture-extraction")`` — a
    fatal-class result surfaces through P1's fail-closed contract, never
    retried as transient, never ignored.
    """
    t0 = time.monotonic()
    try:
        if hasattr(model, "ping"):
            model.ping(PROBE_USER)
        else:
            model.complete(system=PROBE_SYSTEM, user=PROBE_USER)
    except Exception as e:  # noqa: BLE001, RUF100 — classify, don't guess
        cls = classify_llm_error(e)
        status = ("fatal" if cls in (LlmErrorClass.FATAL,
                                     LlmErrorClass.FATAL_CONFIG)
                  else "transient")
        return {
            "what": what,
            "model_id": _adapter_meta(model, "id"),
            "provider": _adapter_meta(model, "provider"),
            "key_env": _adapter_meta(model, "key_env"),
            "status": status,
            "latency_ms": round((time.monotonic() - t0) * 1000.0, 2),
            "detail": f"{type(e).__name__}: {e}",
        }
    return {
        "what": what,
        "model_id": _adapter_meta(model, "id"),
        "provider": _adapter_meta(model, "provider"),
        "key_env": _adapter_meta(model, "key_env"),
        "status": "ok",
        "latency_ms": round((time.monotonic() - t0) * 1000.0, 2),
        "detail": "",
    }


def check_judge_key(judge: Any) -> dict[str, Any]:
    """Explicit judge-key presence check (config-only, no network).

    Resolves the judge's provider from its model spec (default
    ``openai:gpt-4o-2024-08-06`` → ``OPENAI_API_KEY``) and verifies the env
    var is set. The detail message names the expected key env var verbatim
    (E2E-2's "judge key absent → pre-flight aborts with a clear message").
    """
    spec = (getattr(judge, "model_spec", None)
            or f"openai:{getattr(judge, 'model_id', 'gpt-4o-2024-08-06')}")
    provider, model_id = _parse_model_spec(spec)
    provider = provider or "openai"  # the judge's canonical default provider
    key_env = _PROVIDERS[provider][1] if provider in _PROVIDERS else "OPENAI_API_KEY"
    if not os.environ.get(key_env):
        return {
            "what": "judge-key",
            "model_id": model_id,
            "provider": provider,
            "key_env": key_env,
            "status": "fatal",
            "latency_ms": 0.0,
            "detail": f"judge key missing: {key_env} (judge model {spec})",
        }
    return {
        "what": "judge-key",
        "model_id": model_id,
        "provider": provider,
        "key_env": key_env,
        "status": "ok",
        "latency_ms": 0.0,
        "detail": "",
    }


def run_preflight(*, reader: Any, judge: Any, extractor_model: Any = None,
                  mock: bool = False) -> dict[str, Any]:
    """Run the four pre-flight checks and aggregate (D2 order).

    1. judge key presence (config-only, fastest fail)
    2. extractor billing probe (S1-sized, D3 — skipped when the run has no
       extractor model, i.e. deterministic ingest)
    3. reader ping
    4. judge ping

    All checks run and results aggregate BEFORE raising — the operator fixes
    keys once, not iteratively. Any fatal-class result → ``PreflightError``
    listing every failure. ``mock=True`` → no-op (the ``--mock`` run has no
    keys and no network).
    """
    if mock:
        return {"status": "skipped", "mock": True,
                "checks": [], "detail": "mock mode — no API gate"}
    checks: list[dict[str, Any]] = [check_judge_key(judge)]
    if extractor_model is None:
        checks.append({
            "what": "extractor-billing-probe", "model_id": None,
            "provider": None, "key_env": None, "status": "skipped",
            "latency_ms": 0.0,
            "detail": "no extractor model (deterministic ingest)",
        })
    else:
        checks.append(ping_model(extractor_model, what="extractor-billing-probe"))
    checks.append(ping_model(reader, what="reader"))
    checks.append(ping_model(judge, what="judge"))
    fatal = [c for c in checks if c.get("status") == "fatal"]
    if fatal:
        raise PreflightError(checks)
    return {"status": "ok", "mock": False, "checks": checks,
            "detail": f"{len(checks)} checks passed"}


def format_preflight(preflight: dict[str, Any] | None) -> str:
    """One-line gate status for the run summary (visible in stdout)."""
    if not preflight:
        return "pre-flight gate:           n/a"
    status = preflight.get("status", "n/a")
    detail = preflight.get("detail") or ""
    line = f"pre-flight gate:           {status}"
    if detail:
        line += f" — {detail}"
    return line


# Keep the module importable with zero side effects when used standalone.
if __name__ == "__main__":  # pragma: no cover
    print(format_preflight(run_preflight(
        reader=None, judge=None, extractor_model=None, mock=True)),
        file=sys.stdout)
