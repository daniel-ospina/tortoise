#!/usr/bin/env python
"""M2 — retrieval quality under a WINDOWED vs COMPLETE graph (#12186, §9).

WHAT THIS ANSWERS, AND WHY IT IS A GATE AND NOT A NICE-TO-HAVE
--------------------------------------------------------------
``docs/architecture/SEARCH-ARCHITECTURE.md`` §9 M2 asks for *"retrieval quality
under a windowed vs complete graph, on a fixed query set"*, and §7 says the
window is *"armed only after M2 (§1.5, §7)"*. §7 also states the honesty item
plainly: **the bounded-cache literature measures latency and hit rate, not
answer quality, and whether a windowed graph harms retrieval quality is
unmeasured anywhere.** So there is no published number to import; this
instrument is the only place the question can be answered for us.

⛔ THE LEG THAT DECIDES IT IS THE TEXT LEG, AND WHICH NODES MATTER IS NOT OBVIOUS
--------------------------------------------------------------------------------
§3 splits the read surface, and the split is what makes M2 a narrow question
rather than a vague one:

  * the **vector** leg **MOVES** to Postgres (§3, #11642) — once it lands, the
    record holds the whole population and a bounded GRAPH window cannot shrink
    its candidate set;
  * the **text** leg **STAYS** in FalkorDB (§6.2, decision item 2) — it is
    ``db.idx.fulltext.queryNodes`` over five labels
    (``projection._FULLTEXT_INDEX_LABEL_FIELDS``: Point/Event/Subject/Object/
    Source). A fulltext index is maintained BY THE GRAPH over the nodes the
    graph HOLDS, so demoting a node out of the window removes it from the index.

So the text leg is bounded by the window **by construction**, and seed
materialisation does not repair it: §7's repair (*"a walk materialises its seeds
first — search returns ids from Postgres"*) is sound only for a candidate
generator that reads the RECORD. ``db.idx.fulltext`` cannot return an id for a
node it does not index, so there is nothing to materialise.

**Two populations are measured, and the reason is a scope correction that a
reader can otherwise get wrong.** §8 derives the window target as **≈77 MB
against an ≈150 MB complete graph** — i.e. the window keeps roughly HALF the
bytes — and what those bytes are is the claim/structure population, not the
entity dictionary. So:

 ``point``   ``:Point`` nodes built from REAL captured turn text. This is the
             population the window demotes (§8's byte target) and the one the
             product searches most. **This arm decides §7.**
 ``object``  ``:Object`` entity names, harvested by
             ``candidate_recall_11901.harvest`` — IMPORTED, not copied, so there
             is one owner of the harvest rules. This is the capture-prior
             population #11901 measured, kept so M2's curve is comparable with
             the recall figures the epic already carries and so a reader can see
             the loss is not an artefact of one label.

THE ARMS
--------
 ``complete``     every population node resident. The baseline.
 ``windowed@F``   only ``F`` of the population resident; the rest demoted
                  (removed from the graph, which is what a residency window IS —
                  §7: *residency, not retention*; the record still holds them).

 Each coverage is measured under TWO eviction orders, because "which nodes a
 window keeps" is a policy and a random eviction is not a policy anyone would
 ship:

 ``tail``     evict in ASCENDING popularity — the cold tail goes first. ⛔ This is a
              STATIC popularity count, NOT the recency/frequency policy §7
              describes: this corpus carries no read ledger, so a true recency
              policy cannot be built from it, and M2 therefore measures the
              static axis only — it does NOT test §7's "recency beats static
              degree ranking". This is the realistic arm.
 ``random``   a seeded uniform shuffle. The neutral control: it separates "the
              window costs this much" from "this POLICY costs this much".

METRICS, AND WHAT EACH ONE WOULD MAKE US DO
-------------------------------------------
 ``recall@k``         the canonical node is in the top-``k`` window. The gate
                      number §9 asks for.
 ``misbind@1``        the top-1 row is a DIFFERENT node. ⛔ This is not merely
                      degraded recall — it is the epic's own headline failure: a
                      plausible wrong entity handed to link-before-create is how
                      a duplicate gets MINTED (epic #12186 "Capstone":
                      *duplicate-noun rate*). A miss costs a new node; a misbind
                      costs a WRONG merge.
 ``wrong_not_miss``   among queries whose canonical was demoted, how often a
                      different node occupied top-1 rather than the window coming
                      back empty. The duplicate-minting rate per demoted query.
 ``silent_zero``      among demoted queries, how often the read returned NO rows
                      at all. Read beside ``signal`` this is the §5 Q2 violation
                      (§5 Q2: *"Nothing is silently invisible"*), counted.
 ``signal``           whether the reading caller can learn the store was partial.
                      Resolved from the CODE, not asserted, so a later change that
                      wires a signal moves this field instead of leaving the doc
                      claiming silence forever.

THREE INVARIANTS, ASSERTED IN `run()` — because they falsify the INSTRUMENT
--------------------------------------------------------------------------
(and one further property held as a CONSTANT, which is number 4 below)
 1. A demoted node cannot be returned, so
    ``recall@k(windowed) <= resident_share`` for every k and every arm.
 2. The graph must hold EXACTLY the intended resident set after eviction.
    Checks 1 and 2 catch different failures: over-eviction lowers recall AND the
    share, so it satisfies (1) and violates (2); a delete that did not take
    effect violates (1).
 3. No demoted node is returned at ANY rank — ``found_deep_of_demoted == 0``.
    This is the STRONG form of (1) and the one the record's verdict rests on.
    It is not redundant: ``found_deep`` is a rate over ALL queries, so on a
    population whose complete-graph ceiling is below 1.0 (``:Point`` on this
    corpus: 0.410 against a resident share of 0.5267) a leak of demoted queries
    sits UNDER (1)'s bound and passes it. (3) has no ceiling to hide behind.
 4. ⛔ CONSTANT, NOT ASSERTED IN `run()`. The query-sample seed and the ``random`` eviction seed must not collide
    (``EVICT_SEED_OFFSET``). They are permutations of the SAME list, so one shared
    seed makes the retained suffix and the sampled prefix DISJOINT by
    construction — measured on this instrument's first run, which reported
    ``resident_share`` 0.667 / 0.167 / 0.000 at coverage 0.90 / 0.75 / 0.50: true
    about the graph, useless as a control.

    Nothing in ``run()`` compares the two seeds — it applies ``EVICT_SEED_OFFSET``
    as an offset and records it in the artifact, which is not an assertion that they
    cannot collide. This one is a CONSTANT, and the record says so ("a constant in
    the instrument, and it is not counted among the invariants"). An earlier header
    claimed all four were ASSERTED, which nothing enforced;
    ``tests/test_window_quality_m2.py`` pins the distinction.

 An instrument that cannot fail its own arithmetic is not evidence. These are the
 checks that caught the #8919 ``_upsert_object`` ON-CREATE gap in the sibling
 instrument, generalised.

WHAT IS REAL AND WHAT IS NOT (read before quoting a number)
-----------------------------------------------------------
 REAL: the population and the queries. Points are built from real captured turn
 text on this box (``~/.tortoise/session-events/*.jsonl``); Object names come
 from the same corpus by the sibling's declared harvest rules; every node is
 written through the real projection (``_upsert_point_props`` /
 ``_upsert_object``) and every query goes through the real
 ``sdk.tortoise_fts_query`` leg with the flags ``_fts_rows`` passes.

 NOT REAL, declared. (a) The population is a SAMPLE of the strings those
 captures contain, not the rows a production graph holds — the same gap the
 sibling records; a lane cannot census production. (b) **The dense leg is not
 exercised when the venv lacks the ``embeddings`` extra** — the artifact states
 ``retrieval.mode``, and a ``keyword_only`` run is a figure about the LEXICAL leg
 ONLY, never about the fused production read
 (``battery/runner/retrieval_preflight.py`` names that substitution as the class
 it exists to prevent). For M2 that is not a defect — the lexical leg is exactly
 the leg §6.2 keeps in the graph and therefore the leg the window bounds — but it
 IS a defect for any claim about the fused read, and none is made. (c) The query
 for a Point is a contiguous span of that Point's own content (self-retrieval),
 because the corpus carries no labelled query set; that measures *"can the claim
 containing this phrase be found"*, which is the retrieval question, and it is
 NOT a measure of answer quality.

USAGE
    # 1. a lane container, and export its four seams
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    # 2. measure (NOT via `test_lane.py run`, which executes pytest)
    #    ⛔ `--out` points somewhere OTHER than the committed evidence record:
    #    re-running over it would overwrite the artifact you are comparing
    #    against. Reproduce into a temp path and diff the two.
    uv run python tools/window_quality_m2.py \
        --points 900 --objects 1000 --sample 300 \
        --out /tmp/m2-windowed.json

Requires a live FalkorDB in ``TORTOISE_DB_URI`` and REFUSES without one rather
than falling back to the embedded engine (S3 refuses embedded backends for the
same reason: a figure measured on a backend the product cannot use is not a
figure).

EXIT CODES: 0 ok · 2 usage · 3 no reachable graph, or a refused run (never a
silent skip).
"""
from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE,
# before the rest of the imports, so a stale local `python3` fails LOUDLY with a
# remedy instead of with whichever 3.12-only construct happens to trip first.
# `tests/test_entry_point_python_guard.py` asserts structurally that this guard
# precedes every other import and behaviourally that it names the floor, the
# interpreter it got, and the invocation that works.
if sys.version_info < (3, 12):  # noqa: UP036 -- intentional RUNTIME guard
    print(
        f"tools/window_quality_m2.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/window_quality_m2.py`",
        file=sys.stderr,
    )
    raise SystemExit(70)

