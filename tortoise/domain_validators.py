"""Domain integrity validators — import-time registrations (issue #405).

This module is the single registration point for production domain
validators (scoping bullet 1): it is imported by the CLI (__main__.py), the
MCP server (mcp_server.py → sdk.validate_domain) and the SDK path
(sdk.check_structure delegation) so every process sees the same registry —
a missing import would make ``tortoise validate`` report a false-clean.

Registered surfaces:
  - ``graph`` (on-demand): ``product_strategy.validate_chain_integrity`` — a
    mechanism-complete, batched migration of the legacy ``sdk.check_structure``
    (all 5 rule types: orphan_use_case, dangling_use_case_ref,
    dangling_jtbd_ref, dangling_workflow_ref, orphaned_draft). Runs from
    ``tortoise validate --domain`` and the MCP tool. Never runs at commit
    (the commit path has no graph state by construction).
  - ``payload_local`` (commit path): ``validate_payload_intra_chain`` — the
    deterministic intra-payload leapfrog rule, run by
    ``commit_schema.validate_domain_rules`` (warn-first, additive
    ``warnings[]`` on the 200 response).

Draft semantics (#405 guardrails): chain rules run on LIVE points only
(status NULL = live, mirroring the EP ``_live_only`` convention) — orphaned
drafts are the ``orphaned_draft`` rule's job (a draft with zero edges is
already flagged there; draft points with edges are in-progress work, not
chain violations).
"""
from __future__ import annotations

import logging
from typing import Any  # noqa: F401

from .domain_loader import (
    SURFACE_GRAPH,
    SURFACE_PAYLOAD_LOCAL,
    domain_chain_spec,
    domain_validator,
    domain_validators,
    known_domains,
)

logger = logging.getLogger(__name__)

# Product-strategy chain steps (manifest productDelivery, JTBD step-0 — the
# decision 2026-08-15). Read live from the manifest at call time; this
# constant is the fallback when the pack registry is unavailable.
_PRODUCT_DELIVERY_CHAIN = "productDelivery"


# ── kind helpers ──────────────────────────────────────────────────────────

def _bare(kind: str) -> str:
    """Strip a namespace prefix (``product-strategy:useCase`` → ``useCase``)."""
    return kind.split(":", 1)[-1] if ":" in kind else kind


def _expand(kind: str) -> list[str]:
    """Pack-aware kind expansion (subclasses + equivalents) for Cypher IN
    clauses — mirrors sdk._expand_kind via the same cached registry, so the
    migrated validator resolves kinds IDENTICALLY to the legacy check_structure.
    Falls back to [kind] when packs are unavailable (ponytail)."""
    try:
        from .sdk import _get_kind_expander
        return _get_kind_expander().expand_kind(kind)
    except Exception:
        return [kind]


def _live_clause(var: str) -> str:
    """Status-NULL-is-live filter (mirrors EP _live_only)."""
    return f"({var}.status IS NULL OR {var}.status <> 'draft')"


# ── graph-surface validator: product-strategy chain integrity ─────────────

@domain_validator("product-strategy", chain_id=_PRODUCT_DELIVERY_CHAIN,
                  surface=SURFACE_GRAPH)
