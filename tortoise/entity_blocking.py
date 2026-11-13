"""Multi-key blocking and the comparison-side key set for entity identity (#12295).

WHERE WE ALREADY WERE, MEASURED — read this before adding a key
---------------------------------------------------------------
The *stored* folded key is not missing. `#12047` landed it: every write door
(`add_object`, `add_subject`, the `_emit` funnel, a real rename) fills
``canonical_name`` with :func:`tortoise.entity_resolver.normalize_entity_name`
(``sdk.py``: the `_emit` fill at ``:45886``; ``api.py:2628``; ``api.py:2671``;
``sdk.py``: the rename fill at ``:47229``), and the projection writes it ON CREATE
with a `coalesce` fill on match (``projection/entities.py``: the object arm at
``:5094``/``:5101``, the subject arm at ``:4958``/``:4975``). ``name`` was never
rewritten, which is exactly the shape `#12295` asks for — the key rides beside it.

What was missing was the **comparison** and the **blocker**. Both are WIRED NOW —
the bullets below describe the BEFORE state this change replaced, and each names
the shipped rung that replaced it:

* ``extractor_v2._find_existing_entity`` (``extractor_v2.py:5769``) compared
  ``_norm`` — whitespace collapse + lowercase — so a capture that said
  ``the plan-doc`` could not bind the node stored as ``the plan doc``. `#11901`
  measured the consequence: **0 of 419 real surface variants attach at every
  ``k``, in both retrieval modes**, while 62 % of them are *inside* the window.
  ⛔ The folded rung is now reached from that path: `_find_existing_entity`
  delegates to ``_find_by_folded_key`` (``extractor_v2.py:5823``), which binds on
  :func:`tortoise.entity_blocking.key_of` equality.
* ``entity_resolver.entity_candidates`` (``entity_resolver.py:383``) blocked on
  ONE key — ``block_key`` = the first :data:`entity_resolver.MIN_BLOCK_CHARS`
  folded characters — so a transposition or a word-order change
  (``the project alpha`` vs ``alpha project``) landed in two blocks and the pair
  was never compared. ⛔ The scan now blocks on the multi-key union of
  :func:`block_keys`, and
  `test_the_scan_finds_a_transposition_the_single_prefix_key_could_not` asserts
  that pair reaches ``similarity``.

THE SHAPE, TAKEN FROM THE SOURCE RATHER THAN FROM PROSE
-------------------------------------------------------
Zep/Graphiti (`graphiti_core/utils/maintenance/dedup_helpers.py`, read at
`getzep/graphiti@main`):

* ``_normalize_string_exact`` — *"Lowercase text and collapse whitespace so equal
  names map to the same key"* — is **byte-identical to our ``_norm``**. Zep's
  exact rung is where our capture prior already sits.
* ``_normalize_name_for_fuzzy`` strips everything outside ``[a-z0-9' ]`` and
  ``_shingles`` cuts **3-grams** over the space-stripped form; 32 MinHash
  permutations, band size 4, ``_FUZZY_JACCARD_THRESHOLD = 0.9``; the candidate
  set is the **union of LSH band buckets** (`_build_candidate_indexes` /
  ``_resolve_with_similarity``). That is the blocker we did not have.
* ``_has_high_entropy`` (``_NAME_ENTROPY_THRESHOLD = 1.5``,
  ``_MIN_NAME_LENGTH = 6``, ``_MIN_TOKEN_COUNT = 2``) guards the **fuzzy path
  only** — the module's own words: *"Exact normalized-name matching runs first
  for* all *names regardless of length or entropy. The entropy gate only guards
  the fuzzy (MinHash/LSH) path"*.
* Determinism is by construction: ``_hash_shingle`` uses ``blake2b``, never
  Python's salted ``hash()``. This module does the same.

⛔ WHAT THIS MODULE REFUSES TO DO — AND WHY EACH REFUSAL IS A RECORDED DECISION
-----------------------------------------------------------------------------
1. **It never rewrites ``name``.** ``name`` is the natural key and identity is
   the minted id (`#3584`/`#3590`: ``merge_key = "id"``,
   ``projection/entities.py:4907``). Every key here is derived, comparison-side,
   and never persisted by this module.
2. **No similarity score attaches an entity.** Zep auto-resolves at Jaccard
   ≥ 0.9; we do **not**. `#2730` §6.7 (a false merge is invisible and hard to
   undo; a false split is visible and cheap) and the `#11395` KEEP ruling make
   this ladder precision-first, and the whole point of the folded key is that it
   needs no threshold. Shingles and prefixes therefore generate **candidates**;
   only key EQUALITY attaches (:func:`attach_matches`), which is why no second
   similarity constant appears here (`#2730` refused one; the semantic rung
   reuses ``embeddings.DEFAULT_THRESHOLD``).
3. **A non-injective key never attaches alone — and the FOLD is exempt, by
   declaration.** A path basename
   (``lib/config.py``) is carried by every file of that name, so it is marked
   :data:`AMBIGUOUS_KEYS` and attaches **only when exactly one live candidate
   carries it AND the population is known complete** — the same "bare-form
   fallback ONLY when unambiguous" discipline ``_find_existing_entity`` already
   applies to kinds. ⛔ **The ``E`` fold is non-injective too, and it DOES attach
   alone**, which is a ``#12047`` property this module inherits rather than
   creates: :func:`key_of` collapses punctuation, so ``C++``, ``C#`` and ``C``
   all fold to ``c`` (likewise ``.NET``/``NET``, ``50%``/``50``), and a capture
   window holding exactly one of them binds the other —
   ``_find_existing_entity([{id:o1, name:"C#"}], "C++")`` returns ``o1`` by the
   ``folded`` rung. Only ONE collider need be present, so the basename arm's
   "exactly one carries it" test cannot see it. That is a DECLARED RESIDUAL, the
   third beside ``space-to-hyphen`` and ``basename-only``: the dedupe scan reports
   such a pair as ``exact``/``auto_mergeable`` so a reviewer meets it there, and
   the fold is not tightened here (it is the stored ``canonical_name`` key, and a
   tightening is a decision with its own measured cost). ⚠️ **No production caller passes ``complete=True`` today**:
   the only non-test caller is ``extractor_v2._find_by_folded_key``, whose
   population is a capped capture window, so a basename never decides there and
   the rule is currently held for a complete-population caller. ⚠️ A bare ``#`` reference is NOT in this class:
   ``normalize_entity_name`` folds ``#12295`` to ``12295``, so it travels the
   ordinary ``E`` key and attaches on equality like any other fold. It was the
   documented reason for a ``H`` key namespace that this module does not have.

THE METRIC
----------
Blocking is bounded work, and the bound is the metric: **pair completeness** —
the fraction of true duplicate pairs that land in at least one shared block
(`#11901`'s variant fixture is the corpus; the ER survey
`arXiv:1905.06167` is the standard treatment). Below 0.95 the blocker caps
recall before scoring starts, so a gain in reduction ratio bought with pair
completeness is a loss. :func:`pair_completeness` and
:func:`reduction_ratio` are here, next to the blocker that must report both,
rather than in one caller that would then be able to report only the flattering
half.
"""
from __future__ import annotations