import argparse
import ast
import collections
import json
import os
import random
import re
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The windows #11901 measured, reused verbatim so M2's curve is comparable with
#: the recall figures the epic already carries (61.3 % at k=3, 73 % at 15). A
#: different `k` set would make the two artifacts look commensurable and not be.
KS: tuple[int, ...] = (3, 5, 10, 15)

#: The DEPTH of the single read each query makes. ⛔ One read at this width serves
#: BOTH axes, because ``limit`` truncates and never reorders: ``recall@k`` is a
#: slice of it, and ``found@deep`` is the whole of it. The deep axis is what makes
#: the Point arm readable at all — the lexical leg ranks a document holding EVERY
#: query term below documents holding only common subsets (no coordination factor
#: and no length normalisation, §6.2), so a 5-term span over a real capture corpus
#: scores ``recall@3`` ≈ 19 % on a COMPLETE graph. With only ``recall@k`` the
#: window's damage would be indistinguishable from the leg's own ranking weakness;
#: ``found@deep`` asks the question the window actually changes — *is the node in
#: the store at all*.
DEEP_LIMIT = 50

#: The coverage sweep. 1.0 is the baseline arm; the rest bracket the range a
#: window could plausibly be sized to — and 0.5 is the row §8's own target lands
#: on (≈77 MB against an ≈150 MB complete graph), so the curve answers the
#: arming question at the size the document actually proposes.
COVERAGES: tuple[float, ...] = (1.0, 0.90, 0.75, 0.50, 0.25, 0.10)

#: The two eviction orders. `tail` is the realistic policy — a STATIC popularity
#: count, NOT recency (§7 describes a recency policy this corpus cannot supply:
#: there is no read ledger, so that claim is untested here). `random` is the
#: neutral control.
POLICIES: tuple[str, ...] = ("tail", "random")

#: The two measured populations. `point` first: it is the population the window
#: demotes (§8's byte target) and the one §7's arming question is about.
POPULATIONS: tuple[str, ...] = ("point", "object")

#: Node-id prefixes. Eviction and every post-eviction verification scope on these
#: so a run cannot measure a neighbouring population on a reused graph.
ID_PREFIX = {"point": "pt-12186", "object": "obj-11901"}

#: ⛔ TWO INDEPENDENT STREAMS, and the offset is not cosmetic. The query sample
#: and the `random` eviction order are both permutations of the SAME population
#: list, so drawing both from ``seed`` makes them the SAME permutation — and then
#: the retained suffix and the sampled prefix are DISJOINT BY CONSTRUCTION.
#: Measured on this instrument's first run: with one shared seed the control arm
#: reported ``resident_share`` 0.667 / 0.167 / 0.000 at coverage 0.90 / 0.75 /
#: 0.50 — exactly ``(coverage*n - sample)/sample``, the arithmetic of two disjoint
#: slices — instead of the ~coverage an independent subset gives. Those numbers
#: were true about the graph and useless as a control. Applied to the eviction
#: stream only, so the query set stays fixed across both policies.
EVICT_SEED_OFFSET = 1_000_003

