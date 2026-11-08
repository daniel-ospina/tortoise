"""#11070 — repair the PRE-#10852 ``:Source`` prose population, and a census
that can actually reach zero.

WHAT IS BROKEN (measured, not reasoned)
=======================================
A capture that ran BEFORE #10852 wrote the transcript's first substantive
utterance into ``Source.summary`` and derived ``_searchText`` from that same
value. #10852 stopped the writer doing it again; it deliberately did not heal
the nodes already holding the pair, and no OTHER route reaches them:

* a re-capture is the #1727 no-op replay (the mint gate), pinned in
  ``tests/test_10000_source_prose.py``;
* a record-sourced re-projection DECLINES the pair for an episodic Source
  (``cache_projection._EPISODIC_SOURCE_PROSE_COLUMNS``), and a decline means
  "do not SET", so the existing value stays;
* ``record_reconcile`` — the module the tracker points at — is the COMPARISON.
  Its ``declined = episodic_source_prose_properties()`` marks the pair
  CACHE-OWNED so it lands in ``graph_only`` instead of as a permanent
  ``field_mismatch``. It has no write path to the cache and should not get one.

⛔ THE SECOND HALF OF THIS FILE IS THE CENSUS, AND IT IS NOT BOOKKEEPING.
#11070's own indicator is

    MATCH (s:Source) WHERE s.is_episodic = true
      AND (s.summary <> '' OR s._searchText IS NOT NULL) RETURN count(s)

and that count CANNOT REACH ZERO on a healthy graph, for two independent
reasons, each with a leg below:

1. **``is_episodic`` is not the session-Source identity.** The capture commit
   stamps it on every EXTERNAL source of ``payload.sources``, and
   ``backfill_is_episodic.py`` stamps it on every Source reachable from a
   session point — so a DOCUMENT Source carries it. A document legitimately has
   ``summary`` and ``_searchText`` (its FTS field is declared on the latter and
   ``backfill_document_search_text`` writes it), so the number is structurally
   pinioned above zero by the document population. The identity is the repo's
   ONE declaration, :func:`tortoise.cache_projection.is_episodic_source_node`
   (two arms, never a flag).
2. **Non-empty is not prose.** A POST-#10852 node carries a topic LEXICON:
   ``summary`` is ``SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics)`` and
   ``_searchText`` is the same lexicon bare. Both are non-empty by design, so
   ``summary <> ''`` counts every healthy node. The WITNESS that separates the
   two derivations is the prefix: a pre-#10000 row's prose carries no prefix
   (``session_source_display``).

A predicate that cannot reach zero cannot verify a fix, so the census in this
module is built from those two declarations and the legs below pin both.
"""

from __future__ import annotations

import importlib.util as _ilu
import itertools
import json
import sys
from pathlib import Path

import pytest

from tortoise.session_source_display import SESSION_SOURCE_SUMMARY_PREFIX

#: The transcript slice a pre-fix capture left on the node.
PROSE = "We decided the weekly rebuild happens on Fridays."

#: The fake's `id(s)` allocator — see `_session_node` (#11176).
_NODE_IDS = itertools.count(1)


# ── fakes ──────────────────────────────────────────────────────────────────


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _FakeClient:
    """The CLIENT handle — where ``config_get`` actually lives."""

    def __init__(self, graph):
        self._graph = graph

    def config_get(self, name):
        if self._graph.config_get_raises:
            raise RuntimeError("this server cannot be asked")
        assert name == "TIMEOUT_MAX"
        return self._graph.timeout_cap


class _RecordingGraph:
    """A graph that RECORDS every write, so ``dry_run`` can be proven.

    Reads answer from ``rows``; a write returns one node. Every statement (and
    its params) is kept in ``statements`` so a test can assert that a dry run
    issued none, and that a repair wrote the values it claims.
    """

    WRITE_MARKERS = ("SET ", "REMOVE ", "DELETE ", "MERGE ")

    #: The columns the census read projects (it must NOT pull `embedding`, so the
    #: graph is asked for these by name and the fake answers in the same order).
    COLUMNS = ("url", "sourceKind", "summary", "_searchText", "topics",
               "sessionId", "turn_max_index", "node_id")

    #: The server's ``TIMEOUT_MAX`` for this fake, in ms. ``0`` = uncapped, which
    #: is FalkorDB's own default. A NON-ZERO value makes the fake REFUSE an
    #: over-cap request exactly as the server does — the canonical docker test
    #: lane runs ``TIMEOUT_MAX 30000`` (``tools/test_lane.py``), and a fake that
    #: silently ignored ``timeout`` let a 60 000 ms request ship that could never
    #: execute there (#11152 review P1: the census raised on every run).
    TIMEOUT_CAP_ERR = ("The query TIMEOUT parameter value cannot exceed the "
                       "TIMEOUT_MAX configuration parameter value")

    def __init__(self, rows, *, timeout_cap=0, config_get_raises=False):
        self._rows = rows
        self.timeout_cap = timeout_cap
        self.config_get_raises = config_get_raises
        self.statements: list[tuple[str, dict]] = []
        self.requested_timeouts: list[int | None] = []
        # ⛔ THE VERB LIVES ON THE CLIENT, NOT THE GRAPH. A real
        # `falkordb.Graph` has NO `config_get` and NO `connection` — measured
        # `hasattr(Graph,'config_get') is False`, `hasattr(Graph,'client') is
        # True` — and the projection's `_GuardedGraph` forwards to the same
        # object. An earlier version of this fake put `config_get` ON THE GRAPH,
        # which made a dead clamp look alive: the module never found the cap on a
        # real handle, so it never clamped (#11152 review round 2, P1).
        self.client = _FakeClient(self)

    #: Deliberately ABSENT: `config_get` is not an attribute of this object, and
    #: neither is `connection` — a regression that goes back to probing the bare
    #: graph handle now fails here instead of passing quietly.
    @property
    def connection(self):
        raise AttributeError("a real falkordb Graph has no `.connection`")

    def query(self, cypher, params=None, timeout=None):
        if any(m in cypher for m in self.WRITE_MARKERS):
            self.statements.append((cypher, dict(params or {})))
            return _Result([("session:whatever",)])
        self.requested_timeouts.append(timeout)
        if self.timeout_cap and timeout and int(timeout) > int(self.timeout_cap):
            raise RuntimeError(self.TIMEOUT_CAP_ERR)
        return _Result([tuple(props.get(c) for c in self.COLUMNS)
                        for (props,) in self._rows])

    @property
    def writes(self):
        return [s for s in self.statements]


def _session_node(**overrides):
    props = {
        # ⛔ A PER-CALL IDENTITY, NOT A CONSTANT (#11176). `_SOURCE_ROWS` fans a
        # `:Source` out into one row per `:Session` at its `sessionId`, and the
        # census dedupes on `id(s)` — so a fake that gave every `:Source` the SAME
        # `id(s)` would make two DISTINCT nodes look like one node's two rows and
        # silently collapse them. A test that wants two rows of ONE node
        # (`node_id=41`, twice) overrides this by name.
        "node_id": next(_NODE_IDS),
        "url": "session:s-11070",
        "sourceKind": "agentSession",
        "sessionId": "s-11070",
        "is_episodic": True,
        "summary": PROSE,
        "_searchText": PROSE,
    }
    props.update(overrides)
    return props


def _document_node(**overrides):
    """A DOCUMENT Source that ``is_episodic`` misclassifies as a session Source."""
    props = {
        "url": "https://example.com/q3-pricing",
        "sourceKind": "document",
        "is_episodic": True,
        "summary": "Q3 pricing changes",
        "_searchText": "Q3 pricing changes",
    }
    props.update(overrides)
    return props


def _post_fix_node(**overrides):
    topics = ["migration", "plan", "friday"]
    props = {
        "url": "session:s-post",
        "sourceKind": "agentSession",
        "sessionId": "s-post",
        "is_episodic": True,
        "summary": SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(topics),
        "_searchText": ", ".join(topics),
    }
    props.update(overrides)
    return props


# ── the census: it must be able to reach 0 ─────────────────────────────────


def test_the_census_finds_a_session_source_carrying_pre_fix_prose():
    from tortoise import session_source_prose_migration as m

    found = m.find_session_sources_carrying_prose(_RecordingGraph([(_session_node(),)]))
    assert [n["url"] for n in found] == ["session:s-11070"]
    assert found[0]["summary_bytes"] == len(PROSE)
    assert found[0]["search_text_bytes"] == len(PROSE)


def test_a_document_source_is_not_in_the_census_even_with_is_episodic_true():
    """Leg 1: ``is_episodic`` is not the identity — the tracker's predicate is wrong.

    This node is a healthy document. Counting it pins the census above zero
    forever, which is exactly what makes a fixed graph look unfixed.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_document_node(),)])) == []
    assert m.census(_RecordingGraph([(_document_node(),)])) == 0


def test_a_post_fix_lexicon_source_is_not_in_the_census():
    """Leg 2: non-empty is not prose — the prefix is the witness.

    A post-#10852 node carries a non-empty ``summary`` and ``_searchText`` by
    design. ``summary <> ''`` counts it; the prefix test does not.
    """
    from tortoise import session_source_prose_migration as m

    assert m.find_session_sources_carrying_prose(_RecordingGraph([(_post_fix_node(),)])) == []


def test_the_census_reaches_zero_on_a_graph_holding_only_healthy_sources():
    """The whole point: 0 is REACHABLE, so 0 is evidence."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_post_fix_node(),),
        (_document_node(),),
        (_document_node(url="corpus://c/transcript.md", sourceKind="agentSession"),),
        ({"url": "session:s-stub", "sourceKind": "agentSession", "summary": "",
          "_searchText": ""},),
    ])
    assert m.census(graph) == 0


# ── the repair ─────────────────────────────────────────────────────────────


def _reader(transcript):
    """A record reader that answers with ``transcript`` for any session."""
    def read(session_id, turn_max_index=None):
        return transcript
    return read


def test_the_repair_re_derives_the_lexicon_from_the_RECORD():
    """The constraint: derive from the record, never from the node's own prose.

    The repair must not read ``summary`` off the node and call that a repair —
    the narrative's authoritative copy is the record.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph,
        read_transcript=_reader("User: migrate the friday rebuild plan\n"
                                "Assistant: the plan is the migration"),
        dry_run=False,
    )

    assert report["repaired"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX)
    assert PROSE not in params["sum"], "the prose survived into the re-derived summary"
    assert PROSE not in params["st"]
    assert params["st"], "the fixture transcript must derive a lexicon"
    assert not params["st"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the indexed column must be the BARE lexicon — the display prefix in "
        "_searchText makes every session match a query for 'session'/'capture'")
    assert params["st"] == params["sum"][len(SESSION_SOURCE_SUMMARY_PREFIX):], (
        "the display column and the indexed column must be one datum")


def test_the_clear_arm_leaves_topics_alone():
    """`topics` is NOT the prose datum — the pre-fix writer derived it separately.

    The pre-#10000 capture took `topics` from `Counter.most_common(6)` (word
    frequencies) while `summary` was the first utterance, so the two were
    independent on this very population. Clearing it would destroy the only
    non-prose descriptor the node has, and would take a third field on a route
    whose declared bound is the pair.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([
        (_session_node(topics=["rebuild", "friday", "weekly"]),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)

    statement, params = graph.writes[0]
    assert "topics" not in statement, (
        "the clear-only arm wrote `topics` — it is not part of the pair")
    assert "topics" not in params


def test_neither_arm_writes_topics():
    """`topics` is NOT the pair, and `record-reconcile` compares it EXACTLY.

    The pre-fix writer took `topics` from `Counter.most_common(6)` while `summary`
    was the first utterance, so the record's own `topics` column is an independent
    value that the reconciler compares field-by-field (it is a projected property,
    NOT one of the declined pair). Writing a re-derived `topics` here would make
    `record-reconcile` permanently divergent on exactly the population this repair
    heals — the "signal indistinguishable from noise" class, one door over.
    """
    from tortoise import session_source_prose_migration as m

    for transcript in (None, "User: friday migration plan"):
        graph = _RecordingGraph([(_session_node(),)])
        m.reconcile_session_source_prose(
            graph, read_transcript=_reader(transcript), dry_run=False,
            clear_without_record=True)
        statement, params = graph.writes[0]
        assert "topics" not in statement, f"wrote `topics` (transcript={transcript!r})"
        assert "topics" not in params


def test_a_record_restored_node_is_not_a_candidate():
    """P0 (#11070 review): the recovery door's OWN node shape must not be prose.

    `node_from_row` DECLINES `summary` for an episodic Source while projecting the
    lexicon `_searchText` (#10965), so a `record-restore`d graph holds exactly
    `{_searchText: <lexicon>, topics: [...]}` with NO `summary`. Keying the
    blank-summary arm on emptiness alone made every restored session a candidate,
    so `--repair` cleared the whole tenant's Source index — the same unreachable-
    zero defect the tracker's own predicate had, one door over.
    """
    from tortoise import session_source_prose_migration as m

    topics = ["migration", "plan", "friday"]
    restored = {
        "url": "session:s-post", "sourceKind": "agentSession", "sessionId": "s-post",
        "_searchText": ", ".join(topics), "topics": topics,
    }
    assert m.carries_pre_lexicon_prose(restored) is False
    graph = _RecordingGraph([(restored,)])
    report = m.reconcile_session_source_prose(graph, read_transcript=_reader(None))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_restored_node_whose_topics_do_not_prove_the_lexicon_IS_a_candidate():
    """The proof is EXACT equality, and its polarity is fail-toward-candidate.

    `record-restore` always projects the pair together, so a blank `summary` with
    a `_searchText` its `topics` do not explain is the blanked-summary pre-fix
    hazard — prose with no prefix to witness it — and must be repaired.
    """
    from tortoise import session_source_prose_migration as m

    blanked = _session_node(
        summary="", _searchText=PROSE, topics=["rebuild", "friday", "weekly"])
    assert m.carries_pre_lexicon_prose(blanked) is True


def test_record_turn_reader_maps_the_records_three_answers(monkeypatch):
    """Fix pins the PRODUCTION reader, not just an injected lambda.

    The injected-lambda leg below passed while `record_turn_reader` collapsed
    `TurnAnswer.EMPTY` (an answer) into `None` (no answer) — so the `empty_sessions`
    bucket was dead in the only path that runs in production.
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    def fake(sid):
        return {
            "complete": record_read.TurnAnswer(
                answered=True, turns=(_turn("User: hi"),), turn_count=1,
                kind=record_read.TurnAnswer.COMPLETE),
            "empty": record_read.TurnAnswer(
                answered=True, turns=(), turn_count=0,
                kind=record_read.TurnAnswer.EMPTY),
            "none": record_read.TurnAnswer(
                answered=False, turns=(), turn_count=None,
                kind=record_read.TurnAnswer.NO_ACCOUNT),
            "partial": record_read.TurnAnswer(
                answered=False, turns=(_turn("User: hi"),), turn_count=9,
                kind=record_read.TurnAnswer.PARTIAL),
        }[sid]

    monkeypatch.setattr(record_read, "session_turns", lambda *a, **k: fake(k["session_id"]))
    read = m.record_turn_reader(object(), org_id="o", graph_id="g")

    assert read("complete") == "User: hi"
    assert read("empty") == "", "EMPTY is an ANSWER (record spoke, 0 turns)"
    assert read("none") is None
    assert read("partial") is None, (
        "a PARTIAL set must not build a lexicon from half a transcript")


class _Line:
    def __init__(self, line):
        self.line = line


def _turn(line):
    return _Line(line)


def test_a_record_that_spoke_and_states_zero_turns_is_not_reported_as_no_record():
    """`TurnAnswer.EMPTY` is an ANSWER; `NO_ACCOUNT` is not, and they differ.

    Collapsing them sent an operator hunting a DSN or row-level-security problem
    for a session whose record had spoken.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(""), dry_run=False)

    assert report["empty_sessions"] == ["session:s-11070"]
    assert report["no_record"] == []
    assert len(graph.writes) == 1  # the prose still goes (§9.1/D1)


def test_the_repair_clears_the_pair_when_the_record_does_not_answer():
    """No record → no invented lexicon. The CLEAR itself must be asked for.

    Clearing is not a new state — the writer itself produces ``summary=''`` and a
    bare ``_searchText`` for a text-less capture — but it is NOT self-correcting:
    the cleared node stops matching the census, so a later run cannot find it to
    re-derive the lexicon. It is therefore taken only when
    ``clear_without_record`` is set (#11152 review P2: the CLI gated this arm on
    "a DSN was supplied", which a wrong or empty record scope satisfies while
    every session reads as "no answer" — one bad DSN from un-indexing a
    tenant's whole Source population).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)

    assert report["repaired"] == []
    assert report["no_record"] == ["session:s-11070"]
    assert len(graph.writes) == 1
    params = graph.writes[0][1]
    assert params["sum"] == ""
    # The writer's text-less shape: `summary=''` and NO `_searchText` (its SET is
    # `coalesce($st, s._searchText)` with `$st` None, and FalkorDB drops a null
    # property). `''` would be a state no writer reaches.
    assert params["st"] is None


def test_the_default_arm_leaves_an_unanswered_node_ALONE():
    """⛔ The default must not destroy what the record cannot replace.

    Same graph and same reader as the test above; only the flag differs. Without
    it the node is REPORTED and NOT written, so the operator sees the count the
    default refused instead of discovering it after the fact.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=False)

    assert report["no_record"] == ["session:s-11070"]
    assert report["left_alone"] == ["session:s-11070"]
    assert graph.writes == [], "the default cleared a node it could not re-derive"


