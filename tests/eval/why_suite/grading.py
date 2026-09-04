"""W3-b why-suite grading (epic #2080, issue #2100) — pure, DB-free, A11.

Grades the four why-questions per planted point from the SURFACED CONTEXT
ALONE — the canonical §3.1.4 why-block (``{point_id, support_chain, ep,
conflicts, supersession, tradeoffs, dig_deeper}``) produced by the W4
assembly (``tortoise.why.assemble_why_blocks``).  The A11 invariant is a
SIGNATURE boundary: every grader takes ``(block, expected)`` — the surfaced
context + the runner-resolved gold expectations (the ground truth the
harness owns because it planted the corpus).  No grader ever receives a
graph handle or an SDK; if the surfaced context cannot answer a why
question, the grade fails HERE — the fix is the W4 ASSEMBLY (epic R2: a
plan change, never a test fix).

Per-question contracts (E2E-7 + issue #2100 Indicator 4):

* **conflict-surfacing** — the surfaced context identifies the planted
  contradiction iff ``conflicts`` is present with ``contested: true`` AND
  >= 1 ``nands`` entry AND a dig_deeper ``nand``-kind pointer.  A clean
  point must surface NONE of these (false-positive arm: the grader must not
  invent a contradiction).
* **dig-deeper navigation** — each gold-expected ``{kind, target}`` pointer
  must appear in the context's dig_deeper with the correct resolved target
  (supports → the supporting record; nand → the counterargument;
  superseded → the successor; tradeoff → the EP-favored alternative).
* **support-chain sufficiency** — "why is this believed?" answerable from
  ``support_chain`` (non-empty, every entry carries point_id +
  content_snippet + weight) + ``ep`` (has_ep true — the belief is
  measured).
* **trade-off sufficiency** (decision points) — ``tradeoffs`` with >= 2
  alternatives each carrying ``ep_weight`` + ``mitigation``, and the
  alternative EP favors (the max-ep_weight entry — the assembly sorts
  descending) is the planted favorite.

All functions are deterministic and side-effect free.
"""

from __future__ import annotations


def _block_dig_deeper(block: dict) -> list[dict]:
    pointers = block.get("dig_deeper") or []
    return pointers if isinstance(pointers, list) else []


def _pointer_kind_targets(block: dict, kind: str) -> list[str]:
    return [
        p.get("target")
        for p in _block_dig_deeper(block)
        if p.get("kind") == kind and p.get("target")
    ]


def _conflict_surfaced(block: dict) -> bool:
    """Q1 from the surfaced context: contested + >= 1 NAND + a dig-deeper
    nand pointer (all three — E2E-1's surfacing definition)."""
    conflicts = block.get("conflicts")
    if not isinstance(conflicts, dict):
        return False
    nands = conflicts.get("nands") or []
    return (
        conflicts.get("contested") is True
        and isinstance(nands, list)
        and len(nands) >= 1
        and bool(_pointer_kind_targets(block, "nand"))
    )


def _clean_invents(block: dict) -> bool:
    """False-positive arm: a clean point's surfaced context must NOT carry
    any contradiction signal — a conflicts block (any NANDs), a contested
    ep, a nand/tradeoff/superseded dig-deeper pointer, or tradeoffs."""
    conflicts = block.get("conflicts")
    if isinstance(conflicts, dict) and (
        conflicts.get("nands") or conflicts.get("contested") is True
    ):
        return True
    ep = block.get("ep")
    if isinstance(ep, dict) and ep.get("contested") is True:
        return True
    for kind in ("nand", "superseded", "tradeoff"):
        if _pointer_kind_targets(block, kind):
            return True
    return bool(block.get("tradeoffs"))


