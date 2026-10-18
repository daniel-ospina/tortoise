"""#8870 / #7362 — the ORDER of ``SET`` items inside every ``:Source`` version writer.

THE DEFECT. Each writer below advances the §4.6 version anchor
(``contentHash`` + ``version``) in ONE ``SET`` list, and the ``version`` item is
GATED by a comparison against ``s.contentHash``. Put the item that WRITES
``s.contentHash`` first and the guard reads the hash that same clause just
installed: the guard is always false, ``version`` falls through to its preserve
branch, and the ordinal never moves again — *"the hash advances, the counter
does not"* (#7362). A constant ordinal beside a moving hash makes every
``stale``/``current`` anchor read against a number that stopped changing, i.e.
exactly the false ``current`` ONTOLOGY §4.6 forbids.

THE ENGINES DISAGREE — which is why this survived to production.
  * **falkordb/falkordb:6.0.1** (the ``TORTOISE_DB_URI`` production backend)
    applies the items LEFT TO RIGHT, each item seeing the values the EARLIER
    ones just wrote. MEASURED 2026-10-18 on a scratch graph, the minimal
    control: ``SET n.a='y', n.v = CASE WHEN n.a <> 'y' THEN n.v+1 ELSE n.v END``
    leaves ``v`` untouched; on the real statements a content change from
    ``h1`` -> ``h2`` left ``version = 1`` (and, on ``_upsert_source`` also
    ``title``/``updatedAt``/``validFrom``/``_searchText`` stuck).
  * **falkordblite** (the embedded carve-out lane) and **falkordb-server
    v4.20.6** (the CI docker lane) evaluate every right-hand side against the
    PRE-write record, so BOTH orders behave identically there.

Consequence: no behavioural assertion can be RED on the pre-fix code in either
lane the repo runs — a green embedded/v4.20.6 run was never evidence about
production. This file therefore pins the **emitted Cypher's order**, which is
engine-independent and fails on the pre-fix statement in EVERY lane:

    every ``SET`` item whose expression READS ``s.contentHash`` must appear
    BEFORE the item that WRITES it.

The two orderings the fix chose (``version`` before ``contentHash`` in the
connector writer; the ``contentHash`` write LAST in ``_upsert_source``'s
``ON MATCH`` list) are the ones that are correct on BOTH engines.

DIVISION OF LABOUR with ``tests/test_8870_version_anchor_write_order.py`` (the
sibling pin for this defect, deliberately kept separate): that file asserts the
BEHAVIOUR on a live server engine and skips, loudly, where there is none — the
strongest evidence, available only on an engine that applies the SET items LEFT
TO RIGHT — and NOT, as an earlier version of this docstring claimed, "only in
the docker lane": the docker lane runs `falkordb-server v4.20.6`, which reads
pre-write values, so that file MEASURES the engine's semantics and SKIPS there
(naming the property) instead of greening vacuously. This file asserts the
ORDER, which needs no engine, so the defect stays guarded in the embedded
carve-out lane and in any future lane whose backend reads pre-write values. The
behavioural pins that cover the writers' own outcomes: ``test_document_version_counter_advances_with_the_hash``
(``test_projection.py``), ``test_a_content_changing_repoll_advances_the_hash_
and_the_ordinal`` (``test_connector_source_version_5214.py``) and
``test_a_rebuild_reproduces_the_source_field_for_field``
(``test_source_rematerialisation_5024.py`` — pre-fix it reports ``1 == 6`` on
the server engine).
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK

DOC_URL = "doc-set-order-7362"
SRC_URL = "https://example.test/set-order-7362"
GH_URL = "https://github.com/test/repo/issues/42"

#: A ``SET`` list may be introduced bare (``… SET …``) or per arm.
_SET_OPEN = re.compile(r"\b(?:ON CREATE |ON MATCH )?SET\b")
#: A clause keyword that ENDS the current ``SET`` list. Everything that can
#: follow a ``SET`` in the same statement must be here, or the list body runs
#: PAST the clause and a neighbouring clause's read is charged to the last SET
#: item — a false positive (`SET s.b = 1 MERGE (t:Tag {name: s.b})`).
#:
#: ``SET`` itself is the case that bites in BOTH directions (#8939 review):
#: ``SET … SET …`` is legal Cypher. Without ``SET`` here the FIRST list swallows
#: the second clause — a read in clause 2 of a property clause 1 wrote is
#: charged to clause 1's last item when that list has a top-level comma (the
#: loud false positive), and missed entirely when it does not, because the
#: second ``_set_lists`` body is only found via ``_SET_OPEN`` and starts with a
#: fresh ``written`` map (the SILENT false negative — the exact left-to-right
#: hazard this file exists to catch).
_SET_CLOSE = re.compile(
    r"\b(?:RETURN|DELETE|REMOVE|WITH|ON CREATE|ON MATCH|MERGE|CREATE|MATCH|"
    r"WHERE|CALL|UNWIND|FOREACH|LIMIT|ORDER|SKIP|UNION|SET)\b")


class _RecordingGraph:
    """Delegating proxy that records every statement the writer EMITS."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.queries: list[str] = []

    def query(self, statement, *args, **kwargs):
        self.queries.append(statement)
        return self._inner.query(statement, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@contextmanager
def _capture(proj):
    """Run the caller's writes, returning the Cypher they emitted."""
    inner = proj.g
    recorder = _RecordingGraph(inner)
    proj.g = recorder
    try:
        yield recorder
    finally:
        proj.g = inner


def _set_lists(statement: str):
    """Yield ``(keyword, body)`` for every ``SET`` list in ``statement``.

    The list runs from the keyword to the next ``SET``/clause keyword, so the
    two arms of a ``MERGE … ON CREATE SET … ON MATCH SET …`` are checked
    SEPARATELY — an ``ON CREATE`` arm may legitimately write ``contentHash``
    with no reader in front of it (nothing exists to read).

    Quoted spans are blanked FIRST: a clause keyword inside a string literal
    (``s.n = 'see MATCH here'``) would otherwise truncate the body and leave
    everything after it unscanned — a string-literal hole in the boundary scan
    even though the read scan already strips literals.
    """
    statement = _strip_literals(statement)
    for opened in _SET_OPEN.finditer(statement):
        start = opened.end()
        ends = [closer.start() for closer in _SET_CLOSE.finditer(statement, start)]
        end = min(ends) if ends else len(statement)
        yield opened.group(0), statement[start:end]


def _item_end(body: str, start: int) -> int:
    """Index of the top-level ``,`` that ends the SET item starting at ``start``.

    Parenter/quote aware: commas inside ``coalesce(a, b)`` or a CASE's function
    call are not item separators.
    """
    depth = 0
    quote = ""
    i = start
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            return i
        i += 1
    return len(body)


def _set_items(body: str):
    """Split a ``SET`` list into its items — ``(start, end, text)`` triples."""
    items = []
    start = 0
    while start <= len(body):
        end = _item_end(body, start)
        items.append((start, end, body[start:end]))
        if end >= len(body):
            break
        start = end + 1
    return items





def _order_violations(statement: str) -> list[str]:
    """Every ``SET`` item in ``statement`` that reads the hash AFTER it is written."""
    return _same_clause_violations(statement, only="contentHash")


#: A ``SET`` ITEM that assigns ANY ``s.<prop>``. Anchored at the item's start, so
#: a comparison inside another item's CASE (``s.x = ''``, ``s.x <> $v``) is never
#: mistaken for an assignment.
_ITEM_ASSIGN = re.compile(r"\s*s\.(\w+)\s*=(?!=)")
#: A map update — ``SET s = s {.*, title: $t}``, or the parameter form
#: ``SET s += $props`` this repo also uses (`sdk.py`, `commit_ops.py`) — assigns
#: properties the item never names, so it must be recorded as a wildcard or every
#: later read of any property is missed.
_ITEM_MAP_ASSIGN = re.compile(r"\s*s\s*(?:=|\+=)\s*(?:s\s*\{|\$|\{)")
#: Quoted spans, stripped before the read scan: a property name inside a string
#: literal (``s.b = 's.a'``) is text, not a read.
_STR_LIT = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _strip_literals(text: str) -> str:
    """Blank out quoted spans so their contents are never scanned as reads."""
    return _STR_LIT.sub("''", text)


def _same_clause_violations(
    statement: str, *, only: str | None = None
) -> list[str]:
    """Every ``SET`` item that READS a property an EARLIER item in the same statement writes.

    The ``contentHash`` case above is the instance that broke production (#7362),
    but the hazard is the whole rule, not that one property: on a left-to-right
    engine an item that reads ``s.<p>`` sees whatever an earlier item just put
    there, whether ``<p>`` is the version hash, a title, or a search text. The
    ``_searchText`` fallback ``coalesce($st, s._searchText, s.title)`` is exactly
    that — it reads ``s.title``, and when it sat BELOW the ``s.title`` write it
    fell back to the title that same clause had just installed on one engine and
    the stored title on the other (#8870 review).

    An item reading the property IT assigns is fine: its right-hand side is
    evaluated against the pre-write record on both engines, so
    ``s.contentHash = coalesce(s.contentHash, $h)`` is not a violation.

    TWO ``SET`` CLAUSES IN ONE STATEMENT ARE SCANNED AS ONE SEQUENCE (review
    round 9): ``SET … SET …`` is legal, executes in order, and on a
    left-to-right engine the second clause reads what the first wrote — so a
    ``written`` map that restarted per clause read that hazard as clean (the
    silent false negative). Writes therefore CARRY ACROSS consecutive bare
    ``SET`` clauses.

    They do NOT carry across a ``MERGE … ON CREATE SET … ON MATCH SET …`` arm
    boundary: only one arm of a ``MERGE`` ever executes, so an ``ON MATCH``
    read of a property ``ON CREATE`` writes is not a read of anything (and
    ``ON CREATE`` writing ``contentHash`` with no reader in front of it is the
    legitimate shape the two-arm split exists to allow).
    """
    violations: list[str] = []
    # Writes by an EARLIER, NON-EXCLUSIVE (bare) ``SET`` clause of this same
    # statement: property → (clause index, item index), plus the map wildcard.
    carried: dict[str, tuple[int, int]] = {}
    carried_wildcard: tuple[int, int] | None = None
    for clause, (keyword, body) in enumerate(_set_lists(statement)):
        items = _set_items(body)
        # A MERGE arm's OWN writes are mutually exclusive with the sibling arm's,
        # but a BARE `SET` before the MERGE runs UNCONDITIONALLY — so every
        # clause starts from the writes earlier BARE clauses committed. Only a
        # bare clause commits its writes back (below), which is what keeps the
        # two arms invisible to each other.
        bare = keyword == "SET"
        written: dict[str, tuple[int, int]] = dict(carried)
        wildcard: tuple[int, int] | None = carried_wildcard
        for i, (_s, _e, raw) in enumerate(items):
            text = _strip_literals(raw)
            assign = _ITEM_ASSIGN.match(text)
            for read in re.finditer(r"\bs\.(\w+)\b", text):
                prop = read.group(1)
                if only is not None and prop != only:
                    continue
                if assign and assign.group(1) == prop:
                    continue  # its own right-hand side: pre-write on both engines
                if prop not in written and wildcard is None:
                    continue
                hit = written.get(prop, wildcard)
                assert hit is not None  # the guard above excluded the empty case
                w_clause, w_item = hit
                if w_clause == clause:
                    violations.append(
                        f"{keyword}: item {i} of the SET list READS `s.{prop}` AFTER "
                        f"item {w_item} WROTE it — on a left-to-right engine "
                        f"(falkordb 6.x, the production backend) that read sees the "
                        f"value the same clause just installed, so its guarded "
                        f"branch never fires."
                        f"\n  … {text.strip()[:180]} …"
                    )
                else:
                    violations.append(
                        f"{keyword}: item {i} of the SET list READS `s.{prop}`, which "
                        f"an EARLIER `SET` clause of the same statement (clause "
                        f"{w_clause}, item {w_item}) WROTE — on a left-to-right "
                        f"engine (falkordb 6.x, the production backend) the two "
                        f"clauses execute in order, so that read sees the value "
                        f"the earlier clause installed."
                        f"\n  … {text.strip()[:180]} …"
                    )
                break
            if assign:
                written.setdefault(assign.group(1), (clause, i))
            if _ITEM_MAP_ASSIGN.match(text):
                # Recorded AFTER the read scan: the item's own right-hand side
                # may legitimately read a property it is about to write, and the
                # wildcard must not make that item look like it reads its own
                # output.
                wildcard = (clause, i) if wildcard is None else wildcard
        if bare:
            # Hand this clause's writes to the next bare ``SET`` clause, if any.
            # ``written`` already carries every earlier clause's writes, so a
            # chain of three or more ``SET`` clauses composes.
            carried, carried_wildcard = written, wildcard
        # An arm boundary does NOT clear what BARE clauses wrote (those already
        # executed before the MERGE): an arm's writes are simply not committed,
        # so the sibling arm cannot see them and the next one starts from the
        # bare-clause writes again.
    return violations


def _node(proj, url: str):
    rows = proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.contentHash, s.version",
        params={"u": url},
    ).result_set
    return list(rows[0]) if rows else None


