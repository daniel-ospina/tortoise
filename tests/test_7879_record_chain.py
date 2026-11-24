"""`#7879` capstone — a LIVE write reaches the record, and the record ANSWERS it.

WHY THIS FILE EXISTS
====================
The epic's acceptance sentence is one line, in its own words (comment
2026-11-17, item 4):

    *"Prove it from the read: after backfill, a live tenant's dense read reports
    ``mechanism: index`` from the record, not ``scan_fallback`` on the graph.
    That single line is the acceptance test for the whole of A."*

That sentence had **no falsifier**. Measured at `0fbfbb5b210` (2026-11-23), the
three halves are each pinned somewhere — `test_9078_…` pins the *capture* path
writing the four layers, `test_13331_live_write.py` pins the chokepoint's
outcome, `test_11642_vector_move_live.py` executes `vector_search` against
SEEDED rows — but nothing drove the chain **once**, from a public write to the
dense answer, and nothing could distinguish "the record answered" from "the
graph answered and the record happened to be there". Both directions are
available to a broken tree:

* drop the live projection and the write-side suites red (20 reds on this
  mutation, measured), but the READ side stays green because every read pin
  seeds its own rows;
* make the dense leg never serve the record and exactly ONE unit pin reds
  (`test_11642_vector_move.py::test_the_sdk_searcher_passes_its_carried_tenant`,
  measured) while every end-to-end claim still reads green.

So this file measures the chain, and each leg is built so it can FAIL:

    1. `test_a_live_write_lands_in_the_record_with_its_vector`
        the WRITE half — the row AND the `embedding_vec` the dense leg reads.
        A row without the vector satisfies "the record has the row" and answers
        no dense query; that is the state the graph was retired out of.
    2. `test_the_dense_leg_answers_from_the_record`
        the READ half, through the SHIPPED searcher (`_record_vector_searcher`
        → `run_vector_query`), asserting the leg's own verdict on the wire.
    3. `test_the_record_still_answers_with_the_graphs_copy_deleted`
        the NON-VACUITY proof: the graph's node is deleted FIRST (and the
        deletion is asserted, not assumed), so the answer cannot have come from
        the store the epic is migrating away from.
    4. `test_deleting_the_record_row_flips_the_read_to_the_graph`
        the FALSIFIER for (2) and (3). ⛔ Measured on this tree: with the record
        row present and with it deleted, the same read returns the SAME id at
        the SAME distance — the two stores hold the same vector, so the hit
        list cannot tell them apart. Only the leg's own `mechanism` flips,
        `pgvector` → `scan_fallback`. That is what makes a hit-list assertion
        worthless here and the trace assertion load-bearing.
    5–7. the RUNNER's live legs (`tools/record_runner.py`, `#13210`) — the fold's
        only production entry point, whose suite (`test_record_runner.py`) is
        DB-FREE by design and says so in its own CI registration: *"the one live
        proof is the PR body's, not the suite's"*. A plan → refusal → apply →
        no-op chain that is proven only in a PR body is the class of evidence
        that goes stale silently, so it is proven here against a real server.

⛔ WHAT IS NOT HERE, NAMED SO THE ABSENCE IS VISIBLE.
  * **No production graph and no `live-v5`.** The SDK is opened on a `tmp_path`
    embedded store, so the writes land on a throwaway graph nobody else reads;
    the record tenant is a RUN-UNIQUE `(org, graph)` pair. `journal_event` is
    append-only by trigger, which is why a fixed pair is unusable for
    attribution and why `_tenant()` mints one per test (#9307 review F2/F4).
  * **No backfill.** `#7879`'s stamp bundled "runner" with "apply"; the apply
    half is `record_runner`'s and is exercised (5–7) on a journal the record
    does not hold. What this file does NOT prove is a HISTORIC graph's payload
    reaching the record — that needs the graph's own journal, which a lane
    cannot reach (the deployed graph's `events.jsonl`).
  * **No arm.** `TORTOISE_RECORD_VECTOR_LEG` is not set here: `#13610` made
    `ARMING_DEFAULT = True`, so an unset switch IS the armed posture, and a
    test that exported the flag would prove the flag rather than the default.
  * **No embedder.** The write supplies its own 384-vector, so this file runs
    on `test-record-postgres`'s `[test,postgres]` install, which carries no
    `[embeddings]` extra (#11642's measured 71s vs 204s) — on a keyed,
    embedder-bearing box the server would otherwise compute a DIFFERENT vector
    and the assertion would measure the model instead of the seam.

HOW TO RUN IT (the arming the CI job uses):

    TEST_RECORD_DB_URI=postgresql://…  uv run --extra postgres \\
        pytest tests/test_7879_record_chain.py -q

Unset ⇒ 7 of the 8 legs SKIP by name; the DB-free import-graph legs
(`test_the_runner_cannot_reach_the_cache` and the four `test_the_guard_*`
probes that falsify its predicate) RUN either way, so an unarmed run measures
**5 passed, 7 skipped** and is evidence about those five legs and about nothing
else. The `test-record-postgres` leg gate
(`.github/scripts/record-postgres-leg-gate.py`, #9551) fails a job whose record
legs skip, which is what makes the ARMED run real rather than absent.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

#: The application role + its connect-time spelling have ONE home
#: (`test_9078_record_written_by_production_path.py`), imported rather than
#: copied: the `options=-c role=…` / `quote_via=quote` spelling is #9600's own
#: libpq finding — the thing that decides whether the CONNECT succeeds or trips
#: the write circuit breaker — and a second copy would be a second answer to it.
#: Same rule the `schema` fixture below follows for `_ensure_record_schema`.
from tests.test_9078_record_written_by_production_path import (  # noqa: E402
    _app_role_dsn,
)
from tortoise import record_writer  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.search_engine import (  # noqa: E402
    MECHANISM_PGVECTOR,
    MECHANISM_SCAN_FALLBACK,
    VECTOR_MECHANISM_KEY,
)

#: The env a lane points at a throwaway Postgres carrying the record migrations
#: (the `TEST_RECORD_DB_URI` convention). Unset ⇒ the legs SKIP, never pass.
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: The APPLICATION role's name and its connect-time spelling both come from the
#: `test_9078_record_written_by_production_path.py` import above — nothing here
#: re-declares them.

#: The dense leg's own recorded verdict tokens and its trace key, IMPORTED from
#: their one home in `search_engine` (#4280) rather than re-minted here: a local
#: copy would go stale on a rename and redden with a message that reads as a
#: production break instead of a renamed constant.
MECHANISM_RECORD = MECHANISM_PGVECTOR
MECHANISM_GRAPH_SCAN = MECHANISM_SCAN_FALLBACK
MECHANISM_KEY = VECTOR_MECHANISM_KEY

DIM = 384

#: The CACHE'S FRONT END + every module in the package that CONSTRUCTS a graph
#: client. ⛔ MEASURED, NOT LISTED FROM MEMORY: the six client-constructing names
#: below are what this scan returns at `3c45c8ae875` —
#:
#:     python - <<'EOF'
#:     import ast, pathlib
#:     SYMS = {"FalkorDB", "Graph", "Redis", "RedisMixin", "StrictRedis"}
#:     for p in sorted(pathlib.Path("tortoise").glob("*.py")):
#:         t = p.read_text(); s = ast.parse(t); n = set()
#:         for x in ast.walk(s):
#:             if isinstance(x, ast.ImportFrom) and x.module: n |= {a.name for a in x.names}
#:             elif isinstance(x, ast.Import): n |= {a.name.rsplit(".", 1)[-1] for a in x.names}
#:         if n & SYMS: print("tortoise." + p.stem)
#:     EOF
#:
#: — and it is here rather than computed at test time because the scan costs
#: **2.5 s** (it reads all 283 modules of the package) against this file's `2.0`
#: fast-shard weight. The COMMAND is the pin: re-run it and the list must match.
#:
#: ⛔ WHY AN EXPLICIT LIST IS ACCEPTABLE HERE AND WAS NOT BEFORE. An earlier cut
#: listed five names and called itself exhaustive; measured, it missed every one
#: of the five driver importers below, so `from tortoise.backup import …` in the
#: runner passed green while opening exactly the handle this leg refuses. The
#: difference is not the shape — it is that this list is now the scan's OUTPUT and
#: the scan is quoted, so the next reader can re-derive it instead of trusting it.
#: The residual that remains, stated rather than implied: a module that reaches a
#: driver INDIRECTLY (through a shim this scan would not see) is not covered. The
#: leg is a guard against the CHANGE, not a proof about the package.
CACHE_DOORS = (
    # the two folds and the three write doors — the cache's front end by design
    "tortoise.projection",
    "tortoise.cache_projection",
    "tortoise.sdk",
    "tortoise.api",
    "tortoise.mining",
    # the scan's output: every module that CONSTRUCTS a graph client
    "tortoise.__init__",
    "tortoise.__main__",
    "tortoise.backup",
    "tortoise.cypher_guard",
    "tortoise.embedded_lifecycle",
    "tortoise.session_indexer",
)

#: `tortoise/__init__.py`'s PEP 562 `__getattr__` exports — the two attributes a
#: bare `from tortoise import …` can bind that ARE a way into the cache:
#: `FalkorDB` is the guarded graph-client CONSTRUCTOR (`tortoise/__init__.py:59`)
#: and `Tortoise` is the SDK entry point (`_build_guarded_falkordb` / the SDK
#: class). ⛔ THIS IS WHY `tortoise.__init__` ABOVE IS NOT ENOUGH ON ITS OWN:
#: that entry is the SCAN's file-stem spelling (`"tortoise." + p.stem`), while
#: `from tortoise import FalkorDB` yields `tortoise` + `tortoise.FalkorDB` per
#: `ast.ImportFrom` semantics (its `module` is the PACKAGE, and the bound name
#: lives in `names`). Measured before this entry existed: that spelling — the
#: one `tortoise/projection/__init__.py` itself uses — was green here.
PACKAGE_ROOT_HANDLES = ("tortoise.FalkorDB", "tortoise.Tortoise")

#: The DRIVER CLIENTS, by the name a binding would carry. `falkordb` and
#: `redislite` are whole-package roots (importing either imports a client
#: factory); `redis` is NOT — `redis.exceptions` is a diagnostic import, which is
#: why `tortoise/retry.py` (`redis._parsers.base`) is correctly NOT a door above
#: even though it imports the package.
DRIVER_ROOTS = ("falkordb", "redislite")
DRIVER_CLASSES = ("redis.Redis", "redis.asyncio", "redis.client")

#: Package ROOTS whose mere BINDING is a handle — the `import X` spelling, which
#: no `from X import Y` door above can catch because it names no attribute:
#: `import tortoise` makes `tortoise.FalkorDB` one attribute away (PEP 562), and
#: `import redis` makes `redis.Redis(host=…, port=<graph>)` the Fold's way in.
#: ⛔ KEYED ON THE BINDING, NOT ON THE MODULE BEING IMPORTED — that distinction
#: is the whole entry. `from redis import exceptions` also IMPORTS `redis` (it
#: appears in the collector's set) but binds only `exceptions`, so it stays
#: allowed; `import redis.exceptions` BINDS `redis`, so it does not. The same
#: line separates `from tortoise import journal_segments` (the runner's own
#: legitimate import, binds only `journal_segments`) from `import tortoise`.
BOUND_ROOT_HANDLES = ("tortoise", "redis")

#: A `from X import *` binds X's PUBLIC NAMES into this file, so for a DRIVER
#: root it is the same handle as `import X` — `redis.__all__` carries `Redis`
#: (measured), so `from redis import *` puts `redis.Redis(host=…, port=<graph>)`
#: in the runner's namespace. It was GREEN until this entry existed: a star alias
#: is neither an `ast.Import` node (so no bound root) nor a name any door matches
#: (`_imported_modules` records it as `redis.*`).
#:
#: ⛔ NOT the tortoise root, deliberately — refusing `from tortoise import *`
#: would be a false block on an import that grants no handle: `FalkorDB` and
#: `Tortoise` are PEP 562 lazy attributes, so the star binds NEITHER (measured:
#: the package declares no `__all__`, `FalkorDB` is popped from its globals, and
#: `dir()` does not run `__getattr__`).
STAR_BINDING_ROOTS = (*DRIVER_ROOTS, "redis")


def _imported_modules(path: Path) -> set[str]:
    """Every module name ``path`` imports, INCLUDING the ``from X import Y`` form.

    ⛔ BOTH SPELLINGS MUST BE COLLECTED (measured): `from tortoise import
    projection` records the module `tortoise`, so a collector that only looked at
    `node.module` would see nothing to refuse while the handle is right there in
    the binding (`projection.FalkorProjection`). `tools/record_runner.py` already
    uses that spelling for a sibling (`from tortoise import journal_segments`), so
    it is the file's native idiom rather than a hypothetical. An ``ImportFrom``
    therefore yields BOTH ``module`` and ``module.alias`` per imported name, and a
    relative import yields the alias alone (there is no absolute parent to trust).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            reached |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                reached |= {alias.name for alias in node.names}
                continue
            reached.add(node.module)
            reached |= {f"{node.module}.{alias.name}" for alias in node.names}
    return reached


