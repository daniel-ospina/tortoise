#!/usr/bin/env python
"""M3 — does a BM25-capable Postgres option beat the in-graph TF-IDF scorer
on the FTS leg's own recall@k? (tortoise #12298, epic #12186 C2/C3)

WHY THIS IS A RECALL QUESTION AND NOT A RANKING QUESTION
--------------------------------------------------------
``rrf_fusion`` **discards** each leg's score — ``for rank, (pid, _score) in
enumerate(ranked)`` (``tortoise/search_engine.py``) — so on the multi-leg read
the scorer can only act through ``ORDER BY … LIMIT``. The one arm where the
magnitude survives casts it as a *floor* (the single-leg/full-scan arm compares
a caller ``threshold`` against the leg's raw score), which ``SEARCH-ARCHITECTURE.md``
§6.2 already states. Therefore a different scorer is a **recall** lever, and M3
must report the FTS leg's ``recall@k`` **in isolation** — a ranking-only
comparison (nDCG on a fused list) cannot answer it (#12186 round 2, `6837406765`;
round 3, `#12308` A-3).

WHAT IS MEASURED, AND ON WHAT
-----------------------------
The population, the harvest rules and the arm definition are **#11901's**, not
invented here: ``acquire()`` below imports ``harvest``/``build_population``/
``_allocate_shares``/``_interleave`` from ``tools/candidate_recall_11901.py`` so
the two measurements cannot drift apart. Arm 1 of that instrument is reused
verbatim in meaning: *the mention IS the stored name* — every name in the
sample is written to the store under a known id, queried with its own sanitised
text, and counted a hit when that id is in the top-k.

⛔ **The population is PINNED to a file** (``--population-file``) and both arms
read the SAME file, in the same run, with the same ids. That is what makes M3
*paired* — and it closes the gap #11901's brief had to declare (its two
artifacts were between-run because the harness wrote no population out).

THE ARMS, AND WHY THE PRIMARY ONE HOLDS THE TOKENIZER CONSTANT
--------------------------------------------------------------
``falkordb_tfidf``  the shipped leg: ``db.idx.fulltext.queryNodes`` (RediSearch's
                    TF-IDF scorer) through ``search_engine.run_fts_query`` — the
                    real production function, called directly, so no fusion and
                    no dense leg is involved at all.
``postgres_bm25``   the same names in Postgres 16 with ParadeDB ``pg_search``'s
                    ``USING bm25`` index, queried ``name ||| <terms>``.

Two Postgres queries are measured, and the difference between them is a
**control, not a convenience**:

``tokens``   the term set is OUR production one — ``build_or_query``'s exact
             terms (``sparse.tokenize_sparse_query`` + the #8933 identifier
             aliases and adjacency phrase) — handed to Postgres as written.
             Same index-time text is each engine's own, but the QUERY terms and
             therefore the scorer's inputs are identical on both sides, so a
             recall difference is attributable to the **scorer**.
``native``   the sanitised query string handed to ``|||`` raw, letting Postgres
             tokenise it with its own analyser (stopwords kept, digits kept).
             This is what a migration would ship on day one; it is reported as
             supporting evidence, because a difference here may be the analyser
             rather than BM25.

**PRIMARY is ``tokens``.** C2 is *"BM25 instead of TF-IDF"* — a scorer change.
The decision rule is a CONSTANT below, copied verbatim into the artifact so a
reader can check the numbers against it. ⚠️ It cannot be re-chosen per run
without editing two places; that it existed BEFORE this run is a claim about the
lane's process, not something this repository can show (harness, artifact and
brief landed in one commit — see §4.1 of the brief).

⚠️ The token-set control is SELF-CHECKED: the tool rebuilds the RediSearch OR
string from the term list it will hand Postgres and **refuses** if that string
differs from what ``build_or_query`` actually emits for the same query. A
"controlled" arm that silently drifted from the production tokenizer would
measure the wrong thing and look healthy.

THE FALSIFIER
-------------
``recall@3`` (the production ``limit=3``) on the primary arm, tested on the
**discordant pairs only** (exact two-sided McNemar / binomial). A leg that beats
the current scorer by a margin indistinguishable from coin-flipping on the
pairs where the two arms disagree does not pass. All k are reported; the test
is on k=3 because that is the width the product ships.

HOW TO RUN
----------
::

    # 1. a private FalkorDB lane for this worktree (never the shared 6379)
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"          # two steps on purpose — see test_lane.py USAGE

    # 2. Postgres 16 + ParadeDB pg_search (the option M3 names)
    docker run -d --name m3-paradedb -p 127.0.0.1:16550:5432 \\
        -e POSTGRES_PASSWORD=pg -e POSTGRES_DB=m3 \\
        paradedb/paradedb:0.26.6-pg16
    docker exec m3-paradedb psql -U postgres -d m3 -c 'CREATE EXTENSION IF NOT EXISTS pg_search'

    # 3. the measurement (needs the `postgres` extra for psycopg2)
    uv run python tools/m3_fts_leg_recall.py run --recovery-arm \\
        --pg-dsn 'host=127.0.0.1 port=16550 dbname=m3 user=postgres password=pg'

The artifact lands in ``docs/research/2026-11-13-12298-m3-fts-leg/``: one
``population.json`` (the pinned sample) and one ``artifact.json`` (both arms +
the paired comparison).

⛔ **Use ``--recovery-arm`` to measure all four arms**: without it the recovery
arm is not measured and the artifact carries three, not four. The flag is off by
default because that arm measures a fix that is NOT shipped (``#12350``).

⛔ **A re-run reproduces the POPULATION and the four arms — not the committed
byte-for-byte artifact, and it must not be described as doing so.** This harness
has been repaired since the measurement, and each repair is recorded in the
artifacts' ``measurement_notes``: the two FalkorDB arms now write to separate
graphs (a re-run measures ``index_numDocuments`` 300 on both, where the committed
artifacts record 305 for the recovery arm), the Postgres engine string no longer
reads "PostgreSQL PostgreSQL", and a run emits no ``measurement_notes`` at all.
The NUMBERS are the measurement; the differences above are the state of the
instrument, which §3.5 of the brief discloses and calls a re-run the confirmation
of. The pinned ``population.json`` is what makes a re-run paired rather than a
fresh harvest.

WHAT THIS DOES NOT MEASURE (declared, not buried)
-------------------------------------------------
* The population is a **sample of the strings real captures contain**, harvested
  from this box's ``~/.tortoise/session-events`` — never a census of production
  ``:Object`` rows. Same declared limit as #11901; the population file makes the
  sample reproducible, not representative.
* **Index-time tokenisation is each engine's own** and is not controlled. The
  query-side terms are. A recall difference on ``native`` may be the analyser.
* The **embedded** sklearn TF-IDF fallback (``tortoise/sparse.py``'s other
  backend) is not measured — it is outside ``db.idx.fulltext`` entirely.
* Only k ∈ {3, 5, 10, 15} and only the ``:Object`` label (the label the capture
  prior queries, ``extractor_v2._fts_rows``).
"""
from __future__ import annotations

