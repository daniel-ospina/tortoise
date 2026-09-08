"""#2165 — pure pre-retrieval shape classifier + subject-term extraction (PRCA).

Deterministic (zero-LLM, zero-retrieval) router that owns the fired decision
for the connected-assembly branch in ``sdk.ask()``. High-precision ordered
regexes over the question text; nothing else — no model, no graph.

Shapes with MEASURED census support (tests/_assembly_census.json — the 133-Q
temporal taxonomy, see docs/plans/2026-09-08-2165-connected-assembly.md):

* ``current-state`` — "what is the current status of X?" (fixture canary;
  the census's 2 current-state rows are duration-morphology and correctly do
  NOT fire — the measured fireable subset is the fixture + product lane).
* ``ordering`` — two-subject "Which X first, A or B?" / "Who ... first,
  A or B?" (the census's ordering/compare 34: the shape-typical rows fire;
  duration/count/N-ary rows that the SEMANTIC census filed under
  ordering/compare do NOT — measured below).
* ``interval`` — "How many days/weeks/months/years passed between A and B"
  and the two-anchor since-until form "…had passed since X when Y".

Never fires (R13 + R16 + the census negative set, ~68/133 + misfires):

* the measured census negative set — 78/133 rows across the non-fireable
  sub-classes (76/78 never fire; the 2 fires are the ADJUDICATED label-noise
  rows 370a8ff4 + gpt4_70e84552_abs, pinned in test_assembly_pure.py):
  'ago'-relative, frequency/count, duration-state, relative-date-lookup,
  nary-ordering, recency, other, pattern/recurring, offset-comparison;
  'ago' rejects EVEN with state morphology "what is the current status of X
  two weeks ago?" (reject-on-relative-offset);
* duration-state — "how long …" ("how long had I been a member when …",
  "how long did it take"), the census's 7 duration-state rows + the
  duration-morphology rows mis-filed under other classes;
* frequency/count — "how many times", "how many days did I spend", "how
  many … have passed since <one activity>" (single-anchor since);
* relative-date-lookup — "last Saturday", "on Valentine's day";
* nary-ordering — 3+ named options ("Mark and Sarah or Tom");
* recency — "most recently"; pattern/recurring; offset-comparison ("how
  many months before my anniversary");
* misfire (R16) — preference/advice with two named options + compare
  syntax ("compare X and Y which should I pick");
* single-activity / no resolvable subject.

Extraction (extract_subject_terms): template splits for the two-subject
shapes (A/B either side of the ordering "first,"-marker or the interval
"between…and…"/"since…when…" spans), single subject for current-state.
Subjects are returned as RAW text spans (quotes/possessives intact —
"my cousin's wedding", "'The Hate U Give'") for the Task-3 resolver.
"""
from __future__ import annotations

import re
from enum import StrEnum

__all__ = ["AssemblyShape", "classify_question", "extract_subject_terms"]

# public shape vocabulary (Task 6's fired branch keys on these)
class AssemblyShape(StrEnum):
    CURRENT_STATE = "current-state"
    ORDERING = "ordering"
    INTERVAL = "interval"

# ── guard morphology (checked first — a guard hit means None, even if a
#    shape template would later match: R13 reject-on-relative-offset,
#    duration/count/N-ary are NEVER two-subject shapes) ──────────────────

# 'ago'-relative + explicit relative offsets (R13 hard reject)
_RE_AGO = re.compile(
    r"\b(ago|yesterday|tonight|tomorrow|"
    r"last\s+(?:night|week|weekend|month|year|morning|afternoon|evening|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|valentine)|"
    r"this\s+(?:morning|week|month)|in\s+\d+\s+(?:days?|weeks?|months?|"
    r"years?))\b",
    re.IGNORECASE)
# duration-state semantics (NEVER an interval): "how long" with a state
# verb, "how many <unit> did I spend / had I been". NOT the bare "how long/
# how much time passed between A and B" (a genuine two-anchor interval).
_RE_DURATION = re.compile(
    r"\bhow\s+long\s+(?:had|have|did|was|were)\b|"
    r"\bhow\s+much\s+time\s+did\s+(?:i|the\s+narrator)\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?)\s+did\s+"
    r"(?:i|the\s+narrator)\s+(?:spend|take)\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?)\s+had\s+"
    r"(?:i|the\s+narrator)\s+been\b",
    re.IGNORECASE)
# frequency / count: "how many times/events/units", "how many <unit> did I
# spend", single-anchor "how many <unit> have passed since <one activity>",
# offset-count "how many <entity> before <X>" / "how many <unit> before my
# <X>" — all tied to a how-many count head (never a bare "before" search,
# which would kill legit interval anchors like "the day before Christmas").
_RE_FREQUENCY = re.compile(
    r"\bhow\s+many\s+times\b|\bhow\s+often\b|"
    r"\bhow\s+many\s+(?:days?|weeks?|months?|years?|times?|events?|charity\s+"
    r"events?)\s+(?:did\s+(?:i|the\s+narrator)\s+spend\s+|"
    r"have\s+passed\s+since\s+|did\s+it\s+take\s+|"
    r"(?:weeks?|months?|years?)\s+before\s+)",
    re.IGNORECASE)
