#!/usr/bin/env python3
"""Source-version currency — the shared derivation helper (issue #5581).

The **read-path half of #5038**: what a read reports about a fact whose source
has moved on, and what it discloses alongside it.

WHAT THE OWNER RULED (2026-09-26T11:17:30Z, `#5038` comment `5845802608`)

  *"We should not return an out-of-date fact when we have a newer one."* The
  out-of-date fact is **withheld as an answer** and **disclosed as an FYI
  carrying its source** — *"let the user know that (newer fact but no source,
  and older fact from source X) … so I can disambiguate"*. This REFINES rather
  than reverses Policy B: that ruling forbade the fact being withdrawn
  *silently*, and this one requires exactly the disclosure that keeps it
  non-silent. Reporting rides the **existing** read surfaces, and no `status`
  field is stored (ONTOLOGY §4.6: *"a read, never a stored flag"*).

WHAT THIS MODULE IS — AND IS NOT

It is the **derivation**: the graph read that produces the ``(remembered,
current)`` pairs, the §4.6 aggregate, the sourced disclosure, and the
eligibility term. It is NOT a user-facing surface — it adds **no MCP tool and
no SDK method**, so `#4282`'s tool/method rule is satisfied (the placement was
decided by Policy B; see `docs/plans/2026-09-25-5038-source-version-anchor.md`
§8 O3).

⛔ **The vocabulary has exactly ONE home, and it is not here.**
`tortoise.search_engine.currency_status` owns the per-link verdict and
`tortoise.search_engine.aggregate_currency` owns the §4.6 aggregate; both are
imported below and re-exported for callers that already think in those terms.
This module must never grow its own copy of either — two copies of a
three-state vocabulary is the drift the repo has already paid for twice (#4097's
env-truthiness contract is the precedent).

THE THREE ADDRESSES — A FACT (#5581), AN ENTITY (#5199), AND A SOURCE (#5038)

⭐ ``source_staleness`` walks a THIRD address: one ``:Source``, outward, over
BOTH anchored link classes at once — *"which derived things did this source produce from
an older version?"*. That is the **targeting** signal re-inference needs before it can
replace anything (#5038 deliverable 3): the per-fact and per-entity reads can say *that*
something is stale, but neither can enumerate what a re-fetched source invalidated, so
"re-infer the source" had no work list.

``point_currency``/``read_links`` walk ``(Point)-[:extractedFrom]->(Source)``.
``entity_currency``/``read_entity_links`` walk the OTHER provenance hop,
``(Source)-[:references]->(:Object|:Event|:Source)`` (a document IS a
``:Source`` post-D10, and it is addressed by ``url``), for the reasoning classes
that never carry an ``extractedFrom`` link (``:Object`` / ``:Event``) and for
document ``:Source`` derivation targets — which may carry **both** hops
(``extractedFrom`` to the corpus source, ``references`` from it) — asking *"is
this object still about the content we hold?"* used to require guessing a Point
that extracts from the same source. Same pair, same aggregate, same object
shape; the difference is only which link set is addressed, which is why neither
path owns a vocabulary.

⛔ An ``:Object`` reads ``unknown`` — **by decision, not by omission**: the
2026-09-25 owner ruling (option A) keeps identity/mention ``references`` links
carrying no ``sourceVersion``, so the pair is unfillable and ``currency_status``
refuses to call an absent note ``current``. Stamping an anchor onto those links
to make this read return ``current`` would reverse the ruling (pinned in
``tests/test_source_version_references_5199.py``).

CURRENCY IS PER LINK, AND §4.6 AGGREGATES THE POINT

``extractedFrom`` is many→many (§3.3), so a Point read from several sources
carries one ``sourceVersion`` **per link**; the Point is **stale if ANY link is
behind** and **current only when every link is** (ONTOLOGY §4.6). A single
link's verdict therefore cannot speak for the Point, and ``read_links`` returns
every link rather than one arbitrarily chosen hop.

Usage
-----
    # the Point-level verdict, over $TORTOISE_DB_URI
    uv run python tools/source_currency.py <point_id>

    # the SOURCE-level sweep: everything this source derived, and what drifted
    uv run python tools/source_currency.py --source <url>
    uv run python tools/source_currency.py --source <url> --json

    # machine-readable (the verdict + every link)
    uv run python tools/source_currency.py <point_id> --json

    # the sourced FYI the ruling requires when the verdict is `stale`
    uv run python tools/source_currency.py <point_id> --fyi

    # the ENTITY-level verdict (#5199) — over the `references` links an
    # :Object / :Event / document-:Source reaches its source through, addressed
    # by id / eventId / url
    uv run python tools/source_currency.py <entity_id> --entity
    uv run python tools/source_currency.py <entity_id> --entity --json

    # against an explicit store instead of $TORTOISE_DB_URI
    uv run python tools/source_currency.py <point_id> --db /tmp/graph.db

The CLI is for operators and tests; the read path imports the functions.
Readers that need the DENOMINATOR of a staleness claim should carry this
module's ``read_links`` output with the verdict — a bare ``stale`` with no
source named is the flag-alone form the ruling rejects.
"""

