#!/usr/bin/env python3
"""cypher-diff-harness — prove a Cypher read and its SQL form are the SAME answer (#13098).

WHAT THIS IS
============
`#13082`'s rule is *"do not flip a query until the diff is empty on a corpus that
exercises its edge cases"*. This is that diff. For one candidate statement it:

1. seeds ONE corpus into **both** stores — a real FalkorDB lane (the product's
   graph) and a real Postgres record (the product's own `supabase/migrations`
   schema) — so the two forms answer over the **same tenant data**;
2. runs the **Cypher** form against the graph and the **SQL** form against the
   record;
3. diffs **rows · order · null handling · row shape**, and prints the graph lane
   and the record DSN it used.

Exit codes are the verdict: **0 = equal**, **1 = different**, **2 = could not run**
(no lane, no DSN, a missing corpus edge case, an unexpected error), **3 = no
verdict was produced** — the invocation did not diff the two forms at all
(`--bench` measures, `--list` lists). `2` is never a pass, and neither is `3`:
a harness that cannot run, or did not compare, must not read as agreement.
⛔ `3` exists because `--bench` used to `return 0` before the corpus-edge check
and without ever calling `diff()`, so a caller keying on `0` read a MEASUREMENT
as an agreement — the same defect class this module's own header argues against
("a harness that cannot run must not read as agreement"), one branch over
(`#13098` review).

WHAT "THE SAME" MEANS — the comparison rule, in one home
========================================================
The repo already has this argument in code for the record↔cache pair
(`record_serve.parity_difference`, whose rule is: equal on every field both sides
can claim, every absence EXPLAINED by a declaration, and the comparison fails in
both directions so neither a dropped nor an invented field hides). This harness
holds the same posture and adds the two things a *query* diff needs beyond a
field diff:

* **ORDER is compared when the statement declares an order.** A statement with
  `ORDER BY` is compared as a SEQUENCE; without one, as a MULTISET (row order is
  not part of the answer, and asserting it would red on a legitimate planner
  difference). The declaration is the candidate's `ordered` flag — never
  inferred from whatever the two engines happened to return.
* **NULL handling is compared at the RESULT level, and it is three-way.** A
  property absent on a node and a column that is SQL NULL are the SAME answer
  (`null`); an empty string, `0`, `false` and `'None'` are each DIFFERENT from
  it. Engines disagree here in practice — Postgres `ORDER BY x DESC` places NULLs
  FIRST by default, which is a real porting defect this harness catches (the
  `null-order` mutation below is exactly it) — so collapsing nullness into
  "falsy" would erase the class the harness exists to see.
* **ROW SHAPE is compared THREE ways: each engine's columns against the
  candidate's DECLARED `columns` tuple, then the two lists' counts, then their
  names case-insensitively**, so an extra or missing column is a difference even
  when every value matches (a projection drift is not a value difference and must
  not be absorbed by a zip) — and a projection that drifts on BOTH sides in the
  same way still reddens against the declaration (`#13098` review P3).

Value normalization, stated so it is not mistaken for tolerance: `Decimal` (the
record's numeric type) and `float` compare by numeric value AND by type family;
JSONB columns and graph property maps compare structurally; a list keeps its
order. A type family change (int vs str, bool vs int) is a DIFFERENCE — the
kinds of silent coercion a migration actually produces.

THE CORPUS IS AN ARGUMENT, NOT A CONVENIENCE
============================================
Every candidate declares the edge cases it cares about, and the harness REFUSES
(exit 2) if the seeded store did not actually contain one of them — the
`#13099`/`#11859` lesson: a pin that cannot fail for the thing it names is not a
pin. The shared corpus is:

| id | what it is | which edge case |
|---|---|---|
| `p-normal` | a plain point | the ordinary case |
| `p-operator` | `is_operator = true` | a FILTER that must exclude it |
| `p-null-content` | no `content` | NULL property |
| `p-null-created` | no `created_at` | NULL in an ORDER BY |
| `p-unicode` | emoji + a `|` + a trailing space | encoding / a digest that must not be mangled |
| `p-retired` | `valid_to` set | a retracted row (`#11827`'s retire columns) |
| (absent id) | `p-absent` in NEITHER store | the EMPTY result — both forms must answer 0 rows *and* name their columns |

USAGE
=====
    # 0 = equal · 1 = different · 2 = could not run · 3 = no verdict (--bench / --list)
    uv run python tools/cypher_diff_harness.py \
        --graph-uri "docker://:pw@127.0.0.1:16404" \
        --record-dsn "postgresql://postgres:s3cret@127.0.0.1:15499/cypher_diff" \
        --candidate point-by-id

    # prove the harness can FAIL: inject a real porting defect
    … --candidate point-listing --mutate null-order     # exit 1, names the rows

    # a real record DSN from the product's own migrations (no external Postgres):
    docker run -d --name cypher-diff-record -p 15499:5432 \
        -e POSTGRES_PASSWORD=s3cret -e POSTGRES_DB=cypher_diff pgvector/pgvector:pg16
    … --record-dsn postgresql://postgres:s3cret@127.0.0.1:15499/cypher_diff \
      --bootstrap-record          # applies supabase/migrations/*.sql in filename order

CI: `.github/workflows/cypher-diff.yml` starts a FalkorDB service and a
`pgvector/pgvector:pg16` service, bootstraps the record from the committed
migrations, and runs the candidates in TWO steps — an agreement step over
every candidate (each must exit 0), then a mismatch step over the one candidate
whose SQL has an `ORDER BY` to mutate (`point-listing --mutate null-order`, must
exit 1). The `null-order` mutation is NOT applicable to every candidate: it is
refused (exit 2, never a diff) wherever the statement has no
`ORDER BY created_at DESC`, because a mutation that changes nothing would report
agreement the harness never tested. Both names are in the job output.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent

#: Exit code for "this invocation produced no verdict" — `--bench` measures and
#: `--list` lists; neither diffs the two forms. Distinct from `2` (could not run)
#: so a caller can tell "it did not compare" from "it tried and failed".
EXIT_NO_VERDICT = 3

#: The tenant the corpus is seeded under. `EMPTY_TENANT` is a SECOND org/graph
#: chain with no points in it: it exists so the record carries more than one
#: org and the tenancy chain this fires on is exercised twice.
#: ⛔ It is NOT what exercises the EMPTY RESULT — no candidate queries it, by
#: design. That edge is `point-by-absent-id`, which asks BOTH stores for a row
#: neither has. An earlier docstring claimed an `org-other` empty-tenant query
#: that no code performed (`#13098` review F2); the claim now names the
#: candidate that makes it true.
TENANT = {"org_id": "org-diff", "graph_id": "graph-diff"}
EMPTY_TENANT = {"org_id": "org-diff-empty", "graph_id": "graph-diff-empty"}

#: An id no corpus row and no padding row carries — the EMPTY-result fixture.
ABSENT_ID = "p-absent"

#: Retired rows: the record's retire columns (#11827) are `valid_to`/`expired_at`;
#: the cache answers a point by id regardless, so a corpus row that carries
#: `valid_to` is the "deleted/retracted" edge case at RESULT level.
CORPUS: tuple[dict[str, Any], ...] = (
    {"id": "p-normal", "content": "a plain claim", "created_at": "2026-01-01T00:00:00Z", "is_operator": False},
    {"id": "p-operator", "content": "an operator point", "created_at": "2026-01-02T00:00:00Z", "is_operator": True},
    {"id": "p-null-content", "content": None, "created_at": "2026-01-03T00:00:00Z", "is_operator": False},
    {"id": "p-null-created", "content": "no created_at", "created_at": None, "is_operator": False},
    {"id": "p-unicode", "content": "emoji 🐢 | pipe, trailing space ", "created_at": "2026-01-05T00:00:00Z", "is_operator": False},
    {"id": "p-retired", "content": "a retracted claim", "created_at": "2026-01-06T00:00:00Z",
     "is_operator": False, "valid_to": "2026-02-01T00:00:00Z"},
)


# --------------------------------------------------------------------------- #
# candidates — a Cypher form, its SQL form, and what it promises
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Candidate:
    """One migration candidate, both forms, and the edges it must be tested on."""

    name: str
    cypher: str
    sql: str
    columns: tuple[str, ...]
    ordered: bool
    #: names of corpus rows this candidate's corpus must contain (refused if absent)
    requires: tuple[str, ...] = ()
    #: rows whose EDGE-CASE PROPERTY must hold in both stores, as
    #: `(row id, column, must_be_null)`. ⛔ Presence alone cannot fail for the thing
    #: the row is named after: `p-null-created` being PRESENT proves nothing once a
    #: seeding change gives it a `created_at`, and the `null-order` mutation then
    #: reorders an `ORDER BY` with no NULLs in it (`#13098` review P3).
    properties: tuple[tuple[str, str, bool], ...] = ()
    notes: str = ""
    #: the statement parameters THIS candidate binds (merged with the tenant).
    #: It used to be a name check in `main` (`if candidate.name == "point-by-id"`),
    #: which silently left `$id` unbound for any other anchored candidate — a
    #: missing-parameter ERROR reported as "could not run" rather than a diff.
    params: dict[str, str] = field(default_factory=dict)
    #: extra SQL predicate the `--mutate` flag can bend, per mutation name
    mutations: dict[str, str] = field(default_factory=dict)


CANDIDATES: dict[str, Candidate] = {
    "point-by-id": Candidate(
        name="point-by-id",
        cypher=(
            "MATCH (n:Point {id:$id}) "
            "RETURN n.id AS id, n.content AS content, n.point_kind AS point_kind"
        ),
        sql=(
            "SELECT id, content, point_kind FROM public.point "
            "WHERE org_id = %(org_id)s AND graph_id = %(graph_id)s AND id = %(id)s"
        ),
        columns=("id", "content", "point_kind"),
        ordered=False,
        requires=("p-null-content",),
        params={"id": "p-null-content"},
        properties=(("p-null-content", "content", True),),
        mutations={
            # a projection that silently drops the null-content row is the classic
            # "no answer reads as an absence" defect (§17.3)
            "drop-null": " AND content IS NOT NULL",
        },
        notes=(
            "The anchored read behind GET /v1/points/{id} — `record_serve`'s "
            "`point-by-id` coordinate. Both forms are anchored on the same id."
        ),
    ),
    # ⛔ THE EMPTY RESULT, compared rather than declared (`#13098` review F2):
    # the id exists in neither store, so the two forms must both answer zero
    # rows AND still report their column list. A port that turned an empty
    # answer into an error, or into `None`, does not come back `equal` here.
    "point-by-absent-id": Candidate(
        name="point-by-absent-id",
        cypher=(
            "MATCH (n:Point {id:$id}) "
            "RETURN n.id AS id, n.content AS content, n.point_kind AS point_kind"
        ),
        sql=(
            "SELECT id, content, point_kind FROM public.point "
            "WHERE org_id = %(org_id)s AND graph_id = %(graph_id)s AND id = %(id)s"
        ),
        columns=("id", "content", "point_kind"),
        ordered=False,
        requires=(),
        params={"id": ABSENT_ID},
        notes=(
            "The EMPTY result: `p-absent` is in NEITHER store, so both forms must "
            "answer zero rows with their columns intact. MEASURED on a real "
            "FalkorDB lane (2026-11-17): an empty match still returns the header "
            "(`cols=['id','content','point_kind']`), so the row-shape comparison "
            "is not vacuous on this path."
        ),
    ),
    "point-listing": Candidate(
        name="point-listing",
        cypher=(
            "MATCH (n:Point) WHERE n.is_operator = false "
            "RETURN n.id AS id, n.content AS content, n.created_at AS created_at "
            "ORDER BY n.created_at DESC LIMIT 10"
        ),
        # ⛔ MEASURED, and it is the reason this candidate exists: the graph
        # places a NULL `created_at` FIRST under DESC, and Postgres' own default
        # for DESC is also NULLS FIRST — so the forms AGREE only while nobody
        # "tidies" the clause. A port that defensively writes `NULLS LAST`
        # changes the row ORDER while every value still matches, which a
        # value-only diff cannot see. That is the `null-order` mutation below.
        sql=(
            "SELECT id, content, created_at FROM public.point "
            "WHERE org_id = %(org_id)s AND graph_id = %(graph_id)s "
            "AND is_operator = false "
            "ORDER BY created_at DESC LIMIT 10"
        ),
        columns=("id", "content", "created_at"),
        ordered=True,
        requires=("p-null-created", "p-retired"),
        # the two properties that make those rows edge cases: the NULL the
        # `null-order` mutation reorders, and the retraction `drop-row` removes
        properties=(("p-null-created", "created_at", True),
                    ("p-retired", "valid_to", False)),
        mutations={
            "null-order": "",  # mutated in `_mutated`: NULLS LAST is appended
            "drop-row": " AND id <> 'p-retired'",
        },
        notes=(
            "An unfiltered-edge listing (`plain` shape in the inventory): filter, "
            "order, limit. Exercises NULL placement in ORDER BY, which is where "
            "the two engines disagree by DEFAULT."
        ),
    ),
}

#: The mutations that rewrite the SQL rather than append a predicate.
# (The `null-order` rewrite itself lives in `_mutated`; there is no module-level
# table of rewritten statements — a constant here was DEAD, and a dead constant
# next to a live mutation reads as if the mutation consulted it.)


def _mutated(candidate: Candidate, mutation: str | None) -> str:
    """The SQL form with a deliberate porting defect injected."""
    if mutation is None:
        return candidate.sql
    if mutation == "null-order":
        # The well-intentioned "defensive" clause a hand-port adds — and it
        # reverses the null placement the graph answers with.
        if "ORDER BY created_at DESC" not in candidate.sql:
            raise KeyError(f"{candidate.name} has no `ORDER BY created_at DESC` — the "
                           "null-order mutation would change nothing, so it is refused "
                           "rather than reported as a mutation that found no difference")
        return candidate.sql.replace("ORDER BY created_at DESC",
                                     "ORDER BY created_at DESC NULLS LAST")
    if mutation in candidate.mutations:
        extra = candidate.mutations[mutation]
        # append before ORDER BY when the statement has one
        if " ORDER BY " in candidate.sql:
            head, tail = candidate.sql.split(" ORDER BY ", 1)
            return f"{head}{extra} ORDER BY {tail}"
        return candidate.sql + extra
    raise KeyError(f"{candidate.name} has no mutation {mutation!r}; "
                   f"declared: {[*sorted(candidate.mutations), 'null-order']}")


# --------------------------------------------------------------------------- #
# seeding — ONE corpus, both stores
# --------------------------------------------------------------------------- #
def _redis_url(graph_uri: str) -> str:
    """The repo spells a lane `docker://:pw@host:port` (its own seam); the driver
    speaks `redis://`. Translated here, once — a caller passing either form works."""
    if graph_uri.startswith("docker://"):
        return "redis://" + graph_uri[len("docker://"):]
    return graph_uri


def _refuse_a_foreign_graph(graph, graph_name: str) -> None:
    """Refuse to seed into a graph that is not the harness's own corpus graph.

    ⛔ THE SEED DELETES EVERY `:Point` IN THE TARGET (it compares one corpus across
    both stores, so it has to own the graph). That is only safe on a graph the
    harness built — and the target is whatever the operator names, or (until this
    change) whatever `TORTOISE_DB_URI` happened to point at — so a mistyped graph
    name or a bare invocation in a developer shell would wipe a real graph's
    points (`#13098` review P2-3). Two checks, both fail-closed (`main` turns the
    raise into exit 2): a label this harness never writes, or a Point id it never
    wrote.

    ⚠️ This is a TARGET check, not a proof: a graph whose only label is `Point`
    and whose ids all happen to be corpus ids is indistinguishable from a corpus.
    It is here so the destructive path cannot be taken by ACCIDENT.
    """
    labels = {str(r[0]) for r in graph.query(
        "MATCH (n) UNWIND labels(n) AS l RETURN DISTINCT l").result_set}
    foreign_labels = sorted(labels - {"Point"})
    if foreign_labels:
        raise RuntimeError(
            f"refusing to seed graph {graph_name!r}: it holds node label(s) {foreign_labels} "
            "this harness never writes, so it is not a corpus graph — the seed would "
            "DETACH DELETE every :Point in it. Point --graph-name at a fresh graph."
        )
    corpus_ids = {row["id"] for row in CORPUS}
    written = {str(r[0]) for r in graph.query("MATCH (n:Point) RETURN n.id").result_set}
    foreign_ids = sorted(i for i in written
                         if i not in corpus_ids and not i.startswith("p-pad-"))
    if foreign_ids:
        raise RuntimeError(
            f"refusing to seed graph {graph_name!r}: it already holds {len(foreign_ids)} :Point "
            f"id(s) this harness never wrote (e.g. {foreign_ids[:4]}), so it is not a corpus "
            "graph — the seed would DETACH DELETE them. Point --graph-name at a fresh graph."
        )


def seed_graph(graph_uri: str, graph_name: str) -> dict[str, Any]:
    """Seed the corpus into a real FalkorDB graph. Returns the lane it used."""
    from falkordb import FalkorDB  # local import: a missing driver must read as 2

    client = FalkorDB.from_url(_redis_url(graph_uri))
    graph = client.select_graph(graph_name)
    _refuse_a_foreign_graph(graph, graph_name)
    graph.query("MATCH (n:Point) DETACH DELETE n")
    for row in CORPUS:
        props = {k: v for k, v in row.items() if v is not None}
        assigns = ", ".join(f"n.{k} = ${k}" for k in props)
        graph.query(f"MERGE (n:Point {{id:$id}}) SET {assigns}", props)
    # the EMPTY tenant seeds nothing, on purpose: the empty result must come from
    # a query, not from a missing fixture
    total = graph.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
    return {"graph": graph_name, "points": int(total)}


def seed_record(dsn: str) -> dict[str, Any]:
    """Seed the same corpus into the record's own schema."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = 'point'")
            if cur.fetchone()[0] == 0:
                raise RuntimeError(
                    "the record DSN has no public.point — bootstrap it with the "
                    "committed migrations (`--bootstrap-record`, which applies "
                    "supabase/migrations/*.sql in filename order)"
                )
            # The tenant chain is REAL, not stubbed: `organizations` → `graphs` →
            # `node_identity` → `point`, exactly as the product's write path
            # settles it. A harness that bypassed the invariants would compare
            # forms over data the product could never store.
            for tenant in (TENANT, EMPTY_TENANT):
                cur.execute(
                    "INSERT INTO public.organizations (id, name, graph_name) "
                    "VALUES (%(org_id)s, %(org_id)s, %(graph_id)s) ON CONFLICT (id) DO NOTHING",
                    tenant,
                )
                cur.execute(
                    "INSERT INTO public.graphs "
                    "(id, org_id, name, kind, namespace, status, created_at, purged_residual) "
                    "VALUES (%(graph_id)s, %(org_id)s, %(graph_id)s, 'tenant', %(graph_id)s, "
                    "'active', now(), false) ON CONFLICT (id) DO NOTHING",
                    tenant,
                )
            cur.execute("DELETE FROM public.point WHERE org_id IN (%s, %s)",
                        (TENANT["org_id"], EMPTY_TENANT["org_id"]))
            # `public.point` carries a FOREIGN KEY onto `public.node_identity` — the
            # record's real identity contract (the coordinate a `fetch-record`
            # repair is handed), so the corpus mints it the way the product does.
            cur.execute("DELETE FROM public.node_identity WHERE org_id IN (%s, %s)",
                        (TENANT["org_id"], EMPTY_TENANT["org_id"]))
            for row in CORPUS:
                cur.execute(
                    "INSERT INTO public.node_identity "
                    "(org_id, graph_id, node_label, node_key) "
                    "VALUES (%(org_id)s, %(graph_id)s, 'Point', %(node_key)s)",
                    {**TENANT, "node_key": row["id"]},
                )
            for row in CORPUS:
                keys = ["org_id", "graph_id", "node_key", *row.keys()]
                values = [TENANT["org_id"], TENANT["graph_id"], row["id"], *row.values()]
                cols = ", ".join(keys)
                marks = ", ".join(f"%({k})s" for k in keys)
                cur.execute(
                    f"INSERT INTO public.point ({cols}) VALUES ({marks})",
                    dict(zip(keys, values, strict=True)),
                )
            cur.execute("SELECT count(*) FROM public.point WHERE org_id = %s",
                        (TENANT["org_id"],))
            total = cur.fetchone()[0]
    finally:
        conn.close()
    return {"points": int(total)}


