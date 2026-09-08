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
                "p.createdAt, p.status, p.validFrom, p.ep_alpha, p.ep_beta, "
                "p.quote, p.search_keys, p.eventId, p.lme_session_index "
                "ORDER BY p.id LIMIT $cap",
                params={"oid": oid, "cap": per_subject_cap}).result_set
            for r in prow:
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "when": r[3],
                            "created_at": r[4], "status": r[5],
                            "valid_from": r[6], "ep_alpha": r[7],
                            "ep_beta": r[8], "quote": r[9],
                            "search_keys": r[10], "event_id": r[11],
                            "lme_session_index": r[12]})
            erow = proj.g.query(
                "MATCH (o:Object {id:$oid})<-[:aboutObject]-(e:Event) "
                "RETURN 'event' AS kind, e.eventId, e.content, e.startedAt, "
                "e.status, e.lme_event_id, e.lme_session_index "
                "ORDER BY e.eventId LIMIT $cap",
                params={"oid": oid, "cap": per_subject_cap}).result_set
            for r in erow:
                out.append({"object_id": oid, "kind": r[0], "id": r[1],
                            "content": r[2], "started_at": r[3],
                            "status": r[4], "lme_event_id": r[5],
                            "lme_session_index": r[6]})
        return out

    import types
    return types.SimpleNamespace(state_rows=state_rows,
                                 spine_rows=spine_rows)
