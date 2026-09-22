"""W3 harness grading (issue #2099 W3-a) — pure, DB-free.

Grades a session's replay transcript + graph snapshot against the sealed
gold.  The REFLEX decision seam (know_to_ask / push suites) is graded on the
injected pointers a session produced — a NULL reflex injects nothing, so the
honest first numbers are failing (fix-wave protocol); the W4 delivery issue
lands the graded reflex on this seam and its acceptance IS this grader going
green.

Per-suite grade contracts (consumed by runner.py):

* know_to_ask — per gold.per_turn: ``should_retrieve`` turns the replay did
  NOT inject for ⇒ ``missed``; non-retrieve turns that DID fire ⇒ fires
  (anti-gaming: courtesy / re-mention / below-notability never fire).
* push — pointer precision/recall vs the gold ``pointers`` per turn, under
  the pointer budget (runner caps injected per turn).
* write_back — gold planted anchors found in the session graph snapshot with
  provenance intact.
* continuity — READER session graded on the WRITER's planted anchors
  surfacing in the reader's recall transcript.
* isolation — per-team session: other-team anchored content found in this
  team's graph snapshot ⇒ violation (E2E-4 gate, this issue's pass gate).

All functions are deterministic and side-effect free.
"""
from __future__ import annotations

import re

# ── Content matching ────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Casefold + collapse whitespace for anchor matching."""
    return re.sub(r"\s+", " ", text.strip().casefold())


def anchor_hits(anchor: str, texts: list[str]) -> int:
    """Number of ``texts`` whose normalized content contains the normalized
    anchor.  Anchor matching is substring-level (a planted fact embedded in
    a longer point still counts as surfaced)."""
    want = _norm(anchor)
    if not want:
        return 0
    return sum(1 for text in texts if want in _norm(text))


def find_point_for_anchor(anchor: str, points: list[dict]) -> dict | None:
    """The first session point whose content contains the normalized anchor."""
    want = _norm(anchor)
    for point in points:
        if want in _norm(point.get("content") or ""):
            return point
    return None


# ── know_to_ask / push (reflex-graded; transcript-driven) ──────────────────

def grade_kta(session_id: str, gold: dict, injected: dict[int, list[str]]) -> dict:
    """Grade a know_to_ask session.

    ``injected`` maps fixture-turn index (gold's numbering) → pointer ids the
    replay injected for that turn (null reflex ⇒ {} everywhere).  Returns the
    per-session ``kta`` + ``false_fire`` grade.
    """
    labels: dict[int, bool] = {}
    for entry in gold.get("per_turn", []):
        turn = entry["turn"]
        labels[turn] = bool(entry.get("should_retrieve"))
    missed = 0
    should_total = 0
    fires = 0
    silent_required = 0
    for user_index, should in labels.items():
        if should:
            should_total += 1
            if not injected.get(user_index):
                missed += 1
        else:
            silent_required += 1
            if injected.get(user_index):
                fires += 1
    return {
        "session_id": session_id,
        "suite": "know_to_ask",
        "kta": {"missed": missed, "should": should_total},
        "false_fire": {"fires": fires, "silent_required": silent_required},
        "emitted": True,
    }


def grade_push(session_id: str, gold: dict, injected: dict[int, list[str]],
               *, budget: int = 3) -> dict:
    """Grade a push session under the pointer budget.

    precision = gold-acceptable injected / total injected (per turn, capped
    at ``budget``); recall = gold-acceptable injected / gold-required
    pointers.  Pooled across turns by the caller (schema.aggregate_metrics).
    Injections at ``should_retrieve: false`` turns (courtesy / re-mention)
    are FALSE FIRES — the push suite's anti-gaming surface is measured too
    (review round-1 P2: a courtesy fire on the push seam must not vanish
    from false_fire_rate).
    """
    gold_required: dict[int, list[str]] = {}
    silent_turns: list[int] = []
    for entry in gold.get("per_turn", []):
        turn = entry["turn"]
        if entry.get("should_retrieve") and entry.get("pointers"):
            gold_required[turn] = entry["pointers"]
        elif entry.get("should_retrieve") is False:
            silent_turns.append(turn)
    prec_num = prec_den = rec_num = rec_den = 0
    for turn, required in gold_required.items():
        # Unique injected pointers per turn (round-1/4 P2: duplicate injected
        # ids must not over-count recall past 1.0 — recall measures GOLD
        # coverage, so multiplicity on the injected side is meaningless).
        injected_turn = list(dict.fromkeys((injected.get(turn) or [])[:budget]))
        rec_den += len(required)
        rec_num += sum(1 for pid in injected_turn if pid in required)
        prec_den += len(injected_turn)
        prec_num += sum(1 for pid in injected_turn if pid in required)
    fires = sum(1 for turn in silent_turns if injected.get(turn))
    return {
        "session_id": session_id,
        "suite": "push",
        "push": {
            "prec_num": prec_num, "prec_den": prec_den,
            "recall_num": rec_num, "recall_den": rec_den,
        },
        "false_fire": {"fires": fires, "silent_required": len(silent_turns)},
        "emitted": True,
    }