import hashlib
import math
import re

from tortoise.entity_resolver import (
    ENTITY_LABELS,
    MIN_BLOCK_CHARS,
    normalize_entity_name,
)

#: A block bigger than this many members is **SKIPPED, never truncated** by a
#: single QUERY (**the query path only** — see :meth:`BlockIndex.candidates`, which
#: records the skip in :meth:`stats`): it contributes ZERO candidates, not its
#: first N. It is a latency bound on one read, not a limit on a SCAN:
#: `entity_candidates` compares every block, because a scan's pair set is what the
#: reviewer sees and narrowing it here would be a silent behaviour change. A block
#: this large is a degenerate key (one token shared by hundreds of entities) and
#: the caller should narrow it.
MAX_BLOCK_SIZE = 200

#: 3-gram shingles over the folded name with whitespace removed (Zep `_shingles`).
SHINGLE_SIZE = 3
#: MinHash permutations and band width (Zep: 32 / 4 → 8 bands).
MINHASH_PERMUTATIONS = 32
MINHASH_BAND_SIZE = 4
#: Characters kept per token for the word-order/transposition blocking key.
TOKEN_PREFIX_CHARS = 3

#: Zep's entropy gate, kept as the same declared constants so the gate is
#: auditable against its source. It gates the SHINGLE-key generator only —
#: nothing here attaches on a score, so no entropy decision can ever merge two
#: entities (see the module docstring, refusal 2).
NAME_ENTROPY_THRESHOLD = 1.5
MIN_NAME_LENGTH = 6
MIN_TOKEN_COUNT = 2

#: Key namespaces. ``E`` is the identity-bearing fold; ``B`` (path basename) is
#: non-injective and attaches only when unique; ``P``/``T``/``S`` never attach.
EXACT = "E"
BASENAME = "B"
PREFIX = "P"
TOKEN = "T"
SHINGLE = "S"

