"""#13220 — the record's own embeddings for the served labels.

WHAT THIS PINS, and each assertion is a property rather than a restatement of the
code:

1. **The schema.** `object`/`subject` carry `embedding` (bindable `jsonb`),
   `embedding_vec` (`vector`, GENERATED) and an HNSW index — parsed out of
   `supabase/migrations/` by the record's OWN readers (`table_columns`,
   `column_types`), not by a second regex written here.
2. **The writer.** `record_writer.derived_embedding_columns` derives the vector
   for `object`/`subject`/`event`/`document` from the SAME text the GRAPH fold
   composes, and derives NOTHING where the graph derives nothing (a plain
   `:Source`, a `meeting` event, a suppressed document, a carried vector the graph
   honours).
3. **The carrier rule.** The derived value lands in the record's COLUMN and never
   in `public.journal_event.payload` — the record's copy of the journal line.
4. **The rulings that are NOT spent.** The graph copy of `:Object`/`:Subject` is
   still written (2026-11-10 KEEP, #11395), the `props` refusal still stands, and
   the dense leg still does NOT serve `object`/`subject` from the record.

DB-FREE: the migrations are read from disk and the plans are built without a
connection, so this rides `TORTOISE_TEST_CARVE_OUT=1`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import tortoise.record_projection as rp
import tortoise.record_vector as rv
import tortoise.record_writer as rw

_MIGRATION = (Path(__file__).resolve().parent.parent
              / "supabase" / "migrations"
              / "20261117000001_record_object_subject_embedding.sql")


# ════════════════════════════════════════════════════════════════════════════
# 1. The schema
# ════════════════════════════════════════════════════════════════════════════


def test_the_migration_declares_the_bindable_column_and_the_generated_vector():
    """Both columns, on both tables, read by the record's own schema readers.

    ⚠️ BOTH halves matter and they are different kinds of column: `embedding` is
    BINDABLE (the writer puts a value in it) and `embedding_vec` is GENERATED
    (Postgres refuses a bind — `record_projection.GENERATED_COLUMNS`). A file
    that declared only the generated one would index nothing, and one that
    declared only the payload would index nothing either.
    """
    assert _MIGRATION.is_file(), f"{_MIGRATION.name} is missing"
    columns = rp.table_columns()
    types = rp.column_types()
    for table in ("object", "subject"):
        assert "embedding" in columns[table], (
            f"{table} carries no `embedding` column — the record cannot index a "
            "fingerprint it does not store (#13220, owner ruling 2026-11-17)")
        assert types[table]["embedding"] == "jsonb", (
            f"{table}.embedding is declared {types[table]['embedding']!r}; the "
            "one converter takes jsonb (a `vector` payload column could not hold "
            "a non-384 caller embedding)")
        assert "embedding_vec" in columns[table]
        assert types[table]["embedding_vec"] == "vector"


def test_both_hnsw_indexes_are_declared_on_the_one_converter():
    """The index names, and that the vector is GENERATED through the ONE converter."""
    text = _MIGRATION.read_text()
    for name, table in (("idx_object_embedding_vec_hnsw", "object"),
                        ("idx_subject_embedding_vec_hnsw", "subject")):
        assert re.search(
            rf"CREATE INDEX IF NOT EXISTS {name}\s+ON public\.{table} USING hnsw "
            rf"\(embedding_vec public\.vector_cosine_ops\)", text), (
            f"{name} is not declared as an hnsw/cosine index on {table} — a "
            "different opclass is a different ranking, and the graph leg it "
            "replaces scored on cosine")
    assert text.count("public.record_embedding_vector(embedding)") == 2, (
        "object/subject must reuse the #11660 converter, not declare a second one")
    assert "CREATE OR REPLACE FUNCTION" not in text, (
        "this migration must not mint a second jsonb->vector converter; "
        "`public.record_embedding_vector` is the ONE (#11642)")


def test_the_props_refusal_still_stands_after_the_column_arrives():
    """The column is the HOME; the open property tail still refuses the name.

    This is the half of §17.5 that the ruling does NOT spend: the refusal is not
    relaxed, so a payload cannot smuggle an `embedding` into `props` now that a
    column exists.

    ⛔ THE NAME IS RESOLVED TO NEITHER THE COLUMN NOR THE TAIL — it is DROPPED,
    and by the arm this PR ADDS rather than by the refusal. The order at this
    head is `GENERATED_COLUMNS` → `_BOOKKEEPING_COLUMNS` → **`DERIVED_COLUMNS`**
    → `column in cols` → `refused_here` → tail, so the `DERIVED_COLUMNS` arm
    fires first and `continue`s: a carried `embedding` never reaches the column,
    which is what makes the column's only writer the record's own derivation
    (the migration states the same rule, "a payload that NAMES `embedding` is
    DROPPED"), and the sibling
    `test_a_PAYLOAD_carried_object_embedding_is_refused` pins that half. The
    `props` CHECK is the second lock on the same door — the first being this
    projection — and the assertions below do not depend on which arm wins.
    """
    refused = rp.refused_props_names()
    assert "embedding" in refused["object"]
    assert "status" in refused["object"], (
        "`Object.status` is a projection-owned cache and keeps its ban — this "
        "change buys a column for `embedding` and nothing else")
    assert "embedding" in refused["subject"]
    assert "status" in refused["subject"]


# ════════════════════════════════════════════════════════════════════════════
# 2. The writer — the text, per label, is the GRAPH fold's text
# ════════════════════════════════════════════════════════════════════════════


class _Recorder:
    """A stand-in for the ONE embedding seam, recording what it was asked to encode."""

    def __init__(self, vector: list[float] | None = None):
        self.calls: list[tuple[str, int | None]] = []
        self.bounded: list[bool] = []
        self.scopes: list[str] = []
        self.vector = vector

    def __call__(self, content: str, expected_dim: int | None = None, *,
                 bounded: bool = False,
                 bounded_scope: str = "graph_fold"):
        # The signature is mirrored EXACTLY, on purpose (#13417): a keyword the
        # real seam gained and this stub did not would otherwise turn every test
        # in the file into "the encode raised" instead of a signature mismatch.
        self.calls.append((content, expected_dim))
        self.bounded.append(bounded)
        self.scopes.append(bounded_scope)
        if self.vector is not None:
            return list(self.vector)
        return [0.125] * 384

    @property
    def text(self) -> str:
        assert len(self.calls) == 1, f"expected ONE encode, got {self.calls}"
        return self.calls[0][0]


@pytest.fixture()
def encoder(monkeypatch):
    import tortoise.embeddings as embeddings

    recorder = _Recorder()
    monkeypatch.setattr(embeddings, "encode_for_row", recorder)
    return recorder


def test_an_object_derives_from_its_name(encoder):
    out = rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"})
    assert encoder.text == "Acme Corp"
    assert set(out.columns) == {"embedding"}
    assert out.columns["embedding"] == [0.125] * 384


def test_a_subject_derives_from_its_name(encoder):
    rw.derived_embedding_columns(
        {"type": "SubjectAdded", "id": "sub-1", "name": "Daniel"})
    assert encoder.text == "Daniel"


def test_an_event_derives_from_subject_kind_and_object(encoder):
    """The graph's own composition, in its own order (`_upsert_event`)."""
    rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1",
        "subject": "Daniel", "eventKind": "met", "object": "Acme Corp",
    })
    assert encoder.text == "Daniel met Acme Corp"