# ── write_back (graded today — graph snapshot) ─────────────────────────────

def grade_write_back(session_id: str, gold: dict, points: list[dict]) -> dict:
    """Fidelity = planted anchors that survived with their PROVENANCE intact
    when the gold requires it (review round-1 P1 fix: a provenance-stripping
    write path must not pass fidelity 1.0 on content-only matches).
    ``unprovenanced`` stays diagnostic detail."""
    planted = (gold.get("write_back") or {}).get("planted_points", [])
    provenance_required = bool((gold.get("write_back") or {}).get(
        "provenance_required", True))
    survived = 0
    unprovenanced = 0
    missing: list[str] = []
    for anchor in planted:
        point = find_point_for_anchor(anchor, points)
        if point is None:
            missing.append(anchor)
            continue
        if provenance_required and not point.get("provenance_present"):
            unprovenanced += 1
            continue  # not a fidelity survival — provenance is part of the bar
        survived += 1
    return {
        "session_id": session_id,
        "suite": "write_back",
        "write_back": {
            "survived": survived, "total": len(planted),
            "unprovenanced": unprovenanced, "missing": missing,
            "provenance_required": provenance_required,
        },
        "emitted": True,
    }


# ── continuity (reader recall surfaces writer's planted decision) ──────────

def grade_continuity(session_id: str, gold: dict,
                     recall_transcript: list[str]) -> dict:
    """Reader-session grade: writer-planted anchors surfaced in the reader's
    recall transcript (the assistant reply text + any injected context)."""
    spec = (gold.get("continuity") or {})
    anchors = spec.get("reader_planted", [])
    surfaced = sum(1 for anchor in anchors if anchor_hits(anchor, recall_transcript))
    return {
        "session_id": session_id,
        "suite": "continuity",
        "continuity": {
            "surfaced": surfaced, "total": len(anchors),
            "writer_session": spec.get("writer_session"),
        },
        "emitted": bool(recall_transcript),
    }


# ── isolation (cross-team content in the wrong team's graph) ───────────────

def grade_isolation(session_id: str, gold: dict, points: list[dict],
                    *, own_team: str) -> dict:
    """Per-gold isolation grade (unit-tested surface): other-team anchors
    listed in THIS gold's ``teams`` map present in the cell graph are
    violations; own-team anchors present are expected (fidelity)."""
    teams = (gold.get("teams") or {})
    own_anchors = (teams.get(own_team) or {}).get("anchors", [])
    other_anchors = []
    for other_team, spec in teams.items():
        if other_team != own_team:
            other_anchors.extend((spec or {}).get("anchors", []))
    return grade_isolation_vs(
        session_id, points, own_team=own_team,
        own_anchors=own_anchors, other_anchors=other_anchors,
    )


def grade_isolation_vs(session_id: str, points: list[dict], *, own_team: str,
                       own_anchors: list[str], other_anchors: list[str]) -> dict:
    """Union isolation grade (the E2E-4 gate surface — review round 1,
    P1 finding): ``other_anchors`` is the UNION of the other team's anchors
    across ALL corpus suites (write_back planted anchors, continuity
    reader_planted anchors, isolation-gold team anchors) — a leak of ANY
    other-team content (Mercury, Atlas, OR Orion) into this team's cell is
    a violation, not just the single-anchor sample a per-gold map lists."""
    violations = 0
    violation_anchors: list[str] = []
    for anchor in other_anchors:
        if find_point_for_anchor(anchor, points) is not None:
            violations += 1
            violation_anchors.append(anchor)
    own_present = sum(1 for a in own_anchors
                      if find_point_for_anchor(a, points) is not None)
    return {
        "session_id": session_id,
        "suite": "isolation",
        "isolation": {
            "violations": violations,
            "violation_anchors": violation_anchors,
            "own_anchors_present": own_present,
            "own_anchors_total": len(own_anchors),
            "other_anchors_probed": len(other_anchors),
        },
        "emitted": True,
    }
