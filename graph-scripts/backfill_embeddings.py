"""Backfill embeddings for nodes missing them (issue #160) — plus the T12
``--force-re-embed`` backfill for the #1349 MiniLM→bge-small swap.

Computes 384-dim embeddings via BAAI/bge-small-en-v1.5 (tortoise.embeddings,
EMBEDDING_MODEL) and writes them as vecf32 so the HNSW vector index has data
to query.

Width (#4289): this is a Point/vector WRITE path, so it obeys the same rule
as the SDK's — the STORE declares the width it can hold
(``FalkorProjection.required_embedding_dim``, #4280) and every row is encoded
through ``embeddings.encode_for_store``. On a store with a Point HNSW index a
wrong-width encoder yields NO vector (the row is skipped, never written); on
the index-less brute-force lanes (embedded / engine < 4.x / failed index
creation) the encoder's own width governs and is kept.

The width is a PER-GRAPH fact, so it is resolved for the graph being written
(``_required_dim_for_graph``), not once from the URI (``--all-tenants`` and
``--graph <other>`` diverge — see ``_connect_falkordb``).

Two operating modes:
- Default (NULL-only): only nodes where `embedding IS NULL` are touched, so
  re-running after a partial failure is safe and never recomputes existing
  vectors.
- ``--force-re-embed`` (#1349 T12): re-embeds ALL rows with the SAME
  index-time text composition so stored vectors equal what the live index
  path produces under bge. Flow per graph: NULL-only repair pass (meeting
  exclusion applied, runs BEFORE the purge leg) → purge leg (legacy #160
  meeting embeddings — subject-only MiniLM junk — are nulled, they must not
  persist in the winner's index) → all-rows pass. Idempotent: deterministic
  embedder + full overwrite ⇒ re-running rewrites identical vectors.

Meeting handling matches index-time Python ``eventKind != "meeting"``
(projection/entities.py:483 suppression) exactly: in Cypher ``<> 'meeting'``
is NULL-falsy for a NULL eventKind, so the force/repair predicates use
``(n.eventKind IS NULL OR n.eventKind <> 'meeting')`` — meetings never get
embeddings from ANY backfill path, and a repair-after-purge can never
re-embed just-purged meetings.

URI support: docker://, redis://, rediss:// (FalkorDB Cloud) via
FalkorProjection.from_uri().

The sweep covers embedding SURFACES, not node labels (#5126): D10
(ONTOLOGY v3.15 §4.4) retired the ``:Document`` node label, so the ``Document``
surface sweeps ``:Source`` with a non-NULL ``documentKind`` (its text is the
node's own ``_searchText``) while the ``Source`` surface sweeps the provenance
Source (``documentKind`` NULL, text = ``url``, #448). Runs print
``Document [:Source]`` so the two are never confused for one another.

Multi-tenant: --all-tenants queries the registry graph for team IDs and
iterates org_{org_id} graphs. Per-tenant backfill, one team at a time.

Usage:
    uv run python graph-scripts/backfill_embeddings.py [--dry-run] [--graph GRAPH]
        [--uri URI] [--all-tenants] [--limit N] [--batch-size N]
        [--force-re-embed] [--repair-embeddings]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@127.0.0.1:16379/tortoise).

Requires the embeddings extra: pip install 'tortoise-graph[embeddings]'
(or sentence-transformers + scikit-learn). --dry-run only reports counts and
does NOT require the model.
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/backfill_embeddings.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/backfill_embeddings.py`"
    )

import argparse
import json
import os

from tortoise.vector_write import (  # #4520: the ONE clear-then-set vector seam
    clear_then_set_prefix,
    set_embedding_clause,
)

DEFAULT_URI = "docker://:falkordb@127.0.0.1:16379/tortoise"
DEFAULT_BATCH = 500

# Embedding SURFACES — the unit the sweep and the completeness marker count.
# Each spec names:
#   ``label``     — the Cypher NODE label the MATCH targets. It is NOT always
#                   the surface key: D10 (ONTOLOGY v3.15 §4.4) retired the
#                   ``:Document`` label, so the ``Document`` SURFACE sweeps
#                   ``:Source``.
#   ``text_prop`` — the node property vectorized (overridden by ``text_expr``).
#   ``id_prop``   — the property the MATCH/SET Cypher uses as the node key
#                   (#448: a Source's canonical key is ``url``, not ``id``).
#   ``kind_pred`` — the discriminator separating two surfaces that share ONE
#                   node label.
#   ``text_expr`` — the Cypher expression vectorized, when a bare property is
#                   not enough.
#   ``text_pred`` — an optional "has non-empty text" predicate, when
#                   ``IS NOT NULL`` is not the live run's own test (``''`` is
#                   non-NULL in Cypher) — kept in ONE place so the dry run and
#                   the live run cannot disagree (#5126).
#: The Point surface's embedding text — the text the vector DESCRIBES (#4194).
#:
#: ``embeddingText`` is a STORED Point property (``tortoise/embed_compose.py``:
#: "What this text is served as"). It is written by the create door only under
#: three conditions (``sdk.py``: the composed text DIFFERS from ``content``, a
#: derived vector exists, and the caller supplied none), so on a graph where
#: composition never ran the property is absent. The rule is the read/replay
#: side's, verbatim: ``projection/entities.py`` does
#: ``p.get("embeddingText") or p.get("content")`` (#8488), so an ABSENT **or
#: EMPTY** ``embeddingText`` falls back to ``content``.
#:
#: ⚠️ ``coalesce(n.embeddingText, n.content)`` is NOT that rule and was measured
#: to be wrong: ``''`` is non-NULL in Cypher, so an empty-string ``embeddingText``
#: SHADOWED ``content`` and that row's text became ``''`` — the live pass then
#: dropped it as text-less, so the row is **never filled** while the surface
#: still reports the sweep as complete. (It is a SILENT MISS, not a dry-run/live
#: disagreement: this constant feeds both ``text_expr`` and ``text_pred``, so a
#: ``coalesce`` mutant moves both and the two passes still agree with each
#: other — they agree on skipping the row.) The ``CASE`` is the faithful
#: translation of Python ``or``. (``nullif`` is not available in this Cypher
#: dialect — measured: “Unknown function 'nullif'”.)
#:
#: One constant for BOTH ``text_expr`` and ``text_pred`` on purpose: the file's
#: own rule is that the two passes share ONE predicate, and two literals would
#: let them drift apart one edit at a time. (That drift is a REAL measured
#: shape, separately from the ``coalesce`` defect above: a ``text_expr`` that
#: disagrees with a CASE-derived ``text_pred`` made the dry run promise 3 rows
#: and the live pass write 2 — the #5126 failure.)
_POINT_EMBEDDING_TEXT_EXPR = (
    "CASE WHEN n.embeddingText IS NULL OR n.embeddingText = '' "
    "THEN n.content ELSE n.embeddingText END"
)

LABEL_CONFIG: dict[str, dict[str, str]] = {
    # #12489 / #4194: a Point's vector must describe the text the node HOLDS as
    # its embedding input. This sweep did not apply the rule the read/replay side
    # applies when it reads that text back (``projection/entities.py``:
    # ``p.get("embeddingText") or p.get("content")``, #8488) — it read bare
    # ``content``: on an armed graph it CLEAR-THEN-SET the composed vector from the
    # paraphrase and left ``embeddingText`` describing a text the vector no
    # longer is — while the completeness marker counted the row ``updated``.
    # ``text_prop`` stays ``content`` as the declared fallback the docstring
    # above describes; the EXPRESSION carries the identity. Ordering matters:
    # composition is invariant to which of #12436's three design options lands
    # (option 3 leaves ``embeddingText`` NULL for a shared quote, which the
    # fallback catches).
    "Point":    {"label": "Point", "text_prop": "content", "id_prop": "id",
                 "text_expr": _POINT_EMBEDDING_TEXT_EXPR,
                 "text_pred": f"trim({_POINT_EMBEDDING_TEXT_EXPR}) <> ''"},
    "Event":    {"label": "Event", "text_prop": "subject", "id_prop": "eventId"},
    # #5126 / D10 (ONTOLOGY v3.15 §4.4): a document IS a ``:Source``. The
    # pre-fix entry named the retired ``:Document`` LABEL and read the retired
    # ``content`` prop, so the surface matched 0 rows in every graph while the
    # completeness marker reported ``Document: 0`` — which reads as "no
    # documents" rather than "this sweep cannot see documents". The text is the
    # node's OWN search surface, ``_searchText`` (#3518 — the field the document
    # FTS leg reads), with the SAME ``title`` fallback the write path applies
    # (``_upsert_document``: ``s._searchText=coalesce($st, s._searchText,
    # s.title)``), so a pre-#125 document Source is still swept rather than
    # silently skipped. NEVER ``content``: ONTOLOGY §4.4 — "``content`` is not a
    # graph property"; it is scrubbed from the node on every write.
    # ``documentKind IS NOT NULL`` is the SAME document predicate the vector leg
    # filters on (``search_engine.run_vector_query``) and the one
    # ``about_document_target_reason``/``backfill_document_search_text``
    # enforce — the sweep must cover exactly the nodes the read can return.
    "Document": {"label": "Source", "text_prop": "_searchText", "id_prop": "url",
                 "kind_pred": "n.documentKind IS NOT NULL",
                 "text_expr": "coalesce(n._searchText, n.title)",
                 # The dry run and the live run must agree on "has text", or
                 # `--dry-run` (the operator's counts-first gate) over-reports:
                 # the live run drops an empty-after-trim text (`not
                 # text.strip()`), and `''` is NON-NULL in Cypher, so
                 # `coalesce('', title)` returns `''` and `IS NOT NULL` counts
                 # it. The repo itself writes that shape
                 # (`projection/__init__.py::backfill_document_search_text`
                 # sets `_searchText = coalesce(title, '')` on a title-less
                 # document). `trim(x) <> ''` is NULL-falsy, so it subsumes
                 # the IS NOT NULL arm.
                 "text_pred": "trim(coalesce(n._searchText, n.title)) <> ''"},
    # #448: the provenance Source embeds its ``url`` (the canonical key, #149).
    # The discriminator is load-bearing: before #5126 ONE branch served both
    # Source surfaces, so a document was embedded as its own URL string.
    "Source":   {"label": "Source", "text_prop": "url", "id_prop": "url",
                 "kind_pred": "n.documentKind IS NULL"},
    "Object":   {"label": "Object", "text_prop": "name", "id_prop": "id"},
    "Subject":  {"label": "Subject", "text_prop": "name", "id_prop": "id"},
}

#: Node labels this script must NEVER match (D10, ONTOLOGY v3.15 §4.4). A MATCH
#: on one is a permanent zero-row query that makes the sweep claim success over
#: nothing — exactly how #5126 hid for as long as it did.
RETIRED_NODE_LABELS: dict[str, str] = {
    "Document": "the ':Document' label is RETIRED — a document is a ':Source' "
                "with a non-NULL documentKind, which the 'Document' SURFACE "
                "(label Source) already sweeps",
}


def _validate_config() -> None:
    """Fail LOUDLY on a config that cannot mean what it says.

    Two invariants, each one half of the #5126 defect:

    1. no surface may target a RETIRED node label — that is a permanent
       zero-row MATCH dressed up as a sweep; and
    2. two surfaces sharing ONE node label must carry DISJOINT, non-empty
       discriminators — one branch cannot serve a document Source and a
       provenance Source (different text surfaces), so undeclared sharing is a
       silent mis-sweep, never a shortcut.

    Raises ``SystemExit`` (the script's own failure channel, already used for
    the interpreter guard and the missing-embedder check) rather than
    ``assert``, so the guard is live under ``python -O`` too.
    """
    by_label: dict[str, list[str]] = {}
    for surface, cfg in LABEL_CONFIG.items():
        label = cfg["label"]
        if label in RETIRED_NODE_LABELS:
            raise SystemExit(
                f"FATAL: embedding surface {surface!r} targets the retired node "
                f"label {label!r} — {RETIRED_NODE_LABELS[label]}. A MATCH on it "
                f"returns 0 rows in every graph, so the sweep would report "
                f"success over nothing (#5126)."
            )
        by_label.setdefault(label, []).append(surface)
    for label, surfaces in by_label.items():
        if len(surfaces) < 2:
            continue
        for surface in surfaces:
            if not LABEL_CONFIG[surface].get("kind_pred"):
                raise SystemExit(
                    f"FATAL: surfaces {surfaces} share the node label "
                    f"{label!r}, but {surface!r} declares no kind_pred "
                    f"discriminator — the two would sweep the same rows (#5126)."
                )
        preds = [LABEL_CONFIG[s]["kind_pred"] for s in surfaces]
        if len(set(preds)) != len(preds):
            raise SystemExit(
                f"FATAL: surfaces {surfaces} on node label {label!r} declare "
                f"IDENTICAL kind_pred discriminators — they would sweep the "
                f"same rows twice (#5126)."
            )


def _require_surface(surface: str) -> dict[str, str]:
    """The spec for ``surface`` — or a LOUD exit naming what to use instead.

    The pre-#5126 failure mode was a surface that matched nothing and said
    nothing; an unknown name must therefore abort, not fall through to a
    generic branch that reads the wrong column.
    """
    cfg = LABEL_CONFIG.get(surface)
    if cfg is None:
        retired = RETIRED_NODE_LABELS.get(surface)
        hint = f"\n  {surface}: {retired}" if retired else ""
        raise SystemExit(
            f"FATAL: unknown embedding surface {surface!r} — known surfaces: "
            f"{', '.join(LABEL_CONFIG)}.{hint}"
        )
    return cfg


def _surface_title(surface: str) -> str:
    """``surface`` for a human reader, naming the node label when they differ.

    ``Document [ :Source ]`` is the line that would have made #5126 visible in
    a run's own output.
    """
    cfg = LABEL_CONFIG[surface]
    return surface if cfg["label"] == surface else f"{surface} [:{cfg['label']}]"


def _connect_falkordb(uri: str):
    """Connect to FalkorDB via FalkorProjection.from_uri().

    Supports docker:// (local), redis:// and rediss:// (FalkorDB Cloud).
    Returns (FalkorDB client, resolved_graph_name, required_embedding_dim).

    ``required_embedding_dim`` (#4289) is the width the STORE declares, read
    off the projection so this sweep applies the SAME width guard the SDK
    write paths do (#4280). It is derived from whether the store has a Point
    HNSW vector index — ``None`` on the index-less lanes (embedded / engine
    < 4.x / failed index creation), which is exactly where the read path
    brute-force scans and any self-consistent width is storable.

    ⛔ It is a PER-GRAPH fact, NOT a deployment-wide one (#4289 review):
    ``required_embedding_dim`` is read off ``self._vector_index_api``, which
    ``FalkorProjection._ensure_vector_index_api`` resolves by issuing the
    create-index DDL against the projection's OWN graph (``self.g``). The
    value returned here therefore describes **the URI's graph only**. A sweep
    whose target graph differs (``--graph <other>``, or any ``org_{id}``
    iteration of ``--all-tenants``) must ask THAT graph instead — see
    :func:`_required_dim_for_graph`. Reusing this value for another graph is
    wrong in BOTH directions:

      * URI graph indexed, target graph not → the target's brute-force read
        lane can hold the encoder's own width, yet the URI graph's
        ``EMBEDDING_DIM`` guard would silently DROP those vectors — the #4280
        regression shape;
      * URI graph index-less, target graph indexed → ``None`` here hands a
        wrong-width vector to ``vecf32`` for an INDEXED Point — the #4289 hole
        left unguarded for that target.
    """
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(uri)
    # proj.db (public FalkorDB client) + proj.graph_name (public) — NOT the
    # private _db/_graph_name (code-review P0 fix: _db doesn't exist).
    return proj.db, proj.graph_name, proj.required_embedding_dim


def _required_dim_for_graph(uri: str, graph_name: str, *,
                            default_graph: str | None = None,
                            default_dim: int | None = None) -> int | None:
    """Resolve the STORE-declared vector width for ONE target graph (#4289).

    ``FalkorProjection.required_embedding_dim`` is a PER-GRAPH fact (see
    :func:`_connect_falkordb`): the property is read off the projection's own
    graph, which ``_ensure_vector_index_api`` probes/creates the Point HNSW
    index on. So the width a sweep must obey is the width of the graph it is
    about to WRITE, not the URI's.

    ⛔ This is a WRITE-SCOPED PROBE, not a read. ``FalkorProjection.from_uri(
    uri, graph_name=...)`` CONSTRUCTS the projection, and
    ``FalkorProjection.__init__`` runs ``_ensure_indexes()`` on THAT graph
    (``tortoise/projection/__init__.py``). Resolving the width for a target
    therefore issues the schema DDL sweep there and CREATES the Point HNSW
    vector index when it is missing — which is exactly why the property has a
    value at all (it is derived from ``_vector_index_api``, set only by that
    create-index probe), so no per-graph resolution can avoid the DDL. On a
    graph whose legacy Point FTS migration is still owed, ``_ensure_indexes``
    can also run the one-time ``search_keys`` data fixup (a stored-property
    write). This side effect is DELIBERATE: it matches the SDK, which runs
    the same sweep on any open, and the exchange is that the guard and the
    write agree on the width. It also means each ``--all-tenants`` iteration
    pays ``_ensure_indexes`` on its own target graph. A genuinely read-only
    probe was rejected because deriving the width from ``CALL db.indexes()``
    here would be a SECOND home for the width rule, which #4280
    deliberately single-sourced on ``required_embedding_dim``.

    ``default_graph`` / ``default_dim`` short-circuit the common single-graph
    run: when the target IS the URI's graph the value already read by
    :func:`_connect_falkordb` is correct, so no second connection — and no
    second ``_ensure_indexes`` — is opened.
    """
    if default_graph is not None and graph_name == default_graph:
        return default_dim

    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(uri, graph_name=graph_name)
    try:
        return proj.required_embedding_dim
    finally:
        proj.close()


# ── Index-time text composition (composition parity, #1349 T12) ────

# The text vectorized at backfill MUST equal the index-time composition so a
# backfill-encoded vector equals an index-time vector for the same content.
# Event non-meeting = subject + eventKind + object (projection/entities.py
# :484-488); AgentSession = session_embedding_text(name, summary, keywords,
# topics) with the summary parsed from the stored content_metadata JSON.
# Document = the node's stored ``_searchText`` (#5126 / D10): the document fold
# composes that field with ``_build_search_text(title, summary, topics)`` —
# ``content`` is retired (ONTOLOGY §4.4: "``content`` is not a graph property")
# and the ``:Document`` label no longer exists. Reading the stored value, rather
# than re-deriving it, is what makes the FTS parity byte-exact by construction
# for this one surface.
#
# The VECTOR parity is exact only on the index path (``_doc_write``, which sets
# ``suppress_embedding=True`` and so writes no vector — the sweep is the only
# producer). On the legacy ``create_document`` /
# ``create_entity(type='document')`` path ``_upsert_document`` STILL stores a
# vector composed from ``title + content`` whenever ``suppress_embedding`` is
# absent (``projection/entities.py::_upsert_document``), so a
# ``--force-re-embed`` MOVES such a node's vector to its ``_searchText``
# composition. That is a known, separately filed residual (#8419) — this
# surface does NOT give byte-exact vector parity on every write path.

# Index-time meeting suppression (projection/entities.py:483) — meetings
# never receive embeddings. Cypher `eventKind <> 'meeting'` is NULL-falsy for
# a NULL eventKind, so the IS NULL OR arm is required to match the Python
# `eventKind != "meeting"` semantics exactly.
_MEETING_EXCLUSION = "(n.eventKind IS NULL OR n.eventKind <> 'meeting')"


def _summary_from_metadata(content_metadata) -> str:
    """Extract the LLM-extracted summary from the stored content_metadata.

    Index-time session embeddings embed name + summary + keywords + topics
    (session_indexer.session_embedding_text), with summary sourced from the
    extracted metadata persisted as ``content_metadata`` JSON on the Event
    node. The pre-T12 backfill hardcoded summary="" here (silently
    downgrading session vectors) — parsing it restores composition parity.
    Malformed/unparseable metadata degrades to "" (never crashes).
    """
    if not content_metadata:
        return ""
    if isinstance(content_metadata, str):
        try:
            content_metadata = json.loads(content_metadata)
        except (TypeError, ValueError):
            return ""
    if not isinstance(content_metadata, dict):
        return ""
    s = content_metadata.get("summary")
    if not s:
        return ""
    return s if isinstance(s, str) else str(s)


def _text_expr(label: str) -> str:
    """The Cypher expression whose value is vectorized for ``label``.

    Defaults to the surface's ``text_prop``. A surface overrides it (via
    ``text_expr``) when a bare property is not enough — the Document surface's
    ``coalesce(n._searchText, n.title)`` mirrors the write path's own fallback
    so a document Source that never got a ``_searchText`` is still swept.
    """
    cfg = _require_surface(label)
    return cfg.get("text_expr") or f"n.{cfg['text_prop']}"


def _row_text(label: str, branch: str, row: tuple) -> str:
    """Index-time-composition text for a fetched row.

    Byte-identical to the index-time ``" ".join(filter(None, [...]))`` joins
    (entities.py) — NULL/None parts drop exactly like ``""`` defaults there.
    The Document surface is the exception by construction: it reads the stored
    ``_searchText`` the write path already composed, so there is no join here to
    drift from the index-time one (#5126).
    """
    _require_surface(label)  # loud before a generic read picks the wrong column
    if label == "Event":
        if branch == "AgentSession":
            # row = (eventId, name, keywords, topics, content_metadata)
            from tortoise.session_indexer import session_embedding_text
            return session_embedding_text(
                row[1], _summary_from_metadata(row[4]),
                row[2] or [], row[3] or [],
            )
        # row = (eventId, subject, eventKind, object) — non-meeting only
        return " ".join(filter(None, [row[1], row[2], row[3]]))
    # row = (id_prop, text_expr) — the aliased ``_id``/``_text`` pair. Every
    # non-Event surface (incl. Document) is the same two-column shape.
    return row[1]


def _label_branches(label: str, *, force: bool) -> list[tuple[str, str, str]]:
    """Per-label (branch, WHERE, RETURN) query specs for both modes.

    Event splits into AgentSession (session surface: name/keywords/topics/
    content_metadata) and other kinds (subject + eventKind + object). The
    meeting exclusion applies to the Event "other" branch in BOTH modes —
    the NULL-only repair pass must never re-embed a just-purged meeting.
    ``force=False`` adds ``n.embedding IS NULL`` (NULL-only repair);
    ``force=True`` drops it (all rows re-embedded). Index-time session
    embeddings never see a NULL name (the indexers default it from
    summary/filename), but session_embedding_text(None, ...) composes
    summary+keywords+topics safely — a name-less AgentSession is
    index-time-embeddable, so the session branch does not gate on name.

    A surface's ``kind_pred`` discriminator rides in EVERY branch's WHERE: two
    surfaces share the Source node label (document vs provenance), and a branch
    that lost its discriminator would sweep the other surface's rows
    (``_validate_config`` refuses a config where that is undeclared).
    """
    cfg = _require_surface(label)
    null_pred = "" if force else "n.embedding IS NULL AND "
    kind_pred = f"{cfg['kind_pred']} AND " if cfg.get("kind_pred") else ""
    if label == "Event":
        return [
            ("AgentSession",
             f"{kind_pred}{null_pred}n.eventKind = 'AgentSession'",
             "n.eventId AS _id, n.name AS _name, n.keywords AS _keywords, "
             "n.topics AS _topics, n.content_metadata AS _cm"),
            ("other",
             f"{kind_pred}{null_pred}{_MEETING_EXCLUSION} AND "
             "(n.eventKind IS NULL OR n.eventKind <> 'AgentSession') "
             f"AND n.{cfg['text_prop']} IS NOT NULL",
             "n.eventId AS _id, n.subject AS _subject, n.eventKind AS _kind, "
             "n.object AS _object"),
        ]
    # Aliased columns: Source's id_prop == url AND its text is the url too, so a
    # bare ``RETURN n.url, n.url`` is "Multiple result columns with the same
    # name". The text is an EXPRESSION, not necessarily a bare property — the
    # Document surface's is ``coalesce(_searchText, title)`` (#5126).
    text = _text_expr(label)
    # A surface may tighten "has text" beyond ``IS NOT NULL`` (``text_pred``) so
    # the dry run and the live run share ONE predicate — the live run drops an
    # empty-after-trim text, and ``''`` is non-NULL in Cypher (#5126).
    text_pred = cfg.get("text_pred") or f"{text} IS NOT NULL"
    return [("all", f"{kind_pred}{null_pred}{text_pred}",
             f"n.{cfg['id_prop']} AS _id, {text} AS _text")]


def _purge_meeting_embeddings(g, *, dry_run: bool = False) -> int:
    """Remove legacy #160 meeting embeddings (subject-only MiniLM junk).

    Index-time suppresses meeting embeddings entirely (entities.py:483) —
    the #160 backfill embedded them anyway; under bge they must not persist
    in the vector index. Returns the count removed (or to remove in dry-run).
    """
    rows = g.query(
        "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
        "RETURN count(n)"
    ).result_set
    n = rows[0][0] if rows else 0
    if n and not dry_run:
        g.query(
            "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
            "SET n.embedding = null",
        )
    return n


def _dry_run(db, graph_name: str, labels: list[str], *,
             force: bool = False) -> tuple[int, int, int]:
    """Count rows a run WOULD write. Returns (total, purge_count, unreachable).

    NULL-only: rows missing embeddings. Force: ALL rows (meeting-excluded) —
    already-embedded rows ARE re-embedded by force, so they count; meetings
    and text-less rows are the unaffected set and are NOT counted. Dry-run
    never computes embeddings and never writes.

    "Text-less" is taken from ``_label_branches``' predicate, so wherever a
    surface declares a ``text_pred`` the two passes cannot read DIFFERENT
    predicates within that surface — they read the surface's ONE (a bare
    ``IS NOT NULL`` counted rows the live pass skipped because ``''`` is
    non-NULL in Cypher — #5126, #12489). ⚠️ That is a claim about the PREDICATE,
    not about agreement: the live guard is still stricter wherever the Cypher
    construct and Python disagree (residual (b)) and type-strict (residual (c)),
    so the counts CAN and do differ on those rows. Only the Point surface also
    derives ``text_expr`` AND ``text_pred`` from ONE constant
    (``_POINT_EMBEDDING_TEXT_EXPR``), so only there does an edit to the
    expression move both keys; Document's two keys are separate literals.
    Residual, recorded rather than silently over-reported, in THREE parts:
    (a) a surface with only a bare property still uses ``IS NOT NULL``, so a
    *non-empty-check* miss is limited to an EMPTY or whitespace-only value on
    those surfaces; (b) even a ``text_pred`` surface is not perfectly aligned with
    the live guard, because Cypher ``trim()`` strips SPACES only while the live
    pass's Python ``str.strip()`` also strips tab, NBSP and em-space — measured
    on this surface: four Points whose ``content`` was ``"\\t"``, ``"\\u00a0"``,
    ``"\\u2003"``, ``"\\t \\t"`` gave ``dry_total=4`` against ``updated=0,
    skipped=4``. Pre-existing as a CLASS (the pre-fix bare ``IS NOT NULL``
    counted the same four), but #12489 WIDENS the population: an ``embeddingText``
    whose whitespace `trim()` does not strip (tab / NBSP / em-space) over a
    non-empty ``content`` now takes this skip path, where the pre-fix expression
    embedded ``content`` (measured: dry 1 / updated 0 with the fix, dry 1 /
    updated 1 without it). A SPACE-only ``embeddingText`` is excluded by BOTH
    passes instead (`trim()` does strip a space, so the predicate is false and
    the dry run never counts it) — a silent miss the dry run does not report at
    all; the CASE is still faithful there (Python ``or`` also treats ``" "`` as
    text), the divergence is this same pre-existing live-guard class. (c) TYPE:
    the predicate calls ``trim()``, which is TYPE-STRICT. Two classes, and they
    diverge DIFFERENTLY:
      - a non-``str`` ``content`` (int/list/float/bool) was ALREADY fatal to the
        live pass — pre-fix it was selected by ``n.content`` and killed at
        ``text.strip()``, and now it dies earlier, inside the collecting select.
        It additionally makes ``--dry-run`` raise ``Type mismatch: expected
        String or Null but was <Type>`` (the message names the instance —
        ``Integer``, ``List``, ``Float``, ``Boolean``). ⚠️ REACHABLE:
        ``api.EventAPI.add_point`` passes ``content`` straight through —
        ``_point`` copies it verbatim with no type check — so a non-``str``
        ingest payload the store can hold produces exactly this row. (A ``dict``
        is the exception: the projection's value-shape gate nulls it before
        dispatch, #7369, so that one lands text-less instead.) That is the
        hand-edited/foreign-data class ``projection/entities.py`` names for
        ``content`` (#2958). No SDK door does this (``create_point``/
        ``update_point`` refuse a non-``str``).
      - a non-``str`` ``embeddingText`` over a ``str`` ``content`` is the one
        NEW failure: the pre-fix expression was ``n.content``, so that row
        embedded NORMALLY; the CASE now selects the non-string and BOTH passes
        raise ``Type mismatch`` (the live one inside the select, before
        ``text.strip()`` is reached). It fails CLOSED — an abort, never a
        silent wrong vector — and no in-tree MINTING writer produces one (the
        composer returns a ``str`` for every arm; a caller-supplied
        ``embeddingText`` is refused by ``_sanitize_props``). The REPLAY does
        write it, though — ``_upsert_point_props`` copies
        ``p.get("embeddingText")`` verbatim into ``n.embeddingText`` — so a
        malformed or hand-edited journaled Point produces exactly this row, the
        same ``#2958`` class named above for ``content``.
    Hardening this (a predicate or a live guard that skips non-``str`` text) is
    NOT done here: it widens the change past "which text is embedded".

    #12496 — ``total`` counts rows a run would actually WRITE, so a row the
    write cannot reach is excluded here: the write MATCHes on ``id_prop`` and a
    NULL key matches nothing. Each surface's count query returns BOTH numbers
    (``count(n)`` and ``count(CASE WHEN n.<id_prop> IS NOT NULL THEN 1 END)``)
    so the excluded population is derived from the SAME predicate text the live
    pass reads — never a second spelling that can drift (#5126's failure mode).
    Excluded rows come back as ``unreachable`` and are printed on their own
    line, never folded into ``total``: the pre-fix dry run counted them and the
    operator's counts-first gate promised a repair the live pass could not
    perform.

    The two passes answer the SAME two questions by means that coincide, and it
    is worth being exact about which is which after #12496:

    * **is the row REACHABLE?** This pass asks ``n.<id_prop> IS NOT NULL``; the
      live pass asks ``row[0] is None`` over the rows it selected. Same
      question, same key, one predicate text — so at ``limit=0`` `unreachable`
      agrees between the two by construction, not by a test that happens to
      match. ⚠️ **``--limit`` breaks that too**, in the same way it breaks
      ``dry_total == updated``: ``limit`` bounds the live pass (`rows_all =
      rows_all[:limit]`) and this function takes no ``limit`` at all, so the
      live run never sees a keyless row outside the window — measured, one
      reachable + one keyless Point with ``--limit 1`` -> dry
      ``unreachable=1``, live ``unreachable=0``.

      `test_unreachable_agrees_between_dry_and_live_when_the_encoder_declines`
      pins the agreement, and it is a ``limit=0`` test.
    * **did the row get WRITTEN?** Only the live pass can answer that, and it
      answers it from the write's own ``RETURN`` — not from the key.

    ⚠️ ``dry_total == updated`` is NOT an invariant of this file, and this
    change does not make it one. It holds for **a reachable row whose text this
    store can encode**, and it is pinned there by a test
    (``test_dry_run_discloses_but_never_counts_an_unreachable_row``). It still
    does not hold — measured, pre-existing, and unchanged by #12496 — for:

    * **an encoder-declined row** — a keyed row the store's width guard refuses
      (``encode_for_store`` -> ``None``) is counted here and skipped live (#4289);
    * **a text-less row on a surface with no ``text_pred``** — ``Source``,
      ``Object``, ``Subject`` and BOTH ``Event`` branches gate on ``IS NOT NULL``
      only, and ``''`` is non-NULL in Cypher, so an empty-text row is counted
      here and skipped live (residual (a) above);
    * **whitespace-only text in the ``trim()``/``strip()`` gap** — a tab, NBSP or
      em-space value is counted here even on a ``text_pred`` surface and dropped
      live (residual (b) above);
    * **a ``--limit``-bounded run** — ``limit`` bounds the live pass; this
      function has no ``limit`` parameter at all, so it always counts the whole
      graph. That is why the marker carries the run's ``limit``: a non-zero
      ``residual_missing`` is only an assertion on an unbounded run.

    ``_residual_missing`` is the backstop for exactly those populations: it
    reads the graph afterwards, so it says what is still missing whatever the
    counters claim.
    """
    g = db.select_graph(graph_name)
    total = 0
    unreachable = 0
    for label in labels:
        cfg = _require_surface(label)
        n = 0
        no_key = 0
        for _, where, _ in _label_branches(label, force=force):
            # #12496: ONE query answers both "how many rows does the predicate
            # select" and "how many of them can the write reach", so the
            # excluded population is derived from the SAME predicate text the
            # live pass reads — it is never a second spelling that can drift
            # (#5126's failure mode).
            rows = g.query(
                f"MATCH (n:{cfg['label']}) WHERE {where} "
                f"RETURN count(n) AS _selected, "
                f"count(CASE WHEN n.{cfg['id_prop']} IS NOT NULL "
                f"THEN 1 END) AS _reachable"
            ).result_set
            selected, reachable = rows[0] if rows else (0, 0)
            n += reachable
            no_key += selected - reachable
        if no_key:
            print(f"  {_surface_title(label)}: ⚠️ {no_key} rows cannot be "
                  f"written — no {cfg['id_prop']} for the write to MATCH, so "
                  f"no re-run can repair them")
            unreachable += no_key
        if n:
            verb = "rows would be re-embedded" if force else "missing embeddings"
            print(f"  {_surface_title(label)}: {n} {verb}")
            total += n
    purged = 0
    if force:
        rows = g.query(
            "MATCH (n:Event) WHERE n.eventKind = 'meeting' AND n.embedding IS NOT NULL "
            "RETURN count(n)"
        ).result_set
        purged = rows[0][0] if rows else 0
        if purged:
            print(f"  Event: {purged} meeting embeddings would be purged")
    return total, purged, unreachable


def _backfill_graph(db, graph_name: str, labels: list[str],
                    limit: int, batch_size: int, *,
                    force: bool = False,
                    required_dim: int | None) -> dict:
    """Backfill one graph.

    force=False: NULL-only repair (only ``embedding IS NULL`` rows).
    force=True: ALL rows re-embedded (T12) with index-time composition; the
    meeting exclusion applies in both modes.

    ``required_dim`` (#4289) is the STORE-declared width this sweep must obey
    — ``FalkorProjection.required_embedding_dim`` for **this** graph, threaded
    in by :func:`main` from :func:`_required_dim_for_graph`. Every row is
    encoded through
    :func:`tortoise.embeddings.encode_for_store`, so this operator write path
    applies the same width guard as the SDK write paths (#4280): a vector the
    store's index cannot hold becomes ``None`` (counted as ``skipped``; the
    row is not written) instead of being handed to ``vecf32``. ``None`` means
    the store has no width-fixing index, so the encoder's own width governs
    and nothing is dropped — the index-less brute-force read lane is
    dimension-agnostic by contract (see ``search_engine.run_vector_query``).
    This was the one Point writer with no width guard before #4289.

    ``required_dim`` is keyword-only and REQUIRED (no default): a default of
    ``None`` would collapse "the store measured as index-less" with "the
    caller omitted the width", and a future caller that omitted it would
    silently re-open the #4289 hole on an indexed store (#4289 review, P3).

    Returns stats: scanned/updated/skipped counts, per_label updated counts,
    agent_sessions sub-count (Event surface only), and ``unreachable`` — the
    rows the surface predicate SELECTED that carry no key, so the write's
    ``MATCH`` can never find them (#12496).

    ⚠️ Counted over the selected rows (``chunk``), NOT over the write batch: a
    keyless row declined before it is batched (text-less, or encoder-declined)
    can never be repaired either, and counting the batch missed exactly those.
    ``unreachable`` is a BREAKDOWN of ``skipped``, not a disjoint bucket: those
    rows are counted in both.
    """
    g = db.select_graph(graph_name)

    from tortoise.embeddings import encode_for_store

    total = updated = skipped = 0
    # #12496: the rows the write cannot REACH — no key means the MATCH matches
    # nothing, so the row can never be repaired by this sweep or any later one.
    # ⚠️ It is NOT the only non-converging cause: a row whose text is empty (on
    # the surfaces that carry no ``text_pred``) or which the store's width guard
    # declines is equally permanent, and those stay only in ``skipped``. What
    # distinguishes ``unreachable`` is who can clear it: a human must supply a
    # key, whereas the other causes need new text or a new embedder.
    unreachable = 0
    # Seed ALL 6 surface keys at 0 so the completeness marker is unambiguous:
    # a zero-count surface means "no rows re-embedded", not "never processed".
    per_label: dict[str, int] = {label: 0 for label in LABEL_CONFIG}
    agent_sessions = 0
    for label in labels:
        cfg = _require_surface(label)
        label_updated = 0

        # Collect (branch, row) in bounded queries — avoids re-pagination
        # drift from SET mutating the WHERE predicate mid-scan.
        rows_all: list[tuple] = []
        for branch, where, returns in _label_branches(label, force=force):
            offset = 0
            while True:
                rows = g.query(
                    f"MATCH (n:{cfg['label']}) WHERE {where} "
                    f"RETURN {returns} SKIP $skip LIMIT $limit",
                    params={"skip": offset, "limit": batch_size},
                ).result_set
                if not rows:
                    break
                rows_all.extend((branch, r) for r in rows)
                offset += len(rows)
                if limit and offset >= limit:
                    break
        if limit:
            rows_all = rows_all[:limit]
        if not rows_all:
            continue
        verb = "rows re-embedded" if force else "missing embeddings"
        print(f"  {_surface_title(label)}: {len(rows_all)} {verb}")

        # Compute + write in batches. UNWIND avoids N+1 SET queries.
        for start in range(0, len(rows_all), batch_size):
            chunk = rows_all[start:start + batch_size]
            # #12496: the keyless population is counted over ``chunk`` — the
            # rows the surface predicate SELECTED — because that is the same
            # population `_dry_run` counts, so the two agree BY CONSTRUCTION.
            # Counting it over ``batch`` instead (review finding, measured) lost
            # every keyless row declined BEFORE the batch: a keyless row whose
            # encoder returns ``None`` on a width-guarded store gave dry
            # ``unreachable=1`` and live ``unreachable=0`` — the live headline
            # claiming ``0 unreachable`` over a row no re-run can reach.
            # ``unreachable`` stays a subset of ``skipped``: whichever way the
            # row is declined (text-less, encoder, or the write's MATCH), it is
            # counted in ``skipped`` too.
            unreachable += sum(1 for _branch, row in chunk if row[0] is None)
            batch: list[dict] = []
            for branch, row in chunk:
                text = _row_text(label, branch, row)
                if not text.strip():
                    # Index-time guards composition with .strip() before
                    # computing (entities.py) — empty text never embeds.
                    skipped += 1
                    continue
                emb = encode_for_store(str(text), required_dim)
                if emb is None:
                    skipped += 1
                    continue
                batch.append({"id": row[0], "emb": emb})
                if label == "Event" and branch == "AgentSession":
                    agent_sessions += 1
                total += 1

            if batch:
                # #12496: the count DESCRIBES the write. ``RETURN
                # collect(...)`` is the write's OWN report of what it matched,
                # so the marker cannot count a row the MATCH could not find
                # (the pre-fix ``updated += len(batch)`` counted rows that
                # were SUBMITTED, not rows that were written). Two properties
                # are measured, not assumed:
                #   * ``collect(DISTINCT row.id)`` DROPS a NULL id, and
                #   * the membership test below is per SUBMITTED ROW, not per
                #     distinct id: two ``:Point`` nodes sharing one ``id`` are
                #     BOTH written by a single batch row (measured: 2 nodes,
                #     1 distinct id, ``count(n)`` 2, both nodes hold the
                #     vector), so counting distinct ids would UNDER-report a
                #     repair that did happen.
                written = g.query(
                    f"UNWIND $batch AS row "
                    f"MATCH (n:{cfg['label']} {{{cfg['id_prop']}: row.id}}) "
                    + clear_then_set_prefix("n", "row.emb")
                    + "SET " + set_embedding_clause("n", "row.emb")
                    + " RETURN collect(DISTINCT row.id) AS _written",
                    params={"batch": batch},
                ).result_set
                written_ids = written[0][0] if written and written[0] else []
                n_written = _written_row_count(batch, written_ids)
                updated += n_written
                label_updated += n_written
                # A submitted row the write did not match was NOT repaired, so
                # it belongs in ``skipped`` — never in ``updated``. This is the
                # only place ``updated`` is decided: a row is batched like any
                # other, the write declines to match it, and the difference
                # between what was submitted and what the write reports is what
                # lands in ``skipped``.
                skipped += len(batch) - n_written

            if total and total % 25 == 0:
                print(f"    ... {total} processed")

        per_label[label] = label_updated

    return {"scanned": total, "updated": updated, "skipped": skipped,
            "unreachable": unreachable,
            "per_label": per_label, "agent_sessions": agent_sessions}


def _written_row_count(batch: list[dict], written_ids: list) -> int:
    """How many SUBMITTED rows the write actually matched (#12496).

    ``written_ids`` is the write's OWN ``RETURN collect(DISTINCT row.id)`` —
    the ids the ``MATCH`` found a node for. Deriving the count from the write's
    report, rather than from what was handed to it, is the whole point: the
    pre-fix counter was ``updated += len(batch)``, a count of rows SUBMITTED.

    Two deliberate properties:

    * **per SUBMITTED ROW, not per distinct id.** A batch may carry one id
      twice (two nodes sharing a key), and the write then lands the vector on
      every node with that id — so BOTH submitted rows did gain a vector.
      Counting ``len(written_ids)`` would under-report them.
    * **no ``set()``.** A hand-edited / foreign key value can be an unhashable
      Cypher list, and building a set from it would raise ``TypeError`` in the
      middle of an operator sweep. Plain equality costs at most ``batch_size²``
      comparisons (250,000 at the default 500) per batch, against an embedding
      pass that dominates the run.
    """
    return sum(1 for item in batch
               if any(item["id"] == got for got in written_ids))


def _residual_missing(g, label: str) -> tuple[int, int]:
    """Rows this surface's NULL-only predicate still selects AFTER the sweep.

    #12496: the completeness marker's counters are the run's OWN account of
    what it wrote. An independent reading of the graph's END STATE is what
    makes a false PASS need to fool two things instead of one — so the marker
    carries this alongside them.

    Returns ``(missing, keyless)``: the rows the surface's NULL-only predicate
    still selects whose embedding is still NULL, and how many of those carry no
    key (the population no re-run can reach).

    ⚠️ ``missing`` is NOT "rows that should have a vector". For the surfaces
    with no ``text_pred`` — ``Source``, ``Object``, ``Subject`` and BOTH
    ``Event`` branches — it includes rows the live guard legitimately declines
    (bullet 2 below), so read ``missing`` as "still selected and still
    missing", not as damage.

    ⚠️ **Non-zero is not by itself a failure**, and this is measured, not
    conceded: the file has four healthy-run populations that stay residual.

    1. **A ``--limit``-bounded run.** ``_backfill_graph`` is capped, this reads
       the whole graph — 6 healthy keyed Points with ``--limit 2`` measured
       ``residual_missing=4, residual_keyless=0``. The marker carries the run's
       ``limit`` for exactly this reason: the field is an assertion only when
       ``limit == 0``.
    2. **Empty text on a surface with no ``text_pred``.** Only ``Point`` and
       ``Document`` gate on non-empty text. The other four ROW SOURCES —
       ``Source`` (text is ``n.url``), ``Object``/``Subject`` (``n.name``) and
       BOTH ``Event`` branches — gate on ``IS NOT NULL`` alone, so ``''``
       (non-NULL in Cypher) is selected and the live guard drops it: residual
       forever. Measured on healthy keyed rows: ``Object{name:''}`` -> ``(1, 0)``
       and ``Source{url:''}`` -> ``(1, 0)``.
    3. **Whitespace-only text the predicate accepts and the guard drops.** This
       one reaches the GATED surfaces too, so "has a ``text_pred``" is not
       immunity: Cypher ``trim()`` strips SPACES only, while the live guard is
       Python ``str.strip()``, which also strips tab, NBSP and em-space. Measured
       just now — two keyed ``:Point`` rows with ``content`` ``"\t"`` and
       ``"\u00a0"``, ``limit=0``, full sweep: ``updated=0, skipped=2,
       unreachable=0`` and ``_residual_missing`` -> ``(2, 0)``. (Same class as
       ``_dry_run``'s residual (b).)
    4. **A row the store's width guard declines** (#4289) — still missing
       afterwards, keyed, and skipped live.

    The field reads 0 only when ALL FOUR of those are absent: an unbounded run,
    every selected row keyed, every selected text embeddable, and no text in the
    ``trim()``/``strip()`` gap. What it cannot do on its own is tell "nothing to
    do" from "nothing I could reach" — that is what ``keyless``, ``unreachable``
    and ``limit`` are for.

    ⚠️ The predicate is taken from :func:`_label_branches` with ``force=False``
    — the SAME text the NULL-only repair pass reads, not a second spelling that
    can drift (#5126). #12496 does NOT add a non-empty-text predicate to the
    ungated surfaces (``Source``, ``Object``, ``Subject``, both ``Event``
    branches): that would change which rows those surfaces sweep, a separate
    decision with its own evidence — see #12572.
    """
    cfg = _require_surface(label)
    missing = keyless = 0
    for _, where, _ in _label_branches(label, force=False):
        rows = g.query(
            f"MATCH (n:{cfg['label']}) WHERE {where} "
            f"RETURN count(n) AS _missing, "
            f"count(CASE WHEN n.{cfg['id_prop']} IS NULL "
            f"THEN 1 END) AS _keyless"
        ).result_set
        m, k = rows[0] if rows else (0, 0)
        missing += m
        keyless += k
    return missing, keyless


def _merge_stats(agg: dict, stats: dict) -> dict:
    """Accumulate per-graph stats into an aggregate (all-tenants)."""
    agg["scanned"] += stats["scanned"]
    agg["updated"] += stats["updated"]
    agg["skipped"] += stats["skipped"]
    agg["unreachable"] += stats["unreachable"]
    agg["residual_missing"] += stats.get("residual_missing", 0)
    agg["residual_keyless"] += stats.get("residual_keyless", 0)
    # #12496: one cap for the whole run — not a sum (a sum would multiply it by
    # the tenant count and read as a different bound).
    agg["limit"] = stats.get("limit", 0)
    agg["agent_sessions"] += stats["agent_sessions"]
    agg["meeting_purged"] += stats["meeting_purged"]
    agg["repair_skipped"] += stats["repair_skipped"]
    agg["repair_updated"] += stats["repair_updated"]
    for label, n in stats["per_label"].items():
        agg["per_label"][label] = agg["per_label"].get(label, 0) + n
    return agg


def _force_reembed_graph(db, graph_name: str, labels: list[str],
                         limit: int, batch_size: int, *,
                         required_dim: int | None) -> dict:
    """Force-mode flow for one graph: NULL-only repair → meeting purge →
    all-rows pass (#1349 T12).

    ``required_dim`` (#4289) is the width of THIS graph (resolved per target by
    :func:`_required_dim_for_graph`) and is threaded into both passes so
    neither leg can bypass the store's width guard. It is REQUIRED (no
    default) for the same reason as :func:`_backfill_graph`'s. Ordering is
    load-bearing:
    the NULL-only repair pass applies the SAME meeting exclusion and runs BEFORE the purge
    leg, so a repair-after-purge can never re-embed just-purged meetings.
    Returns aggregate stats for the completeness marker (per-label re-embedded
    counts, purge count, repair skips).
    """
    repair = _backfill_graph(db, graph_name, labels, limit, batch_size,
                             force=False, required_dim=required_dim)
    # The repair leg re-embeds the NULL subset once, then the all-rows pass
    # re-embeds everything INCLUDING that subset. The overlap is intentional:
    # the plan (Task 12) mandates the NULL-only repair pass runs BEFORE the
    # purge leg with the same meeting exclusion, and its skips are recorded
    # separately (repair_skipped in the marker). Marker per_label counts come
    # from the force pass only, so "everything moved" per-label accounting is
    # unaffected by the redundant compute on the NULL subset.
    g = db.select_graph(graph_name)
    purged = _purge_meeting_embeddings(g)
    force = _backfill_graph(db, graph_name, labels, limit, batch_size,
                            force=True, required_dim=required_dim)
    # #12496: the end state, read back from the graph rather than asserted from
    # the run's own counters. Computed AFTER the force pass, so a row it wrote
    # is no longer missing and a row it could not write still is.
    residual_missing = residual_keyless = 0
    for label in labels:
        m, k = _residual_missing(g, label)
        residual_missing += m
        residual_keyless += k
    return {
        "scanned": repair["scanned"] + force["scanned"],
        "updated": repair["updated"] + force["updated"],
        "skipped": repair["skipped"] + force["skipped"],
        # #12496: the FORCE leg's count, not a sum of both legs. The repair leg
        # only sees the ``embedding IS NULL`` subset, and the force leg sweeps
        # every row — so summing them would count one unreachable row twice.
        # (``skipped`` keeps its pre-existing summed shape because
        # ``repair_skipped`` is recorded separately for exactly that overlap.)
        "unreachable": force["unreachable"],
        # #12496 review P1: the run's cap, so ``residual_*`` is interpretable.
        # ``_backfill_graph`` is bounded by ``limit`` and ``_residual_missing``
        # reads the WHOLE graph, so a bounded run leaves a non-zero residual that
        # is not damage (measured: 6 healthy Points with ``--limit 2`` ->
        # ``residual_missing=4, residual_keyless=0``). One value, not a sum: the
        # same cap applies to every graph in an ``--all-tenants`` run.
        "limit": limit,
        "residual_missing": residual_missing,
        "residual_keyless": residual_keyless,
        "per_label": force["per_label"],
        "agent_sessions": force["agent_sessions"],
        "meeting_purged": purged,
        "repair_skipped": repair["skipped"],
        "repair_updated": repair["updated"],
    }


def _print_completeness(stats: dict) -> None:
    """Machine-verifiable "everything moved" marker (one JSON line).

    Per-label re-embedded counts (all 6 LABEL_CONFIG labels), AgentSession
    sub-breakdown, meeting purge count, and repair skips — parseable by ops
    tooling in the PR2 evidence.

    #12496 adds three fields, because every count above is the RUN's own
    account of itself: ``unreachable`` (rows no re-run can MATCH) and
    ``residual_missing`` / ``residual_keyless``, read back from the GRAPH after
    the sweep. A false PASS now has to contradict the graph as well as the run.
    ``limit`` rides along, because a bounded run leaves a non-zero residual that
    is not damage — see `_residual_missing`.
    """
    marker = {
        "labels": stats.get("per_label", {}),
        "agent_sessions": stats.get("agent_sessions", 0),
        "meeting_purged": stats.get("meeting_purged", 0),
        "repair_skipped": stats.get("repair_skipped", 0),
        "repair_updated": stats.get("repair_updated", 0),
        # #12496: the population NO re-run can repair — the predicate selected
        # these rows and the write could not MATCH them because they carry no
        # key. ⚠️ It is not the ONLY non-converging cause (empty text on a
        # surface with no ``text_pred``, and an encoder-declined row, are equally
        # permanent and stay only in ``skipped``), but it is the one that needs a
        # HUMAN to supply a key, so it is the one worth naming on its own.
        "unreachable": stats.get("unreachable", 0),
        # #12496: read back from the GRAPH after the sweep, so the marker is not
        # only its own account of itself. ``residual_missing`` counts rows the
        # NULL-only predicate still selects; ``residual_keyless`` is the part no
        # re-run can reach. ⚠️ The field is an ASSERTION only when ``limit`` is 0
        # — a bounded run reads the whole graph here and leaves a residual that
        # is not damage (see `_residual_missing`'s docstring for all FOUR
        # healthy-run populations).
        "residual_missing": stats.get("residual_missing", 0),
        "residual_keyless": stats.get("residual_keyless", 0),
        "limit": stats.get("limit", 0),
    }
    print("COMPLETENESS " + json.dumps(marker, sort_keys=True))


def _repair_legacy_event_embeddings(g, *, dry_run: bool, limit: int) -> int:
    """Rewrite plain-list Event embeddings into vecf32 (per-node, idempotent).

    Pre-#244 _upsert_event stored embeddings as plain Python lists. Before
    #8316 such a node made `vec.euclideanDistance` fail the whole MATCH
    ("expected Null or Vectorf32 but was List") — one legacy node poisoned
    Event vector search. The read boundary now normalises a right-width
    numeric `List` (#8316), so this repair serves SHAPE CONSISTENCY — every
    stored Event embedding typed the way this repo's own writers produce them
    (the typed shape the Point index cohort requires, #8429; a verbatim
    caller-supplied Point embedding is deliberately a plain `List` — PR
    #3018 — and is not this repair's business). A wrong-width or non-numeric `List`
    still aborts a read (#8430), and the two halves differ HERE: `vecf32()`
    DOES retype a wrong-width numeric `List`, so it lands in the `rewritten`
    count without curing the dimension mismatch, while a non-numeric `List`
    makes the rewrite itself raise and is counted as skipped.
    vecf32() on an already-vecf32 node errors, so each node is rewritten in a
    try/except and failures are counted (expected on fully-migrated DBs).

    This is a Δ = 0 SELF-RECAST and is deliberately OUT of #4520's vector-write
    seam: the ``SET`` reads the same property a clear guard would remove, so
    routing it through ``clear_then_set_prefix`` would destroy the value. It
    replaces no stored vector, so it cannot hit the small-Δ dropped-overwrite
    defect the seam exists for. See ``tortoise/vector_write.py``.
    """
    rows = g.query(
        "MATCH (n:Event) WHERE n.embedding IS NOT NULL "
        "RETURN n.eventId"
    ).result_set
    if not rows:
        print("repair: no Events with embeddings to check")
        return 0
    targets = rows if not limit else rows[:limit]
    print(f"repair: checking {len(targets)} Event embedding(s)")
    rewritten = already = 0
    for (eid,) in targets:
        if dry_run:
            continue
        try:
            g.query(
                "MATCH (n:Event {eventId:$eid}) SET n.embedding = vecf32(n.embedding)",
                params={"eid": eid},
            )
            rewritten += 1
        except Exception:
            already += 1  # already vecf32 (or unrepairable) — expected on clean DBs
    print(f"repair: rewritten={rewritten} already_vecf32/skipped={already}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill embeddings for vector search")
    ap.add_argument("--dry-run", action="store_true", help="report only, no writes")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    ap.add_argument("--graph", default="tortoise")
    ap.add_argument("--all-tenants", action="store_true",
                    help="iterate all team_* graphs from the registry")
    ap.add_argument("--limit", type=int, default=0, help="0 = all (per-graph cap)")
    ap.add_argument("--batch-size", type=int, default=DEFAULT_BATCH,
                    help="rows per query/compute batch (default: %(default)s)")
    ap.add_argument(
        "--repair-embeddings", action="store_true",
        help="Rewrite Event embeddings stored as plain lists into vecf32 "
        "(pre-#244 _upsert_event wrote plain lists). Shape consistency: every "
        "stored EVENT embedding typed the way this repo's own writers produce "
        "them — a verbatim caller-supplied Point embedding is deliberately a "
        "plain List (PR #3018) and is not this repair's business. A right-width "
        "numeric List is normalised at read time since #8316, while a wrong "
        "width or a non-numeric List still aborts one (#8430).",
    )
    ap.add_argument(
        "--force-re-embed", action="store_true",
        help="Re-embed ALL rows (not just embedding IS NULL) with the "
        "index-time text composition — the #1349 T12 backfill for the "
        "MiniLM→bge swap. Runs the NULL-only repair pass first, then purges "
        "legacy meeting embeddings, then re-embeds every row. Idempotent.",
    )
    args = ap.parse_args(argv)

    # #5126: refuse a config that cannot mean what it says (a retired node
    # label, or two surfaces sharing a label with no discriminator) BEFORE
    # connecting — the pre-fix config matched 0 rows and silently no-opped.
    _validate_config()

    db, default_graph, default_dim = _connect_falkordb(args.uri)

    # Resolve which surfaces to embed — every entity surface with embedding
    # support. These are SURFACE keys, not node labels (#5126): ``Document``
    # sweeps ``:Source``.
    labels = list(LABEL_CONFIG)

    # ── Dry run ──────────────────────────────────────────────────────
    if args.dry_run:
        grand_total = 0
        grand_purged = 0
        grand_unreachable = 0
        if args.all_tenants:
            print("Dry run: all tenants")
            reg = db.select_graph("registry")
            team_rows = reg.query(
                "MATCH (n:Team) RETURN n.id"
            ).result_set
            if not team_rows:
                print("  No teams found in registry")
            for row in team_rows:
                org_id = row[0]
                graph_name = f"org_{org_id}"
                print(f"\nTeam: {org_id} → {graph_name}")
                total, purged, unreachable = _dry_run(
                    db, graph_name, labels, force=args.force_re_embed)
                grand_total += total
                grand_purged += purged
                grand_unreachable += unreachable
        else:
            graph_name = args.graph
            header = ("Dry run (force-re-embed): " if args.force_re_embed
                      else "Dry run: ")
            print(header + graph_name)
            total, purged, unreachable = _dry_run(
                db, graph_name, labels, force=args.force_re_embed)
            grand_total += total
            grand_purged += purged
            grand_unreachable += unreachable
        if grand_unreachable:
            # #12496: an excluded population is DISCLOSED, never silently
            # dropped from the counts — "0 rows" and "0 rows I could reach"
            # are different answers and the operator must be able to tell
            # which one they got.
            print(f"⚠️ {grand_unreachable} row(s) cannot be written at all — "
                  f"they carry no key for the write to MATCH, so no re-run "
                  f"can repair them (a live run counts them as skipped)")
        if args.force_re_embed:
            print(f"\nDry run: {grand_total} nodes would be re-embedded, "
                  f"{grand_purged} meeting embeddings purged (no writes)")
        else:
            print(f"\nDry run: {grand_total} nodes would be embedded (no writes)")
        return 0

    # ── Repair mode (#244) ───────────────────────────────────────────
    if args.repair_embeddings:
        g = db.select_graph(args.graph)
        return _repair_legacy_event_embeddings(g, dry_run=args.dry_run, limit=args.limit)

    # ── Live run ─────────────────────────────────────────────────────
    from tortoise.embeddings import compute_embedding
    if compute_embedding("probe") is None:
        print("❌ Embeddings unavailable — install 'tortoise-graph[embeddings]' "
              "(sentence-transformers + scikit-learn).", file=sys.stderr)
        return 1

    if args.force_re_embed:
        stats = {"scanned": 0, "updated": 0, "skipped": 0, "per_label": {},
                 "unreachable": 0,
                 "residual_missing": 0, "residual_keyless": 0,
                 "limit": args.limit,
                 "agent_sessions": 0, "meeting_purged": 0,
                 "repair_skipped": 0, "repair_updated": 0}
        if args.all_tenants:
            print("Backfill (force-re-embed): all tenants")
            reg = db.select_graph("registry")
            team_rows = reg.query(
                "MATCH (n:Team) RETURN n.id"
            ).result_set
            if not team_rows:
                print("  No teams found in registry")
            for row in team_rows:
                org_id = row[0]
                graph_name = f"org_{org_id}"
                print(f"\n── Team: {org_id} → {graph_name} ──")
                required_dim = _required_dim_for_graph(
                    args.uri, graph_name,
                    default_graph=default_graph, default_dim=default_dim)
                _merge_stats(
                    stats,
                    _force_reembed_graph(db, graph_name, labels,
                                         args.limit, args.batch_size,
                                         required_dim=required_dim),
                )
        else:
            graph_name = args.graph
            print(f"Backfill (force-re-embed): {graph_name}")
            required_dim = _required_dim_for_graph(
                args.uri, graph_name,
                default_graph=default_graph, default_dim=default_dim)
            _merge_stats(
                stats,
                _force_reembed_graph(db, graph_name, labels,
                                     args.limit, args.batch_size,
                                     required_dim=required_dim),
            )
        _print_completeness(stats)
        print(f"\nDone: {stats['scanned']} scanned, {stats['updated']} re-embedded, "
              f"{stats['skipped']} skipped (no text, encoder unavailable, or no "
              f"key), {stats['unreachable']} unreachable (no key: no re-run can "
              f"repair), "
              f"{stats['meeting_purged']} meeting embeddings purged")
        return 0

    grand_scanned = grand_updated = grand_skipped = 0
    grand_unreachable = 0

    if args.all_tenants:
        print("Backfill: all tenants")
        reg = db.select_graph("registry")
        team_rows = reg.query(
            "MATCH (n:Team) RETURN n.id"
        ).result_set
        if not team_rows:
            print("  No teams found in registry")
        for row in team_rows:
            org_id = row[0]
            graph_name = f"org_{org_id}"
            print(f"\n── Team: {org_id} → {graph_name} ──")
            required_dim = _required_dim_for_graph(
                args.uri, graph_name,
                default_graph=default_graph, default_dim=default_dim)
            s = _backfill_graph(db, graph_name, labels,
                                args.limit, args.batch_size,
                                required_dim=required_dim)
            grand_scanned += s["scanned"]
            grand_updated += s["updated"]
            grand_skipped += s["skipped"]
            grand_unreachable += s["unreachable"]
    else:
        graph_name = args.graph
        print(f"Backfill: {graph_name}")
        required_dim = _required_dim_for_graph(
            args.uri, graph_name,
            default_graph=default_graph, default_dim=default_dim)
        s = _backfill_graph(db, graph_name, labels,
                            args.limit, args.batch_size,
                            required_dim=required_dim)
        grand_scanned += s["scanned"]
        grand_updated += s["updated"]
        grand_skipped += s["skipped"]
        grand_unreachable += s["unreachable"]

    print(f"\nDone: {grand_scanned} scanned, {grand_updated} embedded, "
          f"{grand_skipped} skipped (no text, encoder unavailable, or no key), "
          f"{grand_unreachable} unreachable (no key: no re-run can repair)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
