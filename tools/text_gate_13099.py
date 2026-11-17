#!/usr/bin/env python3
"""#13099 — re-measure the text-index quality gate.

WHAT THIS ANSWERS
-----------------
`docs/architecture/SEARCH-ARCHITECTURE.md` §6.2 keeps the text index in the
graph because of a recorded number: *"`ts_rank`/`ts_rank_cd` measured 8–23
nDCG@10 behind BM25, and no comparable-quality Postgres option exists yet."*
That figure is **third-party** (the doc's own PROVENANCE paragraph says so) and
was measured against a BM25 implementation, **not** against our hosted
`db.idx.fulltext` leg — the doc says that leg's distance from the baseline is
*unmeasured*. #13099 is the stop condition for the Cypher→Postgres migration
(#13082 phase 2): a Postgres option within noise of the shipped leg means the
text index can move; nothing within noise means it stays, with a number.

This tool measures four lexical candidates on ONE fixed corpus with ONE fixed
query set and the committed relevance function, and reports
**nDCG@10 / nDCG@100 / recall@100 per query class** plus a **paired 90 %
bootstrap CI** on the delta against the in-graph baseline.

THE CORPUS IS REUSED, NOT REBUILT (#13099 task 1)
------------------------------------------------
* corpus    — ``benchmarks.synthetic_corpus.generate_oracle_points``, seed 42,
              2000 points, 24 latent topics.
* queries   — ``tests/eval/retrieval/queries/oracle_queries.json``, the 100
              committed oracle queries, **byte-identical**.
* judgements — ``benchmarks.synthetic_corpus.oracle_grades_for_query``: grade 2
              = target topic, 1 = NEAR (bridge-sharing), 0 = rest. Derived from
              the seed; no judges, no human labels, nothing invented here.

This is the same corpus the earlier nDCG work (#1144) used, which is what makes
the figures comparable to the recorded ones. A fresh corpus would make the
comparison meaningless and is the most likely way to get a wrong answer.

⚠️ **The stored anchor is not quoted — it is re-measured.** The committed
baseline (``tests/eval/retrieval/baseline/baseline-embedded-model-2026-11-08.json``)
carries ``fts nDCG@10 = 0.8143`` **and its own notes retract that column**:
*"FTS/structural columns on embedded runs are measurement-environment artifacts,
not quality statements — Docker FalkorDB >= 4.x is authoritative"*, with FTS
populated on only 40/100 queries. The baseline here is measured on a **real
Docker FalkorDB lane**, which is the authoritative environment that note names.

THE THREE QUERY CLASSES (#13099 task 2)
--------------------------------------
The committed grading function depends only on the **target topic**, never on
the query text. That is what lets all three classes reuse the *committed*
judgements unchanged:

``short_keyword``            the 100 committed queries, byte-identical (anchor).
``natural_language_question`` the same tokens wrapped in question scaffolding.
``entity_name``              a stored document's own text used verbatim — the
                             "user types the name they know" surface.

⚠️ **Stated openly rather than buried:** the committed set holds only
short-keyword shapes, and the repo has **no committed qrels** for the other two
(the 50 authored queries are ``labels_pending`` — they need two cross-vendor
LLM judges plus owner adjudication). Classes 2 and 3 are therefore derived
**deterministically over the same corpus with the same grade function**, and
they are a **proxy** for their shape, not a human-judged sample. No label is
invented here, human or LLM.

``natural_language_question`` adds only stopword/scaffold tokens around the
query's own content tokens. That is deliberate: it isolates exactly what a
dictionary or a tokenizer decides (whether the scaffolding is stripped or
matched), which is the axis the candidates differ on.

THE ARMS (#13099 task 3)
------------------------
``in_graph_fulltext``   ``db.idx.fulltext.queryNodes`` (RediSearch), queried
                        through the product's real ``sparse.build_or_query``.
``in_graph_fulltext_raw``  the same leg with the RAW query string — isolates
                        the builder/tokenizer from the scorer. (This is the
                        decomposition #12298 needed: a 25-point gap that was the
                        query builder, not the scorer.)
``pg_ts_rank``          Postgres ``ts_rank`` over a weighted ``tsvector``.
``pg_ts_rank_cd``       ``ts_rank_cd`` (cover-density) on the same index.
``pg_ts_rank_simple``   ``ts_rank`` with the ``simple`` dictionary — records
                        dictionary sensitivity instead of hiding it.
``pg_bm25``             ParadeDB ``pg_search`` 0.26.6 native BM25 index.
``hybrid_baseline``     RRF(in-graph fulltext, pgvector) — the fused baseline.
``hybrid_bm25``         RRF(pg BM25, pgvector).

**The vector leg is held CONSTANT across the two hybrid arms** (same pgvector
query, same embeddings, same k), so the delta between them is attributable to
the lexical swap alone, not to two legs moving at once. That is the controlled
comparison the decision needs: §6.2 already accepted the vectors moving (#11642);
the open question is only whether the text leg can follow.

A candidate that cannot be installed in our deployment is recorded as a
**disqualification, not a low score** — it is never scored. This is implemented,
not merely declared: any arm that raises during a run is counted in the class's
`arm_errors` block, `verdict()` marks it `disqualified` instead of reading an
empty result list as a quality loss, it is excluded from
`qualifying_candidates`, and `main()` exits non-zero so a run with a failed arm
cannot be published as a clean one. (The pre-implementation shape was the wrong
failure direction: an arm that never ran scored 0.0 on every query, read as
*"significantly worse"*, and would have produced the decision text "the text
index stays" for a candidate nobody managed to install.)

FAIRNESS RULES (declared, so the result is auditable)
-----------------------------------------------------
* Both lexical arms apply the SAME product filter (``status <> 'retracted'``
  and not an operator point), on both backends. The graders only label live
  non-operator points, so this removes grade-0 noise from both sides equally.
* Every Postgres arm uses OR semantics over the query's tokens, matching
  ``build_or_query``'s OR-union. An AND-semantics pg arm would lose recall for
  a reason that is not the scorer's.
* No per-customer tuning. The ts_rank configuration is ONE declared setting
  (``english`` dictionary, content weighted ``A``) plus one declared variant
  (``simple``); the ``simple`` row exists to show the dictionary's effect, not
  to pick a winner post-hoc.

USAGE
-----
::

    # both backends up (the lane container and the paradedb container):
    uv run python tools/text_gate_13099.py run \
        --fg-host 127.0.0.1 --fg-port <lane port> \
        --pg-dsn 'postgresql://postgres:s3cret@127.0.0.1:15499/tg13099' \
        --out docs/evidence/13099-text-gate/artifact.json
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE every other import — a module-level
# 3.11+-only import would raise ImportError first, so a later guard could never
# run. Pinned by tests/test_entry_point_python_guard.py.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/text_gate_13099.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        "`uv run python tools/text_gate_13099.py`"
    )

import argparse
import hashlib
import json
import random
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

N_POINTS = 2000
SEED = 42
LIMIT = 100          # nDCG@100 / recall@100 need a 100-deep list
GRAPH_NAME = "tg13099"
# The docker memory probe's bound. A wedged daemon must cost a RECORDED
# `mem["error"]`, never the run — and never a test that calls `seed_graph`.
PROBE_TIMEOUT_S = 10
RRF_K = 60           # search_engine.rrf_fusion's default (Cormack et al. 2009)
BOOTSTRAP_N = 2000
ALPHA = 0.10         # 90% CI, the #1144 pre-registered level

QUERY_CLASSES = ("short_keyword", "natural_language_question", "entity_name")

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")

# Query texts the product tokenizer could not price this run, and WHY. Emptied at
# the start of `run()` and published as the artifact's `tokenizer_fallbacks`; a
# non-empty list means those queries were built with the regex fallback instead of
# the shared product tokenizer, which the record must say rather than hide.
TOKENIZER_FALLBACKS: list[dict[str, Any]] = []


# ── corpus + queries (reused, never rebuilt) ────────────────────────────────

def build_corpus() -> tuple[Any, list[dict], dict[int, list[str]]]:
    """The #1144 deterministic corpus, exactly as committed."""
    from benchmarks.synthetic_corpus import build_topic_oracle, generate_oracle_points

    oracle = build_topic_oracle(seed=SEED)
    points, _counts = generate_oracle_points(N_POINTS, oracle, seed=SEED)
    live_by_topic: dict[int, list[str]] = {}
    for p in points:
        if p["status"] == "live" and not p["is_operator"]:
            live_by_topic.setdefault(p["topic"], []).append(p["id"])
    return oracle, points, live_by_topic


