"""W2-b mechanical grading of the written graph against the sealed gold.

Pure, hermetic metric functions over *session graph snapshots* (plain dicts —
no DB/network/LLM import surface), so the grading math can be unit-tested in
isolation from the replay seam and the runner composes them.  The graded
input abstraction is intentionally small:

``session_points`` — the session's *memory layer*: Points the capture wrote
for this session EXCLUDING the episodic turn echo (turn Points are the raw
transcript, not memory).  Each entry::

    {"point_id": str, "content": str,          # the stored Point content
     "provenance_present": bool,               # eventId / extractedFrom stamped
     "ep_updated": bool}                       # confidence computed by the run's dream pass

``rephrase_edges`` — optional ``(pid_a, pid_b)`` pairs within the session
(memory Points linked by a REPHRASE operator edge; today's capture path does
not emit them — W5's dedup-without-deletion contract does — but the survival
rule honors them when they exist, ``docs/epistemic-layer-eval-spec.md`` §P5).

Metric semantics are pinned here (the single source of truth both the runner
and the CI gate tests assert):

* ``salient_unit_survival_macro`` — content retention.  A salient unit
  survives iff its ``survival.via_anchor`` (== the planted verbatim anchor)
  is a normalized substring of ≥1 session memory Point content, OR the unit
  ``accepts_rephrase_linked`` and the anchor hits a memory Point linked by a
  REPHRASE edge to another session memory Point.  Aggregated pooled
  (survived units / graded units across the run).
* ``salient_unit_survival_strict`` — the full write-verb quality bar
  (plan DM-2/S1: point-level salient unit with provenance + EP update).  A
  macro survivor only counts strict when its qualifying Point ALSO satisfies
  the unit's ``survival.provenance_required`` (Point carries provenance) and
  ``survival.ep_update_required`` (Point received an EP update in the run's
  dream pass).  A stripped-provenance regression drops strict while macro is
  untouched — the CI gate catches write-path regressions that pure content
  retention cannot see.
* ``provenance_accuracy`` — pooled fraction of the run's memory Points that
  carry provenance (eventId / extractedFrom stamped).  Non-vacuous: the
  denominator is the memory layer itself (≥1 when a session emitted).
* ``distractor_leakage_per_run`` — distinct gold distractors whose anchor
  appears in ANY memory Point of their own session (true-but-routine content
  must not surface as memory).  Lower-better; the sealed gold locks the
  tolerance (schema.DISTRACTOR_LEAKAGE_TOLERANCE = 1).
* ``sessions_emitting`` — sessions whose capture produced ≥1 memory Point
  (the extraction seam emitted; a session that only produced turn Points is
  a silent extraction skip) divided by the sessions replayed.
* ``quote_fidelity`` — pooled grounded-quote rate over double-quoted spans
  (≥ 8 chars) in memory Point content, grounded as normalized substrings of
  the quoting Point's OWN session transcript.  A memory that never quotes
  cannot misquote: 0 spans ⇒ fidelity 1.0 with a ``no_quoted_spans`` note on
  the run report (auditable, never silently dropped).

The quoted-span regex is pinned here (``_QUOTE_SPAN``) so judge-vs-runner
drift is impossible; quote grounding reuses ``schema.anchor_present`` (the
same normalized-substring predicate the gold cross-checks use).
"""
from __future__ import annotations

import re
from typing import Any

from tests.eval.write_path import schema

# Doubled-quoted spans that count as "a quote" for quote-fidelity grading.
# ≥ 8 chars skips noise like ``"ok"`` / ``"no"`` while keeping real verbatim
# claims.  Single quotes are NOT mined (possessive/apostrophe false
# positives in prose); a memory writer that only single-quotes surfaces a
# 0-span run → vacuous 1.0 + note (audited, never dropped).
_QUOTE_SPAN = re.compile(r'"([^"\n]{8,})"')

