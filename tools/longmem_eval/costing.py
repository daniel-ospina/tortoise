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

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/costing.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]})"
    )

import math

#: Bump ONLY when the map below changes (the report's pricing snapshot pins
#: this so published numbers carry their exact map version). Bumped 2026-09-04
#: when the openrouter gpt-4o-2024-08-06 row was added (round-2 code review).
PRICING_MAP_VERSION = "2026-09-04"

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
        "gpt-4o-2024-08-06": {
            "prompt_per_1m": 2.625, "completion_per_1m": 10.5,
            "cache_read_per_1m": None,
            "source": ("openrouter.ai/openai/gpt-4o-2024-08-06 listing = "
                       "OpenAI list ($2.50/$10) + ~5% platform fee"),
            "verified_on": "2026-09-03", "estimated": True,
            "note": ("covers the judge lane when the official spec "
                      "openai:gpt-4o-2024-08-06 is SERVED via openrouter "
                      "(a both-keys env resolves the openrouter transport; "
                      "the lane records the bare model id)")},
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
    # Foreign/tampered input: every container level is guarded, so a
    # poisoned export degrades the lane rather than crashing report
    # assembly (the ``_tok`` contract below).
    if not isinstance(by_stage, dict):
        by_stage = {}
    lanes: list[dict] = []
    unpriced: list[dict] = []
    cost = 0.0
    for stage, providers in by_stage.items():
        if not isinstance(providers, dict):
            continue
        for provider, models in providers.items():
            if not isinstance(models, dict):
                continue
            for model, bucket in models.items():
                # Foreign/tampered buckets are skipped, never crashed on
                # (the module's "never crash, never a silent $0" contract).
                if not isinstance(bucket, dict):
                    bucket = {}
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
        "estimated": [lane for lane in lanes if lane.get("estimated")],
        "map_version": PRICING_MAP_VERSION,
    }


def _percentile(xs: list[float], q: float) -> float:
    """Linear-interpolation percentile — the SAME convention as
    ``tools/longmem_eval/report.py:_percentile`` (kept local to avoid an
    import cycle: report.py consumes this module)."""
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo = int(math.floor(k))  # noqa: RUF046
    hi = int(math.ceil(k))  # noqa: RUF046
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _row_props(row) -> dict | None:
    """Normalize an ``analytics_events`` row (or a bare properties dict)."""
    if not isinstance(row, dict):
        return None
    props = row.get("properties")
    if isinstance(props, dict):
        return props
    return row


def _measured_calls(props: dict) -> int:
    """Successful, METERED provider calls for one session row.

    Read from the ``by_stage`` envelope, which ``_accumulate_call_cost``
    only ever writes on a SUCCESSFUL call — unlike the row's top-level
    ``calls``, which is the extractor's ATTEMPT counter. The two differ for
    exactly the sessions this measurement exists to price honestly: a
    deadline-killed or failed generation is attempted (and, for a
    deadline-kill, billed) but never metered.

    A call counts as METERED when the lane produced a usage block
    (``usage_present``). When a lane has ANY usage-less call, only the calls
    that still reported a charge count — the calls with neither usage nor
    charge are the ones this measurement genuinely cannot see.

    #5854: a call whose TOKEN count was malformed is NOT metered either — the
    accumulator rejected the value instead of shaping it, so the lane carries
    the rejection in ``calls_without_tokens``. Without this subtraction those
    calls would be counted metered with 0 tokens, and a session whose only
    measurement was garbage would read as a fully-priced $0.
    """
    total = 0
    by_stage = props.get("by_stage") or {}
    if not isinstance(by_stage, dict):
        return 0
    for providers in by_stage.values():
        if not isinstance(providers, dict):
            continue
        for models in providers.values():
            if not isinstance(models, dict):
                continue
            for bucket in models.values():
                bucket = bucket if isinstance(bucket, dict) else {}
                calls = _as_int(bucket.get("calls"))
                rejected = _as_int(bucket.get("calls_without_tokens"))
                if bucket.get("usage_present"):
                    total += max(0, calls - rejected)
                    continue
                # No usage block: only the calls that still reported a
                # charge are measured.
                total += max(0, calls - _as_int(
                    bucket.get("calls_without_cost")))
    return total


