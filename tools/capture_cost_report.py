#!/usr/bin/env python3
"""#3359 — the queryable per-session LLM cost report (p50 / p95 USD/session).

This is the READ side of the #3359 measurement. The write side is the
hosted capture path, which emits one ``capture_cost`` row per capture
ATTEMPT that ran an extraction (successful **or** errored — a failed
extraction that made provider calls has real spend), into
``analytics_events`` (``tortoise/hosted_api.py``:

    ``_capture_cost_props`` -> ``_track_analytics_event``).

M2 captures (#3824) DO make real provider calls — they simply discard the
usage block — so they emit a row carrying ``unattributed`` with every
measured field zeroed; the report counts those calls into
``unmetered_attempts`` instead of reading the session as an unmeasured or
$0 one.

    row.properties = {
        session_id, harness,    # #3843: which harness captured it
        calls, retries, prompt_tokens, completion_tokens,
        cost_usd,             # the provider's OWN reported charge
        calls_without_cost,   # calls the provider served without a charge
        calls_without_usage,  # calls that carried no usage block at all
        deadline_aborts,      # billed upstream, unpriceable here
        unattributed,         # #3824: calls made, no roll-up survived
        route_cost_overflows, # #5868: OVERFLOW EVENTS — a charge or a
                              # by_stage sub-total the breakdown could not
                              # represent, so by_stage is not a complete
                              # partition of the row and may not reconcile
                              # with cost_usd in either direction
        by_stage: {stage: {provider: {model: {calls, prompt_tokens,
                    completion_tokens, cost_usd, usage_present,
                    calls_without_cost, calls_without_usage}}}},
    }

The percentiles are computed by ``tools/longmem_eval/costing.py``
(``cost_per_session_distribution`` / ``cost_by_stage``) against the
versioned ``PRICING_MAP`` — the same machinery #2185 built for the eval
harness, reused rather than forked. Nothing here prices from a second
table, and nothing here is on the billing path: the customer-visible unit
stays ``write_ops``.

#3840 (lane B7 — the metric set). The report also renders the extraction
TOKEN figures the issue's metric list names: input, output and **total**
tokens (the total DERIVED — ``prompt + completion`` — never stored as a
fourth counter that could drift from its own parts), per window, per stage
and per team; plus tokens per CAPTURE at the **median and 95th percentile**
(a mean is dominated by outliers), measured over the SAME included sessions
as the ``$`` percentiles so the two reads can never describe different
captures. Three disclosures ride the token block, because a token count we
could not see must never read as a tiny extraction: ``partial_captures`` (a
call happened whose tokens we could not see), ``tokenless_captures`` (calls
were metered yet produced no usable token count — absent, zero or rejected; it
overlaps ``invalid_token_captures``) and
``invalid_token_captures`` (a negative or non-integral stored count, rejected
rather than shaped — #5854's rule on the reader side). This is calibration
only — no ceiling and no policy; the metric set itself is defined in
``docs/ops/extraction-cost-metrics.md``.

Usage
-----
Live (needs ``SUPABASE_URL`` + a service key of either name —
``SUPABASE_SERVICE_ROLE_KEY`` on the hosted deployment, the legacy
``SUPABASE_SERVICE_KEY`` elsewhere; service role, since ``analytics_events``
RLS denies customer reads)::

    uv run python tools/capture_cost_report.py --days 7 --top 10
    uv run python tools/capture_cost_report.py --days 7 --out /tmp/cost.json

Offline (the hosted JSONL fallback ``~/.tortoise/analytics_fallback.jsonl``,
an exported rows file, or a JSON array)::

    uv run python tools/capture_cost_report.py --jsonl ~/.tortoise/analytics_fallback.jsonl
    uv run python tools/capture_cost_report.py --json /tmp/capture_cost_rows.json

Exit codes: ``0`` report produced (including an empty window — an empty
window prints the loud "no measurement yet" notice, it is NOT a pass with
numbers); ``2`` unusable input (bad file, unreadable JSON, missing
credentials for a live pull), or a live pull the walk could not CERTIFY
complete (#8151: a refusal, never a silently truncated window).

#3843 (lane B7, owner-decided). The owner's per-capture record shape is
*team · session · timestamp · harness · model(s) · calls · input tokens ·
output tokens · cost*, read at two levels: team and capture. ``harness`` is
now written on every row (``"unknown"`` when the server could not name one);
this reader reports the window's harness set in its header and payload and
adds the TEAM-level view (``per-team split`` / ``by_team``) that the decision
exists for — per team: rows, priced sessions, spend, calls, tokens and the
harnesses captured through. The spend per group comes from the SAME
``costing`` repricing the launch-gate number uses, never a second sum of
``cost_usd``.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/capture_cost_report.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/capture_cost_report.py`"
    )

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import costing

_EVENT_NAME = "capture_cost"


def _now() -> datetime:
    """TZ-aware UTC now."""
    return datetime.now(UTC)


def _iso_days_ago(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def load_rows_from_jsonl(path: str) -> list[dict]:
    """Read the hosted JSONL fallback / an exported rows file."""
    rows: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"{path}:{line_no}: not valid JSON ({exc.msg})") from exc
    return rows


def load_rows_from_json(path: str) -> list[dict]:
    """Read a JSON array of rows (a dumped ``analytics_events`` export)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("data") or []
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a JSON array of rows")
    return data


