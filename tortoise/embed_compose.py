"""#11739 — the deterministic composed embedding text (the half #11706 took lexically).

**The problem this closes.** #11682's research (arXiv 2601.00821) measured that
VERBATIM chunks beat LLM-extracted artifacts by 15.9 pts LoCoMo / 22.0
LongMemEval-S, that the union of the two is statistically indistinguishable from
chunks alone (p=0.39), and that paraphrase scores the highest precision but the
lowest coverage. #11706 took the LEXICAL half: a Point's verbatim ``quote``
joined ``sparse.POINT_INDEX_TEXT_FIELDS``, so BM25 sees the original words. The
VECTOR half was still embedding a representation that is not the source words —
the raw ``content`` (a point's paraphrase) — or, under
``TORTOISE_CONTEXT_ENRICH`` (#10523), a per-turn LLM REWRITE of it.

This module is that half: **``quote`` + the names of RESOLVED neighbours,
composed deterministically**. One composed text, one vector — no model call, no
per-turn cost, and no new attestation path (the composed string is written to
``embeddingText``, the field ``stamp_journal_embedding`` already hashes, so
#4194's rule — *the vector must never describe different text than the node
holds* — stays true **as written**).

⛔ **What THIS change wires, and what it does not.** ``sdk.create_point`` composes
the QUOTE half only, because a Point has no resolved neighbours at its own
creation: ``create_point`` mints NO ``about*`` edge (its only structural writes
are ``extractedFrom`` and ``TAGGED``), its ``about*`` EDGES are written by later
passes (``backfill_about_entities``,
the capture's entity link), and the ``about*`` VALUES it receives are stored as
plain string properties — an asserted relation the graph does not hold. Composing
names from those would put a neighbour's name into the attested text of a point
that has no such neighbour, so it is not done (measured, #11739 review round 4).
The names half — ordering, fan-out ceiling, word budget, redaction — is built and
tested here, and attaches at the RECOMPOSE seam that runs once the edges exist.
That seam is #11739's remaining scope, together with the delete-time redaction
sweep. Until both land the flag stays OFF.

**The decisions #11739 asks for, each answered HERE rather than inferred by a
later lane:**

1. **Composition.** ``quote`` verbatim, then the neighbour names (supplied by
   the caller — see the wiring note above). A node with no resolved neighbours
   composes to exactly its own quote; a node with NO quote AND no neighbours
   returns ``content`` **byte-for-byte** — the unbound/legacy graph degrades to
   exactly today's behaviour. ⛔ The two arms are different on purpose
   (`compose_embedding_text`): a quoted point's vector is meant to change (that
   is the point of the feature), while an unquoted, unbound one must be
   untouched — collapsing ``content`` on that arm would silently re-attest every
   point whose content carries a newline.
2. **Which field.** ``embeddingText`` — the schema A′ field (#8932 §1) that
   ``context_enrich`` already fills for armed captures and that
   ``embeddings.stamp_journal_embedding`` already attests. Nothing here mints a
   hash of its own; the write path passes the composed string through the same
   seam the LLM rewrite used, so the two cannot disagree about which text the
   vector describes.
3. **Ordering.** ``(casefold(name), name)``. Cypher collection order is
   undefined and a caller's dict order is its own, so an explicit total order is
   what makes "byte-identical across runs" TRUE rather than hoped for. The
   tie-break on the exact spelling is deliberate: two names differing only in
   case are distinct strings and must not swap places between runs.
4. **Replay.** The composed text is journalled as ``embeddingText`` on the
   ``PointAdded`` payload (it is an ordinary point property: the create map
   carries it, ``_journal_snapshot_from_create_map`` snapshots it, and
   ``projection/entities.py::_upsert_point_props`` restores it with its own
   ``n.embeddingText=$et`` clause). So ``derived = replay(journal)`` RESTORES the
   composed text and the vector rather than recomposing either — no invariant is
   amended.
5. **The seam — and its honest boundary.** Composition lives at
   ``sdk.create_point``'s embedding computation (it already routes through
   ``embeddings.encode_for_row``, #5008's one gated submission step). ⛔ It is
   NOT the only ``:Point`` mint door — the ingest/extraction door is
   ``api.EventAPI.add_point``, which this change does not touch. Four
   boundaries are stated rather than implied, because "one seam" is a property
   of this CHANGE, not of the tree:
   * the create door composes the QUOTE only (see the wiring note above): the
     names half has no resolved neighbours to name at that moment, so its seam
     is the recompose path, not this one;
   * ``sdk.update_point`` is a SECOND ``:Point`` vector mint (it re-encodes from
     ``content``), and this module composes at CREATE only. A revise that moves
     the composition's own inputs (``quote`` / an ``about*`` ref) therefore
     leaves the EXISTING composed text and its vector in place — they describe
     the text as it was composed, which is a stale-composition window while the
     flag is armed. It is a stated residual: clearing them here was measured to
     diverge live from rebuild (``_revise_point`` writes the vector only under
     ``new_content is not None``, and a derived ``embedding`` is stripped from
     the record), so the clear traded a stale vector for
     ``derived = replay(journal)`` being FALSE — the worse defect. The close is
     the recompose path (#11739's remaining scope), and it must land before the
     flag is armed.
   * ``api.EventAPI.add_point`` (``tortoise/api.py::_point``) mints ``:Point``
     vectors from raw ``content`` and is NOT routed through here. That is the
     "enumerate the writers" arm of this decision: the discovery population is
     ``tools/dod_5090_probe.py::WRITER_CENSUS``, and ``tortoise/embed_writer_register.py``
     declares the writers that are legitimately OUTSIDE the gate — ``create_point``
     is a GATED writer, so it carries NO declaration row in the register and no
     census row (the register's own prose names it as gated). This change gates
     one writer and no other.
   * ``graph-scripts/backfill_embeddings.py`` re-encodes ``:Point`` rows from
     ``text_prop: "content"`` and lives outside ``tortoise/``, so the discovery
     walk (which scans ``tortoise/`` only) never sees it: it is OUTSIDE the
     census population, not merely undeclared within it. An armed graph
     therefore needs it to embed ``embeddingText or content`` before a backfill
     may run — the fourth stated boundary, and gap (d) in ``.env.example``.
     ✅ **CLOSED for the backfill by #12489:** ``LABEL_CONFIG["Point"]`` now
     embeds ``embeddingText or content`` — spelled as a ``CASE``, because
     ``coalesce`` treats ``''`` as present while Python's ``or`` (the
     read/replay rule it must match) does not. Gap (d)'s ``add_point`` half is
     still OPEN.
6. **The truncation budget.** ``EMBED_WORD_BUDGET`` (512, the same cap
   ``embeddings._truncate_for_embedding`` applies at encode time) is spent
   HEAD-FIRST on the quote, and the names line is RESERVED out of it: the quote
   is cut to ``512 - len(names_line)`` words, so names appended to a
   ≥512-word quote still contribute. That is the defect this decision exists to
   prevent — a head-kept cut silently discarding the whole neighbour signal.
   ⛔ The budget drops WHOLE names, never a fragment of one: a name cut mid-way
   would put a string naming nothing into the attested text (and still cost the
   fan-out slot of the name it displaced).
7. **Names only, bounded.** A neighbour contributes its own name — never its own
   composed text (no recursion, no transitive blow-up) — and
   ``NAME_FANOUT_CEILING`` bounds the points with dozens of ``aboutObject``
   edges. The ceiling is applied AFTER the sort, so which names survive is
   deterministic rather than "whatever the driver returned first".

**Resolved names, not caller strings.** This module takes NAMES and never
decides what "resolved" means: resolution is entity resolution, and a second
decision procedure here would be a second mechanism. The recompose seam that
feeds the names half is therefore also the place that must drop a ref resolving
to no live node — an unresolved ref must contribute NOTHING rather than ride
into the attested text as an opaque id.

**What this text is served as.** ``embeddingText`` is a STORED point property
(``_POINT_HANDLED``, its own clause), so it is returned by point reads and by
``tortoise_get_point``. It is not answer context and no read surface renders it
as the claim — but the sentence "it is only an embedding input" would be false,
so it is not written here.

**Redaction — a precondition of the names half, not a property of this change.**
``redacted`` is a parameter of :func:`compose_embedding_text` rather than a
second function, so the delete-time sweep (a journalled re-compose of the Points
that referenced a deleted entity) can pass the deleted name and have it dropped.
⛔ The sweep is NOT built and the names half is NOT wired, so NO composed text
carries a neighbour name today. The sweep is stated here as a PRECONDITION of
wiring it: once names are composed, a hard delete that has no sweep leaves the
deleted entity's name in the readable ``embeddingText`` of every point that
referenced it. See :data:`REDACTION_SWEEP_ISSUE`.

**Gate.** ``TORTOISE_COMPOSED_EMBED_TEXT``, fail-safe OFF via
``env_truthy.is_truthy`` — the same posture as ``TORTOISE_CONTEXT_ENRICH`` and
``TORTOISE_BM25_LEXICAL``. It ships OFF because #11739's own gate (per-subject
raw-vs-composed worst-case pairwise similarity against the calibrated
``NEAR_DUPLICATE_THRESHOLD = 0.89``, plus the rank-distribution/displacement
check that #11706's re-capture already measured one instance of) has not been
run. A composition nobody measured is not a default.
"""
from __future__ import annotations

