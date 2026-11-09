"""#11642 — the Postgres dense leg, and the erasure residual scan.

Two subjects, and they are one file because they are one DECISION's two halves:
the move puts an ANN leg in Postgres (`tortoise/record_vector.py`) and therefore
puts a copy of every vector in two stores, which is why the erasure attestation
had to grow a second store (`tortoise/embedding_residual_scan.py`).

⛔ NOTHING HERE NEEDS A LIVE POSTGRES. That is deliberate rather than convenient:
the repo's record legs that DO need one (`tests/test_record_serve_live.py`) skip
in every CI leg today, so a pin written only there would be green because it
never ran. What is pinnable without a database is the part that has been wrong
before: which labels are served, what an unserved label does, what a failure
does, when the leg is armed, and the difference between "the store holds
nothing" and "nobody looked".
"""
from __future__ import annotations

import pytest

from tortoise import record_vector
from tortoise.embedding_residual_scan import (
    CLEAN,
    EXIT_CLEAN,
    EXIT_RESIDUE,
    EXIT_UNAVAILABLE,
    RESIDUE,
    UNAVAILABLE,
    ResidualScan,
)

# ── the served set ──────────────────────────────────────────────────────────

def test_only_the_labels_whose_vector_the_record_carries_are_served():
    """`object`/`subject` are NOT served, and that is the stated decision.

    Their vector is derived inside the graph fold from `name` (#7845) and rides
    no journal record, so `public.object`/`public.subject` have no column to
    read — see `20261109000001_record_embedding_vector.sql`'s closing block for
    both rejected candidates. A future lane that "adds" them here without a
    column would make the leg query a column that does not exist.
    """
    assert set(record_vector.LABEL_SERVED) == {
        "point", "operator", "source", "document", "event"}
    assert "object" not in record_vector.LABEL_SERVED
    assert "subject" not in record_vector.LABEL_SERVED


def test_every_served_label_is_a_real_entity_type():
    """The map must not invent a vocabulary the runner would reject.

    `run_vector_query` validates `entity_type` against
    `tortoise.security.VALID_ENTITY_TYPES` before it does anything, so a key
    here that is not in that set can never be reached — a dead entry that reads
    as coverage.
    """
    from tortoise.security import VALID_ENTITY_TYPES

    assert set(record_vector.LABEL_SERVED) <= set(VALID_ENTITY_TYPES)


def test_the_id_column_matches_the_runners_id_field():
    """#448's three-way `id_field` derivation, restated in SQL.

    `source`/`document` answer the canonical key `url`, `event` answers
    `event_id`, everything else `id`. A divergence here would return a node key
    the caller cannot join back to the graph — a silent empty dense leg.
    """
    assert record_vector.LABEL_SERVED["source"][1] == "url"
    assert record_vector.LABEL_SERVED["document"][1] == "url"
    assert record_vector.LABEL_SERVED["event"][1] == "event_id"
    assert record_vector.LABEL_SERVED["point"][1] == "id"
    assert record_vector.LABEL_SERVED["operator"][1] == "id"


def test_operator_and_document_are_the_two_discriminated_rows():
    """One table can hold two served entities; the filter is what separates them.

    Operators are Points (#172) and a Document IS a Source (D10, §4.4), so an
    unfiltered read of either table would fuse two entity types into one leg.
    `point`/`source` must therefore carry NO filter while their twins do.
    """
    assert record_vector.LABEL_SERVED["point"][2] == ""
    assert record_vector.LABEL_SERVED["source"][2] == "document_kind IS NULL"
    assert "is_operator" in record_vector.LABEL_SERVED["operator"][2]
    assert "IS NOT NULL" in record_vector.LABEL_SERVED["document"][2]


def test_the_scan_cannot_return_a_short_answer_silently():
    """The measured pgvector hazard, pinned as behaviour.

    On pgvector 0.8.7 with the DEFAULT `hnsw.iterative_scan = off`, a filtered
    ANN over a 20,000-row corpus returned **0 rows** where 152 matched
    (`LIMIT 500`); `strict_order` returned 150. The leg therefore sets the scan
    explicitly on every read — and this constant is what the SQL uses, so a
    "cleanup" that drops the SET re-opens the hole.
    """
    assert record_vector.HNSW_ITERATIVE_SCAN == "strict_order"


