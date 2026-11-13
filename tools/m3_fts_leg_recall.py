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

**PRIMARY (pre-registered before the run): ``tokens``.** C2 is *"BM25 instead
of TF-IDF"* — a scorer change. The decision rule is stated in the artifact and
in ``docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`` before the
numbers exist, and it is not re-cut afterwards.

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
    uv run python tools/m3_fts_leg_recall.py run \\
        --pg-dsn 'host=127.0.0.1 port=16550 dbname=m3 user=postgres password=pg'

The artifact lands in ``docs/research/2026-11-13-12298-m3-fts-leg/``: one
``population.json`` (the pinned sample) and one ``artifact.json`` (both arms +
the paired comparison).

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

import argparse
import collections
import hashlib
import json
import math
import os
import re
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

KS = (3, 5, 10, 15)
HERE = REPO_ROOT / "docs" / "research" / "2026-11-13-12298-m3-fts-leg"

#: The falsifier's k — the width the product ships (``extractor_v2._fts_rows``
#: calls ``tortoise_fts_query`` with ``limit=3``).
PRIMARY_K = 3

#: Pre-registered decision rule. Copied VERBATIM into the artifact so a reader
#: can check the numbers against the rule that was fixed before them.
DECISION_RULE = (
    "PASS iff on the primary arm (postgres_bm25/tokens) recall@3 is strictly "
    "greater than falkordb_tfidf recall@3, AND the exact two-sided McNemar "
    "(binomial) test over the discordant pairs has p < 0.05. "
    "FAIL otherwise — including a tie, and including a gain carried by pairs "
    "where the two arms agree."
)


