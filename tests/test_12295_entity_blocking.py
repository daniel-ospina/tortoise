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
    PREFIX,
    SHINGLE,
    TOKEN,
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
    the fold decides — the ambiguity rule must not degrade a certain match.

    ⛔ THE FIXTURE IS LOAD-BEARING and the previous one proved nothing: it used
    `plan-doc.md` as the weak candidate, which folds to `plan doc md` and so shares
    NO key with the mention — it never entered `out` at all, so both assertions
    stayed green with the precedence block deleted. MEASURED with the
    `if strong_hits: return strong_hits` return removed, on THIS fixture: `[]`
    instead of `[(o1, False)]` — the weak-only path then runs with `complete=False`
    and returns nothing, so a CERTAIN fold match is silently DROPPED (not merely
    degraded), which is the failure this pin exists to catch. `o2` must therefore be
    a real basename carrier of the mention's own basename. The rule is load-bearing
    on a `complete=True` caller too: with the precedence gone,
    `extractor_v2._find_by_folded_key` hands the adjudicator a strong hit plus a weak
    one, `same_kind` reaches length 2, and a certain match degrades to `ambiguous`
    (a HOLD).
    """
    cands = [
        {"id": "o1", "name": "lib config py", "kind": "core:other"},
        {"id": "o2", "name": "src/config.py", "kind": "core:other"},
    ]
    got = attach_matches("lib/config.py", cands)
    assert [(m["id"], m["basename_only"]) for m in got] == [("o1", False)], (
        f"the basename carrier was not suppressed by the fold hit: "
        f"{[(m['id'], m['basename_only']) for m in got]!r}")


def test_attach_matches_prefers_the_stored_fold_over_a_re_fold_of_the_name():
    """Pins `stored = cand.get("folded") or cand.get("canonical_name")`.

    `#12047` stores the folded key on the row (`canonical_name`; `folded` from the
    dedupe scan), and `attach_keys` takes it as an override so ONE value blocks and
    attaches — re-folding `name` instead would key the same row two ways. MEASURED
    before this test: forcing `stored = None` in `attach_matches` left every test in
    this file green, because production `search_graph` rows carry only
    `{id, name, kind}` and no test supplied a stored fold.

    The fixture is chosen so the two folds DISAGREE: `name` is `a b`, whose own fold
    drops the leading article (`key_of("a b") == "b"`), while the stored
    `canonical_name` is `ab`. The mention is `ab`, so the pair binds on the stored
    fold and on nothing else.
    """
    stored = {"id": "o1", "name": "a b", "canonical_name": "ab"}
    got = attach_matches("ab", [stored])
    assert [m["id"] for m in got] == ["o1"], (
        f"the STORED fold was not preferred over a re-fold of `name`: {got!r} — "
        "deleting `stored = cand.get('folded') or cand.get('canonical_name')` must "
        "red this test")
    # …and the same row WITHOUT the stored fold must NOT bind: that is the
    # direction that fails if the preference is dropped rather than honoured.
    re_folded = {"id": "o1", "name": "a b"}
    assert attach_matches("ab", [re_folded]) == [], (
        "a row with no stored fold bound anyway, so this fixture does not "
        "discriminate the stored-fold preference (key_of('a b') is 'b', not 'ab')")


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


def test_every_key_namespace_in_the_union_is_pinned_by_a_pair_that_shares_only_it():
    """One witness per generator — `P`, `T`, `S` — each pair REQUIRING that key.

    ⛔ MEASURED before this test: deleting the `P:` append from `block_keys` left
    all 59 tests green, and deleting the whole shingle block left all 59 green too.
    The union is the candidate generator, so a deleted generator is a silent recall
    loss that nothing else in this file observes: `attach_matches` decides on `E`,
    and the tests that block on multiple keys never assert WHICH key met.

    ⛔ `entity_resolver.py`'s module docstring says
    `test_the_scan_finds_a_transposition_the_single_prefix_key_could_not` "pins"
    the `T` key. It does not: those two names share `T` *and* `S`, so either one
    survives the other's deletion.

    ⛔ AND "SHARES THAT KEY ALONE" WOULD BE FALSE OF TWO OF THESE THREE WITNESSES,
    which is the same shape this docstring just criticised above. MEASURED on this
    head — `set(block_keys(a)) & set(block_keys(b))` per pair:

      * `("ab cd", "ab gh")`                     -> `{P}`              — `P` alone
      * `("project alpha", "alpha project")`      -> `{T, S}`           — `T` *and* `S`
      * `("configuration guide", "configuration guidse")` -> `{P, T, S}` — all three

    So what each arm actually proves is what it ASSERTS, not what the word "alone"
    would suggest: (a) all three namespaces exist for one name, (b) the `P` witness
    requires `P:`, (c) the `T` witness requires `T:` in the intersection (it also
    holds `S:`, which is fine — arm (d) pins `S` independently, on `S`-only sets),
    and (d) the `S` witness requires `S`. Each arm reds when its own namespace is
    deleted, which is the discriminating property a witness needs.
    """
    # (a) all three namespaces are present in ONE ordinary high-entropy name.
    for ns in (PREFIX, TOKEN, SHINGLE):
        assert any(k.startswith(f"{ns}:") for k in block_keys("the project alpha")), (
            f"no `{ns}:` key for `the project alpha` — the `{ns}` generator was "
            "deleted from `block_keys`, and nothing else in this file reds")

    # (b) `P` ALONE, by set equality: both names fold to a 2-token key under 6
    # chars, so neither has a `T:` key, and their 3-gram shingle sets do not
    # intersect (VERIFIED on this head: the intersection below is exactly the one
    # prefix key). `MIN_BLOCK_CHARS == 3`, so the prefix is `"ab "` — with the
    # space, because the prefix is taken over the FOLDED key, not over a word.
    left, right = "ab cd", "ab gh"
    shared = set(block_keys(left)) & set(block_keys(right))
    assert shared == {"P:ab "}, (
        f"the `P` witness pair ({left!r}, {right!r}) no longer shares exactly one "
        f"`P:` key: {sorted(shared)!r}. This test pins `P` ALONE, so either the `P` "
        "generator was deleted (the assertion above already reds) or a second "
        "namespace now intersects here and the witness must be re-picked")

    # (c) `T` ALONE for the word-order class: the two orderings share their token
    # prefixes and NOT their prefix key, so deleting `T` cannot be masked by `P`.
    transposed = set(block_keys("project alpha")) & set(block_keys("alpha project"))
    assert any(k.startswith("T:") for k in transposed), (
        "a word-order change shares no `T:` key — the `T` generator was deleted "
        "from `block_keys`, and nothing else in this file reds")
    assert not any(k.startswith("P:") for k in transposed), (
        f"the transposition witness now shares a `P:` key too ({sorted(transposed)!r}), "
        "so it can no longer pin `T` on its own — `P` would survive a `T` deletion")

    # (d) `S` on a HIGH-ENTROPY name, and two shingle-near names must meet on at
    # least one band. The intersection is COMPUTED: a hardcoded digest would pin
    # one permutation seed as if it were the property.
    cfg_bands = {k for k in block_keys("configuration") if k.startswith("S:")}
    assert cfg_bands, (
        "`configuration` is high-entropy and got no `S:` band — the shingle "
        "generator was deleted from `block_keys`, and nothing else in this reds")
    near_a = {k for k in block_keys("configuration guide") if k.startswith("S:")}
    near_b = {k for k in block_keys("configuration guidse") if k.startswith("S:")}
    assert near_a & near_b, (
        "two shingle-near names share no `S:` band, so the LSH generator no longer "
        f"produces candidates ({len(near_a)} and {len(near_b)} bands, no overlap)")


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


def test_a_from_rows_index_answers_its_own_candidates():
    """`from_rows` rows carry no label, so `candidates()` must not assume one.

    ⛔ MEASURED before the fix: `from_rows([{id,name},…]).candidates(name)` was
    `[]` while `.blocks()` held the members — the index stored label `""` (the
    row shape has no label) but `candidates(label=None)` iterated the
    :data:`ENTITY_LABELS` constant, so every lookup missed. An empty list is
    indistinguishable from "nothing shares this key", which is exactly the false
    negative `test_oversized_block_is_skipped_by_a_QUERY_and_REPORTED` exists to
    prevent — and `from_rows` is the constructor the measurement tool uses.
    """
    index = BlockIndex.from_rows([{"id": "o1", "name": "the plan doc"},
                                  {"id": "o2", "name": "the plan doc"},
                                  {"id": "o3", "name": "another name"}])
    found = index.candidates("the plan doc")
    #: ⛔ THIS is the assertion the label fallback is pinned by: restoring
    #: `labels = list(ENTITY_LABELS)` reds here (`a from_rows index answered []`).
    assert sorted(found) == ["o1", "o2"], (
        f"a from_rows index answered {found!r}; its own blocks() hold "
        f"{index.blocks()!r}")
    #: The stored fold is what the index blocks on, so a re-fold of the mention
    #: must still find them. (Do NOT assert that an unrelated name finds nothing:
    #: the SHINGLE bands deliberately surface fuzzy candidates, so blocking is not
    #: an exact-key lookup and that assertion would pin the wrong contract.)
    assert sorted(index.candidates("The Plan-Doc")) == ["o1", "o2"]
    #: An explicit label still SCOPES the query — this pins the scoping contract,
    #: NOT the fallback (the fallback's own pin is the assertion above).
    assert index.candidates("the plan doc", label="Object") == [], (
        "an explicit label must still scope the query to that label's blocks")


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

    `scan_entities` scores ``normalize_entity_name(canonical or name)`` (cited by
    SYMBOL rather than by line, so an insertion in `entity_resolver.py` cannot rot
    it), and a caller-supplied `canonical_name` is legal
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

    #: ⛔ DO NOT assert `not stats.get("oversized_skipped")` here. That key is
    #: written only by `candidates()` — a QUERY — and the index below never answers
    #: one, so the assertion could never fail and would pin nothing. MEASURED:
    #: restoring the `<= MAX_BLOCK_SIZE` filter in `blocks()` leaves it green while
    #: the row-count assertion above fires (200 rows -> 0). The discriminating
    #: assertion is the row count; this one pins only that the size is REPORTED
    #: rather than truncated at index time.
    stats = BlockIndex.from_rows([dict(e) for e in ents]).stats()
    assert stats["max_block_size"] == n      # reported, not capped


def test_sibling_spellings_of_ONE_entity_surface_as_a_refused_TIE(monkeypatch):
    """Pins the DECLARED cost of the per-block settle, in the fail-CLOSED direction.

    ⛔ This is the residual `entity_resolver.entity_candidates` documents twice
    (its "the difference is deliberate" note above `emitted`, and the "AND ONE
    ARM CAN MAKE A ROW UNMERGEABLE" block): with several spellings of ONE entity,
    the newest candidates gain a second 1.0 row against a SIBLING duplicate and
    come out ``ambiguous``/``tied_with``. Measured on four spellings of one entity
    (all folding to ``plan doc``): o2 merges cleanly into o1, o3/o4 do not, and
    ``entity_pair_for`` — the function ``approve_merge`` applies — RAISES on both.

    ⛔ Why that is the right direction and not a bug to fix: a candidate that
    folds identically to two carriers is indistinguishable at this layer from a
    candidate that folds identically to two DIFFERENT entities, and the second
    case is the declared `E`-fold residual (``C++``/``C#``/``C``). Merging one of
    those is a FALSE MERGE — invisible, and the failure `#2730` §6.7 traded away
    for cheap false splits. Reviewers saw this row and proposed hoisting
    ``settled`` out of the block loop; measured, that collapses the two-carrier
    tie to a clean row (``test_the_per_block_settle_is_what_keeps_a_TWO_CARRIER_tie_VISIBLE``
    pins why it is refused). A lane that wants the mergeability back is REOPENING
    #2730 §6.7, not fixing a regression — say so in the issue, do not flip it here.
    """
    from tortoise import entity_resolver

    ents = [
        _entity("o1", "the plan doc", kind="core:other"),
        _entity("o2", "plan doc", kind="core:other",
                created="2026-01-02T00:00:00Z"),
        _entity("o3", "plan_doc", kind="core:other",
                created="2026-01-03T00:00:00Z"),
        _entity("o4", "plan-doc", kind="core:other",
                created="2026-01-04T00:00:00Z"),
    ]
    assert {e["folded"] for e in ents} == {"plan doc"}, (
        "the four spellings no longer share one fold — this fixture is not measuring "
        "the sibling-duplicate class")
    _stub_scan(monkeypatch, ents)
    rows = entity_resolver.entity_candidates(None, include_semantic=False, limit=None)
    by_id = {r["id"]: r for r in rows}

    assert by_id["o2"]["ambiguous"] is False, by_id["o2"]
    assert entity_resolver.entity_pair_for(None, "o2")["survivor_id"] == "o1"

    for cid, tied in (("o3", ["o2"]), ("o4", ["o2", "o3"])):
        row = by_id[cid]
        assert row["ambiguous"] is True, (
            f"{cid} is no longer reported as a tie: {row} — the per-block settle was "
            "hoisted, which hides the two-CARRIER tie too (see the sibling test)")
        assert row["tied_with"] == tied, row
        assert row["existing_id"] == "o1", row
        try:
            entity_resolver.entity_pair_for(None, cid)
        except ValueError as exc:
            assert "refusing to guess" in str(exc), exc
        else:  # pragma: no cover - the failure this pin exists for
            raise AssertionError(
                f"{cid} resolved to a single survivor — `approve_merge` would fold one "
                "of several spellings of ONE entity on a guess")


def test_the_per_block_settle_is_what_keeps_a_TWO_CARRIER_tie_VISIBLE(monkeypatch):
    """The tie must survive across BLOCKS, or the `E`-fold residual becomes a merge.

    ``C++`` / ``C#`` / ``C`` are three DIFFERENT entities that fold to one key
    (`#12295`'s third declared residual). They land in more than one block, and
    ``settled`` is re-initialised per block, so ``C`` is compared against BOTH
    carriers and comes out ``ambiguous`` — ``entity_pair_for`` refuses, which is
    the only safe answer.

    ⛔ MEASURED, and this is why the reviewer-proposed fix is refused: hoisting
    ``settled`` beside ``emitted`` (one line, scan scope) settles ``C`` against the
    first carrier it meets, and the SAME fixture then reads ``o3 -> o1,
    ambiguous False`` with ``entity_pair_for('o3') -> 'o1'`` — i.e. ``approve_merge``
    would fold ``C`` into ``C++`` while ``C#`` exists. That is a false merge, so the
    fix that buys back the sibling-duplicate mergeability
    (``test_sibling_spellings_of_ONE_entity_surface_as_a_refused_TIE``) spends the
    declared residual to get it. This test reds if anyone hoists it.
    """
    from tortoise import entity_resolver

    ents = [
        _entity("o1", "C++", kind="core:other"),
        _entity("o2", "C#", kind="core:other", created="2026-01-02T00:00:00Z"),
        _entity("o3", "C", kind="core:other", created="2026-01-03T00:00:00Z"),
    ]
    assert {e["folded"] for e in ents} == {"c"}, (
        "the fold no longer collides here — invariant 3's residual is stale")
    #: The collision is only VISIBLE across blocks: with a single block the
    #: per-block settle inside one block would hide it too. Asserted so a future
    #: single-key blocker cannot quietly turn this fixture into a tautology.
    assert len(BlockIndex.from_rows(ents).blocks()) > 1, (
        "this fixture must span more than one block, or it cannot distinguish the "
        "per-block settle from a scan-scope one")
    _stub_scan(monkeypatch, ents)
    rows = entity_resolver.entity_candidates(None, include_semantic=False, limit=None)
    by_id = {r["id"]: r for r in rows}

    assert by_id["o3"]["ambiguous"] is True, (
        f"`C` is no longer a tie against BOTH carriers: {by_id['o3']} — a hoisted "
        "`settled` would let approve_merge fold `C` into `C++`")
    assert by_id["o3"]["tied_with"] == ["o2"], by_id["o3"]
    try:
        entity_resolver.entity_pair_for(None, "o3")
    except ValueError as exc:
        assert "refusing to guess" in str(exc), exc
    else:  # pragma: no cover - the false merge this pin exists for
        raise AssertionError(
            "`C` resolved to ONE survivor while `C#` is a different entity — the "
            "declared E-fold residual just became a false merge")


def test_the_fold_collision_is_a_declared_residual_not_a_regression():
    """Pins the THIRD declared residual: `E` is non-injective and attaches alone.

    ⛔ This test asserts the behaviour this PR *declares*, not behaviour it
    introduced: `key_of` collapses punctuation, so `C++`, `C#` and `C` share one
    folded key and a capture window holding exactly ONE collider binds the other
    through the `folded` rung. The basename arm cannot see it (only one collider
    need be present), so it is a residual of the fold — a `#12047` property the
    dedupe scan reports as `exact`/`auto_mergeable` — and the module docstring's
    invariant 3 names it as such (beside `space-to-hyphen` and `basename-only`).

    Pinned from BOTH directions on purpose: if the fold is ever tightened, this
    reds and the docstring's residual list must be updated in the same commit —
    whereas a silent tightening would leave the docstring claiming a cost the code
    no longer pays.
    """
    from tortoise.extractor_v2 import _find_existing_entity

    keys = {name: key_of(name) for name in ("C++", "C#", "C", ".NET", "NET")}
    assert keys["C++"] == keys["C#"] == keys["C"], (
        f"the fold no longer collides on these: {keys} — the declared residual in "
        "entity_blocking.py invariant 3 is now stale")
    assert keys[".NET"] == keys["NET"]

    existing = [{"id": "obj-csharp", "name": "C#", "kind": "core:other"}]
    node, method = _find_existing_entity(existing, "C++", "core:other")
    assert (node["id"], method) == ("obj-csharp", "folded"), (
        f"`C++` no longer binds a lone `C#` candidate (got {(node['id'], method)!r}) — if "
        "that is deliberate, remove the residual from invariant 3 instead of "
        "leaving it declared")


def test_a_whitespace_only_fold_is_never_indexed():
    """Pins the empty-fold guard, whose justification this branch rewrote.

    ⛔ MEASURED before this test: deleting `if not folded: return` from
    `BlockIndex.add` left all 25 tests green, and two unrelated whitespace-only
    names then landed in ONE `E:` block and paired as `exact` — the mass-merge the
    guard exists to prevent, unpinned. `"   "` is reachable: a reader admits any
    truthy `name`, so the class is real even though punctuation-only names are NOT
    in it (they fold to themselves, `key_of("!!!") == "!!!"`).
    """
    index = BlockIndex()
    index.add("a", "   ", label="Object")
    index.add("b", "\t", label="Object")
    assert index.stats()["members"] == 0, (
        "a whitespace-only fold was indexed — two unrelated names now share one "
        f"`E:` block: {index.blocks()!r}")
    #: ⛔ Asserted on the INDEX's own structure, not on a fake member tuple: the
    #: previous line here was `b[1].startswith("E:")` over `index.blocks()`, whose
    #: elements are LISTS OF MEMBER IDS — `b[1]` is an id, never a key, so the
    #: assertion was vacuous while the guard held and would have raised
    #: `IndexError` on any non-empty one-member block.
    assert index.blocks() == [], (
        f"a block was created for an empty fold: {index.blocks()!r}")
    #: A punctuation-only name is NOT this class: it folds to itself and is indexed.
    index.add("c", "!!!", label="Object")
    assert index.stats()["members"] == 1, (
        "a punctuation-only name was dropped as if its fold were empty — `key_of` "
        "falls back to the stripped fold, so `!!!` keys as `!!!`")