import sys

# #5128 (the repo's entry-point contract): refuse a <3.12 interpreter HERE, before
# the rest of the imports, so a stale local `python3` fails LOUDLY with a remedy
# instead of with whichever 3.12-only construct happens to trip first.
# `tests/test_entry_point_python_guard.py` asserts structurally that this guard
# precedes every other import and behaviourally that it names the floor, the
# interpreter it got, and the invocation that works.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/m3_fts_leg_recall.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/m3_fts_leg_recall.py`"
    )

import argparse
import collections
import hashlib
import json
import math
import os
import re
import statistics
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KS = (3, 5, 10, 15)
HERE = REPO_ROOT / "docs" / "research" / "2026-11-13-12298-m3-fts-leg"


def _repo_relative(path: Path) -> str:
    """A path as this repo would spell it — absolute only if it is outside.

    The artifact is a COMMITTED record read on other boxes, so an authoring
    checkout's absolute path is noise at best and a leak of one machine's layout
    at worst (#12349 review, P2: the committed artifacts carried
    ``/home/cmux/tortoise/.worktrees/fix-12298/…``).
    """
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


#: The falsifier's k — the width the product ships (``extractor_v2._fts_rows``
#: calls ``tortoise_fts_query`` with ``limit=3``).
PRIMARY_K = 3

#: The decision rule, as a CONSTANT. Copied VERBATIM into the artifact so a
#: reader can check the numbers against the same rule the code applies — and a
#: different rule has to be edited in two places. ⚠️ The ORDERING (that this
#: constant predates the 300-name run) is not provable from one commit; the
#: brief's §4.1 says so rather than implying it.
DECISION_RULE = (
    "PASS iff on the primary arm (postgres_bm25/tokens) recall@3 is strictly "
    "greater than falkordb_tfidf recall@3, AND the exact two-sided McNemar "
    "(binomial) test over the discordant pairs has p < 0.05. "
    "FAIL otherwise — including a tie, and including a gain carried by pairs "
    "where the two arms agree."
)


def m3_passes(primary: dict) -> bool:
    """Apply :data:`DECISION_RULE` to the primary comparison's k=3 cell.

    ⛔ A FUNCTION so the PREDICATE is testable, not only the rule TEXT. The rule
    is pinned byte-identical to the artifact, but that pins PROSE: a re-run could
    publish ``M3: PASS`` on a tie (``>`` → ``>=``), on a reversed inequality, or at
    p = 0.5, while the artifact still carried the stricter rule verbatim (#12349
    review cycle 11 — all three were measured green before this existed).
    """
    return (primary["b_recall"] > primary["a_recall"]
            and primary["mcnemar_p_two_sided"] < 0.05)


#: The ``#8933`` IDENTIFIER FIXTURE — #12186 round 3's requirement 5, and it is a
#: **correctness** gate, not a quality one. ``to_tsvector`` cannot reproduce
#: ``ERR-4032 -> err4032`` *plus* the adjacency phrase, so a candidate engine that
#: loses either loses a capability the shipped leg has. Each tuple is
#: ``(id, stored name, query text, what it gates)``; the fixture rows are added
#: AFTER that arm's own recall sweep (so they cannot move a document frequency
#: under it) and the query is asked at the production ``limit=3``.
#:
#: ⛔ "That arm's own" is load-bearing and was WRONG in the committed artifacts
#: (#12349 review, P1): both arms shared one graph, so the recovery arm's sweep
#: ran with the five rows the shipped arm had already inserted — its
#: ``index_numDocuments`` reads 305 against the shipped arm's 300, and a
#: five-document population difference is exactly the thing this comment promises
#: cannot happen. Each arm now writes to its OWN graph (``<graph>__recovery``) so
#: a re-run measures both on the same 300-document population. The committed
#: artifacts predate that fix and still record the 305; the brief states it and
#: says why the direction is not rescued by it. ⛔ Do not hand-edit those counts.
FIXTURES: tuple[tuple[str, str, str, str], ...] = (
    ("m3fx-0", "ERR-4032", "ERR-4032",
     "the composite identifier itself (#8933's own example)"),
    ("m3fx-1", "INV-2024-0091", "INV-2024-0091",
     "a longer composite identifier"),
    ("m3fx-2", "abc_def", "abc_def",
     "snake_case — the shape this measurement found broken"),
    ("m3fx-3", "test_7949_capstone_verdict_check",
     "test_7949_capstone_verdict_check", "a four-segment run"),
    ("m3fx-4", "ERR-4032 retry", "ERR-4032",
     "the ADJACENCY PHRASE: an identifier inside a container record"),
)


def _fixture_summary(name: str, results: list[dict]) -> dict:
    """The fixture verdict: can the engine reach the record AT ALL (k=15)?

    ⛔ The pass criterion is deliberately ``rank is not None`` at the widest
    window, NOT "in the top 3". The DoD's question is a CAPABILITY one — *can
    this store retrieve a record whose name carries a composite identifier* —
    and a fixture row inserted beside 300 look-alike names is ranked among them,
    which is a quality question #11901 already owns. Both ranks are reported so
    neither reading is hidden: a fixture that only appears at k=15 passes here
    and is visible as a rank-15 entry in the table.
    """
    return {
        "arm": name,
        "k_primary": PRIMARY_K,
        "k_widest": 15,
        "requirement": ("#12186 round 3 req 5 — the #8933 identifier fixture is "
                        "a correctness gate: to_tsvector cannot reproduce "
                        "ERR-4032 -> err4032 plus the adjacency phrase"),
        "results": results,
        "hits_at_primary_k": sum(1 for r in results
                                 if r["rank"] is not None),
        "reachable": sum(1 for r in results if r["rank_wide"] is not None),
        "n": len(results),
        "PASS": all(r["rank_wide"] is not None for r in results),
    }


def _import_instrument():
    """The #11901 instrument, imported — never re-implemented.

    ``harvest``/``build_population``/``_allocate_shares``/``_interleave`` ARE
    #11901's definitions of the population. A copy would let the two
    measurements drift, and the drift would be invisible in both artifacts.
    """
    from tools.candidate_recall_11901 import (
        _allocate_shares,
        _interleave,
        build_population,
        harvest,
    )
    return harvest, build_population, _allocate_shares, _interleave


# ── the pinned population ────────────────────────────────────────────────────

