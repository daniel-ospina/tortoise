#!/usr/bin/env python3
"""Migration: update kind values for kinds moved from core to packs.

Run once after packs are loaded. Idempotent — safe to run multiple times.
Handles all entity types: Point, Object, Subject, Event, Document, Source.

Two passes, in this order (the order is load-bearing):

1. ``relabel_legacy_documents`` — relocate the pre-D10 ``:Document``
   population into ``:Source {url}`` (#5140 finding 8). Until this runs, the
   ``document`` rows below match nothing on such a graph: ``_do_migrate``
   derives ``MATCH (n:Source {documentKind: $old})`` from ``ENTITY_LABELS`` and
   cannot relocate a node between labels. It is also what makes the pre-D10
   extraction backlog discoverable by ``tortoise ingest --upgrade-all``
   (#5140 finding 3's discovery half).
2. the ``MIGRATIONS`` rows — the core→pack kind re-typing this tool is named
   for.

Usage:
    python3 tortoise/migrate_kinds.py
    python3 tortoise/migrate_kinds.py --dry-run

⛔ THE ONE LIVE WRITER THAT DELIBERATELY WIDENS THE #5475 APPROVED-PACK SET
(#9068) — stated here because the PR that closed #5475 says "the write path is
closed", and a reader taking that literally would try to gate this and break it.
(One other writer is deliberately ungated WITHOUT widening: `pipeline.py`'s
`SET p.kind` stores a pipeline key the mining leg no longer reads as an
ontology kind — see `docs/EXPANSION_PACKS.md`. `sdk._emit_event` is ungated too,
for a different reason: it appends a payload describing a mutation that has
already written its node. This is the one that intentionally ADDS a namespace.)

``_do_migrate`` ``SET``s a kind PROPERTY with raw Cypher (``proj.g.query``,
label-scoped, one statement per ``MIGRATIONS`` row), which no door screens.
That is BY DESIGN, and it is the opposite of the defect the gate closes:

* **It is an OPERATOR act, not a caller write.** There is no tenant-facing
  surface, no request, and no caller-chosen namespace: every target spelling is
the literal in ``MIGRATIONS`` below, reviewed with this file.
* **It is the SANCTIONED way to introduce a namespace into historical data.**
  The gate refuses a write that names a pack the graph has not approved — and
a row written BEFORE the pack existed can only be re-spelled by a deliberate
  re-typing. Widening the mined set is the POINT of the run, not a leak:
  afterwards ``graph_kind_namespaces`` finds the new namespace in the data and
  the graph may write it, which is exactly #2714 indicator 2.
* **The alternative is worse.** Routing it through ``create_*``/``update_*``
  would make the migration refuse its own first row on every graph that has
  not installed the pack yet (the pack is what the migration is introducing).

The offline twin (``retype.py`` / ``tools/retype_snapshot.py``) is not gated
either, for a different reason: it transforms a dump and never touches a live
graph, and it validates the target kind against the DECLARED vocabulary
instead (``RetypeError``).
"""
from __future__ import annotations

import sys

from tortoise.sdk import TortoiseSDK
from tortoise.source_identity import normalize_source_url