#: ⛔ THE REPRODUCIBILITY BOUND — the instrument's own output, measured across
#: THREE runs of identical input, and the single source `run()` copies into the
#: artifact. It is module-level so the record's pins can import it: see
#: `tests/test_window_quality_m2.py::test_the_artifact_binds_its_own_provenance`.
#:
#: What it says, and how to read it. The GATE metrics are stable — `found@deep`
#: moved by at most 0.33 pp and `recall@k` by at most 1.0 pp — so a single-run
#: value of either may be quoted as a magnitude. The TIE-COUNTING rates are not:
#: they moved by up to 9.85 pp, because near-ties in the lexical leg break on
#: engine-internal document ids and a re-run re-mints the graph. So a
#: tie-counting conclusion is quoted as a DIRECTION and an ORDERING only, never
#: as a single-run magnitude.
#:
#: ⚠️ And the honest ratio: the gaps the VERDICT rests on run 13.3 to 48.3
#: points (the misbind move vs the `complete` graph, up to `found@deep` at §8's
#: target size), so the tie band is up to ~3/4 of the SMALLEST of them — not a
#: small fraction of it. Two secondary sentences in the record rest on smaller
#: gaps (~3, 7.3) and are labelled as approximations or attributions there. The 27.7 % -> 41.0 % misbind move is a tie-counting rate, so
#: its 13.3-point gap carries a ~9.9-point band; the verdict survives because
#: `found_deep_of_demoted == 0` and `recall <= resident_share` are NOT
#: tie-counting quantities and are stable in every arm.
REPRODUCIBILITY_BOUND: dict[str, object] = {
    "runs_compared": 3,
    "found_deep_max_delta_pp": 0.33,
    "recall_at_k_max_delta_pp": 1.0,
    "tie_counting_max_delta_pp": 9.85,
    "tie_counting_metric": "silent_zero (object tail@0.9)",
    "conclusions_rest_on_gap_pp": "13.3 to 48.3",
    "invariants_held_in_every_run": [
        "found_deep == resident_share in every :Object arm",
        "found_deep_of_demoted == 0.0 in every arm of both populations",
    ],
    "why": "Near-ties in the lexical leg break on engine-internal document "
           "ids. Each arm is measured on its own fresh graph so an arm is a "
           "describable graph state, but a re-run still re-mints that graph "
           "and the tie order moves. The gate metrics are stable (found@deep "
           "<=0.33 pp, recall@k <=1.0 pp); the tie-counting rates are not "
           "(<=9.85 pp), so only their DIRECTION and ORDERING are quoted in "
           "the record, never a single-run magnitude.",
}

#: A Point's indexed text, capped. The Point index covers content/search_keys/
#: quote; a captured turn can run to tens of KB and the population is meant to
#: model many claims, not a few huge ones.
POINT_TEXT_LIMIT = 1200

#: Token length below which a word is not a query TERM. A span built from one-
#: or two-character tokens (``x x x x x``, ``a b c``, table pipes, list bullets)
#: is a query no caller issues, and it would make the Point arm measure a
#: degenerate case while looking like a real one.
MIN_TERM_LEN = 3

#: Tokens a query span may not be built from: markdown/structural noise that is
#: not a word, so a "phrase" made of them is not a query anyone would issue.
_NOT_A_WORD = re.compile(r"^[^A-Za-z0-9]+$")


# ── pure arithmetic (unit-tested without a graph) ────────────────────────────

def resident_ids(ordered_ids: list[str], coverage: float) -> list[str]:
    """The ids a window of ``coverage`` keeps, from an EVICTION-ordered list.

    ``ordered_ids`` is ordered COLDEST FIRST, so the retained suffix is the
    hottest ``coverage`` share. Deriving retention from the eviction order (one
    ordering, one call site) rather than sorting twice is deliberate: two
    orderings can disagree, and the disagreement would silently invert which half
    of the population a window keeps.
    """
    if not 0.0 <= coverage <= 1.0:
        raise ValueError(f"coverage must be in [0, 1], got {coverage!r}")
    n = len(ordered_ids)
    keep = round(n * coverage)
    # A coverage that rounds to 0 would measure "the graph is empty", which is a
    # different experiment; and `complete` must keep everything exactly.
    if coverage > 0.0 and keep == 0:
        keep = 1
    return ordered_ids[n - keep:]


def eviction_order(rows: list[dict], policy: str, seed: int) -> list[str]:
    """Ids ordered COLDEST FIRST, under ``policy``.

    ``tail``   lowest popularity first, ties broken by id so the order is TOTAL —
               a ``Counter`` iteration order is insertion order, which is stable
               within a run but not a property to depend on across runs.
               Popularity is the population's own real signal: for an Object the
               number of session RECORDS its name appeared in; for a Point the
               number of query-relevant tokens in its content (a within-corpus
               popularity proxy, declared here because the corpus carries no read
               ledger a lane may use — §17.4.6 forbids reading the tenant-wide
               ``read_ledger`` for any product decision).
    ``random`` a seeded uniform shuffle — the control.
    """
    if policy == "tail":
        return [r["id"] for r in sorted(rows, key=lambda r: (r["popularity"], r["id"]))]
    if policy == "random":
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        return [r["id"] for r in shuffled]
    raise ValueError(f"unknown policy {policy!r}")


def classify_rows(rows: list[str], canonical: str, ks: tuple[int, ...] = KS,
                  deep: int = DEEP_LIMIT) -> dict:
    """One query's outcome, as the claimed classes — stated ONCE for every rate.

    ``hit`` is per ``k`` (the recall axis); ``found_deep`` is the deep axis (the
    node is in the store at all); the other three are top-1 shapes (the
    duplicate-minting axis). Every rate in the artifact is a count over THIS
    dict, so ``misbind`` and ``silent_zero`` cannot drift from ``recall``.
    ``empty`` is tested on the row list rather than inferred from a falsy id,
    because a node id is caller-supplied and ``""`` is representable.
    """
    hits = [canonical in rows[:k] for k in ks]
    top1 = rows[0] if rows else None
    return {
        "hit": {k: h for k, h in zip(ks, hits, strict=True)},
        "found_deep": canonical in rows[:deep],
        "top1_is_canonical": top1 == canonical,
        "top1_other": top1 is not None and top1 != canonical,
        "empty": top1 is None,
    }


