"""#13447 — ONE derive-and-encode seam; a write pays one model call, a restore lands a vector.

WHAT THIS PINS, and each assertion is a property rather than a restatement of the
code:

1. **One model call per write.** The live write encodes a derived row's text twice
   — once for the record's column (#13220) and once in the graph fold (#7845) — and
   `embeddings.encode_for_row` memoises nothing. Driven through the REAL halves
   (`record_writer.derived_embedding_columns` then `derived_embedding.encode`, the
   call the fold makes) on a counting `compute_embedding`, the pair costs **one**
   model call inside `derived_embedding.write_scope()` and **two** without it. The
   counter-test is deliberate: it is the pre-fix behaviour, so a future "cleanup"
   that drops the scope reds here instead of shipping the second call silently.
2. **The width rule is not bypassed** — the second caller may ask for a different
   store width than the first, and it still gets the answer a fresh encode would
   have produced (nothing re-encoded).
3. **A decline is never memoised** — a bounded `None` belongs to the CALLER's wait
   budget; handing it to a caller that would have waited is the #5004 drop.
4. **The restore lands the vector the row holds** (`record-restore`'s RESTORE half,
   which the #13220 migration header says is open) — through the same
   `node_from_row`/`record_plan` pair the rebuild engine uses, so the assertion is
   on the STATEMENT, not on a helper.
5. **The restore derives where the row holds none** — through the LIVE seam
   (`derived_embedding.derive_row`), unbounded, because a rebuild may not decline
   (§2.0: two restores of one record must agree).
6. **The rulings are not spent**: the `props`-tail refusal still stands, the graph
   copy is still written (nothing here stops it), and no label outside
   `ROW_TEXT_PARTS` gains a vector it never had.

DB-FREE: no connection, no graph, no model — `compute_embedding` is a lambda. So
this rides `TORTOISE_TEST_CARVE_OUT=1`.
"""
from __future__ import annotations

import pytest

import tortoise.derived_embedding as de
import tortoise.embeddings as emb
import tortoise.record_writer as rw
from tortoise.cache_projection import (
    NODE_TABLES,
    RECORD_HELD_VECTOR_COLUMNS,
    UNSERVED_VECTOR_LABELS,
    fill_missing_row_vectors,
    node_from_row,
    record_plan,
)

_DIM = 384


@pytest.fixture()
def model_calls(monkeypatch):
    """Count REAL model calls, and make the embedder 'ready' (nothing to wait for).

    ⛔ The counter is on `compute_embedding` — the model seam — not on
    `encode_for_row`. Counting the gated wrapper would measure how many times the
    seam was ENTERED, which is exactly the thing that was already 2; the claim under
    test is about model work (`embeddings._note_embed_encode`'s own unit).
    """
    calls: list[str] = []
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda text, **kw: calls.append(text) or [0.5] * _DIM)
    monkeypatch.setattr(
        emb.EmbeddingModel, "await_write_ready_reason",
        classmethod(lambda cls, budget: None))
    return calls


def _live_pair(record: dict, *, width: int | None = None) -> tuple[object, object]:
    """The two halves of ONE live write, in the order the doors run them.

    The record's derivation (what `write_live_record` does before any fold) and the
    graph fold's encode (what `_upsert_subject`/`_upsert_object` do). `width` is the
    fold's `required_embedding_dim`; the record always asks `None` (its own
    docstring: a store width is not the record's to invent).
    """
    from_record = rw.derived_embedding_columns(record)
    from_fold = de.encode(record["name"], width, bounded=True)
    return from_record, from_fold


# ════════════════════════════════════════════════════════════════════════════
# 1. One encode per write — COUNTED
# ════════════════════════════════════════════════════════════════════════════

_RECORD = {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}


def test_one_model_call_for_the_pair_inside_the_write_scope(model_calls):
    """The target: a single write pays ONE model call for one text."""
    with de.write_scope("test"):
        from_record, from_fold = _live_pair(_RECORD)
    assert model_calls == ["Acme Corp"], (
        f"a live write paid {len(model_calls)} model call(s) for ONE text: "
        f"{model_calls} — the record's column (#13220) and the graph fold (#7845) "
        f"must share one encode (#13447)")
    assert from_record.columns["embedding"] == [0.5] * _DIM
    assert from_fold == [0.5] * _DIM, (
        "the second caller got a different vector than the first — a memo must "
        "hand back the SAME value, or live and rebuild disagree")


