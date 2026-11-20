#!/usr/bin/env python3
"""#13906 — does the record's dense leg RETURN what the graph's dense leg returns?

THE GAP THIS FILLS, stated as the three instruments that do NOT answer it:

* ``tools/entity_vector_parity_census.py`` (#13629) answers *"does the record
  HOLD a vector for every graph node that has one"* — a COPY census, no query;
* ``tests/test_4997_vector_index_parity.py`` compares index-vs-scan **inside**
  the graph — never the record against the graph;
* ``tests/test_11642_vector_move_live.py`` executes the record's SQL and its
  shortfall assertion — it never compares its answer to the leg it replaced.

So the one question the flip (#11642 / #13610) has never had a gate for is the
one the decision's own §6.1 makes load-bearing: **for the same tenant data and
the same query, do the two legs agree on ids, on order, and on recall@k?**

## The four readers, and why ground truth is a THIRD computation

``truth`` is computed in this process, in numpy, over the corpus THIS HARNESS
WROTE. Neither store's index is involved, so no store can bias the oracle
(the research rung is explicit that ground truth must not be regenerated from
the destination index — see ``docs/research/2026-11-20-13906-vector-parity/``).

| reader | how it answers |
|---|---|
| ``truth_cosine`` | numpy exact scan, cosine distance, ``(distance ASC, key)`` |
| ``truth_euclidean`` | numpy exact scan, euclidean, same ordering rule |
| ``record`` | ``record_vector.vector_search`` → pgvector HNSW, **cosine** |
| ``graph_scan`` | ``search_engine.run_vector_query`` → the graph's brute-force fallback, **euclidean** |
| ``graph_index`` | the same call with an HNSW index created first, **cosine** |

## ⛔ THE METRIC IS NOT THE SAME ON THE TWO GRAPH BRANCHES, AND THAT IS A FINDING

The graph's INDEX branch scores cosine (`similarityFunction: 'cosine'`, and it
converts ``1 - d``); its brute-force SCAN branch scores **euclidean**
(``vec.euclideanDistance``, converted ``1/(1+d)``). Our embeddings are
un-normalised model output (``embeddings.compute_embeddings``: *"the same
un-normalised model output"*), so cosine and euclidean orderings are **not**
guaranteed to agree. This harness measures both rather than assuming either:

* ``record`` and ``graph_index`` are both compared to ``truth_cosine`` — the
  apples-to-apples parity test for the migration (the index the record replaces
  was COSINE);
* ``graph_scan`` is compared to ``truth_euclidean`` — *what production does
  today* on a store with no vector index (measured: the live graph has none);
* ``graph_scan`` vs ``truth_cosine`` is reported as ``metric_divergence``,
  INFORMATIONAL — the number of ids the current production path would rank
  differently from the metric the decision names canonical.

## Usage

    uv run python tools/test_lane.py uri                # a FalkorDB for this lane
    uv run python tools/vector_parity_13906.py --record-dsn "$TEST_RECORD_DB_URI"

    # prove the harness can FAIL (this is a gate requirement, not a demo)
    uv run python tools/vector_parity_13906.py ... --poison id
    uv run python tools/vector_parity_13906.py ... --poison order
    uv run python tools/vector_parity_13906.py ... --poison recall
    uv run python tools/vector_parity_13906.py ... --poison vector

Exit codes: ``0`` parity within policy · ``1`` a required comparison diverged ·
``2`` cannot run (no store, no schema, no driver) — never reported as parity.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

#: Imported, not restated: the one vocabulary the read surfaces use. The
#: `Row.eligible` predicate below is the CALLERS' rule, so it reads the rule from
#: its owner (`live._terminal_excluded` / `search_engine._status_vocab_for`)
#: rather than growing a second copy that could drift from it.
from tortoise.live import TERMINAL_EXCLUDED_STATUSES as TERMINAL_STATUSES

DIM = 384

#: The record's application role — `NOBYPASSRLS`, the role `scope_connection`
#: will accept (`tortoise/record_serve.py` names it as the runbook's role).
#: A connect-time `options=-c role=` is libpq's spelling of `SET ROLE`; the
#: query string is escaped with `quote`, never `urlencode`'s default `+`, because
#: libpq does not decode `+` as a space (measured, `#9600` cycle 7).
APP_ROLE = "tortoise_record_app"

#: Point is the label this harness seeds. ⛔ NOT an arbitrary choice: `:Point`
#: holds the pre-existing corpus this migration is about (16,693 of the ~35,312
#: measured nodes in #13906) and it is the label the armed cutover owns. The
#: other four served labels are structurally identical on the record side and
#: pinned by their own live tests (`test_11642_vector_move_live.py`,
#: `test_13422_object_subject_dense_leg.py`); adding their seeding here would be
#: four more INSERT shapes for no new question.
LABEL = "point"
LABEL_CYPHER = "Point"

#: Default tie tolerance for a *reported distance*. Cosine on unit-basis
#: synthetic rows is exact; real vectors carry float noise, so a tie test must
#: be a tolerance, not equality.
TIE_TOL = 1e-6


# ── corpus ──────────────────────────────────────────────────────────────────

@dataclass
class Row:
    key: str
    embedding: list[float] | None
    status: str | None = None
    outdated: bool | None = None
    updated_at: str = "2026-01-01"
    tenant: str = "ours"

    def eligible(self, dim: int) -> bool:
        """The population BOTH legs must serve, restated from the callers' rules.

        Three rules, each one owned elsewhere and imported-by-reference rather
        than re-decided here:

        * a width other than the store's is not ANN material — record side
          `public.record_embedding_vector` returns NULL, graph side the shared
          read seam drops the row (`stored_embedding_vector_expr`);
        * a TERMINAL status is excluded by `live.TERMINAL_EXCLUDED_STATUSES`
          (`_status_vocab_for` is the one dispatcher);
        * the legacy `outdated=true` flag is excluded for Points.
        """
        if self.embedding is None or len(self.embedding) != dim:
            return False
        if self.status in TERMINAL_STATUSES:
            return False
        if self.outdated:
            return False
        return True


def _unit(hot: int, dim: int = DIM) -> list[float]:
    values = [0.0] * dim
    values[hot] = 1.0
    return values


def build_corpus(seed: int = 13906, n_random: int = 48) -> list[Row]:
    """A corpus that exercises the edges this migration can fail on.

    ⛔ Un-normalised on purpose. `embeddings.compute_embeddings` stores the
    model's raw output, so a corpus of unit vectors would hide the
    cosine-vs-euclidean divergence this harness exists to measure — and hide it
    in the direction that reads as "parity".
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    rows: list[Row] = []

    for i in range(n_random):
        vec = rng.normal(size=DIM).astype(float)
        # Deliberately NOT unit length, and NOT the same length as its peers:
        # a norm that varies is what makes cosine and euclidean disagree.
        vec *= float(rng.uniform(0.35, 2.5))
        rows.append(Row(key=f"r{i:03d}", embedding=vec.tolist()))

    # A TIE GROUP of three byte-identical vectors. Any comparison that ignores
    # ties will call a legitimate permutation a divergence — and a comparison
    # that ignores ties in the OTHER direction would call a real re-order a pass.
    tie = rows[0].embedding
    for j in range(3):
        rows.append(Row(key=f"tie{j}", embedding=list(tie)))

    # The query anchor: an exact match the top-1 assertion can name.
    rows.append(Row(key="anchor", embedding=_unit(0)))

    # Edge: a caller-owned 768-wide vector (PR #3018 stores these VERBATIM).
    # It must be stored on BOTH sides and ANN-invisible on BOTH sides.
    rows.append(Row(key="wide768", embedding=_unit(1, dim=768)))

    # Edge: no embedding at all.
    rows.append(Row(key="no_embedding", embedding=None))

    # Edge: terminal rows whose vector IS the query — if the #689 cage fails on
    # either leg, these rank FIRST and the diff is loud rather than subtle.
    rows.append(Row(key="retracted", embedding=_unit(0), status="retracted"))
    rows.append(Row(key="outdated", embedding=_unit(0), outdated=True))

    # Edge: another tenant, same vectors as the query. A tenancy leak on the
    # record leg ranks FOREIGN first; the graph side gets its own graph.
    for j in range(3):
        rows.append(Row(key=f"other{j}", embedding=_unit(0), tenant="other"))

    return rows


