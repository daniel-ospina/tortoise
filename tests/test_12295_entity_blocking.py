"""#12295 — the comparison-side key set and multi-key blocking, pinned.

What this file protects, and why each pin exists
------------------------------------------------
`#11901` measured the defect this module answers: **0 of 419 real surface
variants of an existing name attach, at every window width, in both retrieval
modes**, while 62 % of them are inside the window. The fix is a comparison-side
key (`name` is never rewritten — `#3584`/`#3590`) and a blocker that unions
several keys instead of one.

The tests below are hermetic (no graph, no model, no network): the key set, the
entropy gate, the index, the ambiguity discipline, and the two metrics. The
behavioural measurement on the corpus lives in `tools/candidate_recall_11901.py`
and its artifacts.

Run (a plain `pytest` is refused by the repo's DB seam, epic #1647 P4)::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_12295_entity_blocking.py -q
    uv run python tools/test_lane.py run -- tests/test_12295_entity_blocking.py -q
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.entity_blocking import (
    AMBIGUOUS_KEYS,
    BASENAME,
    EXACT,
    MAX_BLOCK_SIZE,
    BlockIndex,
    attach_keys,
    attach_matches,
    block_keys,
    is_path_shaped,
    key_of,
    low_entropy,
    lsh_bands,
    minhash_signature,
    pair_completeness,
    reduction_ratio,
    shingles,
)
from tortoise.entity_resolver import normalize_entity_name

# ── the key: derived, never the stored name ───────────────────────────────


def test_key_is_the_stored_canonical_fold_not_a_second_fold():
    """One fold, one source of truth — `#12047` already stores this key.

    A second fold is the "two sources of truth for one value" defect `#2730` §6.7
    refuses, and it would drift from `canonical_name` on the live graph.
    """
    for name in ["The Plan Doc", "the plan-doc", "the  plan doc", "THE PLAN DOC"]:
        assert key_of(name) == normalize_entity_name(name) == "plan doc"


def test_key_folds_every_measured_variant_class_that_is_injective():
    """case / article / separator / unicode NFKC all collapse to one key."""
    assert key_of("The Plan Doc") == key_of("the plan doc") == "plan doc"
    assert key_of("a plan_doc") == key_of("plan-doc")
    assert key_of("café") == key_of("cafe\u0301")       # NFKC composes the accent
    assert key_of("  Release-Notes  ") == "release notes"


def test_key_does_NOT_bind_a_different_noun():
    """Precision, stated as a pin: no similarity, so a near-miss cannot attach.

    This is the property that makes the fold safe without a threshold — the
    reason no second similarity constant is introduced (`#2730` §6.7).
    """
    assert key_of("the plan doc") != key_of("the play doc")
    assert key_of("the owner") != key_of("owner manual")


def test_key_of_never_returns_empty_for_punctuation_only_names():
    """An empty key would match EVERY entity — the fold must stay non-empty."""
    assert key_of("...") != ""
    assert key_of("!!!") != ""
    assert key_of(None) == ""


# ── the non-injective keys are marked, and only attach when unique ────────


def test_basename_key_is_declared_non_injective_and_is_symmetric():
    assert BASENAME in AMBIGUOUS_KEYS
    assert EXACT not in AMBIGUOUS_KEYS
    assert f"{BASENAME}:config py" in attach_keys("src/core/config.py")
    # Symmetric on purpose: a path and its basename must MEET on one key.
    assert f"{BASENAME}:config py" in attach_keys("config.py")
    assert is_path_shaped("src/core/config.py") and not is_path_shaped("config.py")


def test_hash_dropped_class_is_bound_by_the_fold_itself_no_second_key():
    """`normalize_entity_name` scrubs every non-word run, so `#12295` IS `12295`.

    A separate `#`-dropped namespace would be dead weight — and `#11901` measured
    that class at 0 % only because its harness fold scrubbed `[-_./]` and left
    the `#` behind.
    """
    assert key_of("#12295") == key_of("12295") == "12295"
    got = attach_matches("12295", [{"id": "o1", "name": "#12295"}])
    assert [m["id"] for m in got] == ["o1"]


def test_one_basename_carrier_attaches_on_a_COMPLETE_population():
    """`#11901`'s path class moves ONLY when exactly one carrier holds it.

    Attaching on a shared basename would link `lib/config.py` to
    `server/config.py` — a false merge, which `#2730` §6.7 records as invisible
    and hard to undo.
    """
    cands = [{"id": "o1", "name": "src/core/config.py", "kind": "core:other"}]
    got = attach_matches("api/config.py", cands, complete=True)
    assert [m["id"] for m in got] == ["o1"]
    assert got[0]["basename_only"] is True

    two = [*cands, {"id": "o2", "name": "server/config.py", "kind": "core:other"}]
    assert attach_matches("api/config.py", two, complete=True) == [], (
        "two basename carriers must BOTH be refused, never one silently picked")


def test_basename_never_attaches_on_a_capture_window():
    """A 3-row window is not the population — one hit there proves nothing.

    The capture prior passes `complete=False`, so a basename hit is a CANDIDATE
    for the adjudicator, never an auto-attach: the window happened to contain one
    `config.py`, and the graph may hold five.
    """
    cands = [{"id": "o1", "name": "src/core/config.py"}]
    assert attach_matches("api/config.py", cands, complete=False) == []


def test_injective_key_wins_over_a_non_injective_one():
    """If one candidate matches on the fold and another only on the basename,
    the fold decides — the ambiguity rule must not degrade a certain match."""
    cands = [
        {"id": "o1", "name": "the plan doc", "kind": "core:other"},
        {"id": "o2", "name": "plan-doc.md", "kind": "core:other"},
    ]
    got = attach_matches("the plan doc", cands)
    assert [m["id"] for m in got] == ["o1"]
    assert got[0]["basename_only"] is False


# ── the entropy gate: Zep's shape, blocking-side ──────────────────────────


def test_entropy_gate_refuses_short_and_repetitive_keys_only():
    """Zep's `_has_high_entropy`: short AND thin, or below the entropy floor.

    The gate guards the SHINGLE key generator; it must never suppress the exact
    key (Zep's own note: exact matching runs for all names regardless).
    """
    assert low_entropy("aa") is True                 # short and thin
    assert low_entropy("ababab") is True             # low entropy
    assert low_entropy("") is True
    assert low_entropy("plan doc") is False
    # …and a low-entropy name still blocks on its exact/prefix/token keys:
    keys = block_keys("aa")
    assert f"{EXACT}:aa" in keys
    assert not any(k.startswith("S:") for k in keys)


# ── the blocker ──────────────────────────────────────────────────────────


def test_shingles_and_bands_are_deterministic_across_processes():
    """`blake2b`, never Python's salted `hash()` — a rebuild must agree.

    A salted hash would make the band keys differ between two processes (and
    between two runs of the same process after PYTHONHASHSEED changes), so a
    persisted index could never be trusted and two runs could not be compared.
    """
    sig = minhash_signature("the plan doc")
    assert sig == minhash_signature("the plan doc")
    assert len(sig) == 32
    assert len(lsh_bands(sig)) == 8                  # 32 permutations / band 4
    assert shingles("ab") == frozenset({"ab"})       # shorter than SHINGLE_SIZE


def test_multikey_blocking_finds_what_the_single_prefix_key_cannot():
    """The measured gap: a word-order change shares no 3-char prefix.

    `#12047`'s single key is `folded[:3]`; these two names are one entity said
    two ways and share no prefix, so the old blocker never compared them.
    """
    index = BlockIndex()
    index.add("a", "the project alpha", label="Object")
    index.add("b", "alpha project", label="Object")
    assert set(index.candidates("alpha project", label="Object")) == {"a", "b"}
    # …the old key, for contrast, keeps them apart:
    assert key_of("the project alpha")[:3] != key_of("alpha project")[:3]


def test_blocking_is_per_label_object_and_subject_never_cross():
    index = BlockIndex()
    index.add("obj", "the owner", label="Object")
    index.add("sub", "the owner", label="Subject")
    assert index.candidates("the owner", label="Object") == ["obj"]
    assert index.candidates("the owner", label="Subject") == ["sub"]
    # …and the union both-labels read sees both, which is what a caller that did
    # not name a label asked for.
    assert set(index.candidates("the owner")) == {"obj", "sub"}


def test_oversized_block_is_skipped_and_REPORTED():
    """A dropped block must never read as "nothing shares this key"."""
    index = BlockIndex()
    for i in range(MAX_BLOCK_SIZE + 1):
        index.add(f"m{i}", "shared token here", label="Object")
    assert index.candidates("shared token here", label="Object") == []
    stats = index.stats()
    assert stats["oversized_skipped"], (
        "an oversized block was skipped without being reported — a caller would "
        "read that as a false negative about the data")


def test_index_never_pairs_a_member_with_itself():
    index = BlockIndex()
    index.add("a", "the plan doc", label="Object")
    assert index.candidates("the plan doc", label="Object", exclude="a") == []


def test_candidate_union_is_deduplicated_and_order_stable():
    index = BlockIndex()
    index.add("a", "the plan doc", label="Object")
    index.add("b", "the plan-doc", label="Object")      # same fold -> many keys
    first = index.candidates("the plan doc", label="Object")
    assert len(first) == len(set(first)) == 2
    assert first == index.candidates("the plan doc", label="Object")


# ── the metrics: both, or the number flatters ────────────────────────────


def test_pair_completeness_is_none_for_an_empty_population():
    """Never 1.0 — an empty fixture is not a perfect blocker."""
    assert pair_completeness([], []) is None
    assert pair_completeness([("a", "b")], [frozenset({"a", "b"})]) == 1.0
    assert pair_completeness([("a", "b"), ("c", "d")],
                             [frozenset({"a", "b"})]) == 0.5


def test_reduction_ratio_is_reported_beside_completeness():
    """Comparing everything reaches PC=1.0 and reduction 1.0 — not a gain."""
    assert reduction_ratio([], 0) is None
    assert reduction_ratio([frozenset({"a", "b"})], 2) == 1.0
    assert reduction_ratio([frozenset({"a", "b"})], 3) == 1 / 3