def acquire(pop_file: Path, population: int, sample: int,
            sessions_dir: str) -> dict:
    """Load the pinned population, or build it once and pin it.

    ⛔ The file is the pin: a second run reads it instead of re-harvesting.
    #11901 measured the same directory returning ~11 % FEWER definite
    descriptions two hours apart, so a paired comparison built on two harvests
    is not paired at all. Re-build deliberately with ``--rebuild-population``.
    """
    if pop_file.exists():
        return json.loads(pop_file.read_text())

    harvest, build_population, _allocate_shares, _interleave = _import_instrument()
    stats: dict = {}
    t0 = time.time()
    harvested = harvest(Path(os.path.expanduser(sessions_dir)), stats=stats)
    pop = build_population(harvested, population)
    if not pop:
        raise SystemExit("REFUSED: the harvest produced no names")

    by_cls: dict[str, list[dict]] = collections.defaultdict(list)
    for row in pop:
        by_cls[row["cls"]].append(row)
    if sample > len(pop):
        raise SystemExit(
            f"REFUSED: --sample {sample} exceeds the population ({len(pop)})")
    shares = _allocate_shares(sample, pop)
    if any(shares.get(c, 0) == 0 for c in by_cls):
        raise SystemExit(
            f"REFUSED: --sample {sample} starves a class ({shares}) — a "
            f"per-class rate needs at least one name per class")
    rows = _interleave(by_cls, shares)
    # The id is minted HERE, once, and carried into both stores: every
    # comparison below is by identity, never by name (a fold-equal pair such as
    # 'the merge base' / 'the merge-base' can appear in one harvested class).
    for i, row in enumerate(rows):
        row["id"] = f"m3-{i:06d}"

    doc = {
        "issue": "#12298",
        "population_from": "#11901 (tools/candidate_recall_11901.py)",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "harvest_s": round(time.time() - t0, 1),
        "corpus": {
            "dir": sessions_dir,
            "input": stats,
            "harvested_distinct": {c: len(v) for c, v in harvested.items()},
        },
        "population": population,
        "sample": len(rows),
        "shares": shares,
        "digest": hashlib.sha256(
            "\n".join(r["name"] for r in rows).encode()).hexdigest()[:16],
        "names": rows,
    }
    pop_file.parent.mkdir(parents=True, exist_ok=True)
    pop_file.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


# ── query construction (one tokenizer, two engines) ──────────────────────────

def production_terms(query: str) -> tuple[list[str], bool]:
    """``(terms, degenerate)`` — EXACTLY the terms ``build_or_query`` emits.

    ``build_or_query`` returns a pipe-joined string (``a|b|"a b"``) on its
    normal branch and the escaped RAW string on its degenerate branch. The
    terms are recovered here by re-running the same public helpers in the same
    order — duplication, which is why :func:`assert_terms_match` re-derives the
    string from this list and REFUSES on any difference. The control is
    verified, never assumed: a drifted "controlled" arm would measure a
    tokenizer that is not the product's and look perfectly healthy.
    """
    from tortoise.sparse import (
        DEFAULT_MAX_OR_TERMS,
        identifier_aliases,
        identifier_phrase_terms,
        identifier_tokens_enabled,
        tokenize_sparse_query,
    )
    on = identifier_tokens_enabled()
    aliases = identifier_aliases(query) if on else []
    phrases = identifier_phrase_terms(query) if on else []
    tokens = tokenize_sparse_query(query, keep_numeric=False,
                                   keep_identifiers=False)
    if len(tokens) <= 1 and not aliases and not phrases:
        return [query], True
    tokens.sort(key=len, reverse=True)  # stable — ties keep source order
    terms = list(tokens[:DEFAULT_MAX_OR_TERMS])
    # `build_or_query`'s own dedupe: an alias/phrase already present as a plain
    # token is not appended twice (``present = set(terms)``).
    present = set(terms)
    for term in list(aliases) + list(phrases):
        if term not in present:
            present.add(term)
            terms.append(term)
    return terms, False


def assert_terms_match(queries: list[str]) -> dict:
    """Fail closed unless the term list reproduces ``build_or_query`` byte-exactly.

    Returns the terms-mode census (how many queries ride the ordinary OR union
    and how many are DEGENERATE), because that split is a property of the
    measurement a reader is owed, not a footnote — see :func:`arm_terms`.
    """
    from tortoise.sparse import (
        build_or_query,
        escape_redisearch_literal,
    )
    bad: list[tuple[str, str, str]] = []
    degenerate_n = 0
    for q in queries:
        terms, degenerate = production_terms(q)
        degenerate_n += int(degenerate)
        rebuilt = build_or_query(q)
        expected = (escape_redisearch_literal(q) if degenerate
                    else "|".join(terms))
        if rebuilt != expected:
            bad.append((q, expected, rebuilt))
    if bad:
        raise SystemExit(
            "REFUSED: the controlled token set does not reproduce "
            f"build_or_query for {len(bad)} of {len(queries)} queries "
            f"(first: {bad[0]!r}) — a tokenizer control that has drifted "
            "measures the wrong thing.")
    return {"queries": len(queries), "degenerate": degenerate_n,
            "tokens_controlled": len(queries) - degenerate_n}


def arm_terms(q: str) -> tuple[str, bool]:
    """``(terms, degenerate)`` as the ``tokens`` arm actually sends them.

    ⛔ **The degenerate branch is a DECLARED BREAK in the tokenizer control, and
    the first version of this harness did not say so** (found by review). When
    ``build_or_query`` takes its degenerate branch it returns the **escaped raw
    string**, so the shipped leg hands RediSearch one string and there is no
    term list of ours to hand Postgres instead: this returns the raw sanitised
    query, which is verbatim what the ``native`` arm sends. On those rows the
    two Postgres arms are ONE arm and the analyser is NOT held constant.

    Measured on the committed population: **126 of 300** queries are degenerate
    (108 definite — a `the X` name whose only surviving token is `X` — and 18
    ident/path), and for every one of them the two arms' ranks are identical.
    The artifact therefore records the split and the tokens-controlled subset's
    own comparison; the brief states it in §2. The alternative (inventing a
    term list the shipped leg never sends) would be a different measurement, not
    a fairer one.
    """
    tlist, degenerate = production_terms(q)
    return (q if degenerate else " ".join(tlist)), degenerate


# ── the FalkorDB arm ────────────────────────────────────────────────────────

def recovery_terms(query: str) -> list[str]:
    """ADDITIVE terms for the recovery arm — the words the shipped tokenizer splits.

    ⛔ MEASURED (#12298, 2026-11-13): RediSearch's tokenizer treats ``_`` as a
    WORD character, so the index holds ``web_search`` as ONE term — and
    ``sparse.tokenize_sparse_query`` splits on every ``[^a-z0-9]``, so the
    shipped leg ORs ``web`` and ``search``, two terms the index **does not
    hold**. Probe on the lane graph, verbatim::

        'web_search'               -> [['web_search', 8.0]]
        'web'                      -> []
        'selection'                -> []
        'test_ci_selection'        -> [['tests/test_ci_selection.py', 14.0], ...]
        'capstone_verdict_check'   -> [['capstone_verdict_check', 8.0]]
        'test_7949_capstone_verdict_check' -> []      # 4 segments, not indexed

    So every snake_case identifier is unretrievable by its own name: the query
    is well-formed and matches terms the index never contains. This arm
    measures what an ADDITIVE repair recovers (the #8933 ``aliases`` pattern —
    only ever adds an OR alternative, never removes a term), so the arm answers
    *"is the gap the scorer, or is it our own query/index contract?"* rather
    than assuming either answer.

    ⚠️ NOT SHIPPED. This is a measurement arm, exactly as #11901's
    ``attached_folded`` was: the finding is recorded, the fix is not smuggled
    into this lane's diff. A term here is a maximal run of ``[A-Za-z0-9_]``
    that still contains ``_`` — a run WITHOUT ``_`` is already a token the
    shipped tokenizer emits.
    """
    out: list[str] = []
    for run in re.split(r"[^A-Za-z0-9_]+", query or ""):
        if len(run) >= 2 and "_" in run:
            out.append(run.lower())
    return out