#: What the PRODUCT assumes and a bare Postgres does not have: Supabase owns the
#: `auth` schema and the three request roles, and the migrations reference both.
#: ⛔ MEASURED (2026-11-17, `pgvector/pgvector:pg16`, 85 committed migrations):
#: WITHOUT this block 69 of 85 files fail — including the two that create
#: `public.node_identity` — so `public.point` never materialises and every seed
#: refuses with "the record DSN has no public.point". WITH it, 84 of 85 apply
#: and the record schema is complete; the one remaining failure is
#: `blog_cms.sql`, which needs Supabase Storage's `storage.buckets` and is not
#: part of the record. Applying it is idempotent, so a re-run is safe.
SUPABASE_PREREQS = """
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS auth;
CREATE TABLE IF NOT EXISTS auth.users (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text,
  raw_user_meta_data jsonb DEFAULT '{}'::jsonb
);
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
$$;
CREATE OR REPLACE FUNCTION auth.role() RETURNS text LANGUAGE sql STABLE AS $$
  SELECT nullif(current_setting('request.jwt.claim.role', true), '')
$$;
CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS $$
  SELECT coalesce(nullif(current_setting('request.jwt.claims', true), '')::jsonb, '{}'::jsonb)
$$;
DO $$ BEGIN CREATE ROLE anon NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE authenticated NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE ROLE service_role NOLOGIN BYPASSRLS; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
"""