def load_committed_queries() -> list[dict]:
    path = REPO / "tests" / "eval" / "retrieval" / "queries" / "oracle_queries.json"
    return json.loads(path.read_text())["queries"]


def committed_queries_digest() -> str:
    """SHA-256 of the committed query FILE, so the figures can be tied to it.

    The comparability of every published number rests on the committed query set
    being the one that produced it, and a path string does not establish that.
    `tests/test_text_gate_13099.py` pins this digest as a constant. The artifact
    records it on a RUN (`run()` writes `corpus.queries_sha256`); the two committed
    artifacts predate that key and do not carry it — the record states which keys
    are absent rather than implying the digest is readable from the artifact.
    """
    path = REPO / "tests" / "eval" / "retrieval" / "queries" / "oracle_queries.json"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_query_classes(oracle: Any, points: list[dict],
                        committed: list[dict]) -> dict[str, list[dict]]:
    """Three classes, one grade function.

    Returns {class: [{qid, text, target, shape}]}. `target` is the oracle
    topic the committed grading function keys on.
    """
    out: dict[str, list[dict]] = {c: [] for c in QUERY_CLASSES}

    # 1. short_keyword — byte-identical to the committed set.
    for q in committed:
        out["short_keyword"].append({
            "qid": q["id"], "text": q["query"],
            "target": q["oracle_target"], "shape": q.get("tier"),
        })

    # 2. natural_language_question — same content tokens, question scaffolding.
    #    Deterministic: the template is chosen by a seeded RNG walked in sorted
    #    query order, so the set is reproducible byte-for-byte.
    heads = ("how does", "what is the role of", "why does", "how should we reason about")
    tails = ("affect this design", "matter for the system",
             "change the outcome", "behave under load")
    rng = random.Random(SEED ^ 0x13099)
    for q in sorted(committed, key=lambda x: x["id"]):
        head = heads[rng.randrange(len(heads))]
        tail = tails[rng.randrange(len(tails))]
        out["natural_language_question"].append({
            "qid": q["id"].replace("-", "-nlq-"), "text": f"{head} {q['query']} {tail}",
            "target": q["oracle_target"], "shape": q.get("tier"),
        })

    # 3. entity_name — a stored document's own text, used verbatim.
    #    Four per topic, chosen by sorted id so the choice is a function of the
    #    corpus alone. n per topic (not 1) because this is the class with the
    #    smallest per-query variance and therefore the one that needs power:
    #    a single-query-per-topic sample would carry a ±10-point CI.
    for topic in sorted({p["topic"] for p in points}):
        ids = sorted(live_by_topic_ids(points, topic))[:4]
        by_id = {p["id"]: p for p in points}
        for j, pid in enumerate(ids):
            out["entity_name"].append({
                "qid": f"ent-{topic:02d}-{j}", "text": by_id[pid]["content"],
                "target": topic, "shape": "verbatim_stored_text",
                "doc_id": pid,
            })
    return out