def test_a_mined_event_derives_from_its_NESTED_carrier(encoder):
    """`ConversationMiner` nests under `event`; the fold reads `event.get("event", event)`."""
    rw.derived_embedding_columns({
        "type": "EventRecorded",
        "event": {"eventId": "e1", "subject": "A", "eventKind": "b",
                  "object": "C"},
    })
    assert encoder.text == "A b C"


def test_a_document_derives_from_title_and_content(encoder):
    rw.derived_embedding_columns({
        "type": "DocumentCreated", "url": "https://example.com/a",
        "title": "Q3 pricing", "content": "the memo",
    })
    assert encoder.text == "Q3 pricing the memo"


# ════════════════════════════════════════════════════════════════════════════
# 3. The writer — where the graph derives NOTHING, the record derives nothing
# ════════════════════════════════════════════════════════════════════════════


def test_the_record_leg_declares_its_OWN_scope_not_the_graph_fold(encoder):
    """#13417: the counter's label is the CALLER's fact, and the seam cannot infer it.

    `record_writer` and the graph fold both reach the same bounded seam; if the
    record leg kept the default scope, a record-side decline would be counted as
    `graph_fold:…` and the operator would send it to the BACKFILL, which cannot
    repair it (the repair for a record decline is a re-derivation/replay). The
    kwarg is the only thing that distinguishes them — so it is pinned here, on
    the stub that mirrors the real signature.
    """
    rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"})
    assert encoder.bounded == [True], "the record leg must stay BOUNDED"
    assert encoder.scopes == ["record_derivation"], (
        f"the record leg declared scope {encoder.scopes!r} — a record-side decline "
        f"would be counted under the graph fold's label and reported as "
        f"backfill-repairable, which it is not")