def summarise(per_query: list[dict]) -> dict:
    """Aggregate one arm.

    Rates are over the QUERY SET, except ``wrong_not_miss`` and ``silent_zero``,
    which are over the DEMOTED subset — and their names say so, because the rate
    the window introduces is a rate per demoted query, not per query.
    """
    n = len(per_query)
    if n == 0:
        raise ValueError("cannot summarise an empty query set")

    def _rate(num: int, den: int) -> float:
        return (num / den) if den else 0.0

    demoted = [q for q in per_query if not q["resident"]]
    out: dict = {
        "queries": n,
        "resident_queries": n - len(demoted),
        "resident_share": _rate(n - len(demoted), n),
        "demoted_queries": len(demoted),
        "empty": _rate(sum(1 for q in per_query if q["empty"]), n),
        "misbind_at_1": _rate(sum(1 for q in per_query if q["top1_other"]), n),
        "wrong_not_miss": _rate(sum(1 for q in demoted if q["top1_other"]), len(demoted)),
        "silent_zero": _rate(sum(1 for q in demoted if q["empty"]), len(demoted)),
        #: THE DEEP AXIS. `found@deep` is the node-is-in-the-store question, which
        #: is the one a window changes; `recall@k` additionally carries the leg's
        #: own ranking weakness, so the two are read together, never one alone.
        "found_deep": _rate(sum(1 for q in per_query if q["found_deep"]), n),
        "found_deep_of_demoted": _rate(
            sum(1 for q in demoted if q["found_deep"]), len(demoted)),
    }
    for k in KS:
        out[f"recall@{k}"] = _rate(sum(1 for q in per_query if q["hit"][k]), n)
    return out


def _valid_window(toks: list[str], start: int, n: int) -> bool:
    """Is the ``n``-token run at ``start`` a phrase a caller could have issued?"""
    if start < 0 or start + n > len(toks):
        return False
    return all(len(t) >= MIN_TERM_LEN and not _NOT_A_WORD.match(t)
               for t in toks[start:start + n])


def query_span(text: str, postings: dict[str, set[int]] | None = None,
               n_tokens: int = 5) -> str | None:
    """A contiguous, word-bearing span of ``text`` — the fixed query for a Point.

    Deterministic. Candidate windows are every run of ``n_tokens`` consecutive
    tokens that are each at least :data:`MIN_TERM_LEN` characters and contain a
    letter or digit. The length floor is load-bearing — real captures are full of
    ``x x x x``, ``- - -`` and list bullets, and a span built from those is not a
    query, so a Point whose text yields none is dropped rather than measured on
    noise.

    ⛔ WHEN ``postings`` IS SUPPLIED THE **MOST SELECTIVE** WINDOW IS CHOSEN. Be
    precise about what "selective" means here, because it is a PROXY and not the
    leg's match model: a window scores on the **size of the term INTERSECTION**
    (documents holding every term), while ``_fts_rows`` issues
    ``sparse.build_or_query`` — an **OR union**, ``'|'``-joined, ranked by
    RediSearch score. The intersection is used as a cheap, monotone stand-in for
    "how distinctive is this window of the text", which is the property being
    chosen for. It is NOT how the leg will match, and it must not be read as one
    (see Finding 7 in the evidence record, and ``tortoise/sparse.py``).

    What this rule is FOR, and what it costs: three weaker rules were measured,
    and the first one rejected is worth stating exactly because the adopted rule
    does **not** beat it on the ceiling —

      * the FIRST window — the captures are dense with shared boilerplate (fleet
        briefs, AGENTS.md, the PR template), so a window taken from the head of
        the text is usually boilerplate rather than the claim;
      * lowest summed document frequency, and deduping on the span — still 825
        near-identical Points shared one phrase, because a span's own terms are
        each common somewhere else;
      * the most-selective window — **adopted**, and it scored ``recall@3``
        **15.7 %** against a COMPLETE graph where the first-window rule scored
        **19.0 %**. So this rule was NOT adopted to raise the ceiling; it was
        adopted so the window is the point's own distinctive text rather than its
        boilerplate. The ~3-point ceiling difference and the absolute ceiling are
        both properties of THIS QUERY SET as much as of the leg, and Finding 7
        discloses them as such — a measurement must not charge the leg for a
        query set the instrument chose.

    Ties break on the earliest window so two runs are byte-identical, and the
    span is a contiguous phrase rather than a bag of keywords because that is the
    shape a caller (or an extractor) actually issues.
    """
    # NOTE: selectivity is scored on the RAW tokens (see `_hits` below) — scoring
    # the SANITISED form here made every window tie, because the sanitiser strips
    # exactly the punctuation that distinguished them. Whether the sanitised form
    # is still a usable query is checked separately, once, in
    # `build_point_population`. Two docstrings stating opposite things was a real
    # review finding (#12186); the truth is this comment.
    toks = text.split()
    candidates = [i for i in range(len(toks) - n_tokens + 1)
                  if _valid_window(toks, i, n_tokens)]
    if not candidates:
        return None
    if postings is None:
        start = candidates[0]
    else:
        def _hits(s: int) -> int:
            # Scored on the RAW tokens, which the canonical document is guaranteed
            # to hold (the span came out of it), so a posting can never be absent
            # for the canonical's own terms and the score cannot be dragged to a
            # degenerate value.
            #
            # ⛔ The intersection size is a SELECTIVITY PROXY, not the leg's match
            # model: the leg OR-unions these terms (`sparse.build_or_query`).
            # Scoring the SANITISED form instead made every window tie (the
            # sanitiser strips the punctuation that told them apart), so the
            # sanitised-form check lives once, in `build_point_population`.
            terms = [toks[s + j].lower() for j in range(n_tokens)]
            sets = [postings.get(t) for t in terms]
            if any(x is None for x in sets):
                return 1 << 30
            return len(set.intersection(*sets))

        start = min(candidates, key=lambda s: (_hits(s), s))
    return " ".join(toks[start:start + n_tokens])


def _term_postings(texts: list[str]) -> dict[str, set[int]]:
    """Inverted index over the population: term -> documents holding it.

    Within-corpus only, computed from the population being measured — a term's
    selectivity is relative to the corpus in front of us, which is the only
    corpus a lane can see.
    """
    postings: dict[str, set[int]] = collections.defaultdict(set)
    for i, text in enumerate(texts):
        for w in {w.lower() for w in text.split()
                  if len(w) >= MIN_TERM_LEN and not _NOT_A_WORD.match(w)}:
            postings[w].add(i)
    return dict(postings)


