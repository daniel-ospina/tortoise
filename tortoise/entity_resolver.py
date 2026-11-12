"""Entity resolution for ``:Object`` / ``:Subject`` — chain, queue, honest empty (#12047).

Why this module exists
----------------------
``TortoiseSDK.list_dedup_candidates(candidate_type='entity')`` answered from a
hard-coded ``return []`` (#783's stub). ``tortoise_list_dedup_candidates`` is a
live MCP tool, so **"are there duplicate entities?"** answered *"none"* when the
truth was *"cannot answer"*. That is the fail-open shape this codebase refuses
elsewhere: ``record-reconcile`` exists so *"could not compare"* (exit 2) can
never be read as *"clean"* (exit 0). ``#12047`` measured the damage on the hosted
graphs — one person under five names, one role under three spellings, documents
filed as roles — with no queue to review even by hand.

The one thing this module must never do is answer an empty list it did not
measure. A scan that cannot run raises :class:`EntityScanUnavailable`; it never
degrades to ``[]``.

Identity — what is recorded, and what is refused
------------------------------------------------
``#3590`` (acceptance: ``tests/test_3590_minted_identity_acceptance.py``) made an
entity's durable identity an **opaque id minted once**, with ``name`` a mutable
natural key. That decision **supersedes** the 2026-08 plan's ``obj-`` + sha256
canonical id (``docs/epics/2026-08-09-insight-mining-p2p4/04-plan.md`` §4.4) and
``#783``'s "``create_object(id=canonical)``" indicator:

* ⛔ **Nothing here derives an id from a name.** ``ids.py`` states it — the
  ``obj-<sha26(name)>`` spelling is RECOGNISED (pre-change graphs) but never
  MINTED, because a rename minted a second entity and a delete+re-create
  resurrected the first.
* ✅ **A near-duplicate resolves onto the existing entity by returning that
  entity's existing opaque id** — never by writing a name-derived id. The
  deterministic rungs do this at create time (``entity_identity.resolve_or_mint``
  consults the folded name); the similarity rungs surface a pair for review.

The ladder — and why only the deterministic rungs auto-resolve
--------------------------------------------------------------
``docs/research/2026-09-24-2730-entity-attachment/research-brief.md`` §6 pins the
ladder (scoped exact → alias → embedding/similarity → adjudication → review
queue) and §6.7 pins the constraint that an alias attaches to the **opaque id**
and never re-derives identity from the name. Its adjudication row is explicit
that a false merge is *invisible and hard to undo* while a false split is
*visible and cheap to repair*, and that external practice ("X-series" rows) is
**precision-first**.

This module therefore auto-resolves on the **deterministic** rungs only:

===========  ==================================================  ================
rung         mechanism                                           action
===========  ==================================================  ================
``exact``    ``normalize_entity_name`` equality                   auto-resolve
``alias``    a folded name recorded by an approved merge         auto-resolve
``fuzzy``    ``difflib.SequenceMatcher`` ratio ≥ REVIEW 0.60      **review queue**
``semantic`` embedding cosine ≥ ``embeddings.DEFAULT_THRESHOLD``  **review queue**
``new``      nothing on the ladder matched                       mint (unchanged)
===========  ==================================================  ================

⚠️ The thresholds are not invented here. ``REVIEW_THRESHOLD`` (0.60) and
``AUTO_MERGE_THRESHOLD`` (0.92) are the constants the #783 plan preamble pins
for its DE2E-2 fixture (``04-plan.md`` §7); the semantic rung reuses
``tortoise.embeddings.DEFAULT_THRESHOLD`` because the #2730 brief REFUSED a
second similarity constant ("Do not invent a second constant; reuse the existing
one"). ``AUTO_MERGE_THRESHOLD`` is *reported* on each row (``auto_mergeable``) so
a caller can see the band; the merge itself is still a review decision.

Blocking, not all-pairs
-----------------------
Pairwise comparison is `n(n-1)/2`: 872 Subjects is ~380k comparisons per scan,
and naive transitive closure compounds a single false positive across a whole
cluster (the #2730 brief's named production failures). Candidate generation is
therefore **blocked** on ``(label, first 3 folded characters)`` — the standard
record-linkage reduction. Equal folded names always share a block, so the exact
rung can never be missed by blocking; the bound is that a pair whose folded names
share no 3-character prefix is never scored.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher

_logger = logging.getLogger(__name__)

#: Similarity floor for a ``fuzzy`` pair to enter the review queue. Pinned by
#: the #783 plan preamble (DE2E-2: "REVIEW 0.60 ≤ sim < AUTO 0.92").
REVIEW_THRESHOLD = 0.60

#: The top of the fuzzy band. Reported as ``auto_mergeable``; NOT applied
#: automatically — a similarity score alone never merges (#2730 §6.7 gate).
AUTO_MERGE_THRESHOLD = 0.92

#: Folding: a leading article carries no identity ("the owner" IS "owner").
_ARTICLES = frozenset({"the", "a", "an"})

#: Blocking width. ``MIN_BLOCK_CHARS`` folded characters is the shortest key that
#: keeps pair completeness for the damage classes measured on #12047 while still
#: cutting the 872-Subject scan by ~3 orders of magnitude.
MIN_BLOCK_CHARS = 3

#: The two labels an entity identity lives on (``entity_identity._IDENTITY_LABELS``).
ENTITY_LABELS = ("Object", "Subject")

#: label → the kind property ONTOLOGY §4.1/§4.2 declares for it.
_KIND_PROP = {"Object": "objectKind", "Subject": "subjectKind"}

#: Above this many scanned entities the semantic rung is skipped (logged), because
#: encoding every name on a read path is unbounded work. The fuzzy + exact rungs
#: still run; the skip is named rather than silent.
SEMANTIC_MAX_ENTITIES = 2000

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)


class EntityScanUnavailable(RuntimeError):
    """The entity scan could not run — never reported as "no duplicates".

    #12047: the stub this module replaces returned ``[]`` unconditionally, which
    a caller had to read as *"no duplicate entities"*. An empty queue is only
    honest when it is the RESULT of a scan; a scan that cannot run raises this so
    the caller sees *"could not compare"* instead of *"clean"*.
    """


# ── Folding ──────────────────────────────────────────────────────────────


def normalize_entity_name(name: object) -> str:
    """The deterministic matching key for an entity name — NEVER the stored name.

    ``#1026`` ruled *"no name patterns; nothing is dropped at mint"*, so this
    value is used for MATCHING only: the node keeps the caller's surface form in
    ``name`` and this folded form rides ``canonical_name`` (the second spelling
    ``entity_identity._NAME_PROPERTIES`` already treats as an addressable name
    key, and the slot the #783 plan §4.1 names for exactly this purpose).

    The fold is: NFKC → casefold → drop a leading article → replace every
    non-word run with a space → collapse whitespace. It is deliberately
    conservative. It does NOT strip legal suffixes or honorifics, and it does NOT
    expand initials: the #2730 brief (§6.7) requires those to read the per-kind
    surface forms from ``ontology.kindDefs.<kind>.synonyms`` rather than carry a
    competing list here — a second source of truth for one value is the defect
    that decision refuses.

    An input that folds to nothing (``"the"``, ``"..."``) returns its casefolded
    punctuation-collapsed form instead of ``""``, so an empty key can never match
    every entity in the graph.
    """
    raw = "" if name is None else str(name)
    folded = unicodedata.normalize("NFKC", raw).casefold()
    tokens = [t for t in _NON_ALNUM.split(folded.replace("_", " ")) if t]
    # A leading article is not identity: "the owner" and "owner" are one entity.
    while len(tokens) > 1 and tokens[0] in _ARTICLES:
        tokens = tokens[1:]
    out = " ".join(tokens)
    if out:
        return out
    # Everything was punctuation. Keep a stable, non-empty key rather than a
    # value that would collide with every other punctuation-only name.
    return " ".join(t for t in re.split(r"\s+", folded.strip()) if t) or folded.strip()


def block_key(folded: str) -> str:
    """The record-linkage blocking key: the first ``MIN_BLOCK_CHARS`` characters."""
    return folded[:MIN_BLOCK_CHARS]


def similarity(a: str, b: str) -> float:
    """Deterministic string similarity over FOLDED names (``difflib`` ratio)."""
    return SequenceMatcher(None, a, b).ratio()


# ── Scan ─────────────────────────────────────────────────────────────────

#: The entity-universe read. ``status`` is a derived cache (§2.6); a terminal
#: entity is a different incarnation (#3590 R5) and is never a merge candidate.
_ENTITY_READ = (
    "MATCH (n:{label}) "
    "WHERE n.id IS NOT NULL "
    "AND (n.status IS NULL OR NOT n.status IN ['superseded','deprecated',"
    "     'archived','retracted']) "
    "RETURN n.id, n.name, n.canonical_name, n.{kind}, n.createdAt, "
    "       coalesce(n.dedup_reviewed, ''), n.dedup_target_id"
)


def _pair_reviewed(survivor: dict, candidate: dict) -> bool:
    """True when this exact pair already carries a review verdict.

    * ``merge`` — the candidate was folded into a survivor, so it must never be
      offered again (its new writes are redirected by ``resolved_from``).
    * ``reject`` — the PAIR was refused. The entities stay separate and may each
      still pair with a THIRD entity; only the reviewed combination is suppressed,
      which is why the rejected verdict names the survivor it was decided against
      (``dedup_target_id``).
    """
    verdict = candidate.get("dedup_reviewed") or ""
    if verdict == "merge":
        return True
    if verdict == "reject":
        target = candidate.get("dedup_target_id")
        return target is None or str(target) == str(survivor.get("id"))
    return False


def _read_entities(graph, label: str) -> list[dict]:
    """Read every live ``label`` entity the scan can compare. Raises on failure."""
    try:
        rows = graph.query(
            _ENTITY_READ.format(label=label, kind=_KIND_PROP[label])
        ).result_set
    except Exception as exc:  # noqa: BLE001 — re-raised as the honest failure
        raise EntityScanUnavailable(
            f"entity scan could not read :{label} — refusing to report an "
            f"empty queue ({type(exc).__name__}: {exc})") from exc
    out = []
    for row in rows:
        node_id, name, canonical, kind, created_at, reviewed, reviewed_target = row
        if not name:
            continue
        folded = normalize_entity_name(canonical or name)
        out.append({
            "id": str(node_id),
            "label": label,
            "name": str(name),
            "canonical_name": canonical,
            "folded": folded,
            "kind": kind,
            "createdAt": created_at,
            "dedup_reviewed": reviewed or "",
            "dedup_target_id": reviewed_target,
        })
    return out


def scan_entities(graph, *, labels=ENTITY_LABELS) -> list[dict]:
    """Every live entity the resolver can compare, across ``labels``."""
    if not labels:
        return []
    entities: list[dict] = []
    for label in labels:
        if label not in ENTITY_LABELS:
            raise ValueError(
                f"entity resolution covers {ENTITY_LABELS}, got {label!r}")
        entities.extend(_read_entities(graph, label))
    return entities


def _semantic_matrix(entities: list[dict], encode_fn) -> dict:
    """Cosine similarity per (id, id) over the FOLDED names — bounded, degrade-safe.

    The rung is not a second identity authority: it only proposes pairs the
    deterministic + fuzzy rungs could not see. The threshold it compares against
    is ``embeddings.DEFAULT_THRESHOLD`` — reused, never re-invented (#2730 §5: a
    second similarity constant was REFUSED).

    Returns ``{(id_a, id_b): cosine}`` for every ordered pair; empty on any
    degrade (numpy/model failure) — and says so in the log, because a silently
    skipped rung reads as "nothing found".
    """
    if len(entities) < 2:
        return {}
    try:
        import numpy as np
    except Exception as exc:  # noqa: BLE001 — degrade, and say so
        _logger.warning("entity semantic rung skipped — numpy unavailable: %s", exc)
        return {}
    ids = [e["id"] for e in entities]
    texts = [e["folded"] for e in entities]
    if encode_fn is None:
        from .embeddings import _encode as encode_fn  # local: heavy import
    try:
        vectors, _degraded = encode_fn(texts)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("entity semantic rung skipped — encoder failed: %s", exc)
        return {}
    if vectors is None or len(vectors) != len(texts):
        _logger.warning(
            "entity semantic rung skipped — encoder returned %s rows for %s names",
            None if vectors is None else len(vectors), len(texts))
        return {}
    mat = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = mat / norms
    sims = unit @ unit.T
    return {(ids[i], ids[j]): float(sims[i][j])
            for i in range(len(ids)) for j in range(i + 1, len(ids))}


def entity_candidates(graph, *, limit: int = 50,
                      review_threshold: float = REVIEW_THRESHOLD,
                      include_semantic: bool = True,
                      encode_fn=None,
                      labels=ENTITY_LABELS) -> list[dict]:
    """Ambiguity pairs over live ``:Object`` / ``:Subject`` — blocked, deterministic.

    Each row is shaped like a content-dedup row so the same review surface and the
    same ``approve_merge`` contract apply:
    ``{id, existing_id, target_id, name, existing_name, label, kind, method,
    similarity, candidate_type: 'entity', status: 'pending', auto_mergeable}``.

    ``id`` is the DUPLICATE (the node a merge would fold away) and ``existing_id``
    the survivor. The pairing is deterministic: within a block the OLDER entity
    survives (``createdAt`` then ``id``), so re-running the scan yields the same
    pair, which is what lets ``approve_merge`` recompute the thing it reviewed.

    Rows whose two kinds are both declared and DIFFER are kept (a wrong kind is
    part of the measured damage) but are never ``auto_mergeable``: mismatched
    kinds are evidence against identity, so they always take a human decision.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    try:
        entities = scan_entities(graph, labels=labels)
        if not entities:
            return []
        blocks: dict[tuple, list[dict]] = {}
        for ent in entities:
            if not ent["folded"]:
                continue
            blocks.setdefault((ent["label"], block_key(ent["folded"])), []).append(ent)

        sem: dict = {}
        if include_semantic and len(entities) <= SEMANTIC_MAX_ENTITIES:
            sem = _semantic_matrix(entities, encode_fn)
        elif include_semantic:
            _logger.warning(
                "entity semantic rung skipped — %d entities exceeds the bounded "
                "scan of %d; the exact and fuzzy rungs still ran",
                len(entities), SEMANTIC_MAX_ENTITIES)
        if sem:
            from .embeddings import DEFAULT_THRESHOLD as _SEM_FLOOR
        else:
            _SEM_FLOOR = 1.0

        pairs: list[dict] = []
        for (_label, _key), members in sorted(blocks.items(), key=lambda kv: kv[0]):
            if len(members) < 2:
                continue
            members.sort(key=lambda e: (str(e["createdAt"] or ""), e["id"]))
            for i, left in enumerate(members):
                for right in members[i + 1:]:
                    fuzzy_sim = (1.0 if left["folded"] == right["folded"]
                                 else similarity(left["folded"], right["folded"]))
                    sem_sim = sem.get((left["id"], right["id"]))
                    if sem_sim is None:
                        sem_sim = sem.get((right["id"], left["id"]))
                    if fuzzy_sim >= 1.0:
                        method, sim = "exact", 1.0
                    elif fuzzy_sim >= review_threshold:
                        method, sim = "fuzzy", fuzzy_sim
                    elif sem_sim is not None and sem_sim >= _SEM_FLOOR:
                        # Only the vector rung sees this pair. It is a PROPOSAL,
                        # never an auto-merge: a raw cosine is not calibrated
                        # against this graph's names (#2730).
                        method, sim = "semantic", sem_sim
                    else:
                        continue
                    # Older survives; the newer is the candidate (deterministic).
                    survivor, candidate = left, right
                    if _pair_reviewed(survivor, candidate):
                        continue
                    kind_mismatch = bool(
                        survivor["kind"] and candidate["kind"]
                        and survivor["kind"] != candidate["kind"])
                    pairs.append({
                        "id": candidate["id"],
                        "candidate_id": candidate["id"],
                        "existing_id": survivor["id"],
                        "target_id": survivor["id"],
                        "name": candidate["name"],
                        "existing_name": survivor["name"],
                        "label": survivor["label"],
                        "kind": candidate["kind"],
                        "existing_kind": survivor["kind"],
                        "kind_mismatch": kind_mismatch,
                        "method": method,
                        "similarity": round(float(sim), 4),
                        "fuzzy_similarity": round(float(fuzzy_sim), 4),
                        "semantic_similarity": (None if sem_sim is None
                                                else round(float(sem_sim), 4)),
                        # Advisory for a write-path caller. A merge still takes a
                        # review verdict, and a mismatched-kind pair never
                        # qualifies (the kinds are evidence AGAINST identity).
                        "auto_mergeable": bool(
                            method in ("exact", "fuzzy")
                            and sim >= AUTO_MERGE_THRESHOLD
                            and not kind_mismatch),
                        "candidate_type": "entity",
                        "status": "pending",
                    })

        # One row per duplicate: a node with several plausible twins is reviewed
        # against its best match only, so a merge cannot silently fold two
        # different entities into one (the transitive-closure failure).
        grouped: dict[str, list[dict]] = {}
        for pair in pairs:
            grouped.setdefault(pair["candidate_id"], []).append(pair)
        best: dict[str, dict] = {}
        for cid, group in grouped.items():
            group.sort(key=lambda p: (-p["similarity"], p["existing_id"]))
            top = dict(group[0])
            tied = [p["existing_id"] for p in group[1:]
                    if abs(p["similarity"] - top["similarity"]) < 1e-9
                    and p["existing_id"] != top["existing_id"]]
            # A candidate with two equally-good survivors is NOT resolved here.
            # The queue shows the tie so a reviewer sees it, and `approve_merge`
            # refuses on it (route-then-refuse, #3590 R8) — the read surface must
            # not pick a survivor on the reviewer's behalf.
            top["ambiguous"] = bool(tied)
            if tied:
                top["tied_with"] = tied
            best[cid] = top
        rows = sorted(best.values(),
                      key=lambda p: (-p["similarity"], p["candidate_id"]))
        return rows[:limit]
    except EntityScanUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — the fail-open this module exists to end
        raise EntityScanUnavailable(
            f"entity scan failed — refusing to report an empty queue "
            f"({type(exc).__name__}: {exc})") from exc


# ── Resolution for a single name ─────────────────────────────────────────


def follow_resolved_from(graph, label: str, node_id: str) -> str:
    """Follow a recorded merge pointer to the entity that SURVIVED.

    A merged-away node keeps its id and its surface form (#2730 §6.7.3 — the
    original is evidence, never deleted or rewritten), so a later write of that
    spelling resolves to the OLD node first. It must not: the merge said these
    are one entity, and a write landing on the retired carrier would grow a third
    node the next time the merge is re-run. ``resolved_from`` is the reversible
    mapping that makes the id hop safe — the same shape record-linkage practice
    calls a redirect from a duplicate to the canonical entity.

    A single hop, cycle-guarded by the ``seen`` set: a chain longer than the graph
    allows is a corrupt record, and following it forever on a WRITE path would
    hang the write. Returns ``node_id`` unchanged when there is no pointer.
    """
    current = str(node_id)
    seen = {current}
    while True:
        try:
            row = graph.query(
                f"MATCH (n:{label} {{id:$id}}) RETURN n.resolved_from",
                params={"id": current},
            ).result_set
        except Exception:  # noqa: BLE001 — a write must not fail on the hop probe
            return current
        target = row[0][0] if row else None
        if not target or str(target) in seen:
            return current
        current = str(target)
        seen.add(current)


def find_live_holder(graph, label: str, folded: str) -> list[str]:
    """Live ``label`` entity ids whose folded name (or recorded alias) == ``folded``."""
    try:
        rows = graph.query(
            f"MATCH (n:{label}) "
            "WHERE n.id IS NOT NULL "
            "AND (n.status IS NULL OR NOT n.status IN ['superseded','deprecated',"
            "     'archived','retracted']) "
            "AND (n.canonical_name = $k OR $k IN coalesce(n.aliases, [])) "
            "RETURN n.id",
            params={"k": folded},
        ).result_set
    except Exception as exc:  # noqa: BLE001
        raise EntityScanUnavailable(
            f"entity resolution could not read :{label} — refusing to guess "
            f"({type(exc).__name__}: {exc})") from exc
    return [str(r[0]) for r in rows if r[0]]


def resolve_entity_name(graph, label: str, name: str, *,
                        include_semantic: bool = False,
                        encode_fn=None) -> dict:
    """Run the ladder for ONE name and say what it found — never silently "new".

    Returns ``{name, folded, resolution, entity_id, similarity, requires_review,
    candidates}`` where ``resolution`` is ``exact`` | ``alias`` | ``fuzzy`` |
    ``semantic`` | ``new``.

    ``exact``/``alias`` are deterministic and safe to write onto
    (``requires_review=False``). ``fuzzy``/``semantic`` are PROPOSALS:
    ``requires_review=True`` always, because similarity alone is not evidence
    enough to merge (#2730 §6.7 gate). ``ambiguous`` (>1 live holder of the folded
    key) is returned as ``resolution='ambiguous'`` with the holder ids, never
    collapsed to one — route-then-refuse (#3590 R8).
    """
    folded = normalize_entity_name(name)
    holders = find_live_holder(graph, label, folded)
    if len(holders) == 1:
        return {"name": name, "folded": folded, "label": label,
                "resolution": "exact", "entity_id": holders[0],
                "similarity": 1.0, "requires_review": False, "candidates": []}
    if len(holders) > 1:
        return {"name": name, "folded": folded, "label": label,
                "resolution": "ambiguous", "entity_id": None,
                "similarity": 1.0, "requires_review": True,
                "candidates": holders}

    entities = scan_entities(graph, labels=(label,))
    scored = []
    for ent in entities:
        if ent["dedup_reviewed"]:
            continue
        sim = (1.0 if ent["folded"] == folded
               else similarity(folded, ent["folded"]))
        scored.append((sim, ent))
    scored.sort(key=lambda s: (-s[0], s[1]["id"]))
    if scored and scored[0][0] >= REVIEW_THRESHOLD:
        sim, ent = scored[0]
        return {"name": name, "folded": folded, "label": label,
                "resolution": "fuzzy", "entity_id": ent["id"],
                "similarity": round(float(sim), 4), "requires_review": True,
                "candidates": [e["id"] for _s, e in scored[:5]]}
    return {"name": name, "folded": folded, "label": label,
            "resolution": "new", "entity_id": None, "similarity": 0.0,
            "requires_review": False, "candidates": []}


# ── Applying a review verdict (non-destructive) ──────────────────────────


def apply_entity_merge(sdk, candidate_id: str, survivor_id: str, *,
                       method: str, similarity: float) -> dict:
    """Fold ``candidate_id`` onto ``survivor_id`` WITHOUT deleting either node.

    The merge is recorded, not performed destructively (#2730 §6.7.3 — "record
    the alias, do not collapse it"; #783's J-2 edge case: never a destructive
    merge):

    * the SURVIVOR gains the candidate's folded name in ``aliases`` (the folded
      spelling ``entity_identity._NAME_PROPERTIES`` already treats as addressable),
      so a later write of the variant resolves instead of minting a twin, and
    * the CANDIDATE is stamped ``resolved_from``/``dedup_reviewed='merge'`` so it
      leaves the queue and keeps its provenance.

    Both nodes and all their edges survive.

    ⛔ **Both writes go through ``update_entity``, not a raw ``SET``.** That is the
    path that journals an ``EntityMutated`` record with the applied map as
    ``state`` (``_journal_entity_mutation``), so ``derived == replay(journal)``
    holds for the merge: a rebuild restores the alias and the resolution pointer
    instead of silently dropping them. A bare property write here would be the
    #7951 class (live-only state a rebuild loses).

    The audit is the PROPERTY form the #783 plan sanctions ("DedupeRecorded
    event OR canonical_id/resolved_from property — E2E-2 asserts at least one")
    rather than a ``DedupeRecorded`` emit: that event's record-projection shape is
    a ``:Point`` snapshot (``record_projection`` ``NodeSnapshot(path="point")``)
    and an entity id has no Point to snapshot, so emitting it here would write a
    keyless record rather than an audit.
    """
    if candidate_id == survivor_id:
        raise ValueError("a merge needs two distinct entities")
    graph = sdk._get_proj().g
    rows = graph.query(
        "MATCH (s) WHERE s.id = $sid RETURN s.name, coalesce(s.aliases, [])",
        params={"sid": survivor_id},
    ).result_set
    if not rows:
        raise ValueError(f"No entity {survivor_id!r}")
    survivor_name, aliases = rows[0]
    rows = graph.query(
        "MATCH (c) WHERE c.id = $cid RETURN c.name, c.canonical_name",
        params={"cid": candidate_id},
    ).result_set
    if not rows:
        raise ValueError(f"No entity {candidate_id!r}")
    candidate_name, candidate_canonical = rows[0]
    folded = normalize_entity_name(candidate_canonical or candidate_name)
    alias_list = sorted({a for a in (aliases or []) if isinstance(a, str)} | {folded})
    sdk.update_entity(
        survivor_id,
        canonical_name=normalize_entity_name(survivor_name),
        aliases=alias_list,
    )
    sdk.update_entity(
        candidate_id,
        resolved_from=survivor_id,
        dedup_reviewed="merge",
        reviewed=True,
        canonical_name=folded,
    )
    return {"survivor_id": survivor_id, "candidate_id": candidate_id,
            "aliases": alias_list, "method": method,
            "similarity": round(float(similarity), 4)}


def entity_pair_for(graph, candidate_id: str, *,
                    review_threshold: float = REVIEW_THRESHOLD) -> dict | None:
    """Recompute the reviewed pair for ``candidate_id`` — or refuse to guess.

    ``list_dedup_candidates`` is a READ surface (``risk="read"``), so it cannot
    persist the pair it showed. ``approve_merge`` therefore re-runs the same
    deterministic scan (the scan is deterministic precisely so this is sound).
    If the graph moved and the candidate now has NO pair, ``None`` says so; if it
    has two equally-good survivors, :class:`ValueError` says so — a merge must
    never pick one for the reviewer (route-then-refuse, #3590 R8).
    """
    entities = scan_entities(graph)
    by_id = {e["id"]: e for e in entities}
    me = by_id.get(candidate_id)
    if me is None:
        return None
    scored = []
    for eid, ent in by_id.items():
        if eid == candidate_id or ent["label"] != me["label"]:
            continue
        if ent["folded"] == me["folded"]:
            sim = 1.0
        else:
            sim = similarity(me["folded"], ent["folded"])
        if sim >= review_threshold:
            scored.append((sim, ent))
    if not scored:
        return None
    scored.sort(key=lambda s: (-s[0], s[1]["id"]))
    top_sim = scored[0][0]
    tied = [e for s, e in scored if abs(s - top_sim) < 1e-9]
    if len(tied) > 1:
        raise ValueError(
            f"entity {candidate_id!r} matches {len(tied)} survivors at "
            f"similarity {top_sim:.4f} ({[e['id'] for e in tied]}) — refusing "
            f"to guess which one to merge into")
    survivor = scored[0][1]
    kind_mismatch = bool(me["kind"] and survivor["kind"]
                         and me["kind"] != survivor["kind"])
    return {
        "candidate_id": candidate_id,
        "survivor_id": survivor["id"],
        "method": "exact" if top_sim >= 1.0 else "fuzzy",
        "similarity": round(float(top_sim), 4),
        "kind_mismatch": kind_mismatch,
    }


# ── holdsRole production (person → role) ─────────────────────────────────


def wire_holds_role(sdk, holder: str, role_id: str) -> dict:
    """Wire ``(holder:Subject)-[:holdsRole]->(role:Subject)`` — the missing producer.

    ``#12047`` measured **zero** ``holdsRole`` edges in both hosted graphs: the
    predicate is declared (ONTOLOGY §3.6, sanctioned and deliberately distinct
    from ``memberOf`` — ``:206``) and read (``sdk._holds_role_available``), but
    nothing PRODUCES it. A role Subject therefore floats free of the person who
    holds it.

    ``holder`` is an id or a name, resolved against LIVE ``:Subject`` nodes in
    that order. Exactly one match is required: an unresolvable or ambiguous
    holder RAISES rather than wiring an edge to a guess (#1370 — *"no subject >
    wrong subject"*). This is the declared channel only; nothing here infers a
    holder from a name pattern.
    """
    from .entity_identity import AmbiguousEntityName  # local: avoids the cycle

    if not holder:
        raise ValueError("holdsRole needs a holder (a Subject id or name)")
    row = sdk._get_proj().g.query(
        "MATCH (s:Subject) WHERE s.id = $h "
        "AND (s.status IS NULL OR NOT s.status IN ['superseded','deprecated',"
        "     'archived','retracted']) "
        "RETURN s.id, s.name",
        params={"h": holder},
    ).result_set
    if not row:
        folded = normalize_entity_name(holder)
        row = sdk._get_proj().g.query(
            "MATCH (s:Subject) WHERE s.canonical_name = $k "
            "AND (s.status IS NULL OR NOT s.status IN ['superseded','deprecated',"
            "     'archived','retracted']) "
            "RETURN s.id, s.name",
            params={"k": folded},
        ).result_set
    ids = [str(r[0]) for r in row if r[0]]
    if not ids:
        raise ValueError(
            f"holdsRole holder {holder!r} matches no live :Subject — refusing to "
            f"wire the role to a guess")
    if len(ids) > 1:
        raise AmbiguousEntityName("Subject", holder, ids)
    return sdk.create_edge("holdsRole", ids[0], role_id)