def test_the_shortfall_compares_strictly_and_only_on_the_short_path(monkeypatch):
    """The comparison itself, pinned — the review's "invert `>` to `>=`" lane.

    ⛔ LIVE POSTGRES CANNOT PIN THIS, and that is why it lives here. The shortfall
    fires when the filter selects MORE material than the scan returned, and
    `hnsw.iterative_scan = strict_order` is the fix for exactly that — so a
    correct server will not produce a short answer on demand. What can be pinned
    without one is the COMPARISON, and it has three arms which a single
    assertion cannot cover:

      * `available > len(rows)`  -> raise (a filter that owes more than it gave)
      * `available == len(rows)` -> NO raise (a complete answer on a small
        tenant; `>=` here would report a shortfall on a correct read)
      * `len(rows) == limit`     -> the count is never executed at all (the cost
        rule: every answer for a tenant smaller than `limit` pays one aggregate,
        a FULL window pays none)

    Drives `vector_search` over a stub cursor, so the SQL is not executed and
    the three arms are decided by the code under test alone.
    """
    class _Cursor:
        def __init__(self, rows, available):
            self._rows, self._available = rows, available
            self.executed: list[str] = []

        def execute(self, sql, params=None):
            self.executed.append(sql)

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return (self._available,)

        def close(self):
            pass

    class _Conn:
        def __init__(self, cursor):
            self._cursor = cursor

        def cursor(self):
            return self._cursor

        def close(self):
            pass

    def _drive(rows, available, limit):
        # Both seams are imported INSIDE `vector_search`, so patching the MODULE
        # attributes is what the call site resolves — the stub never touches a
        # driver, and the only code under test is the comparison.
        import tortoise.record_projection as rp
        import tortoise.record_serve as rs

        cursor = _Cursor(rows, available)

        monkeypatch.setattr(rs, "open_record", lambda dsn: _Conn(cursor))
        monkeypatch.setattr(rp, "scope_connection",
                            lambda conn, org: conn.cursor())
        scope = record_vector._Scope(org_id="o", graph_id="g")
        try:
            return record_vector.vector_search(
                scope, "point", [0.0] * 384, limit, record_dsn="postgresql://x/y"
            ), cursor.executed
        finally:
            monkeypatch.undo()

    rows = [("p1", 0.1, "2026-01-01"), ("p2", 0.2, "2026-01-02")]

    # (1) the filter owes more than the scan gave -> NAMED, never returned.
    with pytest.raises(record_vector.VectorShortfall):
        _drive(rows, available=3, limit=5)

    # (2) a COMPLETE short answer -> no raise. This is the arm an inverted `>=`
    #     reds, and it is the common case for a tenant smaller than the limit.
    got, executed = _drive(rows, available=2, limit=5)
    assert len(got) == 2, got
    assert any("count(*)" in sql for sql in executed), (
        "the short path must ask the filter how much material it selects")

    # (3) a FULL window pays NO aggregate — the per-read cost rule.
    got, executed = _drive(rows, available=99, limit=2)
    assert len(got) == 2, got
    assert not any("count(*)" in sql for sql in executed), (
        "a full window must not pay the count: every answer for a tenant "
        f"smaller than the limit already pays one (executed: {executed})")


# ── arming ─────────────────────────────────────────────────────────────────

