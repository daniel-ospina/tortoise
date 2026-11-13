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
    """`blake2b`, never Python's salted `hash()` — pinned by LITERAL digits.

    Two calls in one process agree under a salted `hash()` too, so comparing two
    calls pins nothing about the property that matters: the band keys must be the
    same in a rebuilt process (PYTHONHASHSEED varies per process), or a persisted
    index could never be trusted and two runs could not be compared. The literal
    digest below is that cross-process witness — it was produced by an earlier
    process and is re-derived here in a different one.
    """
    from tortoise.entity_blocking import _hash_shingle

    assert _hash_shingle("abc", 0) == 7705568351334315143
    assert _hash_shingle("abc", 7) == 9497276989126298643
    assert minhash_signature("the plan doc")[:3] == (
        2235370570471054300, 532509505977748825, 647817455438147115)
    sig = minhash_signature("the plan doc")
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


def test_oversized_block_is_skipped_by_a_QUERY_and_REPORTED():
    """A dropped block must never read as "nothing shares this key".

    This pins the QUERY path (`candidates`) only. The SCAN path deliberately has
    no cap — see :func:`test_the_scan_reports_its_largest_block_and_caps_NOTHING`,
    because a cap there would drop pairs a smaller graph showed.
    """
    index = BlockIndex()
    for i in range(MAX_BLOCK_SIZE + 1):
        index.add(f"m{i}", "shared token here", label="Object")
    assert index.candidates("shared token here", label="Object") == []
    stats = index.stats()
    assert stats["oversized_skipped"], (
        "an oversized block was skipped without being reported — a caller would "
        "read that as a false negative about the data")
    # …and the scan still sees every block, so no pair is lost there.
    assert max(len(m) for m in index.blocks()) == MAX_BLOCK_SIZE + 1


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


# ── the LIVE consumer: `entity_resolver.entity_candidates` ────────────────
# ⛔ These exist because the module's own tests are not coverage of the scan: a
# wider block set is MONOTONE, so every pre-existing `entity_candidates` test
# stayed green when the hunk landed. The four tests below each red without the
# hunk they name — verified by reverting it, not asserted:
#   * the transposition  → 0 rows / 0 `similarity` calls under the single key
#   * the pair dedupe    → the shared pair reported once per block, not once
#   * the stored fold    → 0 rows when the index re-folds `name`
#   * the size cap       → 0 rows when a `<= MAX_BLOCK_SIZE` filter is restored
# `test_the_scan_still_reports_the_pair_a_prefix_shared_name_makes_exact` is the
# baseline those four are read against: it pins the rung itself (which the
# wider block set must NOT change), and it shares `P:pla` under BOTH old and new
# keys — so it is deliberately NOT a regression witness for the union.


def _stub_scan(monkeypatch, entities):
    """Point `entity_candidates` at a fixed population (no graph, no model)."""
    from tortoise import entity_resolver

    monkeypatch.setattr(entity_resolver, "scan_entities",
                        lambda graph, labels=None: list(entities))
    return entity_resolver


def _entity(mid, name, *, kind=None, created="2026-01-01T00:00:00Z",
            canonical=None, label="Object"):
    from tortoise.entity_resolver import normalize_entity_name

    row = {"id": mid, "label": label, "name": name,
           "canonical_name": canonical, "kind": kind, "createdAt": created,
           "dedup_reviewed": "", "dedup_target_id": None,
           "dedup_rejected_targets": None,
           "folded": normalize_entity_name(canonical or name)}
    return row


def test_the_scan_finds_a_transposition_the_single_prefix_key_could_not(monkeypatch):
    """`the project alpha` / `alpha project`: one noun, no shared 3-char prefix.

    `#12047`'s single key was `folded[:3]` (`pro` vs `alp`), so the pair never
    reached `similarity` at all — the claim is about the BLOCKER reaching the
    comparison, not about the verdict (`difflib` may still score a transposition
    under the review floor, and that is the fuzzy rung's business, not blocking's).
    This reds if the multi-key union is reverted to `block_key(ent["folded"])`.
    """
    from tortoise import entity_resolver

    compared: list[tuple[str, str]] = []
    real = entity_resolver.similarity

    def _spy(a, b):
        compared.append((a, b))
        return real(a, b)

    monkeypatch.setattr(entity_resolver, "similarity", _spy)
    er = _stub_scan(monkeypatch, [
        _entity("o1", "the project alpha", kind="core:other"),
        _entity("o2", "alpha project", kind="core:other", created="2026-01-02T00:00:00Z"),
    ])
    assert "pro" != "alp"
    er.entity_candidates(None, include_semantic=False)
    assert ("project alpha", "alpha project") in compared, (
        "the pair never reached the comparison — the block set is the old single "
        "3-char prefix key")