def test_without_a_scope_the_same_pair_pays_twice(model_calls):
    """The counter-test: the pre-#13447 shape, kept so the fix cannot rot silently.

    ⛔ This is not a restatement of the test above — it is the property that makes
    the one above MEANINGFUL. If someone removes `write_scope` from the doors, the
    first test still passes only because the scope is still opened here; if someone
    makes the memo unconditional (a process-wide vector cache, which would be a
    different and riskier change), THIS one fails.
    """
    _live_pair(_RECORD)
    assert len(model_calls) == 2, (
        "outside a write scope the two halves each encode — that is the documented "
        "cost, and the memo is scoped on purpose rather than made global")


def test_the_memo_survives_a_different_store_width(model_calls):
    """The armed-store case: the record asks `None`, the fold asks the store's 384.

    Keying the memo on `(text, width)` would MISS here — i.e. on exactly the
    deployment this issue is about — so the memo is keyed on the TEXT and the width
    is applied to the remembered vector afterwards. That is sound because
    `_degrade_to_width` only DROPS a row, never rescales one: `degrade(raw, W)` is
    the same value a fresh encode at W returns.
    """
    with de.write_scope("test"):
        from_record, from_fold = _live_pair(_RECORD, width=_DIM)
    assert len(model_calls) == 1, model_calls
    assert from_record.columns["embedding"] == [0.5] * _DIM
    assert from_fold == [0.5] * _DIM


#: A document payload, as the record and the fold both receive it.
_DOC = {"type": "DocumentCreated", "id": "doc-1", "title": "My doc",
        "summary": "a summary", "topics": ["topic"]}


def test_a_DOCUMENT_write_pays_ONE_call_too(model_calls):
    """`#13412` landed, so the document pair dedupes — the claim two comments make.

    ⛔ Both `derived_embedding`'s `DocumentCreated` paragraph and
    `projection/entities._upsert_document`'s fold comment assert that a document
    write pays ONE encode, and neither could witness it: the memo is keyed on the
    TEXT, so the pair only dedupes because the record's
    `_document_embedding_parts` CALLS `_build_search_text(title, summary, topics)` —
    the same function the fold passes `st` from. Re-fork that rule back to the
    pre-`#13412` `title + content` and the two legs compose DIFFERENT strings: this
    row reds at 2 calls, and no other row in this file notices (which is why the
    fold comment said "#13412 owns it" while that was true, and why it had to be
    corrected once `#13412` landed — review cycle 8, P3).
    """
    from tortoise.projection.entities import _build_search_text

    with de.write_scope("test"):
        columns = rw.derived_embedding_columns(_DOC)
        fold = de.encode(
            _build_search_text(_DOC["title"], _DOC["summary"], _DOC["topics"]),
            _DIM, bounded=True)
    assert model_calls == ["My doc a summary topic"], (
        f"a DOCUMENT write paid {len(model_calls)} model call(s) {model_calls} — the "
        f"record's document rule and the fold must compose the SAME text (#13412), "
        f"or the text-keyed memo cannot dedupe the pair")
    assert columns.columns["embedding"] == fold == [0.5] * _DIM


def test_a_width_mismatch_still_DROPS_the_vector_for_the_narrower_caller(model_calls):
    """`#4280` is preserved: the memo must not become a width-rule bypass.

    A store declaring 128 cannot hold a 384-wide vector, and `encode_for_row` would
    answer `None` for that caller. The memo hits the same path, so the answer is
    identical — a remembered vector is filtered, never handed over regardless of
    what the caller asked for.
    """
    with de.write_scope("test"):
        from_record = rw.derived_embedding_columns(_RECORD)
        narrow = de.encode(_RECORD["name"], 128, bounded=True)
    assert from_record.columns["embedding"] == [0.5] * _DIM
    assert narrow is None, (
        "a 384-wide vector was written to a store that declares 128 — the memo "
        "bypassed `_degrade_to_width` (#4280)")
    assert len(model_calls) == 1


def test_a_bounded_decline_is_not_memoised(monkeypatch):
    """`#5004`'s direction: a decline is the CALLER's, never inherited.

    The first call declines (its own wait budget ran out); the second would have
    waited. Handing it the `None` would drop a vector this seam has no right to
    drop, so a `None` is never remembered.
    """
    calls: list[str] = []
    monkeypatch.setattr("tortoise.embeddings.compute_embedding",
                        lambda text, **kw: calls.append(text) or [0.5] * _DIM)
    monkeypatch.setattr(
        emb.EmbeddingModel, "await_write_ready_reason",
        classmethod(lambda cls, budget: "budget_expired"))
    with de.write_scope("test"):
        declined = de.encode("Acme Corp", None, bounded=True)
        unbounded = de.encode("Acme Corp", None)
    assert declined is None, "the bounded caller should have declined"
    assert unbounded == [0.5] * _DIM, (
        "an unbounded caller inherited the bounded caller's None — a record-TRUTH "
        "vector would be dropped by a peer's wait budget (#5004)")
    assert len(calls) == 1, calls


