"""Pack-declared enforcement — the ONE shared seam (#1934, epic #1891 slice 3).

Resolves the manifest enforcement ladder (``warn | retry | block``) through a
single primitive consumed by the extraction-side kind classifier's near-miss
hook (the classifier passes NAMESPACED index kinds — see below). The SDK write
path's ``create_operator`` does NOT call this seam: it performs its own inline
bare-predicate relation check (warn-not-block) — see sdk.py. Prior state:
``PackManifest.enforcement_for*`` was validated at load but had ZERO
production call sites — the ladder was dead config. This module completes the
deferred "extractor-retry semantics layer"
(``domain_validators.resolve_rule_severity`` docstring) and the write-path
warn-not-block hook (2026-08-05 governance D1/D2: warn-not-block default;
``block`` stays out of scope — per-pack opt-in at most, adversarial over-
constraint risk).

Resolution order (single source of truth):
    kind:     kindDefs[].enforcement → extraction.enforcement.kinds → default → warn
    relation: extraction.enforcement.relations[predicate] → default → warn
    chain:    chain.enforcement → extraction.enforcement.chains[id] → default → warn

Namespaced kinds (#2030): the classifier index emits kinds in `ns:local`
form (78/79 index kinds). A namespaced kind resolves the LOCAL name against
THE declaring pack (kindDefs / extraction.enforcement.kinds are keyed by
bare local names) — exclusively, never the cross-pack max-severity scan
(a stripped name would collide across namespaces: `issue`, `workflow`, …).
Unknown/malformed namespaces (there is no `core` pack) degrade to warn —
never raise. Bare kinds keep the cross-pack max-severity scan below.

Dispatchers:
    warn  → structured warning + violations event (shape committed for the
            future governance app — tiered D4)
    retry → bounded re-attempt signal (the extractor's M3 loop provides the
            actual bounded retry; this seam marks the near-miss for it)
    block → NOT implemented (rejected at validation; documented, reserved)

The violations event shape (committed now — the governance app's future
data contract): ``{event: violation, code, kind|relation, pack, actor, ts}``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

VALID_LEVELS = ("warn", "retry", "block")


def resolve_enforcement(
    *,
    kind: str | None = None,
    relation: str | None = None,
    chain_id: str | None = None,
) -> str:
    """Resolve the enforcement level for a kind / relation / chain.

    Consumes ``PackManifest.enforcement_for*`` (pack_registry) — the ladder
    is no longer dead config — and falls back to ``warn`` (governance D1).
    ``block`` resolves but callers treat it as reserved (out of scope).
    """
    from tortoise.domain_loader import _get_registry

    reg = _get_registry()
    if reg is None:
        return "warn"
    # Severity ranking: warn(0) < retry(1) < block(2) — the MAX across packs
    # wins (an agent-ops `rule: retry` must not be downgraded by another
    # pack's warn default).
    _SEV = {"warn": 0, "retry": 1, "block": 2}
    # Namespaced kind (ns:local — the classifier index form, #2030):
    # resolve the LOCAL name against THE declaring pack, EXCLUSIVELY
    # (namespace-scoped like KindIndex.near_misses but stricter — no
    # any-namespace fallback: a stripped name feeding the cross-pack scan
    # would mis-attribute on collided bare names). Unknown/malformed
    # namespaces (e.g. `core:` — no core pack) degrade to warn, never raise.
    if kind is not None and ":" in kind:
        ns, _, local = kind.rpartition(":")
        if not local:
            return "warn"  # malformed: an empty local name never resolves
        pack = reg.get_pack(ns)
        if pack is None:
            return "warn"
        lv = pack.enforcement_for(local)
        return lv if lv in VALID_LEVELS else "warn"
    # Bare kind / relation / chain — the cross-pack max-severity scan.
    best = "warn"
    best_sev = 0
    for pack in reg.packs.values():
        if kind is not None:
            lv = pack.enforcement_for(kind)
        elif relation is not None:
            lv = pack.enforcement_for_relation(relation)
        elif chain_id is not None:
            lv = pack.enforcement_for_chain(chain_id)
        else:
            return "warn"
        if lv in VALID_LEVELS and _SEV[lv] > best_sev:
            best, best_sev = lv, _SEV[lv]
            if best == "block":
                break
    return best


def emit_violation(
    *,
    code: str,
    kind: str | None = None,
    relation: str | None = None,
    pack: str | None = None,
    actor: str | None = None,
    detail: str = "",
) -> None:
    """Emit a structured violations event (the governance app's future data
    contract). Logs a single structured line — cheap, fail-open, never raises.
    """
    event = {
        "event": "violation",
        "code": code,
        "ts": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
    }
    if kind:
        event["kind"] = kind
    if relation:
        event["relation"] = relation
    if pack:
        event["pack"] = pack
    if actor:
        event["actor"] = actor
    if detail:
        event["detail"] = detail
    log.warning("violation %s", event)


def warning_for_relation(label: str, pack: str | None = None) -> dict:
    """The structured warning shape for an undeclared relation write
    (warn-not-block — the write proceeds, the warning is returned)."""
    return {
        "code": "undeclared_relation",
        "message": f"relation predicate '{label}' is not declared by any "
                   f"installed pack — the write proceeded but the edge is "
                   f"unconstrained (warn-not-block, epic #1891 governance D1)",
        "pack": pack,
    }