def fetch_rows(days: int, *, timeout: float = 30.0) -> list[dict]:
    """Pull ``capture_cost`` rows from Supabase (service role).

    The same REST surface ``_track_analytics_event`` writes to, walked with
    ``SupabaseControlPlane.query_all`` so a chatty beta cannot silently
    truncate the window — a truncated window would understate p95, the number
    this report exists for.

    #8151: this function used to hand-roll its own ``Range`` paging and end the
    walk on a page SHORTER than the size it asked for. That is not the end of
    the data — it is the deployment's per-request cap (PostgREST's
    ``db-max-rows``, or a proxy in front of it), so on any deployment tuned
    below 1000 rows the reported distribution was computed over PAGE ONE with
    no error and no signal. The same fail-OPEN #5388 removed from
    ``hosted_api._iter_registered_orgs``; the fix never reached this caller.

    So the walk is not re-implemented here — it is the seam #4038 built and
    #5388's doctrine defines, adopted rather than mirrored (a second copy of
    that rule is exactly how the two drifted apart):

    * **keyset on ``id``** (unique + immutable uuid PK, and this table is
      append-only by trigger) — ``offset``/``Range`` paging walks a MOVING
      window, so a capture written mid-walk shifts every later page and one
      row is served twice while another is skipped;
    * **an EMPTY page is the only end-of-walk signal** — a short page continues
      the walk (the defect this issue is);
    * **the server's own total** (``Content-Range`` via ``Prefer: count=exact``
      on page 1) may only DOWNGRADE the walk, never promote it — the total is a
      page-1 snapshot that concurrent inserts pass.

    ``complete`` is therefore a certificate, and it is ENFORCED here: a walk
    that could not reach an empty page, or that met fewer rows than the store
    stated, RAISES (exit 2) instead of returning a lower bound. A p95 over a
    partial window is precisely the silent wrong number this tool must never
    publish, so a refusal is the honest answer.

    #3677: the service key comes from the ONE resolution seam
    (``supabase_control._service_key()``) — the hosted deployment sets only
    ``SUPABASE_SERVICE_ROLE_KEY``. A legacy-name-only lookup would have
    refused to pull from any deployment that supplied only the canonical
    name. That made this the SECOND half of one defect: the writer would have
    dropped the rows and this reader would then have refused to fetch them, so
    the per-session cost evidence (#3359) needed both halves repaired, not
    just the writer.
    """
    # Function-local so this tool stays importable without the SDK.
    from tortoise.supabase_control import SupabaseControlPlane, _service_key

    url = os.environ.get("SUPABASE_URL")
    key = _service_key() or None
    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and a service key (SUPABASE_SERVICE_ROLE_KEY or the "
            "legacy SUPABASE_SERVICE_KEY) must both be set for a live pull — "
            "or pass --jsonl/--json for an offline report")

    since = _iso_days_ago(days)
    # Filters are passed to the seam (never interpolated into a query string):
    # the ISO timestamp's ``+00:00`` sent raw would be decoded as a SPACE by
    # form-encoding rules, silently corrupting the window.
    #
    # The client is constructed HERE rather than taken from
    # ``get_control_plane()``: this is a one-shot CLI, and the process-wide
    # singleton would bind a report run to whatever env/timeout first created
    # it (and leak that across callers in the same process). The plane's
    # persistent httpx client is NOT closed here — ``SupabaseControlPlane``
    # exposes no ``close()`` (filed as #9139); the process exits after the
    # report, so the pool is released with it.
    plane = SupabaseControlPlane(url=url, service_key=key, timeout=timeout)
    try:
        rows, complete = plane.query_all(
            "analytics_events",
            # ``id`` is the walk's cursor: query_all reads the next page's bound
            # from the last row it saw, so the column must be selected (and the
            # other three are what the report reads).
            select=["id", "org_id", "properties", "created_at"],
            filters=[("event_name", "eq", _EVENT_NAME),
                     ("created_at", "gte", since)],
            timeout=timeout,
        )
    except RuntimeError as exc:
        # The tool's PRIMARY documented path is this live pull, so a
        # transport/status failure (expired service key, egress, a malformed
        # SUPABASE_URL) must honour the documented exit-2 contract, not
        # traceback. The seam fails CLOSED by raising; this re-labels it.
        raise ValueError(f"live pull failed: {exc}") from exc
    if not complete:
        raise ValueError(
            f"live pull could not be certified complete over the last {days}d "
            "window — the paginated walk did not reach an empty page, or the "
            "store served fewer rows than the total it stated. Refusing to "
            "report a p95 over a partial window (it would be a lower bound "
            "presented as the measurement); narrow --days to a window the walk "
            "can certify.")
    return rows


