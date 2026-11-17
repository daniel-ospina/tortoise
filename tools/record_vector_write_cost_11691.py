#!/usr/bin/env python3
"""Price the record's WRITE-side vector maintenance — #11691 (deliverable 2).

WHAT THIS MEASURES, and why it is a harness rather than a paragraph.
`supabase/migrations/20261109000001_record_embedding_vector.sql` added a
``GENERATED ALWAYS … STORED`` ``embedding_vec vector(384)`` column to
``point``/``source``/``event`` plus three HNSW indexes. That cost is paid on
**every record write from the day the migration is applied — armed or not**: it
is not gated on ``TORTOISE_RECORD_VECTOR_LEG``, on the cutover, or on anything an
operator can switch off. The #11642 artifact
(`docs/evidence/11642-vector-move/README.md`) prices the BUILD (4.0–4.2 s for the
index at 20k rows; 6.9 s for the generated-column rewrite) and never the ONGOING
line. This is that line:

  1. ms added per record write (p50 / p95), isolated by three tables that differ
     by exactly one feature each;
  2. index + payload bytes per 1,000 writes, from real size deltas;
  3. the same at current production volume, in the units
     ``product/pricing.json`` uses (``postgres_storage``: $0.125/GB-month cost
     basis / $0.30/GB-month overage; ``write_ops``: $5.00 per 10,000).

HOW THE THREE SHAPES ARE BUILT, so a control cannot drift from production:
``point`` is created with this tree's record columns and then the MIGRATION'S OWN
STATEMENTS are applied to it verbatim (extracted from the file, never retyped).
The controls are derived from it structurally — ``point_gen_only`` is
``CREATE TABLE … (LIKE point)``, which copies the generated column (and its
expression) and none of the indexes; ``point_plain`` is that table with the
generated column dropped. So "generated column present" and "HNSW present" are
the only differences between the three — ⚠️ WITH ONE DOCUMENTED EXCEPTION that
this file's first version got wrong (#11691 review F2): `LIKE` copies NO primary
key, so `point` carries `point_pkey` and the two controls carry no index at all.
Every index figure is therefore reported from the index's OWN byte count in the
`indexes` map (``hnsw_bytes_per_1000_writes``), never as "all index bytes of
`point` minus the controls' zero" — that difference also carried the pkey's own
growth (~75 kB per 1,000 writes, 3.5 % of the figure).

⛔ TWO THINGS THE METHOD REFUSES, each because it would report the environment
as if it were the feature:

* **client wall clock is NOT the per-write cost.** This box runs a fleet; a
  measured ``SELECT 1`` round trip can be ~6 ms and a plain insert ~15 ms, purely
  from scheduling. Every timing here is the statement's OWN execution time, read
  from ``EXPLAIN (ANALYZE, FORMAT JSON)`` — server-side, and excluding the
  ``COMMIT`` fsync (which belongs to the storage layer, not to the index). The
  host load average is recorded in the artifact beside the figures so a reader
  can see what the box was doing.
* **the three shapes are INTERLEAVED, never run one after another.** A
  sequential run attributes a load spike to whichever table was being measured
  when it landed. Each round writes a small batch into every shape, so drift is
  spread across them and the DELTA is the feature's.

⛔ NOT PRODUCTION-EXACT, and stated rather than implied: the stand-in tables carry
the columns a WRITE touches (``id``/``org_id``/``graph_id``/``embedding``/
``updated_at``/``created_at`` + the ``point`` kind/status columns), not the full
record schema with its RLS policies, triggers and foreign keys. That makes every
absolute figure here a LOWER bound on the real write — and costs all three shapes
identically, so the DELTAS this file reports are the features' own cost.

⛔ THE INDEX SET IS PART OF THAT GAP, and it is the part the byte figures are most
easily misread over (#11691 review, finding 2): the stand-in's ``point`` carries
``id text PRIMARY KEY`` — ONE column — where production's ``public.point`` has a
THREE-column primary key (``PRIMARY KEY (org_id, graph_id, node_key)``,
``supabase/migrations/20261012000003_record_epistemic.sql:118``) plus
``idx_point_scope`` (same file, ``:148``) and ``idx_point_graph_tier``
(``supabase/migrations/20261029000001_record_point_cache_tier.sql:103``). So the
artifact's ``scale.indexes`` holds exactly TWO keys, and every figure labelled
"the primary key's" here is the ``id``-only key's cost: treat it as a FLOOR on a
composite key's growth, never as production's per-write key cost.

Run (it starts its own throwaway ``pgvector/pgvector:pg16`` container):

    uv run --with psycopg2-binary python tools/record_vector_write_cost_11691.py \\
        --json-out docs/evidence/11691-vector-leg-budget/measurement.json

    # …or against a Postgres you already have (it must carry the `vector`
    # extension and be EMPTY: this drops and recreates the stand-in tables):
    uv run --with psycopg2-binary python tools/record_vector_write_cost_11691.py \\
        --dsn postgresql://postgres:pw@127.0.0.1:5432/postgres
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
MIGRATION = REPO / "supabase/migrations/20261109000001_record_embedding_vector.sql"

DIM = 384
#: The HNSW index the migration creates on ``public.point`` (#11642). Named here
#: because the per-write index cost is read from THAT index's own byte count
#: (#11691 review F2) — the controls are `LIKE`-derived and carry no primary key,
#: so "all of `point`'s index bytes minus zero" would also charge `point_pkey`.
HNSW_INDEX = "idx_point_embedding_vec_hnsw"
#: The ``point`` population the issue measured on the live record (2026-11-16:
#: 33,671 ``public.point`` rows, 27,813 carrying ``embedding_vec``). The index's
#: own size at this base is the storage principal at current production volume.
PRODUCTION_POINT_ROWS = 33_671
#: Rows the scale phase actually BUILD to. 20,000 is chosen because #11642
#: measured the HNSW index at exactly that base (**39 MB**), so this harness has
#: an INDEPENDENT anchor to be wrong against; the production figure is then a
#: linear derivation from the measured bytes/row (an HNSW graph's size is linear
#: in rows at fixed ``M``), and the derivation is printed with its inputs.
SCALE_ROWS = 20_000
#: Timed writes per shape, in interleaved rounds of ``BATCH``.
TIMED = 1_000
BATCH = 25
WARMUP = 200
#: The at-scale phase: fewer writes (the tables already hold `--scale-rows`), and
#: a smaller batch so a preemption inside one batch cannot set its median.
SCALE_TIMED = 200
SCALE_BATCH = 20
SCALE_WARMUP = 20

# ── pricing, read from the artifact rather than re-typed ────────────────────
PRICING = json.loads((REPO / "product/pricing.json").read_text())
STORAGE = PRICING["billing"]["storage"]["dimensions"]["postgres_storage"]
COST_BASIS_GB_MONTH = float(STORAGE["cost_basis_usd_per_gb_month"])
OVERAGE_GB_MONTH = float(STORAGE["overage_price_usd_per_gb_month"])
BYTES_PER_GB = int(PRICING["billing"]["storage"]["bytes_per_gb"])
WRITE_OPS_PRICE = float(PRICING["billing"]["overage_price_per_10k"])
INCLUDED_WRITE_OPS = {
    tier: int(spec["included_write_ops_per_month"])
    for tier, spec in PRICING["tiers"].items()
    if spec.get("included_write_ops_per_month")
}

SHAPES = ("point_plain", "point_gen_only", "point")


# ── the container ─────────────────────────────────────────────────────────

def _docker(*args: str, **kw) -> str:
    return subprocess.run(("docker", *args), check=True, capture_output=True,
                          text=True, **kw).stdout.strip()


def start_container(name: str) -> str:
    """A throwaway pgvector server. Returns its DSN.

    ``pgvector/pgvector:pg16`` and not a plain postgres image: the migration
    begins with ``CREATE EXTENSION vector``, so a bare ``postgres:16`` cannot
    create the generated column's type at all (#11642's note).
    """
    subprocess.run(("docker", "rm", "-f", name), capture_output=True, text=True)
    _docker("run", "--rm", "-d", "--name", name,
            "-e", "POSTGRES_PASSWORD=record",
            "-e", "POSTGRES_DB=record",
            "-e", "POSTGRES_USER=record",
            "-p", "127.0.0.1::5432", "pgvector/pgvector:pg16")
    deadline = time.time() + 90
    while time.time() < deadline:
        port = _docker("port", name, "5432/tcp").split(":")[-1]
        ready = subprocess.run(
            ("docker", "exec", name, "pg_isready", "-U", "record", "-d", "record"),
            capture_output=True, text=True)
        if ready.returncode == 0:
            return f"postgresql://record:record@127.0.0.1:{port}/record"
        time.sleep(0.5)
    raise SystemExit("the pgvector container never became ready")


# ── the schema ────────────────────────────────────────────────────────────

def _migration_statements(kind: str) -> list[str]:
    """The migration's OWN statements for one label, extracted verbatim.

    Extraction rather than transcription: the generated-column expression and the
    index's operator class are the two things a re-typed copy gets wrong
    silently — ``vector_cosine_ops`` vs ``vector_l2_ops`` is a different RANKING,
    and a hand-written ``embedding::vector`` loses a whole write (§3/§4 of
    `docs/evidence/11642-vector-move/README.md`).
    """
    sql = MIGRATION.read_text()
    function = sql.split("CREATE OR REPLACE FUNCTION", 1)[1]
    function = "CREATE OR REPLACE FUNCTION" + function.split("$$;", 1)[0] + "$$;"
    column = re.search(rf"ALTER TABLE public\.{kind}\s+ADD COLUMN[^;]+;", sql)
    index = re.search(
        rf"CREATE INDEX IF NOT EXISTS idx_{kind}_embedding_vec_hnsw[^;]+;", sql)
    if column is None or index is None:
        raise SystemExit(f"could not extract the {kind} statements from {MIGRATION}")
    return [function, column.group(0), index.group(0)]


def build_schema(cur: Any) -> None:
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # The columns a point/source/event WRITE touches. Types and nullability
    # mirror the record's migrations; the surrounding schema (RLS, triggers,
    # FKs) is deliberately absent — see the module docstring.
    for kind in ("point", "source", "event"):
        cur.execute(f"DROP TABLE IF EXISTS public.{kind} CASCADE")
        cur.execute(f"""
            CREATE TABLE public.{kind} (
                id text PRIMARY KEY,
                org_id text NOT NULL,
                graph_id text NOT NULL,
                embedding jsonb,
                updated_at text,
                created_at text
            )""")
        if kind == "point":
            # The columns the dense leg's predicate reads (#943/#689), so the
            # stand-in carries the shape a real point write has.
            cur.execute("ALTER TABLE public.point ADD COLUMN status text, "
                        "ADD COLUMN is_operator boolean, ADD COLUMN op_type text, "
                        "ADD COLUMN outdated boolean")
    for kind in ("point", "source", "event"):
        for statement in _migration_statements(kind):
            cur.execute(statement)
    # Controls, DERIVED from the real shape so they cannot drift from it.
    #
    # ⛔ `INCLUDING GENERATED` IS LOAD-BEARING, and its absence is a measured
    # trap: a bare `LIKE` copies NEITHER default NOR generation expressions, so
    # `point_gen_only` came out with a PLAIN `vector(384)` column that nothing
    # ever wrote — byte-identical to `point_plain`, which reported the generated
    # column as costing 0 ms and 0 bytes on the first run of this harness. The
    # control would have said the feature is free.
    cur.execute("DROP TABLE IF EXISTS public.point_gen_only CASCADE")
    cur.execute("CREATE TABLE public.point_gen_only "
                "(LIKE public.point INCLUDING GENERATED)")
    cur.execute("DROP TABLE IF EXISTS public.point_plain CASCADE")
    cur.execute("CREATE TABLE public.point_plain (LIKE public.point)")
    cur.execute("ALTER TABLE public.point_plain DROP COLUMN embedding_vec")


# ── the corpus ────────────────────────────────────────────────────────────

def _corpus(n: int, seed: int = 11691) -> list[str]:
    """``n`` jsonb embeddings with REAL neighbourhood structure.

    Clustered (50 centroids + noise) rather than uniform random, for the reason
    #11642's artifact gives: a uniform 384-dim corpus is near-degenerate, and an
    HNSW insert into one costs LESS than an insert into a corpus with actual
    neighbourhoods — so the uniform choice would under-price the write. Returned
    as the JSON text a ``jsonb`` column stores (which is what the record holds,
    and why the payload column is ~3.6 kB rather than pgvector's 1,536 B).

    ⚠️ THAT NUMBER IS MEASURED OFF THIS FUNCTION, not eyeballed: the corpus the
    write phase CONSUMES — `_corpus(WARMUP + TIMED, seed=11691)`, 1,200 rows —
    averages 3,607.3 characters a row (min 3,572, max 3,642), i.e. 3,608 B, and
    the artifact records exactly that sample as `corpus.text_bytes_per_row_mean`.
    (The first 50 rows alone average 3,607.6 — the same figure at the quoted
    rounding, but the FIELD must describe the sample that was measured, which is
    the 1,200-row one.) A corpus with a DIFFERENT DIM or precision has a
    different size, so re-measure rather than re-quote it.
    """
    rng = random.Random(seed)
    centroids = [[rng.gauss(0.0, 1.0) for _ in range(DIM)] for _ in range(50)]
    out: list[str] = []
    for i in range(n):
        c = centroids[i % len(centroids)]
        vec = [round(x + rng.gauss(0.0, 0.08), 6) for x in c]
        out.append(json.dumps(vec, separators=(",", ":")))
    return out


def _corpus_shape(n: int = 50, seed: int = 11691) -> dict:
    """The corpus' OWN bytes/row, as a field of the artifact (#11691 review F3).

    The evidence doc quotes the row size to explain why these figures are bigger
    than a 1,536-B `vector` column predicts, and it claims every number it states
    is a field of `measurement.json`. A quoted size that lives only in prose is a
    number nobody can re-derive, so emit it from the generator that produces the
    rows it measures.
    """
    sizes = [len(row) for row in _corpus(n, seed=seed)]
    return {"rows": n,
            "seed": seed,
            "dim": DIM,
            "text_bytes_per_row_mean": round(sum(sizes) / len(sizes), 1),
            "text_bytes_per_row_min": min(sizes),
            "text_bytes_per_row_max": max(sizes),
            "derivation": ("len() of each row `_corpus(n, seed)` emits, i.e. the "
                           "`jsonb` text the record stores; mean/min/max over "
                           "those rows")}


# ── measurement ───────────────────────────────────────────────────────────

def _pct(samples: list[float], q: float) -> float:
    if not samples:
        return float("nan")
    ordered = sorted(samples)
    k = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return ordered[k]


def _sizes(cur: Any, table: str) -> dict[str, Any]:
    cur.execute("SELECT pg_total_relation_size(%s::regclass)", (f"public.{table}",))
    total = int(cur.fetchone()[0])
    cur.execute("SELECT indexrelname, pg_relation_size(indexrelid) "
                "FROM pg_stat_user_indexes WHERE relname = %s", (table,))
    indexes = {name: int(size) for name, size in cur.fetchall()}
    cur.execute("SELECT reltoastrelid FROM pg_class WHERE oid = %s::regclass",
                (f"public.{table}",))
    row = cur.fetchone()
    toast_oid = int(row[0]) if row and row[0] else 0
    toast = 0
    if toast_oid:
        cur.execute("SELECT pg_relation_size(%s::oid)", (toast_oid,))
        toast = int(cur.fetchone()[0])
    return {"total": total, "indexes": indexes, "toast": toast,
            "index_bytes": sum(indexes.values())}


def _insert_sql(table: str) -> str:
    return (f"EXPLAIN (ANALYZE, FORMAT JSON) INSERT INTO public.{table} "
            "(id, org_id, graph_id, embedding, updated_at, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)")


def _execution_ms(cur: Any, table: str, row: tuple) -> float:
    """The statement's OWN execution time, server-side.

    ⛔ Not client wall clock: this box runs a fleet, and a measured ``SELECT 1``
    round trip is ~6 ms of scheduling that has nothing to do with the index. The
    ``COMMIT`` fsync is likewise outside this figure — it is the storage layer's
    cost and identical for all three shapes.
    """
    cur.execute(_insert_sql(table), row)
    plan = cur.fetchone()[0]
    if isinstance(plan, str):
        plan = json.loads(plan)
    return float(plan[0]["Execution Time"])


def run_timed_phase(conn: Any, corpus: list[str], org: str, *,
                    writes: int, batch: int, warmup: int,
                    tag: str) -> dict[str, Any]:
    """Interleaved, so a load spike is spread across all three shapes.

    ``writes // batch`` rounds of ``batch`` writes per shape; every write is its
    own statement (the record's door writes one record per call), and commits
    happen per batch OUTSIDE the timed region.
    """
    cur = conn.cursor()
    samples: dict[str, list[float]] = {name: [] for name in SHAPES}
    rounds: list[dict[str, float]] = []
    for i in range(warmup):
        emb = corpus[i]
        for name in SHAPES:
            _execution_ms(cur, name, (f"{name}-{tag}w{i}", org, org, emb, "u", "c"))
        conn.commit()
    for round_no in range(writes // batch):
        this_round: dict[str, float] = {}
        for name in SHAPES:
            got: list[float] = []
            for j in range(batch):
                emb = corpus[warmup + round_no * batch + j]
                got.append(_execution_ms(
                    cur, name,
                    (f"{name}-{tag}{round_no}-{j}", org, org, emb, "u", "c")))
            # ⚠️ A ROUND MEDIAN, not a round mean: this box runs a fleet, and a
            # single preemption inside a 25-write batch would otherwise set the
            # batch's "cost". The paired deltas below are taken over the medians,
            # which is what makes the attribution survive the load.
            this_round[name] = _pct(got, 0.50)
            samples[name].extend(got)
            conn.commit()
        rounds.append(this_round)
    cur.close()
    return {"samples": samples, "rounds": rounds}


def _summarise(conn: Any, samples: dict[str, list[float]],
               before: dict[str, dict[str, Any]], *,
               extra_rows: int) -> list[dict[str, Any]]:
    """Per-shape percentiles plus the byte deltas over the SAME writes.

    ``before`` is the size snapshot taken immediately before the timed phase, so
    every byte figure describes exactly the writes whose timings are reported —
    never a mixture of two phases.
    """
    out: list[dict[str, Any]] = []
    for name in SHAPES:
        cur = conn.cursor()
        sizes = _sizes(cur, name)
        cur.close()
        got = samples[name]
        # The table's POPULATION (what the index has to carry), not the number of
        # writes this phase timed — the two differ at the at-scale phase, where
        # the population was built in an earlier, untimed phase.
        cur = conn.cursor()
        cur.execute(f"SELECT count(*) FROM public.{name}")
        population = int(cur.fetchone()[0])
        cur.close()
        written = len(got) + extra_rows
        out.append({
            "table": name,
            "timed_writes": len(got),
            "rows_after": population,
            "population": population,
            "p50_ms": round(_pct(got, 0.50), 4),
            "p95_ms": round(_pct(got, 0.95), 4),
            "mean_ms": round(sum(got) / len(got), 4) if got else None,
            "min_ms": round(min(got), 4) if got else None,
            "max_ms": round(max(got), 4) if got else None,
            "bytes_per_1000_writes": round(
                (sizes["total"] - before[name]["total"]) / written * 1000.0, 1),
            "index_bytes": sizes["index_bytes"],
            "index_bytes_growth": sizes["index_bytes"] - before[name]["index_bytes"],
            "total_bytes": sizes["total"],
            "indexes": sizes["indexes"],
        })
    return out


def build_scale(conn: Any, rows: int, embeddings: list[str], org: str) -> dict[str, Any]:
    """Build the real-shaped table to ``rows`` and read its sizes.

    This is the STORAGE PRINCIPAL — what the HNSW index holds at a given
    population — as distinct from the per-write FLOW above. Batched (250 rows per
    statement) and untimed: only the resulting sizes are read.
    """
    cur = conn.cursor()
    per = 250
    for table in SHAPES:
        cur.execute(f"TRUNCATE public.{table}")
        conn.commit()
        for start in range(0, rows, per):
            chunk = embeddings[start:start + per]
            args: list[Any] = []
            for i, emb in enumerate(chunk):
                args.extend((f"prod-{start + i}", org, org, emb, "u", "c"))
            cur.execute(
                f"INSERT INTO public.{table} (id, org_id, graph_id, embedding, "
                "updated_at, created_at) VALUES "
                + ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(chunk)), args)
            conn.commit()
        cur.execute(f"ANALYZE public.{table}")
        conn.commit()
    sizes = _sizes(cur, "point")
    cur.execute("SELECT count(*) FROM public.point WHERE embedding_vec IS NOT NULL")
    sizes["rows_with_vector"] = int(cur.fetchone()[0])
    sizes["rows"] = rows
    cur.close()
    return sizes


# ── the report ────────────────────────────────────────────────────────────

def _usd_gb_month(nbytes: float, rate: float) -> float:
    return nbytes / BYTES_PER_GB * rate


def _loadavg() -> tuple[float, float, float] | None:
    try:
        return os.getloadavg()
    except OSError:  # pragma: no cover — platform without loadavg
        return None


def _control_index_bytes(shape: dict) -> int:
    """The CONTROL shape's index bytes — 0, because it has no index at all.

    Fail-closed in both directions (#11691 review, finding 1): asking the shape
    under test for `HNSW_INDEX` raises if the key is gone (a relabelled index is
    not a free index), while the control's emptiness is ASSERTED here instead of
    being spelled `.get(key, 0)` — the spelling that made a dropped key and a
    genuinely absent index indistinguishable.
    """
    if shape["indexes"]:
        raise SystemExit(
            "the generated-column control carries an index "
            f"({sorted(shape['indexes'])}) — it is built `LIKE … INCLUDING "
            "GENERATED` with none, so the HNSW-vs-control subtraction below is "
            "no longer measuring the index under test")
    return shape["index_bytes"]


def _derive(report: dict) -> dict:
    """The DERIVED half: deltas, production scale, and the money.

    Split out of :func:`main` so it can be re-run on its own artifact with
    ``--recompute`` — the measurement takes ~35 minutes, and the RATES in
    ``product/pricing.json`` move independently of it. Re-deriving must not mean
    re-measuring, and it must not mean hand-editing a committed number either.
    """
    by_table = {s["table"]: s for s in report["shapes"]}
    at_scale = {s["table"]: s for s in report["shapes_at_scale"]}
    plain, gen_only, full = (by_table["point_plain"],
                             by_table["point_gen_only"], by_table["point"])
    s_full = at_scale["point"]

    # PAIRED, per-round attribution: median over rounds of (this shape's round
    # median - the next-cheaper shape's round median). Common-mode load drift
    # cancels, which a difference of two overall percentiles cannot do — on this
    # box the plain insert's OWN p95 is several times its p50, purely scheduling.
    #
    # ⚠️ ``phases is None`` == this artifact predates the rounds being kept (they
    # are dropped from the committed file because they are bulk). The PREVIOUS
    # value is then carried through, so `--recompute` can re-price an artifact
    # whose measurement half is already final instead of demanding a 35-minute
    # re-run. A rate change must not invalidate a measurement.
    previous = report.get("deltas") or {}

    def _paired(phases, previous_phase: str, key: str,
                scarce: str, cheap: str, q: float):
        if phases is None:
            return (previous.get(previous_phase) or {}).get(key)
        diffs = [r[scarce] - r[cheap] for r in phases]
        return round(_pct(diffs, q), 4)

    small_rounds = report.pop("timed_rounds", None)
    big_rounds = report.pop("scale_timed_rounds", None)
    deltas = {
        # The 1,200-row phase: the cheap end of HNSW's insert cost.
        "small_population": {
            "rows": plain["population"],
            "paired_generated_column_ms_p50": _paired(
                small_rounds, "small_population",
                "paired_generated_column_ms_p50",
                "point_gen_only", "point_plain", 0.50),
            "paired_hnsw_ms_p50": _paired(
                small_rounds, "small_population", "paired_hnsw_ms_p50",
                "point", "point_gen_only", 0.50),
            "paired_total_ms_p50": _paired(
                small_rounds, "small_population", "paired_total_ms_p50",
                "point", "point_plain", 0.50),
            "paired_total_ms_p95": _paired(
                small_rounds, "small_population", "paired_total_ms_p95",
                "point", "point_plain", 0.95),
        },
        # At `--scale-rows`: what a write pays TODAY. This is the attribution the
        # money conversions below use, because HNSW's insert cost grows with the
        # graph it inserts into.
        "scale_population": {
            "rows": s_full["population"],
            "paired_generated_column_ms_p50": _paired(
                big_rounds, "scale_population",
                "paired_generated_column_ms_p50",
                "point_gen_only", "point_plain", 0.50),
            "paired_hnsw_ms_p50": _paired(
                big_rounds, "scale_population", "paired_hnsw_ms_p50",
                "point", "point_gen_only", 0.50),
            "paired_hnsw_ms_p95": _paired(
                big_rounds, "scale_population", "paired_hnsw_ms_p95",
                "point", "point_gen_only", 0.95),
            "paired_total_ms_p50": _paired(
                big_rounds, "scale_population", "paired_total_ms_p50",
                "point", "point_plain", 0.50),
            "paired_total_ms_p95": _paired(
                big_rounds, "scale_population", "paired_total_ms_p95",
                "point", "point_plain", 0.95),
        },
        # The unpaired percentile differences, for a reader who wants them and
        # can see how much of the p95 is the HOST (compare plain's own p95).
        "unpaired_p50_delta_ms": {
            "generated_column": round(gen_only["p50_ms"] - plain["p50_ms"], 4),
            "hnsw": round(full["p50_ms"] - gen_only["p50_ms"], 4),
            "total": round(full["p50_ms"] - plain["p50_ms"], 4),
        },
        "unpaired_p95_delta_ms": {
            "generated_column": round(gen_only["p95_ms"] - plain["p95_ms"], 4),
            "hnsw": round(full["p95_ms"] - gen_only["p95_ms"], 4),
            "total": round(full["p95_ms"] - plain["p95_ms"], 4),
            "host_noise_floor": round(plain["p95_ms"] - plain["p50_ms"], 4),
        },
        # The index's OWN growth per 1,000 writes — read from the HNSW index's
        # own byte count, NOT as "all of `point`'s index bytes minus the
        # controls' zero" (#11691 review F2: `LIKE` copies no PRIMARY KEY, so
        # `point` carries `point_pkey` and that difference silently charged the
        # pkey's ~75 kB/1k growth to the index under test).
        "hnsw_bytes_per_1000_writes": round(
            (full["indexes"][HNSW_INDEX] - _control_index_bytes(gen_only))
            / (full["timed_writes"] + WARMUP) * 1000.0, 1),
        # …and the growth the CONTROL SHAPE carries but the index does not: the
        # primary key's own bytes, reported separately so the split is auditable
        # instead of invisible.
        #
        # ⛔ INDEXED, NOT `.get(…, 0)` (#11691 review, finding 1). A default of 0
        # is fail-open in exactly the direction this split exists to catch: a
        # renamed or dropped HNSW index reads 0.0 on the shape UNDER TEST, which
        # moves the whole index cost into the field NAMED "primary key" while the
        # only pinned invariant — their sum — stays identical. A missing key must
        # raise here, not relabel itself.
        #
        # ⚠ THE CONTROL IS THE ONE SHAPE WHOSE ABSENCE IS THE TRUTH: `point_gen_only`
        # is built `LIKE public.point INCLUDING GENERATED` with no index, so it must
        # carry an EMPTY map (README §1.5) — asserted, so "no index" and "the map
        # lost a key" stay distinguishable.
        "primary_key_bytes_per_1000_writes": round(
            (full["index_bytes"] - full["indexes"][HNSW_INDEX]
             - gen_only["index_bytes"] + _control_index_bytes(gen_only))
            / (full["timed_writes"] + WARMUP) * 1000.0, 1),
        # The generated COLUMN's payload (vector + heap) per 1,000 writes.
        "payload_bytes_per_1000_writes": round(
            (gen_only["total_bytes"] - plain["total_bytes"])
            / (gen_only["timed_writes"] + WARMUP) * 1000.0, 1),
    }
    at_scale_pair = deltas["scale_population"]

    # ⛔ THE REPORTED per-row figure is the one the production bytes are scaled
    # from, rounding included — otherwise `production_total_bytes` is not
    # `bytes_per_row × production_rows` in the artifact and the doc's arithmetic
    # stops re-deriving (measured: a 674-byte drift, caught by
    # `tests/test_11691_vector_leg_budget.py`).
    per_row_bytes = round(report["scale"]["total"] / report["scale"]["rows"], 1)
    per_row_index = round(report["scale"]["index_bytes"] / report["scale"]["rows"], 1)
    production = {
        "measured_at_rows": report["scale"]["rows"],
        "measured_at_rows_with_vector": report["scale"]["rows_with_vector"],
        "measured_total_bytes": report["scale"]["total"],
        "measured_toast_bytes": report["scale"]["toast"],
        "measured_index_bytes": report["scale"]["index_bytes"],
        "measured_indexes": report["scale"]["indexes"],
        "bytes_per_row": per_row_bytes,
        "index_bytes_per_row": per_row_index,
        "derivation": ("linear in rows at fixed M; the measured bytes/row above "
                       "times the production row count"),
        "production_rows": PRODUCTION_POINT_ROWS,
        "production_total_bytes": round(per_row_bytes * PRODUCTION_POINT_ROWS),
        "production_index_bytes": round(per_row_index * PRODUCTION_POINT_ROWS),
    }
    production["usd_per_month"] = {
        # The PRINCIPAL: what today's population's index + payload occupies, at
        # the two rates pricing.json publishes.
        "principal_cost_basis": round(_usd_gb_month(
            production["production_total_bytes"], COST_BASIS_GB_MONTH), 4),
        "principal_overage": round(_usd_gb_month(
            production["production_total_bytes"], OVERAGE_GB_MONTH), 4),
        "principal_index_only_cost_basis": round(_usd_gb_month(
            production["production_index_bytes"], COST_BASIS_GB_MONTH), 4),
    }
    flow_bytes_per_1k = (deltas["hnsw_bytes_per_1000_writes"]
                         + deltas["primary_key_bytes_per_1000_writes"]
                         + deltas["payload_bytes_per_1000_writes"])
    production["flow_usd_per_month"] = {
        # The FLOW: the same per-1,000-write growth at the write volumes
        # pricing.json itself publishes per tier (`included_write_ops_per_month`).
        f"{tier}_{rate_name}": round(_usd_gb_month(
            flow_bytes_per_1k * (ops / 1000.0), rate), 4)
        for tier, ops in sorted(INCLUDED_WRITE_OPS.items())
        for rate_name, rate in (("cost_basis", COST_BASIS_GB_MONTH),
                                ("overage", OVERAGE_GB_MONTH))
    }
    production["added_write_time"] = {
        # The PAIRED, AT-SCALE figures: the load-robust attribution at the
        # population a write actually pays today (see `_paired`).
        "rows": at_scale_pair["rows"],
        "ms_per_write_p50": at_scale_pair["paired_total_ms_p50"],
        "ms_per_write_p95": at_scale_pair["paired_total_ms_p95"],
        "seconds_per_month_at_tier_volume": {
            tier: round(at_scale_pair["paired_total_ms_p50"] * ops / 1000.0, 1)
            for tier, ops in sorted(INCLUDED_WRITE_OPS.items())
        },
    }

    report.setdefault("measured",
                      time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    report.update({
        "issue": 11691,
        "pricing": {"cost_basis_usd_per_gb_month": COST_BASIS_GB_MONTH,
                    "overage_usd_per_gb_month": OVERAGE_GB_MONTH,
                    "write_ops_usd_per_10k": WRITE_OPS_PRICE,
                    "included_write_ops_per_month": INCLUDED_WRITE_OPS,
                    "bytes_per_gb": BYTES_PER_GB},
        "deltas": deltas,
        "production_volume": production,
        # the corpus' own bytes/row — the number the evidence doc quotes to
        # explain the row size, emitted so it is a FIELD and not prose (F3).
        # `WARMUP + TIMED` is the corpus the write phase CONSUMES, so the field
        # describes the rows it measures rather than a 50-row prefix of them
        # (#11691 review, finding 5: 3,607.3 vs 3,607.6 B/row — immaterial, but
        # a field that describes a different sample than the one measured is the
        # provenance defect F3 is about).
        "corpus": _corpus_shape(WARMUP + TIMED, seed=11691),
    })

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=None,
                        help="an EMPTY Postgres with the vector extension; "
                             "default: start a throwaway pgvector/pgvector:pg16")
    parser.add_argument("--json-out", default=None,
                        help="write the full record here")
    parser.add_argument("--container", default="tortoise-11691-write-cost")
    parser.add_argument("--scale-rows", type=int, default=SCALE_ROWS)
    parser.add_argument("--recompute", default=None, metavar="JSON",
                        help="re-derive the measurement's DERIVED half (deltas, "
                             "production scale, dollars) from an artifact — for "
                             "when a RATE in product/pricing.json moves and the "
                             "35-minute measurement does not need re-taking")
    args = parser.parse_args(argv)

    if args.recompute:
        artifact = json.loads(Path(args.recompute).read_text())
        artifact = _derive(artifact)
        Path(args.recompute).write_text(json.dumps(artifact, indent=1) + "\n")
        print(json.dumps(artifact, indent=1))
        return 0

    import psycopg2  # the record's driver

    container = None
    dsn = args.dsn
    if dsn is None:
        dsn = start_container(args.container)
        container = args.container
    report: dict[str, Any] = {}
    try:
        # ⚠️ `pg_isready` is NOT "usable yet": during initdb the image runs a
        # TEMPORARY server that answers `pg_isready` and then shuts down, so the
        # first real connection can hit a closed port (measured). Retry the
        # CONNECTION rather than trusting the readiness probe.
        deadline = time.time() + 120
        while True:
            try:
                conn = psycopg2.connect(dsn)
                break
            except psycopg2.OperationalError:
                if time.time() > deadline:
                    raise
                time.sleep(1.0)
        conn.autocommit = False
        build_schema(conn.cursor())
        conn.commit()

        org = "org_11691"
        corpus = _corpus(WARMUP + TIMED, seed=11691)
        # Sizes BEFORE the phase, so every byte figure is a delta over the SAME
        # writes the timings were taken from.
        cur = conn.cursor()
        before = {name: _sizes(cur, name) for name in SHAPES}
        cur.close()
        load_before = _loadavg()
        started = time.time()
        timed = run_timed_phase(conn, corpus, org, writes=TIMED, batch=BATCH,
                                warmup=WARMUP, tag="")
        conn.commit()
        elapsed_s = round(time.time() - started, 1)
        load_after = _loadavg()
        shapes = _summarise(conn, timed["samples"], before, extra_rows=WARMUP)
        rounds = timed["rounds"]

        scale = build_scale(conn, args.scale_rows, _corpus(args.scale_rows, seed=991),
                           org)
        # ── the SAME writes, at PRODUCTION-SCALE populations ────────────────
        # HNSW's insert cost grows with the graph it inserts into, so the
        # 1,200-row phase above is the cheap end. This one measures the cost a
        # write actually pays today, with all three shapes at `--scale-rows`.
        scale_before = {}
        cur = conn.cursor()
        for name in SHAPES:
            scale_before[name] = _sizes(cur, name)
        cur.close()
        scale_timed = run_timed_phase(
            conn, _corpus(SCALE_TIMED + SCALE_WARMUP, seed=4242), org,
            writes=SCALE_TIMED, batch=SCALE_BATCH, warmup=SCALE_WARMUP,
            tag="scalew")
        conn.commit()
        shapes_at_scale = _summarise(conn, scale_timed["samples"], scale_before,
                                     extra_rows=0)

        cur = conn.cursor()
        cur.execute("SHOW server_version")
        server_version = cur.fetchone()[0]
        cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
        vector_version = cur.fetchone()[0]
        cur.close()
        conn.close()
        report.update({"shapes": shapes, "shapes_at_scale": shapes_at_scale,
                       "scale_timed_rounds": scale_timed["rounds"], "scale": scale,
                       "server": {"postgres": server_version,
                                  "pgvector": vector_version}})
    finally:
        if container is not None:
            subprocess.run(("docker", "rm", "-f", container),
                           capture_output=True, text=True)

    report.update({"host": {"platform": platform.platform(),
                            "loadavg_before": load_before,
                            "loadavg_after": load_after,
                            "timed_phase_seconds": elapsed_s},
                   "timed_rounds": rounds})
    report = _derive(report)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
