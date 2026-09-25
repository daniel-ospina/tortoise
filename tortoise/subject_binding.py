"""#1370 — write-time, confidence-gated, fail-closed Point→Subject binding.

**The contract (owner-locked, #1353 D10 + #1370 design comments):**

- Binding happens at **write time**, never at query time.
- **Fail-closed: no subject > wrong subject.** If extraction is unsure, the
  point is left UNBOUND — an absent subject is honest, a wrong one is not.
- The subject is a **≤1-hop** fact: a direct ``(Point)-[:aboutSubject]->``
  ``(Subject)`` edge (or the point's event's edge, resolved by #1353's read
  path). Never derived through operator chains.
- Bindings are **journaled** (auditable, replayable, fixable via the existing
  edge/supersede machinery). A refusal is recorded, never silently dropped.

**This module is the ONLY confidence-gated ``aboutSubject`` producer.** The
legacy ``about_entities`` channel is *topic annotation*, not attribution — it
must not emit ``aboutSubject`` (an un-gated producer would bypass the gate and
the #1353 read surface cannot tell the two apart). The capture seams therefore
*skip* subject-kind names in their ``about_entities`` resolvers.

**Tri-state gate (D4):** ``bound`` ≥ τ_hi / ``suspected`` ∈ [τ_lo, τ_hi) /
``unbound`` < τ_lo. The suspected band is *tracked* (journaled) but writes no
edge. Thresholds default to τ_hi=0.7 / τ_lo=0.4 (D4 "start ~0.6–0.7") and are
env-overridable; they are load-bearing, not decorative.

**Vocabulary (D2):** the declared subject kinds are the keys of
``extractor_v2.SUBJECTS``. ``other`` is deliberately NOT bindable — it is the
fail-closed NIL bucket (``known_kinds("subjectKind")`` resolves empty; the
ONTOLOGY §5 ``other`` is a catch-all, not an identity). Unknown kinds are never
invented into a Subject.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Thresholds (D4) ───────────────────────────────────────────────────────
DEFAULT_TAU_HI = 0.7
DEFAULT_TAU_LO = 0.4

BOUND = "bound"
SUSPECTED = "suspected"
UNBOUND = "unbound"

#: roles this binder processes → (target label, edge type). The ``event`` slot
#: is deliberately absent: it binds CONTENT aboutness per the #1417 contract
#: (Point→aboutEvent), which this issue does not own.
_ROLE_TARGET = {
    "subject": ("Subject", "aboutSubject"),
    "object": ("Object", "aboutObject"),
}

#: The declared §5 Subject kind vocabulary — ``extractor_v2.SUBJECTS`` keys,
#: bare, lowercased, with ``other`` excluded on purpose (see module docstring).
def _declared_subject_kinds() -> frozenset[str]:
    from tortoise.extractor_v2 import SUBJECTS
    return frozenset(
        str(k).split(":", 1)[-1].lower() for k in SUBJECTS
        # `other` is the NIL/catch-all bucket, not a bindable identity:
        # binding it would attach a fact to a node whose kind carries no
        # information — exactly the "wrong subject" the gate forbids.
        if str(k).split(":", 1)[-1].lower() != "other"
    )


_DECLARED_SUBJECT_KINDS: frozenset[str] = _declared_subject_kinds()


def subject_kind_names() -> frozenset[str]:
    """The bindable subject-kind vocabulary (bare, lowercased). Public alias
    for the drift test / audit tooling."""
    return _DECLARED_SUBJECT_KINDS


def is_subject_kind(kind: Any) -> bool:
    """True iff ``kind`` names a declared §5 Subject kind (namespace-tolerant).

    Fail-closed: ``None``, ``""``, an unknown kind, or ``other`` → False, so a
    slot with such a kind can never be bound as a Subject.
    """
    if not isinstance(kind, str):
        return False
    bare = kind.strip().split(":", 1)[-1].lower()
    return bare in _DECLARED_SUBJECT_KINDS


def _as_float(value: Any) -> float | None:
    """Coerce a slot confidence to a finite float, or None (fail-closed).

    A ``bool`` is rejected (``True`` is not a confidence), as are strings,
    NaN/inf, and out-of-range values — an unusable signal must not be read as
    a high one.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    if not math.isfinite(f) or f < 0.0 or f > 1.0:
        return None
    return f


def decide_binding(confidence: Any, *, tau_hi: float = DEFAULT_TAU_HI,
                   tau_lo: float = DEFAULT_TAU_LO) -> str:
    """The tri-state confidence gate (pure).

    Returns ``bound`` / ``suspected`` / ``unbound``. A non-numeric, NaN,
    out-of-range or missing confidence is ``unbound`` (fail-closed).
    """
    c = _as_float(confidence)
    if c is None:
        return UNBOUND
    if c >= tau_hi:
        return BOUND
    if c >= tau_lo:
        return SUSPECTED
    return UNBOUND