from __future__ import annotations

import sys

# #5128 precedent (tools/source_dedup_report.py): refuse an old interpreter
# BEFORE the imports below, so the failure names the real cause. The preamble
# must be exactly [docstring] [__future__] `import sys` <guard> — every other
# stdlib import belongs BELOW the guard (tests/test_entry_point_python_guard.py
# ::test_entry_point_guards_the_interpreter_before_its_imports pins it).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/source_currency.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/source_currency.py`"
    )

import argparse
import contextlib
import json
from collections.abc import Mapping, Sequence

from tortoise.search_engine import (
    FYI_REASON_SOURCE_DRIFT,
    aggregate_currency,
    currency_status,
    read_currency_links,
    read_reference_currency_links,
    version_operand,
)
from tortoise.source_identity import normalize_source_url

__all__ = [
    "aggregate_currency",
    "answer_eligible",
    "currency_status",
    "entity_currency",
    "point_currency",
    "read_currency_links",
    "read_entity_links",
    "read_links",
    "source_staleness",
    "sourced_fyi",
    "version_operand",
]

# ── the graph read ────────────────────────────────────────────────────────────

def read_links(graph, point_id: str) -> list[dict]:
    """Every ``extractedFrom`` link of the Point, with its own verdict.

    One row per edge — NOT one per Point — because §4.6's aggregate is defined
    over the links and a caller that collapses them cannot recover the
    per-source material the disclosure must carry ("older fact from source X").

    The read itself lives in :func:`tortoise.search_engine.read_currency_links`
    (the ONE home, and a batch query so the search path pays it once). This is
    the single-Point convenience wrapper.
    """
    return read_currency_links(graph, [point_id]).get(point_id, [])


def point_currency(graph, point_id: str) -> dict:
    """The §4.6 verdict for ONE Point, plus the links it was derived from.

    ``{"point", "status", "links"}`` where ``status`` is
    :func:`tortoise.search_engine.aggregate_currency` over the links' pairs.
    ``unknown`` covers both "a pair is unfillable" and "the Point has no
    ``extractedFrom`` link at all" — the anti-vacuity rule (an empty link set
    must never read ``current``); see the aggregate's docstring.

    The links ride along on purpose: the verdict alone is the flag-alone form
    the owner's ruling rejects, and a caller that wants the disclosure needs the
    behind links' sources.
    """
    links = read_links(graph, point_id)
    return {
        "point": point_id,
        "status": aggregate_currency(
            (lk["recorded"], lk["current"]) for lk in links),
        "links": links,
    }


def read_entity_links(graph, entity_id: str) -> list[dict] | None:
    """Every inbound ``references`` link of one **entity**, with its verdict.

    The single-entity convenience wrapper over
    :func:`tortoise.search_engine.read_reference_currency_links` (the ONE home,
    and a batch query), exactly as :func:`read_links` wraps
    ``read_currency_links`` for a Point.

    THREE states, and they are deliberately distinguishable — the wrapper
    forwards the read's own refusal rather than flattening it:

    * ``[link, ...]`` — the entity's links, in ``(source, recorded)`` order;
    * ``[]`` — **nothing references this entity** (``unknown`` territory, never
      ``current``; see :func:`entity_currency`);
    * ``None`` — the address was REFUSED: one string named more than one
      referenced node, so no verdict can honestly be given for it.

    ⚠️ Never infer "nothing references this entity" from a falsey answer alone:
    ``None`` is the refusal, and only ``[]`` is the empty link set. This is the
    one place the Point analogue (:func:`read_links`) does not carry over — a
    Point has no refusal state, so its ``[]`` is unambiguous.
    """
    batch = read_reference_currency_links(graph, [entity_id])
    # `in` + index, NOT `.get()`: `.get()` returns `None` for an absent key too,
    # which would make "no inbound reference" and "refused" the same answer.
    if entity_id not in batch:
        return []
    return batch[entity_id]