def _bound_root_packages(path: Path) -> set[str]:
    """Package roots ``path`` BINDS with a bare ``import X[.Y]`` statement.

    ⛔ THIS IS A DIFFERENT QUESTION FROM `_imported_modules`, and collapsing the
    two is what made the package-root door inert. `from redis import exceptions`
    IMPORTS `redis` (so it lands in the set above) but binds only `exceptions`;
    `import redis` BINDS `redis`, through which `redis.Redis(host=…, port=…)` is
    one attribute away. Only the second is a handle, and only an `ast.Import`
    node can produce it — so this reads `Import` nodes and nothing else.

    `import X.Y` binds `X` (Python binds the root), which is why the entry is the
    root and not the dotted name.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }


def _star_import_roots(path: Path) -> set[str]:
    """Roots bound by a ``from X import *`` — a star binds X's public names.

    Kept apart from `_bound_root_packages` because the two spellings do NOT bind
    the same thing for every root — see `STAR_BINDING_ROOTS`: for `redis` a star
    binds the client, for the `tortoise` ROOT it binds no handle at all.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
        and any(alias.name == "*" for alias in node.names)
    }


def _reaches_cache(name: str) -> bool:
    """Does the import NAME (a module or a bound attribute spelling) reach it?"""
    return (
        name.split(".")[0] in DRIVER_ROOTS
        or any(name.startswith(cls) for cls in DRIVER_CLASSES)
        or name in PACKAGE_ROOT_HANDLES
        or any(name == door or name.startswith(door + ".") for door in CACHE_DOORS)
    )