# ── ground truth ────────────────────────────────────────────────────────────

def _cosine_distance(a, b) -> float:
    import numpy as np

    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        # A zero vector has no direction: cosine is undefined. The stores both
        # treat it as maximal distance rather than crashing, so truth does too.
        return 1.0
    return 1.0 - float(np.dot(a, b) / denom)


def _euclidean_distance(a, b) -> float:
    import numpy as np

    return float(np.linalg.norm(np.asarray(a, float) - np.asarray(b, float)))


def truth(rows: list[Row], query: list[float], k: int, *, metric: str,
          tenant: str = "ours",
          dim: int | None = None) -> list[tuple[str, float]]:
    """The oracle: an exact scan over the corpus this harness wrote.

    ``dim`` is the store's own width for this read, and it is taken from the
    QUERY vector rather than a literal — ``run_vector_query`` does exactly that
    (``len(query_vec)``), and #4280 is the reason it may not hardcode 384: an
    index-less store reads back whatever width its own encoder produced. Passing
    ``None`` (the default) applies that same rule here, so the oracle and the
    leg can never disagree about which rows are ANN material.
    """
    dim = len(query) if dim is None else dim
    fn = _cosine_distance if metric == "cosine" else _euclidean_distance
    scored = [
        (row.key, fn(row.embedding, query))
        for row in rows
        if row.tenant == tenant and row.eligible(dim)
    ]
    # `(distance ASC, key ASC)` — a STABLE, unique tiebreak so the oracle is
    # reproducible. The stores' own tie rule is recency-then-id, which is why
    # `compare` treats a within-tie permutation as acceptable rather than red.
    scored.sort(key=lambda item: (item[1], item[0]))
    return scored[:k]


# ── comparison ──────────────────────────────────────────────────────────────

