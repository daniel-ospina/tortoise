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
strongest evidence, available only in the docker lane. This file asserts the
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
import tempfile
from contextlib import contextmanager

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK

DOC_URL = "doc-set-order-7362"
SRC_URL = "https://example.test/set-order-7362"
GH_URL = "https://github.com/test/repo/issues/42"

#: ``s.contentHash`` as a whole property (never ``s.contentHashish``).
_READ = re.compile(r"\bs\.contentHash\b")
#: The WRITE of it — ``=`` not followed by another ``=`` (``<>`` / ``==``).
_WRITE = re.compile(r"\bs\.contentHash\b\s*=(?!=)")
#: A ``SET`` list may be introduced bare (``… SET …``) or per arm.
_SET_OPEN = re.compile(r"\b(?:ON CREATE |ON MATCH )?SET\b")
#: A clause keyword that ENDS the current ``SET`` list.
_SET_CLOSE = re.compile(r"\b(?:RETURN|DELETE|REMOVE|WITH|ON CREATE|ON MATCH)\b")


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
    """
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


#: A ``SET`` ITEM that assigns ``s.contentHash``. Anchored at the item's start,
#: so the ``=`` comparisons INSIDE another item's CASE (``s.contentHash = ''``,
#: ``s.contentHash <> $ch``) are never mistaken for the assignment.
_ITEM_WRITE = re.compile(r"\s*s\.contentHash\s*=(?!=)")


def _order_violations(statement: str) -> list[str]:
    """Every ``SET`` item in ``statement`` that reads the hash AFTER it is written."""
    violations: list[str] = []
    for keyword, body in _set_lists(statement):
        items = _set_items(body)
        write_items = [i for i, (_s, _e, text) in enumerate(items)
                       if _ITEM_WRITE.match(text)]
        if not write_items:
            continue
        first_write = min(write_items)
        for i, (_s, _e, text) in enumerate(items):
            if i in write_items or i <= first_write:
                continue
            for read in _READ.finditer(text):
                violations.append(
                    f"{keyword}: item {i} of the SET list READS `s.contentHash` "
                    f"at offset {read.start()} of its own text, AFTER the item "
                    f"that WRITES it (item {first_write}) — on a left-to-right "
                    f"engine (falkordb 6.x) that read sees the hash the same "
                    f"clause just installed, so its guarded branch never "
                    f"fires.\n  … {text.strip()[:180]} …"
                )
                break
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
def proj():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_set_order_7362_"), "t.db")
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
