"""Multi-key blocking and the comparison-side key set for entity identity (#12295).

WHERE WE ALREADY WERE, MEASURED — read this before adding a key
---------------------------------------------------------------
The *stored* folded key is not missing. `#12047` landed it: every write door
(`add_object`, `add_subject`, the `_emit` funnel, a real rename) fills
``canonical_name`` with :func:`tortoise.entity_resolver.normalize_entity_name`
(``sdk.py:45593``, ``api.py:2628``, ``api.py:2671``, ``sdk.py:46935``), and the
projection writes it ON CREATE with a `coalesce` fill on match
(``projection/entities.py:5077``, ``:5084``). ``name`` was never rewritten, which
is exactly the shape `#12295` asks for — the key rides beside it.

What was missing is the **comparison** and the **blocker**:

* ``extractor_v2._find_existing_entity`` (``extractor_v2.py:5769``) compares
  ``_norm`` — whitespace collapse + lowercase — so a capture that says
  ``the plan-doc`` cannot bind the node stored as ``the plan doc``. `#11901`
  measured the consequence: **0 of 419 real surface variants attach at every
  ``k``, in both retrieval modes**, while 62 % of them are *inside* the window.
* ``entity_resolver.entity_candidates`` (``entity_resolver.py:394``) blocks on
  ONE key — ``block_key`` = the first :data:`entity_resolver.MIN_BLOCK_CHARS`
  folded characters — so a transposition or a word-order change
  (``the project alpha`` vs ``alpha project``) lands in two blocks and the pair
  is never compared.

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
   ``projection/entities.py:5061``). Every key here is derived, comparison-side,
   and never persisted by this module.
2. **No similarity score attaches an entity.** Zep auto-resolves at Jaccard
   ≥ 0.9; we do **not**. `#2730` §6.7 (a false merge is invisible and hard to
   undo; a false split is visible and cheap) and the `#11395` KEEP ruling make
   this ladder precision-first, and the whole point of the folded key is that it
   needs no threshold. Shingles and prefixes therefore generate **candidates**;
   only key EQUALITY attaches (:func:`attach_matches`), which is why no second
   similarity constant appears here (`#2730` refused one; the semantic rung
   reuses ``embeddings.DEFAULT_THRESHOLD``).
3. **A non-injective key never attaches alone.** A path basename
   (``lib/config.py``) is carried by every file of that name, and a bare ``#``
   reference by every issue of that number. Those keys are marked
   :data:`AMBIGUOUS_KEYS` and attach **only when exactly one live candidate
   carries them** — the same "bare-form fallback ONLY when unambiguous"
   discipline ``_find_existing_entity`` already applies to kinds.

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

#: A block larger than this stops generating pairs and says so. The field's
#: control for the failure mode this module's own precedent names — a merge
#: cluster whose badness *"scales with the size of what it joined"* — and the
#: bound that keeps a pathological block from turning a blocked scan back into
#: all-pairs. Sized well above the measured population: `#12047` scanned 872
#: Subjects, and a block holding this many members is a key the caller should
#: narrow, not a population to compare.
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
    """LSH bands ``(band_index, values…)``; a partial trailing band is dropped."""
    out = []
    for start in range(0, len(signature), MINHASH_BAND_SIZE):
        band = signature[start:start + MINHASH_BAND_SIZE]
        if len(band) == MINHASH_BAND_SIZE:
            out.append((start, *band))
    return tuple(out)


# ── the key set ──────────────────────────────────────────────────────────


def attach_keys(name: object) -> tuple[str, ...]:
    """Keys that may resolve a mention onto an existing entity, by EQUALITY.

    ``E`` is always present (and is what folds case, articles, separators,
    unicode and a leading ``#``). ``B`` is the path-basename key; because it is
    carried by every file of that name it is listed in :data:`AMBIGUOUS_KEYS`,
    and :func:`attach_matches` only lets it decide between two PATH-shaped
    names, only when exactly one candidate holds it.
    """
    raw = "" if name is None else str(name)
    base = raw.rstrip("/").rsplit("/", 1)[-1]
    keys = [f"{EXACT}:{key_of(raw)}"]
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


def block_keys(name: object) -> tuple[str, ...]:
    """Every key this name blocks on — the candidate generators.

    ``P`` a folded prefix (the `#12047` key, kept so the exact rung can never be
    missed by the wider set), ``T`` a per-token prefix (word order and
    transposition), ``S`` an LSH shingle band (typo/separator classes FTS cannot
    reach), and the ``E``/``B``/``H`` attach keys. A low-entropy name blocks on
    ``E``/``P``/``T`` only: its shingle set is noise, which is the cost Zep's gate
    exists to avoid.
    """
    keys = list(attach_keys(name))
    key = key_of(name)
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

    def add(self, member_id: str, name: object, *, label: str = "") -> None:
        """Index one member under every key :func:`block_keys` yields.

        A member whose name folds to nothing is NOT indexed: an empty ``E`` key
        would put every punctuation-only name in ONE block and pair them as
        ``exact`` — the pre-existing guard in ``entity_candidates``
        (``if not ent["folded"]: continue``) expressed where the key is made.
        """
        if not member_id:
            return
        if member_id in self._members:
            return
        folded = key_of(name)
        if not folded:
            return
        self._members[member_id] = (label, folded)
        for key in block_keys(name):
            self._blocks.setdefault((label, key), []).append(member_id)
            self._keys += 1

    @classmethod
    def from_rows(cls, rows, *, label_of=None) -> BlockIndex:
        """Build from ``{id, name}`` rows (an optional ``label`` field is used)."""
        index = cls()
        for row in rows or ():
            if not isinstance(row, dict):
                continue
            label = (row.get("label") or row.get("_label")
                     or (label_of(row) if label_of else "") or "")
            index.add(str(row.get("id") or ""), row.get("name"),
                      label=str(label))
        return index

    def candidates(self, name: object, *, label: str | None = None,
                   exclude: str | None = None) -> list[str]:
        """Ids sharing at least one block with ``name``, in first-seen order.

        A block above :data:`MAX_BLOCK_SIZE` is SKIPPED and recorded in
        :meth:`stats` — reporting the skip is the point: a silently dropped block
        reads as "nothing shares this key", which is a false negative the caller
        would attribute to the data.
        """
        seen: set[str] = set()
        out: list[str] = []
        labels = [label] if label is not None else list(ENTITY_LABELS)
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
        """Every block's members (oversized ones included) — for a scan."""
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
            "keys": self._keys,
            "max_block_size": max(sizes) if sizes else 0,
            "oversized_skipped": dict(sorted(self._oversized.items())),
            "max_block_size_allowed": MAX_BLOCK_SIZE,
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
    if not attach_keys(mention):
        return []
    want = set(attach_keys(mention))
    mention_path = is_path_shaped(mention)
    out: list[dict] = []
    for cand in candidates or ():
        if not isinstance(cand, dict) or not cand.get("id"):
            continue
        if label is not None and str(cand.get("label") or label) != label:
            continue
        cand_name = cand.get("name")
        have = set(attach_keys(cand_name))
        strong = want & {k for k in have if not k.startswith(f"{BASENAME}:")}
        if strong:
            out.append({"id": str(cand["id"]), "key": sorted(strong)[0],
                        "name": cand_name or "", "basename_only": False})
            continue
        if mention_path or is_path_shaped(cand_name):
            weak = want & {k for k in have if k.startswith(f"{BASENAME}:")}
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

    Reported BESIDE :func:`pair_completeness`, always: a blocker can reach perfect
    completeness by comparing everything, and a number that rises while this one
    rises with it is not a gain.
    """
    if population is None or population < 2:
        return None
    all_pairs = population * (population - 1) / 2
    return len({frozenset(p) for p in blocked_pairs or ()}) / all_pairs
