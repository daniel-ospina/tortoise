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
import re
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


def _repaired_from_record_node(**overrides):
    """The shape the REPAIR ITSELF writes — the one only the PREFIX arm proves.

    :func:`_derive` takes ``summary``/``_searchText`` from the record's own
    transcript while ``topics`` is NEVER written (the module states why: it is
    pre-fix word frequency, independent of the pair). So on a node this tool
    repaired, ``_searchText`` is the derived lexicon and ``topics`` still holds
    the pre-fix frequency list — :func:`_proves_the_lexicon`'s exact
    ``_searchText == ", ".join(topics)`` is FALSE, and
    :func:`_is_proven_lexicon`'s display-PREFIX arm is the only proof it carries.
    """
    derived = ["migration", "plan", "friday"]
    props = {
        "url": "session:s-repaired",
        "sourceKind": "agentSession",
        "sessionId": "s-repaired",
        "is_episodic": True,
        "summary": SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(derived),
        "_searchText": ", ".join(derived),
        # the PRE-fix word-frequency list, which the repair never rewrites
        "topics": ["second", "latest", "comment", "pr"],
    }
    props.update(overrides)
    return props


def _cleared_session_node(**overrides):
    """A session Source the repair CLEARED — no text, so no index entry to lose."""
    props = {
        "url": "session:s-cleared",
        "sourceKind": "agentSession",
        "sessionId": "s-cleared",
        "is_episodic": True,
        "summary": "",
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


def test_the_PROBE_PHRASE_is_echoed_ON_PURPOSE_and_the_pair_is_not(monkeypatch,
                                                                    capsys):
    """The one NAMED exception to "no piece of the utterance reaches stdout".

    (1) FAILS if the exception is silently widened or silently revoked. The
        redaction doctrine in this script is a WHITELIST over text the tool
        DERIVES from the record (the census pair, the control token); the probe
        phrase is the operator's own INPUT — the runbook tells them to take "a
        SINGLE RARE token that appears only in the pre-fix summary", so the
        token IS transcript text. Printing it discloses nothing the operator did
        not type, and NOT printing it makes a failed verification
        undiagnosable.
    (2) REACHABLE: every `--assert-clean` verification passes `--probe-phrase`,
        so this is the ordinary reporting path, not a corner.
    (3) THE DEMONSTRATED GAP (#11536 review round 7, P2): the previous guard
        asserted only `PROSE not in out` — the WHOLE sentence — so no test would
        have noticed a future change printing a longer span. Both directions are
        pinned here: the token MAY appear, the sentence never does, and neither
        the control token nor the derived pair gains a print path.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_session_node(),)])
    # `Fridays` is one token OF the utterance — exactly the documented exception.
    _install_cli(monkeypatch, graph,
                 ["--dry-run", "--assert-clean", "--probe-phrase", "Fridays"])

    _cli.main()
    out = capsys.readouterr().out
    assert "Fridays" in out, (
        "the probe phrase is the operator's own INPUT and is echoed on purpose "
        "— if this stops being true, the exception in the redaction doctrine "
        "must be removed with it")
    assert PROSE not in out, (
        "the DERIVED pair reached stdout: echoing the probe token is the "
        "documented exception, echoing the sentence is the leak the whitelist "
        "exists to stop")


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
    non-blocking note, now closed): with the IDENTIFIER mechanism ON (its shipped
    default) `zx-99` tokenises to TWO terms (`['zx', 'zx99']`), so it is refused by
    the TOKENISER check; the mechanism is switchable (`TORTOISE_IDENTIFIER_TOKENS=0`
    is a legitimate outage lever) and with it off in an inherited shell `zx-99`
    tokenises to the single term `zx`, which is ACCEPTED — measured. So the row
    would red for a reason that is not the behaviour it claims to protect without
    this pin — the same "inherited shell changes the measurement" class the door's
    pin set exists for.

    ⛔ AND THE REFUSAL IS THE TOKENISER'S, NOT THE `build_or_query` BACKSTOP'S
    (#11536 review round 7, P2): the backstop is unreachable while the tokeniser
    already emits the alias as a second token, and it is labelled a backstop in
    the module for that reason. `test_the_build_or_query_BACKSTOP_fires_when_the_
    tokeniser_stops_expanding` pins the route that would make it live.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    monkeypatch.setenv("TORTOISE_IDENTIFIER_TOKENS", "1")

    # ⛔ ONE EXPECTED REASON PER FIXTURE, NOT ONE GENERIC MESSAGE (#11536 review
    # round 14, P2). Two rounds of this row produced a single failure message for
    # every fixture, and each version was FALSE for some of them: "the read
    # OR-joins it" is the TOKENISER's reason, and "does not address ONE indexed
    # term" is inverted for `"the"` — which IS one indexed term and is refused
    # because a stopword is too COMMON, so it OVER-matches and a REMOVED sentence
    # still reads present. A debugger sent to the wrong guard is the same
    # misstated-evidence defect this PR exists to remove, one layer up.
    # ⛔ AND A PER-FIXTURE REASON PINS EACH GUARD SEPARATELY: a generic message
    # passes whichever guard happens to fire, so it would not notice the stopword
    # guard starting to answer for a whitespace phrase (or the reverse).
    # MEASURED at this head — each string below is the reason
    # `probe_phrase_rejection` actually returns for that fixture.
    for phrase, reason in (
            # the OR-join the tokeniser produces for multi-word input
            ("the second-latest PR comment", "tokenises to 4 term(s)"),
            # ⛔ OVER-match, not under-match: one term, but far too common
            ("the", "search stopword"),
            # the identifier mechanism turns one typed token into several
            ("zx-99", "tokenises to 2 term(s)"),
            # nothing to probe at all
            ("   ", "it is empty"),
            # ⛔ WHITESPACE-FREE IS THE UNIT, NOT "TOKENISES TO ONE TERM"
            # (#11536 review round 5, P2): the tokenizer DROPS stopwords and
            # sub-2-char words, so these pass a count-based check while the
            # read sends the RAW string, which RediSearch evaluates as an
            # implicit AND — an UNDER-match that reads a still-findable
            # sentence as absent. Measured before the fix: both returned
            # None from `probe_phrase_rejection`.
            ("the fridays", "whitespace-separated words"),
            ("fridays x", "whitespace-separated words"),
            # ⛔ THESE TWO ARE THE SHAPES ONLY THE WHITESPACE GUARD REFUSES
            # (#11536 review round 12, P2; wording corrected round 17 after the
            # earlier versions stated an unmeasured mechanism). MEASURED at this
            # head: `_indexed_term_runs("friday .") == ['friday']`, so
            # `single_run` is True and the term-count check (`:1438-1445`) is
            # SKIPPED for it, and `len(runs) > 1` (`:1468`) passes it too; with
            # the whitespace guard (`:1449-1450`) disabled
            # `probe_phrase_rejection("friday .")` and `("the .")` both return
            # None — nothing else refuses them. Without the guard the read also
            # sends `friday .` raw, which RediSearch evaluates as an implicit AND
            # that under-matches.
            # ⛔ THE TWO FIXTURES ABOVE ARE REFUSED BY THIS SAME GUARD AT THIS
            # HEAD (`:1449` runs before `:1468`), but they are not what PINS it:
            # with the guard disabled they are caught by `len(runs) > 1` instead,
            # so their expected reason changes and the row reds on WHICH GUARD
            # answered — not on an accept.
            ("friday .", "whitespace-separated words"),
            ("the .", "whitespace-separated words"),
    ):
        why = probe_phrase_rejection(phrase)
        assert why, (
            f"{phrase!r} was accepted as a probe; expected it to be refused for "
            f"{reason!r}")
        assert reason in why, (
            f"{phrase!r} was refused by the WRONG guard — expected {reason!r}, "
            f"got {why!r}")
    assert probe_phrase_rejection("zebraquux") is None
    assert probe_phrase_rejection("Fridays.") is None, (
        "a single token with punctuation is still ONE token — the read escapes "
        "it and it matches")


def test_the_build_or_query_BACKSTOP_fires_when_the_tokeniser_stops_expanding(
        monkeypatch):
    """The `build_or_query` guard is a REACHABLE backstop, not dead code.

    (1) FAILS on a tree that deletes the guard: this row patches
        `sparse.build_or_query` to OR-join a single-token input and requires the
        refusal, which is the message a deleted guard can never print.
    (2) REACHABLE — by construction of the guard's own premise, not by an
        operator input today: while `tokenize_sparse_query` defaults to
        `keep_identifiers=True` it already emits the identifier alias as a SECOND
        token, so the tokeniser check fires first and this branch is unreachable
        for every input. MEASURED over 7,700 systematic identifier shapes and
        60,000 random strings at this head: ZERO firings. The route that makes it
        live is a change in `sparse` (alias expansion moved out of the tokeniser,
        or this call passing `keep_identifiers=False`) — and on that tree the
        tokeniser check would NOT catch a pipe-joined query, so deleting the
        backstop would open a real hole.

    ⛔ It is pinned with its rationale stated (#11536 review round 7, P2): the
    module docstring used to claim the identifier mechanism is what refuses
    `zx-99`, which is NOT the mechanism that refuses it.
    """
    from tortoise import session_source_prose_migration as m

    # A single surviving token, so the tokeniser check passes …
    assert m.probe_phrase_rejection("zebraquux") is None, (
        "the premise of this row is a token the TOKENISER accepts")

    # … and `build_or_query` returns an OR-union anyway, which is the shape the
    # backstop exists for.
    monkeypatch.setattr("tortoise.sparse.build_or_query",
                        lambda *a, **k: "zebra|quux|\"zebra quux\"")
    refused = m.probe_phrase_rejection("zebraquux")
    assert refused is not None, (
        "a pipe-joined query was ACCEPTED as a single-token probe — the read "
        "would have measured the OR of three terms")
    assert "identifier" in refused and "several terms" in refused, (
        "the backstop fired but did not say which mechanism expanded the token: "
        + refused)


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


def test_the_REPAIRS_OWN_OUTPUT_is_a_collision_through_the_PREFIX_arm_only(monkeypatch):
    """#11536 review, P3: the prefix arm of `_is_proven_lexicon` had no row.

    (1) FAILS if `_is_proven_lexicon` is reduced to `_proves_the_lexicon`: the
        node this repair WRITES carries a prefix `summary` and a derived
        `_searchText` while `topics` is left at the pre-fix word frequencies, so
        the exact join never proves it — the hit falls to `foreign`, and
        `_report_fts` then tells the operator these are "Sources outside this
        population" about the repair's own output. Nothing else red: the one
        pre-existing `lexicon` fixture satisfies BOTH proofs, which is why a
        mutant could delete this arm silently.
    (2) REACHABLE: every record-arm repair writes exactly this shape.
    (3) POLARITY THAT MUST NOT GO RED: the measured `friday`/`fridays` collision.
    """
    from tortoise import session_source_prose_migration as m

    node = _repaired_from_record_node()
    assert not m._proves_the_lexicon(node), (
        "the fixture is meant to be provable ONLY by the prefix arm")
    assert m._is_proven_lexicon(node), "the prefix arm did not prove the lexicon"

    graph = _RecordingGraph([(node,)])
    _install_fts(monkeypatch, {"fridays": [("session:s-repaired", 1.0)]})
    split = m.classify_fts_hits(graph, ["session:s-repaired"], phrase="fridays")
    assert split["lexicon"] == ["session:s-repaired"], (
        "the repair's own output was not classified as a stem collision")
    assert split["foreign"] == [], (
        "the repair's own output was reported as OUTSIDE this population")


def _prefix_over_prose_node(**overrides):
    """A node whose DISPLAY pair is post-fix while its INDEX is still the prose.

    The pre-fix writer could not emit this (its two arms move ``summary`` and
    ``_searchText`` together), so it is the shape a HALF-APPLIED repair or a
    deferred index update leaves behind — see
    :func:`test_a_PREFIXED_summary_over_a_still_PROSE_index_VETOES`.
    """
    props = _session_node()
    props["summary"] = (SESSION_SOURCE_SUMMARY_PREFIX
                        + ", ".join(["migration", "plan", "friday"]))
    props.update(overrides)
    return props


def test_a_PREFIXED_summary_over_a_still_PROSE_index_VETOES(monkeypatch):
    """#11536 review round 5, P2: the prefix proves the DISPLAY pair, not the INDEX.

    (1) FAILS on the shipped classifier: `_is_proven_lexicon`'s prefix arm reads
        only `summary`, and `_probe_explains_the_lexicon` then asked whether the
        probe token occurs in `_searchText` — which on this shape answers YES for
        the PROSE's own words. The leak was labelled `lexicon`, the one bucket
        that never vetoes, and the run certified over a sentence that was still
        retrievable. Measured before the fix: bucket `lexicon` for the probe
        `fridays`, whose `friday` stem the display pair ALSO carries — so the old
        second proof was satisfied rather than merely skipped.
    (2) REACHABLE: a half-applied repair — `summary` re-derived while
        `_searchText` was not — and a deferred index update, the same class
        round 2's P1 was filed for.
    (3) POLARITY THAT MUST NOT GO RED: the prefix-only arms that ARE healthy. The
        repair's own record-arm output (`_repaired_from_record_node`) and the
        canonical post-fix pair (`_post_fix_node`) must stay `lexicon`, and the
        measured `friday`/`fridays` collision with them must still certify.
    """
    from tortoise import session_source_prose_migration as m

    leak = _prefix_over_prose_node()
    # The two guards that had to stay out of the way, asserted so a future edit
    # that makes this fixture vacuous is caught here rather than silently.
    assert not m.carries_pre_lexicon_prose(leak), (
        "the fixture must reach the prefix arm, not be caught as residue")
    assert m._carries_the_display_prefix(leak)
    assert m._is_proven_lexicon(leak), "the prefix arm is meant to prove it"
    assert not m._indexed_text_is_the_displayed_pair(leak), (
        "the indexed text is the PROSE — it must not read as within the display")

    split = m.classify_fts_hits(_RecordingGraph([(leak,)]), ["session:s-11070"],
                               phrase="fridays")
    assert split["lexicon"] == [], (
        "a prefix `summary` over a still-prose `_searchText` was certified as a "
        "benign stem collision — that is the survived-prose leak")
    assert split["unexplained"] == ["session:s-11070"]

    _install_fts(monkeypatch, {"fridays": [("session:s-11070", 1.0)]})
    report = m.probe_fts(_RecordingGraph([(leak,)]), ["fridays"], control=False)
    assert report["probes"][0]["unexplained"] == ["session:s-11070"]
    assert report["probes"][0]["residue"] == []
    folded = _cli._merge_probe(
        {"ran": True, "probes": [{"phrase": "fridays", "hits": 1,
                                   "residue": []}]},
        dict(report, control={"mode": "probe", "ok": True, "hits": 1}))
    assert folded["certifies"] is False and folded["vetoed"] == "unexplained", (
        "a corroborating control certified over a hit whose INDEX still holds "
        "the pre-fix prose")

    # (3) the healthy prefix-only shapes keep the benign label.
    for healthy in (_post_fix_node(), _repaired_from_record_node()):
        assert m._indexed_text_is_the_displayed_pair(healthy), (
            f"{healthy['url']} is a HEALTHY pair — its indexed text is within "
            f"the display, and this fix must not veto it")
    assert m._hit_node_bucket(_post_fix_node(), "fridays") == "lexicon"
    assert m._hit_node_bucket(_repaired_from_record_node(), "fridays") == "lexicon"


def test_an_INFLECTED_prose_token_is_not_absorbed_by_the_display_pair(monkeypatch):
    """#11536 review round 6, P1: the subset check must compare RAW tokens.

    (1) FAILS on the round-5 draft, which folded both sides through `_stem_key`:
        an index still holding the PROSE `planned` was "explained" by a display
        pair carrying `planning`, so the node was bucketed `lexicon` — the bucket
        that never vetoes — and `_merge_probe` certified. MEASURED on that shape
        before the fix: `_indexed_text_is_the_displayed_pair` -> True,
        `_hit_node_bucket` -> `lexicon`. The census is structurally blind to it
        (the `summary` is prefixed), so this guard was the only defence.
    (2) REACHABLE: any half-applied repair whose re-derived lexicon shares a stem
        with the prose it replaced — `planning`/`planned`, `rebuild`/`rebuilt`.
    (3) POLARITY THAT MUST NOT GO RED: stemming is still the correct fold where a
        PROBE token is related to a lexicon token, and every HEALTHY pair still
        proves a subset because both sides derive from the same
        `", ".join(topics)` string.
    """
    from tortoise import session_source_prose_migration as m

    leak = _prefix_over_prose_node(
        summary=SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(
            ["planning", "migration"]),
        _searchText="planned migration")
    assert not m.carries_pre_lexicon_prose(leak), (
        "the fixture must reach the prefix arm, not be caught as residue")
    assert not m._indexed_text_is_the_displayed_pair(leak), (
        "`planned` is not a token of the displayed pair — a stem fold must not "
        "let the prose index read as the display")
    assert m._hit_node_bucket(leak, "planned") == "unexplained"

    _install_fts(monkeypatch, {"planned": [("session:s-11070", 1.0)]})
    report = m.probe_fts(_RecordingGraph([(leak,)]), ["planned"], control=False)
    assert report["probes"][0]["lexicon"] == []
    assert report["probes"][0]["unexplained"] == ["session:s-11070"]
    folded = _cli._merge_probe(
        {"ran": True, "probes": [{"phrase": "planned", "hits": 1,
                                   "residue": []}]},
        dict(report, control={"mode": "probe", "ok": True, "hits": 1}))
    assert folded["certifies"] is False and folded["vetoed"] == "unexplained"

    # (3) the healthy pairs still prove containment, so nothing is falsely vetoed.
    for healthy in (_post_fix_node(), _repaired_from_record_node()):
        assert m._indexed_text_is_the_displayed_pair(healthy)


def test_an_IDENTIFIER_SHAPED_token_is_refused_WHATEVER_the_switch_says(
        monkeypatch):
    """#11536 review round 6, P2: the one-token guarantee must not depend on a switch.

    (1) FAILS on the round-5 tree: the `build_or_query` backstop fires only while
        the identifier mechanism is ON, so with the documented outage lever
        `TORTOISE_IDENTIFIER_TOKENS=0` MEASURED `probe_phrase_rejection('zx-99')`
        returned None — ACCEPTED — and the read sent `zx\\-99`, one term with no
        `|`. RediSearch splits the INDEX on non-alphanumerics, so that escaped
        literal does not address the terms actually indexed: the same UNDER-match
        class as the whitespace guard, on a run that can certify with no BEFORE
        reading.
    (2) REACHABLE: the switch is a supported operator lever, not a mutation.
    (3) POLARITY THAT MUST NOT GO RED: a single-run token with trailing
        punctuation (`Fridays.`) is still ONE indexed term and stays accepted.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    for switch in ("1", "0"):
        monkeypatch.setenv("TORTOISE_IDENTIFIER_TOKENS", switch)
        for phrase in ("zx-99", "ERR-4032", "abc_def"):
            assert probe_phrase_rejection(phrase), (
                f"{phrase!r} was accepted with TORTOISE_IDENTIFIER_TOKENS={switch}: "
                f"the index splits it into several terms, so the probe "
                f"UNDER-matches")
        # the controls: one alphanumeric run is still exactly one token.
        assert probe_phrase_rejection("zebraquux") is None
        assert probe_phrase_rejection("Fridays.") is None, (
            "a single token with trailing punctuation is ONE indexed term")


def test_a_token_the_read_would_ESCAPE_is_REFUSED(monkeypatch, capsys):
    r"""#11536 review round 7, P0: an escaped literal is not a term the index holds.

    (1) FAILS on the round-6 tree: `build_or_query`'s degenerate single-token
        branch sends the token through `escape_redisearch_literal`, which
        backslash-escapes `[\\,;:\-|@~()\[\]{}<>=*%"$]` — and the escaped
        LITERAL is not a term the index holds, because the index split on that
        same character. MEASURED on a real FalkorDB at this head, one node whose
        `_searchText` contains `friday`: probe `friday` -> 1 hit, probe
        `friday,` -> `friday\\,` -> **0 hits**; `(friday)` and `-friday`
        likewise. `probe_phrase_rejection` returned None (ACCEPTED) for all of
        them, so `--dry-run --assert-clean --probe-phrase 'friday,'` exited 0
        over a still-retrievable sentence — the exact UNDER-match class the
        whitespace guard closed, one character class wider.
    (2) REACHABLE: an operator copying the token out of a sentence types the
        punctuation with it. `Fridays.` passed the round-6 no-false-block control
        only because `.` is NOT in that escape set.
    (3) POLARITY THAT MUST NOT GO RED: the bare word still certifies, and a
        trailing `.`/`!` (not escaped, so it still matches) stays accepted.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    for phrase in ("friday,", "(friday)", '"friday"', "friday;", "-friday",
                   "friday*", "friday:"):
        assert probe_phrase_rejection(phrase), (
            f"{phrase!r} was accepted as a probe — the read escapes it into a "
            f"literal term the index does not hold, so it reads 0 while the "
            f"sentence is still findable")
    for phrase in ("friday", "friday.", "Fridays.", "friday!", "zebraquux"):
        assert probe_phrase_rejection(phrase) is None, (
            f"{phrase!r} was refused — it is ONE indexed term, so refusing it "
            f"would be a false block")

    # The argv arm refuses it too, so the run cannot certify on a dead probe.
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean", "--probe-phrase", "friday,"])
    assert _cli.main() == 2
    assert "cannot serve as an FTS probe" in capsys.readouterr().err


def test_a_LONE_UNDERSCORE_probe_is_REFUSED(monkeypatch, capsys):
    r"""#12368 review, P1: the `_`-deferral admitted a probe the read cannot answer.

    (1) FAILS on the committed tree before this fix: `deferred` admits every
        `_`-only token, and `build_or_query('_') == '_'` with `_` outside
        `escape_redisearch_literal`'s set — so the read sends the bare `_`, which
        the index answers with ZERO rows even when an `:Object` literally NAMED
        `_` is indexed. `probe_phrase_rejection('_')` returned None (ACCEPTED),
        so `--dry-run --assert-clean --probe-phrase _` certified 'the pre-fix
        prose is gone' over a still-present sentence.
    (2) MEASURED on a live FalkorDB 4.20.6 (one `:Object` per shape, each queried
        by its own name through the real `run_fts_query`), over the twelve
        accepted `_`-only shapes: `_` is the ONLY unreachable one — `__`, `___`,
        `____`, `_____`, `_a`, `a_`, `_1`, `1_`, `_a_`, `a__b`, `0_` each return
        their own record. And the term really is there: `queryNodes('Object',
        '\\_')` returns the record named `_` where `queryNodes('Object', '_')`
        returns nothing.
    (3) POLARITY THAT MUST NOT GO RED: the eleven reachable `_` shapes stay
        accepted. Refusing them on the same story would be the false block that
        #12360's review round 7 repaired, and `origin/main`'s refusal of ALL of
        them (for the false reason "it tokenises to 0 term(s)") is exactly what
        that round removed.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    why = probe_phrase_rejection("_")
    assert why, (
        "a lone `_` was accepted as a probe — the read sends the bare `_`, the "
        "index answers 0 rows for it, and the guard would then certify over a "
        "still-present sentence")
    assert "ZERO" in why or "zero" in why, why

    # ⛔ THE MESSAGE MUST NOT BE THE PRE-FIX ONE. `origin/main` refused `_` while
    # claiming "OR-joins multi-term input" for a token whose read emits ONE
    # literal; this refusal exists because the probe cannot be ANSWERED.
    assert "OR-joins" not in why, (
        f"the refusal fell through to the term-count branch and repeats the "
        f"false reason #12360 round 7 removed: {why!r}")

    for accepted in ("__", "___", "____", "_____", "_a", "a_", "_1", "1_",
                     "_a_", "a__b", "0_", "a_b", "a_bc", "x_axis", "A_B"):
        assert probe_phrase_rejection(accepted) is None, (
            f"{accepted!r} was refused — the index holds it as ONE term and its "
            f"own record is reachable, so refusing it is a false block")

    # The argv arm refuses it too, so no run can certify on a dead probe.
    _export_a_record_dsn(monkeypatch)
    _install_cli(monkeypatch, _RecordingGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean", "--probe-phrase", "_"])
    assert _cli.main() == 2
    assert "cannot serve as an FTS probe" in capsys.readouterr().err


def test_classify_fts_hits_REQUIRES_the_phrase_so_no_proof_can_be_dropped():
    """#11536 review round 7, P2: the default silently dropped two of three proofs.

    (1) FAILS on the round-6 tree: `phrase: str | None = None` skipped BOTH
        `_indexed_text_is_the_displayed_pair` (it early-returns True on absent
        indexed text) and `_probe_explains_the_lexicon` (`phrase is None`).
        MEASURED on a node carrying the prefix `summary` and NO `_searchText` — an
        index entry that outlived the property, i.e. the leak shape —
        `_hit_node_bucket(props, None)` returned `lexicon` (never vetoes) while
        `_hit_node_bucket(props, 'friday')` returned `unexplained`. One omitted
        keyword restored the round-2 P1 that split the bucket.
    (2) REACHABLE: it is a keyword with a default on a public function; no in-tree
        caller omits it, so the fix costs nothing and closes the class.
    (3) POLARITY THAT MUST NOT GO RED: with `phrase` supplied the same node still
        vetoes, and a healthy post-fix node is still a stem collision.
    """
    import pytest

    from tortoise import session_source_prose_migration as m

    with pytest.raises(TypeError):
        m.classify_fts_hits(_RecordingGraph([(_post_fix_node(),)]),
                            ["session:s-post"])

    # The shape the default used to certify, now that the probe token is required.
    orphan = _post_fix_node(url="session:s-orphan", sessionId="s-orphan")
    orphan.pop("_searchText", None)
    assert m._hit_node_bucket(orphan, "friday") == "unexplained", (
        "a hit on a node with NO indexed text was certified as a collision with "
        "text it does not have")
    split = m.classify_fts_hits(_RecordingGraph([(orphan,)]),
                               ["session:s-orphan"], phrase="friday")
    assert split["lexicon"] == [] and split["unexplained"] == ["session:s-orphan"]

    # (3) the healthy pair is still a stem collision.
    assert m._hit_node_bucket(_post_fix_node(), "fridays") == "lexicon"


def test_the_subset_guard_sees_the_tokens_the_INDEX_keeps(monkeypatch):
    """#11536 review round 8, P1: the guard must not use the QUERY tokeniser.

    (1) FAILS on the round-7 tree, which compared `tokenize_sparse_query` forms.
        That function is the PHRASE parser: it DROPS stopwords, sub-2-character
        tokens and all-digit tokens — all of which the Source FULLTEXT index
        keeps. So a `_searchText` of `"plan friday the of and 4820"` read as a
        subset of the displayed `"plan, friday"`: MEASURED, the guard returned
        True, `_hit_node_bucket` returned `lexicon`, and a
        `--dry-run --assert-clean --probe-phrase friday` exited 0 while
        `run_fts_query('4820')` — and `'the'`, `'of'`, `'and'` — still returned
        the node. The census cannot see it either (the `summary` is prefixed),
        so this guard was the only defence and the benign bucket absorbed it.
    (2) REACHABLE: pre-fix prose is ordinary English; stopwords and numbers are
        in it.
    (3) POLARITY THAT MUST NOT GO RED: a healthy pair derives BOTH sides from the
        same `", ".join(topics)` string, so containment still holds there.
    """
    from tortoise import session_source_prose_migration as m

    leak = _prefix_over_prose_node(
        summary=SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(["plan", "friday"]),
        _searchText="plan friday the of and 4820")
    assert not m._indexed_text_is_the_displayed_pair(leak), (
        "the indexed text still carries stopwords and a number the INDEX keeps, "
        "so it is not the displayed pair")
    assert m._hit_node_bucket(leak, "friday") == "unexplained"

    _install_fts(monkeypatch, {"friday": [("session:s-11070", 1.0)]})
    report = m.probe_fts(_RecordingGraph([(leak,)]), ["friday"], control=False)
    assert report["probes"][0]["lexicon"] == []
    assert report["probes"][0]["unexplained"] == ["session:s-11070"]

    # (3) both healthy prefix-only shapes still prove containment.
    for healthy in (_post_fix_node(), _repaired_from_record_node()):
        assert m._indexed_text_is_the_displayed_pair(healthy), (
            f"{healthy['url']} is a HEALTHY pair and must not be vetoed")

    # ⛔ AND CONTAINMENT IS NOT DERIVATION (#11536 review round 9, P1). The
    # lexicon comes from the SAME transcript as the prose, so a terse utterance
    # with no stopwords is a SUBSET by accident. MEASURED on this shape, the
    # subset test returned True and the bucket was `lexicon` — the third proof
    # cannot help, because on a prose index the text that matched IS the prose.
    terse = _prefix_over_prose_node(
        summary=SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(
            ["migration", "plan", "friday", "invoice", "service", "ready"]),
        _searchText="migration plan friday invoice service ready")
    assert not m._indexed_text_is_the_displayed_pair(terse), (
        "every prose word is also a lexicon word here, so a CONTAINMENT test "
        "passes — but the indexed text is not the DERIVED pair")
    assert m._hit_node_bucket(terse, "invoice") == "unexplained"


def test_a_NUMBER_and_an_ACCENTED_word_are_probes_a_STOPWORD_is_not():
    """#11536 review round 8, P2: two false blocks, and one refusal that is RIGHT.

    (1) FAILS on the round-7 tree on both counts. The term-count guard refused
        `4820`/`2024` with "tokenises to 0 term(s) … the FTS read OR-joins
        multi-term input" — a mechanism that cannot apply to a single term —
        although `build_or_query('4820')` returns `'4820'` and MEASURED
        `run_fts_query('4820')` returns the still-prose node. And the shape guard
        split on ASCII `[^a-z0-9]`, so `résumé` was refused as "2 alphanumeric
        runs" while the index holds it as ONE term (MEASURED:
        `run_fts_query('résumé')` -> 1 hit, `'resum'` -> 0 hits).
    (2) REACHABLE: prose carries numbers, and non-English sessions carry accents.
    (3) AND A REFUSAL THAT IS CORRECT, pinned so a future widening cannot accept
        it: `naïve` builds `na|ve` because the ASCII tokeniser splits the accent,
        and MEASURED that OR-join returns 0 hits — an UNDER-match, not a probe.
        `the` stays refused as a stopword.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    # ⛔ EVERY ACCENTED TOKEN WHOSE ASCII FRAGMENTS ARE ALL TOO SHORT (#11536
    # review round 11, P2): `año`/`uña`/`eñe` tokenise to ZERO ASCII terms, so the
    # term-count guard refused them with "the FTS read OR-joins multi-term input"
    # while `build_or_query('año')` returns `'año'` and the index holds it. The
    # rule is now decided by what the read will SEND (one unicode run), with
    # stopwords refused by name instead.
    for accepted in ("4820", "2024", "résumé", "café", "año", "uña", "eñe",
                     "Fridays."):
        assert probe_phrase_rejection(accepted) is None, (
            f"{accepted!r} was refused — the read sends it as ONE indexed term")
    for refused in ("the", "of", "and", "naïve", "zx-99", "friday,"):
        assert probe_phrase_rejection(refused), (
            f"{refused!r} was accepted — the read does not send it as one term")


def test_a_PUNCTUATED_underscore_token_is_a_probe_because_the_read_addresses_ONE_term():
    """#12368 review, P2: the backstop compared RAW ALTERNATIVES, not INDEX TERMS.

    (1) FAILS on the pre-fix tree. `probe_phrase_rejection('a_.')` refused with
        "the read expands it into several terms ('a_.|a_')" — while BOTH
        alternatives reduce to the SAME index term `a_`, so the read addresses ONE
        term and the record's own name returns it. The index splits every
        alternative on its own separators; that is precisely the sense the raw
        string comparison was blind to.
    (2) MEASURED, live, not inferred: one `:Object` per shape in ITS OWN graph,
        the background index cycle awaited (`CALL db.indexes()` numDocuments), then
        the emitted string issued through the PRODUCTION Cypher on FalkorDB 4.20.6 —
        `a_.`, `a_!`, `.a_`, `_a.` and `a_..` each returned their own record. So
        refusing them was a false block, the class this guard has now repaired
        five times.
        ⛔ **THE DECOY HALF AND THE PROBE METHOD, because both were got wrong here**
        (`#12368` review, P1). Reachability alone does not prove a probe is clean:
        each shape was ALSO measured against a decoy carrying the bare word
        (`zz a zz`), with a POSITIVE CONTROL that the decoy is indexed (query `a`
        and see it come back). And the emitted string must be issued through the
        Cypher directly — `run_fts_query` **re-builds** through `build_or_query`,
        so passing it an already-emitted `'a_bc.|a_bc'` re-parses it into
        `'bc|bc|a_bc'` and the bare `bc` matches the decoy. That artifact produced
        a false "over-match" for `a_bc.` in an earlier round of this branch; with
        the Cypher, `a_bc.`, `x_axis.`, `2_a.`, `a_b!`, `a_bc/` and `a_b_c.` return
        ONLY their own record — they are clean probes, and refusing them was a
        false block fixed in `probe_phrase_rejection`'s deferral. The REAL
        over-match class is a `_` adjacent to a separator on either side (`ab._`,
        `x1&_`, `2._`, `_&x1`) where the QUERY parser drops the `_`: those DO return
        the decoy and are tracked as #12535.
    (3) REACHABLE: such a token is exactly what an operator copies out of prose.
    (4) AND THE OVER-MATCH CLASS STAYS REFUSED — pinned, so a future widening
        cannot swallow it: `web_search` emits `search|web|web_search` (three index
        terms, only one of which is the token), `abc_def` emits `abc|def|abc_def`,
        `cafécafé` emits `caf|caf` where the index holds `cafécafé`, and
        `überüber`/`testétest` are the same shape.

    ⛔ AND THE DOUBLED-SEGMENT CLASS IS PART OF THAT PIN, not an afterthought
    (`#12368` review, P1 — the first cut of this widening REOPENED it). The
    comparison must be made in the INDEX's sense, which keeps `_` inside a word:
    `_indexed_term_runs` splits on `_`, so it mapped both `test_test` and its own
    alternative `test` to `{'test'}` and ACCEPTED `test_test` — whose read is
    `test|test|test_test`, i.e. two distinct index terms, so the probe comes back
    on the common word alone. `origin/main` refused it (two runs), so accepting it
    was a reopening, not a widening.
    """
    from tortoise.session_source_prose_migration import probe_phrase_rejection

    for accepted in ("a_.", "a_!", ".a_", "_a.", "a_..", "friday.",
                     # the punctuated `_`-run: one index term either way, so these
                     # are probes — the deferral widening that made them so is the
                     # P1 fix, and they are REFUSED on the pre-fix tree
                     "a_bc.", "x_axis.", "2_a.", "a_b!", "a_bc/", "a_b_c."):
        assert probe_phrase_rejection(accepted) is None, (
            f"{accepted!r} was refused — every alternative the read emits "
            f"addresses the ONE index term this token denotes, so it is a probe "
            f"(MEASURED live: it returns its own record)")
    for refused in ("web_search", "abc_def", "cafécafé", "überüber", "testétest",
                    "test_test", "ab_ab", "abc_abc", "foo_foo"):
        assert probe_phrase_rejection(refused), (
            f"{refused!r} was accepted — the read OR-joins terms this token is "
            f"not, so the probe would match on any one of them")


def test_a_NUMBER_probe_is_EXPLAINED_by_a_numeric_lexicon(monkeypatch):
    """#11536 review round 10, P1: the sibling predicate kept the QUERY tokeniser.

    (1) FAILS on the round-9 tree: `_probe_explains_the_lexicon` built both sides
        with `tokenize_sparse_query`, which DROPS all-digit tokens — so a
        bare-number probe (ACCEPTED since round 8) could never be explained by a
        numeric lexicon. MEASURED on the shape the writers emit for a numeric
        transcript (`summary = PREFIX + "100, 200, 4820"`,
        `_searchText = "100, 200, 4820"`, `topics` the same): the node classified
        `unexplained`, `_merge_probe` returned `vetoed="unexplained"`, and the
        refusal blamed "a surviving pre-fix index entry" that the run's own data
        falsifies. On the documented runbook path — `--repair --probe-phrase
        <a number copied out of the removed sentence>` — a CORRECT repair exited
        1, and the remedy it implied (clear the lexicon) is the #3518 harm this
        leg exists to prevent. Fail-closed, so nothing shipped; that is why it is
        P1 and not P0.
    (2) REACHABLE: numbers are the most discriminating token in prose and the
        writers derive a lexicon from them without modification.
    (3) POLARITY THAT MUST NOT GO RED: the measured `friday`/`fridays` stem
        collision still earns `lexicon`, and a token the node does not carry is
        still `unexplained`.
    """
    from tortoise import session_source_prose_migration as m

    numeric = _post_fix_node(
        url="session:s-num", sessionId="s-num",
        summary=SESSION_SOURCE_SUMMARY_PREFIX + ", ".join(
            ["100", "200", "4820"]),
        _searchText=", ".join(["100", "200", "4820"]),
        topics=["100", "200", "4820"])
    assert m._indexed_text_is_the_displayed_pair(numeric)
    assert m._probe_explains_the_lexicon(numeric, "4820"), (
        "a bare-number probe cannot be explained by a numeric lexicon — the "
        "query tokeniser drops digits, so a CORRECT repair is refused")
    assert m._hit_node_bucket(numeric, "4820") == "lexicon"

    _install_fts(monkeypatch, {"4820": [("session:s-num", 1.0)]})
    report = m.probe_fts(_RecordingGraph([(numeric,)]), ["4820"], control=False)
    assert report["probes"][0]["lexicon"] == ["session:s-num"]
    assert report["probes"][0]["unexplained"] == []
    folded = _cli._merge_probe(None, report, wrote=True)
    assert folded["vetoed"] != "unexplained", (
        "a correct repair over a numeric lexicon was vetoed as a surviving "
        "pre-fix index entry")

    # (3) the two neighbouring polarities are unchanged.
    assert m._hit_node_bucket(_post_fix_node(), "fridays") == "lexicon"
    assert m._hit_node_bucket(_post_fix_node(), "zebraquux") == "unexplained"


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

    split = m.classify_fts_hits(graph, ["session:cleared"],
                               phrase="zebraquux")
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
                               ["session:restored"], phrase="friday")
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


def test_the_control_line_does_not_assert_an_answer_on_a_ZERO(capsys):
    """#11536 review round 11, P2: the control's own line printed the opposite.

    (1) FAILS on the round-10 tree: `control (one display-prefix Source token —
        the read answers): 0 hit(s)` printed UNCONDITIONALLY, so on hits == 0 —
        the one case the control exists to detect — the line asserted the read
        answered immediately above the `uncorroborated` refusal saying it did
        not. The `--json` note made the same unconditional claim.
    (2) REACHABLE: a dropped index or a breaker trip between the probe and the
        control is exactly the state this arm reports.
    (3) POLARITY THAT MUST NOT GO RED: a control that DID answer still says so,
        because a corroborated zero is the whole point of the arm.
    """
    def _fts(hits):
        return {"ran": True, "entity_type": "source", "limit": 50,
                "probes": [{"phrase": "zebraquux", "hits": 0, "urls": [],
                            "unresolved": 0, "truncated": False, "ran": True,
                            "degraded": False, "reason": None, "residue": [],
                            "lexicon": [], "stale": [], "foreign": [],
                            "unexplained": [], "unread": []}],
                "leaked": [], "stale": [], "stem_only": [],
                "unmeasurable": [], "truncated": [], "unresolved": 0,
                "certifies": False, "vetoed": "uncorroborated",
                "control": {"mode": "probe", "ok": bool(hits), "hits": hits,
                            "note": "control"}}

    _cli._report_fts(_fts(0))
    out = capsys.readouterr().out
    assert "read answers" not in out, (
        "the control line asserted the read answered on a ZERO — the one case "
        "it exists to detect: " + out)
    assert "did NOT answer" in out, (
        "the control line did not name the failure it measured: " + out)

    # (3) the corroborating arm still states its answer.
    _cli._report_fts(_fts(2))
    out = capsys.readouterr().out
    assert "read answers" in out, (
        "an answering control stopped saying so: " + out)
    assert "did NOT answer" not in out


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


def test_a_FOREIGN_only_hit_is_NOT_reported_as_a_stem_collision(monkeypatch):
    """``stem_only`` documents "hits, but every one is a lexicon node".

    (1) FAILS on the shipped fold (#11536 review round 7, P2): ``stem_only``
        excluded residue/stale/unread/unexplained/unresolved and NOT ``foreign``
        — a hit on a ``:Source`` outside the episodic population. That is an
        EXAMINED, bucketed node that is NOT a lexicon node, so the probe was
        summarised as a benign stem collision while its ``lexicon`` bucket was
        EMPTY. Round 3 closed this exact shape for ``unresolved`` and left this
        one in place, one predicate short.
    (2) REACHABLE: every ``--json`` report carries ``stem_only``, and a consumer
        reading it concludes the token matched a REPAIRED node. Nothing had been.
    (3) POLARITY THAT MUST NOT GO RED: a genuine lexicon hit still reports
        ``stem_only`` — asserted here, so the fix cannot over-exclude and turn a
        real stem collision into a veto.
    """
    from tortoise import session_source_prose_migration as m

    _install_fts(monkeypatch, {
        "zebraquux": [("https://example.com/page", 1.0)],
        "migration": [("session:s-post", 2.0)],
    })

    class _ControlGraph(_RecordingGraph):
        """A lexicon node the CONTROL can read, so the zero is corroborated."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    # A non-episodic Source with its own `_searchText`: `_hit_node_bucket` labels
    # it `foreign` (not residue, not a proven lexicon, not a cleared episodic
    # node), which is the shape the fold was dropping into `stem_only`.
    foreign = _post_fix_node(url="https://example.com/page", sourceKind="web",
                             sessionId=None, is_episodic=False, summary="",
                             _searchText="zebraquux page")
    report = m.probe_fts(_ControlGraph([(_post_fix_node(),), (foreign,)]),
                         ["zebraquux"], control=True)
    assert report["probes"][0]["foreign"] == ["https://example.com/page"], (
        "the fixture did not exercise the `foreign` bucket: "
        + repr(report["probes"][0]))
    assert report["probes"][0]["lexicon"] == []
    assert report["stem_only"] == [], (
        "`stem_only` documents 'hits, every one a lexicon node' — this probe's "
        "`lexicon` bucket is EMPTY and its only hit is `foreign`, so reporting "
        "it as a benign stem collision contradicts its own documented meaning")

    # (3) the polarity that must not go red: a REAL lexicon hit still reports.
    _install_fts(monkeypatch, {
        "fridays": [("session:s-post", 2.0)],
        "migration": [("session:s-post", 2.0)],
    })
    lexicon = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), ["fridays"],
                          control=True)
    assert lexicon["probes"][0]["lexicon"] == ["session:s-post"]
    assert lexicon["stem_only"] == ["fridays"], (
        "a genuine stem collision must still be summarised as `stem_only` — the "
        "fix for the foreign case must not swallow the benign one")


def test_a_TRUNCATED_probe_is_not_reported_as_a_stem_collision(monkeypatch):
    """``stem_only`` documents "hits, but every one is a lexicon node".

    (1) FAILS on the shipped fold (#11536 review round 8, P2): `truncated` was
        not excluded, so a probe whose window FILLED — its hits a FLOOR, with
        residue possibly hiding past the window and never examined — was
        summarised as a benign stem collision. Third instance of one defect
        (round 3 `unresolved`, round 7 `foreign`): the predicate was being grown
        one bucket at a time instead of stating the invariant.
    (2) REACHABLE: `_FTS_PROBE_LIMIT` is 50 and a common token fills it on any
        real graph, which is the exact case `truncated` exists for.
    (3) POLARITY THAT MUST NOT GO RED: a genuine, UNTRUNCATED stem collision
        still reports `stem_only` (asserted above in the foreign test, and here
        on the same fixture shape with a single row).
    """
    from tortoise import session_source_prose_migration as m

    class _ControlGraph(_RecordingGraph):
        """A lexicon node the CONTROL can read, so the zero is corroborated."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    # A window-filling read: every row is the SAME lexicon node, so `hits`
    # reaches the LIMIT while `lexicon` holds one url.
    filling = [("session:s-post", 2.0)] * m._FTS_PROBE_LIMIT
    _install_fts(monkeypatch, {"plan": filling,
                              "migration": [("session:s-post", 2.0)]})
    report = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), ["plan"],
                         control=True)
    assert report["truncated"] == ["plan"], (
        "the fixture did not fill the window: " + repr(report["truncated"]))
    assert report["probes"][0]["lexicon"] == ["session:s-post"]
    assert report["stem_only"] == [], (
        "`stem_only` claims every hit is a lexicon node, but this probe's window "
        "FILLED — its hits are a floor and the rest were never examined")


def test_a_BREAKER_OPEN_read_is_UNMEASURABLE_not_a_clean_zero(monkeypatch):
    """A leg that did not run returns `[]` — exactly as a clean miss does.

    (1) FAILS on a leg that reads `hits` alone: an open circuit breaker (or a
        dropped index) yields 0 hits and the run certifies a leak as repaired.
    (2) REACHABLE: `run_fts_query` short-circuits to `[]` on a breaker that is
        open, and returns `[]` with `reason="index_missing"` when the index is
        gone. ⛔ ONLY THE BREAKER IS SEPARATED BY ``leg_trace`` — this docstring
        used to say "both are recorded in `leg_trace`, which is the only signal
        that separates them from a genuine zero", and MEASURED on a real
        FalkorDB that is false for a dropped index: querying a label with NO
        fulltext index does not raise, it returns an EMPTY RESULT SET, so the
        trace reports ``ran=True, degraded=False, reason='empty_results'`` —
        indistinguishable from a clean miss. A dropped index is caught by the
        positive CONTROL, never by ``unmeasurable`` (#11536 review round 8, P2).
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


def test_an_UNMEASURABLE_probe_VETOES_even_when_the_CONTROL_answers(monkeypatch):
    """The fold's `unmeasurable` veto — the leg this whole file exists for.

    (1) FAILS on a `_merge_probe` that drops this veto: the probe's own read was
        DEGRADED (`ran=True, degraded=True` — the shape `run_fts_query` records
        for `query_failed` at `search_engine.py:1780`, and for a promoted
        `index_missing` at `:1776`; the two reasons DIFFER, so this names the
        FLAGS, not one reason), the
        CONTROL answered, and `_merge_probe(None, …)` then reached arm (b)
        `control_ok and not wrote` and returned `certifies=True, vetoed=None`, so
        `--dry-run --assert-clean` printed ✓ and exited 0 over a probe that
        measured NOTHING — the false PASS #11161 exists to close, on the very leg
        this file exists to pin.

        ⛔ WHY THE PRE-EXISTING UNMEASURABLE TESTS DO NOT CATCH THIS. None of them
        puts the fold in front of a DEGRADED probe row AND an ANSWERING control,
        which is the pair this test builds. (The adjacent
        `test_an_UNMEASURABLE_AFTER_read_VETOES_a_DISCRIMINATING_probe` DOES build
        that pair — it pins the arm's ORDER, over a BEFORE row that HIT, so it
        cannot stand in for this one.) MEASURED at this head: the documented-
        contracts test DOES fold over an ANSWERING control
        (`_merge_probe(None, probe_fts(..., control=True))`), but the leg it folds
        is HEALTHY — `unmeasurable == []` for that report — so this veto is never
        reached; its DEGRADED readings are the ones that pass `control=False`
        (`report["control"] is None`, measured on `breaker_open`/`index_missing`/
        `query_failed`) and they never reach `_merge_probe` at all. The
        breaker-open test DOES pass `control=True`, but its fixture does not answer
        the control's own lexicon read, so that arm reports
        `mode="unavailable", ok=False` (`note: "no usable control token was
        found, and the Source index-entry read FAILED …"`). ⛔ AND THE DEGRADED LEG
        IS NOT WHAT MAKES IT UNAVAILABLE: the identical fixture with a HEALTHY leg
        reports a byte-identical control block, because THIS fixture's control
        reads — the lexicon read and the index-entry read — go through `g.query`,
        and it never reaches the control's `mode="probe"` arm (which DOES read
        through the patched `run_fts_query` — `source_fts_probe` is called at
        `session_source_prose_migration.py:2138`). ⛔ AND ON THE SHIPPED LADDER
        THAT TEST IS NOT VETOED AS `uncorroborated` — this arm sits above every
        control arm, so the fold returns `vetoed="unmeasurable"` for it too. The
        wrong-veto outcome is what a fold that DROPS this veto reaches; with the
        arm present, nothing there distinguishes the right refusal from the wrong
        one, which is why none of them pins "the probe's own read did not run".
        Verified by mutation at this head: with `elif out["unmeasurable"]:`
        deleted, every test that folds a report carrying a non-empty
        `unmeasurable` reds on `certifies is False` — this one and the adjacent
        `test_an_UNMEASURABLE_AFTER_read_VETOES_a_DISCRIMINATING_probe` — and no
        other test does; MOVING that veto below arm (b) reds this one the same
        way. ⛔ AND THAT IS ALL THIS FIXTURE PINS: its probe never discriminated,
        so moving the veto below `elif discriminating:` leaves THIS test green —
        the claim that its "ORDER in the ladder is pinned" was false. The arm
        ABOVE `discriminating` is pinned by the adjacent
        `test_an_UNMEASURABLE_AFTER_read_VETOES_a_DISCRIMINATING_probe`.
    (2) REACHABLE: a `query_failed` on the PROBE's own query — the control's read
        is a SEPARATE call, so it still answers (`_breaker_allow` is evaluated per
        call: `search_engine.py:1615`/`:1653`) — or a breaker still open when the
        probe read ran that had recovered before the control read.
    """
    from tortoise import search_engine as se
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        """Answers the CONTROL read, so a control token exists and can answer."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    def fake(graph, query, **kwargs):
        # THE PROBE's read is the DEGRADED one; the CONTROL's read runs and
        # answers. Same shape the read path records for `query_failed`.
        degraded = query == "zebraquux"
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({
                "leg": "fts", "ran": True, "degraded": degraded,
                "reason": "query_failed" if degraded else "ok"})
        return [] if degraded else [("session:s-post", 2.0)]

    monkeypatch.setattr(se, "run_fts_query", fake)
    report = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]), ["zebraquux"],
                         control=True)

    # THE PREMISE, asserted so a fixture change cannot silently empty this test:
    # the probe measured nothing, the instrument still answered, and no earlier
    # veto in the fold's ladder is reached.
    assert report["unmeasurable"] == ["zebraquux"], (
        "the fixture did not degrade the PROBE's read: "
        + repr(report["probes"][0]))
    assert (report["control"] or {}).get("ok") is True, (
        # ⛔ THE REASON IS NOT "uncorroborated" (#11536 review P2, MEASURED):
        # the `unmeasurable` arm sits ABOVE every control arm, so the veto is the
        # same whatever the control reports (`ok=True`, `ok=False` and `None` all
        # fold to `vetoed="unmeasurable"`). This premise matters for a different
        # reason: with a NON-answering control arm (b) could not fire, so with the
        # veto deleted the fold would refuse as `uncorroborated` and
        # `certifies is False` would stay GREEN — only the `vetoed` label would
        # red. The premise is what makes the CERTIFICATION assertion the one that
        # catches the deletion (verified: veto deleted + control ok=False ->
        # `certifies=False vetoed="uncorroborated"`).
        "the fixture did not make the CONTROL answer — with a non-answering "
        "control arm (b) could not fire, so with the veto deleted the fold would "
        "refuse as `uncorroborated` and `certifies is False` would stay GREEN; "
        "only the `vetoed` label would red. This premise is what makes the "
        "CERTIFICATION assertion the one that catches the deletion: "
        + repr(report["control"]))
    assert report["leaked"] == [] and report["stale"] == [], (
        "the fixture is meant to reach the `unmeasurable` arm alone, not an "
        "earlier veto: " + repr(report))

    folded = _cli._merge_probe(None, report)
    assert folded["certifies"] is False, (
        "a probe whose OWN READ WAS DEGRADED certified — `--dry-run "
        "--assert-clean` would exit 0 over a leg that measured nothing")
    assert folded["vetoed"] == "unmeasurable", (
        "the refusal is not the unmeasured-probe one: "
        + repr(folded["vetoed"]))


def test_an_UNMEASURABLE_AFTER_read_VETOES_a_DISCRIMINATING_probe():
    """The `unmeasurable` veto sits ABOVE arm (a) — over a probe that DID hit.

    (1) FAILS on a fold whose `unmeasurable` arm is ordered BELOW
        `elif discriminating:`: `discriminating` is computed from the BEFORE
        row's residue and the AFTER residue buckets, INDEPENDENT of
        `degraded` — so a degraded AFTER read over a BEFORE row that DID hit
        sets BOTH, and arm (a) then certifies a leg whose AFTER read measured
        nothing. MEASURED on that ordering mutation: this fixture's
        `certifies`/`vetoed` go from `False`/`unmeasurable` to `True`/`None`.
    (2) REACHABLE: a `query_failed` on the PROBE's own AFTER read, on a token that
        hit before the writes — the control's read is a separate call, so it
        still answers, which is the same transient the neighbouring test names.
    """
    before = {"ran": True, "probes": [{"phrase": "zebraquux",
                                       "residue": ["tortoise://n1"]}]}
    after = {"unmeasurable": ["zebraquux"], "control": {"ok": True},
             "leaked": [], "truncated": [],
             "probes": [{"phrase": "zebraquux", "residue": [], "stale": False,
                         "unexplained": None, "unread": []}]}
    # `wrote=True` because a BEFORE reading is only taken on a writing run, so
    # that is the production shape this fixture has to stand for.
    folded = _cli._merge_probe(before, after, wrote=True)

    # THE PREMISE: this fixture really does reach BOTH states at once, so a
    # reordering cannot be mistaken for a fixture that never sees one of them.
    assert folded["unmeasurable"] == ["zebraquux"], (
        "the fixture did not degrade the AFTER read: " + repr(folded))
    assert folded["probes"][0]["discriminating"] is True, (
        "the fixture's BEFORE row did not HIT, so the `discriminating` arm this "
        "test is about is never reached: " + repr(folded["probes"][0]))
    assert folded["leaked"] == [] and not folded.get("stale"), (
        "an earlier veto in the ladder is reached, not the one under test: "
        + repr(folded))

    assert folded["certifies"] is False, (
        "a probe whose AFTER read was DEGRADED certified through arm (a) — the "
        "veto is ordered below `discriminating`, so the same false PASS survives "
        "one arm later")
    assert folded["vetoed"] == "unmeasurable", (
        "the refusal is not the unmeasured-probe one: "
        + repr(folded["vetoed"]))


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


def test_a_TYPOd_probe_token_cannot_certify_a_run_that_WROTE(monkeypatch, capsys):
    """#11536 review, P1: `control_ok` certified a probe the run saw match NOTHING.

    (1) FAILS on the shipped fold (`elif discriminating or control_ok`): the
        documented `--repair --probe-phrase <token>` usage makes the operator
        INVENT the token, so a mistyped one reads 0 BEFORE the writes and 0 after,
        the control token answers, and the run prints "Repair complete." and
        exits 0 — over a graph whose index still returns the sentence for its real
        tokens. That is the false PASS #11161 exists to close, reached by
        mistyping one word.
    (2) REACHABLE: `mode="probe"` is available on every graph that has a post-fix
        lexicon node (the record arm's own output), i.e. on the population the
        writing arm is run against.
    (3) POLARITY THAT MUST NOT GO RED: a dry run takes no BEFORE reading at all, so
        it still certifies; a writing arm with NOTHING to write still certifies on
        the control (no removal happened whose absence a token could have shown);
        and a probe that DID hit before certifies on the same control.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    graph = _LexiconGraph([(_post_fix_node(),)])
    # the probe hits nothing; the CONTROL token ("migration") does answer
    _install_fts(monkeypatch, {"zebraquux": [], "migration": [("session:s-post", 2.0)]})
    after = m.probe_fts(graph, ["zebraquux"], control=True)
    assert after["control"]["ok"] is True and after["probes"][0]["hits"] == 0

    before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 0,
                                       "urls": [], "residue": []}]}
    folded = _cli._merge_probe(before, after, wrote=True)
    assert folded["certifies"] is False, (
        "the fold certified a token this run watched match no pre-fix prose, on "
        "the control alone — the control proves the READ, not the TOKEN")
    assert folded["vetoed"] == "undiscriminating-probe"
    assert folded["undiscriminating"] == ["zebraquux"]

    # (3) the three polarities that must stay green.
    assert _cli._merge_probe(before, after, wrote=False)["certifies"] is True, (
        "a writing arm with NOTHING to write lost its ability to certify")
    assert _cli._merge_probe(None, after)["certifies"] is True, (
        "a dry run lost its ability to certify (the round-1 P2 regression)")
    hit_before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                            "urls": ["session:s-post"],
                                            "residue": ["session:s-post"]}]}
    assert _cli._merge_probe(hit_before, after, wrote=True)["certifies"] is True, (
        "a probe that HIT the pre-fix prose and reads 0 now did not certify")

    # argv: the documented writing arm must not report the repair as done.
    _export_a_record_dsn(monkeypatch)
    _install_fts(monkeypatch, {"zebraquux": [], "migration": [("session:s-post", 2.0)]})
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": ["session:s-post"], "repaired": [],
                            "empty_sessions": [], "no_record": [],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": ["session:s-post"], "notes": {},
                            "census_before": 1, "census_after": 0,
                            "dry_run": False})
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1, (
        "a repair WROTE, then signed off on a token it had just measured to "
        "match nothing")
    captured = capsys.readouterr()
    assert "Repair complete." not in captured.out
    assert "matched no pre-fix prose even BEFORE the writes" in captured.err