def compare(truth_rows: list[tuple[str, float]], candidate_ids: list[str],
            *, candidate_distances: list[float] | None = None,
            k: int | None = None, distance_tol: float = 1e-4) -> dict:
    """Diff one candidate list against the oracle.

    Three questions, kept separate because they fail differently:

    * ``ids`` — set agreement, i.e. recall@k. A miss here is a row the leg
      cannot see; no ordering policy can excuse it.
    * ``order`` — positional agreement. A permutation is a divergence ONLY if
      it moves ids across DIFFERENT distances: a swap inside a tie group is the
      research rung's "tie-equivalence" case and is reported as ``tie``.
    * ``recall@k`` — ``|truth ∩ candidate| / |truth|``.

    ``candidate_distances`` adds a fourth: the VALUE, not just the order. It is
    not redundant — measured on this harness, a `--poison vector` that negated
    one stored vector moved its distance from 0.0 to 2.0 while the corpus'
    other rows were far enough away that the returned IDS AND ORDER did not
    change, so an id-only comparison called a genuinely different store
    "parity". A distance check is what makes the harness able to fail there.
    """
    k = k if k is not None else len(truth_rows)
    truth_ids = [key for key, _ in truth_rows]
    # ⛔ NOT truncated to `k`. A leg that returns MORE than it was asked for is a
    # defect, and truncating the candidate list here is exactly how a comparator
    # hides it (pinned by `test_an_extra_id_is_reported_even_when_recall_is_full`).
    cand = list(candidate_ids)
    cand_set, truth_set = set(cand), set(truth_ids)
    overlap = cand_set & truth_set

    distance_of = {key: dist for key, dist in truth_rows}
    tie_groups: dict[float, set[str]] = {}
    for key, dist in truth_rows:
        bucket = round(dist / TIE_TOL) if TIE_TOL else 0.0
        tie_groups.setdefault(bucket, set()).add(key)

    order = "exact"
    mismatches: list[dict] = []
    for pos in range(min(len(truth_ids), len(cand))):
        if truth_ids[pos] == cand[pos]:
            continue
        a, b = truth_ids[pos], cand[pos]
        # A permutation inside ONE distance bucket is a legitimate tie.
        same_bucket = any(a in g and b in g for g in tie_groups.values())
        if a in overlap and b in overlap and same_bucket:
            order = "tie" if order == "exact" else order
            continue
        order = "diverged"
        mismatches.append({"position": pos, "truth": a, "candidate": b,
                           "truth_distance": distance_of.get(a),
                           "candidate_distance": distance_of.get(b)})
        if len(mismatches) >= 5:  # bounded: a report, not a transcript
            break

    distance = {"ok": None, "max_delta": None, "worst": None}
    if candidate_distances is not None:
        worst, worst_delta = None, 0.0
        for key, dist in zip(cand, candidate_distances):
            if key not in distance_of or dist is None:
                continue
            delta = abs(float(dist) - float(distance_of[key]))
            if delta > worst_delta:
                worst, worst_delta = key, delta
        distance = {"ok": worst_delta <= distance_tol, "max_delta": worst_delta,
                    "worst": worst, "tolerance": distance_tol}

    return {
        "distance": distance,
        "ids_equal": cand_set == truth_set,
        "order": order,
        "recall": (len(overlap) / len(truth_set)) if truth_set else 1.0,
        "missing": sorted(truth_set - cand_set)[:10],
        "extra": sorted(cand_set - truth_set)[:10],
        "order_mismatches": mismatches,
        "candidate_ids": cand,
        "truth_ids": truth_ids,
    }


def poison(candidate_ids: list[str], truth_rows: list[tuple[str, float]],
           kind: str) -> list[str]:
    """Deliberately corrupt a candidate list so the harness must report a diff.

    ⛔ THIS IS THE FALSIFIER, and it is why the harness is allowed to say "pass".
    A comparison that has never returned ``False`` is not evidence about either
    store; it is evidence about the comparison's own blind spots.
    """
    out = list(candidate_ids)
    if kind == "id":
        # Replace the best hit with a key that is in the corpus but NOT a hit.
        if out:
            out[0] = "r999_absent_from_truth"
    elif kind == "order":
        # Swap two hits that do NOT share a distance bucket.
        i = next((n for n in range(1, len(out)) if out[n] != out[0]), None)
        if i is not None:
            out[0], out[i] = out[i], out[0]
    elif kind == "recall":
        # Return fewer rows than the oracle does.
        out = out[:-1] if out else out
    else:
        raise ValueError(f"unknown poison {kind!r}")
    return out


# ── stores ──────────────────────────────────────────────────────────────────

LOOPBACK = {"127.0.0.1", "localhost", "::1", "host.docker.internal"}


def _assert_loopback(host: str, what: str, allow_remote: bool) -> None:
    """Refuse to SEED a non-loopback store without an explicit opt-in.

    ⛔ This harness WRITES: it creates an organization, a graph row, node
    identities and `point` rows, then deletes them. Handed a production DSN by
    accident, the writes land in the record — so the default is that both stores
    are local, the refusal names which one is not, and `--allow-remote-writes` is
    the deliberate act. A guard that only warned would be a guard that gets
    scrolled past.
    """
    if allow_remote or host in LOOPBACK:
        return
    raise SystemExit(
        f"refusing to seed a NON-LOCAL {what} ({host}): this harness writes "
        f"(org, graph, node_identity, point rows) and deleting them afterwards "
        f"does not make the write safe if the run is interrupted. Pass "
        f"--allow-remote-writes if you really mean it.")


def app_role_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    query = parse_qs(parts.query)
    query["options"] = [f"-c role={APP_ROLE}"]
    return urlunsplit(parts._replace(
        query=urlencode(query, doseq=True, quote_via=quote)))