def test_no_tenant_means_no_searcher(monkeypatch):
    """The scope comes from the ONE declaration both credential lanes install.

    An unarmed request must produce no searcher at all — not a searcher that
    guesses. RLS renders an unscoped read as ZERO ROWS, so a guess would present
    a total loss of the dense leg as "no matches".
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setattr("tortoise.record_writer.record_tenant", lambda: None,
                        raising=False)
    assert record_vector.searcher() is None


def test_a_half_tenant_is_not_a_tenant():
    """`(None, g)`, `(o, "")`, `"og"`, `(o,)` are all refused, never half-used.

    A mis-scoped read under RLS returns ZERO rows with no error, so the one
    input that produces a silent empty dense leg is a tenant the record cannot
    be scoped to — it must be refused HERE, before any SQL runs.
    """
    import tortoise.record_writer as rw

    original = rw.record_tenant
    try:
        for bad in ((None, "g"), ("", "g"), ("o", None), ("o", ""), "og", ("o",),
                    ("o", "g", "extra"), 3, None):
            rw.record_tenant = lambda b=bad: b
            assert record_vector._resolve_scope() is None, bad
        rw.record_tenant = lambda: ("o", "g")
        resolved = record_vector._resolve_scope()
        assert resolved is not None
        assert (resolved.org_id, resolved.graph_id) == ("o", "g")
    finally:
        rw.record_tenant = original


def test_the_carried_tenant_arms_the_leg_without_a_contextvar(monkeypatch):
    """The BY-VALUE door (#11642 review round 2, P1) — the hosted REST lane.

    Two lanes deliver a tenant by two different doors: the MCP lane installs the
    `use_record_tenant` ContextVar, while `hosted_api._data_sdk` carries it BY
    VALUE on the SDK it opens (`sdk._bind_record_tenant`, "the auth seam is a
    plain dependency … holds no `with`"). A resolver that read only the
    ContextVar would answer "no tenant" on every `/v1/search`, so `pg_vector`
    would be `None` there and — because this change retires the in-graph index —
    every hosted dense read would silently become a brute-force `:Point` scan.

    `record_tenant_effective(carried=…)` is the ONE resolver that knows the
    precedence (carried > scope > env pair), and it exists because `#10067`
    already paid for this exact split on the write path.
    """
    import tortoise.record_writer as rw

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    # The CUTOVER must be armed for the tenant half to be observable at all —
    # it is OFF by default and has its own pin (`test_the_cutover_is_OFF_by_default`).
    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    monkeypatch.setattr(rw, "record_tenant", lambda: None, raising=False)

    assert record_vector.searcher() is None, (
        "with NO tenant from either door the leg must be unarmed")
    armed = record_vector.searcher(carried=("org_1", "graph_1"))
    assert armed is not None, (
        "the carried tenant did not arm the leg — the hosted REST lane would "
        "never read Postgres, on the change that retired the in-graph index")


def test_the_sdk_searcher_passes_its_carried_tenant(monkeypatch):
    """`sdk._record_vector_searcher(sdk)` threads `sdk._record_tenant` down.

    The SDK resolves the searcher on the REQUEST thread, where the instance —
    and therefore the by-value tenant — is in reach. A module-level helper that
    ignored the handle is how the hosted lane goes unserved.
    """
    from tortoise import sdk as sdk_mod

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    import tortoise.record_writer as rw

    monkeypatch.setattr(rw, "record_tenant", lambda: None, raising=False)

    class _WithTenant:
        _record_tenant = ("org_9", "graph_9")

    assert sdk_mod._record_vector_searcher(_WithTenant()) is not None, (
        "the SDK's carried tenant was not consulted")
    assert sdk_mod._record_vector_searcher() is None


def test_an_unarmed_box_reports_unarmed_rather_than_raising():
    """`armed()` is a fact, not an exception — the SDK calls it per read."""
    assert record_vector.armed("") is False


# ── the width is ONE declaration, and the migration is the second copy ─────

def test_the_migration_declares_the_one_embedding_width():
    """`EMBEDDING_DIM` and the migration's `vector(384)` must be the same number.

    ⛔ THIS IS THE TRAP `#4194`/`#4280` ALREADY PAID FOR, one store over. The code
    this change deletes read `embeddings.EMBEDDING_DIM` explicitly so that a
    fresh index and the write path could not disagree — "a bare literal here plus
    a rotated `EMBEDDING_DIM` would bless vectors the index cannot hold". The
    Postgres column and the converter's `jsonb_array_length(doc) = 384` are
    HAND-TYPED literals, and on a width rotation they would go on answering for
    the old width: the converter would return NULL for every row (stored, not
    indexed) and every record dense read would silently degrade to the
    brute-force graph leg — with the move's cost already paid. Nothing else in
    the suite connects the two, so this pin is the connection.
    """
    import re
    from pathlib import Path

    from tortoise.embeddings import EMBEDDING_DIM

    sql = (Path(record_vector.__file__).parent.parent
           / "supabase/migrations/20261109000001_record_embedding_vector.sql")
    text = sql.read_text(encoding="utf-8")
    declared = {int(m) for m in re.findall(r"public\.vector\((\d+)\)", text)}
    declared |= {int(m) for m in re.findall(r"jsonb_array_length\(doc\) <> (\d+)",
                                            text)}
    declared |= {int(m) for m in re.findall(r"[^_\w]vector\((\d+)\)", text)}
    assert declared, "the migration declares no vector width at all"
    assert declared == {EMBEDDING_DIM}, (
        f"the migration declares width(s) {sorted(declared)} but "
        f"`embeddings.EMBEDDING_DIM` is {EMBEDDING_DIM} — rotating the width "
        "would leave the Postgres column and the converter answering for the "
        "old one, and every record dense read would degrade to the graph "
        "silently")


# ── the arming surface ─────────────────────────────────────────────────────

def test_the_cutover_is_OFF_by_default(monkeypatch):
    """The record is a fold with NO backfill, so it cannot be the only source.

    Until the existing vectors are in the record, making it the dense leg's sole
    source would drop every pre-existing point out of the pool — silently, since
    the shortfall assertion counts `available` with the record's own predicate
    and so cannot see material only the graph holds (`#5090` Q2). So arming is a
    deliberate act, on the same posture as `TORTOISE_EMBED_GATE` and
    `record_serve.SERVE_MODE`.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    assert record_vector.searcher(carried=("o", "g")) is None, (
        "the cutover armed itself — a default-on move would silently drop every "
        "vector the record does not yet hold")

    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    assert record_vector.searcher(carried=("o", "g")) is not None


def test_the_gap_level_follows_the_cutover_not_the_dsn(monkeypatch):
    """A record DSN is NOT a replacement, so it must not mute the `#4997` warning.

    ⛔ THIS IS THE MUTED-ALARM DEFECT (#11660 review round 6, P1-3). Round 3 made
    the dense leg's cutover a second, stronger condition and left
    `projection.vector_index_retired()` on the weaker one (a DSN resolving). On
    the posture that makes the DEFAULT for every hosted store — DSN set, cutover
    unset — the in-graph index is dropped, the record does NOT answer the dense
    read, every served label really does full-scan, and the predicate said the
    elevation was earned, so the gap report was downgraded to an INFO whose text
    says the read *is* served from Postgres. The one line an operator reads in
    an incident said the opposite of the truth.

    Falsifiable: the pre-fix predicate returned True on the first case below, so
    the first assertion fails.
    """
    from tortoise import projection

    assert projection.VECTOR_INDEX_RETIRED is True, (
        "this pin is about the retired posture; if the rollback ship-wide flag "
        "moved, the three cases below answer False for a different reason")

    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.delenv(record_vector.ARMING_ENV, raising=False)
    assert projection.vector_index_retired() is False, (
        "a DSN alone muted #4997's WARNING on an unarmed store — the index is "
        "dropped and nothing replaced it, which is the state the warning is FOR")

    monkeypatch.setenv(record_vector.ARMING_ENV, "1")
    assert projection.vector_index_retired() is True, (
        "an armed cutover with a DSN IS the replacement — the exemption is "
        "earned, so the line may be an INFO")

    monkeypatch.delenv("TORTOISE_RECORD_DSN")
    assert projection.vector_index_retired() is False, (
        "an armed cutover with no record to read is not a replacement either")


# ── the residual scan's verdict algebra ────────────────────────────────────

def test_clean_requires_BOTH_stores_to_have_been_read():
    """The whole point: a one-store scan is never CLEAN.

    `None` is "nobody looked", never zero — the distinction the tool exists to
    keep, and the one a `count()`-shaped API loses by default.
    """
    partial = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                           graph_vectors=None,
                           refusals=["graph: no handle supplied"])
    assert partial.verdict == UNAVAILABLE
    assert partial.exit_code == EXIT_UNAVAILABLE

    both = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                        graph_vectors=0)
    assert both.verdict == CLEAN
    assert both.exit_code == EXIT_CLEAN