def entity_currency(graph, entity_id: str) -> dict:
    """The §4.6 verdict for one ``:Object`` / ``:Event`` / document ``:Source``.

    ``{"entity", "status", "links"}``, plus ``"ambiguous": True`` when the
    address named more than one REFERENCED node — in which case ``status`` is
    the anti-vacuity ``unknown`` and ``links`` is empty, so the flag is the only
    thing separating a refusal from "nothing references this entity".

    The entity-addressed sibling of
    :func:`point_currency`, and the read #5199's third indicator asks for: the
    classes that reach their source through ``references`` rather than
    ``extractedFrom`` could not be asked *"are you still about the content we
    hold?"* at all before it.

    The aggregate is the SAME :func:`tortoise.search_engine.aggregate_currency`
    the Point lane uses, so the two read paths cannot disagree about §4.6:
    ``stale`` if ANY link is behind, ``current`` only when EVERY link is and
    there is at least one, ``unknown`` otherwise (including an empty link set,
    which is vacuous rather than fresh).

    ⚠️ An ``:Object`` reads ``unknown`` **by decision**, not by omission: its
    ``references`` link carries no ``sourceVersion`` (owner ruling 2026-09-25,
    option A), so there is no recorded version to compare. The honest answer to
    "is this object current?" is therefore *unknown*, and this read reports it
    instead of degrading to a false ``current``.
    """
    batch = read_reference_currency_links(graph, [entity_id])
    # `None` is the read's REFUSAL state: one address named more than one node,
    # so no verdict can honestly be given for it. Surfacing it as a flag (not
    # just as an empty link list) is what keeps "refused" from being read as
    # "nothing references this entity".
    ambiguous = entity_id in batch and batch.get(entity_id) is None
    links = batch.get(entity_id) or []
    verdict = {
        "entity": entity_id,
        "status": aggregate_currency(
            (lk["recorded"], lk["current"]) for lk in links),
        "links": links,
    }
    if ambiguous:
        verdict["ambiguous"] = True
    return verdict


# ── the SOURCE-side read — the re-inference TARGETING signal ───────────────────

#: The two link classes that carry a version anchor, and therefore the only two
#: this sweep can speak about. Named so a caller can branch on WHY a node is in
#: the result rather than parsing prose.
EXTRACTED_FROM = "extractedFrom"
REFERENCES = "references"

#: The status a `--source` sweep exits with when it could not read every link
#: class. The payload carries `complete`, but a shell caller sees only the exit
#: status — and an incomplete sweep must never look like a certified answer.
#: `3` is used rather than `1` so it cannot be confused with argparse's `2`.
CLI_INCOMPLETE = 3


