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

# ruff: noqa: I001  — the module is deliberately section-appended (each
# #2165 task adds its own imports mid-file); global import-sorting would
# restructure the append boundary on every task.
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


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 3 — resolver: recall-first, confidence-tagged, no LLM
# (R1 both-halves gate, R7 never a silent single-match, R10 embedded
# degrade, R12/C7 collision-with-ids).
#
# Chain per subject term: (1) exact-name/id probe (embedded-safe — one
# batched query over normalized variants) → high; (2) docker Object
# name-FTS for paraphrases → med; (3) alias amplifier from anchored
# points' search_keys → low. Missing/raising FTS/alias legs degrade to []
# (R10) — a term that still resolves nowhere stays UNRESOLVED (the
# both-halves gate keeps the fired decision false, R1).
# ══════════════════════════════════════════════════════════════════════════

from dataclasses import dataclass, field  # noqa: E402
from datetime import UTC as _UTC, date as _date, datetime as _datetime  # noqa: E402
from typing import Protocol  # noqa: E402


@dataclass(frozen=True)
class SubjectCandidate:
    """One resolved Object candidate for one subject term (R7/R12/C7)."""

    subject_index: int          # index into the shape's term list
    object_id: str
    name: str
    confidence: str             # "high" (exact) | "med" (fts) | "low" (alias)
    source: str                 # "exact" | "fts" | "alias"
    term: str = ""


@dataclass(frozen=True)
class ResolveResult:
    candidates: tuple[SubjectCandidate, ...] = ()
    unresolved: tuple[str, ...] = ()

    def candidates_for(self, subject_index: int) -> list[SubjectCandidate]:
        return [c for c in self.candidates if c.subject_index == subject_index]

    def both_halves_ok(self, shape: AssemblyShape | None) -> bool:
        """R1: two-subject shapes admit BOTH halves or fire nothing. A
        single-subject shape (current-state) fires when its one term
        resolved. None shape never fires here (classify first)."""
        if shape is None:
            return False
        if shape is AssemblyShape.CURRENT_STATE:
            # never fire with ZERO candidates — an empty resolved set is an
            # unresolved subject, not a match
            return bool(self.candidates) and not self.unresolved
        # ordering/interval: every term must resolve (a partial admit would
        # silently narrow the shape to a single subject — forbidden)
        return not self.unresolved and len(
            {c.subject_index for c in self.candidates}) == 2


class ResolverPort(Protocol):
    """The graph seam the resolver reads through (dict-stubbed in unit
    tests; the docker adapter wraps a live TortoiseSDK/projection)."""

    def exact_objects(self, names: list[str]) -> list[dict]: ...

    def fts_objects(self, term: str, limit: int = 8) -> list[dict]: ...

    def alias_objects(self, term: str, limit: int = 8) -> list[dict]: ...


# name normalization: leading determiners/possessives/gerunds + a head cut
# at participial/relative modifiers ("the dog bed getting chewed" → dog bed;
# "the couch I bought in March" → the couch)
_LEAD_NOISE = re.compile(
    r"^(?:the|a|an|my|our|their|your|her|his|its)\s+", re.IGNORECASE)
_GERUND_LEAD = re.compile(
    r"^(?:(?:buy|sell|order|get|fix|trim|start|use|visit|attend|join|take|"
    r"paint|move|replace|purchase|clean|repair|finish|read|watch|meet|see|"
    r"plant|water|harvest|cancel|cook|host|try|rent|build|adopt|bring|wear|"
    r"make|collect|receive|deliver|return|lose|find|book|plan|host|attend|"
    r"participate|complete|set\s+up|sign\s+up\s+for)\w*\s+)", re.IGNORECASE)
_HEAD_CUT = re.compile(
    r"\s+(?:(?:i|you|we|they|he|she|it)\s+)?(?:getting|being|bought|"
    r"sold|delivered|received|ordered|moved|chewed|repaired|painted|fixed|"
    r"replaced|started|finished|installed|came|went|that|which|who|from|"
    r"by|with|in)\b", re.IGNORECASE)


