"""LongMemEval usage collector (#2185) — question-keyed LLM token metering.

The harness-side twin of the adapter ``usage_sink`` seam (Task 1). Every
real LLM call in a LongMemEval run fires a sink row at the adapter's
response-parse site; this module accumulates those rows into per-question
envelopes that (a) ride each outcome as the conditional ``llm_usage``
field, and (b) persist failure/preflight/keyless spend as a durable
``usage_overhead`` checkpoint payload (never dropped across resume).

Lifecycle (#2185 A2): a MODULE-LEVEL collector singleton. ``run_main``
inits + registers reader/judge/extractor sinks BEFORE ``run_preflight``;
``run_evaluation`` and ``_load_checkpoint`` consume the same singleton via
import — no parameter threading, no second collector. ``reset()`` guards
in-process double runs (Am 19).

Attribution: a question-key ContextVar set as the FIRST statement of the
per-question task body. Sink fires run in the calling thread (live ingest
path is sequential) or in the extractor's daemon thread — which sees the
caller's context because ``extractor_v2._call_once`` now runs the model
call under ``contextvars.copy_context()`` (Task 1). Keyless rows (preflight
pings, any stray keyless call) bucket under the ``__no_key__`` sentinel and
are drained as overhead — never as a question's evidence-bearing usage.

Bucket keying (#2185 A1): rows key by the REGISTERED ``(stage, provider)``
from ``attach()``; the sink payload's provider is used only when the
registered provider is None (the model_adapters lanes carry their own
class-attribute provider; OpenAICompatModel/OfficialJudgeModel carry none
and are keyed by the registration provider — reader/judge lanes).

Envelope JSON shape (carried on outcomes and in the report):

    {
      "by_stage": {
        "reader": {"openrouter": {"gpt-4o-2024-08-06": {
            "prompt_tokens": 10, "completion_tokens": 4, "calls": 2,
            "usage_present": true,           # false when ANY row had no usage
            "calls_without_usage": 1,         # rows w/o a usage block (when >0)
            "prompt_cache_hit_tokens": 5,    # present only when recorded
            "prompt_tokens_details_cached_tokens": 8}},  # flattened nested
      }},
      "total": {"prompt_tokens": 10, "completion_tokens": 4, "calls": 2},
    }

Only known scalar usage keys survive (sanitizer): prompt_tokens,
completion_tokens, total_tokens, reasoning_tokens, prompt_cache_hit_tokens,
prompt_cache_miss_tokens, and ``prompt_tokens_details.cached_tokens``
(flattened to ``prompt_tokens_details_cached_tokens``). Every accepted
scalar is finite + magnitude-bounded. A usage dict made
up ONLY of unknown keys logs a loud warning instead of vanishing silently.
Merges/folds (overhead store, checkpoints) preserve the detail keys (union
scalar sum — never a fixed-key list). "usage_present" stays conservatively
false when ANY row in the lane lacked a usage block (unknown spend is never
silently priced); "calls_without_usage" discloses how many rows were
unknown.

Drain semantics: ``drain_question`` SWAPS the qid bucket under the lock —
every row is drained exactly once. A late fire (a deadline-killed daemon
call finishing after the outcome was built) lands in the next drain for
that qid (bounded, never double-counted); a completed qid re-drains to
None. ``drain_overhead`` merges the keyless sentinel + moved failed qids
into ONE overhead envelope; the per-qid ``overhead_payload()`` shape keeps
resume fidelity (Task 4 checkpoints it additively).
"""
from __future__ import annotations

import contextvars
import logging
import math
import threading

logger = logging.getLogger(__name__)

# Sentinel qid for keyless rows (preflight pings / stray keyless calls).
NO_KEY = "__no_key__"
# Sentinel for the per-qid overhead checkpoint payload header.
PREFLIGHT_MARKER = "__preflight__"

# Usage keys the sanitizer keeps (scalar, JSON-safe). Nested
# prompt_tokens_details.cached_tokens is flattened to
# prompt_tokens_details_cached_tokens (the OpenAI-compat cache field).
_KNOWN_USAGE_KEYS = frozenset({
    "prompt_tokens", "completion_tokens", "total_tokens",
    "reasoning_tokens", "prompt_cache_hit_tokens",
    "prompt_cache_miss_tokens",
})
_NESTED_FLATTEN = {
    "prompt_tokens_details": ("cached_tokens",),
}

# The question key — set as the FIRST statement of the per-question task
# body; clear() after the question's drains complete.
_QUESTION_KEY: contextvars.ContextVar[str | None] = (
    contextvars.ContextVar("lme_usage_question_key", default=None))