# recency / "most recently / latest" (its own knob family, not a shape)
_RE_RECENCY = re.compile(
    r"\bmost\s+recently\b|\bmost\s+recent\b|\blatest\b|"
    r"\bthe\s+last\s+one\b", re.IGNORECASE)
# advice / preference / recommendation (R16 misfire): "which SHOULD I do
# first, A or B" carries ordering morphology but is preference, not fact —
# firing would REPLACE the working pool with a shape block. Modal + advice
# verbs reject BEFORE the ordering template.
_RE_ADVICE = re.compile(
    r"\b(?:should|ought|recommend(?:ed|ation)?|suggest(?:s|ed|ing)?|"
    r"prefer(?:red|able|ence)?|advise|better|best\s+(?:to|for)\b|"
    r"prioritize|pick|choose|decide)\b",
    re.IGNORECASE)
# N-ary: an enumerated list of 3+ options. Two shapes of escape: repeated
# " or " ("A or B or C") and a comma/semicolon list on the A-side
# ("first, A, B or C") — both checked in classify_question after the
# ordering match (bounded two-option questions carry exactly one " or " and
# a comma-free A-side).
_RE_OR_CHAIN = re.compile(r"\s+or\s+.+?\s+or\s+", re.IGNORECASE | re.DOTALL)

# ── shape templates (ordered high-precision) ─────────────────────────────