def test_a_run_with_no_reader_says_SO_and_not_that_the_record_was_silent():
    """⛔ "nobody asked" is not "the record did not answer" (#11152 review P2).

    The CLI's documented step 1 supplies no reader, so folding the two together
    told the operator the record had failed for N sessions they had never opened
    — sending them hunting a DSN or row-level-security problem that does not
    exist, and pointing at the destructive arm as the only remaining option.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(graph, dry_run=True)

    assert report["no_reader"] == ["session:s-11070"]
    assert report["no_record"] == [], (
        "a record that was never opened was reported as having not answered")
    assert report["left_alone"] == ["session:s-11070"]
    assert graph.writes == []


def test_the_repair_is_dry_by_default_and_writes_nothing():
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"))

    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "dry_run wrote to the graph"


def test_dry_run_reports_the_same_candidate_set_as_a_real_run():
    """`dry_run=True` must return the SAME report shape (#4125's contract)."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    plan = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=True)
    done = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert plan["candidates"] == done["candidates"]
    assert set(plan) == set(done)


def test_the_candidate_read_is_never_conflated_with_a_healthy_node():
    """A repair that re-derived over a good node would rewrite it every run."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: anything"))
    assert report["candidates"] == []
    assert graph.writes == []


def test_a_second_run_finds_nothing_left_to_repair():
    """Idempotent: the repair's own output must not be a candidate.

    A reconciler whose output re-enters its input is a rewrite loop, and the
    census would then never reach zero on the repaired graph — the failure this
    whole lane exists to prevent.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)
    params = graph.writes[0][1]

    healed = _session_node(summary=params["sum"], _searchText=params["st"])
    after = _RecordingGraph([(healed,)])
    report = m.reconcile_session_source_prose(
        after, read_transcript=_reader("User: friday rebuild plan"), dry_run=False)

    assert report["candidates"] == [], (
        "the repair's own output is still a candidate — the census can never "
        "reach zero on a repaired graph")
    assert after.writes == []


def test_the_repaired_node_is_still_findable_through_the_source_fts_field():
    """#3518: a repair that only BLANKS the pair un-indexes every healed session."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday migration plan"),
        dry_run=False)
    st = graph.writes[0][1]["st"]
    assert st and "migration" in st, (
        "the repaired node carries no searchable text, so the Source FTS probe "
        "can never find the session again")


class _Proj:
    def __init__(self, graph):
        self.g = graph


def test_the_replay_hook_is_PLAN_ONLY_without_a_record():
    """Cycle-3 P1: the hook must never take the destructive arm unasked.

    A replay engine holds no record handle, and the clear arm is destructive AND
    not self-correcting (its output is no longer a candidate), so clearing here
    would un-index the pre-#10852 population irreversibly and then report
    `census == 0` — a false PASS of the exact kind this issue exists to close.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_after_replay(_Proj(graph), engine="rebuild")

    assert report is not None
    assert report["dry_run"] is True
    assert report["candidates"] == ["session:s-11070"]
    assert graph.writes == [], "the rebuild hook wrote without a record handle"


def test_the_replay_hook_writes_when_it_HAS_the_record():
    """The guard is the RECORD HANDLE, not a blanket refusal to act."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    m.reconcile_after_replay(
        _Proj(graph), engine="rebuild",
        read_transcript=_reader("User: friday migration plan"))

    assert len(graph.writes) == 1
    assert graph.writes[0][1]["st"], "with the record, the lexicon is re-derived"


def test_the_replay_hook_is_a_no_op_without_a_graph_handle():
    from tortoise import session_source_prose_migration as m

    class _NoGraph:
        pass

    assert m.reconcile_after_replay(_NoGraph(), engine="rebuild") is None


def test_the_prose_never_reaches_the_re_derived_summary():
    """A one-word-prose session must still be repaired, not skipped.

    ``summary`` prose shorter than a lexicon is the shape an over-eager "is
    this really prose?" heuristic would skip.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(summary="ok", _searchText="ok"),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: ok go"), dry_run=False)
    assert report["candidates"] == ["session:s-11070"]
    healed = graph.writes[0][1]
    assert healed["sum"] != "ok" or healed["st"] != "ok"


# ── the read the census makes must SURVIVE a capped server ──────────────────
#
# #11152 review P1, reproduced on a real FalkorDB: the module requested a 60 000
# ms timeout, and a server whose TIMEOUT_MAX is 30 000 REFUSES the statement
# outright, so `census()` raised on every run against the canonical docker test
# lane — the module could not execute at all. The suite was green over it because
# the fake ignored `timeout`, which is the one parameter that decides whether the
# module runs. These three tests make the fake behave like the server.


def test_a_requested_timeout_above_the_servers_cap_is_CLAMPED_not_refused():
    """The census must run on a server that caps TIMEOUT (the lane runs 30 000)."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000)
    assert m.CACHE_READ_TIMEOUT_MS > 30_000, (
        "this test is vacuous unless the module's own request exceeds the cap")

    found = m.find_session_sources_carrying_prose(graph)

    assert [n["url"] for n in found] == ["session:s-11070"]
    assert graph.requested_timeouts == [30_000], (
        "the request must be clamped to the server's own TIMEOUT_MAX")


def test_a_request_under_the_cap_is_honoured_as_written():
    """Clamped, never flattened: an uncapped/capable server gets what was asked."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=0)  # 0 = uncapped
    m.find_session_sources_carrying_prose(graph)
    assert graph.requested_timeouts == [m.CACHE_READ_TIMEOUT_MS]


def test_a_server_that_cannot_be_asked_still_reads_the_census():
    """A server that fails `config_get` must not turn the census into an error.

    Second line of defence: on the cap error the read is retried with NO explicit
    timeout, which the server's own policy bounds. Any OTHER exception is the
    query's and still propagates — asserted here so a bare `except: retry` cannot
    hide a real failure.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000,
                            config_get_raises=True)
    found = m.find_session_sources_carrying_prose(graph)

    assert [n["url"] for n in found] == ["session:s-11070"]
    assert graph.requested_timeouts == [m.CACHE_READ_TIMEOUT_MS, None], (
        "the retry must carry NO timeout, not the refused one again")

    class _Broken(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            raise RuntimeError("some other server failure")

    with pytest.raises(RuntimeError, match="some other server failure"):
        m.find_session_sources_carrying_prose(_Broken([]))


def test_the_census_read_carries_the_sessions_live_turn_end():
    """⛔ The tail witness must ride the SAME scan (#11152 review P2).

    `record_read.session_turns`'s third PARTIAL shape — a transcript that stops
    BELOW the session's live id-space end — is invisible to the row set and to
    `turn_count`; the mark is its ONLY witness. A reader built without it returns
    that shape as COMPLETE, so the repair would derive a lexicon from half a
    transcript and call it a repair.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    found = m.find_session_sources_carrying_prose(graph)
    assert found[0]["turn_max_index"] == 7

    seen = {}

    def read(session_id, turn_max_index=None):
        seen["tmi"] = turn_max_index
        return "User: friday migration plan"

    m.reconcile_session_source_prose(graph, read_transcript=read, dry_run=True)
    assert seen["tmi"] == 7, (
        "the reconciler called the reader without the session's turn_max_index")


def test_a_reader_that_takes_one_argument_is_tolerated_and_SAID_SO():
    """The degraded shape must not crash the run — and must not pass silently."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid: "User: friday migration plan")

    assert report["repaired"] == ["session:s-11070"]
    assert "turn_max_index" in report["notes"]["session:s-11070"], (
        "a one-argument reader silently drops the tail witness")


def test_the_clamp_finds_the_cap_on_the_CLIENT_not_the_graph():
    """⛔ The verb is on the CLIENT — probing the graph alone was dead code.

    A real `falkordb.Graph` has no `config_get` and no `connection`, so an
    implementation that probes the graph handle never finds the cap and never
    clamps: the 60 000 ms request goes out, the server refuses it, and the read
    only survives on the retry. The fake above models that shape (verb on
    `.client`, and `.connection` raises), so this test fails if the probe regresses
    (#11152 review round 2, P1).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)], timeout_cap=30_000)
    assert m._server_timeout_cap(graph) == 30_000
    assert m._effective_timeout_ms(graph, 60_000) == 30_000
    assert m._effective_timeout_ms(graph, 5_000) == 5_000


def test_the_clear_arm_says_CLEARED_and_the_default_says_LEFT_ALONE():
    """The note must state what HAPPENED, not what the default would have done.

    Emitting "left alone unless clear_without_record is set" on the very run that
    cleared the node is a report contradicting its own writes (#11152 review
    round 2, P2).
    """
    from tortoise import session_source_prose_migration as m

    # The dry run is asserted separately (a dry run writes NOTHING, so it must not
    # claim a clear); this test is about the WRITING arm's two outcomes.
    kept_graph = _RecordingGraph([(_session_node(),)])
    kept = m.reconcile_session_source_prose(
        kept_graph, read_transcript=_reader(None), dry_run=False)
    assert "left alone unless clear_without_record is set" in kept["notes"]["session:s-11070"]
    assert "CLEARED" not in kept["notes"]["session:s-11070"]
    assert kept_graph.writes == []

    cleared_graph = _RecordingGraph([(_session_node(),)])
    cleared = m.reconcile_session_source_prose(
        cleared_graph, read_transcript=_reader(None), dry_run=False,
        clear_without_record=True)
    assert "CLEARED" in cleared["notes"]["session:s-11070"]
    assert len(cleared_graph.writes) == 1


def test_a_node_with_no_session_id_is_COUNTED_when_a_reader_is_supplied():
    """⛔ The fourth state must have a counter on the production path.

    With a reader supplied, a pre-#4005 bare-filename node satisfies neither
    `no_reader` (a reader WAS supplied) nor `no_record` (the record was never
    asked), so it landed in NO bucket — and on the clear arm that made a
    destructive write reportable as nothing (#11152 review round 2, P2).
    """
    from tortoise import session_source_prose_migration as m

    legacy = {"url": "session.md", "sourceKind": "agentSession",
              "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([(legacy,)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"))

    assert report["candidates"] == ["session.md"]
    assert report["no_session_id"] == ["session.md"]
    assert report["left_alone"] == ["session.md"]
    assert graph.writes == []


def test_a_PARTIAL_answer_caused_by_the_CACHE_mark_is_named_as_such():
    """The tail witness is the CACHE's, so `no_record` may be the wrong diagnosis.

    `record_turn_reader` forwards the `:Session.turn_max_index` read off the
    cache; a mark ahead of the record's live end makes a fully-answered session
    read as PARTIAL. Refusing to write is the safe direction and stays — but the
    note must name the suspect instead of blaming the record (#11152 review
    round 2, P2).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # ⛔ A REASON THAT DOES **NOT** CONTAIN THE FALLBACK WORDING, so only the
    # STRUCTURAL condition can produce the warning. An earlier version of this
    # test used the real tail wording, which also satisfied the string fallback —
    # deleting the structural computation left the suite green
    # (#11152 review round 4).
    tails = (record_read.Turn(turn_id="t1", index=0, content="[user] hi"),
             record_read.Turn(turn_id="t2", index=1, content="[user] plan"))

    def fake_session_turns(conn, *, org_id, graph_id, session_id,
                           turn_max_index=None):
        return record_read.TurnAnswer(
            answered=False, turns=tails, turn_count=2,
            reason="the record answered for part of this session",
            kind=record_read.TurnAnswer.PARTIAL)

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(self_inner):
            self_inner._real = record_read.session_turns
            record_read.session_turns = fake_session_turns

        def __exit__(self_inner, *exc):
            record_read.session_turns = self_inner._real

    graph = _RecordingGraph([(_session_node(turn_max_index=99),)])
    with _Patch():
        assert reader("s-11070", 99) is None
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "record did not ANSWER" in note
    assert "MAY BE THE CACHE'S FAULT" in note, (
        "a bare `no_record` tells the operator to hunt a record problem that may "
        "not exist")
# ══════════════════════════════════════════════════════════════════════════
# The operator CLI's SCOPE contract (#11152 review: presence is not proof, and
# the requirement belongs on the arm that can destroy something)
# ══════════════════════════════════════════════════════════════════════════
#
# Two independent defects, both only observable at argv level:
#
# 1. The scope guard was keyed on `record_dsn` ALONE, so on the box this repair
#    targets — `TORTOISE_RECORD_DSN` exported, no scope flags typed, which the
#    script's own comment calls "most of them" — the runbook's step 1
#    (`--dry-run`) and step 3 (`--dry-run --assert-clean`) exited 2 before
#    opening the graph. A verification step that cannot run is a verification
#    that cannot fail, and the only escape the error offered was
#    `--clear-without-record` — the flag that authorises clearing.
# 2. Non-empty `--org-id`/`--graph-id` prove PRESENCE, never that the scope
#    ANSWERED. Row-level security returns zero rows (never an error) for a scope
#    that does not match, so a typo'd `--graph-id` reads exactly like "the record
#    did not answer" for every session. One session cannot tell the two apart;
#    the population can, and the run must say which failure it saw.

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLI_PATH = _REPO_ROOT / "graph-scripts" / "11070_repair_session_source_prose.py"
_spec = _ilu.spec_from_file_location("repair_session_source_prose_11070",
                                     str(_CLI_PATH))
_cli = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cli)

_DSN = "postgresql://u@h/db"


class _Reached(Exception):
    """Raised where the run got PAST the argument guards."""


class _FakeRecordConn:
    def close(self):
        pass


class _NotAnswered:
    answered = False
    turns: tuple = ()


class _OneTurn:
    """A session whose record DOES answer — the proof the scope works."""

    answered = True
    turns = (type("_T", (), {"line": "User: friday rebuild plan"})(),)


def _no_account(conn, *, org_id, graph_id, session_id, turn_max_index=None):
    """The record as a WRONG SCOPE presents it: silence for every session."""
    return _NotAnswered()


class _Proj:
    """The projection the CLI opens — just the graph handle it needs."""

    def __init__(self, graph):
        self.g = graph

    def close(self):
        pass


def _export_a_record_dsn(monkeypatch):
    """The box this repair targets: a DSN exported, no scope flag typed."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@127.0.0.1:1/none")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", _DSN)
    monkeypatch.delenv("TORTOISE_ORG_ID", raising=False)


def _install_cli(monkeypatch, graph, argv):
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Proj(graph))
    monkeypatch.setattr("tortoise.record_serve.open_record",
                        lambda dsn: _FakeRecordConn())
    monkeypatch.setattr("tortoise.record_read.session_turns", _no_account)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, *argv])