def _cache_reaching_imports(path: Path) -> list[str]:
    """Every import in ``path`` that gives it the cache, SORTED.

    ⛔ ONE PREDICATE, TWO ENTRY POINTS — the leg below grades
    `tools/record_runner.py` with it, and the synthetic probes further down grade
    THE PREDICATE ITSELF, one import spelling at a time. Without the second
    caller every branch of this function is unexercised: the runner's imports are
    all module-form and reach no door, so deleting a whole branch (the `from X
    import Y` alias collection, any added door, the root-binding rule, the star
    binding) left every row green — the "an assertion whose value cannot vary"
    failure, measured.
    """
    hits = {name for name in _imported_modules(path) if _reaches_cache(name)}
    hits |= {root for root in _bound_root_packages(path)
             if root in BOUND_ROOT_HANDLES}
    hits |= {root for root in _star_import_roots(path)
             if root in STAR_BINDING_ROOTS}
    return sorted(hits)


# ── plumbing ───────────────────────────────────────────────────────────────

def _conn():
    """A connection to a Postgres carrying the record migrations, or skip."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the record chain")
    psycopg2 = pytest.importorskip(
        "psycopg2", reason="psycopg2-binary is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _vec(hot: int = 7) -> list[float]:
    """A unit-basis 384-vector: cosine distance to a copy is 0, to any other
    basis vector 1, so an ORDERING here cannot be reordered by float noise.

    Supplied BY THE CALLER on purpose — see the module docstring's "No
    embedder": the point of the leg is the seam, not the model, and a leg whose
    arming depends on an optional extra silently stops measuring on the install
    that lacks it.
    """
    values = [0.0] * DIM
    values[hot] = 1.0
    return values


@pytest.fixture(scope="module")
def schema():
    """The record schema, applied by THIS tree's migrations.

    `_ensure_record_schema` is imported rather than re-implemented: it is the
    ONE place that knows the apply order, the `creates`-conditional entries and
    the re-apply set (#9602/#9800), and a second copy here would be a second
    answer to "what does a migrated container look like" — the drift
    `test_11642_vector_move_live.py` avoids the same way.
    """
    from tests.test_9078_record_written_by_production_path import _ensure_record_schema

    conn = _conn()
    _ensure_record_schema(conn)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name='point' AND column_name='embedding_vec'")
        assert cur.fetchone(), (
            "the container has no `public.point.embedding_vec` — the vector "
            "migration did not apply, so every assertion here would report the "
            "graph fallback as a clean empty answer")
    return conn


@pytest.fixture()
def tenant(schema):
    """A RUN-UNIQUE `(org, graph)` the record's foreign keys accept.

    Unique per test, not per module: `public.journal_event` is APPEND-ONLY by
    trigger (a tenant erasure sets `app.record_journal_purge=on`), so a shared
    pair makes the second leg's DELTA unattributable — and a DELTA is exactly
    what leg 1 asserts.
    """
    run = uuid.uuid4().hex[:12]
    org, graph = f"org_7879_chain_{run}", f"graph_7879_chain_{run}"
    with schema.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (graph, org, graph))
    return org, graph


@pytest.fixture()
def armed(tmp_path, tenant, monkeypatch):
    """An SDK bound to `tenant` with the record ARMED — the live write's shape.

    The graph is an embedded store under `tmp_path`: a throwaway that no other
    lane reads, so `live-v5` and every deployed graph are unreachable by
    construction rather than by a discipline this file could forget.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ[RECORD_DB_ENV]))
    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "graph.db"), event_log_path=str(journal))
    sdk._bind_record_tenant(*tenant)
    return sdk, journal


