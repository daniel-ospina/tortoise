"""#1370 — write-time, confidence-gated, fail-closed Point→Subject binding.

**The contract (owner-locked, #1353 D10 + #1370 design comments):**

- Binding happens at **write time**, never at query time.
- **Fail-closed: no subject > wrong subject.** If extraction is unsure, the
  point is left UNBOUND — an absent subject is honest, a wrong one is not.
- The subject is a **≤1-hop** fact: a direct ``(Point)-[:aboutSubject]->``
  ``(Subject)`` edge (or the point's event's edge, resolved by #1353's read
  path). Never derived through operator chains.
- Bindings are **journaled** (auditable, replayable, fixable via the existing
  edge/supersede machinery). A refusal is recorded, never silently dropped
  — **for journal-configured SDKs**. ``_emit_event`` is a no-op on a
  journal-less SDK; the hosted lane becomes journal-configured when
  ``TORTOISE_EVENT_LOG_BASE_DIR`` is set (#4240), and stays journal-less
  otherwise (``hosted_api._make_sdk`` / ``_data_sdk``). On a journal-less
  lane refusals are live-only and NOT recorded: the same qualifier
  ``session_link.py`` carries for #3664 applies here. ``live == rebuild`` is
  likewise scoped to journal-configured SDKs.

**This module is the ONLY confidence-gated ``aboutSubject`` producer.** The
legacy ``about_entities`` channel is *topic annotation*, not attribution — it
must not emit ``aboutSubject`` (an un-gated producer would bypass the gate and
the #1353 read surface cannot tell the two apart). The capture seams therefore
*skip* subject-kind names in their ``about_entities`` resolvers. That skip is
DELIBERATE and applies to the hosted **Event** resolver too: an Event's
``about_entities`` is dropped for subject-kind names because the only permitted
Event→Subject writer would be an un-gated one (the gated binder reads a Point's
``slots``, not an Event's ``about_entities``), so the attribution is
intentionally not replaced. Recorded as a residual in the scoping doc.

**Identity is name-keyed.** ``_resolve_target`` resolves a slot to an existing
node by NAME (the same contract as ``_mint_subject_stub`` / ``_upsert_subject``),
so a slot and its target cannot disagree on name. They CAN disagree on KIND: a
slot whose declared kind differs from the resolved node's stored
``subjectKind``/``objectKind`` is REFUSED fail-closed rather than bound to a
node the read surface would then report with a contradicting kind.

**Malformed slots** (a non-dict/non-model entry, a blank name, a non-list role
value) are refused with a warning and a journaled refusal record — never a
silent drop (T4/D6) and never a raise.

**Tri-state gate (D4):** ``bound`` ≥ τ_hi / ``suspected`` ∈ [τ_lo, τ_hi) /
``unbound`` < τ_lo. The suspected band is *tracked* (journaled) but writes no
edge. Thresholds default to τ_hi=0.7 / τ_lo=0.4 (D4 "start ~0.6–0.7") and are
env-overridable; they are load-bearing, not decorative.

**Vocabulary (D2):** the declared subject kinds are the keys of
``extractor_v2.SUBJECTS``. ``other`` is deliberately NOT bindable — it is the
fail-closed NIL bucket (``known_kinds("subjectKind")`` resolves empty; the
ONTOLOGY §5 ``other`` is a catch-all, not an identity). ``is_subject_kind``
accepts a declared key EXACTLY, or its bare form when the declared key is in
the ``core:`` namespace — never an arbitrary/foreign namespace, never a
leading-colon form, and never a case/whitespace variant (T2). Unknown kinds are
never invented into a Subject.
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

#: The declared §5 Subject kind vocabulary — ``extractor_v2.SUBJECTS`` keys
#: EXACT, with ``other`` excluded on purpose (see module docstring).
def _declared_subject_kinds() -> frozenset[str]:
    from tortoise.extractor_v2 import SUBJECTS
    return frozenset(
        str(k).strip() for k in SUBJECTS
        # `other` is the NIL/catch-all bucket, not a bindable identity:
        # binding it would attach a fact to a node whose kind carries no
        # information — exactly the "wrong subject" the gate forbids.
        if str(k).strip().split(":", 1)[-1].lower() != "other"
    )


_DECLARED_SUBJECT_KINDS: frozenset[str] = _declared_subject_kinds()


def _normalize_kind(kind: Any) -> str:
    """Comparison form of a kind: strip a ``core:`` prefix and lowercase.

    Deliberately NOT a blind ``split(":")``: a foreign namespace must not be
    folded onto a declared bare name (``acme:team`` ≠ ``core:team``), so a
    stored kind from an arbitrary namespace still registers as a mismatch.
    """
    k = str(kind).strip()
    if k.lower().startswith("core:"):
        k = k.split(":", 1)[1]
    return k.lower()


def subject_kind_names() -> frozenset[str]:
    """The bindable subject-kind vocabulary, EXACTLY as declared in
    ``extractor_v2.SUBJECTS`` (``other`` excluded). Public alias consumed by
    the drift test in ``tests/test_subject_binding_1370.py``, which pins this
    set against the extractor's declaration so the two cannot drift."""
    return _DECLARED_SUBJECT_KINDS