# Memory-layer exclusion: turn Points echo the transcript verbatim as
# ``[role] <content>``; they are the source window, NOT the graded memory.
# Defensive discriminator on top of the ``is_episodic != true`` pool rule —
# a turn-echo Point that ever reaches grading would rubber-stamp every
# anchor (its own transcript text is verbatim content).
_TURN_ECHO_PREFIX = re.compile(r"^\[(user|assistant|unknown)\]\s")

SessionPoint = dict[str, Any]


def is_turn_echo(content: str) -> bool:
    """True when a Point content is an episodic turn echo (excluded from the
    graded memory layer)."""
    return bool(_TURN_ECHO_PREFIX.match(content.strip()))


# ── Per-unit survival predicates ────────────────────────────────────────────


def _candidate_points(
    unit: dict,
    points: list[SessionPoint],
    rephrase_edges: list[tuple[str, str]],
) -> list[SessionPoint]:
    """Every session memory Point that satisfies the unit's content predicate.

    The point-level survival rule (plan DM-12: a salient unit survives at the
    POINT level — verbatim-anchor substring in a surviving Point, or a
    REPHRASE-linked Point when the unit accepts the link).  ``rephrase_edges``
    name Point ids within the session; a rephrase-linked Point only counts
    when the ANCHOR hits its linked counterpart (dedup-without-deletion: the
    new wording is linked to the verbatim original — the link alone never
    rubber-stamps a hit).
    """
    via = unit.get("survival", {}).get("via_anchor") or unit.get("verbatim_anchor") or ""
    if not via:
        return []
    accepts_rephrase = bool(unit.get("survival", {}).get("accepts_rephrase_linked"))
    by_id = {p.get("point_id"): p for p in points}
    neighbors: dict[str, list[str]] = {}
    if accepts_rephrase:
        for pid_a, pid_b in rephrase_edges:
            neighbors.setdefault(pid_a, []).append(pid_b)
            neighbors.setdefault(pid_b, []).append(pid_a)
    hits: list[SessionPoint] = []
    for point in points:
        content = point.get("content") or ""
        if is_turn_echo(content):
            continue
        if schema.anchor_present(via, content):
            hits.append(point)
            continue
        if not accepts_rephrase:
            continue
        # REPHRASE-linked acceptance (dedup-without-deletion): the point is a
        # paraphrase deduped onto the verbatim original via a REPHRASE
        # operator edge — it counts when its linked neighbor carries the
        # anchor (the link alone never rubber-stamps a hit).
        for nid in neighbors.get(point["point_id"], []):
            neighbor = by_id.get(nid)
            if neighbor is not None and schema.anchor_present(
                via, neighbor.get("content") or ""
            ):
                hits.append(point)
                break
    return hits


def macro_survival_counts(
    gold: dict,
    points: list[SessionPoint],
    rephrase_edges: list[tuple[str, str]] | None = None,
) -> dict:
    """Counts for the run's macro survival dimension over ONE session's gold.

    Returns ``{"survived": int, "total": int, "survived_ids": [...],
    "hit_point_ids": [...]}``.  ``total`` = the session's graded salient
    units (empty gold → 0/0 — the runner reports that session as a runner
    error, never a vacuum 1.0).
    """
    rephrase_edges = rephrase_edges or []
    units = gold.get("salient_units", [])
    survived_ids: list[str] = []
    hit_point_ids: list[str] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        hits = _candidate_points(unit, points, rephrase_edges)
        if hits:
            survived_ids.append(unit.get("id", "?"))
            hit_point_ids.extend(p.get("point_id") for p in hits)
    return {
        "survived": len(survived_ids),
        "total": len(units),
        "survived_ids": survived_ids,
        "hit_point_ids": sorted(set(hit_point_ids)),
    }