@pytest.mark.parametrize("record,graph", [(3, 0), (0, 3), (2, 2)])
def test_residue_in_EITHER_store_is_a_finding(record, graph):
    """Erased means erased in both. A leftover in one store is not "mostly fine".

    This is the assertion that would have caught the reason the scan exists: a
    delete that reached the graph and not the record (or the reverse) leaves a
    vector readable by exactly the leg the customer cannot see.
    """
    outcome = ResidualScan(org_id="o", graph_id="g", record_vectors=record,
                           graph_vectors=graph)
    assert outcome.verdict == RESIDUE
    assert outcome.exit_code == EXIT_RESIDUE


def test_a_refusal_outranks_a_count_even_a_zero_one():
    """A store that could not be read may not contribute a clean zero.

    A record that refuses while the graph reads 0 must NOT be RESIDUE and must
    not be CLEAN — it is no verdict, because the unread store is exactly where
    the residue would be.
    """
    outcome = ResidualScan(org_id="o", graph_id="g", record_vectors=None,
                           graph_vectors=0,
                           refusals=["record: could not be opened"])
    assert outcome.verdict == UNAVAILABLE


def test_the_outcome_names_the_scope_it_measured():
    """A CLEAN about the wrong scope is a true statement about nothing asked.

    The dict carries the scope and what "scope" MEANS in each store, so a reader
    quoting a CLEAN cannot mistake a graph-name scope for an org-wide one.
    """
    payload = ResidualScan(org_id="o", graph_id="g", record_vectors=0,
                           graph_vectors=0).as_dict()
    assert payload["org_id"] == "o" and payload["graph_id"] == "g"
    assert "org_id+graph_id" in payload["scope"]
    assert "graph NAME" in payload["scope"]
    # ⛔ TWO namespaces, never one (#11660 review round 3, P1): the record's
    # `graph_id` is a `public.graphs.id`, the graph's name is that row's
    # `namespace`. A reader quoting this CLEAN must be able to see that the two
    # halves were scoped by DIFFERENT identifiers.
    # ⛔ AND THIS LINE USED TO BE A TAUTOLOGY (#11660 review round 6, P2-4):
    # `in payload["scope"] or "record" in payload["scope"]` is satisfied by the
    # word "record" alone, so it passed against the PRE-fix wording too and
    # pinned nothing. It now names the identifier, which the pre-fix string did
    # not contain.
    assert "public.graphs.id" in payload["scope"], (
        "the scope must name the record's identifier by its real namespace — "
        "'graph_id' alone is what made one value look interchangeable with the "
        "FalkorDB graph name")


