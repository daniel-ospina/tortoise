"""#13422 — `object`/`subject` are SERVED, and the `#689` cage is what serves them.

THE ISSUE THIS FILE IS THE PIN FOR, in one sentence: `#13220`'s step 2 had a
write half (the embedding column/index, landed) and a READ half
(`record_vector.LABEL_SERVED` carrying `object`/`subject`, which closed
unlanded) — and the read half is not "add two dict entries", because
`search_engine._STATUS_FILTERED_LABELS` drops superseded / deprecated / archived
/ retracted entities from BOTH graph branches while the record has no `status`
COLUMN to filter on (`#13335`, `chk_object_props_no_derived`). Adding the labels
without the cage returns exactly the rows `#689` exists to hide, and it reports
as BETTER RECALL, which is why the gate had to be paid rather than waived.

WHAT THIS FILE PINS (DB-FREE — the live arm is
`tests/test_11642_vector_move_live.py::test_object_and_subject_*`):

1. both labels are in `LABEL_SERVED`, with the key column and recency slot the
   record's schema actually has;
2. **every** label the graph leg terminal-filters has a record-side status
   source, so a family added to `_STATUS_FILTERED_LABELS` cannot silently skip
   the cage on the record leg — the structural half of indicator 1;
3. the folded arm reads its vocabulary from `search_engine._status_vocab_for`
   (the ONE dispatcher) rather than carrying a list of its own, and it reads the
   fold from `cache_projection` rather than restating it;
4. the over-budget bounds decline the RECORD's turn and never degrade the
   answer — they RAISE `RecordVectorDeclined`, so the caller NAMES the fallback
   (`#4999`) and hands the graph only what is left of the read (`#11691`). There
   are three of them: the terminal-key BIND budget, and the two prefetch budgets
   (rows, journal events) that bound the part of the fold `statement_timeout`
   cannot reach, because it runs in Python;
5. the folded arm is WIRED — `vector_search` asks the fold and its keys reach the
   statement — so a `if False:` on the folded-arm branch cannot leave this file
   green (`ast` pins alone cannot see that).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tortoise import record_vector

MODULE = Path(__file__).resolve().parent.parent / "tortoise" / "record_vector.py"


# ── 1. the served set ──────────────────────────────────────────────────────

def test_object_and_subject_are_served_from_the_record():
    """The read half of `#13220` step 2, at this head.

    ⛔ Falsifiable in the direction that matters: deleting either key returns
    both labels to the brute-force graph scan and re-opens the issue, and no
    other pin in the tree would say so.
    """
    assert "object" in record_vector.LABEL_SERVED
    assert "subject" in record_vector.LABEL_SERVED


def test_their_key_and_recency_match_the_record_schema():
    """`id` is the join key; `created_at` is the ONLY recency column they have.

    `table_columns()` is the record's own column census (it reads the
    migrations), so this cannot be satisfied by a column that does not exist —
    and it reds if a future migration adds `updated_at` without moving the slot,
    which would silently sort every Object as oldest.
    """
    from tortoise.record_projection import table_columns

    columns = table_columns()
    for label, table in (("object", "object"), ("subject", "subject")):
        served = record_vector.LABEL_SERVED[label]
        assert served[0] == table
        assert served[1] == "id"
        assert served[2] == "", "one table holds one served entity here"
        assert "updated_at" not in columns[table], (
            f"public.{table} gained `updated_at` — the recency pair is "
            "('created_at', None) and must be re-decided, not left to emit a "
            "key the graph leg does not")
        assert served[3] == ("created_at", None), served[3]
        assert "embedding_vec" in columns[table], (
            f"public.{table} lost its generated vector — the leg would read "
            "`embedding_vec` and error on every read")


# ── 2. the cage is TOTAL over the graph's filtered labels ──────────────────

def test_every_graph_terminal_filtered_label_has_a_record_status_source():
    """⛔ THE STRUCTURAL PIN. `_STATUS_FILTERED_LABELS` and `_STATUS_COLUMN` are
    two declarations of one thing — WHICH families get the `#689` exclusion — and
    a family added to the first without the second would serve terminal rows from
    the record with nothing going red.

    Before `#13422` this test could not exist: `object`/`subject` were filtered
    on the graph and absent from `_STATUS_COLUMN`, so the assertion would have
    failed — which is exactly the defect the issue describes, restated as a
    property instead of a warning in a docstring.
    """
    from tortoise.search_engine import _STATUS_FILTERED_LABELS

    for label in _STATUS_FILTERED_LABELS:
        entity_type = label.lower()
        assert entity_type in record_vector._STATUS_COLUMN, (
            f"{label} is terminal-filtered on the graph leg "
            f"(`search_engine._STATUS_FILTERED_LABELS`) but has no record-side "
            "status source — the record leg would serve the rows #689 hides")
        # …and a label carrying a source must be one the leg can actually reach,
        # or the entry is coverage on paper and nothing else.
        assert entity_type in record_vector.LABEL_SERVED


def test_the_column_arm_cannot_be_mistaken_for_no_cage():
    """`_status_predicates` emits NO clause for a folded label — on purpose.

    ⛔ THE TRAP THIS PINS: an empty predicate list is also what "nothing to
    exclude" looks like, so a lane adding `_STATUS_COLUMN['object']` without the
    folded arm in `vector_search` would get a green, silently uncaged leg. The
    predicate function therefore returns `[]` for the folded sentinel *and* the
    caller keys off the sentinel, not off the empty list.
    """
    assert record_vector._STATUS_COLUMN["object"][0] == record_vector._FOLDED_STATUS
    assert record_vector._STATUS_COLUMN["subject"][0] == record_vector._FOLDED_STATUS
    assert record_vector._status_predicates("object", None) == []
    # …while the column arm still answers, so the two are distinguishable.
    assert record_vector._status_predicates("point", None)


class _FakeCursor:
    """A cursor that RECORDS every statement and answers the kNN with no rows.

    ⛔ The DB half of the folded arm (`_folded_terminal_keys`) is stubbed below on
    purpose: what these pins are for is the WIRING — which arm of `vector_search`
    runs, and what statement reaches the server — and neither question needs a
    server. The behavioural half (a real fold excluding a real terminal row) is
    the live arm, `tests/test_11642_vector_move_live.py`.
    """

    def __init__(self):
        self.statements: list[tuple[str, tuple]] = []
        self.description = [("id",), ("distance",), ("recency",)]

    def execute(self, sql, params=None):
        self.statements.append((sql, tuple(params or ())))

    def fetchall(self):
        return []

    def fetchone(self):
        return (0,)

    def close(self):
        return None


class _FakeConn:
    def close(self):
        return None


def _run_folded_read(monkeypatch, fold_answer, *, excluded_statuses=None):
    """One `vector_search` over `object`, with the fold's DB half stubbed.

    Returns ``(fold_calls, knn_statement)`` — the two things the wiring pins
    assert on. ``fold_answer`` is what the stubbed fold returns, so a test can
    choose "terminal keys found" versus "the audit opt-in excluded nothing".
    """
    import tortoise.record_projection as rp
    import tortoise.record_serve as rs

    cur = _FakeCursor()
    conn = _FakeConn()
    monkeypatch.setattr(rs, "open_record", lambda *a, **k: conn, raising=True)
    monkeypatch.setattr(rp, "scope_connection", lambda c, org: cur, raising=True)
    calls: list[tuple[str, object]] = []

    def _fold(conn_, cur_, scope, entity_type, excluded):
        calls.append((entity_type, excluded))
        return fold_answer

    monkeypatch.setattr(record_vector, "_folded_terminal_keys", _fold, raising=True)
    record_vector.vector_search(
        record_vector._Scope(org_id="org_13422", graph_id="graph_13422"),
        "object", [0.0] * 8, 10, record_dsn="postgresql://localhost/record",
        excluded_statuses=excluded_statuses)
    knn = [s for s in cur.statements if "<=>" in s[0]]
    assert len(knn) == 1, cur.statements
    return calls, knn[0]


def test_the_folded_arm_is_wired_and_its_keys_reach_the_statement(monkeypatch):
    """⛔ THE DB-FREE WITNESS THAT THE CAGE IS APPLIED AT ALL.

    The `if False:` mutant on `vector_search`'s `if folded:` leaves every
    structural pin in this file green — the branch simply never runs and the
    column arm answers `[]` for a folded label — so the wiring needs a witness
    that observes the CALL. This one does: the fold must be asked, and the keys it
    returns must arrive as the statement's `<> ALL(%s)` parameter.
    """
    calls, (sql, params) = _run_folded_read(monkeypatch, (["o_13422_term"], 0))
    assert calls == [("object", None)], (
        "`object` never reached the folded arm — it was answered by the column arm "
        f"(which emits NO clause for it) or not caged at all: {calls}")
    assert "<> ALL(%s)" in sql, sql
    assert "o_13422_term" in [p for p in params if isinstance(p, (list, tuple))][0]


def test_the_audit_opt_in_reaches_the_folded_arm_and_emits_no_clause(monkeypatch):
    """`excluded_statuses=()` is the caller's terminal-rows-IN opt, on every arm.

    The graph branches emit NO status clause for it, so the record leg must reach
    the FOLDED arm and emit no exclusion either (the folded arm's opt-in is an
    EMPTY key list, `_folded_terminal_keys`). ⛔ Reachable, unlike the assertion
    this replaces: the first assertion reds if the folded arm is bypassed, the
    second if the clause is appended regardless of the key list being empty.
    """
    calls, (sql, _params) = _run_folded_read(monkeypatch, ([], 0),
                                             excluded_statuses=())
    assert calls == [("object", ())], (
        "the audit opt-in did not reach the folded arm as `()` — the leg would "
        f"answer the default cage for the one caller that asked to SEE it: {calls}")
    assert "<> ALL(%s)" not in sql, (
        "the audit opt-in still emitted an exclusion clause — the record leg "
        f"would disagree with both graph branches on the same request: {sql}")


def test_the_folded_keys_are_the_columns_the_leg_returns():
    """⛔ THE KEY COORDINATE. The exclusion list must name what the SELECT emits.

    ⛔ This docstring used to say the record's ``node_key`` is the ``name``, which
    is FALSE at this head — `20261027000001`'s `chk_*_node_key_is_contract_key`
    forces ``node_key = id``, and `cache_projection.KEY_PROPERTY` is `id` for both
    labels. The claim is corrected here rather than left, because a lane following
    it would build the exclusion from the wrong column and that sentence is what
    would have told them to. What matters is narrower and is what this pins: the
    exclusion must use ``LABEL_SERVED[...][1]`` — the column the SELECT returns —
    and `test_the_fold_key_coordinate_is_the_legs_id_column` below pins the other
    half, that `node_from_row(...).key` IS that column. The mistake is a column
    NAME, so it is pinned textually; the live arm cannot catch it because every
    seeded row satisfies `id == name == node_key` by constraint.
    """
    tree = ast.parse(MODULE.read_text())
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "vector_search")
    exclusion = [
        node for node in ast.walk(func)
        if isinstance(node, ast.If)
        and "terminal_keys" in ast.unparse(node.test)
        and "key_params.append" in ast.unparse(node)
    ]
    assert len(exclusion) == 1, "the folded exclusion clause is gone or duplicated"
    body = ast.unparse(exclusion[0])
    assert "id_column} <> ALL" in body, (
        f"the folded exclusion does not use `id_column` ({body}) — a list of "
        "`node_key` values excludes nothing, because the leg SELECTs `id`")
    assert "node_key" not in body, (
        f"the folded exclusion names `node_key` ({body}) — that is the `name` "
        "column, not the key the leg returns")
    assert record_vector.LABEL_SERVED["object"][1] == "id"
    assert record_vector.LABEL_SERVED["subject"][1] == "id"


def test_the_fold_key_coordinate_is_the_legs_id_column():
    """⛔ THE ACTUAL INVARIANT: `node.key` must BE the column the leg SELECTs.

    `_folded_terminal_keys` appends `node.key`, and `node_from_row` derives that
    from `cache_projection.KEY_PROPERTY` — `id` for both labels at this head. The
    textual pin above cannot see a `node.props["name"]` substitution, and every
    LIVE test seeds `id == name == node_key` (the schema's own
    `chk_*_node_key_is_contract_key` forces `node_key = id`), so no behavioural
    test can tell either. This is the pin that can: if `KEY_PROPERTY` moves, the
    exclusion list names a value the SELECT does not return, excludes NOTHING, and
    serves `#689`'s rows with every other check green.
    """
    from tortoise.cache_projection import KEY_PROPERTY, node_from_row

    for label, entity_type in (("Object", "object"), ("Subject", "subject")):
        assert KEY_PROPERTY[label] == "id", (
            f"`{label}` folds by `{KEY_PROPERTY[label]}`, but the dense leg "
            f"SELECTs `{record_vector.LABEL_SERVED[entity_type][1]}` — the two "
            "coordinates have diverged and the cage excludes nothing")
        assert record_vector.LABEL_SERVED[entity_type][1] == KEY_PROPERTY[label]
        node = node_from_row(label, {"node_key": "n", "name": "n",
                                    "id": "i"})
        assert node.key == "i", (
            f"`{label}`'s RecordNode key is {node.key!r} — the exclusion list "
            "would carry names where the SELECT returns ids")


# ── 3. one home each for the vocabulary and the fold ───────────────────────

def _folded_source() -> str:
    """`_folded_terminal_keys` as CODE — docstring and comments stripped.

    ⛔ It must be the code, not the prose: the docstring discusses
    `NOT IN ('archived', …)` and the hide-a-live-name direction, so a raw text
    scan would red on the very explanation that documents the rule. `ast.unparse`
    keeps the executable shape and drops both.
    """
    tree = ast.parse(MODULE.read_text())
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "_folded_terminal_keys")
    if (func.body and isinstance(func.body[0], ast.Expr)
            and isinstance(func.body[0].value, ast.Constant)
            and isinstance(func.body[0].value.value, str)):
        func.body = func.body[1:]
    return ast.unparse(func)


def test_the_folded_arm_reads_the_ONE_vocabulary_dispatcher():
    body = _folded_source()
    assert "from .search_engine import _status_vocab_for" in body, (
        "the folded arm must ask `_status_vocab_for` for the terminal words — a "
        "literal set here would be a second declaration of the Object/Subject "
        "lifecycle vocabulary (#1423/#2729) and could drift from the graph leg")


def test_the_folded_arm_does_not_restate_the_fold():
    """⛔ The fold is `cache_projection`'s, and this leg must not re-derive it.

    A restatement is not a style problem: `apply_event_stream_folds` replays a
    GUARDED state machine in `seq` order (a terminal fold is never overwritten),
    and `node_from_row` applies the carrier derivation (`superseded_by`/
    `superseded_at` → `superseded`) first. A hand-rolled "latest event wins"
    here would resurrect a terminal Object on the branch that carries production
    traffic — the #2164/#3309 defect, re-opened.
    """
    body = _folded_source()
    for required in ("apply_event_stream_folds", "node_from_row",
                     "read_lifecycle_events"):
        assert required in body, (
            f"the folded arm no longer uses cache_projection.{required} — it is "
            "re-deriving the record's status fold instead of replaying it")
    # Nothing here may name a lifecycle word of its own.
    for word in ("superseded", "deprecated", "archived", "retracted"):
        assert f'"{word}"' not in body and f"'{word}'" not in body, (
            f"the folded arm names {word!r} literally — that is a second "
            "declaration of the terminal vocabulary")


def test_the_folded_comparison_is_exact_and_case_sensitive():
    """The graph clause is `NOT IN ('archived', …)` — case-SENSITIVE.

    A case-folding comparison here would hide a name the graph leg still returns
    (a stored `Archived`), i.e. the hide-a-live-name direction #9974 refused.
    """
    body = _folded_source()
    assert "status in excluded" in body, body
    assert ".lower()" not in body, (
        "case-folding the record's status makes the leg hide names the graph "
        "leg returns — the graph clause is an exact NOT IN")


# ── 4. the over-budget bound declines, never degrades ─────────────────────

def test_the_bind_budget_DECLINES_rather_than_answering_None():
    """⛔ Past the budget the leg RAISES `RecordVectorDeclined`, never returns `[]`.

    `[]` would report an empty corpus and a FILTERED-IN list would serve
    `#689`'s rows; `None` was the first cut and the review caught why it is wrong
    (`#13466` review P3): `None` is the contract's *"no record table serves this
    label"*, which the caller takes SILENTLY, while a DECLINED leg has already
    spent this read's prefetch and must NAME the fallback (`#4999`) and hand the
    graph only the remainder of the budget (`#11691`).
    """
    assert issubclass(record_vector.RecordVectorDeclined,
                      record_vector.RecordVectorError), (
        "a caller that does not know the subclass must still take the graph "
        "branch, so Declined has to be a RecordVectorError")
    tree = ast.parse(MODULE.read_text())
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "vector_search")
    over_budget = [
        node for node in ast.walk(func)
        if isinstance(node, ast.If)
        and "_FOLDED_TERMINAL_KEY_BUDGET" in ast.unparse(node.test)
    ]
    assert len(over_budget) == 1, (
        "the bind-budget guard is gone or duplicated — the folded arm would "
        "bind an unbounded text array into every read")
    body = over_budget[0].body
    assert any(isinstance(node, ast.Raise) for node in body), (
        "the over-budget arm must RAISE RecordVectorDeclined, never return "
        "`None` (silent, unnamed fallback) or a list (empty corpus, or #689's "
        "rows filtered IN)")
    assert not any(isinstance(node, ast.Return) for node in body), body


def test_the_budget_is_a_bound_and_it_is_positive():
    assert isinstance(record_vector._FOLDED_TERMINAL_KEY_BUDGET, int)
    assert record_vector._FOLDED_TERMINAL_KEY_BUDGET > 0


# ── 5. the folded arm leaves the graph its #11691 reserve ────────────────

def test_the_folded_arm_divides_the_budget_by_one_MORE_than_it_issues():
    """⛔ THE `#11691` RESERVE, for a leg that issues three statements.

    The column arm issues two data statements and divides by
    `RECORD_LEG_STATEMENTS_PER_READ` (3), leaving the third as the graph branch's
    reserve. The folded arm issues THREE (its two prefetches, then the kNN), so
    dividing by the same 3 lets it spend the whole read and hand the graph a
    floored `1 ms` — turning these two labels' real path into an empty result.
    `#13466` review P3 measured that the old pin
    (`test_11691_vector_leg_budget.py`) asserts the COLUMN arm's arithmetic and
    stays green for the folded one.
    """
    budget = 500
    column_share = record_vector.record_statement_ms(budget)
    assert column_share * 2 < budget, (
        "the column arm's two statements must fit strictly inside the read")
    folded_share = record_vector.record_statement_ms(
        budget, record_vector.RECORD_LEG_STATEMENTS_PER_READ + 1)
    # ⛔ THE RESERVE'S SIZE, not merely that it is positive (`#13466` cycle 3, P3):
    # `folded_share * 3 < budget` is TRUE at the FAILING divisor too
    # (`record_statement_ms(500, 3) == 166`, and 498 < 500) — so it measured
    # nothing. What the `#11691` guarantee actually is, is that at least ONE FULL
    # statement share is left for the graph branch: at the failing divisor that is
    # 500 - 498 = 2 ms, which is the empty-result path this pin exists to prevent.
    reserve = budget - folded_share * 3
    assert reserve >= folded_share, (
        f"the folded arm leaves only {reserve} ms of a {budget} ms read for the "
        f"graph branch after three {folded_share} ms statements — less than one "
        "full share, so a cancelled kNN hands the label's real path a floored "
        "timeout and it returns nothing")
    # …and the divisor really is one more where it is decided.
    tree = ast.parse(MODULE.read_text())
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
                and node.name == "vector_search")
    assert "RECORD_LEG_STATEMENTS_PER_READ + 1" in ast.unparse(func), (
        "vector_search no longer adds the reserve share for folded labels")


def test_the_prefetch_does_not_select_the_payload_columns():
    """⛔ `SELECT *` ON THE PREFETCH IS A MEASURED 2.0 s -> 27 ms (`#13466` P2).

    The fold reads no vector and no jsonb, but `SELECT *` materialises both for
    every row of the label. Per-row sizes, measured with `pg_column_size` on
    `pgvector/pgvector:pg16`: `embedding_vec` is a FIXED `vector(384)` =
    **1,544 B** (fixed-width, so it cannot be larger), and `embedding` jsonb for a
    384-element array is **4.6–7.7 KB** depending on whether the floats are
    short-repr or full-precision. Measured on 8,000 rows: **~2.0 s** with the
    payload, **~0.19 s** without, against the **125 ms** per-statement share a
    500 ms read gives three folded statements plus the graph's reserve.

    ⚠️ The first version of this docstring carried the two figures REVERSED
    ("jsonb ~2.5 KB, `embedding_vec` ~7 KB") and the COLUMN arm's 166 ms share
    (`#13466` cycle 3, P3). Both are corrected here rather than left, because this
    is the test that defends the narrowing and a wrong cost model here is how the
    next lane re-derives the wrong fix.
    """
    from tortoise.cache_projection import GENERATED_COLUMNS
    from tortoise.record_projection import table_columns
    from tortoise.stored_form import STORED_VECTOR_PROPS

    # ⛔ The dropped set comes from the DECLARED homes, not a literal in this test
    # either (`#13466` cycle 2, P3): the first cut hardcoded the two names while its
    # docstring claimed to read those declarations.
    assert record_vector._fold_prefetch_dropped() == (
        frozenset(STORED_VECTOR_PROPS) | frozenset(GENERATED_COLUMNS))
    assert "embedding" in STORED_VECTOR_PROPS
    assert "embedding_vec" in GENERATED_COLUMNS

    columns = table_columns()
    for table in ("object", "subject"):
        rendered = record_vector._fold_prefetch_select(table)
        assert "embedding" not in rendered.replace(" ", ""), rendered
        assert "embedding_vec" not in rendered, rendered
        # …and it is the census minus exactly the two payload columns, so a
        # migration that adds a column is read without an edit here.
        expected = set(columns[table]) - record_vector._fold_prefetch_dropped()
        named = {part.strip().removeprefix("t.") for part in rendered.split(",")}
        assert named == expected, (sorted(expected - named), sorted(named - expected))
# ── 6. the prefetch's PYTHON half is bounded, and the decline is NAMED ────

def test_the_prefetch_row_budget_declines_instead_of_truncating():
    """⛔ `statement_timeout` CANNOT BOUND THE PYTHON FOLD, so this must.

    `_folded_terminal_keys` reads every row of the label and runs
    `node_from_row` over each in the CLIENT process, where no server-side timeout
    can cancel it. Past `_FOLDED_PREFETCH_ROW_BUDGET` it returns `None` (the
    caller then RAISES `RecordVectorDeclined`, so the fallback is named) and NEVER
    a truncated row set — a dropped row is a terminal key this leg would then
    SERVE, which is the `#689` fail-OPEN direction.

    RED if the budget check is removed (the fold would go on to project
    `budget + 1` rows), and RED if the `LIMIT` stops reaching the statement.
    """
    budget = record_vector._FOLDED_PREFETCH_ROW_BUDGET
    assert isinstance(budget, int) and budget > 0

    class _OverBudgetCursor:
        description = [("id",)]

        def __init__(self):
            self.sql = ""
            self.params: tuple = ()

        def execute(self, sql, params=None):
            self.sql, self.params = sql, tuple(params or ())

        def fetchall(self):
            return [("o_13422_row",)] * (budget + 1)

    cur = _OverBudgetCursor()
    assert record_vector._folded_terminal_keys(
        None, cur, record_vector._Scope(org_id="org", graph_id="graph"),
        "object", None) is None, (
        "an over-budget `object` prefetch must decline, never fold a TRUNCATED "
        "row set")
    assert "LIMIT %s" in cur.sql, cur.sql
    assert cur.params[-1] == budget + 1, cur.params


def test_the_prefetch_event_budget_declines_and_bounds_the_event_read(monkeypatch):
    """The journal half of the same bound, and it is asked for BOUNDED.

    `read_lifecycle_events` with no `target` returns the tenant's whole
    status-bearing stream and `apply_event_stream_folds` then builds two dicts
    over it — Python, uncancellable. The leg must ASK for `budget + 1` events (so
    "exactly at" and "one past" are distinguishable) and decline on the overflow.
    """
    import tortoise.cache_projection as cp

    budget = record_vector._FOLDED_PREFETCH_EVENT_BUDGET
    assert isinstance(budget, int) and budget > 0
    asked: dict = {}

    def _events(conn, **kwargs):
        asked.update(kwargs)
        return tuple(range(budget + 1))

    # The ROWS half of the prefetch is replaced rather than faked column by
    # column: its SELECT is `_fold_prefetch_select`'s business (pinned above) and
    # this pin is about the EVENT bound, so the row shape must not be able to
    # red it.
    monkeypatch.setattr(record_vector, "_rows_as_dicts",
                        lambda cur: [{"id": "o_13422_row",
                                      "has_embedding_vec": True}])
    class _NoopCursor:
        """The prefetch's `execute` — the rows it returns are stubbed above."""

        def execute(self, sql, params=None):
            pass

    monkeypatch.setattr(cp, "node_from_row", lambda label, row: object())
    monkeypatch.setattr(cp, "read_lifecycle_events", _events)
    assert record_vector._folded_terminal_keys(
        object(), _NoopCursor(),
        record_vector._Scope(org_id="org", graph_id="graph"),
        "object", None) is None, (
        "an over-budget journal prefetch must decline, never fold a TRUNCATED "
        "event stream (an unapplied terminal fold serves the row)")
    assert asked.get("limit") == budget + 1, asked


def test_an_over_budget_prefetch_raises_the_NAMED_decline(monkeypatch):
    """`_folded_terminal_keys` signals over-budget with `None`; the LEG must NAME it.

    ⛔ The two halves are pinned separately on purpose: the helper returns the
    signal, and `vector_search` turns it into `RecordVectorDeclined` (a
    `RecordVectorError` subclass) so the caller records `scan_fallback` and hands
    the graph branch only the remainder of the read (`#4999`, `#11691`). A `None`
    returned from the leg instead would be the contract's "no record table serves
    this label" — taken SILENTLY, with the graph given the whole budget again.
    """
    with pytest.raises(record_vector.RecordVectorDeclined):
        _run_folded_read(monkeypatch, None)


