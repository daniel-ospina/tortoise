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
degrades to ``[]``. ⚠️ Precisely: that holds for the ENTITY READ. A similarity
rung that cannot run (no numpy, no encoder, a short row count) degrades to the
rungs below it and logs — a missing model must not make the queue refuse, and
the exact rung still measures. A semantic-only pair is therefore invisible on a
degraded box, which is the declared cost of the degrade chain.

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
therefore **blocked** — the standard record-linkage reduction — on the MULTI-KEY
union :func:`tortoise.entity_blocking.block_keys` yields, partitioned per
``(label, key)``: ``E`` the folded name, ``B`` a path basename, ``P`` the
``MIN_BLOCK_CHARS`` prefix, one ``T`` per word-prefix, and 8 ``S`` minhash bands.
Equal folded names always share ``E``, so the exact rung can never be missed by
blocking. ⛔ The bound the prefix ALONE imposed is gone for names whose tokens clear the `T`
key's own floor (#12295): a pair whose folded names share no 3-character prefix is no longer
excluded by that fact — the ``T`` (word order / transposition) and ``S`` (typo and separator
classes) keys reach it. ⚠️ That is a relief, NOT a universal: ``T`` is emitted per token matched
by ``_WORD = re.compile(r"[a-z0-9']+")`` that is also at least ``MIN_BLOCK_CHARS`` (3)
characters long, so (i) a transposition whose tokens are shorter than 3 characters shares no
``T`` key (``'ab cd'`` / ``'cd ab'`` share NOTHING), and (ii) ``T`` is ASCII-gated, so a
non-ASCII transposition is reached only where an ``S`` band happens to collide — measured:
``'привет клиент'`` / ``'клиент привет'`` share ``S:(20,…)``, while the shorter
``'привет мир'`` / ``'мир привет'`` share NOTHING. ``_WORD`` is the reach limit, not
non-ASCII: do NOT read this arm as "non-ASCII transpositions are never compared".
``test_every_key_namespace_in_the_union_is_pinned_by_a_pair_that_REQUIRES_it``
pins each of ``P``/``T``/``S`` with a pair whose own namespace its arm REQUIRES (not
"shares alone" — measured, only the ``P`` witness shares ``P`` by itself; the ``T``
witness shares ``T`` and ``S``, the ``S`` witness all three). ⚠️ And the arms do NOT all
assert the same shape, which is the point: the ``P`` arm asserts set EQUALITY (its
intersection is exactly ``{"P:ab "}``, so it is the exclusive pin), while the ``T`` and
``S`` arms assert MEMBERSHIP of their own namespace in an intersection that also holds
others.
⚠️ The
transposition test (`test_the_scan_finds_a_transposition_the_single_prefix_key_could_not`)
does NOT pin the namespace: its pair shares ``T`` *and* ``S``, so deleting either
generator leaves it green. ``E`` is always present; ``P`` is added only where the fold is at least
``MIN_BLOCK_CHARS`` long, and there it is byte-identical to the pre-#12295 single
key, so the block
set is a strict superset of `main`'s. The scan applies NO size cap
(:data:`tortoise.entity_blocking.MAX_BLOCK_SIZE` bounds one QUERY's candidate list
only, and an oversized block is skipped and REPORTED there).
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

#: Blocking width for the ``P:`` key. ``MIN_BLOCK_CHARS`` folded characters is the
#: shortest PREFIX that keeps pair completeness for the damage classes measured on
#: #12047; since #12295 it is ONE key among the multi-key union
#: (:func:`tortoise.entity_blocking.block_keys`) rather than the whole scan's
#: blocking rule, so the reduction it provides is additive — it is no longer the
#: bound on which pairs are scored. The ~3-orders-of-magnitude figure was measured
#: for the prefix key against all-pairs, not as the scan's own cost.
MIN_BLOCK_CHARS = 3

#: The block-size at which the SCAN reports its largest block. Not a cap: the
#: scan compares every block (a cap would drop pairs a smaller graph showed), and
#: an emerging giant block is the over-merge signal the report exists for.
LARGE_BLOCK_WARN = 200

#: The two labels an entity identity lives on (``entity_identity._IDENTITY_LABELS``).
ENTITY_LABELS = ("Object", "Subject")

#: label → the kind property ONTOLOGY §4.1/§4.2 declares for it.
_KIND_PROP = {"Object": "objectKind", "Subject": "subjectKind"}

#: Above this many scanned entities the semantic rung is skipped (logged), because
#: encoding every name on a read path is unbounded work. The fuzzy + exact rungs
#: still run; the skip is named rather than silent.
SEMANTIC_MAX_ENTITIES = 2000

_NON_ALNUM = re.compile(r"[^\w]+", re.UNICODE)

