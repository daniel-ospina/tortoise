"""Shared evidence-marking predicates for the LongMemEval runner (M6, #1526).

Replaces the miscalibrated ``>=0.4`` content-overlap evidence predicate
(fired **1/12,085** on the v2 52-healthy run — 51/52 healthy questions with
zero evidence marks; v2 points are PARAPHRASED, so token overlap against the
verbatim answer turn almost never fires) with FOUR independent marks:

  (a) **source-session attribution** — the point's ``session_id`` is an
      evidence session (a haystack session containing ``>=1`` ``has_answer``
      turn). The point already carries its session id from both ingest legs —
      no new point property.
  (b) **verbatim anchor** — the point's source ``quote`` contains an answer
      turn (normalized substring) or n-gram-overlaps it
      ``>= EVIDENCE_QUOTE_OVERLAP``. ``quote`` is the existing commit_schema
      field (``<=200`` chars) the extractor emits as ``""`` — the D3
      deterministic anchoring populates it at ingest so mark (b) is real
      without an extractor prompt change.
  (c) **raw-chunk containment** — a raw chunk's text contains an answer turn
      (normalized-verbatim substring). Today the raw chunk is the per-session
      raw-transcript Point; later R1 turn-granular chunks are marked by the
      same predicate (chunk-agnostic).
  (d) **answer-string containment** — the point's ``content`` (or ``quote`` /
      any ``search_key``) contains the GOLD ANSWER string for its question
      (normalized-verbatim substring, analogous to mark (c)). This is the
      #1763 re-baselining mark: ``source_session`` (a) marks EVERY point from
      the answer session (472/479 of the pilot census), so the legacy
      ``evidence_recall@k`` measures "fraction of the answer session's points
      surfaced", NOT answer availability. Mark (d) is answer-precise. The
      gold answer is ONLY known to the eval harness (the dataset question's
      ``answer`` field — the extractor LLM never sees it), so mark (d) is
      computed at eval-ingest time (the ingest legs receive the full
      question dict) and counted into the census; the per-outcome recall
      numerator over it is computed at retrieval time
      (``answer_string_recall_at_k`` — the seam where ``chunk_evidence_recall@k``
      is computed, retrieve.py ``_recall_metrics``).

Marks are **eval-instrumentation only** (never a production ontology
concept); marks (a)+(b)+(c) are OR-combined at write time into the existing
eval ``has_answer`` point property by both ingest legs (``ingest.py``
deterministic + ``ingest_v2.py`` extractor). Mark (d) is recorded separately
(``marks["answer_string"]`` + the durable ``answer_string_mark`` point
property) and is deliberately NOT OR'd into ``has_answer`` — adding it would
change the legacy ``evidence_recall@k`` denominator and break the D5 #1540
comparability contract; the re-baselined metric is reported alongside it
under ``answer_string_evidence_recall@k`` (report.py, #1763). This module is
the single source of truth so the fixture calibration test
(``tests/test_lme_m6_evidence.py``) runs the identical logic offline — no
graph, no LLM keys.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# (b) n-gram fallback threshold — the run-protocol step-2 calibration knob.
EVIDENCE_QUOTE_OVERLAP = 0.5
# D3: min point<->turn overlap to anchor a quote (below it, the point is not
# attributable to any single turn — no quote written).
EVIDENCE_ANCHOR_FLOOR = 0.25
# commit_schema quote cap (v3.6 #11): ``quote: str = Field(default="", max_length=200)``.
EVIDENCE_QUOTE_CAP = 200

#: Token utilities moved from ``ingest_v2.py`` (M6) — single source of truth.
_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "to", "of",
              "and", "or", "in", "on", "at", "for", "with", "it", "its",
              "this", "that", "i", "you", "he", "she", "we", "they",
              "my", "your", "me", "him", "her", "us", "them", "do", "did",
              "does", "have", "has", "had", "be", "been", "not", "no",
              "yes", "ok", "okay", "so", "but", "if", "then", "there",
              "here", "what", "when", "why", "how", "just", "very", "really"}


def tokens(text: str) -> set[str]:
    """Content tokens of ``text`` (lowercased, punctuation stripped,
    stopwords removed, length > 1)."""
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
            if t not in _STOPWORDS and len(t) > 1}


def overlap(a: str, b: str) -> float:
    """Answer-content coverage: the fraction of ``b``'s content tokens
    present in ``a`` (stopwords stripped). Directional — a point is evidence
    only when it substantially contains the answer's meaning, not when it
    merely shares filler words."""
    tb = tokens(b)
    if not tb:
        return 0.0
    ta = tokens(a)
    if not ta:
        return 0.0
    return len(ta & tb) / len(tb)


# Back-compat aliases: ``ingest_v2.py`` re-exported these privately before M6
# (external callers import ``_overlap`` from there). Keep the private names
# working; new code uses the public ones.
_tokens = tokens
_overlap = overlap


def _normalize(text: str) -> str:
    """Case- and whitespace-insensitive verbatim form (lowercased, all runs
    of whitespace collapsed to a single space)."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def evidence_sessions(question: dict) -> set[str]:
    """Haystack session ids of sessions containing ``>=1`` ``has_answer``
    turn (the M6 evidence-session definition; ``answer_session_ids`` is
    equivalent on the 52-healthy fixture — the M7 dataset-semantics audit
    owns the equivalence proof). Uses the same id fallback as both ingest
    legs so the set always matches points' written ``session_id`` values."""
    sessions = question.get("haystack_sessions") or []
    ids = question.get("haystack_session_ids") or []
    qid = question.get("question_id", "")
    out: set[str] = set()
    for si, session in enumerate(sessions):
        if not any(bool(t.get("has_answer")) for t in session):
            continue
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        out.add(sid)
    return out