def validate_chain_integrity(graph) -> list[dict]:
    """Product-strategy chain-integrity rules (issue #405) — the
    mechanism-complete migration of ``sdk.check_structure``, batched.

    Rules (legacy order preserved for wrapper equivalence):
      1. orphan_use_case        — live useCase with no composedOf/hasPart JTBD
                                  parent (JTBD anchor via composedOf operator)
      2. dangling_use_case_ref  — userJourney.covered_use_cases → missing useCase
      3. dangling_jtbd_ref      — workflow.enables_jtbd → missing JTBD
      4. dangling_workflow_ref  — requirement.enabled_workflow → missing workflow
      5. orphaned_draft         — draft point with zero edges (#131)

    Output shape (enriched, actionable — guardrails): {rule, kind, ref,
    message, fix}. ``sdk.check_structure`` maps this to the legacy
    {type, id, message} contract via :func:`to_legacy_shape`.

    Performance: one batched Cypher per rule (single OPTIONAL MATCH / UNWIND)
    — fixes the legacy N+1 (one query per useCase/userJourney/workflow).
    """
    violations: list[dict] = []
    uc_kind = _expand("useCase")
    jtbd_kind = _expand("jobToBeDone")
    uj_kind = _expand("userJourney")
    wf_kind = _expand("workflow")
    req_kind = _expand("requirement")

    # 1. orphan_use_case — batched: per useCase, count composedOf parents that
    # also reach a JTBD hasPart child. Live points only (draft orphans are the
    # orphaned_draft rule's job).
    rows = graph.g.query(
        "MATCH (uc:Point) WHERE uc.pointKind IN $uc_kinds "
        f"AND {_live_clause('uc')} "
        "OPTIONAL MATCH (uc)<-[:hasPart]-(op:Point {is_operator:true, "
        "op_type:'composedOf'})-[:hasPart]->(jtbd:Point) "
        "WHERE jtbd.pointKind IN $jtbd_kinds "
        "RETURN uc.id, uc.uc_id, count(jtbd) AS parented",
        params={"uc_kinds": uc_kind, "jtbd_kinds": jtbd_kind},
    ).result_set
    for uc_id, uc_ref, parented in rows:
        if not parented:
            violations.append({
                "rule": "orphan_use_case",
                "kind": "useCase",
                "ref": uc_id,
                "message": f"useCase {uc_ref or uc_id} has no parent JTBD",
                "fix": "wire the useCase under a jobToBeDone: "
                       "create_operator('composedOf', jtbd_id, [uc_id])",
            })

    # 2. dangling_use_case_ref — batched (split/trim/UNWIND).
    rows = graph.g.query(
        "MATCH (uj:Point) WHERE uj.pointKind IN $uj_kinds "
        f"AND {_live_clause('uj')} "
        "AND uj.covered_use_cases IS NOT NULL AND uj.covered_use_cases <> '' "
        "WITH uj, [r IN split(uj.covered_use_cases, ',') | trim(r)] AS refs "
        "UNWIND refs AS ref "
        "OPTIONAL MATCH (uc:Point {uc_id: ref}) "
        "WHERE uc.pointKind IN $uc_kinds "
        "WITH uj, ref, uc WHERE uc IS NULL "
        "RETURN uj.id, ref",
        params={"uj_kinds": uj_kind, "uc_kinds": uc_kind},
    ).result_set
    for uj_id, uc_ref in rows:
        violations.append({
            "rule": "dangling_use_case_ref",
            "kind": "userJourney",
            "ref": uj_id,
            "message": f"userJourney {uj_id} refs non-existent useCase {uc_ref}",
            "fix": "create the referenced useCase (with uc_id matching) or "
                   "fix the userJourney's covered_use_cases property",
        })

    # 3. dangling_jtbd_ref — batched.
    rows = graph.g.query(
        "MATCH (wf:Point) WHERE wf.pointKind IN $wf_kinds "
        f"AND {_live_clause('wf')} "
        "AND wf.enables_jtbd IS NOT NULL AND wf.enables_jtbd <> '' "
        "WITH wf, [r IN split(wf.enables_jtbd, ',') | trim(r)] AS refs "
        "UNWIND refs AS ref "
        "OPTIONAL MATCH (j:Point {jtbd_id: ref}) "
        "WHERE j.pointKind IN $jtbd_kinds "
        "WITH wf, ref, j WHERE j IS NULL "
        "RETURN wf.id, ref",
        params={"wf_kinds": wf_kind, "jtbd_kinds": jtbd_kind},
    ).result_set
    for wf_id, jtbd_ref in rows:
        violations.append({
            "rule": "dangling_jtbd_ref",
            "kind": "workflow",
            "ref": wf_id,
            "message": f"workflow {wf_id} refs non-existent JTBD {jtbd_ref}",
            "fix": "create the referenced JTBD (with jtbd_id matching) or "
                   "fix the workflow's enables_jtbd property",
        })

    # 4. dangling_workflow_ref — batched; 'ALL' is a sentinel, not a ref.
    rows = graph.g.query(
        "MATCH (req:Point) WHERE req.pointKind IN $req_kinds "
        f"AND {_live_clause('req')} "
        "AND req.enabled_workflow IS NOT NULL AND req.enabled_workflow <> '' "
        "AND req.enabled_workflow <> 'ALL' "
        "OPTIONAL MATCH (w:Point {wf_id: req.enabled_workflow}) "
        "WHERE w.pointKind IN $wf_kinds "
        "WITH req, w WHERE w IS NULL "
        "RETURN req.id, req.enabled_workflow",
        params={"req_kinds": req_kind, "wf_kinds": wf_kind},
    ).result_set
    for req_id, wf_ref in rows:
        violations.append({
            "rule": "dangling_workflow_ref",
            "kind": "requirement",
            "ref": req_id,
            "message": f"requirement {req_id} refs non-existent workflow {wf_ref}",
            "fix": "create the referenced workflow (with wf_id matching) or "
                   "fix the requirement's enabled_workflow property",
        })

    # 5. orphaned_draft — drafts with zero edges (#131). Verbatim legacy query.
    for row in graph.g.query(
        "MATCH (n:Point {status:'draft'}) "
        "WHERE n.is_operator = false "
        "AND NOT (n)--() "
        "RETURN n.id, n.content, n.pointKind, n.createdAt "
        "ORDER BY n.createdAt"
    ).result_set:
        violations.append({
            "rule": "orphaned_draft",
            "kind": row[2] or "unknown",
            "ref": row[0],
            "message": (
                f"Draft point '{row[1][:80] if row[1] else ''}' "
                f"of kind '{row[2] or 'unknown'}' has no edges "
                f"(created {row[3] or 'unknown'})"
            ),
            "fix": "wire the draft point into the graph (create an operator "
                   "targeting it) or promote it via the reviewer gate "
                   "(promote_point)",
        })

    return violations


