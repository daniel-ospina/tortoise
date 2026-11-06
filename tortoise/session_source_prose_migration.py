"""#11070 — the repair for the PRE-#10852 session ``:Source`` prose population.

WHAT THIS EXISTS FOR
====================
#10852 stopped the capture writing the transcript's first substantive utterance
into ``Source.summary`` (and deriving ``_searchText`` from it). It deliberately
did NOT heal the nodes that already hold the pair, and no route reaches them:

* a **re-capture** of the same ``session_id`` is the #1727 no-op replay — the
  only production caller of ``_materialize_session_source`` sits inside the
  mint gate (``if not session_existed or (retry_failed_capture and …)``);
* a **record-sourced re-projection** DECLINES the pair for an episodic Source
  (``cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS``), and a decline means
  "do not SET", so an existing value stays;
* **``record_reconcile``** — the module the tracker names — is the COMPARISON,
  not a purge. Its ``declined = episodic_source_prose_properties()`` (and the
  ``placed[declared] = placeable - declined`` branch it feeds) SUBTRACTS the
  pair from what the record is believed to place, so a prose-carrying node is
  reported under ``graph_only`` instead of as a permanent ``field_mismatch``.
  That is bookkeeping for the defect, and it must stay that: an instrument that
  repairs the thing it measures can never report a persistent divergence. It has
  no write path to the cache, and adding one would destroy the observability it
  exists for. So this module is a SEPARATE reconciler, in the shape #4125
  established (``session_source_migration``) — not a second purger bolted onto
  the comparison.

THE CENSUS — AND WHY THE TRACKER'S PREDICATE CANNOT REACH ZERO
==============================================================
#11070's indicator is

    MATCH (s:Source) WHERE s.is_episodic = true
      AND (s.summary <> '' OR s._searchText IS NOT NULL) RETURN count(s)

Two independent defects make that count structurally non-zero on a HEALTHY
graph, so a fixed graph would still read as unfixed:

1. **``is_episodic`` is not the session-Source identity.** The capture commit
   stamps it on every EXTERNAL source of ``payload.sources`` and
   ``backfill_is_episodic.py`` stamps it on every Source reachable from a
   session point, so a DOCUMENT Source carries it — and a document legitimately
   has a ``summary`` and a ``_searchText`` (the Source FTS index is declared on
   the latter). The identity is the repo's ONE declaration,
   :func:`tortoise.cache_projection.is_episodic_source_node` — two arms, never a
   flag — and it is imported here, never re-implemented.
2. **Non-empty is not prose.** A POST-#10852 node carries a topic LEXICON:
   ``summary`` is ``SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`` and
   ``_searchText`` is the same lexicon bare, so both are non-empty BY DESIGN.
   The WITNESS is the prefix (:mod:`tortoise.session_source_display`): a
   pre-#10000 row's prose carries no prefix. ``summary <> ''`` counts every
   healthy node; :func:`carries_pre_lexicon_prose` does not.

⚠️ One residual is stated rather than implied: a node whose ``summary`` IS
prefixed but whose ``_searchText`` holds something else is NOT a candidate. No
writer can produce that shape (``_source_search_text`` returns the lexicon
whenever ``topics`` is non-empty, and ``_searchText`` is written under the same
guard as ``summary``), and treating it as prose would make a re-derivation loop
out of a node the repair cannot improve.

THE DERIVATION — the RECORD's, through the WRITER's own functions
=================================================================
⛔ The narrative's authoritative copy is the record. A reconciler that reads the
prose off the node and rewrites it is a NO-OP that looks like a repair, so the
turn text is read with :func:`tortoise.record_read.session_turns` and the pair is
re-derived with the capture's OWN functions — ``sdk._session_source_metadata``
and ``sdk._source_search_text``. They are imported LAZILY, inside the call:
``sdk`` imports ``cache_projection``, so a module-level import here would be a
layering inversion the day anything on that path imports this module.

A node whose record DOES NOT ANSWER is still repaired — the pair is cleared —
and reported under ``no_record``, because §9.1/D1 says the prose does not belong
on a graph node whether or not a lexicon can replace it. The cleared shape is
the writer's own: ``summary=''`` and an ABSENT ``_searchText``, which is exactly
what a text-less capture leaves (``s._searchText=coalesce($st, s._searchText)``
with ``$st`` None). It never INVENTS a lexicon — that is the constraint, and it
is why the two arms are reported separately.

⛔ ``topics`` IS NOT PART OF THE PROSE PAIR AND IS NEVER WRITTEN — not in either
arm. The pre-fix writer proves it: it derived ``topics`` from
``Counter.most_common(6)`` — the transcript's word frequencies — while
``summary`` was the first utterance, so the two were already INDEPENDENT on this
population (which is why ``cache_projection``'s declined set never contained
``topics``). Writing it would also make ``record-reconcile`` permanently
divergent, because ``topics`` is a projected property the reconciler compares
EXACTLY — so a re-derived value would disagree with the record's own column on
exactly the nodes this repair heals. See :data:`_REPAIR_PAIR`.

REBUILD DETECTION, NOT REBUILD REPAIR
=====================================
The journal is append-only, so a pre-fix capture's prose is still in the replayed
``SourceCreated`` record: ``rebuild_all`` re-mints it. Each whole-journal replay
engine therefore runs :func:`reconcile_after_replay` at the end of its trailing
sweep — and there it is **PLAN-ONLY**, because a replay engine holds no record
connection and the clear arm is destructive and NOT self-correcting (its output
is no longer a candidate). So a replay never heals the graph; it MEASURES the
resurrected population, logs how many nodes carry the pair, and writes nothing.

⛔ Consequence, stated plainly because it is the operator's instruction: after any
whole-journal replay (``rebuild``, ``rebuild_all``, ``recover_from_log``,
``backup.restore``) the repair must be RE-RUN with ``--record-dsn``. A replay does
not restore it. The alternative — clearing at replay — would un-index every
pre-#10852 session irreversibly (the record's row cannot prove the lexicon on the
recovery door, so nothing can re-derive it there) and would then report
``census == 0``, a false PASS; that trade is refused here.

⚠️ Stated residual: the single-record ``apply()`` path does not run the hook at
all (it has no journal-wide view), so a differential replay of only a pre-fix
``SourceCreated`` re-materialises the pair with no report from this module until
the next whole-journal replay or a CLI census.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any

from tortoise.cache_projection import is_episodic_source_node
from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_READ_TIMEOUT_MS",
    "carries_pre_lexicon_prose",
    "census",
    "find_session_sources_carrying_prose",
    "reconcile_after_replay",
    "reconcile_session_source_prose",
    "record_turn_reader",
]

#: Wall-clock bound on the cache-wide Source read. Bounded, never truncated —
#: the same rule ``record_reconcile.scan_cache_view`` states: a ``LIMIT`` would
#: need its own truncation detection to stay honest, and a silently truncated
#: read UNDERCOUNTS the population this module exists to drive to zero.
CACHE_READ_TIMEOUT_MS = 60_000

#: The property names this repair reads. ⛔ It WRITES only the pair — see
#: :data:`_REPAIR_PAIR`.
_KIND = "sourceKind"
_SUMMARY = "summary"
_SEARCH_TEXT = "_searchText"
_TOPICS = "topics"

#: Only the properties the predicate READS. `properties(s)` would drag every
#: Source's dense `embedding` across the wire — one vector per document on a
#: corpus — and this read runs on every replay engine's trailing sweep.
_SOURCE_ROWS = (
    "MATCH (s:Source) "
    "RETURN s.url, s.sourceKind, s.summary, s._searchText, s.topics, s.sessionId"
)
#: THE ONE WRITE — the pair, and nothing else, in both arms.
#:
#: ⛔ ``topics`` IS DELIBERATELY NOT WRITTEN. It is not part of the prose datum:
#: the pre-#10000 writer took it from ``Counter.most_common(6)`` (word
#: frequencies) while ``summary`` was the first utterance, so the two were
#: already INDEPENDENT on this population — which is why ``cache_projection``'s
#: declined set never contained it. And the record's own ``topics`` column is
#: what ``record-reconcile`` compares EXACTLY (``topics`` is a projected
#: property, so it is not cache-owned): writing a re-derived value here would
#: make that reconciliation permanently divergent on exactly the population this
#: repair heals — one door over from the "signal indistinguishable from noise"
#: failure ``record_reconcile``'s docstring grades P1. Leaving it untouched also
#: keeps this route inside its declared bound, which is the pair.
_REPAIR_PAIR = (
    "MATCH (s:Source {url:$url}) "
    "SET s.summary=$sum, s._searchText=$st "
    "RETURN s.url"
)


# ── the census predicate ──────────────────────────────────────────────────


def carries_pre_lexicon_prose(props: Mapping[str, Any]) -> bool:
    """True when this node holds the PRE-#10852 derivation of the pair.

    Two clauses, and both are load-bearing:

    * **the identity** — :func:`~tortoise.cache_projection.is_episodic_source_node`
      (the repo's ONE declaration: a ``session:`` url, or an ``agentSession``
      Source on a separator-free bare filename). ``is_episodic=True`` is NOT a
      signal: the capture commit stamps it on every external source and
      ``backfill_is_episodic`` on every Source reachable from a session point,
      so keying on it selects DOCUMENTS — whose ``summary``/``_searchText`` are
      legitimate, and which is why the tracker's count was pinioned above zero.
    * **the witness** — the node carries text, and its ``summary`` does NOT
      start with ``SESSION_SOURCE_SUMMARY_PREFIX``. Every post-#10852 node does,
      so this is what tells a lexicon from prose: a legacy row's
      ``summary == _searchText == <utterance>`` and the utterance can itself be
      a comma-space list that coincides with ``", ".join(topics)`` exactly. The
      prefix cannot be produced by prose (#10852 review P2).

    ⛔ A ``summary`` that is empty while ``_searchText`` carries text is the
    shape the RECORD-RESTORE door writes TODAY, and it is HEALTHY: the projection
    DECLINES ``summary`` for an episodic Source while projecting the lexicon
    ``_searchText`` (#10965), so ``node_from_row`` yields exactly
    ``{..., _searchText: <lexicon>, topics: [...]}`` with no ``summary``. Keying
    the blank-summary arm on emptiness alone made every restored session a
    candidate, so a ``--repair`` on a ``record-restore``d graph cleared the whole
    tenant's Source index. The arm therefore requires that the node NOT prove the
    lexicon: ``topics`` a non-empty list of non-empty strings AND
    ``_searchText == ", ".join(topics)`` — the same proof
    ``cache_projection._episodic_source_search_text_is_the_lexicon`` uses, applied
    to the node's property names. A pre-fix row's ``_searchText`` is the utterance
    and its ``topics`` are the word frequencies, so the join does not hold.
    ⛔ WHAT THIS ARM CAN AND CANNOT DECIDE. It is a NO-OP on the true pre-fix
    population: that writer could not emit a blank ``summary`` with a non-empty
    ``_searchText`` (its text-less branch returns ``("", [])`` and its other arm
    always sets ``summary``), so every genuine pre-fix node carries a
    non-prefixed, non-blank ``summary`` and is caught by the clause above. What
    this arm actually decides is the RESIDUE of the record-restore shape: a node
    with no ``summary`` whose ``topics`` do not prove its ``_searchText``. It
    fails toward CANDIDATE — which is the DESTRUCTIVE direction here, the
    opposite of the projection's polarity, and it is deliberate only because
    :func:`_proves_the_lexicon` is an exact two-term proof rather than a shape
    test: the arm exists for a node whose blank ``summary`` plus unexplained
    ``_searchText`` cannot be the record door's output. It is reported, never
    silent.
    """
    if not isinstance(props, Mapping) or not is_episodic_source_node(props):
        return False
    summary = props.get(_SUMMARY)
    search_text = props.get(_SEARCH_TEXT)
    summary = summary if isinstance(summary, str) else ""
    search_text = search_text if isinstance(search_text, str) else ""
    if not summary.strip() and not search_text.strip():
        return False
    if summary.startswith(SESSION_SOURCE_SUMMARY_PREFIX):
        return False
    # The blank-`summary` arm: a record-RESTORED node has no `summary` (the
    # projection declines it for an episodic Source) but a lexicon `_searchText`
    # with the `topics` it was joined from. Healthy — never a candidate.
    return bool(summary.strip()) or not _proves_the_lexicon(props)


def _proves_the_lexicon(props: Mapping[str, Any]) -> bool:
    """True when the node's own ``topics`` PROVE ``_searchText`` is the lexicon.

    The node-property twin of
    :func:`tortoise.cache_projection._episodic_source_search_text_is_the_lexicon`
    (which answers for a RECORD ROW): EXACT equality against the join, never a
    shape test, and a ``topics`` that is not a list of non-empty strings proves
    nothing. ⚠️ The POLARITY is the opposite of that function's, and deliberately:
    there an unproven row keeps the pair DECLINED (a false positive would put
    prose back in the index), while here an unproven node stays a CANDIDATE (a
    false negative leaves prose on a node, which is visible in the census and
    recoverable by the record-re-derived run — a false positive would destroy a
    healthy node's index entry).
    """
    search_text = props.get(_SEARCH_TEXT)
    if not isinstance(search_text, str) or not search_text:
        return False
    topics = props.get(_TOPICS)
    if not isinstance(topics, (list, tuple)) or not topics:
        return False
    tokens = [t for t in topics if isinstance(t, str) and t]
    if len(tokens) != len(topics):
        return False
    return search_text == ", ".join(tokens)


def _session_id(props: Mapping[str, Any]) -> str | None:
    """The session this node belongs to — derived, never invented.

    ``s.sessionId`` is the capture's own property; a legacy bare-filename node
    has none, and its url carries no id (:func:`derive_session_source_url` is
    the inverse direction), so there is nothing to read the record BY. That
    node is repaired (cleared) and reported — it is never attributed to a guess
    (#4125's attribution rule).
    """
    sid = props.get("sessionId")
    if isinstance(sid, str) and sid.strip():
        return sid
    url = props.get("url")
    if isinstance(url, str) and url.startswith("session:"):
        tail = url[len("session:"):]
        return tail if tail.strip() else None
    return None


def _read_source_rows(g, timeout_ms: int) -> list[dict]:
    rows = g.query(_SOURCE_ROWS, timeout=int(timeout_ms)).result_set
    out: list[dict] = []
    for row in rows or ():
        if not row:
            continue
        props = {"url": row[0], _KIND: row[1], _SUMMARY: row[2],
                 _SEARCH_TEXT: row[3], _TOPICS: row[4], "sessionId": row[5]}
        out.append(props)
    return out


def find_session_sources_carrying_prose(
        g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> list[dict]:
    """READ-ONLY: every node this repair would touch, with its byte sizes.

    The dry-run/audit surface. Writes nothing of any kind.
    """
    out: list[dict] = []
    for props in _read_source_rows(g, timeout_ms):
        if not carries_pre_lexicon_prose(props):
            continue
        summary = props.get(_SUMMARY)
        search_text = props.get(_SEARCH_TEXT)
        out.append({
            "url": props.get("url"),
            "session_id": _session_id(props),
            "summary_bytes": len(summary.encode("utf-8")) if isinstance(summary, str) else 0,
            "search_text_bytes": (len(search_text.encode("utf-8"))
                                  if isinstance(search_text, str) else 0),
        })
    out.sort(key=lambda n: str(n["url"]))
    return out


def census(g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> int:
    """The population size — the number this repair must be able to drive to 0."""
    return len(find_session_sources_carrying_prose(g, timeout_ms=timeout_ms))


# ── the record side ───────────────────────────────────────────────────────


def record_turn_reader(conn: Any, *, org_id: str, graph_id: str
                       ) -> Callable[[str], str | None]:
    """A transcript reader over the RECORD, for :func:`reconcile_session_source_prose`.

    ``None`` means THE RECORD DID NOT ANSWER for those turns — which is not the
    same as "the session has no turns" (``TurnAnswer.NO_ACCOUNT`` and
    ``EMPTY`` are different answers, and #10000 review P0-3 exists because
    conflating them serves an empty transcript over a store that still holds the
    text). Both are ``None`` here on purpose: this repair never derives a
    lexicon from a partial or absent answer, and the node is cleared + reported
    instead. A PARTIAL answer is refused for the same reason — a lexicon built
    from half a transcript is a fabricated descriptor.
    """
    def read(session_id: str) -> str | None:
        from tortoise.record_read import session_turns

        answer = session_turns(conn, org_id=org_id, graph_id=graph_id,
                              session_id=session_id)
        if not answer.answered:
            # NO_ACCOUNT, CONTRADICTION, or a PARTIAL set — the record did not
            # answer for the WHOLE session, and a lexicon built from half a
            # transcript would be a fabricated descriptor.
            return None
        if not answer.turns:
            # EMPTY: the record SPOKE and states the session has no turns. That
            # is an answer, and it is not a DSN/scope problem.
            return ""
        # The writer's transcript is ONE ``Speaker: body`` line per turn, joined
        # by newlines (``sdk._session_llm_transcript``). ``Turn.line`` is the
        # record's declaration of that same rendering rule, so re-deriving
        # through it cannot drift from the writer's frame.
        return "\n".join(t.line for t in answer.turns)

    return read


def _derive(transcript: str | None) -> dict:
    """The capture's OWN derivation — imported lazily, never re-implemented."""
    if transcript is None:
        # The writer's text-less shape: `summary=''` (its clause is
        # `coalesce(s.summary,'')`) and NO `_searchText` at all (`$st` is None and
        # FalkorDB drops a null property). Passing `''` here would mint a state no
        # writer reaches.
        return {"sum": "", "st": None}
    # LAZY: `sdk` imports `cache_projection`, so a module-level import here is a
    # layering inversion the day anything on that path imports this module.
    from tortoise.sdk import _session_source_metadata, _source_search_text

    summary, topics = _session_source_metadata(transcript)
    search_text = _source_search_text(topics=topics)
    return {"sum": summary, "st": search_text}


# ── the repair ────────────────────────────────────────────────────────────


def reconcile_session_source_prose(
        g, *,
        read_transcript: Callable[[str], str | None] | None = None,
        dry_run: bool = True,
        timeout_ms: int = CACHE_READ_TIMEOUT_MS,
        found: list[dict] | None = None) -> dict:
    """Clear the pre-#10852 prose pair on every episodic session ``:Source``.

    Idempotent and re-runnable: the output is not a candidate (:func:`carries_
    pre_lexicon_prose` rejects a prefixed ``summary``), so a second run finds
    nothing and the census reaches 0 on the repaired graph.

    ``dry_run=True`` (the DEFAULT) writes nothing and returns the same report
    shape. ⚠️ The default DIVERGES from #4125's reconciler, which defaults to
    writing: this one destroys a value that has no graph-side copy, so the plan
    is the thing an operator approves, and a bare call can never be the run that
    did it.

    Returns (the SAME keys in both modes):
      ``candidates``  urls carrying the pair,
      ``repaired``    urls whose pair was re-derived FROM THE RECORD,
      ``empty_sessions``  urls whose record SPOKE and states the session has 0
                      turns — cleared, and NOT a record/scope problem,
      ``no_record``   urls whose pair was CLEARED because the record did not
                      ANSWER (never a fabricated lexicon),
      ``notes``       url -> why a candidate was handled the way it was,
      ``census_before``  the population, measured,
      ``census_after``   the population re-MEASURED after the writes; equal to
                      ``census_before`` on a dry run, because nothing was
                      written. Re-measured rather than decremented: a write that
                      silently missed (a node deleted mid-run) must show up as a
                      census that did not move, which `before - healed` hides,
      ``dry_run``     the flag this call carried.
    """
    # `found` lets a caller that has ALREADY taken the census (the CLI's dry run,
    # which wants the byte sizes) hand it in instead of paying for a second
    # whole-`Source` scan.
    if found is None:
        found = find_session_sources_carrying_prose(g, timeout_ms=timeout_ms)
    report: dict = {
        "candidates": [n["url"] for n in found],
        "repaired": [],
        "empty_sessions": [],
        "no_record": [],
        "notes": {},
        "census_before": len(found),
        "census_after": len(found),
        "dry_run": bool(dry_run),
    }
    for node in found:
        url = node["url"]
        if not isinstance(url, str) or not url:
            continue
        sid = node["session_id"]
        # THREE answers, and they are not two: "" is the record SAYING the
        # session has no turns (TurnAnswer.EMPTY), None is the record NOT
        # ANSWERING (NO_ACCOUNT / CONTRADICTION / a PARTIAL set — a lexicon built
        # from half a transcript would be a fabricated descriptor). Collapsing
        # them sent an operator hunting a DSN or row-level-security problem for a
        # session whose record had spoken.
        transcript = None
        if sid is not None and read_transcript is not None:
            transcript = read_transcript(sid)
        elif sid is None:
            report["notes"][url] = (
                "no session id is derivable from this node (a pre-#4005 "
                "bare-filename url, or none), so the record cannot be read BY "
                "session — the pair is cleared, never re-derived")
        values = _derive(transcript)
        if transcript is None:
            report["no_record"].append(url)
        elif transcript == "":
            report["empty_sessions"].append(url)
        else:
            report["repaired"].append(url)
        if dry_run:
            continue
        # ⛔ `updatedAt` is deliberately NOT written: the reconciler must not
        # invent state a writer did not produce (#4125's rule). The pair is a
        # DERIVATION being corrected, not a new capture.
        #
        # The PAIR only, in both arms — `topics` is left exactly as it was (see
        # `_REPAIR_PAIR`'s ⛔).
        g.query(_REPAIR_PAIR,
                params={"url": url, "sum": values["sum"], "st": values["st"]})
    if not dry_run and found:
        # Re-MEASURED, never decremented — see the docstring's `census_after`.
        # Only when something was written: on an empty candidate set the value is
        # provably 0, and every replay engine runs this hook, so a second
        # whole-`Source` property scan for a known answer is a real cost on the
        # recovery path.
        report["census_after"] = census(g, timeout_ms=timeout_ms)
    return report


def reconcile_after_replay(projection, *, engine: str,
                           read_transcript: Callable[[str], str | None] | None = None
                           ) -> dict | None:
    """End-of-replay-sweep entry point for the whole-journal replay engines.

    ⛔ WITHOUT A RECORD HANDLE THIS IS PLAN-ONLY, and that is a correctness
    requirement, not caution. The clear arm is destructive and NOT
    self-correcting (its output is no longer a candidate, so a later
    ``--repair --record-dsn`` finds nothing and writes nothing), and a replay
    engine holds no record connection. A hook that cleared here would un-index
    the whole pre-#10852 population on any rebuild performed before the
    operator's repair — irreversibly, because the record's row cannot prove the
    lexicon on this door — and would then report ``census == 0``, a FALSE PASS of
    exactly the kind this issue exists to close. So a replay with no reader
    reports the population and writes nothing; the operator's run, which has the
    record, is what heals it.

    NEVER raises: this runs AFTER the wipe, where an exception would leave an
    unproven graph (#2943 "no loss without proof"). A projection without a graph
    handle (an injected fake in a test) is a silent no-op.
    """
    g = getattr(projection, "g", None)
    if g is None:
        return None
    try:
        report = reconcile_session_source_prose(
            g, read_transcript=read_transcript,
            dry_run=read_transcript is None)
    except Exception:  # noqa: BLE001, RUF100
        logger.exception(
            "%s: pre-#10852 session :Source prose reconciliation FAILED — the "
            "graph is rebuilt but nodes captured before the fix still carry the "
            "transcript's utterance in `summary`/`_searchText`; re-run "
            "graph-scripts/11070_repair_session_source_prose.py (#11070)",
            engine)
        return None
    if report["candidates"]:
        if report["dry_run"]:
            # LOUD, because the graph is now in a state the operator must finish:
            # the replayed journal carried the pre-fix prose back in.
            logger.warning(
                "%s: %d session Source(s) carry the pre-#10852 prose pair and "
                "this replay engine has no record handle, so NOTHING was "
                "written (a clear here would be irreversible and would hide the "
                "population). Run graph-scripts/11070_repair_session_source_prose"
                ".py --repair --record-dsn … --org-id … --graph-id … (#11070)",
                engine, len(report["candidates"]))
        else:
            logger.info(
                "%s: cleared the pre-#10852 prose pair on %d session Source(s) "
                "(%d re-derived from the record, %d cleared with no record "
                "answer) (#11070)", engine, len(report["candidates"]),
                len(report["repaired"]), len(report["no_record"]))
    return report