def _resolve_source_read_only(graph, ref: str, errors: list[str]) -> tuple[str, str]:
    """``(url, current contentHash)`` of the ``:Source`` this ref identifies.

    READ-ONLY ON PURPOSE. ``source_identity.resolve_source_key`` is the ONE URL
    resolver and it *adopts on touch* — it writes ``canonicalUrl``/``urlAliases``
    on a pre-canonical node. Calling it from here would make a currency sweep a
    WRITER, and the recorded constraint on this read (``#5422``, `#5038`) is that
    a currency check is a read: nothing is stored, nothing is adopted, and two
    readers of one graph cannot disagree because one of them ran first.

    So the identity is resolved the read-only way: the caller's own spelling
    first, then the canonical spelling via ``canonicalUrl`` — ``normalize_source_url``
    is PURE, so using the ONE normalization home costs no write. An unknown ref
    yields ``(ref, "")``: the honest "no such Source, no version", never an
    exception a caller must special-case.

    ``errors`` is appended to — never swallowed — when the read itself fails: a
    resolver that cannot answer is not the same fact as "no such source", and the
    caller degrades the whole sweep to ``unknown`` rather than certifying a
    source it could not read. That includes the one failure this resolver can
    produce on its own (a normaliser refusal): swallowing it would leave
    ``complete: True`` over an identity it never checked.

    ⚠️ THE CANONICAL SPELLING IS TRIED ON BOTH STORED FIELDS, because a
    pre-canonical node may hold it as its ``url`` — the common API/connector case,
    where the inbound variant never matches it by url. The ONE resolver lists
    ``[url, canonical]`` as candidate spellings for exactly this reason; matching
    only ``canonicalUrl`` would miss that node and report an EMPTY, CERTIFIED
    sweep (``complete: True``, ``re_inference_targets: []``) for a source that
    really does hold stale derivations.

    ⚠️ ONE IDENTITY, ONE NODE — OR NO VERDICT. If the identity resolves to TWO OR
    MORE ``:Source`` nodes (the duplicate-identity state the dedup writers produce
    and ``tools/source_dedup_report.py`` exists to repair), the sweep reports an
    error instead of quietly certifying the one node it read: the other node's
    derivations are real, stale or not, and a read that cannot see them must not
    answer ``current`` over them. The repair is a dedup backfill, not a guess
    here. The check runs on BOTH the broad match AND the resolved url, because
    the link reads below are keyed by ``url`` alone — a sibling sharing the
    resolved spelling would otherwise be swept while the broad match saw one node.
    """
    canonical = ""
    try:
        canonical = normalize_source_url(ref) or ""
    except Exception as exc:  # REPORTED, never silent — see `complete`
        errors.append(f"url normalisation failed: {type(exc).__name__}: {exc}")
    params = {"url": ref, "cu": canonical}
    try:
        rows = graph.query(
            "MATCH (s:Source) WHERE s.url = $url "
            "OR ($cu <> '' AND (s.url = $cu OR s.canonicalUrl = $cu)) "
            # The caller's OWN spelling wins ties — the docstring's precedence,
            # which an `ORDER BY s.url` alone does not honour ('E' < 'e' would
            # hand back a differently-cased alias node).
            "RETURN s.url, s.contentHash "
            "ORDER BY CASE WHEN s.url = $url THEN 0 ELSE 1 END, s.url",
            params=params,
        ).result_set
    except Exception as exc:  # REPORTED, never silent — see `complete`
        errors.append(f"source lookup failed: {type(exc).__name__}: {exc}")
        return ref, ""
    if not rows:
        return ref, ""
    urls = []
    for row in rows:
        if row and row[0] and row[0] not in urls:
            urls.append(row[0])
    if len(rows) > 1:
        # ⚠ COUNT ROWS, NOT SPELLINGS: the MATCH yields exactly ONE row per
        # matched node (a node satisfying both clauses is still one row), so two
        # nodes sharing one spelling — the duplicate-identity state, reachable by
        # raw Cypher / ops scripts rather than by the SDK's url-MERGE writers —
        # is two rows and must be an error. Deduping on the spelling would
        # certify whichever node the engine returned first while its sibling
        # carried a different version.
        errors.append(
            f"source identity is AMBIGUOUS — {len(rows)} :Source nodes carry it "
            f"({', '.join(urls) or 'one url'}); a sweep of one of them cannot "
            "speak for the others (repair with tools/source_dedup_report.py)")
    else:
        # The link reads are keyed by the RESOLVED url, so a sibling holding that
        # spelling without a `canonicalUrl` is invisible to the broad match above.
        try:
            shared = graph.query(
                "MATCH (s:Source {url:$u}) RETURN count(s)",
                params={"u": rows[0][0] or ref}).result_set
        except Exception as exc:  # REPORTED, never silent — see `complete`
            errors.append(
                f"source identity check failed: {type(exc).__name__}: {exc}")
            shared = []
        n_shared = (shared[0][0] if shared and shared[0] else 0) or 0
        if n_shared > 1:
            errors.append(
                f"source identity is AMBIGUOUS — {n_shared} :Source nodes share "
                f"the url {rows[0][0] or ref!r}; a sweep of one of them cannot "
                "speak for the others (repair with tools/source_dedup_report.py)")
    url, current = rows[0][0], rows[0][1]
    if not url:
        # Matched through `canonicalUrl` on a node that carries no `url`. The link
        # reads below are keyed by `url`, so falling back to the caller's spelling
        # reads a spelling NO node carries: an EMPTY, CERTIFIED sweep (exit 0) over
        # real derivations — the same false-fresh class the canonical-spelling arm
        # above exists to close. Report it instead.
        errors.append(
            "the matched :Source carries no url, and the link reads are keyed by "
            "url — the sweep cannot address it (repair the node's url)")
        return ref, version_operand(current)
    # `version_operand` — the SAME blank-is-absent rule the Point read applies
    # (`search_engine.read_currency_links`), so the two directions cannot disagree
    # about `"   "`; see that helper for the false-fresh it refuses.
    return url, version_operand(current)