import os
from collections.abc import Iterable, Sequence

from .embeddings import EMBED_WORD_BUDGET as _ENCODER_WORD_BUDGET
from .env_truthy import is_truthy

#: The gate. Fail-safe OFF: unset/blank/garbage → False.
COMPOSE_ENV = "TORTOISE_COMPOSED_EMBED_TEXT"

#: The word budget the encode seam applies, IMPORTED from the encoder rather
#: than restated (#11739 review round 4: a second literal let this drift above
#: the cap, which would have made the head-kept cut discard a names line the
#: composition had reserved room for). The composition spends it BEFORE the cut.
EMBED_WORD_BUDGET = _ENCODER_WORD_BUDGET

#: How much of that budget the names line may take. 48 words is ~12 typical
#: entity names (ONTOLOGY names are short noun phrases); the ceiling below is
#: what bounds the COUNT, so this bounds the BYTES a pathological long name can
#: take from the quote.
NAME_WORD_BUDGET = 48

#: Fan-out ceiling: a point with dozens of ``aboutObject`` edges must not let
#: its neighbours crowd the quote out of the vector.
NAME_FANOUT_CEILING = 12

#: The label the names ride under. Ours, not the caller's.
NAMES_LABEL = "Related:"

#: The delete-time redaction sweep this module's ``redacted`` parameter is built
#: for is NOT wired yet (#11739's remaining scope). Named here so the residual is
#: findable from the code that has to satisfy it, instead of living only in a
#: PR description.
REDACTION_SWEEP_ISSUE = "#11739"


