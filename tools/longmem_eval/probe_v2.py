"""#2578 (Task 5) — v2-lane structural probe (staged, 55-Q subset).

The ONE question this module answers:

    Does the v2 ingest lane actually PRODUCE, for the gold turns of the
    deterministic-fireable temporal questions, the graph structure the
    assembler lane (#2165) needs to read?

Concretely, for each selected question it measures the graph the v2 lane
wrote and reports, HONESTLY (``None``/``0`` when absent — never assumed
from ingest stats):

    (a) ``Object`` nodes for the entities in the answer sessions
        (``objects_total``),
    (b) ``Point``-[:aboutObject]->``Object`` edges
        (``point_about_object_edges``),
    (c) ``Event``-[:aboutObject]->``Object`` edges post-R8
        (``event_about_object_edges``),
    (d) a dated ``startedAt`` per gold session
        (``dated_started_at`` / ``gold_sessions_with_events``), and
    (e) an entity name-match rate for the question's gold subject
        (``entity_name_match``).

Saturation stop rule (pre-registered, plan Task 5): ``substrate_present``
is a BINARY (does the v2 lane produce the read structure at all, or not)
and therefore SATURATES. Wave 1 runs a deterministic stratified sample of
``PROBE_WAVE_SIZE`` questions spanning the three ``ANALYSIS_CLASSES``; if
wave 1 is UNANIMOUS on ``substrate_present`` the binary has saturated and
the full-55 wave 2 is NOT run. Wave 2 (the full pinned 55) runs ONLY if
wave 1 shows variance. ``probe_guard`` refuses a non-55 subset at any wave
and refuses wave 2 before wave 1 has completed.

Lane independence (same boundary as measure_temporal.py): this module only
READS the graph and PLANS a wave — it never ingests, never calls an LLM,
and never builds the assembler. Live wave execution is driven by the #2578
runbook against a per-question v2 graph; ``main`` without ``--dry-run``
refuses rather than touching the network/DB.
"""
from __future__ import annotations

import argparse
import re
import sys
from typing import Any

from .measure_temporal import (
    ANALYSIS_CLASSES,
    CENSUS_DEFAULT,
    deterministic_subset,
    load_census,
)

#: Wave-1 sample size (plan Task 5: "wave-1 sample of ~15 questions").
PROBE_WAVE_SIZE: int = 15

#: The pinned analysis subset (measure_temporal.deterministic_subset): 34
#: ordering/compare + 19 interval + 2 current-state = 55. Any other count is
#: refused — a full-133 census probe is out of scope by construction.
PINNED_SUBSET_SIZE: int = 55

#: Stratified wave-1 allocation (sums to PROBE_WAVE_SIZE). The 2-row
#: ``current-state`` class is allocated BOTH of its rows (a single row would
#: carry no within-class signal); the remainder is spread over the larger
#: classes. Deterministic — no RNG anywhere in selection.
PROBE_WAVE_ALLOCATION: dict[str, int] = {
    "ordering/compare": 9,
    "interval": 4,
    "current-state": 2,
}


# ══════════════════════════════════════════════════════════════════════════
# Wave planning + the pre-registered guards
# ══════════════════════════════════════════════════════════════════════════

