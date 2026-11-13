"""#11070 — the repair for the PRE-#10852 session ``:Source`` prose population.

WHAT THIS EXISTS FOR
====================
#10852 stopped the capture writing the transcript's first substantive utterance
into ``Source.summary`` (and deriving ``_searchText`` from it). It deliberately
did NOT heal the nodes that already hold the pair, and no OTHER route reaches them:

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

A node whose record DOES NOT ANSWER is reported under ``no_record``, and its
pair is cleared ONLY when the caller asks for that by name
(``clear_without_record=True``) — because §9.1/D1 says the prose does not belong
on a graph node whether or not a lexicon can replace it, while a CLEAR cannot be
undone by a second run (the cleared node stops matching the census, so nothing
re-derives it later). The cleared shape is the writer's own: ``summary=''`` and
an ABSENT ``_searchText``, which is exactly what a text-less capture leaves
(``s._searchText=coalesce($st, s._searchText)`` with ``$st`` None). It never
INVENTS a lexicon — that is the constraint, and it is why the arms are reported
separately.

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
``backup.restore``) the repair must be RE-RUN with ``--record-dsn`` AND with
``--graph`` naming the CACHE graph the replay just wrote — without it the CLI
addresses ``derive_uri_graph($TORTOISE_DB_URI)``, which on a hosted or multi-graph
box is not the graph the record scope names, so it finds 0 candidates and prints
``Repair complete.`` over the unrepaired graph. A replay does not restore it. The alternative — clearing at replay — would un-index every
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
import os
import re
from collections.abc import Callable, Mapping
from typing import Any

from tortoise.cache_projection import is_episodic_source_node
from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

logger = logging.getLogger(__name__)

__all__ = [
    "CACHE_READ_TIMEOUT_MS",
    "FTS_PROBE_ENTITY_TYPE",
    "carries_pre_lexicon_prose",
    "census",
    "classify_fts_hits",
    "find_session_sources_carrying_prose",
    "lexicon_control_read",
    "lexicon_control_token",
    "probe_fts",
    "probe_phrase_rejection",
    "reconcile_after_replay",
    "reconcile_session_source_prose",
    "record_turn_reader",
    "source_fts_probe",
    "source_index_is_empty",
]

#: Wall-clock bound on the cache-wide Source read. Bounded, never truncated —
#: the same rule ``record_reconcile.scan_cache_view`` states: a ``LIMIT`` would
#: need its own truncation detection to stay honest, and a silently truncated
#: read UNDERCOUNTS the population this module exists to drive to zero.
#:
#: ⛔ THIS IS A REQUEST, NOT A GUARANTEE. FalkorDB refuses a query whose
#: ``TIMEOUT`` parameter exceeds the server's ``TIMEOUT_MAX``, and that cap is a
#: DEPLOYMENT setting — the canonical docker test lane runs
#: ``TIMEOUT_MAX 30000`` (``tools/test_lane.py:LANE_FALKORDB_ARGS``), so a bare
#: 60 000 here raised ``ResponseError: The query TIMEOUT parameter value cannot
#: exceed the TIMEOUT_MAX configuration parameter value`` on every run against
#: it: the census could never execute, and ``--repair`` wrote the pair and then
#: died in ``census_after``. Measured, not inferred (#11152 review P1).
#: :func:`_effective_timeout_ms` clamps the request to the server's own cap.
CACHE_READ_TIMEOUT_MS = 60_000

#: The subset of ``FalkorDB``'s error text that means "your requested timeout is
#: above MY cap" — not "the query ran long". Matched as a substring because the
#: message is the server's, and it is the only signal that separates the two.
_TIMEOUT_CAP_ERR = "TIMEOUT parameter value cannot exceed"

#: A cut of ``record_read.session_turns``'s PARTIAL reason that names the TAIL
#: witness. Kept ONLY as a fallback for an answer object that carries no
#: ``Turn.index`` — the production reasons DO contain this wording
#: (``… — the tail is missing, so this is a partial transcript``), which is
#: exactly why it cannot be the primary test: a diagnostic keyed on wording is
#: what became dead code in round 3 (the constant then held the NAME
#: ``turn_max_index``, which no reason ever contains) and its test passed over it.
#: The REAL test is structural, in :func:`record_turn_reader`.
_TAIL_WITNESS_HINT = "the tail is missing"

#: OFF-SWITCH for the census's NODE-IDENTITY dedupe (#11176). `=1` restores the
#: PRE-FIX path EXACTLY: one record per ROW ``_SOURCE_ROWS`` returns, carrying
#: that row's ``turn_max_index`` — so a ``:Source`` reached by two ``:Session``
#: rows is counted TWICE, the tail witness is whatever row arrived first, and
#: ``duplicate_session_rows`` is ABSENT (no count was taken).
#:
#: ⛔ NARROW BY DESIGN — only the exact string ``1`` disables it, and the site is
#: declared in ``tests/test_env_truthy.py``'s ``_KNOWN_NARROW_READS`` ledger with
#: its ``OVERRIDES`` reason (#4097/#4128). A `=true` written for legibility would
#: silently leave the FIXED census running, i.e. an operator believing they
#: re-measured with the old instrument would get a number taken by the new one.
#:
#: ⛔ WHY A SWITCH EXISTS ON A BUG FIX. This dedupe changes the NUMBER the whole
#: repair is verified BY (``census_after``, i.e. #10000 criterion 2), so an
#: operator holding a census recorded with the old instrument must be able to
#: re-measure with the OLD instrument — without reverting the module — before
#: concluding that a population grew. It is a kill switch, not a preference:
#: the default is the fixed path, and only the exact string ``1`` turns it off.
_CENSUS_DEDUPE_DISABLED = "TORTOISE_11070_CENSUS_DEDUPE_DISABLED"

#: The property names this repair reads. ⛔ It WRITES only the pair — see
#: :data:`_REPAIR_PAIR`.
_KIND = "sourceKind"
_SUMMARY = "summary"
_SEARCH_TEXT = "_searchText"
_TOPICS = "topics"
#: The census's per-row node identity (`id(s)`). The write addresses ONE node by
#: it; see `_REPAIR_PAIR`'s ⛔.
_NODE_ID = "node_id"

#: Only the properties the predicate READS. `properties(s)` would drag every
#: Source's dense `embedding` across the wire — one vector per document on a
#: corpus — and this read runs on every replay engine's trailing sweep.
#:
#: ⛔ ONE ROW PER MATCHING `:Session`, NOT ONE PER `:Source` (#11176). `:Session.id`
#: carries a plain INDEX and no UNIQUE constraint (``CREATE CONSTRAINT`` count is
#: 0 in this repo), so two `:Session` nodes can carry one ``sessionId`` and this
#: OPTIONAL MATCH then fans one `:Source` out into one row per session. The
#: census dedupes those rows on ``id(s)`` — see
#: :func:`find_session_sources_carrying_prose`.
_SOURCE_ROWS = (
    "MATCH (s:Source) "
    "OPTIONAL MATCH (sess:Session {id: s.sessionId}) "
    "RETURN s.url, s.sourceKind, s.summary, s._searchText, s.topics, "
    "       s.sessionId, sess.turn_max_index, id(s)"
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

# ⛔ THE MATCH IS A NODE MATCH — by the census's own `id(s)` — AND a
# COMPARE-AND-SET, and BOTH are load-bearing.
#
# * `:Source` declares NO uniqueness constraint on `url` — the repo's own
#   reconciler names that state as REACHABLE (``record_reconcile.py``: "the
#   graph declares no uniqueness constraint, so a second writer's ``CREATE``
#   can leave TWO nodes at one (label, key)"). A bare
#   ``MATCH (s:Source {url:$url})`` is therefore a POPULATION match, not a node
#   match: with a HEALTHY sibling at the same url (a ``record-restore``d node —
#   no ``summary``, ``_searchText`` = the lexicon) BOTH arms wrote that sibling,
#   and the CLEAR arm removed its only index entry while no bucket named it.
#   Measured on a real FalkorDB: the census selected ONE node, the write landed
#   on two (PR #11152 review F1).
# * The PAIR ALONE IS NOT AN IDENTITY. Two url-mates can carry an IDENTICAL pair
#   — the most likely duplicate shape, and the only one that matters: the
#   blank-`summary` candidate arm exists for a residue whose ``_searchText`` is
#   the lexicon while its ``topics`` do not prove it, and its HEALTHY twin (same
#   url, same absent ``summary``, same lexicon, topics that DO prove it) has a
#   byte-identical pair. So a value-only guard leaves the F1 symptom intact for
#   exactly that class, and two identical CANDIDATES make the second row's write
#   match nothing and report "NOTHING was written" about a url the same report
#   lists as written (PR #11152 review round 2, P2). The census therefore
#   carries ``id(s)`` and this write addresses ONE node.
# * The pair stays as the SECOND guard: it is what the census decided on, so a
#   node that changed between the read and the write is NOT written, and
#   ``census_after`` (RE-MEASURED, never decremented) shows the miss.
#
# ``{url:$url}`` is kept in the ``MATCH`` on purpose: it is the indexed seek,
# and ``id(s)`` is the filter over the handful of nodes it returns.
#
# ⚠️ The pair comparison is STRING-NORMALIZED (``coalesce(...)=''``), because a
# raw non-string param can trip ``_guard_numeric_params`` mid-run. A candidate
# holding a non-string pair value therefore cannot be compared at all and is
# reported as ``unaddressable`` BY NAME (a pinned census, never a silent one).


_REPAIR_PAIR = (
    "MATCH (s:Source {url:$url}) "
    "WHERE id(s)=$nid "
    "AND coalesce(s.summary,'')=$old_sum "
    "AND coalesce(s._searchText,'')=$old_st "
    "SET s.summary=$sum, s._searchText=$st "
    "RETURN id(s)"
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
    node is reported (``no_record``, with a note) and left alone unless
    ``clear_without_record`` is set — it is never attributed to a guess
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


def _server_timeout_cap(g) -> int | None:
    """The server's ``TIMEOUT_MAX``, or ``None`` when it cannot be asked.

    ``0`` means UNCAPPED, which FalkorDB reports as such — it is not "a cap of
    zero milliseconds", so it is folded into ``None`` here.

    ⛔ ASKING BEATS GUESSING, AND THE VERB IS ON THE **CLIENT**, NOT THE GRAPH.
    A ``falkordb.Graph`` **instance** has NO ``config_get`` and NO ``connection``
    attribute (measured: ``hasattr(g, 'config_get') is False``,
    ``hasattr(g, 'connection') is False``, ``hasattr(g, 'client') is True`` —
    ``client`` is an INSTANCE attribute set in ``Graph.__init__``, not a class
    attribute, so ``hasattr(Graph, 'client')`` is False and quoting that as the
    measurement is itself misleading); the projection's ``_GuardedGraph``
    forwards to the same thing. Probing the graph handle itself therefore found
    nothing and the clamp silently never ran: the 60 000 ms request went out
    unclamped and every read took the ResponseError path below. That is how a
    "fix" can be dead code on every real deployment while its unit test, which
    put ``config_get`` on the fake graph, passes (#11152 review round 2, P1).
    The handles are tried CLIENT-FIRST for that reason, and the test fake models
    the client shape rather than this one.

    A server that cannot answer is NOT an error — the retry in
    :func:`_read_source_rows` is the last resort.
    """
    candidates = (getattr(g, "client", None),
                  getattr(g, "connection", None),
                  getattr(getattr(g, "_g", None), "client", None),
                  g)
    for handle in candidates:
        getter = getattr(handle, "config_get", None)
        if getter is None:
            continue
        try:
            cap = getter("TIMEOUT_MAX")
        except Exception:  # an unaskable server is not a failure
            continue
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            continue
        return cap if cap > 0 else None
    return None


def _effective_timeout_ms(g, requested_ms: int) -> int:
    """*requested_ms*, clamped DOWN to the server's own ``TIMEOUT_MAX``.

    Clamped, never dropped: the bound this module promises stays a bound on a
    server that will state its cap. On an uncapped server (``TIMEOUT_MAX 0``, the
    FalkorDB default) the request is honoured as written.

    ⚠️ A server that will NOT state its cap may still refuse the request (see
    :func:`_read_source_rows`); this function cannot know that, so it returns the
    request it would issue — it is the REQUEST, not a claim about what the read
    carried.
    """
    cap = _server_timeout_cap(g)
    if cap is None or requested_ms <= cap:
        return int(requested_ms)
    return int(cap)


def _read_source_rows(g, timeout_ms: int) -> list[dict]:
    requested = int(timeout_ms)
    effective = _effective_timeout_ms(g, requested)
    try:
        rows = g.query(_SOURCE_ROWS, timeout=effective).result_set
    except Exception as exc:  # discriminated immediately below
        # ⛔ LAST RESORT, and only for the CAP class. A server that will not
        # answer `config_get` (an older build, a proxy, a seam in a test) can
        # still REFUSE an over-cap request, and "the census cannot run anywhere"
        # is the defect being fixed — so on exactly that message the read is
        # retried with NO explicit timeout, which is bounded by the server's own
        # policy instead of by ours. ⚠️ Declared cost: on a server whose default
        # `TIMEOUT` is non-zero (FalkorDB's own default is 1000 ms) and whose cap
        # is below our request, that retry can itself time out — and a timeout is
        # NOT the cap class, so it PROPAGATES rather than being retried again. The
        # caller sees a hard failure, never a short read reported as a census.
        # Any other exception is the query's, and belongs to the caller.
        if _TIMEOUT_CAP_ERR not in str(exc):
            raise
        rows = g.query(_SOURCE_ROWS).result_set
    out: list[dict] = []
    for row in rows or ():
        props = _row_props(row)
        if props is not None:
            out.append(props)
    return out


def _row_props(row) -> dict | None:
    """One ``_SOURCE_ROWS`` row as a property map, or ``None`` for an empty row.

    Split out so the FTS leg's hit-classifier parses a Source row the SAME way
    the census does — one row layout, one decoder (see :data:`_HITS_ROWS`).
    """
    if not row:
        return None
    tmi = row[6] if len(row) > 6 else None
    # `id(s)` — the node identity the write addresses by (`_REPAIR_PAIR`'s
    # ⛔). `None` when the row is short (a seam/fake that projects seven
    # columns): such a row cannot be addressed and is reported, never
    # guessed at.
    nid = row[7] if len(row) > 7 else None
    return {"url": row[0], _KIND: row[1], _SUMMARY: row[2],
            _SEARCH_TEXT: row[3], _TOPICS: row[4], "sessionId": row[5],
            "turn_max_index": tmi if isinstance(tmi, int) else None,
            _NODE_ID: nid if isinstance(nid, int) else None}


def _census_dedupe_enabled() -> bool:
    """Read the #11176 off-switch AT CALL TIME.

    Read per call, never cached at import: the switch exists so one measurement
    can be taken with the old instrument and the next with the new one, and a
    module-level cache would make the second the same as the first.
    """
    return os.environ.get(_CENSUS_DEDUPE_DISABLED) != "1"


def _strongest_mark(first: Any, second: Any) -> int | None:
    """The STRONGER (higher) of two ``turn_max_index`` marks, ``None`` if neither.

    ⛔ THE STRONGEST, NOT THE FIRST — this is the safety-relevant half of
    #11176. The mark is handed to ``record_read.session_turns`` as the tail
    witness: a mark BELOW the session's true id-space end serves a truncated
    transcript as COMPLETE, and the lexicon derived from half a transcript is
    the fabricated descriptor this module refuses to write everywhere else. A
    mark ABOVE the end has its own named cause (:func:`_no_record_note` names
    the cache as the suspect), so taking the maximum loses nothing — while
    taking the first row's value makes REFUSE-vs-WRITE depend on FalkorDB's row
    order. A non-``int`` mark is not a mark: the other side wins.
    """
    marks = [value for value in (first, second)
             if isinstance(value, int) and not isinstance(value, bool)]
    return max(marks) if marks else None


def find_session_sources_carrying_prose(
        g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> list[dict]:
    """READ-ONLY: every NODE this repair would touch, with its byte sizes.

    The dry-run/audit surface. Writes nothing of any kind.

    ⛔ ONE RECORD PER ``id(s)``, NOT ONE PER ROW (#11176). ``_SOURCE_ROWS`` fans
    a `:Source` out into one row per `:Session` at its ``sessionId``, and
    ``:Session.id`` has no uniqueness constraint — so without this dedupe a
    single node is counted TWICE, which inflates the very number #10000
    criterion 2 is measured by, and hands the tail witness to whichever row
    arrived first. The dedupe keeps the FIRST position (so the report's order
    stays the ``url`` sort) and the STRONGEST mark among the node's rows (:func:
    `_strongest_mark`). The duplicate ROW count is kept on the record as
    ``duplicate_session_rows`` — a census that silently collapses rows hides the
    duplicate-`:Session` signal, and that is a graph-health fact its caller is
    entitled to see.

    A row with no ``int`` node identity is appended AS-IS: there is nothing to
    dedupe on, and it cannot be addressed by the write either — it is reported
    under ``unaddressable`` by the reconciler, never silently merged into a peer.

    The record carries ``duplicate_session_rows`` (rows beyond the first) ONLY
    where the dedupe MEASURED it: under the off-switch the key is ABSENT, because
    no count was taken and ``0`` would read as a measurement.

    ``TORTOISE_11070_CENSUS_DEDUPE_DISABLED=1`` restores the pre-fix path
    exactly (one record per row, first-row mark, no duplicate count); see
    :data:`_CENSUS_DEDUPE_DISABLED`.
    """
    out: list[dict] = []
    #: ``id(s)`` -> the record already emitted for that node, so the second row
    #: of a duplicated `:Session` updates a mark instead of appending a record.
    seen: dict[int, dict] = {}
    dedupe = _census_dedupe_enabled()
    for props in _read_source_rows(g, timeout_ms):
        if not carries_pre_lexicon_prose(props):
            continue
        summary = props.get(_SUMMARY)
        search_text = props.get(_SEARCH_TEXT)
        record = {
            "url": props.get("url"),
            "session_id": _session_id(props),
            # The session's LIVE id-space end, read in the SAME scan. It is the
            # only witness for the one PARTIAL shape `record_read` cannot see
            # from the row set (a refused HIGHEST turn), so a reader built
            # without it serves a truncated transcript as COMPLETE — and a
            # lexicon derived from half a transcript is the fabricated
            # descriptor this module refuses to write (#11152 review P2).
            "turn_max_index": props.get("turn_max_index"),
            "summary_bytes": len(summary.encode("utf-8")) if isinstance(summary, str) else 0,
            "search_text_bytes": (len(search_text.encode("utf-8"))
                                  if isinstance(search_text, str) else 0),
            # ⛔ THE NODE IDENTITY (`id(s)`) AND THE PAIR THE CENSUS DECIDED ON.
            # Carried because the write must addres ONE node (`_REPAIR_PAIR`'s
            # ⛔): the pair alone is not an identity — two url-mates can carry a
            # byte-identical pair, and then a value-only match writes a node
            # this row never selected. A re-read at write time would compare
            # against values no census vouched for; omitting them made every
            # write match nothing (measured).
            # ⚠️ `summary`/`_searchText` are the transcript's own TEXT. They
            # exist for the write, not for the report: the CLI REDACTES them
            # (with `node_id`) out of `candidate_detail`, which is what gets
            # printed and serialised (`--json`), because this population's
            # `summary` IS a user utterance. Deleting them here — instead of
            # redacting there — is what would silently disarm the write.
            _NODE_ID: props.get(_NODE_ID),
            _SUMMARY: summary,
            _SEARCH_TEXT: search_text,
        }
        node_id = record.get(_NODE_ID)
        if dedupe and isinstance(node_id, int) and not isinstance(node_id, bool):
            #: Rows this NODE contributed beyond the first. ⛔ SET ONLY WHERE IT WAS
            #: MEASURED — inside the dedupe branch, never on the record's initial
            #: shape. Under the off-switch no de-duplication happens, so the count is
            #: not a `0`, it was never taken; emitting `0` there would report a graph
            #: with two `:Session` rows at one `sessionId` as "no duplicates" — the
            #: exact signal this field exists to carry, hidden as a measurement
            #: (#11176 review P2).
            record["duplicate_session_rows"] = 0
            already = seen.get(node_id)
            if already is not None:
                already["turn_max_index"] = _strongest_mark(
                    already.get("turn_max_index"), record.get("turn_max_index"))
                already["duplicate_session_rows"] += 1
                continue
            seen[node_id] = record
        out.append(record)
    out.sort(key=lambda n: str(n["url"]))
    return out


def census(g, *, timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> int:
    """The population size — the number this repair must be able to drive to 0."""
    return len(find_session_sources_carrying_prose(g, timeout_ms=timeout_ms))


# ── the record side ───────────────────────────────────────────────────────


def record_turn_reader(conn: Any, *, org_id: str, graph_id: str
                       ) -> Callable[..., str | None]:
    """A transcript reader over the RECORD, for :func:`reconcile_session_source_prose`.

    Takes ``(session_id, turn_max_index)``. ⛔ ``turn_max_index`` is NOT optional
    decoration: the record's own read names it as the ONLY witness for one of
    the three PARTIAL shapes — a transcript that stops BELOW the session's live
    id-space end, which is invisible to the row set itself (a refused HIGHEST
    turn; ``record_read.session_turns``'s third PARTIAL bullet). Passing
    ``None`` there makes that shape come back ``COMPLETE``, so the caller would
    write a lexicon derived from half a transcript — the fabricated descriptor
    this whole reader exists to refuse. The census reads the mark off the
    ``:Session`` node in the same scan it already makes; a caller that truly
    cannot name it passes ``None`` and accepts the degraded answer knowingly.

    ⚠️ Stated residual, WIDENED because the narrow form under-claimed it: the
    mark is ``None`` — and the tail arm is then unreachable — for THREE shapes,
    not one. (a) the candidate's ``:Session`` node is missing (`OPTIONAL MATCH`
    yields NULL); (b) its ``sessionId`` is null, so the `OPTIONAL MATCH` cannot
    bind; and (c) the node EXISTS but its ``turn_max_index`` is absent or
    non-int, which :func:`_read_source_rows` also maps to ``None``. Each one
    silently degrades the tail witness; none of them is a record that declined
    to answer.

    ``None`` means THE RECORD DID NOT ANSWER for those turns — which is not the
    same as "the session has no turns" (``TurnAnswer.NO_ACCOUNT`` and
    ``EMPTY`` are different answers, and #10000 review P0-3 exists because
    conflating them serves an empty transcript over a store that still holds the
    text). Both are ``None`` here on purpose: this repair never derives a
    lexicon from a partial or absent answer, and the node is cleared + reported
    instead. A PARTIAL answer is refused for the same reason — a lexicon built
    from half a transcript is a fabricated descriptor.
    """
    def read(session_id: str, turn_max_index: int | None = None) -> str | None:
        from tortoise.record_read import session_turns

        answer = session_turns(conn, org_id=org_id, graph_id=graph_id,
                              session_id=session_id,
                              turn_max_index=turn_max_index)
        # ⛔ The REASON travels with the answer. A bare ``None`` forces the caller
        # to guess WHY, and the guesses are not interchangeable: a PARTIAL caused
        # by the CACHE's ``turn_max_index`` being ahead of the record is not a
        # missing or mis-scoped record at all (see :func:`_no_record_note`).
        # Recorded on the closure so the caller can name it without changing the
        # ``str | None`` contract.
        #
        # ``getattr``, not attribute access: this module needs only ``answered``
        # and ``turns`` from the answer, and a seam (a test double, an older
        # ``TurnAnswer``) that omits the diagnostic fields must not turn a
        # correct read into an AttributeError.
        #
        # ⛔ THE TAIL SUSPECT IS COMPUTED STRUCTURALLY, NOT MATCHED FROM THE
        # WORDING. An earlier version tested the reason string for
        # ``"turn_max_index"``, which NO production reason contains (the message
        # interpolates the VALUE, never the name) — so the whole diagnostic was
        # dead code that a hand-written test could not distinguish from live code
        # (#11152 review round 3, P2). ``Turn.index`` is on the answer already, so
        # the exact condition `_shortfall` refuses on is recomputed here.
        turns = getattr(answer, "turns", ()) or ()
        # ⛔ A **SET**, because `record_read._shortfall` uses one
        # (`held = {turn.index for turn in turns}`). A LIST disagrees with it
        # exactly when an index REPEATS — reachable: `_turn_index` accepts any
        # `int()`-parsable suffix, so a backfilled `s_t01` and `s_t1` both yield
        # 1 — and `[0, 1, 3, 3]` is contiguous to a list-based check while it is a
        # GAP to `_shortfall`. That re-opened the round-5 misdiagnosis: the note
        # named the record's own hole and then blamed the cache mark in the next
        # sentence (#11152 review P2).
        held = {t.index for t in turns
                if isinstance(getattr(t, "index", None), int)}
        # ⛔ THE TAIL ARM IS EVALUATED **LAST** BY `_shortfall`, so only claim it
        # when the two arms that run BEFORE it cannot explain the refusal. A bare
        # `max(held) < turn_max_index` is a SUPERSET: the record's own short-count
        # arm (`len(turns) < turn_count`) and gap arm (a hole in `0 .. max`) are
        # refused before the tail is ever looked at, and blaming the cache for
        # those sends the operator to compare a mark that is irrelevant
        # (#11152 review round 4).
        count = getattr(answer, "turn_count", None)
        short_count = isinstance(count, int) and len(turns) < count
        # ⛔ THE GAP ARM MEASURES FROM ZERO, NOT FROM `min(held)`.
        # `record_read._shortfall` computes `span = max(held) + 1` and
        # `missing_count = span - len(held)`, so a run whose EARLIEST rows are
        # missing is a gap there and contiguous to a `range(min(held), max+1)`
        # comparison — which misdiagnosed exactly that case as the cache mark's
        # fault, quoting the record's own "no turn row for index 0" and then
        # blaming the cache in the next sentence (#11152 review round 5, P2).
        gap = bool(held) and (max(held) + 1) != len(held)
        tail_suspect = bool(
            not getattr(answer, "answered", False) and turn_max_index is not None
            and held and max(held) < int(turn_max_index)
            and not short_count and not gap)
        read.reasons[session_id] = (getattr(answer, "kind", ""),  # type: ignore[attr-defined]
                                    getattr(answer, "reason", ""), tail_suspect)
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

    read.reasons = {}  # type: ignore[attr-defined]
    return read


def _no_record_note(read_transcript, session_id: str | None) -> str:
    """WHY the record did not answer — named, because the guesses differ.

    ⛔ THE TAIL WITNESS IS THE CACHE'S. ``record_turn_reader`` forwards the
    ``:Session.turn_max_index`` it was handed, and this repair exists because the
    CACHE has diverged from the record — so a mark that is AHEAD of the record's
    live end makes ``record_read``'s ``_shortfall`` refuse a session the record
    answered COMPLETELY, and the node is then reported under ``no_record``, whose
    own name says the record was at fault. Measured shape (#11152 review round 2,
    P2): the diagnosis is wrong and the repair under-heals silently. Refusing is
    still the SAFE direction (a lexicon from a possibly-truncated transcript is
    the fabricated descriptor this module refuses), so the behaviour stays and
    the note names the suspect — and the operator can compare the mark with the
    record's live end before concluding the record is missing data.
    """
    reasons = getattr(read_transcript, "reasons", None)
    kind, reason, tail_suspect = ("", "", False)
    if isinstance(reasons, dict):
        entry = reasons.get(session_id) or ("", "", False)
        kind, reason, tail_suspect = (*tuple(entry), "", "", False)[:3]
    note = ("the record did not ANSWER for this session, so no lexicon is "
            "invented")
    if reason:
        note += f" ({kind}: {reason})"
    if tail_suspect or _TAIL_WITNESS_HINT in str(reason or ""):
        note += (". ⚠️ THIS MAY BE THE CACHE'S FAULT, NOT THE RECORD'S: the tail "
                 "witness handed to the record read is the CACHE's "
                 "`:Session.turn_max_index`, and a mark AHEAD of the record's "
                 "live end makes a complete answer read as PARTIAL — compare the "
                 "mark with the record before concluding the record is short")
    return note


def _add_note(report: dict, url: str, text: str) -> None:
    """ACCUMULATE a note — never replace one.

    ⛔ A node can have more than one thing wrong with it, and the second write
    used to erase the first: a one-argument reader's "the tail witness was
    dropped" note was destroyed by the not-answered note written right after it —
    exactly when the operator most needs to know the diagnosis is unreliable
    (#11152 review round 4).
    """
    previous = report["notes"].get(url)
    report["notes"][url] = f"{previous}; {text}" if previous else text


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
        read_transcript: Callable[..., str | None] | None = None,
        dry_run: bool = True,
        timeout_ms: int = CACHE_READ_TIMEOUT_MS,
        found: list[dict] | None = None,
        clear_without_record: bool = False) -> dict:
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
      ``no_reader``   urls this run never asked the record about at all — a
                      reader was not supplied. ⛔ NOT folded into
                      ``no_record``: collapsing "nobody asked" into "the record
                      was silent" sends an operator hunting a DSN or a
                      row-level-security problem that does not exist, which is
                      the inverse of the conflation #10000 review fixed one
                      bucket over,
      ``no_session_id``  urls a reader WAS supplied for but whose node carries
                      no derivable session id — a pre-#4005 bare-filename url,
                      for instance. ⛔ A FOURTH bucket, not folded into
                      ``no_reader`` (a reader was supplied) or ``no_record``
                      (the record was never asked): before it existed these
                      nodes satisfied neither, so on the clear arm a
                      DESTRUCTIVE write was reported as nothing,
      ``left_alone``  urls NOT written at all because the record could not
                      supply a replacement and ``clear_without_record`` is
                      False. Clearing is the arm that cannot be undone by a
                      second run (the second run finds no candidate), so it is
                      taken only when ASKED FOR BY NAME — the same rule #4125's
                      operator CLI states for its own destructive arm,
      ``unaddressable``  urls a CANDIDATE that the write cannot address, and
                      therefore urls that pin ``census_after`` above zero while
                      they exist: no usable url, or no census node id / a
                      non-string pair value (the write compares the pair as
                      text). Named on every arm, never silent — repair the node
                      by hand or delete it,
      ``written``     urls whose write actually MATCHED a ``:Source``. ⛔ Read
                      by the CLI as ``wrote=bool(report["written"])`` to select
                      the fail-closed arm of the FTS verdict, so it is the
                      count the run was certified by — never arithmetic over the
                      buckets above, which overlap (`left_alone` also holds the
                      ``no_session_id``/``no_reader`` nodes) and would report
                      fewer repairs than were made,
      ``notes``       url -> why a candidate was handled the way it was,
      ``census_before``  the population, measured,
      ``census_after``   the population re-MEASURED after the writes; equal to
                      ``census_before`` on a dry run, because nothing was
                      written. Re-measured rather than decremented: a write that
                      silently missed (a node deleted mid-run) must show up as a
                      census that did not move, which `before - healed` hides,
      ``dry_run``     the flag this call carried,
      ``timeout_ms``  the timeout the reads ACTUALLY used, after clamping to the
                      server's own cap — the requested value may be larger, so
                      an operator reading the report sees the bound it ran
                      under.
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
        "no_reader": [],
        "no_session_id": [],
        "unaddressable": [],
        "left_alone": [],
        "written": [],
        "notes": {},
        "census_before": len(found),
        "census_after": len(found),
        "dry_run": bool(dry_run),
        "timeout_ms": _effective_timeout_ms(g, timeout_ms),
    }
    for node in found:
        url = node["url"]
        if not isinstance(url, str) or not url:
            # ⛔ A CANDIDATE THAT CANNOT BE ADDRESSED. `carries_pre_lexicon_prose`
            # accepts an empty-`url` `agentSession` Source (the bare-filename arm
            # needs only "no `://` and no `/`"), and the census counts it — but it
            # cannot be written, so it pinned `census_after` above zero FOREVER
            # with `--repair` exiting 1 and no reason given anywhere in the
            # report: an unreachable zero, which is the class #11070 exists to
            # close (#11152 review round 4).
            report["unaddressable"].append(node.get("url"))
            _add_note(report, "<empty url>",
                      "a candidate with no usable url: it carries the prose pair "
                      "and counts in the census, but cannot be addressed by a "
                      "write, so `census_after` cannot reach 0 while it exists — "
                      "repair the node by hand or delete it (#11070)")
            continue
        sid = node["session_id"]
        # FOUR answers, and they are not three. "" is the record SAYING the
        # session has no turns (TurnAnswer.EMPTY); None is the record NOT
        # ANSWERING (NO_ACCOUNT / CONTRADICTION / a PARTIAL set); and NO READER
        # is a third thing — nobody asked — which must never be reported as the
        # record's silence (#11152 review P2). Collapsing the first two sent an
        # operator hunting a DSN or row-level-security problem for a session
        # whose record had spoken; collapsing the third does the same for a
        # record that was never opened.
        asked = read_transcript is not None and sid is not None
        transcript: str | None = None
        if asked:
            try:
                transcript = read_transcript(sid, node.get("turn_max_index"))
            except TypeError:
                # A reader declared as ``read(session_id)`` — the one-argument
                # shape. Degraded (no turn-end witness), and said so in the
                # notes rather than crashing the run.
                transcript = read_transcript(sid)  # type: ignore[call-arg]
                _add_note(report, url,
                          "the reader takes one argument, so the session's "
                          "turn_max_index was not supplied — a PARTIAL set that "
                          "stops below the id-space end cannot be detected by it")
        elif read_transcript is None:
            report["no_reader"].append(url)
        else:
            # ⛔ A FOURTH BUCKET, and it needs its own counter: with a reader
            # supplied, a node carrying no derivable session id satisfies
            # neither `no_reader` (a reader WAS supplied) nor `no_record` (the
            # record was never asked), so it landed in no bucket at all — and on
            # the clear arm that made a DESTRUCTIVE write reported as nothing
            # (#11152 review round 2, P2).
            report["no_session_id"].append(url)
        if asked and transcript is None:
            report["no_record"].append(url)
            why = _no_record_note(read_transcript, sid)
        elif transcript is None:
            why = ("no session id is derivable from this node (a pre-#4005 "
                   "bare-filename url, or none), so the record cannot be read "
                   "BY session" if sid is None else
                   "no transcript reader was supplied, so the record was never "
                   "asked")
        if transcript is None:
            # ⛔ The note must state what HAPPENED, not what the arm would have
            # done: emitting "left alone" on the run that cleared the node — or
            # "the pair is CLEARED" on a DRY RUN that wrote nothing — is a report
            # contradicting its own writes (#11152 review rounds 2 and 4).
            if dry_run:
                outcome = (" — a dry run writes NOTHING; --repair with "
                           "clear_without_record set would CLEAR it"
                           if clear_without_record else
                           " — a dry run writes NOTHING; --repair would leave it "
                           "alone unless clear_without_record is set")
            else:
                outcome = (" — the pair is CLEARED, because clear_without_record "
                           "is set" if clear_without_record else
                           " — the pair is left alone unless clear_without_record "
                           "is set")
            _add_note(report, url, why + outcome)
        if transcript is None:
            # ⛔ THE ARM THAT CANNOT BE UNDONE BY A SECOND RUN. A clear is not
            # self-correcting: the damaged node no longer matches the census, so
            # the next run cannot find it to re-derive. It is therefore taken
            # ONLY when asked for by name — an empty or wrong record scope reads
            # as "no answer" for EVERY session (#10000 review P0-1), and a
            # default that destroys on that answer is one bad DSN away from
            # un-indexing a tenant's whole Source population.
            if not clear_without_record:
                report["left_alone"].append(url)
                continue
            values = _derive(None)
        elif transcript == "":
            report["empty_sessions"].append(url)
            values = _derive("")
        else:
            report["repaired"].append(url)
            values = _derive(transcript)
        if dry_run:
            continue
        # ⛔ `updatedAt` is deliberately NOT written: the reconciler must not
        # invent state a writer did not produce (#4125's rule). The pair is a
        # DERIVATION being corrected, not a new capture.
        #
        # The PAIR only, in both arms — `topics` is left exactly as it was (see
        # `_REPAIR_PAIR`'s ⛔).
        # ⛔ THE WRITE ADDRESSES ONE NODE: the census's own `id(s)`, with the
        # pair it READ on that row as the second guard (see `_REPAIR_PAIR`'s ⛔).
        # The pair alone is not an identity — two url-mates can carry a
        # byte-identical pair — and a MATCH on `{url:$url}` alone writes
        # whatever else lives at that url.
        #
        # The pair comparison is string-normalized on the Cypher side, so a row
        # whose pair value is not a string cannot be compared AT ALL. Passing the
        # raw value would let a float into `_guard_numeric_params` mid-run, and
        # normalizing it to "" would compare `12 = ''` — a write that can never
        # match, silently pinning `census_after` above zero under a note that
        # blames a concurrent change (PR #11152 review round 2, P2). Such a node
        # is NAMED in `unaddressable` instead: a pinned census, never a silent
        # one.
        old_summary = node.get(_SUMMARY)
        old_search_text = node.get(_SEARCH_TEXT)
        node_id = node.get(_NODE_ID)
        if (not isinstance(node_id, int)
                or not isinstance(old_summary, (str, type(None)))
                or not isinstance(old_search_text, (str, type(None)))):
            report["unaddressable"].append(url)
            _add_note(report, url,
                      "this candidate cannot be addressed by the write: its "
                      "census row carries no node id, or a pair value that is "
                      "not a string (the write compares the pair as text). It "
                      "counts in the census and cannot be repaired here — "
                      "repair the node by hand or delete it (#11070)")
            continue
        wrote = g.query(_REPAIR_PAIR, params={
            "url": url,
            "nid": node_id,
            "old_sum": old_summary if isinstance(old_summary, str) else "",
            "old_st": (old_search_text if isinstance(old_search_text, str)
                       else ""),
            "sum": values["sum"], "st": values["st"]})
        # ⛔ The count the hook reports must come from the WRITE, not from
        # arithmetic over the buckets: `no_record` and `left_alone` overlap
        # (`left_alone` also holds `no_session_id`/`no_reader` nodes), so
        # subtracting one from the other reported fewer repairs than were made
        # and contradicted its own parenthetical (#11152 review round 5, P2).
        # And a write that matched NOTHING is not a write: since the match is a
        # compare-and-set, an empty result is a node that changed under the run
        # (or whose only url-mates are healthy siblings). Counting it as
        # repaired is the same contradiction one layer down.
        if getattr(wrote, "result_set", None):
            report["written"].append(url)
        else:
            _add_note(report, url,
                      "the write matched NO :Source at this url whose current "
                      "pair is the one the census read — the node changed "
                      "between the census and the write (or every node at this "
                      "url is a different, healthy one), so NOTHING was "
                      "written; re-run")
    if not dry_run and found:
        # Re-MEASURED, never decremented — see the docstring's `census_after`.
        # Only when something was written: on an empty candidate set the value is
        # provably 0, and every replay engine runs this hook, so a second
        # whole-`Source` property scan for a known answer is a real cost on the
        # recovery path.
        report["census_after"] = census(g, timeout_ms=timeout_ms)
    return report


def reconcile_after_replay(projection, *, engine: str,
                           read_transcript: Callable[..., str | None] | None = None
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
            "graph-scripts/11070_repair_session_source_prose.py --repair "
            "--graph <THE CACHE GRAPH THIS REPLAY JUST WROTE> --record-dsn … "
            "--org-id … --graph-id … (#11070). ⛔ NAME THE CACHE GRAPH: "
            "without --graph the CLI reads derive_uri_graph($TORTOISE_DB_URI), "
            "which on a hosted or multi-graph box is NOT the graph --graph-id "
            "names (#11152 review round 6).",
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
                ".py --repair --graph <THE CACHE GRAPH THIS REBUILD JUST WROTE> "
                "--record-dsn … --org-id … --graph-id … (#11070). "
                "⛔ NAME THE CACHE GRAPH: without --graph the CLI reads "
                "derive_uri_graph($TORTOISE_DB_URI), which on a hosted or "
                "multi-graph box is NOT the graph --graph-id names — it finds 0 "
                "candidates and prints 'Repair complete.' over the unrepaired "
                "graph (#11152 review round 5, P1).",
                engine, len(report["candidates"]))
        else:
            logger.info(
                "%s: repaired the pre-#10852 prose pair on %d of %d session "
                "Source(s) (%d re-derived from the record, %d the record did "
                "not answer, %d left alone) (#11070)", engine,
                len(report["written"]), len(report["candidates"]),
                len(report["repaired"]), len(report["no_record"]),
                len(report.get("left_alone") or []))
    return report



# ── the FTS leg — the SECOND half of the verification (#11161) ────────────
#
# ⛔ THE CENSUS IS NOT THE TARGET. #11070's own indicator says why in one
# sentence: "the whole point is that the sentence is also in the FTS index, so
# the verification is a search that stops finding it". The census reads NODE
# PROPERTIES; the FTS index is a DIFFERENT artifact built from them
# (``projection._REQUIRED_FULLTEXT_INDEXES`` declares ``("Source", "_searchText",
# "FULLTEXT")``). A repair — or a later regression — that cleared the pair on the
# node while the indexed tokens stayed findable passes every census and leaves
# the leak live in the only way a user experiences it: the sentence is still
# retrievable. So the verification needs a query that STOPS RETURNING THE
# SENTENCE, and that is what this section provides.
#
# ⛔ AND A HIT IS NOT ALWAYS A LEAK — the distinguishing is what makes the leg
# usable rather than a red light nobody believes. FalkorDB's FTS tokenises and
# STEMS, so a probe token can come back from a node that is already repaired:
# measured on a real FalkorDB, the probe ``fridays`` still returns a node whose
# ``_searchText`` is the lexicon ``migration, plan, friday`` — the stem
# ``friday`` matches. That node's indexed text is NOT the sentence; it is the
# descriptor the post-#10852 writer produces. A leg that called that a leak would
# report a CORRECT repair as unfinished, and the way an operator silences it is
# by clearing the lexicon — the #3518 descriptor. So every hit is CLASSIFIED
# against the module's own predicates: a hit whose node still carries
# pre-lexicon prose is RESIDUE (a real leak); a hit whose node carries the
# prefix is a stem collision ONLY when (a) the indexed text IS the displayed
# suffix, EXACTLY — `summary[len(PREFIX):] == _searchText`, the derivation both
# writers produce, and `_proves_the_lexicon` skips this check because there
# `topics` proves the pair itself — AND (b) the probe token relates to one of the
# node's own lexicon tokens — otherwise it is UNEXPLAINED, which vetoes and exits
# 1. ⛔ (a) IS DERIVATION, NOT "adds no token the displayed pair lacks"
# (#11536 review round 11, P2): set CONTAINMENT was the round-9 P1 — a terse
# utterance with no stopwords is a subset of the lexicon by accident, and the
# only bucket that never vetoes absorbed it. Restoring containment here is
# restoring a measured leak path. ``lexicon`` is the only bucket that ABSORBS a hit as BENIGN, and it never
# fails a run — so it is EARNED three times over (#11536 review rounds 2 and 5),
# not assumed from the prefix.
#    ⛔ ``foreign`` ALSO DOES NOT VETO, AND IT IS NOT EXCLUDED FROM CERTIFYING
#    (#11536 review round 9, P2 — an earlier version of this note claimed both).
#    A hit on a Source OUTSIDE this population is neither residue nor proof, and
#    `_merge_probe` has no predicate that reads it: a probe whose only AFTER hits
#    are foreign certifies on the strength of the BEFORE/AFTER pair, exactly as
#    any other non-leak does. The one list it IS kept out of is the `stem_only`
#    summary, whose contract is "hits, but every one is a lexicon node" — a
#    foreign hit is not one, and that key is a `--json` report, not a verdict.
#
# ⛔ AND A ZERO FROM A DEAD INSTRUMENT IS NOT A ZERO. Three ways the leg can read
# ``0`` while measuring nothing:
#
# 1. **The probe never matched anything** — an operator-invented phrase that is
#    not in the corpus hits 0 BEFORE the repair too, so its 0 after proves
#    nothing. That is what the BEFORE/AFTER pair is for: a probe with
#    ``hits_before > 0`` that now reads 0 is the discriminating evidence, and a
#    probe whose before was ALSO 0 is reported as undiscriminating rather than
#    counted as a pass. ⛔ THE SCOPE OF THAT SENTENCE (#11536 review round 7,
#    P2): it describes the arm that TOOK a before reading, i.e. the writing arm.
#    A dry run takes none, so on that arm the control is the only evidence
#    available and a zero-before probe DOES certify — the run's printed claim is
#    narrowed to "the read answers" (see ``_report_fts``), and `--json` still
#    names the token in `undiscriminating` for a machine consumer. Stating the
#    rule without its arm made this paragraph broader than its measurement.
# 2. **The index read itself returned nothing** — an FTS circuit breaker that is
#    open, an index dropped, or a query that failed all surface to
#    ``run_fts_query`` as ``[]``. ``leg_trace`` is therefore read on EVERY probe
#    and a leg that did not run (or ran degraded) is reported as UNMEASURABLE,
#    never as a clean zero.
#    ⛔ BUT ``leg_trace`` DOES NOT COVER A DROPPED INDEX (#11536 review round 8,
#    P2), MEASURED on a real FalkorDB: querying a label with NO fulltext index
#    does not raise — it returns an EMPTY RESULT SET, so the trace records
#    ``ran=True, degraded=False, reason='empty_results'``, indistinguishable from
#    a clean miss. So of the three causes named in the line above, ``leg_trace``
#    catches TWO (an open breaker, a failed query) and NOT the third (a dropped
#    index) — that one is caught by the positive CONTROL below, because a
#    known-present lexicon token stops being findable when the index is gone.
#    The two guard different failure classes and neither substitutes for the
#    other.
#    The positive CONTROL is the second half of the same guard: one token taken
#    from a POST-fix lexicon node's own ``_searchText`` must still be findable,
#    because that proves the index answers at all.
# 3. **The read was TRUNCATED** — a hit count that reaches the read's ``LIMIT``
#    means the probe matched more nodes than were classified, so residue beyond
#    the window would be invisible. A probe that common is not a probe: it is
#    reported as ``truncated`` and it cannot certify.
#
# ⚠️ Why the leg goes through :func:`tortoise.search_engine.run_fts_query` and
# not a raw ``CALL db.idx.fulltext.queryNodes``: that function is the product's
# ONE source-FTS read, and it carries the ``documentKind IS NULL`` discriminator
# (a document IS a ``:Source``, D10/#5026) plus the escaping and the measured
# breaker. A second query here would be a second derivation of the same read.

#: The label whose FULLTEXT index the sentence leaks through. A document is a
#: ``:Source`` too, so ``run_fts_query``'s own ``documentKind IS NULL`` filter is
#: what keeps this from measuring the document corpus.
FTS_PROBE_ENTITY_TYPE = "source"

#: The read's window. Every returned hit is CLASSIFIED (see the section header),
#: so this is a classification budget, not a count: a probe that fills it is
#: common enough that its residue could hide beyond the window, and
#: :func:`probe_fts` marks that as ``truncated`` instead of certifying.
_FTS_PROBE_LIMIT = 50

#: The buckets that DISQUALIFY a probe from ``stem_only`` — every
#: :func:`classify_fts_hits` bucket EXCEPT ``lexicon``. It exists because the same
#: defect recurred three times as an enumerated predicate shortened one term at a
#: time (round 3 ``unresolved``, round 7 ``foreign``, round 8 ``truncated``), and
#: the round-8 comment claimed the predicate was "stated as the INVARIANT …
#: rather than as another name on a list" while the code was still a list — the
#: fourth occurrence was a comment asserting a shape the code did not have
#: (#11536 review round 9, P2).
#:
#: The tuple is not decorative: ``test_stem_only_EXCLUDES_every_bucket_the_
#: classifier_can_emit`` asserts that this set plus ``lexicon`` is EXACTLY the
#: classifier's key set, so adding a seventh bucket to :func:`classify_fts_hits`
#: reds the suite instead of silently restoring the over-claim.
_STEM_ONLY_FORBIDDEN = ("residue", "stale", "foreign", "unexplained", "unread")

#: The SAME columns as :data:`_SOURCE_ROWS` (so :func:`_row_props` decodes both),
#: filtered to the urls the FTS read returned — one read for the whole hit set,
#: so the classifier costs one query per probe, not one per hit.
#:
#: ⛔ ONE ROW PER MATCHING NODE, NOT PER DISTINCT URL (#11536 review round 3).
#: `:Source` declares no uniqueness on `url` (see :func:`find_session_sources_carrying_prose`),
#: so ONE url can carry TWO nodes — a pre-fix prose twin and a repaired one — and
#: the FTS index answers for BOTH. Folding these rows into a url-keyed map keeps
#: whichever row the engine returned last, which is exactly how a prose twin gets
#: classified as a healthy lexicon node and certified over.
_HITS_ROWS = (
    "MATCH (s:Source) "
    "WHERE s.url IN $urls "
    "RETURN s.url, s.sourceKind, s.summary, s._searchText, s.topics, "
    "       s.sessionId, s.turn_max_index, id(s)"
)

#: A FEW ``_searchText`` values of Sources that carry the DISPLAY PREFIX, for the
#: positive control. ⛔ NOT "post-fix lexicon nodes" (#11536 review round 10, P2):
#: the window selects on the prefix witness ALONE, and this module's own round-5
#: guard exists because that witness does not prove the indexed text is the
#: lexicon. The control's JOB only needs a token the index should hold — it
#: answers \"is the read alive\", not \"did the writer produce a lexicon\" — so the
#: selection is left as it is and every CLAIM about it is narrowed to what it
#: established. Read
#: by the WITNESS (the ``summary`` prefix), which is the same discriminator
#: :func:`carries_pre_lexicon_prose` uses — so the control queries a node the
#: lexicon writer produced, not one this repair left behind.
#:
#: ⛔ A WINDOW, NOT ``LIMIT 1`` (#11536 review round 3): the lexicon is
#: word-frequency derived (``Counter.most_common``), so the first node's tokens
#: can all be ordinary words that :func:`probe_phrase_rejection` refuses as probe
#: tokens — and one unusable node was reported as "no POST-fix lexicon node", a
#: false statement about the graph that pointed the operator at ``--no-fts-probe``.
#: How many post-fix lexicon rows the control reads. Named because the `unavailable`
#: note must say WHICH population "no usable token" is about (#11536 review round 3):
#: a bounded window, never the whole graph.
_CONTROL_LEXICON_WINDOW = 8

_CONTROL_LEXICON_ROWS = (
    "MATCH (s:Source) "
    "WHERE s.summary STARTS WITH $prefix "
    "AND s._searchText IS NOT NULL "
    "AND s.documentKind IS NULL "
    "RETURN s._searchText "
    # ⛔ ORDERED, so two runs over an unchanged graph cannot reach different
    # control verdicts from an unordered window (#11536 review round 3).
    f"ORDER BY s.url, id(s) LIMIT {_CONTROL_LEXICON_WINDOW}"
)

#: The SECOND control: does the index's field hold ANY entry at all, over the
#: SAME population ``run_fts_query(entity_type="source")`` reads (a document IS a
#: ``:Source``, so ``documentKind IS NULL`` must be repeated or a document-only
#: graph would read as "the index has entries" while the probe's own read sees
#: none — a false UNMEASURABLE).
#:
#: ⛔ THE TERM IS LOAD-BEARING AND IT IS PINNED (#11536 review, P3 at
#: `cf302453`): deleting it produced no red row in the round-8 mutation battery
#: (M14 → ``exit 0, 122 passed``), because every fake answered this read from a
#: hard-coded count. ``test_a_DOCUMENT_only_graph_does_not_make_the_index_look_
#: POPULATED`` now EVALUATES the statement's predicates over real node shapes, so
#: the deletion reds it.
_SOURCE_INDEX_ENTRIES = (
    "MATCH (s:Source) "
    "WHERE s.documentKind IS NULL "
    "AND coalesce(s._searchText, '') <> '' "
    "RETURN count(s)"
)


def _indexed_term_runs(text: str) -> list[str]:
    """The RAW alphanumeric runs of *text*, as the READ splits them.

    For every character except one this IS the index's own sense; ``_`` is that
    exception, and it is why this line no longer claims the index outright — see
    the ``_`` paragraph at the end of this docstring.

    ⛔ NOT :func:`~tortoise.sparse.tokenize_sparse_query` (#11536 review round 8,
    P1/P2). That function is the QUERY parser: it DROPS stopwords,
    sub-2-character tokens and all-digit tokens. That is right for turning an
    operator's phrase into a single probe, and WRONG for asking what text a node
    carries — the Source FULLTEXT index keeps all three. Comparing tokenised
    forms therefore made a ``_searchText`` of ``"plan friday the of and 4820"``
    read as a subset of the displayed ``"plan, friday"`` (MEASURED: guard True,
    bucket ``lexicon``, while ``run_fts_query('4820')`` still returned the node)
    — the guard's contract, reopened one filter wider.

    ⛔ ``\\W`` IS UNICODE-AWARE ON PURPOSE (#11536 review round 8, P2): an
    accented letter stays INSIDE its run, because the index holds ``résumé`` as
    ONE term (MEASURED: ``run_fts_query('résumé')`` returns the node,
    ``'resum'`` returns nothing). Splitting on ASCII ``[^a-z0-9]`` counted the
    accent as a separator, which false-blocked ``résumé``/``naïve`` with a
    reason that was factually wrong while letting ``café`` through only because
    its accent happened to be trailing.

    ⚠️ ``_`` IS SPLIT HERE, and that is NOT the index's sense of it — the index
    treats it as a WORD character (MEASURED, #12360: `queryNodes('Object',
    'café_menu')` returns a record named ``café_menu`` as ONE term). Splitting it
    is still right for THIS function, whose question is *"what text does the read
    send as terms?"* — and since #12360 an inner ``_`` makes the read emit MORE
    than the composite (``web_search`` -> ``search|web|web_search``). ⚠️ But NOT
    always: where at most one segment survives the tokeniser (``a_bc``) the read
    sends ``a_bc`` alone, so ``_`` cannot be refused by run count and
    :func:`probe_phrase_rejection` defers it to the read's own output. So the runs
    are the QUERY's runs, not the index's, for this one character; the refusal
    message says which of the two reasons fired.
    """
    return [part for part in re.split(r"[\W_]+", text.lower()) if part]


def probe_phrase_rejection(phrase: str) -> str | None:
    """Why *phrase* cannot serve as an FTS probe, or ``None`` when it can.

    ⛔ A PROBE MUST BE ONE TOKEN, AND THE REASON IS THE READ PATH'S OWN
    SEMANTICS, not tidiness. ``run_fts_query`` routes the query through
    :func:`tortoise.sparse.build_or_query`, which OR-joins a multi-token input
    (``'the second-latest PR comment'`` → ``comment|second|latest|pr``). A probe
    measured that way returns rows for ANY of its tokens — and a post-fix
    lexicon's words are ordinary words — so a CORRECT repair would still read
    "leaked", and the obvious way to silence it is to clear the lexicon, i.e.
    destroy the descriptor #3518 exists to provide. Measured on the live hosted
    graph: the single token ``the`` returns the pre-fix fixture at rank 1, and a
    phrase that occurs nowhere still returns rows once it tokenises.

    The identifier mechanism (#8933) is the second way one typed token becomes
    several (``zx-99`` → ``zx|zx99|"zx 99"``). That shape is refused by the
    TOKENISER check above, because :func:`tortoise.sparse.tokenize_sparse_query`
    defaults to ``keep_identifiers=True`` and so already emits the alias as a
    second token (``['zx', 'zx99']``) — MEASURED: over 7,700 systematic
    identifier shapes and 60,000 random strings, the ``build_or_query`` check
    below fired ZERO times. ⛔ It is kept as a BACKSTOP, not as the refusing
    mechanism, and it is labelled that way because a guard that is described as
    live while it can never fire is the over-claim this leg exists to remove: it
    starts firing the moment ``sparse`` moves alias expansion out of the tokeniser
    (or this call passes ``keep_identifiers=False``), and that is a hole the
    tokeniser check would NOT catch. ``tests/test_11070_source_prose_repair.py``
    pins that route by patching ``build_or_query``, so the backstop is reachable
    and tested rather than dead.

    ⚠️ What this CANNOT decide is RARITY — whether the token exists in the
    corpus. That is measured, not declared: the before/after pair on the writing
    arm, and the ``truncated`` flag on a probe that matches too much.

    ⛔ AND A BARE NUMBER IS ACCEPTED (#11536 review round 8, P2). The tokeniser
    drops all-digit tokens along with the stopwords, but that is the PHRASE
    parser's rule, not the index's: ``build_or_query('4820')`` takes the
    degenerate branch and returns ``'4820'``, and MEASURED ``run_fts_query('4820')``
    returns the still-prose node. A number copied out of the removed sentence is
    the most discriminating token there is, so refusing it was a false block —
    and the reason it printed ("OR-joins multi-term input") named a mechanism
    that cannot apply to a single term.

    ⛔ AND ONE TOKEN MEANS ONE WHITESPACE-FREE TOKEN, which the term count alone
    does not establish (#11536 review round 5, P2). ``tokenize_sparse_query``
    DROPS stopwords and sub-2-character words, so ``'the fridays'`` tokenises to
    the single term ``['fridays']`` and passes a count-based check — while the
    read sends ``build_or_query``'s degenerate branch, the RAW two-word string
    ``'the fridays'``, which RediSearch evaluates as an implicit AND. That is the
    OPPOSITE failure from the OR-join above, and the more dangerous one: it
    UNDER-matches, so a sentence that is still findable reads as ABSENT — and on
    the arms that can certify without a BEFORE reading (a dry run, or a
    ``--repair`` whose ``written`` is empty) nothing else contradicts it.
    """
    token = (phrase or "").strip()
    if not token:
        return "it is empty"
    # The index's own term runs — computed ONCE, because both the bare-number
    # acceptance and the shape guard ask this same question (one predicate per
    # question).
    runs = _indexed_term_runs(token)
    if len(runs) == 1 and runs[0].isdigit() and token.lower() == runs[0]:
        # A bare number — the TOKEN is the run, with no punctuation around it.
        # ⛔ ``token.lower() == runs[0]`` IS THE GUARD, not a formality (#11536
        # review round 9, P0): a digit RUN proves nothing about the TOKEN, so
        # ``'4820,'``, ``'50%'``, ``'-4820'`` and ``'(4820)'`` all have one digit
        # run while the read would send ``4820\,``, ``50\%``, ``\-4820`` and
        # ``\(4820\)`` — escaped literals the index does not hold, i.e. the
        # round-7 P0 re-opened by this very branch. MEASURED before the fix:
        # every one returned None (ACCEPTED) and built an escaped literal.
        return None
    # Lazy: `sparse` pulls the tokenizer + identifier machinery, and this module
    # is imported by every whole-journal replay engine's trailing sweep.
    from tortoise.sparse import SPARSE_STOPWORDS, build_or_query, tokenize_sparse_query

    # ⛔ ONE-TOKEN-NESS IS DECIDED BY WHAT THE READ WILL SEND (#11536 review
    # rounds 8, 9 and 11). The ASCII query tokeniser drops stopwords,
    # sub-2-character and all-digit fragments, and it splits an accented word into
    # ASCII pieces — so its TERM COUNT is not the read's. Three measured FALSE
    # BLOCKS came from treating it as one: ``4820`` (round 8), ``résumé`` (round 8)
    # and ``año``/``uña``/``eñe`` (round 11), each of which
    # ``build_or_query`` sends as ONE raw term that the index holds. A token that
    # is a SINGLE unicode run is therefore one term to the read unless a backstop
    # BELOW refuses it, so it skips the term count; what must NOT be accepted that
    # way is a SEARCH STOPWORD, which is refused by NAME instead — ``the``/``of``
    # are common words, so they match almost every node and can evidence nothing.
    single_run = len(runs) == 1
    # ⚠️ A `_`-ONLY TOKEN IS DEFERRED TO THE READ *HERE*, before the term count,
    # and the reason is measured (#12360 review round 7, P3). The deferral has to
    # cover the ZERO-term case too: `build_or_query('a_b') == 'a_b'`,
    # `'x__y' -> 'x__y'`, `'a_b_c' -> 'a_b_c'` — one term, the index's own, because
    # `_` is a word character — yet `tokenize_sparse_query` yields NO term for them,
    # so the count branch below refused them with "the FTS read OR-joins multi-term
    # input, so the probe would match on any one of them", which is false: there
    # are no OR terms. Round 6 fixed only the ≥2-segment case and its own prose
    # over-claimed as a result.
    deferred = "_" in token and not any(
        not (ch.isalnum() or ch == "_") for ch in token)
    # ⛔ …BUT NOT PAST THE COMPOSITE BOUND (#12360 review round 8, P3). Deferring
    # EVERY `_`-only token accepted `'a'*129 + '_b'`: the composites are dropped
    # above that bound, so the read sends the whole 131-char string as ONE escaped
    # literal — and MEASURED on the lane, `queryNodes('Object', <129/131/200-char
    # term>)` returns 0 rows while a 128-char term is returned, i.e. the index
    # truncates. A probe that cannot match anything is a FALSE PASS: the guard
    # would certify "the pre-fix prose is gone" over a still-present sentence.
    from tortoise.sparse import MAX_COMPOSITE_RUN_LEN

    # ⛔ MEASURE THE COMPOSITE, NOT A SEGMENT (#12360 review round 9, P2). The
    # bound is on the maximal ``[A-Za-z0-9_]`` run, and for a `deferred` token that
    # run IS the whole token (no other separator) — while ``runs`` splits ON ``_``,
    # so ``max(len(r) …)`` is strictly smaller and let through every input whose
    # length comes from `_` separation rather than from one long segment. MEASURED
    # before this: `'a'*128 + '_b'` (130) and `'a_'*70 + 'b'` (141) were ACCEPTED,
    # and against the lane their own names return ZERO rows from
    # `run_fts_query`/`queryNodes` while a 128-char name returns one — the same
    # false PASS this branch exists to prevent.
    if deferred and len(token) > MAX_COMPOSITE_RUN_LEN:
        # ⛔ THE CONSEQUENCE CLAUSE IS NOT ONE THING (#12360 review round 11, P3).
        # It used to assert "so the query is ONE literal term the index cannot
        # hold and the probe would match NOTHING", which is FALSE for any
        # over-bound token that still has segments the tokeniser keeps: MEASURED
        # ``('ab_'*43)[:129]`` builds ``ab|ab|...`` (twelve alternatives of one
        # SHORT term) — the OPPOSITE failure from a literal that matches nothing.
        # The refusal and the advice are right either way; only the reason had to
        # describe what the read actually does.
        return (f"its `_` run is longer than {MAX_COMPOSITE_RUN_LEN} characters, "
                f"the bound at which the read stops emitting the composite "
                f"(#12360) — so the composite the index actually holds is NOT "
                f"sent and the probe cannot address ONE indexed term: the read "
                f"falls back to one escaped literal or to a split of its "
                f"segments instead — pass a SHORTER token")
    if (single_run and token.lower() == runs[0]
            and token.lower() in SPARSE_STOPWORDS):
        return ("it is a search stopword; a probe must be a RARE token that "
                "appears only in the pre-fix prose")

    if not single_run and not deferred:
        tokens = tokenize_sparse_query(token)
        if len(tokens) != 1:
            return (f"it tokenises to {len(tokens)} term(s) {tokens!r}; the FTS "
                    f"read OR-joins multi-term input, so the probe would match on "
                    f"any one of them — pass a SINGLE RARE token that appears "
                    f"only in the pre-fix prose (a stopword-only phrase is not a "
                    f"probe)")
    # ⛔ AFTER the term count, because a phrase that tokenises to SEVERAL terms
    # already has the more specific (OR-join) refusal above — and this one is a
    # DIFFERENT failure that the term count is structurally blind to.
    words = token.split()
    if len(words) != 1:
        return (f"it is {len(words)} whitespace-separated words {words!r}; the "
                f"read sends the string RAW and RediSearch evaluates a "
                f"space-separated query as an implicit AND, so the probe "
                f"UNDER-matches and a still-findable sentence reads as absent "
                f"(its extra word(s) drop out of the term count, so that check "
                f"above cannot catch this) — pass a SINGLE RARE token")
    # ⛔ BY SHAPE, so the guarantee does not depend on a switch (#11536 review
    # round 6, P2). The `build_or_query` backstop below fires only while the
    # identifier mechanism is ON: with ``TORTOISE_IDENTIFIER_TOKENS=0`` (a
    # documented outage lever) MEASURED ``probe_phrase_rejection('zx-99')``
    # returned None and the read sent ``zx\-99`` — one term, no ``|`` — while
    # RediSearch splits the INDEX on non-alphanumerics, so the escaped literal
    # does not address the terms that are actually indexed. Same UNDER-match
    # class as the whitespace guard above: a token with more than one alphanumeric
    # run cannot be ONE indexed term, whatever the switch says. ``runs`` is
    # :func:`_indexed_term_runs`' unicode-aware split, so an accented letter is
    # not counted as a separator.
    # ⚠️ `_` IS NOT COVERED BY THIS GUARD, and the reason is measured (#12360
    # review round 6, P3). The premise of the check below — "more than one
    # alphanumeric run cannot be ONE indexed term" — is FALSE for `_`, because the
    # index treats `_` as a word character. For a `_`-only token the read sends
    # exactly one term and that term IS the index's own, in both arms:
    # MEASURED ``build_or_query('a_bc') == 'a_bc'`` (the segment that survives is
    # too short to stand alone, so the degenerate branch sends the literal) and
    # ``build_or_query('web_search') == 'search|web|web_search'``. Refusing the
    # first shape on this branch was a FALSE BLOCK — `a_bc`, `x_axis`, `a_web` are
    # probes that work — and the round-5 attempt to justify it ("cannot be a
    # ONE-TERM probe") was false for exactly those shapes. So a `_`-only token is
    # deferred to the read's OWN answer, :func:`tortoise.sparse.build_or_query`,
    # in the backstop below (which refuses it when the read emits several terms).
    # ⛔ Not a licence for other separators: for `-`, `,`, `:` … the index really
    # does split, so no single-term probe exists and this branch still refuses.
    has_us = "_" in token
    other_sep = any(not (ch.isalnum() or ch == "_") for ch in token)
    if len(runs) > 1 and not (has_us and not other_sep):  # == `not deferred`
        return (f"it splits into {len(runs)} alphanumeric runs {runs!r}; the "
                f"FTS index splits on every non-alphanumeric character EXCEPT "
                f"`_` (which it treats as a word character, #12360), so a token "
                f"carrying any other separator cannot address ONE indexed term "
                f"at all — pass a SINGLE RARE token (no separators other than "
                f"`_` inside it)")
    # ⛔ AND A TOKEN THE READ WILL **ESCAPE** IS NOT A PROBE (#11536 review
    # round 7, P0). `build_or_query`'s degenerate single-token branch sends the
    # token through :func:`tortoise.sparse.escape_redisearch_literal`, which
    # backslash-escapes ``[\\,;:\-|@~()\[\]{}<>=*%"$]`` — and the escaped
    # LITERAL is not a term the index holds, because the index split on that same
    # character. MEASURED on a real FalkorDB at this head, one node whose
    # ``_searchText`` contains ``friday``: probe ``friday`` -> 1 hit, probe
    # ``friday,`` -> ``friday\,`` -> **0 hits** (``(friday)`` and ``-friday``
    # likewise). That is the identical UNDER-match class as the whitespace guard
    # above, one character class wider, and it lands on the arm the module itself
    # declares has NO contradicting evidence (a dry run, or a ``--repair`` whose
    # ``written`` is empty) — so `--dry-run --assert-clean --probe-phrase
    # 'friday,'` certified over a still-retrievable sentence. ``Fridays.`` is
    # accepted BECAUSE ``.`` is not in that set, which is why the no-false-block
    # control never caught this.
    from tortoise.sparse import escape_redisearch_literal

    if escape_redisearch_literal(token) != token:
        return (f"its punctuation is escaped by the read's literal fallback "
                f"({escape_redisearch_literal(token)!r}), so the query is ONE "
                f"literal term the index does not hold — the probe would "
                f"UNDER-match and a still-findable sentence would read as "
                f"absent — pass the bare word, without punctuation")
    built = build_or_query(token)
    # ⚠️ CASED COMPARISON, because RediSearch LOWERCASES (#12360 review round 8,
    # P3): a case-variant of one term is not an OR over-match — MEASURED,
    # ``queryNodes('Object','A_B')`` and ``…'a_b'`` both return the record named
    # ``a_b`` — so refusing it was a false block with a false reason. It is kept
    # as BELT-AND-BRACES: since the same review's builder fix (below, round 10)
    # a capitalised `_` token no longer emits the duplicate at all
    # (``build_or_query('A_B') == 'A_B'``), but this comparison is what makes the
    # guard independent of the builder's dedupe staying case-aware.
    # ⛔ ONE DISTINCT ALTERNATIVE IS NOT ENOUGH — IT MUST BE **THE TOKEN ITSELF**
    # (review round 12, P2, MEASURED). ``len(alternatives) != 1`` accepts any token
    # whose read OR-joins into alternatives that merely COINCIDE — and a doubled
    # accented fragment does exactly that while addressing a term the index does
    # not hold: ``build_or_query('cafécafé') == 'caf|caf'`` (one distinct
    # alternative, ``caf``) while the index holds ``cafécafé`` as ONE term, so
    # ``queryNodes('Source','cafécafé')`` returns the node and ``'caf|caf'``
    # returns NOTHING. Accepting it certified ``ran=True, leaked=[]`` over a
    # still-present sentence — the FALSE PASS this guard exists to prevent, in
    # the fail-open direction, and it was introduced by the round-8 narrowing
    # (``origin/main`` REFUSED ``cafécafé``; verified by loading the base
    # module). Same shape: ``überüber`` -> ``ber|ber``, ``testétest`` ->
    # ``test|test``. So the requirement is the strict one: the emitted set must
    # BE the token, case-folded. That still accepts every shape the file pins
    # (``a_bc``, ``x_axis``, ``A_B``, ``año``, ``résumé``, ``friday.`` — the read
    # sends the token itself) and still refuses ``web_search`` (three distinct
    # terms) and the doubled-fragment class.
    alternatives = {t.lower() for t in built.split("|")}
    if alternatives != {token.lower()}:
        return (f"the read expands it into several terms ({built!r}) — either "
                f"the identifier mechanism (a digit/dash run), the #12360 "
                f"`_`-composite tail (`TORTOISE_UNDERSCORE_TERMS`), or the "
                f"ASCII tokeniser splitting an accented word; pass the plain "
                f"token")
    return None


#: The ONE predicate for "this FTS row's url can be classified".
#:
#: ⛔ TWO PREDICATES FOR ONE QUESTION IS THE BUG (#11536 review round 3, P1).
#: :func:`source_fts_probe` kept every ``isinstance(row[0], str)`` row in ``urls``
#: while :func:`classify_fts_hits` then dropped the falsy ones, so a hit whose
#: ``node.url`` is the EMPTY STRING was counted in ``hits``, counted as resolved
#: (``unresolved == 0``) and reached NO bucket — and the fold certified over it with
#: an answering control. MEASURED on the round-2 tree::
#:
#:     probe   hits=1  urls=['']  unresolved=0
#:     split   all SIX buckets empty (residue, lexicon, stale, foreign, unexplained,
#:             unread) — NOT a five-key dict: `unexplained` is the stem-proof bucket
#:     fold    certifies=True  vetoed=None
#:
#: It is the same class as the non-string url the round-2 fix closed, one
#: predicate short: "a hit that nothing examined is not evidence" applies to every
#: row the classifier cannot address, however it fails to be addressable. Both
#: sides call THIS, so they cannot drift a third time.

def _addressable(value: Any) -> bool:
    """True when *value* is a non-empty string the classifier can resolve."""
    return isinstance(value, str) and bool(value)


def source_fts_probe(g, phrase: str, *,
                     timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Ask the Source FTS index whether *phrase* is still findable.

    Returns ``{"phrase", "hits", "urls", "unresolved", "truncated", "ran",
    "degraded", "reason"}``. ``hits`` counts the rows the read returned (capped at
    :data:`_FTS_PROBE_LIMIT`), so ``0`` reads as "not findable"; ``urls`` is what
    the classifier resolves, and ``truncated`` says the window filled — i.e. the
    count is a FLOOR and residue could be hiding past it.

    ``ran``/``degraded``/``reason`` come from the read path's own ``leg_trace``,
    and they cover DIFFERENT failures: a breaker-open leg reports
    ``ran=False``, and a query that raised reports ``degraded=True``. ⛔ They do
    NOT cover a missing index (#11536 review round 8, P2) — MEASURED on a real
    FalkorDB, querying a label with no fulltext index returns an EMPTY RESULT SET
    with no exception, so the trace says ``ran=True, degraded=False,
    reason='empty_results'``, exactly as a clean miss does. The positive control
    is what detects that class; this row cannot.

    ``timeout_ms`` is CLAMPED to the server's own cap: the canonical docker lane
    runs ``TIMEOUT_MAX 30000``, and an unclamped 60 000 raised ``ResponseError``
    on every run of the census before #11152's review found it — the same
    deployment setting applies to this query.
    """
    from tortoise.search_engine import run_fts_query  # lazy (see module docs)

    trace: list[dict] = []
    rows = run_fts_query(
        g, phrase, entity_type=FTS_PROBE_ENTITY_TYPE, limit=_FTS_PROBE_LIMIT,
        timeout_ms=_effective_timeout_ms(g, timeout_ms), leg_trace=trace)
    leg = next((e for e in trace if e.get("leg") == "fts"), {})
    urls = [row[0] for row in rows or () if row and _addressable(row[0])]
    return {
        "phrase": phrase,
        "hits": len(rows or ()),
        "urls": urls,
        # ⛔ A HIT WHOSE URL IS NOT ADDRESSABLE IS NOT INVISIBLE (#11536 review
        # round 2, P2; round 3 closed the second door). `urls` drops it, so it
        # would be neither residue, lexicon, stale, foreign nor unread — a hit in
        # `hits` that no bucket sees, and the run would certify around it. The
        # census has the same first-class bucket for the same reason
        # (`unaddressable`). The predicate is :func:`_addressable`, the SAME one
        # `classify_fts_hits` filters on, so a row cannot be "resolved" here and
        # dropped there — which is exactly how the EMPTY-STRING url certified.
        "unresolved": max(0, len(rows or ()) - len(urls)),
        "truncated": len(rows or ()) >= _FTS_PROBE_LIMIT,
        "ran": bool(leg.get("ran")),
        "degraded": bool(leg.get("degraded")),
        "reason": leg.get("reason"),
    }


#: A hit on an episodic session `:Source` that carries NO text at all. The index
#: is declared on `_searchText`, so a match on such a node is a STALE INDEX ENTRY
#: — the pair was cleared and the token survived — which is THE leak #11161 names.
#: Measured on FalkorDB 4.20.6 the entry does go away with the property, so this
#: is fail-closed rather than a live population; it must not certify as a "stem
#: collision" if an engine (or a re-index) ever leaves one behind.
#:
#: ⚠️ AND `lexicon` MUST MEAN **PROVEN**, not "not residue" — which is the bug this
#: bucket was split out to close (#11536 review round 2, P1). "Not pre-fix prose"
#: is not "the post-fix lexicon": a CLEARED node is neither, and folding it into
#: `lexicon` let the exact shape the leg exists for certify.

def _carries_the_display_prefix(props: Mapping[str, Any]) -> bool:
    """True when ``summary`` is the display value every post-#10852 writer emits.

    ONE predicate for "which proof did :func:`_is_proven_lexicon` use", because
    the two arms are NOT interchangeable: the prefix proves only the DISPLAYED
    pair, while the index is declared on ``_searchText`` — so
    :func:`_hit_node_bucket` has to know the difference (#11536 review round 5).
    """
    summary = props.get(_SUMMARY)
    return isinstance(summary, str) and summary.startswith(
        SESSION_SOURCE_SUMMARY_PREFIX)


def _is_proven_lexicon(props: Mapping[str, Any]) -> bool:
    """True only when the node's pair is PROVABLY the post-#10852 derivation.

    Two proofs, both of them the repo's own: the display PREFIX on ``summary``
    (:data:`SESSION_SOURCE_SUMMARY_PREFIX` — the witness every writer emits), or
    :func:`_proves_the_lexicon`'s exact ``_searchText == ", ".join(topics)`` for a
    ``record-restore``d node, which has no ``summary`` at all.

    ⚠️ The prefix arm proves the DISPLAY value only — see
    :func:`_indexed_text_is_the_displayed_pair` for the half it does not.
    """
    return (_carries_the_display_prefix(props)
            or _proves_the_lexicon(props))


def _indexed_text_is_the_displayed_pair(
        props: Mapping[str, Any]) -> bool:
    """True when ``_searchText`` IS the lexicon the DISPLAYED pair names.

    ⛔ THE PREFIX PROVES THE DISPLAY VALUE, NOT THE INDEX (#11536 review round 5,
    P2). :func:`_is_proven_lexicon`'s prefix arm reads only ``summary``, while the
    FTS index is declared on ``_searchText`` — and the shape that separates them
    is exactly the leak this leg exists for: a node whose ``summary`` carries
    :data:`SESSION_SOURCE_SUMMARY_PREFIX` while ``_searchText`` still holds the
    pre-fix PROSE. :func:`_probe_explains_the_lexicon` then asks whether the probe
    token occurs in ``_searchText``, which on that shape answers YES for the
    prose's OWN words — so the leak would be labelled ``lexicon``, the one bucket
    that never vetoes, and the run would certify over a still-retrievable
    sentence.

    ⛔ AND IT IS DERIVATION, NOT CONTAINMENT, AND NOT THE QUERY TOKENISER
    (#11536 review rounds 6 and 9, P1). Three drafts of this guard were each one
    filter too loose, and all three were measured:

    * :func:`_stem_key` folded both sides, so an index still holding ``planned``
      read as "explained" by a display pair carrying ``planning``;
    * ``tokenize_sparse_query`` drops stopwords, sub-2-character and all-digit
      tokens that the INDEX keeps, so ``"plan friday the of and 4820"`` read as a
      subset of ``"plan, friday"`` while ``run_fts_query('4820')`` still returned
      the node;
    * set CONTAINMENT is necessary but not sufficient, because the lexicon comes
      from the same transcript as the prose. On
      ``summary = PREFIX + "migration, plan, friday, invoice, service, ready"`` /
      ``_searchText = "migration plan friday invoice service ready"`` (still the
      PROSE) containment passed, the bucket was ``lexicon``, and the third proof
      could not help — on a prose index the text that matched IS the prose.

    The writers make an EXACT relation: :func:`tortoise.sdk._source_search_text`
    returns ``", ".join(topics)`` and the display value is
    ``PREFIX + ", ".join(topics)``, so the summary's SUFFIX is what
    ``_searchText`` must BE. That is the same conclusion
    ``cache_projection._episodic_source_search_text_is_the_lexicon`` reached.

    Polarity: fail-CLOSED. A pair whose two halves are not the same derivation is
    not proven, and the caller vetoes it as ``unexplained``: a false veto is
    re-measured, a false certification is the leak.
    """
    summary = props.get(_SUMMARY)
    if not isinstance(summary, str) or not summary.startswith(
            SESSION_SOURCE_SUMMARY_PREFIX):
        return False
    search_text = props.get(_SEARCH_TEXT)
    if not isinstance(search_text, str) or not search_text:
        # No indexed text at all: nothing here could BE the displayed lexicon,
        # and a blank index cannot carry the sentence.
        return False
    return summary[len(SESSION_SOURCE_SUMMARY_PREFIX):] == search_text


#: The English suffixes :func:`_stem_key` folds when comparing a probe token with
#: a node's own lexicon tokens. The Source FTS index stems (FalkorDB/Snowball)
#: and this repo ships no stemmer, so the comparison is an APPROXIMATION — see
#: :func:`_probe_explains_the_lexicon` for why its polarity is the safe one.
_STEM_SUFFIXES = ("ings", "ing", "ies", "ied", "es", "ed", "s", "d")


def _stem_key(token: str) -> str:
    """A small, DOCUMENTED approximation of the index's English stemmer.

    `fridays` -> `friday` (the measured stem collision), `planning` -> `plan`
    (doubled final consonant), `stories` -> `story`. It is deliberately NOT a
    real stemmer: the only consumer requires an EQUALITY test between a probe
    token and a node's own lexicon tokens, and an approximation that folds too
    little fails toward VETO (see :func:`_probe_explains_the_lexicon`).
    """
    text = str(token or "").strip().lower()
    if len(text) > 5 and text.endswith(("ies", "ied")):
        text = text[:-3] + "y"
    else:
        for suffix in _STEM_SUFFIXES:
            if len(text) - len(suffix) >= 4 and text.endswith(suffix):
                text = text[: -len(suffix)]
                break
    if len(text) >= 4 and text[-1] == text[-2] and text[-1] not in "aeiou":
        text = text[:-1]
    return text


def _probe_explains_the_lexicon(props: Mapping[str, Any], phrase: str) -> bool:
    """True when THIS node's own indexed text can explain the probe's hit.

    ⛔ THE BENIGN LABEL MUST BE **PROVED**, NOT DEFAULTED (#11536 review round
    2, P1). A node can prove it carries the post-fix lexicon
    (:func:`_is_proven_lexicon`) and STILL be the leak: if the index entry
    outlived a property update, the token that matched is one of the REMOVED
    prose's, not one of the lexicon's — and `lexicon` is a bucket that never
    vetoes. So the hit is a stem collision only when the probe token relates to a
    token of the text the index is declared on (``_searchText``), compared through
    :func:`_stem_key`.

    ⚠️ The fold is an approximation, and its failure direction is deliberate: a
    pair it cannot relate lands in ``unexplained``, which VETOES — the safe
    polarity for a verification gate (a false veto is re-measured; a false
    certification is the leak #11161 exists for). The opposite error, relating
    two tokens the engine does NOT relate, would need an English suffix fold to
    invent a match, and where it does match the engine's stemmer matches too — so
    it cannot hide a stale entry behind a token whose stem is absent.
    """
    text = props.get(_SEARCH_TEXT)
    if not isinstance(text, str) or not text.strip():
        return False
    # ⛔ THE INDEX'S TERMS, NOT THE QUERY PARSER'S (#11536 review round 10, P1).
    # `tokenize_sparse_query` DROPS all-digit, sub-2-character and stopword
    # tokens, so a bare-NUMBER probe — ACCEPTED since round 8 — could never be
    # explained by a numeric lexicon. MEASURED: a correctly repaired node with
    # `_searchText="100, 200, 4820"` classified probe `4820` as `unexplained`,
    # and the refusal blamed "a surviving pre-fix index entry" that the run's own
    # data falsifies — on the documented runbook path
    # (`--repair --probe-phrase <a number from the removed sentence>`) a correct
    # repair exited 1 and the implied remedy was clearing the #3518 descriptor.
    # The STEM FOLD stays: relating a PROBE token to a lexicon token is exactly
    # where the index's own stemming is what answered the query. This is the same
    # QUERY-parser-vs-INDEX-tokeniser split round 8 fixed in
    # :func:`_indexed_text_is_the_displayed_pair`.
    indexed = {_stem_key(token) for token in _indexed_term_runs(text)}
    asked = _indexed_term_runs(phrase)
    if not indexed or not asked:
        return False
    return all(_stem_key(token) in indexed for token in asked)


def _has_no_text(props: Mapping[str, Any]) -> bool:
    """True when the node's two indexed text columns both carry nothing."""
    for key in (_SUMMARY, _SEARCH_TEXT):
        value = props.get(key)
        if isinstance(value, str) and value.strip():
            return False
    return True


def classify_fts_hits(g, urls, *, phrase: str,
                      timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Split *urls* into RESIDUE (still pre-fix prose) and LEXICON (repaired).

    ⛔ THE SPLIT IS THE WHOLE POINT OF THE LEG (see the section header): the index
    stems, so a probe token can come back from a node whose indexed text is
    already the topic lexicon — a correct repair, not a leak. The classifier
    applies the module's own :func:`carries_pre_lexicon_prose`, so the two halves
    cannot drift from the census.

    Returns ``{"residue": [...], "lexicon": [...], "stale": [...], "foreign": [...],
    "unexplained": [...], "unread": [...]}`` — SIX keys, and every one of them
    is a bucket the caller folds (#11536 review round 3, P2: this list said five
    and omitted ``unexplained``, the very bucket the paragraph below exists to
    justify, so a reader implementing the fold from the docstring would have
    dropped the veto). A url the read did not return is ``unread`` — a hit that
    could not be classified is NOT evidence either way, and the caller must not
    fold it into ``lexicon``.

    ⛔ FOUR buckets, because "not residue" is not "lexicon" (#11536 review round 2,
    P1): a CLEARED node carries neither the prose nor the lexicon, so a two-way
    split put the very leak this leg exists for into the benign bucket. ``stale`` is
    that shape (an episodic session Source with NO text at all — the index entry
    outlived the property), and ``foreign`` is a Source outside this population (a
    non-episodic source with its own `_searchText`, whose match says nothing about
    this repair).

    ⛔ AND ``lexicon`` IS PROVED THRICE (#11536 review round 2, P1; round 5, P2):
    the node must prove the post-fix pair (:func:`_is_proven_lexicon`); when that
    proof is the display PREFIX alone, the INDEXED text must add no token the
    displayed pair lacks (:func:`_indexed_text_is_the_displayed_pair`) — a
    prefix ``summary`` over a still-prose ``_searchText`` is the leak, not a
    collision; and the probe token must relate to one of that node's OWN lexicon
    tokens (:func:`_probe_explains_the_lexicon`). A node can prove the lexicon and
    still be a stale-index leak, because what matched is the index entry the
    update was supposed to remove. ``unexplained`` is that shape — neither
    provably the prose nor provably a stem collision — and it vetoes.

    ⛔ ``phrase`` IS REQUIRED, NOT DEFAULTED (#11536 review round 7, P2). Third
    proof is the one that catches the survived-index-entry leak, so an omitted
    ``phrase`` silently restored the round-2 P1 that split the bucket: measured
    on a node carrying the prefix ``summary`` and NO ``_searchText`` (an index
    entry that outlived the property) ``_hit_node_bucket(props, None)`` returned
    ``lexicon`` while ``_hit_node_bucket(props, 'friday')`` returned
    ``unexplained``. Every in-tree caller already passes it; making it required
    means no future one can quietly drop two of the three proofs.

    ⛔ ONE URL CAN CARRY SEVERAL NODES, AND EVERY ONE OF THEM IS CLASSIFIED
    (#11536 review round 3, P1): ``:Source`` declares no uniqueness on ``url``, and
    this module already treats a url-mate pair as reachable — so folding the hit
    rows into a url-keyed map kept whichever node the engine returned LAST and
    read a url whose other node still carried the pre-fix prose as a benign stem
    collision. The probe returned BOTH rows; the prose was still retrievable; the
    run certified. A url is now classified from ALL of its nodes: any residue wins
    (the leak is live, whichever twin answers), agreement is that bucket, and
    DISAGREEMENT is ``unexplained`` — a url whose nodes cannot be labelled
    consistently is not provably benign.
    """
    # ⛔ DE-DUPED: the probe returns one row PER HIT, so a url with two nodes
    # (or two index entries) arrives twice — and a bucket that lists it twice
    # makes a count read as "two leaks" for one url. The fold is per URL, so the
    # url is asked once (#11536 review round 3). The addressability predicate is
    # :func:`_addressable` — the SAME one :func:`source_fts_probe` counts with, so
    # a row cannot be "resolved" there and dropped here (that drift is how the
    # empty-string url certified).
    wanted = list(dict.fromkeys(u for u in (urls or ()) if _addressable(u)))
    out: dict = {"residue": [], "lexicon": [], "stale": [], "foreign": [],
                 "unexplained": [], "unread": []}
    if not wanted:
        return out
    try:
        rows = g.query(_HITS_ROWS, params={"urls": wanted},
                       timeout=_effective_timeout_ms(g, timeout_ms)).result_set
    except Exception:  # noqa: BLE001, RUF100 — an unreadable classification is
        # reported as `unread`, never raised: this runs inside the verification
        # step, where an exception would be a crash instead of a verdict.
        logger.debug("#11070: the FTS hit classification read failed",
                     exc_info=True)
        out["unread"] = list(wanted)
        return out
    # ⛔ EVERY node at the url, not one per url (see the docstring).
    props_by_url: dict[str, list[dict]] = {}
    for row in rows or ():
        props = _row_props(row)
        if props is not None and isinstance(props.get("url"), str):
            props_by_url.setdefault(props["url"], []).append(props)
    for url in wanted:
        group = props_by_url.get(url) or ()
        if not group:
            out["unread"].append(url)
            continue
        labels = [_hit_node_bucket(props, phrase) for props in group]
        if "residue" in labels:
            # One twin still carries the sentence: the leak is live, whichever
            # node the engine happened to return last.
            out["residue"].append(url)
        elif len(set(labels)) == 1:
            out[labels[0]].append(url)
        else:
            # The nodes at this url disagree (say a stale twin and a proven
            # lexicon one). Nothing proves the benign reading, and the leg's own
            # polarity is "a false veto is re-measured; a false certification is
            # the leak" — so it vetoes.
            out["unexplained"].append(url)
    return out


def _hit_node_bucket(props: dict, phrase: str) -> str:
    """One hit-row's bucket — the SAME predicates the census uses.

    Split out so the per-node question and the per-url fold cannot drift: the
    fold only ever chooses between these labels, it never re-derives one.
    """
    if carries_pre_lexicon_prose(props):
        return "residue"
    if _is_proven_lexicon(props):
        # ⛔ THE PREFIX ARM PROVES THE DISPLAY PAIR, NOT THE INDEX (#11536 review
        # round 5, P2): a prefix `summary` over a still-prose `_searchText` would
        # otherwise be explained by its own PROSE and land in `lexicon`, the
        # bucket that never vetoes.
        if (_carries_the_display_prefix(props)
                and not _indexed_text_is_the_displayed_pair(props)):
            return "unexplained"
        if _probe_explains_the_lexicon(props, phrase):
            return "lexicon"
        return "unexplained"
    if is_episodic_source_node(props) and _has_no_text(props):
        return "stale"
    return "foreign"


def source_index_is_empty(g, *,
                          timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> bool | None:
    """True when NO Source over the probe's own population carries ``_searchText``.

    ⛔ THE SECOND CORROBORATION, AND THE ONE THAT MAKES THE VERIFY STEP USABLE
    ON A CLEAR-ONLY GRAPH (#11161 review, P2). The positive control needs a
    POST-fix lexicon node, and a graph that has only ever been CLEARED has none
    — so a correctly repaired clear-arm population had no way to certify: the
    probe read 0, nothing corroborated it, and the run exited 1. The documented
    remedy then pointed the operator at ``--no-fts-probe``, i.e. at the weaker
    evidence this leg exists to stop certifying.

    ⛔ WHAT THIS COUNT ACTUALLY ANSWERS, stated as what it measures (#11536
    review: the sentence that used to stand here was the INVERSE of its own
    query). :data:`_SOURCE_INDEX_ENTRIES` counts nodes over this population whose
    ``_searchText`` is NON-EMPTY, so it answers *"does any Source carry the field
    the index is DECLARED on"* — Cypher has no verb to count INDEX ENTRIES, and
    nothing here tries to. A STALE entry is a node the pair was CLEARED from,
    i.e. one with NO ``_searchText`` at all: it contributes **0** to this count
    and can never make it non-empty.

    The corroboration is still sound, for the reasons that ARE measured: a
    surviving entry is caught by the probe itself — it classifies ``stale`` and
    vetoes, see :func:`classify_fts_hits`.
    ⛔ AND NOT BY ``leg_trace`` (#11536 review round 8, P2): this docstring used
    to claim "a dead read is caught by ``leg_trace`` (``degraded`` ⇒
    ``unmeasurable``) before this arm is consulted. So ``True`` means: … AND the
    read ran". MEASURED on a real FalkorDB and false at the second clause: a
    missing Source fulltext index does not raise, it returns an empty result set,
    so the trace reports ``ran=True, degraded=False, reason='empty_results'`` and
    ``unmeasurable`` never fires. This arm therefore claims only what it counted —
    that the population carries nothing for the index to build from — and a DEAD
    READ is caught by the positive control, not here. ``None`` still means the
    count itself failed: unreadable is not empty.
    """
    try:
        rows = g.query(_SOURCE_INDEX_ENTRIES,
                       timeout=_effective_timeout_ms(g, timeout_ms)).result_set
    except Exception:  # noqa: BLE001, RUF100 — see the `None` contract above
        logger.debug("#11070: the Source index-entry read failed", exc_info=True)
        return None
    for row in rows or ():
        count = row[0] if row else None
        if isinstance(count, int) and not isinstance(count, bool):
            return count == 0
    # A row that does not carry an integer count is NOT a measured zero.
    return None


def lexicon_control_read(g, *,
                         timeout_ms: int = CACHE_READ_TIMEOUT_MS
                         ) -> tuple[str | None, str | None]:
    """``(token, failure)`` — one POST-fix lexicon token, or why there is none.

    ⛔ A FAILED READ IS NOT AN EMPTY WINDOW (#11536 review round 5). Collapsing
    both into ``None`` let the caller report "no usable token within the window the
    control reads" about a window the run never read — the same over-claim as its
    two predecessors, one order smaller. The failure is returned as a reason so the
    caller can say which of the two happened; the module's own `source_index_is_empty`
    already distinguishes them for the other control arm.

    The positive control itself: a token of a Source that carries the display
    prefix must still be findable, which is what makes the probes' zero evidence
    that the index ANSWERS rather than evidence that the read is dead.

    ⛔ WHAT IT DOES NOT PROVE (#11536 review round 10, P2): the window selects on
    the display-PREFIX witness alone, and this module's own round-5 guard exists
    because that witness does not establish that the indexed text IS the lexicon.
    The control needs only a token the index ought to hold, so the selection is
    unchanged; the CLAIM is narrowed to liveness.
    """
    try:
        rows = g.query(_CONTROL_LEXICON_ROWS,
                       params={"prefix": SESSION_SOURCE_SUMMARY_PREFIX},
                       timeout=_effective_timeout_ms(g, timeout_ms)).result_set
    except Exception:  # noqa: BLE001, RUF100 — a control that cannot be read is
        # REPORTED, never raised: the probe result is the finding, and an
        # exception here would turn "unmeasurable" into a crash.
        logger.debug("#11070: the FTS control read failed", exc_info=True)
        return None, "the control's lexicon read FAILED"
    for row in rows or []:
        text = row[0] if row else None
        if not isinstance(text, str):
            continue
        for token in text.split(","):
            candidate = token.strip()
            if candidate and probe_phrase_rejection(candidate) is None:
                return candidate, None
    return None, None


def lexicon_control_token(g, *,
                          timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> str | None:
    """One token from a POST-fix lexicon node, or ``None`` when there is none.

    The token alone, discarding WHY there is none — :func:`lexicon_control_read` is
    the verb a caller needing the distinction should use.
    """
    return lexicon_control_read(g, timeout_ms=timeout_ms)[0]


def probe_fts(g, phrases, *, control: bool = True,
              timeout_ms: int = CACHE_READ_TIMEOUT_MS) -> dict:
    """Measure the Source FTS index for every probe phrase, plus the control.

    ⛔ PRECONDITION — EACH PHRASE MUST TOKENISE TO EXACTLY ONE TERM (#11536 review
    round 9, P2). This is enforced here, not only at the CLI's ARGV: the read goes
    through :func:`tortoise.sparse.build_or_query`, which OR-joins a multi-token
    input, so a phrase returns rows for ANY of its words and a CORRECT repair
    reads as ``leaked`` — the operator's likely response to which is to clear the
    post-fix lexicon, i.e. destroy the ``#3518`` descriptor. A library caller
    reaching this exported verb directly used to get that false ``leaked`` with no
    guard at all; :func:`probe_phrase_rejection` now gates it and a rejected
    phrase raises ``ValueError`` (fail closed, loudly) rather than measuring
    something the caller did not ask for.

    Returns::

        {"ran": bool,                # at least one probe was MEASURED — a
                                     # breaker-open leg makes this False, exactly
                                     # as it makes that probe's own `ran` False
         "entity_type": "source", "limit": 50,
         "probes": [{phrase, hits, urls, unresolved, truncated, ran, degraded,
                     reason, residue, lexicon, stale, foreign, unexplained,
                     unread}, ...],
         "leaked": [phrase, ...],          # residue still findable — a LEAK
         "stale": [phrase, ...],           # a hit on a CLEARED node — the index
                                           # entry outlived the pair (vetoes)
         "stem_only": [phrase, ...],       # hits, but every one is a lexicon node
         "unexplained": [phrase, ...],     # hits nothing could PROVE benign
                                           # (a survived pre-fix entry, or a stem
                                           # this check cannot fold) — vetoes
         "unmeasurable": [phrase, ...],    # the leg did not run / is degraded
         "truncated": [phrase, ...],       # the window filled — a FLOOR, not a 0
         "unresolved": int,                # summed hits whose url was not
                                           # ADDRESSABLE — examined by no bucket
         "control": {"mode": "probe" | "empty-index" | "unavailable",
                     # `probe`:      a known-present lexicon token was measured
                     #               (the ONLY arm that carries ran/degraded)
                     # `empty-index`: no Source carries `_searchText` at all —
                     #               structurally consistent, and the ONLY arm
                     #               where ok=True with hits=0
                     # `unavailable`: nothing corroborates the probe's zero
                     "ok", "hits",          # hits is None when not measured
                     "note",
                     # ⛔ `ran`/`degraded`/`reason` ARE PRESENT ON `probe` ONLY and
                     # ABSENT on ``empty-index``/``unavailable`` (#11536 review
                     # round 7, P2). On ``empty-index`` they were LITERAL
                     # constants — ``"ran": True, "degraded": False`` — beside a
                     # note that said "and the read RAN", on the ONE arm that
                     # runs no FTS read at all: the measurement behind it is
                     # ``source_index_is_empty``, a NODE-PROPERTY count. A `--json`
                     # consumer therefore could not tell a corroborating `probe`
                     # (measured) from this one (a constant) — the same over-claim
                     # this leg spent six rounds removing.
                     "ran", "degraded", "reason",   # `probe` ONLY
                     # ``measured`` = what this arm actually counted, so the
                     # `empty-index` consumer is told a population was read and
                     # the FTS read was not exercised.
                     "measured"} | None}          # `empty-index`: "node-property"
    # ⛔ ``mode`` IS LOAD-BEARING, not decoration (#11536 review round 5): the
    # three arms are distinguishable ONLY by it — a `probe` control can be ok True
    # or False, `empty-index` is ok True with hits 0, `unavailable` carries
    # hits None — so a `--json` consumer that read only {ok, hits, note} could not
    # tell a corroborating control from a structurally empty index.

    ⛔ EVERY KEY ABOVE IS RETURNED, and every returned key is listed here (#11536
    review round 3, P2). This docstring used to describe five of them: it omitted
    top-level ``stale``/``unexplained``/``unresolved`` (the three VETOING
    aggregates) and the probe row's ``unresolved``. A consumer reading the
    contract would not know the vetoes existed — the same class as the
    five-key ``classify_fts_hits`` list, on the report the fold is built from.
    ⛔ ``ran`` IS ``any(probe["ran"] AND NOT probe["degraded"])`` — neither
    ``bool(phrases)`` nor ``any(probe["ran"])`` (#11536 review round 3 P2, and
    round 8 P2). `bool(phrases)` reported True on a breaker-open leg where
    ``probes[0]["ran"]`` was False. But ``any(probe["ran"])`` was STILL wrong on
    the two DEGRADED exits, because the read path (``run_fts_query``) records
    ``ran=True, degraded=True`` for BOTH ``index_missing`` and ``query_failed``:
    measured on the shipped path, an `index_missing` trace gives
    ``report["ran"] is True`` on a leg that measured nothing, which is exactly
    what this key is documented NOT to mean. Requiring ``not degraded`` makes the
    documented meaning true.

    ⚠️ ``control`` is only meaningful AFTER a repair has written a lexicon: on a
    graph with no post-fix node it is ``{"ok": False, "hits": None, "note": …}``
    and the caller reports why rather than treating the absence as a failure of
    the repair itself.
    """
    measured: list[dict] = []
    for phrase in phrases:
        rejection = probe_phrase_rejection(phrase)
        if rejection is not None:
            raise ValueError(
                f"probe phrase {phrase!r} cannot measure the Source FTS index: "
                f"{rejection}")
        got = source_fts_probe(g, phrase, timeout_ms=timeout_ms)
        split = classify_fts_hits(g, got["urls"], phrase=phrase,
                                  timeout_ms=timeout_ms)
        got.update(split)
        measured.append(got)
    report: dict = {
        # ⛔ THE DOCUMENTED MEANING, IMPLEMENTED (#11536 review round 3 P2, and
        # round 8 P2): "at least one probe was MEASURED" is
        # `any(p["ran"] and not p["degraded"])`, not "phrases were asked for"
        # and not `any(p["ran"])`. `bool(measured)` reported True on a
        # breaker-open leg where every probe's own `ran` was False. `any(p["ran"])`
        # then stayed True on the two DEGRADED exits, because `run_fts_query`
        # records `ran=True, degraded=True` for BOTH `index_missing` and
        # `query_failed` — a report that says the instrument answered when it
        # measured nothing.
        "ran": any(p["ran"] and not p["degraded"] for p in measured),
        "entity_type": FTS_PROBE_ENTITY_TYPE,
        "limit": _FTS_PROBE_LIMIT,
        "probes": measured,
        # ⛔ A HIT ON A STALE INDEX ENTRY IS THE LEAK, not a stem collision: the
        # node carries no text at all and the index still answered (#11536 review
        # round 2, P1). It is named apart from `residue` so the refusal can say
        # which shape was found — "the pair is still on the node" and "the pair is
        # gone but the index entry survived" are different repairs.
        "leaked": [p["phrase"] for p in measured if p["residue"]],
        "stale": [p["phrase"] for p in measured if p["stale"]],
        # ⛔ `unexplained` IS NOT `stem_only` (#11536 review round 2, P1): a hit
        # on a proven-lexicon node whose own tokens do NOT explain the probe is
        # the stale-entry candidate, not a benign collision.
        "stem_only": [p["phrase"] for p in measured
                      if p["hits"] and not p["truncated"]
                      and not p.get("unresolved")
                      and not any(p[bucket] for bucket in
                                  _STEM_ONLY_FORBIDDEN)],
        "unexplained": [p["phrase"] for p in measured if p["unexplained"]],
        "unmeasurable": [p["phrase"] for p in measured
                         if not p["ran"] or p["degraded"]],
        "truncated": [p["phrase"] for p in measured if p["truncated"]],
        "unresolved": sum(p.get("unresolved") or 0 for p in measured),
        "control": None,
    }
    if control:
        token, control_failure = lexicon_control_read(g, timeout_ms=timeout_ms)
        if control_failure is not None:
            # ⛔ A FAILED READ IS NOT AN EMPTY WINDOW (#11536 review round 5): this
            # arm used to fall through to "no usable token within the window",
            # i.e. it claimed to have READ a window it never read.
            report["control"] = {
                "mode": "unavailable", "ok": False, "hits": None,
                "note": f"{control_failure} — an unreadable instrument is not an "
                        "empty one, so nothing here corroborates the probe's zero",
            }
        elif token is not None:
            # ⛔ The TOKEN IS NOT ECHOED. It is a transcript-derived word (the
            # capture's own topic lexicon), and the dry run's report is
            # deliberately redacted by whitelist so no piece of the utterance
            # reaches stdout or `--json`; only the measured COUNT is reported.
            got = source_fts_probe(g, token, timeout_ms=timeout_ms)
            report["control"] = {
                "mode": "probe",
                "ok": bool(got["hits"]), "hits": got["hits"],
                "ran": got["ran"], "degraded": got["degraded"],
                "reason": got["reason"],
                # ⛔ THE NOTE MUST MATCH THE MEASUREMENT (#11536 review round 11,
                # P2): it asserted "the READ answers" unconditionally, so on
                # hits == 0 — the ONE case the control exists to detect — the
                # machine-readable note said the opposite of the datum beside it
                # (and of the `uncorroborated` refusal the same run prints).
                "note": (
                    "one token of a Source whose `summary` carries the display "
                    "prefix (the witness the lexicon writer emits) — the window "
                    "selects on that witness, so a HIT proves the READ answers"
                    if got["hits"] else
                    "one token of a Source whose `summary` carries the display "
                    "prefix — the read returned NOTHING for it, so the index did "
                    "not answer and this zero corroborates nothing"),
            }
        else:
            empty = source_index_is_empty(g, timeout_ms=timeout_ms)
            if empty is True:
                # A graph whose Source population carries no `_searchText` at all
                # (a clear-only repair): nothing here is left for the index to
                # build on, so the probe's zero is structurally consistent. ⛔ A
                # stale entry does NOT appear in this count — it is the node with
                # the field ABSENT (#11536 review).
                # ⛔ AND NOT ``leg_trace`` (#11536 review round 8, P2 — this
                # clause SURVIVED that round's sweep and is corrected here): the
                # arm is sound because when no Source over `documentKind IS NULL`
                # carries `_searchText`, the index has nothing to hold, so a dead
                # read cannot hide a sentence there. A dead read is caught by the
                # positive CONTROL — never by ``leg_trace``, which MEASURED on a
                # real FalkorDB reports ``ran=True, degraded=False,
                # reason='empty_results'`` for a label with NO fulltext index, so
                # it cannot tell a dead read from a clean miss.
                # ⛔ THIS ARM CARRIES NO `ran`/`degraded` (#11536 review
                # round 7, P2). They were LITERAL constants here — `"ran": True,
                # "degraded": False` — and the note said "and the read RAN", on
                # the ONE arm that runs NO FTS read: what is measured is
                # `source_index_is_empty`, a NODE-PROPERTY count. The arm is still
                # sound, but for the reasons stated above — the probe row's own
                # `stale` veto catches a surviving entry, and a dead read is the
                # CONTROL's job — NOT because of anything this arm claims to have
                # read.
                report["control"] = {
                    "mode": "empty-index", "ok": True, "hits": 0,
                    "measured": "node-property",
                    "note": "no SOURCE over the probe's own population "
                            "(`documentKind IS NULL`) carries `_searchText` (the "
                            "field the index is declared on), so the corpus holds "
                            "nothing for the index to build on and the probe's 0 "
                            "is consistent with a repaired graph. ⛔ This arm "
                            "counted NODES; it did not exercise the FTS read, so "
                            "it does not by itself show that the read answers",
                }
            elif empty is None:
                report["control"] = {
                    "mode": "unavailable", "ok": False, "hits": None,
                    "note": "no usable control token was found, and the "
                            "Source index-entry read FAILED — an unreadable "
                            "instrument is not an empty one"}
            else:
                report["control"] = {
                    "mode": "unavailable", "ok": False, "hits": None,
                    # ⛔ NOT "no POST-fix lexicon node" (#11536 review round 3):
                    # the lexicon is word-frequency derived, so a node CAN exist
                    # and still yield no token `probe_phrase_rejection` accepts.
                    # The old wording asserted a fact about the graph the run had
                    # not measured, and pointed the operator at the weaker
                    # evidence this leg exists to stop certifying on.
                    # ⛔ BOUNDED, AND SAID SO: the control reads a WINDOW of
                    # post-fix lexicon rows, so "no usable token" is a statement
                    # about that window, never about the whole graph — the same
                    # over-claim, one order smaller, that the round-3 rewording
                    # removed (#11536 review round 3). The query is ORDERed so
                    # the window is the same one on every run.
                    # ⛔ "the window it READS", not "the rows it RETURNED": the
                    # read may return fewer than the window (a small graph), so
                    # naming the window as a returned count was the same
                    # over-claim one order smaller (#11536 review round 4).
                    # ⛔ "THE INDEX IS ANSWERING" IS NOT MEASURED HERE (#11536 review
                    # round 6, P3): this note is built from the CONTROL's Cypher
                    # read and the node-property count — neither consults the FTS
                    # leg, so on a breaker-open run it told the operator the
                    # instrument was alive two lines under a probe line that said
                    # `UNMEASURABLE (breaker_open)`. State the property count and
                    # what it does NOT prove; the fold's own `unmeasurable` veto is
                    # what speaks to the read.
                    "note": "no usable control TOKEN was found within the "
                            f"{_CONTROL_LEXICON_WINDOW}-row window the control "
                            "reads (no post-fix lexicon row in it, or none whose "
                            "tokens are valid single-token probes), and the "
                            "probe's own population DOES carry `_searchText` (a "
                            "node-property count) — so nothing here proves the "
                            "index would answer for a known-present token. Run "
                            "the measurement on the writing arm "
                            "(`--repair --probe-phrase …`), which takes its own "
                            "BEFORE reading."}
    return report