@pytest.fixture()
def unarmed(tmp_path, tenant, monkeypatch):
    """An SDK bound to `tenant` with the record UNARMED — journal only.

    The runner's legs need a journal the record does NOT hold, and this is how a
    real one is produced: the write path is identical, only the DSN is absent,
    so `#9078`'s "no DSN ⇒ no connection at all" is what keeps the record empty.
    """
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "graph.db"), event_log_path=str(journal))
    sdk._bind_record_tenant(*tenant)
    return sdk, journal


def _count(conn, table: str, tenant, *, ids_only: bool = False):
    org, graph = tenant
    column = "count(embedding_vec)" if ids_only else "count(*)"
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*), {column} FROM public.{table} "
            "WHERE org_id=%s AND graph_id=%s", (org, graph))
        return cur.fetchone()


def _run_runner(*args: str, journal: Path, tenant, env_extra: dict | None = None):
    """`tools/record_runner.py` as a SUBPROCESS — its exit code is the contract."""
    org, graph = tenant
    env = dict(os.environ)
    env.pop("TORTOISE_RECORD_DSN", None)
    env[record_writer.RECORD_DSN_ENV] = _app_role_dsn(os.environ[RECORD_DB_ENV])
    env.update(env_extra or {})
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "record_runner.py"), *args,
         "--org-id", org, "--graph-id", graph, "--journal", str(journal), "--json"],
        capture_output=True, text=True, timeout=300, env=env, cwd=str(REPO_ROOT))
    return proc


def _json(proc) -> dict:
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:  # pragma: no cover - a crash names itself
        raise AssertionError(
            f"record_runner emitted no JSON (exit {proc.returncode}):\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}") from None


# ── 1. the WRITE half ──────────────────────────────────────────────────────

def test_a_live_write_lands_in_the_record_with_its_vector(schema, armed, tenant):
    """A public write reaches the record AS A ROW AND AS A VECTOR.

    ⛔ THE VECTOR IS THE ASSERTION. `#7879`'s stamp described the state this
    replaces — *"a live tenant's record legitimately holds ZERO rows while the
    graph holds the whole payload"* — and a row without `embedding_vec` is
    strictly worse than that: it looks like coverage, satisfies every
    `count(*)` gate, and answers no dense query. The graph was retired out of
    `:Object`/`:Subject` on the strength of this column (#13629), so the two
    halves are asserted together and neither may be satisfied alone.

    The identity row and the position row are asserted too, because both are
    what make the row *reachable*: `public.point`'s (org, graph, label, key) FK
    is the `#9881` key move (a point without an identity row is one the record
    refuses with 23503), and the position row is the record's own ledger — the
    thing `record_runner` reads as a checkpoint and `record-reconcile` reads as
    a lag.
    """
    sdk, journal = armed
    embedding = _vec()
    #: ONE spelling of the content, so the write below and the lookup after it
    #: cannot drift apart — the lookup is what makes the key assertion
    #: non-vacuous, so a second literal here would quietly put it back to
    #: "find the row by the id, then assert the id".
    content = "the record holds this claim"
    out = sdk._create_point("statement", content,
                            extractedFrom=None, embedding=embedding)
    point_id = out["id"]

    total, with_vec = _count(schema, "point", tenant, ids_only=True)
    assert total == 1, (
        "the live write did not reach `public.point` — the record is a journal "
        "FOLD, so a missing row here means the fold was not invoked on this "
        f"write (rows={total}). journal on disk: "
        f"{journal.read_text(encoding='utf-8') if journal.exists() else '<none>'}")
    assert with_vec == 1, (
        "the point reached the record WITHOUT a vector — `embedding_vec` is the "
        "only column the dense leg reads, so this row is invisible to every "
        "semantic query while satisfying `count(*)`")

    # ⛔ THE LOOKUP IS BY CONTENT, NOT BY `id`, and that is the whole point of
    # this assertion. Selecting the row with `WHERE … id = point_id` and then
    # asserting `row[0] == point_id` cannot fail: the returned column IS the
    # value it was selected by, and `public.point.id` is `text`
    # (`20261012000003_record_epistemic.sql:55`), so not even a coercion edge
    # exists. That was cycle 1's F2 finding — and its "fix" reproduced the very
    # defect it removed, so this lookup takes the expected value from something
    # the fold was never told to preserve: the content the write supplied. A
    # fold that keys the row by, say, a content hash now satisfies `count(*)`
    # above and REDS here, which is the `#9881` reachability claim the
    # docstring makes.
    with schema.cursor() as cur:
        cur.execute(
            "SELECT id FROM public.point WHERE org_id=%s AND graph_id=%s "
            "AND content=%s", (*tenant, content))
        rows = cur.fetchall()
    assert len(rows) == 1, (
        f"expected exactly one `public.point` row carrying the written content "
        f"{content!r}, found {len(rows)} — the row the dense leg joins its kNN "
        "hit back to is the one a reader can find from the payload, not one "
        "only a row-count gate can see")
    assert rows[0][0] == point_id, (
        f"the record's row is keyed {rows[0][0]!r}, not the id the write "
        f"returned {point_id!r} — the dense leg joins the kNN's hit back to "
        "this column, so a row under a different key is a row the read "
        "returns and cannot resolve")
    # ⛔ NO `vector_dims() == DIM` ASSERTION HERE, on purpose: `embedding_vec` is
    # declared `vector(384) GENERATED ALWAYS AS public.record_embedding_vector(
    # embedding) STORED` (`20261109000001_record_embedding_vector.sql:207`), so
    # `vector_dims()` returns 384 for EVERY non-NULL value — and `with_vec == 1`
    # above already proved non-NULL. It would be an assertion that cannot fail,
    # which is the one thing this file exists to not be. A width drift is caught
    # where it happens instead: the INSERT raises, or `with_vec` reads 0.

    assert _count(schema, "journal_event", tenant)[0] == 1, (
        "no `journal_event` position row — the fold consumed the record but did "
        "not file its own ledger entry, which is what every reader uses to say "
        "whether a tenant's record holds its journal")
    assert _count(schema, "node_identity", tenant)[0] == 1, (
        "no `node_identity` row — the point's identity FK is what makes the "
        "row reachable at all (#9881)")