def _candidate_names(term: str) -> list[str]:
    """Ordered name variants for the exact probe (raw span → cleaned head →
    article-stripped head → gerund+article-stripped → quote-stripped).
    Empty/inert terms yield [] (no probe)."""
    out: list[str] = []
    t = term.strip().strip(" ?.,;:—–-")
    if not t:
        return []
    # strip a WRAPPING quote pair so quoted titles reach the exact probe
    # ("'The Hate U Give'" → The Hate U Give); mid-span quotes untouched
    if len(t) >= 2 and t[0] in "'\"'" and t[-1] == t[0]:
        t = t[1:-1].strip()
    if not t:
        return []
    out.append(t)
    # head cut at a participial/relative modifier
    m = _HEAD_CUT.search(t)
    head = t[: m.start()].strip() if m else t
    for v in (head,):
        if v and v not in out:
            out.append(v)
    v = _LEAD_NOISE.sub("", head, count=1).strip() if head else ""
    for cand in (v,):
        if cand and cand not in out:
            out.append(cand)
    v2 = _GERUND_LEAD.sub("", t, count=1)
    for cand in (v2, _LEAD_NOISE.sub("", v2, count=1).strip()):
        if cand and cand not in out:
            out.append(cand)
    return out


def resolve_subjects(port: ResolverPort, terms: list[str], *,
                     shape: AssemblyShape | None) -> ResolveResult:
    """Deterministic no-LLM resolver: prose terms → Object candidates.

    Confidence/source: exact probe → high/exact; Object name-FTS → med/fts;
    anchored search_keys alias → low/alias. Never a silent single match
    (R7): a term matching several Objects yields every candidate with its
    id. Missing/raising FTS or alias legs degrade to [] (R10 embedded).
    """
    candidates: list[SubjectCandidate] = []
    unresolved: list[str] = []
    for idx, term in enumerate(terms):
        names = _candidate_names(term)
        # leg 1 — exact name/id probe (embedded-safe, batched)
        found: list[dict] = []
        if names:
            try:
                found = list(port.exact_objects(names) or [])
            except Exception:
                found = []
        if found:
            seen: set[str] = set()
            for row in found:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="high", source="exact", term=term))
            continue
        # leg 2 — docker Object name-FTS (paraphrase recall)
        rows: list[dict] = []
        try:
            if names:
                rows = list(port.fts_objects(names[0][:160]) or [])
        except Exception:
            rows = []
        if rows:
            seen = set()
            for row in rows:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen or oid in {c.object_id
                                          for c in candidates
                                          if c.subject_index == idx}:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="med", source="fts", term=term))
            continue
        # leg 3 — alias amplifier (anchored points' search_keys)
        rows = []
        try:
            rows = list(port.alias_objects(term) or [])
        except Exception:
            rows = []
        if rows:
            seen = set()
            for row in rows:
                oid = str(row.get("id") or "")
                key = f"{oid}\x00{row.get('name')}"
                if key in seen or oid in {c.object_id
                                          for c in candidates
                                          if c.subject_index == idx}:
                    continue
                seen.add(key)
                candidates.append(SubjectCandidate(
                    subject_index=idx, object_id=oid,
                    name=str(row.get("name") or ""),
                    confidence="low", source="alias", term=term))
            continue
        unresolved.append(term)
    return ResolveResult(candidates=tuple(candidates),
                         unresolved=tuple(unresolved))