def oov_evidence(points: list[dict], classes: dict[str, list[dict]]) -> dict:
    """Measured reason the question class cannot discriminate here.

    A natural-language question's scaffolding words do not occur in the corpus,
    and NO candidate prices an unmatched query term (no coordination-level
    penalty). Rather than assert that, count it: for each derived question, how
    many of its tokens are absent from the corpus token pool.
    """
    pool: set[str] = set()
    for p in points:
        pool.update(t for t in _TOKEN_RE.findall(str(p["content"]).lower()) if t)

    def tokens(text: str) -> set[str]:
        return {t for t in _TOKEN_RE.findall(text.lower()) if t}

    committed = {q["qid"]: tokens(q["text"]) for q in classes["short_keyword"]}
    added_total = added_oov = 0
    examples: list[str] = []
    for q in classes["natural_language_question"]:
        base_id = q["qid"].replace("-nlq-", "-")
        added = tokens(q["text"]) - committed.get(base_id, set())
        oov = {t for t in added if t not in pool}
        added_total += len(added)
        added_oov += len(oov)
        if len(examples) < 3:
            examples.append(f"{sorted(added)} -> oov {sorted(oov)}")
    return {
        "added_scaffold_tokens": added_total,
        "added_scaffold_tokens_out_of_vocabulary": added_oov,
        "oov_fraction": (round(added_oov / added_total, 4) if added_total else None),
        "examples": examples,
        "reading": (
            "The question class adds only out-of-vocabulary scaffolding, and no "
            "candidate prices an unmatched query term, so the four POSTGRES "
            "LEXICAL arms return the keyword class's numbers exactly. It is "
            "reported for completeness and must NOT be read as independent "
            "evidence either way. ⛔ Not every arm duplicates it: the two RRF "
            "hybrids move by the amount the question's own wording shifts the "
            "vector leg, and the raw diagnostic returns zero rows for question "
            "text (README §4/§5.3)."),
    }


def live_by_topic_ids(points: list[dict], topic: int) -> list[str]:
    return [p["id"] for p in points
            if p["topic"] == topic and p["status"] == "live" and not p["is_operator"]]


def labels_for(oracle: Any, live_by_topic: dict[int, list[str]],
               target: int) -> dict[str, int]:
    """The committed deterministic grade function, unchanged (#1144)."""
    from benchmarks.synthetic_corpus import oracle_grades_for_query

    return oracle_grades_for_query(oracle, target, live_by_topic)


# ── seeding ─────────────────────────────────────────────────────────────────

def seed_graph(fg_host: str, fg_port: int, points: list[dict]) -> dict:
    from falkordb import FalkorDB

    from benchmarks.synthetic_corpus import seed_corpus
    from tortoise.projection import (
        _bounded_server_socket_kwargs,
        is_missing_graph_error,
    )

    # #3065/#8118: a measurement tool is not exempt from the read bound. An
    # unbounded socket is the defect this repo already carries a number for —
    # a stalled engine parks the caller in `recv()` at 0% CPU with no error,
    # which a run would then record as "no rows" rather than "no answer".
    # The bound comes from the product's ONE resolution point, so the two
    # `TORTOISE_DB_SOCKET_*` knobs reach this client too.
    db = FalkorDB(host=fg_host, port=fg_port, **_bounded_server_socket_kwargs())
    g = db.select_graph(GRAPH_NAME)
    # A fresh graph has nothing to delete, so an ABSENT graph is not a reason to
    # abort the seed — and that is the ONLY class excused here. ⛔ NOT
    # `contextlib.suppress(Exception)`: this client is now BOUNDED (above), so a
    # stalled engine raises `redis.TimeoutError` at the DROP instead of parking
    # forever, and a blanket suppress would swallow exactly that, seed over the
    # PREVIOUS run's points — `GRAPH_NAME` is fixed, not per-run, and
    # `seed_corpus` MERGEs by id — and then print a verdict for a population
    # nothing reset. That is the "records `no rows` rather than `no answer`"
    # failure this bound exists to prevent, so a timeout MUST abort the seed.
    # `is_missing_graph_error` is the product's own classifier (#2163), so this
    # stays one definition of "the graph was never there" rather than a second
    # text match.
    try:
        g.delete()
    except Exception as exc:
        if not is_missing_graph_error(exc):
            raise
    t0 = time.perf_counter()
    n = seed_corpus(g, points)
    seed_s = time.perf_counter() - t0

    # The Point FULLTEXT index over the product's declared field set
    # (sparse.POINT_INDEX_TEXT_FIELDS). No OPTIONS — the shipped declaration
    # carries none, and adding any would tune the baseline.
    t0 = time.perf_counter()
    g.query("CALL db.idx.fulltext.createNodeIndex('Point', 'content', 'search_keys', 'quote')")
    index_s = time.perf_counter() - t0

    mem = None
    try:
        import subprocess
        # #3065/#8118, the same class as the client bound above: a docker
        # daemon that is wedged parks these calls too, and a MEMORY READING is
        # never a reason to lose a measurement run. `timeout=` turns that into
        # `TimeoutExpired`, which the `except` below RECORDS (`mem["error"]`)
        # instead of hanging the tool — and instead of hanging a test that
        # exercises this function.
        name = subprocess.run(
            ['docker', 'ps', '--filter', f'publish={fg_port}', '--format', '{{.Names}}'],
            capture_output=True, text=True, check=False,
            timeout=PROBE_TIMEOUT_S).stdout.strip().splitlines()
        if name:
            out = subprocess.run(
                ['docker', 'exec', name[0], 'redis-cli', 'INFO', 'memory'],
                capture_output=True, text=True, check=False,
                timeout=PROBE_TIMEOUT_S).stdout
            mem = {ln.split(':')[0]: ln.split(':', 1)[1]
                   for ln in out.splitlines()
                   if ln.startswith(('used_memory_human', 'used_memory_dataset',
                                     'used_memory_peak_human'))}
            mem['container'] = name[0]
    except Exception as exc:  # recorded, never silent
        mem = {"error": f"{type(exc).__name__}: {exc}"}
    return {"points": n, "seed_s": round(seed_s, 3),
            "fulltext_index_build_s": round(index_s, 3),
            "graph_memory_usage": mem}