#: A leading article at a WORD boundary (space or the underscore that stands for
#: internal whitespace) — never a bare prefix, so `a-b`/`the-owner` keep their
#: first token (#12047 round-4 finding 5).
_LEADING_ARTICLE = re.compile(r"^(?:the|an|a)(?=[\s_])", re.IGNORECASE)


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
    # `.strip()` first: the article anchor is `^`, so a padded name ("  The Owner")
    # would otherwise keep its article and fold differently from the same name
    # written unpadded.
    folded = unicodedata.normalize("NFKC", raw).casefold().strip()
    # ⛔ THE ARTICLE IS A WORD, NOT A PREFIX (#12047 round-4 finding 5). The fold
    # used to split on every punctuation run and then drop a leading
    # ``the``/``an``/``a`` TOKEN — so ``normalize("a-b")`` was ``"b"``, a
    # different key from ``_legacy_fold("a-b") == "ab"``. The create path unions
    # both arms, so a write of "a-b" resolved onto an unrelated `"AB"` node: a
    # merge the queue can never show, because the two spellings do not compare
    # equal under EITHER fold. Stripping is therefore anchored on a real SPACE (or
    # the ``_`` written for internal whitespace): "the owner" → "owner",
    # "a-b" → "a b".
    while True:
        stripped = _LEADING_ARTICLE.sub("", folded, count=1)
        if stripped == folded:
            break
        folded = stripped
    tokens = [t for t in _NON_ALNUM.split(folded.replace("_", " ")) if t]
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
    # A merge records that this id and its survivor are ONE entity
    # (`resolved_from`), so the retired carrier must not be elected as a
    # SURVIVOR or re-offered as a duplicate — otherwise a re-run writes the
    # alias onto a node the system itself says is no longer the entity, and the
    # real survivor never learns the variant (review F3).
    "AND n.resolved_from IS NULL "
    "AND (n.status IS NULL OR NOT n.status IN ['superseded','deprecated',"
    "     'archived','retracted']) "
    "RETURN n.id, n.name, n.canonical_name, n.{kind}, n.createdAt, "
    "       coalesce(n.dedup_reviewed, ''), n.dedup_target_id, "
    "       n.dedup_rejected_targets"
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
        # ⛔ The verdict is SET-VALUED (#12047 review F1): `dedup_rejected_targets`
        # is the authoritative list, because one scalar slot could not hold two
        # rejections and overwriting it un-suppressed the pair the reviewer had
        # already answered. The scalar `dedup_target_id` stays as the legacy /
        # single-verdict fallback for rows written before the list existed.
        targets = candidate.get("dedup_rejected_targets")
        wanted = str(survivor.get("id"))
        if targets:
            return any(str(t) == wanted for t in targets if t is not None)
        target = candidate.get("dedup_target_id")
        # A rejection that recorded NO target was decided against an ambiguous
        # set (`approve_merge` cannot name one survivor), so it suppresses the
        # candidate outright — re-offering it against a different survivor would
        # re-ask a question the reviewer already answered.
        return target is None or str(target) == wanted
    return False


def _read_entities(graph, label: str) -> list[dict]:
    """Read every live ``label`` entity the scan can compare. Raises on failure."""
    try:
        rows = graph.query(
            _ENTITY_READ.format(label=label, kind=_KIND_PROP[label])
        ).result_set
    except Exception as exc:
        raise EntityScanUnavailable(
            f"entity scan could not read :{label} — refusing to report an "
            f"empty queue ({type(exc).__name__}: {exc})") from exc
    out = []
    for row in rows:
        (node_id, name, canonical, kind, created_at, reviewed, reviewed_target,
         rejected_targets) = row
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
            "dedup_rejected_targets": rejected_targets,
        })
    return out


