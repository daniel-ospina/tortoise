"""#13320 — an ARMED store must actually REMOVE the in-graph Point vector index.

THE DEFECT (issue #13320; reproduced on production 2026-11-17 and again here on
``falkordb-server:v4.20.6``, graph module 42006). On a deployment whose
replacement is armed — a record DSN **and** ``TORTOISE_RECORD_VECTOR_LEG=1``, so
``projection.vector_index_retired()`` is ``True`` — constructing a projection for
a graph that HOLDS the in-graph ``Point.embedding`` HNSW index did not drop it:

* the catalogue was read and the index WAS seen (so the gate is not the defect),
* both statements the retirement issued — ``DROP INDEX ON :Point(embedding)`` and
  ``DROP INDEX FOR (p:Point) ON (p.embedding)`` — were rejected by the engine
  (``Unable to drop index on :Point(embedding): no such index.``), because a
  VECTOR index is not reachable through the generic drop, and
* each rejection was logged at DEBUG ONLY, so on a box logging at INFO the whole
  sweep was silent: ``before: present=True``, ``after: present=True``, no line.

The statement that DOES drop it — ``DROP VECTOR INDEX FOR (p:Point) ON
(p.embedding)`` — was already measured and written down in
``tortoise/graph_key_restore.py`` (``VectorIndexDefinition.drop_cypher``) for the
RESTORE path, with the very same error text quoted. The retirement path derived
its own list instead. Both now render it from ONE place
(``index_catalogue.vector_index_drop_statement``), so the next site cannot
re-derive the guess.

WHAT IS PINNED HERE

1. ``test_an_armed_setup_REMOVES_a_real_in_graph_vector_index`` — the LIVE
   falsifier the issue asks for: a real VECTOR index on a real engine, armed, the
   setup path invoked ⇒ the index is GONE from ``CALL db.indexes()``, and a line
   names what removed it.
   ⛔ RED-FIRST, stated exactly: on the parent commit (``54682177bcff``) this file
   does not even IMPORT — ``vector_index_drop_statement`` does not exist yet — so
   that is the first red. With the import stubbed to the shipped generic forms,
   the LIVE pin reds on the survived-index assertion (verified: the armed sweep
   left ``{'embedding': ['VECTOR']}`` in the catalogue) and the two DB-free pins
   red too. The behavioural red is the one that matters; the import red is what a
   reader would see first.
2. ``test_the_retirement_drops_with_the_VECTOR_form`` — DB-free, so CI without a
   live engine still reds if the form regresses to the generic one. The fake
   engine answers the generic DROPs the way the real one does (``no such
   index``), so a fake that merely says "something dropped" cannot pass.
3. ``test_a_vector_index_that_SURVIVES_is_not_silent`` — the second half of the
   defect: an index that survives the sweep must produce a WARNING naming it, and
   must NOT produce the "dropped" INFO. Silence is how #13320 shipped.
4. ``test_an_engine_that_ACCEPTS_the_drop_and_keeps_the_index_is_a_false_PASS``,
   ``test_a_RESHAPED_post_drop_catalogue_does_not_claim_a_removal`` and
   ``test_an_UNREADABLE_catalogue_on_an_armed_store_is_loud`` — the three ways the
   REPORT (not the sweep) can lie, each added because the #13330 review showed the
   first draft got them wrong: "dropped, confirmed absent" read off a half that
   also covers an unparseable catalogue; an accepted-then-surviving index printed
   as ``Statements tried: none``; and an armed store whose catalogue read fails
   was DEBUG-only silence — the issue's own complaint, one arm over.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
import redis

from tests import _live_utils
from tortoise.embeddings import EMBEDDING_DIM
from tortoise.index_catalogue import vector_index_drop_statement
from tortoise.projection import (
    FalkorProjection,
    _reset_vector_gap_warnings,
    vector_index_retired,
)

#: The arming surface (a record DSN) and the arming DECISION — BOTH, or
#: ``vector_index_retired()`` is False and this file would be testing the
#: unarmed posture (see that function's docstring).
RECORD_DSN_ENV = "TORTOISE_RECORD_DSN"
ARMING_ENV = "TORTOISE_RECORD_VECTOR_LEG"
_RECORD_DSN = "postgresql://localhost/record"

_SUPPORTED_SCHEMES = ("docker", "redis", "rediss")


@pytest.fixture(autouse=True)
def _fresh_warning_latches():
    """Clear the process-global latches around EVERY test here.

    ``_VECTOR_INDEX_DROP_FAILED_WARNED`` (#13320) and the #4997/#11087 registers
    are keyed per ``(endpoint, graph)`` and never expire, so without this a second
    in-process run of the survivor pin would find its bucket already latched and
    red spuriously — a pin that only passes on the first run is not a pin. This is
    the same hook ``tests/test_4997_vector_index_parity.py`` uses (#13330 review).
    """
    _reset_vector_gap_warnings()
    yield
    _reset_vector_gap_warnings()


# ── the live lane ───────────────────────────────────────────────────────────

def _live_target():
    """``(host, port, username, password, ssl)`` for the live FalkorDB, or None.

    Read at CALL time: the conftest recomposes ``TORTOISE_DB_URI`` per test (a
    unique graph name), so the connection must follow the env it is given.
    """
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    parsed = urlparse(uri)
    if parsed.scheme not in _SUPPORTED_SCHEMES or not parsed.hostname:
        return None
    from tortoise.config import parse_uri_userinfo

    username, password = parse_uri_userinfo(uri)
    return (
        parsed.hostname,
        parsed.port or _live_utils.docker_port(),
        username,
        password,
        parsed.scheme == "rediss",
    )


_LIVE_ONLY = pytest.mark.skipif(
    _live_target() is None, reason=_live_utils.LIVE_URI_SKIP_REASON)


def _raw_client():
    target = _live_target()
    assert target is not None, "the live lane was not skipped but has no URI"
    host, port, username, password, ssl = target
    return redis.Redis(
        host=host,
        port=port,
        username=username,
        password=password,
        ssl=ssl,
        decode_responses=True,
        socket_timeout=30.0,
        socket_connect_timeout=10.0,
    )


def _rows(client, graph: str, cypher: str):
    """The result rows of a ``GRAPH.QUERY`` reply (``[header, rows, stats]``)."""
    out = client.execute_command("GRAPH.QUERY", graph, cypher)
    return out[1] if isinstance(out, (list, tuple)) and len(out) > 1 else []


def _type_tokens(cell) -> set[str]:
    """The index-TYPE tokens of a ``db.indexes()`` row's ``types`` cell.

    ⛔ The raw client hands this cell over as a STRING
    (``"{embedding: [VECTOR]}"``), not a dict, so it is TOKENISED — never
    substring-tested — the rule ``tortoise/index_catalogue.py`` states for the
    same column's property names.
    """
    if isinstance(cell, dict):
        return {
            str(token).upper()
            for types in cell.values()
            for token in (types or ())
        }
    return {token.upper() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", str(cell))}


def _vector_index_present(client, graph: str) -> bool:
    """Is there a VECTOR index on ``Point.embedding`` in this graph's catalogue?"""
    for row in _rows(client, graph, "CALL db.indexes()"):
        if len(row) < 3 or str(row[0]) != "Point":
            continue
        if "VECTOR" in _type_tokens(row[2]):
            return True
    return False


def _seed_vector_index(client, graph: str) -> None:
    """Create the in-graph index the way the store does: procedure, then Cypher.

    Mirrors ``_create_vector_index_api``: the procedure is not registered on the
    pinned server image, so the Cypher-native form is the live path — the same
    the products's own creation code takes there.
    """
    try:
        _rows(client, graph,
              "CALL db.idx.vector.createNodeIndex('Point', 'embedding', "
              f"{EMBEDDING_DIM}, 'HNSW')")
        return
    except Exception:
        # The procedure is NOT registered on the pinned server image — the
        # Cypher-native form below is the measured path there, and the same one
        # the product's own creation code takes.
        pass
    _rows(client, graph,
          "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
          f"OPTIONS {{dimension: {EMBEDDING_DIM}, similarityFunction: 'cosine'}}")


@_LIVE_ONLY
def test_an_armed_setup_REMOVES_a_real_in_graph_vector_index(monkeypatch, caplog):
    """THE FALSIFIER (#13320 acceptance): armed + index present ⇒ index absent.

    Written to FAIL on the shipped code, and it does: the sweep issued two DROPs
    the engine rejects and reported nothing above DEBUG, so the catalogue still
    answered ``Point … {'embedding': ['VECTOR']}`` after the projection was
    constructed — the exact post-state the production transcript recorded.

    Falsifiable both ways: dropping the retirement gate's arming check would make
    an UNARMED store lose its index, which the sibling pins in
    ``tests/test_4997_vector_index_parity.py`` own; a sweep that issues the
    generic DROP forms leaves this one red on the ``VECTOR still present``
    assertion.
    """
    client = _raw_client()
    graph = f"test_13320_{uuid.uuid4().hex[:10]}"
    proj = None
    try:
        # The fixture's PRECONDITION, asserted rather than assumed: a seed that
        # silently created no index would make this pin vacuous.
        _rows(client, graph, "CREATE (:Point {id:'p1', pointKind:'entity'})")
        _seed_vector_index(client, graph)
        assert _vector_index_present(client, graph), (
            "the fixture did not seed a VECTOR index — this pin would be vacuous")

        monkeypatch.setenv(RECORD_DSN_ENV, _RECORD_DSN)
        monkeypatch.setenv(ARMING_ENV, "1")
        assert vector_index_retired() is True, (
            "the store is not armed, so this pin is not testing the retirement")

        with caplog.at_level(logging.INFO):
            # The production path verbatim: constructing the projection runs
            # `_ensure_indexes()` (`Projection.__init__`), which is where the
            # retirement lives.
            proj = FalkorProjection.from_uri(
                os.environ["TORTOISE_DB_URI"], graph_name=graph)

        assert not _vector_index_present(client, graph), (
            "#13320: an ARMED store kept its in-graph Point.embedding VECTOR "
            "index — the retirement issued a statement the engine rejects and "
            "logged it only at DEBUG, so the deployment pays for the index AND "
            "the pgvector copy while reporting the switch as on. The DROP that "
            "works is "
            f"{vector_index_drop_statement('Point', 'embedding')!r}; the "
            "catalogue after the sweep still reports: "
            f"{_rows(client, graph, 'CALL db.indexes()')}")

        dropped = [
            r.getMessage() for r in caplog.records
            if r.levelno >= logging.INFO
            and "dropped the in-graph" in r.getMessage()
        ]
        assert dropped, (
            "the retirement removed the index without a line saying so — the "
            "operator has no artifact for what the sweep did "
            f"(INFO lines seen: {[r.getMessage()[:90] for r in caplog.records]})")
        assert vector_index_drop_statement("Point", "embedding") in dropped[0], (
            "the line that names the retirement must name the statement that "
            f"did it; got {dropped[0]!r}")
    finally:
        if proj is not None:
            try:  # noqa: SIM105
                proj.close()
            except Exception:
                pass
        for cypher in ("MATCH (n) DETACH DELETE n", None):
            try:
                if cypher is None:
                    client.execute_command("GRAPH.DELETE", graph)
                else:
                    _rows(client, graph, cypher)
            except Exception:
                pass


# ── DB-free pins: the FORM, and the silence ─────────────────────────────────

def _catalogue_row(label: str, prop: str, kind: str):
    """A 9-column ``CALL db.indexes()`` row, the shape the inventory walks."""
    return (label, [prop], {prop: [kind]}, 0, 0, 0, "NODE", "OPERATIONAL", "")


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _EngineLikeGraph:
    """A fake engine that answers the DROP forms the REAL engine answers.

    ``mode``:

    * ``"vector"`` — only ``DROP VECTOR INDEX`` drops the index (the measured
      engine).
    * ``"none"`` — nothing drops it; every DROP is REFUSED (the survivor lane).
    * ``"accept-noop"`` — the engine ACCEPTS ``DROP VECTOR INDEX`` and keeps the
      index: the false PASS the report must not call a removal.
    * ``"vector-reshaped"`` — the drop works, and the POST-drop catalogue comes
      back in a shape this store cannot parse (the engine changed its row shape
      between two reads). The removal is then NOT measurable.
    * ``"unreadable"`` — ``CALL db.indexes()`` raises on every read: the armed
      sweep cannot even tell whether an index is there.

    ⛔ The generic forms are REJECTED with the engine's own message rather than
    accepted. A fake that accepts any statement mentioning ``embedding`` is how
    #13320 shipped: the pin asserted "a DROP was issued", which was true and
    useless.
    """

    def __init__(self, *, drop_accepts: str = "vector"):
        self.calls: list[str] = []
        self.drop_accepts = drop_accepts
        self._rows = [_catalogue_row("Point", "embedding", "VECTOR")]

    def _drop_row(self):
        self._rows = [
            r for r in self._rows
            if not (str(r[0]) == "Point" and "VECTOR" in str(r[2]).upper())
        ]

    def query(self, cypher, params=None, timeout=None):
        self.calls.append(cypher)
        low = cypher.lower()
        if "db.indexes()" in low:
            if self.drop_accepts == "unreadable":
                raise RuntimeError("CALL db.indexes() unavailable")
            return _Result(list(self._rows))
        if low.startswith("drop"):
            if "drop vector index" not in low or self.drop_accepts in {
                    "none", "unreadable"}:
                raise RuntimeError(
                    "Unable to drop index on :Point(embedding): no such index.")
            if self.drop_accepts == "accept-noop":
                return _Result([])          # accepted — and the index stays
            if self.drop_accepts == "vector-reshaped":
                # The row survives in a shape this store cannot parse: dict-less
                # `types` cell, i.e. "returned but not interpretable".
                self._rows = [("Point", ["embedding"], "not-a-dict", 0, 0, 0,
                               "NODE", "OPERATIONAL", "")]
                return _Result([])
            self._drop_row()
            return _Result([])
        return _Result([])

    @property
    def drops(self) -> list[str]:
        return [c for c in self.calls if c.lower().startswith("drop")]


def _bare(graph_obj, *, graph: str = "test_13320_bare") -> FalkorProjection:
    """A projection over a fake engine — no connection, no health probe.

    ``.db`` carries a PRODUCTION-shaped pool so ``_stable_endpoint()`` resolves a
    store identity and the #11087/#13320 latches key on something stable.
    """
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = False
    proj._graph_name = graph
    proj._skip_guard = True
    proj._host = "test-host"
    proj.db = SimpleNamespace(connection=SimpleNamespace(
        connection_pool=SimpleNamespace(
            connection_kwargs={"host": "test-host", "port": 6379})))
    proj._falkordb_version = (4, 20, 6)
    proj._vector_index_api = None
    proj._vector_indexed_labels = None
    proj._vector_indexed_labels_for_reads = None
    proj._vector_index_served_labels = None
    proj._vector_index_props = None
    proj._index_catalogue_rows = None
    proj.g = graph_obj
    return proj


def _arm(monkeypatch):
    monkeypatch.setenv(RECORD_DSN_ENV, _RECORD_DSN)
    monkeypatch.setenv(ARMING_ENV, "1")


def test_the_retirement_drops_with_the_VECTOR_form(monkeypatch):
    """The DROP the sweep issues is the form the engine actually honours (#13320).

    DB-free, because the form is where the defect was and CI must catch its
    regression without a live engine. The fake rejects the generic forms exactly
    as graph module 42006 does, so an implementation that issues them cannot
    satisfy this pin (its index survives, and the assertion below reports it).
    """
    _arm(monkeypatch)
    assert vector_index_retired() is True

    graph = _EngineLikeGraph(drop_accepts="vector")
    proj = _bare(graph)
    proj._ensure_indexes()

    expected = vector_index_drop_statement("Point", "embedding")
    assert expected in graph.drops, (
        f"the retirement never issued {expected!r} — it issued {graph.drops!r}")
    assert not any("VECTOR" in str(r[2]).upper() for r in graph._rows), (
        "the store still reports the VECTOR index after the sweep")
    assert proj._vector_index_api is None
    assert proj._vector_indexed_labels == set(), (
        "the post-drop measurement still reports the retired index as present")


def test_a_vector_index_that_SURVIVES_is_not_silent(monkeypatch, caplog):
    """Second half of #13320: an index that survives must SAY SO (#13320).

    The shipped sweep failed both DROP statements and logged each at DEBUG, so a
    production box at INFO recorded nothing at all — the "silently switched
    backend" shape this repo has a rule about. A survivor now produces a WARNING
    that names the index, and must NOT produce the "dropped" INFO: a line that
    claims a removal that did not happen is worse than the silence.
    """
    _arm(monkeypatch)
    graph = _EngineLikeGraph(drop_accepts="none")
    proj = _bare(graph)

    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()

    assert graph.drops, (
        "no DROP was even attempted — the gate, not the drop, is being tested")

    warnings = [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "#13320" in r.getMessage()
    ]
    assert warnings, (
        "a VECTOR index survived an ARMED retirement sweep with no line above "
        f"DEBUG — the box believes the switch is on and nothing changed "
        f"(records: {[(r.levelname, r.getMessage()[:70]) for r in caplog.records]})")
    joined = " ".join(warnings)
    assert "Point.embedding" in joined, (
        f"the survivor warning must name the index it left in place: {joined!r}")
    assert "no such index" in joined, (
        f"the survivor warning must carry the engine's own refusal: {joined!r}")
    assert not any("dropped the in-graph" in r.getMessage() for r in caplog.records), (
        "the sweep claimed a drop that did not happen — that is the false PASS "
        "#13320 was filed on")


def _retirement_warnings(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "#13320" in r.getMessage()
    ]


def test_an_engine_that_ACCEPTS_the_drop_and_keeps_the_index_is_a_false_PASS(
        monkeypatch, caplog):
    """A `g.query()` that raised nothing is NOT evidence the index went (#13320).

    This is the false PASS the report exists to prevent, and the first draft of
    this fix got its wording wrong twice (#13330 review P2 x2): it called the
    store a SURVIVOR by measurement it had not taken, and it printed
    ``Statements tried: none`` because only REFUSED candidates were recorded —
    which reads as "nothing was attempted" at exactly the moment an accepted
    statement is the whole story.
    """
    _arm(monkeypatch)
    graph = _EngineLikeGraph(drop_accepts="accept-noop")
    proj = _bare(graph)

    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()

    assert not any("dropped the in-graph" in r.getMessage() for r in caplog.records), (
        "the engine ACCEPTED the statement and kept the index, and the sweep "
        "reported a drop anyway")
    warnings = _retirement_warnings(caplog)
    assert warnings, "a false PASS was reported silently"
    line = warnings[0]
    assert "SURVIVED" in line and "FALSE PASS" in line, (
        f"the line must say the index SURVIVED a measurement and why: {line!r}")
    assert "none refused" in line, (
        "the line must distinguish 'nothing was refused' from 'nothing was "
        f"attempted'; got {line!r}")


def test_a_RESHAPED_post_drop_catalogue_does_not_claim_a_removal(monkeypatch, caplog):
    """An unparseable catalogue is NOT "measured, nothing indexed" (#13320).

    ``_vector_indexed_labels`` documents a ``set()`` that also covers a response
    which came back in a shape nothing could parse. Reading the CONFIRMED-ABSENT
    verdict off that half is how a report prints "confirmed absent from the
    post-drop catalogue" over an index that is still there — so this pin drives
    exactly that shape and requires the unconfirmed arm instead. The behavioural
    half (``_vector_indexed_labels_for_reads``, #13108) is what the verdict reads.
    """
    _arm(monkeypatch)
    graph = _EngineLikeGraph(drop_accepts="vector-reshaped")
    proj = _bare(graph)

    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()

    assert "confirmed absent from the post-drop catalogue" not in caplog.text, (
        "the sweep claimed a CONFIRMED removal from a catalogue it could not parse")
    warnings = _retirement_warnings(caplog)
    assert warnings, "an unmeasurable removal was reported silently"
    assert "NOT CONFIRMED" in warnings[0], (
        f"the line must say the removal is unconfirmed: {warnings[0]!r}")


def test_an_UNREADABLE_catalogue_on_an_armed_store_is_loud(monkeypatch, caplog):
    """The fourth state: armed, and the catalogue cannot be read at all (#13320).

    The fail-SAFE half (never drop blind) is right and unchanged. The SILENT half
    was not: the read failure was ``_log.debug``, so on an armed store an
    operator at INFO could not tell "nothing to drop" from "cannot even look" —
    the issue's own complaint, one arm over. Latched, because this method runs on
    the hosted per-request path and the store's state is permanent.
    """
    _arm(monkeypatch)
    graph = _EngineLikeGraph(drop_accepts="unreadable")
    proj = _bare(graph)

    with caplog.at_level(logging.INFO):
        proj._ensure_indexes()

    assert not [d for d in graph.drops if "embedding" in d.lower()], (
        "an unreadable catalogue must never authorise a DROP of the vector "
        f"index; the sweep issued {graph.drops!r} (the `is_operator` purges the "
        "full sweep always issues are unrelated and are not what this pins)")
    warnings = _retirement_warnings(caplog)
    assert warnings, "an armed store whose catalogue could not be read was silent"
    assert "could not read the index catalogue" in warnings[0], warnings[0]

    # …and it is LATCHED per store: a second sweep in this process says it once.
    with caplog.at_level(logging.INFO):
        _bare(graph)._ensure_indexes()
    assert len(_retirement_warnings(caplog)) == 1, (
        "the unable-to-retire line is not latched — on the hosted PER-REQUEST "
        "path that is a per-request warning for the life of the deployment")


# ── #13330 rebase lane: the three findings the peer fix left open ────────────
#
# The pins above came from the first review of `898c5dde`; these three answer a
# second review at `b6bfde2ad7`, and each goes RED on the code that review read.


class _PostDropReadFailsOnce(_EngineLikeGraph):
    """Every DROP refused, and the FIRST post-drop catalogue read fails.

    Sequences the two states one latch must tell apart: a sweep whose outcome
    could not be MEASURED, then the next sweep's CONFIRMED survivor on the same
    store.

    ⛔ Armed by the RETIREMENT DROP, not by a call count: a setup issues several
    `CALL db.indexes()` reads before the sweep (`_schema_is_current` probes the
    catalogue too), so counting reads fails the GATE read instead — a different
    lane that bails before any DROP and proves nothing.
    """

    def __init__(self):
        super().__init__(drop_accepts="none")
        self._drop_seen = False
        self._failed = False

    def query(self, cypher, params=None, timeout=None):
        low = cypher.lower()
        if low.startswith("drop") and "embedding" in low:
            self._drop_seen = True
        if "db.indexes()" in low and self._drop_seen and not self._failed:
            self._failed = True
            self.calls.append(cypher)
            raise RuntimeError("catalogue read timed out")
        return super().query(cypher, params=params, timeout=timeout)


class _DropAppliesThenRaises(_EngineLikeGraph):
    """The server EXECUTES the vector DROP and the connection then fails.

    A transport failure after the DDL ran: the statement raises, so
    `_dropped_with` stays unset — the case where "nothing returned" is not
    "nothing happened".
    """

    def __init__(self):
        super().__init__(drop_accepts="none")

    def query(self, cypher, params=None, timeout=None):
        low = cypher.lower()
        if low.startswith("drop") and "drop vector index" in low:
            self.calls.append(cypher)
            self._drop_row()  # the engine DID drop it
            raise RuntimeError("connection reset by peer after the DDL ran")
        return super().query(cypher, params=params, timeout=timeout)


class _MixedParsePostDrop(_EngineLikeGraph):
    """The drop works; the post-drop catalogue holds one parseable row and one
    the store cannot interpret.

    The first cut counted `parsed_rows` only, so `parsed_rows >= 1` put this in
    the MEASURED lane even though the row that could have been the `Point` index
    was the unreadable one — a false PASS in the one place this change exists to
    remove.
    """

    def __init__(self):
        super().__init__(drop_accepts="vector")

    def _drop_row(self):
        self._rows = [
            _catalogue_row("Object", "embedding", "VECTOR"),
            ("Point", ["embedding"], "a reshaped cell", 0, 0, 0, "NODE",
             "OPERATIONAL", ""),
        ]


def _retirement_warnings(caplog):
    return [
        r.getMessage() for r in caplog.records
        if r.levelno >= logging.WARNING and "#13320" in r.getMessage()
    ]


def test_an_UNCONFIRMED_sweep_does_not_swallow_the_CONFIRMED_survivor(
        monkeypatch, caplog):
    """A NOT-MEASURED sweep must not consume the latch for a MEASURED one.

    The latch was a single key per store, added before the outcome was known, so
    a sweep that could not read the catalogue silenced the CONFIRMED survivor
    that followed it — on the same store, for the life of the process. That is
    the "a survivor is never silent" half of this work failing on a two-sweep
    sequence (review P1). Distinct KINDS, each reported once, is the fix.
    """
    _arm(monkeypatch)
    graph = _PostDropReadFailsOnce()
    proj = _bare(graph)

    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()
        unconfirmed = _retirement_warnings(caplog)
        caplog.clear()
        proj._ensure_indexes()
        confirmed = _retirement_warnings(caplog)

    assert unconfirmed, (
        "a sweep whose post-drop catalogue read FAILED said nothing — the "
        "operator cannot tell it apart from a clean store")
    assert "NOT CONFIRMED removed" in " ".join(unconfirmed), (
        f"the not-measured lane must say so: {unconfirmed!r}")
    assert "SURVIVED" not in " ".join(unconfirmed), (
        "the line claims the index SURVIVED while also saying nothing measured "
        f"it: {unconfirmed!r}")

    assert confirmed, (
        "the CONFIRMED survivor that followed an UNCONFIRMED sweep was silent — "
        "the unmeasured sweep spent the latch, so the store that really does keep "
        f"its index is never reported (records: "
        f"{[(r.levelname, r.getMessage()[:60]) for r in caplog.records]})")
    assert "SURVIVED" in " ".join(confirmed), (
        f"the measured survivor line must say SURVIVED: {confirmed!r}")


def test_a_foreign_Point_vector_index_neither_arms_the_sweep_nor_is_reported(
        monkeypatch, caplog):
    """The retirement targets `Point.embedding`, so the gate must ask for it.

    A label-grained gate armed on ANY `Point` VECTOR index: a store whose vector
    index sits on a different property got three refused DDLs on every setup —
    the #4465 repeated-bootstrap defect — and then a warning naming a
    `Point.embedding` index that never existed.
    """
    _arm(monkeypatch)
    graph = _EngineLikeGraph(drop_accepts="vector")
    graph._rows = [_catalogue_row("Point", "some_other_prop", "VECTOR")]
    proj = _bare(graph)

    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()

    # ⛔ Scoped to the retirement's candidates: the schema sync issues its own
    # DROP/`CREATE` for `is_operator`, so `graph.drops` is not the sweep's alone.
    retirement_drops = [d for d in graph.drops if "embedding" in d.lower()]
    assert retirement_drops == [], (
        "the gate armed on a VECTOR index over a DIFFERENT property and issued "
        f"{retirement_drops!r} — refused DDL on the setup path of a healthy store")
    assert not _retirement_warnings(caplog), (
        "a store with no `Point.embedding` index was reported as a survivor: "
        f"{_retirement_warnings(caplog)}")


def test_a_DROP_that_RAISES_after_applying_is_MEASURED_not_assumed(
        monkeypatch, caplog):
    """A DROP that raised may still have run — so its rows are not reusable.

    `_dropped_with` is set only by a statement that RETURNED. Reading "no
    statement returned" as "nothing wrote" handed the inventory the PRE-drop
    rows (#5434's stale-row rule), so a real removal was reported as `every
    candidate DROP was refused`.
    """
    _arm(monkeypatch)
    graph = _DropAppliesThenRaises()
    proj = _bare(graph)

    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()

    assert proj._vector_index_props == {}, (
        "the sweep published a catalogue read from BEFORE a DROP that may have "
        f"applied — the measurement is stale: {proj._vector_index_props!r}")
    assert not _retirement_warnings(caplog), (
        "the index WAS removed, but the sweep reported a survivor on stale "
        f"rows: {_retirement_warnings(caplog)}")


def test_a_row_that_did_not_parse_withholds_the_confirmed_absence(
        monkeypatch, caplog):
    """`parsed_rows >= 1` is not enough to claim the index is gone.

    With one parseable row beside one the store cannot interpret, the old lane
    said "confirmed absent from the post-drop catalogue" — over a catalogue whose
    UNREADABLE row may be the `Point` index. Fail-closed: UNCONFIRMED.
    """
    _arm(monkeypatch)
    graph = _MixedParsePostDrop()
    proj = _bare(graph)

    with caplog.at_level(logging.DEBUG):
        proj._ensure_indexes()

    infos = [r.getMessage() for r in caplog.records
             if r.levelno == logging.INFO and "confirmed absent" in r.getMessage()]
    assert not infos, (
        "the sweep claimed a CONFIRMED removal from a catalogue holding a row it "
        f"could not parse: {infos}")
    assert _retirement_warnings(caplog), (
        "the unconfirmable removal was silent — a reshape this store cannot read "
        "must be reported, not assumed absent")