def recovery_query(query: str) -> str:
    """The recovery arm's OR string — the shipped terms PLUS the additive ones.

    ⛔ This is a FUNCTION and not three inline lines at each call site because of
    what the zero-regression claim rests on (#12349 review, P2): the pin has to
    assert the property on the code that produced the artifact, and a test that
    re-implements the construction can only assert a tautology about its own
    local list. A ``recovery_query`` that ever *removed* a shipped term would
    red the pin instead of quietly making the arm non-additive.

    The recovery terms are computed from the SANITISED QUERY, never from
    ``build_or_query``'s output: the output has already split ``web_search`` into
    ``web``/``search``, so reading it back would recover nothing.
    """
    from tortoise.sparse import build_or_query

    built = build_or_query(query)
    extra = [t for t in recovery_terms(query) if t not in built.split("|")]
    return "|".join([built, *extra]) if extra else built


def _fts_cypher(graph, or_string: str, limit: int) -> list[tuple[str, float]]:
    """The production ``queryNodes`` Cypher, verbatim, with a caller-supplied OR string.

    ⛔ This exists because ``run_fts_query`` **re-builds** the query through
    ``build_or_query`` (``search_engine.py``, the R2/#1541 leg), so a caller
    cannot hand it a term set of their own — a first cut of this arm passed
    ``search|web|web_search`` and measured +0.33 pts, because the function
    re-tokenised it straight back to ``search|web``. That is the harness
    destroying the thing under test, and it is why the recovery arm issues the
    Cypher itself.

    The statement below mirrors ``run_fts_query``'s ``queryNodes`` branch for
    ``entity_type="object"``: same ``CALL``, same status filter
    (``_STATUS_FILTERED_LABELS`` / ``_status_vocab_for`` /
    ``_exclude_status_clause``), same ``ORDER BY score DESC`` + recency tie,
    same ``LIMIT``. ``verify_cypher_parity`` refuses if it ever stops
    reproducing ``run_fts_query`` — a replica nobody checks is a second
    implementation.
    """
    from tortoise.search_engine import (
        _exclude_status_clause,
        _status_vocab_for,
        recency_tie_clause,
    )
    label, id_field = "Object", "id"
    vocab, flag = _status_vocab_for(label)
    status_filter = ("WHERE " + _exclude_status_clause(
        "node", vocab, include_outdated_flag=flag) + " ")
    cypher = (
        f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
        "YIELD node, score " + status_filter +
        f"RETURN node.{id_field}, score "
        f"ORDER BY score DESC, {recency_tie_clause('node', id_field)} "
        "LIMIT $limit"
    )
    rows = graph.query(cypher, params={"query": or_string,
                                       "limit": limit}).result_set
    return [(r[0], float(r[1])) for r in rows]


def _refuse_degraded_legs(trace: list[dict], query: str) -> None:
    """Refuse the run if the shipped leg did not actually RUN for a query.

    ``run_fts_query`` returns ``[]`` from every degraded exit as well as from a
    genuine empty result, so the sweep cannot tell them apart by looking at the
    rows. The trace is the leg's own account of what happened, and this is the
    measurement's alarm for the direction that would otherwise flatter a
    *conclusion*: a wedged FalkorDB collapsing the shipped baseline to ~0 turns
    a non-result into a spectacular PASS.
    ... an ABSENT trace is a refusal too (review r5): the repo's own
    ``declared_degraded_read`` / ``require_hybrid_read`` treat an empty trace as
    ``leg_trace_unavailable`` and fail closed for exactly this reason — a future
    exit branch that forgets to ``_record`` would otherwise sail through a guard
    that only ever looked for BAD entries. Every ``return []`` in
    ``run_fts_query`` records first today; this keeps that a requirement rather
    than an accident.
    """
    entries = [e for e in trace
               if isinstance(e, dict) and e.get("leg") == "fts"]
    if not entries:
        raise SystemExit(
            f"REFUSED: the shipped FTS leg left NO trace entry for {query!r} — "
            "an absent trace is not a clean read; the leg's own account is "
            "required before its empty result may be counted as a miss.")
    bad = [e for e in entries if e.get("degraded") or not e.get("ran")]
    if bad:
        raise SystemExit(
            f"REFUSED: the shipped FTS leg did not run cleanly for {query!r} "
            f"— {bad}. A degraded read (breaker open / index missing / query "
            "failed) returns [] exactly like a genuine miss, so continuing "
            "would publish an understated baseline.")


def verify_cypher_parity(graph, queries: list[str]) -> None:
    """Refuse unless :func:`_fts_cypher` reproduces ``run_fts_query`` exactly.

    ⛔ Anti-vacuity: two EMPTY result sets compare equal, so a parity check over
    queries the shipped leg finds nothing for would pass while proving nothing
    about the replica. At least one query must return rows (review r3).
    """
    from tortoise.search_engine import run_fts_query
    from tortoise.sparse import build_or_query
    matched = 0
    for q in queries:
        want = run_fts_query(graph, q, entity_type="object", limit=10)
        got = _fts_cypher(graph, build_or_query(q), 10)
        if [i for i, _ in want] != [i for i, _ in got]:
            raise SystemExit(
                "REFUSED: the recovery arm's Cypher no longer reproduces "
                f"run_fts_query for {q!r} — want {want[:3]}, got {got[:3]}. A "
                "replica that has drifted measures a different leg.")
        if want:
            matched += 1
    if not matched:
        raise SystemExit(
            "REFUSED: not one parity query returned a row, so the comparison "
            "was empty-vs-empty and proves nothing about the replica — pass "
            f"queries the shipped leg resolves ({len(queries)} were tried).")