def source_staleness(graph, source_ref: str) -> dict:
    """Everything ONE source derived, and which of it was read from an older
    version — the targeting read (`#5038` deliverable 3).

    ``#5038``'s acceptance is *"given a source re-fetched with DIFFERENT
    content, the graph can report WHICH ENTITIES were extracted from the EARLIER
    version"*. The fact-addressed read answers half of it (:func:`point_currency`
    says a Point is stale) and could only ever answer it one Point at a time; this
    is the enumeration, so re-inference has something to walk.

    WHAT IS READ, AND WHY EXACTLY THESE LINKS

    * ``(Point)-[:extractedFrom]->(Source)`` — unambiguously a **derivation**
      (§4.6), so EVERY such link is read: an anchored one is compared, and an
      unanchored one (a minted stub, the capture path before materialization) is
      reported ``unknown``. That is the checklist's *"pre-model nodes degrade
      explicitly (not silently current)"*.
    * ``(Source)-[:references]->(target)`` — the derivation form carrying the
      ``#5199`` anchor. The class is decided at read time from the two observable
      signals — the target's LABEL and the anchor's PRESENCE — because the
      write-time discriminator (`projection/edges.py`:
      ``_DERIVATION_REFERENCES_LABELS``) is the *caller's* relation spelling and
      is stored nowhere:

      - an **``Event``** target is a derivation (every provenance writer of one
        is), so it is read whether or not it is anchored — an unanchored one (a
        pre-``#5214``/``create_edge`` edge; §9.6 records *"no
        backfill, deliberately"*) is reported ``unknown``. Dropping it would let
        a source whose legacy Event derivations were never anchored read
        ``current``: a false-fresh over an incomplete link set. ⚠ The escape
        hatch can mint an ``Event`` reference that is NOT a derivation; that can
        only ever add an ``unknown`` row (the hatch cannot set an anchor), so it
        can downgrade a verdict but can never fabricate a ``current`` or a
        target.
      - an **anchored** target of any other label (a document ``:Source``) is a
        derivation too — only a derivation writer sets the anchor. ⛔ "Anchored"
        is the write-side predicate (``r.sourceVersion IS NOT NULL AND
        r.sourceVersion <> ''``), **not** the normalised operand: a
        blank-but-present anchor (``"   "``) is an anchored derivation with an
        UNREADABLE operand, so it stays in ``links`` and reads ``unknown``. Testing
        the stripped value here instead would withhold it and let the aggregate
        certify ``current`` over a link it never judged.
      - an **unanchored ``Source``-targeted** link is genuinely AMBIGUOUS and is
        excluded: referential containment and a document derivation are stored
        as ``(Source)-[:references]->(Source)`` either way (*"the stored edge is
        ... either way"*), so the anchor is the only read-time discriminator. A
        property-free ``Object`` link is excluded for a different reason in the
        same class of situation — it is identity/mention by design (option A,
        `#5199`), and there is no version of an identity to be stale.
        ⚠️ Excluded, but **COUNTED** (``counts["withheld"]``): a sweep that
        silently ignored them could certify ``current`` over a link set it knows
        it did not read.

    NOTHING IS WRITTEN. The verdicts are computed by
    :func:`tortoise.search_engine.currency_status` and aggregated by
    :func:`tortoise.search_engine.aggregate_currency` — the ONE home for both —
    so this module still owns no copy of the vocabulary, and no ``status``
    property lands on a node or edge (§4.6: *"a read, never a stored flag"*).
    The derived nodes are never touched: *stale ≠ wrong*, and supersession is
    additive and deferred (Policy B), which is why the payload names targets
    instead of demoting them.

    AN INCOMPLETE SWEEP IS NOT A VERDICT. A link read that fails is REPORTED, not
    swallowed: the error is carried in ``errors`` and the whole sweep degrades to
    ``unknown`` with ``complete: false``. The alternative — a silently shorter
    link set — turns "could not read" into ``current`` with an empty target list,
    which is the exact false-fresh this module exists to prevent (`#6971` is the
    precedent for read decoration that reports its own unavailability). A stale
    row found before the failure is still listed as a target: that is positive
    evidence, and hiding it would be a second defect.

    THE PAYLOAD

    ``{"source", "current", "status", "complete", "errors", "counts",
    "links", "re_inference_targets"}`` where

    * ``counts`` is ``{"stale", "current", "unknown", "links", "withheld"}``.
      ``withheld`` counts the links deliberately NOT judged, and it covers TWO
      classes that are indistinguishable at read time and unjudgeable for
      different reasons: an **unanchored ``Source``-targeted** link (containment
      or a document derivation — stored identically), and an **unanchored
      ``Object``** link (identity/mention by design, option A of `#5199`). It is
      reported so a reader can tell *"nothing to report"* from *"something I
      refused to classify"*. It is ``None`` when the ``references`` read itself
      failed — an UNREAD class is not an empty one.
    * ``status`` is the §4.6 aggregate over every link found — ``stale`` if ANY
      is behind, ``current`` only when every one is and there is at least one,
      ``unknown`` otherwise (including a source that derived nothing: an empty
      link set is not a currency claim) — and ``unknown`` unconditionally when
      ``complete`` is false.
    * ``links`` is one row per link, ``{"via", "kind", "node", "recorded",
      "current", "currency"}``, sorted by ``(via, node, recorded)`` so two reads
      of an unchanged graph agree. ``recorded`` is a DEFENSIVE tie-break only:
      every writer MERGEs one edge per pair, so two rows equal in ``(via, node)``
      are reachable today only through raw Cypher — the third component is there
      so such a graph still reads deterministically, not because the current
      writers can produce it.
    * ``re_inference_targets`` is the node ids whose recorded version differs from
      the source's current one — the ANSWER to the acceptance line. ``unknown``
      rows are NOT targets: an unfilled pair is not evidence of drift, and
      guessing there is how a re-inference lane re-reads a corpus it did not need
      to.
    """
    errors: list[str] = []
    url, current = _resolve_source_read_only(graph, source_ref, errors)
    links: list[dict] = []
    try:
        point_rows = graph.query(
            "MATCH (p:Point)-[r:extractedFrom]->(s:Source {url:$url}) "
            "RETURN p.id, r.sourceVersion",
            params={"url": url},
        ).result_set
    except Exception as exc:  # REPORTED, never silent — see `complete`
        errors.append(
            f"extractedFrom read failed: {type(exc).__name__}: {exc}")
        point_rows = []
    for node_id, remembered in point_rows:
        links.append({
            "via": EXTRACTED_FROM,
            "kind": "point",
            "node": node_id or "",
            "recorded": version_operand(remembered),
        })
    references_failed = False
    try:
        # EVERY ``references`` link is read and CLASSIFIED HERE, not filtered in
        # Cypher. Two reasons, both load-bearing:
        #
        # * `head(labels(t)) = 'Event'` was a LABEL-ORDER dependency: a target
        #   carrying `Event` not first (`:Object:Event`) was dropped, so an
        #   unanchored legacy Event derivation vanished from the sweep and a
        #   source read `current` over a link it never read — the false-fresh
        #   this module exists to prevent.
        # * the class that is deliberately WITHHELD must still be COUNTED, or
        #   ``status: current`` gets certified over a link set the code knows it
        #   did not read. Every other incompleteness is reported (`errors`,
        #   `complete`); this one is reported as ``counts["withheld"]``, which
        #   keeps the aggregate honest without pretending a guess is a verdict.
        #
        # ⚠ COST, stated because it is paid: this returns EVERY `references` row of
        # the source, including the withheld class, where the old server-side
        # filter returned only derivations. It is still an index seek from ONE
        # Source (never a global edge scan), but it is O(degree) in that source's
        # fan-out — a corpus source's containment links are typically the majority
        # class — and there is no `LIMIT` (an enumeration cannot be truncated
        # without lying about what it found). Counting `withheld` in a second
        # `RETURN count(*)` query would keep the old selectivity at the cost of a
        # second round trip; this read pays the rows instead.
        reference_rows = graph.query(
            "MATCH (s:Source {url:$url})-[r:references]->(t) "
            "RETURN labels(t), coalesce(t.url, t.eventId, t.id), "
            "r.sourceVersion",
            params={"url": url},
        ).result_set
    except Exception as exc:  # REPORTED, never silent — see `complete`
        errors.append(f"references read failed: {type(exc).__name__}: {exc}")
        reference_rows = []
        references_failed = True
    # `None` on a failed read: 0 must keep meaning "nothing was refused", and an
    # unread class is not an empty one (the same distinction `complete` draws for
    # the judged rows).
    withheld: int | None = 0 if not references_failed else None
    for labels, node_id, remembered in reference_rows:
        labels = list(labels or [])
        recorded = version_operand(remembered)
        # ⛔ ANCHORED IS **PRESENCE**, NOT THE NORMALISED OPERAND. The write-side
        # rule is `r.sourceVersion IS NOT NULL AND r.sourceVersion <> ''`, and a
        # blank-BUT-PRESENT anchor (`"   "` from `create_source(..., contentHash="   ")`,
        # which `_anchor_on_create` stamps because it nulls only `''`; or a
        # non-string restored by a graph restore) is an ANCHORED DERIVATION WITH
        # AN UNREADABLE OPERAND — its honest verdict is `unknown`. Testing the
        # stripped operand here instead classed it unanchored, withheld it from
        # `links`, and the aggregate could then certify `current` over a link it
        # never judged: a false-fresh, and a disagreement with the
        # #4282-DELETED `get_provenance_chain`, which reported that same link
        # `unknown` (this module reports the same verdict, which is why the rule
        # stands on its own).
        anchored = remembered is not None and remembered != ""
        # The write-time discriminator is "did a DERIVATION writer mint it", and
        # at read time the two observable signals are the target's label and the
        # anchor's presence: every provenance writer of an `Event` reference is a
        # derivation, and only a derivation writer sets the anchor.
        #
        # ⚠ The escape hatch (`create_edge`, allowlisted for `references`) can
        # mint an `Event` reference that is NOT a derivation. That can only ever
        # add an `unknown` row — the hatch cannot set an anchor — so it can
        # downgrade a verdict to `unknown` and can never fabricate a `current` or
        # a re-inference target. Conservative in the direction §4.6 prefers.
        if "Event" in labels or anchored:
            links.append({
                "via": REFERENCES,
                "kind": ("event" if "Event" in labels
                         else (labels[0].lower() if labels else "")),
                "node": node_id or "",
                "recorded": recorded,
            })
        else:
            # A property-free `Source`-targeted link: containment and a document
            # derivation are STORED IDENTICALLY, so it is withheld rather than
            # guessed at (see the docstring). Counted, never silent.
            withheld += 1

    for link in links:
        link["current"] = current
        link["currency"] = currency_status(link["recorded"], current)
    links.sort(key=lambda lk: (lk["via"], lk["node"], lk["recorded"]))

    counts = {"stale": 0, "current": 0, "unknown": 0}
    for link in links:
        counts[link["currency"]] = counts.get(link["currency"], 0) + 1
    counts["links"] = len(links)
    counts["withheld"] = withheld

    return {
        "source": url,
        "current": current,
        "status": ("unknown" if errors else aggregate_currency(
            (lk["recorded"], lk["current"]) for lk in links)),
        "complete": not errors,
        "errors": errors,
        "counts": counts,
        "links": links,
        "re_inference_targets": [lk["node"] for lk in links
                                 if lk["currency"] == "stale"],
    }


