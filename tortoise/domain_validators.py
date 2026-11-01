"""Domain integrity validators — import-time registrations (issue #405).

This module is the single registration point for production domain
validators (scoping bullet 1): it is imported by the CLI (__main__.py), the
MCP server (mcp_server.py → sdk.validate_domain) and the SDK path
(sdk.check_structure delegation) so every process sees the same registry —
a missing import would make ``tortoise validate`` report a false-clean.

Registered surfaces:
  - ``graph`` (on-demand): ``validate_chain_integrity`` for `product-strategy` — a
    mechanism-complete, batched migration of the legacy ``sdk.check_structure``
    (all 5 rule types: orphan_use_case, dangling_use_case_ref,
    dangling_jtbd_ref, dangling_workflow_ref, orphaned_draft). Runs from
    ``tortoise validate --domain`` and the MCP tool. Never runs at commit
    (the commit path has no graph state by construction).
  - ``graph`` (on-demand): ``validate_portfolio_shape`` for `portfolio` (#9663) —
    rules ``undeclared_object_status`` and ``orphaned_valuation_mark``, derived
    from the overlay pack's own declarations. Without a rule, a domain's
    ``validate`` verdict is ``([], [])`` — a green light over nothing — which is
    why this list exists.
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


def _dangling_workflow_ref_query() -> str:
    """Cypher for rule 4 (`dangling_workflow_ref`), kept in one place so the
    anchoring property is testable (#7848).

    ⛔ `wf_id` MUST stay INSIDE each workflow node pattern. FalkorDB cannot
    anchor a bare `MATCH (w) WHERE w.wf_id = …`: that leg degrades to
    ``|req| × |V|``, and on a ~3 000-node graph it exceeds the default 1000 ms
    ``GRAPH.CONFIG TIMEOUT``. ``run_domain_graph_validators`` swallows the
    exception, so the timeout surfaces as a FALSE CLEAN — 300 genuine dangling
    requirements reported as none (measured on PR #8173). Point and Object are
    separate legs precisely so each one stays anchored; a workflow is a core
    Object kind, and the legacy `dev:requirement` spelling is a stored Point.
    """
    return (
        "MATCH (req) WHERE (req:Point OR req:Object) "
        "AND (req.pointKind IN $req_kinds OR req.objectKind IN $req_kinds) "
        f"AND {_live_clause('req')} "
        "AND req.enabled_workflow IS NOT NULL AND req.enabled_workflow <> '' "
        "AND req.enabled_workflow <> 'ALL' "
        "OPTIONAL MATCH (wp:Point {wf_id: req.enabled_workflow}) "
        "WHERE wp.pointKind IN $wf_kinds "
        "OPTIONAL MATCH (wo:Object {wf_id: req.enabled_workflow}) "
        "WHERE wo.objectKind IN $wf_kinds "
        "WITH req, wp, wo WHERE wp IS NULL AND wo IS NULL "
        "RETURN req.id, req.enabled_workflow"
    )


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
    # #7848: `requirement` is an OBJECT kind (owner ruling), so the node may be
    # an Object, not a Point — match both labels and both kind properties, or
    # the rule goes silently dead for the kind it exists for. `_expand` also
    # carries the legacy `dev:requirement` point spelling as a read alias
    # (LEGACY_KIND_ALIASES), so stored Points stay covered. The workflow lookup
    # stays PATTERN-ANCHORED per label — see `_dangling_workflow_ref_query`.
    rows = graph.g.query(
        _dangling_workflow_ref_query(),
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


# ── graph-surface validator: the portfolio overlay (#9663) ────────────────

_PORTFOLIO_DOMAIN = "portfolio"

#: Statuses that mean "no lifecycle state was written", not "a state outside the
#: kind's vocabulary" — so a freshly-minted mandate is not reddened until someone
#: sets a status, which is not a graph defect. Named here (not inlined) because
#: the exemption is a decision, not a test.
#:
#: ⛔ TWO different reasons carry `live` in this exemption, and they are no longer
#: the same one (#9974 R1 review P3). The node scan below filters
#: `o.status IS NOT NULL` (:560), so a node born without an explicit status
#: — which since R1 is the ORDINARY shape, the fold seeds `status=$st` rather
#: than the deleted `coalesce($st, 'live')` — is SKIPPED, not exempted. This set
#: therefore covers only the LEGACY / caller-supplied shape: a node that already
#: CARRIES the literal `live` (a pre-R1 mandate, or `create_object(…,
#: status='live')`). The older text here claimed the fold filled `live` in; it
#: does not, and a reader trusting it would think this set is what keeps new
#: nodes out of the warning.
_OBJECT_UNSET_STATUSES = frozenset({"live"})

#: The mark's kind, and the FALLBACK contract used only when the pack registry
#: cannot be read (the same degradation `_portfolio_declared_statuses` accepts).
#: The live contract is read off the pack's own declarations at call time
#: (`_portfolio_mark_contract`) — a hardcoded copy is the drift this pack exists
#: to avoid, so these two values are pinned against the manifest by
#: ``tests/test_investment_portfolio_manager_pack.py::
#: test_the_mark_contract_is_pinned_to_the_manifest``.
MARK_KIND = "portfolio:valuationMark"
POSITION_KIND = "venture:investment"
MARK_RELATION = "marks"


def _pack_relations() -> list[dict]:
    """The loaded portfolio pack's declared relations (``[]`` when unreadable)."""
    try:
        from .domain_loader import _get_registry
        reg = _get_registry()
        pack = reg.get_pack(_PORTFOLIO_DOMAIN) if reg is not None else None
    except Exception:  # noqa: BLE001, RUF100 — advisory surface, see docstring
        return []
    if pack is None:
        return []
    return [r for r in (pack.relations or []) if isinstance(r, dict)]


def _portfolio_mark_contract() -> tuple[str, str, str]:
    """``(mark_kind, position_kind, relation)`` for the valuation-mark rule.

    Read from the loaded pack at call time — the ``marks`` relation whose
    ``fromKind`` is this pack's ``valuationMark`` — so a manifest edit to
    ``toKind`` or a predicate rename moves the rule with it instead of silently
    desynchronising it (the orphan rule then flags every correctly-wired mark,
    or goes blind, with nothing red in CI). Falls back to the module constants
    when the registry is unavailable, which is the same fail-open-but-visible
    degradation ``_portfolio_declared_statuses`` takes: a validator that raised
    here would be swallowed by ``run_domain_graph_validators`` and read CLEAN.
    """
    forms = {MARK_KIND.lower(), MARK_KIND.partition(":")[2].lower()}
    for rel in _pack_relations():
        from_kind = str(rel.get("fromKind") or "").lower()
        to_kind = rel.get("toKind")
        predicate = rel.get("predicate")
        if from_kind in forms and to_kind and predicate:
            return MARK_KIND, str(to_kind), str(predicate)
    return MARK_KIND, POSITION_KIND, MARK_RELATION


def _admitted_kind_forms(namespaced_kind: str) -> list[str]:
    """Every case-folded spelling the write gate admits for a pack kind.

    ``portfolio:valuationMark`` → ``["portfolio:valuationmark",
    "valuationmark"]``. Both spellings matter here for two INDEPENDENT reasons,
    and missing either is a false CLEAN (or a false violation) on the shape the
    rule exists to report:

      * the write gate admits ``ns:local`` AND bare ``local``
        (``extractor_v2._object_kind_forms``/``_event_kind_forms`` lowercase
        both) and the projection stores the spelling VERBATIM;
      * the extraction pipeline's own event commit STRIPS the namespace
        (``sdk.py`` ``_extract_session_v2`` writes ``ekind.rsplit(":", 1)[-1]``),
        so every mark minted by extraction is stored bare — the #2729 review
        P1 that widened ``StatusFoldIndex``'s keys, and the reason the rule
        must read the same map rather than re-derive it.

    The spellings come from that canonical seam
    (``StatusFoldIndex.kind_forms_for``); the inline pair is the registry-less
    fallback, identical to what the index returns.
    """
    try:
        from .domain_loader import _get_registry
        reg = _get_registry()
        fold_index = reg.status_fold_index if reg is not None else None
    except Exception:  # noqa: BLE001, RUF100 — advisory surface, see docstring
        fold_index = None
    if fold_index is not None:
        try:
            return [str(f).lower() for f in
                    fold_index.kind_forms_for(namespaced_kind)]
        except Exception:  # advisory surface — fall through to the pair below
            pass
    forms = [namespaced_kind.lower()]
    local = namespaced_kind.partition(":")[2]
    if local and local.lower() not in forms:
        forms.append(local.lower())
    return forms


def _portfolio_declared_statuses() -> dict[str, frozenset[str]]:
    """``{case-folded kind spelling: declared statuses}`` for the pack.

    Read from the registry at call time (never a hardcoded copy — that is the
    map `#2729` exists to remove). ``{}`` when the pack machinery is unavailable:
    the same degradation ``_expand`` accepts, since a validator that raised here
    would be swallowed by ``run_domain_graph_validators`` and read as CLEAN.

    Keyed on EVERY spelling the write gate admits for the kind — the namespaced
    ``portfolio:mandate`` AND the bare ``mandate`` — because the gate admits both
    (``extractor_v2._object_kind_forms`` lowercases every form) and the
    projection stores ``objectKind`` verbatim. Keyed on the namespaced spelling
    alone, the rule is BLIND to a bare-spelled Object the gate produced: an
    out-of-vocabulary status on it is never reported, i.e. a false CLEAN. The
    spellings come from the canonical seam
    (``StatusFoldIndex.kind_forms_for`` — the same map the declared fold uses,
    #2729) instead of being re-derived here.
    """
    try:
        from .domain_loader import _get_registry
        reg = _get_registry()
        pack = reg.get_pack(_PORTFOLIO_DOMAIN) if reg is not None else None
    except Exception:  # noqa: BLE001, RUF100 — advisory surface, see docstring
        return {}
    if pack is None:
        return {}
    try:
        fold_index = reg.status_fold_index
    except Exception:  # advisory surface, see docstring — never raise into the swallow
        fold_index = None
    out: dict[str, frozenset[str]] = {}
    for kind, spec in (pack.kind_defs or {}).items():
        statuses = spec.get("statuses") if isinstance(spec, dict) else None
        if not (isinstance(statuses, list) and statuses):
            continue
        namespaced = f"{_PORTFOLIO_DOMAIN}:{kind}"
        if fold_index is not None:
            forms = fold_index.kind_forms_for(namespaced)
        else:  # registry without a fold index — the same two admitted forms
            forms = [namespaced.lower(), str(kind).lower()]
        allowed = frozenset(s for s in statuses if isinstance(s, str))
        for form in forms:
            out[str(form).lower()] = allowed
    return out


@domain_validator(_PORTFOLIO_DOMAIN, surface=SURFACE_GRAPH)
def validate_portfolio_shape(graph) -> list[dict]:
    """The portfolio overlay's own graph-surface rules (issue #9663).

    The pack's acceptance asks for ``tortoise_validate_domain`` to be CLEAN, and
    a validator that does not exist cannot be clean:
    ``run_domain_graph_validators`` returns ``([], [])`` for a known namespace
    with no rules registered, so the check would pass on ANY graph. These two
    rules come from the manifest's own declarations:

      1. ``undeclared_object_status`` — a `portfolio:mandate` /
         `portfolio:allocationTarget` whose ``status`` is outside its kind's
         declared ``statuses``. A declared-statuses kind OWNS its state (the
         connector fold no longer applies to it), so an out-of-vocabulary status
         is a state nothing declared — and every "what is the current position?"
         read misses it. The rule reads the BARE kind spelling too (the write
         gate admits `mandate` as well as `portfolio:mandate`), because an
         address the gate accepts but the rule cannot see is a false CLEAN.
      2. ``orphaned_valuation_mark`` — a `portfolio:valuationMark` Event not
         attached to a POSITION (a `venture:investment`), by the declared
         relation (`marks`, an operator edge) or by the structural
         ``aboutObject`` form some callers use. An unattached one records that a
         mark happened and can never be joined back to what it marked; one
         attached to the WRONG thing (a `producedBy` operator pointing at a
         mandate) answers the wrong question and is just as unusable.

    One batched query per rule (no N+1), each anchored on a LABEL — and the
    claim stops there. ``objectKind``/``eventKind`` are properties with no index,
    so the kind filter is a label scan within the anchored walk. That is the
    honest residual: a query slow enough to time out is swallowed by
    ``run_domain_graph_validators`` and reads as CLEAN (#8173), and this rule
    does not design that away — it keeps the walk label-anchored so it cannot
    degrade to a whole-graph scan.
    """
    violations: list[dict] = []

    # The framework's terminal vocabulary (``superseded`` / ``deprecated`` /
    # ``archived`` / ``retracted``) is written by the ENGINE, never by a caller:
    # ``commit_ops.apply_supersessions`` folds a superseded Object to
    # ``superseded``. Supersession is exactly how a mandate or target changes
    # state, so treating that status as undeclared would redden
    # `validate_domain portfolio` on every graph that ever superseded one — the
    # pack's own acceptance would forbid its own write path. Exempt, from the
    # one definition (never a second literal).
    from .commit_ops import OBJECT_TERMINAL_STATUSES

    declared = _portfolio_declared_statuses()
    if not declared:
        # Fail-open but NAMED — on the channel the CALLER reads, not only here.
        # With no readable `portfolio` pack there is nothing for the
        # undeclared-status rule to grade, and the verdict stays `ok=True`
        # (there is no violation to report, only a rule that graded nothing).
        # A `logger.warning` alone is not enough: a caller of
        # `tortoise_validate_domain` (MCP or CLI) never sees the server log, so
        # it would receive an unqualified CLEAN over an EMPTY rule set — a
        # green light over nothing. `_drift_warnings` therefore adds a
        # `portfolio_rules_unavailable` entry to the payload the caller
        # receives, and this line is the operator-facing half of the same fact.
        logger.warning(
            "portfolio validator: no readable `portfolio` pack declarations — "
            "the undeclared-status rule graded nothing")
    if declared:
        # BOTH sides case-folded. The vocabulary keys are the write gate's
        # lowercased forms, while `objectKind` is stored VERBATIM
        # (`portfolio:allocationTarget` keeps its capital) — a case-sensitive
        # `IN` over lowercased forms would miss the namespaced spelling
        # entirely, which is the wider half of the same blindness.
        rows = graph.g.query(
            "MATCH (o:Object) WHERE toLower(o.objectKind) IN $kinds "
            "AND o.status IS NOT NULL "
            "RETURN o.id, o.objectKind, o.name, o.status",
            params={"kinds": sorted(declared)},
        ).result_set
        exempt = OBJECT_TERMINAL_STATUSES | _OBJECT_UNSET_STATUSES
        for node_id, object_kind, name, status in rows:
            valid = declared.get((object_kind or "").lower())
            if not valid or status in valid or status in exempt:
                continue
            violations.append({
                "rule": "undeclared_object_status",
                "kind": object_kind,
                "ref": node_id,
                "message": (
                    f"{object_kind} '{name}' carries status '{status}', which "
                    f"its kind does not declare (declared: "
                    f"{', '.join(sorted(valid))})"
                ),
                "fix": (
                    "write the state the kind declares, or amend the kind's "
                    "`statuses` in packs/investment-portfolio-manager/"
                    "manifest.yaml — a declared-statuses kind OWNS its state"
                ),
            })

    mark_kind, position_kind, mark_relation = _portfolio_mark_contract()
    # A mark is attached when it reaches a POSITION either way round:
    #   * the DECLARED relation. The manifest declares `marks` with
    #     ``mechanism: IMPL``, and a mechanism resolves to the OPERATOR write
    #     path (``pack_registry.relation_write_paths``) —
    #     ``create_operator(op_type='IMPL', label='marks', ...)`` writes
    #     ``(e)-[:INPUT]->(op:Point)-[:IMPL]->(o)``, NOT ``aboutObject``.
    #     Checking only ``aboutObject`` would call a mark wired exactly as
    #     declared an orphan. The operator's own ``label`` is REQUIRED here
    #     (``create_operator`` writes it, ``sdk.create_operator`` →
    #     ``label:$label``): otherwise any IMPL operator — ``producedBy`` to a
    #     mandate — would pass and the rule would only answer "touches some
    #     Object".
    #   * the STRUCTURAL ``aboutObject`` form some callers use, which is just as
    #     attached (it is not a declared relation, and is accepted as such).
    # Both legs require the target to be the declared ``toKind`` (read from the
    # pack, ``_portfolio_mark_contract``) — a mark that values something else is
    # not a mark on a position.
    #
    # Every kind filter is ``toLower(...) IN $forms`` over EVERY spelling the
    # write gate admits, on BOTH sides: an exact namespaced literal misses the
    # marks the extraction pipeline writes (``eventKind`` stored bare, see
    # ``_admitted_kind_forms``) — a false CLEAN on the rule's own purpose — and
    # misses a position stored under the bare ``investment``, which would flag a
    # correctly-wired mark as an orphan. Same seam, same widening as rule 1.
    rows = graph.g.query(
        "MATCH (e:Event) WHERE toLower(e.eventKind) IN $mark_forms "
        "OPTIONAL MATCH (e)-[:aboutObject]->(o1:Object) "
        "WHERE toLower(o1.objectKind) IN $position_forms "
        "OPTIONAL MATCH (e)-[:INPUT]->(op:Point {label: $rel})"
        "-[:IMPL]->(o2:Object) "
        "WHERE toLower(o2.objectKind) IN $position_forms "
        "WITH e, count(DISTINCT o1) + count(DISTINCT o2) AS targets "
        "WHERE targets = 0 "
        "RETURN e.id, e.name",
        params={
            "mark_forms": _admitted_kind_forms(mark_kind),
            "position_forms": _admitted_kind_forms(position_kind),
            "rel": mark_relation,
        },
    ).result_set
    for node_id, name in rows:
        violations.append({
            "rule": "orphaned_valuation_mark",
            "kind": mark_kind,
            "ref": node_id,
            "message": (
                f"valuation mark '{name}' is not linked to any Object — the "
                f"manifest declares `marks` as a relation to the position it "
                f"marks, so the mark cannot be joined back to what it valued"
            ),
            "fix": (
                f"record the declared `{mark_relation}` relation: "
                "create_operator('IMPL', <mark>, [<the "
                f"{position_kind} it values>], label='{mark_relation}', "
                "semantics='related')"
            ),
        })
    return violations


def _drift_warnings(domain: str) -> list[dict]:
    """Advisory drift for the domain, in the CALLER's payload (never fails a
    clean run — out-of-scope chains, e.g. dev/marketing, must not gate).

    Two sources:

      * a manifest chain declared for the domain with NO registered validator;
      * the `portfolio` degradation: its rules read their whole vocabulary off
        the pack, so an unreadable pack leaves them grading ZERO rules while
        still returning `ok=True`. That verdict is honest (there is no
        violation to report) but it is not evidence, and a server-side log line
        is not a channel any MCP/CLI caller of `tortoise_validate_domain` can
        see — so the degradation is named HERE, where it reaches the caller.
    """
    out: list[dict] = []
    if domain == _PORTFOLIO_DOMAIN and not _portfolio_declared_statuses():
        out.append({
            "rule": "portfolio_rules_unavailable",
            "kind": "domain",
            "ref": _PORTFOLIO_DOMAIN,
            "message": (
                "no readable `portfolio` pack declarations — the "
                "undeclared-status rule graded ZERO rules, so a clean "
                "verdict for this domain is not evidence"
            ),
            "fix": (
                "install or repair the `investment-portfolio-manager` pack "
                "(tortoise pack validate) and re-run "
                "tortoise_validate_domain"
            ),
        })
    spec = domain_chain_spec(domain)
    if not spec:
        return out
    registered = {s["chain_id"] for s in domain_validators(domain)}
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
