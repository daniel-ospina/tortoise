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
LABEL_CONFIG: dict[str, dict[str, str]] = {
    "Point":    {"label": "Point", "text_prop": "content", "id_prop": "id"},
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
             force: bool = False) -> tuple[int, int]:
    """Count rows a run WOULD write. Returns (total, meeting_purge_count).

    NULL-only: rows missing embeddings. Force: ALL rows (meeting-excluded) —
    already-embedded rows ARE re-embedded by force, so they count; meetings
    and text-less rows are the unaffected set and are NOT counted. Dry-run
    never computes embeddings and never writes.

    "Text-less" is taken from ``_label_branches``' predicate, so the count and
    the live pass agree BY CONSTRUCTION wherever a surface declares a
    ``text_pred`` (the Document surface does: ``''`` is non-NULL in Cypher, so
    a bare ``IS NOT NULL`` counted rows the live pass skipped — #5126).
    Residual, recorded rather than silently over-reported: a surface with only
    a bare property still uses ``IS NOT NULL``, so a *non-empty-check* miss is
    limited to a whitespace-only value on those surfaces.
    """
    g = db.select_graph(graph_name)
    total = 0
    for label in labels:
        cfg = _require_surface(label)
        n = 0
        for _, where, _ in _label_branches(label, force=force):
            rows = g.query(
                f"MATCH (n:{cfg['label']}) WHERE {where} RETURN count(n)"
            ).result_set
            n += rows[0][0] if rows else 0
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
    return total, purged


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
    agent_sessions sub-count (Event surface only).
    """
    g = db.select_graph(graph_name)

    from tortoise.embeddings import encode_for_store

    total = updated = skipped = 0
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
                g.query(
                    f"UNWIND $batch AS row "
                    f"MATCH (n:{cfg['label']} {{{cfg['id_prop']}: row.id}}) "
                    + clear_then_set_prefix("n", "row.emb")
                    + "SET " + set_embedding_clause("n", "row.emb"),
                    params={"batch": batch},
                )
                updated += len(batch)
                label_updated += len(batch)

            if total and total % 25 == 0:
                print(f"    ... {total} processed")

        per_label[label] = label_updated

    return {"scanned": total, "updated": updated, "skipped": skipped,
            "per_label": per_label, "agent_sessions": agent_sessions}


def _merge_stats(agg: dict, stats: dict) -> dict:
    """Accumulate per-graph stats into an aggregate (all-tenants)."""
    agg["scanned"] += stats["scanned"]
    agg["updated"] += stats["updated"]
    agg["skipped"] += stats["skipped"]
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
    return {
        "scanned": repair["scanned"] + force["scanned"],
        "updated": repair["updated"] + force["updated"],
        "skipped": repair["skipped"] + force["skipped"],
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
    """
    marker = {
        "labels": stats.get("per_label", {}),
        "agent_sessions": stats.get("agent_sessions", 0),
        "meeting_purged": stats.get("meeting_purged", 0),
        "repair_skipped": stats.get("repair_skipped", 0),
        "repair_updated": stats.get("repair_updated", 0),
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
                total, purged = _dry_run(db, graph_name, labels,
                                         force=args.force_re_embed)
                grand_total += total
                grand_purged += purged
        else:
            graph_name = args.graph
            header = ("Dry run (force-re-embed): " if args.force_re_embed
                      else "Dry run: ")
            print(header + graph_name)
            total, purged = _dry_run(db, graph_name, labels,
                                     force=args.force_re_embed)
            grand_total += total
            grand_purged += purged
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
              f"{stats['skipped']} skipped (embedding unavailable), "
              f"{stats['meeting_purged']} meeting embeddings purged")
        return 0

    grand_scanned = grand_updated = grand_skipped = 0

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

    print(f"\nDone: {grand_scanned} scanned, {grand_updated} embedded, "
          f"{grand_skipped} skipped (embedding unavailable)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