#: Keys whose value does not name exactly one entity. See module docstring, 3.
#: ⚠️ There is no separate ``#``-dropped key, and that is a MEASURED decision:
#: ``normalize_entity_name`` folds every non-word run, so ``#12295`` already folds
#: to ``12295`` on the ``E`` key — the `#`-dropped class is bound by the fold
#: itself, not by a second namespace. (`#11901`'s harness fold
#: ``_fold_punct`` scrubbed ``[-_./]`` only, which is why that class measured
#: 0 % there and is expected to move here.)
AMBIGUOUS_KEYS = frozenset({BASENAME})

_SPACES = re.compile(r"\s+")
_WORD = re.compile(r"[a-z0-9']+")


def key_of(name: object) -> str:
    """The identity-bearing comparison key (``E``) — the fold, never the name.

    Delegates to :func:`tortoise.entity_resolver.normalize_entity_name`, which is
    already stored on ``:Object``/``:Subject`` as ``canonical_name`` and is
    already the exact rung of the dedupe ladder (`#12047`). A second fold here
    would be a second source of truth for one value — the defect `#2730` §6.7
    and the ontology's synonyms rule both refuse.
    """
    return normalize_entity_name(name)


def low_entropy(key: str) -> bool:
    """Is this folded key too short/repetitive to key a shingle block on?

    Transcribed from Zep's ``_has_high_entropy`` **as an OR, not an AND**: a name
    is low-entropy when it is short AND thin (the source's refusal condition) or
    when its character entropy is under the threshold. Both refuse the fuzzy
    path only; exact matching runs for every name regardless (Zep's own note).
    """
    if not key:
        return True
    token_count = len(key.split())
    if len(key) < MIN_NAME_LENGTH and token_count < MIN_TOKEN_COUNT:
        return True
    return _entropy(key) < NAME_ENTROPY_THRESHOLD


def _entropy(key: str) -> float:
    """Shannon entropy over characters, spaces removed (Zep ``_name_entropy``)."""
    counts: dict[str, int] = {}
    for char in key.replace(" ", ""):
        counts[char] = counts.get(char, 0) + 1
    total = sum(counts.values())
    if not total:
        return 0.0
    return -sum((count / total) * math.log2(count / total)
                for count in counts.values())


def shingles(key: str) -> frozenset[str]:
    """3-gram character shingles over the space-stripped folded key."""
    cleaned = _SPACES.sub("", key or "")
    if len(cleaned) < SHINGLE_SIZE:
        return frozenset({cleaned} if cleaned else ())
    return frozenset(cleaned[i:i + SHINGLE_SIZE]
                     for i in range(len(cleaned) - SHINGLE_SIZE + 1))


def _hash_shingle(shingle: str, seed: int) -> int:
    """Deterministic 64-bit shingle hash — ``blake2b``, never salted ``hash()``."""
    return int.from_bytes(
        hashlib.blake2b(f"{seed}:{shingle}".encode(), digest_size=8).digest(),
        "big")


def minhash_signature(key: str) -> tuple[int, ...]:
    """MinHash signature over the key's shingles (Zep ``_minhash_signature``)."""
    grams = shingles(key)
    if not grams:
        return ()
    return tuple(min(_hash_shingle(g, seed) for g in grams)
                 for seed in range(MINHASH_PERMUTATIONS))


def lsh_bands(signature: tuple[int, ...]) -> tuple[tuple[int, int, ...], ...]:
    """LSH bands ``(band_offset, values…)``; a partial trailing band is dropped.

    The first element is the band's byte OFFSET into the signature
    (``0, 4, 8, …``), **not** a ``0..n-1`` band index: it is ``start`` from the
    ``range`` below. Zep's ``_lsh_bands`` returns **bare** bands and its caller
    (``_build_candidate_indexes``) supplies the ``enumerate`` index, so the two
    tables are not interchangeable across libraries — the offset here is
    ``band_index * MINHASH_BAND_SIZE``. No behaviour depends on which is used
    (the tuple is only stringified into a bucket key and both sides scale
    identically), so the name is stated rather than the value changed.
    """
    out = []
    for start in range(0, len(signature), MINHASH_BAND_SIZE):
        band = signature[start:start + MINHASH_BAND_SIZE]
        if len(band) == MINHASH_BAND_SIZE:
            out.append((start, *band))
    return tuple(out)