# ── payload-local validator: intra-payload chain rules (commit path) ──────

@domain_validator("product-strategy", chain_id=_PRODUCT_DELIVERY_CHAIN,
                  surface=SURFACE_PAYLOAD_LOCAL)
def validate_payload_intra_chain(payload) -> list[dict]:
    """Intra-payload product-strategy rules — the deterministic Phase-A set
    (issue #405 scoping bullet 2). NEVER touches the graph.

    leapfrog_chain_step — an IMPL operator between two payload points of
    chain kinds that SKIPS a step (forward, non-adjacent: dst step index
    exceeds src by > 1). Both endpoints must be in the payload (cross-commit
    edges are the graph-surface rule's job). Back-references (dst at an
    earlier step than src) are EXEMPT — legitimate feedback edges; property
    back-refs (userJourney.covered_use_cases → useCase) are not operators.

    Scoping deviation (documented): the proposed intra-payload dangling-ref
    rule is NOT expressible in the CommitPayload schema — Point is
    extra='forbid' and carries no domain ref properties
    (uc_id/covered_use_cases/enables_jtbd/enabled_workflow live on graph
    nodes written via SDK props, never in a commit payload). Those refs are
    validated graph-globally by validate_chain_integrity (validate --domain).
    """
    chain = _product_delivery_steps()
    if not chain:
        return []  # manifest chain unavailable → nothing to check
    idx = {k: i for i, k in enumerate(chain)}
    points = {p.id: p for p in payload.points}
    warnings: list[dict] = []
    for op in payload.operators:
        if op.op_type != "IMPL":
            continue  # chain-forward edges are IMPL; NAND is epistemic opposition
        src = points.get(op.src)
        dst = points.get(op.dst)
        if src is None or dst is None:
            continue
        sk, dk = _bare(src.pointKind), _bare(dst.pointKind)
        if sk not in idx or dk not in idx:
            continue
        i, j = idx[sk], idx[dk]
        if j > i + 1:  # forward non-adjacent → leapfrog (back-refs exempt)
            skipped = chain[i + 1:j]
            warnings.append({
                "rule": "leapfrog_chain_step",
                "kind": sk,
                "ref": op.src,
                "message": (
                    f"{sk} {op.src} → {dk} {op.dst} skips productDelivery "
                    f"chain step(s) {skipped}"
                ),
                "fix": "wire through the skipped intermediate step(s) "
                       f"{skipped} — the chain advances one step at a time",
            })
    return warnings