# ── module-level collector singleton (A2) ───────────────────────────────────

_collector: UsageCollector | None = None
_collector_lock = threading.Lock()


def get_collector() -> UsageCollector:
    """The run-level collector singleton (lazy init on first use)."""
    global _collector
    if _collector is None:
        with _collector_lock:
            if _collector is None:
                _collector = UsageCollector()
    return _collector


def reset_collector() -> None:
    """Reset the singleton (in-process double-run guard, Am 19)."""
    global _collector
    with _collector_lock:
        _collector = None


def set_question_key(qid: str) -> None:
    """Bind subsequent sink rows to ``qid`` (first statement of the task body)."""
    _QUESTION_KEY.set(qid)


def clear_question_key() -> None:
    _QUESTION_KEY.set(None)


def current_question_key() -> str | None:
    return _QUESTION_KEY.get()


# ── sanitizer ───────────────────────────────────────────────────────────────

def _sanitize_usage(usage: dict | None) -> dict:
    """Keep only the known scalar usage keys; flatten the nested cache
    detail; warn loudly when a non-empty usage dict has NO known keys.

    Round-2 code-review hardening: non-dict usage (a malformed provider
    response) degrades to {} — never raises (a metering observer must not
    flip call outcomes); every accepted scalar is finite + magnitude-
    bounded via ``_bounded`` (poison never reaches the buckets)."""
    if not isinstance(usage, dict):
        return {}
    usage = usage or {}
    out: dict = {}
    for k, v in usage.items():
        if isinstance(v, dict):
            flat = _NESTED_FLATTEN.get(k)
            if flat:
                for fk in flat:
                    fv = v.get(fk)
                    if (isinstance(fv, bool)
                            or not isinstance(fv, (int, float))):
                        continue
                    if _bounded(fv):
                        out[f"{k}_{fk}"] = (int(fv)
                                              if float(fv).is_integer()
                                              else fv)
            continue
        if k in _KNOWN_USAGE_KEYS and isinstance(v, (int, float)) \
                and not isinstance(v, bool) and _bounded(v):
            out[k] = int(v) if float(v).is_integer() else v
    if usage and not out:
        logger.warning(
            "usage row carried NO known usage keys (all dropped by the "
            "sanitizer): %s — the lane still counts a call but its tokens "
            "are unknown", sorted(str(k) for k in usage)[:6])
    return out