def bootstrap_record(dsn: str) -> int:
    """Apply the product's own migrations to a fresh Postgres — a REAL record DSN.

    The Supabase-managed prerequisites are created first (`SUPABASE_PREREQS`),
    then the full migration set is applied in filename order with
    `ON_ERROR_STOP=1` per file — so a file that fails STOPS at its first error
    rather than half-applying the rest of that file — and every failure is
    REPORTED. A partial apply is tolerated (the record schema builds on the
    control-plane schema and on Supabase Storage), but it is tolerated only
    while the thing the corpus needs exists: `public.point` is checked at the
    end and its absence is **2 = could not run**, never 0.

    Returns 0 only when the record schema materialised; 2 when `psql` is absent
    or the schema did not build.
    """
    migrations = sorted((ROOT / "supabase" / "migrations").glob("*.sql"))
    failures: list[str] = []
    try:
        prereq = subprocess.run(
            ["psql", dsn, "-q", "-v", "ON_ERROR_STOP=1", "-c", SUPABASE_PREREQS],
            capture_output=True, text=True,
        )
        if prereq.returncode != 0:
            print(f"cypher-diff: could not run — the Supabase prerequisites did not apply: "
                  f"{(prereq.stderr or '').strip()[:400]}", file=sys.stderr)
            return 2
        for path in migrations:
            proc = subprocess.run(
                ["psql", dsn, "-q", "-v", "ON_ERROR_STOP=1", "-f", str(path)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                first = (proc.stderr or "").strip().splitlines()
                failures.append(f"{path.name}: {first[0] if first else 'failed'}")
        # The schema the corpus actually needs, read back from the server — not
        # inferred from "the loop finished".
        present = subprocess.run(
            ["psql", dsn, "-tAc", "SELECT to_regclass('public.point')"],
            capture_output=True, text=True,
        )
    except OSError as exc:
        # ⛔ `psql` not on PATH used to escape as FileNotFoundError, which the
        # interpreter reports as exit **1** — this tool's own code for "the two
        # forms DIFFER". A missing client is "could not run" (2), never a
        # finding.
        print(f"cypher-diff: could not run — `psql` is not usable here: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(f"bootstrapped {len(migrations) - len(failures)}/{len(migrations)} migrations")
    for failure in failures[:20]:
        print(f"  FAILED {failure}")
    if present.returncode != 0 or not (present.stdout or "").strip():
        print("cypher-diff: could not run — the record has no public.point after "
              "bootstrapping, so every corpus seed would refuse; the migrations "
              "above are the reason", file=sys.stderr)
        return 2
    return 0


# --------------------------------------------------------------------------- #
# execution
# --------------------------------------------------------------------------- #
def run_cypher(graph_uri: str, graph_name: str, cypher: str, params: dict) -> tuple[list[tuple], list[str]]:
    from falkordb import FalkorDB

    graph = FalkorDB.from_url(_redis_url(graph_uri)).select_graph(graph_name)
    res = graph.query(cypher, params)
    return [tuple(r) for r in res.result_set], [c[1] if len(c) > 1 else str(c[0]) for c in (res.header or [])]


def run_sql(dsn: str, sql: str, params: dict) -> tuple[list[tuple], list[str]]:
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [tuple(r) for r in cur.fetchall()]
            cols = [d[0] for d in (cur.description or [])]
    finally:
        conn.close()
    return rows, cols


# --------------------------------------------------------------------------- #
# the diff
# --------------------------------------------------------------------------- #
def _family(value: Any) -> str:
    """The value's scalar FAMILY — the unit row-shape comparison uses."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (list, tuple)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _family_signature(value: Any) -> Any:
    """`_family` applied RECURSIVELY, so a bool inside a list is still a bool.

    ⛔ `_family` alone answers only for the top-level value, so a collection was
    compared as `list` vs `list` and its ELEMENTS' families were never checked:
    `[True]` and `[1]` reached the value pass as two `list`s, and Python's own
    `[True] == [1]` then called them equal — the module header's rule ("a type
    family change — int vs str, bool vs int — is a DIFFERENCE") was silently not
    applied inside a collection (`#13098` review P3). A false PASS is the worst
    class this file can produce, so the family check is recursive now rather than
    left to a sort-order accident.
    """
    family = _family(value)
    if family == "list":
        return (family, tuple(_family_signature(v) for v in value))
    if family == "dict":
        return (family, tuple(sorted((str(k), _family_signature(v)) for k, v in value.items())))
    return family


def _norm(value: Any) -> Any:
    """Normalize for VALUE comparison only — never for the family check."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in sorted(value.items())}
    return value


def _canon(row: Sequence[Any]) -> str:
    """A canonical TEXT form — for the fingerprint and for display, never equality.

    ⛔ Goes through `_render_canonical` (not a bare `_norm` dump) so a `Decimal`
    dict KEY — which `json.dumps` refuses — cannot take `diff()` down with a
    `TypeError` after every comparison has already succeeded (`#13098` review P3).
    """
    return json.dumps([_render_canonical(v) for v in row], sort_keys=True, default=str)


def _norm_row(row: Sequence[Any]) -> tuple:
    """A row normalised for ORDER comparison — the SAME equality as the value pass.

    ⛔ NOT `_canon`. `_canon` serialises, so `1` and `1.0` become different json
    strings while `_norm` already treats them as one number — which made an
    ordered numeric candidate report `order` on two forms the value pass had just
    certified identical (`#13098` review P2). The two stores really do disagree
    on Python type here: `public.point.confidence` is `double precision`, so the
    record answers `float`/`Decimal` while FalkorDB answers the `int` the property
    was written with. A tuple compares element-wise with `==`, so numerics compare
    numerically and nested lists/dicts compare by value.
    """
    return tuple(_norm(v) for v in row)


def _render_canonical(value: Any) -> Any:
    """`_norm`'s value, re-rendered so two `_norm`-equal values render the SAME.

    ⛔ `_norm` recurses into lists/dicts, but a json rendering of its result does
    not collapse `[1]` and `[1.0]` — so `_sort_key` was NOT the total order it
    claims to be for a list/dict column, and the false-`value`-difference this
    file's `_sort_key` closes for scalars stayed reproducible for a collection
    (`[("a", [1]), ("a", [10])]` vs the `[1.0]`/`[10.0]` side, `#13098` review P3).
    Recursive, because one level was the whole defect.
    """
    value = _norm(value)
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        # ⛔ INSIDE a collection a bool must render as its number, because `_norm`
        # says `[True] == [1]` — the top-level `part()` gives bools their own rank
        # on purpose (a bool and a number are different FAMILIES, reported first),
        # and nothing does that for a bool nested in a list or map, so rendering it
        # verbatim left `[True]` and `[1]` with different keys (`#13098` review P3).
        return int(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, (list, tuple)):
        return [_render_canonical(v) for v in value]
    if isinstance(value, dict):
        # `str(k)`: `json.dumps` accepts only str/int/float/bool/None keys, and a
        # `Decimal` key raised a TypeError out of `diff()` — which the outer
        # exit-code boundary turns into `2` ("could not run") rather than the
        # traceback it deserves to be (`#13098` review P3).
        return {str(k): _render_canonical(v) for k, v in value.items()}
    return value if isinstance(value, int) else str(value)


def _sort_key(row: Sequence[Any]) -> tuple:
    """A total order over rows that AGREES with `_norm_row`'s equality.

    ⛔ The multiset pass sorted by `_canon` and then compared by `_norm`, so the two
    sides could sort into OPPOSITE orders and the positional pairing compared
    different rows: `[("a", 1), ("a", 10)]` sorts `["\"a\", 10", "\"a\", 1"]`
    because `]` > `,` > `.`, while the `1.0`/`10.0` side sorts the other way — a
    false `value` difference on multisets that are equal (`#13098` review P3). Each
    element renders through `_render`, which collapses `1`/`1.0`/`Decimal("1")`
    INSIDE a list or dict as well as at the top level, so two `_norm`-equal rows
    always produce the same key.
    """
    def part(value: Any) -> tuple[int, str]:
        if isinstance(value, bool):
            # its own rank: a bool and a number are different FAMILIES (`_family`
            # reports that difference first), so they must not share a sort order.
            return (2, "true" if value else "false")
        number = _render_canonical(value)
        if isinstance(number, (int, float)):
            return (0, repr(number))
        return (1, json.dumps(number, sort_keys=True, default=str))

    return tuple(part(v) for v in row)


def diff(candidate: Candidate, cypher_rows: list[tuple], cypher_cols: list[str],
         sql_rows: list[tuple], sql_cols: list[str], seeded_ids: list[str]) -> dict:
    """Rows · order · null handling · row shape. Empty `differences` == equal."""
    differences: list[dict] = []

    # 1. ROW SHAPE — the candidate's DECLARED column list against each engine, then
    # the two engines against each other. The declaration arm is what makes the
    # `columns` field a contract rather than a comment: a projection that drifts on
    # BOTH sides in the same way passes every cross-engine check (`#13098` review P3).
    declared = list(candidate.columns)
    if list(cypher_cols) != declared or list(sql_cols) != declared:
        differences.append({"kind": "shape", "detail": "declared column list",
                            "declared": declared,
                            "cypher": list(cypher_cols), "sql": list(sql_cols)})
    if len(cypher_cols) != len(sql_cols):
        differences.append({"kind": "shape", "detail": "column count",
                            "cypher": cypher_cols, "sql": sql_cols})
    # `strict=False` is DELIBERATE: a column-count mismatch is already recorded as a
    # `shape` difference above, and zipping the common prefix is what lets the
    # per-column NAME check still run and report which names disagree.
    for index, (c_col, s_col) in enumerate(zip(cypher_cols, sql_cols, strict=False)):
        # the Cypher alias and the SQL output name must agree case-insensitively;
        # a different name is a shape difference a caller reads.
        if c_col.lower() != s_col.lower():
            differences.append({"kind": "shape", "detail": f"column {index} name",
                                "cypher": c_col, "sql": s_col})

    # 2. ROW COUNT
    if len(cypher_rows) != len(sql_rows):
        differences.append({"kind": "row-count",
                            "cypher": len(cypher_rows), "sql": len(sql_rows)})

    # 3. ROW SET (as a MULTISET) + NULL HANDLING
    # ⛔ The comparison is multiset-first even for an ORDERED statement: a pure
    # ⛔ SORTED BY `_sort_key`, so the pairing compares like with like — and the key
    # is built from `_norm`, so the sort agrees with the equality one line below.
    # Sorting by `_canon` here paired rows from OPPOSITE orders whenever a numeric
    # column was decimal-prefix-related (`1` vs `10`), producing two `value`
    # differences on multisets that are equal (`#13098` review P3).
    width = min(len(cypher_cols), len(sql_cols))
    c_sorted = sorted(cypher_rows, key=_sort_key)
    s_sorted = sorted(sql_rows, key=_sort_key)
    value_diffs: list[dict] = []
    # `strict=False` for the same reason as the column pass: a row-count mismatch is
    # its own finding (step 2), and the per-value pass must still compare what both
    # sides actually returned.
    for row_index, (c_row, s_row) in enumerate(zip(c_sorted, s_sorted, strict=False)):
        for col_index in range(width):
            c_val = c_row[col_index] if col_index < len(c_row) else "<absent>"
            s_val = s_row[col_index] if col_index < len(s_row) else "<absent>"
            c_fam, s_fam = _family_signature(c_val), _family_signature(s_val)
            # null handling is THREE-WAY: null != '' != 0 != False != 'None'
            if c_fam != s_fam:
                value_diffs.append({"kind": "family", "row": row_index,
                                    "column": cypher_cols[col_index],
                                    "cypher": [c_fam, c_val], "sql": [s_fam, s_val]})
                continue
            if _norm(c_val) != _norm(s_val):
                value_diffs.append({"kind": "value", "row": row_index,
                                    "column": cypher_cols[col_index],
                                    "cypher": c_val, "sql": s_val})
    differences.extend(value_diffs)

    # 4. ORDER — only when the statement declares it, and only on a row set that
    # already matches (an order claim over different rows is a value finding).
    if (candidate.ordered and not value_diffs and len(cypher_rows) == len(sql_rows)
            and [_norm_row(r) for r in cypher_rows] != [_norm_row(r) for r in sql_rows]):
        differences.append({"kind": "order", "detail": "row sequence differs "
                            "although the row set matches — the NULL-placement "
                            "class (a graph DESC puts NULLs FIRST; a hand-port "
                            "that adds `NULLS LAST` reverses it)",
                            "cypher": [_canon(r) for r in cypher_rows][:10],
                            "sql": [_canon(r) for r in sql_rows][:10]})

    # The fingerprint covers the CYPHER answer's row set (sorted), so two runs over
    # the same data are recognisably the same measurement even when the row order
    # a planner chose differs.
    import hashlib

    fingerprint = json.dumps([_canon(r) for r in c_sorted], sort_keys=True, default=str)
    return {
        "candidate": candidate.name,
        "equal": not differences,
        "differences": differences,
        "row_count": {"cypher": len(cypher_rows), "sql": len(sql_rows)},
        "columns": {"cypher": cypher_cols, "sql": sql_cols},
        "edges_seeded": seeded_ids,
        "fingerprint": hashlib.sha256(fingerprint.encode()).hexdigest()[:16],
    }


# --------------------------------------------------------------------------- #
# the measurement a flip depends on
# --------------------------------------------------------------------------- #
#: The cold-restore penalty (`#13082`), the number a SQL form must beat to be
#: worth a flip: re-materialising a graph from the record costs 0.03 s
#: index-dropped and 1.585 s index-kept, and the index-kept arm BLOCKS the
#: instance. A read that is slower than the penalty is not a saving — you would
#: pay it back on every restore.
COLD_RESTORE_S = 0.03
COLD_RESTORE_INDEX_KEPT_S = 1.585


def bench(graph_uri: str, graph_name: str, dsn: str, candidate: Candidate,
          rows: int, iterations: int) -> dict:
    """Time both forms over the SAME corpus, and state the flip verdict.

    Seeds `rows` synthetic points into both stores (the shared corpus plus
    padding), then times `iterations` executions of each form, reporting p50 and
    p95. ⚠️ What this measures is the FORM's cost on one box — the graph lane and
    the record were seeded by the same harness and are reached over the same
    client — so it answers "is SQL slower than Cypher" and the cold-restore
    comparison, not "what will production latency be".
    """
    import statistics
    import time

    from falkordb import FalkorDB

    graph = FalkorDB.from_url(_redis_url(graph_uri)).select_graph(graph_name)
    padding = {f"p-pad-{i:05d}" for i in range(max(0, rows - len(CORPUS)))}
    for pid in padding:
        graph.query(
            "MERGE (n:Point {id:$id}) SET n.content = $c, n.created_at = $t, "
            "n.is_operator = false",
            {"id": pid, "c": f"padding claim {pid}", "t": f"2026-03-01T00:00:{int(pid[-2:]) % 60:02d}Z"},
        )
    import psycopg2

    conn = psycopg2.connect(dsn, connect_timeout=10)
    try:
        with conn, conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key) "
                "VALUES (%(org_id)s, %(graph_id)s, 'Point', %(node_key)s) "
                "ON CONFLICT DO NOTHING",
                [{**TENANT, "node_key": pid} for pid in padding],
            )
            cur.executemany(
                "INSERT INTO public.point "
                "(org_id, graph_id, node_key, id, content, created_at, is_operator) "
                "VALUES (%(org_id)s, %(graph_id)s, %(id)s, %(id)s, %(content)s, %(created_at)s, false) "
                "ON CONFLICT DO NOTHING",
                [{**TENANT, "id": pid, "content": f"padding claim {pid}",
                  "created_at": f"2026-03-01T00:00:{int(pid[-2:]) % 60:02d}Z"} for pid in padding],
            )
    finally:
        conn.close()
    params = _scoped_params(candidate.params)

    def timeit(fn) -> list[float]:
        samples = []
        for _ in range(iterations):
            start = time.perf_counter()
            fn()
            samples.append(time.perf_counter() - start)
        return samples

    cypher_times = timeit(lambda: run_cypher(graph_uri, graph_name, candidate.cypher, params))
    # TWO SQL arms, because they answer different questions: the COLD arm opens a
    # connection per call (what the shipped `record_serve` read does), the POOLED
    # arm reuses one (#9558's pooled `_default_connect`, the state production runs
    # in). Reporting only the cold arm would make SQL look ~2× worse than the path
    # a deployment actually takes — and a flip decided on that number would be
    # decided on the harness's own overhead.
    sql_times = timeit(lambda: run_sql(dsn, candidate.sql, params))

    import psycopg2 as _pg

    pooled = _pg.connect(dsn, connect_timeout=10)

    def _pooled_run() -> None:
        with pooled, pooled.cursor() as cur:
            cur.execute(candidate.sql, params)
            cur.fetchall()

    try:
        sql_pooled_times = timeit(_pooled_run)
    finally:
        pooled.close()

    def stats(samples: list[float]) -> dict:
        ordered = sorted(samples)
        return {
            "p50_ms": round(statistics.median(ordered) * 1000, 3),
            "p95_ms": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] * 1000, 3),
            "n": len(ordered),
        }

    cypher_stats, sql_stats = stats(cypher_times), stats(sql_times)
    sql_pooled_stats = stats(sql_pooled_times)
    # the verdict uses the POOLED arm: it is the state a flipped read would run in
    ratio = sql_pooled_stats["p50_ms"] / cypher_stats["p50_ms"] if cypher_stats["p50_ms"] else None
    slower_than_penalty = sql_pooled_stats["p50_ms"] / 1000.0 > COLD_RESTORE_S
    return {
        "candidate": candidate.name,
        "rows_seeded": rows,
        "iterations": iterations,
        "graph_lane": {"uri": _redact(graph_uri), "graph": graph_name},
        "record": {"dsn": _redact(dsn)},
        "cypher": cypher_stats,
        "sql_cold_connect": sql_stats,
        "sql_pooled": sql_pooled_stats,
        "sql_pooled_over_cypher_p50": round(ratio, 3) if ratio else None,
        "cold_restore_s": {"index_dropped": COLD_RESTORE_S,
                           "index_kept": COLD_RESTORE_INDEX_KEPT_S},
        "sql_slower_than_cold_restore": slower_than_penalty,
    }