# ── the disclosure ────────────────────────────────────────────────────────────

def _address(verdict: Mapping) -> tuple[str, str]:
    """The addressed id, under the key its verdict carries (#5199).

    A :func:`point_currency` verdict keys its address ``point``; an
    :func:`entity_currency` verdict keys it ``entity``. The CLI's ``--fyi``
    payload used to hard-code ``point``, so every entity disclosure named an
    empty id (`": stale"`) — and a consumer that dutifully read ``fyi["point"]``
    as a Point id would get an event id or a document url. Returning the pair
    lets the payload keep BOTH facts (the value and what kind of address it is)
    without inventing a third name for either.
    """
    if "entity" in verdict:
        return "entity", str(verdict.get("entity") or "")
    return "point", str(verdict.get("point") or "")


def sourced_fyi(verdict: Mapping, *, newer: Mapping | None = None) -> dict | None:
    """The FYI the ruling requires for a **stale** verdict — or ``None``.

    ``verdict`` is a :func:`point_currency` **or** :func:`entity_currency`
    result — the addressed node decides which link set the pairs came from, not
    this function. ``None`` when nothing is
    withheld: a ``current`` or ``unknown`` Point has no drift to disclose, and
    emitting an empty FYI would make the disclosure meaningless (a caller could
    not tell "no drift" from "drift we failed to name").

    The payload CARRIES ITS SOURCE — the whole requirement. Each entry in
    ``sources`` names the Source that moved and the two versions, which is the
    material the owner asked for (*"older fact from source X"*) and the reason a
    bare ``stale`` flag is not a disclosure: the field's practice of flagging
    beside the stale fact is measurably not acted on (`arXiv 2609.08258`; see
    the plan's `OVERRIDES:` line).

    ``newer`` is the caller's knowledge of a replacement fact, as
    ``{"id": ..., "source": ... | None}`` — its absence is the ORDINARY case in
    the window before re-inference produces a successor (`#5422` owns emitting
    those), and it is deliberately not guessed here: naming a successor this
    read cannot see would fabricate the very fact the disclosure exists to
    disambiguate.
    """
    if verdict.get("status") != "stale":
        return None
    outdated = [lk for lk in (verdict.get("links") or [])
                if lk.get("currency") == "stale"]
    if not outdated:
        # Unreachable from `point_currency` (its aggregate IS stale only when a
        # link is) but reachable from a hand-built verdict, and a disclosure
        # that names no source is worse than none: refuse it rather than emit
        # the flag-alone shape the ruling rejected.
        return None
    fyi: dict = {
        "reason": FYI_REASON_SOURCE_DRIFT,
        # #5199: the verdict may be ADDRESSED as an entity (`entity_currency`)
        # rather than a Point, and the address is emitted under the key the
        # verdict itself carries — never relabelled. Reading only ``point``
        # emitted `""` for every entity disclosure, and a FYI that cannot say
        # WHAT it is about is the flag-alone shape the ruling rejects; emitting
        # an event id or a document url as ``point`` would be worse, because a
        # consumer cannot then tell an entity address from a Point id.
        **dict([_address(verdict)]),
        "sources": outdated,
    }
    if newer is not None:
        fyi["newer"] = dict(newer)
    return fyi