def test_the_certify_line_states_ONLY_what_the_run_measured(monkeypatch, capsys):
    """#11536 review, P1 (the claim half): a dry run may not assert the Target.

    (1) FAILS on the shipped report: with no BEFORE reading the run printed
        "no node still carrying the pre-fix prose is findable" — the Target's
        conjunctive property — from a probe whose token was never shown to be in
        the corpus. An answering instrument is evidence about the READ.
    (2) REACHABLE: the runbook's own step-3 command (`--dry-run --assert-clean`),
        which by construction takes no BEFORE reading.
    (3) POLARITY THAT MUST NOT GO RED: a probe that matched pre-fix prose BEFORE
        the writes STILL earns the categorical line.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    # (1)+(2) the documented step 3: a zero, corroborated by the control only.
    # ⛔ A URI, or the CLI exits at its own `no DB URI` guard before ANY assertion
    # (#11536 review round 5, P2) — the row then has zero discriminating power on a
    # box with no ambient URI, which is exactly how it looked green.
    _export_a_record_dsn(monkeypatch)
    _install_fts(monkeypatch, {"zebraquux": [], "migration": [("session:s-post", 2.0)]})
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--dry-run", "--assert-clean", "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 0
    out = capsys.readouterr().out
    # ⛔ assert the ABSENCE claim that would be the over-claim, not a phrase that a
    # later rewording silently drops (#11536 review round 4: this row passed
    # vacuously once the wording changed).
    assert "no node still carrying the pre-fix prose is findable" not in out, (
        "a dry run asserted the Target's before/after property from a token it "
        "never showed was in the corpus")
    assert "did NOT establish that the token was ever in the corpus" in out
    # ⛔ ASSERT ON THE GREEN ITSELF (#11536 review round 5, P2): a bare
    # `"0 hit(s)"` is emitted by the per-probe line on EVERY run, so it passed on
    # the pre-fix wording too. These two red on the pre-fix green AND on a revert.
    # ⛔ AND IT MUST NOT PIN THE OVER-CLAIM (round 8, P1): this row used to assert
    # `"findable through the Source FTS index (0 hit(s)" in out`, which POSITIVELY
    # required the Target's unmeasured conjunctive property in the green — so the
    # wording fix could not land without this assertion's permission, and the
    # absence row above was satisfiable by dropping one adverb. The count is now
    # pinned on the counted clause, and the Target phrase is asserted ABSENT.
    assert "(0 hit(s), across 0 url(s) whose nodes' own" in out, (
        "the green did not state the count it measured")
    assert "through the Source FTS index" not in out, (
        "the non-discriminating green is back to claiming the Target's "
        "conjunctive property from a token nothing showed was ever in the "
        "corpus")
    assert "the probe token returns NOTHING" not in out, (
        "the pre-fix green is back — it claims the probe returns nothing, which "
        "is false as soon as a hit is classified benign")

    # (3) the writing arm, where the token DID hit before, keeps the full claim.
    hit_before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                            "urls": ["session:s-post"],
                                            "residue": ["session:s-post"]}]}
    after = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]), ["zebraquux"],
                        control=True)
    _cli._report_fts(_cli._merge_probe(hit_before, after, wrote=True))
    out = capsys.readouterr().out
    assert "no node still carrying the pre-fix prose is findable" in out, (
        "a probe that watched the sentence LEAVE the index lost the categorical "
        "claim it had earned")


def test_a_PARTIAL_repair_does_not_state_the_POPULATION_claim_from_one_token(
        capsys):
    """#11536 review round 8, P2: a token-scoped measurement cannot carry a
    population claim.

    (1) FAILS on the shipped green: it printed "no node still carrying the pre-fix
        prose is findable through the Source FTS index" whenever ANY probe was
        `discriminating` — and `discriminating` is built from ONE token's
        before/after pair, so a PARTIAL repair satisfied it while another pre-fix
        node still carried prose. MEASURED on a real FalkorDB: two pre-fix nodes,
        only one repaired → `certifies=True`, `discriminating=True`,
        `census_after == 1`, and `query 'greenhouse'` still returned the
        UNREPAIRED node while the ✓ line claimed none was findable.
    (2) REACHABLE: any `--repair` whose probe token appears in only SOME of the
        pre-fix nodes, which is the ordinary case on a real graph — the token is
        one word of one sentence.
    (3) POLARITY THAT MUST NOT GO RED: with the census at 0 (a COMPLETE repair)
        the categorical line is still printed, and with no census supplied the
        pinned round-1 decision (`test_the_certify_line_states_ONLY_what_the_run_
        measured`) still holds. Only the case the census CONTRADICTS narrows.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    hit_before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 1,
                                            "urls": ["session:s-post"],
                                            "residue": ["session:s-post"]}]}
    after = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]), ["zebraquux"],
                        control=True)
    folded = _cli._merge_probe(hit_before, after, wrote=True)

    # (3) a COMPLETE repair — the census this run measured is 0 — keeps the claim.
    _cli._report_fts(folded, 0)
    assert ("no node still carrying the pre-fix prose is findable"
            in capsys.readouterr().out), (
        "a complete repair lost the categorical claim it had earned")

    # (3) and the pinned no-census call is unchanged.
    _cli._report_fts(folded)
    assert ("no node still carrying the pre-fix prose is findable"
            in capsys.readouterr().out), (
        "the round-1 pinned polarity went red — a caller that supplies no census "
        "still gets the categorical line")

    # (1)+(2) a PARTIAL repair: the census lists a node this token never covered.
    _cli._report_fts(folded, 1)
    out = capsys.readouterr().out
    assert "no node still carrying the pre-fix prose is findable" not in out, (
        "a partial repair claimed that NO pre-fix node is findable while its own "
        "census still listed one: " + out)
    assert "the census still lists 1 node(s)" in out, (
        "the narrowed line did not say what the census DID measure: " + out)