# ── the key set ──────────────────────────────────────────────────────────


def attach_keys(name: object, *, key: str | None = None) -> tuple[str, ...]:
    """Keys that may resolve a mention onto an existing entity, by EQUALITY.

    ``E`` is always present (and is what folds case, articles, separators,
    unicode and a leading ``#``). ``B`` is the path-basename key; because it is
    carried by every file of that name it is listed in :data:`AMBIGUOUS_KEYS`,
    and :func:`attach_matches` only lets it decide between two PATH-shaped
    names, only when exactly one candidate holds it.

    ``key`` overrides the fold for a row whose STORED key is not ``key_of(name)``
    — `#12047` writes ``canonical_name`` and `entity_resolver.scan_entities`
    scores ``normalize_entity_name(canonical or name)``, so a caller holding that
    stored key must be able to say so. Re-folding ``name`` instead would block
    and score on two different values for the same row.
    """
    raw = "" if name is None else str(name)
    base = raw.rstrip("/").rsplit("/", 1)[-1]
    keys = [f"{EXACT}:{key if key is not None else key_of(raw)}"]
    if base:
        # Symmetric: BOTH sides derive the basename, so a path and its bare
        # basename meet on this key. It is emitted for every non-empty name — for
        # an ordinary name it folds identically to ``E``, so it can never widen a
        # relation — and :func:`attach_matches` refuses to let it decide unless a
        # side is path-shaped.
        keys.append(f"{BASENAME}:{key_of(base)}")
    return tuple(dict.fromkeys(keys))     # order-stable, no duplicates


def is_path_shaped(name: object) -> bool:
    """Does this surface form carry a directory component?

    The basename key is symmetric (both sides derive it), but it may only DECIDE
    when at least one side is a path — otherwise ``B`` would be a second copy of
    ``E`` for every ordinary name and every ordinary name would read ambiguous.
    """
    return "/" in ("" if name is None else str(name))


def block_keys(name: object, *, key: str | None = None) -> tuple[str, ...]:
    """Every key this name blocks on — the candidate generators.

    ``P`` a folded prefix (this is the `#12047` key — kept because the scan used
    to block on it, so keeping it preserves **main's pairing set**; where the fold is
    at least ``MIN_BLOCK_CHARS`` long, ``E`` pairs are a subset of ``P`` pairs, so
    ``P`` adds no exactness of its own (a SHORTER fold gets no ``P`` key at all and
    is carried by ``E`` alone)), ``T`` a
    per-token prefix (word order and transposition), ``S`` an LSH shingle band
    (typo/separator classes FTS cannot reach), and the ``E``/``B`` attach keys. A
    low-entropy name blocks on ``E``/``P``/``T`` only: its shingle set is noise,
    which is the cost Zep's gate exists to avoid.
    """
    keys = list(attach_keys(name, key=key))
    key = key if key is not None else key_of(name)
    if not key:
        return tuple(keys)
    if len(key) >= MIN_BLOCK_CHARS:
        keys.append(f"{PREFIX}:{key[:MIN_BLOCK_CHARS]}")
    for token in _WORD.findall(key):
        if len(token) >= MIN_BLOCK_CHARS:
            keys.append(f"{TOKEN}:{token[:TOKEN_PREFIX_CHARS]}")
    if not low_entropy(key):
        keys.extend(f"{SHINGLE}:{band}" for band in lsh_bands(minhash_signature(key)))
    return tuple(dict.fromkeys(keys))


# ── blocking index ───────────────────────────────────────────────────────