def _support_sufficient(block: dict) -> bool:
    """Q3: support_chain non-empty with the full entry shape + measured ep."""
    chain = block.get("support_chain")
    if not isinstance(chain, list) or not chain:
        return False
    for entry in chain:
        if not isinstance(entry, dict):
            return False
        for key in ("point_id", "content_snippet", "weight"):
            value = entry.get(key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return False
    ep = block.get("ep")
    return isinstance(ep, dict) and ep.get("has_ep") is True


def _tradeoff_sufficient(block: dict, *, favored_id: str) -> bool:
    """Q4: >= 2 alternatives with ep_weight + mitigation, a tradeoff
    dig-deeper pointer, and the EP-favored alternative (max ep_weight — the
    assembly's deterministic descending sort) is the planted favorite."""
    tradeoffs = block.get("tradeoffs")
    if not isinstance(tradeoffs, list) or len(tradeoffs) < 2:
        return False
    for alt in tradeoffs:
        if not isinstance(alt, dict):
            return False
        if not isinstance(alt.get("ep_weight"), (int, float)):
            return False
        if not isinstance(alt.get("mitigation"), str) or not alt["mitigation"].strip():
            return False
        if not isinstance(alt.get("point_id"), str) or not alt["point_id"]:
            return False
    if not _pointer_kind_targets(block, "tradeoff"):
        return False
    # The assembly sorts tradeoffs by (-ep_weight, point_id): the max-ep
    # weight alternative is tradeoffs[0] — deterministic from the context.
    favored = tradeoffs[0].get("point_id")
    return favored == favored_id


def resolve_expected(gold_entry: dict, role_map_entry: dict) -> dict:
    """Resolve one gold entry's expectations against the planted role map
    (pure — the harness-side ground truth; gold carries ROLES, the runtime
    role map carries ids).  Returns::

        {"expected_conflict": bool, "clean": bool, "family": str,
         "expected_targets": [{"kind": str, "target_id": str}],
         "expected_tradeoff": bool, "favored_option_id": str | None}
    """
    expected = gold_entry.get("expected") or {}
    targets: list[dict] = []
    for t in expected.get("dig_deeper_targets") or []:
        role = t.get("target_role")
        target_id = (role_map_entry or {}).get(role)
        if target_id:
            targets.append({"kind": t.get("kind"), "target_id": target_id})
    return {
        "expected_conflict": bool(expected.get("conflict_surfacing")),
        "clean": bool(gold_entry.get("clean")),
        "family": gold_entry.get("family"),
        "expected_targets": targets,
        "expected_tradeoff": bool(expected.get("tradeoff_sufficient")),
        "favored_option_id": (role_map_entry or {}).get("option_a"),
    }


def grade_point(block: dict, expected: dict) -> dict:
    """Grade ONE point's surfaced context (the canonical why-block) against
    the resolved gold expectations.

    A11: ``block`` is the ONLY product-derived input — no graph access
    beyond the surfaced context.  Returns the per-point grade consumed by
    ``schema.aggregate_metrics``.
    """
    expected_conflict = bool(expected.get("expected_conflict"))
    clean = bool(expected.get("clean"))
    conflict_surfaced = _conflict_surfaced(block) if expected_conflict else None
    nav_correct = 0
    nav_errors: list[dict] = []
    for target in expected.get("expected_targets") or []:
        kind = target.get("kind")
        want = target.get("target_id")
        targets = _pointer_kind_targets(block, kind) if kind else []
        if want in targets:
            nav_correct += 1
        else:
            nav_errors.append(
                {
                    "kind": kind,
                    "expected_target": want,
                    "got": targets[:1],
                }
            )
    support_sufficient = _support_sufficient(block)
    family = expected.get("family")
    tradeoff_sufficient = None
    fabricated_tradeoffs = False
    if expected.get("expected_tradeoff"):
        tradeoff_sufficient = _tradeoff_sufficient(
            block, favored_id=expected.get("favored_option_id") or ""
        )
    elif block.get("tradeoffs"):
        # Q4 anti-fabrication (closed world): a non-decision point's surfaced
        # context must never carry tradeoffs — the plant never produced them.
        fabricated_tradeoffs = True
    return {
        "point_id": block.get("point_id"),
        "family": family,
        "clean": clean,
        "expected_conflict": expected_conflict,
        "conflict_surfaced": conflict_surfaced,
        "nav_correct": nav_correct,
        "nav_total": len(expected.get("expected_targets") or []),
        "nav_errors": nav_errors,
        "support_sufficient": support_sufficient,
        "tradeoff_sufficient": tradeoff_sufficient,
        "fabricated_tradeoffs": fabricated_tradeoffs,
        "false_positive": bool(_clean_invents(block)) if clean else False,
    }
