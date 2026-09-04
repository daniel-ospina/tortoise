"""LongMemEval costing (#2185 Task 5) — versioned per-provider USD pricing.

Pure functions + a module-constant pricing map. Cost is computed ONLY at
report time from raw token envelopes (collector drain shapes) — outcomes
stay raw and repriceable: a pricing-map correction never mutates stored
usage.

PRICING MAP — provenance discipline (the honesty contract):

* Every entry carries ``source`` (the exact URL/page), ``verified_on``
  (ISO date of the web verification), and ``estimated`` (True = single-
  source or cross-check-conflicting — surfaced in the report breakdown,
  never silently asserted).
* Out-of-map lanes → ``priced=False`` with a loud marker. NEVER a crash,
  never a silent $0, never an unlabelled guess.
* Cache-hit input priced at the reduced ``cache_read_per_1m`` ONLY where
  the map entry carries a verified rate (the DeepSeek lanes). A lane with
  cache-hit tokens but no verified cache rate is full-priced AND flagged
  ``cache_discount_unpriced`` (disclosure, never silent).
* Reasoning tokens ride ``completion_tokens`` — billed once via completion
  (never double-added). ``prompt_tokens`` includes the cached portion
  (OpenAI-compatible convention): billable input = miss_tokens × prompt
  rate + hit_tokens × cache_read rate.

Map entries (USD per 1M tokens; verified 2026-09):

  openrouter / deepseek/deepseek-v4-flash      in 0.14  out 0.28  cache 0.028
      source: openrouter.ai/deepseek/deepseek-v4-flash provider table
      (the DeepSeek-majority listing; cross-checked against
      api-docs.deepseek.com/quick_start/pricing and deepseek.ai/pricing —
      v4-flash $0.14/$0.28). NOTE: OpenRouter's header line shows the
      DigitalOcean listing ($0.0679/$0.168); balanced routing may land on
      any provider ($0.0679–$0.20 in / $0.168–$0.50 out) — we cost the
      STANDARD LIST and document the variance in the report methodology.
      Cache-read $0.028 (20% — the OpenRouter table's per-provider rate).
  deepseek-direct / deepseek-v4-flash           in 0.14  out 0.28  cache 0.0028
      source: api-docs.deepseek.com/quick_start/pricing + deepseek.com
      platform page ($0.14 in / $0.28 out; cache-hit in $0.0028 = 2%).
  openrouter / deepseek/deepseek-v4-pro         in 0.435 out 0.87  (ESTIMATED)
      source: openrouter.ai/deepseek/deepseek-v4-pro/pricing (effective
      pricing page). Cross-check conflicted ($0.435/$0.87 vs $0.87/$1.74
      vs $1.115/$3.346 — listing/effort-tier variance) → estimated=True.
  deepseek-direct / deepseek-v4-pro             in 0.87  out 1.74  (ESTIMATED)
      source: DeepSeek official page does not list v4-pro rates as of the
      verification date; priced from the OpenRouter listing for visibility
      → estimated=True.
  openai / gpt-4o-2024-08-06                    in 2.50  out 10.00
      source: OpenAI model page (developers.openai.com/api/docs/models/
      gpt-4o) — the gpt-4o snapshot's $2.50/$10.00 rate. No verified cache
      discount for gpt-4o → cache flags apply.
  venice / deepseek-v4-flash                    in 0.138 out 0.275 (ESTIMATED)
      source: the Venice-hosted row of the OpenRouter v4-flash provider
      table (venice.ai's DIRECT API rate is not separately verified — the
      VeniceModel lane calls venice.ai native, so this entry is flagged).

Provider normalization (the reader/judge lanes register the _PROVIDERS
resolution names — openrouter/deepseek/openai/gemini; the model_adapters
lanes register the class names — openrouter/venice/deepseek-direct): the
``deepseek`` and ``deepseek-direct`` spellings fold onto the DeepSeek
table (same API family); every other provider keys by its own name.

Lookup: exact (provider, model) → bare-model (strip the vendor prefix up
to the last ``/``) → unpriced (None). "deepseek/deepseek-v4-flash" and
"deepseek-v4-flash" therefore both resolve on their own lanes.
"""
from __future__ import annotations