def docker_resolver_port(sdk) -> ResolverPort:
    """Adapter over a live TortoiseSDK: exact probe via the projection's
    Object id/name index (one batched query), FTS via
    ``tortoise_fts_query(entity_type='object')``, alias via one anchored
    search_keys query. Function-level imports keep the module import-safe
    (no sdk import at module scope)."""
    proj = sdk._get_proj()

    def exact_objects(names: list[str]) -> list[dict]:
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.name IN $names OR o.id IN $names "
            "RETURN o.id, o.name",
            params={"names": names}).result_set
        return [{"id": r[0], "name": r[1]} for r in rows]

    def fts_objects(term: str, limit: int = 8) -> list[dict]:
        # raises on embedded (no fulltext index) — the resolver degrades
        hits = sdk.tortoise_fts_query(term, entity_type="object",
                                      limit=limit)
        return [{"id": h.get("id", ""), "name": h.get("content", "")}
                for h in hits or []]

    def alias_objects(term: str, limit: int = 8) -> list[dict]:
        tokens = [t for t in re.split(r"[^a-z0-9]+", term.lower())
                  if len(t) >= 3][:6]
        if not tokens:
            return []
        rows = proj.g.query(
            "MATCH (p:Point)-[:aboutObject]->(o:Object) "
            "WHERE p.search_keys IS NOT NULL AND "
            "ANY(t IN $tokens WHERE toLower(p.search_keys) CONTAINS t) "
            "RETURN o.id, o.name, collect(p.id) LIMIT $limit",
            params={"tokens": tokens, "limit": limit}).result_set
        return [{"id": r[0], "name": r[1]} for r in rows]

    import types
    # SimpleNamespace — NOT a class body: class-local assignment would shadow
    # the enclosing closure names (the classic class-body scoping gotcha)
    return types.SimpleNamespace(exact_objects=exact_objects,
                                 fts_objects=fts_objects,
                                 alias_objects=alias_objects)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 4 — typed walker + slice builder (R2/R3-8/R12, R17 P3-1/
# P3-6; P3-3 DEFERRED: the _RECALL_OBJECT_EXCLUDED_STATUS Object-status
# exclusion tuple binds at RESOLVE/RENDER time (Task 5) — it is NEVER
# applied to a resolved subject's own state row (the superseded couch's
# state IS the answer) nor to Points). One batched typed walk (never
# row-level N+1, never blind BFS): state slice
# (Object status/supersededBy/supersededAt in ONE statement), dated spine
# (aboutObject Points ∪ Event-aboutObject edges ∪ product-lane eventId
# join), evidence view (points with validity + EP). Per-lane date ladder
# when → createdAt (sentinel-stripped) → eventId-joined startedAt (R2);
# parse-fail FALLS THROUGH the ladder (never undated while a usable date
# exists); undated is reserved for rows with NO usable date. As-of windows
# exclude rows dated after question_date (equality-day inclusive, UTC date
# truncation via the shared _norm_date helper).
# ══════════════════════════════════════════════════════════════════════════

# the v2-lane undated sentinel (tools/longmem_eval/ingest.UNDATED_SENTINEL —
# stable value, mirrored here so the pure module never imports tools)
_UNDATED_SENTINEL = "1970-01-01T00:00:00Z"
_TIER_ORDER = {"when": 0, "created": 1, "started": 2, "undated": 3}


@dataclass(frozen=True)
class AssemblySlices:
    """The walker's typed output — consumed by the Task-5 renderer."""

    state_rows: tuple = ()
    timeline_rows: tuple = ()
    evidence_rows: tuple = ()
    admission: dict = field(default_factory=dict)


class WalkerPort(Protocol):
    """Graph seam for the walker (dict-stubbed in unit tests; the docker
    adapter wraps a live projection)."""

    def state_rows(self, object_ids: list[str]) -> list[dict]: ...

    def spine_rows(self, object_ids: list[str],
                   per_subject_cap: int) -> list[dict]: ...


def _norm_date(value) -> _date | None:
    """Shared date normalization — the SINGLE source for the as-of equality
    boundary AND the Task-5 ordering/diff arithmetic: parse (ISO datetime
    with Z, or YYYY-MM-DD) → sentinel-strip → UTC DATE truncation
    (date-only semantics: 2026-06-10T23:30:00Z → 2026-06-10). Returns None
    for absent/garbage/sentinel values — never raises."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        if "T" in s or " " in s:
            dt = s.replace("Z", "+00:00").replace(" ", "T")
            dt = _datetime.fromisoformat(dt)
            if dt.tzinfo is not None:
                # aware instants truncate in UTC — NEVER the machine-local
                # zone (a host-dependent day-boundary would scramble the
                # as-of equality boundary and Task-5 byte-goldens)
                dt = dt.astimezone(_UTC)
            d = dt.date()
        else:
            d = _date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        return None
    # sentinel-strip: the v2-lane undated marker (and any pre-1970 epoch
    # artifact) is NOT a usable date
    if d <= _date(1970, 1, 1):
        return None
    return d


def _tier_and_date(row: dict) -> tuple[str, _date | None]:
    """R2 ladder for ONE spine row: when → createdAt → startedAt, each
    parse-gated; a malformed value FALLS THROUGH to the next tier (never
    undated while a usable date exists). Returns (tier, normalized date)."""
    for key, tier in (("when", "when"), ("created_at", "created"),
                      ("started_at", "started")):
        d = _norm_date(row.get(key))
        if d is not None:
            return tier, d
    return "undated", None


def collect_slices(port: WalkerPort, candidates: list[SubjectCandidate], *,
                   shape: AssemblyShape | None,
                   question_date=None,
                   per_subject_cap: int = 200) -> AssemblySlices:
    """One batched typed walk over the resolved subjects → typed slices.

    State rows come back in a SINGLE port.state_rows call (one statement —
    no torn supersession header under a concurrent #2242 fold); spine rows
    in one port.spine_rows call. Each spine row is date-tiered by the R2
    ladder and ordered: dated ascending (deterministic tiebreak: subject
    object_id, then row id), undated LAST. ``question_date`` (optional) is
    the as-of window: rows dated AFTER it are excluded (equality-day
    inclusive after UTC truncation); undated rows are always retained
    undated-last. Never raises on malformed stored dates (they fall through
    the ladder).
    """
    if not candidates:
        return AssemblySlices()
    object_ids = [c.object_id for c in candidates]
    state_rows = list(port.state_rows(object_ids) or [])
    spine_raw = list(port.spine_rows(object_ids, per_subject_cap) or [])

    timeline: list[dict] = []
    per_subject_counts: dict[str, int] = {}
    for row in spine_raw:
        oid = str(row.get("object_id") or "")
        # per-subject cap enforced post-fetch (single batched query)
        per_subject_counts[oid] = per_subject_counts.get(oid, 0) + 1
        if per_subject_counts[oid] > per_subject_cap:
            continue
        row = dict(row)
        tier, d = _tier_and_date(row)
        row["tier"] = tier
        row["date"] = d
        timeline.append(row)

    # as-of window: exclude rows dated AFTER question_date (undated kept)
    if question_date is not None:
        dq = _norm_date(question_date)
        if dq is not None:
            timeline = [r for r in timeline
                        if r["date"] is None or r["date"] <= dq]

    # CHRONOLOGICAL spine: date primary (undated -> _date.max -> naturally
    # LAST), deterministic tiebreak object_id then id (R17 P3-6)
    timeline.sort(key=lambda r: (r["date"] or _date.max,
                                 r.get("object_id", ""), r.get("id", "")))
    # evidence view = the point rows (validity + EP carried on the row)
    evidence_rows = [r for r in timeline if r.get("kind") == "point"]
    rows_requested = per_subject_cap * len(object_ids)
    truncated = any(per_subject_counts.get(oid, 0) >= per_subject_cap
                    for oid in object_ids)
    return AssemblySlices(
        state_rows=tuple(state_rows),
        timeline_rows=tuple(timeline),
        evidence_rows=tuple(evidence_rows),
        admission={"rows_requested": rows_requested,
                   "rows_admitted": len(timeline),
                   "truncated": truncated})


def docker_walker_port(sdk) -> WalkerPort:
    """Adapter over a live TortoiseSDK: the state read is ONE batched
    Cypher statement (status/supersededBy/supersededAt together — no torn
    header under a concurrent fold); the spine is ONE batched points+events
    walk per subject set (never N+1)."""
    proj = sdk._get_proj()

    def state_rows(object_ids: list[str]) -> list[dict]:
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.id IN $ids "
            "RETURN o.id, o.name, o.status, o.supersededBy, o.supersededAt",
            params={"ids": object_ids}).result_set
        return [{"object_id": r[0], "name": r[1], "status": r[2],
                 "superseded_by": r[3], "superseded_at": r[4]} for r in rows]

    def spine_rows(object_ids: list[str],
                   per_subject_cap: int = 200) -> list[dict]:
        # Deterministic + PER-SUBJECT-FAIR: one ORDER BY id LIMIT query per
        # subject per kind (bounded: 2 kinds x len(subjects) — never per-row
        # N+1). A shared LIMIT over the subject set would let a hub starve a
        # co-subject and make the surviving rows engine-order-dependent.
        if not object_ids:
            return []
        out: list[dict] = []
        for oid in object_ids:
            prow = proj.g.query(
                "MATCH (o:Object {id:$oid})<-[:aboutObject]-(p:Point) "
                "RETURN 'point' AS kind, p.id, p.content, p.when, "
                "p.createdAt, p.status, p.validFrom, p.validTo, "
                "p.expiredAt, p.ep_alpha, p.ep_beta, p.quote, "
                "p.search_keys, p.eventId, p.lme_session_index "
                "ORDER BY p.id LIMIT $cap",
                params={"oid": oid, "cap": per_subject_cap}).result_set
            for r in prow:
                # validTo/expiredAt mirror the FTS-hit shape (validity-window
                # marker parity — P1-2: [valid X -> Y] vs a misleading
                # open-ended [valid since X] on the fired render)
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "when": r[3],
                            "created_at": r[4], "status": r[5],
                            "valid_from": r[6], "valid_to": r[7],
                            "expired_at": r[8], "ep_alpha": r[9],
                            "ep_beta": r[10], "quote": r[11],
                            "search_keys": r[12], "event_id": r[13],
                            "lme_session_index": r[14]})
            erow = proj.g.query(
                "MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event) "
                "RETURN 'event' AS kind, e.eventId, e.name, e.startedAt, "
                "e.status, e.lme_event_id, e.lme_session_index "
                "ORDER BY e.eventId LIMIT $cap",
                params={"oid": oid, "cap": per_subject_cap}).result_set
            for r in erow:
                # the Event node stores its human text under `name`
                # (create_event's first arg) — NOT `content`
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "started_at": r[3],
                            "status": r[4], "lme_event_id": r[5],
                            "lme_session_index": r[6]})
        return out

    import types
    return types.SimpleNamespace(state_rows=state_rows,
                                 spine_rows=spine_rows)


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 5 — renderer: synthesized hits + deterministic ordering/diff
# (R1/R2/R3-5/R6/R12, R17 P2-1). Slices -> ordinary annotated hit dicts the
# UNCHANGED assemble_context/_render_block render (Task 6's byte-parity
# invariant). CONTRACT (pinned here — Task 6 captures the byte-goldens):
#   * state/section labels EMBED in hit content ("STATE (couch): superseded
#     by sofa on 2026-09-01" / "STATE (bike #obj-b): live"); synthesized
#     rows carry NO `id` (why.enrich skips them) and lme_session_index-less
#     -> _render_block prefixes "[session ?]".
#   * superseded_by is DICT-shaped ({"content_snippet": name-or-snippet});
#     empty supersededBy -> {"content_snippet": ""} (the fired path passes
#     [] to the D8 gate at Task 6 — never flips retrieval_degraded).
#   * successor-absent (verified-empty) and torn rows render NAME-ONLY
#     annotations — a successor is never fabricated into a date/evidence
#     line; >200-char names truncate.
#   * real rows pass through unchanged minus the pure walker derivation
#     keys {date, tier} (no point_id — W4-OUTPUT-only).
#   * per-subject sectioning (R12/C7): subject-major line blocks in
#     candidate order, rows chronological within a section; a point
#     anchored to two subjects renders once PER section (not globally).
#   * ordering/diff arithmetic uses the SAME _norm_date helper as the as-of
#     boundary (second-model P2-3) — one date source everywhere.
# ══════════════════════════════════════════════════════════════════════════

_MAX_SUCC_NAME = 200


def _fmt_date(d: _date) -> str:
    return d.isoformat() if d is not None else ""


def _as_str(v) -> str:
    """Never-raise string coercion: a non-str value on a hand-built slice
    (AssemblySlices is a public pure type) must not crash the renderer nor
    leak ``b'...'`` reprs into the reader text."""
    if isinstance(v, str):
        return v
    return "" if v is None else str(v)


def _subject_names(slices: AssemblySlices) -> dict[str, str]:
    """object_id -> display name from the state slice."""
    names: dict[str, str] = {}
    for sr in slices.state_rows:
        oid = sr.get("object_id")
        if oid and not names.get(oid):
            names[oid] = _as_str(sr.get("name")).strip() or "(unnamed)"
    return names


def _display_label(name: str, oid: str, colliding: bool) -> str:
    """Section/state label: bare name, or name + '#<oid>' when two
    same-named entities share this assembly (R12/C7 disambiguation)."""
    return f"{name} #{oid}" if colliding else name


def _oid_rows(slices: AssemblySlices, oid: str) -> list[dict]:
    return [r for r in slices.timeline_rows
            if r.get("object_id") == oid]


def _subject_order(slices: AssemblySlices,
                   candidates: tuple | list) -> list[tuple[str, str]]:
    """Ordered (object_id, name) list: candidate order when supplied (else
    state-row order, then spine-only oids)."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    names = _subject_names(slices)
    for c in candidates:
        oid = c.object_id
        if oid in seen:
            continue
        seen.add(oid)
        if _oid_rows(slices, oid) or oid in names:
            out.append((oid, (c.name or "").strip() or names.get(oid,
                        "(unnamed)")))
    for sr in slices.state_rows:
        oid = sr.get("object_id")
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, names.get(oid, "(unnamed)")))
    for r in slices.timeline_rows:
        oid = r.get("object_id")
        if oid and oid not in seen:
            seen.add(oid)
            out.append((oid, names.get(oid, "(unnamed)")))
    return out


