#!/usr/bin/env python3
"""Read-only: can each tier actually DELIVER its published write-op allowance? (#4495)

**Why this exists.** The price book sells two independent numbers per tier and
the dashboard renders both as entitlements in one list
(``website/apps/dashboard/src/pricing.js``): ``"<cap>-node graph"`` and
``"<allowance> write ops/mo"``. One is a monthly **flow** (purchasable as
overage on paid tiers), the other a permanent **stock** enforced as a write
refusal. They were set with no designed relationship — ``cap / allowance`` is
**1.0–3.0** across the published tiers — so the stock can refuse the writes the
flow was sold for.

A tier's allowance is deliverable only when the cap can absorb what those ops
store (``tortoise/pricing.py::allowance_reachability``):

    cap >= allowance * nodes_per_op          (r = net-new non-episodic nodes
                                              per BILLED write op)

Deliverability is a STEP FUNCTION of ``r``: fine up to ``cap / allowance``
(1–3 for the published tiers), gone above it. A single-node write (r = 1) is
deliverable everywhere; a capture/batch commit (r ~ 48–549, the two live
refusals recorded on #4495) is not deliverable anywhere.

``r`` is the one input every prior hand-derived table on #4495 had to assume.
**It is not an assumption: the write path has recorded it on every increment
since epic #909 §4.4/W-4** — ``MeteringRecord.nodes_written`` sits beside
``write_ops`` in both lanes — and nothing ever read it for this question. This
tool reads it (``metering.get_write_op_yield``) and applies the published model
to it.

⛔ **WHY A MEASURED "REACHABLE" IS NOT A PASS.** The ledger records LESS yield
than really happens: three write paths bill a write op and leave
``nodes_written`` at its 0 default — ``POST /v1/points``
(``hosted_api.py:8651``), the live ``/v1/sessions`` capture lane
(``_capture_session_impl``, ``hosted_api.py:13709``), and every MCP tool write
(``mcp_server.py:1550``). So a measured ``r`` is a LOWER BOUND on the true
``r``. That makes the two verdicts ASYMMETRIC:

- measured UNREACHABLE ⇒ the truth is worse or equal ⇒ **exit 1 CONFIRMS** the
  defect. Sound.
- measured REACHABLE ⇒ the truth may be far worse (dilute 1 recorded node into
  1,000 billed ops and ``r`` collapses to 0.001, which reads green) ⇒ it
  certifies NOTHING. **exit 3, not 0.**

Exit 0 is therefore reserved for a yield the caller STATED (``--nodes-per-op``),
where ``r`` is a hypothesis being evaluated rather than a lower bound. Do not
"fix" this by weighting the denominator: the ledger cannot attribute ops to
paths, and a heuristic weight would be an invented number — the one thing this
tool must never produce.

**Read-only, with one stated boundary.** ``--nodes-per-op`` is fully offline and
``--org`` on the Supabase control plane is a plain row read. ``--org`` in
REGISTRY mode opens the SDK, whose projection constructor runs its idempotent
``_ensure_indexes()`` DDL on the SDK's OWN graphs — the same boundary
``tools/edge_census.py`` measured and refuses by default, so this tool refuses it
without ``--accept-schema-writes``. Nothing here touches the tenant graph,
prices, caps, throttles or refuses; no price, cap, tier or allowance is set here
— setting those is the owner's call (#4495, ruling 2026-09-26).

Modes
-----
    --nodes-per-op R   apply a HYPOTHETICAL (stated) yield — offline, no DB
    --org ORG_ID       measure the yield from that org's current ledger row
                       (a LOWER BOUND: can confirm the defect, cannot certify)

Exit codes — a MISSING MEASUREMENT IS NEVER A PASS
    0  STATED yield, and every EVALUATED tier can deliver its allowance at it
       (all published tiers unless `--tier` restricts the set — the verdict text
       says which, and names how many of the published tiers it covers)
    1  UNREACHABLE — a stated yield, or a measured lower bound, that NOT every
       tier can deliver (the condition is "any tier fails", not "all fail").
       Both are sound: a lower bound that already overshoots means reality
       overshoots too.
    2  NOT MEASURABLE — no yield supplied, the ledger was not consulted or could
       not be read, the org has not written yet, or writes were billed without
       recording a node yield. Also every bad/unrankable INPUT (a nonsense yield,
       an unknown tier, an unreadable price book, an empty tier set): 1 means
       "a measurement CONFIRMED the defect", so nothing else may return it.
    3  MEASURED and reachable AT THE RECORDED LOWER BOUND — NOT CERTIFIED, for
       the dilution reason above. Never 0.

⛔ ``--org`` cannot certify a TRUE zero yield either: ``0`` cannot be told apart
from "never recorded", so it is reported as ``unattributed`` (exit 2), never as
full reachability.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below (the repo gate
# tests/test_entry_point_python_guard.py requires this shape for tools/**/*.py).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/write_op_reachability.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/write_op_reachability.py`"
    )

import argparse
import json
import math

from tortoise import pricing

EXIT_REACHABLE = 0
EXIT_UNREACHABLE = 1
EXIT_NOT_MEASURABLE = 2
EXIT_UNCERTIFIED = 3


def _row(reach: dict) -> dict:
    """Round a reachability dict for display (the pure dict stays exact)."""
    return {
        **reach,
        "cap_per_allowance": round(reach["cap_per_allowance"], 2),
        "reachable_ops": int(reach["reachable_ops"]),
        # NUMERIC, never a display string: --json is machine-readable, and a
        # "<0.01" here would raise TypeError on any numeric compare/sort of
        # exactly the near-threshold rows. The "<0.01" rendering lives in
        # _print_table.
        "unreachable_share_pct": reach["unreachable_share"] * 100,
        "required_cap": int(reach["required_cap"]),
        "cap_shortfall": int(reach["cap_shortfall"]),
    }


def evaluate(tiers: list[str], nodes_per_op: float) -> list[dict]:
    """Apply the published model to every tier at a given yield."""
    return [_row(pricing.allowance_reachability(t, nodes_per_op)) for t in tiers]


def _published_tiers_or_exit2(ap: argparse.ArgumentParser) -> list[str]:
    """The tier list, with an unreadable price book mapped to exit 2.

    ``_load`` raises OSError for a missing book, and that must not present as
    the P0 finding (exit 1) it never measured.
    """
    try:
        published = pricing.all_tiers()
    except Exception as e:  # ANY unreadable book is exit 2, never 1
        ap.error(f"not measurable: price book unreadable: "
                 f"{type(e).__name__}: {e}")
        raise  # unreachable — ap.error exits
    if not published:
        ap.error("the price book publishes no tiers — nothing was measured")
    return published


def _evaluate_or_exit2(ap: argparse.ArgumentParser, tiers: list[str],
                       nodes_per_op: float) -> list[dict]:
    """Evaluate, mapping every unrankable input to exit 2 (never 1).

    ``tier_limits`` is documented to raise KeyError on config drift (#750.8)
    and ``_load`` raises OSError for a missing/unreadable book, so a broken
    price book would otherwise crash out as exit 1 — the code that asserts
    "a measurement CONFIRMED the defect".
    """
    try:
        rows = evaluate(tiers, nodes_per_op)
    except Exception as e:  # ANY unrankable input is exit 2, never 1
        ap.error(f"not measurable: {type(e).__name__}: {e}")
        raise  # unreachable — ap.error exits
    if not rows:
        # all([]) is True: an empty surface would print a green verdict having
        # measured nothing.
        ap.error("the price book publishes no tiers — nothing was measured")
    return rows


def _print_table(rows: list[dict], nodes_per_op: float, source: str,
                 *, certified: bool = True,
                 published: int | None = None) -> None:
    """Render the per-tier table and the verdict.

    ``published`` is the number of tiers the price book publishes. When it is
    larger than ``len(rows)`` the caller restricted the evaluation with
    ``--tier``, and EVERY verdict must speak for the evaluated subset only —
    a pass produced from less than the thing it certifies is the same
    overclaim this tool exists to refuse (see the measured-lower-bound path
    below, which qualifies its verdict for the same reason).
    """
    # ‼ SET-based, not row-count-based: `--tier` is appendable and the rows are
    # not deduped, so a repeated tier inflated `len(rows)` until the subset
    # guard switched itself off and a ONE-tier run printed "every published
    # tier can deliver" (measured on the live book with `--tier free` x5).
    # The count that decides the scope is the count of DISTINCT tiers measured.
    distinct = len({r["tier"] for r in rows})
    subset = published is not None and published > distinct
    if subset:
        scope = (f"EVALUATED tier{'s' if distinct != 1 else ''} "
                 f"({distinct} of {published} published)")
    else:
        scope = ("published tier" if distinct == 1 else "published tiers")
    print(f"nodes per billed write op (r): {nodes_per_op:g}   [{source}]")
    print()
    head = (f"{'tier':<6} {'allowance':>10} {'node cap':>10} {'cap/allw':>9} "
            f"{'reachable ops':>14} {'unreachable':>12} {'required cap':>13}")
    print(head)
    print("-" * len(head))
    for r in rows:
        share = r["unreachable_share_pct"]
        # A sub-0.01% share must not render as "0.00%" beside a non-zero
        # shortfall — the round-1 contradiction in another guise.
        share_txt = ("<0.01%" if 0 < share < 0.01 else f"{share:.2f}%")
        print(f"{r['tier']:<6} {r['allowance']:>10,} {r['cap']:>10,} "
              f"{r['cap_per_allowance']:>9.2f} {r['reachable_ops']:>14,} "
              f"{share_txt:>12} {r['required_cap']:>13,}")
    print()
    short = [r for r in rows if not r["reachable"]]
    if not short:
        if certified:
            if subset:
                print(f"VERDICT: REACHABLE — every {scope} can deliver its "
                      "write-op allowance at this yield.")
            else:
                print("VERDICT: REACHABLE — every published tier can deliver "
                      "its write-op allowance at this yield.")
        else:
            # The TEXT header must carry the same qualification as the exit
            # code and the JSON verdict: a measured yield is a LOWER BOUND.
            print("VERDICT: REACHABLE AT THE RECORDED LOWER BOUND — "
                  "NOT CERTIFIED."
                  + (f" (evaluated: {len(rows)} of {published} published "
                     f"tiers)" if subset else ""))
        return
    worst = max(short, key=lambda r: r["unreachable_share"])
    print(f"VERDICT: UNREACHABLE — {len(short)}/{len(rows)} {scope} "
          f"cannot deliver {'its' if len(rows) == 1 else 'their'} allowance "
          f"at r={nodes_per_op:g}.")
    print(f"  worst: {worst['tier']} — the cap refuses after "
          f"{worst['reachable_ops']:,} of the {worst['allowance']:,} monthly "
          f"write ops it sells, and the cap never resets.")
    print("  the cap needs to be "
          f"{worst['cap_shortfall']:,} nodes larger at {worst['tier']} alone "
          "for the two published numbers to be reachable together "
          "(cap >= allowance x r).")
    print("  ⚠️ nothing was changed by this measurement: the values are the "
          "owner's call (#4495).")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Can each tier deliver its published write-op allowance? "
                    "(#4495) Read-only: changes no price, cap or tier.")
    ap.add_argument("--nodes-per-op", type=float, default=None,
                    help="a STATED net-new nodes per billed write op "
                         "(offline: no DB and no ledger are consulted)")
    ap.add_argument("--org", default=None,
                    help="measure the yield from this org's current period "
                         "ledger row (a LOWER BOUND — see the module docstring)")
    ap.add_argument("--tier", action="append", default=None,
                    help="restrict to a tier (repeatable; default: all)")
    ap.add_argument("--accept-schema-writes", action="store_true",
                    help="permit --org in registry mode, which opens the SDK "
                         "and runs its idempotent index DDL "
                         "(tools/edge_census.py's boundary)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    if args.nodes_per_op is not None and args.org:
        ap.error("--nodes-per-op and --org are mutually exclusive: "
                 "one is a stated yield, the other a measured one")

    published = _published_tiers_or_exit2(ap)
    if args.tier:
        unknown = [t for t in args.tier if t not in published]
        if unknown:
            # tier_limits falls back to `free` for an unknown name, which would
            # print free's figures under the requested name.
            ap.error(f"unknown tier(s) {unknown}; the price book publishes "
                     f"{published}")
    tiers = list(dict.fromkeys(args.tier)) if args.tier else published

    if args.nodes_per_op is not None:
        if not math.isfinite(args.nodes_per_op) or args.nodes_per_op < 0:
            ap.error("--nodes-per-op must be finite and >= 0, got "
                     f"{args.nodes_per_op!r}")
        rows = _evaluate_or_exit2(ap, tiers, args.nodes_per_op)
        reachable = all(r["reachable"] for r in rows)
        if args.json:
            print(json.dumps({
                "source": "stated", "nodes_per_op": args.nodes_per_op,
                "tiers": rows,
                "tiers_evaluated": len({r["tier"] for r in rows}),
                "tiers_published": len(published),
                "verdict": "REACHABLE" if reachable else "UNREACHABLE",
            }, indent=2))
        else:
            _print_table(rows, args.nodes_per_op, "stated",
                         published=len(published))
        return EXIT_REACHABLE if reachable else EXIT_UNREACHABLE

    if args.org:
        from tortoise import metering
        if not metering._supabase_mode() and not args.accept_schema_writes:
            ap.error(
                "--org in registry mode opens the SDK, whose projection "
                "constructor runs idempotent index DDL (_ensure_indexes) on "
                "the SDK's own graphs. Re-run with --accept-schema-writes to "
                "accept that, or use --nodes-per-op for an offline read "
                "(see tools/edge_census.py for the same boundary).")
        yield_view = metering.get_write_op_yield(args.org)
        attribution = yield_view["nodes_attribution"]
        if not yield_view["measured"]:
            # Absence, unattributed writes and both read faults refuse to
            # answer — and each says which, so an operator is not sent hunting
            # in the wrong place or told an unrecorded yield is zero.
            detail = {
                "period_error": (
                    "the billing WINDOW could not be resolved (a subscription "
                    "anchor fault) — the ledger was never consulted"),
                "read_error": (
                    "the ledger row could NOT BE READ (a read failure, not an "
                    "absence of writes)"),
                "absent": (
                    "no write ops are recorded for this org in this period "
                    "(nobody has written yet)"),
                "unattributed": (
                    f"{yield_view['write_ops']:,} write ops were BILLED but no "
                    "node yield was recorded (nodes_written=0). The ledger "
                    "cannot distinguish a true zero from an unrecorded one: "
                    "POST /v1/points, the /v1/sessions capture lane and MCP "
                    "tool writes all bill an op without recording its nodes"),
            }[attribution]
            if args.json:
                print(json.dumps({"source": "ledger", "org": args.org,
                                  "measured": False,
                                  "nodes_attribution": attribution,
                                  "ledger": yield_view,
                                  "verdict": "NOT MEASURABLE"}, indent=2))
            else:
                print(f"NOT MEASURABLE — {detail}.", file=sys.stderr)
                print("  Nothing is concluded about whether the published "
                      "allowance is reachable.", file=sys.stderr)
            return EXIT_NOT_MEASURABLE

        r = yield_view["nodes_per_op"]
        rows = _evaluate_or_exit2(ap, tiers, r)
        reachable = all(x["reachable"] for x in rows)
        if args.json:
            print(json.dumps({
                "source": "ledger", "org": args.org, "ledger": yield_view,
                "tiers": rows,
                "tiers_evaluated": len({r["tier"] for r in rows}),
                "tiers_published": len(published),
                "lower_bound": True,
                "verdict": ("REACHABLE AT THE RECORDED LOWER BOUND "
                            "(NOT CERTIFIED)" if reachable else "UNREACHABLE"),
            }, indent=2))
        else:
            _print_table(rows, r, f"measured LOWER BOUND, org={args.org}, "
                                  f"period={yield_view['period']}, "
                                  f"{yield_view['write_ops']:,} ops / "
                                  f"{yield_view['nodes_written']:,} nodes",
                         certified=False, published=len(published))
            if reachable:
                print()
                print("⚠️ NOT CERTIFIED: the recorded yield is a LOWER BOUND — "
                      "ops from write paths that record no node yield are "
                      "counted in the denominator but not the numerator, so a "
                      "true yield above this threshold would be undisclosed. "
                      "Use --nodes-per-op to evaluate a stated yield.")
        if reachable:
            return EXIT_UNCERTIFIED
        return EXIT_UNREACHABLE

    ap.error("supply --nodes-per-op (a stated yield) or --org (measured); "
             "there is no default yield and this tool will not invent one")
    return EXIT_NOT_MEASURABLE


if __name__ == "__main__":
    raise SystemExit(main())