#: D10 (ONTOLOGY v3.15 §4.4) retired the ``:Document`` label: a document is a
#: ``:Source`` keyed ``url = <document id>``. Every graph written BEFORE D10
#: holds the other shape — ``(:Document {id, documentKind})`` — so the
#: ``document`` rows of ``MIGRATIONS`` (which ``_do_migrate`` turns into
#: ``MATCH (n:Source {documentKind: $old})``) count 0 on exactly the population
#: they exist to re-type. That is #5140 finding 8: **a migration that cannot
#: see the population it migrates**, because ``_do_migrate`` ``SET``s a kind
#: property and cannot relocate a node between labels (its own note on
#: ``requirement``/``bug`` says so).
#:
#: ``relabel_legacy_documents`` closes that gap and runs FIRST, so the rows see
#: the relocated population. It also closes the DISCOVERY half of #5140 finding
#: 3 for the population it can fold: ``ingest._do_upgrade`` MATCHes
#: ``(:Source {url})`` and ``ingest._discover_pending_documents`` MATCHes a bare
#: ``(:Source)`` with ``documentKind IS NOT NULL``, so the
#: ``doc_status='captured'`` arm they carry for a pre-D10 capture can only fire
#: once the node is a ``:Source``. (The bare MATCH is why the sweep can select —
#: and then hand a NULL ``url`` to ``_do_upgrade`` for — a url-less stranded
#: node. ⚠️ A node this pass REFUSES as ``undated`` stays ``:Document`` and is
#: still invisible to both; the backlog is closed for the foldable part only.)
#:
#: ⛔ LIVE-GRAPH ONLY, and that is not an omission. A replay of a pre-D10
#: journal folds it through TODAY's ``entities._upsert_document``, which writes
#: ``:Source {url}`` — so a rebuilt graph never holds a ``:Document`` node and
#: needs no reconciler (unlike ``session_source_migration``, whose legacy
#: identity the replay legitimately re-mints). What a rebuild DOES lose is the
#: retired ``doc_status`` (the fold REMOVEs it) — a separate, decision-blocked
#: residual recorded on #5140, not something a relabel can reach.
_LEGACY_DOCUMENT_LABEL = "Document"

# Entity type → (Neptune label, kind property name)
ENTITY_LABELS: dict[str, tuple[str, str]] = {
    "object":   ("Object",   "objectKind"),
    "point":    ("Point",    "pointKind"),
    "subject":  ("Subject",  "subjectKind"),
    "event":    ("Event",    "eventKind"),
    # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source.
    "document": ("Source", "documentKind"),
    "source":   ("Source",   "sourceKind"),
}

# Migration entries: old core kind → (new pack-prefixed kind, entity_type)
MIGRATIONS: list[tuple[str, str, str]] = [
    # ── Object kinds ───────────────────────────────────────────────────
    ("product",      "product-strategy:product",      "object"),
    ("customer",     "product-strategy:customer",     "object"),
    ("competitor",   "product-strategy:competitor",   "object"),
    ("customerSegment", "product-strategy:customerSegment", "object"),
    ("market",       "product-strategy:market",       "object"),
    ("epic",         "dev:epic",                      "object"),
    ("issue",        "dev:issue",                     "object"),
    ("code",         "dev:code",                      "object"),
    ("api",          "dev:api",                       "object"),
    ("database",     "dev:database",                  "object"),
    ("software",     "dev:software",                  "object"),
    ("infrastructure", "dev:infrastructure",          "object"),
    # `deployment` is a CORE event kind (ONTOLOGY §5); dev no longer declares it
    # as an objectKind (#7848), so there is no pack form to migrate a legacy
    # `objectKind: deployment` to. Such nodes are LEFT AS-IS — a row here could
    # only set `objectKind` to another Object kind, which would mislabel a
    # build/deploy event as an entity. Canonical form is `:Event
    # {eventKind: 'deployment'}`; relocating it is #2786's cross-layer work.
    ("indicator",    "dev:indicator",                 "object"),
    # #7848 owner ruling: `requirement` is an OBJECT kind declared once by
    # product-strategy (it used to be a dev pointKind). This row only matches
    # `:Object {objectKind:'requirement'}`, because `_do_migrate` derives the
    # label from ENTITY_LABELS — it SETs a kind property, it cannot relocate a
    # node between labels. Legacy `:Point {pointKind:'requirement'}` nodes are
    # therefore not migrated; deciding point→object relocation is the same
    # follow-up the `bug` note below carries (#2786).
    ("requirement",  "product-strategy:requirement",  "object"),

    # ── Point kinds ────────────────────────────────────────────────────
    ("useCase",      "product-strategy:useCase",      "point"),
    ("jobToBeDone",  "product-strategy:jobToBeDone",  "point"),
    ("userJourney",  "product-strategy:userJourney",  "point"),
    ("workflow",     "product-strategy:workflow",     "point"),
    ("valueProposition", "product-strategy:valueProposition", "point"),
    # bug is now an OBJECT kind (Problem subclass, dev 0.3.0 problem-family
    # landing) — a legacy bug POINT can no longer be re-typed dev:bug point
    # (commit-schema calibration). Legacy dev:bug points pre-date the re-
    # model; follow-up decides point→object relocation (risk is the point
    # form).
    ("technicalDebt", "dev:technicalDebt",            "point"),

    # ── Document kinds ─────────────────────────────────────────────────
    ("competitiveAnalysis", "product-strategy:competitiveAnalysis", "document"),
    ("marketResearch",      "product-strategy:marketResearch",      "document"),
    ("productSpec",         "product-strategy:productSpec",         "document"),
    ("featureSpec",         "product-strategy:featureSpec",         "document"),
    ("architectureDoc",     "dev:architectureDoc",                  "document"),
    ("apiSpec",             "dev:apiSpec",                          "document"),
    ("deployRunbook",       "dev:deployRunbook",                    "document"),

    # ── Event kinds ────────────────────────────────────────────────────
    ("cardCreated",     "pm:cardCreated",     "event"),
    ("cardMoved",       "pm:cardMoved",       "event"),
    ("sprintStarted",   "pm:sprintStarted",   "event"),
    ("sprintCompleted", "pm:sprintCompleted", "event"),
    ("stepStarted",     "pm:stepStarted",     "event"),
    ("stepCompleted",   "pm:stepCompleted",   "event"),
    ("gatePassed",      "pm:gatePassed",      "event"),
    ("gateBlocked",     "pm:gateBlocked",     "event"),
]