def _as_int(value, default: int = 0) -> int:
    """Poison-tolerant integer read, mirroring ``_price_lane._tok``: a
    tampered/foreign value (bool, non-finite, absurd magnitude, junk type)
    degrades to ``default`` instead of crashing report assembly.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if abs(value) > 1e300:
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _as_token_count(value) -> int | None:
    """Read a stored TOKEN count, or ``None`` when it cannot be one (#3840).

    The reader-side counterpart of ``extractor_v2._normalise_token_count``,
    and the rules are deliberately THAT function's, not ``_as_int``'s:

    * ``bool`` / non-numeric / non-finite / ``|v| > 1e300`` — not a number
      (via the shared ``_valid_number`` predicate, so this and
      ``_price_lane._tok`` cannot drift);
    * a **negative** is rejected. #5854 established that a token count cannot
      be negative and that accepting one sign-flips a total while the row
      still reads priced. ``_price_lane._tok`` already rejects it for the
      dollar path; the token path must not re-open the hole the same issue
      closed, or a pre-#5854 row stored in ``analytics_events`` reports a
      negative calibration total with nothing disclosed;
    * a **non-integral float** is rejected, never floored — the same
      reject-and-disclose decision the emit seam makes (``int(100.9)`` would
      shape a plausible wrong count). ``_price_lane._tok`` is laxer here
      because it prices a RATE against the number; a COUNT has no such
      latitude.

    ``None`` is a DISCLOSED ABSENCE — the caller marks the capture's token
    figure as a floor (``partial_captures``) rather than substituting a
    shaped ``0``, which would read as a genuinely tiny extraction.
    """
    if not _valid_number(value) or value < 0:
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value)


def _lane_tokens_absent(props: dict) -> bool:
    """#3840: a METERED lane in this row carried no token field at all.

    The row-level token totals cannot see this case: a call that reported a
    CHARGE but no token field has ``has_usage`` true at the accumulator
    (``extractor_v2._accumulate_call_cost``), so its lane counts as metered
    (``_measured_calls``), adds 0 to every token total, and inflates nothing
    the attempt-based disclosures would notice. When such a call shares a
    capture with a token-bearing one the row total is non-zero, so the
    session-level ``tokenless`` test misses it too — the measured tokens are
    silently short, which is the one outcome this measurement must never
    produce. Reading the lanes is the only place the absence is still
    visible.
    """
    by_stage = props.get("by_stage") or {}
    if not isinstance(by_stage, dict):
        return False
    for providers in by_stage.values():
        if not isinstance(providers, dict):
            continue
        for models in providers.values():
            if not isinstance(models, dict):
                continue
            for bucket in models.values():
                bucket = bucket if isinstance(bucket, dict) else {}
                if not bucket.get("usage_present"):
                    continue
                if _as_int(bucket.get("calls")) <= 0:
                    continue
                if (_as_token_count(bucket.get("prompt_tokens")) or 0) \
                        + (_as_token_count(bucket.get("completion_tokens"))
                           or 0) == 0:
                    return True
    return False


def cost_per_session_distribution(rows: list[dict] | None) -> dict:
    """#3359 report-time ``cost_per_session`` (thresholds.yaml A18) producer.

    Consumes ``analytics_events`` rows with ``event_name == "capture_cost"``
    (or bare properties dicts). Per row:

    * the provider's OWN reported charge (``properties.cost_usd``) is the
      primary number (#2906 — in-band reconciliation), used whenever the
      row discloses no unpriced calls (``calls_without_cost == 0``);
    * when a row has unpriced calls, the raw ``by_stage`` token envelope is
      repriced from the versioned ``PRICING_MAP`` (a provider that stops
      reporting a charge, or a map correction, never invalidates the
      measurement).

    THE POPULATION (why ``n`` != ``n_rows``). ``p50``/``p95`` are computed
    over sessions that actually ran a METERED extraction. A row with no
    attempted call (``calls == 0`` — an empty/turn-less capture) and a row
    whose calls were all attempted but never metered (``measured_calls ==
    0`` — e.g. every generation deadline-killed) carry NO measured spend, and
    silently counting them as $0 would drag p50 toward zero for exactly the
    reason the launch gate exists. They are EXCLUDED from the percentiles and
    counted separately in ``excluded_no_calls`` / ``excluded_unmeasured``, so
    a missing measurement can never read as a cheap one. ``deadline_aborts``
    is reported alongside: deadline-killed generations are billed upstream
    but produce no tokens, so they are spend this measurement cannot price.

    Returns ``{n, n_rows, p50, p95, max, total_usd, provider_reported_usd,
    map_priced_usd, unpriced_sessions, priced_sessions, fully_priced, tokens,
    calls_without_cost, calls_without_usage, calls_without_tokens,
    deadline_aborts, route_cost_overflows,
    unattributed_calls, unattributed_captures, unmetered_attempts,
    excluded_no_calls, excluded_unmeasured, source, map_version,
    heaviest}``.
    ``source`` is ``provider`` / ``map`` / ``mixed``; ``unpriced_sessions``
    counts sessions whose tokens could NOT be priced even from the map —
    surfaced, never silently zeroed. ``heaviest`` lists the costliest
    included sessions individually (the p95/heaviest read the pricing
    decision is actually made from).

    #3840 — ``tokens`` is the TOKEN half of the per-capture record, over the
    SAME included sessions as the cost percentiles: ``prompt`` / ``completion``
    / ``total`` summed across them, and ``p50`` / ``p95`` / ``max`` of the
    per-CAPTURE total. ``total`` is derived (``prompt + completion``) rather
    than carried as a fourth stored counter, and the median/p95 pair is the
    figure the issue names — a mean is dominated by outliers. Three
    disclosures ride it: ``partial_captures`` (the capture's tokens are a
    floor — unmetered, #3824-unrolled, a metered call reporting a charge but
    no token field, or a stored value the reader rejected),
    ``tokenless_captures`` (calls were metered yet produced no usable token
    count — absent, zero, or rejected — so the 0 is an absence, not a tiny
    extraction) and
    ``invalid_token_captures`` (a negative or non-integral stored count,
    rejected rather than shaped — #5854's reject-don't-shape rule on the
    reader side); ``lower_bound`` is true when any capture's figure is a
    floor.

    ``provider_reported_usd`` and ``map_priced_usd`` OVERLAP and are NOT
    additive: both are accumulated over every row carrying the respective
    evidence (see the ``overlap: True`` marker in the return), so a row
    with BOTH a provider total and priceable tokens lands in both fields.
    They show which pricing source carried the window — never read them as
    components of ``total_usd`` (the session-total distribution's sum,
    i.e. the spend actually reported).

    PER SESSION, NOT PER ROW. ``analytics_events`` is append-only and a
    failed capture that is then retried (#2335 WI-2b) writes a SECOND row
    for the SAME ``session_id`` — the retry's spend only. Percentiling rows
    directly would split one session's true cost across two samples and
    understate it for exactly the long/flaky sessions the p95 read is
    about, so rows are summed per ``session_id`` FIRST and the distribution
    is over session totals. ``p50``/``p95`` are therefore "$/session" in the
    literal sense.

    #3824 — THE ABSENCE IS A SESSION TOO. A capture that reached the
    provider but whose roll-up did not survive (the M2 lane, #3747) writes a
    row carrying ``unattributed`` — and before this change it wrote no row
    at all, so it contributed to NOTHING here: not ``n_rows``, not ``n``,
    not ``excluded_no_calls``, not ``unmetered_attempts``. ``unmetered`` is
    computed WITHIN a row, so a session with no row can never be unmetered —
    the denominator simply undercounts, and the cohort cap set from it
    under-refuses in exactly the case it exists to catch. Those disclosed
    calls now fold into ``attempts`` (they are attempts with no meterable
    response, like a timed-out call), so they land in ``unmetered_attempts``,
    the session is excluded as ``unmetered`` rather than mislabelled
    ``no_calls``, ``fully_priced`` refuses to read true, and
    ``unattributed_calls`` / ``unattributed_captures`` name the cause.
    """
    costs: list[float] = []
    heaviest: list[dict] = []
    provider_total = 0.0
    map_total = 0.0
    unpriced = 0
    without_cost_total = 0
    without_usage_total = 0
    without_tokens_total = 0
    route_cost_overflows_total = 0
    deadline_aborts = 0
    unattributed_total = 0
    unattributed_captures = 0
    unmetered_attempts = 0
    excluded_no_calls = 0
    excluded_unmeasured = 0
    used_map = 0
    used_provider = 0
    n_rows = 0
    # session key -> aggregate. A row without a ``session_id`` gets its own
    # synthetic key (it cannot be attributed to another session).
    sessions: dict[str, dict] = {}
    # #3840: the token figures, accumulated over the SAME included population
    # as ``costs``. ``token_totals`` is the per-CAPTURE sample the issue asks
    # for (median + p95, not a mean — a mean is dominated by outliers).
    token_totals: list[int] = []
    prompt_tokens_total = 0
    completion_tokens_total = 0
    tokens_lower_bound = 0
    tokenless_captures = 0
    invalid_token_captures = 0
    for row in rows or []:
        props = _row_props(row)
        if props is None:
            continue
        n_rows += 1
        # A provider total is only AUTHORITATIVE when the key was actually
        # supplied and is a sane number. An absent/None/junk ``cost_usd``
        # sanitizes to 0.0 for the percentile's sake, but must NOT then be
        # taken as "the provider reported $0" — that would emit a silent $0
        # with ``fully_priced`` true while the row's own priceable tokens
        # were discarded (the exact failure this module exists to disclose).
        raw_provider = props.get("cost_usd")
        provider_reported = not (
            isinstance(raw_provider, bool)
            or not isinstance(raw_provider, (int, float))
            # Magnitude BEFORE isfinite: ``math.isfinite`` on a huge
            # arbitrary-precision int raises OverflowError (a 400-digit
            # JSON integer is valid JSON and loads as ``int``).
            or abs(raw_provider) > 1e300
            or (isinstance(raw_provider, float)
                and not math.isfinite(raw_provider)))
        provider_usd = float(raw_provider) if provider_reported else 0.0
        map_usd, priced, _ = price_usage_envelope(
            {"by_stage": props.get("by_stage") or {}})
        without = _as_int(props.get("calls_without_cost"))
        without_usage = _as_int(props.get("calls_without_usage"))
        without_tokens = _as_int(props.get("calls_without_tokens"))
        deadline_aborts += _as_int(props.get("deadline_aborts"))
        # Reporting totals stay over EVERY row (the aggregate spend is real
        # even where a single row cannot be priced).
        provider_total += provider_usd
        map_total += map_usd
        without_cost_total += without
        without_usage_total += without_usage
        without_tokens_total += without_tokens
        route_cost_overflows_total += _as_int(props.get("route_cost_overflows"))
        measured = _measured_calls(props)
        # #3824: calls the writer disclosed as made-but-unrolled (F2 — the
        # row exists, the roll-up did not). They are ATTEMPTS with no
        # meterable response, exactly like a provider-side timeout, so they
        # fold into ``attempts`` below and reach ``unmetered_attempts``.
        # Counting them as nothing is the undercount #3824 exists to remove.
        unattributed = _as_int(props.get("unattributed"))
        unattributed_total += unattributed
        sid = props.get("session_id")
        sess = sessions.setdefault(
            str(sid) if sid else f"__row{n_rows}",
            {"session_id": sid, "cost": 0.0, "rows": 0, "attempts": 0,
             "measured": 0, "without": 0, "unattributed": 0,
             "prompt_tokens": 0, "completion_tokens": 0,
             "token_gap": False, "invalid_tokens": 0,
             "used_map": False, "used_provider": False, "unpriced": False})
        sess["rows"] += 1
        sess["attempts"] += _as_int(props.get("calls")) + unattributed
        sess["unattributed"] += unattributed
        sess["measured"] += measured
        sess["without"] += without
        # #3840: the TOKEN half of the per-capture record. Summed per session
        # by the SAME grouping the cost percentiles use — a second session
        # key here would let the cost figure and the token figure disagree
        # about which captures they describe (#3843's one-record rule).
        # ``_as_token_count`` is the reader-side counterpart of the emit
        # seam's normaliser (#5854): a value that cannot be a token count —
        # a negative above all — contributes NO number and marks the
        # capture's token figure as a floor, instead of being truncated, or
        # sign-flipped, into a plausible-looking wrong one.
        for key in ("prompt_tokens", "completion_tokens"):
            raw_tokens = props.get(key)
            parsed_tokens = _as_token_count(raw_tokens)
            if parsed_tokens is None:
                # an ABSENT key is the ordinary zero (a metered call may
                # report none); a PRESENT, unusable value is a rejection
                if raw_tokens is not None:
                    sess["invalid_tokens"] += 1
                continue
            sess[key] += parsed_tokens
        if _lane_tokens_absent(props):
            sess["token_gap"] = True
        if without == 0 and provider_reported:
            sess["cost"] += provider_usd
            sess["used_provider"] = True
        elif priced:
            sess["cost"] += map_usd
            sess["used_map"] = True
        else:
            # No usable provider total AND nothing priceable from the map:
            # a lower bound (0.0 when the key was absent), disclosed below.
            sess["cost"] += provider_usd  # lower bound; disclosed below
            sess["unpriced"] = True
    for sess in sessions.values():
        # Attempts that produced no meterable response (a provider-side
        # timeout is the common case — the adapter's 60 s read timeout fires
        # long before the extractor's own deadline, so it is NOT the
        # `deadline_aborts` class). They may still be billed upstream, so a
        # session carrying them must not report as fully priced.
        sess["unmetered"] = max(0, sess["attempts"] - sess["measured"])
        # Counted for EVERY session, including the ones excluded below: an
        # all-unmetered session is exactly the billed-spend-we-cannot-see
        # case this counter exists for, so excluding it must not also erase
        # the disclosure — nor leave ``fully_priced`` true.
        unmetered_attempts += sess["unmetered"]
        # #3824: a session whose attempts included unrolled calls is the
        # capture the reader must be able to NAME, not merely exclude.
        if sess["unattributed"]:
            unattributed_captures += 1
        if sess["attempts"] == 0 and sess["measured"] == 0:
            excluded_no_calls += 1
            continue
        if sess["measured"] == 0 and sess["cost"] == 0.0:
            # Calls were attempted, nothing was metered — no measurement
            # exists, so this must not enter the distribution as a $0.
            excluded_unmeasured += 1
            continue
        session_cost = round(sess["cost"], 6)
        costs.append(session_cost)
        # #3840: the token sample for THIS capture, over the SAME included
        # population as ``costs`` — the two distributions must never describe
        # different sets of captures.
        session_tokens = sess["prompt_tokens"] + sess["completion_tokens"]
        token_totals.append(session_tokens)
        prompt_tokens_total += sess["prompt_tokens"]
        completion_tokens_total += sess["completion_tokens"]
        # #3840 disclosure, the token-side mirror of ``unpriced``: the token
        # count is a FLOOR whenever we know a call happened that we could not
        # see the tokens of — an unmetered/timeout attempt, an #3824 unrolled
        # call, a METERED call that carried a charge but no token field (the
        # mixed case ``_lane_tokens_absent`` finds, which no attempt-based
        # counter can see), or a token value the reader rejected (a negative
        # or a fraction — #5854's reject-don't-shape rule, reader half).
        # Counted ONCE per capture: three overlapping causes must not read as
        # three captures.
        if sess["unmetered"] or sess["token_gap"] or sess["invalid_tokens"]:
            tokens_lower_bound += 1
        if sess["invalid_tokens"]:
            invalid_token_captures += 1
        # A capture whose calls were all metered yet produced NO usable token
        # count — absent, an honest zero, or rejected. An absence wearing a 0,
        # counted separately rather than left to deflate p50 as a genuinely
        # tiny extraction. It CAN overlap ``invalid_token_captures`` (a
        # rejected value with no other usable count in the capture is both; a
        # rejected value beside a usable one is only invalid), so the rendered
        # wording must not claim "no token field" for a capture that had one.
        if sess["measured"] > 0 and session_tokens == 0:
            tokenless_captures += 1
        if sess["unpriced"]:
            unpriced += 1
        # Count only what was actually used. A session whose lanes were all
        # UNPRICED set neither flag, and must not be counted as both (that
        # would report ``source: "mixed"`` for a dataset where nothing was
        # priced at all).
        if sess["used_map"]:
            used_map += 1
        if sess["used_provider"]:
            used_provider += 1
        if sess["used_map"] and sess["used_provider"]:
            heaviest_source = "mixed"
        elif sess["used_map"]:
            heaviest_source = "map"
        elif sess["used_provider"]:
            heaviest_source = "provider"
        else:
            heaviest_source = "unpriced"
        heaviest.append({
            "session_id": sess["session_id"],
            "cost_usd": session_cost,
            "calls": sess["attempts"],
            "measured_calls": sess["measured"],
            "rows": sess["rows"],
            "calls_without_cost": sess["without"],
            "unattributed_calls": sess["unattributed"],
            "unmetered_attempts": sess["unmetered"],
            # #3840: the capture-level token read, so the "this capture was
            # unusually expensive" drill-down can be read in tokens too —
            # the whole point of a calibration figure that is comparable
            # across providers and models.
            "prompt_tokens": sess["prompt_tokens"],
            "completion_tokens": sess["completion_tokens"],
            "total_tokens": session_tokens,
            "source": heaviest_source,
        })
    if used_map == 0:
        source = "provider"
    elif used_provider == 0 and unpriced == 0:
        source = "map"
    else:
        source = "mixed"
    heaviest.sort(key=lambda r: r["cost_usd"], reverse=True)
    return {
        "n": len(costs),
        "n_rows": n_rows,
        "p50": _percentile(costs, 0.50),
        "p95": _percentile(costs, 0.95),
        "max": max(costs) if costs else 0.0,
        "total_usd": round(sum(costs), 6),
        "provider_reported_usd": round(provider_total, 6),
        "map_priced_usd": round(map_total, 6),
        # Diagnostic, NOT additive (see the docstring): the marker makes the
        # overlap machine-checkable so a consumer cannot sum the two into a
        # phantom "total spend".
        "overlap": True,
        "unpriced_sessions": unpriced,
        # Sessions with an actual PRICED number. ``n`` counts every session
        # whose spend is reported (an all-unpriced session still contributes
        # its provider-reported lower bound, which may be $0), so a consumer
        # needs this to tell "measured" from "nothing could be priced".
        "priced_sessions": max(0, len(costs) - unpriced),
        # #3840 — THE TOKEN SET. The issue's metric list asks for input
        # tokens, output tokens, and TOTAL tokens as a tracked figure; the
        # "total" is the one that is comparable across providers and models,
        # and it is DERIVED here (prompt + completion) rather than stored as
        # a fourth redundant counter on the row. A stored total is a second
        # representation of the same quantity and can drift from the parts
        # (the #5868 lesson: a sub-total that will not reconcile is worse
        # than no sub-total). Every model route we call is OpenAI-compatible,
        # where ``usage.total_tokens == prompt_tokens + completion_tokens``,
        # so the derivation is exact rather than an approximation.
        #
        # THE POPULATION IS THE COST POPULATION — the same included sessions
        # as ``costs``, so "tokens per capture" and "$ per capture" can never
        # describe different captures.
        "tokens": {
            "sessions": len(token_totals),
            "prompt": prompt_tokens_total,
            "completion": completion_tokens_total,
            "total": prompt_tokens_total + completion_tokens_total,
            "p50": _percentile([float(t) for t in token_totals], 0.50),
            "p95": _percentile([float(t) for t in token_totals], 0.95),
            "max": max(token_totals) if token_totals else 0,
            # Captures whose token count is a FLOOR: at least one call
            # happened whose tokens we could not see — an unmetered or
            # #3824-unrolled attempt, a metered call that reported a charge
            # but no token field, or a value the reader rejected.
            "partial_captures": tokens_lower_bound,
            # Captures whose calls were all metered yet produced NO usable
            # token count (the field was absent, was an honest zero, or was
            # rejected). An absence wearing a 0, disclosed rather than left
            # to deflate p50 as a genuinely tiny extraction. A SUBSET of
            # ``partial_captures``; it CAN overlap ``invalid_token_captures``
            # (a rejected value with no other usable count is both), which is
            # why the rendered cause says "no usable token count" and not
            # "no token field".
            "tokenless_captures": tokenless_captures,
            # Captures carrying a token value the reader REJECTED (a negative
            # or a non-integral float — #5854's reject-don't-shape rule
            # applied on the reader side). A subset of ``partial_captures``,
            # named separately so a pre-#5854 row in ``analytics_events`` is
            # visible rather than merely present in a count.
            "invalid_token_captures": invalid_token_captures,
            "lower_bound": bool(tokens_lower_bound or tokenless_captures),
        },
        # True only when EVERY counted session was fully priced — no unpriced
        # lanes AND no attempt that produced no meterable response.
        "fully_priced": bool(costs) and unpriced == 0
        and unmetered_attempts == 0,
        # Attempts that produced no meterable response. Possibly billed
        # upstream, never silently priced at $0. Includes #3824's
        # billed-but-unrolled calls.
        "unmetered_attempts": unmetered_attempts,
        # #3824: the unrolled-call share of the line above, named by cause.
        # ``unattributed_calls`` is COUNTED INSIDE ``unmetered_attempts``
        # (not additive with it); ``unattributed_captures`` is how many
        # sessions carried any. Both are zero for a replay-only window.
        "unattributed_calls": unattributed_total,
        "unattributed_captures": unattributed_captures,
        "calls_without_cost": without_cost_total,
        "calls_without_usage": without_usage_total,
        "calls_without_tokens": without_tokens_total,
        # #5868: OVERFLOW EVENTS — a row whose ``by_stage`` breakdown dropped a
        # cost sub-total it could not represent (one event may stand for
        # several calls at the merge seams). ``cost_usd`` is still the
        # authoritative total UNLESS the row also carries
        # ``calls_without_cost``; this counter is a BREAKDOWN disclosure only —
        # it says the ``by_stage`` partition is incomplete and may not
        # reconcile with ``cost_usd`` in either direction (when the session sum
        # overflowed too, the two can still agree).
        "route_cost_overflows": route_cost_overflows_total,
        "deadline_aborts": deadline_aborts,
        "excluded_no_calls": excluded_no_calls,
        "excluded_unmeasured": excluded_unmeasured,
        "source": source,
        "map_version": PRICING_MAP_VERSION,
        "heaviest": heaviest,
    }


def cost_by_stage(rows: list[dict] | None) -> dict:
    """#3359 per-stage / per-model priced breakdown across ``capture_cost``
    rows — the evidence for whether the cheap-point-model mitigation works.

    Aggregates the ``by_stage`` token envelopes (not the row totals) through
    the SAME versioned ``PRICING_MAP``, so each stage carries its own
    measured tokens, the provider's own reported charge, and the map-priced
    equivalent. Returns ``{"by_stage": {stage: {...}}, "map_version":
    <PRICING_MAP_VERSION>, "overlap": True}`` where each stage has
    ``calls``/``prompt_tokens``/``completion_tokens``/``provider_reported_usd``
    /``map_priced_usd``/``models`` (``{provider: {model: {...}}}``) and
    ``unpriced_calls`` (map-less calls — never silently $0).

    ``overlap`` is carried on the return, on every stage bucket, AND on
    every per-model lane entry: ``provider_reported_usd`` and
    ``map_priced_usd`` are the provider's own charge and the map's price for
    the SAME tokens, so they are NOT additive and must never be summed into
    a "total spend".
    """
    stages: dict[str, dict] = {}
    for row in rows or []:
        props = _row_props(row)
        if props is None:
            continue
        by_stage = props.get("by_stage") or {}
        if not isinstance(by_stage, dict):
            continue
        for stage, providers in by_stage.items():
            if not isinstance(providers, dict):
                continue
            bucket = stages.setdefault(stage, {
                "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "provider_reported_usd": 0.0, "map_priced_usd": 0.0,
                "unpriced_calls": 0, "models": {}})
            for provider, models in providers.items():
                if not isinstance(models, dict):
                    continue
                for model, lane in models.items():
                    lane = lane if isinstance(lane, dict) else {}
                    calls = _as_int(lane.get("calls"))
                    # #3840: the lane token reads obey the SAME
                    # reject-don't-shape rule the distribution's token block
                    # does (``_as_token_count``: no negative, no fraction, no
                    # poison), because these values now feed the report's
                    # per-stage and per-model ``in=``/``out=``/``total=`` —
                    # part of the metric set. Reading them with ``_as_int``
                    # would let ONE metric set render a negative token count on
                    # the stage line while the capture line rejected and
                    # disclosed the very same row: two readers, two rules.
                    ptoks = _as_token_count(lane.get("prompt_tokens")) or 0
                    ctoks = _as_token_count(lane.get("completion_tokens")) or 0
                    raw_reported = lane.get("cost_usd")
                    if (isinstance(raw_reported, bool)
                            or not isinstance(raw_reported, (int, float))
                            # Magnitude BEFORE isfinite (huge ints raise
                            # OverflowError in math.isfinite).
                            or abs(raw_reported) > 1e300
                            or (isinstance(raw_reported, float)
                                and not math.isfinite(raw_reported))):
                        reported = 0.0
                    else:
                        reported = float(raw_reported)
                    lane_map_usd, lane_priced, _ = price_usage_envelope(
                        {"by_stage": {stage: {provider: {model: lane}}}})
                    bucket["calls"] += calls
                    bucket["prompt_tokens"] += ptoks
                    bucket["completion_tokens"] += ctoks
                    bucket["provider_reported_usd"] = round(
                        bucket["provider_reported_usd"] + reported, 6)
                    bucket["map_priced_usd"] = round(
                        bucket["map_priced_usd"] + lane_map_usd, 6)
                    if not lane_priced:
                        bucket["unpriced_calls"] += calls
                    entry = (bucket["models"].setdefault(provider, {})
                             .setdefault(model, {
                                 "calls": 0, "prompt_tokens": 0,
                                 "completion_tokens": 0,
                                 "provider_reported_usd": 0.0,
                                 "map_priced_usd": 0.0, "priced": True,
                                 # The SAME non-additive pair appears at
                                 # this leaf, so the marker must too — the
                                 # `--out` JSON emits this shape verbatim.
                                 "overlap": True,
                                 "estimated": bool(
                                     (lookup_rate(provider, model) or {})
                                     .get("estimated", False))}))
                    # AND-merge: ``priced`` is per-LANE (it depends on the
                    # row's ``usage_present``), so a lane that is priced in
                    # one row and unpriced in another must end up UNPRICED —
                    # latching the first row's flag would print a
                    # priced-looking lane whose tokens partly were not.
                    entry["priced"] = bool(entry["priced"] and lane_priced)
                    entry["calls"] += calls
                    entry["prompt_tokens"] += ptoks
                    entry["completion_tokens"] += ctoks
                    entry["provider_reported_usd"] = round(
                        entry["provider_reported_usd"] + reported, 6)
                    entry["map_priced_usd"] = round(
                        entry["map_priced_usd"] + lane_map_usd, 6)
    # Drop stages that ended up with no usable lane (a poisoned/foreign
    # stage value must not surface as a phantom ``calls=0`` row).
    for stage in [s for s, b in stages.items() if not b["models"]]:
        del stages[stage]
    for bucket in stages.values():
        bucket["map_priced_usd"] = round(bucket["map_priced_usd"], 6)
        bucket["provider_reported_usd"] = round(
            bucket["provider_reported_usd"], 6)
        # Same non-additive pair as the distribution return: a stage's
        # provider total and its map valuation are two views of the SAME
        # tokens, so summing them manufactures phantom spend. Marked on
        # every bucket too, so a consumer reading `by_stage` directly cannot
        # miss what the top-level `overlap` marker already says.
        bucket["overlap"] = True
    return {"by_stage": stages, "map_version": PRICING_MAP_VERSION,
            "overlap": True}


def _valid_number(v) -> bool:
    """A real, finite, bounded number — a ``bool`` is NOT a number here.

    #5854: the shared predicate behind ``_price_lane._tok`` and its
    negative-token detection, so the two can never drift. ``abs(v) > 1e300``
    runs BEFORE ``isfinite`` because ``math.isfinite`` on a huge
    arbitrary-precision ``int`` raises ``OverflowError``.
    """
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return False
    if abs(v) > 1e300:
        return False
    return not (isinstance(v, float) and not math.isfinite(v))


def _price_lane(stage: str, provider: str, model: str, bucket: dict,
                entry: dict | None) -> dict:
    def _tok(key: str) -> int | float:
        """Bounded token read: poison in a tampered checkpoint bucket (non-
        finite / |v| > 1e300 / bool) is excluded, never converted — the lane
        degrades to unpriced instead of crashing report assembly (round-2
        code-review P2, mirroring report._numeric).

        #5854: a NEGATIVE is excluded too. A token count cannot be negative,
        and an accepted one feeds ``prompt * rate`` straight into the dollar
        total, so a stored/replayed row written before the accumulator's
        token normaliser landed would sign-flip a session cost negative while
        the reader still reported the session ``fully_priced``. This is the
        same poison-tolerance contract, extended to the one sign the reader
        still let through.
        """
        v = bucket.get(key, 0) or 0
        if not _valid_number(v) or v < 0:
            return 0
        return v

    prompt = _tok("prompt_tokens")
    completion = _tok("completion_tokens")
    hit = _tok(_CACHE_KEY)
    usage_present = bucket.get("usage_present", True)
    # #5854: a negative token must not read as a silent $0 either. It is
    # poison the accumulator would now reject, but a row stored before the
    # fix still carries it, so the lane is marked UNPRICED (disclosed) rather
    # than priced at 0 — a missing measurement can never read as a cheap one.
    negative_token = any(
        _valid_number(bucket.get(k)) and bucket.get(k, 0) < 0
        for k in ("prompt_tokens", "completion_tokens", _CACHE_KEY))
    row: dict = {
        "stage": stage, "provider": provider, "model": model,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "calls": bucket.get("calls", 0), "usd": 0.0,
        "priced": True, "estimated": False,
        "usage_present": usage_present,
        "cache_discount_applied": False, "cache_discount_unpriced": False,
    }
    calls_without_usage = bucket.get("calls_without_usage", 0) or 0
    if isinstance(calls_without_usage, bool) \
            or not isinstance(calls_without_usage, (int, float)) or abs(calls_without_usage) > 1e300 or (isinstance(calls_without_usage, float)
          and not math.isfinite(calls_without_usage)):
        calls_without_usage = 0
    row["calls_without_usage"] = int(calls_without_usage)
    calls_without_tokens = bucket.get("calls_without_tokens", 0) or 0
    if isinstance(calls_without_tokens, bool) \
            or not isinstance(calls_without_tokens, (int, float)) \
            or abs(calls_without_tokens) > 1e300 or (isinstance(calls_without_tokens, float)
          and not math.isfinite(calls_without_tokens)):
        calls_without_tokens = 0
    row["calls_without_tokens"] = int(calls_without_tokens)
    if entry is None:
        row.update(priced=False,
                   reason="unknown_model" if usage_present
                   else "usage_present_false")
        return row
    if not usage_present:
        row.update(priced=False, reason="usage_present_false")
        return row
    if negative_token:
        # #5854: disclosed, never priced — see the detection above.
        row.update(priced=False, reason="negative_token")
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