def test_a_census_runs_on_a_box_that_exports_a_record_dsn(monkeypatch, capsys):
    """Finding (1): steps 1 and 3 must be runnable where they are needed.

    (1) FAILS on the pre-fix code: the guard was keyed on `record_dsn`, so this
        argv exited 2 before `open_projection` was reached — proving the census
        refused to run and hiding the reason behind the clearing flag.
    (2) REACHABLE: the same argv now reaches the cache read, and the run SAYS
        the record was not consulted instead of silently reporting silence.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])

    def _boom(uri, name):
        raise _Reached

    monkeypatch.setattr(_cli, "open_projection", _boom)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--dry-run"])

    with pytest.raises(_Reached):
        _cli.main()
    # …and with a real projection the census completes, exit 0, no scope asked.
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Proj(graph))
    assert _cli.main() == 0
    captured = capsys.readouterr()
    assert "census BEFORE: 1" in captured.out
    assert "NOT consulted" in captured.err
    # ⛔ AND THE REMEDY MUST NAME THE ARM THAT WRITES. This branch fires only on a
    # census, where the write is `continue`d before the Cypher — so an operator
    # who follows "pass --record-dsn … to re-derive" and nothing else gets a PLAN
    # and zero writes (#11152 review P3).
    assert "NOT written" in captured.err, (
        "this test is vacuous unless the no-reader warning fired")
    assert "--repair" in captured.err.split("NOT written")[1], (
        "the census's own warning offers a remedy that cannot write")


def test_the_writing_arm_still_refuses_a_missing_record_scope(monkeypatch,
                                                              capsys):
    """The guard is not relaxed where it protects a destructive write."""
    _export_a_record_dsn(monkeypatch)
    monkeypatch.setattr(_cli, "open_projection", lambda uri, name: _Reached)
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--repair"])

    assert _cli.main() == 2
    err = capsys.readouterr().err
    assert "WRITING arm" in err
    assert "NOTE:" not in err, (
        "the census path's note fired on a writing arm — the two arms are "
        "being conflated")


def test_the_writing_arm_names_an_UNPROVEN_scope(monkeypatch, capsys):
    """Finding (2): a typed-but-wrong scope exits 3 and destroys nothing.

    (1) FAILS on the pre-fix code: the run reported `census_after != 0` and
        exited 1 with "re-run --dry-run and read the report" — the same exit a
        half-finished repair produces, so a wrong scope sent the operator to
        re-read a census instead of checking the scope it typed.
    (2) REACHABLE: a record that answers for NOBODY while the operator supplied a
        scope — exactly what row-level security does for a scope that does not
        match.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([
        (_session_node(sessionId="s-a", url="session:s-a"),),
        (_session_node(sessionId="s-b", url="session:s-b"),),
    ])
    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "WRONG"])

    assert _cli.main() == 3
    assert graph.writes == []
    err = capsys.readouterr().err
    assert "UNPROVEN" in err
    assert "--clear-without-record" in err, (
        "the refusal names no escape hatch for the deliberate clear-only arm")


def test_a_json_run_cannot_report_success_over_the_unproven_scope(monkeypatch,
                                                                  capsys):
    """`--json` must not defeat the refusal (the earlier fix-7 defect class)."""
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "WRONG", "--json"])

    assert _cli.main() == 3
    payload = json.loads(capsys.readouterr().out)
    assert payload["scope_unproven"] is True
    assert graph.writes == []


def test_ONE_answered_session_clears_the_scope(monkeypatch, capsys):
    """The proof is population-level, so a real repair is never refused.

    (1) FAILS if the refusal keys on any `no_record` at all — the ordinary case,
        where older sessions have aged out of the record: the operator would be
        told their scope is wrong on a scope that works.
    (2) REACHABLE: one session answers, one does not.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([
        (_session_node(sessionId="s-a", url="session:s-a"),),
        (_session_node(sessionId="s-b", url="session:s-b"),),
    ])

    def answer_one(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn() if session_id == "s-a" else _NotAnswered()

    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "gx"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answer_one)

    code = _cli.main()
    assert code != 3
    assert len(graph.writes) == 1, "the answered session must be re-derived"
    assert "UNPROVEN" not in capsys.readouterr().err


def test_the_clear_without_record_arm_is_never_second_guessed(monkeypatch,
                                                              capsys):
    """The operator asked for the destructive arm BY NAME — run it.

    (1) FAILS if the scope diagnosis also fires on this arm: `no_record` is empty
        here (the record is never opened), so a naive key on `left_alone` or on
        "no candidate was repaired" would refuse the one arm the flag exists for.
    (2) REACHABLE: `--repair --clear-without-record` with a DSN exported.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record"])

    code = _cli.main()
    assert code != 3
    assert len(graph.writes) == 1, "the by-name clear arm did not write"
    err = capsys.readouterr().err
    assert "UNPROVEN" not in err
    assert "NOT written" not in err


def test_an_EXPLICIT_record_dsn_survives_the_by_name_clear_flag(monkeypatch,
                                                                capsys):
    """⛔ The safe combination must be reachable FROM THE CLI (#11152 r3, P2).

    `--clear-without-record` exists so the clear arm is reachable on a box that
    EXPORTS a `TORTOISE_RECORD_DSN` the operator did not mean to use. It must
    never discard a `--record-dsn` the operator TYPED — doing so made
    "re-derive where the record answers, clear where it does not" inexpressible,
    and cleared nodes the record could have healed.

    ⛔ This test exists because the fix had NO test: reverting the guard to
    `if args.clear_without_record: record_dsn = ""` left every test green while
    turning the same argv from "re-derive" into "destroy" (review round 3).
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])

    def answers(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn()

    _install_cli(monkeypatch, graph, [
        "--repair", "--record-dsn", "postgresql://u@h/db",
        "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answers)

    code = _cli.main()
    err = capsys.readouterr().err
    # ⚠️ This harness's graph does not replay its own writes, so `census_after`
    # stays above 0 and the run legitimately exits 1 ("did not reach 0"). The
    # subject here is WHICH arm ran, and exit 3 would mean the scope refusal fired.
    assert code != 3, err
    assert "UNPROVEN" not in err
    assert len(graph.writes) == 1
    written = graph.writes[0][1]
    assert written["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the explicitly supplied record was ignored and the pair was CLEARED "
        "instead of re-derived")
    assert written["st"], "the indexed column was cleared instead of re-derived"


def test_an_unproven_scope_is_not_reported_over_the_by_name_clear_arm(
        monkeypatch, capsys):
    """The refusal must never claim "nothing was destroyed" after destroying.

    `--record-dsn` + `--clear-without-record` with a record that answers for
    nobody is a DELIBERATE clear: the operator already passed the flag the old
    message advised, and `no_record` there means CLEARED. Firing the refusal
    printed "nothing was destroyed (0 candidates left alone) … pass
    --clear-without-record" over a run that had cleared the population
    (#11152 review round 3, P2).
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, [
        "--repair", "--record-dsn", "postgresql://u@h/db",
        "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])

    code = _cli.main()
    err = capsys.readouterr().err
    assert "UNPROVEN" not in err
    assert "nothing was destroyed" not in err, (
        "the refusal claimed nothing was destroyed over a run that cleared")
    assert code != 3, err
    assert len(graph.writes) == 1
    assert graph.writes[0][1]["sum"] == "", "the by-name arm did not clear"


def test_a_SHORT_COUNT_PARTIAL_is_not_blamed_on_the_cache_mark():
    """⛔ The tail arm runs LAST; the record's own arms must win the diagnosis.

    `record_read._shortfall` refuses a short-count (`len(turns) < turn_count`) or
    a gap BEFORE it ever compares against `turn_max_index`, so a bare
    `max(held) < turn_max_index` test is a SUPERSET: it blamed the cache's mark
    for refusals the record itself declared, sending the operator to compare a
    datum that is irrelevant (#11152 review round 4).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # The record declares 2 turns and holds 1 — the SHORT-COUNT arm, which
    # `_shortfall` returns before the tail is examined.
    held = (record_read.Turn(turn_id="t1", index=0, content="[user] hi"),)
    answer = record_read.TurnAnswer(
        answered=False, turns=held, turn_count=2,
        reason=record_read._shortfall(held, 2, 99),
        kind=record_read.TurnAnswer.PARTIAL)
    assert "turn(s) but holds only" in answer.reason, (
        "this test is vacuous unless the reason is the SHORT-COUNT arm")

    def fake(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return answer

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(s):
            s._real = record_read.session_turns
            record_read.session_turns = fake

        def __exit__(s, *exc):
            record_read.session_turns = s._real

    graph = _RecordingGraph([(_session_node(turn_max_index=99),)])
    with _Patch():
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "record did not ANSWER" in note
    assert "MAY BE THE CACHE'S FAULT" not in note, (
        "the record declared this shortfall itself — the cache's mark is "
        "irrelevant and naming it sends the operator to the wrong datum")


def test_a_dry_run_never_says_the_pair_WAS_cleared():
    """A report must not assert a write the run did not make."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader(None), dry_run=True,
        clear_without_record=True)

    note = report["notes"]["session:s-11070"]
    assert "writes NOTHING" in note
    assert "the pair is CLEARED" not in note, (
        "a dry run claimed the pair was cleared while writing nothing")
    assert graph.writes == []


def test_a_one_argument_reader_keeps_its_warning_when_the_read_also_fails():
    """Two defects on one node must both be reported — notes ACCUMULATE."""
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(turn_max_index=7),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid: None)

    note = report["notes"]["session:s-11070"]
    assert "takes one argument" in note, (
        "the degraded-read warning was overwritten exactly when the read also "
        "failed, i.e. when the diagnosis is least reliable")
    assert "did not ANSWER" in note


def test_a_candidate_with_no_usable_url_is_REPORTED_not_silently_dropped():
    """Otherwise it pins the census above zero forever with no reason given.

    `is_episodic_source_node`'s bare-filename arm accepts an empty url, so such a
    node counts as a candidate; it cannot be written, so `census_after` can never
    reach 0 — an unreachable zero with no reported cause, the exact class #11070
    exists to close (#11152 review round 4).
    """
    from tortoise import session_source_prose_migration as m

    empty = {"url": "", "sourceKind": "agentSession", "is_episodic": True,
             "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([(empty,)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"), dry_run=True)

    assert report["candidates"] == [""]
    assert report["unaddressable"] == [""]
    assert any("no usable url" in n for n in report["notes"].values()), (
        "the run exits 1 forever with no explanation of the pinning candidate")
    assert graph.writes == []


def test_the_CLEARED_shape_is_not_a_candidate_again_EITHER():
    """The body claims both arms are idempotent; only the re-derived one was pinned.

    The clear arm writes `summary=''` with `_searchText` ABSENT (its SET is
    `coalesce($st, s._searchText)` with `$st` None, and FalkorDB drops a null
    property), and THAT is the shape nothing fed back into the predicate — the
    nearest census fixture carried `_searchText=''`, which the module itself calls
    "a state no writer reaches".

    This is the arm that CANNOT be undone, so its non-candidacy is what makes the
    irreversibility safe to reason about: if a later edit makes the absent shape a
    candidate again, a second run re-derives a lexicon over a node whose `topics`
    are the pre-fix word frequencies, and no test would red (#11152 review P3).
    """
    from tortoise import session_source_prose_migration as m

    for cleared in (
        # the write's own shape: the property is ABSENT after `SET … = null`
        {"url": "session:s-11070", "sourceKind": "agentSession",
         "summary": "", "topics": ["friday", "rebuild"]},
        # …and the row a Cypher read returns for the same node
        {"url": "session:s-11070", "sourceKind": "agentSession",
         "summary": "", "_searchText": None, "topics": ["friday", "rebuild"]},
    ):
        assert m.carries_pre_lexicon_prose(cleared) is False, (
            f"the clear arm's own output is a candidate again: {cleared}")
        assert m.census(_RecordingGraph([(cleared,)])) == 0, (
            f"a cleared node still counts: {cleared}")