#: Bump ONLY when the map below changes (the report's pricing snapshot pins
#: this so published numbers carry their exact map version).
PRICING_MAP_VERSION = "2026-09-03"

PRICING_MAP: dict[str, dict[str, dict]] = {
    "openrouter": {
        "deepseek/deepseek-v4-flash": {
            "prompt_per_1m": 0.14, "completion_per_1m": 0.28,
            "cache_read_per_1m": 0.028,
            "source": ("openrouter.ai/deepseek/deepseek-v4-flash "
                       "(provider table)"),
            "verified_on": "2026-09-03", "estimated": False,
            "note": ("standard list $0.14/$0.28 (DeepSeek-majority); "
                     "balanced routing may land $0.0679–$0.20 in / "
                     "$0.168–$0.50 out (provider discounts)")},
        "deepseek/deepseek-v4-pro": {
            "prompt_per_1m": 0.435, "completion_per_1m": 0.87,
            "cache_read_per_1m": None,
            "source": "openrouter.ai/deepseek/deepseek-v4-pro/pricing",
            "verified_on": "2026-09-03", "estimated": True,
            "note": ("effective-pricing listing; cross-check conflicted "
                     "(0.87/1.74 and 1.115/3.346 listings exist)")},
        "deepseek/deepseek-chat": {
            "prompt_per_1m": 0.14, "completion_per_1m": 0.28,
            "cache_read_per_1m": 0.028,
            "source": ("openrouter.ai/deepseek/deepseek-v4-flash provider "
                       "table (deepseek-chat is the v4-flash alias)"),
            "verified_on": "2026-09-03", "estimated": True,
            "note": "alias resolution — verify at next map update"},
    },
    "deepseek": {
        "deepseek-v4-flash": {
            "prompt_per_1m": 0.14, "completion_per_1m": 0.28,
            "cache_read_per_1m": 0.0028,
            "source": ("api-docs.deepseek.com/quick_start/pricing + "
                       "deepseek.com platform"),
            "verified_on": "2026-09-03", "estimated": False,
            "note": "cache-hit in $0.0028/1M (2% of input)"},
        "deepseek-v4-pro": {
            "prompt_per_1m": 0.87, "completion_per_1m": 1.74,
            "cache_read_per_1m": None,
            "source": ("DeepSeek official page lists no v4-pro rate as of "
                       "verification; priced from the OpenRouter listing"),
            "verified_on": "2026-09-03", "estimated": True,
            "note": "not cross-confirmed on the official page"},
        "deepseek-chat": {
            "prompt_per_1m": 0.14, "completion_per_1m": 0.28,
            "cache_read_per_1m": 0.0028,
            "source": "api-docs.deepseek.com/quick_start/pricing",
            "verified_on": "2026-09-03", "estimated": False,
            "note": ""},
    },
    "openai": {
        "gpt-4o-2024-08-06": {
            "prompt_per_1m": 2.5, "completion_per_1m": 10.0,
            "cache_read_per_1m": None,
            "source": "developers.openai.com/api/docs/models/gpt-4o",
            "verified_on": "2026-09-03", "estimated": False,
            "note": "no verified gpt-4o cache discount"},
    },
    "venice": {
        "deepseek-v4-flash": {
            "prompt_per_1m": 0.138, "completion_per_1m": 0.275,
            "cache_read_per_1m": 0.028,
            "source": ("OpenRouter v4-flash provider table (Venice row) — "
                       "venice.ai DIRECT API not separately verified"),
            "verified_on": "2026-09-03", "estimated": True,
            "note": "VeniceModel calls venice.ai native — verify"},
    },
}