# --------------------------------------------------------------------------- #
# the edge-case PROPERTY pin
# --------------------------------------------------------------------------- #
def _disarmed_properties(graph_uri: str, graph_name: str, gdsn: str,
                        candidate: Candidate) -> list[str]:
    """Edge-case rows whose PROPERTY no longer holds, read back from BOTH stores.

    The presence pin above (`id in seeded_graph & seeded_record`) cannot fail when
    a seeding change leaves the row in place but strips the property that made it
    an edge case: give `p-null-created` a `created_at` and the `null-order`
    mutation has no NULL placement left to reverse, so the mismatch run still
    exits 1 — over a corpus that no longer exercises the class the doc claims
    (`#13098` review P3).

    ⚠️ The column name and the predicate are interpolated from this module's own
    declared `Candidate.properties` constants, never from argv, so a caller cannot
    reach either: this is not an injection seam.
    """
    disarmed: list[str] = []
    for row_id, column, must_be_null in candidate.properties:
        predicate = "IS NULL" if must_be_null else "IS NOT NULL"
        graph_rows = run_cypher(
            graph_uri, graph_name,
            f"MATCH (n:Point {{id:$id}}) WHERE n.{column} {predicate} RETURN n.id",
            {"id": row_id},
        )[0]
        record_rows = run_sql(
            gdsn,
            "SELECT id FROM public.point "
            "WHERE org_id = %(org_id)s AND graph_id = %(graph_id)s "
            f"AND id = %(id)s AND {column} {predicate}",
            _scoped_params({"id": row_id}),
        )[0]
        if not graph_rows or not record_rows:
            disarmed.append(f"{row_id}.{column} {predicate} "
                            f"(graph={bool(graph_rows)}, record={bool(record_rows)})")
    return disarmed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--graph-uri", default="",
                        help="a real FalkorDB lane (docker://:pw@host:port) — REQUIRED and "
                             "never taken from the environment: the seed DELETES the "
                             "target graph's :Point rows")
    parser.add_argument("--graph-name", default=os.environ.get("CYPHER_DIFF_GRAPH", "cypher-diff"))
    parser.add_argument("--record-dsn", default="",
                        help="a real record Postgres DSN — REQUIRED and never taken from the "
                             "environment (the seed deletes and rewrites its tenant's "
                             "public.point rows)")
    parser.add_argument("--candidate", default="point-by-id", choices=[*sorted(CANDIDATES), "all"])
    parser.add_argument("--mutate", default=None,
                        help="inject a deliberate porting defect (null-order / drop-row / drop-null)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list", action="store_true", help="list candidates and exit")
    parser.add_argument("--bench", type=int, default=0, metavar="ROWS",
                        help="seed ROWS points into both stores and time both forms")
    parser.add_argument("--bench-iters", type=int, default=30)
    parser.add_argument("--bootstrap-record", action="store_true",
                        help="apply supabase/migrations/*.sql to the record DSN first")
    args = parser.parse_args(argv)

    # ⛔ THE DECLARED EXIT-CODE CONTRACT NEEDS ONE OUTER BOUNDARY. `2` means
    # "could not run"; `1` means "the diff is NON-EMPTY". Without this guard an
    # unexpected exception escaped as the interpreter's own exit 1 — this tool's
    # word for a FINDING — and the CI mismatch step accepts exactly 1 as proof that
    # the harness can fail, so a crash satisfied the assertion the job exists for.
    # Measured instance: `--bench` calls `bench()` outside every inner `try`, and
    # `--bench-iters 0` makes `statistics.median([])` raise `StatisticsError`
    # (`#13098` review P2). The inner guards stay: they name the STORE that could
    # not answer, which this boundary cannot.
    try:
        return _run(args)
    except Exception as exc:
        print(f"cypher-diff: could not run — {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


def _run(args: argparse.Namespace) -> int:
    """Everything after argv parsing — guarded by `main()`'s exit-code boundary."""

    if args.list:
        for name, candidate in CANDIDATES.items():
            print(f"{name}: {candidate.notes}")
        # a listing is not a diff — see EXIT_NO_VERDICT
        return EXIT_NO_VERDICT

    if not args.graph_uri or not args.record_dsn:
        # ⛔ NO AMBIENT DEFAULT for either target. They used to fall back to
        # `TORTOISE_DB_URI` / `TORTOISE_RECORD_DSN` — the PRODUCT's own stores —
        # while this harness DELETEs the target graph's :Point rows and rewrites
        # its tenant's `public.point` rows, so a bare invocation in a developer
        # shell could wipe the graph that shell pointed at (`#13098` review P2-3).
        print("cypher-diff: could not run — both --graph-uri and --record-dsn are required "
              "(a diff without both stores would be comparing nothing to nothing; both are "
              "named explicitly because the seed DELETES the target graph's :Point rows)",
              file=sys.stderr)
        return 2

    if args.bench and args.candidate == "all":
        # refused BEFORE anything is seeded: a misuse must not touch a store
        print("cypher-diff: --bench takes one --candidate", file=sys.stderr)
        return 2

    if args.bootstrap_record:
        rc = bootstrap_record(args.record_dsn)
        if rc != 0:
            return rc

    try:
        graph_seed = seed_graph(args.graph_uri, args.graph_name)
        record_seed = seed_record(args.record_dsn)
    except Exception as exc:  # a store that cannot answer is "could not run" (2)
        print(f"cypher-diff: could not seed — {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    # ⛔ THE EDGE-CASE PIN IS CHECKED AGAINST THE STORES, not against the constant:
    # a pin that reads its own declaration cannot fail for the thing it names
    # (#11859). A missing edge is "could not run" (2) — the diff would be vacuous.
    try:
        seeded_graph = {str(r[0]) for r in run_cypher(
            args.graph_uri, args.graph_name,
            "MATCH (n:Point) RETURN n.id", {})[0]}
        seeded_record = {str(r[0]) for r in run_sql(
            args.record_dsn,
            "SELECT id FROM public.point WHERE org_id = %(org_id)s AND graph_id = %(graph_id)s",
            _scoped_params({}))[0]}
    except Exception as exc:  # a read-back that cannot run is 2, never a verdict
        print(f"cypher-diff: could not read back the seeded corpus — "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    names = list(CANDIDATES) if args.candidate == "all" else [args.candidate]
    # ⛔ THE CORPUS-EDGE CHECK RUNS ON EVERY PATH, INCLUDING `--bench`. It used to
    # sit BELOW the bench branch, so a corpus that lacked a declared edge was
    # measured instead of refused — the "a pin that cannot fail for the thing it
    # names" defect, one branch over (`#13098` review).
    for name in names:
        absent = [edge for edge in CANDIDATES[name].requires
                  if edge not in seeded_graph or edge not in seeded_record]
        if absent:
            print(f"cypher-diff: could not run {name} — the corpus edge case(s) {absent} "
                  f"are not in both stores (graph={sorted(seeded_graph)}, "
                  f"record={sorted(seeded_record)}); the diff would be vacuous for them",
                  file=sys.stderr)
            return 2
        # ⛔ PRESENCE IS NOT THE PIN. A row can be in both stores and no longer
        # BE the edge case it is named after (see `_disarmed_properties`).
        try:
            disarmed = _disarmed_properties(args.graph_uri, args.graph_name,
                                            args.record_dsn, CANDIDATES[name])
        except Exception as exc:  # a store that cannot answer is "could not run" (2)
            print(f"cypher-diff: could not verify the corpus edge properties — "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        if disarmed:
            print(f"cypher-diff: could not run {name} — the corpus row(s) are present but the "
                  f"PROPERTY that makes them edge cases does not hold: {disarmed}",
                  file=sys.stderr)
            return 2

    if args.bench:
        # the measurement a flip depends on — refused for "all" above, before the seed
        measurement = bench(args.graph_uri, args.graph_name, args.record_dsn,
                            CANDIDATES[args.candidate], args.bench, args.bench_iters)
        print(json.dumps(measurement, indent=2))
        # ⛔ NOT 0. `0` is this tool's word for "the two forms are the same
        # answer", and a benchmark is not that measurement: it times one form, it
        # diffs nothing, and it never compared the two. A caller (or a future CI
        # step) keying on 0 must not read a timing run as agreement.
        return EXIT_NO_VERDICT
    results = []
    for name in names:
        candidate = CANDIDATES[name]
        try:
            sql = _mutated(candidate, args.mutate)
        except KeyError as exc:
            print(f"cypher-diff: {exc}", file=sys.stderr)
            return 2
        try:
            cypher_rows, cypher_cols = run_cypher(
                args.graph_uri, args.graph_name,
                _scoped_cypher(candidate.cypher), _scoped_params(candidate.params),
            )
            sql_rows, sql_cols = run_sql(args.record_dsn, sql,
                                         _scoped_params(candidate.params))
        except Exception as exc:  # a store that cannot answer is "could not run" (2)
            print(f"cypher-diff: could not run {name} — {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 2
        result = diff(candidate, cypher_rows, cypher_cols, sql_rows, sql_cols,
                      sorted(seeded_graph & seeded_record))
        result["graph_lane"] = {"uri": _redact(args.graph_uri), "graph": args.graph_name,
                               "seeded_points": graph_seed["points"]}
        result["record"] = {"dsn": _redact(args.record_dsn), "seeded_points": record_seed["points"]}
        result["mutation"] = args.mutate
        results.append(result)

    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, indent=2, default=str))
    else:
        for result in results:
            _render(result)
    return 0 if all(r["equal"] for r in results) else 1


def _scoped_cypher(cypher: str) -> str:
    """Bind the tenant into the graph statement — the corpus lives in one graph
    (`--graph-name`), so the graph's tenancy IS the graph name; the record's is
    the (org_id, graph_id) pair. Named here so the asymmetry is not silent."""
    return cypher


def _scoped_params(params: dict) -> dict:
    return {**params, **TENANT}


def _redact(dsn: str) -> str:
    """Never print a credential — the secret-hygiene rule the record modules apply."""
    if "://" not in dsn:
        return dsn
    scheme, rest = dsn.split("://", 1)
    if "@" in rest:
        # ⛔ The authority ends at the LAST `@`, not the first: both libpq and
        # redis-py split userinfo that way, and a generated password routinely
        # contains a raw `@`. Splitting on the first one PRINTED THE TAIL OF THE
        # PASSWORD (`postgres:p@ssw0rd@host` -> `postgres:***@ssw0rd@host`),
        # which is the one thing the secret-hygiene rule forbids outright
        # (`#13098` review; MEASURED, and `test_a_credential_never_reaches_the_
        # output` stayed green over it because its password had no `@`).
        creds, host = rest.rsplit("@", 1)
        user = creds.split(":", 1)[0]
        return f"{scheme}://{user}:***@{host}"
    return dsn


def _render(result: dict) -> None:
    lane = result["graph_lane"]
    record = result["record"]
    mutation = result.get("mutation")
    print(f"candidate: {result['candidate']}" + (f"  (mutation: {mutation})" if mutation else ""))
    print(f"  graph lane : {lane['uri']} graph={lane['graph']} points={lane['seeded_points']}")
    print(f"  record DSN : {record['dsn']} points={record['seeded_points']}")
    print(f"  rows       : cypher={result['row_count']['cypher']} sql={result['row_count']['sql']}")
    print(f"  columns    : cypher={result['columns']['cypher']} sql={result['columns']['sql']}")
    print(f"  fingerprint: {result['fingerprint']}")
    if result["equal"]:
        print("  DIFF: EMPTY — the two forms are the same answer on this corpus")
    else:
        print(f"  DIFF: {len(result['differences'])} difference(s)")
        for difference in result["differences"]:
            print(f"    - {difference['kind']}: {json.dumps(difference, default=str)[:300]}")


if __name__ == "__main__":
    raise SystemExit(main())