def arm_falkordb(rows: list[dict], graph_name: str, namespace: str,
                 ks: tuple[int, ...], *, recovery: bool = False) -> dict:
    from tortoise.extractor_v2 import _sanitise_query_text
    from tortoise.sdk import TortoiseSDK
    from tortoise.search_engine import run_fts_query
    from tortoise.sparse import build_or_query

    sdk = TortoiseSDK(namespace=namespace, graph_name=graph_name)
    proj = sdk._get_proj()
    for row in rows:
        # The REAL projection write (`_upsert_object`, MERGE on the id per
        # #3590) — not a hand-rolled CREATE, so the index under measurement is
        # the production one.
        proj._upsert_object({"id": row["id"], "name": row["name"],
                             "object_kind": "core:other"})

    # ⛔ `o.name` is an ON CREATE write (#8919), so a reused graph silently
    # measures a previous population. Refuse rather than report a stale score.
    stored = {r[0]: r[1] for r in proj.g.query(
        "MATCH (o:Object) WHERE o.id STARTS WITH 'm3-' RETURN o.id, o.name",
        params={}).result_set}
    stale = [r["name"] for r in rows if stored.get(r["id"]) != r["name"]]
    if stale:
        raise SystemExit(
            f"REFUSED: {len(stale)} of {len(rows)} names did not land (first: "
            f"{stale[:3]!r}) — `o.name` is ON CREATE (#8919); pass a fresh "
            f"--graph.")

    # ⛔ The name check above only catches a reused graph whose population
    # CHANGED. It cannot see a graph carrying the SAME population plus extras —
    # and `m3fx-*` does not match `STARTS WITH 'm3-'`, so a previous run's five
    # fixture rows are invisible to it entirely. That is the exact `305 vs 300`
    # contamination the committed artifacts disclose (#12349 review), so the
    # total is asserted directly rather than inferred from the names.
    total = proj.g.query(
        "MATCH (o:Object) RETURN count(o)", params={}).result_set[0][0]
    if int(total) != len(rows):
        raise SystemExit(
            f"REFUSED: the graph carries {int(total)} :Object rows, expected "
            f"{len(rows)} — a reused graph measures a previous population (or "
            f"a previous arm's fixture rows) and neither the stale-name check "
            f"nor a `>=` index wait can see it. Pass a fresh --graph.")

    # One warm query so the first measured query does not pay the cold
    # full-scan cost (the #4997 unindexed-label scan, constant across arms),
    # then WAIT for the indexer to catch up: FalkorDB's fulltext index builds on
    # a background cycle (`db.indexes()` reports `numCycles` / `numDocuments`),
    # so a query issued straight after the writes can see a PARTIALLY built
    # index and book a present name as a miss. That is a harness artefact in the
    # miss direction — the worst direction — so it is waited out, and the wait
    # is recorded.
    run_fts_query(proj.g, "warm", entity_type="object", limit=1)
    indexed, waited_s = _wait_indexed(proj, len(rows))
    verify_cypher_parity(proj.g, [
        _sanitise_query_text(r["name"]) for r in rows[:8]])

    per_k = {k: {"in_window": 0, "rank_sum": 0, "rows_zero": 0} for k in ks}
    ranks: dict[str, int | None] = {}
    top_scores: list[float] = []
    by_class = collections.defaultdict(lambda: {"n": 0, **{str(k): 0 for k in ks}})
    t0 = time.time()
    n_q = 0
    for row in rows:
        q = _sanitise_query_text(row["name"])
        if recovery:
            # ⛔ The recovery terms are computed from the SANITISED QUERY, never
            # from `build_or_query`'s output: the output has already split
            # ``web_search`` into ``web``/``search``, so reading it back would
            # recover nothing.
            or_string = recovery_query(q)
        by_class[row["cls"]]["n"] += 1
        found_at: int | None = None
        for k in ks:
            if recovery:
                out = _fts_cypher(proj.g, or_string, k)
            else:
                # ⛔ `leg_trace` is not decoration: `run_fts_query` returns `[]`
                # on EVERY degraded path too (breaker open, index missing,
                # query failed — `tortoise/search_engine.py`), so a bare call
                # cannot tell "the leg ran and found nothing" from "the leg did
                # not run". Without it a box where the breaker trips mid-sweep
                # publishes a shipped recall near 0, which flips the verdict to
                # a huge PASS — the exact adoption trigger of #12298 step 3.
                trace: list[dict] = []
                out = run_fts_query(proj.g, q, entity_type="object", limit=k,
                                    leg_trace=trace)
                _refuse_degraded_legs(trace, q)
            n_q += 1
            if not out:
                per_k[k]["rows_zero"] += 1
            ids = [pid for pid, _s in out]
            if ids:
                top_scores.append(out[0][1])
            rank = ids.index(row["id"]) + 1 if row["id"] in ids else None
            if rank is not None:
                per_k[k]["in_window"] += 1
                per_k[k]["rank_sum"] += rank
                by_class[row["cls"]][str(k)] += 1
                if found_at is None:
                    found_at = rank
        ranks[row["id"]] = found_at
    elapsed = time.time() - t0

    # ── the #8933 identifier fixture (req 5), AFTER the sweep so it cannot move
    # a document frequency under a measured query.
    for fid, stored, _q, _why in FIXTURES:
        proj._upsert_object({"id": fid, "name": stored,
                             "object_kind": "core:other"})
    _wait_indexed(proj, len(rows) + len(FIXTURES))
    fx: list[dict] = []
    for fid, stored, query, why in FIXTURES:
        q = _sanitise_query_text(query)
        ranks_by_k: dict[int, int | None] = {}
        sent = ""
        for kk in (PRIMARY_K, 15):
            if recovery:
                sent = recovery_query(q)
                out = _fts_cypher(proj.g, sent, kk)
            else:
                sent = build_or_query(q)
                # ⛔ The fixture rows are requirement 5 — the DoD's correctness
                # gate and the lane's headline result — so the same degraded-
                # read refusal applies here: without it, a breaker trip during
                # THIS loop publishes "the shipped leg cannot reach this name"
                # for a read that never ran.
                trace = []
                out = run_fts_query(proj.g, q, entity_type="object", limit=kk,
                                    leg_trace=trace)
                _refuse_degraded_legs(trace, q)
            ids = [pid for pid, _s in out]
            ranks_by_k[kk] = ids.index(fid) + 1 if fid in ids else None
        fx.append({"id": fid, "stored": stored, "query": query, "gates": why,
                   "terms_sent": sent,
                   "rank": ranks_by_k[PRIMARY_K],
                   "rank_wide": ranks_by_k[15]})
    fixture = _fixture_summary(
        "falkordb_tfidf" + ("__tokens_fixed" if recovery else ""), fx)

    return _summarise("falkordb_tfidf" + ("__tokens_fixed" if recovery else ""),
                      per_k, ranks, by_class, top_scores,
                      rows, elapsed, n_q,
                      engine_note=("FalkorDB db.idx.fulltext.queryNodes "
                                   "(RediSearch TF-IDF scorer; index created "
                                   "with no OPTIONS)"
                                   + (" — recovery arm: `build_or_query` output "
                                      "PLUS the underscore-preserving runs as "
                                      "additive OR alternatives (NOT shipped)"
                                      if recovery else "")),
                      extra={"index_numDocuments": indexed,
                             "index_wait_s": waited_s,
                             "identifier_fixture": fixture})