def relabel_legacy_documents(sdk: TortoiseSDK, dry_run: bool = False) -> dict:
    """Relocate pre-D10 ``:Document`` nodes into the ``:Source`` label. #5140 f8.

    A pre-D10 document node is ``(:Document {id, documentKind, ...})``; a D10
    document is ``(:Source {url, documentKind, ...})``. This moves the label and
    the identity property in ONE statement and writes the §4.6 REQUIRED fields a
    document ``:Source`` must carry.

    ⛔ IT IS NOT "indistinguishable from what the fold would write", and saying
    so would be this issue's own defect class. Two deliberate differences, both
    stated: the fold stamps ``ingestedAt=coalesce(s.ingestedAt, $now)`` — a
    MIGRATION clock, which this refuses (see below) — and it defaults ``title``
    to the id, which this does not touch. What the relocation guarantees is that
    the node lands inside the label every D10 reader, index and resolver
    addresses, carrying every REQUIRED field THIS FUNCTION CAN DERIVE, with
    nothing invented. ⚠️ That is not every §4.6 ✅ mark: ``contentHash`` stays
    ABSENT. No pre-D10 document writer emitted one (the ``DocumentCreated``
    payload never carried a digest, and none of the three computed one), and
    ``_upsert_document`` DID store the raw ``content`` on its subpopulation —
    but digesting that text here would mint a version anchor the journal never
    recorded, which is a versioning decision, not a migration's. §4.6's
    document scoping note permits its absence — ``contentHash`` is conditional
    on the read path — and the extraction path completes it through the
    preserve/replace gate (#5422), so a relocated node carries
    ``version = 1`` beside a NULL anchor until extraction, exactly as a
    freshly folded one does.

    WHAT IT WRITES, AND WHY EACH FIELD IS DERIVED RATHER THAN INVENTED
    ----------------------------------------------------------------
    * ``url`` ← the node's own ``id`` (identity — §4.6: identity is ``url``).
      ``id`` is KEPT, exactly as ``_upsert_document`` keeps it
      (``s.id=coalesce(s.id, $id)``), so ``_resolve_entity``'s lookups and the
      node's records still find it.
    * ``sourceKind='document'`` when absent — the TYPE axis every document
      writer stamps since #5082 (ONTOLOGY §4.6's document scoping note).
    * ``version=1`` when absent — §4.6: "1 at creation".
    * ``canonicalUrl``/``urlAliases`` — S0a/S0b parity (#5012/#5053, #5140
      finding 12). Without them the resolver cannot SEE the node, so the same
      identity arriving in another spelling would mint a SECOND ``:Source`` —
      recreating finding 12 for this population. ``canonicalUrl`` is
      ``normalize_source_url(url)`` (a pure function of the key, which is what
      ``resolve_source_key``'s adopt-on-touch writes for the same node).
    * ``_searchText`` — the ``:Source`` FTS field. ``title``, else ``url``:
      ONE STEP BEYOND the fold's ``coalesce($st, s._searchText, s.title)``,
      which would leave it NULL for a node carrying neither, so an id-only
      document would still be invisible to the FTS leg finding 1 made
      document-aware.
    * ``ingestedAt`` — §4.6 REQUIRED, and a legacy node has none. DERIVED from
      the node's own recorded clock (``ingestedAt``, else ``createdAt``, else
      ``updatedAt``). It is a LATER bound on first-sighting for a node
      re-emitted after creation, never an EARLIER one, and it is the node's own
      fact rather than the migration's clock — which is the whole point: a
      stamped ``now`` would assert an ingest that never happened, and every
      recency/decay read keyed on ``ingestedAt`` would inherit it.
      ⛔ THE CLOCK IS NOT ALWAYS THERE, and this is not an edge case. Pre-D10
      had THREE document writers: ``_upsert_document`` and ``hosted_api``'s
      transcript MERGE both stamp ``updatedAt``, but ``_event_plain_merge``'s
      ``objectType='Document'`` branch wrote ONLY
      ``ON CREATE SET d.title=$id, d.documentKind='transcript'`` — so every
      document that branch minted is ``undated`` and is REFUSED. The operator
      path is explicit, never automatic: assert the instant on the node
      (``SET d.ingestedAt = '<iso>'`` — the operator's own claim) and re-run;
      the migration then takes it. Silently dating it here would be the
      fabricated fact this refusal exists to prevent.

    ⛔ WHAT IT DOES NOT TOUCH: every other property is preserved verbatim,
    including the RETIRED ones (``content``, ``doc_status``, ``objectKind``) and
    every relationship. Retirement forbids WRITING those fields; it does not
    license DELETING stored history — and ``doc_status`` is load-bearing here,
    because ``'captured'`` is precisely the marker the sweep's compatibility arm
    reads to discover this backlog.

    REFUSALS (reported and LEFT IN PLACE — fail-closed, never guessed):
    * ``unidentified`` — no ``id`` and no ``url``: no identity to key on.
    * ``ambiguous`` — the node carries BOTH an ``id`` and a stored ``url`` that
      normalise to DIFFERENT identities. Identity is the ``id`` (pre-D10's key;
      see the loop), so the stray ``url`` is preserved as an alias — reported
      with ``kept_as='urlAlias'`` — unless another node occupies that spelling,
      in which case the node is REFUSED rather than making one spelling have two
      owners.
    * ``collision`` — this canonical identity is ALREADY OCCUPIED, by a stored
      ``:Source`` or by an earlier node THIS RUN relocated (candidates are
      taken in ``ID(n)`` order, so the lower id keeps the identity and every
      later one is reported against it — deterministic, arbitrary in the sense
      that the graph does not say which of two pre-D10 duplicates is the
      "real" document). The incumbent set is built from every stored
      ``url``/``canonicalUrl``/``urlAliases`` through ``normalize_source_url`` —
      the ONE normaliser the resolver uses — because a pre-#5012 incumbent is
      stored under a spelling of its own with ``canonicalUrl IS NULL`` and is
      invisible to a ``{url: …}`` match; taking it would leave TWO ``:Source``
      nodes for one identity and make the loser unreachable by the resolver's
      fast path, stranding its ``extractedFrom``/provenance edges. Which node
      keeps the edges is not derivable here, so it is an operator decision,
      not a migration's. (Boundary, stated: a spelling this normaliser does not
      collapse — ``http`` vs ``https``, ``www.`` — is a different identity by
      S0a's own rule, so it is not a collision.)
    * ``undated`` — no clock to derive ``ingestedAt`` from (see above).

    Idempotent: a node this function already moved is no longer a candidate
    (its ``:Document`` label is gone), so a re-run finds nothing to do.
    ``dry_run=True`` performs the same selection and reports the same shape
    without a single write.

    Returns ``{'relabelled': [url, …], 'ingested_at_derived': int,
    'unidentified': [...], 'collision': [...], 'undated': [...],
    'kinds': {documentKind: count},
    'kinds_were_source': {documentKind: count}, 'ambiguous': [...]}`` —
    ``kinds`` counts the relabelled nodes per genre; ``kinds_were_source`` is the
    subset that ALREADY carried ``:Source`` before the run (the kind-row preview
    subtracts them, because its ``:Source`` arm counts them already); this IS the
    report a programmatic caller wants (``migrate()`` returns the KIND rows only,
    by long-standing contract).
    """
    proj = sdk._get_proj()
    # EVERY `:Document` node is a candidate — including one that has ALREADY been
    # given the `:Source` label, by an interrupted older attempt (the pre-single-
    # statement shape: `SET n:Source` → `SET n.url` → `REMOVE n:Document`, cut
    # short) or by a hand edit. Those are the worst case to skip: a url-less one
    # is invisible to every `MATCH (s:Source {url: …})` reader, and a url-SET one
    # still carries the retired label and whatever §4.6 fields the interruption
    # left NULL. Running them through the same path COMPLETES them, and a node
    # this function already finished is simply not a candidate any more (its
    # label is gone) — which is what makes the pass idempotent rather than
    # dependent on a "skip it, it looks migrated" judgement.
    #
    # `ORDER BY ID(n)` makes the run DETERMINISTIC: when two candidates
    # normalise onto one identity the lower-id one keeps it, and a re-run on the
    # same graph picks the same winner instead of following the engine's scan
    # order.
    candidates = proj.g.query(
        f"MATCH (n:{_LEGACY_DOCUMENT_LABEL}) "
        "RETURN ID(n), n.id, n.url, n.ingestedAt, n.createdAt, n.updatedAt, "
        "       n.title, n.documentKind, n.urlAliases "
        "ORDER BY ID(n)"
    ).result_set
    # Candidates that ALREADY carry `:Source` (interrupted older attempt, hand
    # edit). Counted per genre because the dry-run preview needs them: the
    # `:Source` half of the preview counts them already, so the relabel half
    # must subtract them or the operator is told about work the write does not
    # do. Read BEFORE any write, so a real run reports the same numbers a dry
    # run would have.
    already_source_ids = {
        r[0] for r in proj.g.query(
            f"MATCH (n:{_LEGACY_DOCUMENT_LABEL}:Source) RETURN ID(n)"
        ).result_set
    }
    # Every identity a `:Source` already occupies, keyed canonically. Built from
    # the graph rather than asked per-node: `resolve_source_key`'s
    # adopt-on-touch only finds the two spellings it is handed, so a per-node
    # `{url: …}` or even a `canonicalUrl`-arm read misses a pre-#5012 incumbent
    # stored under a third spelling — the duplicate #5012/#5053/#5140-f12 exist
    # to prevent. A migration is a one-off operator act, so the whole-label read
    # is the cheap side of that trade. `ID(s)` is returned so a node can never
    # collide with ITSELF. Each candidate this run relocates joins the map (see
    # below), so a SIBLING candidate — not a stored `:Source` — can be the
    # occupier too.
    occupied: dict[str, tuple[int, str]] = {}
    for incumbent_id, s_url, s_canonical, s_aliases in proj.g.query(
        "MATCH (s:Source) RETURN ID(s), s.url, s.canonicalUrl, s.urlAliases"
    ).result_set:
        for spelling in (s_url, s_canonical, *(s_aliases or [])):
            if isinstance(spelling, str) and spelling.strip():
                occupied.setdefault(normalize_source_url(spelling),
                                    (incumbent_id, spelling))
    report: dict = {
        "relabelled": [], "ingested_at_derived": 0, "unidentified": [],
        "collision": [], "undated": [], "kinds": {},
        "kinds_were_source": {}, "ambiguous": [],
    }
    for (node_id, raw_id, stored_url, ingested, created, updated,
         title, _document_kind, stored_aliases) in candidates:
        # ⛔ IDENTITY IS THE `id`, NOT A STORED `url`. A pre-D10 document's key
        # was `id` (`_upsert_document` MERGEd on it), so a `url` prop on a
        # legacy node is something ELSE: `url` was absent from the pre-D10
        # `_DOCUMENT_HANDLED` set, so `_persist_extra_props` stored a
        # caller-supplied `url` from the `DocumentCreated` payload. Keying on it
        # would move the node OFF the key a later `DocumentCreated` folds onto
        # (`_upsert_document` MERGEs `{url: $id}`) — one document, two `:Source`
        # nodes, the duplicate class this function's collision arm exists to
        # prevent. Only a node with no `id` at all is keyed on its `url`.
        has_id = isinstance(raw_id, str) and bool(raw_id.strip())
        url = raw_id if has_id else stored_url
        if not isinstance(url, str) or not url.strip():
            report["unidentified"].append({"node": node_id, "id": raw_id})
            print(f"  :Document node {node_id} REFUSED — no id and no url "
                  f"(nothing to key the :Source on)")
            continue
        canonical = normalize_source_url(url)
        # A stored `url` that is a DIFFERENT identity is preserved as an alias
        # rather than dropped: it is the spelling some reader may still ask for,
        # and `urlAliases` is where alternates live. It is subject to the same
        # occupancy rule — an alias pointing at a spelling another node owns
        # would make two nodes claim one identity, so that one is refused.
        stray = None
        if (has_id and isinstance(stored_url, str) and stored_url.strip()
                and normalize_source_url(stored_url) != canonical):
            stray = stored_url
            holder = occupied.get(normalize_source_url(stray))
            if holder is not None and holder[0] != node_id:
                report["ambiguous"].append(
                    {"node": node_id, "id": raw_id, "url": stored_url,
                     "incumbent": holder[1]})
                print(f"  :Document node {raw_id!r} REFUSED — it carries a "
                      f"stray url {stored_url!r} that is a DIFFERENT identity "
                      f"and is already occupied by {holder[1]!r}; folding it in "
                      f"as an alias would give one spelling two owners")
                continue
            report["ambiguous"].append(
                {"node": node_id, "id": raw_id, "url": stored_url,
                 "kept_as": "urlAlias"})
        incumbent = occupied.get(canonical)
        # `incumbent[0] != node_id` — a node can never collide with ITSELF (a
        # node already carrying `:Source` is in the `:Source` scan under its own
        # id; that is its own identity, not another node's).
        if incumbent is not None and incumbent[0] != node_id:
            report["collision"].append({"node": node_id, "url": url,
                                        "incumbent": incumbent[1]})
            print(f"  :Document node {url!r} REFUSED — the identity is already "
                  f"occupied by {incumbent[1]!r} (a :Source, or an earlier "
                  f"node in this run); taking both would leave two :Source "
                  f"nodes for one identity, so which keeps the edges is an "
                  f"operator decision")
            continue
        derived_at = ingested is None
        at = ingested or created or updated
        if not at:
            report["undated"].append({"node": node_id, "url": url})
            print(f"  :Document node {url!r} REFUSED — no ingestedAt/createdAt/"
                  f"updatedAt to derive §4.6's REQUIRED ingestedAt from; assert "
                  f"the instant on the node and re-run")
            continue
        report["relabelled"].append(url)
        report["kinds"][_document_kind] = (
            report["kinds"].get(_document_kind, 0) + 1)
        if node_id in already_source_ids:
            report["kinds_were_source"][_document_kind] = (
                report["kinds_were_source"].get(_document_kind, 0) + 1)
        # The identity this run is about to INSTALL joins the incumbent set, or
        # two `:Document` nodes normalising onto ONE identity are both
        # relabelled and the map never notices — the exact duplicate the
        # collision arm exists to refuse, produced by the migration itself.
        # `setdefault`: the FIRST mover keeps the identity (it is the incumbent
        # for the second), and the second is then refused and reported.
        occupied.setdefault(canonical, (node_id, url))
        if derived_at:
            report["ingested_at_derived"] += 1
        if dry_run:
            continue
        # The alias list is computed in PYTHON, not in Cypher: the single
        # statement below cannot grow a CASE per spelling without becoming the
        # thing it exists to avoid (two statements on the label). Order is
        # `[every earlier alias, the stray url, this node's url]`, de-duplicated
        # — `$url` last so the canonical key is always present.
        aliases: list = [a for a in (stored_aliases or [])
                         if isinstance(a, str) and a.strip()]
        if stray is not None:
            aliases.append(stray)
        aliases.append(url)
        aliases = list(dict.fromkeys(aliases))
        # ⛔ ONE STATEMENT, one round trip: a raise between a label-add and a
        # label-remove would leave a node carrying BOTH labels and no `url`,
        # which no `MATCH (s:Source {url: …})` reader can see.
        proj.g.query(
            f"MATCH (n:{_LEGACY_DOCUMENT_LABEL}) WHERE ID(n) = $nid "
            "SET n:Source "
            "SET n.url=$url, "
            "    n.sourceKind=coalesce(n.sourceKind, 'document'), "
            "    n.version=coalesce(n.version, 1), "
            "    n.ingestedAt=coalesce(n.ingestedAt, $at), "
            "    n._searchText=coalesce(n._searchText, $txt), "
            "    n.canonicalUrl=coalesce(n.canonicalUrl, $cu), "
            "    n.urlAliases=$aliases "
            f"REMOVE n:{_LEGACY_DOCUMENT_LABEL}",
            params={
                "nid": node_id, "url": url, "at": at, "cu": canonical,
                "txt": title or url, "aliases": aliases,
            },
        )
    action = "would be relabelled" if dry_run else "relabelled"
    extra = ""
    if report["ingested_at_derived"]:
        extra += (f" (ingestedAt derived from the node clock on "
                  f"{report['ingested_at_derived']})")
    refused = (len(report["unidentified"]) + len(report["collision"])
               + len(report["undated"]))
    if refused:
        extra += f"; {refused} REFUSED and left in place"
    print(f"  D10 relabel :Document → :Source: "
          f"{len(report['relabelled'])} node(s) {action}{extra}")
    return report


