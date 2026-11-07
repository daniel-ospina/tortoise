#!/usr/bin/env python3
"""Measure the S4 re-emit tax — the output-token cost of the legacy
complete-list contract vs the #1789 delta/gaps-only contract, on REAL
recorded geometry.

THE QUESTION THIS EXISTS FOR
----------------------------
`#1789` switched S4's prompt from "emitting the COMPLETE embed list" to a
delta contract (gap ADDITIONS + full-item CORRECTIONS only; unchanged S2
items are OMITTED and preserved by the `merge_embed_lists` union). The
structural change landed (PR #8041) but the issue stayed open for exactly
one reason, in its own words:

    "the honest remainder is a measurement, not code … run the S4-token
     measurement against a frozen baseline, then compare to ≥40 %."

The plan doc specified a fresh 50-question end-to-end run for that
(`docs/plans/2026-08-27-1789-s4-delta-contract.md`, Task 10). That needs a
key, a live graph and hours — and it measures the WHOLE pipeline, so a
change in S1/S2 could mask the S4 shape change. The tax is an
OUTPUT-CONTRACT property, so it can be measured directly and cheaply:
hold every input constant and vary only `delta`.

WHAT IT DOES (one session, two arms, one variable)
--------------------------------------------------
For each recorded session it replays `run_s4()` twice over the SAME real
inputs —

  * ``legacy`` runs with ``delta=False`` → the legacy re-emit template
    (`S4_TMPL` / `S4_TMPL_CORE_ONLY`), i.e. today's ``TORTOISE_S4_DELTA=0``;
  * ``delta``  runs with ``delta=True``  → `S4_TMPL_DELTA`, the shipped
    default.

— and reads the **provider's own** `usage.completion_tokens` for each call
(`_complete` records it in `stats["completion_tokens"]`). Nothing is
estimated, tokenised or modelled: the number is what the model actually
emitted.

The S3 "existing graph" block is reconstructed from the session's own S2
list, which is what production S4 sees (S2 has already been written when
S4 runs — see ``extract_session_v2``'s step order), so both arms get the
SAME search results and the comparison stays single-variable.

Composition is recorded alongside the tokens (`_s4_merge_stats` /
`_verbatim_reemissions`, the #2408 census quantities), so a reader can see
WHY the delta arm is smaller: how many items it omitted (the tax), how many
it added (gaps) and how many it corrected.

    # the measurement (needs DEEPSEEK_API_KEY; ~20 s and ~6k output tokens
    # per arm per session, so a 7-session A/B is cents, not dollars)
    DEEPSEEK_API_KEY=… uv run python tools/s4_reeemit_tax.py run \
        --receipt tests/eval/write_path/receipts/7947-arms-2026-10-18 \
        --out docs/evidence/1789-s4-reeemit-tax/ab-<utc>.json

    # zero-cost, no-network: both arms' PROMPT geometry (the input-side half,
    # and the CI pin that the two renders differ at all)
    uv run python tools/s4_reeemit_tax.py dry-run --receipt …

Exit codes: ``0`` measured (the report is written) · ``1`` usage/IO (a bad
flag, an unreadable receipt, a receipt with no embed list) · ``2`` refused
(no ``DEEPSEEK_API_KEY`` in the environment — a measurement that cannot
call the model is not a zero result).

⚠️ WHAT THIS DOES NOT MEASURE, stated here so the report cannot be read
wider than it is: accuracy / chunk@20 (the eval lane's gates), and model
COMPLIANCE in the wild (the delta arm here is one call per session, not a
production run). Non-compliance is nonetheless *observable* per row —
`verbatim_reemissions`, `added_by_s4`, `s4_delta_reemit_suspect` — so a
model that ignored the delta contract shows up in the artifact instead of
being assumed away.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tortoise import extractor_v2 as x  # noqa: E402

# The 4 content-bearing sections S4 can carry — the tax surface
# (#2408 I-2-7: chain_notes / link_before_create / retractions are tiny and
# carry different re-emit semantics, so they are outside the cost surface).
_TAX_SECTIONS = ("entities", "events", "points", "operators")
_DEFAULT_RECEIPT = "tests/eval/write_path/receipts/7947-arms-2026-10-18"
_ARM_CHOICES = ("legacy", "delta")


# ── receipt loading ────────────────────────────────────────────────────────


def load_sessions(receipt_dir: Path, *, embeds_arm: str = "narrative",
                  only: list[str] | None = None) -> list[dict]:
    """Read the recorded (story, S2 embed list) pairs out of the write-path
    eval receipts.

    A receipt is ``{"embed_list": {<arm>: <embed list>}, "stories": {<arm>:
    <S1 story>}, "s2_stats": {<arm>: …}}`` — the S1/S2 half of a real
    write-path run. `embeds_arm` picks which arm's output to replay (both
    arms of that eval are real S2 outputs; ``narrative`` is the production
    shape).

    Raises ValueError on a receipt that carries no usable embed list — a
    silently skipped session would shrink the sample without saying so.
    """
    paths = sorted(receipt_dir.glob("*.arms.json"))
    if not paths:
        raise ValueError(f"no *.arms.json receipts under {receipt_dir}")
    out: list[dict] = []
    for p in paths:
        name = p.name.split(".")[0]
        if only and name not in only:
            continue
        doc = json.loads(p.read_text())
        embeds = (doc.get("embed_list") or {}).get(embeds_arm)
        story = (doc.get("stories") or {}).get(embeds_arm)
        if not isinstance(embeds, dict) or not story:
            raise ValueError(
                f"{p} carries no '{embeds_arm}' embed list + story pair")
        s2_stats = (doc.get("s2_stats") or {}).get(embeds_arm) or {}
        out.append({
            "session": name,
            "receipt": str(p.relative_to(_REPO)) if p.is_relative_to(_REPO) else str(p),
            "story": story,
            "s2": {s: list(embeds.get(s) or []) for s in _TAX_SECTIONS},
            "s2_reference": {
                "completion_tokens": s2_stats.get("completion_tokens"),
                "prompt_tokens": s2_stats.get("prompt_tokens"),
            },
            "turns": doc.get("turns"),
            "session_date": doc.get("session_date"),
        })
    if not out:
        raise ValueError(f"no session matched {only!r} under {receipt_dir}")
    return out


def search_from_s2(s2: dict) -> dict:
    """The S3 "existing graph" block, reconstructed from the session's S2 list.

    Production runs S4 *after* S2's items reach the graph, so S4's search
    results are the S2 items themselves. Handing both arms the same
    reconstructed block keeps the A/B single-variable (an empty block would
    be the "degraded search" condition the plan excludes from compliance
    reads, and it would change both arms' behaviour).
    """
    return {
        "entities": [
            {"id": f"s2-ent-{i}", "name": e.get("name", ""),
             "kind": e.get("kind", "")}
            for i, e in enumerate(s2.get("entities") or []) if isinstance(e, dict)
        ],
        "points": [
            {"id": f"s2-pt-{i}", "content": p.get("content", ""),
             "kind": p.get("pointKind") or p.get("kind") or ""}
            for i, p in enumerate(s2.get("points") or []) if isinstance(p, dict)
        ],
        "events": [
            {"id": f"s2-ev-{i}", "content": e.get("content", ""),
             "kind": e.get("eventKind") or e.get("kind") or ""}
            for i, e in enumerate(s2.get("events") or []) if isinstance(e, dict)
        ],
    }


# ── token accounting ───────────────────────────────────────────────────────


def arm_out_tokens(stats: dict) -> tuple[int | None, str]:
    """(TOTAL output tokens this arm emitted, the basis used).

    TOTAL over EVERY call the arm made — the escalated retry and the
    error-informed re-prompt are separate billed calls, and a final-call read
    under-counts exactly the expensive arms (the ones that truncate or emit a
    malformed list), i.e. it shrinks the legacy arm and flatters the delta.

    `stats["cost"]["completion_tokens"]` is the ONE accumulator with that
    property: `_record_call_cost` adds one entry per SUCCESSFUL call, so an
    escalation or parse-retry is summed, never overwritten
    (``tortoise/extractor_v2.py::_complete``'s ``cost`` docstring). It is read
    FIRST for that reason.

    The recovery-based sum is only a fallback: `recovery.s4_out_tokens` is the
    terminal successful call and `truncation_completion_tokens_s4` the
    truncated base call, so it is complete ONLY on the plain truncation path
    (no re-prompt, no partial accept). Where the cost accumulator is absent —
    a fake model in a test, a provider that reports no usage — those are the
    best available numbers, and the basis string names them so a reader never
    has to guess which quantity they are looking at.

    A tokenless response yields ``(None, …)`` — an unmeasured arm, never a 0
    that would read as "this arm emitted nothing".
    """
    cost = int((stats.get("cost") or {}).get("completion_tokens") or 0)
    if cost > 0:
        return cost, "stats.cost.completion_tokens"
    rec = stats.get("recovery") or {}
    healthy = int(rec.get("s4_out_tokens") or 0)
    trunc = int(rec.get("truncation_completion_tokens_s4") or 0)
    if healthy or trunc:
        return healthy + trunc, "recovery.s4_out_tokens+truncation"
    fallback = int(stats.get("completion_tokens") or 0)
    if fallback > 0:
        return fallback, "stats.completion_tokens"
    # ⛔ UNMEASURED, never 0. A provider that reports no usage (or a rejected
    # count normalised to 0 — `model_adapters` defaults `usage.get(..., 0)` and
    # `_accumulate_seam_out_tokens` deliberately writes nothing on a non-positive
    # count) would otherwise hand `compare` a delta of 0, i.e. a —100 %
    # "reduction": the most flattering possible number, indistinguishable from a
    # real one. `None` propagates to "not measured" in the reduction column.
    return None, "unmeasured (provider reported no token usage)"


def run_arm(model: Any, sess: dict, arm: str) -> dict:
    """One S4 call under one contract.

    Never raises for a model/parse failure — the failure is the observation,
    and dropping it would bias the delta (the expensive arm is the one that
    truncates).
    """
    stats: dict = {}
    started = time.monotonic()
    row: dict = {"arm": arm, "delta": arm == "delta"}
    try:
        s4 = x.run_s4(model, sess["story"], search_from_s2(sess["s2"]),
                      sess["s2"], session_date=sess["session_date"],
                      stats=stats, delta=(arm == "delta"))
    except Exception as e:  # a failed arm is data, not a crash
        row.update({
            "error": f"{type(e).__name__}: {e}"[:400],
            "out_tokens": None,
            "basis": None,
            "prompt_tokens": int(stats.get("prompt_tokens") or 0),
            "truncated": bool(stats.get("truncated")),
            "elapsed_s": round(time.monotonic() - started, 2),
        })
        return row
    merged = x.merge_embed_lists(sess["s2"], s4)
    # the #2408 census instrument, reused verbatim — one implementation
    comp = x._s4_merge_stats(sess["s2"], s4, merged)
    out_tokens, basis = arm_out_tokens(stats)
    rec = stats.get("recovery") or {}
    row.update({
        "out_tokens": out_tokens,
        "basis": basis,
        "prompt_tokens": int(stats.get("prompt_tokens") or 0),
        "truncated": bool(stats.get("truncated")),
        "finish_reason": stats.get("finish_reason"),
        "attempts": int(stats.get("attempts") or 0),
        # ⛔ `run_s4` does not write `s4_delta_empty` / `s4_delta_reemit_suspect`
        # — both are `extract_session_v2`-stage keys, so reading them here would
        # be a STRUCTURALLY DEAD field (None on every row, and `False` on rows
        # whose delta emitted nothing, which contradicts the data). They are
        # DERIVED from this arm's own composition with production's own
        # definitions instead: empty = no content-bearing item emitted; suspect
        # = pure verbatim re-emission at ≥ `_S4_REEMIT_SUSPECT_RATIO` × S2.
        "s4_delta_empty": (comp["s4_items"] == 0) if arm == "delta" else None,
        "s4_delta_reemit_suspect": (
            (comp["verbatim_reemissions"]
             >= x._S4_REEMIT_SUSPECT_RATIO * comp["s2_items"])
            if (arm == "delta" and comp["s2_items"]) else None),
        "escalation_out_tokens": int(
            rec.get("truncation_completion_tokens_s4") or 0),
        "s2_items": comp["s2_items"],
        "s4_items": comp["s4_items"],
        "merged_items": comp["merged_items"],
        "verbatim_reemissions": comp["verbatim_reemissions"],
        "corrected_by_s4": comp["corrected_by_s4"],
        "added_by_s4": comp["added_by_s4"],
        "kept_from_s2": comp["kept_from_s2"],
        "elapsed_s": round(time.monotonic() - started, 2),
    })
    return row


# ── the comparison ─────────────────────────────────────────────────────────


def _reduction(legacy: int, delta: int | None) -> float | None:
    """1 − delta/legacy. None when the legacy arm is unusable — never 0.0,
    which would read as "no saving" instead of "not measured"."""
    if not legacy or legacy <= 0 or delta is None:
        return None
    return round(1.0 - (delta / legacy), 4)


def compare(pairs: list[dict]) -> dict:
    """Aggregate one (legacy, delta) pair set into the measurement.

    PRIMARY number is the TOKEN-WEIGHTED reduction (Σdelta/Σlegacy), because
    the ticket's target is a per-chunk output-size drop and sessions differ
    in size; the per-session median is reported as the robustness check (a
    single huge session must not carry the verdict alone), plus the min so a
    reader can see whether the effect is universal or carried by outliers.
    """
    rows = []
    for p in pairs:
        lg, dl = p["legacy"], p["delta"]
        rows.append({
            "session": p["session"],
            "legacy_out_tokens": lg.get("out_tokens"),
            "delta_out_tokens": dl.get("out_tokens"),
            "reduction": _reduction(lg.get("out_tokens") or 0,
                                    dl.get("out_tokens")),
            "legacy_truncated": lg.get("truncated"),
            "delta_truncated": dl.get("truncated"),
            "legacy_s4_items": lg.get("s4_items"),
            "delta_s4_items": dl.get("s4_items"),
            "s2_items": lg.get("s2_items"),
            "delta_verbatim_reemissions": dl.get("verbatim_reemissions"),
            "delta_added_by_s4": dl.get("added_by_s4"),
            "delta_corrected_by_s4": dl.get("corrected_by_s4"),
            "legacy_error": lg.get("error"),
            "delta_error": dl.get("error"),
        })
    usable = [r for r in rows if r["reduction"] is not None]
    sum_lg = sum(r["legacy_out_tokens"] for r in usable)
    sum_dl = sum(r["delta_out_tokens"] for r in usable)
    reductions = sorted(r["reduction"] for r in usable)
    median = None
    if reductions:
        mid = len(reductions) // 2
        median = (reductions[mid] if len(reductions) % 2
                  else round((reductions[mid - 1] + reductions[mid]) / 2, 4))
    return {
        "sessions_measured": len(usable),
        "sessions_attempted": len(rows),
        "sum_legacy_out_tokens": sum_lg,
        "sum_delta_out_tokens": sum_dl,
        # THE number the issue asks for:
        "token_weighted_reduction": _reduction(sum_lg, sum_dl),
        "median_session_reduction": median,
        "min_session_reduction": reductions[0] if reductions else None,
        "max_session_reduction": reductions[-1] if reductions else None,
        "composition": _composition_totals(
            [p for p, r in zip(pairs, rows, strict=True)
             if r["reduction"] is not None]),
        "rows": rows,
    }


def _composition_totals(pairs: list[dict]) -> dict:
    """Sum the census quantities per arm — WHY the delta arm is smaller, and
    what it costs in gap-finding.

    `verbatim_reemissions` is the tax itself (items re-typed unchanged);
    `added_by_s4` is the gap channel (the value the contract exists to keep)
    and `corrected_by_s4` the correction channel. Reporting all three per arm
    Summed over the SAME usable pairs the token totals use — a "why it is
    smaller" table built over a different population than the headline number
    would silently describe different sessions.

    It is what stops the token delta from being read as a pure win: a delta arm
    that emits nothing because it stopped LOOKING would show up as `added_by_s4`
    collapsing against the legacy arm, and the reader sees that instead of
    having to trust the reduction number.
    """
    keys = ("s2_items", "s4_items", "merged_items", "added_by_s4",
            "corrected_by_s4", "verbatim_reemissions")
    out: dict = {}
    for arm in _ARM_CHOICES:
        tot = dict.fromkeys(keys, 0)
        for p in pairs:
            row = p.get(arm) or {}
            for k in keys:
                tot[k] += int(row.get(k) or 0)
        out[arm] = tot
    return out


# ── commands ───────────────────────────────────────────────────────────────


def _model_for(model_id: str):
    """The direct DeepSeek adapter — the same route the extractor uses when
    DEEPSEEK_API_KEY is set (`build_extractor_model`'s deepseek-direct
    branch), constructed directly so the measurement does not depend on
    routing config."""
    from tortoise.model_adapters import DeepSeekDirectModel
    return DeepSeekDirectModel(model_id)


def cmd_run(args: argparse.Namespace) -> int:
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("REFUSED: DEEPSEEK_API_KEY is not set — an S4 A/B with no model "
              "call is not a measurement of the model's output size.",
              file=sys.stderr)
        return 2
    sessions = load_sessions(Path(args.receipt), embeds_arm=args.embeds_arm,
                             only=args.sessions)
    model = _model_for(args.model)
    pairs: list[dict] = []
    for sess in sessions:
        pair: dict = {"session": sess["session"]}
        for arm in _ARM_CHOICES:
            print(f"  {sess['session']} · {arm} …", file=sys.stderr, flush=True)
            pair[arm] = run_arm(model, sess, arm)
            r = pair[arm]
            print(f"    → out={r.get('out_tokens')} items={r.get('s4_items')} "
                  f"trunc={r.get('truncated')} {r.get('elapsed_s')}s "
                  f"{r.get('error') or ''}", file=sys.stderr, flush=True)
        pairs.append(pair)
    summary = compare(pairs)
    report = {
        "instrument": "tools/s4_reeemit_tax.py",
        "issue": "#1789",
        "measured_at": _dt.datetime.now(_dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model_id": args.model,
        "receipt_dir": str(args.receipt),
        "embeds_arm": args.embeds_arm,
        "contracts": {
            "legacy": "S4_TMPL (complete-list re-emit) — TORTOISE_S4_DELTA=0",
            "delta": "S4_TMPL_DELTA (gaps + corrections only) — shipped default",
        },
        "inputs": [
            {"session": s["session"], "receipt": s["receipt"],
             "s2_items": sum(len(v) for v in s["s2"].values()),
             "s2_reference": s["s2_reference"], "turns": s["turns"]}
            for s in sessions
        ],
        # every per-arm observation, so the artifact is auditable without
        # re-running (the token totals are only trustworthy if the rows behind
        # them are inspectable)
        "arms": [
            {"session": p["session"], "legacy": p["legacy"],
             "delta": p["delta"]}
            for p in pairs
        ],
        "summary": summary,
        "caveats": [
            "Measured per-session S4 output tokens on RECORDED real S2 geometry; "
            "the prompt inputs are identical across arms, so the delta is the "
            "output contract's effect.",
            "One call per arm per session (no production-run composition): model "
            "compliance in the wild is not measured here — it is OBSERVABLE per "
            "row via verbatim_reemissions / added_by_s4 / "
            "s4_delta_reemit_suspect.",
            "The S3 search block is reconstructed from the session's own S2 list "
            "(what production S4 sees after S2 is written), not a live graph read.",
            "Accuracy / chunk@20 are the eval lane's gates and are NOT measured here.",
        ],
    }
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
        print(f"wrote {out}", file=sys.stderr)
    print(json.dumps({k: v for k, v in summary.items() if k != "rows"},
                     indent=1))
    # FAIL-CLOSED: a run whose every arm failed measured NOTHING, and exit 0
    # there would let a broken credential or a dead endpoint read as "the tax
    # was measured, reduction is null".
    if summary["sessions_measured"] == 0:
        print("FAILED: 0 sessions produced a usable pair — no measurement was "
              "made (see the per-arm errors above; a 401/transport failure is "
              "NOT a zero result).", file=sys.stderr)
        return 1
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Both arms' PROMPT geometry, no model call and no key.

    This is the input-side half of the tax (the delta template is longer than
    the legacy one) and the CI-runnable proof that the two renders really are
    different documents — a cheap pin that cannot rot into "both arms render
    the same prompt", which would make the whole measurement vacuous.
    """
    sessions = load_sessions(Path(args.receipt), embeds_arm=args.embeds_arm,
                             only=args.sessions)
    rows = []
    for sess in sessions:
        search = search_from_s2(sess["s2"])
        legacy = x.render_s4_prompt(sess["story"], search, sess["s2"],
                                    session_date=sess["session_date"],
                                    delta=False)
        delta = x.render_s4_prompt(sess["story"], search, sess["s2"],
                                   session_date=sess["session_date"],
                                   delta=True)
        rows.append({
            "session": sess["session"],
            "s2_items": sum(len(v) for v in sess["s2"].values()),
            "legacy_prompt_chars": len(legacy),
            "delta_prompt_chars": len(delta),
            "delta_minus_legacy_pct": round(
                100.0 * (len(delta) - len(legacy)) / len(legacy), 3),
            "renders_differ": legacy != delta,
            "legacy_has_reexmit_mandate": (
                "the COMPLETE embed list" in legacy),
            "delta_has_reexmit_mandate": (
                "the COMPLETE embed list" in delta),
            "delta_has_delta_mandate": "EMIT ONLY DELTAS" in delta,
        })
    report = {"instrument": "tools/s4_reeemit_tax.py dry-run",
              "issue": "#1789", "rows": rows}
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("dry-run", cmd_dry_run)):
        s = sub.add_parser(name)
        s.add_argument("--receipt", default=_DEFAULT_RECEIPT,
                       help="write-path eval receipt dir (default: the "
                            "7947-arms real-session receipts)")
        s.add_argument("--embeds-arm", default="narrative",
                       choices=("narrative", "raw"),
                       help="which recorded S2 arm to replay (default narrative)")
        s.add_argument("--sessions", default=None,
                       help="comma-separated session names (default: all)")
        s.add_argument("--out", default=None, help="write the JSON report here")
        if name == "run":
            s.add_argument("--model", default="deepseek-v4-flash",
                           help="extractor model id (default deepseek-v4-flash)")
        s.set_defaults(fn=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.sessions:
        args.sessions = [s.strip() for s in args.sessions.split(",") if s.strip()]
    try:
        return args.fn(args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