def is_subject_kind(kind: Any) -> bool:
    """True iff ``kind`` names a DECLARED §5 Subject kind (D2).

    Accepted EXACTLY: either a declared key (``core:team``) or — only when the
    declared key lives in the ``core:`` namespace — its bare form (``team``).
    A foreign namespace (``acme:team``, ``pack:role``, ``evil.org:naturalPerson``),
    a leading-colon form (``:team``), a case/whitespace variant of a declared
    key, ``other``, and ``None``/``""`` are all False (T2). Never invent a
    Subject from an arbitrary namespace.
    """
    if not isinstance(kind, str):
        return False
    # G2: compare the RAW string — NO whitespace normalization. The former
    # `kind.strip()` made `' core:team '` / `'core:team\n'` classify as
    # subject kinds while the module docstring and scoping D2 declare a
    # case/WHITESPACE variant non-subject. Fail-closed is this module's
    # contract, so reject rather than normalize.
    if kind in _DECLARED_SUBJECT_KINDS:
        return True
    if ":" in kind:
        # A namespaced form is accepted only when it is a declared key itself
        # (handled above). Never strip an arbitrary namespace to a bare name.
        return False
    # Bare form: accepted only for a declared ``core:`` key.
    return f"core:{kind}" in _DECLARED_SUBJECT_KINDS


def _as_float(value: Any) -> float | None:
    """Coerce a slot confidence to a finite float, or None (fail-closed).

    Delegates to the shared ``session_link.coerce_confidence`` so the binder,
    the live writer and the replay fold cannot disagree on what a usable
    confidence is (bool/NaN/±inf/overflow/out-of-[0,1] ⇒ None).
    """
    from tortoise.session_link import coerce_confidence
    return coerce_confidence(value)


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
    ``TORTOISE_SUBJECT_TAU_LO``) > module default.

    Fail-closed validation (F11/G3): the resolved pair must be finite and
    satisfy ``0.0 < tau_hi <= 1.0`` and ``0.0 <= tau_lo <= tau_hi``. A
    parseable-but-out-of-range value (``TAU_HI=0`` / ``-1``), an ALL-ZERO
    pair (``TAU_HI=0.0`` with ``TAU_LO=0.0`` — the G3 fail-open), an inverted
    pair, and NaN/inf are all rejected — a warning is logged and the SHIPPED
    defaults are used. An unvalidated out-of-range/zero ``tau_hi`` would make
    a zero-confidence slot ``bound`` and turn the gate fail-open.
    """
    default = (DEFAULT_TAU_HI, DEFAULT_TAU_LO)

    def _pick(value, env_name, fallback):
        if value is None:
            raw = os.environ.get(env_name, "").strip()
            if not raw:
                return fallback, True
            try:
                value = float(raw)
            except ValueError:
                logger.warning("%s=%r is not a number — using defaults",
                               env_name, raw)
                return fallback, False
        try:
            f = float(value)
        except (TypeError, ValueError, OverflowError):
            logger.warning("%s=%r is unusable — using defaults",
                           env_name, value)
            return fallback, False
        if not math.isfinite(f):
            logger.warning("%s=%r is not finite — using defaults",
                           env_name, value)
            return fallback, False
        return f, True

    hi, hi_ok = _pick(tau_hi, "TORTOISE_SUBJECT_TAU_HI", DEFAULT_TAU_HI)
    lo, lo_ok = _pick(tau_lo, "TORTOISE_SUBJECT_TAU_LO", DEFAULT_TAU_LO)
    # G3: `tau_hi` must be STRICTLY positive. `0.0 <= lo <= hi <= 1.0`
    # admitted the `(0.0, 0.0)` pair, under which a 0.0-confidence slot is
    # `bound` and an edge is written — the gate fails open.
    if not (hi_ok and lo_ok) or not (0.0 < hi <= 1.0 and 0.0 <= lo <= hi):
        logger.warning(
            "subject_binding: invalid thresholds (tau_lo=%r, tau_hi=%r) — "
            "fail-closed to the shipped defaults %r", lo, hi, default)
        return default
    return hi, lo


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
    a malformed entry yields blanks and is skipped by the caller.

    ``kind`` is returned RAW (no whitespace normalization) — the D2/T2 gate
    (``is_subject_kind``) compares the exact declared key, so normalizing here
    would hand the gate a value the caller never supplied and make a
    whitespace-variant kind bind. Normalization for the *post-gate* stored-kind
    comparison lives in ``_normalize_kind``, applied only after the gate has
    already accepted the raw value.
    """
    if isinstance(entry, dict):
        return (str(entry.get("name") or "").strip(),
                str(entry.get("kind") or ""),
                entry.get("confidence"))
    return (str(getattr(entry, "name", "") or "").strip(),
            str(getattr(entry, "kind", "") or ""),
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
            # Fail-closed/honest: a NaN/inf/overflow/out-of-range value is
            # recorded as None, never as an unusable number.
            confidence=_as_float(confidence),
            outcome=outcome, reason=reason)
    except Exception:  # noqa: BLE001, RUF100 — journaling is best-effort
        logger.warning("subject_binding: refusal journal emit failed for %s",
                       point_id, exc_info=True)