def test_a_gap_caused_by_a_REPEATED_index_is_not_blamed_on_the_cache_mark():
    """`_shortfall` holds a SET; the tail arm must too (#11152 review P2).

    `record_read._shortfall` does `held = {turn.index for turn in turns}` and
    subtracts `len(held)`, so a repeated index (`s_t01` and `s_t1` both parse to
    1 — `_turn_index` takes any `int()`-parsable suffix, and
    `tools/backfill_graph_turns.py` copies ids verbatim) is a GAP there. The
    module built `held` as a LIST, where `[0, 1, 3, 3]` is contiguous to
    `max(held) + 1 != len(held)` — so it named the record's own hole and then
    blamed the cache mark in the next sentence, the round-5 misdiagnosis again.
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # [0, 1, 3, 3]: a GAP to `_shortfall`, contiguous to a list-based check.
    held = (record_read.Turn(turn_id="t0", index=0, content="[user] a"),
            record_read.Turn(turn_id="t1", index=1, content="[user] b"),
            record_read.Turn(turn_id="t3a", index=3, content="[user] c"),
            record_read.Turn(turn_id="t3b", index=3, content="[user] d"))
    answer = record_read.TurnAnswer(
        answered=False, turns=held, turn_count=None,
        reason=record_read._shortfall(held, None, 5),
        kind=record_read.TurnAnswer.PARTIAL)
    assert "no turn row for index" in answer.reason, (
        "this test is vacuous unless the reason is the GAP arm")

    def fake(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return answer

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(s):
            s._real = record_read.session_turns
            record_read.session_turns = fake

        def __exit__(s, *exc):
            record_read.session_turns = s._real

    graph = _RecordingGraph([(_session_node(turn_max_index=5),)])
    with _Patch():
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "MAY BE THE CACHE'S FAULT" not in note, (
        "the record reported a gap itself; the cache's mark is irrelevant")


def test_a_LEADING_GAP_partial_is_not_blamed_on_the_cache_mark():
    """⛔ `_shortfall`'s gap arm measures from ZERO, not from `min(held)`.

    Its `span = max(held) + 1` / `missing_count = span - len(held)` treats a run
    whose EARLIEST rows are missing as a gap, while a `range(min(held), …)`
    comparison calls it contiguous — so that case quoted the record's own "no turn
    row for index 0" and then blamed the cache's mark in the next sentence
    (#11152 review round 5, P2).
    """
    from tortoise import record_read
    from tortoise import session_source_prose_migration as m

    # Indices [1, 2]: contiguous to a min-based check, a GAP to `_shortfall`.
    held = (record_read.Turn(turn_id="t1", index=1, content="[user] a"),
            record_read.Turn(turn_id="t2", index=2, content="[user] b"))
    answer = record_read.TurnAnswer(
        answered=False, turns=held, turn_count=None,
        reason=record_read._shortfall(held, None, 5),
        kind=record_read.TurnAnswer.PARTIAL)
    assert "no turn row for index 0" in answer.reason, (
        "this test is vacuous unless the reason is the LEADING-GAP arm")

    def fake(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return answer

    reader = m.record_turn_reader(object(), org_id="o", graph_id="g")

    class _Patch:
        def __enter__(s):
            s._real = record_read.session_turns
            record_read.session_turns = fake

        def __exit__(s, *exc):
            record_read.session_turns = s._real

    graph = _RecordingGraph([(_session_node(turn_max_index=5),)])
    with _Patch():
        report = m.reconcile_session_source_prose(
            graph, read_transcript=reader, dry_run=True)

    note = report["notes"]["session:s-11070"]
    assert "MAY BE THE CACHE'S FAULT" not in note, (
        "the record reported a LEADING gap itself; the cache's mark is irrelevant")


def test_the_repair_count_comes_from_the_WRITES_not_from_bucket_arithmetic():
    """`left_alone` overlaps the other buckets, so subtracting mis-counts.

    `left_alone` also holds `no_session_id`/`no_reader` nodes, which are not in
    `no_record` — the old count reported 1 repaired on a run that wrote 2.
    """
    from tortoise import session_source_prose_migration as m

    legacy = {"url": "legacy.md", "sourceKind": "agentSession",
              "summary": PROSE, "_searchText": PROSE}
    graph = _RecordingGraph([
        (_session_node(sessionId="s1", url="session:s1"),),
        (_session_node(sessionId="s2", url="session:s2"),),
        (legacy,),
    ])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday rebuild plan"),
        dry_run=False)

    assert len(report["repaired"]) == 2
    assert report["left_alone"] == ["legacy.md"]
    assert report["written"] == ["session:s1", "session:s2"], (
        "the report's own write log is the only honest repair count")
    assert len(graph.writes) == len(report["written"])


def test_a_dry_run_writes_nothing_and_reports_no_writes():
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: friday plan"))
    assert report["written"] == []
    assert graph.writes == []


def test_the_embedded_arm_honours_graph_and_the_report_names_what_it_opened(
        monkeypatch, capsys):
    """⛔ A clean verdict must never describe a graph the run did not open.

    `open_projection` dropped `graph_name` on the EMBEDDED branch, so
    `--db <path> --graph X` opened the default `tortoise` graph while printing
    `graph: X` — and `--dry-run --assert-clean` then passed over a graph the run
    never touched. And `--json` printed no graph field at all, so a
    machine-readable record of a DESTRUCTIVE run could not say which cache graph
    it wrote (#11152 review round 6).
    """
    seen = {}

    class _Proj:
        def __init__(self, graph_name):
            self.graph_name = graph_name
            self.g = _RecordingGraph([(_session_node(),)])

        def close(self):
            pass

    class _Capturing:
        def __init__(self, *a, **kw):
            seen["graph_name"] = kw.get("graph_name")
            # ⛔ NO `or "tortoise"` HERE. That normalisation is what let the
            # empty-name regression through round 6: the fake reinstated the
            # default the real constructor no longer receives (#11152 r7).
            self._inner = _Proj(kw.get("graph_name"))

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr(_cli, "FalkorProjection", _Capturing)
    monkeypatch.setattr(sys, "argv", [
        _CLI_PATH.name, "--db", "/tmp/embedded-11070", "--graph",
        "org_acme_tortoise", "--dry-run", "--json"])

    _cli.main()
    payload = json.loads(capsys.readouterr().out)
    assert seen["graph_name"] == "org_acme_tortoise", (
        "the embedded branch dropped --graph and opened the default graph")
    assert payload["graph"] == "org_acme_tortoise", (
        "--json could not name the cache graph the run addressed")


def test_the_embedded_DEFAULT_graph_is_tortoise_not_the_empty_name(
        monkeypatch, capsys):
    """⛔ `--db <path>` with NO `--graph` must open `tortoise`, not `""`.

    The empty string is a REAL, distinct, empty FalkorDB graph, so a default of
    `""` made `--dry-run --assert-clean` exit 0 over an unrepaired graph the run
    never opened — a no-op verification gate, on the arm round 6 touched
    (#11152 review round 7, MATERIAL). `sdk._derive_graph_name` returns `tortoise`
    for an embedded checkout, and so must this.
    """

    seen = {}

    class _Inner:
        def __init__(self):
            self.graph_name = "tortoise"
            self.g = _RecordingGraph([(_session_node(),)])

        def close(self):
            pass

    class _Capturing:
        def __init__(self, *a, **kw):
            seen["graph_name"] = kw.get("graph_name")
            self._inner = _Inner()

        def __getattr__(self, item):
            return getattr(self._inner, item)

    monkeypatch.setattr(_cli, "FalkorProjection", _Capturing)
    monkeypatch.setattr(sys, "argv", [
        _CLI_PATH.name, "--db", "/tmp/embedded-11070", "--dry-run", "--json"])

    _cli.main()
    assert seen["graph_name"] == "tortoise", (
        "the embedded default reached the projection as an empty graph name")
    assert json.loads(capsys.readouterr().out)["graph"] == "tortoise"


def test_every_uv_run_runbook_command_names_the_cache_graph():
    """⛔ STRUCTURE, not prose: scanning free text for commands is a trap.

    Three attempts failed in a row, each measured: matching the name and the flag
    on ONE source line found ZERO commands (every command wraps with `\\`); a
    fixed-width window bled into the next command's flags; and a segment between
    occurrences matched the flag inside step 2's PROSE ("⛔ PASS --graph"), and
    `--graph-id` contains `--graph`. The round-4/5/7 defect — a census that reads
    the wrong graph, so `--assert-clean` exits 0 over an unrepaired graph — passed
    all three (#11152 review round 10, MATERIAL).

    So this asserts on structure only: a line that STARTS a `uv run ... 11070_`
    command must carry the standalone flag (joined across `\\` continuations),
    and the two commands the MODULE emits must carry it too.
    """
    import re
    from pathlib import Path

    import tortoise.session_source_prose_migration as mod

    standalone = re.compile(r"--graph(?![\w-])")

    def _commands(text: str) -> list[str]:
        out, buf = [], ""
        for raw in text.splitlines():
            line = raw.rstrip()
            if buf:
                buf = f"{buf} {line.strip()}"
            elif line.strip().startswith("uv run python graph-scripts/11070_"):
                buf = line.strip()
            if buf and buf.endswith("\\"):
                buf = buf[:-1].rstrip()
                continue
            if buf:
                out.append(buf)
                buf = ""
        return out

    cli_commands = _commands(Path(_CLI_PATH).read_text())
    assert len(cli_commands) >= 3, (
        f"only {len(cli_commands)} runbook command(s) parsed — the parse is not "
        f"reaching them, which is how the earlier versions passed while checking "
        f"nothing: {cli_commands!r}")
    for command in cli_commands:
        assert standalone.search(command), (
            f"a runbook command does not name the cache graph: {command}")

    # The two commands the MODULE emits (the replay hook's WARNING and the
    # FAILED-path message) are what an operator copies after a rebuild.
    emitted = Path(mod.__file__).read_text()
    assert emitted.count("--graph <") >= 2, (
        "an instruction the module emits no longer names the cache graph")


def test_the_flag_still_USES_the_record_when_a_scope_is_typed(monkeypatch,
                                                              capsys):
    """⛔ `--clear-without-record` must not throw away a record you asked for.

    The flag exists so the clear arm is REACHABLE on a box that exports
    `TORTOISE_RECORD_DSN`. Passing it WITH a typed scope is an explicit statement
    that the record is to be used, so the run must keep the inherited DSN and
    re-derive where the record answers — not open no record at all and clear every
    candidate, including ones the record could have healed (#11152 round 8, P1).

    Before the fix this argv wrote `{'sum': '', 'st': None}` over an answerable
    session.
    """
    _export_a_record_dsn(monkeypatch)          # inherited DSN only
    graph = _RecordingGraph([(_session_node(),)])

    def answers(conn, *, org_id, graph_id, session_id, turn_max_index=None):
        return _OneTurn()

    _install_cli(monkeypatch, graph, [
        "--repair", "--org-id", "org_x", "--graph-id", "gx",
        "--clear-without-record"])
    monkeypatch.setattr("tortoise.record_read.session_turns", answers)

    _cli.main()
    assert len(graph.writes) == 1
    written = graph.writes[0][1]
    assert written["sum"].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
        "the inherited record was discarded although a scope was typed — the "
        "answerable session was CLEARED instead of re-derived")
    assert written["st"]


def test_the_flag_still_drops_the_inherited_dsn_when_NO_scope_is_typed(
        monkeypatch):
    """The flag's original purpose must survive: reachable where a DSN is exported."""
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record"])

    _cli.main()
    assert len(graph.writes) == 1
    assert graph.writes[0][1]["sum"] == "", (
        "the by-name clear arm did not clear where no scope was typed")


@pytest.mark.parametrize("half_argv", [
    ["--org-id", "org_x"],          # --graph-id forgotten (a typo)
    ["--graph-id", "gx"],           # --org-id forgotten
])
def test_a_HALF_typed_scope_REFUSES_instead_of_clearing_everything(
        monkeypatch, capsys, half_argv):
    """⛔ A forgotten half must not select the destructive arm (#11152 P1).

    `typed_scope_here` required BOTH halves, so a half scope counted as NO scope:
    with ONE half typed and the other forgotten, a `--clear-without-record` run
    DISCARDED the inherited `TORTOISE_RECORD_DSN`, never opened the record, and
    CLEARED every candidate — exit 0 — while the identical half scope with a
    TYPED `--record-dsn` was refused with exit 2. The omission picked the
    destructive arm, and the record was never asked.
    """
    _export_a_record_dsn(monkeypatch)          # inherited DSN only
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--repair", *half_argv,
                                      "--clear-without-record"])

    code = _cli.main()
    err = capsys.readouterr().err
    assert code == 2, (
        "a half-typed scope must REFUSE, not degrade to clear-everything")
    assert "requires BOTH --org-id and --graph-id" in err
    assert graph.writes == [], (
        "the half scope cleared every candidate instead of refusing")


def test_a_census_with_an_UNUSABLE_record_dsn_EXITS_instead_of_tracebacking(
        monkeypatch, capsys):
    """The runbook's step 3 must not die with a traceback (#11152 review P2).

    `open_record` sat OUTSIDE any `try`, so `--dry-run --assert-clean --org-id X
    --graph-id Y` on a box whose exported `TORTOISE_RECORD_DSN` is unusable
    raised `RecordServeError` out of `main()` — no census, no exit code, on the
    VERIFICATION step. A verification step that cannot run cannot fail.

    The real door is used here (no fake): a non-Postgres DSN is refused by
    `record_writer.record_dsn` BEFORE any connection is attempted, so this case
    dials nothing.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@127.0.0.1:1/none")
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "sqlite:///not-postgres.db")
    monkeypatch.delenv("TORTOISE_ORG_ID", raising=False)
    monkeypatch.setattr(sys, "argv", [
        _CLI_PATH.name, "--dry-run", "--assert-clean",
        # ⛔ THE FTS LEG IS NAMED AWAY ON PURPOSE (#11161). `--assert-clean`
        # now refuses (exit 2) unless the sign-off names its evidence, and that
        # refusal fires BEFORE any door is opened — which would make this test
        # assert about the FTS flag instead of about the DSN it exists to pin.
        "--no-fts-probe",
        "--org-id", "org_x", "--graph-id", "gx"])

    code = _cli.main()                     # must RETURN, not raise

    err = capsys.readouterr().err
    assert code == 2, "an unusable record DSN is a misconfiguration, not a crash"
    assert "could not be opened" in err
    assert "Traceback" not in err


def test_the_help_text_does_NOT_promise_the_clear_arm_is_recoverable():
    """`--help` is `description=__doc__`; an operator acts on it (#11152 P1).

    It read "…un-findable through the Source FTS index **until a run WITH the
    record re-derives it**", which the NEXT sentence in the same paragraph
    contradicted. `_derive(None)` writes `summary=''` with `_searchText` ABSENT,
    which `carries_pre_lexicon_prose` rejects at its blank check — so no later
    run selects the node again, `record-restore` declines `_searchText` for a
    pre-fix row, a re-capture is the #1727 no-op, and the CLI has no `--url`.
    """
    doc = " ".join((_cli.__doc__ or "").split())
    assert "until a run WITH the record" not in doc, (
        "--help still promises a later recorded run re-derives a cleared node")
    assert "PERMANENTLY" in doc and "NOTHING re-derives it" in doc, (
        "--help does not state that the clear arm's effect is permanent")
    # …and the claim is false for a measurable reason, not a stylistic one.
    from tortoise import session_source_prose_migration as m

    assert m.carries_pre_lexicon_prose(
        {"url": "session:x", "summary": "", "topics": ["a"]}) is False


def test_an_EMPTY_graph_name_is_refused_at_the_boundary(monkeypatch, capsys):
    """`--graph ""` is not "the default": it is a real graph, or a driver crash.

    On the served arm it reached `select_graph("")` and raised
    `TypeError: Expected a string parameter` from inside the driver, and
    `report["graph"]` would print "(URI default)" over a graph it never opened.
    """
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@127.0.0.1:1/none")
    monkeypatch.setattr(sys, "argv", [_CLI_PATH.name, "--graph", "", "--dry-run"])

    assert _cli.main() == 2
    assert "--graph may not be empty" in capsys.readouterr().err


# ── the four replay CALL SITES ────────────────────────────
#
# #11152 round 8: every hook test above calls `reconcile_after_replay` DIRECTLY,
# so deleting one of the four wiring blocks — or copying the wrong `engine=`
# literal — reded nothing. The sibling migration (#4125) shipped exactly that
# defect: its `rebuild()` site passed `engine="rebuild_all"` and misdirected a
# failure investigation. These two tests close the gap the cheap way: one drives a
# REAL `rebuild()` and asserts the hook is invoked with the right label (the defect
# 4125 shipped), and one asserts all FOUR sites exist with their own literals (a
# deleted call site would otherwise be invisible).

def test_the_REAL_rebuild_engine_invokes_the_prose_hook_with_its_own_label(
        monkeypatch, tmp_path):
    """(1) FAILS if `projection.rebuild` drops the hook or mislabels the engine.

    (2) REACHABLE: a real `rebuild()` over a real journal. The population is empty
    here on purpose — the hook runs even when it reports nothing, and the LABEL is
    what this test is about.
    """
    from tortoise import session_source_prose_migration as mod
    from tortoise.log import EventLog
    from tortoise.sdk import TortoiseSDK

    events = tmp_path / "events"
    events.mkdir()
    s = TortoiseSDK(str(tmp_path / "g.db"), namespace="test_11070_hook",
                    event_log_path=str(events / "events.jsonl"))
    calls: list[str] = []
    real = mod.reconcile_after_replay

    def spy(projection, *, engine, **kw):
        calls.append(engine)
        return real(projection, engine=engine, **kw)

    monkeypatch.setattr(mod, "reconcile_after_replay", spy)
    try:
        s._get_proj().rebuild(EventLog(str(events / "events.jsonl")),
                             confirm_destructive=True)
    finally:
        s.close()
    assert calls == ["rebuild"], (
        f"rebuild() invoked the hook as {calls!r}; #4125's defect was exactly a "
        f"wrong engine label here")


def test_EACH_replay_file_calls_THIS_hook_exactly_once_with_its_own_label():
    """⛔ AST, not a substring scan — the substring version could not fail.

    The first version of this test scanned each file for `reconcile_after_replay`
    plus an engine literal. Both ALREADY appear in those files for the #4125
    sibling hook, so deleting a #11070 call site (or mislabelling it) left the
    suite green — measured on three separate mutations (#11152 review round 9,
    MATERIAL: a gate that cannot fail).

    This walks the AST instead and keys on the #11070 IMPORT ALIAS
    (`sspm.reconcile_after_replay`), which #4125's bare `reconcile_after_replay`
    calls cannot satisfy. It therefore reds on a deleted site (count 0) and on a
    wrong label (the kwarg differs).

    Declared limit: it proves the call EXISTS with the right label, not that the
    engine reaches it at runtime. `rebuild` is driven for real by
    `test_the_REAL_rebuild_engine_invokes_the_prose_hook_with_its_own_label`; the
    other three are pinned here because each needs a distinct hostile setup
    (`rebuild_all`'s string-dir form, `recover_from_log`'s 0-node discriminator,
    `backup.restore`'s backup dir) and #4125 drives those in its own file.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    expected = {
        "tortoise/projection/__init__.py": ["rebuild", "rebuild_all"],
        "tortoise/consistency.py": ["recover_from_log"],
        "tortoise/backup.py": ["backup.restore"],
    }
    for rel, literals in expected.items():
        tree = ast.parse((root / rel).read_text())
        found: list[str | None] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and func.attr == "reconcile_after_replay"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "sspm"):
                continue
            label = None
            for kw in node.keywords:
                if kw.arg == "engine" and isinstance(kw.value, ast.Constant):
                    label = kw.value.value
            found.append(label)
        assert sorted(found) == sorted(literals), (
            f"{rel}: the #11070 hook is called as {found!r}, expected {literals!r} "
            f"— a deleted call site or a wrong engine label")


# ── the write is a COMPARE-AND-SET, not a url lookup (PR #11152 review F1) ──
#
# `:Source` declares NO uniqueness constraint on `url` — the repo's own
# reconciler names that state as REACHABLE (`record_reconcile.py`: "the graph
# declares no uniqueness constraint, so a second writer's CREATE can leave TWO
# nodes at one (label, key)"). A bare `MATCH (s:Source {url:$url})` is therefore
# a POPULATION match: with a HEALTHY sibling at the same url (a `record-restore`d
# node — no `summary`, `_searchText` = the lexicon) the write landed on the
# sibling too, and the CLEAR arm removed that node's only index entry while no
# bucket named it. Measured on a real FalkorDB: the census selected ONE node,
# the write hit TWO, `census_after` still said 0.
#
# Two legs, because either alone is defeatable: the first pins the GUARD and its
# wiring (a constant string cannot be "tested" by reading it), the second proves
# the CYPHER actually holds on the engine the repair runs against.


def test_the_write_is_a_compare_and_set_on_the_pair_the_census_read():
    """The guard, and that it compares against the READ values.

    (1) FAILS if the `WHERE` pair is dropped from `_REPAIR_PAIR` — the original
        defect — and FAILS if the parameters are not the pair the census row
        carried (an empty/absent `old_sum`/`old_st` matched NOTHING and silently
        repaired nothing: measured, after the guard was added and before the
        census started carrying the pair).
    (2) REACHABLE: an ordinary pre-fix candidate, cleared by name.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid, tmi=None: None, dry_run=False,
        clear_without_record=True)

    assert len(graph.writes) == 1, "the by-name clear arm did not write once"
    cypher, params = graph.writes[0]
    assert "coalesce(s.summary,'')=$old_sum" in cypher, (
        "the write is addressed by url alone again — a healthy sibling at the "
        "same url receives it (PR #11152 review F1)")
    assert "coalesce(s._searchText,'')=$old_st" in cypher
    assert params["old_sum"] == PROSE, (
        "the compare-and-set values must be the pair the CENSUS read, not a "
        "re-read: an empty `old_sum` matches no node, so every write becomes a "
        "silent no-op that reports `written` (measured)")
    assert params["old_st"] == PROSE
    assert report["written"] == ["session:s-11070"], (
        "a write that matched its node must still be reported as written — the "
        "guard must not turn the whole repair into a no-op")


class _WriteMatchesNothing(_RecordingGraph):
    """A graph whose WRITE matches no node (the node changed under the run).

    Reads answer normally, so the re-measured census still sees the candidate —
    which is the pair of facts the report must not contradict.
    """

    def query(self, cypher, params=None, timeout=None):
        if any(m in cypher for m in self.WRITE_MARKERS):
            self.statements.append((cypher, dict(params or {})))
            return _Result([])
        return super().query(cypher, params=params, timeout=timeout)


def test_a_write_that_matches_nothing_is_not_reported_as_repaired():
    """A compare-and-set miss must read as a MISS, not as a repair.

    (1) FAILS if `written` is appended unconditionally: the report then claims
        the pair was written while `census_after` has not moved — the same
        "report contradicts its own writes" class this module has fixed twice
        (`_add_note` on the dry run, and `written` from the write rather than
        from arithmetic over the buckets).
    (2) REACHABLE: a node that changed between the census read and the write.
    """
    from tortoise import session_source_prose_migration as m

    graph = _WriteMatchesNothing([(_session_node(),)])
    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid, tmi=None: None, dry_run=False,
        clear_without_record=True)

    assert len(graph.writes) == 1, "the guard must still issue the write"
    assert report["written"] == [], (
        "a write that matched no node was counted as repaired")
    assert report["census_after"] == report["census_before"] == 1, (
        "the census was decremented (or not re-measured) over a write that "
        "matched nothing")
    assert "NOTHING was written" in report["notes"]["session:s-11070"], (
        "the miss is not reported anywhere")


def test_a_candidate_with_no_node_id_or_a_non_string_pair_is_NAMED():
    """A candidate the write cannot compare must land in `unaddressable`.

    (1) FAILS if such a row is written anyway (a raw float reaches
        `_guard_numeric_params` and raises mid-run) or normalized to "" (which
        the Cypher-side `coalesce` cannot match: `12 = ''` is false), silently
        pinning `census_after` above zero under a note that blames a concurrent
        change — an unreachable zero, the class #11070 exists to close.
    (2) REACHABLE: a candidate holding an int `summary` (the census normalizes
        non-strings to "" and takes the blank-`summary` arm).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(summary=12, _searchText=PROSE),)])
    assert m.census(graph) == 1, "precondition: the census selects this node"

    report = m.reconcile_session_source_prose(
        graph, read_transcript=lambda sid, tmi=None: None, dry_run=False,
        clear_without_record=True)

    assert graph.writes == [], "the write must not be issued for this node"
    assert report["unaddressable"] == ["session:s-11070"]
    assert report["written"] == []
    assert "cannot be addressed" in report["notes"]["session:s-11070"]