def source_session_mark(point_session_id: Any, evidence_sessions: set[str]) -> bool:
    """Mark (a): the point was written from an evidence-bearing session."""
    return bool(point_session_id) and str(point_session_id) in evidence_sessions


def quote_mark(quote: str, answer_turn_contents: list[str]) -> bool:
    """Mark (b): the point's source ``quote`` contains an answer turn
    (normalized substring) OR n-gram-overlaps it ``>= EVIDENCE_QUOTE_OVERLAP``
    (the 200-char cap truncates long turns, so containment alone would
    under-fire — D4)."""
    q = _normalize(quote)
    if not q:
        return False
    for turn in answer_turn_contents:
        t = _normalize(turn)
        if not t:
            continue
        if t in q:
            return True
        if overlap(q, t) >= EVIDENCE_QUOTE_OVERLAP:
            return True
    return False


def chunk_mark(chunk_text: str, answer_turn_contents: list[str]) -> bool:
    """Mark (c): a raw chunk's text contains an answer turn (normalized-
    verbatim substring). Chunk-agnostic — works for the per-session raw
    transcript today and R1 turn-granular chunks later."""
    c = _normalize(chunk_text)
    if not c:
        return False
    return any(bool((t := _normalize(turn)) and t in c)
               for turn in answer_turn_contents)


def answer_string_mark(point: dict, gold_answer: str) -> bool:
    """Mark (d): the point carries the question's GOLD ANSWER STRING
    (normalized-verbatim substring containment in ``content``, ``quote`` or
    any ``search_key``). Analogous to mark (c)'s containment semantics —
    answer-precise where (a) is session-wide: the pilot census is 98.5%
    source-session (472/479), so the legacy metric understates answer
    availability; mark (d) is the re-baselining denominator (#1763).

    The gold answer is a benchmark truth only the EVAL HARNESS holds (the
    dataset question's ``answer`` field — never an extractor input), so this
    predicate is computed at eval-ingest/retrieve time, never at extraction.
    Empty gold answers mark nothing (no answer, no availability claim).

    Semantics note: containment is a normalized SUBSTRING match (no word-
    boundary guard), deliberately identical to ``chunk_mark`` (c) — the
    issue's stated analog. A short/single-token gold answer therefore marks
    every point that mentions that token (e.g. gold "key" marks "monkey"):
    that is the defined containment semantics, and it is the honest "the
    answer string is available in this point" reading. Multi-word gold
    answers (the pilot norm — e.g. "Business Administration") are safe.
    """
    gold = _normalize(gold_answer)
    if not gold:
        return False
    if gold in _normalize(str(point.get("content") or "")):
        return True
    if gold in _normalize(str(point.get("quote") or "")):
        return True
    for key in (point.get("search_keys") or []) if isinstance(
            point.get("search_keys"), list) else []:
        if gold in _normalize(str(key)):
            return True
    return False