def compose_enabled() -> bool:
    """Is the deterministic composition armed for this process?

    Fail-safe OFF (unset/blank/garbage → False) via the repo's one truthy
    contract (:mod:`tortoise.env_truthy`), the same spellings every other lever
    in this tree accepts.
    """
    return is_truthy(os.environ.get(COMPOSE_ENV))


def _collapse(value: object) -> str:
    """Whitespace-normalise a scalar into ONE line of text.

    ``" ".join(split())`` — the same normalisation
    ``embeddings._truncate_for_embedding`` applies — so the composed string is
    stable against a caller's incidental newlines/indentation. A non-string is
    not silently stringified: an int/None is "no text", never ``"None"``.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def quote_text(quote: object) -> str:
    """The verbatim quote as composed text, from a str OR a list-valued prop.

    ``quote`` is a declared flatten-list property
    (``projection/entities.py::_POINT_FLATTEN_LIST_PROPS``), so a legacy/odd
    record can carry several spans. They are joined in ORDER with a single space
    — the record's own order is meaningful here (it is the order the spans
    appear in the source), which is why this one is not sorted.
    """
    if isinstance(quote, str):
        return _collapse(quote)
    if isinstance(quote, (list, tuple)):
        parts = [_collapse(q) for q in quote]
        return " ".join(p for p in parts if p)
    return ""


def _redact_set(redacted: Iterable[str] | None) -> set[str]:
    """The casefolded redaction set, normalised the same way a name is."""
    return {n.casefold() for n in (_collapse(r) for r in (redacted or ())) if n}


def _ordered_names(names: Iterable[object]) -> list[str]:
    """Distinct names in the declared order — the survivor chosen by the order."""
    seen: dict[str, str] = {}
    for raw in names:
        name = _collapse(raw)
        if not name:
            continue
        key = name.casefold()
        current = seen.get(key)
        if current is None or (key, name) < (key, current):
            seen[key] = name
    return sorted(seen.values(), key=lambda n: (n.casefold(), n))


def names_line(
    names: Iterable[object],
    *,
    redacted: Iterable[str] | None = None,
) -> str:
    """``"Related: a, b, c"`` — ordered, deduped, capped, whole names only.

    Three bounds, applied in this order and for these reasons:

    * ``redacted`` drops a hard-deleted entity's name BEFORE the ceiling, so a
      redaction never silently costs a live neighbour its slot;
    * ``NAME_FANOUT_CEILING`` caps the COUNT (a point with dozens of edges);
    * ``NAME_WORD_BUDGET`` caps the WORDS, dropping whole names — never cutting
      one mid-way, which would put a fragment naming nothing into the attested
      text. A single name longer than the whole budget yields ``""`` (the base
      text alone) rather than a fragment.

    Returns ``""`` when there is nothing to say — an unbound graph must not gain
    a label with an empty list behind it.
    """
    redact = _redact_set(redacted)
    live = [n for n in _ordered_names(names) if n.casefold() not in redact]
    live = live[:NAME_FANOUT_CEILING]
    kept: list[str] = []
    words = len(NAMES_LABEL.split())
    for name in live:
        cost = len(name.split())
        if words + cost > NAME_WORD_BUDGET:
            break
        kept.append(name)
        words += cost
    if not kept:
        return ""
    return f"{NAMES_LABEL} {', '.join(kept)}"


def compose_embedding_text(
    quote: object,
    names: Sequence[str] = (),
    *,
    content: object = "",
    redacted: Iterable[str] | None = None,
) -> str:
    """The composed embedding text: verbatim ``quote`` + neighbour names.

    * ``quote`` present → the quote is the base (verbatim, whitespace-collapsed);
    * ``quote`` absent  → ``content`` is the base;
    * ``names`` empty   → the base alone. ⛔ With a quote that is the QUOTE (a
      deliberate change of embedding input, not the pre-change text); with no
      quote either, the RAW ``content`` is returned untouched — byte-identical
      to the pre-#11739 input, which is what makes the flag a no-op on an
      unbound, unquoted graph;
    * ``names`` present → ``"<base>\\n\\nRelated: a, b, c"``, with the names line
      RESERVED out of :data:`EMBED_WORD_BUDGET` so the encode seam's head-kept
      cut can never discard it.

    Every branch is a pure function of its arguments — no clock, no graph read,
    no dict order — which is what makes the text (and therefore the vector)
    reproducible from the same inputs.
    """
    base = quote_text(quote)
    line = names_line(names, redacted=redacted)
    if not line:
        # ⛔ Byte-identity arm: an unquoted, unbound point must be handed back
        # EXACTLY as it arrived. `_collapse(content)` here would re-attest every
        # point whose content carries a newline or a double space (measured
        # review finding, #11739) — the opposite of "degrades to current
        # behaviour".
        return base or (content if isinstance(content, str) else "")
    if not base:
        base = _collapse(content)
    room = EMBED_WORD_BUDGET - len(line.split())
    base_words = base.split()
    if room <= 0:
        # A names line alone fills the budget: the names are the ONLY part of
        # this text the base could not have contained, so they win.
        return line
    if len(base_words) > room:
        base = " ".join(base_words[:room])
    return f"{base}\n\n{line}" if base else line