def _dated_instances(rows: list[dict]) -> list[_date]:
    """Per-ROW normalized dates over the rows, read from the walker's OWN
    tier date (the R2 ladder already ran inside collect_slices — when ->
    createdAt -> startedAt with parse FALL-THROUGH, UTC truncation). NEVER
    re-derive here: an or-chain would silently drop a row whose truthy but
    unparseable `when` shadows a valid createdAt (P1: silent wrong math)."""
    out: list[_date] = []
    for r in rows:
        d = r.get("date")
        if isinstance(d, _date):
            out.append(d)
    return out


def _date_anchors(rows: list[dict]) -> tuple[_date | None, _date | None]:
    """(earliest, latest) dated row dates — identical date semantics to the
    walker's tier sort (one date source everywhere)."""
    ds = _dated_instances(rows)
    return (min(ds), max(ds)) if ds else (None, None)


def _state_header_hit(sr: dict, label: str,
                      successors_verified: frozenset[str],
                      ) -> dict:
    status = _as_str(sr.get("status")).strip()
    succ = _as_str(sr.get("superseded_by")).strip()
    if len(succ) > _MAX_SUCC_NAME:
        succ = succ[: _MAX_SUCC_NAME] + "…"
    date = _norm_date(sr.get("superseded_at"))
    if status == "superseded":
        if not succ:
            text = f"STATE ({label}): superseded (successor unknown)"
            sb = {"content_snippet": ""}
        elif succ in successors_verified:
            on = f" on {_fmt_date(date)}" if date else ""
            text = f"STATE ({label}): superseded by {succ}{on}"
            sb = {"content_snippet": succ}
        else:
            # name-only annotation — the successor resolved to zero visible
            # nodes (never created / recall-excluded / the fired path's
            # probe found nothing); NEVER fabricate a date/evidence line
            text = (f"STATE ({label}): superseded by {succ} — "
                    f"no successor record found")
            sb = {"content_snippet": ""}
    else:
        text = f"STATE ({label}): {status or 'live'}"
        sb = {"content_snippet": ""}
    return {"content": text, "kind": "state", "status": status,
            "superseded_by": sb}