class RecordStore:
    def __init__(self, dsn: str, org: str, graph: str):
        import psycopg2

        self.org, self.graph = org, graph
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True
        self.app_dsn = app_role_dsn(dsn)

    def require_schema(self) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='point' "
                "AND column_name='embedding_vec'")
            if not cur.fetchone():
                raise SystemExit(
                    "cannot run: public.point.embedding_vec is absent — the "
                    "pgvector migration has not been applied to this database, "
                    "so every comparison would report the graph fallback as a "
                    "clean answer")

    def seed(self, rows: list[Row]) -> None:
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) "
                        "VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                        (self.org, self.org))
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (self.graph, self.org, self.graph))
            for row in rows:
                if row.tenant != "ours":
                    continue
                cur.execute(
                    "INSERT INTO public.node_identity "
                    "(org_id, graph_id, node_label, node_key) "
                    "VALUES (%s, %s, 'Point', %s) ON CONFLICT DO NOTHING",
                    (self.org, self.graph, row.key))
                cur.execute(
                    "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                    "status, outdated, embedding, is_operator, op_type, "
                    "updated_at, created_at, point_kind) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)",
                    (self.org, self.graph, row.key, row.key, row.status,
                     row.outdated,
                     None if row.embedding is None else json.dumps(row.embedding),
                     None, None, row.updated_at, row.updated_at, "claim"))

    def seed_foreign_tenant(self, rows: list[Row]) -> None:
        """The SAME vectors under a second org — a leak would rank it first."""
        other_org = f"{self.org}_foreign"
        other_graph = f"{self.graph}_foreign"
        with self.conn.cursor() as cur:
            cur.execute("INSERT INTO public.organizations (id, name) "
                        "VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
                        (other_org, other_org))
            cur.execute("INSERT INTO public.graphs (id, org_id, name) "
                        "VALUES (%s, %s, %s) ON CONFLICT (id) DO NOTHING",
                        (other_graph, other_org, other_graph))
            for row in rows:
                if row.tenant != "other":
                    continue
                cur.execute(
                    "INSERT INTO public.node_identity "
                    "(org_id, graph_id, node_label, node_key) "
                    "VALUES (%s, %s, 'Point', %s) ON CONFLICT DO NOTHING",
                    (other_org, other_graph, row.key))
                cur.execute(
                    "INSERT INTO public.point (org_id, graph_id, node_key, id, "
                    "status, outdated, embedding, is_operator, op_type, "
                    "updated_at, created_at, point_kind) "
                    "VALUES (%s, %s, %s, %s, NULL, NULL, %s::jsonb, NULL, NULL, "
                    "%s, %s, 'claim')",
                    (other_org, other_graph, row.key, row.key,
                     json.dumps(row.embedding), row.updated_at, row.updated_at))

    def read(self, query: list[float], k: int) -> list[tuple[str, float, object]]:
        from tortoise import record_vector

        scope = record_vector._Scope(org_id=self.org, graph_id=self.graph)
        return record_vector.vector_search(
            scope, LABEL, query, k, record_dsn=self.app_dsn) or []

    def teardown(self) -> None:
        with self.conn.cursor() as cur:
            for org in (self.org, f"{self.org}_foreign"):
                cur.execute("DELETE FROM public.point WHERE org_id = %s", (org,))
                cur.execute("DELETE FROM public.node_identity WHERE org_id = %s",
                            (org,))
                cur.execute("DELETE FROM public.graphs WHERE org_id = %s", (org,))
                cur.execute("DELETE FROM public.organizations WHERE id = %s", (org,))

    def close(self) -> None:
        self.conn.close()