def _do_migrate(sdk: TortoiseSDK, dry_run: bool = False) -> dict:
    """Run kind migration across all entity types.

    Returns {old_kind: {entity_type: str, new: str, count: int}}.

    ⚠️ UNGATED BY DESIGN — see the module docstring (#9068). The ``SET`` below
    is a deliberate re-typing of the graph's own historical rows by an
    operator, and it is the sanctioned way a pack namespace enters data written
    before the pack existed; the #5475 write gate would refuse exactly the row
    the migration exists to rewrite.
    """
    # #5140 finding 8: relocate the pre-D10 `:Document` population FIRST, or
    # every `document` row below counts 0 on the nodes it exists to re-type (and
    # the pre-D10 extraction backlog stays undiscoverable — finding 3's
    # discovery half). Live-graph only; see `_LEGACY_DOCUMENT_LABEL`. Its report
    # is kept because the dry-run preview needs the per-genre count of the nodes
    # this pass WOULD move (and only those).
    relabel = relabel_legacy_documents(sdk, dry_run=dry_run)

    proj = sdk._get_proj()
    results: dict[str, dict] = {}

    for old_kind, new_kind, entity_type in MIGRATIONS:
        label, kind_prop = ENTITY_LABELS[entity_type]
        if dry_run and entity_type == "document":
            # #5140 finding 8: a dry run must not LIE about this population, in
            # EITHER direction. Nothing was relabelled (that is what `dry_run`
            # means), so `MATCH (n:Source {…})` alone still sees 0 on a graph
            # whose document nodes are `:Document` — the preview would report
            # "no kind work to do" on exactly the population the two-pass order
            # exists for. But counting every `:Document` node over-reports the
            # other way: the ones the relabel REFUSED (`undated`/`collision`/
            # `unidentified`) stay `:Document` and are never re-typed, so the
            # real run would update fewer than the preview promised — and the
            # same preview would print "N REFUSED" beside it.
            #
            # The arithmetic, and why each term is needed — the two sets OVERLAP
            # and a guard cannot make them disjoint:
            #
            #   real = |S0| + |relabelled∩kind| − |S0∩relabelled∩kind|
            #
            # `|S0|` is every node the kind MATCH already sees, WITHOUT a label
            # guard: the real run re-types a `:Source` whatever else it carries,
            # including a both-labelled url-less node that the relabel REFUSES as
            # `undated` (a guard excluding url-less `:Document` nodes
            # UNDER-counted exactly that shape: preview 0, real 1). The second
            # term adds the nodes this pass relabels. The third subtracts the
            # ones ALREADY in `|S0|` — a candidate may carry `:Source` from an
            # interrupted older attempt, in both mutating shapes (url-less and
            # url-set), and without the subtraction it is counted twice (preview
            # 2, real 1). `kinds_were_source` is read before any write, so a real
            # run and a dry run agree.
            rows = proj.g.query(
                f"MATCH (n:{label} {{{kind_prop}: $old}}) RETURN count(n)",
                params={"old": old_kind},
            ).result_set
            count = ((rows[0][0] if rows else 0)
                     + relabel["kinds"].get(old_kind, 0)
                     - relabel["kinds_were_source"].get(old_kind, 0))
            # No `count > 0` guard here: a dry run REPORTS every row, so a zero
            # for this kind is a real claim the operator needs ("the preview
            # would do nothing here"), not a row to omit.
            results[old_kind] = {
                "entity_type": entity_type,
                "new": new_kind,
                "count": count,
            }
            print(f"  {entity_type}:{old_kind} → {new_kind}: {count} nodes "
                  f"would update")
            continue
        if dry_run:
            rows = proj.g.query(
                f"MATCH (n:{label} {{{kind_prop}: $old}}) "
                f"RETURN count(n)",
                params={"old": old_kind},
            ).result_set
        else:
            rows = proj.g.query(
                f"MATCH (n:{label} {{{kind_prop}: $old}}) "
                f"SET n.{kind_prop} = $new "
                f"RETURN count(n)",
                params={"old": old_kind, "new": new_kind},
            ).result_set

        count = rows[0][0] if rows else 0
        if count > 0 or dry_run:
            results[old_kind] = {
                "entity_type": entity_type,
                "new": new_kind,
                "count": count,
            }
            action = "would update" if dry_run else "updated"
            print(f"  {entity_type}:{old_kind} → {new_kind}: {count} nodes {action}")

    return results


