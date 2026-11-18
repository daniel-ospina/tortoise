"""#13447 — the ONE derive-and-encode seam for DERIVED-entity rows.

**One cause, two symptoms.** An `:Object`/`:Subject`/`:Event`/`:Document`/`:Source`
vector is DERIVED: it is a pure function of text the row already carries (`name`;
`subject`+`eventKind`+`object`; the document's `_build_search_text`; a source's
`_searchText`). That fact is the same on the live write and on a rebuild, so
anything that computes it must be the same code — and it was not:

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

⛔ THE TEXT TABLE IS *THE* ONE COPY, AND ITS ENTRIES ARE CALLABLES (`#13412`,
`#13418`). `#13412` moved the document's text onto the graph's own
`_build_search_text` by CALLING it, and `#13418` gave a plain `:Source` a rule over
`_searchText`; both landed on `main` after this seam was cut, and both survive here
rather than being re-derived from a field-name list — a list cannot express either
composition, and one that tried would stringify a document's `topics` and keep an
event's empty parts. `record_writer._RECORD_EMBEDDING_TEXT` is an ALIAS of this
table (pinned by `is`-identity), not a second copy.

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
    "compose",
    "compose_for_row",
    "derive_row",
    "encode",
    "one_encode_scope",
    "reset_memo_for_tests",
    "write_scope",
]

_logger = logging.getLogger(__name__)

#: The record types whose node carries an embedding the JOURNAL does not, and the
#: carried keys its text is composed from — the RECORD half of #13220, and the ONE
#: text rule both callers read (#13447).
#:
#: ⛔ THIS TABLE IS A PARITY TABLE RATHER THAN A PREFERENCE. Each text expression is
#: the one the GRAPH fold already composes for the same label, so the record's
#: vector and the node's vector are the same function of the same datum:
#:
#:   * `ObjectRegistered` / `SubjectAdded` — `encode_for_row(name)`
#:     (`projection/entities.py::_upsert_object`, `::_upsert_subject`, #7845);
#:   * `EventRecorded` — `" ".join(filter(None, [subject, eventKind, object]))`
#:     (`projection/entities.py::_upsert_event` — the `filter(None, …)` is part of
#:     the expression, not decoration: a FALSY part is dropped and the rest still
#:     composes);
#:   * `DocumentCreated` — `_build_search_text(title, summary, topics)`, CALLED
#:     (`projection/entities.py::_upsert_document`; the same call result is written
#:     to `s._searchText`, so the dense and sparse legs share one text). ⛔ It used
#:     to compose `title`+`content` — the two-store divergence `#13412` removed by
#:     calling the GRAPH's function. That fix also makes the document pair dedupe
#:     for free here, because `encode`'s memo is keyed on the TEXT: one text earns
#:     one model call, however many legs encode it;
#:   * `SourceCreated` / `SourceVersioned` — `payload["_searchText"]`, the text the
#:     record ROW itself holds (`#13418`). The earlier absence reasoned that a
#:     plain `:Source` must derive nothing because `_upsert_source` computes no
#:     vector, so the record's dense leg would answer what the graph leg never
#:     had. `#13418` reverses the premise rather than the goal: `source`/`document`
#:     are served from the RECORD, the vector's home is Postgres (§14.3 V2,
#:     #13082's 584× RAM rule), and the owner's 2026-11-18 ruling puts
#:     `:Event`/`:Source` on the same treatment as `:Object`/`:Subject` — so the
#:     record holding a vector the graph does not is the INTENDED direction.
#:
#: ⛔ AN ENTRY IS A CALLABLE, NOT A LIST OF FIELD NAMES, and that is a correctness
#: requirement rather than a style choice. Each composition has its own shape
#: (`DocumentCreated` calls a function whose third argument is a LIST,
#: `EventRecorded` joins three parts with falsy ones dropped), so a field-name list
#: would DESCRIBE the datum without being able to produce it — a seam that joined
#: such a list would stringify a document's `topics` and keep an event's empty
#: ones (`#13412`, `#13447`). The callable IS the plug point, and it returns the
#: COMPOSITION and nothing else: the arms that REFUSE to derive (a carried vector,
#: a session `:Source`, a record that states no text) belong to the CALLER
#: (`record_writer.derived_embedding_columns`), which is why this table cannot
#: grow a policy.
_TextParts = Callable[[Mapping[str, Any]], Sequence[tuple[str, Any]]]


def _document_embedding_parts(
    payload: Mapping[str, Any],
) -> Sequence[tuple[str, Any]]:
    """`_upsert_document`'s `_searchText` composition — by CALLING it (#13412).

    ⛔ NOT A RE-IMPLEMENTATION, and that is the whole point. The record used to
    compose `title + content` while the graph composed
    `_build_search_text(title, summary, topics)` (#8419/PR #13290), so one
    `DocumentCreated` line produced two vectors — the divergence #13412 records,
    which no test caught because neither side was pinned to the other. Calling the
    GRAPH's function makes drift impossible instead of detectable.

    The import is LAZY, deliberately: importing `projection.entities` at module
    scope would pull the write-side `projection` package in for one pure string
    function, and a record-only fold never composes a document text at all. It
    also has to happen per CALL, not once at import, so patching the graph's
    composer moves the record's text with it (the anti-drift pin in
    `tests/test_13418_source_and_document_derivation.py`).

    The composition arrives as ONE part so the shared arms still apply: blank text
    derives nothing, and a composition that RAISES (a truthy non-string `topics`
    entry, which aborts the graph's `" ".join` outside its `try`) derives nothing
    here — the record stays the quieter of the two, as `compose` states.
    """
    from .projection.entities import _build_search_text

    return (("title+summary+topics", _build_search_text(
        payload.get("title", ""), payload.get("summary"),
        payload.get("topics"))),)


def _source_embedding_parts(
    payload: Mapping[str, Any],
) -> Sequence[tuple[str, Any]]:
    """`_upsert_source`'s `_searchText` — the text the RECORD row holds.

    `projection/entities.py` composes the source node's search surface as
    `ev.get("_searchText") or ev.get("title")` and writes it to `s._searchText`,
    where the `:Source` FTS index is declared.

    ⛔ IT READS `_searchText` AND NOT THE `title` FALLBACK, because this is the one
    arm where the graph's expression and the record's are NOT interchangeable. The
    split binds the row's `_search_text` from the payload's `_searchText` alone, so
    that key is the only text the row is known to hold. A `title` fallback would
    also be a REVIEWED REGRESSION: a `SourceCreated` that resolves onto an existing
    DOCUMENT row (legal — a document IS a `:Source`, D10) would rewrite that row's
    `_build_search_text` vector with the plain-source composition while
    `document_kind IS NOT NULL` still routes the row to the `document` dense leg.
    The record derives from the text it holds, or it derives nothing — and the
    third skip arm on the caller's table names that population with its root
    (`#13474`: the producer must carry the composed text) instead of guessing.

    ⛔ THE VERSION TRANSITION USES THIS SAME RULE. `SourceVersioned` is the one
    `:Source` record that MOVES a row's text, so a version line deriving nothing
    would leave the dense leg on the previous version's vector while the FTS leg
    searched the new text — one served label answering from two texts.

    ⛔ THE IDENTITY IS NEVER THE TEXT (STORAGE §9.4 ③: *"NEVER use a vector to
    decide whether two sources are the same"* — identity is the canonicalised URL
    plus a content hash), so `url`/`id`/`contentHash` are absent by construction,
    and `summary` is absent because `_upsert_source` does not compose from it.
    """
    return (("_searchText", payload.get("_searchText")),)


TEXT_PARTS_BY_RECORD_TYPE: dict[str, _TextParts] = {
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
#: question the record-side lanes own (`#13417`'s counting/backfill pass), not this
#: one. `#13412` and `#13418` widened the RECORD's text table above; neither moved
#: this boundary, and widening the rebuild rule to those labels would change what
#: the two legs return for `:Source`/`:Event` — a separate decision with its own
#: evidence, not a side effect of a restore repair.
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

    ⛔ THE FALSY TEST IS THE GRAPH'S OWN (`filter(None, …)`), NOT `is None or == ""`
    — a falsy part of ANY type (`None`, `""`, `0`, `False`, `[]`) never reaches the
    graph's join: `filter(None, …)` drops it and the rest of the text still
    composes, so the node earns a vector. Testing identity instead refused it and
    left the record's row NULL where the node carried one — the #5004 divergence
    class this rule exists to prevent.

    A TRUTHY non-string part is refused here: the graph's ``" ".join(...)`` sits
    OUTSIDE the `try` that guards its encode, so a non-string makes the FOLD RAISE,
    not silently skip. The record refuses instead (raising out of a fold would let
    a malformed journal line poison a rebuild the graph's own writer already
    aborts, and "no vector" is a state the record can hold); the divergence is
    bounded and stated rather than papered over.
    The refusal arms above the join are the CALLER's (`record_writer`), because
    only the caller knows which record types may abstain at all; this function is
    the composition and the join, nothing more.
    """
    rule = (TEXT_PARTS_BY_RECORD_TYPE.get(record_type)
            if isinstance(record_type, str) else None)
    if rule is None:
        return ComposedText(skipped="no embedding rule for this record type")
    # The GRAPH's own expression, per rule. A composition that RAISES derives
    # nothing: `_build_search_text` aborts on a truthy non-string `topics` entry
    # (`" ".join` outside the fold's `try`), and the record stays the quieter of
    # the two — the same trade the non-string part below states.
    try:
        parts = rule(payload)
    except Exception:  # noqa: BLE001, RUF100 — every failure is "this row has no vector"
        return ComposedText(
            skipped="the graph's text composition raised on this payload")
    composed: list[str] = []
    for key, value in parts:
        # ⛔ THE GRAPH'S OWN PREDICATE (`filter(None, …)`): a FALSY part is
        # dropped and composition continues, whatever its type. `is None or
        # == ""` was narrower than that and refused a falsy non-string
        # (`0`, `False`, `[]`) the graph happily joins past — a vector on the
        # node and none in the record, for one event.
        if not value:
            continue
        if not isinstance(value, str):
            return ComposedText(
                skipped=f"{key} is {type(value).__name__}, which the graph's "
                        "text composition cannot join (the fold raises there)")
        composed.append(value)
    text = " ".join(composed)
    if not text.strip():
        return ComposedText(skipped="the composed text is blank")
    return ComposedText(text=text)


def compose_for_row(label: str, row: Mapping[str, Any]) -> ComposedText:
    """The same rule, spelled in a record ROW's column names (the rebuild caller)."""
    keys = ROW_TEXT_PARTS.get(label) if isinstance(label, str) else None
    if keys is None:
        return ComposedText(skipped="no embedding rule for this label")
    return _compose_keys(keys, row)


def _compose_keys(keys: Iterable[str], payload: Mapping[str, Any]) -> ComposedText:
    parts: list[str] = []
    for key in keys:
        value = payload.get(key)
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
