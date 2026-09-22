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

# #2405: the paraphrase-survival band. A salient unit flagged
# ``accepts_rephrase_linked`` ALSO survives when a memory Point token-covers
# its ``via_anchor`` at >= SURVIVAL_PARAPHRASE_OVERLAP (anchor-coverage
# recall semantics: the extractor distills, and verbatim-only grading
# converted paraphrase into false content_missing — measured on wp01: 2/16
# verbatim survivors vs ~12/16 semantically present). 0.45 requires roughly
# half of the anchor's content tokens to be present in the point. NOT a
# product-parity claim: the product's own symmetric dedup band
# (extractor_v2._token_overlap, NOOP_MIN_OVERLAP = 0.45 over max-length) is
# deliberately stricter — this is a RECALL band for the retention metric,
# with precision guarded by the shared-token floor + the polarity gate below.
SURVIVAL_PARAPHRASE_OVERLAP = 0.45
# Precision floor: against a multi-token anchor, a < 2-token coincidence
# never passes the band (a 1-content-token anchor is the len()==1 carve-out
# below — dead on this corpus, whose anchors carry >= 3 content tokens).
SURVIVAL_MIN_SHARED = 2
# Polarity gate (R1 P1-2): an anchor asserting a negation is only retained
# by a point that ALSO carries a negator — a point asserting the OPPOSITE of
# a negated claim must never "retain" it ("no lease rows at all" is NOT
# retained by "we found lease rows in the table"). Scope note (R2): the gate
# is one-directional + membership-anywhere — it does NOT catch an incidental
# negator elsewhere in the point ("no lease rows at all" vs "we found lease
# rows with no errors at all" passes), and the mirror direction (a point
# negating a POSITIVE anchor) is intentionally not gated (lexically
# unsolvable without false-rejecting "we shipped the fix, no rollback
# needed"). Both limits are accepted precision trade-offs of a lexical band.
_NEGATORS = frozenset({"no", "not", "never", "none", "neither", "nor",
                       "zero", "nothing", "nobody", "without"})
# Local stopword snapshot: the shared filler set (identical to
# tools/longmem_eval/evidence.py _STOPWORDS as of 2026-09-06) MINUS
# {no, not} — negators stay visible to the survival band (polarity
# precision). Deliberately LOCAL: M6's list is that benchmark's calibration
# knob; the write-path survival band must not ride it silently (R1 P2-3).
# tests/eval/write_path/test_write_path_grading.py::test_survival_stopword_set_pinned
# cross-checks this snapshot against evidence.py so drift is caught.
_SURVIVAL_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
    "in", "on", "at", "for", "with", "it", "its", "this", "that", "i",
    "you", "he", "she", "we", "they", "my", "your", "me", "him", "her",
    "us", "them", "do", "did", "does", "have", "has", "had", "be", "been",
    "yes", "ok", "okay", "so", "but", "if", "then", "there", "here",
    "what", "when", "why", "how", "just", "very", "really"})


def _survival_tokens(text: str) -> set[str]:
    """Content tokens for the survival band (lowercased, punctuation
    stripped, len > 1, filler stopwords removed — negators KEPT)."""
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
            if t not in _SURVIVAL_STOPWORDS and len(t) > 1}