def _harness_ids(rows: list[dict]) -> list[str]:
    """#3843: the harnesses the window's per-capture records actually name.

    The DECIDED per-capture record shape is *team · session · timestamp ·
    harness · model(s) · calls · input tokens · output tokens · cost* (owner,
    2026-09-17); ``harness`` is the element that had no carrier on the row
    until now, so it is surfaced here rather than left to a hand-written query
    (the #4493 ``tortoise_team_cost_cents`` lesson: a recorded dimension with
    no reader reads as a measurement that does not exist).

    Rows written before the harness was carried — and any row from a
    deployment that predates this — carry NO ``harness`` key. That is a THIRD
    fact, not the same one as ``"unknown"`` (a capture the server could not
    name because no agent credential made it, #3700), so it is not folded
    into it: a missing key is simply not listed.
    """
    seen: set[str] = set()
    for row in rows:
        props = costing._row_props(row) or {}
        value = props.get("harness")
        if isinstance(value, str) and value.strip():
            seen.add(value.strip())
    return sorted(seen)


def team_rollup(rows: list[dict]) -> list[dict]:
    """#3843: the per-capture record read at TEAM level, heaviest first.

    One entry per ``org_id``: rows, priced sessions, spend, measured calls and
    tokens, and the harnesses that team captured through. This is the read the
    owner's record exists for — "this team's usage is climbing", "how fast",
    and *which* of a struggling team's harnesses the spend sits on — as
    opposed to the capture-level drill-down the rest of this report already
    serves (``heaviest sessions``).

    The spend is taken from ``costing.cost_per_session_distribution`` per
    group — the SAME repricing the launch-gate number uses — rather than
    re-summing ``cost_usd`` here: two pricings of the same row is how a second
    cost basis appears. A group is priced over ITS OWN sessions, so a team's
    ``priced_sessions`` is that team's, not a share of the global figure.
    """
    groups: dict[str, list[dict]] = {}
    for row in rows:
        raw = row.get("org_id") if isinstance(row, dict) else None
        team = raw.strip() if isinstance(raw, str) and raw.strip() else "<no team>"
        groups.setdefault(team, []).append(row)
    out: list[dict] = []
    for team, group in groups.items():
        dist = costing.cost_per_session_distribution(group)
        props = [costing._row_props(r) or {} for r in group]
        out.append({
            "team": team,
            "rows": dist["n_rows"],
            "sessions": dist["n"],
            "priced_sessions": dist["priced_sessions"],
            "total_usd": dist["total_usd"],
            "fully_priced": dist["fully_priced"],
            "calls": sum(costing._as_int(p.get("calls")) for p in props),
            # #3840: the team's token figures come from the SAME repriced
            # distribution the team's spend does, so a team's ``total_usd``
            # and its ``total_tokens`` describe ONE set of captures. Summing
            # the raw rows here instead would count captures the percentile
            # excludes as ``excluded_no_calls`` / ``excluded_unmeasured`` —
            # the window total (``distribution.tokens.total``) and the sum of
            # the per-team totals would then disagree, and two token totals
            # that will not reconcile is the #5868 defect in a new place.
            "prompt_tokens": dist["tokens"]["prompt"],
            "completion_tokens": dist["tokens"]["completion"],
            # the single figure comparable across providers and models,
            # derived from the two measured halves rather than stored as a
            # fourth counter that can drift from its own parts
            "total_tokens": dist["tokens"]["total"],
            "harnesses": _harness_ids(group),
        })
    # Heaviest first, team id breaking ties: the report's whole posture is that
    # the number which needs a look is the one on top, and a stable order keeps
    # two runs over the same window diffable.
    out.sort(key=lambda e: (-float(e["total_usd"] or 0.0), e["team"]))
    return out