def answer_eligible(status: str, *, newer_fact_exists: bool) -> bool:
    """May a fact with this verdict be returned AS AN ANSWER?

    The currency TERM of the owner's rule, and only that term:
    ``NOT (stale ∧ a-newer-fact-exists)``. Composed by the read path into the
    existing participation gate — ``measured AND NOT terminal AND <this>`` — and
    NOT an EP factor: validity is a relabeling/gate, while evidence quality
    already reaches EP through the point's Beta prior (`#5038` O9, resolved by
    research 2026-09-26; summarised in the plan's §8).

    Two boundaries, both deliberate:

    * ``unknown`` is **eligible**. It says the pair could not be compared, not
      that the fact is out of date — withholding on it would suppress every
      Point whose Source carries no version (including every orphan write), a
      far larger change than the ruling makes, and one no policy covers.
    * ``stale`` with NO newer fact is **eligible**. The supersession is
      deferred until the successor exists (§4.6 *"supersession is deferred until
      the replacement exists"*): while the stale fact is the only belief on the
      subject, withdrawing it would leave the graph asserting nothing — the
      exact loss the "a stale belief is strictly better than no belief" clause
      forbids. It is reported stale, and disclosed, but it still answers.

    ⛔ This function decides nothing on its own. It returns the term; whether a
    read actually withholds is the caller's contract change, and that half of
    #5581 is not delivered by this module.
    """
    return not (status == "stale" and newer_fact_exists)


# ── CLI (operator/test entry point — not a surface) ───────────────────────────