def test_the_four_live_doors_carry_the_scope():
    """The doors that write a derived record AND fold it are the ones decorated.

    ⛔ A missing decorator is a COST regression, never a wrong vector — that is why
    this is an enumeration rather than a behavioural assertion per door: the
    behaviour is measured on the real halves above, and what a door contributes is
    exactly "open the scope across the pair".
    """
    from tortoise.api import EventAPI
    from tortoise.mining import ConversationMiner
    from tortoise.sdk import TortoiseSDK

    for door in (EventAPI._emit, ConversationMiner._emit_event,
                 TortoiseSDK._apply_and_journal, TortoiseSDK._create_entity):
        assert getattr(door, "__wrapped__", None) is not None, (
            f"{door.__qualname__} does not carry `one_encode_scope` — that door "
            f"pays two model calls for one text (#13447)")


# ════════════════════════════════════════════════════════════════════════════
# 2. The restore half — a vector the ROW holds reaches the node
# ════════════════════════════════════════════════════════════════════════════

_VECTOR = [0.25] * _DIM


def _row(label: str, **over) -> dict:
    row = {"id": f"{label.lower()}-1", "name": "Acme Corp", "embedding": None,
           "props": {}}
    row.update(over)
    return row


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_a_row_that_holds_a_vector_restores_it(label, model_calls):
    """The RESTORE half the #13220 migration header says is open.

    ⛔ ASSERTED ON THE STATEMENT, not on `node.props`: `record_plan` →
    `node_statement` is what actually runs against the graph, and `embedding` is
    written in its OWN clause (`vecf32($embedding)`, `stored_form.STORED_VECTOR_PROPS`)
    rather than in `$props` — so a view-only assertion would pass while the writer
    dropped it.
    """
    node = node_from_row(label, _row(label, embedding=_VECTOR))
    assert node.props.get("embedding") == _VECTOR, (
        f"a {label} row holding a vector produced no `embedding` prop — the "
        f"projection dropped a value the record holds (#13447)")
    plan = record_plan([node], [])
    cypher = plan.statements[0].cypher
    assert "vecf32($embedding)" in cypher, cypher
    assert plan.statements[0].params["embedding"] == _VECTOR
    assert model_calls == [], (
        "restoring a vector the record already holds must not call a model — that "
        f"is a PUT-BACK, not a re-derivation; got {model_calls}")


@pytest.mark.parametrize("label", ["Object", "Subject"])
def test_a_row_that_holds_NO_vector_derives_it_through_the_LIVE_seam(
        label, model_calls):
    """A NULL row is derived — by the seam, unbounded, so two restores agree.

    Journal replay re-derives this vector in the fold from the payload text
    (#7845), so a `record-restore` that skipped it would land a graph a
    `rebuild_all` of the same journal would not produce — the §2.0 divergence the
    rebuild engines exist to rule out.
    """
    node = node_from_row(label, _row(label))
    assert node.props.get("embedding") is None
    (filled,) = fill_missing_row_vectors((node,), width=None)
    assert filled.props.get("embedding") == [0.5] * _DIM, (
        f"a {label} row with no stored vector restored VECTORLESS — the restore "
        f"half is still open (#13447)")
    assert model_calls == ["Acme Corp"], model_calls
    # The rebuild asks UNBOUNDED: a decline here would make the restore's output a
    # function of wall-clock timing, so two restores of one record could differ.
    plan = record_plan([filled], [])
    assert "vecf32($embedding)" in plan.statements[0].cypher


def test_the_rebuild_does_not_ask_the_write_bound(monkeypatch, model_calls):
    """`bounded=False` on the rebuild path is load-bearing, and this is the witness."""
    consulted: list[float] = []
    monkeypatch.setattr(
        emb.EmbeddingModel, "await_write_ready_reason",
        classmethod(lambda cls, budget: consulted.append(budget) or "budget_expired"))
    node = node_from_row("Object", _row("Object"))
    (filled,) = fill_missing_row_vectors((node,), width=None)
    assert consulted == [], (
        "the restore consulted the LIVE write bound — a rebuild may not decline "
        "(`encode_for_row`'s own note; §2.0 determinism)")
    assert filled.props.get("embedding") == [0.5] * _DIM


