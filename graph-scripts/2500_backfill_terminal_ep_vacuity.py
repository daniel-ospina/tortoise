#!/usr/bin/env python3
"""#2500 — backfill PRE-#2490 terminal claims' frozen EP posteriors to vacuity.

WHY (the trap this closes)
-------------------------
#2490 ("freeze terminal-posterior") makes every terminalizing WRITE decay the
claim's belief to vacuity — ``live.decay_clause``: ``confidence=0.5,
posterior_alpha=1.0, posterior_beta=1.0``. That is a WRITE-TIME fix: it only
covers claims terminalized after it shipped.

A claim already terminal in a graph keeps the frozen PRE-terminal posterior it
had when it was last measured. #2490's reader gates stop a terminal claim being
served as CONTESTED/UNCERTAIN, but they do not rewrite the stored columns, and
two include-terminal/audit surfaces read the stored posterior directly:

  * ``GraphRanker._fetch_signals`` — the sort key of ``order_by="confidence"``
    and the ``order_by="graph"`` boost — reads ``coalesce(n.confidence,
    posterior→prior mean)`` and has NO terminal gate, so a pre-#2490 terminal
    claim still ranks at its full pre-terminal belief.
  * ``search_engine.annotate_ep_batch`` gates ``has_ep``/``contested`` to False
    for a terminal claim but still returns the frozen ``confidence_mean``.

This one-shot sweep aligns those existing rows with the invariant #2490 writes
going forward. It is a DATA sweep only — it changes no read path.

Idempotency
-----------
The MATCH requires the EFFECTIVE belief read (``coalesce(posterior, prior)``)
to differ from the vacuous tuple, which is exactly the post-condition a second
run finds empty — so re-running is a no-op by construction. ``--dry-run``
reports (and NAMES) the rows it would change, without writing.

⚠️ Why the comparison is against the READ, not the stored column: the readers
fall back to the PRIOR when no posterior was ever computed
(``coalesce(n.posterior_alpha, n.ep_alpha, 1.0)``), so a terminal claim with a
prior but no stored posterior READS at that prior — exactly #2490's 0.904
repro. A predicate that treated an absent posterior as "already vacuous"
skipped that whole class (caught in review of PR #5769).

Scope
-----
``:Point`` only: the EP belief columns (``posterior_alpha``, ``posterior_beta``,
``confidence``) and the lifecycle vocabulary (``status``, ``outdated``) are
Point semantics — no other label carries them.

Operator nodes ARE ``:Point``-labelled (``is_operator=true``), so they need an
EXPLICIT exclusion: the terminal predicate does NOT gate on ``is_operator``,
and ``invalidate_point`` used to flag an operator node ``outdated=true``
(``tortoise/sdk.py:6253`` — "``invalidate_point`` had no guard at all"). The
sweep therefore carries the house operator predicate (``ep.py:890``) in its
WHERE, so an operator's EP state is never decayed by this script.

NOT touched, deliberately: ``ep_alpha``/``ep_beta`` — the persisted prior
history and, per #2490, the SOLE recovery vector if a terminal state is ever
reversed — plus ``status``, ``outdated`` and ``validTo``/``expiredAt``. Only
the three decay columns ``live.decay_clause`` names are written.

Usage:
    python3 graph-scripts/2500_backfill_terminal_ep_vacuity.py [--dry-run] [--yes] [--uri URI]

Defaults to the ``TORTOISE_DB_URI`` env var (or
``docker://:falkordb@localhost:6379/tortoise``).
Test safety: the guard runs on the graph the sweep ACTUALLY writes (the
SDK-resolved project graph name), never on the URI path — so a real write
always needs ``--yes``.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from the worktree root or from graph-scripts/.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"

# #2901/#2500: import the CANONICAL terminal predicate rather than re-declaring
# a status set here. live.py's own comment forbids a call-site status subset (a
# hand-written one omitted ``outdated`` from three reader filters), and
# ``_terminal_expression`` is the HOUSE pattern for graph-scripts consumers —
# audit_beta_gate.py and 1714_dedup_observation.py both import from live.py, and
# tests/test_terminal_status_vocabulary.py directs consumers to call it. It is
# private by naming only: it is the one positive-side composition of the shared
# vocabulary, and re-deriving it here would duplicate the NULL/flag handling.
from tortoise.live import _terminal_expression, decay_clause  # noqa: E402

#: The PRIOR fallback each consumer uses when no posterior was ever computed —
#: the ``coalesce(n.posterior_alpha, n.ep_alpha, 1.0)`` chain in
#: ``GraphRanker._fetch_signals`` and the EP readers. The sweep must compare
#: against the effective READ, not the raw stored column: a terminal claim with
#: a prior but no stored posterior reads at that prior (0.909 on a (10, 1)
#: baseline — #2490's 0.904 repro class), so treating an absent posterior as
#: "already vacuous" silently skips it.
_PRIOR_FALLBACK = {"alpha": 1.0, "beta": 1.0}


def _vacuous_targets(alias: str = "n") -> dict[str, float]:
    """Parse #2490's ``decay_clause`` into ``{property: target value}``.

    The sweep's write uses ``decay_clause`` itself (never a copy), and its
    idempotency predicate is derived from this same fragment — so the backfill
    cannot point at a stale tuple if #2490's decay values ever change.
    """
    targets: dict[str, float] = {}
    for part in decay_clause(alias).split(","):
        prop, _, value = part.strip().partition("=")
        targets[prop.split(".", 1)[1]] = float(value)
    return targets


_VACUOUS = _vacuous_targets("n")


def _graph_of(proj):
    """Accept a ``FalkorProjection`` (``.g``) or a bare graph handle."""
    return getattr(proj, "g", proj)


def _unaligned(alias: str = "n") -> str:
    """TRUE when a terminal claim's EFFECTIVE belief is not yet vacuous.

    Compares the readers' own coalesce chain (``posterior`` else ``prior`` else
    1.0) against #2490's decay targets — see ``_PRIOR_FALLBACK``. ``confidence``
    is separate because the readers prefer a STORED confidence over the
    posterior mean, so a stored non-vacuous confidence is unaligned even when
    the posterior is already (1, 1). An ABSENT confidence is read from the
    posterior mean, which the two arms above already cover.
    """
    arms = [
        f"coalesce({alias}.posterior_{k}, {alias}.ep_{k}, "
        f"{_PRIOR_FALLBACK[k]}) <> {_VACUOUS[f'posterior_{k}']}"
        for k in ("alpha", "beta")
    ]
    arms.append(
        f"({alias}.confidence IS NOT NULL "
        f"AND {alias}.confidence <> {_VACUOUS['confidence']})")
    return " OR ".join(arms)


def _sweep_where(alias: str = "n") -> str:
    """The sweep's WHERE — three load-bearing conjuncts:

      1. ``_terminal_expression`` — the canonical terminal predicate (status in
         the #2901 vocabulary OR the legacy ``outdated=true`` flag).
      2. NOT an operator. This conjunct is NOT redundant: the terminal
         predicate has no ``is_operator`` gate, and ``invalidate_point`` used
         to flag an operator node ``outdated=true`` (``sdk.py:6253``), so
         without it a legacy-flagged operator is swept. Mirrors ``ep.py:890``.
      3. ``_unaligned`` — the effective belief read differs from the vacuous
         tuple, which is the post-condition of the write, so a second run
         matches nothing.
    """
    return (f"{_terminal_expression(f'{alias}.status')} "
            f"AND ({alias}.is_operator IS NULL "
            f"OR {alias}.is_operator = false) "
            f"AND {alias}.op_type IS NULL "
            f"AND ({_unaligned(alias)})")


def _statements(alias: str = "n") -> tuple[str, str, str]:
    """``(count, list, sweep)`` Cypher for the #2500 match set."""
    where = _sweep_where(alias)
    count = f"MATCH ({alias}:Point) WHERE {where} RETURN count({alias})"
    listed = (
        f"MATCH ({alias}:Point) WHERE {where} "
        f"RETURN {alias}.id, {alias}.status, coalesce({alias}.outdated, false), "
        f"{alias}.confidence, {alias}.posterior_alpha, {alias}.posterior_beta "
        f"ORDER BY {alias}.id"
    )
    sweep = (f"MATCH ({alias}:Point) WHERE {where} "
             f"SET {decay_clause(alias)} RETURN count({alias})")
    return count, listed, sweep


def backfill_terminal_ep_vacuity(proj, *, dry_run: bool = False) -> dict:
    """Decay every terminal Point's non-vacuous stored EP posterior. Idempotent.

    Importable so tests drive it directly on an embedded test graph. ``proj``
    is a ``FalkorProjection`` (or a bare graph handle with ``.query``).

    Returns ``{"found": int, "swept": int, "points": [...]}`` where each point
    is ``(id, status, outdated, confidence, posterior_alpha, posterior_beta)``
    — the stored values AS SEEN BEFORE the write. ``found`` counts the rows
    matching the sweep predicate, ``swept`` the rows written (0 on a dry run
    and on an already-clean graph).

    ``points`` names the rows on BOTH paths: ``--dry-run`` is the operator's
    only pre-flight before an irreversible write (on a terminal claim the
    pre-terminal posterior is discarded), so the read-only pass must say WHICH
    claims it would change, not just how many.
    """
    graph = _graph_of(proj)
    count_stmt, list_stmt, sweep_stmt = _statements()

    count_rows = graph.query(count_stmt).result_set
    found = int(count_rows[0][0] or 0) if count_rows else 0
    if found == 0:
        return {"found": 0, "swept": 0, "points": []}

    listed = graph.query(list_stmt).result_set or []
    points = [
        (str(r[0]), r[1], bool(r[2]), r[3], r[4], r[5]) for r in listed
    ]
    if dry_run:
        return {"found": found, "swept": 0, "points": points}

    swept_rows = graph.query(sweep_stmt).result_set
    swept = int(swept_rows[0][0] or 0) if swept_rows else 0
    return {"found": found, "swept": swept, "points": points}


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: must be given the graph the sweep ACTUALLY writes.

    Gating on the URI path would auto-approve a run whose path merely LOOKS
    test-prefixed while the write lands on the resolved production graph — the
    reason this takes the SDK-resolved name. A real write must pass ``--yes``.
    """
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This sweep DECAYS every terminal claim's stored EP posterior to")
    print("    vacuity (confidence=.5, posterior_alpha/beta=1.0) so a pre-#2490")
    print("    claim cannot rank at full belief (#2500). ep_alpha/ep_beta are")
    print("    kept as prior history, but the frozen posterior is DISCARDED.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#2500: decay pre-#2490 terminal claims' frozen EP posteriors")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, no writes")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    args = ap.parse_args()

    # Connect through the SDK so the sweep shares the exact project graph the
    # readers use. The graph NAME is the SDK's to resolve; the script never
    # guesses it from the URI path.
    os.environ["TORTOISE_DB_URI"] = args.uri
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK()
    try:
        proj = sdk._get_proj()
        target = proj.g.name
        test_guard(target, args.yes)
        print(f"Project graph (SDK-resolved): {target}")
        report = backfill_terminal_ep_vacuity(proj, dry_run=args.dry_run)
    finally:
        sdk.close()

    mode = "DRY-RUN" if args.dry_run else "SWEEP"
    print(f"[{mode}] terminal Points with a non-vacuous stored EP column: "
          f"{report['found']}")
    for pid, status, outdated, conf, pa, pb in report["points"]:
        print(f"           · {pid} status={status!r} outdated={outdated} "
              f"confidence={conf!r} posterior=({pa!r}, {pb!r})")
    print(f"[{mode}] rows decayed to (0.5, (1.0, 1.0)):  {report['swept']}")
    if args.dry_run:
        print("\nDry-run complete — re-run without --dry-run to sweep.")
    elif report["found"] == 0:
        print("\nNo frozen terminal EP state — graph is clean "
              "(idempotent sweep, safe to re-run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
