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
4. the over-budget bound declines the RECORD's turn and never degrades the
   answer — it returns `None`, which the caller answers from the graph.
"""
from __future__ import annotations

import ast
from pathlib import Path

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


def test_the_audit_opt_in_emits_no_clause_on_the_column_arm():
    """`excluded_statuses=()` is the caller's terminal-rows-IN opt.

    Mirrored from the graph branches: they emit NO status clause for it, so a
    record leg that kept excluding would make the two legs disagree about the
    same request.
    """
    assert record_vector._status_predicates("point", ()) == []


def test_the_folded_keys_are_the_columns_the_leg_returns():
    """⛔ THE KEY COORDINATE. The exclusion list must name what the SELECT emits.

    For `object`/`subject` the record's ``node_key`` is the ``name``, while the
    leg SELECTs ``id`` and `node_from_row` folds by `id`
    (`cache_projection.KEY_PROPERTY`). A list built from ``node_key`` would
    exclude nothing — every terminal row would still be served — and nothing
    would error, which is the silent-`#689` shape this whole change is about.
    Pinned textually because the mistake is a column NAME, and the live arm can
    only catch it once a row exists.
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
    assert "t.{id_column} <> ALL" in body or "id_column} <> ALL" in body, (
        f"the folded exclusion does not use `id_column` ({body}) — a list of "
        "`node_key` values excludes nothing, because the leg SELECTs `id`")
    assert "node_key" not in body, (
        f"the folded exclusion names `node_key` ({body}) — that is the `name` "
        "column, not the key the leg returns")
    assert record_vector.LABEL_SERVED["object"][1] == "id"
    assert record_vector.LABEL_SERVED["subject"][1] == "id"


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

def test_the_bind_budget_declines_the_record_branch():
    """Past the budget the leg returns `None`, so the caller takes the graph.

    `None` is the documented "not served HERE" answer and the caller falls
    through to the branch that applies the identical cage in Cypher. A bound that
    instead returned a FILTERED-IN list would serve `#689`'s rows; one that
    returned `[]` would report an empty corpus.
    """
    src = MODULE.read_text()
    tree = ast.parse(src)
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
    assert any(isinstance(node, ast.Return)
               and isinstance(node.value, ast.Constant)
               and node.value.value is None
               for node in over_budget[0].body), (
        "the over-budget arm must RETURN None (graph branch), never an empty or "
        "unfiltered list")


def test_the_budget_is_a_bound_and_it_is_positive():
    assert isinstance(record_vector._FOLDED_TERMINAL_KEY_BUDGET, int)
    assert record_vector._FOLDED_TERMINAL_KEY_BUDGET > 0