def _build_payload(verdict: Mapping, *, fyi: bool, newer: Mapping | None) -> dict:
    if fyi:
        _key, _id = _address(verdict)
        payload = {"address_kind": _key, _key: _id,
                   "status": verdict.get("status", ""),
                   "fyi": sourced_fyi(verdict, newer=newer)}
        if verdict.get("ambiguous"):
            # The refusal must survive EVERY rendering, `--fyi` included: this
            # payload is rebuilt from a fixed key set, so a flag carried only by
            # the raw verdict would vanish exactly on the door an operator uses
            # to ask "what drifted?".
            payload["ambiguous"] = True
            payload["links"] = []
        return payload
    return dict(verdict)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report a Point's §4.6 source-version currency (issue #5581), "
                    "an :Object/:Event's (issue #5199), or sweep EVERYTHING one "
                    "source derived and what drifted (issue #5038).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("id", nargs="?", default=None,
                    help="the Point (or, with --entity, the Object/Event) to read")
    ap.add_argument("--source", default=None, metavar="URL",
                    help="sweep one :Source instead of one id — every derived node "
                         "it produced and which of them was read from an older "
                         "version (the re-inference targeting read, #5038)")
    ap.add_argument("--entity", action="store_true",
                    help="read an :Object/:Event through its `references` links "
                         "instead of a Point through `extractedFrom` (#5199)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--fyi", action="store_true",
                    help="emit the sourced FYI the ruling requires (null when "
                         "the verdict is not `stale`)")
    ap.add_argument("--db", default=None,
                    help="explicit store path; default: $TORTOISE_DB_URI")
    args = ap.parse_args(argv)

    if (args.id is None) == (args.source is None):
        ap.error("give exactly one of <id> or --source <url>")
    if args.source is not None and (args.entity or args.fyi):
        ap.error("--entity/--fyi describe one addressed id; use --source alone")

    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(args.db) if args.db else TortoiseSDK()
    try:
        graph = sdk._get_proj().g
        if args.source is not None:
            sweep = source_staleness(graph, args.source)
            if args.json:
                print(json.dumps(sweep, indent=2, sort_keys=True))
            else:
                counts = sweep["counts"]
                print(f"{sweep['source']}: {sweep['status']} "
                      f"({counts['stale']} stale / {counts['current']} current / "
                      f"{counts['unknown']} unknown of {counts['links']} link(s); "
                      f"now {sweep['current'] or '∅'})")
                for link in sweep["links"]:
                    print(f"  {link['currency']:<7} {link['via']:<13} "
                          f"{link['node']} "
                          f"(recorded {link['recorded'] or '∅'} → "
                          f"current {link['current'] or '∅'})")
                if counts["withheld"] is None:
                    print("  withheld unread — the `references` read failed, so "
                          "the withheld class is unknown, not empty")
                elif counts["withheld"]:
                    # The withheld class is NOT judged, and saying so is what
                    # keeps `current` from being read as "every link checked".
                    # TWO classes land here, for two different reasons: an
                    # unanchored `Source`-targeted link cannot be told from
                    # containment, and an unanchored `Object` link is
                    # identity/mention by design (option A, #5199).
                    print(f"  withheld {counts['withheld']} link(s) — unanchored "
                          f"non-Event `references` links (ambiguous containment/"
                          f"document-derivation, or identity/mention by design) "
                          f"are not judged")
                if sweep["re_inference_targets"]:
                    print("  re-inference: " + ", ".join(sweep["re_inference_targets"]))
                if not sweep["complete"]:
                    print("  ⚠ INCOMPLETE — this sweep could not read every link "
                          "class, so it certifies nothing:")
                    for err in sweep["errors"]:
                        print(f"    {err}")
            # A sweep that could not read everything exits non-zero: the JSON
            # carries `complete`, but a shell caller only sees the status, and an
            # incomplete sweep must not look like a certified answer.
            return 0 if sweep["complete"] else CLI_INCOMPLETE
        verdict = (entity_currency(graph, args.id) if args.entity
                   else point_currency(graph, args.id))
        payload = _build_payload(verdict, fyi=args.fyi, newer=None)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            # #5199: a REFUSED address (one string naming more than one node) is
            # `unknown` with no links — byte-identical to "nothing references
            # this entity" unless the refusal is rendered. The text door is the
            # only user-reachable consumer of that distinction.
            _suffix = (" (refused — the address names more than one node)"
                       if verdict.get("ambiguous") else "")
            print(f"{payload.get('entity') or payload.get('point')}: "
                  f"{payload.get('status')}{_suffix}")
            for link in payload.get("links") or []:
                print(f"  {link['currency']:<7} {link['source']} "
                      f"(recorded {link['recorded'] or '∅'} → "
                      f"current {link['current'] or '∅'})")
            if args.fyi:
                for link in (payload.get("fyi") or {}).get("sources") or []:
                    print(f"  FYI {link['source']} moved "
                          f"{link['recorded']} → {link['current']}")
    finally:
        with contextlib.suppress(Exception):
            sdk.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
