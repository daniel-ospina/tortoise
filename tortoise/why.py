"""W4 why-layer enrichment — the shared why-block assembly (epic #2080, DM-1).

ONE read-only, bounded, zero-LLM assembly feeds every enriched surface
(search / analyze / ask / MCP ``recall_state``) AND the ``/v1/context``
delivery contract (D1/D2 shared implementation). The canonical why-block
(plan §3.1.4) is::

    {point_id, support_chain, ep, conflicts, supersession, tradeoffs,
     dig_deeper}

The enriched-item projection (plan §3.1.1 / §6.1) nests ``support_chain``
under a top-level ``why`` key and promotes ``conflicts``/``supersession``/
``tradeoffs``/``dig_deeper`` to top-level additive item keys (dropping the
redundant ``ep`` copy — the item's flat ``ep`` stays canonical).

Contract invariants (plan §3.1.1/§3.1.3/§3.1.4/§6.1 + #2101):

- **Additive-only, backward compatible (#1353 D8):** every emitted key is NEW;
  nothing existing is renamed/removed; flag-off emission is byte-identical
  to today.
- **Budgets:** ≤ 3 support_chain entries, ≤ 2 NAND conflicts, 1 supersession
  line, ≤ 3 dig_deeper pointers.
- **Deterministic dig-deeper labels:** ``{label, kind, target}`` derived from
  kind + target verb phrases (UXD 4), never LLM prose. Kinds:
  ``supports | nand | superseded | tradeoff`` (ONTOLOGY §5 vocabulary).
  ``superseded``-kind pointers appear ONLY when supersession data exists;
  at decision points the ``tradeoff`` pointer takes precedence within the
  ≤ 3 cap.
- **Flag-first (UXD 3):** ``warnings`` (derived from ``ep.contested`` /
  ``variance``) is a TOP-OF-ITEM array; ``conflicts`` is emitted before
  ``tradeoffs``/``dig_deeper``.
- **Empty/null/absent (§3.1.3):** ``null`` = unset scalar (e.g.
  ``successor_label: null``); ``[]`` = empty collection; absent key =
  dimension not computed (e.g. ``conflicts`` on an uncontested point,
  ``tradeoffs`` on a non-decision point). Clean empty is NEVER a
  degradation — this module raises nothing into the recall turn
  (fail-open: any assembly error degrades to "no enrichment keys").
- **Bounded reads (S8):** the assembly issues a FIXED number of batch
  Cypher queries (supports×2, conflicts, supersession, tradeoffs, plus one
  dedicated persisted ep read — the single-node α/β read that never runs
  full propagation), each covering the whole batch of point ids — never a
  per-result loop. The ep read is DEDICATED (not riding the conflict query)
  so the canonical ``ep`` block is real for every point, including points
  with zero active NANDers.

The W4 flag (``TORTOISE_W4_ENRICHMENT``) is honored on every enriched
surface — a surface that emits the keys with the flag OFF, or drops them
with the flag ON, is flag drift (E2E-1).
"""
from __future__ import annotations

import logging
import os
import re

from .env_truthy import is_truthy  # #4097: the declared truthy contract
from .live import (
    _live_only,  # #9962: the dampener's own predicate
    is_terminal_status,  # #2490: terminal rows override has_ep
)
from .search_engine import (  # type: ignore[import-not-found]
    CONTESTED_VARIANCE_THRESHOLD,
    # #11702: the ONE presence vocabulary. The canonical ``ep`` block is the
    # block `/v1/context`, the volunteer reflex and the pushed webhook all
    # serve, so it must report the same state as `annotate_ep_batch` and the
    # rankers — otherwise the fabricated 0.5 survives on the largest
    # agent-facing read surface while search reports null for the same claim.
    EP_STATE_ABSENT,
    EP_STATE_BASELINE,
    EP_STATE_MEASURED,
    _beta_mean,  # #11702: the ONE Beta mean (replaces this module's local copy)
    _beta_variance,  # #11702: the ONE Beta width (replaces this module's local copy)
    _exclude_status_clause,
    belief_mirror,
    belief_value,  # #11702: the ONE Python belief VALUE reader
    ep_has_belief_cypher,
    ep_measured_cypher,  # #3276: has_ep == EP measured (baseline prior != measured)
    ep_pair_stored,  # #11702: a variance needs BOTH α/β operands stored
    ep_state,
    ep_state_resolve,
    fetch_point_epistemic_state,
    numeric_operand,  # #11702: the ONE total numeric coercion (bool != number)
)

logger = logging.getLogger(__name__)

# ── W4 flag ────────────────────────────────────────────────────────────────
# Default OFF (production exposure gated by the epic's user-exposure gate —
# both conditions must hold before the flip). Tests / dev / the A11 pilot set
# it explicitly, mirroring the flag-gated rollout precedent.
W4_FLAG_ENV = "TORTOISE_W4_ENRICHMENT"

# ── Budgets (plan §3.1.1 — pinned by the S6 contract test) ────────────────
W4_MAX_SUPPORT = 3
W4_MAX_CONFLICTS = 2
W4_MAX_TRADEOFFS = 5
W4_MAX_DIG_DEEPER = 3

# ── Dig-deeper deterministic label registry (UXD 4 + ONTOLOGY §5) ─────────
# kind → label verb phrase. NEVER LLM prose — the label is derived from the
# kind + target only.
DIG_DEEPER_LABELS: dict[str, str] = {
    "supports": "read supports",
    "nand": "read the counterargument (NAND)",
    "tradeoff": "weigh the alternatives",
    "superseded": "see what changed",
}
DIG_DEEPER_KINDS: tuple[str, ...] = tuple(DIG_DEEPER_LABELS)

# Snippet cap for content_snippet/successor_label fields (privacy-safe
# synopses — world-visibility only, ≤ 160 chars per ux-research).
SNIPPET_MAX = 160

def w4_enrichment_enabled() -> bool:
    """Resolve the W4 enrichment flag (honored on ALL enriched surfaces).

    Default OFF — production exposure is gated by the epic's user-exposure
    gate. Truthy values: the declared contract (1/true/yes/on) — #4097.
    """
    return is_truthy(os.environ.get(W4_FLAG_ENV))


# ── The shared assembly ────────────────────────────────────────────────────
# Terminal-exclusion uses search_engine._exclude_status_clause (the canonical
# filter — ALSO excludes legacy ``outdated=true`` points with no status, so the
# why-block's active-structure reads stay consistent with the search/recall
# surfaces that feed the items).