def migrate(sdk: TortoiseSDK, dry_run: bool = False) -> dict:
    """Public API: run kind migration across all entity types.

    Thin wrapper over `_do_migrate` (kept as a public entry point for
    programmatic consumers; the leading-underscore rename in #442 broke
    `from tortoise.migrate_kinds import migrate`).

    ⚠️ Returns the KIND rows only (``{old_kind: {entity_type, new, count}}``),
    by long-standing contract. The relabel's own report — ``relabelled``,
    ``ingested_at_derived``, and the ``unidentified``/``collision``/``undated``
    REFUSAL buckets — is what ``relabel_legacy_documents`` returns; call it
    directly when a caller needs the refusals programmatically rather than on
    stdout. It is idempotent and safe to run before or after this call.
    """
    return _do_migrate(sdk, dry_run=dry_run)


if __name__ == "__main__":
    import os

    dry_run = "--dry-run" in sys.argv

    from tortoise.config import resolve_db_path, is_docker_uri  # noqa: I001
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        # Embedded migration against canonical path (issue #176)
        print(f"Using embedded DB at {resolve_db_path()} (set TORTOISE_DB_URI to run against a server)")
        sdk = TortoiseSDK(db_path=resolve_db_path())
        results = _do_migrate(sdk, dry_run=dry_run)
        exit(0)
    if not is_docker_uri(uri):
        print("Set TORTOISE_DB_URI to a docker:// URI to run migration")
        exit(1)

    sdk = TortoiseSDK(uri)
    results = _do_migrate(sdk, dry_run=dry_run)

    total = sum(r["count"] for r in results.values())
    by_type: dict[str, int] = {}
    for r in results.values():
        by_type[r["entity_type"]] = by_type.get(r["entity_type"], 0) + r["count"]

    action = "would be migrated" if dry_run else "migrated"
    print(f"\nTotal: {total} nodes {action} across {len(results)} kinds")
    for etype, cnt in sorted(by_type.items()):
        print(f"  {etype}: {cnt}")