def _wait_indexed(proj, expected: int, timeout_s: float = 60.0) -> tuple[int, float]:
    """Block until the Object fulltext index reports ``expected`` documents.

    Returns ``(numDocuments, waited_seconds)``. A timeout is a REFUSAL, never a
    proceed: measuring against a partially built index understates the leg, and
    an understated leg is indistinguishable from a real one in the artifact.
    """
    t0 = time.time()
    last = -1
    # ⛔ EQUALITY, not `>=` (#12349 review, P2). `>=` returns immediately on an
    # index that is TOO BIG, which is how the committed artifacts accepted a
    # 305-document index for a 300-document population without a word — the
    # body of that PR says an `==` "would have timed out and refused loudly",
    # and it is right. A fresh graph reaches exactly `len(rows)` (and exactly
    # `len(rows) + len(FIXTURES)` after the fixture inserts), so equality is
    # reachable on every path this harness takes.
    while time.time() - t0 < timeout_s:
        rows = proj.g.query("CALL db.indexes()").result_set
        for row in rows:
            # row shape (measured, v4.20.6): [label, fields, types, options,
            # language, stopwords, entity_type, status, stats…] where the dict
            # carrying ``numDocuments`` is the indexer's own stats. Find it by
            # KEY rather than by position — a positional read is how a
            # version bump turns this guard into a silent no-op. ``next()``
            # yields the FIRST such dict, which is what this row shape gives
            # (measured); an earlier comment said LAST and contradicted the code.
            if not row or row[0] != "Object":
                continue
            stats = next((c for c in row
                          if isinstance(c, dict) and "numDocuments" in c), None)
            last = int(stats["numDocuments"]) if stats else -1
            break
        if last == expected:
            return last, round(time.time() - t0, 2)
        time.sleep(0.2)
    raise SystemExit(
        f"REFUSED: the Object fulltext index reports {last} documents of "
        f"exactly {expected} after {timeout_s}s — a query against a partially "
        f"built index books present names as misses, and an OVER-count means "
        f"the graph is not the one this run wrote.")


# ── the Postgres arm ────────────────────────────────────────────────────────

def arm_postgres(rows: list[dict], dsn: str, ks: tuple[int, ...]) -> dict:
    import psycopg2

    from tortoise.extractor_v2 import _sanitise_query_text
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    cur.execute("DROP TABLE IF EXISTS m3_objects")
    cur.execute("CREATE TABLE m3_objects (id text PRIMARY KEY, name text NOT NULL)")
    from psycopg2.extras import execute_values
    execute_values(cur, "INSERT INTO m3_objects (id, name) VALUES %s",
                   [(r["id"], r["name"]) for r in rows], page_size=500)
    cur.execute("CREATE INDEX m3_objects_bm25 ON m3_objects USING bm25 (id, name)")
    # ANALYZE is not optional: a BM25 index that has never been analysed has no
    # document-frequency statistics, and IDF is exactly what makes BM25 BM25.
    cur.execute("ANALYZE m3_objects")
    cur.execute("SELECT count(*) FROM m3_objects")
    n_docs = cur.fetchone()[0]
    if n_docs != len(rows):
        raise SystemExit(f"REFUSED: {n_docs} rows landed, expected {len(rows)}")
    cur.execute("SELECT extversion FROM pg_extension WHERE extname='pg_search'")
    extver = cur.fetchone()[0]
    cur.execute("SELECT version()")
    pgver = cur.fetchone()[0]

    def _run(mode: str) -> dict:
        per_k = {k: {"in_window": 0, "rank_sum": 0, "rows_zero": 0} for k in ks}
        ranks: dict[str, int | None] = {}
        top_scores: list[float] = []
        by_class = collections.defaultdict(
            lambda: {"n": 0, **{str(k): 0 for k in ks}})
        degenerate_ids: set[str] = set()
        t0 = time.time()
        n_q = 0
        for row in rows:
            q = _sanitise_query_text(row["name"])
            if mode == "tokens":
                terms, degenerate = arm_terms(q)
                if degenerate:
                    degenerate_ids.add(row["id"])
            else:
                terms = q
            by_class[row["cls"]]["n"] += 1
            found_at = None
            for k in ks:
                cur.execute(
                    # ⛔ The tie rule is part of the measurement, not a detail
                    # (#12186 round 3, requirement 6): the shipped leg reads
                    # through ``recency_tie_clause``, whose id key is DESC on
                    # EVERY label, so `id DESC` here breaks a score tie the same
                    # way. `id` ASC — the first version — broke them in the
                    # OPPOSITE direction, and the whole k=3 result lives at the
                    # window boundary.
                    "SELECT id, paradedb.score(id) AS s FROM m3_objects "
                    "WHERE name ||| %s ORDER BY s DESC, id DESC LIMIT %s",
                    (terms, k))
                out = cur.fetchall()
                n_q += 1
                if not out:
                    per_k[k]["rows_zero"] += 1
                ids = [r[0] for r in out]
                if out:
                    top_scores.append(float(out[0][1]))
                rank = ids.index(row["id"]) + 1 if row["id"] in ids else None
                if rank is not None:
                    per_k[k]["in_window"] += 1
                    per_k[k]["rank_sum"] += rank
                    by_class[row["cls"]][str(k)] += 1
                    if found_at is None:
                        found_at = rank
            ranks[row["id"]] = found_at
        elapsed = time.time() - t0
        out = _summarise(f"postgres_bm25/{mode}", per_k, ranks, by_class,
                         top_scores, rows, elapsed, n_q,
                         # ``server_version`` already renders as
                         # "PostgreSQL 16.15 (Debian …) on …" — prefixing it with
                         # another "PostgreSQL" produced "PostgreSQL PostgreSQL
                         # 16.15", which the committed artifacts carry (#12349
                         # review, P2) and which a reader quotes as the engine.
                         engine_note=(f"{pgver.split(' on ')[0]}, "
                                      f"ParadeDB pg_search {extver}, "
                                      f"USING bm25 (id, name); query "
                                      f"`name ||| <terms>` with "
                                      f"ORDER BY paradedb.score(id) DESC, "
                                      f"id DESC LIMIT k"))
        if degenerate_ids:
            # The declared break in the control, recorded as DATA so a reader
            # need not take the prose's word for it.
            out["degenerate_ids"] = sorted(degenerate_ids)
            out["degenerate_n"] = len(degenerate_ids)
        return out

    out = {"tokens": _run("tokens"), "native": _run("native")}

    # ── the #8933 identifier fixture (req 5), after both sweeps.
    execute_values(cur, "INSERT INTO m3_objects (id, name) VALUES %s",
                   [(f[0], f[1]) for f in FIXTURES], page_size=500)
    cur.execute("ANALYZE m3_objects")
    for mode in ("tokens", "native"):
        fx: list[dict] = []
        for fid, stored, query, why in FIXTURES:
            q = _sanitise_query_text(query)
            terms, _deg = arm_terms(q) if mode == "tokens" else (q, False)
            ranks_by_k: dict[int, int | None] = {}
            for kk in (PRIMARY_K, 15):
                cur.execute(
                    "SELECT id, paradedb.score(id) AS s FROM m3_objects "
                    "WHERE name ||| %s ORDER BY s DESC, id DESC LIMIT %s",
                    (terms, kk))
                ids = [r[0] for r in cur.fetchall()]
                ranks_by_k[kk] = ids.index(fid) + 1 if fid in ids else None
            fx.append({"id": fid, "stored": stored, "query": query,
                       "gates": why, "terms_sent": terms,
                       "rank": ranks_by_k[PRIMARY_K],
                       "rank_wide": ranks_by_k[15]})
        out[mode]["identifier_fixture"] = _fixture_summary(
            f"postgres_bm25/{mode}", fx)
    conn.close()
    return out