def test_the_CLI_passes_the_census_it_measured_INTO_the_sign_off(monkeypatch):
    """#11536 review round 8, P2: the fix's PRODUCTION WIRING was unpinned.

    (1) FAILS on the pre-fix call site: replacing
        `_report_fts(fts, report.get("census_after"))` with `_report_fts(fts)` —
        the exact pre-fix call — left the ENTIRE suite green (MEASURED: mutation
        M5, 115 passed / 0 failed). So the P2 the previous commit closes could be
        reverted at its only production site with nothing red, which is the
        "verification step that cannot fail" class this leg exists to close.
    (2) REACHABLE: every `--dry-run`/`--repair` run that passes `--probe-phrase`
        reaches this call — it is the operator's sign-off line.
    """
    seen: list = []
    monkeypatch.setattr(_cli, "_report_fts",
                        lambda fts, census_after=None: seen.append(census_after))
    _export_a_record_dsn(monkeypatch)
    _install_fts(monkeypatch, {"zebraquux": [("session:pre", 3.0)]})
    graph = _RecordingGraph([(_post_fix_node(),)])
    monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                        lambda *a, **k: {
                            "candidates": [], "repaired": [],
                            "empty_sessions": [], "no_record": [],
                            "no_reader": [], "no_session_id": [],
                            "unaddressable": [], "left_alone": [],
                            "written": [{"url": "session:pre"}], "notes": {},
                            "census_before": 2, "census_after": 1,
                            "dry_run": False})
    _install_cli(monkeypatch, graph,
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    _cli.main()
    assert seen == [1], (
        "the sign-off was not handed the census this run MEASURED, so the "
        "narrowed line is unreachable in production even though the function "
        f"supports it — got {seen!r}")


def test_the_green_does_not_claim_a_BEFORE_reading_that_did_not_MEASURE(monkeypatch,
                                                                       capsys):
    """#11536 review round 5, P2: the new BEFORE clause was not the four-state one.

    (1) FAILS on the shipped green: its BEFORE clause was gated on
        `residue_before is not None`, which is true whenever a BEFORE reading
        EXISTS — so a leg that never ran (breaker open), came back DEGRADED, or
        returned hits nothing could classify printed as a measured zero:
        "the BEFORE reading this run took returned no pre-fix node either".
        `_before_leg_kind` already separates those four states, and both refusals
        branch on it; the green derived the same fact a second time.
    (2) REACHABLE: a breaker trip between the BEFORE and AFTER reads (the run that
        WROTE NOTHING is the one that certifies on the control).
    (3) POLARITY THAT MUST NOT GO RED: a BEFORE leg that DID measure and found no
        pre-fix node still says so.
    """
    from tortoise import session_source_prose_migration as m

    def _after():
        return {"ran": True, "entity_type": "source",
                "probes": [{"phrase": "zebraquux", "hits": 0, "urls": [],
                            "truncated": False, "ran": True, "degraded": False,
                            "reason": None, "residue": [], "lexicon": [],
                            "stale": [], "foreign": [], "unexplained": [],
                            "unread": []}],
                "leaked": [], "stale": [], "stem_only": [], "unmeasurable": [],
                "truncated": [], "unresolved": 0,
                "control": {"mode": "probe", "ok": True, "hits": 1}}

    measured = "returned no pre-fix node either"

    # (1) the three states that are NOT a measurement must not print as one.
    # ⛔ EACH STATE GETS ITS OWN WORD (#11536 review round 6): a degraded leg did
    # not measure; an `unclassified` one DID and could not finish — stamping
    # "did not measure" over the urls it measured contradicts the line.
    for overrides, marker in (
            ({"degraded": True, "reason": "breaker_open"},
             "BEFORE leg did not measure"),
            ({"unread": ["session:s-x"]}, "could not fully classify"),
            ({"unresolved": 1}, "could not fully classify"),
            # ⛔ AND THE TRUNCATED STATE HAS ITS OWN WORD (#11536 review round 9,
            # P2): the leg RAN and every hit was classified, only the window
            # filled — so "did not measure" was false about a count that is a
            # floor. It was the one state of `_before_leg_kind` with no row.
            ({"truncated": True, "hits": 50}, "window FILLED")):
        before = {"ran": True, "probes": [dict({"phrase": "zebraquux", "hits": 0,
                                                "residue": []}, **overrides)]}
        fts = _cli._merge_probe(before, _after(), wrote=False)
        assert _cli._before_leg_kind(fts["probes"][0]) != "clean-zero"
        _cli._report_fts(fts)
        out = capsys.readouterr().out
        assert measured not in out, (
            f"the green claimed a BEFORE reading that did not measure: {overrides}")
        assert marker in out, (
            f"the probe line's marker did not name this leg's state ({marker!r}): "
            + out)
    assert m  # the module import keeps this row's name in the probe diagnostic

    # (3) a BEFORE leg that DID measure still states its finding.
    before = {"ran": True, "probes": [{"phrase": "zebraquux", "hits": 0,
                                       "residue": [], "ran": True,
                                       "degraded": False, "reason": None}]}
    fts = _cli._merge_probe(before, _after(), wrote=False)
    assert _cli._before_leg_kind(fts["probes"][0]) == "clean-zero"
    _cli._report_fts(fts)
    out = capsys.readouterr().out
    assert measured in out

    # (4) THE PARENTHETICAL ITSELF (#11536 review round 5, P3): the refusal used to
    # infer "a token that is not in the corpus", which is FALSE when the BEFORE leg
    # ran and matched a benign node — and nothing pinned the correction, so a revert
    # went green. This is the `uncorroborated` arm, the one that prints it.
    # (5) TWO UNITS, NAMED AS TWO (#11536 review round 6, P3): `hits` counts FTS
    # rows, `lexicon` holds urls — so the url-mate pair (two lexicon nodes at one
    # url, which this module declares reachable) must not read as one accounted-for
    # hit beside one unexplained one.
    dup = _after()
    dup["probes"][0].update({"hits": 2, "lexicon": ["session:dup"],
                             "urls": ["session:dup", "session:dup"]})
    _cli._report_fts(_cli._merge_probe(before, dup, wrote=False))
    out = capsys.readouterr().out
    assert "(2 hit(s), across 1 url(s)" in out, (
        "the green mixed a row count with a url count, implying a hit it did not "
        "account for")

    no_control = _after()
    no_control["control"] = {"mode": "unavailable", "ok": False, "hits": None,
                             "note": "no post-fix lexicon node on this graph"}
    fts = _cli._merge_probe(before, no_control, wrote=False)
    assert fts["vetoed"] == "uncorroborated"
    _cli._report_fts(fts)
    err = capsys.readouterr().err
    assert "not in the corpus" not in err, (
        "the refusal inferred that the token is absent from the corpus, which the "
        "BEFORE reading did not measure")
    assert "BEFORE hit(s)" in err, (
        "the parenthetical does not state the BEFORE hit count it measured")


def test_the_green_line_claims_only_the_two_data_the_fold_holds(monkeypatch,
                                                              capsys):
    """#11536 review round 4, P2: the narrowed green asserted unmeasured specifics.

    (1) FAILS on the shipped narrowed line, in the two states the fold reaches:
        (a) "the probe token returns NOTHING" is false whenever the hits were
            classified BENIGN — the ORDINARY record-arm outcome, since the derived
            lexicon is the transcript's own topic list, so the runbook's own advice
            (take a token from the removed sentence) lands here; and
        (b) "this run took no BEFORE reading" is false on a WRITING arm that WROTE
            NOTHING, whose own probe line prints `0 residue before` two lines above
            — the operator is told to run the command they just ran.
    (2) REACHABLE: both are the documented usage — `--repair --probe-phrase` on a
        graph whose census is already 0.
    (3) POLARITY THAT MUST NOT GO RED: the dry run still states the absence it did
        measure, and still refuses to assert the Target.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    # a BENIGN hit: one row, on a node whose own lexicon accounts for the token
    _install_fts(monkeypatch, {"fridays": [("session:s-post", 2.0)],
                               "migration": [("session:s-post", 2.0)]})
    graph = _LexiconGraph([(_post_fix_node(),)])
    after = m.probe_fts(graph, ["fridays"], control=True)
    assert after["probes"][0]["hits"] == 1
    assert after["probes"][0]["lexicon"] == ["session:s-post"]

    # (a) the DRY RUN must not say the probe returns NOTHING.
    _cli._report_fts(_cli._merge_probe(None, after))
    out = capsys.readouterr().out
    assert "returns NOTHING" not in out, (
        "the green said the probe returns NOTHING while its own report listed a "
        "hit")
    assert "1 hit(s)" in out
    assert "did NOT establish that the token was ever in the corpus" in out

    # (b) a WRITING arm that wrote NOTHING did take a BEFORE reading.
    _install_fts(monkeypatch, {"notaword": [], "migration": [("session:s-post", 2.0)]})
    before = m.probe_fts(graph, ["notaword"], control=False)
    assert before["probes"][0]["residue"] == []
    _install_fts(monkeypatch, {"notaword": [], "migration": [("session:s-post", 2.0)]})
    after_zero = m.probe_fts(graph, ["notaword"], control=True)
    fts = _cli._merge_probe(before, after_zero, wrote=False)
    assert fts["certifies"] is True, "a run that wrote nothing lost its certify"
    _cli._report_fts(fts)
    out = capsys.readouterr().out
    assert "took no BEFORE reading" not in out, (
        "a writing arm that wrote nothing was told it took no BEFORE reading, "
        "while its own probe line printed `0 residue before`")
    assert "the BEFORE reading this run took returned no pre-fix node" in out


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
    _export_a_record_dsn(monkeypatch)
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
    assert "1 residue before" in captured.out, captured.out
    # The AFTER read here returns no rows at all, so both counts are 0 — that
    # pins the WORDS, not the split between them (#11536 review round 5: an
    # earlier version of this line asserted `1 hit(s)`, which this fake can never
    # produce). The split is pinned where the two counts differ, in
    # `test_the_report_SEPARATES_the_url_count_from_the_row_count`.
    assert "0 residue url(s) after, 0 hit(s)" in captured.out, captured.out


def test_an_UNCORROBORATED_zero_does_not_certify(monkeypatch, capsys):
    """A probe that hit nothing before AND has no control proves nothing.

    (1) FAILS on a leg that equates "0 hits" with "verified": an operator-typed
        token that is not in the corpus reads 0 on an unrepaired graph and on a
        repaired one alike.
    (2) REACHABLE: the documented usage makes the operator invent the token, and
        a typo is the ordinary case.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {"zebraquux": []})
    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--probe-phrase", "zebraquux"])
    # ⛔ PATCH THE VERB `probe_fts` ACTUALLY CALLS (#11536 review round 5): it
    # reads `lexicon_control_read`, so patching the `lexicon_control_token`
    # wrapper left this row's control reached by accident (via the fake's row
    # shape) instead of by the seam it names — and a change to that shape would
    # have moved the row to a different arm without reddening anything.
    monkeypatch.setattr(
        "tortoise.session_source_prose_migration.lexicon_control_read",
        lambda g, **k: (None, None))

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
        # ⛔ NOT `leaked` ALONE (#11536 review round 5, P3). `leaked` is built from
        # `residue` only, and an entry that survived the record arm's `_searchText`
        # REWRITE classifies `unexplained` — so the sentence could still be
        # retrievable while this row stayed green. Mirror the clear arm: assert the
        # ENGINE dropped the entry and that no bucket holds it.
        record_after = m.probe_fts(g, ["zebraquux"], control=True)
        assert record_after["probes"][0]["hits"] == 0, (
            "the probe still returns a node after the record arm — inspect "
            "whether it is a surviving index entry, which is the #11161 leak")
        assert record_after["leaked"] == [] and \
            record_after["unexplained"] == [], (
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
        exists for. ⛔ And the zero is not vacuous, but NOT for the reason the
        shipped rationale gave (#11536 review): `_SOURCE_INDEX_ENTRIES` counts
        nodes whose `_searchText` is NON-empty, so a STALE entry — a node the pair
        was cleared from — contributes 0 to it and can never make it non-empty.
        What makes the arm sound is that a surviving entry is caught by the probe
        itself (`stale`), and — ⛔ NOT by ``leg_trace`` (#11536 review round 8,
        P2; this rationale is the test-side copy of the one corrected on the
        arm): ``leg_trace`` reports ``ran=True, degraded=False,
        reason='empty_results'`` for a label with NO fulltext index, so
        ``unmeasurable`` never fires on a dropped index. A dead read is the
        positive CONTROL's job, and this arm deliberately has no control (it runs
        no FTS read at all) — it is sound because when no Source over
        ``documentKind IS NULL`` carries ``_searchText`` there is nothing for the
        index to hold.

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
    _export_a_record_dsn(monkeypatch)
    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(graph, ["zebraquux"], control=True)
    assert report["control"]["mode"] == "empty-index"
    assert report["control"]["ok"] is True
    # ⛔ THE ARM REPORTS ONLY WHAT IT MEASURED (#11536 review round 7, P2): `ran`
    # and `degraded` used to be LITERAL constants here (True/False) beside a note
    # that said "the read RAN", on the arm that runs no FTS read at all.
    assert "ran" not in report["control"] and "degraded" not in report["control"], (
        "the empty-index control still asserts that a read ran — it counts NODES, "
        f"so it establishes no such thing: {report['control']}")
    assert report["control"]["measured"] == "node-property", (
        "the arm does not say WHICH population it counted, so a consumer cannot "
        "tell it from a measured FTS control")
    assert m.source_index_is_empty(graph) is True

    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--probe-phrase", "zebraquux"])
    assert _cli.main() == 0, (
        "a CORRECTLY repaired clear-only graph could not be certified")
    out = capsys.readouterr().out
    assert "EMPTY Source index" in out
    assert "documentKind IS NULL" in out, (
        "the empty-index line does not state the population the query asks about "
        "— a DOCUMENT is a :Source whose `_searchText` is the #125 field, so an "
        "unscoped \"no Source on this graph\" is false there")
    assert "did not exercise the FTS read" in out, (
        "the empty-index control line still claims a read it never ran — it "
        "counts nodes: " + out)
    assert "the read RAN" not in out, (
        "the empty-index control still says the read ran: " + out)
    # ⛔ AND THE GREEN LINE MUST NOT BORROW THE `probe` ARM'S EVIDENCE (#11536
    # review round 7, P2): "the read ANSWERS" is what a MEASURED control proves;
    # this arm counted nodes, so the clause has to say so instead.
    assert "the read ANSWERS" not in out, (
        "the sign-off claimed the read ANSWERS on the arm that ran no FTS read: "
        + out)

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


class _PopulationGraph(_RecordingGraph):
    """Answers the index-entry read by EVALUATING the statement's predicates.

    ⛔ NOT A STRING-CONTAINS PASS (#11536 review, P3 at `cf302453`): the review's
    mutation M14 deleted ``documentKind IS NULL`` from
    :data:`_SOURCE_INDEX_ENTRIES` and **no row reddened** (``exit 0, 122
    passed``), because every fake in this file answers that read from a
    hard-coded count. Here the count is COMPUTED over the nodes actually listed,
    applying each predicate the statement carries — so dropping one lets a node
    through that the population is supposed to exclude, and the row fails.

    It also answers the positive control's lexicon window (``STARTS WITH
    $prefix``) from the same nodes, so a document-only graph yields no token.
    """

    def __init__(self, nodes, **kw):
        super().__init__([(n,) for n in nodes], **kw)
        self._nodes = list(nodes)

    def query(self, cypher, params=None, timeout=None):
        if "RETURN count(s)" in cypher:
            population = list(self._nodes)
            if "documentKind IS NULL" in cypher:
                population = [n for n in population
                              if n.get("documentKind") is None]
            if "coalesce(s._searchText, '') <> ''" in cypher:
                population = [n for n in population
                              if (n.get("_searchText") or "") != ""]
            return _Result([(len(population),)])
        if "STARTS WITH $prefix" in cypher:
            prefix = (params or {}).get("prefix", "")
            return _Result([(n.get("_searchText"),) for n in self._nodes
                            if str(n.get("summary") or "").startswith(prefix)
                            and n.get("_searchText") is not None])
        return super().query(cypher, params=params, timeout=timeout)


def test_a_DOCUMENT_only_graph_does_not_make_the_index_look_POPULATED(
        monkeypatch):
    """#11536 review (P3 at `cf302453`): `documentKind IS NULL` is LOAD-BEARING.

    (1) FAILS if that term is dropped from `_SOURCE_INDEX_ENTRIES` — the mutation
        the round-8 reviewer ran as M14, which reddened no row: a DOCUMENT is a
        `:Source` (D10/#5026) whose `_searchText` is the #125 field, so the
        UNSCOPED count reads 1 on a graph where the probe's own read
        (`run_fts_query(entity_type="source")`, same discriminator) sees none.
        The arm then reports a POPULATED index, `unavailable`, and a correctly
        repaired clear-only graph loses its only corroboration — a false
        UNMEASURABLE. The drift is fail-CLOSED, which is why it is a P3 and not
        higher; but it is the "guard whose regression is silent" class this PR
        exists to close, so it gets a row rather than a comment.
    (2) REACHABLE: every graph with document Sources — the census module's own
        §"Leg 1" exists because `is_episodic` is stamped on documents.
    """
    from tortoise import session_source_prose_migration as m

    docs_only = _PopulationGraph([_document_node(documentKind="report")])
    assert m.source_index_is_empty(docs_only) is True, (
        "a DOCUMENT-only graph read as a POPULATED Source index: the population "
        "count is not scoped to `documentKind IS NULL`, so the arm reports "
        "`unavailable` on a graph whose probe read can only see 0")

    # …and the arm the count feeds reports the STRUCTURAL zero, not `unavailable`.
    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(docs_only, ["zebraquux"], control=True)
    assert report["control"]["mode"] == "empty-index", (
        "the document-only graph did not reach the empty-index corroboration: "
        f"{report['control']}")
    assert report["control"]["ok"] is True

    # (3) AND THE SCOPE IS NOT A BLANKET EMPTINESS: a SESSION Source carrying
    # `_searchText` makes the same read non-empty, so the zero above is measured
    # against the population rather than asserted by the fake's own shape. (The
    # CLEARED node beside it contributes 0 — that is the stale-entry case, and it
    # is the probe's `stale` veto, not this arm.)
    with_session = _PopulationGraph([_document_node(documentKind="report"),
                                     _cleared_session_node(),
                                     _post_fix_node()])
    assert m.source_index_is_empty(with_session) is False


# ── #11536 review round 3: every hit node is examined, and the skip is stated ──


def test_TWO_nodes_at_ONE_url_are_BOTH_classified_so_a_prose_twin_cannot_certify(
        monkeypatch):
    """#11536 review round 3, P1 (fail-open): the url-keyed fold kept the last row.

    (1) FAILS on the shipped classifier: ``classify_fts_hits`` folded the hit
        rows into ``props_by_url[url] = props``, so ONE url carrying TWO
        ``:Source`` nodes kept whichever node the engine returned LAST. Here the
        last row is a REPAIRED twin and the first is a prose-carrying one, so the
        url was bucketed as a BENIGN stem collision (``residue`` came back empty)
        while the FTS read that produced the rows had returned the prose node too
        — the sentence was still retrievable. That empty ``residue`` is what this
        row's assertion reds on; it does NOT claim ``_merge_probe`` certified here
        (with no control and no BEFORE reading the fold vetoes `uncorroborated`
        anyway — the CLI-level shape is the next test's). #11161's own failure
        mode, through a different door.
    (2) REACHABLE: ``:Source`` declares NO uniqueness on ``url``
        (``find_session_sources_carrying_prose`` says so and dedupes on ``id(s)``
        for exactly this reason, #11176), so a second writer's CREATE can leave two
        nodes at one (label, key) and the probe's read returns BOTH rows.
    (3) WHAT THIS ROW DOES AND DOES NOT CLAIM (#11536 review round 3, note on this
        very test): it pins the FOLD's BUCKET — a url whose other node still
        carries the prose is not reported as a benign stem collision. It is NOT by
        itself a CLI-level false PASS: the census reads the same predicate over the
        same population, so it lists this prose twin as a candidate (asserted
        below) and the repair either clears it or the census refuses to reach 0.
        The CLI-level shape — census 0 while the index still answers — is the
        STALE twin, and it is the next test (`..._DISAGREE_...`) that carries it.
        Stating the split matters: the previous docstring claimed the CLI would
        certify here, which this fixture cannot show.
    (4) THE OTHER POLARITIES: an episodic prose twin still WINS as `residue`, and
        two PROVEN lexicon twins stay a stem collision, so agreement is not
        punished.
    """
    from tortoise import session_source_prose_migration as m

    prose_twin = _session_node(url="session:dup", sessionId="dup",
                               summary=PROSE, _searchText=PROSE)
    repaired_twin = _post_fix_node(url="session:dup", sessionId="dup")
    # ⛔ ORDER IS THE WHOLE TEST (#11536 review round 4, blocking): the pre-fix
    # fold was `props_by_url[url] = props`, which keeps the LAST row. So the
    # REPAIRED twin must come LAST for that fold to read the url as benign — with
    # the prose twin last, the old fold yields `residue` on its own and this row
    # would pass on the very code it exists to red.
    graph = _RecordingGraph([(prose_twin,), (repaired_twin,)])

    # `friday` is a token BOTH twins carry: the repaired one as lexicon, the
    # prose one as prose.
    _install_fts(monkeypatch, {"friday": [("session:dup", 1.0),
                                         ("session:dup", 2.0)]})

    assert [n["url"] for n in m.find_session_sources_carrying_prose(graph)] == [
        "session:dup"], (
        "the census reads the same predicate over the same population, so it DOES "
        "see this prose twin — which is why this row claims the bucket, not a "
        "census-blind false PASS")

    report = m.probe_fts(graph, ["friday"], control=False)
    probe = report["probes"][0]
    assert probe["hits"] == 2
    assert probe["residue"] == ["session:dup"], (
        "a url whose OTHER node still carries the pre-fix prose was classified "
        f"as benign: {probe}")
    assert probe["lexicon"] == [] and probe["unread"] == []

    folded = _cli._merge_probe(None, report)
    assert folded["certifies"] is False and folded["vetoed"] == "leaked", (
        "the fold certified over a url whose prose twin the probe returned")

    # (2b) an EPISODIC prose twin still takes the `leaked` arm, not `unexplained`.
    episodic_twin = _session_node(url="session:dup3", sessionId="dup3",
                                  summary=PROSE, _searchText=PROSE)
    repaired_twin3 = _post_fix_node(url="session:dup3", sessionId="dup3")
    _install_fts(monkeypatch, {"friday": [("session:dup3", 1.0),
                                         ("session:dup3", 2.0)]})
    report = m.probe_fts(_RecordingGraph([(repaired_twin3,), (episodic_twin,)]),
                         ["friday"], control=False)
    probe = report["probes"][0]
    assert probe["residue"] == ["session:dup3"] and probe["lexicon"] == [], (
        f"an episodic prose twin lost to a repaired twin: {probe}")
    folded = _cli._merge_probe(None, report)
    assert folded["certifies"] is False and folded["vetoed"] == "leaked"

    # (3) two proven-lexicon twins at one url: agreement, not a false veto.
    twin_a = _post_fix_node(url="session:dup2", sessionId="dup2")
    twin_b = _post_fix_node(url="session:dup2", sessionId="dup2")
    _install_fts(monkeypatch, {"friday": [("session:dup2", 1.0),
                                         ("session:dup2", 2.0)]})
    split = m.classify_fts_hits(_RecordingGraph([(twin_a,), (twin_b,)]),
                               ["session:dup2"], phrase="friday")
    assert split["lexicon"] == ["session:dup2"] and split["residue"] == [], (
        "two PROVEN lexicon twins were vetoed — a false veto is re-measured, "
        "but it must not fire on agreement")


def test_nodes_at_one_url_that_DISAGREE_are_unexplained_not_benign(monkeypatch):
    """#11536 review round 3: disagreement at one url is not provably benign.

    (1) FAILS on a fold that picks one label per url: a url holding a STALE twin
        (index entry outlived a cleared node) and a proven-lexicon twin would be
        labelled from whichever came last, so half of what the read returned was
        bucketed without being examined.
    (2) REACHABLE — and this is the CLI-level OVER-BLOCK (#11536 review round 3;
        RATIONALE CORRECTED in round 7, P2): the stale twin carries NO text, so the
        CENSUS cannot see it (asserted below), and the lexicon twin is not a
        candidate either. So `--dry-run --assert-clean` reads a ZERO census while
        the probe still returns this url.
        ⛔ It is NOT the #11161 "sentence is still retrievable" shape, which the
        earlier rationale here claimed: the stale twin is `summary=""` with
        `_searchText` ABSENT, so it carries no value of the field the Source index
        is declared on and can hold no index entry (the PR's own live leg asserts
        exactly that — `hits == 0` once the pair is cleared), and the hit in this
        fixture comes from the BENIGN lexicon twin. What the row therefore pins is
        the FAIL-CLOSED VETO for a url whose nodes disagree — a false veto, which
        is the correct polarity ("a false veto is re-measured; a false
        certification is the leak") but is not a leak.

    The leg's own polarity decides the bucket: "a false veto is re-measured; a
    false certification is the leak".
    """
    from tortoise import session_source_prose_migration as m

    stale_twin = _session_node(url="session:mixed", sessionId="mixed",
                               summary="", _searchText=None,
                               topics=["friday", "rebuild"])
    lexicon_twin = _post_fix_node(url="session:mixed", sessionId="mixed")
    graph = _RecordingGraph([(stale_twin,), (lexicon_twin,)])

    assert m.find_session_sources_carrying_prose(graph) == [], (
        "the false PASS depends on the census NOT seeing this url: the stale twin "
        "carries no text and the lexicon twin is not a candidate")

    split = m.classify_fts_hits(graph, ["session:mixed"], phrase="friday")
    assert split["unexplained"] == ["session:mixed"], (
        f"a url whose nodes disagree was bucketed anyway: {split}")
    assert split["lexicon"] == [] and split["stale"] == []

    # …and it VETOES: a census that reads 0 must not certify over it.
    _install_fts(monkeypatch, {"friday": [("session:mixed", 2.0)]})
    report = m.probe_fts(graph, ["friday"], control=False)
    assert report["probes"][0]["hits"] == 1
    folded = _cli._merge_probe(None, report)
    assert folded["certifies"] is False and folded["vetoed"] == "unexplained", (
        "a census-blind hit at a disagreeing url certified the leg")

    # …and the message names the cause that applies, so the next reader is not
    # told the single-node story about a two-node url (#11536 review round 3).
    import io
    from contextlib import redirect_stderr
    buf = io.StringIO()
    with redirect_stderr(buf):
        _cli._report_fts(folded)
    said = buf.getvalue()
    assert "DISAGREE" in said, (
        "the refusal did not name the disagreeing-twins cause: " + said)
    assert "PROVES it carries the post-fix lexicon, while the probe token " \
           "matches none" not in said, (
        "the refusal still asserted the single-node cause for a two-node url: "
        + said)


def test_the_control_walks_PAST_a_lexicon_node_whose_tokens_are_ALL_unusable(
        monkeypatch):
    """#11536 review round 3, note: ``LIMIT 1`` reported a false absence.

    (1) FAILS on the shipped control: ``_CONTROL_LEXICON_ROWS`` read ONE node and
        rejected its tokens through ``probe_phrase_rejection`` — a token that
        tokenises to 0 terms is not a probe. The lexicon is word-FREQUENCY
        derived (``Counter.most_common``), so a node whose ``_searchText`` is
        ``"the, and, to, of, it"`` yields no usable token, and the run reported
        "no POST-fix lexicon node to control with" — a statement about the graph
        it had not measured — while a perfectly usable node sat in the next row.
        The documented remedy it then offered was ``--no-fts-probe``, i.e. the
        weaker evidence this leg exists to stop certifying on.
    (2) REACHABLE: the lexicon writer takes the top word frequencies, and the
        stopword filter is what makes a high-frequency node's tokens unusable.
    """
    from tortoise import session_source_prose_migration as m

    class _StoppardThenReal(_RecordingGraph):
        """Honours the Cypher ``LIMIT``: rows are all stopwords, then usable.

        ⛔ THE FAKE MUST HONOUR ``LIMIT``, or this row measures nothing: the
        pre-fix read was ``LIMIT 1``, and a fake that returned every row anyway
        made the shipped ``LIMIT 1`` look like it walked the window (#11536
        review round 3). The real server honours the clause, so a fake that
        ignores it cannot discriminate.
        """

        ROWS: tuple[tuple[str], ...] = (("the, and, to, of, it",),
                                       ("migration, plan, friday",))

        def __init__(self):
            super().__init__([])

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                # The real server honours the clause, wherever it sits (the
                # query carries an ORDER BY before it) — a fake that returned
                # every row regardless could not discriminate a `LIMIT 1` read.
                limit = int(re.search(r"LIMIT (\d+)\s*$", cypher).group(1))
                return _Result(self.ROWS[:limit])
            return super().query(cypher, params=params, timeout=timeout)

    assert m.lexicon_control_token(_StoppardThenReal()) == "migration", (
        "the control stopped at the first lexicon row and reported the graph "
        "had no usable node")

    # ⛔ THE WINDOW IS ORDERED, and what the note claims is BOUNDED BY IT
    # (#11536 review round 3, note): an unordered `LIMIT` makes the control's
    # availability non-deterministic between two runs over an unchanged graph, and
    # "no usable token" over a window is not a statement about the whole graph.
    assert "ORDER BY s.url, id(s)" in m._CONTROL_LEXICON_ROWS, (
        "the control's window is not TOTALLY ordered: a url with two `:Source` "
        "nodes (no uniqueness on `url`) is a tie, and a tie leaves the window "
        "free to differ between two runs over an unchanged graph")
    assert f"LIMIT {m._CONTROL_LEXICON_WINDOW}" in m._CONTROL_LEXICON_ROWS, (
        "the query's LIMIT is not the constant the note names, so the two can "
        "drift and the note would state a window the read did not use")

    class _Busy(_StoppardThenReal):
        """A graph the index DOES hold entries for, with NO usable control row."""

        ROWS: tuple[tuple[str], ...] = (("the, and, to, of, it",),)

        def query(self, cypher, params=None, timeout=None):
            if "coalesce(s._searchText, '') <> ''" in cypher:
                return _Result([(3,)])
            return super().query(cypher, params=params, timeout=timeout)

    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(_Busy(), ["zebraquux"], control=True)
    assert report["control"]["mode"] == "unavailable"
    note = report["control"]["note"]
    assert "window the control reads" in note, (
        f"the note claims a universal about a bounded read: {note}")
    assert str(m._CONTROL_LEXICON_WINDOW) in note, (
        f"the note does not name the window it read: {note}")


def test_a_JSON_signoff_STATES_the_leg_it_did_not_measure(monkeypatch, capsys):
    """#11536 review round 3, note: the `--no-fts-probe` notice was `--json`-blind.

    (1) FAILS on the shipped CLI: the notice sat BELOW the ``--json`` return, so
        ``--dry-run --assert-clean --no-fts-probe --json`` printed pure JSON with
        no ``fts_probe`` key at all and NOTHING on either stream said the index
        was skipped — a machine consumer could not tell "not measured" from
        "measured and clean", which is the census-only green this flag exists to
        make auditable.
    (2) REACHABLE: the documented by-name escape, in the mode a pipeline uses.
    """
    _export_a_record_dsn(monkeypatch)
    graph = _RecordingGraph([(_post_fix_node(),)])
    _install_fts(monkeypatch, {})
    _install_cli(monkeypatch, graph, ["--dry-run", "--assert-clean",
                                      "--no-fts-probe", "--json"])
    capsys.readouterr()
    assert _cli.main() == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["fts_probe_skipped"] == "no_fts_probe", (
        "the JSON sign-off did not state that the FTS leg was skipped")
    assert "fts_probe" not in payload
    assert "CENSUS only" in captured.err, (
        "the skip was silent on stderr too")
    assert "taken by name" in captured.err, (
        "`--no-fts-probe` IS the by-name acceptance, and the notice must say so: "
        + captured.err)

    # …and a repair with no probe names its own reason, on the same field.
    # ⛔ AND IT MUST NOT CLAIM A NAME THAT WAS NEVER GIVEN (#11536 review round
    # 12, P2). `fts_probe_skipped` has two arms; this is the OMISSION one — the
    # flags were simply left off, which `--repair` does not refuse (only
    # `--assert-clean` does, at exit 2). The notice used to print "taken by name"
    # on BOTH arms, i.e. it asserted an operator acceptance that never happened,
    # in the one message whose job is to state the evidence honestly.
    _install_cli(monkeypatch, graph, ["--repair", "--clear-without-record",
                                      "--json"])
    capsys.readouterr()
    assert _cli.main() in (0, 1)
    omitted = capsys.readouterr()
    payload = json.loads(omitted.out)
    assert payload["fts_probe_skipped"] == "no_probe_phrase"
    assert "by OMISSION" in omitted.err, (
        "the omission arm did not disclose that nothing was named: "
        + omitted.err)
    assert "taken by name" not in omitted.err, (
        "the notice claimed a by-name acceptance the operator never gave: "
        + omitted.err)


def test_the_documented_report_contracts_are_the_ones_the_run_RETURNS(monkeypatch):
    """#11536 review round 3, P2: the docstring described FIVE of the SIX buckets.

    (1) FAILS on the shipped contracts, both halves:
        * `classify_fts_hits`'s ``Returns`` listed residue/lexicon/stale/foreign/
          unread and OMITTED ``unexplained`` — the bucket the very paragraph below
          it exists to justify, and the one that VETOES. A caller implementing the
          fold from the docstring would have dropped the veto;
        * `probe_fts`'s ``Returns`` listed five of the EIGHT top-level keys: it
          omitted ``stale``/``unexplained``/``unresolved`` (the three vetoeing
          aggregates) and the probe row's ``unresolved``, so the report's own
          contract did not mention that a hit nothing could examine is vetoed;
        * and ``"ran": bool  # at least one probe was measured`` was implemented
          as ``bool(phrases)`` — MEASURED: a breaker-open leg gave
          ``report["ran"] is True`` while ``probes[0]["ran"] is False``, i.e. the
          report claimed the instrument answered for a run that measured nothing.
    (2) REACHABLE: both keys are on every ``--json`` report, and the breaker case
        is the ordinary degraded leg this leg already separates from a clean miss.
    (3) POLARITY THAT MUST NOT GO RED: a measured leg reports ``ran`` True, and
        every documented key is genuinely present in the returned dict.
    """
    from tortoise import session_source_prose_migration as m

    class _LexiconGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    # (3) a MEASURED leg: the documented keys are all there, and `ran` is True.
    _install_fts(monkeypatch, {"zebraquux": [], "migration": [("session:s-post", 2.0)]})
    report = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]), ["zebraquux"],
                         control=True)
    assert report["ran"] is True
    probe_doc = " ".join((m.probe_fts.__doc__ or "").split())
    for key in report:
        assert f'"{key}"' in probe_doc, (
            f"`probe_fts` RETURNS {key!r} but its documented `Returns` block "
            f"never lists it — the report's contract omits a key the run "
            f"actually emits")
    for key in report["probes"][0]:
        assert key in probe_doc, (
            f"a probe ROW carries {key!r} but the documented row shape never "
            f"names it")
    # ⛔ AND THE CONTROL BLOCK (#11536 review round 5): it is the one sub-dict the
    # contract used to undersell — `mode` is emitted on every arm and is what
    # separates a corroborating probe from a structurally empty index.
    assert report["control"] is not None
    for key in report["control"]:
        # ⛔ QUOTED, like the top-level leg above (#11536 review round 6): an
        # unquoted membership test is satisfied by the PROSE around the contract,
        # so deleting `"mode": …` from the documented shape would leave this leg
        # green — measured.
        assert f'"{key}"' in probe_doc, (
            f"the control block carries {key!r} but the documented contract "
            f"never names it")
    split = m.classify_fts_hits(_LexiconGraph([(_post_fix_node(),)]),
                                ["session:s-post"], phrase="migration")
    split_doc = " ".join((m.classify_fts_hits.__doc__ or "").split())
    for key in split:
        assert f'"{key}"' in split_doc, (
            f"`classify_fts_hits` returns {key!r} and its `Returns` list omits "
            f"it")
    assert set(split) == {"residue", "lexicon", "stale", "foreign",
                          "unexplained", "unread"}

    # ⛔ AND THE FOLD ITSELF (#11536 review round 7, P2). `_merge_probe`'s dict is
    # `--json`'s `fts_probe` AND the only input to `fts_gate`, so a key it emits
    # and does not document is a contract change nothing records — the same rule
    # the two functions above are held to. It was the one report in the chain
    # left out, on the branch whose head commit is "the repair's contract names
    # the keys its verdict is read by".
    _install_fts(monkeypatch, {"zebraquux": [],
                              "migration": [("session:s-post", 2.0)]})
    folded = _cli._merge_probe(None, m.probe_fts(_LexiconGraph(
        [(_post_fix_node(),)]), ["zebraquux"], control=True))
    merge_doc = " ".join((_cli._merge_probe.__doc__ or "").split())
    for key in folded:
        assert f'"{key}"' in merge_doc, (
            f"`_merge_probe` RETURNS {key!r} but its documented contract never "
            f"lists it — and that dict is the `--json` surface the verdict is "
            f"read from")
    for key in folded["probes"][0]:
        assert key in merge_doc, (
            f"a merged probe row carries {key!r} but the documented row shape "
            f"never names it")

    # (1)+(2) the breaker-open leg: "measured" must not survive a read that never
    # ran — `bool(phrases)` reported exactly that.
    _install_fts(monkeypatch, {}, leg={"leg": "fts", "ran": False,
                                       "degraded": True,
                                       "reason": "breaker_open"})
    degraded = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]), ["zebraquux"],
                           control=False)
    assert degraded["probes"][0]["ran"] is False
    assert degraded["ran"] is False, (
        "`ran` documents 'at least one probe was MEASURED', but this leg "
        "measured nothing and reported True — the report claimed an answering "
        "instrument")
    assert degraded["unmeasurable"] == ["zebraquux"]

    # ⛔ AND THE TWO DEGRADED EXITS THE BREAKER CASE DOES NOT COVER (#11536 review
    # round 8, P2): `run_fts_query` records `ran=True, degraded=True` for BOTH
    # `index_missing` and `query_failed`, so `any(p["ran"])` still reported
    # "measured" for a leg that measured nothing. MEASURED on a real FalkorDB: a
    # missing Source fulltext index does NOT raise — it returns an empty result
    # set — so `index_missing`-shaped emptiness is the ORDINARY shape of a dropped
    # index. The report's `ran` must be False there while the row's own `ran`
    # stays True, or a consumer cannot tell the two apart.
    for degraded_reason in ("index_missing", "query_failed"):
        _install_fts(monkeypatch, {}, leg={"leg": "fts", "ran": True,
                                           "degraded": True,
                                           "reason": degraded_reason})
        degraded = m.probe_fts(_LexiconGraph([(_post_fix_node(),)]),
                               ["zebraquux"], control=False)
        assert (degraded["probes"][0]["ran"] is True
                and degraded["probes"][0]["degraded"] is True), (
            "the read path's own flags must pass through verbatim — this row's "
            "premise is the shipped `index_missing`/`query_failed` shape")
        assert degraded["ran"] is False, (
            f"`ran` documents 'at least one probe was MEASURED', but the "
            f"{degraded_reason} leg measured nothing and reported True — a "
            f"consumer reading `ran` concludes the instrument answered")
        assert degraded["unmeasurable"] == ["zebraquux"]