# ── 2. the READ half, through the shipped searcher ─────────────────────────

def _dense_answer(sdk, query_vec, *, limit: int = 5):
    """One dense read through the SHIPPED path, with its own trace returned.

    `_record_vector_searcher` is the production resolver *of the leg* — it is
    where the tenant, the arming switch, the per-tenant journal readiness and
    the coverage gate are decided — and `run_vector_query` is the ONE ordering
    pass both stores feed. Calling `record_vector.vector_search` directly (as
    `test_11642_vector_move_live.py` does, correctly, for its own subject) would
    skip every one of those decisions and prove the SQL alone.
    """
    from tortoise import sdk as sdk_module
    from tortoise.search_engine import run_vector_query

    projection = sdk._get_proj()
    searcher = sdk_module._record_vector_searcher(sdk, projection=projection)
    assert searcher is not None, (
        "the dense leg's record searcher resolved to None — the record is armed "
        "by DEFAULT since #13610 and this tenant has a fresh journal, so every "
        "reason to decline here (no DSN, no tenant, unarmed) is a regression")
    trace: list[dict] = []
    hits = run_vector_query(projection.g, query_vec, limit=limit,
                            entity_type="point", pg_vector=searcher,
                            leg_trace=trace, provenance="test_7879_chain")
    return hits, trace, projection


def test_the_dense_leg_answers_from_the_record(schema, armed, tenant):
    """The epic's acceptance line, on the wire: `mechanism: pgvector`.

    ⛔ `pgvector`, NOT the epic's word `index`, AND THE DIFFERENCE IS LOAD-BEARING.
    The epic's sentence (quoted at the top of this file) says *`mechanism: index`*;
    the wire token is `pgvector` because `search_engine.MECHANISM_PGVECTOR` is
    deliberately a FOURTH value rather than a reuse of `MECHANISM_INDEX` — its own
    docstring: *"the graph's index and the record's index are different stores with
    different failure modes, and a measurement that cannot tell which one answered
    cannot price the move or detect a store silently reverting to the other"*. So a
    reader who "repairs" the token below to the epic's spelling would red a HEALTHY
    tree; the constant is imported, not re-minted, for the same reason.

    ⛔ AND THE TRACE IS THE ASSERTION, not the hit list. A hit proves *some* store
    answered, and the two stores hold the same vector by construction — so a
    broken record leg falling through to the graph returns the SAME id and the
    same distance. The leg's own recorded verdict is the only thing that says
    which of them answered, which is why it is asserted rather than inspected.
    """
    sdk, _ = armed
    embedding = _vec()
    out = sdk._create_point("statement", "the dense leg reads this from the record",
                            extractedFrom=None, embedding=embedding)
    point_id = out["id"]

    hits, trace, _ = _dense_answer(sdk, embedding)

    ids = [row[0] if isinstance(row, (tuple, list)) else row for row in hits]
    assert point_id in ids, (
        f"the freshly written point {point_id!r} is absent from the dense answer "
        f"{ids!r} — the write reached the record but the read did not serve it")
    assert trace, "the read recorded no leg trace at all — nothing can say which store answered"
    leg = trace[0]
    assert leg["ran"] is True, f"the vector leg did not run: {leg}"
    assert leg[MECHANISM_KEY] == MECHANISM_RECORD, (
        f"the dense leg answered with {MECHANISM_KEY}={leg[MECHANISM_KEY]!r}, not "
        f"{MECHANISM_RECORD!r} — the record did not serve this read. Full trace: {leg}")
    assert leg["reason"] == "ok", f"the leg ran but did not say `ok`: {leg}"
    assert leg["degraded"] is False, (
        f"the leg reported `degraded` on a healthy record: {leg}")


# ── 3. the non-vacuity proof ───────────────────────────────────────────────

def test_the_record_still_answers_with_the_graphs_copy_deleted(schema, armed, tenant):
    """Delete the graph's node FIRST; the record must still answer.

    ⛔ THIS IS WHAT MAKES LEG 2 NON-VACUOUS. Both stores hold the same vector, so
    a read that served the graph would return the identical hit — the answer is
    only evidence about the RECORD once the other store cannot produce it.

    ⛔ AND THE DELETION IS ASSERTED, not assumed. `DETACH DELETE` on an embedded
    store is cheap and synchronous, but an assertion of the form "the graph is
    empty, therefore…" that never checked the graph is the exact shape this repo
    removes on sight (`#7996`'s class: a premise measured by its own absence).
    """
    sdk, _ = armed
    embedding = _vec()
    out = sdk._create_point("statement", "the record survives losing its cache copy",
                            extractedFrom=None, embedding=embedding)
    point_id = out["id"]

    projection = sdk._get_proj()

    # ⛔ THE COUNT IS A TYPED, PRIVATE MEASUREMENT, NOT RAW CYPHER. Two reasons, and
    # the second is not stylistic: `_label_vector_count` is the ONE aggregate that
    # answers "how many nodes of this label can the cache serve densely" (#13610),
    # so it is exactly this leg's premise; and `surface-guard`'s privatisation
    # sweep counts every untyped `.query` receiver as residue it must re-pin, so a
    # test adding raw Cypher for a question the package already answers in a typed
    # method is a cost the gate pays. (The gate is red on `origin/main` already —
    # it was 11901 observed against a 11896 pin before this file existed — so the
    # point is not to turn someone else's red green, it is to not add to it.)
    def _graph_vectors() -> int | None:
        return projection._label_vector_count("point")

    assert _graph_vectors() == 1, (
        "the premise: the cache holds one vector-bearing :Point before the delete, "
        f"measured {_graph_vectors()!r}")
    # ⛔ AND THE DELETE IS TYPED TOO — `_delete_entity_by_id` is the projection's
    # own hard-delete of ONE canonical entity by id (the replay counterpart of the
    # SDK's live `_delete_entity`, #3299). It touches the cache and nothing else,
    # which is what this leg needs: the record row must survive the cache losing
    # its copy. Reaching for `MATCH (n:Point) DETACH DELETE n` would delete the
    # WHOLE label — a blunter instrument than the subject — and would add another
    # untyped `.query` receiver to the privatisation sweep's residue count.
    deleted = projection._delete_entity_by_id(point_id, "Point")
    assert deleted == 1, (
        f"the cache delete removed {deleted!r} node(s), not 1 — the premise of "
        "this leg is that exactly this point's cache copy went away")
    assert _graph_vectors() == 0, (
        "the cache still holds a vector-bearing :Point after DETACH DELETE "
        f"({_graph_vectors()!r}) — the rest of this test would then be proving the "
        "cache answered, which is the opposite of its subject")

    assert _count(schema, "point", tenant)[0] == 1, (
        "the record lost the row when the graph's copy went — this is the "
        "§2.0 inversion: the cache is derived, the record is the truth")

    hits, trace, _ = _dense_answer(sdk, embedding)
    ids = [row[0] if isinstance(row, (tuple, list)) else row for row in hits]
    assert point_id in ids, (
        f"with the graph's node deleted the dense read returned {ids!r} — the "
        "point exists ONLY in the record now, so an empty (or short) answer is "
        "the record failing to serve the read the epic exists for")
    assert trace[0][MECHANISM_KEY] == MECHANISM_RECORD, (
        f"a read that can only have come from the record reported "
        f"{MECHANISM_KEY}={trace[0][MECHANISM_KEY]!r}: {trace[0]}")