def test_the_unindexed_split_is_counted_apart_from_the_residue(monkeypatch):
    """A vector the ANN index cannot see is COUNTED, not silently excluded.

    `embedding_vec` is GENERATED, and the converter returns NULL for a width the
    store cannot hold (non-384, an object, `[]`, a `null` element). The dense
    leg's `WHERE embedding_vec IS NOT NULL` then excludes that row with no
    counter anywhere, which reads exactly like "this tenant has no vectors"
    (#11660 review round 6, P2-6). `record_unindexed_by_table` is that counter.

    Falsifiable: before this field existed there was no second query at all, so
    both the attribute and the key are absent.
    """
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs
    from tortoise.embedding_residual_scan import _count_record

    executed: list[str] = []

    class _Cur:
        sql = ""

        def execute(self, sql, params):
            self.sql = sql
            executed.append(sql)

        def fetchone(self):
            # 4 unindexed of 10 held, per table — the exact split the ANN leg
            # cannot serve, so a swapped pair is observable.
            return (4 if "embedding_vec IS NULL" in self.sql else 10,)

        def close(self):
            return None

    class _Conn:
        def close(self):
            return None

    monkeypatch.setattr(rs, "open_record", lambda dsn: _Conn())
    monkeypatch.setattr(rp, "scope_connection", lambda conn, org: _Cur())

    outcome = ResidualScan(org_id="o", graph_id="g")
    _count_record(outcome, "postgresql://localhost/record")

    assert outcome.record_vectors == 30
    assert outcome.record_by_table == {"point": 10, "source": 10, "event": 10}
    assert outcome.record_unindexed_by_table == {
        "point": 4, "source": 4, "event": 4}
    assert any("embedding IS NOT NULL AND embedding_vec IS NULL" in sql
               for sql in executed), (
        "the split is counted by the database, not inferred from the total")
    payload = outcome.as_dict()
    assert payload["record_unindexed_total"] == 12
    assert payload["record_vectors"] == 30, (
        "the split is counted APART, not added: double-counting would inflate "
        "every residue report by the number of rows the index cannot see")
    assert payload["verdict"] == RESIDUE, (
        "an unindexed vector is still a vector IN the record — if the scope was "
        "meant to be erased it is residue whether or not the ANN can see it. "
        "The counter answers a different question (\"why did the dense leg not "
        "return this row\"), and it must not change the erasure verdict.")


