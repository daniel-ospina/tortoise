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
        session_id, calls, retries, prompt_tokens, completion_tokens,
        cost_usd,             # the provider's OWN reported charge
        calls_without_cost,   # calls the provider served without a charge
        calls_without_usage,  # calls that carried no usage block at all
        deadline_aborts,      # billed upstream, unpriceable here
        unattributed,         # #3824: calls made, no roll-up survived
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
credentials for a live pull).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
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

    The same REST surface ``_track_analytics_event`` writes to. Paginated
    with ``Range`` so a chatty beta does not silently truncate the window
    (Supabase caps a single response; a truncated window would understate
    p95 — the number the whole issue is about).

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
    from tortoise.supabase_control import _service_key

    url = os.environ.get("SUPABASE_URL")
    key = _service_key() or None
    if not url or not key:
        raise ValueError(
            "SUPABASE_URL and a service key (SUPABASE_SERVICE_ROLE_KEY or the "
            "legacy SUPABASE_SERVICE_KEY) must both be set for a live pull — "
            "or pass --jsonl/--json for an offline report")
    import httpx

    since = _iso_days_ago(days)
    # Filters go through httpx ``params`` so they are percent-encoded: the
    # ISO timestamp's ``+00:00`` sent as a raw query string would be decoded
    # as a SPACE by form-encoding rules, silently corrupting the window.
    params = {
        "event_name": f"eq.{_EVENT_NAME}",
        "created_at": f"gte.{since}",
        "select": "org_id,properties,created_at",
        "order": "created_at.desc",
    }
    endpoint = f"{url}/rest/v1/analytics_events"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    rows: list[dict] = []
    page = 1000
    with httpx.Client(timeout=timeout) as client:
        start = 0
        while True:
            try:
                resp = client.get(
                    endpoint,
                    params=params,
                    headers={**headers, "Range-Unit": "items",
                             "Range": f"{start}-{start + page - 1}"},
                )
                resp.raise_for_status()
                batch = resp.json()
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                # The tool's PRIMARY documented path is this live pull, so a
                # transport/status failure (expired service key, egress) — or
                # a malformed SUPABASE_URL, which raises ``InvalidURL`` and is
                # NOT an ``HTTPError`` — must honour the documented exit-2
                # contract, not traceback.
                raise ValueError(f"live pull failed: {exc}") from exc
            if not isinstance(batch, list):
                raise ValueError(
                    "unexpected Supabase response (expected a JSON array)")
            rows.extend(batch)
            if len(batch) < page:
                return rows
            start += page


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
    add("── honesty / disclosure counters ───────────────────────────────")
    add(f"  excluded, no calls at all      : {dist['excluded_no_calls']}")
    add(f"  excluded, calls but unmetered  : {dist['excluded_unmeasured']}")
    add(f"  deadline-killed (billed, no toks): {dist['deadline_aborts']}")
    add(f"  calls with no surviving roll-up   : {dist['unattributed_calls']} "
        f"(across {dist['unattributed_captures']} capture(s)) — counted in "
        "the attempts line below, not additional to it")
    add(f"  calls served without a charge  : {dist['calls_without_cost']}")
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
                    f" reported={_fmt_usd(lane['provider_reported_usd'])}"
                    f" mapped={_fmt_usd(lane['map_priced_usd'])}"
                    f" [NOT additive]{flag}")
    add("")
    add(f"── {top} heaviest sessions ──────────────────────────────────────")
    for entry in dist["heaviest"][:top]:
        add(f"  {_fmt_usd(entry['cost_usd'])}  "
            f"{entry['session_id']}  calls={entry['calls']}/"
            f"{entry['measured_calls']}m  src={entry['source']}")
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
                        help="how many heaviest sessions to list (default 10)")
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