def test_a_HEALTHY_SIBLING_at_the_same_url_is_never_written():
    """Both arms, against a real FalkorDB: the sibling at the same url survives.

    (1) FAILS on the bare `{url:$url}` match: the clear arm blanks the healthy
        node's `_searchText` (its only index entry) and the repair arm writes it
        a `summary` the record door never writes — neither reported in any
        bucket, because the census never selected that node.
    (2) REACHABLE: two `:Source` nodes at one url, one PRE-FIX candidate, one
        HEALTHY `record-restore`d node (no `summary`, lexicon `_searchText`
        its `topics` prove).
    """
    from types import SimpleNamespace

    from tests import _live_utils

    _live_utils._skip_unless_live_uri()
    import os

    from falkordb import FalkorDB

    from tortoise import session_source_prose_migration as m
    from tortoise.projection import resolve_db_endpoint

    # PER-RUN name (`_<8+ hex>` suffix): `drop_test_graph` refuses any other
    # shape, and a fixed name would sit on the shared instance for the whole
    # session (the #8746 cache-churn leak) — refused, it warned and left it.
    graph_name = f"test_11070_dupurl_{os.urandom(4).hex()}"
    endpoint = resolve_db_endpoint(os.environ["TORTOISE_DB_URI"], graph_name)
    client = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl)
    proj = SimpleNamespace(graph_name=endpoint.graph_name, db=client)
    # Epic #1647 (T7, cycle-6 P1-3 / cycle-8 P2-2): a raw client bypasses
    # `_assert_test_graph` AND the per-test wipe scope. The `test_`-prefixed
    # per-run name above makes the handle UNIQUE and OWNED; `_journal_append` is
    # the third half of that one contract — the record that tells a live PEER's
    # `scope=None` sweep this graph is in use. Without it the graph is UNOWNED and
    # a peer session may `GRAPH.DELETE` it MID-TEST (the cross-session flake
    # #3074/#3214); the `finally: drop_test_graph` below covers only this lane's
    # own teardown. Pinned by
    # `tests/test_derived_names.py::test_raw_falkordb_client_detach_uses_per_test_unique_graph`
    # (which reds on main — #11182 — while this half is missing).
    from tests._embedded import _journal_append

    # Journal the LOCAL, not `endpoint.graph_name`: they are the same string
    # (`resolve_db_endpoint` passes `graph_name` through verbatim), and the local
    # is the one whose literal (`test_11070_dupurl_…`) the ownership census can
    # PROVE owned. Reaching through the endpoint leaves the census with nothing
    # decidable, which it now fails closed on (#11217 review round 5).
    _journal_append(graph_name)
    g = client.select_graph(endpoint.graph_name)
    url = "session:dup-11152"

    def seed():
        g.query("MATCH (n) DETACH DELETE n")
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " summary:$p, _searchText:$p, topics:['friday','rebuild']})",
            {"u": url, "p": PROSE})
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " _searchText:'friday, rebuild', topics:['friday','rebuild']})",
            {"u": url})

    def pairs():
        return sorted(map(tuple, g.query(
            "MATCH (s:Source {url:$u}) RETURN coalesce(s.summary,''),"
            " coalesce(s._searchText,'')", {"u": url}).result_set))

    try:
        # ── the CLEAR arm: the sibling's index entry must survive ──────────
        seed()
        assert m.census(g) == 1
        report = m.reconcile_session_source_prose(
            g, read_transcript=lambda sid, tmi=None: None, dry_run=False,
            clear_without_record=True)
        assert report["written"] == [url], report
        assert report["census_after"] == 0, (
            "the candidate was not cleared — the compare-and-set matched nothing")
        assert pairs() == sorted([("", ""), ("", "friday, rebuild")]), (
            "the CLEAR arm wrote a node the census never selected: a healthy "
            "sibling at the same url lost its only index entry (PR #11152 "
            "review F1)")

        # ── the pair ALONE is not an identity ─────────────────────────────
        # The blank-`summary` candidate arm exists for a residue whose
        # `_searchText` is the lexicon while its `topics` do not prove it, and
        # its HEALTHY twin (same url, same lexicon, topics that DO prove it)
        # carries a BYTE-IDENTICAL pair. A value-only compare-and-set matches
        # both, so this is the one duplicate class a pair guard cannot close.
        g.query("MATCH (n) DETACH DELETE n")
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " _searchText:'friday, rebuild', topics:['friday']})", {"u": url})
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " _searchText:'friday, rebuild', topics:['friday','rebuild']})",
            {"u": url})
        assert m.census(g) == 1, "precondition: exactly one of the twins is a candidate"
        report = m.reconcile_session_source_prose(
            g, read_transcript=lambda sid, tmi=None: None, dry_run=False,
            clear_without_record=True)
        assert report["census_after"] == 0, report
        assert pairs() == sorted([("", ""), ("", "friday, rebuild")]), (
            "the CLEAR arm blanked the healthy twin (identical pair, different "
            "`topics`): the match is not a node match — the census must carry "
            "`id(s)`")

        # ── the REPAIR arm: the sibling must not gain a `summary` ──────────
        seed()
        report = m.reconcile_session_source_prose(
            g, read_transcript=lambda sid, tmi=None: "We chose the Friday rebuild.",
            dry_run=False)
        assert report["written"] == [url], report
        after = pairs()
        assert ("", "friday, rebuild") in after, (
            "the REPAIR arm wrote the healthy sibling — it now carries a "
            "`summary` the record door never writes")
        assert len(after) == 2 and report["census_after"] == 0, after
    finally:
        _live_utils.drop_test_graph(proj)


def test_the_printed_and_json_detail_never_carries_the_utterance(monkeypatch,
                                                                 capsys):
    """The census carries the pair for the write; the REPORT must not print it.

    (1) FAILS if `candidate_detail` is built from the census dict wholesale (the
        natural shape when the pair is added to it), and would ALSO fail if the
        pair were dropped from the census instead — the write would then compare
        against values no census read, and `written` would be empty (pinned by
        `test_the_write_is_a_compare_and_set_on_the_pair_the_census_read`).
    (2) REACHABLE: a dry run over one pre-fix candidate, `--json`.
    """
    # ⛔ THE BOX THE STEP RUNS ON: the URI is EXPORTED, because the CLI refuses
    # `--db`-less runs without one ("no DB URI") before it ever prints a report.
    # Without this the test reds on the ENVIRONMENT, not on the redaction it
    # claims to prove — a gate that fails for the wrong reason is not a gate.
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    _install_cli(monkeypatch, graph, ["--dry-run", "--json"])

    assert _cli.main() == 0
    out = capsys.readouterr().out
    payload = json.loads(out)

    assert PROSE not in out, (
        "the user's own utterance reached stdout — `candidate_detail` is "
        "printed and serialised, and this population's `summary` IS a "
        "transcript")
    detail = payload["candidate_detail"][0]
    assert set(detail) == {"url", "session_id", "turn_max_index",
                           "summary_bytes", "search_text_bytes",
                           # #11176: an INT graph-health counter, whitelisted on
                           # purpose — a dedupe nobody can see hides the
                           # duplicate-`:Session` signal.
                           "duplicate_session_rows"}, detail
    assert detail["summary_bytes"] == len(PROSE)
    assert detail["duplicate_session_rows"] == 0, (
        "a node reached by ONE `:Session` reports a duplicate row")


# ── #11176: the census counts NODES, not rows — and the mark is the STRONGEST ──


def _two_rows_one_node(*, marks=(3, 7), node_id=41, session_id="dup"):
    """Two `:Session` rows for ONE `:Source` — the shape `_SOURCE_ROWS` fans out.

    `:Session.id` carries a plain INDEX and no UNIQUE constraint (this repo has
    zero `CREATE CONSTRAINT` statements; every writer is a `MERGE`), so a second
    `:Session` node at one id is schema-permitted, and the OPTIONAL MATCH then
    yields one row per session for a single `:Source`.
    """
    return [
        (_session_node(node_id=node_id, sessionId=session_id,
                       url=f"session:{session_id}", turn_max_index=marks[0]),),
        (_session_node(node_id=node_id, sessionId=session_id,
                       url=f"session:{session_id}", turn_max_index=marks[1]),),
    ]