def _drive_document(proj) -> None:
    """The document writer (``_upsert_document``) through a real content change."""
    proj._upsert_document({"id": DOC_URL, "title": "v0",
                           "content_hash": "sha-v1", "suppress_embedding": True})
    proj._upsert_document({"id": DOC_URL, "title": "v1",
                           "content_hash": "sha-v2", "suppress_embedding": True})


def _drive_upsert_source(proj) -> None:
    """``_upsert_source``'s ``ON MATCH`` arm through a real hash change."""
    base = {"url": SRC_URL, "sourceKind": "document", "title": "v0",
            "contentHash": "h-0"}
    proj._upsert_source(dict(base))
    proj._upsert_source({**base, "title": "v1", "contentHash": "h-1"})


def _drive_connector(proj) -> None:
    """The connector writer (``_materialize_connector_source``) through a change."""
    proj._materialize_connector_source(
        {"sourceKind": "github_issue", "sourceUrl": GH_URL, "title": "open"},
        "evt-7362-a")
    proj._materialize_connector_source(
        {"sourceKind": "github_issue", "sourceUrl": GH_URL, "title": "closed"},
        "evt-7362-b")


@pytest.fixture()
def proj(tmp_path):
    """A projection for the writer; ``tmp_path`` so pytest reclaims the store.

    A hand-rolled ``tempfile.mkdtemp`` here would leak one tree per test — the
    #4096 class `tests/test_tmpdir_hygiene_4096.py` fails the whole suite on.
    """
    db_path = str(tmp_path / "t.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk._get_proj()
    finally:
        with contextlib.suppress(Exception):  # teardown must not mask the failure
            sdk.close()


# ── the invariant, per writer ────────────────────────────────────────

def test_the_upsert_source_write_to_contenthash_follows_every_reader(proj):
    """``_upsert_source``'s ``ON MATCH`` list (#8870 review P1).

    PRE-FIX (contentHash written FIRST) this fails with FIVE violations —
    ``title``, ``version``, ``updatedAt``, ``validFrom`` and ``_searchText``
    all read the hash the clause had just installed, so on the production
    engine a content change moved the hash and NOTHING else. Post-fix the
    write is the last item, so every reader sees the stored hash.
    """
    with _capture(proj) as recorder:
        _drive_upsert_source(proj)
    statements = [q for q in recorder.queries if "previousProps" in q]
    assert statements, "the writer emitted no _upsert_source statement"
    assert "ON MATCH SET" in statements[-1], (
        "the ON MATCH arm is gone — this pin no longer guards what it names"
    )
    assert _order_violations(statements[-1]) == [], (
        "a `s.contentHash` read follows its write in `_upsert_source`'s SET "
        "list; on falkordb 6.x the guard reads the value the clause just "
        "installed and the field never updates:\n"
        + "\n".join(_order_violations(statements[-1]))
    )
    # …and the writer still does its job on THIS engine.
    assert _node(proj, SRC_URL) == ["h-1", 2]


def test_the_connector_version_is_computed_before_the_hash_is_written(proj):
    """``_materialize_connector_source``'s gated version statement (#5214/#8870).

    PRE-FIX (``contentHash`` first) the ordinal was frozen at 1 on the
    production engine through every content change — measured ``['sha2', 1]``
    where the pair must be ``['sha2', 2]`` — so a connector re-poll could
    never mark an earlier derivation ``stale``.
    """
    with _capture(proj) as recorder:
        _drive_connector(proj)
    statements = [q for q in recorder.queries if "s.sourceKind IN $ck" in q]
    assert len(statements) == 2, (
        f"expected one gated version write per poll, got {len(statements)}"
    )
    for statement in statements:
        assert _order_violations(statement) == [], (
            "the connector writer reads `s.contentHash` after writing it:\n"
            + "\n".join(_order_violations(statement))
        )
    assert _node(proj, GH_URL)[1] == 2, (
        "a content-changing re-poll must advance the ordinal 1 -> 2"
    )


def test_the_document_version_is_computed_before_the_hash_is_written(proj):
    """``_upsert_document`` (§4.6 / #5422 / #8870) — the writer this PR fixed.

    This one is GREEN at the reviewed head and is kept as the standing guard
    against a future "tidy" restoring the pre-#8870 order.
    """
    with _capture(proj) as recorder:
        _drive_document(proj)
    statements = [q for q in recorder.queries if "s.documentKind=coalesce($dk" in q]
    assert statements, "the document writer emitted no upsert statement"
    for statement in statements:
        assert _order_violations(statement) == [], (
            "the document writer reads `s.contentHash` after writing it:\n"
            + "\n".join(_order_violations(statement))
        )
    assert _node(proj, DOC_URL) == ["sha-v2", 2]


# ── the detector itself must not be vacuous ──────────────────────────

def test_the_order_detector_flags_the_pre_fix_shapes():
    """The invariant must RED on the two statements as they were before #8870.

    A guard that cannot fail is worse than none: these are the SHAPES the fix
    replaced, and the detector reports them. (Kept in-test rather than in a
    fixture file so the pre-fix text cannot drift out of sync with the
    production statement unnoticed — the two `_set_lists` texts below are
    verbatim.)
    """
    document_prefix = (
        "MERGE (s:Source {url:$id}) SET "
        "s.contentHash=CASE WHEN $ch IS NULL OR $ch = '' THEN s.contentHash "
        "WHEN s.contentHash IS NULL OR s.contentHash = '' OR s.contentHash <> $ch "
        "THEN $ch ELSE s.contentHash END, "
        "s.version=CASE WHEN $ch IS NULL OR $ch = '' THEN coalesce(s.version, 1) "
        "WHEN s.contentHash IS NULL OR s.contentHash = '' OR s.contentHash <> $ch "
        "THEN coalesce(s.version, 0) + 1 ELSE coalesce(s.version, 1) END"
    )
    assert len(_order_violations(document_prefix)) == 1, document_prefix

    upsert_on_match = (
        "MERGE (s:Source {url:$url}) ON MATCH SET "
        "s.contentHash = CASE WHEN $hash IS NULL THEN s.contentHash "
        "WHEN s.contentHash IS NULL OR s.contentHash <> $hash THEN $hash "
        "ELSE s.contentHash END, "
        "s.version = CASE WHEN $hash IS NULL THEN s.version "
        "WHEN s.contentHash IS NULL OR s.contentHash <> $hash THEN s.version + 1 "
        "ELSE s.version END "
        "RETURN 1"
    )
    assert len(_order_violations(upsert_on_match)) == 1, upsert_on_match

    # A write whose only `s.contentHash` reads are its OWN right-hand side is
    # fine (the `_upsert_source` ON CREATE arm; the supersede sweep's fill-a-
    # gap CASE) — the detector must not flag those.
    own_rhs_only = (
        "MERGE (s:Source {url:$url}) ON CREATE SET "
        "s.contentHash = coalesce($hash, '') "
        "RETURN 1"
    )
    assert _order_violations(own_rhs_only) == [], own_rhs_only
    sweep = (
        "MATCH (old:Source)-[r:references]->(e:Event {eventId: $eid}) "
        "WITH old, s, deg WHERE deg = 1 SET "
        "s.contentHash = CASE WHEN coalesce(s.contentHash, '') = '' "
        "THEN coalesce(old.contentHash, '') ELSE s.contentHash END "
        "DELETE old"
    )
    assert _order_violations(sweep) == [], sweep


# ── the SAME rule, for any property (not just the hash) ──────────────

def test_the_document_search_text_is_not_computed_from_the_title_it_writes(proj):
    """`_upsert_document` (#8870 review P2): `_searchText` must precede `s.title`.

    Its last-resort fallback READS `s.title`, so with the item below the title
    write a text-less re-emit fell back to the title that same clause had just
    installed on a left-to-right engine (`'https://example.test/doc-title2'`)
    and to the STORED title on the other — one clause, two answers, chosen by
    the backend. This is the same read-after-write rule as the hash, which is
    why the detector below is generalised rather than duplicated.
    """
    with _capture(proj) as recorder:
        _drive_document(proj)
    statements = [q for q in recorder.queries if "s.documentKind=coalesce($dk" in q]
    assert statements, "the document writer emitted no upsert statement"
    for statement in statements:
        assert _same_clause_violations(statement) == [], (
            "a same-clause read-after-write survives in the document writer — on "
            "a left-to-right engine the read sees the value the clause just "
            "installed:\n" + "\n".join(_same_clause_violations(statement))
        )


def test_the_writers_have_no_other_same_clause_read_after_write(proj):
    """Standing guard for the whole class, over every statement the writers emit."""
    drivers = (_drive_document, _drive_upsert_source, _drive_connector)
    offenders: list[str] = []
    for drive in drivers:
        with _capture(proj) as recorder:
            drive(proj)
        for statement in recorder.queries:
            for violation in _same_clause_violations(statement):
                offenders.append(f"{drive.__name__}: {violation}")
    assert offenders == [], "\n".join(offenders)


def test_the_generalised_detector_flags_the_search_text_shape():
    """Non-vacuous: the pre-fix `_searchText`-below-`title` shape REDS.

    Without this the generalised rule could be satisfied by a detector that no
    longer matches anything — the failure mode of every regex guard.
    """
    pre_fix = (
        "MERGE (s:Source {url:$id}) SET "
        "s.title=coalesce($title, s.title), "
        "s._searchText=coalesce($st, s._searchText, s.title), "
        "s.updatedAt=$now"
    )
    violations = _same_clause_violations(pre_fix)
    assert len(violations) == 1, violations
    assert "s._searchText" in violations[0] or "s.title" in violations[0], violations

    # The FIXED order: reading the title BEFORE writing it is not a violation.
    post_fix = (
        "MERGE (s:Source {url:$id}) SET "
        "s._searchText=coalesce($st, s._searchText, s.title), "
        "s.title=coalesce($title, s.title), "
        "s.updatedAt=$now"
    )
    assert _same_clause_violations(post_fix) == [], post_fix

    # An item reading the property it assigns is its own right-hand side — the
    # pre-write record on both engines, so never a violation.
    own_rhs = (
        "MERGE (s:Source {url:$id}) SET "
        "s.title=coalesce($title, s.title), "
        "s._searchText=coalesce($st, s._searchText)"
    )
    assert _same_clause_violations(own_rhs) == [], own_rhs


def test_the_capability_probe_agrees_with_the_engine_it_measures(proj):
    """The probe the live pin gates on must agree with a DIRECT measurement.

    The live pin skips when the probe says the engine reads pre-write values, so
    a probe that lied in either direction would either run vacuously or skip on an
    engine that could have shown the defect. This asserts the CONTRACT — the
    probe's verdict equals a bare control clause's ordinal on the SAME engine —
    which is what keeps it honest on ANY backend.

    The `proj` fixture here FOLLOWS THE LANE, so the contract is measured on
    whatever engine that lane runs — and that is exactly what makes it
    lane-agnostic. It passes an explicit `db_path`, and `TortoiseSDK.__init__`
    only honours `TORTOISE_DB_URI` when `db_path is None` (`tortoise/sdk.py`) —
    but the SDK is only the FIRST gate: `_get_proj` then constructs
    `FalkorProjection(path=…)` with an explicit path, and that class's Epic
    #1647 TEST-MODE redirect discards the path and connects to
    `TORTOISE_DB_URI` (`tortoise/projection/__init__.py`) unless the caller's
    stem is exempt in `TEST_NO_REDIRECT_STEMS`, which this module is NOT
    (`tests/_embedded.py`). So:
      * on a URI-less carve-out lane, `proj` is the EMBEDDED backend — a
        PRE-write engine, the case where a lying probe would run the live file
        vacuously;
      * on ANY URI lane (the docker shards, a 6.x measurement lane), `proj` IS
        that server, so the LEFT-TO-RIGHT half of the contract is covered here
        too.
    Both readings are taken on the SAME engine in either case, which is the
    property the assertion needs. (Two earlier versions of this paragraph each
    mis-stated the redirect — first "follows TORTOISE_DB_URI", then "embedded
    and nothing else"; both were false on one lane or the other.)
    """
    from tests.test_8870_version_anchor_write_order import (
        SET_ORDER_LEFT_TO_RIGHT,
        SET_ORDER_PRE_WRITE,
        _probe_set_order,
    )

    proj.g.query("CREATE (n:_ProbeControl7362 {a: 0})")
    try:
        proj.g.query("MATCH (n:_ProbeControl7362) SET n.a = 1, n.b = n.a")
        raw = proj.g.query("MATCH (n:_ProbeControl7362) RETURN n.b").result_set
    finally:
        proj.g.query("MATCH (n:_ProbeControl7362) DELETE n")
    direct = (SET_ORDER_LEFT_TO_RIGHT if raw and raw[0][0] == 1
              else SET_ORDER_PRE_WRITE)
    assert _probe_set_order(proj) == direct


# ── the detector's own edges (review round 4, each demonstrated) ────

def test_the_detector_does_not_charge_a_neighbouring_clause_to_a_set_item():
    """A clause AFTER the SET list is not part of it.

    Without `MERGE`/`MATCH`/`CREATE`/… in `_SET_CLOSE` the list body ran past the
    clause, so a read in `MERGE (t:Tag {name: s.b})` was charged to the last SET
    item — a FALSE POSITIVE that would red a correct writer, which is how a guard
    gets deleted.
    """
    assert _same_clause_violations(
        "MATCH (s:Source) SET s.b = $b, s.a = 1 MERGE (t:Tag {name: s.b})"
    ) == []


def test_a_second_set_clause_is_scanned_with_the_first_clauses_writes():
    """`SET … SET …` is legal and runs in order — the second clause is not clean.

    #8939 review round 9, the SILENT half. `SET` was missing from `_SET_CLOSE`,
    so with no top-level comma the first list swallowed the second clause
    (`'s.a = 1 SET s.b = s.a'` became ONE item whose own-read exemption hid the
    read), and the second `_set_lists` body started from a fresh `written` map.
    Either way a cross-clause read-after-write — the exact left-to-right hazard
    this file exists for — was reported clean. PRE-FIX: 0 violations here.
    """
    violations = _same_clause_violations(
        "MATCH (s:Source {url:$u}) SET s.a = 1 SET s.b = s.a"
    )
    assert len(violations) == 1, violations
    assert "EARLIER `SET` clause" in violations[0], violations

    # …and a violation INSIDE the second clause is found too. PRE-FIX this was
    # invisible as well: the clause boundary was never located, so the second
    # clause's own item list was never scanned with its own writes.
    inside = _same_clause_violations(
        "MATCH (s:Source {url:$u}) SET s.a = 1 SET s.b = 2, s.c = s.b"
    )
    assert len(inside) == 1, inside
    assert "item 0 WROTE it" in inside[0], inside


def test_the_second_set_clause_is_not_charged_to_the_first_clause_items():
    """#8939 review round 9, the LOUD half: ATTRIBUTION.

    With `SET` missing from `_SET_CLOSE` the first list body of the comma form
    is ``'s.a = 1, s.b = 2 SET s.c = s.a'``, so the second clause's read was
    charged to the FIRST list's item 1 — naming an item (`s.b = 2`) that reads
    nothing at all. A guard that mis-attributes a violation is a guard that
    gets deleted, so the read must be charged to the clause that contains it
    and to the clause (and item) that actually wrote the property.
    """
    violations = _same_clause_violations(
        "MATCH (s:Source {url:$u}) SET s.a = 1, s.b = 2 SET s.c = s.a"
    )
    assert len(violations) == 1, violations
    assert "clause 0, item 0" in violations[0], violations
    assert "s.b = 2 SET" not in violations[0], (
        "the second clause's read was charged to the first clause's last item\n"
        + violations[0])


def test_the_detector_does_not_carry_writes_across_merge_arms():
    """`MERGE … ON CREATE SET … ON MATCH SET …` — only ONE arm ever runs.

    So an `ON MATCH` read of a property `ON CREATE` writes is not a read of
    anything (`ON CREATE` writing `contentHash` with no reader in front of it is
    the legitimate shape the two-arm split exists to allow). The cross-clause
    carry must not leak across that boundary, or every arm-split writer reds
    falsely.
    """
    assert _same_clause_violations(
        "MERGE (s:Source {url:$u}) ON CREATE SET s.a = 1 ON MATCH SET s.b = s.a"
    ) == []


def test_the_detector_treats_a_map_update_as_writing_every_property():
    """`SET s = s {.*, title: $t}` writes ALL properties — a later read is a hit.

    A property-by-property matcher records nothing for a map update, so a writer
    that used one and then read a property it had just merged in was silently
    clean.
    """
    violations = _same_clause_violations(
        "MERGE (s:Source {url:$u}) SET s = s {.*, title: $t}, "
        "s._searchText = coalesce($st, s.title)"
    )
    assert len(violations) == 1, violations


def test_the_detector_ignores_a_property_name_inside_a_string_literal():
    """`s.b = 's.a'` is text, not a read of `s.a`."""
    assert _same_clause_violations(
        "MERGE (s:Source {url:$u}) SET s.a = 1, s.b = 's.a'"
    ) == []
    # …and a REAL read in the same item is still caught.
    assert len(_same_clause_violations(
        "MERGE (s:Source {url:$u}) SET s.a = 1, s.b = 'x', s.c = s.a"
    )) == 1


def test_the_detector_scans_past_a_clause_keyword_inside_a_string_literal():
    """A literal must not truncate the SET list before the real violation.

    `_set_lists` located clause boundaries on the RAW statement, so a keyword
    inside a string (`'see MATCH here'`) ended the body early and everything
    after it went unscanned — the string-literal hole, half-closed.
    """
    violations = _same_clause_violations(
        "MERGE (s:Source {url:$u}) SET s.n = 'see MATCH here', s.a = 1, s.b = s.a"
    )
    assert len(violations) == 1, violations


def test_the_detector_does_not_flag_a_map_update_reading_its_own_input():
    """`SET s = s {.*, title: coalesce($t, s.title)}` reads what it is writing.

    The wildcard was recorded before the item's own read scan, so the item looked
    like it read its own output — a false positive against the detector's own
    documented rule.
    """
    assert _same_clause_violations(
        "MERGE (s:Source {url:$u}) SET s = s {.*, title: coalesce($t, s.title)}"
    ) == []


def test_the_detector_treats_the_parameter_map_form_as_writing_everything():
    """`SET s += $props` (used in `sdk.py` / `commit_ops.py`) writes every property.

    Only the `s = s {` form was recognised, so the parameter form was invisible
    and a later read of a property it wrote passed clean.
    """
    violations = _same_clause_violations(
        "MATCH (s:Source {url:$u}) SET s.a = 1, s += $props, s.c = s.b"
    )
    assert len(violations) == 1, violations


def test_a_bare_set_before_a_merge_arm_is_still_a_read_after_write():
    """A bare `SET` executes unconditionally, so an arm's read of it is a hit.

    The carry was cleared at every arm boundary, which discarded writes that had
    certainly run: `SET s.a = 1 MERGE (s) ON MATCH SET s.b = s.a` reported clean
    while the bare→bare form reported the violation (review round 10).
    """
    assert len(_same_clause_violations(
        "MATCH (s:Source {url:$u}) SET s.a = 1 MERGE (s) ON MATCH SET s.b = s.a"
    )) == 1