def survival_match(point_content: str, anchor: str) -> bool:
    """The #2405 paraphrase leg: does ``point_content`` retain ``anchor``?

    Anchor-coverage recall band + shared-token floor + polarity gate (see the
    constants above). Verbatim containment is NOT required here — the caller
    runs ``schema.anchor_present`` first as the high-precision leg."""
    pa = _survival_tokens(anchor)
    pp = _survival_tokens(point_content)
    if not pa or not pp:
        return False
    shared = pa & pp
    if len(shared) < min(SURVIVAL_MIN_SHARED, len(pa)):
        return False
    if pa & _NEGATORS and not (pp & _NEGATORS):
        return False
    return len(shared) / len(pa) >= SURVIVAL_PARAPHRASE_OVERLAP


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
    POINT level — verbatim-anchor substring in a surviving Point, OR a
    paraphrase-band token coverage of the anchor for flagged units (#2405),
    OR a REPHRASE-linked Point when the unit accepts the link).
    ``rephrase_edges``
    name Point ids within the session; a rephrase-linked Point only counts
    when the ANCHOR hits its linked counterpart (dedup-without-deletion: the
    new wording is linked to the verbatim original — the link alone never
    rubber-stamps a hit). Units NOT flagged ``accepts_rephrase_linked``
    (dates/numbers/names/mechanics — the corpus demands near-verbatim
    fidelity) keep the verbatim-only bar.
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
        # #2405: paraphrase-survival leg — a FLAGGED unit survives when the
        # point retains its anchor under the anchor-coverage band (see
        # survival_match). Verbatim stays the high-precision leg and runs
        # first; this leg rescues meaning-retention units ONLY, with the
        # shared-token floor + polarity gate guarding precision.
        if survival_match(content, via):
            hits.append(point)
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


# ── Planted-operator (layer-2) edge grading (#2514) ───────────────────────
# The write-path bench grades CONTENT survival + leakage only; issue #2514
# adds the layer-2 question: did the extractor wire the RIGHT operator EDGE
# between the right points (instead of a bare new point)?  The planted
# operator gold (gold.<session>.planted_operators) names, per planted edge:
#   expected_kind ∈ SUPERSEDE | NEGATE | MITIGATES | SUPPORTS
#   from = the epistemically ACTIVE endpoint (newer decision / attacker /
#          evidence / mitigation action), to = the object it acts on.
#
# The graded surface extends the session snapshot (see
# runner.snapshot_session) with three additive keys:
#   operator_edges — reified operator-mediated edges: [{"op_id", "op_type"
#     (IMPL|NAND|...), "direction", "label", "endpoints": {pid: idx},
#     "source_id": the idx-0 endpoint or None}].  Reified operator nodes are
#     :Point {is_operator:true} WITHOUT eventId, so they never enter the
#     eventId-keyed memory layer — this surface is what makes them gradeable.
#   direct_edges — direct Point→Point relationships among the session's
#     memory points: [{"rel_type", "from_id", "to_id"}] (the supersession
#     CORRECTS edge lands here — supersede_point writes a direct edge).
#   mitigations — mitigation Points on operators that touch the session:
#     [{"op_id", "point_id", "content"}] ((op)-[:mitigated_by]->(m)).
#
# Kind → graph-form map (the ONTOLOGY reading; ambiguity findings F1/F2 in
# the scoping note docs/scoping/2026-09-07-2514-operator-corpus.md):
#   SUPERSEDE → a CORRECTS direct edge from-point → to-point (new corrects
#               superseded old; §3.1 supersession semantics)
#   NEGATE    → a NAND operator edge touching both endpoints (extraction
#               NANDs default unidirectional — the counter-claim attacks)
#   SUPPORTS  → an IMPL operator edge touching both endpoints
#   MITIGATES → the write path's MITIGATES form: a mitigation Point whose
#               content carries the from-anchor on an operator that touches
#               the to-anchor claim (or an op_type MITIGATES operator)
# Direction: graded as a non-blocking flag (direction_correct) — the primary
# assertion is that the RIGHT KIND of edge connects the two anchored claims.
# #2552 (the grader leg): endpoint anchoring grades VERBATIM-FIRST with the
# #2405-style paraphrase band (the SAME token-coverage recall band + polarity
# gate the unit metrics use — ``survival_match``) — a memory Point that
# distills the planted anchor still anchors. The layer-2 audit measures
# WIRING, not surface text: an S2 distillation that paraphrases an endpoint
# claim previously graded ``*_content_missing`` even when the edge itself
# was correct (measured on the #2556 run: op_01 SUPPORTS was wired and
# graded 0 because verbatim-only anchoring never found its endpoint). The
# band is recall-side on purpose; precision is guarded by the shared-token
# floor + the negation-polarity gate, exactly as the unit band is.