def answer_string_recall_at_k(hits: list[dict], gold_answer: str,
                              k: int) -> float | None:
    """The #1763 re-baselined point-level recall: fraction of answer-string-
    marked points (mark (d)) surfaced in the top-k hits.

    Mirrors the chunk_evidence_recall@k shape (N/A-not-0.0): None when no
    answer-string-marked point exists in ``hits`` (the deduped pool — the
    same surface the legacy ``evidence_recall@k`` is computed over), so
    "the answer never entered the pool" stays distinguishable from "it
    entered but ranked below k". ``hits`` are the annotated pool hits
    (``content`` / ``quote`` / ``search_keys`` consumed — same fields the
    ingest-leg mark (d) marks from).

    ⚠ Seam: this is the eval-time numerator the retrieval leg computes per
    outcome (the same seam where ``chunk_evidence_recall@k`` is computed —
    retrieve.py ``_recall_metrics``); report.py aggregates the per-outcome
    value as ``answer_string_evidence_recall@k``. Keeping the math here
    (evidence.py — the M6 single source of truth) lets the report's
    synthetic-outcome tests run the IDENTICAL logic offline.

    ⚠ The retrieval leg MUST use THIS content-based helper (not the durable
    ``answer_string_mark`` point property, which the ingest leg writes for
    EXTRACTED points only): raw chunks never carry the property, so a
    property-keyed numerator would silently diverge from the canonical
    pool-scoped count below. The ingest census ``answer_string`` likewise
    counts extracted points only (D5 hygiene — the raw-chunk leg is
    represented here by content matching, exactly like mark (c)).
    """
    marked = [h for h in hits if answer_string_mark(h, gold_answer)]
    if not marked:
        return None
    top = [h for h in hits[:k] if answer_string_mark(h, gold_answer)]
    return len(top) / len(marked)


def mark_for(point: dict, *, session_id: Any,
             evidence_sessions: set[str],
             answer_turn_contents: list[str],
             gold_answer: str = "") -> dict:
    """The four marks, with the per-mark breakdown for stats.

    ``point`` is the point dict (``content`` + ``quote`` + ``search_keys``
    consumed); the passed ``session_id`` is the point's session (its
    existing prop — M6 mark (a) never adds a new point property).
    ``gold_answer`` (the dataset question's ``answer`` — #1763 mark (d),
    empty when the caller has no gold answer) is NOT OR'd into
    ``has_answer``: ``has_answer`` stays the legacy (a)|(b)|(c) OR so the
    existing ``evidence_recall@k`` denominator is byte-identical (D5 #1540
    comparability); the re-baselined view reads ``marks["answer_string"]``.
    NOTE (scope): mark (d) is computed on the v2 extracted-point leg only —
    the deterministic leg (ingest.py) marks its turn points directly and
    carries no answer-string census (#1763 re-baseline targets the v2
    pilot's source-session inflation).
    Returns ``{"has_answer": bool, "marks": {"source_session": bool,
    "verbatim": bool, "raw_chunk": bool, "answer_string": bool}}``.
    """
    marks = {
        "source_session": source_session_mark(session_id, evidence_sessions),
        "verbatim": quote_mark(str(point.get("quote") or ""),
                               answer_turn_contents),
        "raw_chunk": chunk_mark(str(point.get("content") or ""),
                                answer_turn_contents),
        # #1763: answer-precise mark — recorded + census-counted, NEVER
        # OR'd into has_answer (legacy denominator must not move).
        "answer_string": answer_string_mark(point, gold_answer),
    }
    return {"has_answer": any(
        marks[m] for m in ("source_session", "verbatim", "raw_chunk")),
        "marks": marks}


