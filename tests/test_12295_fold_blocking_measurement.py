"""#12295 — the fold/blocking measurement's internal consistency, pinned.

The artifact is `docs/research/2026-11-12-12295-fold-blocking/measurement.json`,
produced by the SAME harness `#11901` shipped (`tools/candidate_recall_11901.py`,
reused, not rebuilt) with three additive arms: `attached_norm_only` (a frozen copy
of pre-#12295 phase 1), `in_multikey_block` (the multi-key blocker's pair
completeness) and `duplicate_rate` (per configuration).

What is pinned here is NOT "the numbers are good" — it is that the numbers the
brief quotes are the ones the artifact holds, and that the artifact cannot be
internally inconsistent while still being quotable. `#11901` paid for that
lesson twice (a pooled mean on a published surface that no committed artifact
held, corrected in a follow-up comment).

Run::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_12295_fold_blocking_measurement.py -q
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

DOC_DIR = (Path(__file__).resolve().parent.parent
           / "docs" / "research" / "2026-11-12-12295-fold-blocking")
ARTIFACT = DOC_DIR / "measurement.json"
BRIEF = DOC_DIR / "research-brief.md"


def _quotes(text: str, value: object) -> bool:
    """True iff ``text`` quotes ``value`` as a COMPLETE number, not a prefix of one.

    ⛔ A bare ``str(value) in text`` needle is satisfied by a SHORTER artifact
    value: with the artifact mutated ``reduction_ratio`` 0.0255 -> 0.02 the
    generated needle is ``"0.02"``, which is a substring of the brief's
    ``"0.0255"`` — so the pin stayed green over an artifact that contradicts the
    brief (measured #12304 review round 24: the four mutations below and this one
    each gave ``9 passed``). Every numeric needle therefore goes through this.

    ⛔ AND THE TRAILING BOUNDARY HAS TO REJECT A DECIMAL TAIL, not just a digit:
    with only ``(?!\d)`` an INTEGER needle matches the integer part of a decimal
    the prose happens to contain — ``_quotes("x 42.65 per", 42)`` was ``True``,
    so mutating ``attached_folded`` 386 -> 42 left the suite green by matching
    the brief's ``42.65`` (measured #12304 review round 25). ``(?!\.\d)`` closes
    that direction without rejecting a legitimate sentence-final ``386.``.
    """
    pattern = r"(?<![\d.])" + re.escape(str(value)) + r"(?!\d)(?!\.\d)"
    return re.search(pattern, text) is not None


@pytest.fixture(scope="module")
def report() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


def test_the_artifact_declares_one_uniform_retrieval_mode(report):
    """A mixture published as one mode is the false-label class #2952 refuses."""
    r = report["retrieval"]
    assert r["uniform_windows"] is True
    assert report.get("measured_for") == 12295, (
        "the artifact does not name the issue it is evidence for")
    #: The run MUST be identifiable from the brief alone. Without these three the
    #: brief can describe a different run than the artifact holds — which it did
    #: (#12295 round-2 B2): the header named elapsed/graph/digest from the run
    #: BEFORE the reviewer fixes, so a reader reproducing via the digest landed on
    #: a population the artifact does not contain.
    brief = BRIEF.read_text()
    corpus = report["corpus"]
    for field in ("graph", "population_digest", "elapsed_s"):
        assert str(corpus[field]) in brief, (
            f"the brief does not name the artifact's corpus.{field} "
            f"({corpus[field]!r}) — the header describes a different run")
    assert report.get("code_head"), "the artifact does not name the commit it measured"
    head = str(report["code_head"])
    assert re.fullmatch(r"[0-9a-f]{40}", head), (
        f"code_head is not a full commit sha: {head!r}")
    #: ⛔ THE BRIEF MUST NAME THE SAME HEAD AS THE ARTIFACT. This is the class the
    #: previous three checks left open: they pin `graph`/`population_digest`/
    #: `elapsed_s`, but nothing pinned `code_head`, so the brief went on naming
    #: `437d5d17a5f5` — a commit of the FIRST rebase generation, dangling exactly
    #: as the disqualifying `e9a572d4b4` beside it is — through two further rebases
    #: while the artifact had moved to `ae63e846e410` (#12304 review round 10). Two
    #: documents, one measurement, two heads; and the brief's own paragraph is
    #: what makes it dangerous, because it argues at length about which heads are
    #: `git merge-base --is-ancestor`-able. A reader reproducing from the brief
    #: lands on a commit that no longer exists.
    assert head in brief, (
        f"the brief names no {head} — it describes a different code head than the "
        "artifact, the same divergence #12295 round-2 B2 had for the corpus fields")
    #: ⛔ THE NOTE IS REQUIRED ON EVERY READ, ABOVE any git gate. The #12304 rebase
    #: rewrote the measured commit out of every ref, so the field names a rebased
    #: stand-in; without the note the artifact cannot say which commit the numbers
    #: actually describe. It is checked here — not inside the git branch — because a
    #: shallow CI clone has no history objects at all, which is exactly the
    #: environment where the original defect (a dead 40-hex sha passing as evidence)
    #: was invisible.
    note = report.get("code_head_note")
    assert note, (
        "code_head is a rebased stand-in but the artifact does not say so; the note "
        "must name the commit actually measured and the equivalence it claims")
    measured = re.search(r"[0-9a-f]{40}", str(note))
    assert measured, (
        "code_head_note names no full 40-hex commit — it must name the commit the "
        "numbers were measured at")
    for _f in ("entity_resolver.py", "entity_blocking.py", "extractor_v2.py"):
        assert _f in str(note), (
            f"code_head_note does not name {_f} — the equivalence claim must be "
            "scoped to the files the measurement exercises")
    #: ⛔ THE NOTE MUST NAME A DIFFERENT COMMIT THAN THE FIELD. Every arm below is
    #: keyed on `other != head`; a note that repeats `code_head` ("measured at
    #: <code_head> — the same commit, so no substitution occurred") therefore
    #: satisfies the note-shape checks while disclosing nothing, and skips the one
    #: arm that still has teeth after the rail's squash merge (a fresh clone holds
    #: NEITHER sha, so dead-sha/absent/dishonest all take the skip path there —
    #: only this shape check and the equivalence arm can red). A note whose sha is
    #: the field's own sha is a tautology, not provenance.
    assert measured.group(0) != head, (
        f"code_head_note names code_head itself ({head}) — a note that repeats the "
        "field discloses no substitution, so the equivalence claim is vacuous")
    #: Git is OPTIONAL here: a shallow clone, or an image with no git binary, must
    #: not red a CORRECT artifact — an over-block is the failure this guard must not
    #: add. Where git and BOTH objects are present, require the two commits to be
    #: code-equivalent on the three measurement files. ⛔ NOT ancestry: the rail
    #: SQUASH-merges (`admin-merge.sh` defaults to `--squash`), so after merge the
    #: branch commit is not an ancestor of main while its object survives in every
    #: object DB that fetched the PR — an ancestry assert would red healthy main.
    #: ⛔ THESE TWO READ THE ARTIFACT ONLY, so they run ABOVE the git gate: a
    #: `pytest.skip` inside that gate aborts THIS test, and putting them below
    #: it meant every fresh clone (where neither sha is an object — the normal
    #: post-squash shape) skipped the retrieval-mode check entirely. MEASURED:
    #: an artifact declaring `legs_seen.vector=False` and
    #: `window_modes.keyword_only=5` — the keyword-only false-label class this
    #: file exists to refuse — reported `1 skipped … UNVERIFIED` instead of red.
    assert r["legs_seen"].get("vector") is True, (
        "the artifact is not the fused read — re-run with the embeddings extra "
        "(a keyword-only figure is never a figure about the production read)")
    assert r["window_modes"]["keyword_only"] == 0
    repo = Path(__file__).resolve().parent.parent
    if (repo / ".git").exists() and shutil.which("git"):
        #: ⛔ EVERY git subprocess here needs a timeout: on a promisor/partial
        #: clone an ABSENT object makes `git cat-file -e` spawn a lazy fetch whose
        #: negotiation can burn minutes before failing (MEMORY.md
        #: `[git-exit-code-is-not-proof]`: 5m46s of CPU, suite aborted at pytest's
        #: 280 s faulthandler). A timeout is "could not answer", which this guard
        #: must treat exactly like a shallow checkout — never as a red.
        def _git(*args: str):
            try:
                return subprocess.run(["git", "-C", str(repo), *args],
                                      capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                #: ⛔ A TIMEOUT IS "COULD NOT MEASURE" AND MUST SAY SO, not pass.
                #: Returning None here let both call sites below skip their
                #: assertions and go GREEN — so the promisor-clone hang, which is
                #: the whole reason the timeout exists, would disarm the guard in
                #: the one case it was added for. SKIP is the visible form: it is
                #: not a pass, and it names itself in the report.
                pytest.skip("git " + " ".join(args) + " did not answer within 10s — "
                            "code_head equivalence UNVERIFIED")

        def _has(sha: str) -> bool:
            r = _git("cat-file", "-e", f"{sha}^{{commit}}")
            return r is not None and r.returncode == 0

        other = measured.group(0)
        #: ⛔ A DEAD SHA IS NOT A SHALLOW CLONE. The skip below is for "could not
        #: answer"; it must not also cover "answered, and the artifact's head is not
        #: an object here". When the note's measured commit IS resolvable, history is
        #: demonstrably present, so an absent `code_head` is the very defect this
        #: guard exists for — a dead 40-hex sha passing as evidence — not a clone that
        #: cannot see it.
        if _has(other) and not _has(head):
            raise AssertionError(
                f"code_head {head} is not an object in this repo, while the measured "
                f"commit {other} is — history is present, so this is a dead sha, not "
                "a shallow clone")
        #: ⛔ NO "EVERY QUOTED SHA RESOLVES" ARM HERE, DELIBERATELY — measured, twice.
        #: `head in brief` (above) binds only the ONE sha the brief shares with the
        #: field; the provenance paragraph before it quotes a whole list, and a
        #: rebase that rewrites the branch leaves every one of them dangling while the
        #: header still reads correct — which is what happened: the brief named
        #: `437d5d17a5f5` plus four first-generation shas through TWO rebases (#12304
        #: round 10), all five failing `merge-base --is-ancestor`.
        #: The obvious pin for the rest is `git cat-file -e <sha>` for each sha the
        #: brief quotes, and it CANNOT RED ON THE DEFECT IT LOOKS LIKE IT CATCHES. The
        #: shas this class produces are rebased-OUT commits: they leave every ref but
        #: their objects stay in the local object DB, so `cat-file` answers 0 and the
        #: arm stays green exactly where it is supposed to fire. MEASURED on this
        #: branch: substituting a first-generation sha for a live one in the brief left
        #: this test PASSING (exit 0), while the `head in brief` arm above reds on the
        #: same mutation (exit 1). An ancestry assert is the other candidate and is
        #: worse: the rail SQUASH-merges, so after merge NONE of these branch commits
        #: is an ancestor of `main` and a correct brief would red on healthy main — the
        #: over-block this file already carries two comments about avoiding.
        #: So the class is closed where it can be closed — the artifact's own head must
        #: appear in the brief verbatim (above), which is the divergence that actually
        #: occurred (#12304 round 10) — and this gap is DECLARED rather than papered
        #: over with an arm that cannot fail. ⛔ Do NOT read a gate into this comment:
        #: nothing below it is conditional on `_has(other)`, and an earlier cut left a
        #: bare `if _has(other): pass` here that read as one.
        if other != head:
            #: ⛔ AN ABSENT OBJECT IS "COULD NOT MEASURE", NOT A PASS. A fresh clone
            #: of `main` after the squash-merge has neither sha (one is a branch
            #: commit, the other is dangling), so silently skipping the comparison
            #: here would disarm the guard in the NORMAL CI shape — the same
            #: fail-open as the timeout arm. SKIP is visible and is still not a red,
            #: so a correct artifact is never over-blocked.
            for _sha, _why in ((head, "code_head"), (other, "the head the note names")):
                if not _has(_sha):
                    pytest.skip(f"{_why} ({_sha}) is not an object in this clone — "
                                "code_head equivalence UNVERIFIED")
            scoped = _git("diff", "--quiet", other, head, "--",
                          "tortoise/entity_resolver.py",
                          "tortoise/entity_blocking.py",
                          "tortoise/extractor_v2.py")
            #: 0 = identical, 1 = differ, anything else = could not answer.
            if scoped.returncode not in (0, 1):
                pytest.skip(f"git diff --quiet answered {scoped.returncode} — "
                            "code_head equivalence UNVERIFIED")
            assert scoped.returncode == 0, (
                f"code_head {head} and the head the note names ({other}) differ "
                "on the files this measurement exercises — the substitution is "
                "not code-equivalent, so the numbers no longer describe this "
                "tree")


def test_the_pre_12295_control_is_the_zero_it_claims(report):
    """The BEFORE half of the before/after, frozen in the harness.

    If `attached_norm_only` is ever non-zero, either the frozen control drifted
    or a variant is `_norm`-equal to its canonical — in both cases the 0 % claim
    this lane's headline rests on is no longer what was measured.
    """
    v = report["arms"]["variant"]
    for k, row in v.items():
        assert row["attached_norm_only"] == 0, (
            f"k={k}: the pre-#12295 control attached {row['attached_norm_only']} "
            "— the fold's contribution is no longer isolated")
    assert report["arms"]["duplicate_rate"]["prior_on_norm_only"]["attached"] == 0


def test_the_fold_moves_the_variant_class_and_the_rate_follows(report):
    """The AFTER half, and the arithmetic that ties it to the duplicate rate."""
    v = report["arms"]["variant"][str(report["arms"]["duplicate_rate"]["window_k"])]
    dr = report["arms"]["duplicate_rate"]
    rate = dr["prior_on_shipped_folded"]
    assert v["attached"] > 0, "the folded rung bound nothing — the lane did nothing"
    assert rate["attached"] == v["attached"]
    assert dr["pairs"] == v["pairs"], (
        "the duplicate rate was computed on a different pair count than the arm it quotes")
    assert rate["minted"] == dr["pairs"] - rate["attached"]
    assert rate["rate"] == pytest.approx(rate["minted"] / dr["pairs"], abs=1e-3)
    # …and the prior being ON is what makes the before/after comparable at all.
    assert report["arms"]["duplicate_rate"]["prior_off"]["rate"] == 1.0


def test_pair_completeness_is_computed_from_the_counts_it_reports(report):
    """No published number may come from anywhere but the artifact's own counts."""
    b = report["arms"]["blocking"]
    assert b["multikey"]["in_block"] == b["multikey"]["pair_completeness"] * b["pairs"] \
        or b["multikey"]["pair_completeness"] == pytest.approx(
            b["multikey"]["in_block"] / b["pairs"], abs=1e-3)
    for k, pc in b["fts_only"]["pair_completeness"].items():
        assert pc == pytest.approx(b["fts_only"]["in_window"][k] / b["pairs"], abs=1e-3)


def test_the_blocker_is_NOT_earned_on_this_corpus_and_the_artifact_says_so(report):
    """N-3's falsifier F1, pinned as the result it is.

    The shipped window reaches pair completeness 1.0 at k=15 with 15 candidates
    per mention; the multi-key blocker reaches 1.0 at ~35. If that ever flips —
    the window losing completeness the blocker keeps at a comparable cost — the
    brief's "not earned for the capture prior" verdict is wrong and must be
    re-derived. Pinned in BOTH directions so neither sentence can rot:
    completeness is never worse than the window, AND it is not cheaper.
    """
    b = report["arms"]["blocking"]
    mk, fts = b["multikey"], b["fts_only"]
    for k, pc in fts["pair_completeness"].items():
        assert mk["pair_completeness"] >= pc, (
            f"multi-key blocking was worse than the k={k} window")
    assert fts["pair_completeness"]["15"] == 1.0, (
        "the window no longer reaches completeness 1.0 at k=15 — the 'not "
        "earned' verdict rested on that")
    # The COST comparison must be per mention, in one unit; the pooled capture
    # figure is recorded separately precisely so it cannot be used as one.
    assert mk["candidates_mean_per_mention"] > fts["candidates_mean_per_mention"]["15"], (
        "the blocker is now cheaper per mention than the k=15 window — re-derive "
        "the verdict, it may now be earned")
    assert fts["pooled_candidates_per_capture"] > fts["pooled_candidates_per_mention"], (
        "a capture's union is smaller than one mention's — the two quantities were "
        "swapped somewhere")
    assert mk["k_independent"] is True


def test_the_residual_is_the_DECLARED_classes_and_nothing_unexplained(report):
    """Every unattached pair is attributed — the brief's §4 table is this test.

    The unattached remainder must be exactly the DECLARED classes:
    ``space-to-hyphen`` (the article anchor, #12047 round-4 finding 5),
    ``basename-only`` (the precision rule: a basename attaches only on a complete
    population), and ``article-drop`` (the pre-existing retrieval miss #11901 owns,
    out of window at k=3). An unexplained gap means the explanation is incomplete.

    ⛔ Only the first TWO are decisions this lane explains; ``article-drop`` is
    tolerated because #11901 owns it, and it is LOAD-BEARING in the tolerated set —
    `arms.variant_by_class["article-drop"]["3"]` is 180 pairs / 175 attached, so its
    5 unattached pairs must be tolerated or this test reds on work it did not do.
    The docstring previously listed two classes while `declared` held three, which
    told a reader that a newly unattached ``article-drop`` pair would fail here.
    """
    by_class = report["arms"]["variant_by_class"]
    total = sum(d["3"]["pairs"] for d in by_class.values())
    attached = sum(d["3"]["attached"] for d in by_class.values())
    unattached = {
        c: d["3"]["pairs"] - d["3"]["attached"] for c, d in by_class.items()
    }
    assert total == report["arms"]["blocking"]["pairs"]
    assert attached == report["arms"]["variant"]["3"]["attached"]
    # Three declared classes: the two residuals carry the explanation, the third
    # is the pre-existing retrieval miss #11901 owns (out of window at k=3).
    declared = {"space-to-hyphen", "basename-only", "article-drop"}
    unexplained = {c: n for c, n in unattached.items() if n and c not in declared}
    assert not unexplained, (
        f"unattached pairs in undeclared classes {unexplained} — the brief's "
        "residual table no longer explains the remainder")
    # Both declared classes are REACHABLE by the blocker: this is what makes the
    # residual a decision (the anchor, the precision rule) and not a retrieval gap.
    for cls in ("space-to-hyphen", "basename-only"):
        d = by_class[cls]["3"]
        assert d["in_multikey_block"] >= d["in_window"], (
            f"{cls}: the blocker did not reach at least the window's candidates")


def test_the_brief_exists_and_quotes_the_artifact(report):
    """A brief whose numbers are not in the artifact is the #11901 erratum class."""
    assert BRIEF.exists(), f"missing committed brief {BRIEF}"
    text = BRIEF.read_text()
    b = report["arms"]["blocking"]
    attached = report["arms"]["variant"][str(report["arms"]["duplicate_rate"]["window_k"])]["attached"]
    for needle in (str(b["pairs"]), str(attached),
                   f"{b['multikey']['candidates_mean_per_mention']}",
                   f"{b['fts_only']['pooled_candidates_per_capture']}",
                   str(report["arms"]["variant"]["3"]["attached_folded"]),
                   f"{b['multikey']['reduction_ratio']}"):
        assert _quotes(text, needle), f"the brief does not quote {needle!r} from the artifact"
    #: The two figures the brief quotes in the SAME cells as the pinned ones
    #: above, and which no assertion held: `34.9` was pinned but its `(max 152)`
    #: parenthetical was not, and `42.65` per capture was pinned while the `2.84`
    #: per mention beside it was not. Both moved with this file fully green
    #: (measured #12304 review round 24). Pinned in the RENDERED form, because a
    #: bare `152` can be satisfied by an unrelated number elsewhere in the prose.
    assert f"(max {b['multikey']['candidates_max']})" in text, (
        "the brief does not quote the artifact's multikey.candidates_max in its "
        "``(max N)`` parenthetical")
    assert f"| {b['fts_only']['pooled_candidates_per_mention']} (" in text, (
        "the brief does not quote the artifact's "
        "fts_only.pooled_candidates_per_mention in its table cell")


def test_the_per_k_width_claim_matches_the_artifact(report):
    """The brief's article-anchor section turns on ONE class's per-`k` rows.

    ⛔ A substring needle cannot see this: `"180"`, `"179"`, `"207"` and `"386"`
    occur throughout the prose, so a brief quoting a SUPERSEDED row would stay
    green while its central claim went stale — the #11901 erratum class this file
    exists to prevent. So assert the artifact's shape, then assert the brief
    quotes the figures derived from it.
    """
    cls = report["arms"]["variant_by_class"]["space-to-hyphen"]
    ks = ("3", "5", "10", "15")
    pairs = {cls[k]["pairs"] for k in ks}
    assert len(pairs) == 1, (
        f"the class population moved between ``k`` ({sorted(pairs)}) — the rows "
        "are no longer a window sweep of ONE population")
    total = pairs.pop()
    windows = [cls[k]["in_window"] for k in ks]
    assert windows == sorted(windows), (
        f"a wider window cannot lose a member, measured {windows} at k={ks}")
    assert windows[-1] == total, (
        f"the widest window holds {windows[-1]} of {total} — the brief's "
        "``width therefore cannot recover them`` is false once a member is "
        "still out of reach")
    assert [cls[k]["attached"] for k in ks] == [0] * len(ks), (
        "some member of the class attached, so the article anchor is not the "
        "sole cause — the brief's diagnosis is then wrong")
    text = BRIEF.read_text()
    assert f"{windows[0]} of {total}" in text, (
        f"the brief does not quote the narrowest window ({windows[0]} of {total})")
    assert f"all {windows[-1]} are in the window" in text, (
        f"the brief does not quote the widest window (all {windows[-1]})")


def test_the_index_stats_key_set_is_the_one_the_code_emits(report):
    """The artifact's ``index_stats`` must carry the SHAPE ``BlockIndex.stats()``
    emits, not just the values it happened to hold.

    ⛔ This is the rename-drift guard. `stats()["keys"]` was renamed to
    `key_memberships` in this PR, and the committed artifact was hand-renamed with
    it (the harness writes `index.stats()` verbatim, so the artifact is no longer
    reproducible at its own `code_head` for that field). The values were identical,
    so nothing noticed: no test compared the artifact's key SET against the code's.
    A future rename would slip through the same hole, leaving the artifact naming a
    field the shipped module cannot emit.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tortoise.entity_blocking import BlockIndex

    emitted = set(BlockIndex().stats())
    held = set(report["arms"]["blocking"]["index_stats"])
    assert held == emitted, (
        f"the artifact's index_stats keys {sorted(held)} are not the ones "
        f"BlockIndex.stats() emits {sorted(emitted)} — the artifact names a field "
        "the shipped module cannot produce")


def test_the_completeness_claim_cannot_survive_a_skipped_block(report):
    """The brief's "complete for THIS corpus" rests on TWO artifact facts, and
    neither was pinned — a key-SET comparison cannot see either.

    ⛔ Measured #12304 review round 24: mutating
    ``index_stats.oversized_skipped`` from ``{}`` to ``{"E:xxx": 900}``, or
    ``index_stats.max_block_size`` from 93 to 500 (above its own
    ``query_candidate_cap``), each left this file fully green while the brief
    still said **complete for THIS corpus**. That is the false-PASS direction
    the pin exists to prevent: a skipped block DEFLATES pair completeness
    silently, so the artifact's own ``pair_completeness: 1.0`` and the brief's
    completeness sentence go false TOGETHER and nothing reds.
    """
    stats = report["arms"]["blocking"]["index_stats"]
    assert stats["oversized_skipped"] == {}, (
        "a block was skipped, so the capped path dropped candidates and this "
        f"corpus is NOT complete: {stats['oversized_skipped']} — pair "
        "completeness is then deflated over an unknown population, and the "
        "brief's ``complete for THIS corpus`` must be re-cut")
    assert stats["max_block_size"] <= stats["query_candidate_cap"], (
        f"the largest block ({stats['max_block_size']}) exceeds the cap "
        f"({stats['query_candidate_cap']}) — the cap fired, so the published "
        "completeness is deflated")
    #: And the cap the artifact names must be the one the MODULE enforces: the
    #: key-set guard below catches a renamed field, not a retuned constant, so a
    #: tightened `MAX_BLOCK_SIZE` would leave the brief quoting "the 200 cap"
    #: against a cap the shipped code no longer honours.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tortoise.entity_blocking import MAX_BLOCK_SIZE

    assert stats["query_candidate_cap"] == MAX_BLOCK_SIZE, (
        f"the artifact was measured against a cap of "
        f"{stats['query_candidate_cap']} while the shipped module enforces "
        f"{MAX_BLOCK_SIZE} — the brief's \"against the 200 cap\" then describes a "
        "bound the code no longer applies")
    text = BRIEF.read_text()
    assert f"`max_block_size` **{stats['max_block_size']}**" in text, (
        "the brief does not quote the artifact's max_block_size")
    assert f"against the {stats['query_candidate_cap']} cap" in text, (
        "the brief does not quote the cap the artifact was measured against")
    assert "`oversized_skipped` **empty**" in text, (
        "the brief no longer says the skipped set is empty — the claim guarded "
        "above is gone or reworded, so re-read it against that assertion")


def test_the_cost_column_and_the_population_are_bound_to_the_artifact(report):
    """The brief's COST column and its POPULATION are the figures its verdict rests on.

    The verdict sentence is that the blocker is **not earned on COST**: it compares
    the blocker's ``34.9`` against the window's ``15`` and states the ratio as
    ``2.3x``. Only ``34.9`` was pinned, and only ONE-SIDEDLY (``>``), so a different
    window width or population left the brief's own ratio and denominator false
    with the file green — measured #12304 review round 25, each giving
    ``10 passed``: ``candidates_mean_per_mention["15"]`` 15 -> 14 (ratio becomes
    2.49, the brief still says 2.3x), ``["5"]`` 5 -> 9, ``["3"]`` 3 -> 7,
    ``corpus.population`` 1000 -> 2000, and ``blocking.population`` 1000 -> 500
    (the denominator of the pinned ``reduction_ratio``).
    """
    fits = report["arms"]["blocking"]["fts_only"]
    per_mention = fits["candidates_mean_per_mention"]
    widths = sorted(int(k) for k in per_mention)
    #: The INVARIANT, not a copied number: an FTS window at ``k`` sees exactly
    #: ``k`` candidates per mention, which is what makes the two generators
    #: quotable in one column at all.
    assert {w: int(per_mention[str(w)]) for w in widths} == {w: w for w in widths}, (
        "an FTS window's per-mention candidate count is not its own width — the "
        f"brief's cost column is then not comparable to the blocker's: {per_mention}")
    corpus_pop = report["corpus"]["population"]
    blocking_pop = report["arms"]["blocking"]["population"]
    assert corpus_pop == blocking_pop, (
        f"the blocker was measured over {blocking_pop} names while the corpus "
        f"holds {corpus_pop} — reduction_ratio's denominator is then not the "
        "population the brief states")
    text = BRIEF.read_text()
    for w in widths:
        assert f"| {per_mention[str(w)]} |" in text, (
            f"the brief does not tabulate the artifact's k={w} cost cell "
            f"({per_mention[str(w)]} candidates per mention)")
    #: The verdict's ratio, derived from the two pinned figures.
    ratio = (report["arms"]["blocking"]["multikey"]["candidates_mean_per_mention"]
             / per_mention[str(max(widths))])
    #: ⛔ BOUND IN THE MULTIPLIER FORM, IN THE SENTENCE THAT MAKES THE COST CLAIM
    #: (round 28). The first cut asked only whether `round(ratio, 1)` appeared
    #: ANYWHERE (`_quotes(text, ...)`), which the brief's own prose satisfies by
    #: accident: `round(50.0/15, 1)` is `3.3`, and the brief is full of section
    #: numbers like `### 3.3` and `see 3.3`. Measured: mutating
    #: `multikey.candidates_mean_per_mention` `34.9` -> `50.0` in the artifact AND
    #: the brief's two `34.9` occurrences kept the suite **`14 passed`** while the
    #: artifact derived `3.3x` and the verdict sentence still said `2.3x the cost`
    #: — the pin was satisfied by a heading. A bare number cannot carry this claim,
    #: so the needle is the `×` form and it must sit in the cost sentence.
    ratio_txt = f"{round(ratio, 1)}\u00d7"
    cost_sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if "the cost" in s]
    assert cost_sentences, (
        "the ratio is quoted somewhere, but not attached to a COST claim — the "
        "verdict sentence is about cost and that sentence has gone")
    assert any(ratio_txt in s for s in cost_sentences), (
        f"the cost sentence does not quote the artifact's own ratio as {ratio_txt!r} "
        f"(sentences carrying 'the cost': {cost_sentences[:2]}) — a BARE number is "
        "satisfied by the brief's own section numbering, which is how a wrong ratio "
        "stayed green")
    for rendered in (f"--population {corpus_pop}", f"all {corpus_pop}",
                     f"C({corpus_pop}, 2)"):
        assert rendered in text, (
            f"the brief does not state the population as {rendered!r}")


def test_the_provenance_note_states_no_count_it_cannot_sustain(report):
    """Neither document may state a commit count for the rewritten range.

    The note's own disclaimer says no count is stated, "because the commit that
    writes this sentence adds itself to any number it would quote" — and both
    documents nevertheless said "25-commit list" while the very command the note
    documents printed 26, in the commit that wrote the sentence (measured #12304
    review round 25). The range is a base-moved range, so a number in that
    position moves with every commit that lands: the ABSENCE is what is pinned.
    """
    pattern = re.compile(r"\b\d+-commit\b|\b\d+\s+commits?\b")
    for name, blob in (("code_head_note", report["code_head_note"]),
                       ("research-brief.md", BRIEF.read_text())):
        hit = pattern.search(blob)
        assert hit is None, (
            f"{name} states a commit count ({hit.group(0)!r}) that the note's own "
            "disclaimer forbids — the range is base-moved, so the number is "
            "stale as soon as the next commit lands")


def test_every_figure_the_brief_quotes_is_bound_to_the_artifact(report):
    """Table-driven: every MEASUREMENT figure the brief attributes to the artifact
    must be bound, so an artifact mutation cannot leave the prose standing.

    ⛔ This test exists because rounds 24, 25 and 26 each found the SAME defect one
    figure at a time: the brief quotes a number, and no assertion reads it, so the
    artifact can be mutated while the brief's claim stands. Round 26's audit
    enumerated the survivors — the FTS completeness column and its numerators, the
    arm tables' cells outside the hand-picked needles, the run-identity block, and
    the two sentences that name a pair count at a window (``202 at k=3 instead of
    409``). Measured green before this test, each with the file fully passing:
    ``in_window["3"]`` 409->421 TOGETHER WITH ``pair_completeness["3"]``
    0.9692->0.9976 (the falsifier's headline answer), ``variant["15"].attached``
    212->213, ``in_window_not_attached`` 202->203, ``prior_on_norm_only.minted``
    422->400 and ``.rate`` 1.0->0.9, per-class ``in_window`` 175->170 / 21->20 /
    11->10 / 23->24, ``in_multikey_block`` 180->181 / 30->29, per-class
    ``attached_folded`` 11->12 / 179->180 (breaking the 386 decomposition),
    ``exact_by_class.definite`` 179->178, ``corpus.sample`` 300->400,
    ``corpus.files`` 18->19, ``window_modes.hybrid`` 4388->4000, and
    ``legs_seen.structural`` True->False.
    """
    text = BRIEF.read_text()
    arms = report["arms"]
    blocking = arms["blocking"]
    pool = blocking["fts_only"]
    pairs = blocking["pairs"]
    ks = sorted(int(k) for k in pool["pair_completeness"])

    #: §3.1's completeness COLUMN and its NUMERATORS. Pinning only their mutual
    #: arithmetic (`pc == in_window/pairs`) leaves both free to move together,
    #: which is exactly how the falsifier's 0.9692 answer could be rewritten.
    for k in ks:
        if pool["pair_completeness"][str(k)] != 1.0:
            assert _quotes(text, pool["pair_completeness"][str(k)]), (
                f"the brief does not quote the artifact's k={k} pair completeness "
                f"({pool['pair_completeness'][str(k)]})")
    #: The brief quotes a NUMERATOR only for the production window, so pin that
    #: one by name and the rest of the column by the shape it must have.
    prod_k = str(arms["duplicate_rate"]["window_k"])
    assert f"({pool['in_window'][prod_k]}/{pairs})" in text, (
        f"the brief does not quote the artifact's k={prod_k} numerator "
        f"({pool['in_window'][prod_k]}/{pairs})")
    counts = [pool["in_window"][str(k)] for k in ks]
    assert counts == sorted(counts), (
        f"a wider FTS window cannot contain fewer pairs, measured {counts} at "
        f"k={ks} — the brief's per-k column is no longer a window sweep")
    assert counts[-1] == pairs, (
        f"the widest window holds {counts[-1]} of {pairs}, so the table's 1.0000 "
        "row is not the whole population")

    #: The run-identity block, quoted EXACTLY — a backticked token cannot be
    #: satisfied by a truncated one, which the old bare-substring needles allowed.
    corpus = report["corpus"]
    for label, needle in (
        ("corpus.graph", f"`{corpus['graph']}`"),
        ("corpus.population_digest", f"`{corpus['population_digest']}`"),
        ("corpus.elapsed_s", f"{corpus['elapsed_s']} s"),
        ("corpus.files", f"{corpus['files']} harvested session files"),
        ("corpus.sample", f"sample {corpus['sample']}"),
        ("corpus.sample_by_class", "definite {definite} / ident {ident} / "
                                  "path {path}".format(**corpus["sample_by_class"])),
        ("retrieval.window_modes.hybrid",
         f"{report['retrieval']['window_modes']['hybrid']} windows"),
    ):
        assert needle in text, (
            f"the brief does not quote {label} as the artifact holds it "
            f"({needle!r})")
    assert corpus["sample"] == sum(corpus["sample_by_class"].values()), (
        "the stratified sample's class split does not add up to the sample size "
        f"({corpus['sample_by_class']} vs {corpus['sample']})")
    assert report["retrieval"]["legs_seen"]["structural"] is True, (
        "the brief contrasts this run as `legs_seen.structural` `true` here")
    assert "`legs_seen.structural` is `false` there, `true` here" in text, (
        "the brief no longer states the structural-leg contrast this run rests on")

    #: The duplicate-rate table: the two control arms the ablation's verdict
    #: depends on (only the shipped arm had assertions).
    for arm in ("prior_off", "prior_on_norm_only"):
        assert arms["duplicate_rate"][arm]["minted"] == pairs, (
            f"the {arm} arm did not mint every pair — the brief's `422` is false")
        assert arms["duplicate_rate"][arm]["rate"] == 1.0, (
            f"the {arm} arm's rate is not 1.0 — the brief's `1.0000` is false")

    #: §3.3's per-class table: pin each row's first three cells (the claim cells
    #: the prose reads), then the COLUMN totals, so no single cell can move without
    #: breaking a total or a row.
    by_class = arms["variant_by_class"]
    for cls, rows in by_class.items():
        row = rows["3"]
        assert f"| {cls} | {row['pairs']} | {row['in_window']} |" in text, (
            f"the brief does not tabulate the artifact's {cls} row at k=3")
    assert sum(r["3"]["pairs"] for r in by_class.values()) == pairs
    assert sum(r["3"]["in_window"] for r in by_class.values()) == pool["in_window"]["3"], (
        "the per-class in-window column does not sum to the FTS window's k=3 count")
    assert (sum(r["3"]["in_multikey_block"] for r in by_class.values())
            == pairs), (
        "the per-class blocker column does not sum to the whole pair population, "
        "so the brief's claim that the blocker reaches all 422 is false")
    assert (sum(r["3"]["attached"] for r in by_class.values())
            == arms["duplicate_rate"]["prior_on_shipped_folded"]["attached"]), (
        "the per-class attached column does not sum to the shipped arm's attached")
    assert (sum(r["3"]["attached_folded"] for r in by_class.values())
            == arms["variant"]["3"]["attached_folded"]), (
        "the per-class attached_folded column does not sum to the variant arm's "
        "attached_folded, so the brief's 386 decomposition is broken")

    #: The two sentences that name a pair count AT A WINDOW.
    variant = arms["variant"]
    assert f"{variant['15']['attached']} at `k=15`" in text, (
        "the brief does not quote the artifact's k=15 attached count")
    assert (f"{variant['3']['in_window_not_attached']} at `k=3` instead of "
            f"{variant['3']['in_window']}") in text, (
        "the brief does not quote the artifact's k=3 in-window-but-unattached "
        "count against its in-window count")

    #: The `exact` arm's class figure the brief contrasts with #11901.
    exact = arms["exact_by_class"]["definite"]["3"]
    assert f"definite class {exact['in_window']}/{exact['n']} here" in text, (
        "the brief does not quote the artifact's exact-arm definite class figure")


def _row(*cells: object) -> str:
    """The markdown table row the brief renders for ``cells``."""
    return "| " + " | ".join(str(c) for c in cells) + " |"


def test_the_briefs_tables_are_exactly_the_artifacts(report):
    """Every claim-bearing TABLE ROW must equal the row the artifact generates.

    ⛔ A COLUMN TOTAL is not a pin: one cell can be moved by moving a second so the
    total holds. Measured #12304 review round 27 — each gave ``13 passed`` while
    the brief's claim went false: ``article-drop.attached_folded`` 175 -> 174
    TOGETHER WITH ``space-to-hyphen`` 179 -> 180 (sum still 386, breaking the
    brief's ``386 = 175 + 179 + 21 + 11`` decomposition); and
    ``space-to-hyphen.in_multikey_block`` 180 -> 179 with ``article-drop``
    180 -> 181 (sum still 422, breaking ``180/180 reachable by the blocker`` and
    silently making the class only as reachable as the window, which the residual
    test's ``>=`` arm cannot see).

    ⛔ And a needle cannot tell "the brief quotes this as its figure" from "this
    numeral occurs in unrelated prose": ``pair_completeness["5"]`` 0.9953 -> 0.99
    was satisfied by the falsifier's OWN ``>= 0.99`` elsewhere, and a value mutated
    TO 1.0 skipped the completeness check entirely. An exact row closes both at
    once — a moved cell changes the row, and no unrelated numeral supplies one.
    """
    text = BRIEF.read_text()
    arms = report["arms"]
    blocking = arms["blocking"]
    pool = blocking["fts_only"]
    pairs = blocking["pairs"]
    prod_k = int(arms["duplicate_rate"]["window_k"])

    #: §3.1 — cost and completeness per window. Bold marks the production row and
    #: any completeness of 1.0; only the production row carries its numerator.
    for k in sorted(int(x) for x in pool["pair_completeness"]):
        ks = str(k)
        pc = pool["pair_completeness"][ks]
        production = k == prod_k
        label = (f"**FTS window, k={k}** (production)" if production
                 else f"FTS window, k={k}")
        cell = f"**{pc:.4f}**" if (production or pc == 1.0) else f"{pc:.4f}"
        if production:
            cell = f"{cell} ({pool['in_window'][ks]}/{pairs})"
        expected = _row(label, k, cell)
        assert expected in text, (
            "the brief's k={} table row is not the one the artifact generates "
            "({!r})".format(k, expected))

    #: §3.3 — the per-class table at the production window. The blocker cell is bold
    #: exactly where it exceeds the window, which is the shape the brief uses; the
    #: attached cell is always bold.
    for cls, rows in arms["variant_by_class"].items():
        row = rows[str(prod_k)]
        reach = row["in_multikey_block"]
        reach_cell = f"**{reach}**" if reach > row["in_window"] else f"{reach}"
        expected = _row(cls, row["pairs"], row["in_window"], reach_cell,
                        f"**{row['attached']}**")
        assert expected in text, (
            "the brief's {!r} class row is not the one the artifact generates "
            "({!r})".format(cls, expected))

    classes = arms["variant_by_class"]
    folded = arms["variant"][str(prod_k)]["attached_folded"]
    addends = [classes[c][str(prod_k)]["attached_folded"]
               for c in ("article-drop", "space-to-hyphen", "hyphen-to-space",
                         "hash-dropped")]
    #: The prose WRAPS mid-claim, so the sentence-level assertions read a
    #: whitespace-normalised copy; the table rows above must stay exact because a
    #: row is one line by construction.
    flat = " ".join(text.split())
    #: Each addend bound to its OWN class name, so the 386 total cannot be
    #: preserved by trading between two classes.
    for cls, value in zip(("article-drop", "space-to-hyphen", "hyphen-to-space",
                           "hash-dropped"), addends):
        assert f"{value} (`{cls}`" in flat, (
            f"the brief does not bind {value} to `{cls}` in its 386 decomposition")
    assert (f"{folded} = {addends[0]} + {addends[1]} + {addends[2]} + "
            f"{addends[3]}") in flat, (
        f"the brief does not state the decomposition as the artifact's addends "
        f"({folded} = {' + '.join(map(str, addends))})")

    #: The class the residual argument rests on: its reach, and the per-k claim the
    #: brief asserts by NAME ("= 180 at each of those k").
    spc = classes["space-to-hyphen"]
    row3 = spc[str(prod_k)]
    assert (f"{row3['pairs']}/{row3['in_multikey_block']} reachable by the "
            "blocker") in flat, (
        "the brief does not quote this class's reach as the artifact holds it")
    wide = {spc[str(k)]["in_window"] for k in (5, 10, 15)}
    assert wide == {row3["pairs"]}, (
        f"the artifact's k=5/10/15 in-window counts are {sorted(wide)}, not the "
        f"single value ({row3['pairs']}) the brief states at each of those k")
    assert f"all {row3['pairs']} are in the window" in flat, (
        "the brief no longer states the wide-window claim this class's diagnosis "
        "rests on")