def synthesize_hits(
    slices: AssemblySlices, *, shape: AssemblyShape,
    candidates: tuple | list = (), halves: tuple | list = (),
    successors_verified: frozenset[str] = frozenset(),
) -> list[dict]:
    """Render slices -> annotated hit dicts (the assemble_context input).

    Deterministic: subject-major sections in candidate order, rows
    chronological within a section; state/ordering/interval lines computed
    through the SHARED _norm_date helper (never an independent date lib).
    """
    cands = list(candidates)
    order = _subject_order(slices, cands)
    # colliding names -> disambiguate every label for those names
    by_name_rows: dict[str, int] = {}
    for _oid, name in order:
        by_name_rows[name] = by_name_rows.get(name, 0) + 1
    collide: set[str] = {n for n, k in by_name_rows.items() if k > 1}

    state_by_oid = {sr.get("object_id"): sr for sr in slices.state_rows}
    hits: list[dict] = []
    if shape is AssemblyShape.INTERVAL:
        # span over DISTINCT dated row instances across the pair — a window
        # with < 2 distinct dates is degenerate and must NOT fabricate a
        # "0 days" line (sparse `when` is the fixture's primary design)
        spans = sorted(_dated_instances(list(slices.timeline_rows)))
        if len(spans) >= 2 and spans[-1] > spans[0]:
            days = (spans[-1] - spans[0]).days
            halves_l = [h for h in halves if h and _as_str(h).strip()]
            if len(halves_l) >= 2:
                phrase = (f"{days} days between {_as_str(halves_l[0]).strip()} "
                          f"and {_as_str(halves_l[1]).strip()}")
            elif len(order) >= 2:
                l0 = _display_label(order[0][1], order[0][0],
                                    order[0][1] in collide)
                l1 = _display_label(order[1][1], order[1][0],
                                    order[1][1] in collide)
                phrase = f"{days} days between {l0} and {l1}"
            else:
                phrase = f"{days} days between the events described"
            hits.append({"content": phrase, "kind": "interval"})
    elif shape is AssemblyShape.ORDERING:
        dated: list[tuple[str, str, _date, int]] = []
        for i, (oid, name) in enumerate(order):
            lo, _hi = _date_anchors(_oid_rows(slices, oid))
            if lo is not None:
                dated.append((oid, name, lo, i))
        dated.sort(key=lambda t: (t[2], t[3]))
        if len(dated) >= 2:
            # (earliest-date, subject_index) — a same-day tie is decided by
            # the candidate/transcript order, deterministically
            oid_a, name_a, da, _i = dated[0]
            label = _display_label(name_a, oid_a, name_a in collide)
            hits.append({"content": f"{label} came first on "
                                   f"{_fmt_date(da)}",
                         "kind": "ordering"})
    elif shape is AssemblyShape.CURRENT_STATE:
        # headers follow the SAME candidate-ordered subject sequence as the
        # sections below — never the raw state_rows order (the docker state
        # query has no ORDER BY; P2-1 determinism)
        for oid, name in order:
            sr = state_by_oid.get(oid)
            if sr is None:
                continue
            label = _display_label(name, oid, name in collide)
            hits.append(_state_header_hit(sr, label,
                                          successors_verified))

    # per-subject sections (subject-major). Rows are re-sorted internally on
    # (date, id) so synthesize_hits output is INDEPENDENT of the input row
    # order — the chronological-within-a-section contract never depends on
    # the caller having pre-sorted (matched-control order-shuffle safe).
    for oid, _name in order:
        section_rows = _oid_rows(slices, oid)
        section_rows.sort(key=lambda r: (r.get("date") or _date.max,
                                         _as_str(r.get("id"))))
        seen: set[str] = set()
        for r in section_rows:
            rid = r.get("id")
            if rid is not None:
                if rid in seen:
                    continue  # cross-subject anchor: once PER section
                seen.add(rid)
            cleaned = {k: v for k, v in r.items()
                       if k not in ("date", "tier")}
            hits.append(cleaned)
    return hits


