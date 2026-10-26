"""Hard spend stop (#8486 F1) — a ceiling enforced against ACTUAL spend.

WHY THIS MODULE EXISTS. ``HARD_STOP_USD = 10.00`` was pre-registered in
``docs/benchmarks/longmemeval-500q/run-report.md`` and enforced nowhere:
``grep -c HARD_STOP_USD`` found it in two markdown files and zero code, so the
report read as though a stop existed over a harness that would happily spend
$100. Prose is not an enforcer.

WHAT IT ENFORCES, AND AGAINST WHAT. The ceiling is checked BEFORE each LLM
call and is evaluated against the run's spend RE-DERIVED FROM RAW TOKEN
COUNTS through the versioned ``PRICING_MAP`` (``costing.price_usage_envelope``)
— never against the harness's own ``usage.cost.usd`` meter. That distinction
is measured, not stylistic: on the 2026-10-25 pilot the meter reported
**$0.008456** for a run that spent **$0.0620**, because the served id
``deepseek-flash`` had no map entry and an out-of-map lane contributes
``usd: 0.0`` to the total. A ceiling enforced against that meter would have
let a $100 run through while reading green.

THE RESIDUAL (the part that cannot be priced). An unpriced lane is not
cheap, it is UNKNOWN. So under an armed ceiling an unpriced lane REFUSES:
(1) eagerly at ``arm()``, for every lane the run declares it will bill
    (``UnpricedModelRefused`` — a model without a ``PRICING_MAP`` entry must
    refuse to run under a ceiling rather than silently meter zero);
(2) at every ``check()``, for any unpriced or usage-less lane that appears
    mid-run (a failover to a model nobody priced, a provider that stops
    returning a usage block).
The only way to spend on an unpriced model is to disarm the ceiling
deliberately (``--hard-stop-usd 0``), which the report's ``hard_stop`` block
records as ``armed: false``. Nothing here is silent.

ABORT + RESUME. ``check()`` raises ``HardStopExceeded``; ``run.py``'s dispatch
handler records a ``spend_hard_stop`` marker WITH the completed outcomes on
the checkpoint, so the artifact is resumable (raise the ceiling and re-run the
same command) — unlike ``degraded_aborted``, a spend stop says nothing bad
about the questions that already completed.

WIRING. ``UsageCollector.record`` calls ``observe()`` once per completed LLM
call (the same sink the report's cost meter reads); ``run.py`` seeds the
accumulator from a resumed checkpoint's persisted envelopes
(``_fold_checkpoint_usage``), so a resumed run cannot reset its own ceiling
by restarting the process.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/spend_guard.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import threading

from .costing import PRICING_MAP_VERSION, lookup_rate, price_usage_envelope

#: The pre-registered ceiling (``docs/benchmarks/longmemeval-500q/run-report.md``
#: — "HARD_STOP_USD | 10.00 (pre-registered)"). This module makes that number
#: executable; it does not re-decide it. Override per run with
#: ``--hard-stop-usd`` (an explicit operator choice, recorded in the report).
HARD_STOP_USD = 10.00

#: ``--hard-stop-usd 0`` (or any value <= 0) disarms the ceiling. Recorded as
#: ``armed: false`` in the report's ``hard_stop`` block — never silent.
DISARMED_USD = 0.0

#: Empty-bucket template (the collector's own shape, so the pricing reader
#: sees exactly the keys a live row carries).
_BUCKET_KEYS = ("prompt_tokens", "completion_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens",
                "total_tokens", "calls")


class SpendGuardError(RuntimeError):
    """Base class for the ceiling's two refusals (both abort the run)."""


class UnpricedModelRefused(SpendGuardError):
    """Armed ceiling + a declared lane with no ``PRICING_MAP`` entry.

    Raised at ``arm()`` — BEFORE the first call. Refusing here is the whole
    point: an unpriced lane would spend real money that the ceiling cannot
    see, and the meter would report it as $0.00.
    """

    def __init__(self, lanes: list[dict[str, str]]):
        self.lanes = lanes
        described = ", ".join(
            f"{lane['stage']}:{lane['provider'] or '?'}/{lane['model']}"
            for lane in lanes)
        super().__init__(
            "hard-stop: refusing to run under a "
            f"${HARD_STOP_USD:,.2f} ceiling — {len(lanes)} lane(s) have no "
            f"PRICING_MAP entry ({described}); an unpriced lane meters $0.00 "
            "and would spend invisibly. Add the price entry "
            "(tools/longmem_eval/costing.py) or disarm explicitly with "
            "--hard-stop-usd 0.")