def mark_for_question(question: dict | None) -> Callable[[dict], dict[str, bool]]:
    """Build the read-time mark provider for a dataset question — the eval
    adapter that binds the eval's mark computation into the PRODUCT's
    ``tortoise.retrieval.apply_evidence_boost`` (the product inversion:
    the boost transform ships in tortoise/, the dataset-derived marks stay
    eval-instrumentation).

    The returned callable maps an annotated hit to its four marks
    (``{"source_session", "verbatim", "raw_chunk", "answer_string"}``)
    via :func:`mark_for`, using the question's evidence sessions, answer
    turns and gold answer — the C2 (#1745) read-time recompute (the OR'd
    ``has_answer`` prop cannot express the verbatim-vs-source split). The
    gold answer (mark (d), #1763/#1945) is empty when the question carries
    none, in which case the answer_string class is inert.
    """
    sessions = evidence_sessions(question) if question else set()
    answer_turn_contents = [
        (t.get("content") or "")
        for s in ((question or {}).get("haystack_sessions") or [])
        for t in s if t.get("has_answer")
    ]
    gold_answer = str((question or {}).get("answer") or "")

    def _marks(h: dict) -> dict[str, bool]:
        return mark_for(
            h, session_id=h.get("session_id"),
            evidence_sessions=sessions,
            answer_turn_contents=answer_turn_contents,
            gold_answer=gold_answer)["marks"]

    return _marks


def anchor_quote(point_content: str, turns: list[Any]) -> str:
    """D3 deterministic turn-anchoring: the ``<= EVIDENCE_QUOTE_CAP``-char
    span of the raw turn with maximum token overlap against ``point_content``.

    Returns ``""`` when the best anchor overlap is below
    ``EVIDENCE_ANCHOR_FLOOR`` (the point is not attributable to any single
    turn — no provenance quote). ``turns`` accepts the session turn list
    (``{"role", "content"}`` dicts) or plain content strings.
    """
    best_turn, best_ov = "", 0.0
    for turn in turns:
        content = (str(turn.get("content") or "")
                   if isinstance(turn, dict) else str(turn))
        ov = overlap(point_content, content)
        if ov > best_ov:
            best_turn, best_ov = content, ov
    if not best_turn or best_ov < EVIDENCE_ANCHOR_FLOOR:
        return ""
    return _best_window(best_turn, point_content)


def _best_window(turn_text: str, point_content: str) -> str:
    """The ``<= EVIDENCE_QUOTE_CAP``-char span of ``turn_text`` maximizing
    token overlap with ``point_content`` (the part of the turn the point
    paraphrases — the span sharing the most content tokens with the point,
    tie-broken toward the longer window so the quote also covers the most
    of the turn for mark (b)). Prefix truncation alone fails 7/54 evidence
    turns on the 52-healthy fixture — the best-window fallback keeps mark
    (b) real for long answer turns."""
    if len(turn_text) <= EVIDENCE_QUOTE_CAP:
        return turn_text
    pt = tokens(point_content)
    words = turn_text.split()
    best, best_score = "", -1.0
    for start in range(len(words)):
        window_words: list[str] = []
        chars = 0
        for w in words[start:]:
            # a single token larger than the cap is truncated to the cap
            # (URLs/base64 — the <=200-char contract must always hold)
            if len(w) >= EVIDENCE_QUOTE_CAP:
                window_words.append(w[:EVIDENCE_QUOTE_CAP])
                chars = EVIDENCE_QUOTE_CAP
                break
            if window_words and chars + len(w) + 1 > EVIDENCE_QUOTE_CAP:
                break
            window_words.append(w)
            chars += len(w) + 1
            if chars >= EVIDENCE_QUOTE_CAP:
                break
        window = " ".join(window_words)
        if not window:
            continue
        # intersection size = the window's content tokens present in the
        # point (every window token is a turn token, so this also grows
        # the turn coverage that mark (b)'s n-gram fallback measures).
        score = len(pt & tokens(window))
        if score > best_score or (score == best_score
                                  and len(window) > len(best)):
            best, best_score = window, score
    return best or turn_text[:EVIDENCE_QUOTE_CAP]