# ══════════════════════════════════════════════════════════════════════════
# #2165 Task 6 — _assemble_connected (R5/R6/R11/R14/R17): the product seam.
# One single-source fired path shared by ask()'s pre-retrieval branch and
# the public sdk.ask_assembled(). R14 drift pin: this function is imported
# ONLY by sdk.ask()'s branch and sdk.ask_assembled (a source-text test
# enforces it). The fired envelope wraps ONLY the content stages
# (classify->resolve->walk->render->decorate->enrich->assemble); the ONE
# reader call stays under the shared ask()/ask_assembled reader envelope.
# ══════════════════════════════════════════════════════════════════════════

# Object recall-excluded statuses (the successor-EXISTENCE probe treats an
# excluded successor as invisible -> the renderer's NAME-ONLY annotation).
_RECALL_OBJECT_EXCLUDED_STATUSES = frozenset(
    {"superseded", "deprecated", "archived", "retracted"})


@dataclass(frozen=True)
class _AssembledBlock:
    """Internal fired block (content stages only — NO reader, NO answer).
    Ask() and ask_assembled() both consume this and add their own envelope
    (shared reader machinery, metering, response shape)."""
    fired: bool
    shape: str | None
    subjects: tuple
    slices: dict
    admission: dict
    post_cap_lines: list
    # NOTE: evidence/context_tokens are NOT computed here — ask()/the
    # assembled path render post_cap_lines through the SHARED
    # render_context/estimate path so the alignment invariant
    # (context_tokens == estimate_tokens(evidence)) holds by construction.