def _fmt_usd(value: float) -> str:
    return f"${value:.6f}"


def _model_ids(rows: list[dict]) -> list[str]:
    models: set[str] = set()
    for row in rows:
        props = costing._row_props(row) or {}
        by_stage = props.get("by_stage") or {}
        if not isinstance(by_stage, dict):
            continue
        for providers in by_stage.values():
            if not isinstance(providers, dict):
                continue
            for models_by_provider in providers.values():
                if not isinstance(models_by_provider, dict):
                    continue
                for model in models_by_provider:
                    models.add(str(model))
    return sorted(models)


def _window(rows: list[dict]) -> tuple[str, str]:
    # Coerced to str: an export may carry epoch ints or structured stamps,
    # and comparing mixed types raises TypeError (killing the report).
    stamps = [str(r.get("created_at")) for r in rows
              if isinstance(r, dict) and r.get("created_at") is not None]
    if not stamps:
        return ("unknown", "unknown")
    return (min(stamps), max(stamps))


def _overflow_disclosure_lines(count: int, indent: str = "  ") -> list[str]:
    """#5868: the operator-facing disclosure for an unrepresentable
    ``by_stage`` sub-total.

    Emitted ONLY when the window actually carries one — an unconditional
    "the breakdown is short of cost_usd" reads as a false alarm on every
    healthy report. It also names no DIRECTION: a dropped bucket sub-total
    makes the breakdown an incomplete partition of the row, but the gap is
    not guaranteed to run either way (when the session sum overflowed too,
    the two totals can still agree), so the line states only what is true of
    every row that carries the counter.

    Its closing pointer is to the ``calls served without a charge`` line,
    which BOTH callers print — keep that line in any new branch that renders
    this disclosure, or the pointer dangles.
    """
    if not count:
        return []
    return [
        f"{indent}by_stage sub-totals unrepresentable: {count}"
        " (overflow events, not charges)",
        f"{indent}  — the by_stage breakdown is not a complete partition of",
        f"{indent}    the row, so it may not reconcile with cost_usd in",
        f"{indent}    either direction. This is NOT a statement about",
        f"{indent}    cost_usd itself (see the calls-without-a-charge line",
        f"{indent}    for the total's own completeness).",
    ]