def _resolve_target(proj, label: str, name: str) -> tuple[str | None, str | None]:
    """Resolve ``name`` to an existing ``:label`` node; return ``(id, kind)``.

    Never mints: an unresolved name is a refusal (D8 — the fail-open name-stub
    path is not reachable from the binder). The returned id is ONLY the
    ``{id:...}`` identity ``link_entity`` can address — an id-less/``eventId``-only
    legacy stub is unresolved (G1), never offered as a target nothing can link
    to. ``kind`` is the resolved node's
    stored ``subjectKind`` (Subject) / ``objectKind`` (Object) when present and
    non-blank, else None — the binder compares it against the slot's declared
    kind fail-closed (F10).
    """
    kind_prop = "subjectKind" if label == "Subject" else "objectKind"
    rows = proj.g.query(
        f"MATCH (n:{label} {{name:$name}}) "
        f"RETURN n.id, n.{kind_prop} LIMIT 2",
        params={"name": name}).result_set
    if len(rows) != 1:
        return None, None
    row = list(rows[0])
    # G1: the returned id MUST be the ``{id:...}`` identity ``link_entity``
    # (and the binder's F7 re-probe) matches on. A legacy stub carrying ONLY
    # an ``eventId`` is NOT addressable — advertising it as a target made
    # ``link_entity`` match nothing and (pre-G1) emit a phantom
    # ``EntityLinked``. A non-addressable node is an unresolved target:
    # refuse fail-closed rather than name an id nothing can link to.
    target_id = (row[0] if row and isinstance(row[0], str)
                 and row[0].strip() else None)
    stored = row[1] if len(row) > 1 else None
    return target_id, (stored if isinstance(stored, str) and stored.strip()
                       else None)


def _is_slot_entry(entry: Any) -> bool:
    """True when ``entry`` is a dict or a model carrying a slot shape.

    A JSON scalar (``None``/``str``/``int``) is NOT — the caller refuses it
    with a warning + a journaled record (T4/D6: never a silent drop).
    """
    return (isinstance(entry, dict)
            or hasattr(entry, "name") or hasattr(entry, "kind"))