def seed_postgres(dsn: str, points: list[dict]) -> dict:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS tg_docs CASCADE")
        cur.execute("""
            CREATE TABLE tg_docs (
                id          text PRIMARY KEY,
                content     text NOT NULL,
                status      text,
                is_operator boolean,
                embedding   vector(384),
                -- ONE declared ts_rank configuration: english dictionary,
                -- content weighted A. Declared, not tuned.
                tsv_en   tsvector GENERATED ALWAYS AS
                         (setweight(to_tsvector('english', coalesce(content,'')), 'A')) STORED,
                -- the declared VARIANT, to record dictionary sensitivity.
                tsv_simple tsvector GENERATED ALWAYS AS
                         (setweight(to_tsvector('simple', coalesce(content,'')), 'A')) STORED
            )""")
        t0 = time.perf_counter()
        with cur.copy("COPY tg_docs (id, content, status, is_operator, embedding) FROM STDIN") as cp:
            for p in points:
                cp.write_row((p["id"], p["content"], p["status"], p["is_operator"],
                              "[" + ",".join(f"{v:.8f}" for v in p["embedding"]) + "]"))
        copy_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        cur.execute("CREATE INDEX tg_docs_tsv_en ON tg_docs USING GIN (tsv_en)")
        cur.execute("CREATE INDEX tg_docs_tsv_simple ON tg_docs USING GIN (tsv_simple)")
        gin_s = time.perf_counter() - t0

        t0 = time.perf_counter()
        cur.execute("""CREATE INDEX tg_docs_bm25 ON tg_docs
                       USING bm25 (id, content) WITH (key_field='id')""")
        bm25_s = time.perf_counter() - t0
        cur.execute("ANALYZE tg_docs")

        sizes = {}
        for rel in ("tg_docs", "tg_docs_tsv_en", "tg_docs_tsv_simple", "tg_docs_bm25",
                    "tg_docs_pkey"):
            cur.execute("SELECT pg_total_relation_size(%s)", (rel,))
            sizes[rel] = cur.fetchone()[0]
        # `pg_total_relation_size` above INCLUDES the relation's indexes
        # (docs/ops/postgres-storage-measurement.md), so `tg_docs` is heap + TOAST
        # + every index on it, NOT the table's own bytes. Record the split
        # explicitly, in the SAME definitions the identities use:
        # `pg_total_relation_size = pg_table_size + pg_indexes_size`, so
        # `tg_docs_table` (heap + TOAST + FSM/VM) is exactly `tg_docs` minus the
        # indexes and can be compared store-to-store against FalkorDB's
        # `used_memory` (#13107). ⛔ NOT `pg_relation_size` (= the main fork only):
        # that is a SMALLER quantity, so recording it under this name would make
        # the field disagree with the figure derived from the totals the moment
        # the table TOASTs.
        cur.execute("SELECT pg_table_size('tg_docs')")
        sizes["tg_docs_table"] = cur.fetchone()[0]
        cur.execute("SELECT pg_indexes_size('tg_docs')")
        sizes["tg_docs_indexes"] = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='pg_search'")
        pg_search_ver = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        pgvector_ver = cur.fetchone()[0]
        cur.execute("SHOW shared_preload_libraries")
        preload = cur.fetchone()[0]
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]
    return {"rows": len(points), "copy_s": round(copy_s, 3),
            "gin_index_build_s": round(gin_s, 3), "bm25_index_build_s": round(bm25_s, 3),
            "sizes_bytes": sizes, "pg_search": pg_search_ver,
            "pgvector": pgvector_ver, "server_version": server_version,
            "shared_preload_libraries": preload}


# ── query builders ──────────────────────────────────────────────────────────

def or_tokens(text: str) -> list[str]:
    """Tokens for a Postgres OR query, from the product's own tokenizer.

    `sparse.tokenize_sparse_query` is the recorded ONE tokenizer both lexical
    stacks import; using it keeps the comparison about the scorer, not about
    two different ideas of what a token is.
    """
    from tortoise.sparse import tokenize_sparse_query

    reason = None
    try:
        toks = tokenize_sparse_query(text, keep_numeric=False)
    except Exception as exc:  # recorded, never silent — a fallback that quietly
        # swapped the tokenizer would make the run's own claim ("the comparison
        # is about the scorer, both stacks import ONE tokenizer") false with
        # nothing in the artifact saying so.
        toks = []
        reason = f"{type(exc).__name__}: {exc}"
    if not toks:
        if not any(e["text"] == text for e in TOKENIZER_FALLBACKS):
            # `or_tokens` is invoked once per LEXICAL ARM — four times per query
            # through three syntactic sites (`pg_ts_rank` runs for both `en` and
            # `simple`), so a raw append
            # would record one entry per call and make the published count a
            # multiple of the texts. Keyed by text: one entry per affected query.
            TOKENIZER_FALLBACKS.append(
                {"text": text, "reason": reason or "tokenizer returned no tokens"})
        toks = [t for t in _TOKEN_RE.findall(text.lower()) if t]
    # dedupe, preserve order
    seen: set[str] = set()
    return [t for t in toks if not (t in seen or seen.add(t))]


def tsquery_string(tokens: list[str]) -> str | None:
    """OR-joined tsquery, matching build_or_query's OR-union semantics."""
    safe = [t.replace("'", "") for t in tokens if t.replace("'", "")]
    return " | ".join(safe) if safe else None


def bm25_query_string(tokens: list[str]) -> str | None:
    """OR-joined tantivy query; each term quoted so `-`/`_` stay literal."""
    safe = [t.replace('"', "") for t in tokens if t.replace('"', "")]
    return " OR ".join(f'"{t}"' for t in safe) if safe else None


# ── arms ────────────────────────────────────────────────────────────────────