def build_point_population(turns: list[str], n: int, sanitise=None) -> list[dict]:
    """Points from real captured turn text, each carrying its own fixed query.

    The QUERY is chosen AFTER the whole population is known, because the choice
    is by selectivity and a selectivity computed from a prefix would be a
    different corpus from the one measured. Two passes: normalise + filter, then
    pick the most selective span per Point — scored on its RAW tokens (the
    sanitised form tied every window; see `query_span`), with the SANITISED form
    then checked once to reject a span the sanitiser reduces below a usable
    query.

    A Point whose text yields no query span is DROPPED rather than given a
    synthetic query: a query invented here would be a query the corpus never
    contained, and the whole population's job is to be real. The drop count is
    recorded by the caller's own accounting.
    """
    kept: list[str] = []
    for text in turns:
        if len(kept) >= n:
            break
        text = " ".join(text.split())[:POINT_TEXT_LIMIT]
        # A turn too short to hold a span, or too short to be a claim, is not a
        # search target.
        if len(text) < 80:
            continue
        kept.append(text)

    postings = _term_postings(kept)
    out: list[dict] = []
    seen_spans: set[str] = set()
    for text in kept:
        q = query_span(text, postings=postings)
        if q is None:
            continue
        # ⛔ The SELECTIVITY that chose ``q`` was scored on its raw tokens; the
        # form the LEG receives is ``sanitise(q)``, and `_sanitise_query_text`
        # rewrites it (measured: ``first. INTERNAL: read the diff`` → ``first read
        # the diff``). A span the sanitiser reduces below three terms is no longer
        # a query that can single out a claim, so it is dropped HERE rather than
        # scored against a selectivity it does not have.
        if sanitise is not None and len(sanitise(q).split()) < 3:
            continue
        # ⛔ ONE CANONICAL PER QUERY, enforced rather than hoped for. The captures
        # repeat boilerplate verbatim (fleet briefs, AGENTS.md, the PR template),
        # so several Points can yield the SAME span — and a query that identifies
        # several claims has no single canonical to recall, so scoring it would
        # charge the window for an ambiguity the corpus created. Deduping on the
        # span makes every query name exactly one node; the count of Points lost
        # to it is reported by the caller's accounting, not swallowed.
        key = q.casefold()
        if key in seen_spans:
            continue
        seen_spans.add(key)
        # Popularity proxy = the number of distinct word-bearing tokens at least
        # MIN_TERM_LEN long. It is a WITHIN-CORPUS ordering signal and is
        # labelled as such; it is NOT the tenant read ledger, which §17.4.6
        # forbids as a product input.
        pop = len({t for t in text.split()
                   if len(t) >= MIN_TERM_LEN and not _NOT_A_WORD.match(t)})
        out.append({"id": f"{ID_PREFIX['point']}{len(out):06d}",
                    "content": text, "query": q, "popularity": pop})
    return out