def test_the_reconcile_contract_lists_every_key_the_run_returns():
    """#11536 review note: the repair's ``Returns`` block named ten of FOURTEEN keys.

    (1) FAILS on the shipped docstring. ``reconcile_session_source_prose`` emits
        ``candidates``/``repaired``/``empty_sessions``/``no_record``/
        ``no_reader``/``no_session_id``/``unaddressable``/``left_alone``/
        ``written``/``notes``/``census_before``/``census_after``/``dry_run``/
        ``timeout_ms``, and its contract listed ten of them: it omitted
        ``no_session_id`` (the bucket that stopped a DESTRUCTIVE write being
        reported as nothing), ``written`` (the count the CLI certifies a run by —
        ``wrote=bool(report["written"])`` selects the fail-closed arm of the FTS
        verdict) and ``timeout_ms``. A caller implementing the report from the
        docstring would not know the key the verdict depends on exists.
    (2) REACHABLE: all three are on EVERY report, dry run included (the "SAME
        keys in both modes" claim the block opens with), so this is the ordinary
        return shape, not a branch.
    (3) POLARITY THAT MUST NOT GO RED: a repair that WROTE reports its url in
        ``written``, so the documented key is genuinely emitted with meaning
        rather than merely named.

    ⛔ The membership test is QUOTED (``key``), the lesson round 6 measured one
    function over: an unquoted test is satisfied by the PROSE around the
    contract, so deleting a key's line would leave the leg green.
    """
    from tortoise import session_source_prose_migration as m

    graph = _RecordingGraph([(_session_node(),)])
    dry = m.reconcile_session_source_prose(graph, dry_run=True)
    doc = " ".join((m.reconcile_session_source_prose.__doc__ or "").split())
    for key in dry:
        assert f"``{key}``" in doc, (
            f"`reconcile_session_source_prose` RETURNS {key!r} but its `Returns` "
            f"block never lists it — the report's contract omits a key the run "
            f"actually emits")

    # (3) the same report, WRITING: the documented `written` key carries the url
    # the write matched, so naming it is not a claim about a key that is always
    # empty.
    real = m.reconcile_session_source_prose(
        _RecordingGraph([(_session_node(),)]),
        read_transcript=_reader("User: friday rebuild plan"), dry_run=False)
    assert real["written"] == ["session:s-11070"]
    assert set(real) == set(dry), "dry and writing runs must return the same keys"