# ── 4. the falsifier ───────────────────────────────────────────────────────

def test_deleting_the_record_row_flips_the_read_to_the_graph(schema, armed, tenant):
    """The other side: remove the RECORD row and the leg reports the GRAPH.

    ⛔ THE TRACE IS THE WHOLE TEST, AND THE HIT LIST WOULD BE A LIE. Measured on
    this tree: with the record row present and with it deleted, the SAME read
    returns the SAME id at the SAME distance — because `point` is a FUSED leg
    (record first, graph fallback, `#13610`'s non-contract set) and both stores
    hold the same vector. Only `mechanism` changes, `pgvector` →
    `scan_fallback`. An assertion on the ids therefore cannot distinguish the
    two states, which is exactly why this file asserts the leg's own verdict.

    Together with legs 2-3 this makes the claim two-sided: the answer follows
    the RECORD ROW in both directions — present ⇒ `pgvector`, absent ⇒ the graph
    — so "the record is the dense source" is a measurement of this row rather
    than a coincidence of two stores holding the same data.

    ⚠️ What this leg deliberately does NOT assert: that the id vanishes. It
    must not — the graph is a complete copy for this label, and a read that
    returned nothing would be a REGRESSION (the `#11691` `scan_fallback`
    contract). A lane reading this test should take "the record stopped
    serving" from the mechanism token, never from an empty list.
    """
    sdk, _ = armed
    embedding = _vec()
    out = sdk._create_point("statement", "this row is about to be removed",
                            extractedFrom=None, embedding=embedding)
    point_id = out["id"]

    # The control: with the row present the leg says it served the record.
    control_hits, control_trace, _ = _dense_answer(sdk, embedding)
    assert control_trace[0][MECHANISM_KEY] == MECHANISM_RECORD, (
        f"the control read did not come from the record, so this leg cannot "
        f"say anything about the row it removes: {control_trace[0]}")

    with schema.cursor() as cur:
        cur.execute("DELETE FROM public.point WHERE org_id=%s AND graph_id=%s "
                    "AND id=%s", (*tenant, point_id))
    assert _count(schema, "point", tenant)[0] == 0, (
        "the record row was not deleted — the rest of this leg would assert "
        "nothing")

    hits, trace, _ = _dense_answer(sdk, embedding)
    ids = [row[0] if isinstance(row, (tuple, list)) else row for row in hits]
    assert trace[0][MECHANISM_KEY] == MECHANISM_GRAPH_SCAN, (
        f"the record row is gone and the leg STILL reports "
        f"{MECHANISM_KEY}={trace[0][MECHANISM_KEY]!r} — the answer did not follow the "
        f"record row, so legs 2-3 were proving something else. Trace: {trace}")
    assert point_id in ids, (
        f"the graph branch did not answer after the record declined ({ids!r}) "
        "— an empty result here is a REGRESSION of the named fallback, not the "
        "expected outcome of this leg")
    # Restate the two states in one line so the evidence is non-vacuous even to
    # a reader who only skims the failure: the ids are IDENTICAL across the flip.
    assert [r[0] if isinstance(r, (tuple, list)) else r for r in control_hits] == ids, (
        "the control and the post-delete reads returned different ids, so this "
        "leg is not measuring a mechanism flip over a constant answer")


# ── 5-7. the RUNNER's live legs ────────────────────────────────────────────

def test_the_runner_sees_a_journal_the_record_does_not_hold(schema, unarmed, tenant):
    """`status` measures the gap the runner exists to close.

    The journal is real (the same production write path, record unarmed) and the
    record is a fresh tenant, so "2 events remaining" is a fact about this
    fixture rather than about a deployment.
    """
    sdk, journal = unarmed
    sdk._create_point("statement", "historic payload one", extractedFrom=None,
                      embedding=_vec())
    sdk._create_object("Historic Object", "product")

    proc = _run_runner("status", journal=journal, tenant=tenant)
    assert proc.returncode == 0, f"status failed: {proc.stderr}"
    status = _json(proc)
    assert status["journal"]["records"] == 2, (
        f"the journal read back {status['journal']} — the fixture's two writes "
        "are what every count below is a delta of")
    assert status["events_remaining"] == 2, (
        f"status reported {status['events_remaining']} remaining for a journal "
        f"the record has never seen: {status}")
    assert status["fully_consumed"] is False