def test_a_plain_source_derives_nothing_because_the_graph_holds_none(encoder):
    """A `SourceCreated` `:Source` carries no vector, because `_upsert_source` holds none.

    Deriving one would make the record's dense leg return sources the graph leg
    never had — the divergence in the other direction, on a label whose
    `document_kind IS NULL` filter is exactly the non-document population.

    ⛔ SCOPED TO THIS EVENT, not to the `:Source` label (#13265 review cycle-7 F2):
    a DOCUMENT-backed `:Source` DOES carry a vector — `_upsert_document` computes
    one (`projection/entities.py:5398-5409`) and writes it (`:5616`,
    `set_embedding_clause("s")`). What holds 0 vectors is the source the
    `SourceCreated` fold writes, because `_upsert_source` has no embedding write
    at all (`:6988`+). Asserting "`:Source` carries 0 embeddings" flatly was the
    over-claim; this docstring is the label claim's corrected form.
    """
    out = rw.derived_embedding_columns(
        {"type": "SourceCreated", "url": "https://example.com/a",
         "name": "some source"})
    assert out.columns == {}
    assert encoder.calls == []


def test_a_meeting_event_derives_nothing(encoder):
    """`_upsert_event` suppresses it (cycle-16: no subject/object ⇒ a junk vector)."""
    out = rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1", "eventKind": "meeting",
        "subject": "", "object": "",
    })
    assert out.columns == {}
    assert encoder.calls == []


def test_a_suppressed_document_derives_nothing(encoder):
    out = rw.derived_embedding_columns({
        "type": "DocumentCreated", "url": "https://example.com/a",
        "title": "t", "content": "c", "suppress_embedding": True,
    })
    assert out.columns == {}
    assert encoder.calls == []


def test_a_carried_event_vector_is_HONOURED_and_not_recomputed(encoder):
    """`_upsert_event` consumes `inner["embedding"]` when present — so must the record."""
    carried = [0.5] * 384
    out = rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1", "subject": "A",
        "eventKind": "b", "object": "C", "embedding": carried,
    })
    assert out.columns == {}
    assert encoder.calls == []


def test_a_carried_object_vector_is_IGNORED_because_the_graph_ignores_it(encoder):
    """`_upsert_object` recomputes unconditionally (#7845) — so must the record.

    ⚠️ Mirroring the graph's *refusal* is the point, not a preference: if the
    record kept a carried vector the node does not have, the two stores would
    disagree about the same object with no error anywhere.
    """
    out = rw.derived_embedding_columns({
        "type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp",
        "embedding": [0.5] * 384,
    })
    assert encoder.text == "Acme Corp"
    assert out.columns["embedding"] == [0.125] * 384