#: The record's table/column for one label, and whether a vector index exists.
class GraphStore:
    def __init__(self, host: str, port: int, graph: str):
        from falkordb import FalkorDB

        self.graph_name = graph
        self.client = FalkorDB(host=host, port=port)
        self.g = self.client.select_graph(graph)

    def reset(self) -> None:
        try:
            self.g.delete()
        except Exception:  # noqa: BLE001 — a missing graph is the goal
            pass
        self.g = self.client.select_graph(self.graph_name)

    def seed(self, rows: list[Row]) -> None:
        for row in rows:
            if row.tenant != "ours":
                continue
            props = {"id": row.key, "key": row.key}
            if row.status is not None:
                props["status"] = row.status
            if row.outdated is not None:
                props["outdated"] = row.outdated
            props["updatedAt"] = row.updated_at
            props["createdAt"] = row.updated_at
            if row.embedding is None:
                self.g.query(
                    "CREATE (n:Point) SET n += $props", params={"props": props})
                continue
            if len(row.embedding) == DIM:
                # The writer's form: a stored vector is `vecf32(...)`.
                self.g.query(
                    "CREATE (n:Point) SET n += $props "
                    "SET n.embedding = vecf32($vec)",
                    params={"props": props, "vec": [float(v) for v in row.embedding]})
            else:
                # A caller-owned width is stored VERBATIM — a plain list, not a
                # `Vectorf32` (PR #3018). Seeding it any other way would test a
                # shape no writer produces, and would trip #8430's declared
                # residual instead of the seam that exists for this case.
                self.g.query(
                    "CREATE (n:Point) SET n += $props SET n.embedding = $vec",
                    params={"props": props,
                            "vec": [float(v) for v in row.embedding]})

    def create_index(self, ef_runtime: int | None = None) -> str:
        extra = f", efRuntime: {int(ef_runtime)}" if ef_runtime else ""
        try:
            self.g.query(
                "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine'{extra}}}")
            return "cypher"
        except Exception as e:  # noqa: BLE001
            if "already" in str(e).lower():
                return "existing"
            try:
                self.g.query(
                    "CREATE VECTOR INDEX FOR (p:Point) ON (p.embedding) "
                    f"OPTIONS {{dimension: {DIM}, similarityFunction: 'cosine', "
                    f"'HNSW'{extra}}}")
                return "procedure"
            except Exception as e2:  # noqa: BLE001
                if "already" in str(e2).lower():
                    return "existing"
                raise

    def wait_index_ready(self, probe: list[float], timeout_s: float = 60.0) -> bool:
        """Block until `queryNodes` ANSWERS — the index build is not synchronous.

        ⛔ MEASURED, and it silently corrupts the measurement if skipped: on
        FalkorDB 4.20.6 a freshly-created HNSW index returns ZERO rows for a few
        seconds while it builds, and `run_vector_query` answers that with the
        `#8429` fall-through to the brute-force scan. The arm then reports
        ``mechanisms: ["index", "scan_fallback"]`` and the "indexed" column is
        really a second scan — which is what this harness produced on its first
        run against a real store (`q_anchor` graph_index recall 0.5 = the scan's
        number, not an ANN result). A harness that measures the fallback while
        claiming to measure the index is the failure class this whole file is
        about, so readiness is a WAIT with a loud failure, never a `sleep`.
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            try:
                res = self.g.query(
                    "CALL db.idx.vector.queryNodes('Point', 'embedding', 1, "
                    "vecf32($q)) YIELD node RETURN node.id",
                    params={"q": [float(v) for v in probe]})
                if res.result_set:
                    return True
            except Exception:  # noqa: BLE001, RUF100 — not built yet, or not registered yet
                pass
            time.sleep(0.25)
        return False

    def index_params(self) -> dict:
        """The parameters the store ACTUALLY built the index with.

        ⛔ Read from the engine, never assumed. FalkorDB's default `efRuntime` is
        **10**, and a `queryNodes` call takes exactly FOUR arguments on 4.20.6
        (measured: passing a fifth, `{efRuntime: N}`, is refused with
        ``requires 4 arguments, got 5``), so the ef cannot be raised per query —
        only by re-creating the index. Recording the parameters is what turns a
        recall shortfall into a diagnosis instead of a bug report.
        """
        try:
            # `CALL db.indexes()` row shape on 4.20.6 (measured):
            #   [label, fields, types, OPTIONS, language, stopwords, entity, status, …]
            # — so the index PARAMETERS are column 3, keyed by property. Reading
            # column 2 instead yields `{'embedding': ['VECTOR']}` and an empty
            # params dict, which silently costs the artifact its diagnosis.
            for row in self.g.query("CALL db.indexes()").result_set:
                if row[0] != LABEL_CYPHER or len(row) < 4:
                    continue
                params = row[3] or {}
                if "embedding" in params:
                    return dict(params["embedding"])
        except Exception:  # noqa: BLE001, RUF100
            pass
        return {}

    def rebuild_with_ef(self, ef_runtime: int, rows: list[Row]) -> None:
        """Drop + re-create the index at a higher `efRuntime`, re-seeding rows.

        A re-seed rather than an index DROP because the DROP spelling varies
        across engines and a silently failed drop would leave the OLD index in
        place — i.e. the `efRuntime` diagnostic would measure the default again
        and report the gap as unexplained.
        """
        self.reset()
        self.seed(rows)
        self.create_index(ef_runtime=ef_runtime)

    def read(self, query: list[float], k: int) -> dict:
        from tortoise import search_engine

        trace: list[dict] = []
        rows = search_engine.run_vector_query(
            self.g, query, limit=k, is_embedded=False,
            entity_type=LABEL, leg_trace=trace)
        mechanism = trace[-1].get("mechanism") if trace else None
        # Similarity → distance, PER BRANCH. The index branch converts `1 - d`
        # (cosine); the scan branch converts `1 / (1 + d)` (euclidean). Reading
        # the mechanism from the leg's own trace rather than inferring it is the
        # only way to invert the right one.
        if mechanism == "index":
            distances = [(key, 1.0 - sim) for key, sim in rows]
        else:
            distances = [(key, (1.0 / sim) - 1.0 if sim > 0 else math.inf)
                         for key, sim in rows]
        return {"rows": distances, "mechanism": mechanism, "trace": trace}

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass


# ── the run ─────────────────────────────────────────────────────────────────

def run(args) -> dict:
    rows = build_corpus(seed=args.seed, n_random=args.n_random)
    k = args.k

    run_id = uuid.uuid4().hex[:10]
    org = f"org_13906_parity_{run_id}"
    graph = f"graph_13906_parity_{run_id}"
    graph_name = f"g13906parity{run_id}"

    host = urlsplit(args.record_dsn).hostname if args.record_dsn else None
    if host:
        _assert_loopback(host, "record database", args.allow_remote_writes)
    if args.graph_port:
        _assert_loopback(args.graph_host, "graph store", args.allow_remote_writes)

    record = None
    store = None
    if args.record_dsn:
        record = RecordStore(args.record_dsn, org, graph)
        record.require_schema()
    if args.graph_port:
        store = GraphStore(args.graph_host, args.graph_port, graph_name)

    if record is None or store is None:
        raise SystemExit(
            "cannot run: both --record-dsn (a Postgres carrying the record "
            "migrations AND pgvector) and --graph-port (a FalkorDB) are "
            "required — a one-sided run is not a parity measurement")

    queries = _queries(rows, args.queries)
    report: dict = {
        "issue": "#13906",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "k": k, "seed": args.seed, "n_rows": len(rows),
        "label": LABEL,
        "poison": args.poison,
        "arms": {},
        "queries": [],
    }

    try:
        record.seed(rows)
        record.seed_foreign_tenant(rows)
        store.reset()
        # ⛔ THE `--poison vector` MUTATION HAPPENS AT SEED TIME, not after the
        # arms are read. A store divergence that is applied after `scan_answers`
        # was taken leaves that arm measuring the UNPOISONED data and the poison
        # goes undetected — measured on this harness's first attempt: `vector`
        # reported POISON NOT DETECTED while the other three were caught. The
        # oracle stays on the unperturbed corpus, which is the point.
        store_rows = rows
        if args.poison == "vector":
            store_rows = [
                Row(key=r.key,
                    # ⛔ NOT a negation. `-unit` is 2.0 away from the query while
                    # this corpus' unrelated rows are 15-25 away, so a negated
                    # anchor stays in the top-k and an id-only comparison sees
                    # nothing (measured). `-100` on every axis is beyond every
                    # corpus row, so the row leaves the window AND its distance
                    # moves — the two detections a divergence needs.
                    embedding=([(-100.0 if i == 0 else 0.0) for i in range(len(r.embedding))]
                               if r.key == "anchor" and r.embedding else r.embedding),
                    status=r.status, outdated=r.outdated,
                    updated_at=r.updated_at, tenant=r.tenant)
                for r in rows]
        store.seed(store_rows)
        rows_ours = [r for r in rows if r.tenant == "ours"]

        # ── record arm ────────────────────────────────────────────────────
        rec_answers = {qid: record.read(qvec, k) for qid, qvec in queries}
        report["arms"]["record"] = {"driver": "pgvector HNSW, cosine"}

        # ── graph arms: scan first (production today), then indexed ──────
        scan_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
        report["arms"]["graph_scan"] = {
            "driver": "run_vector_query brute-force scan, euclidean",
            "mechanisms": sorted({a["mechanism"] for a in scan_answers.values()}),
        }
        index_api = store.create_index()
        # ⛔ The index is built ASYNCHRONOUSLY. Wait for it to ANSWER, or the
        # "indexed" arm silently measures the scan fallback (see
        # `wait_index_ready`). Failing loudly is correct: an index arm that is
        # not the index arm is not a measurement.
        ready = store.wait_index_ready(queries[0][1])
        index_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
        mechanisms = sorted({a["mechanism"] for a in index_answers.values()})
        report["arms"]["graph_index"] = {
            "driver": f"run_vector_query HNSW ({index_api}), cosine",
            "index_ready": ready,
            "mechanisms": mechanisms,
        }
        report["arms"]["graph_index"]["index_params"] = store.index_params()
        if ready and mechanisms != ["index"]:
            raise SystemExit(
                f"cannot run: the graph index is ready but the arm answered via "
                f"{mechanisms} — the indexed comparison is not measuring the "
                f"index, so any parity it reports would be about the scan")

        # ── the efRuntime diagnostic ──────────────────────────────────────
        # Run ONLY when the default-arm recall is short: it exists to explain a
        # shortfall, and a green default arm has nothing to explain. It is
        # INFORMATIONAL (it re-creates an index production would not have) and
        # its purpose is to separate "the graph index is semantically different"
        # from "the graph index answers with efRuntime=10".
        idx_recall = min(
            compare(truth(rows, qvec, k, metric="cosine"),
                    [key for key, _d in index_answers[qid]["rows"]], k=k)["recall"]
            for qid, qvec in queries)
        report["arms"]["graph_index"]["min_recall"] = idx_recall
        ef200_answers: dict[str, dict] = {}
        if idx_recall < 1.0:
            ef = args.ef_diagnostic
            store.rebuild_with_ef(
                ef, [r for r in store_rows if r.tenant == "ours"])
            if not store.wait_index_ready(queries[0][1]):
                raise SystemExit("cannot run: the ef-diagnostic index never answered")
            ef200_answers = {qid: store.read(qvec, k) for qid, qvec in queries}
            report["arms"][f"graph_index_ef{ef}"] = {
                "driver": f"run_vector_query HNSW at efRuntime={ef} (diagnostic)",
                "index_params": store.index_params(),
                "mechanisms": sorted({a["mechanism"] for a in ef200_answers.values()}),
            }

        for qid, qvec in queries:
            t_cos = truth(rows, qvec, k, metric="cosine")
            t_euc = truth(rows, qvec, k, metric="euclidean")

            rec_ids = [key for key, _d, _r in rec_answers[qid]]
            scan_ids = [key for key, _d in scan_answers[qid]["rows"]]
            idx_ids = [key for key, _d in index_answers[qid]["rows"]]

            if args.poison in ("id", "order", "recall"):
                rec_ids = poison(rec_ids, t_cos, args.poison)
            if args.poison == "vector":
                # The graph arm is re-read AFTER the rewrite; that is the point.
                index_answers[qid] = store.read(qvec, k)
                idx_ids = [key for key, _d in index_answers[qid]["rows"]]

            entry = {
                "query": qid,
                "truth_cosine": [key for key, _ in t_cos],
                # ⛔ DISTANCES ARE ALWAYS PASSED, never conditionally. An earlier
                # cut skipped them whenever the candidate list had been poisoned
                # (`len(answers) != len(ids)`), which left `distance.ok = None` on
                # exactly the runs where a value-level check matters most — and
                # `_verdict` treats "unmeasured" as "not failed". Two lengths that
                # disagree now simply stop the zip, so the ids check (which is the
                # one that fails) still sees the whole poisoned list.
                "record": compare(
                    t_cos, rec_ids, k=k,
                    candidate_distances=[d for _k2, d, _r in rec_answers[qid]]),
                "graph_index": compare(
                    t_cos, idx_ids, k=k,
                    candidate_distances=[d for _k2, d in index_answers[qid]["rows"]]),
                "graph_scan_vs_euclidean": compare(
                    t_euc, scan_ids, k=k,
                    candidate_distances=[d for _k2, d in scan_answers[qid]["rows"]]),
                "graph_scan_vs_cosine": compare(t_cos, scan_ids, k=k,
                                                candidate_distances=None),
            }
            if ef200_answers:
                entry[f"graph_index_ef{args.ef_diagnostic}"] = compare(
                    t_cos, [key for key, _d in ef200_answers[qid]["rows"]], k=k,
                    candidate_distances=[d for _k2, d in ef200_answers[qid]["rows"]])
            # ⛔ THE UNDER-RETURN COLUMN, and it is why this harness earned its
            # keep. `queryNodes` picks its `$limit` rows BEFORE the `#689`
            # predicate is applied (the predicate rides between `YIELD` and
            # `RETURN`), so a terminal row inside the candidate window shrinks the
            # answer BELOW k and nothing over-fetches or asserts. Measured on the
            # first live run: `k=10` returned **8** rows for a query whose
            # neighbourhood contains a retracted and an outdated Point. The record
            # leg applies the predicate server-side ahead of its LIMIT, so it
            # returns 10 — which is the asymmetry `#5090` Q2 forbids.
            entry["graph_index_returned"] = len(index_answers[qid]["rows"])
            entry["graph_scan_returned"] = len(scan_answers[qid]["rows"])
            entry["record_returned"] = len(rec_answers[qid])
            entry["graph_index_shortfall"] = k - len(index_answers[qid]["rows"])
            entry["record_vs_graph_scan"] = _cross_recall(rec_ids, scan_ids)
            entry["record_vs_graph_index"] = _cross_recall(rec_ids, idx_ids)
            entry["metric_divergence_ids"] = sorted(
                {key for key, _ in t_cos} ^ {key for key, _ in t_euc})
            report["queries"].append(entry)

    finally:
        if args.keep:
            print(f"[keep] org={org} graph={graph} cypher_graph={graph_name}")
        else:
            try:
                record.teardown()
            finally:
                store.close()
                record.close()

    report["verdict"] = _verdict(report, poisoned=args.poison)
    return report


def _cross_recall(a: list[str], b: list[str]) -> dict:
    sa, sb = set(a), set(b)
    return {"recall": len(sa & sb) / len(sa) if sa else 1.0,
            "only_a": sorted(sa - sb)[:10], "only_b": sorted(sb - sa)[:10]}


def _verdict(report: dict, *, poisoned: str | None) -> dict:
    """Which comparisons are REQUIRED, and did any fail?

    Two are required, and they answer different questions:

    * ``record`` vs ``truth_cosine`` — **the acceptance for the migration**: the
      leg being moved to, on the metric its index is built with;
    * ``graph_scan_vs_euclidean`` — **the comparator's own sanity check**: the
      graph scan must reproduce ITS oracle, or the harness's eligibility
      predicate or distance inversion is wrong and nothing else here is verified.

    Two are INFORMATIONAL, because they measure the leg being REPLACED and a
    metric drift this issue does not decide:

    * ``graph_index`` vs ``truth_cosine`` — measured on a real store at
      FalkorDB's DEFAULT ``efRuntime: 10``, where it under-returns (recall 0.33
      at k=3, 0.80 at k=10, 0.95 at k=40 on a 55-vector corpus). Gating on it
      would red this harness for the state of the leg the record replaces; the
      ``graph_index_ef200`` diagnostic arm exists to show the shortfall is an ef
      setting and not a semantic difference;
    * ``graph_scan`` vs ``truth_cosine`` — the cosine/euclidean drift.
    """
    failures: list[str] = []
    divergences = 0
    for entry in report["queries"]:
        q = entry["query"]
        # REQUIRED: the arm the migration is moving TO, against the metric the
        # record's index is built with.
        cmp_ = entry["record"]
        if (not cmp_["ids_equal"] or cmp_["order"] == "diverged"
                or (cmp_.get("distance") or {}).get("ok") is False):
            failures.append(
                f"{q}: record diverged from truth_cosine "
                f"(recall={cmp_['recall']:.3f}, order={cmp_['order']}, "
                f"distance_delta={(cmp_.get('distance') or {}).get('max_delta')})")
        # REQUIRED, as the COMPARATOR'S OWN sanity check: the graph scan must
        # reproduce its own oracle. If it does not, the harness's distance
        # inversion or its eligibility predicate is wrong and every other column
        # here is unverified.
        cmp_ = entry["graph_scan_vs_euclidean"]
        if (not cmp_["ids_equal"] or cmp_["order"] == "diverged"
                or (cmp_.get("distance") or {}).get("ok") is False):
            failures.append(
                f"{q}: graph_scan diverged from truth_euclidean "
                f"(recall={cmp_['recall']:.3f}, order={cmp_['order']}, "
                f"distance_delta={(cmp_.get('distance') or {}).get('max_delta')})")
        divergences += len(entry["metric_divergence_ids"])

    ok = not failures
    if poisoned:
        # The falsifier: a poisoned run MUST fail. A poisoned run that reports
        # parity is a BLIND harness, and saying so is the only useful output it
        # has — so the verdict is red either way (the run is never a pass), and
        # `poison_status` records WHICH reading it produced.
        status = ("POISON DETECTED" if not ok
                  else "POISON NOT DETECTED — HARNESS BLIND")
        return {"ok": False, "poison": poisoned,
                "poison_status": status,
                "failures": failures or [
                    "poison produced no divergence — the comparison is blind"],
                "informational": {"metric_divergence_ids_total": divergences}}
    return {"ok": ok, "failures": failures,
            "informational": {"metric_divergence_ids_total": divergences,
                              "metric_divergence_note":
                                  "ids the graph SCAN (euclidean, production "
                                  "today on an index-less store) ranks "
                                  "differently from the canonical cosine "
                                  "metric — informational, not a gate"}}


def _queries(rows: list[Row], count: int) -> list[tuple[str, list[float]]]:
    import numpy as np

    rng = np.random.default_rng(7919)
    out: list[tuple[str, list[float]]] = [("q_anchor", _unit(0)),
                                          ("q_tie", list(rows[0].embedding))]
    for i in range(max(0, count - len(out))):
        vec = rng.normal(size=DIM).astype(float)
        vec *= float(rng.uniform(0.35, 2.5))
        out.append((f"q{i:02d}", vec.tolist()))
    return out[:count]


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--record-dsn", default=os.environ.get("TEST_RECORD_DB_URI")
                    or os.environ.get("TORTOISE_RECORD_DSN"))
    ap.add_argument("--graph-host", default="localhost")
    ap.add_argument("--graph-port", type=int,
                    default=int(os.environ.get("TORTOISE_TEST_DOCKER_PORT",
                                               "0") or 0) or None)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--queries", type=int, default=6)
    ap.add_argument("--n-random", type=int, default=48)
    ap.add_argument("--seed", type=int, default=13906)
    ap.add_argument("--ef-diagnostic", type=int, default=200,
                    help="the efRuntime the informational index diagnostic "
                         "re-creates the graph index with (default 200; "
                         "FalkorDB's own default is 10)")
    ap.add_argument("--poison", choices=("id", "order", "recall", "vector"),
                    default=None,
                    help="corrupt the candidate side on purpose; a run that "
                         "still reports parity is a BLIND harness")
    ap.add_argument("--allow-remote-writes", action="store_true",
                    help="permit seeding a NON-loopback store; this harness "
                         "writes (org/graph/point rows) and only its cleanup "
                         "makes that transient")
    ap.add_argument("--keep", action="store_true",
                    help="leave the seeded tenant/graph behind (debugging)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args(argv)

    if not args.graph_port:
        print("cannot run: no --graph-port and no TORTOISE_TEST_DOCKER_PORT — "
              "start one with `uv run python tools/test_lane.py uri`",
              file=sys.stderr)
        return 2
    if not args.record_dsn:
        print("cannot run: no --record-dsn / TEST_RECORD_DB_URI — a one-sided "
              "run is not a parity measurement", file=sys.stderr)
        return 2

    report = run(args)
    text = json.dumps(report, indent=2, default=str)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")

    v = report["verdict"]
    print(f"=== #13906 vector parity — k={args.k} "
          f"queries={len(report['queries'])} poison={args.poison} ===")
    for entry in report["queries"]:
        print(f"  {entry['query']}: "
              f"record recall={entry['record']['recall']:.3f} "
              f"order={entry['record']['order']} | "
              f"graph_index recall={entry['graph_index']['recall']:.3f} "
              f"order={entry['graph_index']['order']} | "
              f"graph_scan(euc) recall="
              f"{entry['graph_scan_vs_euclidean']['recall']:.3f} | "
              f"scan-vs-cosine recall="
              f"{entry['graph_scan_vs_cosine']['recall']:.3f}")
    print(f"informational: {v['informational']}")
    if v.get("poison_status"):
        print(f"poison: {v['poison_status']}")
    if v["ok"]:
        print("PARITY: all required comparisons agree")
        return 0
    for f in v["failures"]:
        print(f"  FAIL {f}", file=sys.stderr)
    print("PARITY: FAILED", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