#: Provider-name normalization — the model_adapters class names and the
#: _PROVIDERS resolution names that denote the SAME API family fold onto
#: one table.
_PROVIDER_NORMALIZATION = {
    "deepseek-direct": "deepseek",
    "deepseek": "deepseek",
}

_CACHE_KEY = "prompt_cache_hit_tokens"
_MISS_KEY = "prompt_cache_miss_tokens"


def _normalize_provider(provider: str | None) -> str:
    return _PROVIDER_NORMALIZATION.get(provider or "", provider or "unknown")


def _bare_model(model: str) -> str:
    return model.rsplit("/", 1)[-1] if "/" in model else model


def lookup_rate(provider: str | None, model: str) -> dict | None:
    """Exact (provider, model) → bare-model → None (unpriced)."""
    prov = _normalize_provider(provider)
    table = PRICING_MAP.get(prov)
    if not table:
        return None
    entry = table.get(model)
    if entry is None:
        bare = _bare_model(model)
        if bare != model:
            entry = table.get(bare)
    return entry


def price_usage_envelope(envelope: dict | None
                         ) -> tuple[float, bool, dict]:
    """Price a usage envelope (collector drain shape).

    Returns ``(cost_usd, priced, breakdown)`` where ``priced`` is True only
    when EVERY lane with usage_present rows was priced (any unpriced lane —
    unknown model OR usage_present=False spend — flips it False, and the
    lane lands in ``breakdown["unpriced"]`` with its reason). Priced lanes
    are ALWAYS totaled (an unpriced lane never zeroes the priced ones).
    """
    by_stage = ((envelope or {}).get("by_stage") or {})
    lanes: list[dict] = []
    unpriced: list[dict] = []
    cost = 0.0
    for stage, providers in by_stage.items():
        for provider, models in (providers or {}).items():
            for model, bucket in (models or {}).items():
                entry = lookup_rate(provider, model)
                row = _price_lane(stage, provider, model, bucket, entry)
                if row["priced"]:
                    cost += row["usd"]
                lanes.append(row)
                if not row["priced"]:
                    unpriced.append(row)
    priced = not unpriced
    cost = round(cost, 6)
    return cost, priced, {
        "lanes": lanes,
        "unpriced": unpriced,
        "priced": priced,
        "estimated": [l for l in lanes if l.get("estimated")],
        "map_version": PRICING_MAP_VERSION,
    }


def _price_lane(stage: str, provider: str, model: str, bucket: dict,
                entry: dict | None) -> dict:
    prompt = bucket.get("prompt_tokens", 0)
    completion = bucket.get("completion_tokens", 0)
    hit = bucket.get(_CACHE_KEY, 0) or 0
    usage_present = bucket.get("usage_present", True)
    row: dict = {
        "stage": stage, "provider": provider, "model": model,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "calls": bucket.get("calls", 0), "usd": 0.0,
        "priced": True, "estimated": False,
        "usage_present": usage_present,
        "cache_discount_applied": False, "cache_discount_unpriced": False,
    }
    if entry is None:
        row.update(priced=False,
                   reason="unknown_model" if usage_present
                   else "usage_present_false")
        return row
    if not usage_present:
        row.update(priced=False, reason="usage_present_false")
        return row
    row["estimated"] = bool(entry.get("estimated"))
    rate_in = float(entry["prompt_per_1m"])
    rate_out = float(entry["completion_per_1m"])
    cache_rate = entry.get("cache_read_per_1m")
    if hit and cache_rate is not None:
        miss = max(0, prompt - hit)
        input_cost = miss * rate_in + hit * float(cache_rate)
        row["cache_discount_applied"] = True
    else:
        input_cost = prompt * rate_in
        if hit:
            # provider reported cache hits but no verified discount rate →
            # full-priced AND disclosed (never silently either way).
            row["cache_discount_unpriced"] = True
    row["usd"] = round((input_cost + completion * rate_out) / 1e6, 6)
    return row