def test_ONE_source_reached_by_two_session_rows_is_ONE_census_record():
    """#11176: the census doubled a node, inflating the number #10000 is measured by.

    (1) FAILS on the un-deduped census: `_SOURCE_ROWS` yields one row per
        matching `:Session`, `find_session_sources_carrying_prose` appended one
        record per ROW, and nothing deduped on the `id(s)` the module already
        carried — `census()` returned 2 for ONE node.
    (2) REACHABLE: two `:Session` nodes at one `sessionId`, which the schema
        permits (no `CREATE CONSTRAINT`; `:Session(id)` is a plain index).
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph(_two_rows_one_node())

    found = m.find_session_sources_carrying_prose(graph)

    assert len(found) == 1, (
        f"ONE node was counted {len(found)} times — `census_after` is the number "
        f"#11070's repair is verified by, so an inflated census is a wrong "
        f"verification, not a cosmetic duplicate")
    assert found[0]["node_id"] == 41
    assert found[0]["duplicate_session_rows"] == 1, (
        "the duplicate-`:Session` signal is hidden, not reported")
    assert m.census(graph) == 1


def test_the_mark_handed_to_the_reader_is_the_STRONGEST_not_the_FIRST():
    """#11176, the safety-relevant half: REFUSE-vs-WRITE must not be row order.

    A `turn_max_index` BELOW the session's true end makes `record_read` serve a
    truncated transcript AS COMPLETE, and the lexicon derived from half a
    transcript is the fabricated descriptor this module refuses to write. A mark
    ABOVE the end has its own named cause (`_no_record_note` blames the cache),
    so the strongest mark loses nothing.
    """
    from tortoise import session_source_prose_migration as m

    seen: list[int | None] = []

    def reader(session_id, turn_max_index=None):
        seen.append(turn_max_index)
        return "User: the friday rebuild plan"

    for marks in ((3, 7), (7, 3)):
        seen.clear()
        report = m.reconcile_session_source_prose(
            _RecordingGraph(_two_rows_one_node(marks=marks)),
            read_transcript=reader, dry_run=True)
        assert seen == [7], (
            f"rows {marks} handed the reader {seen} — the tail witness is chosen "
            f"by ROW ORDER, so a mark BELOW the true end can serve a truncated "
            f"transcript as COMPLETE")
        assert report["candidates"] == ["session:dup"]


def test_a_duplicated_node_reports_ONE_write_and_no_self_contradicting_note():
    """The report must not contradict its own writes (#11176, report half).

    Before the dedupe, the SECOND row of an already-written node emitted a
    compare-and-set miss note — *"the write matched NO :Source at this url …
    so NOTHING was written; re-run"* — beside a `written` entry for the same
    node, and offered a re-run that finds ZERO candidates (the node is already
    cleared). Two of the three symptoms are the "report contradicts its own
    writes" class.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph(_two_rows_one_node())
    report = m.reconcile_session_source_prose(
        graph, read_transcript=_reader("User: the friday rebuild plan"),
        dry_run=False)

    assert report["candidates"] == ["session:dup"], (
        "the same url listed twice is the inflated census again")
    assert report["repaired"] == ["session:dup"]
    assert report["written"] == ["session:dup"]
    assert len(graph.writes) == 1, "ONE node, ONE write"
    assert "NOTHING was written" not in report["notes"].get("session:dup", ""), (
        "the report says a node it just wrote was not written")


def test_a_mark_that_is_absent_on_one_row_is_recovered_from_the_other():
    """A non-`int` mark is not a mark — the other row's mark wins, never `None`.

    `_read_source_rows` maps a missing / non-int `turn_max_index` to `None`,
    which makes the tail arm unreachable and degrades the witness silently. A
    duplicate row that DOES carry the mark is the stronger evidence, so the
    dedupe must not keep the degraded `None` merely because it arrived first.
    """
    from tortoise import session_source_prose_migration as m

    for rows in (_two_rows_one_node(marks=(None, 7)),
                 _two_rows_one_node(marks=(7, None))):
        found = m.find_session_sources_carrying_prose(_RecordingGraph(rows))
        assert found[0]["turn_max_index"] == 7, (
            "the usable mark was dropped in favour of the degraded one")


def test_the_OFF_SWITCH_restores_the_pre_fix_census_exactly(monkeypatch):
    """The off-switch is part of the fix, not decoration.

    This dedupe changes the NUMBER the repair is verified by, so an operator
    comparing a fresh census with a recorded one must be able to re-measure with
    the OLD instrument. `=1` restores it EXACTLY: one record per ROW, first-row
    mark, no duplicate count.
    """
    monkeypatch.setenv("TORTOISE_11070_CENSUS_DEDUPE_DISABLED", "1")
    from tortoise import session_source_prose_migration as m

    assert m._census_dedupe_enabled() is False

    graph = _RecordingGraph(_two_rows_one_node(marks=(3, 7)))
    found = m.find_session_sources_carrying_prose(graph)
    assert [n["turn_max_index"] for n in found] == [3, 7], (
        "the old path must keep ROW ORDER — that is what 'the old instrument' means")
    assert m.census(graph) == 2
    assert all("duplicate_session_rows" not in n for n in found), (
        "the off-switch reported a duplicate count it never took — `0` is a "
        "measurement, and this run did not take one (#11176 review P2)")

    reversed_graph = _RecordingGraph(_two_rows_one_node(marks=(7, 3)))
    assert [n["turn_max_index"]
            for n in m.find_session_sources_carrying_prose(reversed_graph)] == [7, 3]

    monkeypatch.delenv("TORTOISE_11070_CENSUS_DEDUPE_DISABLED")
    assert m._census_dedupe_enabled() is True
    assert m.census(graph) == 1, "clearing the switch did not restore the fix"


def test_the_duplicate_session_signal_reaches_the_OPERATOR_report(monkeypatch,
                                                                 capsys):
    """The census settles #11176's open question: it REPORTS, it does not hide.

    A dedupe nobody can see is the same silence #11070 exists to end: the
    duplicate `:Session` is graph-health, and the census (which scans every
    `:Source`) is the only instrument that meets it for free. So the count
    reaches BOTH operator surfaces — `--json` (`candidate_detail`) and the
    human-readable line — and stays an INT, so no utterance rides along.

    (1) FAILS if the whitelist drops the key (the JSON arm) or the print line
        omits it (the human arm).
    (2) REACHABLE: the shape `_SOURCE_ROWS` produces for a duplicated
        `:Session`, through the real CLI entry point.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph(_two_rows_one_node())
    _install_cli(monkeypatch, graph, ["--dry-run", "--json"])

    assert _cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    detail = payload["candidate_detail"]
    assert len(detail) == 1, f"one node reported {len(detail)} times"
    assert detail[0]["duplicate_session_rows"] == 1, (
        "`--json` deduped the node and hid the duplicate-`:Session` signal")

    # ── the human arm: same run, no `--json` ───────────────────────────────
    _install_cli(monkeypatch, _RecordingGraph(_two_rows_one_node()),
                 ["--dry-run"])
    assert _cli.main() == 0
    out = capsys.readouterr().out
    assert "duplicate_session_rows=1" in out, (
        "the human report collapsed the duplicate silently")


def test_a_clean_census_report_carries_NO_duplicate_marker(monkeypatch, capsys):
    """The counter must not appear on a clean run — noise is how a signal dies.

    (1) FAILS if the print suffix is unconditional: every ordinary census then
        reads `duplicate_session_rows=0`, which trains the operator to ignore it.
    (2) REACHABLE: the ordinary one-row-per-node census.
    """
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph([(_session_node(),)]),
                 ["--dry-run"])

    assert _cli.main() == 0
    out = capsys.readouterr().out
    assert "duplicate_session_rows" not in out, out


def test_a_duplicate_Session_row_does_not_double_the_census_on_a_REAL_graph():
    """#11176 against a real FalkorDB: the fan-out is the SERVER's, not the fake's.

    The unit legs above hand `find_session_sources_carrying_prose` the rows the
    fan-out is *assumed* to produce. This leg makes the server produce them: one
    `:Source` whose `sessionId` is carried by two `:Session` nodes — the shape
    `#11176` measured — so a fix that relied on the fake's arrangement, or on
    `OPTIONAL MATCH` collapsing the rows, cannot pass here.

    (1) FAILS on the un-deduped census: `census()` returns 2 for ONE node and the
        reported mark is one of `(3, 7)` picked by row order.
    (2) REACHABLE: two `:Session` nodes at one `sessionId`, which the schema
        permits (no `CREATE CONSTRAINT`; `:Session(id)` is a plain index).
    """
    import os
    from types import SimpleNamespace

    from tests import _live_utils

    _live_utils._skip_unless_live_uri()
    from falkordb import FalkorDB

    from tests._embedded import _journal_append
    from tortoise import session_source_prose_migration as m
    from tortoise.projection import resolve_db_endpoint

    # PER-RUN name: `drop_test_graph` refuses any other shape, and a fixed name
    # would sit on the shared instance for the whole session (#8746).
    graph_name = f"test_11070_dupsess_{os.urandom(4).hex()}"
    endpoint = resolve_db_endpoint(os.environ["TORTOISE_DB_URI"], graph_name)
    client = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl)
    proj = SimpleNamespace(graph_name=endpoint.graph_name, db=client)
    # Epic #1647: a raw client bypasses `_assert_test_graph` and the per-test
    # wipe scope, so the graph must be OWNED before a peer can sweep it.
    #
    # Journal the LOCAL, not `endpoint.graph_name`: `resolve_db_endpoint` passes
    # `graph_name` through verbatim (`graph_name if graph_name is not None`), so
    # they are the same string — but only the local's literal
    # (`test_11070_dupsess_…`) carries an owned prefix the ownership census can
    # PROVE. Reaching through the endpoint leaves the census nothing decidable,
    # and it fails closed on that (#11217; same repair as the sibling site at
    # `test_a_HEALTHY_SIBLING_at_the_same_url_is_never_written`).
    _journal_append(graph_name)
    g = client.select_graph(endpoint.graph_name)
    url = "session:dup-sessions"

    def seed(marks):
        g.query("MATCH (n) DETACH DELETE n")
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " summary:$p, _searchText:$p, topics:['friday','rebuild']})",
            {"u": url, "p": PROSE})
        for mark in marks:
            g.query("CREATE (:Session {id:'dup', turn_max_index:$t})", {"t": mark})

    try:
        for marks in ((3, 7), (7, 3)):
            seed(marks)
            found = m.find_session_sources_carrying_prose(g)
            assert len(found) == 1, (
                f"the server fanned ONE `:Source` into {len(found)} rows "
                f"(sessions seeded {marks}) and the census counted each — "
                f"`census_after` is the number this repair is verified by")
            assert found[0]["turn_max_index"] == 7, (
                f"sessions seeded {marks}: the tail witness was "
                f"{found[0]['turn_max_index']}, not the session's end — a mark "
                f"below the end serves a truncated transcript as COMPLETE")
            assert m.census(g) == 1
    finally:
        _live_utils.drop_test_graph(proj)


def test_the_off_switch_reports_the_ROW_level_census_against_a_REAL_graph():
    """The kill switch on the server's own fan-out, not only on the fake's.

    An operator re-measuring with the OLD instrument must get the OLD number, and
    'the old number' is defined by what the SERVER returns — not by what a fake
    was arranged to return. ``=1`` ⇒ one record per row, so ONE node counts twice
    and the mark is the first row's.

    (1) FAILS if the switch is read at import time, cached, or applied to only
        part of the loop: any of those leaves the fixed path (or a half-fixed
        one) in place.
    (2) REACHABLE: the same duplicate-`:Session` shape the repair meets live.
    """
    import os
    from types import SimpleNamespace

    from tests import _live_utils

    _live_utils._skip_unless_live_uri()
    from falkordb import FalkorDB

    from tests._embedded import _journal_append
    from tortoise import session_source_prose_migration as m
    from tortoise.projection import resolve_db_endpoint

    graph_name = f"test_11070_dupoff_{os.urandom(4).hex()}"
    endpoint = resolve_db_endpoint(os.environ["TORTOISE_DB_URI"], graph_name)
    client = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl)
    proj = SimpleNamespace(graph_name=endpoint.graph_name, db=client)
    # Journal the LOCAL, not `endpoint.graph_name` — same value
    # (`resolve_db_endpoint` passes `graph_name` through verbatim), but only the
    # local's `test_11070_dupoff_…` literal carries an owned prefix the census
    # can PROVE; reaching through the endpoint is what it fails CLOSED on.
    _journal_append(graph_name)
    g = client.select_graph(endpoint.graph_name)

    previous = os.environ.pop("TORTOISE_11070_CENSUS_DEDUPE_DISABLED", None)
    try:
        g.query("MATCH (n) DETACH DELETE n")
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'dup',"
            " summary:$p, _searchText:$p, topics:['friday','rebuild']})",
            {"u": "session:dup-sessions-off", "p": PROSE})
        for mark in (3, 7):
            g.query("CREATE (:Session {id:'dup', turn_max_index:$t})", {"t": mark})

        assert m.census(g) == 1, "precondition: the fixed instrument counts one node"

        os.environ["TORTOISE_11070_CENSUS_DEDUPE_DISABLED"] = "1"
        assert m.census(g) == 2, (
            "the off-switch did not restore the OLD number: an operator comparing "
            "a fresh census against a recorded one cannot re-measure with the "
            "instrument that recorded it")
        rows = m.find_session_sources_carrying_prose(g)
        assert sorted(n["turn_max_index"] for n in rows) == [3, 7]
        assert all("duplicate_session_rows" not in n for n in rows), (
            "the off-switch reported a duplicate count it never took on a real "
            "graph (#11176 review P2)")

        del os.environ["TORTOISE_11070_CENSUS_DEDUPE_DISABLED"]
        assert m.census(g) == 1, "clearing the switch did not restore the fix"
    finally:
        if previous is not None:
            os.environ["TORTOISE_11070_CENSUS_DEDUPE_DISABLED"] = previous
        else:
            os.environ.pop("TORTOISE_11070_CENSUS_DEDUPE_DISABLED", None)
        _live_utils.drop_test_graph(proj)


def test_the_off_switch_never_reports_an_UNMEASURED_zero(monkeypatch, capsys):
    """#11176 review P2: a count that was never taken must not print as `0`.

    Under `TORTOISE_11070_CENSUS_DEDUPE_DISABLED=1` no de-duplication happens, so
    `duplicate_session_rows` was never measured. Emitting `0` there is worse than
    emitting nothing: a duplicate-`:Session` population reads as "no duplicates"
    in `--json`, which is the graph-health signal this field exists to carry —
    the same silence, wearing the shape of a measurement.

    (1) FAILS if the key is initialised on the record's base shape (reported as
        `0` on every row of a two-`:Session` node) instead of inside the dedupe
        branch.
    (2) REACHABLE: the off-switch on the duplicate-`:Session` fixture, through
        the module API and through the CLI's `--json` payload.
    """
    from tortoise import session_source_prose_migration as m

    monkeypatch.setenv("TORTOISE_11070_CENSUS_DEDUPE_DISABLED", "1")
    found = m.find_session_sources_carrying_prose(
        _RecordingGraph(_two_rows_one_node()))
    assert len(found) == 2, "precondition: the old instrument sees both rows"
    assert [n.get("duplicate_session_rows") for n in found] == [None, None], (
        "the un-deduped run reported a duplicate count it never measured")

    # …and the operator surface must not manufacture one either.
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph(_two_rows_one_node()),
                 ["--dry-run", "--json"])
    assert _cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert [d["duplicate_session_rows"] for d in payload["candidate_detail"]] \
        == [None, None], (
        "`--json` said `0` duplicates on a run that never counted them")

    # With the switch OFF the same fixture IS measured (so `None` is the absence
    # of a measurement, not a bug in the field).
    monkeypatch.delenv("TORTOISE_11070_CENSUS_DEDUPE_DISABLED")
    row = m.find_session_sources_carrying_prose(
        _RecordingGraph(_two_rows_one_node()))[0]
    assert row["duplicate_session_rows"] == 1


# ══════════════════════════════════════════════════════════════════════════
#
# THE FTS LEG (#11161) — the repair's SECOND verification surface.
#
# #11070's own indicator: "the whole point is that the sentence is also in the
# FTS index, so the verification is a search that stops finding it". The census
# reads NODE PROPERTIES; the Source FULLTEXT index on `_searchText` is a
# different artifact, so a census-only green certifies the half that cannot break
# independently. The legs below pin the three ways a naive probe misleads, and
# each is a MEASURED shape, not a hypothetical:
#
# * a hit is not a leak — FalkorDB stems, so `fridays` still returns a node whose
#   `_searchText` is already the post-fix lexicon `migration, plan, friday`;
# * a zero from a dead read is not a zero — an open breaker / a dropped index
#   returns `[]` exactly as a clean miss does;
# * a multi-token probe is not a probe — the read OR-joins it, so it matches on
#   any of its words and a CORRECT repair would read as leaked.

#: A pre-fix node's prose, with a token that appears nowhere else.
FTS_PROSE = "We decided the weekly zebraquux rebuild happens on Fridays."


def _install_fts(monkeypatch, mapping, *, leg=None):
    """Replace the product's one source-FTS read; keep the leg_trace contract.

    ``mapping`` is ``{query: [(url, score), ...]}``. ``leg`` overrides the trace
    entry so a breaker-open / index-missing read can be simulated — the shapes
    that return ``[]`` while measuring NOTHING.
    """
    from tortoise import search_engine as se

    calls: list[dict] = []

    def fake(graph, query, entity_type="point", limit=20, timeout_ms=500,
             excluded_statuses=None, leg_trace=None, keep_numeric=False,
             expansion_terms=None):
        rows = [tuple(r) for r in mapping.get(query, [])]
        calls.append({"query": query, "entity_type": entity_type,
                      "limit": limit})
        if leg_trace is not None:
            leg_trace.append(dict(
                leg or {"leg": "fts", "ran": True, "degraded": False,
                        "reason": "ok" if rows else "empty_results"},
                count=len(rows)))
        return rows

    monkeypatch.setattr(se, "run_fts_query", fake)
    return calls


def test_a_MULTI_token_probe_is_REFUSED_because_the_FTS_read_OR_joins_it(
        monkeypatch):
    """A phrase is not a probe — the read turns it into an OR of its words.

    (1) FAILS on a naive probe: `build_or_query('the second-latest PR comment')`
        → `comment|second|latest|pr`, so the read returns rows for ANY of those
        words. Measured on the live hosted graph, the single token `the` returns
        the pre-fix fixture at rank 1 — a CORRECT repair would read as "leaked",
        and the way an operator silences that is by clearing the post-fix lexicon
        (the #3518 descriptor this repair must not destroy).
    (2) REACHABLE: both inputs are ordinary operator guesses, and `the` is the
        most likely first guess of all.

    ⛔ THE ASSERTION IS PINNED TO THE SHIPPED SWITCH VALUE (#11536 review round 2,
    non-blocking note, now closed): `zx-99` is refused because the IDENTIFIER
    mechanism expands it, and that mechanism is default-ON but switchable
    (`TORTOISE_IDENTIFIER_TOKENS=0` is a legitimate outage lever). With it off in
    an inherited shell, `zx-99` tokenises to the single term `zx` and the row
    would red for a reason that is not the behaviour it claims to protect — the
    same "inherited shell changes the measurement" class the door's pin set
    exists for.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    monkeypatch.setenv("TORTOISE_IDENTIFIER_TOKENS", "1")

    for phrase in ("the second-latest PR comment", "the", "zx-99", "   "):
        assert probe_phrase_rejection(phrase), (
            f"{phrase!r} was accepted as a probe — the FTS read OR-joins it, so "
            f"it measures the OR of its words, not the sentence")
    assert probe_phrase_rejection("zebraquux") is None
    assert probe_phrase_rejection("Fridays.") is None, (
        "a single token with punctuation is still ONE token — the read escapes "
        "it and it matches")


def test_a_hit_on_a_REPAIRED_node_is_a_STEM_COLLISION_not_a_leak(monkeypatch):
    """The index STEMS, so a repaired node can answer a probe token.

    (1) FAILS without the classifier: `run_fts_query` returns the url of a node
        whose `_searchText` is the post-fix lexicon and the naive reading calls
        that "the sentence is still findable" — reported against a repair that
        worked (measured: the probe `fridays` matches the lexicon token `friday`).
    (2) REACHABLE: the lexicon's words come from the transcript's own word
        frequencies, so a probe token from the prose collides with them whenever
        they share a stem.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"friday": [("session:s-post", 2.0)]})

    report = m.probe_fts(graph, ["friday"], control=False)
    assert report["leaked"] == [], (
        "a repaired node's own lexicon entry was reported as a LEAK — that "
        "invites clearing the #3518 descriptor")
    assert report["stem_only"] == ["friday"]
    assert report["probes"][0]["lexicon"] == ["session:s-post"]
    assert report["probes"][0]["residue"] == []


def test_a_hit_on_a_PRE_FIX_node_IS_a_leak(monkeypatch):
    """The other polarity, and it is the one that must RED.

    (1) FAILS on any census-only verification: the census sees a node whose
        properties still carry the prose and the FTS read still returns it.
    (2) REACHABLE: the pre-fix population, which is the state the repair exists
        for.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(url="session:pre-fix",
                                            summary=FTS_PROSE,
                                            _searchText=FTS_PROSE),)])
    _install_fts(monkeypatch, {"zebraquux": [("session:pre-fix", 3.0)]})

    report = m.probe_fts(graph, ["zebraquux"], control=False)
    assert report["leaked"] == ["zebraquux"]
    assert report["probes"][0]["residue"] == ["session:pre-fix"]