def test_the_record_half_counts_both_the_payload_and_the_indexed_projection():
    """A residual can survive in `embedding` OR in `embedding_vec`.

    The indexed projection is GENERATED from the payload, so today they cannot
    disagree — but an erasure that nulls one and not the other is precisely the
    shape a `delete`-based attestation misses, and counting only the payload
    would report it clean.
    """
    import inspect

    src = inspect.getsource(
        __import__("tortoise.embedding_residual_scan", fromlist=["x"])._count_record)
    assert "embedding IS NOT NULL" in src
    assert "embedding_vec IS NOT NULL" in src


# ── the CLI must not read a graph the engine does not hold ─────────────────

def _fake_falkordb(monkeypatch, names, *, listing_raises=False):
    """Install a stand-in `FalkorDB` and return the names `select_graph` was asked for."""
    import falkordb

    selected: list[str] = []

    class _DB:
        @classmethod
        def from_url(cls, uri):
            if listing_raises:
                raise RuntimeError("the listing is down")
            return cls()

        def list_graphs(self):
            return list(names)

        def select_graph(self, name):
            selected.append(name)
            return ("handle", name)

    monkeypatch.setattr(falkordb, "FalkorDB", _DB)
    return selected


def test_a_graph_name_the_engine_does_not_hold_is_REFUSED_not_read(monkeypatch):
    """`--graph-name org_typo` must be UNAVAILABLE, never CLEAN (#11660 P1-4).

    An aggregate over a name the engine does not hold answers `[[0]]`, which is
    the same number a genuinely empty tenant answers — so the old code recorded
    no refusal and printed a false erasure attestation. Worse, a Cypher read on
    an unknown name CREATES an empty graph (verified on FalkorDB 4.20.4; see
    `tortoise/hosted_backup.py`), which is why the check runs BEFORE any query.

    Falsifiable: the pre-fix `_graph_handle` went straight to `select_graph` and
    returned a handle, so `is None` and the empty `selected` list both fail.
    """
    import tools.embedding_residual_scan as cli

    selected = _fake_falkordb(monkeypatch, names=["org_acme"])
    assert cli._graph_handle("bolt://localhost:6379", "org_typo") is None, (
        "a name the engine does not hold must not produce a handle — a scan "
        "over it would answer 0 and report CLEAN")
    assert selected == [], (
        "nothing may be SELECTED for an unverified name: a Cypher read on it "
        "creates an empty graph as a side effect")


def test_a_graph_name_the_engine_holds_is_still_read(monkeypatch):
    """The refusal is not an over-block: a real name produces its handle."""
    import tools.embedding_residual_scan as cli

    selected = _fake_falkordb(monkeypatch, names=["org_acme", "org_other"])
    assert cli._graph_handle("bolt://localhost:6379", "org_acme") == (
        "handle", "org_acme")
    assert selected == ["org_acme"]


def test_an_unreadable_graph_listing_is_a_refusal_not_a_CLEAN(monkeypatch):
    """`GRAPH.LIST` failing is "not measured", never "nothing there"."""
    import tools.embedding_residual_scan as cli

    _fake_falkordb(monkeypatch, names=[], listing_raises=True)
    assert cli._graph_handle("bolt://localhost:6379", "org_acme") is None