class HardStopExceeded(SpendGuardError):
    """The ceiling was reached (or spend became unpriced) mid-run."""

    def __init__(self, *, ceiling_usd: float, spend_usd: float, where: str,
                 reason: str, unpriced: list[dict] | None = None):
        self.ceiling_usd = ceiling_usd
        self.spend_usd = spend_usd
        self.where = where
        self.reason = reason
        self.unpriced = unpriced or []
        if reason == "unpriced_spend":
            detail = (f"{len(self.unpriced)} lane(s) became unpriced "
                      "(no PRICING_MAP entry or no usage block) — spend is "
                      "UNKNOWN, not zero")
        else:
            detail = (f"actual spend ${spend_usd:,.6f} reached the ceiling "
                      f"${ceiling_usd:,.6f}")
        super().__init__(f"hard-stop at {where}: {detail}")

    def artifact(self) -> dict:
        """The checkpoint marker payload (what was enforced, and where)."""
        return {"reason": self.reason,
                "ceiling_usd": self.ceiling_usd,
                "spend_usd": self.spend_usd,
                "where": self.where,
                "unpriced": self.unpriced,
                "map_version": PRICING_MAP_VERSION}


# ── run-level state (process singleton, like the usage collector) ───────────

_lock = threading.Lock()
#: The armed ceiling, or None (disarmed / never armed).
_ceiling: float | None = None
#: (stage, provider, model) -> token bucket — MONOTONIC over the run, so a
#: drained question's spend is never forgotten by a later check.
_lanes: dict[tuple[str, str, str], dict] = {}
_calls = 0
#: Was a ceiling DECISION taken in this process? (``arm`` called at all —
#: including the explicit disarm.) Lets the report distinguish "the operator
#: disarmed the stop" (record ``armed: false``) from "this caller never armed
#: one" (a direct ``outcomes_to_report`` caller — no block, byte-identical).
_decided = False


def reset() -> None:
    """Arm-time reset (an in-process second run never inherits a total)."""
    global _ceiling, _calls, _decided
    with _lock:
        _ceiling = None
        _calls = 0
        _decided = False
        _lanes.clear()


def was_decided() -> bool:
    """Did this process ``arm()`` at all (armed OR explicitly disarmed)?"""
    return _decided


def ceiling_usd() -> float | None:
    """The armed ceiling in USD, or None when disarmed."""
    return _ceiling


def _declared_unpriced(lanes) -> list[dict[str, str]]:
    missing = []
    for stage, provider, model_id in lanes:
        if lookup_rate(provider, model_id) is None:
            missing.append({"stage": stage, "provider": provider,
                            "model": model_id})
    return missing


def arm(ceiling: float | None = None, *,
        declared_lanes=()) -> float | None:
    """Arm (or disarm) the ceiling for this process.

    ``ceiling`` None → ``HARD_STOP_USD`` (the pre-registered value); <= 0 →
    DISARMED. ``declared_lanes`` is the ``(stage, provider, model_id)`` set
    the run is about to bill (the members ``UsageCollector.attach`` wired):
    an unpriced one raises ``UnpricedModelRefused`` BEFORE the first call.

    Returns the armed ceiling (None when disarmed).
    """
    global _ceiling, _decided
    reset()
    value = HARD_STOP_USD if ceiling is None else float(ceiling)
    with _lock:
        _decided = True
    if value <= 0:
        return None
    missing = _declared_unpriced(declared_lanes)
    if missing:
        raise UnpricedModelRefused(missing)
    with _lock:
        _ceiling = value
    return value


def _bucket_for(key: tuple[str, str, str]) -> dict:
    bucket = _lanes.get(key)
    if bucket is None:
        bucket = {"usage_present": True}
        bucket.update({k: 0 for k in _BUCKET_KEYS})
        _lanes[key] = bucket
    return bucket


def observe(*, stage: str, provider: str | None, model_id: str,
            tokens: dict | None, usage_present: bool) -> None:
    """Record one completed call's tokens (called from ``UsageCollector``)."""
    global _calls
    with _lock:
        _calls += 1
        bucket = _bucket_for((stage, provider or "unknown", model_id))
        bucket["calls"] += 1
        bucket["usage_present"] = bucket["usage_present"] and usage_present
        for k, v in (tokens or {}).items():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            bucket[k] = bucket.get(k, 0) + v