def resolve_thresholds(tau_hi: float | None = None,
                       tau_lo: float | None = None) -> tuple[float, float]:
    """Resolve thresholds: explicit arg > env (``TORTOISE_SUBJECT_TAU_HI`` /
    ``TORTOISE_SUBJECT_TAU_LO``) > module default. Invalid env values fall
    back to the default (never raise on the write path)."""
    def _pick(explicit, env_name, default):
        if explicit is not None:
            return float(explicit)
        raw = os.environ.get(env_name, "").strip()
        if raw:
            try:
                return float(raw)
            except ValueError:
                logger.warning("%s=%r is not a number — using default %s",
                               env_name, raw, default)
        return default

    return (_pick(tau_hi, "TORTOISE_SUBJECT_TAU_HI", DEFAULT_TAU_HI),
            _pick(tau_lo, "TORTOISE_SUBJECT_TAU_LO", DEFAULT_TAU_LO))


# ── Slot reading (dict payload OR ParticipantSlots model) ─────────────────

def _role_entries(slots: Any, role: str) -> list[Any]:
    if slots is None:
        return []
    value = slots.get(role) if isinstance(slots, dict) else getattr(slots, role, None)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    logger.warning("subject_binding: slots.%s is %s, not a list — dropped",
                   role, type(value).__name__)
    return []


def _entry_fields(entry: Any) -> tuple[str, str, Any]:
    """(name, kind, confidence) for a slot entry (dict or model). Best-effort:
    a malformed entry yields blanks and is skipped by the caller."""
    if isinstance(entry, dict):
        return (str(entry.get("name") or "").strip(),
                str(entry.get("kind") or "").strip(),
                entry.get("confidence"))
    return (str(getattr(entry, "name", "") or "").strip(),
            str(getattr(entry, "kind", "") or "").strip(),
            getattr(entry, "confidence", None))


def _journal_refusal(sdk, *, point_id: str, role: str, name: str, kind: str,
                     confidence: Any, outcome: str, reason: str) -> None:
    """Record an attempted-and-refused binding (D6: a tracked state, never a
    silent drop). Best-effort — journaling never sinks the commit."""
    if sdk is None:
        return
    try:
        sdk._emit_event(
            "EntityBindingRefused", id=point_id, point_id=point_id,
            role=role, name=name, kind=kind,
            confidence=confidence if isinstance(confidence, (int, float))
            and not isinstance(confidence, bool) else None,
            outcome=outcome, reason=reason)
    except Exception:  # noqa: BLE001, RUF100 — journaling is best-effort
        logger.warning("subject_binding: refusal journal emit failed for %s",
                       point_id, exc_info=True)


def _resolve_target(proj, label: str, name: str) -> str | None:
    """Resolve ``name`` to an existing ``:label`` node id, or None.

    Never mints: an unresolved name is a refusal (D8 — the fail-open name-stub
    path is not reachable from the binder).
    """
    rows = proj.g.query(
        f"MATCH (n:{label} {{name:$name}}) "
        "RETURN n.id, n.eventId LIMIT 2",
        params={"name": name}).result_set
    if len(rows) != 1:
        return None
    # A Subject node's identity is `id`; be tolerant of an eventId-only stub
    # (never produced here, but the graph may hold legacy nodes).
    return rows[0][0] or rows[0][1]


def bind_point_subjects(proj, sdk, *, point_id: str, slots: Any,
                        tau_hi: float | None = None,
                        tau_lo: float | None = None) -> list[dict]:
    """Bind one Point's ``subject``/``object`` slots with the tri-state gate.

    Returns a per-slot outcome list ``[{role, name, kind, confidence, outcome,
    reason}]``. Writes an ``aboutSubject``/``aboutObject`` edge **only** for a
    ``bound`` subject-kind (resp. object-kind) slot that resolves to an
    existing target; every other outcome is journaled as a refusal.
    """
    from tortoise.session_link import link_entity

    hi, lo = resolve_thresholds(tau_hi, tau_lo)
    outcomes: list[dict] = []
    if not point_id or slots is None:
        return outcomes

    for role, (label, edge_type) in _ROLE_TARGET.items():
        for entry in _role_entries(slots, role):
            try:
                name, kind, confidence = _entry_fields(entry)
            except Exception:  # noqa: BLE001, RUF100 — never sinks the commit
                logger.warning("subject_binding: malformed %s slot entry "
                               "skipped for %s", role, point_id)
                continue
            if not name:
                continue
            rec = {"role": role, "name": name, "kind": kind,
                   "confidence": confidence}

            if role == "subject" and not is_subject_kind(kind):
                # D2: never invent a Subject from an undeclared kind.
                rec.update(outcome=UNBOUND, reason="kind_not_subject")
                outcomes.append(rec)
                _journal_refusal(sdk, point_id=point_id, role=role, name=name,
                                 kind=kind, confidence=confidence,
                                 outcome=UNBOUND, reason="kind_not_subject")
                continue

            outcome = decide_binding(confidence, tau_hi=hi, tau_lo=lo)
            if outcome != BOUND:
                rec.update(outcome=outcome,
                           reason="confidence_band")
                outcomes.append(rec)
                _journal_refusal(sdk, point_id=point_id, role=role, name=name,
                                 kind=kind, confidence=confidence,
                                 outcome=outcome, reason="confidence_band")
                continue

            target_id = _resolve_target(proj, label, name)
            if not target_id:
                rec.update(outcome=UNBOUND, reason="unresolved")
                outcomes.append(rec)
                _journal_refusal(sdk, point_id=point_id, role=role, name=name,
                                 kind=kind, confidence=confidence,
                                 outcome=UNBOUND, reason="unresolved")
                continue

            c = _as_float(confidence)
            created = link_entity(proj, "Point", point_id, target_id,
                                  edge_type, label, sdk=sdk, confidence=c)
            rec.update(outcome=BOUND, reason="linked", created=int(created),
                       target_id=target_id)
            outcomes.append(rec)
    return outcomes