def test_a_hit_on_a_CLEARED_node_is_a_STALE_INDEX_LEAK_not_a_stem_collision(monkeypatch):
    """#11161 review round 2, P1 (fail-open): "not residue" is NOT "lexicon".

    (1) FAILS on a two-way split: `carries_pre_lexicon_prose` is False for a node
        whose two text columns are EMPTY, so the shape the leg exists for — the
        pair CLEARED on the node while the index entry survived (#11161's own
        words) — landed in `lexicon`, which never vetoes. `_merge_probe` then saw
        `residue=[]`, `unread=[]` and an answering control, and certified: exit 0
        and "Repair complete." over the exact leak, while `_report_fts` printed
        "stem collision — 1 hit(s) on nodes whose `_searchText` is already the
        post-fix lexicon" about a node with no `_searchText` at all.
    (2) REACHABLE: it is the issue's stated failure mode. Measured on FalkorDB
        4.20.6 the entry DOES go away with the property, which is why the live
        leg reads 0 — so this arm is fail-closed rather than a live population,
        and it is exactly why the fold must not depend on the engine agreeing.
    (3) AND THE OTHER POLARITY: a `record-restore`d node (no `summary`, lexicon
        `_searchText`) is PROVEN by `topics`, stays a stem collision, and the
        measured `friday`/`fridays` collision does not turn red.
    """
    from tortoise import session_source_prose_migration as m

    cleared = _session_node(url="session:cleared", sessionId="cleared",
                            summary="", _searchText=None,
                            topics=["friday", "rebuild"])
    graph = _RecordingGraph([(cleared,)])
    _install_fts(monkeypatch, {"zebraquux": [("session:cleared", 1.0)]})

    split = m.classify_fts_hits(graph, ["session:cleared"])
    assert split == {"residue": [], "lexicon": [], "stale": ["session:cleared"],
                     "foreign": [], "unexplained": [], "unread": []}, (
        "a CLEARED node's surviving index entry was classified as the post-fix "
        "lexicon — that is the #11161 leak reported as a stem collision")

    report = m.probe_fts(graph, ["zebraquux"], control=False)
    assert report["leaked"] == [] and report["stale"] == ["zebraquux"]
    folded = _cli._merge_probe({"ran": True, "probes": [{"phrase": "zebraquux",
                                                          "hits": 1,
                                                          "residue": []}]},
                              report)
    assert folded["certifies"] is False and folded["vetoed"] == "stale-index", (
        "the stale-index leak certified")

    # …and it is not corroborated away by a control or a BEFORE reading: this is
    # the writing arm, which had residue before the repair — the worst case.
    folded = _cli._merge_probe(
        {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                   "urls": ["session:cleared"],
                                   "residue": ["session:cleared"]}]},
        dict(report, control={"mode": "probe", "ok": True, "hits": 1}))
    assert folded["certifies"] is False and folded["vetoed"] == "stale-index"

    # (3) the proof-backed post-fix node is still a stem collision, not a leak.
    _install_fts(monkeypatch, {"friday": [("session:restored", 2.0)]})
    restored = _session_node(url="session:restored", sessionId="restored",
                             summary="", _searchText="friday, rebuild",
                             topics=["friday", "rebuild"])
    split = m.classify_fts_hits(_RecordingGraph([(restored,)]),
                               ["session:restored"])
    assert split["lexicon"] == ["session:restored"] and split["stale"] == [], (
        "a record-restored node PROVES the lexicon through `topics` — calling it "
        "stale would red a correct repair")


def test_a_hit_with_no_addressable_url_cannot_certify(monkeypatch):
    """#11161 review round 2, P2 (fail-open): a hit can vanish between the reads.

    (1) FAILS on the shipped probe: `urls` keeps only str rows while `hits` counts
        every row, so a hit whose ``node.url`` is not a string is in neither
        bucket — not residue, not lexicon, not stale, not unread — and the run
        certifies with `hits=1` and empty classifications. That is the previous
        round's doctrine ("a hit nothing examined is not evidence") reached
        through a different door.
    (2) REACHABLE: this module already has a first-class `unaddressable` bucket
        for a `:Source` without a usable url, and an index entry can outlive the
        node it points at.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"zebraquux": [(None, 1.0)],
                              "migration": [("session:s-post", 2.0)]})

    class _ControlGraph(_RecordingGraph):
        """A lexicon node the CONTROL can read, so the zero is corroborated."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    graph = _ControlGraph([(_post_fix_node(),)])
    report = m.probe_fts(graph, ["zebraquux"], control=True)
    assert report["probes"][0]["hits"] == 1
    assert report["probes"][0]["urls"] == []
    assert report["unresolved"] == 1, (
        "a hit with no usable url disappeared: it is in `hits` and in no bucket")
    assert report["control"]["ok"] is True, (
        "the control answers here on purpose — a corroborating zero must not "
        "certify around a hit that nothing examined")

    folded = _cli._merge_probe({"ran": True, "probes": []}, report)
    assert folded["certifies"] is False and folded["vetoed"] == "unclassified", (
        "an unaddressable hit certified the leg")


def test_a_PROVEN_lexicon_node_explains_the_hit_or_it_is_not_a_collision(
        monkeypatch, capsys):
    """#11536 review round 2, P1 (second half): the benign label must be PROVED.

    (1) FAILS on the shipped classifier: `lexicon` absorbed EVERY hit on a node
        that proves the post-fix pair, so an index entry that outlived a property
        update — the SAME node, now carrying the lexicon, still matching the
        REMOVED prose's token — certified as a "stem collision". `_report_fts`
        then stated, on the spot, that the node's `_searchText` accounts for the
        hit, which nothing had checked.
    (2) REACHABLE: a deferred index update on the record arm, which is the one arm
        that writes a lexicon (the clear arm's shape is the `stale` bucket).
    (3) POLARITY THAT MUST NOT GO RED: the measured `friday`/`fridays` collision,
        whose token DOES come from the node's own lexicon, stays benign.
    """
    from tortoise import session_source_prose_migration as m

    # `_post_fix_node()` proves the lexicon: summary prefix + `_searchText`
    # "migration, plan, friday".
    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"zebraquux": [("session:s-post", 2.0)]})

    report = m.probe_fts(graph, ["zebraquux"], control=False)
    assert report["probes"][0]["lexicon"] == [], (
        "a node whose OWN lexicon cannot account for the probe token was reported "
        "as a benign stem collision — that is the survived-index-entry leak")
    assert report["probes"][0]["unexplained"] == ["session:s-post"]
    assert report["stem_only"] == [], (
        "an unexplained hit was summarised as `stem_only`")
    folded = _cli._merge_probe(
        {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                   "residue": []}]},
        dict(report, control={"mode": "probe", "ok": True, "hits": 1}))
    assert folded["certifies"] is False and folded["vetoed"] == "unexplained", (
        "a corroborating control certified over a hit the node's own lexicon "
        "cannot explain")

    # argv: the writing arm must not print "Repair complete." over it.
    _export_a_record_dsn(monkeypatch)
    _install_fts(monkeypatch, {"zebraquux": [("session:s-post", 3.0)]})
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": ["session:s-post"], "repaired": [],
                            "empty_sessions": [], "no_record": ["session:s-post"],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": ["session:s-post"], "notes": {},
                            "census_before": 1, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, _RecordingGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1, (
        "the repair reported success over a hit its own lexicon cannot explain")
    captured = capsys.readouterr()
    assert "Repair complete." not in captured.out
    assert "matches none of that node's OWN lexicon tokens" in captured.err

    # (3) the measured stem collision still certifies: `fridays` folds to the
    # node's own `friday`, so the benign label IS earned here.
    _install_fts(monkeypatch, {"fridays": [("session:s-post", 2.0)]})
    report = m.probe_fts(_RecordingGraph([(_post_fix_node(),)]), ["fridays"],
                         control=False)
    assert report["probes"][0]["lexicon"] == ["session:s-post"]
    assert report["probes"][0]["unexplained"] == [], (
        "the measured `friday`/`fridays` collision was rejected — the token is "
        "the node's own lexicon word")


def test_the_uncorroborated_refusal_names_only_what_the_run_measured(monkeypatch,
                                                                    capsys):
    """#11536 review round 2, P2: a refusal must not assert unmeasured facts.

    (1) FAILS on the shipped refusal: on every documented step-3 DRY RUN it
        asserted "it returned no PRE-FIX node BEFORE the repair either" — a
        BEFORE reading a dry run never takes (its own report prints "—" for the
        field two lines above) — and "no post-fix lexicon node exists", which it
        cannot know when the control's OWN read did not run. An operator
        debugging a real leak is sent hunting for a missing lexicon node while
        the index was simply unreadable.
    (2) REACHABLE: step 3 as documented IS a dry run, and a degraded control read
        is the same circuit-breaker state the leg already separates from a clean
        miss on the probe line.
    """
    from tortoise import search_engine as se

    class _LexiconGraph(_RecordingGraph):
        """Answers the CONTROL read with a lexicon, so a token exists to probe."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    def fake(graph, query, **kwargs):
        # The PROBE's own read ran and found nothing; the CONTROL's read is the
        # degraded one. Same shape the read path records on a breaker trip.
        degraded = query == "migration"
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({
                "leg": "fts", "ran": not degraded, "degraded": degraded,
                "reason": "breaker_open" if degraded else "empty_results"})
        return []

    monkeypatch.setattr(se, "run_fts_query", fake)
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean",
                  "--probe-phrase", "zebraquux"])
    assert _cli.main() == 1, "an uncorroborated zero certified"
    err = capsys.readouterr().err
    assert "cannot be corroborated" in err
    assert "BEFORE READING WAS TAKEN" in err, (
        "the refusal did not say the BEFORE reading was never taken")
    assert "returned no PRE-FIX node BEFORE the repair" not in err, (
        "the refusal asserted a BEFORE reading this dry run never took")
    assert "no post-fix lexicon node exists" not in err, (
        "the refusal asserted the graph has no lexicon node while the CONTROL's "
        "own read was the thing that failed")
    assert "CONTROL read did not run" in err

    # …and the other polarity: on the WRITING arm the BEFORE reading WAS taken,
    # so the claim is a fact and is stated as one.
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": [], "repaired": [],
                            "empty_sessions": [], "no_record": [],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": [], "notes": {},
                            "census_before": 0, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1
    err = capsys.readouterr().err
    assert "returned no PRE-FIX node BEFORE the repair either" in err, (
        "the writing arm took a BEFORE reading, so the claim is measured and must "
        "be stated")
    assert "BEFORE READING WAS TAKEN" not in err


def test_an_EMPTY_STRING_url_is_UNRESOLVED_and_cannot_certify(monkeypatch):
    """#11536 review round 3, P1 (fail-open): the second door onto the same bucket.

    (1) MEASURED on the round-2 tree: `source_fts_probe` kept every
        `isinstance(row[0], str)` row in `urls`, so a hit whose `node.url` is the
        EMPTY STRING made `len(rows) - len(urls)` **0** — resolved — while
        `classify_fts_hits`'s truthiness filter dropped it. Result: `hits=1`,
        `unresolved=0`, and five EMPTY buckets; `_merge_probe` then returned
        `certifies=True, vetoed=None` with an answering control. The run printed
        "no node still carrying the pre-fix prose is findable" over a hit nothing
        examined — the same class as the non-string url, one predicate short.
    (2) ROOT CAUSE: two predicates for one question. The probe asked "is this a
        str?" and the classifier asked "is this a USABLE str?", so the shapes
        between them were counted as resolved and then dropped. Both now call
        :func:`_addressable`, which no longer leaves a gap to rediscover.
    (3) AND THE SURFACE THE FIX HAS TO CARRY (#11536 review round 3, P2 on this
        very test): `stem_only` documented "hits, but every one is a lexicon node"
        and reported this probe as a benign collision while `lexicon` was EMPTY,
        and the refusal printed `for []` plus two causes neither of which applied.
        Both are asserted below, so the next lane cannot re-open either.
    """
    from tortoise import session_source_prose_migration as m

    _install_fts(monkeypatch, {"zebraquux": [("", 1.0)],
                              "migration": [("session:s-post", 2.0)]})

    class _ControlGraph(_RecordingGraph):
        """A lexicon node the CONTROL can read, so the zero is corroborated."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    report = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), ["zebraquux"],
                         control=True)
    assert report["probes"][0]["hits"] == 1
    assert report["probes"][0]["urls"] == []
    assert report["unresolved"] == 1, (
        "an empty-string url was counted as RESOLVED: the probe kept it in `urls` "
        "and the classifier then dropped it, so the hit reached no bucket")
    assert report["control"]["ok"] is True, (
        "the control answers on purpose: a corroborated zero must not certify "
        "around a hit that nothing examined")
    assert report["stem_only"] == [], (
        "`stem_only` documents 'hits, every one a lexicon node' — this probe's "
        "`lexicon` bucket is EMPTY, so reporting it as a benign stem collision "
        "contradicts its own documented meaning in `--json`")

    # (3) the refusal must name the cause that applies, not two that do not.
    folded = _cli._merge_probe({"ran": True, "probes": []}, report)
    assert folded["certifies"] is False and folded["vetoed"] == "unclassified", (
        "a hit with an unaddressable url certified the leg")
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        _cli._report_fts(folded)
    said = buf.getvalue()
    assert "could not be ADDRESSED" in said, (
        "the refusal did not name the unaddressable url as the cause: " + said)
    assert "COULD NOT BE CLASSIFIED for []" not in said, (
        "the refusal named an empty phrase list and a classification-read "
        "failure that did not happen: " + said)


def test_a_BREAKER_OPEN_read_is_UNMEASURABLE_not_a_clean_zero(monkeypatch):
    """A leg that did not run returns `[]` — exactly as a clean miss does.

    (1) FAILS on a leg that reads `hits` alone: an open circuit breaker (or a
        dropped index) yields 0 hits and the run certifies a leak as repaired.
    (2) REACHABLE: `run_fts_query` short-circuits to `[]` on a breaker that is
        open, and returns `[]` with `reason="index_missing"` when the index is
        gone — both are recorded in `leg_trace`, which is the only signal that
        separates them from a genuine zero.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {}, leg={"leg": "fts", "ran": False,
                                       "degraded": True, "reason": "breaker_open"})

    report = m.probe_fts(graph, ["zebraquux"], control=True)
    assert report["probes"][0]["hits"] == 0
    assert report["unmeasurable"] == ["zebraquux"], (
        "a probe whose read never ran was reported as a clean zero")
    assert report["leaked"] == []


def test_a_probe_that_FILLS_the_window_is_TRUNCATED_not_certified(monkeypatch):
    """A count that reaches the LIMIT is a FLOOR: residue could hide past it.

    (1) FAILS on a leg that reads only `hits`: 50 hits classified cleanly says
        nothing about hit 51, so a common token certifies a leaked graph.
    (2) REACHABLE: any token common enough to fill 50 rows — the same class the
        OR-union pathology comes from.
    """
    from tortoise import session_source_prose_migration as m

    node = _post_fix_node()
    rows = [(f"session:s-{i}", 1.0) for i in range(m._FTS_PROBE_LIMIT)]
    graph = _RecordingGraph([(node,)])
    _install_fts(monkeypatch, {"plan": rows})

    report = m.probe_fts(graph, ["plan"], control=False)
    assert report["probes"][0]["truncated"] is True
    assert report["truncated"] == ["plan"]


def test_the_control_TOKEN_is_measured_and_never_ECHOED(monkeypatch, capsys):
    """The positive control proves the read ANSWERS — without leaking a word.

    (1) FAILS without a control: a probe that reads 0 on a graph whose index is
        empty (or unreadable) is indistinguishable from a probe that reads 0
        because the sentence left.
    (2) REACHABLE: a post-fix lexicon node, whose `_searchText` is exactly what
        the index is declared on.

    ⛔ AND THE TOKEN STAYS OUT OF THE REPORT: it is a transcript-derived word,
    and `candidate_detail` is redacted by WHITELIST precisely so no piece of the
    utterance reaches stdout or `--json`.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        """Answers the CONTROL read with a lexicon, as a real Source would.

        The base fake projects every read through `COLUMNS` (the census layout),
        so it cannot answer a one-column read with ONE column — and the control's
        token is exactly what this test is about.
        """

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    graph = _LexiconGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"migration": [("session:s-post", 2.0)]})

    report = m.probe_fts(graph, ["zebraquux"], control=True)
    control = report["control"]
    assert control["ok"] is True and control["hits"] == 1
    assert "migration" not in json.dumps(report), (
        "the control's lexicon token reached the report — it is transcript text")

    # …and the CLI prints the COUNT, not the token.
    clean = _LexiconGraph([(_post_fix_node(),)])
    _install_cli(monkeypatch, clean, ["--dry-run", "--assert-clean",
                                      "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 0
    out = capsys.readouterr().out
    assert "control" in out
    assert "migration" not in out, (
        "the CLI echoed the control's lexicon token")


def test_a_census_only_signoff_is_REFUSED_and_no_fts_probe_is_the_escape(
        monkeypatch, capsys):
    """`--assert-clean` must NAME its evidence.

    (1) FAILS on the shipped CLI: `--dry-run --assert-clean` certified on the
        census alone, i.e. on the half of the Target that cannot break
        independently of the properties it reads.
    (2) REACHABLE: the runbook's own step-3 command, unextended.

    The escape is BY NAME (`--no-fts-probe`), the same doctrine the destructive
    arm uses (`--clear-without-record`): the weaker evidence is available, and it
    cannot be taken by omission.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {})
    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean"])
    assert _cli.main() == 2
    err = capsys.readouterr().err
    assert "--probe-phrase" in err and "--no-fts-probe" in err

    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--no-fts-probe"])
    assert _cli.main() == 0
    err = capsys.readouterr().err
    assert "CENSUS only" in err, (
        "the sign-off did not say which half it skipped")


def test_probe_phrase_and_no_fts_probe_together_are_REFUSED(monkeypatch,
                                                            capsys):
    """Contradictory asks are a usage error, never a silent pick.

    (1) FAILS on a CLI that lets one flag win: "measure the index" and "do not
        measure the index" is not a preference, and whichever loses reads as an
        unexamined default.
    (2) REACHABLE: both flags are one keystroke apart in the help.
    """
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean", "--probe-phrase", "zebraquux",
                  "--no-fts-probe"])
    assert _cli.main() == 2
    assert "contradictory" in capsys.readouterr().err


def test_a_LEAK_reds_a_repair_that_the_census_calls_complete(monkeypatch,
                                                             capsys):
    """The false PASS this issue exists for, at argv level.

    (1) FAILS on the shipped CLI: the census reached 0, so the run printed
        "Repair complete." and exited 0 while the Source FTS index still
        returned a node carrying the prose — the sentence is still retrievable,
        which is the only way a user experiences the leak.
    (2) REACHABLE: a node that still carries the prose (this fixture) — and, one
        rung further out, a node CLEARED while its index entry survived, which is
        `test_a_hit_on_a_CLEARED_node_is_a_STALE_INDEX_LEAK_not_a_stem_collision`
        (#11536 review round 2, P1: this fixture is the first shape, not the
        second — the earlier rationale here overstated what it built).
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(url="session:stale",
                                            summary=FTS_PROSE,
                                            _searchText=FTS_PROSE),)])
    _install_fts(monkeypatch, {"zebraquux": [("session:stale", 3.0)]})
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": ["session:stale"], "repaired": [],
                            "empty_sessions": [], "no_record": ["session:stale"],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": ["session:stale"], "notes": {},
                            "census_before": 1, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record",
                                      "--probe-phrase", "zebraquux"])

    assert _cli.main() == 1, (
        "the repair reported success while the sentence was still findable")
    captured = capsys.readouterr()
    assert "Repair complete." not in captured.out
    assert "still findable" in captured.err