def test_a_BEFORE_reading_that_did_not_MEASURE_is_not_reported_as_a_clean_zero(
        monkeypatch, capsys):
    """#11536 review round 3, P2: `residue_before == 0` was three states in one.

    (1) FAILS on the shipped fold: it carried only ``len(residue)`` from the
        BEFORE leg, so a BEFORE read that never RAN (a breaker trip between the
        two reads — ``probe_fts`` records ``ran=False``/``degraded=True`` on that
        row and the fold discarded both) and a BEFORE read whose hits could not be
        CLASSIFIED (``unread``/``unresolved``) both reached the refusal as "it
        returned no PRE-FIX node BEFORE the repair either (a token that is not in
        the corpus)" — a measurement the run never made. The verdict was already
        fail-closed; the diagnostic sent the operator hunting for a typo that the
        tool had not looked for (or for a corpus the breaker never read).
    (2) REACHABLE: a degraded BEFORE read is the SAME circuit-breaker state the
        probe line already prints as ``⚠️ UNMEASURABLE``, and an unaddressable
        BEFORE hit is the shape the same run reports as ``unresolved``.
    (3) POLARITY THAT MUST NOT GO RED: a BEFORE read that DID run and matched
        nothing keeps the categorical wording — it is a fact there.
    """
    from tortoise import search_engine as se

    class _LexiconGraph(_RecordingGraph):
        """Answers the CONTROL read with a lexicon, so a token exists to probe."""

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    def _repair_wrote_one(monkeypatch):
        _export_a_record_dsn(monkeypatch)
        monkeypatch.setattr(_cli, "reconcile_session_source_prose",
                            lambda *a, **k: {
                                "candidates": ["session:s-post"], "repaired": [],
                                "empty_sessions": [], "no_record": [],
                                "no_reader": [], "no_session_id": [],
                                "unaddressable": [], "left_alone": [],
                                "written": ["session:s-post"], "notes": {},
                                "census_before": 1, "census_after": 0,
                                "dry_run": False})

    # (1)+(2) the BEFORE read is the degraded one; the AFTER read is healthy.
    first = {"done": False}

    def fake_degraded_before(graph, query, **kwargs):
        degraded = not first["done"] and query == "zebraquux"
        if query == "zebraquux":
            first["done"] = True
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({
                "leg": "fts", "ran": not degraded, "degraded": degraded,
                "reason": "breaker_open" if degraded else "empty_results"})
        return [("session:s-post", 2.0)] if query == "migration" else []

    monkeypatch.setattr(se, "run_fts_query", fake_degraded_before)
    _repair_wrote_one(monkeypatch)
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1, "a degraded BEFORE reading certified the leg"
    err = capsys.readouterr().err
    assert "did NOT RUN or was DEGRADED" in err, (
        "the refusal did not name the BEFORE read's own state: " + err)
    assert "reason='breaker_open'" in err
    assert "returned no PRE-FIX node BEFORE the repair" not in err, (
        "the refusal asserted a BEFORE measurement that never ran: " + err)
    assert "matched no pre-fix prose even BEFORE the writes" not in err, (
        "the refusal asserted the token matched nothing before the writes, from "
        "a read that did not run: " + err)

    # (1)+(2) the BEFORE read ran, but its hit could not be classified: the urls
    # it returned are the EMPTY STRING, i.e. `unresolved` — it established nothing.
    first["done"] = False

    def fake_unaddressable_before(graph, query, **kwargs):
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({"leg": "fts", "ran": True,
                                        "degraded": False,
                                        "reason": "empty_results"})
        if query == "zebraquux" and not first["done"]:
            first["done"] = True
            return [("", 1.0)]          # the BEFORE read's only hit, unaddressable
        if query == "migration":
            return [("session:s-post", 2.0)]
        return []

    monkeypatch.setattr(se, "run_fts_query", fake_unaddressable_before)
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1
    err = capsys.readouterr().err
    assert "could NOT be classified" in err, (
        "the refusal did not name the unclassified BEFORE hit: " + err)
    assert "carried no addressable url" in err
    assert "returned no PRE-FIX node BEFORE the repair" not in err, (
        "the refusal called an unclassified BEFORE hit a measured zero: " + err)

    # (3) a BEFORE read that ran and matched nothing IS a measured zero.
    def fake_clean_before(graph, query, **kwargs):
        if kwargs.get("leg_trace") is not None:
            kwargs["leg_trace"].append({"leg": "fts", "ran": True,
                                        "degraded": False,
                                        "reason": "empty_results"})
        return [("session:s-post", 2.0)] if query == "migration" else []

    monkeypatch.setattr(se, "run_fts_query", fake_clean_before)
    _install_cli(monkeypatch, _LexiconGraph([(_post_fix_node(),)]),
                 ["--repair", "--clear-without-record",
                  "--probe-phrase", "zebraquux"])
    capsys.readouterr()
    assert _cli.main() == 1
    err = capsys.readouterr().err
    assert "matched no pre-fix prose even BEFORE the writes" in err, (
        "a BEFORE reading that DID run and matched nothing lost its wording: "
        + err)
    assert "returned no PRE-FIX node BEFORE the repair either" in err