# ── Audit + quality gate (indicators 3 & 4) ──────────────────────────────

def audit_subject_binding(graph, journal_path: str | None = None) -> dict:
    """Attribution audit: the bound / unbound / suspected fractions.

    Graph-derived counts are always available; the *unbound* denominator (the
    attempted-but-refused population) lives in the JSONL journal — without one
    the report says so honestly rather than inventing a denominator.
    """
    rows = graph.query(
        "MATCH (p:Point) RETURN count(p)").result_set
    points_total = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    rows = graph.query(
        "MATCH (:Point)-[r:aboutSubject]->(:Subject) "
        "RETURN count(r), count(r.confidence)"
    ).result_set
    bound = int(rows[0][0]) if rows else 0
    with_conf = int(rows[0][1]) if rows and rows[0][1] is not None else 0
    rows = graph.query("MATCH (s:Subject) RETURN count(s)").result_set
    subjects_total = int(rows[0][0]) if rows and rows[0][0] is not None else 0

    report = {
        "points_total": points_total,
        "subjects_total": subjects_total,
        "bound": bound,
        "edges_with_confidence": with_conf,
        "bound_fraction": (bound / points_total) if points_total else 0.0,
        "journal": journal_path or None,
        "attempted": None,
        "unbound": None,
        "suspected": None,
        "unbound_fraction": None,
    }
    if journal_path and os.path.exists(journal_path):
        attempted = bound_journal = suspected = unbound = 0
        with open(journal_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if t == "EntityLinked" and ev.get("edge_type") == "aboutSubject":
                    bound_journal += 1
                elif t == "EntityBindingRefused":
                    attempted += 1
                    outcome = ev.get("outcome")
                    if outcome == SUSPECTED:
                        suspected += 1
                    elif outcome == UNBOUND:
                        unbound += 1
        attempted += bound_journal
        report.update(
            attempted=attempted, unbound=unbound, suspected=suspected,
            unbound_fraction=(unbound / attempted) if attempted else 0.0,
        )
    return report


def quality_gate_metrics(gold_path: str, *, tau_hi: float | None = None,
                         tau_lo: float | None = None) -> dict:
    """Sampled review of the binding POLICY against authored ground truth.

    Each gold row is ``{id, name, kind, confidence, gold_subject|null,
    resolvable}``. A binding is a **misattribution** when it is ``bound`` but
    the gold subject is absent (should have stayed unbound) or names a
    different entity. ``unbound_rate`` is the fraction of rows that SHOULD
    have bound (gold subject present) but did not.

    Honest limitation: this measures the *policy* (threshold + fail-closed +
    resolution), not the LLM's extraction quality — the labels are authored,
    not model-produced.
    """
    hi, lo = resolve_thresholds(tau_hi, tau_lo)
    rows = []
    with open(gold_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    bound = misattributed = 0
    gold_rows = unmet = 0
    for row in rows:
        name = str(row.get("name") or "")
        kind = str(row.get("kind") or "")
        gold = row.get("gold_subject")
        resolvable = row.get("resolvable", True)
        outcome = UNBOUND
        if is_subject_kind(kind):
            outcome = decide_binding(row.get("confidence"),
                                     tau_hi=hi, tau_lo=lo)
            if outcome == BOUND and not resolvable:
                outcome = UNBOUND
        if gold is not None:
            gold_rows += 1
        if outcome == BOUND:
            bound += 1
            if gold is None or gold != name:
                misattributed += 1
        elif gold is not None:
            unmet += 1

    return {
        "tau_hi": hi, "tau_lo": lo, "rows": len(rows),
        "bound": bound, "misattributed": misattributed,
        "misattribution_rate": (misattributed / bound) if bound else 0.0,
        "gold_rows": gold_rows, "unmet": unmet,
        "unbound_rate": (unmet / gold_rows) if gold_rows else 0.0,
    }
