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

from .search_engine import (  # type: ignore[import-not-found]
    CONTESTED_VARIANCE_THRESHOLD,
    _exclude_status_clause,
    fetch_point_epistemic_state,
)

logger = logging.getLogger(__name__)

# ── W4 flag ────────────────────────────────────────────────────────────────
# Default OFF (production exposure gated by the epic's user-exposure gate —
# both conditions must hold before the flip). Tests / dev / the A11 pilot set
# it explicitly, mirroring the TORTOISE_ENABLE_ASK gating precedent (#2013).
W4_FLAG_ENV = "TORTOISE_W4_ENRICHMENT"

# ── Budgets (plan §3.1.1 — pinned by the S6 contract test) ────────────────
W4_MAX_SUPPORT = 3
W4_MAX_CONFLICTS = 2
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
    gate. Truthy values: 1/true/yes/on.
    """
    v = os.environ.get(W4_FLAG_ENV)
    if v is None:
        return False
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── The shared assembly ────────────────────────────────────────────────────
# Terminal-exclusion uses search_engine._exclude_status_clause (the canonical
# filter — ALSO excludes legacy ``outdated=true`` points with no status, so the
# why-block's active-structure reads stay consistent with the search/recall
# surfaces that feed the items).


def _beta_variance(alpha: float, beta: float) -> float:
    """Beta(α, β) posterior variance: αβ/((α+β)²(α+β+1)) — same formula as
    search_engine._beta_variance / TortoiseEP (kept local to avoid a second
    import surface; the threshold constant is imported from search_engine)."""
    s = alpha + beta
    if s <= 0:
        return 0.0
    return (alpha * beta) / (s * s * (s + 1))


def _mean(alpha: float, beta: float) -> float:
    s = alpha + beta
    return (alpha / s) if s > 0 else 0.5


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


def _assemble_supports(rows: list, by_id: dict[str, dict], *, merge: bool = False) -> None:
    """Fold support-chain rows into the blocks. Collect ALL candidates per
    point (dedup by point id), then sort by (weight desc, point_id) and
    truncate to the ≤3 cap — the selected subset is the strongest ≤3 and
    the ordering is deterministic regardless of Cypher row order.

    ``merge=True`` (the second leg) ADDS the leg's candidates into the
    chain the first leg already built instead of overwriting it — a point
    with BOTH operator-mediated supports AND direct statement→statement
    IMPL edges must surface the union of both legs (mixed-edge points were
    silently dropping the operator-mediated leg before this fix)."""
    pending: dict[str, dict[str, dict]] = {}
    for pid, sup_id, content, a, b in rows:
        if not pid or not sup_id:
            continue
        if sup_id not in pending.setdefault(pid, {}):
            pending[pid][sup_id] = {
                "point_id": sup_id,
                "content_snippet": _snippet(content),
                "edge": "IMPL",
                "weight": round(_mean(float(a), float(b)), 4),
            }
    for pid, chain_map in pending.items():
        chain = sorted(chain_map.values(),
                       key=lambda s: (-s["weight"], s["point_id"]))
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
                           key=lambda s: (-s["weight"], s["point_id"]))
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
            pending[pid][nid] = {
                "point_id": nid,
                "content_snippet": _snippet(text),
                "severity": _severity(_mean(float(row[3]), float(row[4]))),
            }
    for pid, nand_map in pending.items():
        nands = sorted(
            nand_map.values(),
            key=lambda n: (0 if n["severity"] == "high" else 1, n["point_id"]))
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
                "ep_weight": round(_mean(float(row[7]), float(row[8])), 4),
                "mitigation": _mitigation_text(row[6]),
            }
    for pid, alts in pending.items():
        alt_list = list(alts.values())
        if len(alt_list) < 2 and kinds.get(pid) != "decision":
            continue
        if len(alt_list) < 1:
            continue
        if kinds.get(pid) != "decision" and not any(
                a.get("kind") == "option" for a in alt_list):
            continue
        block = by_id[pid]
        # Contract shape: {point_id, label, ep_weight, mitigation} — the
        # option-kind marker is internal detection only, never emitted.
        block["tradeoffs"] = sorted(
            [
                {k: v for k, v in a.items() if k != "kind"}
                for a in alt_list
            ],
            key=lambda t: (-t["ep_weight"], t["point_id"]))


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
    EXISTING :Point node (the query is the existence anchor). Unmeasured
    points coalesce to the Beta(1,1) uniform prior: mean 0.5, variance
    1/12, has_ep False — absence of measurement is NOT low support (repo
    neutral-0.5 convention)."""
    for row in rows:
        if not row or not row[0]:
            continue
        pid = row[0]
        a, b = float(row[1]), float(row[2])
        has_ep = bool(row[3])
        variance = _beta_variance(a, b)
        by_id[pid]["ep"] = {
            "confidence_mean": round(_mean(a, b), 4),
            "variance": round(variance, 6),
            "contested": has_ep and variance > CONTESTED_VARIANCE_THRESHOLD,
            "has_ep": has_ep,
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


# Support-chain queries (bounded). Operator-mediated: the operator's INPUT
# source at idx 0 (the evidence/statement that implies the target through the
# operator — create_operator writes source → INPUT idx0 → op → IMPL → target).
# Direct: a bare statement→statement IMPL edge (the reification rule).
_SUPPORT_OP_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "MATCH (sup:Point)-[r:INPUT]->(op:Point {is_operator:true})-[:IMPL]->(n) "
    f"WHERE r.idx = 0 AND sup.id <> n.id "
    f"AND (sup.is_operator = false OR sup.is_operator IS NULL) AND {_exclude_status_clause('sup')} "
    "RETURN n.id, sup.id, sup.content, "
    "  coalesce(sup.posterior_alpha, sup.ep_alpha, 1.0), "
    "  coalesce(sup.posterior_beta, sup.ep_beta, 1.0)"
)
_SUPPORT_DIRECT_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "MATCH (sup:Point)-[r:IMPL]->(n) "
    f"WHERE sup.id <> n.id "
    f"AND (sup.is_operator = false OR sup.is_operator IS NULL) AND {_exclude_status_clause('sup')} "
    "RETURN n.id, sup.id, sup.content, "
    "  coalesce(sup.posterior_alpha, sup.ep_alpha, 1.0), "
    "  coalesce(sup.posterior_beta, sup.ep_beta, 1.0)"
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
    "MATCH (c:Point)-[r:NAND]->(n) "
    "OPTIONAL MATCH (src:Point)-[ri:INPUT]->(c) "
    "WITH n, c, r, src, ri "
    "WHERE c.id <> n.id AND ("
    "  (c.is_operator = true AND r.idx > 0 AND ri.idx = 0) OR "
    "  (c.is_operator = false OR c.is_operator IS NULL)) "
    f"AND {_exclude_status_clause('c')} "
    "RETURN n.id, coalesce(src.id, c.id), "
    "  coalesce(src.content, c.label, c.content, ''), "
    "  coalesce(src.posterior_alpha, src.ep_alpha, c.posterior_alpha, c.ep_alpha, 1.0), "
    "  coalesce(src.posterior_beta, src.ep_beta, c.posterior_beta, c.ep_beta, 1.0)"
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
    "  (n.posterior_alpha IS NOT NULL OR n.ep_alpha IS NOT NULL)"
)
# Tradeoffs (decision points): the point is the operator SOURCE (INPUT idx 0)
# and the alternatives are the operator's IMPL TARGETS at idx > 0 (the
# file_decision / option structure — idx 0 is the operator's source self-link,
# never an alternative); mitigations ride the connecting operator.
_TRADEOFFS_CYPHER = (
    "MATCH (n:Point) WHERE n.id IN $ids "
    "MATCH (n)-[ri:INPUT]->(op:Point {is_operator:true})-[r2:IMPL]->(alt:Point) "
    f"WHERE ri.idx = 0 AND r2.idx > 0 "
    f"AND (alt.is_operator = false OR alt.is_operator IS NULL) AND {_exclude_status_clause('alt')} "
    "OPTIONAL MATCH (op)-[:mitigated_by]->(m:Point) "
    "RETURN n.id, n.pointKind, alt.id, alt.content, alt.pointKind, op.id, m.content, "
    "  coalesce(alt.posterior_alpha, alt.ep_alpha, 1.0), "
    "  coalesce(alt.posterior_beta, alt.ep_beta, 1.0)"
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

    Used by the ask surface (its pool hits flow through the search-path
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
        entry["ep"] = {
            "confidence_mean": ep.get("confidence_mean", 0.0),
            "variance": ep.get("variance", 0.0),
            "contested": bool(ep.get("contested", False)),
            "has_ep": bool(ep.get("has_ep", False)),
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