def test_the_runner_refuses_to_write_without_the_plan_acknowledgement(
        schema, unarmed, tenant):
    """`#11469`'s report-before-drop guarantee, made mechanical.

    A runner that folds without first making an operator read the plan is the
    "act on a plan nobody read" shape `CURATION-11469.md` forbids, and the
    refusal is only worth anything if it ALSO wrote nothing — so the row count
    is the second half of the assertion, not a comment.
    """
    sdk, journal = unarmed
    sdk._create_point("statement", "must not be folded yet", extractedFrom=None,
                      embedding=_vec())

    proc = _run_runner("plan", journal=journal, tenant=tenant)
    assert proc.returncode == 0, f"plan failed: {proc.stderr}"
    plan = _json(proc)
    to_consume = plan["events_to_consume"]
    assert to_consume == 1, f"the plan reported {to_consume} events to consume"

    refused = _run_runner("apply", journal=journal, tenant=tenant)
    assert refused.returncode == 1, (
        "`apply` without `--acknowledge-plan` must exit REFUSED (1); it exited "
        f"{refused.returncode}. stdout={refused.stdout!r} stderr={refused.stderr!r}")
    assert "REFUSED" in refused.stderr, (
        f"the refusal did not name itself on stderr: {refused.stderr!r}")
    assert _count(schema, "point", tenant)[0] == 0, (
        "the refused run WROTE a row — a refusal that writes is not a refusal")
    assert _count(schema, "journal_event", tenant)[0] == 0, (
        "the refused run filed a position row — the record's ledger now claims "
        "a consumption that did not happen, which is worse than the write")


def test_the_runner_folds_and_a_second_run_is_a_no_op(schema, unarmed, tenant):
    """plan → apply → rows land → re-run does NOTHING (and says so).

    The ledger half is the point: `#7879`'s item 1 asked for *"a recorded ledger
    so a re-run is a no-op"*, and the runner derives that ledger from the
    record's own `journal_event` positions rather than keeping a second cursor.
    A re-run that replays "and relies on ON CONFLICT" would pass a row-count
    assertion while doing 4 statements of work, so the assertion is on the
    runner's OWN `statements_executed` — the claim, not the side effect.
    """
    sdk, journal = unarmed
    sdk._create_point("statement", "fold me into the record", extractedFrom=None,
                      embedding=_vec())
    sdk._create_object("Foldable Object", "product")

    plan = _json(_run_runner("plan", journal=journal, tenant=tenant))
    to_consume = plan["events_to_consume"]
    assert to_consume == 2, f"the plan reported {to_consume} events: {plan}"
    assert plan["rows_written"] == 0 and plan["statements_executed"] == 0, (
        f"`plan` reported work performed ({plan}) — the plan path must write "
        "nothing, which is what makes `--dry-run` structural rather than a flag")

    applied = _run_runner("apply", "--acknowledge-plan", str(to_consume),
                          journal=journal, tenant=tenant)
    assert applied.returncode == 0, (
        f"apply failed (exit {applied.returncode}): stderr={applied.stderr!r}")
    report = _json(applied)
    assert report["consumed"] == to_consume, (
        f"apply consumed {report.get('consumed')} of {to_consume}: {report}")
    assert report["statements"] > 0, (
        f"apply reported {report['statements']} statements for {to_consume} "
        f"events — nothing was written: {report}")
    assert report["no_op"] is False, f"a folding run claimed to be a no-op: {report}"

    # ⛔ `ids_only=True` IS LOAD-BEARING, not decoration. `_count` builds
    # `SELECT count(*), count(*)` without it, so `points[1]` would BE `points[0]`
    # and the assertion below could not fail — while its message claims the
    # vector is missing. A fold that wrote the row with `embedding = NULL` was
    # green against that shape (measured: `count(*) = 1`, `count(embedding_vec)
    # = 0` over one NULL-embedding row on this schema).
    points = _count(schema, "point", tenant, ids_only=True)
    objects = _count(schema, "object", tenant)
    assert points[0] == 1, f"the folded point is not in the record: {points}"
    assert points[1] == 1, (
        "the folded point has no `embedding_vec` — a backfill that drops the "
        "vector moves rows into a store whose dense leg cannot read them")
    assert objects[0] == 1, f"the folded object is not in the record: {objects}"
    assert _count(schema, "journal_event", tenant)[0] == 2, (
        "the position rows the fold writes are the runner's OWN checkpoint; "
        "without them a second run cannot know it is done")

    again = _json(_run_runner("apply", "--acknowledge-plan", "0",
                              journal=journal, tenant=tenant))
    assert again["consumed"] == 0, (
        f"a second apply consumed {again['consumed']} events: {again}")
    assert again["statements"] == 0, (
        f"a second apply executed {again['statements']} statements — the run is "
        f"replaying rather than declining: {again}")
    assert again["no_op"] is True, (
        f"a second apply did not report itself as the no-op it is: {again}")
    assert _count(schema, "point", tenant)[0] == 1, (
        "the second run duplicated the point instead of folding onto it")

    final = _json(_run_runner("status", journal=journal, tenant=tenant))
    assert final["events_remaining"] == 0, f"status still sees a gap: {final}"
    assert final["fully_consumed"] is True, f"status did not report completion: {final}"


def test_the_runner_cannot_reach_the_cache():
    """The runner is a fold, not a migration of the graph (`#10175`).

    ⛔ IT IS STRUCTURAL AND IT IS DB-FREE. `#10175` forbids the runner from
    deleting, pruning or "cleaning" the graph and `#5333` governs customer
    memories, so the guarantee cannot be a promise the tool makes at runtime — it
    has to be that the tool *has no way in*. `#7879`'s own stamp said the same
    thing in its "not authorised here" line.

    Asserted on the module's IMPORTS (via `ast`), not on its text: a runner that
    reached the cache would do it through `from tortoise.projection import …` or
    a `falkordb`/`redis`/`redislite` client, and every one of those is an import
    binding. A substring scan over the source would also match PROSE — this very
    file's sibling suites discuss the graph in comments — so it both over- and
    under-matches. `ast` sees what the module can actually reach.

    Deliberately no DSN and no tenant: the subject is a file's import graph, so
    this leg RUNS in the fast matrix instead of skipping there.
    """
    reached = _imported_modules(REPO_ROOT / "tools" / "record_runner.py")

    hits = _cache_reaching_imports(REPO_ROOT / "tools" / "record_runner.py")
    assert hits == [], (
        f"`tools/record_runner.py` imports {hits} — the runner must not be able "
        "to reach the cache at all (#10175 forbids pruning or deleting it, and "
        "#5333 governs customer memories). A fold that can open a graph handle "
        "is a data-loss surface, not a regression. Imports seen: "
        f"{sorted(reached)}")
    # POSITIVE CONTROL — the leg is graded against a file that was really read.
    for expected in ("tortoise.record_projection", "tortoise.record_writer"):
        assert expected in reached, (
            f"the runner does not import `{expected}`, so this leg read nothing "
            f"it expected and would be vacuous: {sorted(reached)}")