def seed(envelope: dict | None) -> None:
    """Fold a persisted usage envelope into the run total (resume read-back).

    A resumed run's completed questions were paid for by the ABANDONED
    process; without this the ceiling restarts at $0.00 on every resume and a
    long run could spend the ceiling once per restart. Same lane/merge
    semantics as ``observe`` — additive, and ``usage_present`` ANDs."""
    by_stage = ((envelope or {}).get("by_stage")
                if isinstance(envelope, dict) else None)
    for stage, providers in (by_stage or {}).items():
        if not isinstance(providers, dict):
            continue
        for provider, models in providers.items():
            if not isinstance(models, dict):
                continue
            for model, bucket in models.items():
                if not isinstance(bucket, dict):
                    continue
                observe(stage=stage, provider=provider, model_id=model,
                        tokens={k: bucket[k] for k in _BUCKET_KEYS
                                if k in bucket},
                        usage_present=bucket.get("usage_present", True))


def seed_payload(payload: dict | None) -> None:
    """Seed from a checkpoint's ``usage_overhead`` payload.

    Shape: ``{qid_or_marker: {stage: {provider: {model: bucket}}}}`` (the
    collector's ``overhead_payload`` form) — folded into one envelope
    ADDITIVELY, the same semantics as
    ``UsageCollector.merge_overhead_payload``.

    Summing is not a formatting detail. Every qid that failed or aborted
    contributes its own rows to the SAME ``(stage, provider, model)`` lane, so
    collapsing by replacement keeps only the LAST qid's bucket and hands the
    resumed ceiling a total SHORTER than the money already spent. The run then
    overspends the ceiling by exactly that shortfall — the ceiling reset the
    resume accounting exists to prevent.
    """
    merged: dict = {}
    for lanes in (payload or {}).values():
        if not isinstance(lanes, dict):
            continue
        for stage, providers in lanes.items():
            if not isinstance(providers, dict):
                continue
            for provider, models in providers.items():
                if not isinstance(models, dict):
                    continue
                for model, bucket in models.items():
                    if not isinstance(bucket, dict):
                        continue
                    tgt = merged.setdefault(stage, {}).setdefault(
                        provider, {}).setdefault(
                            model, {k: 0 for k in _BUCKET_KEYS})
                    for k in _BUCKET_KEYS:
                        v = bucket.get(k)
                        if isinstance(v, bool) or not isinstance(v, (int, float)):
                            continue
                        tgt[k] = tgt.get(k, 0) + v
                    tgt["usage_present"] = (
                        tgt.get("usage_present", True)
                        and bucket.get("usage_present", True))
    seed({"by_stage": merged})


def _envelope() -> dict:
    by_stage: dict = {}
    with _lock:
        snapshot = {k: dict(v) for k, v in _lanes.items()}
    for (stage, provider, model), bucket in snapshot.items():
        by_stage.setdefault(stage, {}).setdefault(provider, {})[model] = bucket
    return {"by_stage": by_stage}


def spend() -> tuple[float, bool, dict]:
    """``(usd, all_priced, breakdown)`` re-derived from raw tokens."""
    return price_usage_envelope(_envelope())


def check(where: str) -> None:
    """Called BEFORE each LLM call. No-op when disarmed or still under.

    Raises ``HardStopExceeded`` when the re-derived spend has reached the
    ceiling, or when any observed lane is unpriced / carries no usage block
    (unknown spend is never treated as zero)."""
    ceiling = _ceiling
    if ceiling is None:
        return
    usd, priced, breakdown = spend()
    if not priced:
        raise HardStopExceeded(
            ceiling_usd=ceiling, spend_usd=usd, where=where,
            reason="unpriced_spend", unpriced=breakdown.get("unpriced") or [])
    if usd >= ceiling:
        raise HardStopExceeded(
            ceiling_usd=ceiling, spend_usd=usd, where=where,
            reason="ceiling_reached")


def status() -> dict:
    """The report/checkpoint record of what the ceiling actually did."""
    usd, priced, breakdown = spend()
    return {
        "ceiling_usd": _ceiling,
        "armed": _ceiling is not None,
        "spend_usd": usd,
        "priced": priced,
        "calls": _calls,
        "unpriced_lanes": [
            {k: lane.get(k) for k in ("stage", "provider", "model", "reason")}
            for lane in (breakdown.get("unpriced") or [])],
        "map_version": PRICING_MAP_VERSION,
    }