def test_blank_text_derives_nothing(encoder):
    out = rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "   "})
    assert out.columns == {}
    assert encoder.calls == []


def test_a_non_string_part_derives_nothing(encoder):
    """A non-string part refuses on the RECORD side; the graph fold raises.

    ⛔ Not "mirroring the graph", which an earlier cut of this docstring claimed:
    the graph's `" ".join` sits OUTSIDE its `try` (`projection/entities.py`), so
    a non-string part aborts the fold instead of swallowing the vector. The
    record is deliberately the quieter of the two — see `derived_embedding_columns`.
    """
    out = rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1", "subject": ["not", "a", "str"],
        "eventKind": "met", "object": "C",
    })
    assert out.columns == {}
    assert encoder.calls == []


def test_a_FALSY_non_string_part_is_DROPPED_like_the_graph_drops_it(encoder):
    """`filter(None, …)` is the graph's predicate, so `0` is skipped, not refused.

    Pinned because the narrower `is None or value == ""` test REFUSED every
    non-string, and the graph does not: `projection/entities.py::_upsert_event`
    composes `" ".join(filter(None, [subject, eventKind, object]))`, so a falsy
    `subject=0` is dropped and `"met C"` still earns a vector on the node. Under
    the refused form the node carries `e.embedding` while the record's
    `event.embedding` stayed NULL — the #5004 divergence class this table exists
    to prevent, in the one direction a `test_a_non_string_part…` (a TRUTHY list)
    could not see.
    """
    out = rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1", "subject": 0,
        "eventKind": "met", "object": "C",
    })
    assert encoder.text == "met C", (
        "a falsy part must not be joined into the text — the graph's "
        "filter(None, …) drops it")
    assert set(out.columns) == {"embedding"}


def test_an_embedder_that_answers_nothing_derives_nothing(monkeypatch):
    """The #4280 shape — an uninstalled embedder is `None`, never a junk vector."""
    import tortoise.embeddings as embeddings

    monkeypatch.setattr(embeddings, "encode_for_row", lambda c, d=None, **k: None)
    out = rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"})
    assert out.columns == {}