class BlockIndex:
    """``key → members``, built once; a mention's candidates are the key union.

    The index is the *candidate generator*, never an identity authority: a hit
    says "compare these", and only :func:`attach_matches` (or the resolver's own
    rungs) may resolve. Members are partitioned per label, so an ``Object`` and a
    ``Subject`` that share a key can never be paired by this index alone.
    """

    __slots__ = ("_blocks", "_keys", "_members", "_oversized")

    def __init__(self) -> None:
        self._blocks: dict[tuple[str, str], list[str]] = {}
        self._members: dict[str, tuple[str, str]] = {}
        #: keys dropped for exceeding :data:`MAX_BLOCK_SIZE` — reported, never silent
        self._oversized: dict[str, int] = {}
        self._keys = 0

    def add(self, member_id: str, name: object, *, label: str = "",
            key: str | None = None) -> None:
        """Index one member under every key :func:`block_keys` yields.

        ``key`` is the member's STORED fold. Pass it whenever the caller already
        holds one, which `entity_resolver.scan_entities` does: it scores
        ``normalize_entity_name(canonical or name)`` (``entity_resolver.py:284``),
        so indexing this row by ``key_of(name)`` would BLOCK on one value and
        SCORE on another — and a row whose ``canonical_name`` differs from its
        name's fold (legal: a caller-supplied key, ``api.py:2671``, or a pre-
        `#12047` row) would then be invisible to every rung. One value, one key.

        A member whose fold is empty is NOT indexed. ⚠️ "Empty" is NARROWER than it
        sounds: a punctuation-only name is NOT empty, because `key_of` falls back to
        the stripped fold, so `...` keys as `...` and `!!!` as `!!!` (pinned at
        `tests/test_12295_entity_blocking.py`). The class this guard excludes is the
        fold that is WHITESPACE only — `"   "`, reachable because a reader admits
        any truthy name — which is the one that would put otherwise-unrelated names
        in one ``E`` block and pair them as ``exact``. The pre-existing guard in
        ``entity_candidates`` (``if not ent["folded"]: continue``) expressed where
        the key is made.
        """
        if not member_id:
            return
        if member_id in self._members:
            return
        folded = key if key is not None else key_of(name)
        if not folded:
            return
        self._members[member_id] = (label, folded)
        for block in block_keys(name, key=folded):
            self._blocks.setdefault((label, block), []).append(member_id)
            self._keys += 1

    @classmethod
    def from_rows(cls, rows, *, label_of=None) -> BlockIndex:
        """Build from ``{id, name}`` rows; a ``folded`` field is used when present."""
        index = cls()
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            label = (row.get("label") or row.get("_label")
                     or (label_of(row) if label_of else "") or "")
            stored = row.get("folded")
            index.add(str(row.get("id") or ""), row.get("name"),
                      label=str(label),
                      key=str(stored) if stored is not None else None)
        return index

    def candidates(self, name: object, *, label: str | None = None,
                   exclude: str | None = None) -> list[str]:
        """Ids sharing at least one block with ``name``, in first-seen order.

        A block above :data:`MAX_BLOCK_SIZE` is SKIPPED and recorded in
        :meth:`stats` — reporting the skip is the point: a silently dropped block
        reads as "nothing shares this key", which is a false negative the caller
        would attribute to the data.

        ⛔ ``label=None`` means **every label this index actually holds**, not the
        :data:`ENTITY_LABELS` list: an index built by :meth:`from_rows` carries
        whatever label its rows did (``""`` by default), and iterating the
        constant instead returned ``[]`` for it — a silent false negative that is
        indistinguishable from "nothing shares this key". The constant's order is
        kept first so a scan-path result order is unchanged; any other label the
        index holds is appended in sorted order for determinism.
        """
        seen: set[str] = set()
        out: list[str] = []
        if label is not None:
            labels = [label]
        else:
            present = {lbl for lbl, _ in self._blocks}
            labels = [lbl for lbl in ENTITY_LABELS if lbl in present]
            labels += sorted(present - set(ENTITY_LABELS))
        for key in block_keys(name):
            for lbl in labels:
                members = self._blocks.get((lbl, key))
                if not members:
                    continue
                if len(members) > MAX_BLOCK_SIZE:
                    self._oversized[key] = len(members)
                    continue
                for member_id in members:
                    if member_id in seen or (exclude and member_id == exclude):
                        continue
                    seen.add(member_id)
                    out.append(member_id)
        return out

    def blocks(self, *, label: str | None = None) -> list[list[str]]:
        """Every block's members — the SCAN path, which applies no size cap.

        :data:`MAX_BLOCK_SIZE` bounds one query's candidate list
        (:meth:`candidates`), never a scan's pair set: a scan that silently
        dropped a large block would lose pairs the reviewer could previously see,
        which is a behaviour change disguised as a bound. The block-size
        distribution is reported instead (:meth:`stats`), because an emerging
        giant block is the over-merge signal a cap would merely hide.
        """
        out = []
        for (lbl, _key), members in sorted(self._blocks.items()):
            if label is not None and lbl != label:
                continue
            out.append(sorted(set(members)))
        return out

    def stats(self) -> dict:
        sizes = [len(set(m)) for m in self._blocks.values()]
        return {
            "members": len(self._members),
            "blocks": len(self._blocks),
            #: `_keys` counts (member, block) PAIRS, not distinct keys — one member
            #: usually lands in several blocks, so this exceeds `blocks` and a
            #: key count it cannot be. Named for what it is; renaming this field
            #: is why the artifact's `index_stats` uses the same name.
            "key_memberships": self._keys,
            "max_block_size": max(sizes) if sizes else 0,
            "oversized_skipped": dict(sorted(self._oversized.items())),
            #: The QUERY path's cap. Published under a name that says so: the scan
            #: takes no cap, and a key called `max_block_size_allowed` beside an
            #: observed `max_block_size` of 500 reads as a bound the scan honoured.
            "query_candidate_cap": MAX_BLOCK_SIZE,
        }