# ── 8b. the guard's OWN falsifiers ──────────────────────────────────────────

#: ⛔ THESE EXIST BECAUSE THE LEG ABOVE CANNOT FAIL ON THE SPELLINGS THAT MATTER.
#: `tools/record_runner.py` is one fixed file whose imports are all module-form
#: and reach no door, so every branch of the predicate is UNEXERCISED by it: a
#: reviewer measured the `from X import Y` alias collection deleted, the six
#: added `CACHE_DOORS` deleted, and a bare `import redis` / `from tortoise import
#: FalkorDB` appended to the runner — all four stayed GREEN. Each probe below is a
#: synthetic source in `tmp_path`, DB-free and microseconds, whose every row goes
#: red if the branch it names is reverted.
#:
#: ⛔ `tmp_path` SOURCES, NEVER THE REAL RUNNER: the leg's subject is a repo file
#: and must not be mutated by a test run, and `ast` only needs the TEXT — these
#: modules are parsed, never imported.
def _probe(tmp_path, source: str) -> Path:
    probe = tmp_path / "probe.py"
    probe.write_text(source, encoding="utf-8")
    return probe


def test_the_guard_refuses_the_bare_root_bindings(tmp_path):
    """⛔ P1-1, P1-2 — the two spellings that were GREEN on the pre-fix predicate.

    `import redis` was refused by the code this leg replaced (its `FORBIDDEN`
    listed the bare `redis` root and matched by prefix) and became green when the
    predicate was rewritten around `DRIVER_ROOTS`, which deliberately omits
    `redis`. `from tortoise import FalkorDB` was green in both cuts. Both open
    exactly the handle `#10175`/`#5333` forbid, from a file whose subject is that
    the door was too narrow.
    """
    probe = _probe(tmp_path, (
        "import redis\n"
        "import redis.exceptions\n"
        "from tortoise import FalkorDB\n"
        "from tortoise import Tortoise\n"
        "import tortoise\n"
    ))
    assert _cache_reaching_imports(probe) == [
        "redis", "tortoise", "tortoise.FalkorDB", "tortoise.Tortoise",
    ], (
        "a bare driver/package-root binding is a way INTO the cache — `import "
        "redis` gives `redis.Redis(host=…, port=<graph>)` and `import tortoise` "
        "gives `tortoise.FalkorDB` one attribute away. Measured: "
        f"{_cache_reaching_imports(probe)} — every one of these was GREEN before "
        "the root-binding rule.")


def test_the_guard_refuses_the_from_import_doors(tmp_path):
    """The `from X import Y` spelling (item 2's alias collection) — every door.

    Each name below is reachable ONLY through the `module.alias` branch of
    `_imported_modules`: `from tortoise import projection` records `tortoise`,
    which matches no door by itself. Deleting that branch reddens every row.
    """
    probe = _probe(tmp_path, (
        "from tortoise import projection\n"
        "from tortoise import cache_projection\n"
        "from tortoise.backup import something\n"
        "from redis import Redis\n"
        "import falkordb\n"
        "import redislite\n"
    ))
    assert _cache_reaching_imports(probe) == [
        "falkordb",
        "redis.Redis",
        "redislite",
        "tortoise.backup",
        "tortoise.backup.something",
        "tortoise.cache_projection",
        "tortoise.projection",
    ], f"these are all doors: {_cache_reaching_imports(probe)}"


def test_the_guard_allows_the_folds_own_imports(tmp_path):
    """The ALLOW side — without it the two probes above are satisfied by a
    predicate that refuses everything, which would be a different bug.

    Every line here is a spelling the runner actually uses or a diagnostic import
    the door list deliberately permits, and each is the *minimal pair* of a
    refused one above: same package, different attribute (`from tortoise import
    journal_segments` vs `FalkorDB`), or same package, different binding (`from
    redis import exceptions` vs `import redis`).
    """
    probe = _probe(tmp_path, (
        "from tortoise import journal_segments\n"
        "from tortoise.log import EventLog\n"
        "from tortoise.record_projection import replay\n"
        "from tortoise.record_writer import record_dsn\n"
        "from redis import exceptions\n"
    ))
    assert _cache_reaching_imports(probe) == [], (
        "these imports grant no handle — `from tortoise import …` binds only the "
        "named submodule, and `from redis import exceptions` binds only "
        "`exceptions` (so no `redis` root is bound) — refusing them would block "
        f"the runner itself: {_cache_reaching_imports(probe)}")


def test_the_guard_refuses_a_star_import_from_a_driver_root(tmp_path):
    """`from redis import *` BINDS `Redis` — measured, and GREEN until this row.

    The star spelling is the one binding NEITHER collector above sees: it is not
    an `ast.Import` node (so no bound root), and `_imported_modules` records it as
    `redis.*`, which matches no door. `redis.__all__` carries `Redis`, so the
    statement puts `redis.Redis(host=…, port=<graph>)` in the runner's namespace —
    the same handle `import redis` gives, which the row above refuses.

    ⛔ THE ALLOW ROW IS THE OTHER HALF, and it is why `STAR_BINDING_ROOTS` is not
    the package root: `from tortoise import *` binds NEITHER `FalkorDB` nor
    `Tortoise` (PEP 562 lazy attributes, absent from `dir()`, and the package
    declares no `__all__`; measured), so refusing it would be a false block on an
    import that cannot reach a client.
    """
    assert _cache_reaching_imports(
        _probe(tmp_path, "from redis import *\n")) == ["redis"]
    assert _cache_reaching_imports(
        _probe(tmp_path, "from falkordb import *\n")) == ["falkordb", "falkordb.*"]
    assert _cache_reaching_imports(
        _probe(tmp_path, "from tortoise import *\n")) == []