# ordering: "Which <X> (did I …|…) first[,:] A or B?" / "Who … first, A or B?"
_RE_ORDERING = re.compile(
    r"^\s*(?:which|who|what)\b[^?]*?\bfirst\b[^?]*?\b(?P<a>.+?)\s+or\s+"
    r"(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# interval: "how many <unit> (had|have) passed between A and B"
# interval "between": "how many <unit> (had|have) passed between A and B" and
# the bare "how many <unit> between A and B" form. Greedy-A/lazy-B splits on
# the LAST " and " so subjects containing " and " (e.g. "Mark and Sarah") stay
# intact on the A side.
_RE_INTERVAL_BETWEEN = re.compile(
    r"^\s*how\s+(?:many\s+(?:days?|weeks?|months?|years?)|long|"
    r"much\s+time)\s+(?:(?:had|have|were\s+there|went|elapsed)\s+)?"
    r"(?:passed\s+|elapsed\s+)?between\s+(?P<a>.+)\s+and\s+"
    r"(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# leading "Between A and B, how many <unit> passed?"
_RE_INTERVAL_BETWEEN_LEAD = re.compile(
    r"^\s*between\s+(?P<a>.+?)\s+and\s+(?P<b>[^?]+?)\s*,\s*how\s+many\s+"
    r"(?:days?|weeks?|months?|years?)\s+(?:(?:had|have)\s+)?passed\??\s*$",
    re.IGNORECASE | re.DOTALL)
# interval since-until: "how many <unit> had passed since X when Y"
_RE_INTERVAL_SINCE = re.compile(
    r"^\s*how\s+many\s+(?:days?|weeks?|months?|years?)\s+had\s+passed\s+"
    r"since\s+(?P<a>.+?)\s+when\s+(?P<b>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# current-state: "what is the current status/state of X"
_RE_CURRENT = re.compile(
    r"^\s*(?:what|which|how)\b[^?]*?\bcurrent\s+(?:status|state)\s+of\s+"
    r"(?P<a>[^?]+?)\??\s*$",
    re.IGNORECASE | re.DOTALL)
# current-state "is X still …" (product-lane state question)
_RE_CURRENT_STILL = re.compile(
    r"^\s*is\s+(?P<a>.+?)\s+still\s+(?:live|active|around|in\s+use|mine)"
    r"\??\s*$", re.IGNORECASE | re.DOTALL)


def classify_question(question: str) -> AssemblyShape | None:
    """Deterministic pre-retrieval shape router.

    Returns the AssemblyShape for questions the assembled branch may answer,
    else None (the caller falls through to the legacy lane byte-identically).
    Guards run first — a duration/'ago'/frequency/N-ary/recency hit returns
    None even when a later shape template would textually match (R13/R16).
    """
    if not question or not question.strip():
        return None
    q = question.strip()
    if (_RE_AGO.search(q) or _RE_DURATION.search(q)
            or _RE_FREQUENCY.search(q) or _RE_RECENCY.search(q)
            or _RE_ADVICE.search(q)):
        return None
    if _RE_OR_CHAIN.search(q):
        return None
    if _RE_INTERVAL_SINCE.match(q):
        return AssemblyShape.INTERVAL
    if _RE_INTERVAL_BETWEEN.match(q) or _RE_INTERVAL_BETWEEN_LEAD.match(q):
        return AssemblyShape.INTERVAL
    if _RE_ORDERING.match(q):
        # N-ary comma check: any comma between the "first" marker and the
        # final " or " means a 3+ option list ("first, A, B or C" /
        # "first: A, B or C") — reject (R13 N-ary out of v1). Legit A-sides
        # carry NO comma there ("first, my cousin's wedding or Michael's…").
        parts = re.split(r"\s+or\s+", q, flags=re.IGNORECASE)
        a_side = parts[-2] if len(parts) >= 2 else ""
        m_first = re.search(r"\bfirst\b", a_side, re.IGNORECASE)
        a_seg = (a_side[m_first.end():] if m_first else a_side)
        # skip the marker DELIMITER itself (", my cousin's wedding" /
        # ": my graduation" / "- the couch") before the N-ary comma check
        a_seg = re.sub(r"^[\s,:;–—-]+", "", a_seg)
        # strip a leading TIME/scope clause ("first in February, the bike" —
        # the comma is a clause boundary, NOT a second option)
        a_seg = re.sub(
            r"^(?:in|on|at|during|for|after|since|before|around)\s+"
            r"(?:[a-z]+\s+)*?(?:january|february|march|april|may|june|july|"
            r"august|september|october|november|december|monday|tuesday|"
            r"wednesday|thursday|friday|saturday|sunday|"
            r"20\d{2}|the\s+\w+)\s*,\s*",
            "", a_seg, flags=re.IGNORECASE)
        if "," in a_seg or ";" in a_seg:
            return None
        return AssemblyShape.ORDERING
    if _RE_CURRENT.match(q) or _RE_CURRENT_STILL.match(q):
        return AssemblyShape.CURRENT_STATE
    return None


def _clean(term: str) -> str:
    """Whitespace/leading-noise clean of an extracted subject span."""
    t = term.strip()
    t = re.sub(
        r"^(?:the\s+)?(?:day|time)\s+(?=(?:i|we|they|my)\s)", "", t,
        flags=re.IGNORECASE)
    t = re.sub(r"^(?:my\s+|the\s+)?(?:participation\s+in|attendance\s+at)\s+",
               "", t, flags=re.IGNORECASE)
    # strip ONLY whitespace + sentence punctuation — NEVER quote chars: a
    # quoted subject may be a mid-span title ("finished reading 'The Hate U
    # Give'") whose closing quote must survive for the Task-3 resolver
    return t.strip(" ?.,;:—–-") or term.strip()


def extract_subject_terms(question: str,
                          shape: AssemblyShape | None = None,
                          ) -> list[str]:
    """Subject text spans for the shape (raw — quotes/possessives intact).

    * ordering: A/B either side of the marker (split on the LAST " or ").
    * interval: the "between A and B" / "since A when B" spans.
    * current-state: the single subject of "the current status of X".

    Returns [] when the template yields no usable span (the caller's R1
    both-halves gate then keeps the fired decision false).
    """
    if not question or not question.strip():
        return []
    if shape is None:
        shape = classify_question(question)
    if shape is None:
        return []
    q = question.strip()
    if shape is AssemblyShape.CURRENT_STATE:
        m = _RE_CURRENT.match(q) or _RE_CURRENT_STILL.match(q)
        return [_clean(m.group("a"))] if m else []
    if shape is AssemblyShape.INTERVAL:
        m = (_RE_INTERVAL_SINCE.match(q) or _RE_INTERVAL_BETWEEN.match(q)
             or _RE_INTERVAL_BETWEEN_LEAD.match(q))
        if not m:
            return []
        return [_clean(m.group("a")), _clean(m.group("b"))]
    # ordering — split on the LAST " or " (two named options guaranteed by
    # the shape template + the N-ary guard)
    parts = re.split(r"\s+or\s+", q, maxsplit=0, flags=re.IGNORECASE)
    if len(parts) < 2:
        return []
    a_raw, b_raw = parts[-2], parts[-1]
    if "," in a_raw:
        # the A term sits after the marker clause ("… first, A" / "… first in
        # February, A") — take everything after the LAST comma
        a = a_raw.rsplit(",", 1)[-1]
    else:
        # dash/space-delimited marker ("which came first - the couch")
        m_first = re.search(r"\bfirst\b", a_raw, re.IGNORECASE)
        if m_first:
            a = a_raw[m_first.end():]
            a = re.sub(r"^[\s\-–—:]+", "", a)
        else:
            a = a_raw
    return [_clean(a), _clean(b_raw)]