def _token_section(tokens: dict) -> list[str]:
    """#3840: the extraction-cost metric set's TOKEN section.

    Rendered on BOTH paths that can carry token evidence — the priced window
    and the "rows exist but nothing could be priced" branch — because those
    tokens are measured even when the pricing map cannot put a dollar figure
    on them, and a calibration figure that only appears once the map is
    complete is invisible exactly when the map is what is missing.

    Kept out of the `$` launch-gate block deliberately: tokens are comparable
    across providers and models, dollars are not once the model changes, and
    the issue's scope is calibration — no ceiling, no policy.
    """
    lines = [
        "── extraction tokens (#3840: internal calibration, no ceiling) ──",
        f"  captures counted : {tokens['sessions']}",
        f"  input tokens     : {tokens['prompt']}",
        f"  output tokens    : {tokens['completion']}",
        f"  total tokens     : {tokens['total']}",
        f"  p50 tokens/capture : {tokens['p50']:.1f}",
        f"  p95 tokens/capture : {tokens['p95']:.1f}",
        f"  max tokens/capture : {tokens['max']}",
    ]
    if tokens["lower_bound"]:
        # A mean would have hidden this; the median/p95 pair must not. Every
        # cause is named, because "some captures are a floor" is not
        # actionable while "three had metered calls carrying no token field"
        # is.
        causes = [
            f"{tokens['partial_captures']} capture(s) had a call whose "
            "tokens we could not see",
            # "no usable token count", NOT "no token field": a capture whose
            # field was present but rejected is counted here AND in
            # ``invalid_token_captures``, and two causes that contradict each
            # other about the same capture would make the warning unreadable.
            f"{tokens['tokenless_captures']} had metered calls carrying "
            "no usable token count",
        ]
        if tokens["invalid_token_captures"]:
            causes.append(
                f"{tokens['invalid_token_captures']} carried a token count "
                "that is not a count (rejected, not shaped)")
        lines.append("  ⚠ token counts are a LOWER BOUND — "
                     + ", ".join(causes))
    return lines