def strict_survival_counts(
    gold: dict,
    points: list[SessionPoint],
    rephrase_edges: list[tuple[str, str]] | None = None,
) -> dict:
    """Macro survivors that clear the write-verb quality bar on the point.

    A unit survives strict iff ≥1 of its candidate Points carries the
    provenance the unit requires (``survival.provenance_required``) AND the
    EP update the unit requires (``survival.ep_update_required``).  A
    stripped-provenance write-path regression therefore drops strict while
    macro is untouched — the two dimensions disagree, which is exactly the
    regression the CI gate must catch.
    """
    rephrase_edges = rephrase_edges or []
    units = gold.get("salient_units", [])
    strict_ids: list[str] = []
    strict_point_ids: list[str] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        survival = unit.get("survival", {})
        provenance_required = bool(survival.get("provenance_required"))
        ep_required = bool(survival.get("ep_update_required"))
        for point in _candidate_points(unit, points, rephrase_edges):
            if provenance_required and not point.get("provenance_present"):
                continue
            if ep_required and not point.get("ep_updated"):
                continue
            strict_ids.append(unit.get("id", "?"))
            strict_point_ids.append(point["point_id"])
            break
    return {
        "survived": len(strict_ids),
        "total": len(units),
        "survived_ids": sorted(strict_ids),
        "hit_point_ids": sorted(set(strict_point_ids)),
    }


def unit_level_detail(
    gold: dict,
    points: list[SessionPoint],
    rephrase_edges: list[tuple[str, str]] | None = None,
) -> dict[str, dict]:
    """Per-unit verdicts for the run report: ``{unit_id: {macro, strict}}``.

    The runner's report renders the fix-wave failure classes from these —
    naming WHICH units failed and on WHICH dimension (content missing vs
    provenance missing vs EP-update missing) is the audit trail a bare
    number cannot carry.
    """
    rephrase_edges = rephrase_edges or []
    out: dict[str, dict] = {}
    for unit in gold.get("salient_units", []):
        if not isinstance(unit, dict):
            continue
        survival = unit.get("survival", {})
        provenance_required = bool(survival.get("provenance_required"))
        ep_required = bool(survival.get("ep_update_required"))
        hits = _candidate_points(unit, points, rephrase_edges)
        macro = bool(hits)
        strict = False
        if macro:
            for point in hits:
                if provenance_required and not point.get("provenance_present"):
                    continue
                if ep_required and not point.get("ep_updated"):
                    continue
                strict = True
                break
        failure = None
        if not macro:
            failure = "content_missing"
        elif strict is False:
            if provenance_required and not any(
                p.get("provenance_present") for p in hits
            ):
                failure = "provenance_missing"
            else:
                failure = "ep_update_missing"
        out[unit.get("id", "?")] = {"macro": macro, "strict": strict, "failure": failure}
    return out


# ── Per-run dimensions ──────────────────────────────────────────────────────


def distractor_leakage(gold: dict, points: list[SessionPoint]) -> list[str]:
    """Distractor ids (of this session's gold) that leaked into the memory.

    A true-but-routine distractor's anchor appearing in ANY memory Point
    content of its own session is leakage (routine content must not surface
    as memory).  Counted distinct; the run report sums over sessions.
    """
    leaked: list[str] = []
    for distractor in gold.get("distractors", []):
        if not isinstance(distractor, dict):
            continue
        anchor = distractor.get("anchor") or ""
        if not anchor:
            continue
        for point in points:
            content = point.get("content") or ""
            if is_turn_echo(content):
                continue
            if schema.anchor_present(anchor, content):
                leaked.append(distractor.get("id", "?"))
                break
    return leaked


def quoted_spans(point_content: str) -> list[str]:
    """Pinned quoted-span extraction for quote-fidelity grading."""
    return _QUOTE_SPAN.findall(point_content)