def attach_matches(mention: object, candidates, *,
                   label: str | None = None,
                   complete: bool = False) -> list[dict]:
    """The candidates a mention may resolve onto **by key equality**.

    Returns ``[{id, key, name}]`` in candidate order, containing only matches this
    function is willing to let decide:

    * an ``E`` (fold) match always decides — equal folds, one folded name;
    * a ``B`` (basename) match decides **only** when at least one of the two
      names is path-shaped AND exactly one candidate matched on ``B`` AND that
      candidate did not also need ``B`` to win a tie. ``complete`` says the
      caller's candidate list IS the live population (the dedupe scan); a
      capture-time window is not, so its basename matches are candidates only.
      Attaching a shared basename inside a 3-row window would link
      ``lib/config.py`` to ``server/config.py`` on the strength of what happened
      to be retrieved — a false merge, invisible and hard to undo (`#2730` §6.7).
    """
    if not key_of(mention):
        return []
    want = set(attach_keys(mention))
    mention_path = is_path_shaped(mention)
    weak_ns = {f"{ns}:" for ns in AMBIGUOUS_KEYS}
    out: list[dict] = []
    for cand in candidates or ():
        if not isinstance(cand, dict) or not cand.get("id"):
            continue
        if label is not None and str(cand.get("label") or label) != label:
            continue
        cand_name = cand.get("name")
        # A row may carry its STORED fold (`canonical_name`, or `folded` from the
        # scan); prefer it over re-folding the name — one value, one key.
        stored = cand.get("folded") or cand.get("canonical_name")
        have = set(attach_keys(cand_name,
                               key=str(stored) if stored else None))
        strong = {k for k in want & have
                  if not any(k.startswith(ns) for ns in weak_ns)}
        if strong:
            out.append({"id": str(cand["id"]), "key": sorted(strong)[0],
                        "name": cand_name or "", "basename_only": False})
            continue
        if mention_path or is_path_shaped(cand_name):
            weak = {k for k in want & have if any(k.startswith(ns) for ns in weak_ns)}
            if weak:
                out.append({"id": str(cand["id"]), "key": sorted(weak)[0],
                            "name": cand_name or "", "basename_only": True})
    strong_hits = [m for m in out if not m["basename_only"]]
    if strong_hits:
        return strong_hits
    weak_hits = [m for m in out if m["basename_only"]]
    if complete and len(weak_hits) == 1:
        return weak_hits
    return []


# ── the metrics the blocker must report ──────────────────────────────────


def pair_completeness(true_pairs, blocked_pairs) -> float | None:
    """Fraction of true duplicate pairs sharing at least one block.

    ``blocked_pairs`` must be a superset-shaped container of the pairs the
    blocker actually produced (e.g. a set of ``frozenset({a, b})``).
    ``None`` when there is nothing to measure — never 1.0, which would read as
    "the blocker is perfect" for an empty population.
    """
    true_set = {frozenset(p) for p in true_pairs or ()}
    if not true_set:
        return None
    blocked = {frozenset(p) for p in blocked_pairs or ()}
    return len(true_set & blocked) / len(true_set)


def reduction_ratio(blocked_pairs, population: int) -> float | None:
    """Blocked pairs as a fraction of all pairs — the cost the completeness buys.

    ``population`` is the number of entities, so ``all pairs`` is
    ``n*(n-1)/2``. A caller whose blocker proposes a candidate LIST per mention
    (rather than materialising pairs) expresses the same quantity by passing
    ``(mention, candidate)`` for each proposal — that is what the measurement
    harness does, so the published ``reduction_ratio`` has ONE definition and the
    helper is the only place it is computed.

    Reported BESIDE :func:`pair_completeness`, always: a blocker can reach perfect
    completeness by comparing everything, and a number that rises while this one
    rises with it is not a gain.
    """
    if population is None or population < 2:
        return None
    all_pairs = population * (population - 1) / 2
    return len({frozenset(p) for p in blocked_pairs or ()}) / all_pairs