def test_an_ARMED_but_unavailable_embedder_does_not_abort_the_restore(monkeypatch):
    """#13447 review cycle 3, issue 4 — a derivation that RAISES is skipped.

    This is the seam's one encode call site that used to be unguarded. Nothing in
    the chain declines quietly when the embedder is ARMED BUT UNAVAILABLE:
    `EmbeddingModel.get()` raises `EmbedderUnavailableError` when
    `TORTOISE_EMBEDDING_MODEL_REQUIRED` is truthy and the model cannot load, and
    `compute_embeddings` calls it OUTSIDE every `try`. Unguarded, that made
    `rebuild_from_record` — and so `python -m tortoise record-restore` — FAIL in
    exactly the state the flag exists to make loud, where the pre-fix path performed
    no encode at all and completed.

    Non-vacuity: with the `try` removed this test reds by RAISING out of
    `fill_missing_row_vectors`; the sibling arms (`record_writer`'s `skipped`, the four
    folds' `except Exception: pass`) are the shape being mirrored.
    """
    from tortoise.exceptions import EmbedderUnavailableError

    def _unavailable(cls, *args, **kwargs):
        raise EmbedderUnavailableError(
            failure_kind="not_installed", model="bge-small-en-v1.5",
            last_error="Explicitly required but unavailable",
            context="compute_embeddings")

    monkeypatch.setattr(emb.EmbeddingModel, "get", classmethod(_unavailable))
    node = node_from_row("Object", _row("Object"))
    assert node.props.get("embedding") is None
    # The rebuild must COMPLETE (a shipped repair path), leaving the row vectorless
    # rather than aborting the whole restore.
    (filled,) = fill_missing_row_vectors((node,), width=None)
    assert filled.props.get("embedding") is None, (
        "a raising embedder produced a vector — impossible, so this arm is not "
        "measuring what it claims")


def test_a_label_with_no_row_text_rule_is_never_invented(model_calls):
    """The scope stays where the gap was: `source`/`event` are not derived here.

    `public.source.embedding` / `public.event.embedding` are ALREADY projected, so a
    row that holds one restores it with no help from this seam; a row that holds
    none is `#13417`'s recording/backfill question. `#13412` and `#13418` widened the
    RECORD's text table (the document text, and a plain `:Source`'s `_searchText`) and
    neither moved this boundary: a rebuild that suddenly derived for those labels
    would change what the two legs return for `:Source`, which is a separate
    decision with its own evidence rather than a side effect of a restore repair.
    """
    assert "Source" not in de.ROW_TEXT_PARTS
    assert "Event" not in de.ROW_TEXT_PARTS
    source = node_from_row("Source", {"id": "s-1", "url": "u", "name": "Doc",
                                      "props": {}})
    (filled,) = fill_missing_row_vectors((source,), width=None)
    assert filled.props.get("embedding") is None
    assert model_calls == []


# ════════════════════════════════════════════════════════════════════════════
# 3. The rulings this change does NOT spend
# ════════════════════════════════════════════════════════════════════════════


def test_the_props_TAIL_still_refuses_the_name():
    """`#13220`'s kept half: the column is a home, the open tail is still closed.

    The two doors read DIFFERENT sets on purpose — `RECORD_HELD_VECTOR_COLUMNS` for
    the column, the raw schema refusal for the tail. Collapsing them would let a
    payload write the record's authority, which is the exact thing the migration
    says it did not relax.
    """
    from tortoise.cache_projection import CacheProjectionError, _refused

    assert "embedding" in _refused("object")
    assert "embedding" in _refused("subject")
    with pytest.raises(CacheProjectionError):
        node_from_row("Object", {"id": "o", "name": "X",
                                 "props": {"embedding": [0.1]}})


def test_both_labels_are_SERVED_so_the_vector_is_readable():
    """A vector no query leg can reach is a vector `#11747` refuses to store.

    `Object`/`Subject` are in `run_vector_query`'s vocabulary, so putting their
    vector back is legitimate; this pins the dependency rather than assuming it — if
    a label ever leaves the served set, `#11747`'s drop must take precedence over
    this seam's put-back, and that conflict should red here first.
    """
    assert {"Object", "Subject"} <= set(NODE_TABLES)
    assert not ({"Object", "Subject"} & set(UNSERVED_VECTOR_LABELS))
    assert set(RECORD_HELD_VECTOR_COLUMNS) == {"object", "subject"}


def test_the_record_side_table_is_the_seams_ONE_copy():
    """No second text rule: the name the docs cite IS the seam's dict."""
    assert rw._RECORD_EMBEDDING_TEXT is de.TEXT_PARTS_BY_RECORD_TYPE