def bind_point_subjects(proj, sdk, *, point_id: str, slots: Any,
                        tau_hi: float | None = None,
                        tau_lo: float | None = None) -> list[dict]:
    """Bind one Point's ``subject``/``object`` slots with the tri-state gate.

    Returns a per-slot outcome list ``[{role, name, kind, confidence, outcome,
    reason}]``. Writes an ``aboutSubject``/``aboutObject`` edge **only** for a
    ``bound`` subject-kind (resp. object-kind) slot that resolves to an
    existing target; every other outcome is journaled as a refusal.
    """
    from tortoise.session_link import emit_entity_linked, link_entity

    hi, lo = resolve_thresholds(tau_hi, tau_lo)
    outcomes: list[dict] = []
    if not point_id or slots is None:
        return outcomes
    if not isinstance(slots, dict) and not any(
            hasattr(slots, role) for role in _ROLE_TARGET):
        # T4: a wrong container type (a bare list/scalar) reads no slots —
        # warned, never a silent drop, and never a raise.
        logger.warning(
            "subject_binding: slots payload for %s is %s — neither a dict "
            "nor a slot model; no slots read", point_id, type(slots).__name__)
        return outcomes

    for role, (label, edge_type) in _ROLE_TARGET.items():
        for entry in _role_entries(slots, role):
            # T4 / D6: a malformed entry (non-dict/model, or a blank name) is
            # REFUSED with a warning and a journaled record — never a silent
            # drop, never a raise that could sink the commit.
            if not _is_slot_entry(entry):
                logger.warning(
                    "subject_binding: %s slot entry for %s is %s, not a "
                    "dict/model — refused", role, point_id,
                    type(entry).__name__)
                outcomes.append({"role": role, "name": "", "kind": "",
                                 "confidence": None, "outcome": UNBOUND,
                                 "reason": "malformed_entry"})
                _journal_refusal(sdk, point_id=point_id, role=role, name="",
                                 kind="", confidence=None, outcome=UNBOUND,
                                 reason="malformed_entry")
                continue
            name, kind, confidence = _entry_fields(entry)
            if not name:
                logger.warning(
                    "subject_binding: %s slot entry for %s has a blank name "
                    "— refused", role, point_id)
                outcomes.append({"role": role, "name": "", "kind": kind,
                                 "confidence": confidence,
                                 "outcome": UNBOUND,
                                 "reason": "malformed_entry"})
                _journal_refusal(sdk, point_id=point_id, role=role, name="",
                                 kind=kind, confidence=confidence,
                                 outcome=UNBOUND, reason="malformed_entry")
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

            target_id, stored_kind = _resolve_target(proj, label, name)
            if not target_id:
                rec.update(outcome=UNBOUND, reason="unresolved")
                outcomes.append(rec)
                _journal_refusal(sdk, point_id=point_id, role=role, name=name,
                                 kind=kind, confidence=confidence,
                                 outcome=UNBOUND, reason="unresolved")
                continue
            # F10: the resolved node's stored kind must agree with the slot's
            # declared kind (normalized: `core:team` == bare `team`). A mismatch
            # is refused fail-closed — the read surface would otherwise report
            # a `kind` contradicting the very edge.
            if stored_kind is not None \
                    and _normalize_kind(stored_kind) != _normalize_kind(kind):
                rec.update(outcome=UNBOUND, reason="kind_mismatch",
                           target_id=target_id, stored_kind=stored_kind)
                outcomes.append(rec)
                _journal_refusal(sdk, point_id=point_id, role=role, name=name,
                                 kind=kind, confidence=confidence,
                                 outcome=UNBOUND, reason="kind_mismatch")
                continue

            c = _as_float(confidence)
            created = link_entity(proj, "Point", point_id, target_id,
                                  edge_type, label, sdk=sdk, confidence=c)
            if created:
                rec.update(outcome=BOUND, reason="linked", created=1,
                           target_id=target_id)
            else:
                # F7/G1: `link_entity` returns 0 for TWO distinct states — (a)
                # the edge ALREADY EXISTS (its pre-probe short-circuits) and
                # (b) the endpoint pair was ABSENT so nothing was written (its
                # documented "neither reported nor replayed" contract). Never
                # infer (a) from `created == 0`: RE-PROBE the edge and take the
                # already-present path ONLY when it is genuinely there. The old
                # inference emitted a phantom `EntityLinked` for an edge that
                # does not exist and reported `bound`/`already_present` —
                # replay would then resurrect it.
                present = proj.g.query(
                    f"MATCH (s:Point {{id:$sid}})-[r:{edge_type}]->"
                    f"(t:{label} {{id:$tid}}) RETURN count(r)",
                    params={"sid": point_id, "tid": target_id},
                ).result_set
                if not (present and present[0][0]):
                    # Identical disposition to an unresolvable target: refuse
                    # fail-closed, journal the refusal, emit NO EntityLinked.
                    rec.update(outcome=UNBOUND, reason="unresolved",
                               target_id=target_id)
                    outcomes.append(rec)
                    _journal_refusal(sdk, point_id=point_id, role=role,
                                     name=name, kind=kind,
                                     confidence=confidence, outcome=UNBOUND,
                                     reason="unresolved")
                    continue
                # The legacy `about_entities` channel may have written this
                # edge BEFORE the binder ran (with no confidence).
                # `link_entity` short-circuits on the pre-probe, so the
                # confidence would be discarded. Apply it to the EXISTING edge
                # and journal the same `EntityLinked` record the writer emits,
                # so live == rebuild (the fold's conditional SET re-applies it).
                if c is not None:
                    proj.g.query(
                        f"MATCH (s:Point {{id:$sid}})-[r:{edge_type}]->"
                        f"(t:{label} {{id:$tid}}) SET r.confidence=$conf",
                        params={"sid": point_id, "tid": target_id,
                                "conf": c})
                    emit_entity_linked(
                        sdk, source_label="Point", source_id=point_id,
                        target_id=target_id, target_label=label,
                        edge_type=edge_type, confidence=c)
                rec.update(outcome=BOUND, reason="already_present", created=0,
                           target_id=target_id)
            outcomes.append(rec)
    return outcomes