def harvest_turn_texts(sessions_dir: Path, limit: int) -> tuple[list[str], dict]:
    """Real turn text from the capture corpus, plus this read's own accounting.

    ⛔ THE LIMIT IS SPREAD ACROSS EVERY FILE, not consumed from the first few.
    Measured: taking the first ``limit`` turns read 6 of 22 files, and those six
    are one probe-heavy day whose turns share a `README`-shaped boilerplate — a
    population that dense in near-duplicates makes every query ambiguous and
    would have been reported as a retrieval result. Scaling the per-file budget
    to the file count keeps the population spanning the corpus, and the file count
    actually consumed is recorded so a reader can see the spread.

    ⛔ The corpus parse lives here rather than being borrowed from
    ``candidate_recall_11901.harvest`` because that function's contract is NAME
    extraction (it returns Counters keyed by class and deliberately loses
    document identity); the OBJECT population still uses it, so the harvest RULES
    keep one owner. What is shared is the input, not the reading of it.
    """
    stats = {"files": 0, "files_contributed": 0, "lines": 0,
             "parse_errors": 0, "records_without_text": 0}
    files = sorted(sessions_dir.glob("*.jsonl"))
    stats["files"] = len(files)
    if not files:
        return [], stats
    per_file = max(1, -(-limit // len(files)))  # ceil, so the budget can fill
    texts: list[str] = []
    for f in files:
        taken = 0
        for line in f.open(errors="replace"):
            stats["lines"] += 1
            try:
                rec = json.loads(line)
            except Exception:
                stats["parse_errors"] += 1
                continue
            parts = [t.get("content") for t in (rec.get("conversation") or [])
                     if isinstance(t.get("content"), str)]
            if not parts:
                stats["records_without_text"] += 1
                continue
            for text in parts:
                if taken >= per_file:
                    break
                texts.append(text)
                taken += 1
            if taken >= per_file:
                break
        if taken:
            stats["files_contributed"] += 1
    return texts[:limit], stats


# ── the measurement ──────────────────────────────────────────────────────────

def _git_head() -> str | None:
    """The commit this artifact describes, or None (never a fabricated sha)."""
    try:
        import subprocess
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=10, check=False,
        ).stdout.strip() or None
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    """sha256 of a file's bytes — provenance that binds a figure to its producer."""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signal_available() -> dict:
    """Can the reading caller learn that the store was PARTIAL?

    Resolved from the CODE so the artifact records a FACT rather than this
    docstring's opinion. Both facts are required for §5 Q2 to hold, and they are
    asked separately because they can diverge.

    ``capture_prior_read_status``  does ``extractor_v2._fts_rows`` — the capture
                                   path, the one place a wrong prior is PERSISTED
                                   rather than merely displayed — request
                                   ``read_status_out``?
    ``residency_marker_armed``     is ``TORTOISE_RESIDENCY`` set? The marker
                                   exists (§7 item 1) and is OFF by default, so
                                   unarmed means the response carries no residency
                                   verdict at all.
    """
    import inspect

    from tortoise import extractor_v2
    from tortoise.residency import residency_enabled

    # ⛔ AST, not substring. A substring test over `inspect.getsource` reads the
    # DOCSTRING AND COMMENTS too, so a comment saying "this path does not pass
    # `read_status_out`" would flip the recorded fact to True and publish a
    # signal-present verdict on a §5 Q2 claim — a correct answer reached by an
    # accident that the next comment would reverse. Walking keyword ARGUMENTS
    # reads what the function actually passes.
    #
    # `getsource` returns a METHOD with its original indentation, so it is
    # dedented before parsing (an indented module-level parse is a SyntaxError).
    keywords = set()
    tree = ast.parse(textwrap.dedent(inspect.getsource(extractor_v2._fts_rows)))
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg:
            keywords.add(node.arg)
    return {
        "capture_prior_read_status": "read_status_out" in keywords,
        "capture_prior_truncation": "_truncation_out" in keywords,
        "residency_marker_armed": bool(residency_enabled()),
    }


def run(args: argparse.Namespace) -> int:
    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — S3 itself refuses "
              "the embedded backend, and a window figure measured on a backend "
              "the product cannot use is not a figure.\n"
              "  start one:  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"\n"
              "  (test_lane.py `run` executes PYTEST, not this tool — see USAGE)",
              file=sys.stderr)
        return 3

    # ⛔ IMPORTED, never re-implemented: the harvest rules and the population
    # builder have ONE owner. A second copy here could drift from the one whose
    # 61.3 % / 73 % / 27 % figures this epic quotes.
    from tools.candidate_recall_11901 import _has_embedder, build_population, harvest

    # The SAME sanitiser production hands the leg, from its own home.
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.sdk import TortoiseSDK

    sessions = Path(os.path.expanduser(args.sessions_dir))
    artifact_stats: dict = {}

    # ── population construction ──────────────────────────────────────────────
    texts, text_stats = harvest_turn_texts(sessions, args.points * 4)
    artifact_stats["corpus"] = text_stats
    pops: dict[str, list[dict]] = {
        "point": build_point_population(texts, args.points, sanitise=_sanitise_query_text),
    }

    _input_stats: dict = {}
    harvested = harvest(sessions, stats=_input_stats)
    obj_pop = build_population(harvested, args.objects)
    # ``build_population`` names the popularity signal ``records`` (the number of
    # session RECORDS the name appeared in — one owner of that definition, in the
    # harvest). It is carried as ``popularity`` because that is what ``tail``
    # orders by.
    for i, row in enumerate(obj_pop):
        row["id"] = f"{ID_PREFIX['object']}{i:06d}"
        row["popularity"] = int(row.get("records") or 0)
        row["query"] = row["name"]
    pops["object"] = obj_pop
    artifact_stats["names_harvested"] = {c: len(v) for c, v in harvested.items()}

    for kind, pop in pops.items():
        if not pop:
            print(f"REFUSED: the {kind} population is empty", file=sys.stderr)
            return 3

    # ── ONE FRESH GRAPH PER ARM ───────────────────────────────────────────────
    # ⛔ Not one graph reloaded per arm. Even rebuilt from an empty prefix, a
    # reloaded graph carries the engine's internal document ids forward, and the
    # lexical leg breaks near-ties on them: measured across two runs of identical
    # input, every `:Object` arm was bit-identical while the `:Point` arm's
    # `recall@3` moves on a `:Point` arm. The MAGNITUDE is not restated here: the
    # only bound the instrument may quote is `REPRODUCIBILITY_BOUND`, and an earlier
    # cut of this comment said "up to 2 points at random@0.50 (0.120 vs 0.117)" —
    # 0.120 vs 0.117 is 0.3 pp, and it contradicted that very block. A fresh
    # graph per arm makes each arm an independently reproducible graph state, which
    # is what the artifact claims to record.
    graph_name = args.graph or f"m2win_{int(time.time())}_{os.getpid()}"
    print(f"m2: graph prefix {graph_name} (point={len(pops['point'])}, "
          f"object={len(pops['object'])})", file=sys.stderr)

    legs_seen: dict[str, bool] = {}
    graphs: list[str] = []
    cur: dict = {"sdk": None, "proj": None}

    def fresh(kind: str, tag: str) -> None:
        """Bind the helpers below to a BRAND-NEW graph holding nothing yet."""
        # The tag carries a decimal point (`tail_0.9`), which a graph name may
        # not; the dot becomes an `x` so the name is legal and still readable.
        name = f"{graph_name}_{kind}_{tag.replace('.', 'x')}"
        graphs.append(name)
        sdk = TortoiseSDK(namespace=args.namespace, graph_name=name)
        cur["sdk"], cur["proj"] = sdk, sdk._get_proj()
        # A fresh graph has no degraded snapshot, so nothing stale can be served.
        return

    def invalidate_degraded_snapshot() -> None:
        """Invalidate the degraded-fallback corpus snapshot after a graph edit.

        ⛔ REQUIRED, and its absence is a MEASURED defect in this instrument's
        first real run. ``fallback_snapshot`` keeps a 60 s in-memory copy of the
        whole corpus and serves it from the DEGRADED path (the in-memory TF-IDF
        tier that runs when the retrieval strategies fail). It is invalidated by
        ``SDK._mark_dirty``, which every normal write surface calls — and which a
        raw Cypher ``DELETE`` does NOT. So an eviction performed with bare Cypher
        leaves the snapshot serving the demoted corpus, and the arm reports recall
        above its own resident share: exactly what happened here, caught by
        invariant 1 (``recall@10`` 0.1133 > ``resident_share`` 0.1100), not by
        inspection.

        ``_mark_dirty([])`` is the hook itself: with an empty id list it does the
        snapshot invalidation and returns before any EP work.

        ⚠️ This is a WINDOW requirement, not a test detail — §7's window may not
        serve a node it has demoted, on ANY leg. Recorded in the evidence record
        and filed as its own issue, because the shipped egress executor
        (``tortoise/cache_evictor.py`` ``_delete_nodes``) issues the same bare
        ``DETACH DELETE`` and inherits the same staleness.
        """
        try:
            cur["sdk"]._mark_dirty([])
        except Exception as exc:
            raise RuntimeError(
                f"could not invalidate the degraded-fallback snapshot ({exc}) — "
                f"an arm measured against a stale snapshot is not a windowed graph") from exc

    def ids_now(kind: str) -> set[str]:
        """The ids the GRAPH actually holds for ``kind`` — never what we meant."""
        return {r[0] for r in cur["proj"].g.query(
            f"MATCH (n:{_label(kind)}) WHERE n.id STARTS WITH '{ID_PREFIX[kind]}' "
            "RETURN n.id", params={}).result_set}

    def write(kind: str, rows: list[dict]) -> None:
        for row in rows:
            if kind == "point":
                cur["proj"]._upsert_point_props({"id": row["id"], "content": row["content"]})
            else:
                cur["proj"]._upsert_object({"id": row["id"], "name": row["name"],
                                            "object_kind": "core:other"})

    def query(kind: str, text: str, k: int) -> list[str]:
        """EXACTLY the leg ``_fts_rows`` runs, at width ``k``, returning ids."""
        trace: list[dict] = []
        rows = cur["sdk"].tortoise_fts_query(
            _sanitise_query_text(text), entity_type=kind, limit=k,
            # `_fts_rows` opts the entity legs back INTO terminal-inclusive; the
            # Point leg keeps the default. Mirrored exactly, per leg.
            include_terminal=kind in ("object", "subject"),
            _record_reads=False, _valid_time_rerank=False, leg_trace=trace)
        for entry in trace or []:
            if not isinstance(entry, dict):
                continue
            leg = entry.get("leg")
            if leg:
                legs_seen[leg] = bool(legs_seen.get(leg)) or bool(entry.get("ran"))
        return [r.get("id") for r in rows]

    def measure(kind: str, resident: set[str], query_rows: list[dict]) -> dict:
        per_query: list[dict] = []
        for row in query_rows:
            ids = query(kind, row["query"], DEEP_LIMIT)
            # ONE read per query at DEEP_LIMIT: `limit` truncates and never
            # reorders, so `recall@k` is a slice of this one window and
            # `found@deep` is the whole of it. Reading once is also what makes the
            # arms comparable — a second read could return a different order.
            cls = classify_rows(ids, row["id"])
            per_query.append({
                "id": row["id"],
                "resident": row["id"] in resident,
                "empty": cls["empty"],
                "top1_other": cls["top1_other"],
                "found_deep": cls["found_deep"],
                "hit": cls["hit"],
            })
        return summarise(per_query)

    def reset(kind: str) -> None:
        """Drop this population's nodes and invalidate the degraded snapshot."""
        cur["proj"].g.query(f"MATCH (n:{_label(kind)}) WHERE n.id STARTS WITH "
                            f"'{ID_PREFIX[kind]}' DETACH DELETE n", params={})
        invalidate_degraded_snapshot()

    def run_population(kind: str) -> tuple[dict, list[dict]]:
        pop = pops[kind]
        fresh(kind, "complete")
        write(kind, pop)
        invalidate_degraded_snapshot()
        landed = ids_now(kind)
        if landed != {row["id"] for row in pop}:
            raise RuntimeError(
                f"{kind}: {len(pop) - len(landed)} of {len(pop)} nodes did not land")

        # The fixed query set — sampled ONCE and reused by EVERY arm. §9 says "on
        # a fixed query set", and an arm that re-sampled would compare two
        # populations rather than two graph states.
        rng = random.Random(args.seed)
        query_rows = list(pop)
        rng.shuffle(query_rows)
        query_rows = query_rows[: min(args.sample, len(query_rows))]

        # One warming query so the first measured query is not paying the
        # full-scan cold cost — constant across every arm, so it cannot bias a
        # comparison.
        query(kind, query_rows[0]["query"], 1)

        all_ids = {row["id"] for row in pop}
        arms: dict[str, dict] = {"complete": measure(kind, all_ids, query_rows)}
        if ids_now(kind) != all_ids:
            raise RuntimeError(f"{kind}: the complete arm lost nodes")

        for policy in POLICIES:
            order = eviction_order(pop, policy, args.seed + EVICT_SEED_OFFSET)
            for coverage in COVERAGES:
                if coverage >= 1.0:
                    continue  # the shared baseline above
                keep = set(resident_ids(order, coverage))

                # A FRESH graph, written in POPULATION order (not eviction order),
                # so the write sequence is the same for every arm and cannot
                # itself move a tie.
                fresh(kind, f"{policy}_{coverage:g}")
                write(kind, [r for r in pop if r["id"] in keep])
                invalidate_degraded_snapshot()
                held = ids_now(kind)

                # ⛔ INVARIANT 2 — the arm's graph must hold EXACTLY the set it
                # names. A mismatch publishes a rate for a graph state that did
                # not exist, and it is invisible in invariant 1 — a graph holding
                # FEWER nodes lowers recall AND the share together, so the bound
                # stays satisfied.
                if held != keep:
                    raise RuntimeError(
                        f"{kind}/{policy}@{coverage:g}: intended {len(keep)} "
                        f"resident ids, graph holds {len(held)} "
                        f"({len(keep - held)} missing, {len(held - keep)} unexpected)")

                arm = measure(kind, held, query_rows)
                arm["coverage_requested"] = coverage
                arm["coverage_delivered"] = len(held) / len(pop)

                # ⛔ INVARIANT 1 — a demoted node cannot be returned, so neither
                # the deep axis nor any recall@k can exceed the resident share.
                #
                # ⚠️ This aggregate bound is NOT the verdict's load-bearing claim,
                # and on its own it cannot enforce it (#12186 review P1-2).
                # `found_deep` is a rate over ALL queries, so on a population
                # whose complete-graph ceiling is below 1.0 (`:Point` on this
                # corpus: 0.410 vs resident 0.5267) a leak of demoted queries
                # sits UNDER the bound and passes. The comment here used to claim
                # the opposite — that it "fails for any demoted node surfaced at
                # any rank" — which was only true on `:Object`, where the
                # ceiling is 1.0. INVARIANT 3 below is the per-demoted detector
                # and is the one the record's "no demoted node is returned"
                # sentence rests on.
                for name in [f"recall@{k}" for k in KS] + ["found_deep"]:
                    if arm[name] > arm["resident_share"] + 1e-9:
                        raise RuntimeError(
                            f"{kind}/{policy}@{coverage:g}: {name}="
                            f"{arm[name]:.4f} exceeds resident_share="
                            f"{arm['resident_share']:.4f} — a demoted node was "
                            f"returned, so the eviction did not take effect")

                # ⛔ INVARIANT 3 — no demoted node is returned at any rank. This
                # is the strong form and the one the verdict turns on, so it is
                # asserted unconditionally on EVERY arm of BOTH populations: it
                # does not depend on the ceiling, and an `:Object`-only or
                # single-arm check would let the one claim the record makes
                # about `:Point` go unverified.
                if arm["found_deep_of_demoted"] != 0.0:
                    raise RuntimeError(
                        f"{kind}/{policy}@{coverage:g}: "
                        f"found_deep_of_demoted="
                        f"{arm['found_deep_of_demoted']:.4f} — a demoted node "
                        f"was returned at depth, so the loss is NOT purely the "
                        f"eviction")
                arms[f"{policy}@{coverage:g}"] = arm
                print(f"  {kind:6s} {policy}@{coverage:<5g} resident "
                      f"{arm['resident_share']:6.1%}  r@3 {arm['recall@3']:6.1%}  "
                      f"deep {arm['found_deep']:6.1%}  mis@1 "
                      f"{arm['misbind_at_1']:6.1%}  silent0 {arm['silent_zero']:6.1%}",
                      file=sys.stderr)
        return arms, query_rows

    result: dict = {}
    for kind in POPULATIONS:
        arms, query_rows = run_population(kind)
        complete = arms["complete"]
        result[kind] = {
            "nodes": len(pops[kind]),
            "queries": len(query_rows),
            "arms": arms,
            "recall_gap_vs_complete": {
                arm: {f"recall_drop@{k}": round(complete[f"recall@{k}"] - data[f"recall@{k}"], 6)
                      for k in KS}
                for arm, data in arms.items() if arm != "complete"
            },
        }
        # Clear this population's NODES. The per-arm GRAPHS are NOT dropped —
        # see the `cleanup` block in the artifact, which declares how many a run
        # leaves behind rather than claiming a cleanup that does not happen.
        reset(kind)

    artifact = {
        "measurement": "M2",
        "issue": 12186,
        "document": "docs/architecture/SEARCH-ARCHITECTURE.md §9",
        "gate_for": "§7 — arming the cache window",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "git_head": _git_head(),
        "params": {
            "points": args.points, "objects": args.objects,
            "sample": args.sample, "ks": list(KS), "deep_limit": DEEP_LIMIT,
            "coverages": list(COVERAGES), "policies": list(POLICIES),
            "seed": args.seed, "evict_seed_offset": EVICT_SEED_OFFSET,
            "graph_prefix": graph_name, "point_text_limit": POINT_TEXT_LIMIT,
        },
        "corpus": artifact_stats,
        "retrieval": {
            "has_embedder": _has_embedder(),
            "legs_seen": dict(sorted(legs_seen.items())),
            #: ⛔ Read this before quoting anything: a `keyword_only` run is a
            #: figure about the LEXICAL leg, never about the fused read.
            "mode": "hybrid" if _has_embedder() else "keyword_only",
        },
        "signal": _signal_available(),
        "provenance": {
            #: The instrument's OWN bytes, so a figure can be traced to the code
            #: that produced it — not to a head sha, which the repo squash-merges
            #: and which a later re-run could never re-verify.
            "tool_sha256": _sha256_file(Path(__file__).resolve()),
        },
        #: ⛔ Measured, and NOT a second copy of this record. It lives at MODULE
        #: level (`REPRODUCIBILITY_BOUND`) so `tests/test_window_quality_m2.py`
        #: can import the block the instrument EMITS and pin the committed
        #: artifact's block EQUAL to it. That equality is what the artifact's
        #: `provenance.tool_sha256` claims, and without the pin the binding is an
        #: assertion about bytes that may no longer emit these keys — which is
        #: exactly what had happened here (#12186 review P0-1): the artifact
        #: carried 3 runs / 0.33 / 1.0 / 9.85 while these bytes emitted
        #: 2 / 0.0 / 2.94 and a "17-43" gap, so a reproducer following the
        #: README would have reported a diff that was not a measurement.
        "reproducibility": dict(REPRODUCIBILITY_BOUND),
        "cleanup": {
            "graphs_created": len(graphs),
            "graphs_dropped": 0,
            #: ⛔ `reset()` runs ONCE PER POPULATION and acts on `cur["proj"]`, which the
            #: last arm rebound — so only the FINAL arm's graph per population is
            #: emptied. An earlier version said "nodes are cleared after each
            #: population, but the graphs themselves are left behind", which reads as
            #: 22 empty graphs; it is 20 POPULATED ones, and a reader sizing the lane's
            #: footprint needs that number.
            "note": "each arm is measured on its own graph (params.graph_prefix) "
                    "and NO graph is dropped. reset() runs once per population and "
                    "acts on the graph the last arm left current, so only the final "
                    "arm's graph per population is emptied: a run leaves "
                    "2 * (1 + policies * coverages) - 2 graphs still POPULATED "
                    "(20 of 22 at the committed parameters), not 20 empty ones. On a "
                    "lane container `test_lane.py down` disposes of them, which is the "
                    "intended path — a shared server accumulates one full set per run",
        },
        "populations": result,
    }

    out = Path(os.path.expanduser(args.out))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(f"m2: wrote {out}", file=sys.stderr)
    return 0