def test_the_repair_arm_takes_the_BEFORE_reading_itself(monkeypatch, capsys):
    """`--repair --probe-phrase` proves the sentence LEFT, in one run.

    (1) FAILS on a leg that only measures AFTER: on a repaired graph a 0 cannot
        be told apart from a token that was never in the corpus, so the
        verification would pass on any invented word.
    (2) REACHABLE: the run that writes is the only moment the BEFORE state still
        exists.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(url="session:pre", summary=FTS_PROSE,
                                            _searchText=FTS_PROSE),)])
    reads = {"n": 0}

    def fake(graph_, query, **kwargs):
        # FIRST call = the BEFORE reading (the node is still prose); everything
        # after = the repaired graph.
        reads["n"] += 1
        rows = [("session:pre", 3.0)] if reads["n"] == 1 else []
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({"leg": "fts", "ran": True,
                                        "degraded": False,
                                        "reason": "ok" if rows else
                                        "empty_results", "count": len(rows)})
        return rows

    from tortoise import search_engine as se
    monkeypatch.setattr(se, "run_fts_query", fake)
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": ["session:pre"], "repaired": [],
                            "empty_sessions": [], "no_record": ["session:pre"],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": ["session:pre"], "notes": {},
                            "census_before": 1, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record",
                                      "--probe-phrase", "zebraquux"])

    assert _cli.main() == 0
    captured = capsys.readouterr()
    assert "Repair complete." in captured.out
    assert "1 residue before" in captured.out and "0 residue after" in captured.out


def test_an_UNCORROBORATED_zero_does_not_certify(monkeypatch, capsys):
    """A probe that hit nothing before AND has no control proves nothing.

    (1) FAILS on a leg that equates "0 hits" with "verified": an operator-typed
        token that is not in the corpus reads 0 on an unrepaired graph and on a
        repaired one alike.
    (2) REACHABLE: the documented usage makes the operator invent the token, and
        a typo is the ordinary case.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_post_fix_node(),)])   # no lexicon node ⇒ no control
    _install_fts(monkeypatch, {"zebraquux": []})
    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--probe-phrase", "zebraquux"])
    monkeypatch.setattr(
        "tortoise.session_source_prose_migration.lexicon_control_token",
        lambda g, **k: None)

    assert _cli.main() == 1, (
        "an uncorroborated zero certified the verification")
    assert "cannot be corroborated" in capsys.readouterr().err


def test_the_sentence_LEAVES_the_Source_FTS_index_on_a_REAL_graph():
    """Both arms against a real FalkorDB and the real Source FULLTEXT index.

    (1) FAILS on any census-only verification: the pre-fix node is findable by a
        token only its prose carries, and after the clear arm it must not be.
    (2) REACHABLE: the pre-fix population, and the record arm's post-fix lexicon
        (which must remain findable — a repair that un-indexes the session is the
        #3518 regression, not a fix).

    `tests/test_11070_source_prose_repair.py` runs on a `_RecordingGraph`
    everywhere else, so no Cypher in it is ever parsed by FalkorDB. This leg is
    the one that puts the repair's OUTPUT in front of the ENGINE: a stale-index
    leak, a stemming collision and a dead read are all invisible to a fake.
    """
    from tests import _live_utils

    _live_utils._skip_unless_live_uri()

    from tortoise import session_source_prose_migration as m
    from tortoise.projection import FalkorProjection

    graph_name = f"test_11070_fts_{__import__('os').urandom(4).hex()}"
    # `FalkorProjection.from_uri` journals the graph in a test frame, and it
    # creates the FULLTEXT indexes this leg needs (a raw client would have none —
    # and an FTS query against a missing index returns [] exactly like a clean
    # miss; that is the instrument this leg exists to exercise).
    proj = FalkorProjection.from_uri(__import__("os").environ["TORTOISE_DB_URI"],
                                     graph_name=graph_name)
    g = proj.g
    url = "session:fts-11161"

    def seed():
        g.query("MATCH (n) DETACH DELETE n")
        g.query(
            "CREATE (:Source {url:$u, sourceKind:'agentSession', sessionId:'fts',"
            " summary:$p, _searchText:$p, topics:['friday','rebuild']})",
            {"u": url, "p": FTS_PROSE})

    try:
        # ── the CLEAR arm ────────────────────────────────────────────────
        seed()
        before = m.probe_fts(g, ["zebraquux"], control=False)
        assert before["probes"][0]["residue"] == [url], (
            "the fixture is not findable through the Source FTS index, so this "
            "test could not tell a repair from a dead read")
        assert before["probes"][0]["ran"] is True
        report = m.reconcile_session_source_prose(
            g, dry_run=False, clear_without_record=True)
        assert report["written"] == [url]
        after = m.probe_fts(g, ["zebraquux"], control=True)
        assert after["probes"][0]["residue"] == [], (
            "the sentence is STILL findable through the Source FTS index after "
            "the pair was cleared (#11161)")
        # ⛔ AND THE RAW COUNT, not only the classification (#11536 review round 2):
        # `residue == []` is a statement about a node the index RETURNED. This row
        # is the one that says the ENGINE dropped the entry with the property, which
        # is what makes the `stale` bucket fail-closed rather than live here — and
        # if a future engine kept the entry, the node would be `stale` and the fold
        # would veto it, so this assertion reds instead of the leak shipping.
        assert after["probes"][0]["hits"] == 0, (
            "the probe still returns a node after the clear — inspect whether it "
            "is a stale index entry, which is the #11161 leak")
        assert after["stale"] == [], (
            "a cleared node is still indexed: the leak the repair exists for")
        assert after["leaked"] == []

        # ── the RECORD arm: the lexicon must be findable, and not by the
        #    prose's token ────────────────────────────────────────────────
        seed()
        transcript = "the migration plan for the invoice service is ready"
        report = m.reconcile_session_source_prose(
            g, dry_run=False,
            read_transcript=lambda sid, mark=None: transcript)
        assert report["repaired"] == [url]
        node = g.query("MATCH (s:Source {url:$u}) RETURN s.summary, s._searchText",
                       {"u": url}).result_set[0]
        assert node[0].startswith(SESSION_SOURCE_SUMMARY_PREFIX), (
            "the record arm did not write the post-fix lexicon")
        stems = {w.strip() for w in (node[1] or "").split(",")}
        assert stems, "the re-derived lexicon is empty"
        control = m.lexicon_control_token(g)
        assert control is not None
        assert m.source_fts_probe(g, control)["hits"] > 0, (
            "the re-derived lexicon is NOT findable — the session was "
            "un-indexed (#3518)")
        assert m.probe_fts(g, ["zebraquux"], control=True)["leaked"] == [], (
            "the prose's token still returns a node after the record arm")
    finally:
        _live_utils.drop_test_graph(proj)


def test_an_UNCLASSIFIED_hit_does_not_certify_the_leg(monkeypatch):
    """#11161 review, P1 (fail-open): a hit nothing examined is not evidence.

    (1) FAILS on the shipped fold: `classify_fts_hits` puts a hit it could not
        resolve in `unread` and says the caller must not fold it into `lexicon` —
        and `_merge_probe` ignored the key, so a probe with `hits=1, urls=[<pre-fix
        url>], unread=[<pre-fix url>]` PLUS an answering control certified. On the
        writing arm `discriminating` was `True` too (the BEFORE reading had
        residue), so the run printed "Repair complete." over a hit that was never
        looked at — the exact false PASS #11161 exists to close.
    (2) REACHABLE: the classification read is a SECOND round-trip; it fails under
        load, and an index entry can point at a url no `:Source` holds (an index
        is updated asynchronously, so a node can be gone before its entry is).
    """
    argv = ["--repair", "--clear-without-record", "--probe-phrase", "zebraquux"]
    for unread in (["session:pre"],):
        before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                           "urls": ["session:pre"],
                                           "residue": ["session:pre"]}]}
        after = {"ran": True, "entity_type": "source", "limit": 50,
                 "probes": [{"phrase": "zebraquux", "hits": 1,
                             "urls": unread, "truncated": False, "ran": True,
                             "degraded": False, "reason": "ok", "residue": [],
                             "lexicon": [], "unread": unread}],
                 "leaked": [], "stem_only": [], "unmeasurable": [],
                 "truncated": [],
                 # an ANSWERING control — the only thing that used to matter
                 "control": {"mode": "probe", "ok": True, "hits": 1}}
        folded = _cli._merge_probe(before, after)
        assert folded["certifies"] is False, (
            "a hit that could not be classified certified the leg")
        assert folded["vetoed"] == "unclassified"
        assert folded["probes"][0]["discriminating"] is False, (
            "an unclassified hit was treated as DISCRIMINATING — on the writing "
            "arm that is what printed 'Repair complete.'")
    # …and the argv path refuses: the read that resolves urls must FAIL for this.
    _export_a_record_dsn(monkeypatch)

    class _UnreadableClassification(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "WHERE s.url IN $urls" in cypher:
                raise RuntimeError("the classification read failed under load")
            return super().query(cypher, params=params, timeout=timeout)

    graph = _UnreadableClassification([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"zebraquux": [("session:pre", 3.0)]})
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": [], "repaired": [], "empty_sessions": [],
                            "no_record": [], "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [], "written": [],
                            "notes": {}, "census_before": 0, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, graph, argv)
    assert _cli.main() == 1, (
        "the run certified over a hit whose classification read failed")


def test_a_probe_filling_the_window_is_VETOED_by_the_fold(monkeypatch):
    """The `truncated` veto lives in `_merge_probe`, and only `probe_fts` was pinned.

    (1) FAILS on a fold without that arm: `probe_fts` sets `truncated`, nothing
        reads it, and a probe that matched 50 nodes certifies on the 50 it
        classified while hit 51 goes unexamined.
    (2) REACHABLE: any token common enough to fill the window.
    """
    before = {"ran": True, "probes": [{"phrase": "plan", "hits": 5,
                                       "urls": [], "residue": []}]}
    after = {"ran": True, "entity_type": "source", "limit": 50,
             "probes": [{"phrase": "plan", "hits": 50, "urls": [], "truncated": True,
                         "ran": True, "degraded": False, "reason": "ok",
                         "residue": [], "lexicon": [], "unread": []}],
             "leaked": [], "stem_only": [], "unmeasurable": [], "truncated": ["plan"],
             "control": {"mode": "probe", "ok": True, "hits": 1}}
    folded = _cli._merge_probe(before, after)
    assert folded["certifies"] is False and folded["vetoed"] == "truncated"


def test_a_MULTI_token_probe_is_refused_at_ARGV_not_only_in_the_module(monkeypatch,
                                                                     capsys):
    """The module's predicate is useless if the CLI never calls it.

    (1) FAILS if the per-probe validation loop is deleted: the phrase reaches the
        OR-joining read and a correct repair reads as leaked (see the module leg).
    (2) REACHABLE: the runbook tells the operator to type a token from the
        removed sentence, and sentences have spaces.
    """
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean",
                  "--probe-phrase", "the weekly rebuild"])
    assert _cli.main() == 2
    err = capsys.readouterr().err
    assert "cannot serve as an FTS probe" in err
    assert "OR-joins" in err


def test_an_EMPTY_Source_index_corroborates_a_CLEAR_only_graph(monkeypatch,
                                                              capsys):
    """#11161 review, P2: the documented step 3 must be able to certify a repair.

    (1) FAILS on the shipped leg: the positive control needs a POST-fix lexicon
        node, and a graph that has only ever been CLEARED has none — so a
        correctly repaired population read "cannot be corroborated" and exited 1,
        and the only route to green was `--no-fts-probe`, i.e. the weaker
        evidence the leg exists to stop certifying.
    (2) REACHABLE: the clear arm (`--clear-without-record`), whose output is
        `summary=''` with `_searchText` ABSENT — the very population this repair
        exists for. And the zero is NOT vacuous: a stale index entry is exactly
        what would make the index non-empty.

    (3) AND THE GUARD SURVIVES: an index that DOES hold entries, with no lexicon
        node to probe, still cannot corroborate — the emptiness arm is a
        MEASUREMENT, not a default.
    """
    from tortoise import session_source_prose_migration as m

    cleared = {"url": "session:cleared", "sourceKind": "agentSession",
               "summary": "", "topics": ["friday", "rebuild"]}

    class _IndexGraph(_RecordingGraph):
        """Answers the index-entry read: ``empty`` as a count over the population."""

        def __init__(self, rows, *, empty, **kw):
            super().__init__(rows, **kw)
            self._empty = empty

        def query(self, cypher, params=None, timeout=None):
            if "coalesce(s._searchText, '') <> ''" in cypher:
                return _Result([(0 if self._empty else 1,)])
            return super().query(cypher, params=params, timeout=timeout)

    graph = _IndexGraph([(cleared,)], empty=True)
    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(graph, ["zebraquux"], control=True)
    assert report["control"]["mode"] == "empty-index"
    assert report["control"]["ok"] is True
    assert m.source_index_is_empty(graph) is True

    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--probe-phrase", "zebraquux"])
    assert _cli.main() == 0, (
        "a CORRECTLY repaired clear-only graph could not be certified")
    out = capsys.readouterr().out
    assert "EMPTY Source index" in out

    # …and a non-empty index with no lexicon node to probe stays uncorroborated.
    busy = _IndexGraph([(cleared,)], empty=False)
    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(busy, ["zebraquux"], control=True)
    assert report["control"]["mode"] == "unavailable"
    assert report["control"]["ok"] is False

    # …and an UNREADABLE index-entry read is not an empty one.
    class _Unreadable(_IndexGraph):
        def query(self, cypher, params=None, timeout=None):
            if "coalesce(s._searchText, '') <> ''" in cypher:
                raise RuntimeError("read failed")
            return super().query(cypher, params=params, timeout=timeout)

    unreadable = _Unreadable([(cleared,)], empty=True)
    assert m.source_index_is_empty(unreadable) is None
    report = m.probe_fts(unreadable, ["zebraquux"], control=True)
    assert report["control"]["ok"] is False
    assert "FAILED" in report["control"]["note"]