def test_an_encode_that_raises_derives_nothing(monkeypatch):
    """Every failure is "this row has no vector", never a refused write."""
    import tortoise.embeddings as embeddings

    def _boom(content, expected_dim=None, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(embeddings, "encode_for_row", _boom)
    out = rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"})
    assert out.columns == {}


def test_the_width_is_NOT_the_encoders_business(encoder):
    """The width is the CONVERTER's, and the WAIT is bounded (`#13265` cycle-2 P1).

    Two separate claims about the one seam call, both load-bearing and both
    invisible to every other test here:

    * `expected_dim=None` — passing `required_embedding_dim` would make
      `encode_for_row` DROP a vector the graph holds whenever the store's
      embedder is not 384-wide — a second width rule in the record, which is the
      §12.2a defect this change must not create.
    * `bounded=True` — this call runs on the CALLER's thread, inside
      `write_live_record`, BEFORE any cache fold, so an unbounded wait for a cold
      embedder stalls the user's request: `get()` blocks up to
      `_LOAD_TIMEOUT_S` (90 s) and, in a warm window, on the load another thread
      is already running (measured 504 at 18.4 s, #13224/#13229). The graph's own
      folds already opt in; without this keyword the record's duplicate encode
      re-introduces the stall the graph bounded.

    The bound is safe for this column on the reasoning that makes the derived
    folds the only opt-in: the vector is a DERIVED copy of the journal payload, so
    a live decline is repairable by `replay(derived=…)`, and `_in_replay_scope()`
    suppresses the bound inside a replay window, so parity holds on both sides.
    """
    rw.derived_embedding_columns(
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"})
    assert encoder.calls == [("Acme Corp", None)], encoder.calls
    assert encoder.bounded == [True], (
        "the record's derivation took an UNBOUNDED embedder wait — it runs on the "
        "caller's request thread before any cache fold, so a cold embedder would "
        "stall live writes for up to `_LOAD_TIMEOUT_S` (90 s). See #13224/#13229 "
        "for the graph's own `bounded=True` opt-in and the measured 18.4 s 504")


# ════════════════════════════════════════════════════════════════════════════
# 4. The carrier rule — the derived value is a COLUMN, never the journal copy
# ════════════════════════════════════════════════════════════════════════════


def _object_plan(vector: list[float]):
    record = {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}
    derived = {0: {"embedding": vector}}
    return record, rp.replay_plan([record], org_id="org", graph_id="g",
                                  derived=derived)


def test_the_derived_vector_lands_in_the_object_COLUMN(tmp_path):
    vector = [0.75] * 384
    _, plan = _object_plan(vector)
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.object ")]
    assert len(upserts) == 1
    assert "embedding" in upserts[0].param_columns
    bound = upserts[0].params[upserts[0].param_columns.index("embedding")]
    # ⚠️ The column is `jsonb`, so the writer hands the driver the JSON TEXT
    # (`_jsonb_or_scalar`) rather than a Python list, which the driver would
    # adapt as a Postgres ARRAY. Asserting the encoded form is the point: a list
    # reaching this parameter is the #9548 row-drop class.
    assert isinstance(bound, str), (
        f"the {type(bound).__name__} vector was bound directly to a jsonb column")
    assert bound.startswith("[0.75, 0.75,"), bound[:40]
    assert bound.count("0.75") == 384


def test_the_journal_copy_of_the_record_is_VERBATIM():
    """⛔ The derived value must NOT enter `public.journal_event.payload`.

    That row is the record's copy of the JOURNAL line. A value the record
    invented would make a record-sourced rebuild carry a vector a JSONL rebuild
    has to derive — two rebuild routes that disagree after an embedder rotation.
    """
    record, plan = _object_plan([0.75] * 384)
    journalled = [s for s in plan
                  if s.sql.startswith("INSERT INTO public.journal_event")]
    assert journalled, "the plan carries no journal position row to check"
    blob = repr(journalled[0].params)
    assert "0.75" not in blob, (
        "the derived embedding leaked into journal_event.payload — the journal "
        "copy must stay verbatim")
    # …and the payload really is the caller's record, so the assertion above is
    # not passing over an empty statement.
    assert "Acme Corp" in blob or "obj-1" in blob
    assert record == {"type": "ObjectRegistered", "id": "obj-1",
                      "name": "Acme Corp"}, "the caller's record was mutated"


# ════════════════════════════════════════════════════════════════════════════
# 4b. The SEAM that joins the two halves — `RecordWriter.write` → `replay`
# ════════════════════════════════════════════════════════════════════════════


def test_the_live_seam_threads_the_derived_columns_into_replay(encoder, monkeypatch):
    """The join between the halves, pinned on its own (review of #13265, P2).

    ⛔ WHY THIS EXISTS SEPARATELY FROM EVERYTHING ABOVE. Every other test here
    exercises ONE half in isolation — `derived_embedding_columns(...)` called
    directly, or `replay_plan(..., derived={0: …})` fed by hand. So the three
    lines in `RecordWriter.write` that JOIN them
    (`won = derived_embedding_columns(record)` / `derived[index] = won.columns` /
    `derived=derived or None`) could be deleted with this whole file still green,
    and the outcome would be two declared, HNSW-indexed columns that nothing
    fills — the state `20261117000001` itself calls worse than an absence. This
    drives the REAL seam and spies the ONE projection call it makes, so deleting
    the join reds HERE instead of shipping silently.

    No database: the connection is the sibling seam suite's recording fake and
    `position=False`, so the byte meter (`_measure_written_bytes` skips a replay
    by its own correctness gate — metering a re-materialisation would double the
    org's figure) never reaches a driver.
    """
    from tests.test_record_writer_seam import FakeConnection

    seen: dict[str, object] = {}

    def _spy(records, conn, **kwargs):
        seen["records"] = list(records)
        seen["derived"] = kwargs.get("derived")
        # The REAL report type, not a fake: `write` reads `.events`,
        # `.statements`, `.first_seq` and `.last_seq` off it, so a stand-in here
        # would rot silently the day that contract grows a field.
        return rp.ReplayReport(events=1, statements=1, first_seq=1, last_seq=1)

    monkeypatch.setattr(rw.record_projection, "replay", _spy)
    # ⛔ TWO records, and the FIRST one derives nothing on purpose (review
    # cycle-2 P2): with a single record every index IS 0, so `derived[index] ==
    # derived[0]` and the map's KEY went unpinned — measured, mutating
    # `derived[index] = …` to `derived[0] = …` left this whole file green while
    # the real two-record plan bound the Object's fingerprint on the SOURCE row.
    # `SourceCreated` has no text of its own, so it contributes no entry and the
    # surviving key is 1.
    rw.RecordWriter(dsn="postgresql://record/db",
                    connect=lambda _dsn: FakeConnection()).write(
        [{"type": "SourceCreated", "id": "src-1", "title": ""},
         {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}],
        org_id="org", graph_id="g", position=False)

    assert seen["records"] == [
        {"type": "SourceCreated", "id": "src-1", "title": ""},
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}], (
        "the spy did not receive the caller's records — the assertion below would "
        "pass over a call that never happened")
    derived = seen["derived"]
    assert derived is not None, (
        "the seam passed `derived=None`: `derived_embedding_columns`' result is "
        "computed and then DROPPED, so the two fingerprint columns would never "
        "be filled on the live path")
    assert set(derived) == {1}, (
        f"expected exactly the SECOND record's entry, got {sorted(derived)} — "
        "`derived[0]` would put the Object's fingerprint on the `SourceCreated` "
        "record, i.e. bind it to `public.source` instead of `public.object`")
    assert derived[1] == {"embedding": [0.125] * 384}, derived


def test_replay_forwards_the_derived_columns_to_the_PLAN(monkeypatch):
    """The junction below the live seam, which had no witness (review of #13265 P2).

    ⛔ MEASURED GAP: `replay()` forwards `derived=derived` into `replay_plan(`,
    and DELETING that kwarg reds THIS test and nothing else — every other test in
    `test_13220_record_label_embeddings.py test_record_schema_placement.py
    test_cache_projection.py test_record_projection.py` stays GREEN (the whole
    four-file set passes without the forward). The seam test spies
    `record_projection.replay` (so it stops one call short) and
    `test_the_derived_vector_lands_in_the_object_COLUMN` calls `replay_plan`
    directly — both ends pinned, the wire between them not. With that kwarg gone
    the live path binds nothing and both HNSW-indexed fingerprint columns stay
    empty while every test is green: the same class as the finding the seam test
    exists for, one call deeper.

    The spy RAISES on arrival instead of returning a plan: everything after this
    call in `replay()` needs a real connection (and, on the `allocate=True` arm,
    a live allocator — this test passes `allocate=False`, so it is the connection
    that would be needed), and the fact under test is the FORWARD, not the
    execution.
    """
    seen: dict[str, object] = {}

    class _Stop(Exception):
        """Deliberate: this asserts the call, not the execution."""

    def _spy(records, **kwargs):
        seen["records"] = list(records)
        seen.update(kwargs)
        raise _Stop

    monkeypatch.setattr(rp, "replay_plan", _spy)
    derived = {0: {"embedding": [0.125] * 384}}
    with pytest.raises(_Stop):
        rp.replay(
            [{"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}],
            None, org_id="org", graph_id="g", position=False, derived=derived)

    assert seen["records"] == [
        {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}], (
        "the spy did not receive the caller's records — the assertion below would "
        "pass over a call that never happened")
    assert seen.get("derived") == derived, (
        "`replay()` did NOT forward `derived` to `replay_plan`: the record's "
        "derived columns are computed, handed to the seam, then dropped one call "
        "deeper, so both HNSW-indexed fingerprint columns stay empty on the live "
        f"path. Got: {seen.get('derived')!r}")


def test_a_label_with_no_rule_binds_no_embedding_column():
    """Without `derived`, the plan is byte-identical to `main`'s."""
    record = {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp"}
    plan = rp.replay_plan([record], org_id="org", graph_id="g")
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.object ")]
    assert "embedding" not in upserts[0].param_columns


def test_a_PAYLOAD_carried_object_embedding_is_refused():
    """The column exists; the payload still may not write it (#13220).

    ⛔ This is the half of §17.5 the ruling did NOT spend, and it needs a pin of
    its own because the two rules now overlap: without
    `DERIVED_COLUMNS` the column alone made an arbitrary carried
    vector storable as record AUTHORITY, which is exactly what the carve-out
    exists to prevent (and what `test_record_projection.py::test_no_derived_or_
    volatile_property_is_ever_stored` reds on). The graph fold ignores a carried
    Object vector for the same reason (`_upsert_object` recomputes from `name`).
    """
    record = {"type": "ObjectRegistered", "id": "obj-1", "name": "Acme Corp",
              "embedding": [0.5] * 384}
    plan = rp.replay_plan([record], org_id="org", graph_id="g")
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.object ")]
    assert "embedding" not in upserts[0].param_columns, (
        "a payload-carried `embedding` reached the object column — a DERIVED "
        "value just became record authority (§2.4/§17.5)")
    # …and it did not slip into the props tail either, where the CHECK would
    # refuse it only at execution time.
    tail = [p for p in upserts[0].params
            if isinstance(p, str) and p.startswith("{")]
    assert not any("embedding" in t for t in tail), tail


def test_a_PAYLOAD_carried_EVENT_embedding_is_still_stored():
    """`event.embedding` is a PRIMARY property — a carried vector IS the value.

    ⚠️ The mirror of the test above, and the reason the refusal is a per-table
    map rather than a rule about the name: `_upsert_event` consumes
    `inner["embedding"]` when present, so refusing a carried event vector in the
    record would make the record POORER than the graph — Interface A's failure
    in the other direction.
    """
    carried = [0.375] * 384
    record = {"type": "EventRecorded", "eventId": "e1", "subject": "A",
              "eventKind": "b", "object": "C", "embedding": carried}
    plan = rp.replay_plan([record], org_id="org", graph_id="g")
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.event ")]
    assert "embedding" in upserts[0].param_columns
    bound = upserts[0].params[upserts[0].param_columns.index("embedding")]
    assert isinstance(bound, str) and bound.startswith("[0.375")


# ════════════════════════════════════════════════════════════════════════════
# 5. The rulings this change does NOT spend
# ════════════════════════════════════════════════════════════════════════════


def test_object_and_subject_still_have_no_record_served_dense_leg():
    """⛔ WITHHELD ON PURPOSE — the record cannot apply the `#689` cage.

    `search_engine._STATUS_FILTERED_LABELS` is ``("Point", "Object", "Subject")``
    and `record_vector._status_predicates` applies that exclusion on the record
    leg "because the graph branches do". The record has no `status` column for
    these two labels (`OpDispatch.never_store`, §2.6) and its procedural fold
    (`public.object_current_status`) answers only `live`/`superseded` — while the
    graph's vocabulary has four terminal members (deprecated/archived/retracted
    arrive as `EntityMutated op=restatus` and the record DROPS them). Serving them
    from the record today would return entities the graph leg hides: the `#689`
    loophole on the branch that would carry production traffic. The COLUMN is
    what this change owes; the leg is blocked on the status cage.
    """
    assert "object" not in rv.LABEL_SERVED
    assert "subject" not in rv.LABEL_SERVED
    assert "event" in rv.LABEL_SERVED
    assert "document" in rv.LABEL_SERVED and "source" in rv.LABEL_SERVED


def test_the_graph_still_writes_object_and_subject_embeddings():
    """The 2026-11-10 KEEP ruling (#11395): no pass may stop writing them.

    Pinned against the graph fold's SOURCE rather than against a behaviour,
    because the behaviour needs a store and the ruling is about the write
    existing at all. If a future lane reads "retire the expensive parts from
    FalkorDB" as covering these two labels, this reds and names the ruling.
    """
    entities = (Path(__file__).resolve().parent.parent
                / "tortoise" / "projection" / "entities.py").read_text()
    for fn in ("def _upsert_object", "def _upsert_subject"):
        start = entities.index(fn)
        body = entities[start:start + 6000]
        assert "encode_for_row(" in body, (
            f"{fn} no longer encodes a vector — the 2026-11-10 KEEP ruling "
            "(#11395 comment 6781921075) forbids removing `:Object`/`:Subject` "
            "embeddings or stopping their write; this needs an explicit reopen, "
            "not a refactor")


def test_a_document_backed_source_DOES_carry_a_vector_while_a_plain_one_does_not():
    """The `:Source` label is written by TWO writers that disagree on embedding.

    `#13265` review cycle-7 F2: the writer's docstrings said "`:Source` carries 0
    embeddings", which is true only of the `SourceCreated` fold. `_upsert_document`
    is a `:Source` writer too (ONTOLOGY §4.4 / D10: a document IS a `:Source`) and
    it computes a vector and writes it with `set_embedding_clause("s")`, so a
    document-backed source carries one. Pinned against the graph fold's SOURCE for
    the same reason the KEEP-ruling test above is: the distinct claim is about
    which writer emits the write, and a behaviour test would need a store.
    """
    entities = (Path(__file__).resolve().parent.parent
                / "tortoise" / "projection" / "entities.py").read_text()
    doc = entities[entities.index("def _upsert_document"):]
    doc_body = doc[:doc.index("\n    def ", 1)] if "\n    def " in doc[1:] else doc
    assert "set_embedding_clause(\"s\")" in doc_body, (
        "_upsert_document no longer writes `s.embedding` — if that write is gone, "
        "a document-backed `:Source` no longer carries a vector and the scoped "
        "docstrings in this file (and the writer's) must be re-measured, not "
        "left claiming the opposite")
    source = entities[entities.index("def _upsert_source"):]
    source_body = source[:source.index("\n    def ", 1)] if "\n    def " in source[1:] else source
    # The WRITE form only, never the bare word: `_upsert_source`'s own comment says
    # "Identity is a canonicalised URL — NEVER an embedding (STORAGE §9.4)", and a
    # substring check on "embedding" read that PROSE as a write (measured while
    # writing this test — the assertion failed on its own comment).
    assert not re.search(r'set_embedding_clause\(|s\.embedding\s*=', source_body), (
        "_upsert_source now writes an embedding — then the `SourceCreated` parity "
        "assertions in this file (and `derived_embedding_columns`'s 'no vector "
        "for a source' arm) are stale and this needs a re-measure, not a quiet pass")


def test_the_record_still_binds_a_plain_source_without_a_vector():
    """Parity for the `SourceCreated` source, which the graph holds 0 vectors for,
    at the plan level. (A document-backed `:Source` is a different writer and
    DOES carry one — `_upsert_document`, see the docstring above.)
    """
    record = {"type": "SourceCreated", "url": "https://example.com/a",
              "name": "a source"}
    derived = rw.derived_embedding_columns(record)
    assert derived.columns == {}
    plan = rp.replay_plan([record], org_id="org", graph_id="g",
                          derived={0: derived.columns})
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.source ")]
    assert "embedding" not in upserts[0].param_columns