def test_the_scan_still_reports_the_pair_a_prefix_shared_name_makes_exact(monkeypatch):
    """The rung itself is unchanged: an exact folded pair is still `exact`."""
    er = _stub_scan(monkeypatch, [
        _entity("o1", "the plan doc", kind="core:other"),
        _entity("o2", "plan doc", kind="core:other", created="2026-01-02T00:00:00Z"),
    ])
    rows = er.entity_candidates(None, include_semantic=False)
    assert len(rows) == 1 and rows[0]["method"] == "exact"
    assert rows[0]["similarity"] == 1.0


def test_the_scan_COMPARES_each_pair_once_across_the_blocks_it_shares(monkeypatch):
    """The dedupe must skip the COMPARISON, not just the append.

    A union of keys puts one pair in several blocks. Checking the dedupe after
    ``similarity`` saved the dict append and left the repeated comparison the
    comment promised to avoid: measured 6 ``similarity`` calls for one pair in
    six blocks, and 3 for a pair that is never emitted at all (`#12295` round-2
    B4). Both are pinned here — the transposition case is the one a
    "record it when emitted" dedupe cannot cover, because it emits nothing.
    """
    import collections

    from tortoise import entity_resolver

    ents = [
        _entity("o1", "the project alpha", kind="core:other"),
        _entity("o2", "alpha project", kind="core:other",
                created="2026-01-02T00:00:00Z"),
        _entity("o3", "the plan doc", kind="core:other"),
        _entity("o4", "the plan-docs", kind="core:other",
                created="2026-01-02T00:00:00Z"),
    ]
    calls: collections.Counter = collections.Counter()
    real = entity_resolver.similarity

    def spy(a, b):
        calls[(min(a, b), max(a, b))] += 1
        return real(a, b)

    _stub_scan(monkeypatch, ents)
    monkeypatch.setattr(entity_resolver, "similarity", spy)
    entity_resolver.entity_candidates(None, include_semantic=False, limit=None)
    assert calls, "no comparison ran at all — the fixture reaches no block"
    for pair, n in calls.items():
        assert n == 1, (
            f"{pair} was compared {n}x — the pair dedupe runs after the work it "
            "exists to skip")


def test_the_scan_blocks_on_the_STORED_fold_not_on_a_re_fold_of_the_name(monkeypatch):
    """One value, one key: block on what the scan SCORES.

    `scan_entities` scores ``normalize_entity_name(canonical or name)``
    (`entity_resolver.py:269`), and a caller-supplied `canonical_name` is legal
    (`api.py:2671`) — as is a pre-`#12047` row whose fold differs from its name.
    Indexing such a row by ``key_of(name)`` blocks on one value and scores on
    another, so an `exact` 1.0 pair silently leaves the queue.
    """
    er = _stub_scan(monkeypatch, [
        # pre-#12047 shape: the stored key is `ab`, the name folds to `a b`
        _entity("o1", "a-b", kind="core:other", canonical="ab"),
        _entity("o2", "ab", kind="core:other", created="2026-01-02T00:00:00Z"),
    ])
    rows = er.entity_candidates(None, include_semantic=False)
    assert rows, ("the pair vanished: the index blocked on key_of(name) while the "
                  "scan scored canonical_name")
    assert rows[0]["method"] == "exact"


def test_the_scan_reports_its_largest_block_and_caps_NOTHING(monkeypatch):
    """A cap on the scan's pair set would be a behaviour change, not a bound.

    ⛔ The population is ``MAX_BLOCK_SIZE + 1`` **on purpose**: with fewer
    entities no cap could fire, and this test would stay green under exactly the
    defect it exists to catch (a `len(block) <= MAX_BLOCK_SIZE` filter on the
    scan restored it and 6 entities passed — #12295 round-2 B1). Every name
    here folds to one key, so the block IS the population.

    The row count is the witness: a capped scan drops the whole block and
    reports 0 rows, while an uncapped one reports one row per duplicate — so
    this asserts EXACTLY `n - 1` rows, i.e. `MAX_BLOCK_SIZE` duplicates read
    against the single survivor. The count comes from the FIXTURE, not from the
    survivor/tie rules: every name here folds to one key and the block IS the
    population, so an uncapped scan cannot report any fewer than `n - 1`.
    """
    from tortoise.entity_blocking import MAX_BLOCK_SIZE, BlockIndex

    n = MAX_BLOCK_SIZE + 1
    ents = [_entity(f"o{i:04d}", "the shared token here", kind="core:other",
                    created=f"2026-01-{(i % 28) + 1:02d}T{(i % 24):02d}:00:00Z")
            for i in range(n)]
    er = _stub_scan(monkeypatch, ents)
    rows = er.entity_candidates(None, include_semantic=False, limit=None)
    assert len(rows) == n - 1, (
        f"the scan compared {len(rows)} of {n - 1} duplicates — a size cap on the "
        "scan path dropped pairs a smaller graph would have reported")

    stats = BlockIndex.from_rows([dict(e) for e in ents]).stats()
    assert stats["max_block_size"] == n      # reported, not capped
    assert not stats.get("oversized_skipped"), (
        "the SCAN indexed a block as oversized — the cap belongs to the query path")