def _anchor_points(points: list[SessionPoint], anchor: str) -> list[SessionPoint]:
    """Memory Points of a session carrying the planted anchor text.

    Verbatim containment (``schema.anchor_present``) is the high-precision
    leg and runs first; when a point does not contain the anchor verbatim,
    the #2405-style paraphrase band (``survival_match`` — token-coverage of
    the anchor at >= SURVIVAL_PARAPHRASE_OVERLAP with the shared-token
    floor + negation-polarity gate) rescues a distillation that retained
    the claim's meaning. An empty/unmatchable anchor anchors nothing."""
    out: list[SessionPoint] = []
    for point in points:
        content = point.get("content") or ""
        if is_turn_echo(content):
            continue
        if schema.anchor_present(anchor, content) or survival_match(content, anchor):
            out.append(point)
    return out


def operator_edge_detail(
    planted: dict,
    from_points: list[SessionPoint],
    to_points: list[SessionPoint],
    snapshot: dict,
) -> dict:
    """One planted-operator edge verdict against a session's operator surface.

    ``planted`` is a gold planted_operators entry; ``from_points``/``to_points``
    are the memory Points carrying the endpoint anchors (already resolved
    across sessions by the corpus-level grader); ``snapshot`` carries the
    session's operator_edges/direct_edges/mitigations.  Returns a detail dict
    with the verdict + named failure class:
    ``from_content_missing`` / ``to_content_missing`` (a bare-point emission
    fails HERE — no Point carries the claim), ``edge_missing`` (both points
    present but no qualifying edge), ``edge_correct`` (+ optional
    ``direction_off`` flag when the edge kind is right but the active
    endpoint is not the idx-0 source / CORRECTS direction is inverted).
    """
    kind = planted.get("expected_kind")
    from_ids = [p.get("point_id") for p in from_points]
    to_ids = [p.get("point_id") for p in to_points]
    if not from_ids:
        return {"id": planted.get("id"), "expected_kind": kind,
                "verdict": "from_content_missing", "edge_correct": False}
    if not to_ids:
        return {"id": planted.get("id"), "expected_kind": kind,
                "verdict": "to_content_missing", "edge_correct": False}
    fset, tset = set(from_ids), set(to_ids)
    forms: list[str] = []
    direction_correct = False
    if kind == "SUPERSEDE":
        for edge in snapshot.get("direct_edges", []):
            if edge.get("rel_type") != "CORRECTS":
                continue
            if edge.get("from_id") in fset and edge.get("to_id") in tset:
                forms.append("CORRECTS")
                direction_correct = True  # CORRECTS is new→old by construction
    elif kind in ("NEGATE", "SUPPORTS"):
        want = "NAND" if kind == "NEGATE" else "IMPL"
        for edge in snapshot.get("operator_edges", []):
            if edge.get("op_type") != want:
                continue
            endpoints = edge.get("endpoints") or {}
            if not (fset & set(endpoints) and tset & set(endpoints)):
                continue
            forms.append(want)
            if edge.get("source_id") in fset:
                direction_correct = True
    elif kind == "MITIGATES":
        # Accepted forms (scoping finding F1 — the write path's MITIGATES is
        # a mitigation Point on an operator, or a reified op_type MITIGATES).
        for edge in snapshot.get("operator_edges", []):
            if edge.get("op_type") != "MITIGATES":
                continue
            endpoints = edge.get("endpoints") or {}
            if fset & set(endpoints) and tset & set(endpoints):
                forms.append("MITIGATES")
                direction_correct = True
        ops_by_id = {e.get("op_id"): e for e in snapshot.get("operator_edges", [])}
        for mit in snapshot.get("mitigations", []):
            content = mit.get("content") or ""
            op = ops_by_id.get(mit.get("op_id")) or {}
            endpoints = op.get("endpoints") or {}
            if not content:
                continue
            # mitigation Point content == the planted from-anchor (commit_ops
            # passes the src point content as mitigate_operator's reason) and
            # the mitigated operator touches the to-anchor claim.
            from_carries = any(schema.anchor_present(p.get("content") or "", content)
                               for p in from_points)
            if not from_carries:
                # compare directly when from_points is empty of content matches
                from_carries = schema.anchor_present(
                    planted.get("from", {}).get("verbatim_anchor") or "", content)
            if not from_carries:
                # #2552 paraphrase leg: the mitigation reason (commit_ops
                # passes the src point content verbatim) may itself distill
                # the planted from-anchor — the audit grades the WIRING, not
                # the surface text (same band + polarity gate as endpoints).
                from_carries = survival_match(
                    content,
                    planted.get("from", {}).get("verbatim_anchor") or "")
            if from_carries and (tset & set(endpoints)):
                forms.append(f"mitigated_by({mit.get('point_id')})")
                direction_correct = True
    if forms:
        detail = {"id": planted.get("id"), "expected_kind": kind,
                  "verdict": "edge_correct", "edge_correct": True,
                  "forms_found": forms}
        if not direction_correct:
            detail["direction_off"] = True
        return detail
    return {"id": planted.get("id"), "expected_kind": kind,
            "verdict": "edge_missing", "edge_correct": False}