@dataclass
class AssemblyAnswer:
    """Public ask_assembled() return shape (pinned — Task 7's eval arm reads
    post_cap_lines for gold-id admission and answer for conversion; field
    names are the arm's contract)."""
    fired: bool
    shape: str | None
    question_type: str
    subjects: list
    slices: dict
    post_cap_lines: list
    admission: dict
    evidence: str
    context_tokens: int
    answer: str | None
    retrieval_degraded: bool


def _probe_visible_successors(sdk, slices: AssemblySlices) -> frozenset[str]:
    """Successor-EXISTENCE probe (docker): each distinct non-empty
    superseded_by name in the state slice -> verified ONLY when >= 1 Object
    with that name exists AND is not recall-excluded. The renderer turns an
    unverified name into a NAME-ONLY annotation (never a fabricated link)."""
    names = {(_as_str(sr.get("superseded_by")) or "").strip()
             for sr in slices.state_rows}
    names.discard("")
    if not names:
        return frozenset()
    try:
        proj = sdk._get_proj()
        # ONE batch query: name -> set of visible (non-excluded) statuses
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.name IN $names "
            "RETURN o.name AS nm, o.status",
            params={"names": sorted(names)}).result_set
    except Exception:  # noqa: BLE001, RUF100 — probe fails open to empty
        rows = []
    statuses_by_name: dict[str, set] = {}
    for nm, st in rows:
        statuses_by_name.setdefault(nm, set()).add(st or "")
    verified = {nm for nm in names
                if statuses_by_name.get(nm)
                and not (statuses_by_name[nm]
                         & _RECALL_OBJECT_EXCLUDED_STATUSES)}
    return frozenset(verified)