def _bounded(v: int | float) -> bool:
    """A magnitude-bounded FINITE scalar (mirrors report._numeric): bools,
    NaN/Infinity and abs(v) > 1e300 are excluded, never converted — the
    sanitizer is the choke point keeping poison out of every usage row
    (round-2 code-review P2: json.loads accepts NaN/Infinity/arbitrary-
    precision literals; a tampered provider response or checkpoint must
    never crash aggregation or round() into inf)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if abs(v) > 1e300:
        return False
    return not (isinstance(v, float) and not math.isfinite(v))


# ── the collector ───────────────────────────────────────────────────────────

_CANONICAL_BUCKET_KEYS = frozenset({
    "prompt_tokens", "completion_tokens", "calls", "usage_present"})


_NON_ACCUMULATING_KEYS = frozenset({
    "calls", "calls_without_usage", "usage_present"})


def _emit_bucket(bucket: dict) -> dict:
    """Envelope row: canonical keys always present; optional detail keys
    (cache hits/misses, total_tokens, reasoning, flattened nested detail)
    only when non-zero — the envelope stays lean and mock-free."""
    out = {k: bucket.get(k, 0 if k != "usage_present" else True)
           for k in _CANONICAL_BUCKET_KEYS}
    out["usage_present"] = bucket.get("usage_present", True)
    for k, v in bucket.items():
        if k not in _CANONICAL_BUCKET_KEYS and v:
            out[k] = v
    return out


class UsageCollector:
    """Lock-guarded per-question token accumulator (see module docstring)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # qid -> {bucket_key: bucket}; bucket_key = (stage, provider, model)
        self._rows: dict[str, dict[tuple[str, str, str], dict]] = {}
        # per-qid overhead lanes (moved failed qids + __no_key__ sentinel)
        self._overhead: dict[str, dict[tuple[str, str, str], dict]] = {}

    # ── recording ──────────────────────────────────────────────────────────

    def _sink_for(self, stage: str, registered_provider: str | None):
        """Build the bound sink closure ``attach`` wires onto adapters.

        Bucket provider = the REGISTERED provider (A1); the sink payload's
        provider is the fallback for the self-provider-bearing
        model_adapters lanes (registered provider None there).
        """
        def _sink(*, provider: str | None, model_id: str, usage: dict | None,
                  usage_present: bool) -> None:
            key_provider = (registered_provider
                            if registered_provider is not None else provider)
            self.record(stage=stage, provider=key_provider,
                        model_id=model_id, usage=usage,
                        usage_present=usage_present)
        return _sink

    def record(self, *, stage: str, provider: str | None, model_id: str,
               usage: dict | None, usage_present: bool) -> None:
        """Accumulate one sink row under the current question key.

        ``calls_without_usage`` counts the rows whose provider response
        carried NO usage block (round-2 code-review P2 disclosure);
        ``usage_present`` stays CONSERVATIVELY False when ANY row in the
        bucket lacked usage — a lane with unknown spend is never silently
        priced (the count above tells the reader how many rows were
        unknown)."""
        sanitized = _sanitize_usage(usage)
        qid = _QUESTION_KEY.get() or NO_KEY
        key = (stage, provider if provider is not None else "unknown",
               model_id)
        with self._lock:
            bucket = self._rows.setdefault(qid, {}).setdefault(key, {
                "prompt_tokens": 0, "completion_tokens": 0,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 0,
                "total_tokens": 0,
                "calls": 0, "usage_present": True,
            })
            bucket["calls"] += 1
            bucket["calls_without_usage"] = (
                bucket.get("calls_without_usage", 0)
                + (0 if usage_present else 1))
            bucket["usage_present"] = (
                bucket["usage_present"] and usage_present)
            for k, v in sanitized.items():
                bucket[k] = bucket.get(k, 0) + v

    # ── attach (A6: wires usage_sink on walked members) ───────────────────

    def attach(self, model, *, stage: str,
               provider: str | None = None) -> int:
        """Register a model (or routing/rotating wrapper) for capture.

        Walks RoutingModel ``.primary``/``.fallback``, RotatingModel
        ``.providers``, or a plain chat adapter; ASSIGNS ``usage_sink`` on
        each member. No-op (returns 0) for mocks/non-adapters that expose
        no ``complete()`` path. Returns the number of members wired.
        """
        members = _walk_members(model)
        if not members:
            return 0
        sink = self._sink_for(stage, provider)
        for m in members:
            m.usage_sink = sink
        return len(members)

    # ── drains ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_envelope(buckets: dict[tuple[str, str, str], dict],
                     ) -> dict | None:
        if not buckets:
            return None
        by_stage: dict = {}
        total = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        for (stage, provider, model), bucket in buckets.items():
            stage_map = by_stage.setdefault(
                stage, {}).setdefault(provider, {})
            stage_map[model] = _emit_bucket(bucket)
            total["prompt_tokens"] += bucket.get("prompt_tokens", 0)
            total["completion_tokens"] += bucket.get("completion_tokens", 0)
            total["calls"] += bucket.get("calls", 0)
        return {"by_stage": by_stage, "total": total}

    @staticmethod
    def _merge_buckets(target: dict, src: dict) -> None:
        """Sum src into target over the UNION of scalar keys — cache/reasoning
        detail keys recorded on one bucket survive a merge (round-2 code-review
        P2: a fixed-key merge silently dropped reasoning_tokens / flattened
        nested detail whenever spend passed through the overhead store).
        usage_present ANDs (conservative: one unknown row flags the lane
        unpriced); calls / calls_without_usage accumulate."""
        tgt = target
        tgt["calls"] += src.get("calls", 0)
        tgt["usage_present"] = (
            tgt["usage_present"] and src.get("usage_present", True))
        tgt["calls_without_usage"] = (
            tgt.get("calls_without_usage", 0)
            + src.get("calls_without_usage", 0))
        for k, v in src.items():
            if k in _NON_ACCUMULATING_KEYS:
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                tgt[k] = tgt.get(k, 0) + v

    def drain_question(self, qid: str) -> dict | None:
        """Atomically swap + aggregate the qid's rows (drained exactly once)."""
        with self._lock:
            buckets = self._rows.pop(qid, None)
        return self._to_envelope(buckets)

    def move_failed_qid_to_overhead(self, qid: str) -> bool:
        """Reclassify a terminally-failed question's spend as overhead.

        Returns False when the qid had no rows (nothing to move)."""
        env = self.drain_question(qid)
        if env is None:
            return False
        self.merge_overhead_payload({qid: env["by_stage"]})
        return True

    def drain_to_overhead(self, qid: str) -> dict | None:
        """Drain the qid's CUMULATIVE envelope into the overhead store and
        return the FULL cumulative qid envelope (payload rows already in the
        store + the just-drained rows — round-2 code-review P2: a
        --retry-failed re-attempt whose burn is smaller than the already-
        persisted payload would otherwise write a replica that folds
        nothing, permanently losing the drained rows on the next kill-9).

        Callers (breaker-open / terminal-failure paths) persist the returned
        envelope as the kill-9-safe replica on the entry/outcome; the A4
        load fold reconstructs exactly the drained-but-unsaved delta.
        Returns None when the qid had no rows (nothing to move / persist)."""
        with self._lock:
            buckets = self._rows.pop(qid, None)
            if buckets is None:
                return None
            target = self._overhead.setdefault(qid, {})
            for key, bucket in buckets.items():
                tgt = target.setdefault(key, {
                    "prompt_tokens": 0, "completion_tokens": 0,
                    "prompt_cache_hit_tokens": 0,
                    "prompt_cache_miss_tokens": 0,
                    "total_tokens": 0,
                    "calls": 0, "usage_present": True,
                })
                self._merge_buckets(tgt, bucket)
            merged = self._to_envelope(target)
        return merged

    def fold_replica(self, qid: str, by_stage: dict) -> bool:
        """A4 kill-9 read-back: fold a checkpoint replica's usage into the
        overhead store, SHORTFALL-ONLY per (stage, provider, model) bucket
        — never the overlap (idempotent on resume: a replica whose burn is
        already in the payload folds nothing; a kill -9 between the failure
        upsert and the trailing save leaves the payload short by exactly
        the replica's un-saved rows). The replica is the CUMULATIVE qid
        envelope (drain_to_overhead returns payload+candidate), so a
        --retry-failed re-attempt whose burn is smaller than the already-
        persisted payload still folds its exact un-saved delta (round-2
        code-review P2). Folds over the union of scalar keys so cache /
        reasoning detail survives.

        Returns True when any shortfall was folded."""
        folded = False
        with self._lock:
            target = self._overhead.setdefault(qid, {})
            for stage, providers in (by_stage or {}).items():
                for provider, models in (providers or {}).items():
                    for model, bucket in (models or {}).items():
                        key = (stage, provider, model)
                        tgt = target.setdefault(key, {
                            "prompt_tokens": 0, "completion_tokens": 0,
                            "prompt_cache_hit_tokens": 0,
                            "prompt_cache_miss_tokens": 0,
                            "total_tokens": 0,
                            "calls": 0, "usage_present": True,
                        })
                        changed = False
                        for k, v in bucket.items():
                            if k in _NON_ACCUMULATING_KEYS:
                                continue
                            if not isinstance(v, (int, float)) \
                                    or isinstance(v, bool):
                                continue
                            shortfall = max(0, v - tgt.get(k, 0))
                            if shortfall:
                                tgt[k] = tgt.get(k, 0) + shortfall
                                changed = True
                        calls_shortfall = max(
                            0, bucket.get("calls", 0) - tgt.get("calls", 0))
                        if calls_shortfall:
                            tgt["calls"] = tgt.get("calls", 0) + calls_shortfall
                            changed = True
                        cu_shortfall = max(
                            0, bucket.get("calls_without_usage", 0)
                            - tgt.get("calls_without_usage", 0))
                        if cu_shortfall:
                            tgt["calls_without_usage"] = (
                                tgt.get("calls_without_usage", 0)
                                + cu_shortfall)
                            changed = True
                        if changed:
                            tgt["usage_present"] = (
                                tgt["usage_present"]
                                and bucket.get("usage_present", True))
                            folded = True
        return folded

    def sweep_to_overhead(self) -> int:
        """Reclassify any RESIDUAL qid rows (daemon-thread late fires after
        a question completed) as overhead — called once at report assembly
        so no fired row is ever dropped from the run's spend totals.
        Returns the number of residual qids swept."""
        with self._lock:
            residual = [qid for qid in self._rows if qid != NO_KEY]
        for qid in residual:
            self.move_failed_qid_to_overhead(qid)
        return len(residual)

    def drain_overhead(self) -> dict | None:
        """One merged overhead envelope: keyless rows + moved failed qids."""
        with self._lock:
            merged: dict[tuple[str, str, str], dict] = {}
            # keyless rows (preflight pings / stray calls) are overhead too
            keyless = self._rows.pop(NO_KEY, {})
            buckets_sets = [keyless, *self._overhead.values()]
            for buckets in buckets_sets:
                for key, bucket in buckets.items():
                    tgt = merged.setdefault(key, {
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 0,
                        "total_tokens": 0,
                        "calls": 0, "usage_present": True,
                    })
                    self._merge_buckets(tgt, bucket)
            self._overhead = {}
        return self._to_envelope(merged)

    # ── checkpoint payload (Task 4: durable resume) ───────────────────────

    def overhead_payload(self, *, checkpoint_form: bool = False) -> dict:
        """Per-qid overhead lanes for the checkpoint (never drained).

        CONSUMES the keyless rows bucket: rows[NO_KEY] is moved into the
        overhead store under NO_KEY so a checkpoint save and the final
        report drain can never double-count the same rows. Shape:
        {qid_or_sentinel: {stage: {provider: {model: bucket}}}}.

        ``checkpoint_form`` renames the NO_KEY sentinel to the
        ``__preflight__`` marker (the checkpoint spelling run.py uses;
        merge normalizes back on load)."""
        with self._lock:
            keyless = self._rows.pop(NO_KEY, None)
            if keyless:
                target = self._overhead.setdefault(NO_KEY, {})
                for key, bucket in keyless.items():
                    tgt = target.setdefault(key, {
                        "prompt_tokens": 0, "completion_tokens": 0,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 0,
                        "total_tokens": 0,
                        "calls": 0, "usage_present": True,
                    })
                    self._merge_buckets(tgt, bucket)
            out: dict = {}
            for qid, buckets in self._overhead.items():
                inner: dict = {}
                for (stage, provider, model), bucket in buckets.items():
                    inner.setdefault(stage, {}).setdefault(
                        provider, {})[model] = dict(bucket)
                key = qid
                if checkpoint_form and qid == NO_KEY:
                    key = PREFLIGHT_MARKER
                out[key] = inner
            return out

    def merge_overhead_payload(self, payload: dict) -> None:
        """Additively fold a loaded checkpoint's overhead payload (A4)."""
        if not payload:
            return
        with self._lock:
            for qid, inner in payload.items():
                # __preflight__ is the checkpoint spelling of NO_KEY (run.py
                # renames at save) — normalize on load.
                key = NO_KEY if qid == "__preflight__" else qid
                target = self._overhead.setdefault(key, {})
                for stage, providers in (inner or {}).items():
                    for provider, models in (providers or {}).items():
                        for model, bucket in (models or {}).items():
                            bkey = (stage, provider, model)
                            tgt = target.setdefault(bkey, {
                                "prompt_tokens": 0, "completion_tokens": 0,
                                "prompt_cache_hit_tokens": 0,
                                "prompt_cache_miss_tokens": 0,
                                "total_tokens": 0,
                                "calls": 0, "usage_present": True,
                            })
                            self._merge_buckets(tgt, bucket)

    def reset(self) -> None:
        with self._lock:
            self._rows = {}
            self._overhead = {}

    def has_rows(self) -> bool:
        with self._lock:
            return bool(self._rows) or bool(self._overhead)