def test_the_report_SEPARATES_the_url_count_from_the_row_count(capsys):
    """#11536 review round 5: two units on one line, and they must not merge.

    ⛔ ``residue`` (and every other bucket) holds URLS while ``probe["hits"]``
    counts ROWS, so a print that renders both with one unit is wrong for at least
    one of them — and the twin case is where they differ (two rows, one url). It
    is pinned HERE because the repair-arm row cannot show it: its AFTER read
    returns no rows at all, so both counts are 0 and any unit reads as plausible.
    """
    folded = {
        "entity_type": "source", "probes": [
            {"phrase": "zebraquux", "hits": 2, "ran": True, "degraded": False,
             "residue": ["session:dup"], "residue_before": 2,
             "lexicon": [], "stale": [], "foreign": [], "unexplained": [],
             "unread": [], "unresolved": 0, "truncated": False}],
        "control": None, "certifies": False, "vetoed": "leaked",
        "leaked": ["zebraquux"], "stale": [], "unexplained": [],
        "unmeasurable": [], "truncated": [], "unclassified": [],
        "unresolved": 0,
    }
    _cli._report_fts(folded)
    out = capsys.readouterr().out
    assert "1 residue url(s) after, 2 hit(s)" in out, (
        "the line reports a URL count and a ROW count as one unit: " + out)