def _import_instrument():
    """The #11901 instrument, imported — never re-implemented.

    ``harvest``/``build_population``/``_allocate_shares``/``_interleave`` ARE
    #11901's definitions of the population. A copy would let the two
    measurements drift, and the drift would be invisible in both artifacts.
    """
    from tools.candidate_recall_11901 import (  # noqa: PLC0415 — deliberate lazy import
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
    from tortoise.sparse import (  # noqa: PLC0415 — lazy, keeps import cheap
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


def assert_terms_match(queries: list[str]) -> None:
    """Fail closed unless the term list reproduces ``build_or_query`` byte-exactly."""
    from tortoise.sparse import (  # noqa: PLC0415
        build_or_query,
        escape_redisearch_literal,
    )
    bad: list[tuple[str, str, str]] = []
    for q in queries:
        terms, degenerate = production_terms(q)
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
    from tortoise.search_engine import (  # noqa: PLC0415
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


def verify_cypher_parity(graph, queries: list[str]) -> None:
    """Refuse unless :func:`_fts_cypher` reproduces ``run_fts_query`` exactly."""
    from tortoise.search_engine import run_fts_query  # noqa: PLC0415
    from tortoise.sparse import build_or_query  # noqa: PLC0415
    for q in queries:
        want = run_fts_query(graph, q, entity_type="object", limit=10)
        got = _fts_cypher(graph, build_or_query(q), 10)
        if [i for i, _ in want] != [i for i, _ in got]:
            raise SystemExit(
                "REFUSED: the recovery arm's Cypher no longer reproduces "
                f"run_fts_query for {q!r} — want {want[:3]}, got {got[:3]}. A "
                "replica that has drifted measures a different leg.")


def arm_falkordb(rows: list[dict], graph_name: str, namespace: str,
                 ks: tuple[int, ...], *, recovery: bool = False) -> dict:
    from tortoise.extractor_v2 import _sanitise_query_text  # noqa: PLC0415
    from tortoise.sdk import TortoiseSDK  # noqa: PLC0415
    from tortoise.search_engine import run_fts_query  # noqa: PLC0415
    from tortoise.sparse import build_or_query  # noqa: PLC0415

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
            built = build_or_query(q)
            extra = [t for t in recovery_terms(q) if t not in built.split("|")]
            or_string = "|".join([built] + extra) if extra else built
        by_class[row["cls"]]["n"] += 1
        found_at: int | None = None
        for k in ks:
            if recovery:
                out = _fts_cypher(proj.g, or_string, k)
            else:
                out = run_fts_query(proj.g, q, entity_type="object", limit=k)
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
                             "index_wait_s": waited_s})


def _wait_indexed(proj, expected: int, timeout_s: float = 60.0) -> tuple[int, float]:
    """Block until the Object fulltext index reports ``expected`` documents.

    Returns ``(numDocuments, waited_seconds)``. A timeout is a REFUSAL, never a
    proceed: measuring against a partially built index understates the leg, and
    an understated leg is indistinguishable from a real one in the artifact.
    """
    t0 = time.time()
    last = -1
    while time.time() - t0 < timeout_s:
        rows = proj.g.query("CALL db.indexes()").result_set
        for row in rows:
            # row shape (measured, v4.20.6): [label, fields, types, options,
            # language, stopwords, entity_type, status, stats…] where the LAST
            # dict carrying ``numDocuments`` is the indexer's own stats. Find it
            # by key rather than by position — a positional read is how a
            # version bump turns this guard into a silent no-op.
            if not row or row[0] != "Object":
                continue
            stats = next((c for c in row
                          if isinstance(c, dict) and "numDocuments" in c), None)
            last = int(stats["numDocuments"]) if stats else -1
            break
        if last >= expected:
            return last, round(time.time() - t0, 2)
        time.sleep(0.2)
    raise SystemExit(
        f"REFUSED: the Object fulltext index reports {last} documents of "
        f"{expected} after {timeout_s}s — a query against a partially built "
        f"index books present names as misses.")


# ── the Postgres arm ────────────────────────────────────────────────────────

def arm_postgres(rows: list[dict], dsn: str, ks: tuple[int, ...]) -> dict:
    from tortoise.extractor_v2 import _sanitise_query_text  # noqa: PLC0415
    import psycopg2  # noqa: PLC0415
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    cur.execute("DROP TABLE IF EXISTS m3_objects")
    cur.execute("CREATE TABLE m3_objects (id text PRIMARY KEY, name text NOT NULL)")
    from psycopg2.extras import execute_values  # noqa: PLC0415
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
        t0 = time.time()
        n_q = 0
        for row in rows:
            q = _sanitise_query_text(row["name"])
            if mode == "tokens":
                # Degenerate queries take the RAW string on both sides: the
                # production leg sends it backslash-escaped, which Postgres'
                # analyser would read as literal punctuation — the SEMANTICS
                # ("match this text literally") is what is held constant here.
                tlist, degenerate = production_terms(q)
                terms = q if degenerate else " ".join(tlist)
            else:
                terms = q
            by_class[row["cls"]]["n"] += 1
            found_at = None
            for k in ks:
                cur.execute(
                    "SELECT id, paradedb.score(id) AS s FROM m3_objects "
                    "WHERE name ||| %s ORDER BY s DESC, id LIMIT %s",
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
        return _summarise(f"postgres_bm25/{mode}", per_k, ranks, by_class,
                          top_scores, rows, elapsed, n_q,
                          engine_note=(f"PostgreSQL {pgver.split(' on ')[0]}, "
                                       f"ParadeDB pg_search {extver}, "
                                       f"USING bm25 (id, name); query "
                                       f"`name ||| {terms!r}`"[:400]))

    out = {"tokens": _run("tokens"), "native": _run("native")}
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
    assert_terms_match([_sanitise(r["name"]) for r in rows[:50]])

    ks = tuple(int(x) for x in args.ks.split(","))
    if PRIMARY_K not in ks:
        raise SystemExit(f"REFUSED: --ks must include the primary k={PRIMARY_K}")

    art: dict = {
        "issue": "#12298",
        "measure": "M3 — FTS leg recall@k in isolation (SEARCH-ARCHITECTURE §6.2)",
        "decision_rule": DECISION_RULE,
        "primary_k": PRIMARY_K,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "population_file": str(pop_file),
        "population_digest": pop["digest"],
        "sample": len(rows),
        "ks": list(ks),
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
            art["arms"]["falkordb_tfidf__tokens_fixed"] = arm_falkordb(
                rows, args.graph, args.namespace, ks, recovery=True)
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
        primary = art["comparisons"][
            f"falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
        passed = (primary["b_recall"] > primary["a_recall"]
                  and primary["mcnemar_p_two_sided"] < 0.05)
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
    from tortoise.extractor_v2 import _sanitise_query_text  # noqa: PLC0415
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