def render(rows: list[dict], *, top: int, since_label: str) -> str:
    """The human/CI-readable report. Returns the text; caller prints it."""
    dist = costing.cost_per_session_distribution(rows)
    stages = costing.cost_by_stage(rows)
    first, last = _window(rows)
    lines: list[str] = []
    add = lines.append
    add("=" * 72)
    add("#3359 — per-session LLM cost (measured, hosted capture path)")
    add("=" * 72)
    add(f"window requested : last {since_label}")
    add(f"rows observed    : {dist['n_rows']}")
    add(f"first / last row : {first}  →  {last}")
    add(f"pricing map      : {dist['map_version']}")
    models = _model_ids(rows)
    add(f"model ids        : {', '.join(models) if models else '(none)'}")
    harnesses = _harness_ids(rows)
    add(f"harness ids      : {', '.join(harnesses) if harnesses else '(none)'}")
    add("")
    if dist["n_rows"] == 0:
        add("NO capture_cost ROWS IN THIS WINDOW — no measurement exists yet.")
        add("This is NOT a pass: the launch-gate number cannot be computed.")
        add("(Check that the hosted capture path is deployed and emitting.)")
        return "\n".join(lines)
    if dist["n"] == 0 or dist["priced_sessions"] == 0:
        # Rows exist but NOTHING was actually priced (empty captures, all
        # deadline-killed, all usage-less, or every lane on an unmapped
        # model). Printing $0.000000 here would fail OPEN against the A18
        # launch gate — the exact "a missing measurement reads as a cheap
        # one" failure mode this design exists to prevent.
        add("NO PRICED capture_cost SESSIONS — the launch-gate number")
        add("CANNOT be computed. This is NOT a pass.")
        add(f"  rows observed                     : {dist['n_rows']}")
        add(f"  sessions counted (n)              : {dist['n']}")
        add(f"  of which priced                   : {dist['priced_sessions']}")
        add(f"  tokens we could not price         : {dist['unpriced_sessions']}")
        add(f"  excluded, no calls at all         : {dist['excluded_no_calls']}")
        add(f"  excluded, calls but unmetered     : {dist['excluded_unmeasured']}")
        add(f"  deadline-killed (billed, no toks) : {dist['deadline_aborts']}")
        add(f"  calls with no surviving roll-up   : {dist['unattributed_calls']}")
        add(f"  captures behind those calls       : {dist['unattributed_captures']}")
        # the referent the disclosure line points at must EXIST on this branch
        # too — a pointer to a line this path never prints is worse than no
        # pointer, and this is the path where the reader has least else to go on
        add(f"  calls served without a charge     : {dist['calls_without_cost']}")
        lines.extend(_overflow_disclosure_lines(dist["route_cost_overflows"]))
        add("")
        lines.extend(_token_section(dist["tokens"]))
        add("  (check that captures are actually running extraction, that the")
        add("   hosted emit path is deployed, and that the serving model ids")
        add("   have a row in the versioned PRICING_MAP)")
        return "\n".join(lines)
    add("── the launch-gate number ──────────────────────────────────────")
    add(f"  sessions counted : n={dist['n']}  (of {dist['n_rows']} rows)")
    add(f"  of which priced  : {dist['priced_sessions']}")
    add(f"  p50 $/session   : {_fmt_usd(dist['p50'])}")
    add(f"  p95 $/session   : {_fmt_usd(dist['p95'])}")
    add(f"  max $/session   : {_fmt_usd(dist['max'])}")
    add(f"  total           : {_fmt_usd(dist['total_usd'])}")
    add(f"  cost source     : {dist['source']} "
        f"(provider-reported {_fmt_usd(dist['provider_reported_usd'])} / "
        f"map-priced {_fmt_usd(dist['map_priced_usd'])} — "
        "OVERLAPPING diagnostics, NOT additive)")
    if not dist["fully_priced"]:
        # The percentile still includes unpriced sessions at their
        # provider-reported lower bound (a deliberate design choice), but a
        # reader must not take the number as fully measured.
        add("")
        if dist["unmetered_attempts"]:
            add(f"  ⚠ {dist['unmetered_attempts']} attempt(s) produced no "
                "meterable response (possibly billed upstream) — treat the "
                "number as a LOWER BOUND.")
        if dist["unpriced_sessions"]:
            add(f"  ⚠ PARTIALLY PRICED — {dist['unpriced_sessions']} of "
                f"{dist['n']} counted sessions have lanes we could not "
                "price; their contribution is a LOWER BOUND.")
    add("")
    # #3840: the token figures ride this branch too — see ``_token_section``.
    lines.extend(_token_section(dist["tokens"]))
    add("")
    add("── honesty / disclosure counters ───────────────────────────────")
    add(f"  excluded, no calls at all      : {dist['excluded_no_calls']}")
    add(f"  excluded, calls but unmetered  : {dist['excluded_unmeasured']}")
    add(f"  deadline-killed (billed, no toks): {dist['deadline_aborts']}")
    add(f"  calls with no surviving roll-up   : {dist['unattributed_calls']} "
        f"(across {dist['unattributed_captures']} capture(s)) — counted in "
        "the attempts line below, not additional to it")
    add(f"  calls served without a charge  : {dist['calls_without_cost']}")
    lines.extend(_overflow_disclosure_lines(dist["route_cost_overflows"],
                                            indent="    "))
    add(f"  attempts with no meterable reply: {dist['unmetered_attempts']}")
    add(f"  sessions tokens we could not price: {dist['unpriced_sessions']}")
    add("")
    add("── per-stage split (is the cheap point model working?) ─────────")
    by_stage = stages.get("by_stage") or {}
    if not by_stage:
        add("  (no per-stage lanes recorded)")
    for stage in sorted(by_stage):
        bucket = by_stage[stage]
        add(f"  {stage}: calls={bucket['calls']} "
            f"in={bucket['prompt_tokens']} out={bucket['completion_tokens']} "
            f"total={bucket['prompt_tokens'] + bucket['completion_tokens']} "
            f"reported={_fmt_usd(bucket['provider_reported_usd'])} "
            f"mapped={_fmt_usd(bucket['map_priced_usd'])} [NOT additive]"
            + (f" UNPRICED_CALLS={bucket['unpriced_calls']}"
               if bucket["unpriced_calls"] else ""))
        for provider in sorted(bucket["models"]):
            for model in sorted(bucket["models"][provider]):
                lane = bucket["models"][provider][model]
                flag = "" if lane["priced"] else "  [UNPRICED]"
                if lane["estimated"]:
                    flag += "  [ESTIMATED RATE]"
                add(f"      {provider} / {model}: calls={lane['calls']} "
                    f"in={lane['prompt_tokens']} out={lane['completion_tokens']}"
                    f" total={lane['prompt_tokens'] + lane['completion_tokens']}"
                    f" reported={_fmt_usd(lane['provider_reported_usd'])}"
                    f" mapped={_fmt_usd(lane['map_priced_usd'])}"
                    f" [NOT additive]{flag}")
    add("")
    add("── per-team split (#3843: team · harness · spend) ──────────────")
    teams = team_rollup(rows)
    if not teams:
        add("  (no teams in this window)")
    for entry in teams[:top]:
        named = ", ".join(entry["harnesses"]) if entry["harnesses"] else "(none)"
        add(f"  {entry['team']}: rows={entry['rows']} "
            f"priced={entry['priced_sessions']}/{entry['sessions']} "
            f"calls={entry['calls']} "
            f"in={entry['prompt_tokens']} out={entry['completion_tokens']} "
            # ``tokens=``, not ``total=``: on this line ``total=`` is the
            # team's USD spend, and a second meaning for the same label on
            # one line is how a reader swaps tokens for dollars.
            f"tokens={entry['total_tokens']} "
            f"total={_fmt_usd(entry['total_usd'])} harness={named}"
            + ("" if entry["fully_priced"] else "  [PARTIALLY PRICED]"))
    if len(teams) > top:
        add(f"  … {len(teams) - top} more team(s) — raise --top, or read "
            "`by_team` from --out")
    add("")
    add(f"── {top} heaviest sessions ──────────────────────────────────────")
    for entry in dist["heaviest"][:top]:
        # #3840: ``tok=`` is the capture's TOTAL tokens — the same "this
        # capture was unusually expensive" drill-down, read in the unit that
        # survives a model change.
        add(f"  {_fmt_usd(entry['cost_usd'])}  "
            f"{entry['session_id']}  calls={entry['calls']}/"
            f"{entry['measured_calls']}m  tok={entry['total_tokens']}  "
            f"src={entry['source']}")
    if not dist["heaviest"]:
        add("  (none)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="#3359 measured per-session LLM cost report")
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--jsonl", help="read rows from a JSONL file")
    src.add_argument("--json", help="read rows from a JSON array file")
    parser.add_argument("--days", type=int, default=7,
                        help="live-pull window in days (default 7)")
    parser.add_argument("--top", type=int, default=10,
                        help="how many heaviest sessions AND teams to list "
                             "(default 10)")
    parser.add_argument("--out", help="also write the raw report as JSON")
    args = parser.parse_args(argv)

    try:
        if args.jsonl:
            rows = load_rows_from_jsonl(args.jsonl)
            label = f"(jsonl {args.jsonl})"
        elif args.json:
            rows = load_rows_from_json(args.json)
            label = f"(json {args.json})"
        else:
            rows = fetch_rows(args.days)
            label = f"{args.days}d (live)"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    rows = [r for r in rows
            if not (isinstance(r, dict) and r.get("event_name")
                    and r.get("event_name") != _EVENT_NAME)]

    distribution = costing.cost_per_session_distribution(rows)
    print(render(rows, top=args.top, since_label=label))

    if args.out:
        payload = {
            "generated_at": _now().isoformat(),
            "window": label,
            "rows": len(rows),
            "model_ids": _model_ids(rows),
            # #3843: the harness dimension of the per-capture record, plus the
            # team-level read. Both are additive to the launch-gate payload so
            # the decided record is readable without walking raw rows.
            "harness_ids": _harness_ids(rows),
            "by_team": team_rollup(rows),
            # Explicit, so a mechanical launch gate can never read
            # ``p50 == 0.0`` as a pass. Keyed on the PRICED-session count,
            # not merely on how many sessions were counted: an all-unpriced
            # window has n > 0 but no number anyone can gate on.
            "priced_sessions": distribution["priced_sessions"],
            "fully_priced": distribution["fully_priced"],
            "measurable": bool(distribution["priced_sessions"]),
            "distribution": distribution,
            "by_stage": costing.cost_by_stage(rows),
        }
        Path(args.out).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