def test_a_FAILED_control_read_is_not_an_EMPTY_control_window(monkeypatch):
    """#11536 review round 5: an unreadable control is not an empty one.

    (1) FAILS on the shipped control: ``lexicon_control_token`` swallowed the read
        failure and returned ``None``, so a run whose control read RAISED reported
        "no usable control TOKEN was found within the 8-row window the control
        reads" — a statement about a window it never read, which is the same
        over-claim one order smaller than the two this leg already closed. The
        module distinguishes exactly this shape for its OTHER control arm
        (``source_index_is_empty`` returns ``None`` for "read failed").
    (2) REACHABLE: a breaker-open / timed-out control read on a graph that does
        hold a usable lexicon row.
    """
    from tortoise import session_source_prose_migration as m

    class _ControlReadRaises(_RecordingGraph):
        """A graph with a usable lexicon row whose CONTROL read fails."""

        def __init__(self):
            super().__init__([])

        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                raise RuntimeError("control read timed out")
            return super().query(cypher, params=params, timeout=timeout)

    _install_fts(monkeypatch, {"zebraquux": []})
    report = m.probe_fts(_ControlReadRaises(), ["zebraquux"], control=True)
    control = report["control"]
    assert control["mode"] == "unavailable" and control["ok"] is False
    assert "FAILED" in control["note"], (
        "a control read that RAISED was reported as an empty window: "
        + control["note"])
    assert "window the control reads" not in control["note"], (
        "the note claims to have read a window the run never read: "
        + control["note"])

    # …and the token-only verb still answers the old question, for callers that
    # do not need the distinction.
    assert m.lexicon_control_read(_ControlReadRaises())[1] is not None
    assert m.lexicon_control_token(_ControlReadRaises()) is None