def _even_pick(items: list[dict], k: int) -> list[dict]:
    """Deterministically pick ``k`` items spread evenly across ``items``.

    Index rule ``(i * n) // k`` for ``i in range(k)`` — monotonic, distinct
    (``n >= k``), and RNG-free, so two calls on the same census order return
    the identical sample. ``k >= n`` returns every item.
    """
    n = len(items)
    if k <= 0:
        return []
    if k >= n:
        return list(items)
    return [items[(i * n) // k] for i in range(k)]


def wave_questions(rows: list[dict]) -> list[dict]:
    """The wave-1 stratified sample: ``PROBE_WAVE_SIZE`` pinned questions
    spanning the three ``ANALYSIS_CLASSES``.

    ``rows`` MUST be the pinned 55-Q analysis subset
    (``deterministic_subset(census_rows)``); anything else — the 133-row
    census included — raises ``ValueError`` naming the count received. The
    selection is deterministic (no RNG, stable census ordering) and ordered
    by ``ANALYSIS_CLASSES`` then census position.
    """
    subset = deterministic_subset(rows)
    if len(rows) != PINNED_SUBSET_SIZE or len(subset) != PINNED_SUBSET_SIZE:
        raise ValueError(
            f"wave_questions requires the pinned {PINNED_SUBSET_SIZE}-Q "
            f"analysis subset (deterministic_subset of the census); got "
            f"{len(rows)} rows, {len(subset)} of them in "
            f"{sorted(ANALYSIS_CLASSES)}. A full-133 probe is refused "
            "(plan Task 5: 'a full-133 probe run is refused').")
    if sum(PROBE_WAVE_ALLOCATION.values()) != PROBE_WAVE_SIZE:
        raise ValueError(
            "wave allocation does not sum to PROBE_WAVE_SIZE: "
            f"{PROBE_WAVE_ALLOCATION}")
    selected: list[dict] = []
    for cls in ANALYSIS_CLASSES:
        pool = [r for r in subset if r.get("cls") == cls]
        k = PROBE_WAVE_ALLOCATION.get(cls, 0)
        if k > len(pool):
            raise ValueError(
                f"wave allocation wants {k} {cls!r} questions but the pinned "
                f"subset has only {len(pool)}.")
        selected.extend(_even_pick(pool, k))
    return selected


def probe_guard(subset_size: int, wave: int, wave1_done: bool) -> None:
    """Refuse an out-of-scope probe run BEFORE it burns any LLM ingest.

    Raises ``ValueError`` when:

    * ``subset_size != 55`` — the probe runs ONLY on the pinned analysis
      subset; a full-133 census run is refused (plan Task 5 test line).
    * ``wave > 1`` while ``wave1_done`` is False — the full-55 run is
      refused before wave 1 completes. Substrate-existence SATURATES (the
      pre-registered stop rule): wave 1's ``PROBE_WAVE_SIZE`` sample decides
      whether the binary has converged, and the full 55 runs ONLY if wave 1
      shows variance.
    """
    if int(subset_size) != PINNED_SUBSET_SIZE:
        raise ValueError(
            f"probe_guard: subset_size={subset_size} — the probe runs ONLY "
            f"on the pinned {PINNED_SUBSET_SIZE}-Q analysis subset; a "
            "full-133 run is refused. Build the subset with "
            "measure_temporal.deterministic_subset(census['rows']).")
    if int(wave) not in (1, 2):
        raise ValueError(f"probe_guard: wave={wave} — only waves 1 and 2 exist.")
    if int(wave) > 1 and not wave1_done:
        raise ValueError(
            f"probe_guard: wave={wave} requested but wave 1 has not "
            "completed (wave1_done=False) — the full-55 run is refused "
            "before wave 1 decides saturation (pre-registered stop rule).")


# ══════════════════════════════════════════════════════════════════════════
# Graph reads (the conventional eval path: sdk._get_proj().g.query)
# ══════════════════════════════════════════════════════════════════════════

#: Gold-turn entity candidates: capitalized word runs ("Museum of Modern
#: Art"), trimmed of leading/trailing stopwords. Deliberately SIMPLE — see
#: ``derive_subject_candidates`` for the stated limitations.
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'-]*")
_CAP_RE = re.compile(r"^[A-Z]")
#: Lowercase connectors allowed INSIDE a capitalized run ("Museum of Modern
#: Art") when both neighbours are capitalized.
_CONNECTORS: frozenset[str] = frozenset({
    "of", "the", "de", "del", "van", "von", "and", "for", "at", "in", "on",
    "du", "la", "le", "da", "di", "el",
})
#: Function words, pronouns, interjections, weekdays/months and high-frequency
#: generic nouns — never entity subjects by themselves. Deliberately
#: conservative; the limitation is documented on the derivation function.
_STOPWORDS: frozenset[str] = frozenset({
    "a", "about", "actually", "after", "again", "all", "almost", "also",
    "always", "am", "an", "and", "another", "any", "anyway", "april", "are",
    "around", "as", "at", "august",
    "back", "be", "because", "been", "before", "being", "best", "better",
    "between", "both", "but", "by",
    "can", "could", "couldn",
    "day", "december", "did", "didn", "do", "does", "doesn", "doing", "don",
    "down", "during",
    "each", "either", "else", "even", "ever", "every",
    "february", "few", "for", "friday", "from",
    "get", "getting", "go", "going", "good", "got", "great",
    "had", "has", "have", "having", "he", "hello", "her", "here", "hers",
    "hi", "him", "his", "how", "however",
    "i", "if", "in", "into", "is", "isn", "it", "its",
    "january", "july", "june", "just",
    "know",
    "last", "later", "let", "like", "little", "look", "looking",
    "made", "make", "many", "march", "may", "maybe", "me", "might", "monday",
    "month", "more", "most", "much", "must", "my",
    "need", "new", "next", "no", "not", "now", "november",
    "october", "of", "off", "oh", "ok", "okay", "on", "once", "one", "only",
    "or", "other", "our", "out", "over", "own",
    "please", "pretty", "probably", "put",
    "quite",
    "really", "right",
    "said", "same", "saturday", "saw", "say", "see", "september", "she",
    "should", "since", "so", "some", "something", "still", "such", "sunday",
    "sure",
    "take", "than", "thank", "thanks", "that", "the", "their", "them",
    "then", "there", "these", "they", "thing", "think", "this", "those",
    "though", "thought", "through", "thursday", "time", "to", "today",
    "together", "tomorrow", "tonight", "too", "took", "tuesday", "two",
    "under", "until", "up", "us", "use", "used",
    "very",
    "wait", "want", "was", "wasn", "way", "we", "wednesday", "week", "well",
    "went", "were", "what", "when", "where", "which", "while", "who", "why",
    "will", "with", "without", "would",
    "yeah", "yes", "yesterday", "yet", "you", "your", "yours",
})


def _cypher(sdk: Any, query: str, params: dict | None = None) -> list:
    """Run ONE read-only Cypher query via the conventional eval graph path.

    ``sdk._get_proj().g.query(query, params=...).result_set`` is the driver
    every other ``tools/longmem_eval`` module uses (ingest.py, retrieve.py,
    run.py) — reused verbatim so the probe reads exactly the graph the v2
    ingest wrote, with no second client and no new dependency. Returns a
    plain list of rows.
    """
    return list(sdk._get_proj().g.query(query, params=params or {}).result_set)


def _count(sdk: Any, query: str, params: dict) -> int:
    """First cell of a ``count(...)`` query as an int (0 when empty/odd)."""
    rows = _cypher(sdk, query, params)
    if not rows:
        return 0
    try:
        return int(rows[0][0] or 0)
    except (TypeError, ValueError, IndexError):
        return 0


def _capitalized_runs(text: str) -> list[list[str]]:
    """Capitalized word runs in ``text``, connectors allowed inside a run."""
    tokens = _WORD_RE.findall(text)
    runs: list[list[str]] = []
    i = 0
    n = len(tokens)
    while i < n:
        if not _CAP_RE.match(tokens[i]):
            i += 1
            continue
        run = [tokens[i]]
        j = i + 1
        while j < n:
            if _CAP_RE.match(tokens[j]):
                run.append(tokens[j])
                j += 1
            elif (tokens[j].casefold() in _CONNECTORS and j + 1 < n
                  and _CAP_RE.match(tokens[j + 1])):
                run.extend([tokens[j], tokens[j + 1]])
                j += 2
            else:
                break
        runs.append(run)
        i = j
    return runs


def _trim_stopwords(run: list[str]) -> list[str]:
    while run and run[0].casefold() in _STOPWORDS:
        run = run[1:]
    while run and run[-1].casefold() in _STOPWORDS:
        run = run[:-1]
    return run


def derive_subject_candidates(turns: list[str]) -> list[str]:
    """Candidate gold-subject names from the answer-session turn texts.

    SIMPLE, documented derivation (there is no entity resolver on the eval
    side, so the probe CANNOT know the true gold subject — this is a proxy,
    stated as such): take capitalized word runs, trim leading/trailing
    stopwords, deduplicate case-insensitively, return sorted for determinism.

    LIMITATIONS (honest reporting, per plan Task 5: "honest resolution
    reporting, not assumed"): this over-generates on sentence-initial and
    title-case words and under-generates on lowercase subjects (e.g. "the
    concert"); it never claims to enumerate the gold subject. It is a
    consistent, comparable proxy across questions — the reported
    ``entity_name_match.rate`` is a lower-bound-shaped signal, not a
    resolution rate.
    """
    seen: dict[str, str] = {}
    for text in turns:
        for run in _capitalized_runs(str(text or "")):
            trimmed = _trim_stopwords(run)
            if not trimmed:
                continue
            name = " ".join(trimmed)
            seen.setdefault(name.casefold(), name)
    return [seen[key] for key in sorted(seen)]


def _answer_session_turn_texts(question: dict,
                               gold_session_ids: list[str]) -> list[str]:
    """Turn contents of the answer sessions, matched by dataset session id."""
    ids = [str(s) for s in (question.get("haystack_session_ids") or [])]
    sessions = question.get("haystack_sessions") or []
    golds = set(gold_session_ids)
    texts: list[str] = []
    for i, sid in enumerate(ids):
        if sid in golds and i < len(sessions):
            for turn in sessions[i] or []:
                texts.append(str(turn.get("content") or ""))
    return texts


def _count_name_matches(candidates: list[str],
                        object_names: list[str]) -> int:
    """Candidates appearing as a whole-word, case-insensitive substring of
    any created Object name (the documented match rule)."""
    names = [str(n) for n in object_names if str(n).strip()]
    matched = 0
    for cand in candidates:
        pattern = re.compile(rf"(?<!\w){re.escape(cand)}(?!\w)", re.IGNORECASE)
        if any(pattern.search(name) for name in names):
            matched += 1
    return matched


def measure_question_structure(sdk: Any, question: dict, *,
                               gold_session_ids: list[str],
                               namespace: str,
                               cls: str | None = None) -> dict:
    """Structural measurement for ONE question against an ALREADY-INGESTED
    v2 graph (no ingest, no LLM, read-only).

    Returns a plain dict; every absent signal is ``None``/``0`` — nothing is
    inferred from ingest stats. ``substrate_present`` is the binary the
    saturation stop rule is pre-registered on:

        objects_total > 0 AND point_about_object_edges > 0 AND
        gold_sessions_with_events > 0

    (the three reads the assembler lane cannot do without: entity nodes, the
    point→entity link, and at least one dated gold-session event).

    ``cls`` — the CENSUS class used for the per-class report. A raw LongMemEval
    instance carries ``question_type``, NOT the census class, so passing the
    dataset row alone silently yields an empty class and collapses every
    result into one report bucket: pass the census class explicitly (the
    caller has it from ``wave_questions``).

    ``entity_name_match`` is the documented proxy from
    :func:`derive_subject_candidates` — never a gold-subject resolution
    claim. ``namespace`` (the per-question ``team_<namespace>`` FalkorDB
    graph the numbers were read from) is echoed for provenance.
    """
    qid = str(question.get("question_id") or "")
    cls = str(cls or question.get("cls") or "")
    golds = [str(g) for g in (gold_session_ids or []) if str(g).strip()]

    obj_rows = _cypher(
        sdk,
        "MATCH (o:Object {lme_question_id:$q}) RETURN o.name",
        {"q": qid})
    object_names = [str(row[0]) for row in obj_rows
                    if row and row[0] is not None]
    objects_total = len(obj_rows)

    point_edges = _count(
        sdk,
        "MATCH (p:Point {lme_question_id:$q})-[:aboutObject]->(:Object) "
        "RETURN count(*)",
        {"q": qid})
    event_edges = _count(
        sdk,
        "MATCH (e:Event {lme_question_id:$q})-[:aboutObject]->(:Object) "
        "RETURN count(*)",
        {"q": qid})

    dated = _count(
        sdk,
        "MATCH (e:Event {lme_question_id:$q}) "
        "WHERE e.startedAt IS NOT NULL AND e.startedAt <> '' RETURN count(e)",
        {"q": qid})
    undated = _count(
        sdk,
        "MATCH (e:Event {lme_question_id:$q}) "
        "WHERE e.startedAt IS NULL OR e.startedAt = '' RETURN count(e)",
        {"q": qid})

    gold_with_events = 0
    if golds:
        rows = _cypher(
            sdk,
            "MATCH (e:Event {lme_question_id:$q}) "
            "WHERE e.sessionId IN $golds "
            "  AND e.startedAt IS NOT NULL AND e.startedAt <> '' "
            "RETURN DISTINCT e.sessionId",
            {"q": qid, "golds": golds})
        gold_with_events = len(rows)

    candidates = derive_subject_candidates(
        _answer_session_turn_texts(question, golds))
    matched = _count_name_matches(candidates, object_names)
    total = len(candidates)

    return {
        "qid": qid,
        "cls": cls,
        "namespace": str(namespace),
        "gold_sessions": len(golds),
        "gold_sessions_with_events": gold_with_events,
        "objects_total": objects_total,
        "point_about_object_edges": point_edges,
        "event_about_object_edges": event_edges,
        "dated_started_at": dated,
        "undated_events": undated,
        "entity_name_match": {
            "matched": matched,
            "total": total,
            "rate": (matched / total) if total else None,
        },
        "substrate_present": bool(
            objects_total > 0 and point_edges > 0 and gold_with_events > 0),
    }


def probe_report(results: list[dict], *, wave: int,
                 saturation: dict) -> dict:
    """Aggregate per-question results into the wave report.

    Emits per-class counts + substrate-present rates, the overall
    substrate-present rate, the aggregated name-match figures, and the
    ``saturated`` flag. ``saturated`` is the PRE-REGISTERED stop outcome:
    wave 1 saturates when every question agrees on ``substrate_present``
    (zero variance on the binary); wave 2 — the full pinned 55, the largest
    sample that exists — is saturated by construction (sampling capacity
    exhausted regardless of the split). ``saturation`` is the caller's
    echoed stop-rule context (e.g. ``{"rule": ...}``).
    """
    by_class: dict[str, dict] = {}
    present = 0
    match_matched = 0
    match_total = 0
    for r in results:
        cls = str(r.get("cls") or "")
        bucket = by_class.setdefault(cls, {
            "n": 0, "substrate_present": 0, "objects_total": 0,
            "point_about_object_edges": 0, "event_about_object_edges": 0,
            "dated_started_at": 0, "gold_sessions_with_events": 0,
            "name_match_matched": 0, "name_match_total": 0})
        bucket["n"] += 1
        is_present = bool(r.get("substrate_present"))
        bucket["substrate_present"] += int(is_present)
        present += int(is_present)
        for key in ("objects_total", "point_about_object_edges",
                    "event_about_object_edges", "dated_started_at",
                    "gold_sessions_with_events"):
            bucket[key] += int(r.get(key) or 0)
        entity_match = r.get("entity_name_match") or {}
        m = int(entity_match.get("matched") or 0)
        t = int(entity_match.get("total") or 0)
        bucket["name_match_matched"] += m
        bucket["name_match_total"] += t
        match_matched += m
        match_total += t
    for bucket in by_class.values():
        bucket["substrate_present_rate"] = (
            bucket["substrate_present"] / bucket["n"] if bucket["n"] else None)
        bucket["name_match_rate"] = (
            bucket["name_match_matched"] / bucket["name_match_total"]
            if bucket["name_match_total"] else None)
    n = len(results)
    unanimous = bool(n) and len(
        {bool(r.get("substrate_present")) for r in results}) == 1
    saturated = bool(results) and (int(wave) >= 2 or unanimous)
    return {
        "schema": "probe-v2-report/v1",
        "wave": int(wave),
        "n": n,
        "by_class": by_class,
        "substrate_present": present,
        "substrate_present_rate": (present / n) if n else None,
        "entity_name_match": {
            "matched": match_matched,
            "total": match_total,
            "rate": (match_matched / match_total) if match_total else None,
        },
        "unanimous": unanimous,
        "saturated": saturated,
        "saturation": {**dict(saturation or {}), "saturated": saturated},
    }


def main(argv: list[str] | None = None) -> int:
    """``--wave {1,2}`` planner CLI.

    Loads the census, pins the 55-Q subset, applies ``wave_questions`` +
    ``probe_guard``, and prints exactly what WOULD run (questions, per-class
    breakdown, the saturation stop rule, the dominant LLM-ingest cost) —
    all from committed JSON, with NO graph/docker/network. ``--dry-run``
    stops after the plan. Live execution is the #2578 runbook's job (it
    needs the v2 ingest lane + LLM keys), so a non-dry-run invocation
    refuses with exit code 3 rather than touching the network.
    """
    parser = argparse.ArgumentParser(
        prog="probe_v2",
        description=("v2-lane structural probe planner (#2578 Task 5) — "
                     "prints the wave that WOULD run; live execution is the "
                     "#2578 runbook driver's job."))
    parser.add_argument("--wave", type=int, choices=(1, 2), default=1,
                        help="wave 1 = stratified PROBE_WAVE_SIZE sample; "
                             "wave 2 = the full pinned 55 (requires "
                             "--wave1-done).")
    parser.add_argument("--census", default=CENSUS_DEFAULT,
                        help=f"census JSON path (default: {CENSUS_DEFAULT})")
    parser.add_argument("--wave1-done", action="store_true",
                        help="operator attestation that wave 1 completed "
                             "(required for --wave 2).")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the plan and stop (no graph, no LLM).")
    args = parser.parse_args(argv)

    census = load_census(args.census)
    subset = deterministic_subset(census["rows"])
    probe_guard(len(subset), args.wave, wave1_done=args.wave1_done)
    wave_rows = wave_questions(subset) if args.wave == 1 else list(subset)

    subset_by_class = {
        c: sum(1 for r in subset if r.get("cls") == c)
        for c in ANALYSIS_CLASSES}
    wave_by_class = {
        c: sum(1 for r in wave_rows if r.get("cls") == c)
        for c in ANALYSIS_CLASSES}
    print(f"[probe_v2] census       : {args.census} "
          f"(n={census.get('n', len(census['rows']))})")
    print(f"[probe_v2] pinned subset: {len(subset)} Q ("
          + ", ".join(f"{c}={subset_by_class[c]}" for c in ANALYSIS_CLASSES)
          + ")")
    print(f"[probe_v2] wave         : {args.wave} -> {len(wave_rows)} Q ("
          + ", ".join(f"{c}={wave_by_class[c]}" for c in ANALYSIS_CLASSES)
          + ")")
    print("[probe_v2] questions    :")
    for row in wave_rows:
        print(f"    {row.get('qid')}  {row.get('cls')}")
    print("[probe_v2] stop rule    : substrate-existence saturates — wave 1 "
          "decides; the full 55 (wave 2) runs ONLY if wave 1 shows variance.")
    print(f"[probe_v2] dominant cost: one v2 LLM-extractor ingest per session "
          f"of the {len(wave_rows)} selected questions (~2630 sessions over "
          "the full 55-Q haystacks).")
    if args.dry_run:
        return 0
    print("[probe_v2] live execution is NOT wired in this module — the wave "
          "runs against an already-ingested per-question v2 graph and needs "
          "LLM keys + docker; it is driven by the #2578 runbook. Re-run with "
          "--dry-run for the plan only.", file=sys.stderr)
    return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