# ⛔ #11702 cycle 12: this module used to carry its OWN `_beta_variance` and
# `_mean` ("kept local to avoid a second import surface"). That second spelling
# of the arithmetic is the defect class this issue removes, and by cycle 11 it
# had drifted: the local `_beta_variance` had no finiteness or sign guard, so
# the CANONICAL why block (`/v1/context`, the volunteer reflex, webhooks) was
# the one surface still emitting a negative `variance` (-0.25), a `NaN` onto the
# JSON wire (Starlette renders with `allow_nan=False`, so that is a 500), a 0.0
# confidence at max-disbelief for an overflowing pair, and a ZeroDivisionError
# for a subnormal one — which discarded the WHOLE batch's enrichment, not just
# the offending point (#8504's failure shape). The one definition lives in
# `search_engine`; both names are imported at the top of this module.


def _snippet(text: str) -> str:
    text = (text or "").strip()
    return text[:SNIPPET_MAX]


def _mitigation_text(text: str | None) -> str | None:
    """Mitigation display text — strips the stored ``[MITIGATION] `` prefix
    (mitigate_operator writes content='[MITIGATION] <reason>')."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("[MITIGATION]"):
        text = text[len("[MITIGATION]"):].lstrip()
    return _snippet(text) or None


def _severity(mean: float) -> str:
    """Deterministic NAND severity from the counter-claim's persisted EP
    mean: a confident counterargument is a high-severity dispute."""
    return "high" if mean >= 0.6 else "medium"


def _assemble_supports(rows: list, by_id: dict[str, dict], *,
                       merge: bool = False,
                       operator_leg: bool = False) -> None:
    """Fold support-chain rows into the blocks. Collect ALL candidates per
    point (dedup by point id), then sort by (weight desc, point_id) and
    truncate to the ≤3 cap — the selected subset is the strongest ≤3 and
    the ordering is deterministic regardless of Cypher row order.

    ``merge=True`` (the second leg) ADDS the leg's candidates into the
    chain the first leg already built instead of overwriting it — a point
    with BOTH operator-mediated supports AND direct statement→statement
    IMPL edges must surface the union of both legs (mixed-edge points were
    silently dropping the operator-mediated leg before this fix).

    ``operator_leg=True`` reads the FOUR extra columns of
    ``_SUPPORT_OP_CYPHER`` (the mediating operator's id / type / label and its
    mitigation) — #3990: objective 4 requires the operator, not just the
    supporting claim, so the block must name it. The DIRECT leg carries no
    operator and passes ``operator_leg=False``; its entries still get all four
    keys set to ``None`` so every support entry has ONE shape (a present null
    is a measured "no operator", never an unread dimension — §3.1.3)."""
    pending: dict[str, dict[str, dict]] = {}
    # The deterministic tie-break rank per (target, supporter) is kept OUT of
    # the emitted entry so the block's key set stays exactly the contract's.
    rank: dict[tuple[str, str], tuple[str, str]] = {}
    for row in rows:
        if operator_leg:
            (pid, sup_id, content, sup_conf, pa, pb, ea, eb,
             op_id, op_type, op_label, mitigation, mit_id) = row
        else:
            pid, sup_id, content, sup_conf, pa, pb, ea, eb = row
            op_id = op_type = op_label = mitigation = mit_id = None
        if not pid or not sup_id:
            continue
        # #11702: `weight` is the supporter's PERSISTED belief, or None when it
        # has none -- never the 0.5 the `coalesce(…, 1.0)` default produced.
        weight = belief_value(confidence=sup_conf, posterior_alpha=pa,
                              posterior_beta=pb, ep_alpha=ea, ep_beta=eb,
                              round_to=4)
        entry = {
            "point_id": sup_id,
            "content_snippet": _snippet(content),
            "edge": "IMPL",
            "weight": weight,
            # #3990 — the WHY layer's operator leg. ``operator_label`` is
            # the Layer-3 predicate (ONTOLOGY §8); ``operator_type`` is the
            # epistemic mechanism. It is ``IMPL`` on every entry today
            # because this leg traverses ``-[:IMPL]->`` only — a NAND
            # operator over the same pair is a CONFLICT and surfaces in
            # ``conflicts``, never here. ``mitigation`` is on THAT operator
            # (#4937: a mitigation is a separate Point attacking the
            # operator bridge), which is why the operator id had to exist
            # first — the mitigation is unreachable without it.
            "operator_id": op_id or None,
            "operator_type": op_type or None,
            "operator_label": op_label or None,
            "mitigation": _mitigation_text(mitigation),
        }
        # Deterministic tie-break rank for this (target, supporter): the pair
        # (operator_id, mitigation_id). The "no operator" sentinel orders a
        # DIRECT row after every operator row — within ONE call. The direct leg
        # runs as a SEPARATE call (merge=True) and its union keeps the existing
        # entry via ``setdefault``, so cross-leg precedence comes from that call
        # order, not from this rank; the sentinel exists so a future single-call
        # merge cannot silently invert it.
        key = (op_id or "\uffff", mit_id or "\uffff")
        existing = pending.get(pid, {}).get(sup_id)
        if existing is None:
            pending.setdefault(pid, {})[sup_id] = entry
            rank[(pid, sup_id)] = key
            continue
        # ONE entry per supporter (the ≤3 cap and the merge leg both dedup by
        # point id), so a supporter mediated by MORE THAN ONE operator cannot
        # be represented faithfully — and an operator mitigated MORE THAN ONCE
        # has the same shape. Pick the LOWEST (operator, mitigation) pair
        # rather than whichever row the storage layer returned first: row order
        # is not guaranteed, and an arbitrary pick made the surfaced operator —
        # and with it the surfaced mitigation TEXT — insertion-order dependent
        # (the same pair reported a different reason depending on which edge
        # was written first). #9962: the mitigation leg is read live-only, so
        # "more than once" here is several CURRENT mitigations; a withdrawn one
        # is not a candidate and can no longer win the tie-break.
        if key < rank[(pid, sup_id)]:
            pending[pid][sup_id] = entry
            rank[(pid, sup_id)] = key
    for pid, chain_map in pending.items():
        # #11702: absent-supporter TIER -- `weight` is None for a supporter
        # with no persisted belief, and it sorts after EVERY weighted one so
        # the ≤3 cap cannot be filled by fabricated 0.5s (measured: two absent
        # supporters crowded a real 0.4 reading out of the chain).
        chain = sorted(chain_map.values(),
                       key=lambda s: (s["weight"] is None,
                                      -(s["weight"] or 0.0),
                                      s["point_id"]))
        if merge and by_id[pid].get("support_chain"):
            # Union with the first leg's already-selected candidates, then
            # re-sort + re-cap across BOTH legs (deterministic strongest ≤3).
            # Dedup by point_id first — a supporter reachable through both
            # the operator-mediated and the direct edge must appear once.
            by_sup: dict[str, dict] = {
                s["point_id"]: s for s in by_id[pid]["support_chain"]}
            for s in chain:
                by_sup.setdefault(s["point_id"], s)
            chain = sorted(by_sup.values(),
                           key=lambda s: (s["weight"] is None,
                                          -(s["weight"] or 0.0),
                                          s["point_id"]))
        by_id[pid]["support_chain"] = chain[:W4_MAX_SUPPORT]


def _assemble_conflicts(rows: list, by_id: dict[str, dict]) -> None:
    """Fold NAND rows into the blocks (dedup by nander id). Collect ALL
    candidates, then deterministic selection: highest severity first (high
    > medium), then point_id — truncated to the ≤2 cap. The TARGET's ep
    read lives in a dedicated batch query (_EP_CYPHER) so it survives for
    EVERY point — including points with zero active NANDers (this query
    only returns rows for points that HAVE NANDers)."""
    pending: dict[str, dict[str, dict]] = {}
    for row in rows:
        if not row or not row[0] or not row[1]:
            continue
        pid = row[0]
        nid = row[1]
        if nid not in pending.setdefault(pid, {}):
            text = row[2]
            # #11702: the counter-claim's PERSISTED belief -- a direct Point's
            # own columns, else the NAND operator's. `None` when neither has
            # one, and that is a TIER of its own below every measured severity
            # (an absent belief used to weigh 0.5 and take a real tier, so the
            # ≤2 cap could be filled by a claim nothing measured).
            weight = belief_value(
                confidence=row[3], posterior_alpha=row[4],
                posterior_beta=row[5], ep_alpha=row[6], ep_beta=row[7])
            if weight is None:
                weight = belief_value(
                    confidence=row[8], posterior_alpha=row[9],
                    posterior_beta=row[10], ep_alpha=row[11], ep_beta=row[12])
            pending[pid][nid] = {
                "point_id": nid,
                "content_snippet": _snippet(text),
                "severity": None if weight is None else _severity(weight),
            }
    for pid, nand_map in pending.items():
        nands = sorted(
            nand_map.values(),
            key=lambda n: (n["severity"] is None,
                           0 if n["severity"] == "high" else 1,
                           n["point_id"]))
        by_id[pid]["nands"] = nands[:W4_MAX_CONFLICTS]


def _assemble_tradeoffs(rows: list, by_id: dict[str, dict]) -> None:
    """Fold alternative rows into the blocks. A point is a DECISION point
    when it is a decision-kind point with outgoing IMPL alternatives, OR it
    carries ≥ 2 distinct outgoing alternatives with option-kind children
    (the file_decision / option structure). tradeoffs are emitted only for
    decision points (absent = dimension not computed)."""
    pending: dict[str, dict[str, dict]] = {}
    kinds: dict[str, str] = {}
    for row in rows:
        if not row or not row[0]:
            continue
        pid = row[0]
        kinds.setdefault(pid, row[1] or "")
        alt_id = row[2]
        if not alt_id:
            continue
        alts = pending.setdefault(pid, {})
        if alt_id not in alts:
            alts[alt_id] = {
                "point_id": alt_id,
                "label": _snippet(row[3]),
                "kind": row[4] or "",
                # #3990: the connecting operator was already READ (row[5]) to
                # reach the mitigation and then discarded — the alternative's
                # own explanation needs it. Same additive-shape rule as the
                # support leg: present, ``None`` when the edge carries none.
                "operator_id": row[5] or None,
                # #11702: `None` for an alternative with no persisted belief
                # (never the 0.5 the `coalesce(…, 1.0)` default produced).
                "ep_weight": belief_value(
                    confidence=row[7], posterior_alpha=row[8],
                    posterior_beta=row[9], ep_alpha=row[10], ep_beta=row[11],
                    round_to=4),
                "mitigation": _mitigation_text(row[6]),
            }
    for pid, alts in pending.items():
        alt_list = list(alts.values())
        # Decision points only (≥ 2 alternatives — "weigh the alternatives"
        # is meaningless for a single option; a decision-kind point whose
        # other alternatives were retracted degrades to NO tradeoffs rather
        # than a one-row false decision).
        if len(alt_list) < 2:
            continue
        if kinds.get(pid) != "decision" and not any(
                a.get("kind") == "option" for a in alt_list):
            continue
        block = by_id[pid]
        # Contract shape: {point_id, label, ep_weight, mitigation,
        # operator_id} (#3990 — the connecting operator) — the option-kind
        # marker is internal detection only, never emitted.
        # Bounded: strongest W4_MAX_TRADEOFFS by EP weight (the other
        # dimensions are capped; a decision with N alternatives must not
        # make every enriched recall of it emit N entries).
        block["tradeoffs"] = sorted(
            [
                {k: v for k, v in a.items() if k != "kind"}
                for a in alt_list
            ],
            key=lambda t: (t["ep_weight"] is None,
                           -(t["ep_weight"] or 0.0),
                           t["point_id"]))[:W4_MAX_TRADEOFFS]


def _assemble_supersession(by_id: dict[str, dict],
                           state: dict[str, dict]) -> None:
    """Project the flat supersession state (fetch_point_epistemic_state) into
    the canonical view: full enum status + superseded_by/supersedes +
    successor_label (plan §3.1.4 — flat keys stay canonical). Only points
    present in the state fetch get a view (the fetch returns rows only for
    existing Points — non-Point ids never get a fabricated view)."""
    for pid, block in by_id.items():
        st = state.get(pid)
        if st is None:
            continue
        status = st.get("status") or "live"
        sb = st.get("superseded_by")
        superseded_by = None
        successor_label = None
        if sb and sb.get("id"):
            superseded_by = {
                "point_id": sb["id"],
                "content_snippet": _snippet(sb.get("content_snippet") or ""),
            }
            successor_label = superseded_by["content_snippet"] or sb["id"]
        block["supersession"] = {
            "status": status,
            "superseded_by": superseded_by,
            "supersedes": [
                {"point_id": s["id"],
                 "content_snippet": _snippet(s.get("content_snippet") or "")}
                for s in (st.get("supersedes") or [])
                if s.get("id")
            ],
            "successor_label": successor_label,
        }


def _assemble_ep_rows(rows: list, by_id: dict[str, dict]) -> None:
    """Fold the dedicated persisted α/β read into the canonical ``ep``
    sub-block (flag-first pairing for agents — variance/contested ride the
    block; same formula as annotate_ep_batch / TortoiseEP). Every row is an
    EXISTING :Point node (the query is the existence anchor). A point with NO
    persisted belief in any form carries ``confidence_mean: None`` and
    ``ep_state: "absent"`` (#11702) — never the Beta(1,1) mean 0.5, which
    admitted a never-measured claim through any floor at or under 0.5 as if it
    carried neutral evidence. ``variance`` is ``0.0`` unless BOTH operands of
    the α/β pair are stored (`search_engine.ep_pair_stored`, the same rule
    `annotate_ep_batch` and all three rankers apply): the `coalesce(…, 1.0)`
    inputs otherwise answer the Beta(1,1) MAXIMUM 1/12, i.e. "there is no
    belief here, and it is maximally uncertain", and an α-only half pair
    turned `contested` on so the volunteer surfaced "read the
    counterargument" for a claim with zero counterarguments. ``has_ep``/
    ``measured`` are unchanged by #11702. "Absence of measurement is not low
    support" still holds — the belief term simply does not participate.

    #2490: the gate is a PROJECTION-side has_ep OVERRIDE, NOT a WHERE
    insertion — why() explicitly serves terminal ids in the supersession
    block, so filtering them out of _EP_CYPHER's WHERE would drop the very
    rows why must present. Terminal rows (status in the terminal vocab OR
    the legacy outdated flag) read has_ep=False + contested=False even when
    EP measured them pre-terminalization (their posterior decayed to vacuity
    at the write)."""
    for row in rows:
        if not row or not row[0]:
            continue
        pid = row[0]
        # #11702 cycle 16: TOTAL coercion on both operands and the MIRROR. A
        # stored non-numeric value raised here and the caller's guard dropped
        # the whole block map (`{}`), so `why` disappeared for every id in the
        # batch — measured: `W4 ep read failed: could not convert string to
        # float` and no `ep` block at all, on the surface every agent read
        # goes through.
        a, b = numeric_operand(row[1]) or 0.0, numeric_operand(row[2]) or 0.0
        has_ep = bool(row[3])
        # #2490: terminal rows never surface a decayed posterior as measured
        # EP — has_ep=False + contested=False (projection-side override).
        terminal = len(row) > 5 and is_terminal_status(row[4], bool(row[5]))
        if terminal:
            has_ep = False
        # #3276: explicit measurement state — see EpBreakdown. baseline_set
        # (index 6) marks a LIVE prior-only claim (declared #2199 baseline, no
        # EP measurement): its confidence_mean is the PRIOR mean, not measured.
        # A TERMINAL row is neither measured nor prior-only (its 0.5 is the
        # #2490 decayed posterior) — the terminal gate also clears `baseline`.
        baseline_set = bool(row[6]) if len(row) > 6 else False
        measured = bool(has_ep)
        variance = _beta_variance(a, b)
        # #11702: the state, resolved through the SAME vocabulary every other
        # surface uses (presence from the raw columns, then the #2490 fold).
        # Index map for `_EP_CYPHER`: 0 id, 1 alpha, 2 beta, 3 measured,
        # 4 status, 5 outdated, 6 baseline_set, 7 has_belief, 8 posterior_alpha,
        # 9 ep_alpha, 10 confidence, 11 posterior_beta, 12 ep_beta. The len
        # guard covers the pre-#11702 7-col contract doubles, which carry a
        # belief by construction.
        if len(row) > 12:
            state = ep_state(posterior_alpha=row[8], ep_alpha=row[9],
                             confidence=row[10], posterior_beta=row[11],
                             ep_beta=row[12], baseline_set=baseline_set)
        else:
            state = (EP_STATE_MEASURED if measured
                     else EP_STATE_BASELINE)
        state = ep_state_resolve(state, measured=measured)
        mirror = row[10] if len(row) > 10 else None
        # #11702: the VARIANCE is only a posterior statistic when the PAIR is
        # stored. `_EP_CYPHER` coalesces α/β to `1.0`, so a row storing only
        # `posterior_alpha` (accepted by `update_point`) got `b = 1.0`, a
        # variance of the MAXIMUM 1/12, and therefore `contested: true` on a
        # block that reports `ep_state: "absent"` and `confidence_mean: null` —
        # "there is no belief here, and it is a maximum-entropy one". The
        # volunteer then emitted "read the counterargument" for a claim with
        # ZERO counterarguments, and the why-suite's false-positive arm counts
        # `ep.contested` as inventing a contradiction. Half a pair has no
        # width, exactly as it has no mean.
        # #11702 cycle 9: the rule is `search_engine.ep_pair_stored`, the same
        # one all three rankers and `annotate_ep_batch` now apply — a second
        # spelling here is how the surfaces drifted apart in the first place
        # (this guard existed only in `why`, so the rankers kept reporting a
        # fabricated 1/12 for every absent claim).
        pair_stored = (len(row) <= 12
                       or ep_pair_stored(posterior_alpha=row[8],
                                         ep_alpha=row[9],
                                         posterior_beta=row[11],
                                         ep_beta=row[12]))
        if not pair_stored:
            variance = 0.0
        by_id[pid]["ep"] = {
            # #11702: `None` when nothing is persisted — never the Beta(1,1)
            # mean. This is the block a caller reads to decide whether to
            # surface/notify, so a fabricated 0.5 here admitted a
            # never-measured claim through every `min_confidence <= 0.5`
            # floor (volunteer.py, why_snapshot.py) as if it carried neutral
            # evidence.
            #
            # The VALUE follows the same precedence every other read uses —
            # the `n.confidence` mirror first (index 10, the served belief),
            # the coerced α/β mean as the fallback for a node the flush never
            # reached. α/β-only here made a mirror-only node (what
            # `compute_confidence` / `dream` write, and what a pre-#852 node
            # carries) report 0.5 on this one surface while annotate, all
            # three rankers and the subgraph read its real 0.95.
            "confidence_mean": (
                None if state == EP_STATE_ABSENT
                else round(belief_mirror(mirror), 4)
                if belief_mirror(mirror) is not None
                else round(_beta_mean(a, b), 4)),
            "ep_state": state,
            "variance": round(variance, 6),
            # #11702: an ABSENT state is never contested — there is no
            # posterior width to be wide (see the `pair_stored` guard above;
            # this is the belt to that brace, so a future row shape cannot
            # reintroduce "no belief, but maximally uncertain").
            "contested": (measured and state != EP_STATE_ABSENT
                          and variance > CONTESTED_VARIANCE_THRESHOLD),
            "has_ep": measured,
            "measured": measured,
            "baseline": baseline_set and not measured and not terminal,
        }


def _materialize_conflicts(by_id: dict[str, dict]) -> None:
    """Fold the top-level ``nands`` into the canonical ``conflicts`` block
    ({contested, nands}) — the §3.1.4 shape — when the point is contested
    or carries ≥ 1 active NAND (absent otherwise: an uncontested point
    carries no conflict noise)."""
    for block in by_id.values():
        nands = block.get("nands") or []
        ep = block.get("ep") or {}
        if nands or ep.get("contested"):
            block["conflicts"] = {
                "contested": bool(ep.get("contested")),
                "nands": nands,
            }
        block.pop("nands", None)


def _assemble_dig_deeper(by_id: dict[str, dict]) -> None:
    """Deterministic labeled pointers (UXD 4). Order pins the precedence
    rule: supports → nand → tradeoff → superseded, capped at 3 — at a
    decision point the tradeoff pointer therefore takes precedence over a
    superseded pointer within the ≤ 3 cap, and superseded-kind pointers
    appear ONLY when supersession data exists."""
    for block in by_id.values():
        pointers: list[dict] = []
        chain = block.get("support_chain") or []
        if chain:
            pointers.append({
                "label": DIG_DEEPER_LABELS["supports"],
                "kind": "supports",
                "target": chain[0]["point_id"],
            })
        nands = (block.get("conflicts") or {}).get("nands") or []
        if nands:
            pointers.append({
                "label": DIG_DEEPER_LABELS["nand"],
                "kind": "nand",
                "target": nands[0]["point_id"],
            })
        tradeoffs = block.get("tradeoffs") or []
        if tradeoffs:
            pointers.append({
                "label": DIG_DEEPER_LABELS["tradeoff"],
                "kind": "tradeoff",
                "target": tradeoffs[0]["point_id"],
            })
        supersession = block.get("supersession") or {}
        if supersession.get("superseded_by"):
            pointers.append({
                "label": DIG_DEEPER_LABELS["superseded"],
                "kind": "superseded",
                "target": supersession["superseded_by"]["point_id"],
            })
        if pointers:
            block["dig_deeper"] = pointers[:W4_MAX_DIG_DEEPER]


# ══════════════════════════════════════════════════════════════════════════
# #7927 RESIDUAL — THE ``:Point``-ONLY SCOPE OF THIS FILE IS DELIBERATE AND
# PENDING A LANE, NOT AN ACCIDENT OF HISTORY. EVERY read in this block is
# Point-anchored: the two support reads below, the conflicts read, the
# tradeoffs read, and the supersession read
# (``search_engine.fetch_point_epistemic_state``). The EXISTENCE ANCHOR among
# them is ``_EP_CYPHER``.
#
# WHAT A PARTIAL WIDENING DOES, stated as the mechanism rather than as a maxim,
# because the mechanism is what a reader will test their observation against.
# ``_EP_CYPHER`` runs FIRST and its ids become ``existing_ids``. Every dependent
# read still runs and still folds its rows into ``blocks`` — so a widened
# conflicts read DOES return rows. They are discarded at the END: the output
# loop in ``assemble_why_blocks`` skips every id absent from ``existing_ids``.
# So widening one dependent read alone changes no output, and it can look like
# the widening "did not work" rather than like the anchor was not widened.
#
# CONSEQUENCE, recorded rather than implied: a non-Point belief is absent from
# the why layer, while ``ranking.resolve_contested_relevance`` — widened by
# #7927 — can fire ``W4_CONTESTED_BOOST`` on a conflict this file will not
# render. The same note lives on that function, which is where the divergence is
# actually experienced. The residual is the anchor plus those five dependent
# reads, tracked on #7927 as its own lane: widening the anchor is a new
# why-layer contract (block shape, per-label text fields, the ``:Event``
# ``eventStatus`` vocabulary), not a read widening.
# ══════════════════════════════════════════════════════════════════════════

# Support-chain queries (bounded). Operator-mediated: the operator's INPUT
# source at idx 0 (the evidence/statement that implies the target through the
# operator — create_operator writes source → INPUT idx0 → op → IMPL → target).
# Direct: a bare statement→statement IMPL edge (the reification rule).
_SUPPORT_OP_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    # #6976: load-bearing `WITH n` — without it FalkorDB 6.0.0 drops the id
    # predicate at the re-binding MATCH below (foreign rows).
    "WITH n "
    "MATCH (sup:Point)-[r:INPUT]->(op:Point {is_operator:true})-[:IMPL]->(n) "
    f"WHERE r.idx = 0 AND sup.id <> n.id "
    f"AND (sup.is_operator = false OR sup.is_operator IS NULL) AND {_exclude_status_clause('sup')} "
    # #3990: the operator that mediates THIS support rides the same traversal
    # — objective 4 requires the operator (id + kind) plus any mitigation on
    # it, and a second query per support would be a new read on the hot path.
    # The mitigation is OPTIONAL (an operator need not be mitigated).
    #
    # ⛔ #9962 — BOTH ``mitigated_by`` legs in this module (this one and
    # ``_TRADEOFFS_CYPHER`` below) read only mitigations that PRICE the
    # operator (``live._live_only``, the predicate ``weights.py``'s
    # ``compute_operator_weight`` applies). Before it, this leg's tie-break
    # (lowest mitigation id) picked the OLDEST mitigation, and ULIDs ascend —
    # so after mitigate → retract → re-mitigate (the #9962 re-assertion, which
    # mints a NEW Point and leaves the retracted one in place) the why-block
    # named the WITHDRAWN mitigation while the number was damped by the live
    # one. A predicate HERE, rather than a status field in the emitted entry,
    # keeps the surfaced text and the arithmetic the same object.
    "OPTIONAL MATCH (op)-[:mitigated_by]->(mit:Point) "
    f"WHERE {_live_only('mit.status')} "
    "RETURN n.id, sup.id, sup.content, "
    # #11702: the RAW belief columns, not `coalesce(…, 1.0)` -- the default
    # supplied a missing operand and made an ABSENT supporter weigh 0.5 (and a
    # β-only one 0.25), which then ordered the ≤3 cap. `belief_value` is the
    # ONE Python reader, so this leg cannot re-decide presence.
    "  sup.confidence, sup.posterior_alpha, sup.posterior_beta, "
    "  sup.ep_alpha, sup.ep_beta, "
    # mit.id rides the same traversal so an operator mitigated MORE THAN ONCE
    # resolves deterministically (lowest mitigation id) instead of to whichever
    # row the storage layer returned first — the sibling of the operator
    # tie-break below, and the same defect shape.
    "  op.id, op.op_type, op.label, mit.content, mit.id"
)
_SUPPORT_DIRECT_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "WITH n "
    "MATCH (sup:Point)-[r:IMPL]->(n) "
    f"WHERE sup.id <> n.id "
    f"AND (sup.is_operator = false OR sup.is_operator IS NULL) AND {_exclude_status_clause('sup')} "
    "RETURN n.id, sup.id, sup.content, "
    "  sup.confidence, sup.posterior_alpha, sup.posterior_beta, "
    "  sup.ep_alpha, sup.ep_beta"
)
# Conflicts (NANDer-only; the target ep read is the dedicated _EP_CYPHER).
# The NANDer is either a direct statement (operator-less NAND edge) or a NAND
# operator whose counterargument is its INPUT source at idx 0 and whose NAND
# edge INTO the target sits at idx > 0 (idx 0 is the operator's source link —
# a NAND operator IS the counter-claim plumbing in the Tortoise model, so
# surface the actual counterargument statement; the snippet prefers the
# counterargument's own content over the operator label).
_CONFLICTS_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "WITH n "
    "MATCH (c:Point)-[r:NAND]->(n) "
    "OPTIONAL MATCH (src:Point)-[ri:INPUT]->(c) "
    "WITH n, c, r, src, ri "
    "WHERE c.id <> n.id AND ("
    "  (c.is_operator = true AND r.idx > 0 AND ri.idx = 0) OR "
    "  (c.is_operator = false OR c.is_operator IS NULL)) "
    f"AND {_exclude_status_clause('c')} "
    "RETURN n.id, coalesce(src.id, c.id), "
    "  coalesce(src.content, c.label, c.content, ''), "
    # #11702: the RAW belief columns of the counter-claim (mirror first, then
    # its own posterior/prior pair), NOT `coalesce(…, 1.0)` -- the 1.0 default
    # supplied a missing operand, so an ABSENT nander weighed the neutral 0.5
    # and then took a real severity TIER on it.
    "  src.confidence, src.posterior_alpha, src.posterior_beta, "
    "  src.ep_alpha, src.ep_beta, "
    "  c.confidence, c.posterior_alpha, c.posterior_beta, c.ep_alpha, c.ep_beta"
)
# Dedicated persisted ep read for every requested Point (single-node α/β —
# never full propagation). Runs unconditionally so the canonical ``ep`` block
# is REAL for every existing point — including points with zero active
# NANDers (a fabricated zeroed ep for a measured point would be wrong data).
# Also serves as the existence anchor: only ids returned here are existing
# :Point nodes (non-Point ids never get a fabricated block).
_EP_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "RETURN n.id, "
    "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
    "  coalesce(n.posterior_beta, n.ep_beta, 1.0), "
    # #3276: has_ep == EP MEASURED (ep_measured_cypher) — a #2199 baseline
    # prior alone is prior-only, never a measured confidence.
    f"  {ep_measured_cypher('n')}, "
    # #2490: status/outdated ride the RETURN so _assemble_ep_rows can apply
    # the projection-side has_ep override for terminal rows (why must serve
    # terminal ids in the supersession block — the exclusion is NOT a WHERE
    # insertion here).
    "  n.status, coalesce(n.outdated, false), "
    # #3276: baseline_set rides LAST-3 (index 6) so the pre-#3276 6-col row
    # shape keeps its index mapping under the len guard.
    "  coalesce(n.baseline_set, false), "
    # #11702: PRESENCE + the RAW presence columns (7/8/9). Without these the
    # block answers `_mean(coalesce(…, 1.0), …)` = 0.5 for a claim with no
    # belief at all — the defect this issue removes, one level above the
    # coalesce that was fixed in `annotate_ep_batch`.
    f"  {ep_has_belief_cypher('n')}, "
    # #11702 cycle 3: the β columns ride so `ep_state()` can apply the same
    # α+β <= 0 rule the predicate does — a stored (0, 0) pair is no belief.
    "  n.posterior_alpha, n.ep_alpha, n.confidence, "
    "  n.posterior_beta, n.ep_beta"
)
# Tradeoffs (decision points): the point is the operator SOURCE (INPUT idx 0)
# and the alternatives are the operator's IMPL TARGETS at idx > 0 (the
# file_decision / option structure — idx 0 is the operator's source self-link,
# never an alternative); mitigations ride the connecting operator.
_TRADEOFFS_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "WITH n "
    "MATCH (n)-[ri:INPUT]->(op:Point {is_operator:true})-[r2:IMPL]->(alt:Point) "
    f"WHERE ri.idx = 0 AND r2.idx > 0 "
    f"AND (alt.is_operator = false OR alt.is_operator IS NULL) AND {_exclude_status_clause('alt')} "
    # #9962 — the SAME liveness predicate the support leg above applies, for
    # the same reason: a withdrawn (or draft) mitigation computes no dampening,
    # so it must not be presented as one. Before it, a decision point's
    # ``tradeoffs[]`` named the retracted mitigation as the alternative's
    # explanation — and, because ``_assemble_tradeoffs`` keeps the first row it
    # sees per alternative, which of two attached mitigations won was row-order
    # dependent. Only CURRENT mitigations can satisfy the filter now, so the
    # remaining ambiguity is limited to the #2315 import artifact (two LIVE
    # mitigations on one operator compose by max, and either explains the
    # alternative); no SDK door mints that shape.
    "OPTIONAL MATCH (op)-[:mitigated_by]->(m:Point) "
    f"WHERE {_live_only('m.status')} "
    "RETURN n.id, n.pointKind, alt.id, alt.content, alt.pointKind, op.id, m.content, "
    # #11702: raw columns (see `_CONFLICTS_CYPHER`) -- `coalesce(…, 1.0)` gave
    # an absent alternative `ep_weight` 0.5, which then SORTED it above a
    # measured 0.4 one (the same inversion the support chain just had fixed).
    "  alt.confidence, alt.posterior_alpha, alt.posterior_beta, "
    "  alt.ep_alpha, alt.ep_beta"
)


def assemble_why_blocks(
    proj,
    point_ids: list[str],
    *,
    max_support: int = W4_MAX_SUPPORT,
    max_conflicts: int = W4_MAX_CONFLICTS,
    max_dig_deeper: int = W4_MAX_DIG_DEEPER,
) -> dict[str, dict]:
    """Assemble canonical §3.1.4 why-blocks for a batch of Point ids.

    ONE read-only, bounded, zero-LLM assembly: a fixed number of batch
    Cypher queries (supports×2 / conflicts / supersession / tradeoffs /
    dedicated persisted ep read), persisted single-node α/β reads only —
    never full EP propagation, never a per-result loop. Fail-open: any
    graph error logs and returns ``{}``
    (the caller degrades to "no enrichment", never breaks the recall turn).

    Only ids that exist as :Point nodes get a block (the ep read is the
    existence anchor — a non-Point id never receives a fabricated block).

    Returns ``{point_id: canonical_block}`` where the canonical block is::

        {point_id, support_chain: [...], ep: {...}, conflicts: {...}?,
         supersession: {...}, tradeoffs: [...]?, dig_deeper: [...]?}

    ``conflicts`` / ``tradeoffs`` / ``dig_deeper`` are absent when their
    dimension is not computed / empty (uncontested / non-decision points).
    """
    ids = [pid for pid in (point_ids or []) if pid]
    if not ids:
        return {}
    blocks: dict[str, dict] = {pid: {} for pid in ids}
    try:
        g = proj.g
        # 1. Persisted ep read + existence anchor (single-node α/β — the
        #    dedicated query guarantees a REAL ep for EVERY point, including
        #    points with zero active NANDers; only existing :Point ids
        #    return rows).
        try:
            ep_rows = g.query(_EP_CYPHER, params={"ids": ids}).result_set
            existing_ids = {row[0] for row in ep_rows}
            _assemble_ep_rows(ep_rows, blocks)
        except Exception as e:  # noqa: BLE001, RUF100 — whole-assembly fail-open
            logger.warning("W4 ep read failed: %s", e)
            return {}
        # 2. Support chain (≤ max_support per point) — bounded traversal.
        #    Two legs (operator-mediated + direct statement→statement IMPL);
        #    the SECOND leg MERGES into the first (a mixed-edge point keeps
        #    both legs' supports — never a silent clobber of one leg).
        try:
            _assemble_supports(
                g.query(_SUPPORT_OP_CYPHER, params={"ids": ids}).result_set,
                blocks,
                operator_leg=True,
            )
            _assemble_supports(
                g.query(_SUPPORT_DIRECT_CYPHER, params={"ids": ids}).result_set,
                blocks,
                merge=True,
            )
        except Exception as e:  # noqa: BLE001, RUF100 — fail-open per dimension
            logger.warning("W4 support-chain read failed: %s", e)
        # 3. NAND conflicts (≤ max_conflicts) — NANDer-only query.
        try:
            _assemble_conflicts(
                g.query(_CONFLICTS_CYPHER, params={"ids": ids}).result_set,
                blocks,
            )
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("W4 conflict read failed: %s", e)
        # 4. Supersession view — persisted flat state projection.
        try:
            _assemble_supersession(
                blocks,
                fetch_point_epistemic_state(g, ids),
            )
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("W4 supersession read failed: %s", e)
        # 5. Tradeoffs — decision points only (≥ 2 alternatives).
        try:
            _assemble_tradeoffs(
                g.query(_TRADEOFFS_CYPHER, params={"ids": ids}).result_set,
                blocks,
            )
        except Exception as e:  # noqa: BLE001, RUF100
            logger.warning("W4 tradeoff read failed: %s", e)
        # 6. Pure projections (no reads): conflicts + dig_deeper.
        _materialize_conflicts(blocks)
        _assemble_dig_deeper(blocks)
    except Exception as e:  # noqa: BLE001, RUF100 — whole-assembly fail-open
        logger.warning("W4 why-block assembly failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    for pid, block in blocks.items():
        if pid not in existing_ids:
            # Not an existing :Point node (e.g. an Event/Subject ULID that a
            # raw-row regex happened to match) — never fabricate a block.
            continue
        block = dict(block)
        block["point_id"] = pid
        if "support_chain" not in block:
            block["support_chain"] = []
        out[pid] = block
    return out


# ── Enriched-item projection (§3.1.1 / §6.1 — search/recall surfaces) ─────

def _contested_from_item(item: dict, block: dict | None) -> bool:
    """Derive the item's contestation signal: the item's own ``ep`` wins
    (annotate_ep_batch already computed it), else the canonical block's.
    Tolerant coercion: a non-float variance on a rogue item is treated as
    unmeasured (False) — never raises into the recall turn."""
    ep = item.get("ep")
    if isinstance(ep, dict):
        if ep.get("contested"):
            return True
        if ep.get("has_ep"):
            try:
                variance = float(ep.get("variance") or 0.0)
            except (TypeError, ValueError):
                variance = 0.0
            if variance > CONTESTED_VARIANCE_THRESHOLD:
                return True
    if block and isinstance(block.get("ep"), dict):
        return bool(block["ep"].get("contested"))
    return False


def enrich_items(proj, items: list[dict]) -> list[dict]:
    """Add the W4 additive keys to SearchResult-style item dicts.

    The items' own ``ep`` (already computed by the search path) stays
    canonical — only the NEW keys are added. ``warnings`` is inserted
    TOP-OF-ITEM (after ``content`` — flag-first, UXD 3); the W4 keys are
    appended at the tail in contract order (why → conflicts → supersession
    → tradeoffs → dig_deeper). Fail-open: any assembly error returns the
    items unchanged (never breaks the recall turn).
    """
    if not items:
        return items
    if not w4_enrichment_enabled():
        return items
    try:
        ids = [i["id"] for i in items if i.get("id")]
        blocks = assemble_why_blocks(proj, ids)
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("W4 enrichment failed: %s", e)
        return items
    out: list[dict] = []
    try:
        for item in items:
            pid = item.get("id")
            block = blocks.get(pid)
            if block is None:
                out.append(item)
                continue
            out.append(project_item(item, block))
    except Exception as e:  # noqa: BLE001, RUF100 — projection fail-open
        # A projection error (e.g. a non-float-coercible ep.variance on a
        # rogue item) must never break the recall turn — degrade to "no
        # enrichment keys" for the whole batch, byte-identical to flag-off.
        logger.warning("W4 projection failed: %s", e)
        return items
    return out


def project_item(item: dict, block: dict) -> dict:
    """Project one canonical why-block onto an item dict (§3.1.1 shape).

    Pure dict projection (no graph reads). Emits ONLY the additive keys,
    per the empty/null/absent conventions:
      - ``warnings: ["contested"]`` — contested items only, TOP-OF-ITEM;
      - ``why: {support_chain}`` — when the block carries supports (an
        empty support_chain is an empty collection — no why marker,
        never a degradation);
      - ``conflicts`` — contested OR ≥ 1 active NAND; absent otherwise
        (an uncontested point carries no conflict noise);
      - ``supersession`` — the §3.1.4 VIEW ({line, successor_label} — the
        flat status/superseded_by/supersedes keys stay canonical);
      - ``tradeoffs`` — decision points only;
      - ``dig_deeper`` — when ≥ 1 pointer exists.
    """
    out = dict(item)
    contested = _contested_from_item(item, block)
    if contested:
        # TOP-OF-ITEM (flag-first, UXD 3): warnings rides directly after
        # content, before point_kind — a top-down parser sees the dispute
        # first. id/content keep their canonical leading positions.
        out = {}
        if "id" in item:
            out["id"] = item["id"]
        if "content" in item:
            out["content"] = item["content"]
        out["warnings"] = ["contested"]
        for k, v in item.items():
            if k in ("id", "content"):
                continue
            out[k] = v
    # §3.1.4 "top-level ep is canonical": the flag-ON enriched item's ep
    # must be the CANONICAL posterior mean α/(α+β) over the persisted
    # posterior_alpha/ep_alpha coalesce. Post-#2206 annotate_ep_batch
    # computes the identical number (same coalesce + 4dp rounding), so this
    # override is a defensive no-op when both reads agree — retained so an
    # item whose ep came from a legacy/edge-ratio source still projects the
    # canonical belief. Applied AFTER the contested rebuild (which re-copies
    # from item). Copy-then-override — never mutate the caller's dict (out
    # is a shallow copy, ep would alias item["ep"]). Cross-surface
    # consistency: ask/analyze/search report the same belief number for the
    # same point.
    item_ep = item.get("ep")
    block_ep = (block or {}).get("ep")
    if isinstance(item_ep, dict) and isinstance(block_ep, dict) \
            and block_ep.get("has_ep"):
        out["ep"] = dict(item_ep)
        out["ep"]["confidence_mean"] = block_ep["confidence_mean"]
    if block.get("support_chain"):
        out["why"] = {"support_chain": block["support_chain"]}
    if (block.get("conflicts") or {}).get("nands") or contested:
        out["conflicts"] = {
            "contested": contested,
            "nands": (block.get("conflicts") or {}).get("nands") or [],
        }
    ss = block.get("supersession") or {}
    # §3.1.4: the view is a projection of the FLAT canonical keys. When the
    # block's state-fetch degraded (no supersession data on the block), fall
    # back to the item's OWN flat status/superseded_by — NEVER fabricate a
    # "live" line for a point whose flat status is terminal (a superseded
    # predecessor reads superseded even on a degraded read; absent = not
    # computed is honest, a false "live" is a wrong-side degradation).
    line = ss.get("status") or item.get("status") or "live"
    successor_label = ss.get("successor_label")
    if successor_label is None and not ss.get("superseded_by"):
        flat_sb = item.get("superseded_by")
        if isinstance(flat_sb, dict) and flat_sb.get("id"):
            successor_label = (
                _snippet(flat_sb.get("content_snippet") or "")
                or flat_sb["id"])
    out["supersession"] = {
        "line": line,
        "successor_label": successor_label,
    }
    if block.get("tradeoffs"):
        out["tradeoffs"] = block["tradeoffs"]
    if block.get("dig_deeper"):
        out["dig_deeper"] = block["dig_deeper"]
    return out


def item_to_why_entry(item: dict) -> dict | None:
    """Project an enriched item back to the canonical §3.1.4 why entry.

    Used by the ask lane (its pool hits flow through the search-path
    enrichment) — zero extra graph reads. Returns None when the item was
    not enriched (no W4 data).
    """
    if "why" not in item and "conflicts" not in item and \
            "dig_deeper" not in item and "tradeoffs" not in item:
        return None
    why = item.get("why") or {}
    entry: dict = {
        "point_id": item.get("id"),
        "support_chain": why.get("support_chain") or [],
    }
    ep = item.get("ep")
    if isinstance(ep, dict):
        # #11702: `None` passes through as `None` — the `0.0` defaults read as
        # "maximum disbelief" for a claim nothing measured, and they are the
        # same coercion this change removed from `why_snapshot`. The state NAME
        # rides along too: without it a consumer cannot tell a `None` value
        # from an absent belief (the §6.3 contract is value + state, always).
        entry["ep"] = {
            "confidence_mean": ep.get("confidence_mean"),
            "variance": ep.get("variance"),
            "contested": bool(ep.get("contested", False)),
            "ep_state": ep.get("ep_state"),
            "has_ep": bool(ep.get("has_ep", False)),
            # #3276: carry the explicit measurement state through the
            # projection (a pre-#3276 item with only has_ep stays honest:
            # measured mirrors has_ep, baseline defaults False).
            "measured": bool(ep.get("measured", ep.get("has_ep", False))),
            "baseline": bool(ep.get("baseline", False)),
        }
    if "conflicts" in item:
        entry["conflicts"] = item["conflicts"]
    # Canonical supersession from the item's flat canonical keys + view.
    superseded_by = item.get("superseded_by")
    entry["supersession"] = {
        "status": item.get("status") or "live",
        "superseded_by": (
            {"point_id": superseded_by["id"],
             "content_snippet": _snippet(superseded_by.get("content_snippet") or "")}
            if isinstance(superseded_by, dict) and superseded_by.get("id")
            else None
        ),
        "supersedes": [
            {"point_id": s["id"],
             "content_snippet": _snippet(s.get("content_snippet") or "")}
            for s in (item.get("supersedes") or [])
            if isinstance(s, dict) and s.get("id")
        ],
        "successor_label": (item.get("supersession") or {}).get("successor_label"),
    }
    if "tradeoffs" in item:
        entry["tradeoffs"] = item["tradeoffs"]
    if "dig_deeper" in item:
        entry["dig_deeper"] = item["dig_deeper"]
    return entry


# ── Analyze-surface helpers (point ids inside raw rows) ───────────────────

# ULID point ids ("<timestamp-hex>-<uuid12>") and deterministic pt_<hash> ids
# (commit endpoint). Conservative: a cell that doesn't match is ignored; a
# match that isn't a real Point node produces no block.
_POINT_ID_RE = re.compile(r"^(?:[0-9a-f]{10,16}-[0-9a-f]{12}|pt_[0-9a-f]+)$")


def point_ids_in_raw(raw_rows: list[list[str]]) -> list[str]:
    """Extract candidate Point ids from analyze()'s raw rows (row cells).

    The analyze patterns return point ids as the first column (and the
    paired id at column 3 for pair patterns); scanning every cell is
    robust to future patterns. Unmatched cells are ignored.
    """
    seen: list[str] = []
    for row in raw_rows or []:
        for cell in row:
            if isinstance(cell, str) and _POINT_ID_RE.match(cell) and cell not in seen:
                seen.append(cell)
    return seen


#: Depth bound for :func:`point_ids_in_payload` — the event payloads the
#: ``:GraphEvent`` store carries are 1-2 levels deep (``{id}``,
#: ``{id, op_type, source_id, target_ids[]}``); the bound is what keeps a
#: pathological/attacker-shaped payload from becoming a deep walk.
PAYLOAD_MAX_DEPTH = 4


def point_ids_in_payload(payload: object, *,
                         max_depth: int = PAYLOAD_MAX_DEPTH) -> list[str]:
    """Extract the Point ids a durable event payload references, in order.

    The #2081 delivery path has to answer "which points does this event
    touch?" for a payload it does NOT shape: ``PointAdded`` carries
    ``{id}``, ``OperatorAdded`` carries ``{id, source_id, target_ids[]}``,
    the promote/dedupe types carry a candidate id, and future producers may
    nest. Scanning every string cell against the SAME conservative id
    grammar :func:`point_ids_in_raw` uses (one regex, one home) is robust to
    that; a cell that is not id-shaped is ignored, and a match that is not a
    real Point node produces no block (the caller's existence anchor).

    Order is first-appearance, de-duplicated — deterministic for a given
    payload, which is what makes the pushed ``why`` list deterministic. The
    ``set``/``frozenset`` branch is walked in SORTED order for exactly that
    reason: a set's iteration order varies across processes (``PYTHONHASHSEED``),
    so an unsorted walk would push the same event's ``why`` list in a different
    order on a different worker (#8099 review F3 sub-note).
    """
    seen: list[str] = []

    def walk(value: object, depth: int) -> None:
        if depth > max_depth:
            return
        if isinstance(value, str):
            if _POINT_ID_RE.match(value) and value not in seen:
                seen.append(value)
        elif isinstance(value, dict):
            for cell in value.values():
                walk(cell, depth + 1)
        elif isinstance(value, (set, frozenset)):
            # Sorted by rendered form: elements need not be mutually orderable
            # (a set can hold a str and a dict), so the key is the type name
            # plus repr, which is total and stable within a process.
            for cell in sorted(value, key=lambda c: (type(c).__name__, repr(c))):
                walk(cell, depth + 1)
        elif isinstance(value, (list, tuple)):
            for cell in value:
                walk(cell, depth + 1)

    walk(payload, 0)
    return seen