# ── #11536 review round 9: four gaps the round-8 fixes left ──────────────────


def test_stem_only_EXCLUDES_every_bucket_the_classifier_can_emit():
    """The predicate must be STRUCTURALLY complete, not enumerated by hand.

    (1) FAILS the moment `classify_fts_hits` grows a bucket: `_STEM_ONLY_FORBIDDEN`
        plus `lexicon` is asserted to be EXACTLY the classifier's key set, so a
        seventh bucket cannot silently restore the over-claim "hits, but every
        one is a lexicon node". The defect recurred three times as a hand-kept
        list shortened one term at a time (round 3 `unresolved`, round 7
        `foreign`, round 8 `truncated`), and the round-8 comment CLAIMED the
        predicate was stated as an invariant while the code was still a list —
        so the claim and the shape are pinned separately here.
    (2) REACHABLE: `classify_fts_hits` is exported and is the single place that
        decides what a hit means, so its key set is the contract this predicate
        has to cover.
    (3) POLARITY THAT MUST NOT GO RED: the classifier still emits six buckets and
        `lexicon` is still the only benign one.
    """
    from tortoise import session_source_prose_migration as m

    # ⛔ CONSULT THE CLASSIFIER, DO NOT SPELL ITS KEYS (#11536 review round 10,
    # P2). This used to compare `_STEM_ONLY_FORBIDDEN` against a LITERAL set in
    # the test file — two constants, so it could never fail, while its own
    # docstring claimed it reds when `classify_fts_hits` grows a bucket. The
    # empty-url path returns the full bucket dict, which IS the contract.
    split = m.classify_fts_hits(_RecordingGraph([]), [], phrase="zebraquux")
    assert set(split) == set(m._STEM_ONLY_FORBIDDEN) | {"lexicon"}, (
        "`_STEM_ONLY_FORBIDDEN` no longer covers every non-lexicon bucket "
        "`classify_fts_hits` can emit — a hit in an unlisted bucket would be "
        "reported as a benign stem collision")
    assert "lexicon" not in m._STEM_ONLY_FORBIDDEN


def test_a_TRUNCATED_BEFORE_read_cannot_certify(monkeypatch, capsys):
    """A BEFORE window that FILLED is a floor, not a measurement of absence.

    (1) FAILS on the shipped fold (#11536 review round 9, P1): `_merge_probe`
        carried only hits/ran/degraded/reason/unread/unresolved from the BEFORE
        row, dropping `truncated`. A BEFORE read that returned a FULL window of
        lexicon hits therefore became `clean-zero`, the green asserted "the
        BEFORE reading this run took returned no pre-fix node either", and the
        run CERTIFIED — measured end-to-end: 50-hit BEFORE, 0 after, answering
        control → exit 0, "Repair complete." Residue could have been sitting
        past the window, so both the claim and the verdict were unearned.
    (2) REACHABLE: `_FTS_PROBE_LIMIT` is 50 and any common token fills it.
    (3) POLARITY THAT MUST NOT GO RED: a genuine discriminating BEFORE (a real
        pre-fix hit, then 0) still certifies.
    """
    from tortoise import session_source_prose_migration as m

    class _ControlGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    filling = [("session:s-post", 2.0)] * m._FTS_PROBE_LIMIT
    _install_fts(monkeypatch, {"plan": filling,
                               "migration": [("session:s-post", 2.0)]})
    before = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), ["plan"],
                         control=True)
    assert before["truncated"] == ["plan"]

    _install_fts(monkeypatch, {"plan": [], "migration": [("session:s-post", 2.0)]})
    after = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), ["plan"],
                        control=True)
    assert after["truncated"] == []

    folded = _cli._merge_probe(before, after)
    assert folded["probes"][0]["before"]["truncated"] is True, (
        "the BEFORE row's `truncated` was dropped in the fold")
    assert folded["certifies"] is False and folded["vetoed"] == "before-truncated", (
        "a BEFORE read whose window FILLED certified: its residue count is a "
        "floor, so 'no pre-fix node either' was never measured")
    capsys.readouterr()
    _cli._report_fts(folded)
    err = capsys.readouterr().err
    assert "window FILLED" in err, "the refusal did not name the truncated BEFORE leg" + err
    # ⛔ AND IT MUST NOT ASSERT CAUSES THIS RUN DID NOT HAVE (#11536 review round
    # 10, P1). With no `before-truncated` branch the veto fell through to the
    # `uncorroborated` arm, whose message says the control did not corroborate
    # (it did — printed on stdout one line above) and tells the operator to run
    # `--repair --probe-phrase`, the command they had just run. The old
    # assertion here checked only the phrase above, so both false clauses passed.
    assert "cannot be corroborated" not in err, (
        "the refusal told the operator the zero was never corroborated, but the "
        "control answered: " + err)
    assert "did not corroborate" not in err, err
    assert "RARER token" in err, (
        "the refusal did not name the remedy that applies (a rarer token): " + err)

    # (3) a real discriminating BEFORE still certifies.
    hit_before = {"ran": True, "truncated": False,
                  "probes": [{"phrase": "plan", "hits": 1,
                              "urls": ["session:s-post"],
                              "residue": ["session:s-post"]}]}
    assert _cli._merge_probe(hit_before, after)["certifies"] is True


def test_the_before_truncated_refusal_names_the_TRUNCATED_probe(capsys):
    """#11536 review round 11, P2: it read `probes[0]` and asserted the control.

    (1) FAILS on the round-10 tree on two counts. The `before-truncated` veto
        fires on ANY truncated BEFORE row, but the refusal printed
        `probes[0]`'s hit count as "the read's limit" — MEASURED with `alpha`
        (3 hits, fine) beside `beta` (50 = the limit), it printed "(3 hit(s) is
        the read's limit)", a count that is neither the limit nor the probe the
        veto was about. And because this arm sits ABOVE `control_ok and not
        wrote` in the ladder, an `unavailable` control still got "the control
        answered", which the run had not established.
    (2) REACHABLE: a multi-probe run is the documented shape, and a control read
        can fail on the same circuit breaker the leg already separates.
    (3) POLARITY THAT MUST NOT GO RED: a single truncated probe with a live
        control still gets the "control answered / RARER token" wording pinned by
        `test_a_FILLED_before_window_does_not_certify_as_a_BEFORE_zero`.
    """
    def _row(phrase, before_hits, truncated):
        return {"phrase": phrase, "hits": 0, "urls": [], "unresolved": 0,
                "truncated": False, "ran": True, "degraded": False,
                "reason": None, "residue": [], "lexicon": [], "stale": [],
                "foreign": [], "unexplained": [], "unread": [],
                "before": {"hits": before_hits, "truncated": truncated,
                           "ran": True, "degraded": False}}

    fts = {"ran": True, "entity_type": "source", "limit": 50,
           "probes": [_row("alpha", 3, False), _row("beta", 50, True)],
           "leaked": [], "stale": [], "stem_only": [], "unmeasurable": [],
           "truncated": [], "unresolved": 0, "certifies": False,
           "vetoed": "before-truncated",
           "control": {"mode": "unavailable", "ok": False, "hits": None,
                       "note": "the control's lexicon read FAILED"}}
    _cli._report_fts(fts)
    err = capsys.readouterr().err
    assert "'beta'" in err and "50 hit(s)" in err, (
        "the refusal did not name the TRUNCATED probe and its own count: " + err)
    assert "'alpha'" not in err, (
        "the refusal named a probe whose window did not fill: " + err)
    assert "the control answered" not in err, (
        "the refusal asserted the control answered while it was UNAVAILABLE: "
        + err)
    assert "did NOT corroborate it either" in err


def test_an_EMPTY_probe_set_cannot_certify(monkeypatch, capsys):
    """A ✓ over zero probes is earned by the control alone.

    (1) FAILS on the shipped fold (#11536 review round 9, P2): with no probes the
        ladder reached arm (b) (`control_ok and not wrote`) and certified, and
        `_report_fts` printed a ✓ with a dangling "— and ; its zero therefore …"
        clause. Unreachable from the CLI today (`if probes:` guards both arms),
        so this is a latent gate gap — but the fold is exported and nothing in
        its contract said a non-empty set was a precondition.
    (2) REACHABLE: a direct caller of the exported `_merge_probe`, or any future
        arm that forwards an empty list.
    (3) POLARITY THAT MUST NOT GO RED: one real probe still certifies.
    """
    from tortoise import session_source_prose_migration as m

    class _ControlGraph(_RecordingGraph):
        def query(self, cypher, params=None, timeout=None):
            if "STARTS WITH $prefix" in cypher:
                return _Result([("migration, plan, friday",)])
            return super().query(cypher, params=params, timeout=timeout)

    _install_fts(monkeypatch, {"migration": [("session:s-post", 2.0)]})
    empty = m.probe_fts(_ControlGraph([(_post_fix_node(),)]), [], control=True)
    assert empty["probes"] == [] and empty["control"]["ok"] is True
    folded = _cli._merge_probe(None, empty)
    assert folded["certifies"] is False and folded["vetoed"] == "no-probes", (
        "a ✓ was earned over an EMPTY probe set, on the control alone")
    capsys.readouterr()
    _cli._report_fts(folded)
    err = capsys.readouterr().err
    assert "NO probe phrase" in err, err
    assert "no-probes" not in capsys.readouterr().out


def test_probe_fts_REFUSES_a_multi_token_phrase(monkeypatch):
    """The OR-join hazard is guarded at the library verb, not only at ARGV.

    (1) FAILS on the shipped verb (#11536 review round 9, P2): `probe_fts` is
        exported and validated nothing, so a caller passing "the weekly rebuild"
        got `build_or_query`'s OR-join, a hit on any ordinary word, and a FALSE
        `leaked` — whose likely remedy is clearing the post-fix lexicon, i.e.
        destroying the `#3518` descriptor.
    (2) REACHABLE: `probe_fts` is in `__all__`; only the CLI ran the guard.
    (3) POLARITY THAT MUST NOT GO RED: a single token still measures normally.
    """
    from tortoise import session_source_prose_migration as m

    _install_fts(monkeypatch, {"plan": [("session:s-post", 2.0)],
                               "migration": [("session:s-post", 2.0)]})
    graph = _RecordingGraph([(_post_fix_node(),)])
    with pytest.raises(ValueError, match="cannot measure the Source FTS index"):
        m.probe_fts(graph, ["the weekly rebuild"])
    # whitespace-only is not one token either
    with pytest.raises(ValueError):
        m.probe_fts(graph, ["   "])
    got = m.probe_fts(graph, ["plan"])
    assert got["probes"][0]["phrase"] == "plan"