def grade_planted_operators(
    golds: dict[str, dict],
    points_by_session: dict[str, list[SessionPoint]],
    snapshots_by_session: dict[str, dict],
) -> dict:
    """Corpus-level planted-operator grading over the graded snapshot surface.

    ``golds`` maps session_id -> gold (only sessions carrying
    ``planted_operators`` contribute), ``points_by_session`` the memory layer
    per session, ``snapshots_by_session`` the operator surface per session.
    Endpoint anchors resolve against the memory layer of the session they
    name (a planted edge's own session by default — the cross-session
    SUPERSEDE resolves its ``to`` anchor in the OTHER session's points).
    Returns::

        {"planted": int, "edge_correct": int, "content_ok": int,
         "results": {edge_id: detail}, "by_session": {sid: {...}}}

    Pooled fractions are honest denominators (planted == 0 ⇒ empty audit).
    """
    results: dict[str, dict] = {}
    by_session: dict[str, dict] = {}
    for sid, gold in sorted(golds.items()):
        ops = gold.get("planted_operators") or []
        if not ops:
            continue
        bucket: list[dict] = []
        for planted in ops:
            if not isinstance(planted, dict):
                continue
            from_sid = (planted.get("from") or {}).get("session_id") or sid
            to_sid = (planted.get("to") or {}).get("session_id") or sid
            from_points = _anchor_points(
                points_by_session.get(from_sid, []),
                (planted.get("from") or {}).get("verbatim_anchor") or "",
            )
            to_points = _anchor_points(
                points_by_session.get(to_sid, []),
                (planted.get("to") or {}).get("verbatim_anchor") or "",
            )
            # Operator surface of the FROM-anchor's session (the session whose
            # capture must have emitted the edge — for the cross-session
            # SUPERSEDE that is the owning/from session).
            surface = snapshots_by_session.get(from_sid, {})
            detail = operator_edge_detail(planted, from_points, to_points, surface)
            detail["from_session"] = from_sid
            detail["to_session"] = to_sid
            detail["owner_session"] = sid
            results[planted.get("id", "?")] = detail
            bucket.append(detail)
        by_session[sid] = {
            "planted": len(bucket),
            "edge_correct": sum(1 for d in bucket if d.get("edge_correct")),
            "content_ok": sum(
                1 for d in bucket
                if d.get("verdict") not in ("from_content_missing", "to_content_missing")
            ),
            "failures": [d for d in bucket if not d.get("edge_correct")],
        }
    all_results = list(results.values())
    return {
        "planted": len(all_results),
        "edge_correct": sum(1 for d in all_results if d.get("edge_correct")),
        "content_ok": sum(
            1 for d in all_results
            if d.get("verdict") not in ("from_content_missing", "to_content_missing")
        ),
        "results": results,
        "by_session": by_session,
    }


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
        # never read as a real fidelity bar (the note text names the vacuity;
        # the report carries the numeric span count; build_receipt copies
        # quote_spans_total into the receipt for auditors).
        "quote_fidelity": (grounded / quote_total if quote_total else 1.0),
        "provenance_accuracy": (prov_present / prov_total if prov_total else 0.0),
    }