def scan_entities(graph, *, labels=ENTITY_LABELS) -> list[dict]:
    """Every live entity the resolver can compare, across ``labels``."""
    if not labels:
        raise ValueError(
            "entity scan needs at least one label — an empty label set would "
            "report an empty queue that never read the graph (#12047)")
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
    deterministic + fuzzy rungs could not see. ⚠️ It is scoped by the SAME block
    SET as every other rung — since #12295 that is the multi-key union, not one
    prefix — so a pair that shares NO block with this mention is not proposed.
    (It is NOT scoped by a 3-character prefix: a semantically-close pair whose
    folded names share no prefix is compared whenever any key of the union — a
    token, a shingle band — puts them in one block, and the union is a superset of
    the old prefix key, so the semantic pool is WIDER than it was.) That is
    deliberate: cross-block all-pairs is the pair explosion the ladder forbids,
    and a bounded rung that says so is better than an unbounded one. (The semantic
    test therefore places its pair inside one block.) ⛔ It is NOT scoped by any other rung's arithmetic:
    the fuzzy rung's length prune is a LEXICAL bound, so `entity_candidates` only
    applies it to a pair the vectors cannot answer for (#12047 round-3 P2) — a
    lexical omission must never cap the vector pool's recall. The threshold it
    compares against
    is ``embeddings.DEFAULT_THRESHOLD`` — reused, never re-invented (#2730 §5: a
    second similarity constant was REFUSED).

    Returns ``{(id_a, id_b): cosine}`` for every pair ``i < j``; empty on any
    degrade (numpy/model/math failure) — and says so in the log, because a
    silently skipped rung reads as "nothing found".
    """
    if len(entities) < 2:
        return {}
    try:
        import numpy as np
    except Exception as exc:
        _logger.warning("entity semantic rung skipped — numpy unavailable: %s", exc)
        return {}
    ids = [e["id"] for e in entities]
    texts = [e["folded"] for e in entities]
    if encode_fn is None:
        from .embeddings import _encode as encode_fn  # local: heavy import
    try:
        vectors, _degraded = encode_fn(texts)
    except Exception as exc:
        _logger.warning("entity semantic rung skipped — encoder failed: %s", exc)
        return {}
    if vectors is None or len(vectors) != len(texts):
        _logger.warning(
            "entity semantic rung skipped — encoder returned %s rows for %s names",
            None if vectors is None else len(vectors), len(texts))
        return {}
    try:
        mat = np.asarray(vectors, dtype=float)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        unit = mat / norms
        sims = unit @ unit.T
    except Exception as exc:
        # ⛔ The MATH is a degrade path too, not just the encoder call. A
        # length-matching but non-2-D encoder result (e.g. `[1.0, 1.0]`) passed
        # the row check above and then raised `numpy.AxisError` from
        # `np.linalg.norm` OUTSIDE this guard — and because `AxisError` subclasses
        # `ValueError`, `entity_candidates`' caller-error clause re-raised it
        # verbatim, so the whole queue refused with a bare numpy error where this
        # docstring promised a logged degrade.
        _logger.warning(
            "entity semantic rung skipped — similarity math failed %s: %s",
            type(exc).__name__, exc)
        return {}
    return {(ids[i], ids[j]): float(sims[i][j])
            for i in range(len(ids)) for j in range(i + 1, len(ids))}


def entity_candidates(graph, *, limit: int | None = 50,
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

    ``limit=None`` means NO truncation. The review surface passes a limit (it is a
    paged read); :func:`entity_pair_for` passes ``None`` because it must be able
    to find the row the surface displayed for ANY limit the caller used.
    """
    if limit is not None and limit < 1:
        raise ValueError(f"limit must be >= 1, got {limit}")
    try:
        entities = scan_entities(graph, labels=labels)
        if not entities:
            return []
        # ⛔ #12295 — MULTI-KEY BLOCKING. This used to group on ONE key,
        # ``block_key(folded)`` = the first ``MIN_BLOCK_CHARS`` folded
        # characters, so ``the project alpha`` and ``alpha project`` — a
        # transposition, and one of the classes `#11901` measured at a 0.0 %
        # attach rate — landed in different blocks and the pair was never
        # compared, at ANY similarity. The block set is now the union of the
        # folded key, the folded prefix (KEPT: it is the key main blocked on, so
        # keeping it preserves main's pairing set), per-token prefixes and LSH
        # shingle bands (``entity_blocking``, Zep's shape,
        # ``dedup_helpers.py``). Candidate generation only: every rung below
        # still decides, and nothing here can auto-merge on a similarity.
        # ⛔ NO SIZE CAP on this path. A cap would drop pairs main compared, which
        # is a behaviour change disguised as a bound; the block-size distribution
        # is reported instead, because an emerging giant block is the over-merge
        # signal a cap would merely hide.
        from .entity_blocking import BlockIndex

        index = BlockIndex.from_rows(entities)
        blocks = index.blocks()
        _stats = index.stats()
        if _stats["max_block_size"] >= LARGE_BLOCK_WARN:
            _logger.warning(
                "entity blocking: largest block holds %d of %d %s entities "
                "(%d blocks) — an emerging giant block is the over-merge signal "
                "(#12295); the scan compared it in full rather than dropping it",
                _stats["max_block_size"], _stats["members"],
                "/".join(sorted({e["label"] for e in entities})),
                _stats["blocks"])
        by_id = {str(ent["id"]): ent for ent in entities}
        #: Pairs already compared. A union of keys puts one PAIR in several blocks
        #: (up to ~12: ``E``, ``B``, ``P``, one ``T`` per token, 8 ``S`` bands), and
        #: a pair's ROW is identical in every one of them — `fuzzy_sim`, `sem_sim`,
        #: `kind_mismatch` and the survivor/candidate assignment are pure functions
        #: of the pair. So the row is unchanged; the SET of rows is not quite, and
        #: the difference is deliberate: ``settled`` is per block, so a pair emitted
        #: in one block no longer re-populates a later block's ``settled``, and a
        #: candidate that is an exact twin of two carriers can surface as
        #: ``ambiguous``/``tied_with`` where the old order hid it. That is
        #: fail-CLOSED — ``approve_merge`` refuses an ``ambiguous`` row rather than
        #: merging it. ⛔ The one arm that is NOT purely additive is the per-candidate
        #: reduction below (``best``: ONE row per candidate, sorted by
        #: ``-similarity``): when the extra blocks hand a candidate a
        #: HIGHER-scoring survivor, that better pair displaces the one ``main``
        #: offered instead of being emitted beside it, and the read surface
        #: re-derives its own best from the rows it is given. So do not read this
        #: key as a strict superset of ``main``'s PAIRS — it is a superset of the
        #: blocks (nothing is blocked less), and its reduction shows a strictly
        #: better match where it differs.
        #: ⛔ AND ONE ARM CAN MAKE A ROW UNMERGEABLE, not merely undecided. The
        #: ``settled`` skip below is PER BLOCK, so a pair one block suppressed is
        #: re-compared in a later block that the union adds. With three or more
        #: spellings of ONE entity (``plan_doc`` / ``plan-doc`` / ``the plan doc``,
        #: all folding to ``plan doc``) the newest candidate can gain a second 1.0
        #: row against a SIBLING DUPLICATE, become ``ambiguous``/``tied_with``, and
        #: ``entity_pair_for`` then raises on it — so ``approve_merge`` can never
        #: merge it, where ``main`` merged it cleanly, and the queue reports
        #: "matches 2 survivors" when both survivors are the same entity. The
        #: mechanism is determined by the code above (``settled`` is per block, and
        #: the ``best`` reduction keys on the candidate), NOT by a rate quoted here:
        #: a count that no committed artifact or test holds cannot be re-run, and
        #: this one is the sole evidence for a decision to leave the scan alone.
        #: It bites on populations that carry several spellings of ONE entity.
        #: That is deliberate and it is the SAFE direction (#2730 §6.7: a false
        #: merge is invisible, a false split is cheap), but it is a real loss of
        #: mergeability on duplicate-heavy data, so it is stated rather than
        #: implied by the word "fail-CLOSED".
        #: ⛔ The dedupe is checked at the TOP of the pair loop, before the vector
        #: lookup, the length prune and ``similarity``: a dedupe placed after them
        #: skips only the append, and the repeated comparison it was added to avoid
        #: still happens (a pair in 6 blocks measured 6 ``similarity`` calls, and a
        #: never-emitted transposition 3 — #12295 round-2 B4).
        emitted: set[tuple[str, str]] = set()

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
        for member_ids in blocks:
            members = [by_id[i] for i in member_ids if i in by_id]
            if len(members) < 2:
                continue
            members.sort(key=lambda e: (str(e["createdAt"] or ""), e["id"]))
            settled: set[str] = set()
            for i, left in enumerate(members):
                for right in members[i + 1:]:
                    if right["id"] in settled:
                        continue
                    # Already COMPARED from another block this pair shares — the
                    # verdict, not just the row, is a pure function of the pair,
                    # so it is recorded here rather than at the emit path. (At the
                    # emit path a pair that is NOT emitted — a transposition whose
                    # similarity stays under the review floor — was recorded
                    # nowhere and re-compared once per shared block: measured 3
                    # `similarity` calls for one pair, #12295 round-2 B4.)
                    _pair_key = (left["id"], right["id"])
                    if _pair_key in emitted:
                        continue
                    emitted.add(_pair_key)
                    # ⛔ TWO rungs, TWO candidate pools (#12047 round-3 P2). The
                    # length bound below is a LEXICAL one: difflib's ratio is
                    # <= 2*min/(sum), so a pair that cannot reach the review
                    # floor needs no comparison. Applying it BEFORE the vector
                    # lookup let one rung's arithmetic prune another rung's pool —
                    # 'abc corp' vs 'abc corporation international holdings' is
                    # scored by the vectors and was dropped unlooked-at, an
                    # undeclared bound and exactly the under-report this module
                    # exists to remove. So the vector is read FIRST (a dict hit),
                    # and the prune applies only when the semantic rung cannot
                    # answer for this pair — `_SEM_FLOOR` is 1.0 when `sem` is
                    # empty, so an off/absent rung prunes exactly as before.
                    _la, _lb = len(left["folded"]), len(right["folded"])
                    sem_sim = sem.get((left["id"], right["id"]))
                    if sem_sim is None:
                        sem_sim = sem.get((right["id"], left["id"]))
                    _sem_can_answer = sem_sim is not None and sem_sim >= _SEM_FLOOR
                    if (not _sem_can_answer and _la and _lb
                            and 2 * min(_la, _lb) < review_threshold * (_la + _lb)):
                        continue
                    fuzzy_sim = (1.0 if left["folded"] == right["folded"]
                                 else similarity(left["folded"], right["folded"]))
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
                    if fuzzy_sim >= 1.0:
                        # Only an EMITTED 1.0 row settles its candidate. Doing
                        # this before the reviewed-pair filter (as this did) hid
                        # two things: after a REJECTED exact pair the candidate
                        # matched no other survivor for the rest of the scan (a
                        # genuine duplicate silently vanished from the queue,
                        # contradicting `_pair_reviewed`'s "only the reviewed
                        # combination is suppressed"), and a candidate that is an
                        # exact twin of TWO survivors lost the tie, so the queue
                        # elected the older one instead of showing `ambiguous`
                        # and `entity_pair_for` would merge a pair the reviewer
                        # was never told was a tie.
                        settled.add(right["id"])
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
        return rows if limit is None else rows[:limit]
    except (EntityScanUnavailable, ValueError):
        # A caller error (an empty label set) is not a scan failure and must not
        # be relabelled as one — nor swallowed into an empty queue.
        raise
    except Exception as exc:
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
    if label not in ENTITY_LABELS:
        raise ValueError(
            f"entity resolution covers {ENTITY_LABELS}, got {label!r}")
    while True:
        try:
            row = graph.query(
                f"MATCH (n:{label} {{id:$id}}) RETURN n.resolved_from",
                params={"id": current},
            ).result_set
        except Exception as exc:
            # ⛔ NOT swallowed (review): returning the PRE-hop id on a failed read
            # is the one outcome this hop exists to prevent — a write resolved
            # onto a carrier the merge already retired — and it looked like a
            # successful resolve. Sibling reads here (`_read_entities`,
            # `find_live_holder`) raise for the same reason: the module's contract
            # is "cannot answer" ≠ "no answer".
            raise EntityScanUnavailable(
                f"could not follow resolved_from for {node_id!r} — refusing to "
                f"resolve onto a possibly-retired carrier "
                f"({type(exc).__name__}: {exc})") from exc
        target = row[0][0] if row else None
        if not target or str(target) in seen:
            return current
        current = str(target)
        seen.add(current)


def find_live_holder(graph, label: str, folded: str) -> list[tuple[str, str]]:
    """Live ``label`` entities matching ``folded`` — as ``(id, rung)`` pairs.

    ``rung`` is ``"exact"`` when the folded key is the entity's own
    ``canonical_name`` and ``"alias"`` when it was recorded by an approved merge
    (``aliases[]``). Both resolve deterministically; the caller reports which
    rung fired.

    ⛔ A merged-away carrier is followed to its survivor **before**
    de-duplication (review F8). The candidate keeps its own ``canonical_name``
    (its surface form is evidence), so a merged variant matches BOTH the
    survivor's alias and the retired carrier's key — reporting that as
    ``ambiguous`` would make "these are one entity" turn the read surface into
    "there are two", inviting a review of a settled merge.
    """
    if label not in ENTITY_LABELS:
        # The label reaches a Cypher label position, so it is validated here
        # exactly as `scan_entities` validates it: `entity_identity`'s
        # `_require_identity_label` is the repo's allowlist guard for this, and
        # the projection states the rule (a label is never interpolated from an
        # unvalidated source). `resolve_entity_name` is a public entry point that
        # passes its label straight here.
        raise ValueError(
            f"entity resolution covers {ENTITY_LABELS}, got {label!r}")
    try:
        rows = graph.query(
            f"MATCH (n:{label}) "
            "WHERE n.id IS NOT NULL "
            "AND (n.status IS NULL OR NOT n.status IN ['superseded','deprecated',"
            "     'archived','retracted']) "
            "AND (n.canonical_name = $k OR "
            "     (typeof(n.aliases) = 'List' AND $k IN n.aliases)) "
            "RETURN n.id, (n.canonical_name = $k)",
            params={"k": folded},
        ).result_set
    except Exception as exc:
        raise EntityScanUnavailable(
            f"entity resolution could not read :{label} — refusing to guess "
            f"({type(exc).__name__}: {exc})") from exc
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for node_id, is_canonical in rows:
        if not node_id:
            continue
        resolved = follow_resolved_from(graph, label, str(node_id))
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append((resolved, "exact" if is_canonical else "alias"))
    return out


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
    enough to merge (#2730 §6.7 gate). ``ambiguous`` — more than one live entity
    whose name FOLDS to this key, whether found by its stored key or by the fold
    — is returned with every holder id and ``entity_id=None``, never collapsed to
    one: route-then-refuse (#3590 R8).
    """
    folded = normalize_entity_name(name)
    holders = find_live_holder(graph, label, folded)
    if len(holders) == 1:
        entity_id, rung = holders[0]
        return {"name": name, "folded": folded, "label": label,
                "resolution": rung, "entity_id": entity_id,
                "similarity": 1.0, "requires_review": False, "candidates": []}
    if len(holders) > 1:
        return {"name": name, "folded": folded, "label": label,
                "resolution": "ambiguous", "entity_id": None,
                "similarity": 1.0, "requires_review": True,
                "candidates": [h[0] for h in holders]}

    entities = scan_entities(graph, labels=(label,))
    # ⛔ DETERMINISTIC EQUALITY COMES FIRST, and it is a GROUP, not a winner.
    # `find_live_holder` above answers from the STORED keys (`canonical_name`,
    # `aliases`). An entity written before #12047 carries neither — the exact
    # shape of the damage #12047 measured on the hosted graphs — so the folded
    # rung below would have called a fold-EQUAL match "fuzzy, requires_review"
    # (a deterministic identity mis-reported as a proposal, while the queue
    # reports the same pair as `exact`). Worse, when TWO such legacy rows fold
    # to one key, picking the best-scored one returned a SINGLE id for a pair of
    # live twins: a resolver answering "this is the entity" where the truth is
    # "ambiguous", which is the fail-open shape route-then-refuse (#3590 R8)
    # exists to prevent. Group first, then decide.
    ties = [ent for ent in entities if ent["folded"] == folded]
    if len(ties) == 1:
        return {"name": name, "folded": folded, "label": label,
                "resolution": "exact", "entity_id": ties[0]["id"],
                "similarity": 1.0, "requires_review": False, "candidates": []}
    if len(ties) > 1:
        return {"name": name, "folded": folded, "label": label,
                "resolution": "ambiguous", "entity_id": None,
                "similarity": 1.0, "requires_review": True,
                "candidates": [ent["id"] for ent in ties]}
    scored = []
    for ent in entities:
        # ⛔ Only a MERGE verdict removes an entity from the ladder. It was a
        # blanket `if ent["dedup_reviewed"]: continue`, which also skipped a
        # REJECT — and `_pair_reviewed` is explicit that a rejection suppresses
        # only the reviewed COMBINATION, so a candidate rejected against one
        # survivor must still be visible against the next name queried. Skipping
        # it reported `resolution="new"`, `similarity=0.0` beside a live
        # 0.8-similar node: the caller was told "nothing similar exists" about an
        # entity the scan had just read, and with `requires_review=False`.
        if (ent.get("dedup_reviewed") or "") == "merge":
            continue
        sim = similarity(folded, ent["folded"])
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


def _identity_row(graph, node_id: str, var: str, projection: str):
    """``projection`` over the ONE identity label that carries ``node_id``.

    Both identity labels are tried in a fixed order — never an unlabelled
    `MATCH (n) WHERE n.id = $id`, which full-scans every label in the graph.
    """
    for label in ("Object", "Subject"):
        rows = graph.query(
            f"MATCH ({var}:{label} {{id:$id}}) " + projection,
            params={"id": node_id}).result_set
        if rows:
            return rows[0]
    return None


def _entity_label(graph, node_id: str) -> str | None:
    """Which identity label carries ``node_id`` — the fixed two-label probe.

    Needed because ``follow_resolved_from`` is LABEL-scoped and a merge caller
    supplies only an id. The order is ``("Object", "Subject")``, the same one
    ``_identity_row`` uses, so the two cannot disagree about which node a
    projection read.
    """
    for label in ("Object", "Subject"):
        rows = graph.query(
            f"MATCH (n:{label} {{id:$id}}) RETURN n.id",
            params={"id": node_id}).result_set
        if rows:
            return label
    return None


def _survivor_fixpoint(graph, survivor_id: str) -> tuple[str, bool]:
    """``(id, hopped)`` — the entity ``survivor_id`` finally names (#12296, M-4).

    ``apply_entity_merge`` must write the redirect onto an entity that is not
    itself retired. Writing ``candidate -> survivor`` where the survivor already
    carries ``resolved_from`` creates a CHAIN — ``candidate -> survivor ->
    fixpoint`` — which ``follow_resolved_from`` then collapses silently
    (``entity_resolver.py:556-577``), so a second hop the reviewer never saw is
    applied by every identity read from then on. `#12186` round 2 measured the
    consequence: nothing in this tree bounds or reports chain length.

    Hopping the survivor first makes the invariant hold BY CONSTRUCTION — after
    this, no merge can create a chain of length >= 2 — which is also what makes
    ``entity_verdict.group_join``'s ``dependents >= 1`` a reliable reading of
    "this target is a group".

    ⛔ **Hopping the SURVIVOR is only one of the two legs — and the other was
    missing (measured: `N0 -> N1`, then `N1 -> N2` left `N0 -> N1 -> N2`, length
    3 after one more).** A group survivor is a perfectly eligible CANDIDATE
    (`scan_entities` keeps live nodes; its only filter is `resolved_from IS
    NULL`), so the chain is created by retiring the *candidate*:

    * a candidate that is ALREADY retired is refused — its entity is the fixpoint,
      and merging it would write a second hop the reviewer never saw;
    * the candidate's DEPENDENTS are re-pointed at the final survivor in the same
      merge (path-shortening), so every edge stays one hop deep;
    * the candidate's ``aliases[]`` are unioned into the survivor's, so a spelling
      the group folded earlier is not stranded on a retired node — which is what
      keeps ``entity_verdict``'s alias escape working after a group merge.

    ``hopped`` is returned rather than only applied: it is the evidence that a
    chain existed (`#12186` M-10's *reporting* half), and a caller that expected a
    live survivor learns it was redirected rather than discovering it from a
    later read. It is a BOOLEAN, not a hop count, on purpose:
    ``follow_resolved_from`` already walks to a fixpoint, so counting here would
    report the length of a chain this function deliberately does not walk. A
    lookup that cannot resolve the label returns ``(survivor_id, False)`` — the
    caller's own ``_identity_row`` read then raises ``No entity``, which is the
    honest answer for an id that is not an entity.
    """
    label = _entity_label(graph, survivor_id)
    if label is None:
        return str(survivor_id), False
    fixed = follow_resolved_from(graph, label, survivor_id)
    # ⛔ `follow_resolved_from` STOPS on a cycle and returns `current` (its own
    # cycle guard) — so on a cyclic `resolved_from` record the "fixpoint" it hands
    # back is still retired, and writing `candidate -> fixed` would create the
    # 2-hop chain this function exists to make impossible. Cannot-answer is the
    # honest response, and `EntityScanUnavailable` is this module's contract for
    # it. Reachable only from an out-of-band/legacy cycle: this verb cannot create
    # one.
    row = graph.query(
        f"MATCH (n:{label} {{id:$id}}) RETURN n.resolved_from",
        params={"id": str(fixed)}).result_set
    if row and row[0] and row[0][0]:
        raise EntityScanUnavailable(
            f"the fixpoint for {survivor_id!r} is itself retired ({fixed!r}) — a "
            "cyclic resolved_from record; refusing to write a 2-hop chain")
    return str(fixed), str(fixed) != str(survivor_id)


def _dependents_of(graph, node_id: str) -> list[str]:
    """Ids whose ``resolved_from`` names ``node_id`` — the group it stands for.

    Label-scoped on the two identity labels (this module's own rule: an unlabelled
    ``MATCH (n) WHERE n.resolved_from = $id`` full-scans every label in the graph).
    """
    out: list[str] = []
    for label in ("Object", "Subject"):
        rows = graph.query(
            f"MATCH (d:{label}) WHERE d.resolved_from = $id RETURN d.id",
            params={"id": node_id}).result_set
        out.extend(str(r[0]) for r in rows if r and r[0] is not None)
    return out


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
    # #12296 (M-4's positive half): a merge must not CREATE a chain. Detected by
    # hopping the survivor to its fixpoint BEFORE any write, so the redirect this
    # function records always points at an entity that is not itself retired.
    survivor_id, survivor_hops = _survivor_fixpoint(graph, survivor_id)
    if str(candidate_id) == str(survivor_id):
        raise ValueError(
            "a merge needs two distinct entities — the requested survivor was "
            f"itself retired and resolves onto the candidate {survivor_id!r}")
    # The CANDIDATE side of the same invariant (see the docstring): merging a node
    # that is already retired would write `fixpoint -> survivor` on top of the
    # existing hop. The fixpoint is the entity; the caller's id is stale.
    candidate_fixpoint, candidate_hops = _survivor_fixpoint(graph, candidate_id)
    if candidate_hops or str(candidate_fixpoint) != str(candidate_id):
        raise ValueError(
            f"a merge needs a LIVE candidate — {candidate_id!r} was already "
            f"retired and resolves onto {candidate_fixpoint!r}")
    # LABEL-SCOPED probes, never `MATCH (n) WHERE n.id = $id` (this module's own
    # rule — an unlabelled id probe full-scans every label in the graph, and
    # `id` is a natural key on exactly two of them).
    survivor = _identity_row(
        graph, survivor_id, "s",
        "RETURN s.name, coalesce(s.aliases, []), s.canonical_name")
    if survivor is None:
        raise ValueError(f"No entity {survivor_id!r}")
    survivor_name, aliases, survivor_canonical = survivor
    # `aliases` is an undeclared-but-writable property, so a caller may have
    # stored a bare string (`update_entity(id, aliases="foo")` is reachable).
    # Iterating it would then manufacture one "alias" PER CHARACTER — silently
    # dropping the real one and writing junk keys onto the survivor.
    if isinstance(aliases, str):
        aliases = [aliases]
    elif not isinstance(aliases, (list, tuple)):
        aliases = []
    candidate = _identity_row(
        graph, candidate_id, "c", "RETURN c.name, c.canonical_name, c.aliases")
    if candidate is None:
        raise ValueError(f"No entity {candidate_id!r}")
    candidate_name, candidate_canonical, candidate_aliases = candidate
    folded = normalize_entity_name(candidate_canonical or candidate_name)
    # The candidate's own aliases travel with it: a group that already folded
    # `the team` into THIS node keeps that spelling after the node itself is
    # folded, so `entity_verdict`'s alias escape (and the by-name resolver's
    # `$k IN n.aliases`) still answers for it.
    if isinstance(candidate_aliases, str):
        candidate_aliases = [candidate_aliases]
    elif not isinstance(candidate_aliases, (list, tuple, set)):
        candidate_aliases = []
    alias_list = sorted(
        {a for a in (aliases or []) if isinstance(a, str)}
        | {a for a in candidate_aliases if isinstance(a, str)}
        | {folded})
    # The survivor's folded key is only FILLED, never rewritten: a caller may
    # have supplied its own `canonical_name` (`_with_folded_entity_name`
    # setdefaults), and a merge has no business overwriting what the caller
    # wrote. Its absence is the only case this write repairs.
    if not survivor_canonical:
        sdk.update_entity(survivor_id,
                          canonical_name=normalize_entity_name(survivor_name))
    sdk.update_entity(survivor_id, aliases=alias_list)
    # ⛔ The candidate's `canonical_name` is only FILLED, never rewritten — the
    # same rule as the survivor arm above. A caller-supplied value is a real
    # addressable spelling (`_with_folded_entity_name` setdefaults, and
    # `find_live_holder` states that "the candidate keeps its own
    # canonical_name (its surface form is evidence)"); overwriting it with the
    # fold deletes a key `resolve_entity_id(name_props=...)` answered for, which
    # is exactly what a NON-destructive merge must not do.
    _candidate_props = {
        "resolved_from": survivor_id,
        "dedup_reviewed": "merge",
        "reviewed": True,
    }
    if not candidate_canonical:
        _candidate_props["canonical_name"] = folded
    # ⛔ Path-shortening runs BEFORE the candidate is retired, and the order is
    # load-bearing: `update_entity` is a heavy call (per-node sanitize + money/
    # value probes + its own reads), so a transient failure mid-loop is realistic.
    # Re-pointing FIRST leaves an intermediate that is harmless and idempotent on
    # a retry (dependents pointing at a live survivor); the reverse order leaves
    # the candidate already retired while its dependents still point at it — the
    # 2-hop chain this function exists to make impossible — and reports a landed
    # merge as failed. The wording is exact about what is guaranteed: no chain
    # THIS verb creates exceeds one hop (a pre-existing legacy chain is left to
    # the fold's bounded walk, which grades cannot-answer).
    dependents = _dependents_of(graph, candidate_id)
    for dependent_id in dependents:
        sdk.update_entity(dependent_id, resolved_from=survivor_id)
    sdk.update_entity(candidate_id, **_candidate_props)
    out = {"survivor_id": survivor_id, "candidate_id": candidate_id,
           "aliases": alias_list, "method": method,
           "similarity": round(float(similarity), 4)}
    if survivor_hops:
        # Evidence, not a warning: the merge LANDED, on the entity the graph says
        # survives. A caller comparing this id with the one it passed can see the
        # redirect; nothing here is silent.
        out["survivor_hopped"] = True
    if dependents:
        # The group the candidate stood for moved with it. Reported for the same
        # reason: a reviewer who merged `N1` into `N2` should be able to see that
        # its earlier members now point at `N2`.
        out["dependents_repointed"] = len(dependents)
    return out


def entity_pair_for(graph, candidate_id: str, *,
                    review_threshold: float = REVIEW_THRESHOLD,
                    encode_fn=None) -> dict | None:
    """The pair ``approve_merge`` applies — **the same row the queue showed**.

    ``list_dedup_candidates`` is a READ surface (``risk="read"``), so it cannot
    persist the pair it displayed; the verdict therefore re-derives it. That is
    only sound if re-derivation is the SAME selection — so this function is a
    consumer of :func:`entity_candidates`, never a second scanner (review F2).

    A second implementation was measurably wrong: it had no blocking, no
    ``createdAt`` ordering and no reviewed-pair filter, so it elected a different
    survivor than the queue had shown — a verdict on (C into A) could fold C into
    a newer node the reviewer never saw.

    ``None`` when the candidate has no pair on the current graph. An AMBIGUOUS
    pair (two equally-good survivors) RAISES: a merge must never pick one for
    the reviewer (route-then-refuse, #3590 R8).

    ⛔ **The rung set is the QUEUE's, not a cheaper one** (review P0). This used
    to pass ``include_semantic=False`` while ``list_dedup_candidates`` used the
    default ``True``, so on a graph where the vectors rank a different survivor
    than the string rungs, the reviewer approved (C into B) and the verdict folded
    C into A — the exact "a verdict applies to a pair the reviewer never saw"
    failure this function's parity requirement exists to prevent. The queue is a
    read surface and degrades WITH the encoder (no model ⇒ no semantic row
    shown), so recomputing with the same ladder keeps the two answers equal on
    the same box, which is the property that matters.

    ``limit=None`` — no truncation. The queue truncates ITS page; a verdict must
    not fail because the row sat past a bound this call chose.
    """
    # SAME knobs as the queue (review N2): the queue defaults to
    # `include_semantic=True`, so a merge that excluded it could not re-derive a
    # `semantic` row it had just shown — the review surface would offer a
    # decision and then refuse it with "no pair exists".
    #
    # And NO window (review N4/P2): a truncated recompute can report "no pair"
    # for a candidate that lies outside it — a report false for the graph it
    # describes. `limit=None` removes the truncation instead of failing closed on
    # it, so the recompute is the queue's FULL selection whatever page the
    # reviewer was shown.
    rows = entity_candidates(graph, limit=None, include_semantic=True,
                             encode_fn=encode_fn,
                             review_threshold=review_threshold)
    mine = [r for r in rows if r["candidate_id"] == candidate_id]
    if not mine:
        return None
    top = mine[0]
    if top.get("ambiguous"):
        tied = [top["existing_id"], *(top.get("tied_with") or [])]
        raise ValueError(
            f"entity {candidate_id!r} matches {len(tied)} survivors at "
            f"similarity {top['similarity']:.4f} ({tied}) — refusing to guess "
            f"which one to merge into")
    return {"candidate_id": candidate_id,
            "survivor_id": top["existing_id"],
            "method": top["method"],
            "similarity": top["similarity"],
            "kind_mismatch": bool(top.get("kind_mismatch"))}


# ── holdsRole production (person → role) ─────────────────────────────────


def resolve_role_holder(sdk, holder: str, *, required: bool = True) -> str | None:
    """Resolve a DECLARED role holder to exactly one live ``:Subject`` id.

    ``holder`` is an id or a name, tried in that order. Exactly one match is
    required: an ABSENT holder resolves to ``None`` when ``required`` is False and
    RAISES when it is True; an AMBIGUOUS holder ALWAYS raises, whatever
    ``required`` says, because wiring the edge to a guess is the failure #1370
    (*"no subject > wrong subject"*) refuses — ``resolve_entity_id`` performs that
    refusal itself, so it is not this function's to downgrade.

    ⛔ This is a CONSUMER of :func:`entity_identity.resolve_entity_id_same_entity`
    — :func:`entity_identity.resolve_entity_id`'s route-then-refuse authority
    (#3633) taken over the WRITER's folded union, which is what the source-level
    sweep in ``tests/test_entity_identity.py`` enforces: a hand-written
    name-keyed identity read is a second resolver, and a second resolver is how
    two surfaces start answering one question differently. The union carries the
    folded spelling (so a Subject written before #12047 — no ``canonical_name`` —
    and one written after are both addressable) AND the variant spellings a
    non-destructive merge records in ``aliases`` (review round 4 P2: the
    raw-then-folded retry over ``name``/``canonical_name`` could not see those, so
    this door refused a holder the write door resolved).

    Split from :func:`wire_holds_role` so the CALLER can run it **before** it
    writes the role (#12047 review F5): a refused create must leave nothing
    behind, and a check after the write would journal a node the caller was told
    did not happen.
    """
    from .entity_identity import resolve_entity_id_same_entity

    if not holder:
        raise ValueError("holdsRole needs a holder (a Subject id or name)")
    g = sdk._get_proj().g
    # ⛔ ONE call over the WRITER's key space (#12047 review round 4 P2). The
    # raw-then-folded RETRY that used to live here passed `name_props=("name",
    # "canonical_name")` and no `alias_of`, so it covered the folded spelling of
    # those two props and NOT the variant spelling a non-destructive merge
    # records — `apply_entity_merge` puts the folded variant in the SURVIVOR's
    # `aliases` (see :func:`apply_entity_merge`). Measured consequence: after
    # merge+retire, `create_subject("Acme Corporaton")` lands on the survivor
    # while `create_entity(..., roleHolder="Acme Corporaton")` refused with "no
    # live :Subject" AND recorded a non-folded refusal for a name the write door
    # resolves — the exact #12120 reader/writer disagreement, one site short of
    # the nine adopted readers. `resolve_entity_id_same_entity` IS the writer's
    # key space (name · canonical_name · aliases, raw and folded), so the two
    # doors cannot disagree; it records a miss only when nothing resolved, so the
    # speculative-probe concern (review N6) is satisfied by one call.
    entity_id = resolve_entity_id_same_entity(g, "Subject", holder)
    if entity_id is None:
        if not required:
            return None
        raise ValueError(
            f"holdsRole holder {holder!r} matches no live :Subject — refusing to "
            f"wire the role to a guess")
    # A merged-away carrier is not the holder any more (same rule as the write
    # path): hop to the entity the merge recorded.
    return follow_resolved_from(g, "Subject", entity_id)


def refuse_role_self_edge(holder_id: str, role_id: str) -> None:
    """Raise when the DECLARED holder IS the role being written.

    A role is not its own holder (#1370's "no subject > wrong subject"): writing
    it would assert a category error as a fact, reported as success.

    ⛔ ONE spelling, callable BEFORE the write (#12047 round-3 P1). The refusal
    used to live only inside :func:`wire_holds_role`, which runs AFTER the role's
    node and its ``SubjectAdded`` journal line exist — so the caller was told the
    create failed while the graph recorded that the person had become a role.
    ``create_entity`` now calls this between resolving the role's id and
    ``_create_entity``; :func:`wire_holds_role` keeps calling it as its own guard
    (defence in depth for every other caller). A second copy of the message would
    be two spellings of one rule — the divergence ``named_kind`` exists to
    prevent.
    """
    if holder_id != role_id:
        return
    raise ValueError(
        f"holdsRole: {role_id!r} cannot hold itself — a role needs a "
        f"DIFFERENT person Subject as its holder. The role's name resolved "
        f"onto the holder itself (the two folds collide), so give the role "
        f"its own name (e.g. 'the owner role') rather than the person's")


def wire_holds_role(sdk, holder: str, role_id: str) -> dict:
    """Wire ``(holder:Subject)-[:holdsRole]->(role:Subject)`` — the missing producer.

    ``#12047`` measured **zero** ``holdsRole`` edges in both hosted graphs: the
    predicate is declared (ONTOLOGY §3.6, sanctioned and deliberately distinct
    from ``memberOf`` — ``:206``) and read (``sdk._holds_role_available``), but
    nothing PRODUCED it. A role Subject therefore floated free of the person who
    holds it. ``heldRole`` is never inferred from a name pattern — the holder is
    DECLARED by the caller (``create_subject(..., roleHolder=…)``).
    """
    holder_id = resolve_role_holder(sdk, holder)
    # Unreachable from `create_entity` (which refuses BEFORE the write), and kept
    # for every other caller: wiring a self edge is a category error either way.
    refuse_role_self_edge(holder_id, role_id)
    return sdk.create_edge("holdsRole", holder_id, role_id)