def _walk_members(model) -> list:
    """The sink-capable members of a model (or wrapper) — empty for mocks.

    Descends RECURSIVELY through routing/rotating wrappers (RoutingModel
    ``.primary``/``.fallback``, RotatingModel ``.providers`` — each may
    nest further) and returns the LEAF adapters that carry a real
    ``complete()`` transport (the sites where usage fires). A wrapper with
    its own ``complete()`` is NOT a leaf: its ``complete()`` delegates to
    the members' transports, so binding the wrapper would capture nothing
    (the members fire ``_emit_usage_sink(self, ...)`` with THEIR own
    identity — the per-call serving provider/model the pricing needs).
    """
    if model is None:
        return []
    wrapper = (hasattr(model, "primary") or hasattr(model, "fallback")
               or isinstance(getattr(model, "providers", None),
                             (list, tuple)))
    if not wrapper:
        if (hasattr(model, "complete")
                and callable(model.complete)):
            return [model]
        return []
    members: list = []
    for attr in ("primary", "fallback"):
        sub = getattr(model, attr, None)
        if sub is not None:
            members.extend(_walk_members(sub))
    for sub in (getattr(model, "providers", None) or []):
        members.extend(_walk_members(sub))
    # de-dup (a RotatingModel may wrap the same adapters)
    seen = set()
    out = []
    for m in members:
        if id(m) not in seen:
            seen.add(id(m))
            out.append(m)
    return out