class Arms:
    def __init__(self, fg_host: str, fg_port: int, pg_dsn: str):
        import psycopg
        from falkordb import FalkorDB

        from tortoise.projection import _bounded_server_socket_kwargs

        # #3065/#8118 — same bound as `seed_graph` above: the arms read the
        # graph on the measurement path, so a stalled server must surface as a
        # bounded read error (recorded as `arm_errors` → `disqualified`), never
        # as an empty result set that scores 0.0 and reads as a quality loss.
        self.db = FalkorDB(host=fg_host, port=fg_port,
                           **_bounded_server_socket_kwargs())
        self.g = self.db.select_graph(GRAPH_NAME)
        self.pg = psycopg.connect(pg_dsn, autocommit=True)

    # -- graph -------------------------------------------------------------
    def graph_fts(self, text: str, *, use_builder: bool = True) -> list[tuple[str, float]]:
        from tortoise.sparse import build_or_query

        q = build_or_query(text) if use_builder else text
        rows = self.g.query(
            "CALL db.idx.fulltext.queryNodes('Point', $q) YIELD node, score "
            "WHERE node.status <> 'retracted' AND node.is_operator = false "
            "RETURN node.id AS id, score ORDER BY score DESC, id LIMIT $k",
            params={"q": q, "k": LIMIT},
        ).result_set
        return [(str(r[0]), float(r[1])) for r in rows]

    # -- postgres lexical --------------------------------------------------
    def _pg(self, sql: str, params: tuple) -> list[tuple[str, float]]:
        with self.pg.cursor() as cur:
            cur.execute(sql, params)
            return [(str(r[0]), float(r[1])) for r in cur.fetchall()]

    def pg_ts_rank(self, text: str, *, config: str = "en") -> list[tuple[str, float]]:
        qs = tsquery_string(or_tokens(text))
        if not qs:
            return []
        col = "tsv_en" if config == "en" else "tsv_simple"
        return self._pg(f"""
            SELECT id, ts_rank({col}, q) AS s
              FROM tg_docs, to_tsquery('{ 'english' if config=='en' else 'simple' }', %s) q
             WHERE {col} @@ q AND status <> 'retracted' AND is_operator = false
             ORDER BY s DESC, id LIMIT {LIMIT}""", (qs,))

    def pg_ts_rank_cd(self, text: str) -> list[tuple[str, float]]:
        qs = tsquery_string(or_tokens(text))
        if not qs:
            return []
        return self._pg(f"""
            SELECT id, ts_rank_cd(tsv_en, q) AS s
              FROM tg_docs, to_tsquery('english', %s) q
             WHERE tsv_en @@ q AND status <> 'retracted' AND is_operator = false
             ORDER BY s DESC, id LIMIT {LIMIT}""", (qs,))

    def pg_bm25(self, text: str) -> list[tuple[str, float]]:
        qs = bm25_query_string(or_tokens(text))
        if not qs:
            return []
        return self._pg(f"""
            SELECT id, paradedb.score(id) AS s
              FROM tg_docs
             WHERE content @@@ %s AND status <> 'retracted' AND is_operator = false
             ORDER BY s DESC, id LIMIT {LIMIT}""", (qs,))

    def pg_vector(self, vec: list[float]) -> list[tuple[str, float]]:
        lit = "[" + ",".join(f"{v:.8f}" for v in vec) + "]"
        return self._pg(f"""
            SELECT id, embedding <=> %s::vector AS s
              FROM tg_docs
             WHERE status <> 'retracted' AND is_operator = false
             ORDER BY s ASC, id LIMIT {LIMIT}""", (lit,))


# ── metrics ─────────────────────────────────────────────────────────────────

def score_list(ranked: list[tuple[str, float]]) -> list[str]:
    return [pid for pid, _ in ranked]


def per_query_metrics(ids: list[str], labels: dict[str, int]) -> dict[str, float]:
    from tests.eval.retrieval.metrics import ndcg_at_k, recall_at_k

    return {
        "ndcg@10": ndcg_at_k(ids, labels, k=10),
        "ndcg@100": ndcg_at_k(ids, labels, k=100),
        "recall@100": recall_at_k(ids, labels, k=100),
    }


def mean(xs: list[float]) -> float:
    return round(statistics.fmean(xs), 6) if xs else 0.0


def paired_ci(deltas_pts: list[float]) -> dict:
    from tests.eval.retrieval.bootstrap import paired_bootstrap_ci

    if not deltas_pts:
        return {"lower": 0.0, "upper": 0.0, "mean": 0.0, "n": 0, "level": "90%"}
    ci = paired_bootstrap_ci(deltas_pts, n_resamples=BOOTSTRAP_N, alpha=ALPHA,
                            rng=random.Random(SEED))
    d = ci.to_dict() if hasattr(ci, "to_dict") else {
        "lower": ci.lower, "upper": ci.upper, "mean": ci.mean, "n": ci.n}
    d["level"] = "90%"
    return d


# ── orchestration ───────────────────────────────────────────────────────────

ARM_NAMES = (
    "in_graph_fulltext",
    "in_graph_fulltext_raw",
    "pg_ts_rank",
    "pg_ts_rank_cd",
    "pg_ts_rank_simple",
    "pg_bm25",
    "hybrid_baseline",
    "hybrid_bm25",
)

BASELINE_ARM = "in_graph_fulltext"

# An arm built FROM other arms inherits their disqualification. Without this the
# rule is defeated one level up: `rrf_fusion([[], vlist])` succeeds, so a hybrid
# whose lexical leg never ran still scores — and can qualify — on half of its own
# definition, which is the failure the disqualification rule exists to prevent.
ARM_DEPENDS_ON: dict[str, tuple[str, ...]] = {
    "hybrid_baseline": (BASELINE_ARM, "pg_vector"),
    "hybrid_bm25": ("pg_bm25", "pg_vector"),
}