def _assemble_connected(sdk, question: str, *, question_date: str | None = None,
                        caps: dict | None = None) -> _AssembledBlock:
    """The fired content pipeline (env-gated at the CALLER — ask() reads
    TORTOISE_ASK_CONNECTED_ASSEMBLY BEFORE calling; this function assumes
    the gate already passed and fires when the question routes).

    classify -> resolve (both-halves gate R1) -> walk (typed slices) ->
    render (synthesize_hits) -> decorate real rows (annotate_ask_hits —
    synthesized rows have no id and are untouched) -> explicit
    why.enrich_items when W4 is on (R5/R17; id-less rows skipped by the
    guard) -> assemble_context(caps). NEVER raises untyped: the ask()
    envelope maps any raise to AskRetrievalUnavailable.
    """
    from tortoise.retrieval import assemble_context
    if caps is None:
        from tortoise.retrieval import resolve_ask_retrieval_caps
        caps = resolve_ask_retrieval_caps()

    shape = classify_question(question)
    if shape is None:
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    terms = extract_subject_terms(question, shape)
    if not terms:
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    resolved = resolve_subjects(docker_resolver_port(sdk), terms, shape=shape)
    if not resolved.both_halves_ok(shape):
        # one half failed to resolve (or a both-subject shape lacks one) ->
        # the R1 gate: fire NOTHING, let legacy fall through
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    candidates = list(resolved.candidates)
    slices = collect_slices(
        docker_walker_port(sdk), candidates, shape=shape,
        question_date=question_date,
        per_subject_cap=caps.get("limit") or 200)
    verified = _probe_visible_successors(sdk, slices)
    hits = synthesize_hits(slices, shape=shape, candidates=candidates,
                           halves=terms, successors_verified=verified)
    # decorate REAL rows (id-keyed additive session/speaker join); the
    # synthesized no-id state/section lines ride through untouched
    import contextlib as _contextlib
    with _contextlib.suppress(Exception):
        hits = sdk.annotate_ask_hits(hits)
    # D8 claim-level marker parity (P1-2): the SAME decoration source the
    # FTS path uses attaches superseded_by/supersedes to real POINT rows
    # (id-keyed additive; synthesized no-id rows untouched). Fail-open.
    with _contextlib.suppress(Exception):
        from tortoise.search_engine import fetch_point_epistemic_state
        ids = [h.get("id") for h in hits if h.get("id")]
        state = fetch_point_epistemic_state(sdk._get_proj().g, ids) or {}
        out = []
        for h in hits:
            pid = h.get("id")
            st = state.get(str(pid))
            if st is None:
                out.append(h)
                continue
            e = dict(h)
            for k in ("status", "superseded_by", "supersedes"):
                if k in st and st[k] is not None:
                    e[k] = st[k]
            out.append(e)
        hits = out
    # W4 enrich real rows when the flag is on (id-less rows skipped by the
    # enrich_items guard). Fail-open degrade: block WITHOUT why keys.
    with _contextlib.suppress(Exception):
        from tortoise.why import enrich_items, w4_enrichment_enabled
        if w4_enrichment_enabled():
            hits = enrich_items(sdk._get_proj(), hits)
    selected = assemble_context(
        hits, top_k=caps.get("context_item_cap", 40),
        max_context_tokens=caps.get("context_token_cap", 8000),
        question_date=question_date,
        context_item_cap=caps.get("context_item_cap", 40),
        byte_cap=32768)
    if not selected:
        # P1-1: both halves resolved but the assembly has NOTHING to say
        # (content-less subjects) — firing would replace legacy evidence
        # with an empty block and burn the ONE reader call on nothing.
        # Fall through to legacy (the R1 spirit: fire only when the fired
        # block is a REAL replacement).
        return _AssembledBlock(fired=False, shape=None, subjects=(),
                               slices={}, admission={}, post_cap_lines=[])
    subs = [{"object_id": c.object_id, "name": c.name,
             "confidence": c.confidence}
            for c in candidates]
    return _AssembledBlock(
        fired=True, shape=shape.value, subjects=tuple(subs),
        slices={"state_rows": list(slices.state_rows),
                "timeline_rows": list(slices.timeline_rows),
                "evidence_rows": list(slices.evidence_rows)},
        admission=dict(slices.admission), post_cap_lines=selected)