# ── summarising and the paired test ─────────────────────────────────────────

def _summarise(name: str, per_k: dict, ranks: dict, by_class: dict,
               top_scores: list[float], rows: list[dict], elapsed: float,
               n_q: int, *, engine_note: str, extra: dict | None = None) -> dict:
    n = len(rows)
    table = {}
    for k, d in per_k.items():
        found = sorted(r for r in ranks.values() if r is not None and r <= k)
        table[str(k)] = {
            "recall": round(d["in_window"] / n, 4) if n else 0.0,
            "in_window": d["in_window"],
            "n": n,
            "mean_rank_when_found": (round(statistics.fmean(found), 3)
                                     if found else None),
            "rows_zero": d["rows_zero"],
        }
    dist = {}
    if top_scores:
        s = sorted(top_scores)
        p = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
        dist = {
            "min": round(s[0], 6),
            "p50": round(statistics.median(s), 6),
            "p90": round(p(0.90), 6),
            "max": round(s[-1], 6),
            "distinct_rounded": len({round(x, 3) for x in s}),
            "n": len(s),
        }
    return {
        "arm": name,
        "engine": engine_note,
        "queries": n_q,
        "elapsed_s": round(elapsed, 1),
        "ms_per_query": round(1000.0 * elapsed / n_q, 2) if n_q else None,
        "recall_by_k": table,
        "per_class": {c: {"n": d["n"], **{k: round(d[k] / d["n"], 4)
                                          for k in d if k != "n"}}
                      for c, d in sorted(by_class.items())},
        # M3's DoD names score DISTRIBUTIONS as well as ranks; they are
        # recorded because a recall tie with a different score distribution is
        # not the same leg (it is what a later `threshold` caller would see).
        "top1_score_distribution": dist,
        "ranks": ranks,
        **(extra or {}),
    }


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p for k successes of n (p=0.5), McNemar-style."""
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return min(1.0, 2.0 * tail)


def compare(a: dict, b: dict, ks: tuple[int, ...]) -> dict:
    """Paired comparison over the SAME population (same ids, same order)."""
    ra, rb = a["ranks"], b["ranks"]
    out: dict = {
        "a": a["arm"], "b": b["arm"],
        "paired": True,
        "population_ids_identical": sorted(ra) == sorted(rb),
    }
    for k in ks:
        a_found = {i for i, r in ra.items() if r is not None and r <= k}
        b_found = {i for i, r in rb.items() if r is not None and r <= k}
        both = len(a_found & b_found)
        only_b = len(b_found - a_found)   # b gained
        only_a = len(a_found - b_found)   # b lost
        n_disc = only_a + only_b
        out[f"k{k}"] = {
            "a_recall": round(len(a_found) / len(ra), 4) if ra else 0.0,
            "b_recall": round(len(b_found) / len(rb), 4) if rb else 0.0,
            "delta_pts": round(100.0 * (len(b_found) - len(a_found)) / len(ra), 2)
            if ra else 0.0,
            "both": both, "only_a": only_a, "only_b": only_b,
            "discordant": n_disc,
            "mcnemar_p_two_sided": round(_binom_two_sided(only_b, n_disc), 5),
        }
    return out


# ── the run ─────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> int:
    if args.arm == "falkordb":
        uri = os.environ.get("TORTOISE_DB_URI") or ""
        if not uri.startswith(("docker://", "redis://")):
            print("REFUSED: no live FalkorDB in TORTOISE_DB_URI — the leg under "
                  "measurement is the in-graph index, and a figure measured on "
                  "another backend is not a figure.\n"
                  "  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"",
                  file=sys.stderr)
            return 3

    pop_file = Path(args.population_file)
    pop = acquire(pop_file, args.population, args.sample, args.sessions_dir)
    rows = pop["names"]
    print(f"population: {len(rows)} names (digest {pop['digest']}, "
          f"shares {pop['shares']}) from {pop_file}")
    # ALL rows, not a sample: the check is a pure function over the population
    # and the degenerate QUERIES are the ones that matter (they are where the
    # control breaks — see `arm_terms`), so a head-50 sample could miss them.
    terms_census = assert_terms_match([_sanitise(r["name"]) for r in rows])

    ks = tuple(int(x) for x in args.ks.split(","))
    if PRIMARY_K not in ks:
        raise SystemExit(f"REFUSED: --ks must include the primary k={PRIMARY_K}")

    art: dict = {
        "issue": "#12298",
        "measure": "M3 — FTS leg recall@k in isolation (SEARCH-ARCHITECTURE §6.2)",
        "decision_rule": DECISION_RULE,
        "primary_k": PRIMARY_K,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # REPO-RELATIVE, never the authoring checkout's absolute path: the
        # committed artifacts carried `/home/cmux/tortoise/.worktrees/…`, which
        # is a path on one box and a leak of it (#12349 review, P2).
        "population_file": _repo_relative(pop_file),
        "population_digest": pop["digest"],
        "sample": len(rows),
        "ks": list(ks),
        "terms_census": terms_census,
        # #12186 round 3 req 4 — the single-leg floor arm. No `threshold` is
        # passed to EITHER arm: this harness calls the FTS leg directly, so the
        # floor that lives in the SDK's single-leg arm never applies. The
        # shipped default is 0.0 (filter off), so on this axis both arms are the
        # shipped configuration; recorded rather than assumed.
        "threshold_arm": {
            "applied": False,
            "shipped_default": 0.0,
            "note": ("no threshold is passed on either arm — the harness calls "
                     "run_fts_query directly, so the SDK's single-leg floor "
                     "does not apply; equivalent to the shipped 0.0 (filter "
                     "off), and therefore not exercised"),
        },
        "arms": {},
        "comparisons": {},
    }
    if not args.graph:
        # RUN-SCOPED: a reused graph re-upserts into a live index, and a
        # re-index window can book a present name as a miss.
        args.graph = "m3_12298_" + time.strftime("%H%M%S", time.gmtime())
    art["falkordb_graph"] = args.graph

    if args.arm in ("falkordb", "both"):
        print("arm falkordb_tfidf …", flush=True)
        art["arms"]["falkordb_tfidf"] = arm_falkordb(
            rows, args.graph, args.namespace, ks)
        if args.recovery_arm:
            print("arm falkordb_tfidf__tokens_fixed …", flush=True)
            # ⛔ A SEPARATE graph, not `args.graph` (#12349 review, P1).
            # `arm_falkordb` inserts the five #8933 fixture rows at the END of
            # its own call, deliberately — but a second arm sharing the graph
            # inherits them, so its recall sweep ran over 305 documents while
            # the first ran over 300. The fixture-ordering invariant the
            # docstring promises is per-arm, and sharing a graph silently
            # breaks it for every arm after the first. The committed artifacts
            # were produced before this line existed and record the 305.
            art["arms"]["falkordb_tfidf__tokens_fixed"] = arm_falkordb(
                rows, args.graph + "__recovery", args.namespace, ks,
                recovery=True)
    if args.arm in ("postgres", "both"):
        print("arm postgres_bm25 …", flush=True)
        pg = arm_postgres(rows, args.pg_dsn, ks)
        art["arms"]["postgres_bm25/tokens"] = pg["tokens"]
        art["arms"]["postgres_bm25/native"] = pg["native"]

    if "falkordb_tfidf" in art["arms"] and "falkordb_tfidf__tokens_fixed" in art["arms"]:
        art["comparisons"][
            "falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"] = compare(
                art["arms"]["falkordb_tfidf"],
                art["arms"]["falkordb_tfidf__tokens_fixed"], ks)

    if "falkordb_tfidf" in art["arms"] and "postgres_bm25/tokens" in art["arms"]:
        base = art["arms"]["falkordb_tfidf"]
        for other in ("postgres_bm25/tokens", "postgres_bm25/native"):
            art["comparisons"][f"falkordb_tfidf__vs__{other}"] = compare(
                base, art["arms"][other], ks)
        # ⛔ THE TOKENS-CONTROLLED SUBSET. On a DEGENERATE query the two
        # Postgres arms are one arm (both send the raw string), so the control
        # is only real on the other queries. This comparison restricts BOTH arms
        # to those ids, and it is the number the brief quotes for `tokens`.
        deg = {r["id"] for r in rows if arm_terms(_sanitise(r["name"]))[1]}
        keep = {r["id"] for r in rows} - deg
        base_c = dict(base, ranks={i: v for i, v in base["ranks"].items()
                                   if i in keep})
        tok_c = dict(art["arms"]["postgres_bm25/tokens"],
                     ranks={i: v for i, v in
                            art["arms"]["postgres_bm25/tokens"]["ranks"].items()
                            if i in keep})
        art["comparisons"]["tokens_controlled_subset"] = compare(
            base_c, tok_c, ks)
        art["comparisons"]["tokens_controlled_subset"]["n"] = len(keep)
        art["comparisons"]["tokens_controlled_subset"]["excluded_degenerate"] = len(deg)

        primary = art["comparisons"][
            "falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
        passed = m3_passes(primary)
        art["verdict"] = {
            "rule": DECISION_RULE,
            "primary_arm": "postgres_bm25/tokens",
            "recall_at_3_base": primary["a_recall"],
            "recall_at_3_primary": primary["b_recall"],
            "mcnemar_p_two_sided": primary["mcnemar_p_two_sided"],
            "M3": "PASS" if passed else "FAIL",
        }
        # The DEPLOYABLE arm, reported beside the scorer control: this is the
        # falsifier's own question ("does a BM25-capable Postgres option beat
        # what we run"), and the two answers differ — which is the finding.
        dep = art["comparisons"]["falkordb_tfidf__vs__postgres_bm25/native"]["k3"]
        art["verdict"]["deployable_arm"] = {
            "recall_at_3_base": dep["a_recall"],
            "recall_at_3": dep["b_recall"],
            "delta_pts": dep["delta_pts"],
            "mcnemar_p_two_sided": dep["mcnemar_p_two_sided"],
        }
        if "falkordb_tfidf__tokens_fixed" in art["arms"]:
            rec = art["comparisons"][
                "falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"]
            art["verdict"]["recovery_arm_no_index_move"] = {
                "recall_at_3": rec["b_recall"], "delta_pts": rec["delta_pts"],
                "mcnemar_p_two_sided": rec["mcnemar_p_two_sided"],
            }
        cs = art["comparisons"]["tokens_controlled_subset"]["k3"]
        art["verdict"]["primary_arm_tokens_controlled_subset"] = {
            "n": art["comparisons"]["tokens_controlled_subset"]["n"],
            "excluded_degenerate": art["comparisons"][
                "tokens_controlled_subset"]["excluded_degenerate"],
            "recall_at_3_base": cs["a_recall"],
            "recall_at_3": cs["b_recall"],
            "delta_pts": cs["delta_pts"],
            "mcnemar_p_two_sided": cs["mcnemar_p_two_sided"],
        }
        art["verdict"]["requirements"] = {
            "3_recall_at_k": "measured (k=3,5,10,15)",
            "4_single_leg_floor_arm": "recorded in `threshold_arm` (not "
                                      "exercised — the harness bypasses it)",
            "5_identifier_fixture": {
                a: (d.get("identifier_fixture") or {}).get("PASS")
                for a, d in art["arms"].items()},
            "6_tie_rule": ("both stores read through ORDER BY score DESC, id "
                           "DESC — the shipped `recency_tie_clause` id key is "
                           "DESC on every label; `createdAt` is not comparable "
                           "across the two stores, so the recency component is "
                           "declared, not modelled"),
        }
        print(json.dumps(art["verdict"], indent=1))

    out = HERE / "artifact.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(art, indent=1, sort_keys=True) + "\n")
    print(f"wrote {out}")
    for name, arm in art["arms"].items():
        print(f"  {name:26s} " + "  ".join(
            f"@{k}={arm['recall_by_k'][str(k)]['recall']:.3f}"
            for k in ks) + f"   {arm['ms_per_query']}ms/q")
    return 0


def _sanitise(name: str) -> str:
    from tortoise.extractor_v2 import _sanitise_query_text
    return _sanitise_query_text(name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="measure both arms on the pinned population")
    r.add_argument("--arm", choices=("both", "falkordb", "postgres"),
                   default="both")
    r.add_argument("--population-file", default=str(HERE / "population.json"),
                   help="the PIN — created on first run, read afterwards")
    r.add_argument("--rebuild-population", action="store_true",
                   help="discard the pin and harvest again (breaks pairing)")
    r.add_argument("--population", type=int, default=1000)
    r.add_argument("--sample", type=int, default=300)
    r.add_argument("--sessions-dir", default="~/.tortoise/session-events")
    r.add_argument("--graph", default="")
    r.add_argument("--namespace", default="m3_12298")
    r.add_argument("--pg-dsn", default=os.environ.get("M3_PG_DSN", ""))
    r.add_argument("--ks", default=",".join(str(k) for k in KS))
    r.add_argument("--recovery-arm", action="store_true",
                   help="also measure the additive-token recovery arm on the "
                        "CURRENT index (the fix is measured, not shipped)")
    a = p.parse_args(argv)

    if getattr(a, "rebuild_population", False):
        Path(a.population_file).unlink(missing_ok=True)
    if a.arm in ("postgres", "both") and not a.pg_dsn:
        print("REFUSED: --pg-dsn (or M3_PG_DSN) is required for the Postgres "
              "arm — see this module's docstring for the pg_search container.",
              file=sys.stderr)
        return 2
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
