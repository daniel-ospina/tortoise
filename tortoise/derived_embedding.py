"""#13447 — the ONE derive-and-encode seam for DERIVED-entity rows.

**One cause, two symptoms.** An `:Object`/`:Subject`/`:Event`/`:Document` vector is
DERIVED: it is a pure function of text the row already carries (`name`;
`subject`+`eventKind`+`object`; the graph's own `_search_text` composition). That
fact is the same on the live write and on a rebuild, so anything that computes it
must be the same code — and it was not:

* the LIVE write encoded the text **twice** — once in
  :func:`tortoise.record_writer.derived_embedding_columns` (the record's column,
  #13220) and again in the graph fold (`projection/entities.py::_upsert_object` /
  `::_upsert_subject` / the document and Event arms). `encode_for_row` memoises
  nothing, so one text cost two model calls;
* `python -m tortoise record-restore` encoded **nothing** and could not put a
  vector back at all: `cache_projection._props_from_row` drops `embedding` by
  name for `object`/`subject` (the schema's `props` refusal), so the row's own
  vector never reached the node.

Both are the same missing seam. This module is it, with exactly two callers
shapes:

1. the **live write**, through :func:`compose` (the ONE per-label text rule) and
   :func:`encode` (the ONE encode step);
2. the **rebuild**, through :func:`derive_row` (a record ROW → its vector), called
   by `cache_projection.fill_missing_row_vectors` on the `record-restore` path.

⛔ WHY NOT TWO IMPLEMENTATIONS KEPT IN STEP. The record's vector and the node's
vector for one write are the SAME function of the SAME datum — that is what
`tests/test_13220_record_label_embeddings.py` pins, and what
`supabase/migrations/20261117000001_record_object_subject_embedding.sql` relies on
when it calls the column "recomputable rather than authority". Two encoders that
must agree are two rules that WILL disagree (the §12.2a "second rule set" defect,
and the live≠replay divergence class it produces); one seam cannot.

⛔ `bounded` IS NOT TOUCHED HERE. `#13224`'s write bound exists so a live request
does not wait for a COLD embedder; `#5004` (PRESENCE IS OWNERSHIP) says a
record-TRUTH vector — a `Point`'s, stamped on the journal — must never be dropped,
because a cold `None` there is a recorded CLEAR that a rebuild reproduces and no
graph-side backfill can reach (`embeddings.encode_for_row`'s own `bounded` note).
Every DERIVED arm keeps the bound it had: `True` on the live doors, and `False` on
the rebuild (`_rebuild_decides` below), where a decline would make two rebuilds of
one record disagree. This module changes **how many times** a text is encoded, never
**whether** an encode may be declined.

⛔ NOT a placement change. The graph copy of `:Object`/`:Subject` is still written
(the 2026-11-10 KEEP ruling, #11395); `#13220`'s column stays in Postgres and the
vector is not re-copied into the graph as a second authority.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ROW_TEXT_PARTS",
    "TEXT_PARTS_BY_RECORD_TYPE",
    "ComposedText",
    "EmbeddingTextParts",
    "compose",
    "compose_for_row",
    "derive_row",
    "encode",
    "one_encode_scope",
    "reset_memo_for_tests",
    "write_scope",
]

_logger = logging.getLogger(__name__)

#: One record type's embedding text, as the `(name, value)` parts the text is
#: composed from, in composition order. A table of CALLABLES rather than of field
#: names: every composition here has its own shape (`DocumentCreated` calls a
#: function whose third argument is a LIST, `EventRecorded` joins three parts with
#: falsy ones dropped), so a field-name list would describe the datum without being
#: able to produce it — and a seam that joined such a list (`#13447`) would
#: stringify a document's topics and keep an event's empty ones. The callable IS
#: the plug point, and it returns the COMPOSITION and nothing else: the arms that
#: REFUSE to derive (a carried vector, a session `:Source`, a source record that
#: states no text — `record_writer.derived_embedding_columns`) are the CALLER's, so a
#: consumer of this table must apply them too, or it will embed a session source's
#: topic lexicon.
#: A rule's return type: the `(name, value)` parts a text is composed from.
EmbeddingTextParts = Callable[[Mapping[str, Any]], Sequence[tuple[str, Any]]]
_EmbeddingTextParts = EmbeddingTextParts


def _document_embedding_parts(
    payload: Mapping[str, Any],
) -> Sequence[tuple[str, Any]]:
    """`_upsert_document`'s `_searchText` composition — by CALLING it (#13412).

    ⛔ NOT a re-implementation, and that is the whole point of this function. The
    record used to compose `title + content` while the graph composed
    `_build_search_text(title, summary, topics)` (#8419/PR #13290), so one
    `DocumentCreated` line produced two vectors — the divergence #13412 records,
    which no test caught because neither side was pinned to the other. Calling
    the GRAPH's function makes drift impossible instead of detectable: change the
    graph's composition and the record's text moves with it.

    The import is LAZY, and deliberately: importing `projection.entities` at
    module scope would make `record_writer`'s own import pull in the write-side
    `projection` package (`tortoise/projection/__init__.py` imports this module
    back, at `:841`, inside a function — which is exactly the shape a package
    import is allowed to rely on and a module-level one is not), for one pure
    string function. A record-only fold (`tools/record_runner.py`) never composes
    a document text and so never pays for it.

    The composition arrives as ONE part so the shared arms below still apply:
    blank text derives nothing, and a composition that RAISES (a truthy non-string
    `topics` entry, which aborts the graph's `" ".join` outside its `try`)
    derives nothing here — the record stays the quieter of the two, as stated.
    """
    from .projection.entities import _build_search_text

    return (("title+summary+topics", _build_search_text(
        payload.get("title", ""), payload.get("summary"),
        payload.get("topics"))),)


def _source_embedding_parts(
    payload: Mapping[str, Any],
) -> Sequence[tuple[str, Any]]:
    """`_upsert_source`'s `_searchText` — the text the RECORD row holds (#13418).

    `projection/entities.py:7106` composes the source node's search surface as
    `ev.get("_searchText") or ev.get("title")` and writes it at `:7194`
    (`s._searchText = $st`; the `:Source` FTS index is declared on exactly that
    field — `projection/__init__.py:6870` and the FULLTEXT declaration at
    `:20548`), so the vector encodes that string and nothing else.

    ⛔ IT READS `_searchText` AND NOT THE `title` FALLBACK, because this is the one
    arm where the graph's expression and the record's are NOT interchangeable. The
    split binds the row's `_search_text` from the payload's `_searchText` alone, so
    `_searchText` is the only text the record row is known to hold; a `title`
    fallback would encode text the row's own indexed column does not contain. And
    on a `SourceCreated` that resolves onto a DOCUMENT row — legal, since a
    document IS a `:Source` (D10), and reachable through `create_source` — the
    title composition would overwrite the `_build_search_text` vector
    `DocumentCreated` put on a row that `document_kind IS NOT NULL` still routes to
    the `document` dense leg (`record_vector.py:169`/`:171`). The record derives
    from the text it holds, or it derives nothing (the third skip arm on the table
    above), which is why that population is named with its root instead of served
    by a guess.

    ⚠️ The residual this leaves, stated rather than hidden: a payload that states
    no `_searchText` is not derived, so on `_upsert_source`'s ON CREATE arm the
    node's `_searchText` (= `title`) has a text the record row does not — the
    `#13474` family (producer must state the composed text). The reverse residual
    is the fold's, not this one's: on the ON MATCH arms `s._searchText` is
    hash-gated (`entities.py:7360-7368` preserves it on a same-hash re-emit), so a
    re-emitted source can carry in the record a text the node kept from before.
    The record is the truth and the graph its derived cache (§2.0); when the two
    disagree the record's value is the one the repair re-derives.

    ⛔ THE VERSION TRANSITION USES THIS SAME RULE, and it is the arm where the
    composition MATTERS MOST. `SourceVersioned` is the one `:Source` record that
    MOVES a row's text: `create_source` emits it with `**payload` on a content-hash
    change (`sdk.py:58641`) and `payload` includes `_searchText` — the retitle is a
    named, in-tree write (`sdk._index_source_merge` → `create_source(…, title=…,
    _searchText=_source_search_text(title=title))`). The split rebinds
    `public.source._search_text` from that key, so a version line that derived
    NOTHING would leave the row's dense leg on the previous version's vector while
    its FTS leg searched the new text — one served label (`LABEL_SERVED["source"]`)
    answering from two different texts, the class `#13412` closes for
    `DocumentCreated` and the reason `title` is refused above. Re-embedding when
    the embedded text changes is also the rule comparable systems converge on
    (the #13473 review's external pass); nothing else would repair it, because a
    STALE vector is not a VECTORLESS one, so `#13417`'s counting/backfill pass
    cannot see it. ⚠️ Its own residual is the SAME third skip arm and not a new
    one: a version line that states no `_searchText` binds no column either, so
    its old text and its old vector stay a matched pair — coherent, one version
    behind, and repaired when the producer states the composed text (`#13474`).

    ⛔ THE IDENTITY IS NEVER THE TEXT (STORAGE §9.4 ③: *"NEVER use a vector to
    decide whether two sources are the same"* — identity is the canonicalised URL
    plus a content hash), so `url`/`id`/`contentHash` are absent by construction,
    and `summary` is absent because `_upsert_source` does not compose from it.
    """
    return (("_searchText", payload.get("_searchText")),)


def _session_source_skip(payload: Mapping[str, Any]) -> str:
    """Why a SESSION `:Source` derives no vector here, or `""` if it is not one.

    §9.4: *"one vector attached to the `:Source` node, whose embedded text is the
    S1 narrative read from `public.session_narrative` — ⛔ never from
    `:Source.summary`"*. The narrative is a RECORD column (`#9555`), not a
    journal key, so this fold cannot compose it: a derivation from the journal
    line would encode the node's topic LEXICON (`#10000` puts it in
    `_searchText`) — the wrong text in a column the narrative arm must own.

    ⛔ THE CLASSIFICATION IS NOT RE-IMPLEMENTED. `cache_projection.
    is_episodic_source_node` is the repo's ONE declaration of a session
    `:Source`'s identity (two arms, never a flag: a `session:` url prefix, or
    `sourceKind == 'agentSession'` on a non-URL) and it already reads GRAPH
    property names, which is exactly what a journal payload carries. A second
    predicate here is how the two would drift.

    Fail-CLOSED on an unreadable predicate (`"the predicate is unreadable"` ⇒
    no vector) rather than fail-open: this function's answer decides WHICH TEXT
    a column encodes, and a wrong text is invisible where a missing vector is
    counted (`#13417`).

    ⚠️ NO WRITER COMPUTES THE NARRATIVE VECTOR TODAY, so this arm does not take
    a vector away from anyone: the encode calls on the capture path are the
    Point/turn ones, and nothing reads `public.session_narrative` to embed it.
    The population therefore has no vector on either side, and §14.3 V2's answer
    for the `:Source` label does not require inventing one out of the wrong text
    — `#13418`'s indicator 1 allows a documented skip in exactly this shape.
    """
    # ⛔ THE IDENTITY IS RESOLVED THE WAY BOTH LAYERS RESOLVE IT, before the
    # predicate sees it. `is_episodic_source_node` reads `props["url"]` (its own
    # docstring: it is the CACHE side's vocabulary), while the record resolves a
    # `:Source` key as `url OR id` (`record_projection._key_value`: "Absence is the
    # one shape that is a real producer — `add_document` emits `id` and no `url` at
    # all") and the fold does the same (`projection/entities.py:7081`, `raw_key =
    # url or sid`). Handing the payload over VERBATIM meant an `id`-keyed
    # `session:…` source was classified as a plain one and got the topic lexicon
    # embedded — the exact text §9.4 forbids, on the row `document_kind IS NULL`
    # routes to the `source` dense leg. Latent today (both in-tree emitters pass
    # `url=`), and free to close here.
    resolved = payload
    if payload.get("url") is None and payload.get("id") is not None:
        resolved = {**payload, "url": payload.get("id")}
    try:
        from .cache_projection import is_episodic_source_node

        episodic = is_episodic_source_node(resolved)
    except Exception:  # noqa: BLE001, RUF100 — unclassifiable ⇒ derive nothing
        return ("the episodic-source predicate is unreadable, so the session "
                "narrative rule (STORAGE §9.4) cannot be ruled out")
    if not episodic:
        return ""
    return ("a session :Source embeds the S1 narrative read from "
            "public.session_narrative (STORAGE §9.4), which the journal line "
            "this fold reads does not carry")


#: The record types whose node carries an embedding the JOURNAL does not, and the
#: text expression its vector encodes — the RECORD half of #13220.
#:
#: ⛔ THIS TABLE IS THE WHOLE RULE, AND IT IS A PARITY TABLE RATHER THAN A
#: PREFERENCE. Each text is the one the GRAPH fold composes for the same label, so
#: the record's vector and the node's vector are the same function of the same
#: datum:
#:
#:   * `ObjectRegistered` / `SubjectAdded` — `encode_for_row(name)`
#:     (`projection/entities.py::_upsert_object`, `::_upsert_subject`, #7845);
#:   * `EventRecorded` — `" ".join(filter(None, [subject, eventKind, object]))`
#:     (`projection/entities.py::_upsert_event`, `:5803-5807` — the
#:     `filter(None, …)` is part of the expression, not decoration: a FALSY part
#:     is dropped and the rest still composes);
#:   * `DocumentCreated` — `_build_search_text(title, summary, topics)`
#:     (`projection/entities.py::_upsert_document`, `:5423`; the SAME call result
#:     is written to `s._searchText` at `:5530`, so the dense and sparse legs
#:     share one text). ⛔ It used to fold `title + content` — `#13412`'s
#:     two-store divergence, now removed rather than documented;
#:   * `SourceCreated` — `ev._searchText`, the row's own indexed text
#:     (`projection/entities.py::_upsert_source` composes `_searchText or title` at
#:     `:7106` and writes it at `:7194`; ⛔ the `title` arm is deliberately NOT
#:     mirrored here — see `_source_embedding_parts`).
#:
#: ✅ `SourceCreated` IS PRESENT (`#13418`), and the earlier absence was reasoned
#: from a premise that no longer holds. The note that used to sit here said a
#: plain `:Source` must derive nothing because the graph holds no vector for one
#: (`_upsert_source` indeed computes none) — so the record's dense leg would
#: return sources the graph leg never had. But `source` and `document` are served
#: from the RECORD (`record_vector.LABEL_SERVED`), the vector's home is Postgres
#: (§14.3 V2, decided; #13082's 584× RAM rule is why the graph must not grow a
#: second copy), and the owner's 2026-11-18 ruling puts `:Event`/`:Source` on the
#: same treatment as `:Object`/`:Subject`. The consequence is the opposite of the
#: old note's: the record having the vector while the graph does not IS the
#: intended direction: the graph copy is what `#13220` retires (record-first
#: sequencing), not the thing the record must match.
#:
#: ⚠️ Three `:Source` populations derive NOTHING, each for a stated reason:
#:   * a **session** `:Source` (its `_searchText` is the topic lexicon) — §9.4
#:     fixes its vector text as the S1 narrative read from
#:     `public.session_narrative`, which the journal line this fold reads does not
#:     carry; encoding the lexicon instead would write text a later narrative arm
#:     must overwrite;
#:   * a record that already CARRIES a vector — `_SOURCE_EXTRA_PROPS`
#:     (`projection/entities.py:2111`) makes `embedding` a caller passthrough, so
#:     the caller's vector is the node's own and recomputing would make the two
#:     stores disagree (`EventRecorded`'s arm is the same shape);
#:   * a record that states NO text (`_searchText` absent — the
#:     `register_source(url, kind, title=…)` door, where `title` rides `**props`).
#:     ⛔ This is a TEXT-CARRIER gap, not a vector rule: the record column is NULL
#:     because the journal payload carries nothing for the split to bind, and
#:     `_upsert_source` meanwhile puts `title` on the node (`:7194`, its ON CREATE
#:     arm) — so the node is FTS-searchable and the record row is not. Same family —
#:     and same root — as `#13474` (a `DocumentCreated` row whose `_search_text` is
#:     NULL): the fix belongs to the PRODUCER stating the composed text, not to a
#:     record rule inventing a column value the journal never carried.
#:   ⚠️ BOTH `:Source` writers run through these arms — `SourceCreated` and the
#:   `SourceVersioned` transition, which share one rule in the table below. The
#:   transition is the reason the rule cannot be create-only: it REBINDS
#:   `public.source._search_text` from its own payload, so a row versioned without
#:   a derived vector would have its dense leg answering the PRIOR version's text
#:   while its FTS leg searched the new one.
#:
#: ⛔ THE TEXT A RULE COMPOSES IS TEXT THE RECORD ROW ALREADY HOLDS. That is why
#: `_source_embedding_parts` reads `_searchText` and NOT `title`: `_search_text` is
#: bound by the split from that same payload key, so the vector and the column
#: describe one datum by construction. A `title`-only source record states no text
#: for the row (the split binds `_search_text` only from the payload), so it
#: derives nothing — see the third `:Source` skip below. ⚠️ The alternative is a
#: REVIEWED REGRESSION and is not taken: a `title` fallback makes a `SourceCreated`
#: for an existing DOCUMENT url rewrite that row's vector (measured: the plan emits
#: `embedding = EXCLUDED.embedding` on conflict) with the plain-source composition,
#: while `document_kind IS NOT NULL` still routes the row to the `document` dense
#: leg — one served label answering a text its owner never composed.
TEXT_PARTS_BY_RECORD_TYPE: dict[str, _EmbeddingTextParts] = {
    "ObjectRegistered": lambda payload: (("name", payload.get("name")),),
    "SubjectAdded": lambda payload: (("name", payload.get("name")),),
    "EventRecorded": lambda payload: (
        ("subject", payload.get("subject")),
        ("eventKind", payload.get("eventKind")),
        ("object", payload.get("object")),
    ),
    "DocumentCreated": _document_embedding_parts,
    "SourceCreated": _source_embedding_parts,
    # The version transition, on the SAME rule: it is the one `:Source` record
    # that moves a row's `_search_text`, so a vector left behind on the prior
    # version would make the served row's dense and sparse legs describe two
    # different texts. See `_source_embedding_parts`'s version paragraph.
    "SourceVersioned": _source_embedding_parts,
}

#: ⛔ ONE COPY, AND THIS MODULE IS ITS HOME (#13447).
#: `tortoise/record_writer.py::_RECORD_EMBEDDING_TEXT` is an ALIAS of this
#: dict — the same object, not a second table — because the record column, the
#: graph fold and the rebuild must read ONE rule or they drift (§12.2a's
#: "second rule set" defect). `tests/test_13447_derived_embedding_seam.py` pins
#: the identity (`is`), not the equality of two copies.

#: The same rule expressed in the RECORD ROW's own spelling (`public.object.name`,
#: …), for the rebuild caller, which holds a ROW and not a journal payload.
#:
#: ⚠️ SCOPED TO THE TWO LABELS `record-restore` could not put a vector back for:
#: `public.object.embedding` / `public.subject.embedding` are the columns
#: `cache_projection` drops by name (the `props` refusal that #13220's column did
#: not relax), so on the rebuild path there is NO other source of the vector for
#: them. `event`/`source` are a different shape — their `embedding` column is
#: already projected into the view, so a row that HOLDS one lands it on the node
#: without any help from here, and a row that holds none is a NULL-vs-derived
#: question the RECORD-side rules own (`#13412`/`#13418` landed the document text
#: and the plain-`:Source` rule; a NULL there is a text-carrier gap, `#13474`),
#: not this one. Stating the boundary rather than deriving for all four
#: labels keeps this change to the gap it was filed for (#13447's scope note).
ROW_TEXT_PARTS: dict[str, tuple[str, ...]] = {
    "Object": ("name",),
    "Subject": ("name",),
}


@dataclass(frozen=True)
class ComposedText:
    """A row's embedding text, and WHY it has none when it has none.

    The second field is not decoration: a derivation that silently produced
    nothing is indistinguishable from "this label carries no vector", and the two
    have different causes (`the text is blank`, `a non-string part`). It rides the
    caller's DEBUG log — `RecordWriter.write` logs it per record — and nothing
    downstream branches on it.
    """

    text: str = ""
    skipped: str = ""


def compose(record_type: str, payload: Mapping[str, Any]) -> ComposedText:
    """The ONE per-label text rule, from a journal record's payload (#13447).

    ``payload`` is the carrier the fold reads — for an `EventRecorded` that is the
    NESTED ``event`` mapping when the producer used its own envelope key
    (`ConversationMiner` emits ``{type: "EventRecorded", event: {...}}``), which the
    caller resolves before calling here.

    ⛔ THE RULE IS A CALLABLE TABLE (`#13412`'s plug point), so this function
    COMPOSES what the rule returns and owns nothing else: the per-label expression
    lives in `TEXT_PARTS_BY_RECORD_TYPE`, where the graph fold's own composition is
    pinned against it.

    ⛔ THE FALSY TEST IS THE GRAPH'S OWN (`filter(None, …)`), NOT `is None or == ""`
    — a falsy part of ANY type (`None`, `""`, `0`, `False`, `[]`) never reaches the
    graph's join: `filter(None, …)` drops it and the rest of the text still
    composes, so the node earns a vector. Testing identity instead refused it and
    left the record's row NULL where the node carried one — the #5004 divergence
    class this rule exists to prevent.

    A composition that RAISES derives nothing: `_build_search_text` aborts on a
    truthy non-string `topics` entry (`" ".join` outside the fold's `try`), and the
    record stays the quieter of the two — the same trade the non-string arm below
    states.

    A TRUTHY non-string part is refused here: the graph's ``" ".join(...)`` sits
    OUTSIDE the `try` that guards its encode, so a non-string makes the FOLD RAISE,
    not silently skip. The record refuses instead (raising out of a fold would let
    a malformed journal line poison a rebuild the graph's own writer already
    aborts, and "no vector" is a state the record can hold); the divergence is
    bounded and stated rather than papered over.
    """
    rule = (TEXT_PARTS_BY_RECORD_TYPE.get(record_type)
            if isinstance(record_type, str) else None)
    if rule is None:
        return ComposedText(skipped="no embedding rule for this record type")
    try:
        rule_parts = rule(payload)
    except Exception:  # noqa: BLE001, RUF100 — every failure is "this row has no vector"
        return ComposedText(
            skipped="the graph's text composition raised on this payload")
    return _compose_parts(rule_parts)


def compose_for_row(label: str, row: Mapping[str, Any]) -> ComposedText:
    """The same rule, spelled in a record ROW's column names (the rebuild caller)."""
    keys = ROW_TEXT_PARTS.get(label) if isinstance(label, str) else None
    if keys is None:
        return ComposedText(skipped="no embedding rule for this label")
    return _compose_parts((key, row.get(key)) for key in keys)


def _compose_parts(pairs: Iterable[tuple[str, Any]]) -> ComposedText:
    parts: list[str] = []
    for key, value in pairs:
        if not value:
            continue
        if not isinstance(value, str):
            return ComposedText(
                skipped=f"{key} is {type(value).__name__}, which the graph's "
                        "text composition cannot join (the fold raises there)")
        parts.append(value)
    text = " ".join(parts)
    if not text.strip():
        return ComposedText(skipped="the composed text is blank")
    return ComposedText(text=text)


# ── One encode per write: the memo ──────────────────────────────────────────
#
# ⛔ WHAT THIS FIXES, AND WHAT IT MUST NOT CHANGE. Before #13447 a live
# `ObjectRegistered` write encoded `name` twice — the record's column, then the
# graph node — because neither caller knew the other had already paid. The memo is
# keyed on the TEXT (the thing the model call is a function of) and lives only for
# ONE live write (`write_scope`), so:
#
#   * a SECOND encode of the same text in the same write is served from the memo —
#     zero model calls, which is the whole measured target (1, not 2);
#   * the WIDTH is applied per caller AFTER the memo, from the raw vector: the
#     encoder's output is filtered by `_degrade_to_width`, never transformed, so
#     `degrade(raw, W)` is exactly what a fresh `encode_for_row(text, W)` returns
#     (#4280's width rule is preserved, not bypassed). Keying on `(text, width)`
#     instead would MISS whenever the record (width `None`) and the fold (the
#     store's declared width) ask differently — i.e. on exactly the armed store
#     this issue is about;
#   * a `None` result is NOT memoised: a bounded decline is a fact about the
#     CALLER's wait budget, and handing it to a caller that would have waited
#     would drop a vector this seam has no right to drop (#5004).
#: The per-write memo. A `ContextVar` for the reason the projection's own write
#: lanes are: concurrent writers on one SDK instance (#5612) must never see each
#: other's entries, and a thread that was never given a scope reads `None`.
_MEMO: contextvars.ContextVar[dict[str, list[float]] | None] = contextvars.ContextVar(
    "tortoise_derived_embedding_memo", default=None)


@contextlib.contextmanager
def write_scope(name: str = "live-write"):
    """Bind ONE memo to this write: the record write and its graph fold.

    The live doors open this AROUND both halves (`EventAPI._emit`,
    `TortoiseSDK._apply_and_journal`, `ConversationMiner._emit_event`), because the
    record's column is written before the fold and only a scope spanning the two
    can make the second encode free.

    A NESTED scope shares the outermost memo rather than replacing it: an inner
    write must not silently re-enable the double encode, and a memo is a
    within-write optimisation, never a cache with a lifetime of its own.
    """
    if _MEMO.get() is not None:
        yield _MEMO.get()
        return
    memo: dict[str, list[float]] = {}
    token = _MEMO.set(memo)
    try:
        yield memo
    finally:
        _MEMO.reset(token)


def one_encode_scope(func):
    """Decorator form of :func:`write_scope` for ONE live door (#13447).

    The four doors that write a derived-entity record AND fold it — the two halves
    that used to encode the same text twice — carry this:

      * `tortoise.api.EventAPI._emit` — every `EventAPI` producer (ingest, the v1
        extractor, `docs_miner`, `mining`, the CLI, connectors);
      * `tortoise.mining.ConversationMiner._emit_event` — its own envelope;
      * `tortoise.sdk.TortoiseSDK._apply_and_journal` — the SDK's record door;
      * `tortoise.sdk.TortoiseSDK._create_entity` — the SDK's Object/Subject/Event
        door, which folds BEFORE it journals (contract exception 2), so the scope
        has to span the pair in the other order as well.

    ⛔ A DOOR THAT DOES NOT CARRY IT IS NOT A CORRECTNESS BUG — it pays the
    pre-#13447 second model call. That is the deliberate direction: the memo is an
    optimisation whose absence costs money, never a vector, so a new door added
    tomorrow cannot silently write a WRONG row. The discovery population for
    "which doors exist" is `tortoise/embed_writer_register.py`'s census, not
    memory.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with write_scope(getattr(func, "__qualname__", "live-door")):
            return func(*args, **kwargs)
    return wrapper


def reset_memo_for_tests() -> None:
    """Clear the thread's memo — a test hook, never a production reset path."""
    _MEMO.set(None)


def encode(text: str, width: int | None = None, *, bounded: bool = False,
           bounded_scope: str | None = None) -> list[float] | None:
    """The ONE encode step for a DERIVED row's text (#13447).

    Faithful to :func:`tortoise.embeddings.encode_for_row` in every respect that
    decides the VALUE — the `#5008` EMBED gate, the `#13224` bound, and the `#4280`
    store width — and adds exactly one thing: a second call for the same text
    inside the same :func:`write_scope` costs no model call.

    ⚠️ The seam is reached through the MODULE attribute
    (``embeddings.encode_for_row``), never a from-import, so the `#5008` gate's own
    tests — which patch that name — keep intercepting it (the `#11641` rule: a
    from-import binds the object at import time and a patched replacement is never
    seen).

    ``bounded_scope`` is `#13417`'s LEG LABEL, and it is forwarded rather than
    defaulted here on purpose: the record leg
    (``BOUNDED_SCOPE_RECORD_DERIVATION``) and the graph fold leg are repaired
    differently, so the seam must not decide which one a caller is. ``None``
    means "leave the primitive's own default alone", so a caller that has no
    opinion (the folds) keeps `encode_for_row`'s signature exactly.
    """
    memo = _MEMO.get()
    if memo is not None:
        raw = memo.get(text)
        if raw is not None:
            from .embeddings import _degrade_to_width
            # The stored vector is the RAW encoder output: `_degrade_to_width`
            # only DROPS rows, never rescales one, so this is the same value a
            # fresh encode at this caller's width would have produced — without
            # the model call.
            return _degrade_to_width([list(raw)], width)[0]
    from . import embeddings
    extra: dict[str, Any] = {}
    if bounded_scope is not None:
        extra["bounded_scope"] = bounded_scope
    vector = embeddings.encode_for_row(text, width, bounded=bounded, **extra)
    if vector is not None and memo is not None:
        memo[text] = list(vector)
    return vector


def derive_row(label: str, row: Mapping[str, Any], *,
               width: int | None = None,
               bounded: bool = False) -> list[float] | None:
    """A record ROW → the vector a rebuild must put on its node (#13447).

    The rebuild caller. ``bounded`` defaults to **False** on purpose and the
    default is the load-bearing part: `record-restore` is a rebuild engine, and
    `embeddings.encode_for_row`'s own note says a rebuild may not decline — a
    timing-dependent rebuild gives two restores of ONE record different graphs
    (§2.0). The live doors opt INTO the bound instead; this is the direction the
    two callers must differ in, and the only one.
    """
    composed = compose_for_row(label, row)
    if not composed.text:
        return None
    return encode(composed.text, width, bounded=bounded)