def _product_delivery_steps() -> list[str]:
    """productDelivery steps from the pack manifest (JTBD step-0)."""
    spec = domain_chain_spec("product-strategy")
    return list(spec.get(_PRODUCT_DELIVERY_CHAIN, {}).get("steps", []))


# ── orchestration helpers (CLI / MCP / SDK) ───────────────────────────────

def run_domain_graph_validators(
    domain: str, graph, *, include_drift: bool = True
) -> tuple[list[dict], list[dict]]:
    """Run every graph-surface validator registered for ``domain`` against a
    live projection (``graph`` needs ``.g.query(cypher, params=...)``).

    Returns ``(violations, drift_warnings)`` — enriched, actionable dicts
    ({rule, kind, ref, message, fix}).

    Raises ValueError for an unknown domain (no loaded pack AND no registered
    validators) — the CLI maps this to exit 2. A validator exception is
    logged and skipped: validation is advisory and a broken rule must never
    crash the tool (guardrails).
    """
    specs = domain_validators(domain, surface=SURFACE_GRAPH)
    if not specs and domain not in known_domains():
        known = ", ".join(known_domains()) or "none"
        raise ValueError(f"unknown domain {domain!r} (known: {known})")
    violations: list[dict] = []
    for spec in specs:
        try:
            violations.extend(spec["fn"](graph))
        except Exception:
            logger.exception(
                "domain validator failed (domain=%s chain=%s) — skipped",
                domain, spec.get("chain_id"),
            )
            continue
    drift = _drift_warnings(domain) if include_drift else []
    return violations, drift


def _drift_warnings(domain: str) -> list[dict]:
    """Manifest chains declared for the domain with NO registered validator →
    advisory drift warning (never fails a clean run — out-of-scope chains,
    e.g. dev/marketing, must not gate)."""
    spec = domain_chain_spec(domain)
    if not spec:
        return []
    registered = {s["chain_id"] for s in domain_validators(domain)}
    out: list[dict] = []
    for chain_id, cspec in spec.items():  # noqa: B007
        if chain_id not in registered:
            out.append({
                "rule": "drift_unregistered_chain",
                "kind": "chain",
                "ref": chain_id,
                "message": (
                    f"chain '{chain_id}' is declared in the {domain} manifest "
                    "but has no registered validator"
                ),
                "fix": f"register a validator via register_domain_validator"
                       f"('{domain}', chain_id='{chain_id}', ...)",
            })
    return out


def to_legacy_shape(violations: list[dict]) -> list[dict]:
    """Map enriched validator output {rule, kind, ref, message, fix} → the
    legacy ``check_structure`` contract {type, id, message} (#405 wrapper
    equivalence — consumed by mcp_server, tool_registry, test_sdk,
    test_integration_search)."""
    return [
        {
            "type": v.get("rule") or v.get("type", "unknown"),
            "id": v.get("ref"),
            "message": v.get("message", ""),
        }
        for v in violations
    ]


def resolve_rule_severity(
    domain: str, chain_id: str | None, rule: str | None = None
) -> str:
    """Resolve a rule's enforcement severity (#405): the manifest chain's
    enforcement level (warn/retry/block) → warn default. ``retry`` maps to a
    Phase-A warning at commit (extractor-retry semantics layer later); only
    ``block`` rejects the commit (Phase B, wired-but-inactive in prod).
    """
    if chain_id:
        chain = domain_chain_spec(domain).get(chain_id)
        if chain:
            level = chain.get("enforcement")
            if level in ("warn", "retry", "block"):
                return level
    return "warn"