def _label(kind: str) -> str:
    """The Cypher label for a population kind. One map, so a query cannot name a
    label the writer did not use."""
    return {"point": "Point", "object": "Object"}[kind]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="M2 — retrieval quality under a windowed vs complete graph "
                    "(#12186 §9). Needs a live FalkorDB in TORTOISE_DB_URI.")
    p.add_argument("--points", type=int, default=1000,
                   help=":Point nodes built from real captured turns (default 1000)")
    p.add_argument("--objects", type=int, default=1000,
                   help=":Object nodes from the harvest (default 1000)")
    p.add_argument("--sample", type=int, default=300,
                   help="queries in the FIXED query set, per population (default 300)")
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    p.add_argument("--namespace", default="default")
    p.add_argument("--graph", default=None,
                   help="graph name; default is run-scoped. A REUSED graph must be "
                        "empty — `name`/`content` are ON CREATE writes (#8919)")
    p.add_argument("--seed", type=int, default=11901,
                   help="seed for the query sample AND (offset) the `random` order")
    p.add_argument("--out", default="m2-windowed.json")
    args = p.parse_args(argv)
    if args.points < 1 or args.objects < 1 or args.sample < 1:
        print("REFUSED: --points, --objects and --sample must be >= 1", file=sys.stderr)
        return 2
    try:
        return run(args)
    except RuntimeError as exc:
        # Every invariant failure lands here: the run is REFUSED, never published
        # with a warning, because a rate from a graph state that did not exist is
        # worse than no rate at all.
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