# ── Audit + quality gate (indicators 3 & 4) ──────────────────────────────

def audit_subject_binding(graph, journal_path: str | None = None) -> dict:
    """Attribution audit: the bound / unbound / suspected fractions.

    Numerator and denominator are the SAME population (F8): the point-level
    ``bound_fraction`` is ``DISTINCT bound Points / all Points`` (an EDGE count
    over a POINT count could exceed 1.0 — reproduced 3 edges / 2 points = 1.5),
    and the raw edge count is reported separately as ``bound``. The journal
    count is scoped to Point-sourced ``EntityLinked`` records — permitted
    ``Document→Subject`` / ``Event→Subject`` records must not inflate a
    Point-only denominator.

    Graph-derived counts are always available; the *unbound* denominator (the
    attempted-but-refused population) lives in the JSONL journal. Without one —
    or with a path that does not exist — the report says so honestly
    (``journal: None``) rather than inventing a denominator.
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
    rows = graph.query(
        "MATCH (p:Point)-[:aboutSubject]->(:Subject) "
        "RETURN count(DISTINCT p)").result_set
    bound_points = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    rows = graph.query("MATCH (s:Subject) RETURN count(s)").result_set
    subjects_total = int(rows[0][0]) if rows and rows[0][0] is not None else 0

    journal_exists = bool(journal_path) and os.path.exists(journal_path)
    report = {
        "points_total": points_total,
        "subjects_total": subjects_total,
        "bound": bound,               # EDGES (reported separately from the rate)
        "bound_points": bound_points,  # DISTINCT Points (the rate numerator)
        "edges_with_confidence": with_conf,
        "bound_fraction": (
            (bound_points / points_total) if points_total else 0.0),
        "journal": journal_path if journal_exists else None,
        "attempted": None,
        "unbound": None,
        "suspected": None,
        "unbound_fraction": None,
    }
    if journal_exists:
        attempted = bound_journal = suspected = unbound = 0
        # F14/G5: a path that EXISTS but cannot be READ (a directory, a
        # chmod-000 file) must not crash the report — the graph counts are
        # already computed and the honest answer is "denominator UNKNOWN".
        # Leave `journal` as the path (it does exist) and `unbound_fraction`
        # None, so the CLI prints its UNKNOWN branch instead of formatting a
        # None metric.
        try:
            # Split so only the OPEN is guarded (a read-time OSError is
            # equally non-fatal); the handle is closed by `with fh:` below.
            fh = open(journal_path)  # noqa: SIM115
        except OSError:
            logger.warning(
                "subject_binding: journal %r exists but is unreadable — "
                "unbound denominator stays UNKNOWN", journal_path,
                exc_info=True)
            return report
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                t = ev.get("type")
                if (t == "EntityLinked"
                        and ev.get("edge_type") == "aboutSubject"
                        and ev.get("source_label") == "Point"):
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


def _seed_gate_graph(proj, rows: list[dict]) -> None:
    """Seed one ``:Subject``/``:Object`` node per distinct ``(label, name)``
    across every row's ``graph_nodes`` — the row-carried mini-graph the quality
    gate resolves against. First-seen kind wins for a repeated name."""
    seen: set[tuple[str, str]] = set()
    idx = 0
    for row in rows:
        for node in (row.get("graph_nodes") or []):
            if not isinstance(node, dict):
                continue
            nname = str(node.get("name") or "").strip()
            nkind = str(node.get("kind") or "").strip()
            if not nname:
                continue
            label = "Subject" if is_subject_kind(nkind) else "Object"
            if (label, nname) in seen:
                continue
            seen.add((label, nname))
            idx += 1
            kind_prop = "subjectKind" if label == "Subject" else "objectKind"
            proj.g.query(
                f"MERGE (n:{label} {{id:$id}}) "
                f"SET n.name=$name, n.{kind_prop}=$kind",
                params={"id": f"sbgate_node_{idx}", "name": nname,
                        "kind": nkind})


def quality_gate_metrics(gold_path: str, *, tau_hi: float | None = None,
                         tau_lo: float | None = None) -> dict:
    """Sampled review of the binding POLICY against authored ground truth.

    Each gold row is ``{id, name, kind, confidence, gold_subject|null,
    graph_nodes}``. The metric is LOAD-BEARING (F9): it seeds a throwaway graph
    from every row's ``graph_nodes`` and runs the REAL resolution/decision path
    (``bind_point_subjects`` → ``_resolve_target`` → the tri-state gate) once
    per row, then reads the resolved subject back off the graph edge. A binding
    is a **misattribution** when an edge exists but the resolved subject is not
    the row's gold (including ``gold_subject: null`` — a row that should have
    stayed unbound). ``unbound_rate`` is the fraction of rows that SHOULD have
    bound (gold subject present) but did not.

    This is NOT a static-flag computation: a row's outcome depends on its
    ``graph_nodes`` (a name absent from the mini-graph is unresolvable even at
    confidence 1.0) and on the resolved node's stored kind (a kind mismatch is
    refused fail-closed, F10). Flipping a row's ``graph_nodes`` changes the
    computed rate.

    Honest limitation: this measures the *policy* (threshold + fail-closed +
    resolution) against AUTHORED labels, not the LLM's extraction quality — the
    labels are not model-produced. Real per-model τ calibration (D4) needs
    model calls against a ~100–500-fact gold set and is out of scope here.
    """
    hi, lo = resolve_thresholds(tau_hi, tau_lo)
    rows = []
    with open(gold_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    import shutil
    import tempfile
    import uuid

    from tortoise.projection import FalkorProjection

    tmp = tempfile.mkdtemp(prefix="tt_sbgate_")
    graph_name = f"test_sbgate_{uuid.uuid4().hex[:12]}"
    proj = FalkorProjection(os.path.join(tmp, "gate.db"),
                            graph_name=graph_name)
    try:
        _seed_gate_graph(proj, rows)
        bound = misattributed = 0
        gold_rows = unmet = 0
        for row in rows:
            name = str(row.get("name") or "")
            kind = str(row.get("kind") or "")
            gold = row.get("gold_subject")
            pid = f"sbgate_pt_{row.get('id')}"
            proj.g.query("MERGE (p:Point {id:$pid})", params={"pid": pid})
            bind_point_subjects(
                proj, None, point_id=pid,
                slots={"subject": [{"name": name, "kind": kind,
                                     "confidence": row.get("confidence")}]},
                tau_hi=hi, tau_lo=lo)
            edges = proj.g.query(
                "MATCH (:Point {id:$pid})-[:aboutSubject]->(s:Subject) "
                "RETURN s.name", params={"pid": pid}).result_set
            resolved = edges[0][0] if len(edges) == 1 else None
            if gold is not None:
                gold_rows += 1
            if resolved is not None:
                bound += 1
                if gold is None or gold != resolved:
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
    finally:
        try:
            proj.db.select_graph(graph_name).delete()
        except Exception:  # noqa: BLE001, RUF100 — best-effort cleanup
            logger.warning("quality_gate_metrics: gate graph cleanup failed",
                           exc_info=True)
        shutil.rmtree(tmp, ignore_errors=True)
