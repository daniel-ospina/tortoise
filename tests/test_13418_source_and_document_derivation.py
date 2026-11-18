"""#13418 + #13412 — the RECORD's derivation for `:Source` and for a document.

Two defects, one seam, one table (`record_writer._RECORD_EMBEDDING_TEXT`):

1. **#13418 — a plain `:Source` derived no vector at all.** The table had no
   `SourceCreated` key, so a source's record row never received one while the
   label IS served from the record (`record_vector.LABEL_SERVED["source"]`,
   `document_kind IS NULL`) — i.e. the label the dense leg answers from Postgres
   had no writer on that leg. The graph fold computes no vector for a plain
   `:Source` either (`_upsert_source` has no embedding write), so this is not a
   two-store disagreement: it is an absence on BOTH sides that the record now
   fills, per §14.3 V2 (`:Source` gets a vector, in Postgres).

2. **#13412 — a document's two stores encoded DIFFERENT text.** After #8419/PR
   #13290 the graph composes the document vector's input from
   `_build_search_text(title, summary, topics)` and writes it to `s._searchText`
   (`projection/entities.py:5423`, `:5530`), while the record still folded
   `title + content` — two different texts, two different vectors, for one row.
   `content` is not even a graph property post-D10 (`_DOC_RETIRED_KEYS`).

WHAT MAKES THESE TESTS FAIL ON THE OLD TREE, rather than merely pass on the new
one:

* the document test asserts the text equals `projection.entities.
  _build_search_text(...)` — the GRAPH's function, imported here — and asserts
  the payload's `content` is ABSENT from it. On the old table the record joined
  `title + content`, so both halves red;
* the plain-source test asserts a non-empty vector where the old table answered
  `no embedding rule for this record type`;
* the parity test below mutates the graph's OWN composer and requires the
  record's text to follow it — a copy of the composition inside `record_writer`
  cannot pass that, which is the drift #13412 exists to make impossible.

DB-FREE: the plans are built without a connection and the embedder is a stub, so
this rides `TORTOISE_TEST_CARVE_OUT=1` like its sibling
`tests/test_13220_record_label_embeddings.py`.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import tortoise.record_projection as rp
import tortoise.record_vector as rv
import tortoise.record_writer as rw

_ENTITIES = (Path(__file__).resolve().parent.parent
             / "tortoise" / "projection" / "entities.py")


# ════════════════════════════════════════════════════════════════════════════
# The one embedding seam, stubbed (signature mirrored EXACTLY, #13417)
# ════════════════════════════════════════════════════════════════════════════


class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, int | None]] = []

    def __call__(self, content: str, expected_dim: int | None = None, *,
                 bounded: bool = False,
                 bounded_scope: str = "graph_fold"):
        self.calls.append((content, expected_dim))
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


# ════════════════════════════════════════════════════════════════════════════
# #13412 — a document's text is the GRAPH's text, and `content` is not in it
# ════════════════════════════════════════════════════════════════════════════


def test_a_document_derives_from_the_graphs_search_text(encoder):
    """`_searchText` = title + summary + topics — the surface the node holds.

    `_upsert_document` computes `st = _build_search_text(title, summary, topics)`
    on ONE line and reads it TWICE — for the vector and for `s._searchText`
    (`projection/entities.py:5423`, `:5530`) — so the graph's dense and sparse
    legs describe the same text by construction. The record must encode that
    text and no other, or one journal line yields two vectors (#13412).
    """
    from tortoise.projection.entities import _build_search_text

    out = rw.derived_embedding_columns({
        "type": "DocumentCreated",
        "url": "https://example.com/q3",
        "title": "Q3 pricing",
        "summary": "the memo",
        "topics": ["pricing", "q3"],
        "content": "the long body text",
    })
    assert encoder.text == _build_search_text(
        "Q3 pricing", "the memo", ["pricing", "q3"]), (
        "the record encoded a different text than the graph's `_searchText` "
        "composition for the same DocumentCreated payload (#13412)")
    assert "the long body text" not in encoder.text, (
        "`content` reached the record's vector: it is not a graph property "
        "post-D10 (`_DOC_RETIRED_KEYS`) and the graph stopped embedding it in "
        "#8419, so a vector computed from it is derived from text the node "
        "never holds — the divergence #13412 measures")
    assert set(out.columns) == {"embedding"}


def test_the_document_composition_IS_the_graphs_function_not_a_copy(encoder, monkeypatch):
    """Mutate the graph's composer; the record's text must follow it.

    This is the anti-drift pin. A re-implemented `" ".join(filter(None, …))` in
    `record_writer` would produce the same bytes as long as nobody changed
    either side — which is exactly how `title + content` survived #8419
    unnoticed. Here the GRAPH's function is replaced with a sentinel, so only a
    call THROUGH it can produce the sentinel text.
    """
    import tortoise.projection.entities as entities

    monkeypatch.setattr(entities, "_build_search_text",
                        lambda title, summary=None, topics=None: "SENTINEL")
    rw.derived_embedding_columns({
        "type": "DocumentCreated", "url": "https://example.com/a",
        "title": "t", "summary": "s", "topics": ["x"],
    })
    assert encoder.text == "SENTINEL", (
        "the record composed the document text itself instead of calling "
        "`projection.entities._build_search_text` — a second copy of the "
        "composition is the defect #13412 exists to prevent")


def test_the_document_rule_names_the_graphs_three_fields_and_not_content():
    """The declaration a shared seam (#13447) reads: which payload fields compose it."""
    rule = rw._RECORD_EMBEDDING_TEXT["DocumentCreated"]
    assert rule.fields == ("title", "summary", "topics"), (
        f"the document rule declares {rule.fields!r}; `_build_search_text` "
        "composes title + summary + topics and `content` is retired (#13412)")
    assert "content" not in rule.fields


# ════════════════════════════════════════════════════════════════════════════
# #13418 — a plain :Source derives from the text the NODE holds
# ════════════════════════════════════════════════════════════════════════════


def test_a_plain_source_derives_from_the_search_text_the_node_holds(encoder):
    """`s._searchText = ev._searchText or ev.title` — `_upsert_source`'s own line.

    `projection/entities.py:7106` composes the source node's FTS surface as
    `ev.get("_searchText") or ev.get("title")` and writes it at `:7194`
    (`s._searchText = $st`). The record derives from THAT expression: same
    datum, same text, so the source's sparse and dense legs agree exactly as a
    document's do after #8419.
    """
    out = rw.derived_embedding_columns({
        "type": "SourceCreated",
        "url": "https://example.com/pricing",
        "title": "Acme pricing page",
        "_searchText": "acme pricing discounts renewal",
    })
    assert encoder.text == "acme pricing discounts renewal", (
        "a plain `:Source` must encode the text its node holds in "
        "`_searchText` (`_upsert_source`, #13418)")
    assert set(out.columns) == {"embedding"}


def test_a_plain_source_falls_back_to_its_title(encoder):
    """No `_searchText` ⇒ `title`, the same `or` arm the graph takes."""
    rw.derived_embedding_columns({
        "type": "SourceCreated", "url": "https://example.com/a",
        "title": "Acme pricing page",
    })
    assert encoder.text == "Acme pricing page"


def test_a_plain_source_never_embeds_its_IDENTITY(encoder):
    """STORAGE §9.4 ③: *"NEVER use a vector to decide whether two sources are the same"*.

    Identity is the canonicalised URL plus a content hash; `url` is never part of
    the embedded text. Pinned because the obvious shortcut for "a Source has no
    text" is to embed its id, which would make every `session:`/URL-keyed source
    encode its own identity as meaning.
    """
    rw.derived_embedding_columns({
        "type": "SourceCreated", "url": "https://example.com/pricing",
        "title": "Acme pricing page",
    })
    assert "example.com" not in encoder.text


def test_a_plain_source_with_no_text_derives_nothing(encoder):
    """No `_searchText`, no `title` ⇒ blank text ⇒ no vector (the graph's own arm)."""
    out = rw.derived_embedding_columns({
        "type": "SourceCreated", "url": "https://example.com/a"})
    assert out.columns == {}
    assert encoder.calls == []


def test_a_session_source_derives_NOTHING_and_says_why(encoder):
    """§9.4: a session `:Source`'s vector text is the S1 narrative — never `summary`.

    `STORAGE-ARCHITECTURE.md` §9.4 says it in one sentence: *"one vector attached
    to the `:Source` node, whose embedded text is the S1 narrative read from
    `public.session_narrative` — ⛔ never from `:Source.summary`"*. That narrative
    lives in the record's own table (`#9555`), NOT in the journal line this fold
    reads, so the derivation cannot compose it — and encoding the node's topic
    LEXICON instead (which is what `_searchText` holds for a session, `#10000`)
    would write the wrong text into a column the narrative arm must later own.
    The skip is therefore the decision-preserving answer, and it is NAMED rather
    than silent.
    """
    out = rw.derived_embedding_columns({
        "type": "SourceCreated",
        "url": "session:abc123",
        "sourceKind": "agentSession",
        "title": None,
        "_searchText": "pricing, discount, renewal",
        "summary": "session capture topics: pricing, discount, renewal",
        "topics": ["pricing", "discount", "renewal"],
        "sessionId": "abc123",
    })
    assert out.columns == {}, (
        "a session `:Source` derived a vector from the journal line — its text "
        "is the S1 narrative (§9.4), which this fold cannot read")
    assert encoder.calls == []
    assert "narrative" in out.skipped, (
        f"the skip must name §9.4's rule, not read as 'no rule for this type' "
        f"(got {out.skipped!r})")


def test_a_carried_source_vector_is_HONOURED_and_not_recomputed(encoder):
    """`_upsert_source` computes none, so a carried vector is the node's own.

    `_SOURCE_EXTRA_PROPS` (`projection/entities.py:2111`) declares `embedding` a
    caller passthrough, so a caller-supplied vector lands on the node. Deriving
    over it in the record would give the two stores different vectors for the
    same source; the `EventRecorded` precedent is the same shape.
    """
    out = rw.derived_embedding_columns({
        "type": "SourceCreated", "url": "https://example.com/a",
        "title": "Acme pricing page", "embedding": [0.5] * 384,
    })
    assert out.columns == {}
    assert encoder.calls == []


def test_a_source_written_to_an_armed_record_CARRIES_a_vector(encoder):
    """The #13418 indicator, at the plan level: `public.source.embedding` is bound.

    Drives the real writer seam (`derived_embedding_columns` → `replay_plan`), so
    the value is asserted where it lands — the `INSERT INTO public.source` — and
    in the JSON-text form a `jsonb` column requires (`_jsonb_or_scalar`), not as
    a Python list the driver would adapt as an ARRAY.
    """
    record = {"type": "SourceCreated", "url": "https://example.com/pricing",
              "title": "Acme pricing page"}
    derived = rw.derived_embedding_columns(record)
    assert derived.columns, (
        "the record derived no embedding for a plain `:Source` — the served "
        "label (`LABEL_SERVED['source']`) would answer from a column nothing "
        "writes (#13418)")
    plan = rp.replay_plan([record], org_id="org", graph_id="g",
                          derived={0: derived.columns})
    upserts = [s for s in plan if s.sql.startswith("INSERT INTO public.source ")]
    assert len(upserts) == 1
    assert "embedding" in upserts[0].param_columns, (
        "the derived vector never reached the `public.source` INSERT")
    bound = upserts[0].params[upserts[0].param_columns.index("embedding")]
    assert isinstance(bound, str), (
        f"the {type(bound).__name__} vector was bound directly to a jsonb column")
    assert bound.startswith("[0.125, 0.125,"), bound[:40]
    assert bound.count("0.125") == 384


# ════════════════════════════════════════════════════════════════════════════
# The scope of the table: every served label has a writer
# ════════════════════════════════════════════════════════════════════════════


def test_every_served_source_side_label_has_a_derivation_rule():
    """#13418's indicator: `public.source.embedding` is written for what is served.

    `record_vector.LABEL_SERVED` maps the labels the dense leg answers from the
    RECORD. `source` and `document` are two filters over ONE table
    (`public.source`), and `event` is its own — so all three need a rule here.
    `point`/`operator` live on `public.point`, whose vector is record-TRUTH
    written by the Point path (`#5004`), not by this table.
    """
    assert {"source", "document", "event"} <= set(rv.LABEL_SERVED)
    assert {"SourceCreated", "DocumentCreated", "EventRecorded"} <= set(
        rw._RECORD_EMBEDDING_TEXT), (
        "a served label's record row has no derivation rule — the `#13220` "
        "scope sentence ('every served label's embeddings live in Postgres') "
        "has no writer behind it")


# ════════════════════════════════════════════════════════════════════════════
# :Event — covered by the SAME rule, pinned against the graph fold's source
# ════════════════════════════════════════════════════════════════════════════


def test_the_event_rule_is_the_graph_folds_own_composition():
    """`:Event` shares this seam already — pinned so a fourth rule cannot appear.

    The owner's 2026-11-18 ruling puts `:Event` and `:Source` on the same
    treatment as `:Object`/`:Subject`. For `:Event` that was already true: the
    graph composes `" ".join(filter(None, [subject, eventKind, object]))`
    (`projection/entities.py:5803-5807`) and the record's rule names exactly
    those three keys in that order. This reads the GRAPH's own source rather than
    restating the literal, so a change on either side reds.
    """
    text = _ENTITIES.read_text()
    start = text.index('event_content = " ".join(filter(None, [')
    block = text[start:text.index("]))", start)]
    keys = re.findall(r'inner\.get\("(\w+)"', block)
    assert keys == ["subject", "eventKind", "object"], (
        f"the graph's event composition changed to {keys!r} — the record's rule "
        "must be re-derived from it, not left on the old keys")
    rule = rw._RECORD_EMBEDDING_TEXT["EventRecorded"]
    assert rule.fields == tuple(keys), (
        f"the record's event rule declares {rule.fields!r} while the graph "
        f"composes {keys!r} — two stores, two texts")


def test_an_event_still_derives_the_graphs_text(encoder):
    rw.derived_embedding_columns({
        "type": "EventRecorded", "eventId": "e1",
        "subject": "Daniel", "eventKind": "met", "object": "Acme Corp",
    })
    assert encoder.text == "Daniel met Acme Corp"