def run(fg_host: str, fg_port: int, pg_dsn: str, out_path: Path,
        reuse: bool = False, full: bool = False) -> dict:
    TOKENIZER_FALLBACKS.clear()   # this run's fallbacks only — never a previous run's
    oracle, points, live_by_topic = build_corpus()
    committed = load_committed_queries()
    classes = build_query_classes(oracle, points, committed)

    corpus_meta = {
        "source": "benchmarks/synthetic_corpus.generate_oracle_points",
        "seed": SEED, "n_points": N_POINTS, "n_topics": oracle.n,
        "queries_source": "tests/eval/retrieval/queries/oracle_queries.json",
        # The path above only NAMES the input; this ties the published figures to
        # the exact bytes that produced them (the class carrying the committed
        # qrels is that file, and a silently edited query set would otherwise
        # move every table in the README with nothing going red).
        "queries_sha256": committed_queries_digest(),
        "grades": "benchmarks/synthetic_corpus.oracle_grades_for_query",
        "live_by_topic_counts": {str(k): len(v) for k, v in sorted(live_by_topic.items())},
        "baseline_arm": "in_graph_fulltext",
        "anchor_note": (
            "The embedded baseline (fts nDCG@10 0.8143) is NOT quoted: its own "
            "notes mark that column an embedded-environment artifact "
            "(FTS populated 40/100 queries) and name Docker FalkorDB >= 4.x "
            "authoritative, which is where this baseline is measured."),
    }

    if not reuse:
        corpus_meta["graph_seed"] = seed_graph(fg_host, fg_port, points)
        corpus_meta["postgres_seed"] = seed_postgres(pg_dsn, points)
    else:
        corpus_meta["graph_seed"] = corpus_meta["postgres_seed"] = "reused"

    arms = Arms(fg_host, fg_port, pg_dsn)

    # Deterministic query vectors for the (constant) vector leg.
    vec_cache: dict[str, list[float]] = {}
    def qvec(text: str) -> list[float]:
        if text not in vec_cache:
            vec_cache[text] = oracle.query_vector_for(text, seed=SEED)
        return vec_cache[text]

    results: dict[str, dict[str, dict]] = {}
    errors: list[dict] = []
    worked: list[dict] = []

    for cls in QUERY_CLASSES:
        qs = classes[cls]
        per_arm: dict[str, dict[str, list[float]]] = {a: {} for a in ARM_NAMES}
        per_query_ids: dict[str, dict[str, list[str]]] = {a: {} for a in ARM_NAMES}
        pq_by_arm: dict[str, dict[str, dict[str, float]]] = {a: {} for a in ARM_NAMES}
        depths: dict[str, list[int]] = {a: [] for a in ARM_NAMES}
        arm_errors: dict[str, int] = {}

        for q in qs:
            labels = labels_for(oracle, live_by_topic, q["target"])
            text = q["text"]

            from tortoise.search_engine import rrf_fusion

            lists: dict[str, list[tuple[str, float]]] = {}
            attempts = {
                "in_graph_fulltext": lambda t=text: arms.graph_fts(t, use_builder=True),
                "in_graph_fulltext_raw": lambda t=text: arms.graph_fts(t, use_builder=False),
                "pg_ts_rank": lambda t=text: arms.pg_ts_rank(t, config="en"),
                "pg_ts_rank_cd": lambda t=text: arms.pg_ts_rank_cd(t),
                "pg_ts_rank_simple": lambda t=text: arms.pg_ts_rank(t, config="simple"),
                "pg_bm25": lambda t=text: arms.pg_bm25(t),
            }
            for name, fn in attempts.items():
                try:
                    lists[name] = fn()
                except Exception as exc:  # recorded, never silent
                    errors.append({"class": cls, "qid": q["qid"], "arm": name,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    arm_errors[name] = arm_errors.get(name, 0) + 1
                    lists[name] = []

            try:
                vlist = arms.pg_vector(qvec(text))
            except Exception as exc:  # recorded, never silent
                errors.append({"class": cls, "qid": q["qid"], "arm": "pg_vector",
                               "error": f"{type(exc).__name__}: {exc}"})
                arm_errors["pg_vector"] = arm_errors.get("pg_vector", 0) + 1
                vlist = []

            # Hybrids — RRF with the VECTOR LEG HELD CONSTANT, so the delta
            # between them is the lexical swap alone.
            for hname, lex in (("hybrid_baseline", lists["in_graph_fulltext"]),
                               ("hybrid_bm25", lists["pg_bm25"])):
                try:
                    fused = rrf_fusion([lex, vlist], k=RRF_K)
                    lists[hname] = sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))
                except Exception as exc:  # recorded, never silent
                    errors.append({"class": cls, "qid": q["qid"], "arm": hname,
                                   "error": f"{type(exc).__name__}: {exc}"})
                    arm_errors[hname] = arm_errors.get(hname, 0) + 1
                    lists[hname] = []

            for name in ARM_NAMES:
                ids = score_list(lists.get(name, []))[:LIMIT]
                per_query_ids[name][q["qid"]] = ids
                depths[name].append(len(ids))
                pq = per_query_metrics(ids, labels)
                pq_by_arm[name][q["qid"]] = {
                    k: round(v, 6) for k, v in pq.items()}
                for m, v in pq.items():
                    per_arm[name].setdefault(m, []).append(v)

            # WORKED EXAMPLE — the mechanism, recorded where it is generated so
            # the README's explanation is reproducible rather than folklore.
            # Captured only where a query has ONE target document (the entity_name
            # class): a keyword query's target is a whole topic, so recording a
            # "target rank" there would be a null dressed up as evidence.
            if q.get("doc_id") and not any(w["class"] == cls for w in worked):
                g_ids = score_list(lists.get("in_graph_fulltext", []))
                if g_ids and labels.get(g_ids[0]) != 2:
                    tgt = q.get("doc_id")
                    gmap = dict(lists.get("in_graph_fulltext", []))
                    p_ids = score_list(lists.get("pg_ts_rank", []))
                    worked.append({
                        "class": cls, "qid": q["qid"], "query": text,
                        "target_doc": tgt,
                        "in_graph_top1": {"id": g_ids[0],
                                          "score": round(gmap.get(g_ids[0], 0.0), 4),
                                          "grade": labels.get(g_ids[0])},
                        "in_graph_target": {
                            "rank": (g_ids.index(tgt) + 1) if tgt in g_ids else None,
                            "score": round(gmap[tgt], 4) if tgt in gmap else None,
                            "grade": labels.get(tgt) if tgt else None},
                        "pg_ts_rank_target_rank": (
                            p_ids.index(tgt) + 1 if tgt in p_ids else None),
                        "reading": (
                            "The in-graph scorer ranks a document matching FEWER query "
                            "terms above one matching ALL of them, because the rare-term "
                            "weight dominates coverage. That is a scoring property, not "
                            "a harness artifact: both documents are live and unfiltered."),
                    })

        # ARTIFACT GUARD: an arm that cannot return 100 rows has a lower
        # nDCG@100 ceiling than one that can, and that is a property of the
        # MATCH SET, not of ranking. Published so the delta is readable.
        depth_block = {
            a: {"mean_returned": round(statistics.fmean(depths[a]), 2) if depths[a] else 0,
                "min_returned": min(depths[a]) if depths[a] else 0,
                "queries_returning_lt_limit": sum(1 for d in depths[a] if d < LIMIT)}
            for a in ARM_NAMES
        }

        base = per_arm["in_graph_fulltext"]
        table: dict[str, Any] = {}
        for name in ARM_NAMES:
            row: dict[str, Any] = {
                "n_queries": len(qs),
                "metrics": {m: mean(v) for m, v in per_arm[name].items()},
            }
            if name != "in_graph_fulltext":
                row["delta_vs_baseline"] = {}
                for m in ("ndcg@10", "ndcg@100", "recall@100"):
                    deltas = [
                        (a - b) * 100.0
                        for a, b in zip(per_arm[name][m], base[m], strict=True)
                    ]
                    ci = paired_ci(deltas)
                    ci["within_noise"] = ci["lower"] <= 0.0 <= ci["upper"]
                    row["delta_vs_baseline"][m] = ci
            table[name] = row

        results[cls] = {
            "n_queries": len(qs),
            "arms": table,
            "result_depth": depth_block,
            "query_texts": [{k: q[k] for k in ("qid", "text", "target", "shape")} for q in qs],
            # Per-query METRICS are always kept: they are what makes the paired
            # CIs recomputable by a reader. The full id lists are opt-in
            # (`--full`), because they are ~4 MB and prove nothing the metrics
            # do not — the harness regenerates them exactly.
            "per_query_metrics": pq_by_arm,
            "per_query_ids": per_query_ids if full else "omitted (run with --full)",
            "empty_pairs": {a: sum(1 for v in per_query_ids[a].values() if not v)
                            for a in ARM_NAMES},
            # Arms that RAISED in this class, and how often. A non-zero count is
            # a DISQUALIFICATION, never a score: `verdict()` reads this block
            # before it reads any metric, so an uninstallable candidate cannot be
            # reported as "significantly worse than the baseline".
            "arm_errors": arm_errors,
        }

    artifact = {
        "schema_version": 1,
        "issue": "13099",
        "measured_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "limit": LIMIT, "rrf_k": RRF_K, "bootstrap_n": BOOTSTRAP_N, "alpha": ALPHA,
        "corpus": corpus_meta,
        "query_classes": {c: len(classes[c]) for c in QUERY_CLASSES},
        "question_class_evidence": oov_evidence(points, classes),
        "worked_examples": worked,
        "results": results,
        "errors": errors,
        "tokenizer_fallbacks": list(TOKENIZER_FALLBACKS),
        "verdict": verdict(results, errors),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if errors:
        # ⛔ NEVER clobber a published record with a run that had a failed arm. The
        # documented invocation points `--out` at the committed evidence file, so a
        # half-failed rerun would otherwise overwrite the record with one that
        # cannot support a decision. The failed run still lands — beside it.
        failed = out_path.with_suffix(".failed.json")
        failed.write_text(json.dumps(artifact, indent=1, sort_keys=False))
        print(f"!! wrote {failed} (NOT {out_path}) — the run had "
              f"{len(errors)} arm error(s)")
        return artifact
    out_path.write_text(json.dumps(artifact, indent=1, sort_keys=False))
    return artifact


# The metrics the decision rule is evaluated on. §5/§6 publish all three per arm
# per class, so the rule reads all three — see `verdict()`'s docstring for why a
# head-only rule is the wrong rule here.
METRICS_CHECKED = ("ndcg@10", "ndcg@100", "recall@100")


def _metric_status(d: dict) -> str:
    """`worse` only on a significant LOSS — the direction rule, in one place."""
    if d["upper"] < 0.0:
        return "worse"
    if d["lower"] > 0.0:
        return "better"
    return "within_noise"


def _worst_status(statuses: list[str]) -> str:
    """The arm's status is its WORST metric's status, so a loss anywhere bites."""
    for level in ("worse", "disqualified", "within_noise", "better"):
        if level in statuses:
            return level
    return "within_noise"


def verdict(results: dict, errors: list[dict] | None = None) -> dict:
    """One line, with the number.

    THE DECISION RULE, stated so it cannot be read the wrong way. The question
    is *does moving the text index HURT quality*, so the verdict keys on the
    direction of the interval, not on whether it happens to contain zero:

    ``worse``          CI upper < 0 — a statistically significant LOSS. This is
                       the only outcome that disqualifies a candidate.
    ``within_noise``   CI spans 0 — indistinguishable from the baseline.
    ``better``         CI lower > 0 — a statistically significant GAIN.
    ``disqualified``   the arm RAISED during the run (see `errors` /
                       `arm_errors`), so its metrics are an empty result list,
                       not a quality statement. Checked BEFORE the three above:
                       a candidate that could not be installed must never be
                       reported as a significant loss — and an arm BUILT from a
                       disqualified arm inherits it (`ARM_DEPENDS_ON`).

    ⛔ If the BASELINE raised, there is no verdict at all. Every delta is
    measured against that arm, so a dead baseline turns every candidate into a
    large, meaningless GAIN — the same wrong-direction failure, relocated to the
    reference arm. `baseline_disqualified` is set and `decision` refuses.

    Treating ``better`` as "not within noise, therefore keep the index" would
    reverse the decision the issue exists to test.

    ⛔ IT KEYS ON EVERY PUBLISHED METRIC, NOT ON nDCG@10 ALONE. The claim this
    record makes is "no Postgres option loses significantly on any query class",
    and §5/§6 publish three metrics per arm per class. A rule that read only the
    head would call an arm `qualifying` while it lost significantly AT DEPTH — a
    real reason not to adopt it, and exactly the kind of unstated narrowing the
    evidence record must not contain. The arm's status is its WORST metric's
    status, so a loss anywhere disqualifies. (Verified on the committed run:
    strengthening the rule changes no verdict — every candidate's depth intervals
    exclude 0 in the POSITIVE direction, and the only arm that loses on all three
    metrics is `in_graph_fulltext_raw`, a non-candidate diagnostic.)
    """
    # Arms that raised ANYWHERE in the run. Read from the caller's error list AND
    # from the per-class `arm_errors` block, so a hand-built `results` dict (the
    # tests) and a real artifact are both handled.
    disqualified: set[str] = {e["arm"] for e in (errors or []) if e.get("arm")}
    for blk in results.values():
        disqualified.update(a for a, n in (blk.get("arm_errors") or {}).items() if n)
    # …and close over the dependency graph: a hybrid whose leg never ran is not a
    # measurement of that hybrid.
    grew = True
    while grew:
        grew = False
        for arm, deps in ARM_DEPENDS_ON.items():
            if arm not in disqualified and any(d in disqualified for d in deps):
                disqualified.add(arm)
                grew = True

    def _metric_row(d: dict, status: str) -> dict[str, Any]:
        return {
            "delta_pts": round(d["mean"], 2),
            "ci": [round(d["lower"], 2), round(d["upper"], 2)],
            "status": status,
            "n": d["n"],
        }

    headline: dict[str, Any] = {}
    # The same per-arm/per-class view, per METRIC — what the decision reads.
    by_metric: dict[str, Any] = {}
    worst: dict[str, dict[str, str]] = {}
    for cls, blk in results.items():
        for arm, row in blk["arms"].items():
            if arm == BASELINE_ARM:
                continue
            deltas = row.get("delta_vs_baseline") or {}
            d = deltas.get("ndcg@10")
            if arm in disqualified:
                headline.setdefault(arm, {})[cls] = {
                    "delta_pts": None, "ci": None, "status": "disqualified",
                    "n": (d or {}).get("n")}
                by_metric.setdefault(arm, {}).setdefault(cls, {})["all"] = {
                    "delta_pts": None, "ci": None, "status": "disqualified",
                    "n": (d or {}).get("n")}
                worst.setdefault(arm, {})[cls] = "disqualified"
                continue
            if not d:
                continue
            headline.setdefault(arm, {})[cls] = _metric_row(d, _metric_status(d))
            per_metric: dict[str, Any] = {}
            for metric in METRICS_CHECKED:
                dm = deltas.get(metric)
                if not dm:
                    continue
                per_metric[metric] = _metric_row(dm, _metric_status(dm))
            if not per_metric:
                continue
            by_metric.setdefault(arm, {})[cls] = per_metric
            worst.setdefault(arm, {})[cls] = _worst_status(
                [v["status"] for v in per_metric.values()])

    def qualifies(clsmap: dict) -> bool:
        return bool(clsmap) and all(s not in ("worse", "disqualified")
                                    for s in clsmap.values())

    # ⛔ A dead baseline is not a result. Every delta is measured AGAINST
    # `in_graph_fulltext`; if that arm raised, its empty result list makes every
    # candidate look like a large gain, so the verdict must refuse rather than
    # publish the opposite of what the instrument knows.
    baseline_disqualified = BASELINE_ARM in disqualified

    qualifying = [] if baseline_disqualified else [
        arm for arm, clsmap in worst.items() if qualifies(clsmap)]

    # The DISQUALIFIED-BY-DESIGN arms are not candidates: they are diagnostics.
    # `in_graph_fulltext_raw` exists to decompose builder-vs-scorer, and it is
    # expected to lose (it drops the OR-union the product builds).
    not_candidates = {"in_graph_fulltext_raw"}
    # CONTROL arms are not ALTERNATIVES either. `hybrid_baseline` is the RRF fusion
    # of the IN-GRAPH leg with pgvector — the control `hybrid_bm25` is measured
    # against. Listing it among the qualifying candidates would count the baseline
    # as evidence for replacing the baseline, and make the decision line name a
    # "Postgres option" that contains no Postgres lexical leg at all. It stays in
    # `qualifying_arms` (the arm does not lose; that is the point of a control) and
    # is named separately rather than dropped, so the exclusion is auditable.
    control_arms = {"hybrid_baseline"}
    candidates = [a for a in qualifying
                  if a not in not_candidates and a not in control_arms]

    # ⛔ ONE construction of the decision string, and it is the one that is
    # returned. It used to be built into a local that nothing read, while the
    # returned `decision` was rebuilt from scratch — so the dead copy said the
    # rule decides "at nDCG@10" and the live one said "any published metric",
    # and editing the visible block changed nothing. `decision += …` counts as
    # a use, so no linter could see it.
    if baseline_disqualified:
        decision = (
            "NO VERDICT — the baseline arm (`in_graph_fulltext`) raised during the "
            "run, so every candidate delta is measured against an empty result "
            "list. Re-run before reading ANY number here, including the tables.")
    else:
        decision = (
            ("a Postgres option does not lose to the in-graph leg on any query "
             "class at any published metric (" + ", ".join(METRICS_CHECKED) + "): "
             + ", ".join(candidates)
             if candidates else
             "every Postgres option loses significantly on at least one query "
             "class; the text index stays")
            + (f" — but {len(disqualified)} arm(s) were DISQUALIFIED by a "
               f"recorded error and are not scored: {', '.join(sorted(disqualified))}"
               if disqualified else ""))

    return {
        "rule": ("a candidate qualifies unless it is significantly WORSE "
                 "(paired 90% CI upper < 0) on any query class on ANY of the "
                 "published metrics (" + ", ".join(METRICS_CHECKED) + "), or was "
                 "disqualified by a recorded error (inherited by arms built from a "
                 "disqualified arm); NO VERDICT AT ALL if the baseline was "
                 "disqualified"),
        "metrics_checked": list(METRICS_CHECKED),
        "headline_ndcg@10": headline,
        "headline": by_metric,
        "worst_status": worst,
        "qualifying_arms": qualifying,
        "qualifying_candidates": candidates,
        "diagnostic_arms_not_candidates": sorted(not_candidates),
        "control_arms_not_candidates": sorted(control_arms),
        "disqualified_arms": sorted(disqualified),
        "baseline_disqualified": baseline_disqualified,
        "decision": decision,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="seed both backends and measure every arm")
    r.add_argument("--fg-host", default="127.0.0.1")
    r.add_argument("--fg-port", type=int, required=True)
    r.add_argument("--pg-dsn", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--reuse", action="store_true",
                   help="do not re-seed (both backends already hold the corpus)")
    r.add_argument("--full", action="store_true",
                   help="also write the per-query retrieved-id lists (~4 MB); "
                        "per-query METRICS are always written")
    args = ap.parse_args(argv)

    art = run(args.fg_host, args.fg_port, args.pg_dsn, Path(args.out),
              reuse=args.reuse, full=args.full)
    print(json.dumps(art["verdict"], indent=1))
    if art.get("tokenizer_fallbacks"):
        print(f"!! {len({e['text'] for e in art['tokenizer_fallbacks']})} query "
              "text(s) used the REGEX "
              "fallback because the product tokenizer returned nothing (see "
              "`tokenizer_fallbacks` in the artifact); those queries are not "
              "priced by the shared tokenizer the record claims")
    if art["errors"]:
        # NOT a success: a run with a failed arm cannot support a decision, and
        # exiting 0 here is what let a disqualified candidate read as a verdict.
        print(f"!! {len(art['errors'])} recorded arm error(s) — see artifact.json; "
              "the affected arms are DISQUALIFIED, not scored")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