def quote_fidelity_counts(
    gold: dict, points: list[SessionPoint], conversation: list[dict[str, str]]
) -> dict:
    """Quote-grounding counts for one session.

    Every double-quoted span (≥ 8 chars) in the session's memory Points must
    ground as a normalized substring of the session's OWN transcript (the
    memory never invents quoted speech — attribution-hazard discipline).
    Returns ``{"grounded": int, "total": int, "no_quoted_spans": bool}``.
    """
    transcript = " ".join(t.get("content") or "" for t in conversation)
    grounded = 0
    total = 0
    for point in points:
        content = point.get("content") or ""
        if is_turn_echo(content):
            continue
        for span in quoted_spans(content):
            total += 1
            if schema.anchor_present(span, transcript):
                grounded += 1
    return {"grounded": grounded, "total": total, "no_quoted_spans": total == 0}


def provenance_counts(points: list[SessionPoint]) -> dict:
    """Provenance presence counts over the session memory layer."""
    memory = [p for p in points if not is_turn_echo(p.get("content") or "")]
    present = sum(1 for p in memory if p.get("provenance_present"))
    return {"provenanced": present, "total": len(memory)}


def session_emitted(points: list[SessionPoint]) -> bool:
    """True when the capture wrote ≥1 memory Point for this session.

    A session whose capture only produced the episodic turn echo (extraction
    silently skipped or failed) did NOT emit — the 100% sessions-emitting
    invariant grades the extraction seam, not the turn-store.
    """
    return any(not is_turn_echo(p.get("content") or "") for p in points)


# ── Run aggregation ─────────────────────────────────────────────────────────


def aggregate_metrics(session_results: list[dict]) -> dict:
    """Fold per-session grading results into the canonical 6-metric snapshot.

    ``session_results`` is one dict per replayed session (see
    ``runner.grade_session``)::

        {"session_id", "gold_total_units", "macro": {...}, "strict": {...},
         "leaked": [...], "quotes": {...}, "provenance": {...},
         "emitted": bool}

    Aggregation is POOLED (units/points across sessions), so a larger session
    cannot be gamed by weighting; every session in the corpus contributes.
    Sessions that were never replayed (runner errors) are excluded from the
    denominator of ``sessions_emitting`` — but a corpus session missing from
    ``session_results`` entirely makes the emitting rate < 1.0 below, which
    is the honest signal (the runner also raises when a session's gold has no
    graded units — a vacuum 1.0 would otherwise rubber-stamp an empty gold).
    """
    n_sessions = len(session_results)
    n_emitted = sum(1 for r in session_results if r["emitted"])
    macro_survived = sum(r["macro"]["survived"] for r in session_results)
    macro_total = sum(r["macro"]["total"] for r in session_results)
    strict_survived = sum(r["strict"]["survived"] for r in session_results)
    strict_total = sum(r["strict"]["total"] for r in session_results)
    leaked = [leak for r in session_results for leak in r["leaked"]]
    grounded = sum(r["quotes"]["grounded"] for r in session_results)
    quote_total = sum(r["quotes"]["total"] for r in session_results)
    prov_present = sum(r["provenance"]["provenanced"] for r in session_results)
    prov_total = sum(r["provenance"]["total"] for r in session_results)
    return {
        "salient_unit_survival_macro": (
            macro_survived / macro_total if macro_total else 0.0
        ),
        "salient_unit_survival_strict": (
            strict_survived / strict_total if strict_total else 0.0
        ),
        "distractor_leakage_per_run": len(leaked),
        "sessions_emitting": n_emitted / n_sessions if n_sessions else 0.0,
        # REVIEW-FIX (F3, PR #2183 code-review): quote_fidelity is NOT a
        # measure of memory when the gold never quotes — `1.0` on zero quote
        # spans is a vacuous floor, not a hit bar. The snapshot stays the
        # canonical 6-metric vocabulary (the bless gate validates against
        # METRIC_VALUES); vacuity is surfaced as a separate REPORT-level
        # ``quote_spans_total`` field + runner note so the committed 1.0 is
        # never read as a real fidelity bar (see runner: quote note + receipt
        # carry ``quote_vacuous``).
        "quote_fidelity": (grounded / quote_total if quote_total else 1.0),
        "provenance_accuracy": (prov_present / prov_total if prov_total else 0.0),
    }
