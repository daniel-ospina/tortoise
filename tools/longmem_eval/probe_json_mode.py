#!/usr/bin/env python3
"""#1746 (D6): JSON-mode honor probe for the extractor's LLM path.

Tests whether the ACTIVE provider/model actually honors ``response_format``
``json_object`` for pennies BEFORE trusting the lever in a full run (H1 —
the pilot's direct path ran JSON-mode-free because
``DeepSeekDirectModel.complete`` never sent the field; the lever was
UNTESTED). Verdict ∈ {honored, ignored, rejected, inconclusive} per the
plan's D6 rules; the verdict JSON (``--out``) is consumed by the closing-run
record (Task 5) so criterion 1 is interpreted against the actual
configuration.

Probe-verdict → run-mode mapping (operational, D6):
- ``rejected`` (any HTTP 400/404) → the closing run aborts pre-flight OR
  re-runs with ``TORTOISE_JSON_MODE=0`` — never a wholesale-400 mid-run.
- ``inconclusive`` (n too small / transient errors / both-zero) → re-probe
  at ``--n 20`` and make an explicit mode decision for the run record.
- ``honored`` / ``ignored`` → proceed with the verdict noted.

Adapter selection (H1 validity): the probe exercises the PILOT's path — the
same ``build_extractor_model`` resolution the eval run uses
(``DeepSeekDirectModel`` when ``DEEPSEEK_API_KEY`` is set and
``TORTOISE_EXTRACTOR_PROVIDER != "openrouter"``, else the resolved default)
— so the verdict tests H1, not a different route.

Usage:
    python tools/longmem_eval/probe_json_mode.py --n 10 [--model deepseek/deepseek-v4-flash] [--out /tmp/probe.json] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

#: S2-shaped probe prompt — same shape class as the real S2 prompt (JSON
#: object + example + "JSON" token, satisfying DeepSeek's json-mode
#: requirement); deliberately NOT the live template (this is a smoke probe,
#: zero prompt-text churn, D11).
_PROBE_PROMPT = (
    "You are the GRAPH MAPPER. Map the following story into the exact "
    "embed-list JSON object: "
    '{"entities": [{"name": str, "kind": str}], '
    '"events": [{"content": str, "eventKind": str}], '
    '"points": [{"content": str, "pointKind": "statement"}], '
    '"operators": [{"src": str, "dst": str, "op_type": "IMPL"}]}. '
    "Print ONLY the JSON object, no markdown fences, no commentary.\n"
    "STORY: we decided to migrate the EP tests to live points rather than "
    "change production semantics; the old approach was dropped."
)

_MAX_TOKENS = 8000  # mirror the S2/S4 stage cap (truncation is part of the signal)


def _parses(text: str) -> bool:
    """Strict canonical parse of one completion (fence-strip + first-brace
    json.loads — the pre-ladder consumer, so the probe measures the
    provider's RAW shape quality, uncontaminated by the recovery ladder)."""
    t = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    start = t.find("{")
    if start < 0:
        return False
    try:
        json.loads(t[start:])
        return True
    except json.JSONDecodeError:
        return False


def _run_mode(adapter, mode: str, n: int) -> dict:
    """Run ``n`` S2-shaped completions under one TORTOISE_JSON_MODE value;
    returns the per-mode statistics block (malformed rate + finish_reason
    distribution + transient/400-404 counts)."""
    os.environ["TORTOISE_JSON_MODE"] = mode
    malformed = 0
    parse_success = 0
    finish: dict[str, int] = {}
    transient = 0
    http_400_404 = 0
    for _ in range(n):
        try:
            text = adapter.complete(system=_PROBE_PROMPT, user="",
                                    max_tokens=_MAX_TOKENS)
        except Exception as e:
            st = getattr(getattr(e, "response", None), "status_code", None)
            if st in (400, 404):
                http_400_404 += 1
            else:
                transient += 1
            continue
        fr = getattr(adapter, "last_finish_reason", None) or "stop"
        finish[fr] = finish.get(fr, 0) + 1
        if _parses(text):
            parse_success += 1
        else:
            malformed += 1
    return {
        "n": n,
        "malformed": malformed,
        "malformed_rate": round(malformed / n, 4) if n else 0.0,
        "parse_success": parse_success,
        "finish_reason": finish,
        "transient": transient,
        "http_400_404": http_400_404,
    }


def verdict_for(on: dict, off: dict) -> tuple[str, str]:
    """D6 verdict rules. Returns (verdict, mode_delta):

    - ``rejected`` — any HTTP 400/404 in either mode (the provider REJECTS
      the field; a run must not ship into wholesale-400s).
    - ``inconclusive`` — transient errors (signal contaminated), or BOTH
      rates zero (n too small to distinguish an inert mode from a clean
      model — a false-honored would mislabel the H1 test).
    - ``honored`` — mode-on malformed-rate < mode-off AND ≥ 1 mode-on parse
      success (or mode-on 0 / mode-off > 0 — the same condition's extreme).
    - ``ignored`` — indistinguishable otherwise; a strictly WORSE mode-on
      rate still returns ``ignored`` but records ``mode_delta: "worse"`` so
      the harmful-direction signal is not lost (the ladder + C4 backstop
      it).
    """
    if on["http_400_404"] or off["http_400_404"]:
        return "rejected", "same"
    if on["transient"] or off["transient"]:
        return "inconclusive", "same"
    on_rate, off_rate = on["malformed_rate"], off["malformed_rate"]
    if on_rate == 0 and off_rate == 0:
        return "inconclusive", "same"
    if (on_rate < off_rate and on["parse_success"] > 0) or (
            on_rate == 0 and off_rate > 0):
        return "honored", "better"
    if on_rate > off_rate:
        return "ignored", "worse"
    return "ignored", "same"


class _ScriptedAdapter:
    """Deterministic adapter for ``--dry-run`` (unit-testable): WELL-FORMED
    JSON under TORTOISE_JSON_MODE=1, malformed under =0 — a scripted
    'honored' verdict that exercises the whole path with zero network."""

    provider = "dry-run-scripted"
    last_finish_reason = "stop"

    def complete(self, *, system: str, user: str,
                 max_tokens: int | None = None) -> str:
        if os.environ.get("TORTOISE_JSON_MODE", "1") == "1":
            return ('{"entities": [], "events": [], "operators": [], '
                    '"points": [{"content": "x", "pointKind": "statement"}]}')
        return "this is not json"


def probe_json_mode(adapter, *, n: int = 10, dry_run: bool = False) -> dict:
    """Run ``n`` S2-shaped completions per mode (on/off, same prompts),
    compute the malformed-rate + finish_reason distribution, and return the
    verdict dict (D6). ``dry_run=True`` substitutes the scripted adapter
    (no network). The TORTOISE_JSON_MODE env is restored on exit."""
    if dry_run:
        adapter = _ScriptedAdapter()
    prev = os.environ.get("TORTOISE_JSON_MODE")
    try:
        on = _run_mode(adapter, "1", n)
        off = _run_mode(adapter, "0", n)
    finally:
        if prev is None:
            os.environ.pop("TORTOISE_JSON_MODE", None)
        else:
            os.environ["TORTOISE_JSON_MODE"] = prev
    verdict, mode_delta = verdict_for(on, off)
    return {
        "verdict": verdict,
        "mode_delta": mode_delta,
        "adapter": getattr(adapter, "provider", type(adapter).__name__),
        "model": getattr(adapter, "id", getattr(adapter, "model_id", "?")),
        "effective_torto_json_mode": os.environ.get("TORTOISE_JSON_MODE", "1"),
        "probe_date": datetime.now(UTC).isoformat(),
        "mode_on": on,
        "mode_off": off,
    }


def resolve_probe_adapter(model_id: str | None):
    """D6 adapter selection: exercise the PILOT's path — the same
    ``build_extractor_model`` resolution the eval run uses (the direct
    DeepSeek adapter when DEEPSEEK_API_KEY is set and
    TORTOISE_EXTRACTOR_PROVIDER != 'openrouter', else the resolved
    default), so the verdict tests H1 and not a different route."""
    from tests.model_adapters import build_extractor_model
    return build_extractor_model(model_id)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Probe whether the active extractor provider/model "
                    "honors response_format json_object (H1, #1746 D6).")
    ap.add_argument("--n", type=int, default=10,
                    help="completions per mode (default 10)")
    ap.add_argument("--model", default=None,
                    help="model spec (default: TORTOISE_EXTRACT_MODEL)")
    ap.add_argument("--out", default=None,
                    help="write the verdict JSON to this path")
    ap.add_argument("--dry-run", action="store_true",
                    help="scripted adapter, no network (unit-testable)")
    args = ap.parse_args(argv)
    if args.n < 1:
        ap.error("--n must be >= 1")
    if args.dry_run:
        result = probe_json_mode(None, n=args.n, dry_run=True)
    else:
        adapter = resolve_probe_adapter(args.model)
        result = probe_json_mode(adapter, n=args.n)
    if args.out:
        Path(args.out).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
