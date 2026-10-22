"""The record is WRITTEN BY PRODUCTION — the call site, not a probe (#9188 / #9078).

THE DEFECT THIS PINS. At `221673135` (and at the #9078 head it was measured on),
`tortoise/record_projection.py` was complete, proven, and **called by nothing in
production**: its only importer was `supabase/tests/pglite/validate.mjs`. So the
epic's acceptance sentence — *"a user's Points, operators, edges, Objects,
Subjects and the event stream exist in Postgres"* — was a claim about a test
fixture. No unit test could see that, because every unit test drove the fold
itself; the missing thing was a **caller**.

So the proof here is deliberately not "the fold can write". It is:

1. **There is a production caller**, and it is inside the shipped package
   (`tortoise/sdk.py`), which is what `tools/record_wiring_check.py` asserts —
   a `tests/**` or `docs/evidence/**` caller cannot satisfy it, because
   production is *the package that is installed*, not a path allowlist a probe
   can join. (Indicator 3 of #9188: "no `docs/evidence/**` or `tests/**` caller
   is what satisfies the gate".)
2. **The SDK's own public capture path writes rows into all four ontology
   layers** — `event` · `object`/`subject` · `point`/`relation` · procedural —
   with no probe module and no monkeypatched writer. This leg needs a Postgres
   carrying the record migrations (`TEST_RECORD_DB_URI`, the same pattern
   `tests/test_audit_events.py` uses for `TEST_AUDIT_DB_URI`); the schema is
   applied from `supabase/migrations/` on first use, so no manual setup.
3. **An armed write is never a guess**: no tenant ⇒ refused and counted, no
   DSN ⇒ no connection at all (byte-identical capture for an unarmed
   deployment), and the live path writes layer 1 (`journal_event`) from the
   ordinal the journal's own append reported — never a number it derives
   (#9190).
4. **A loss is OBSERVABLE.** One record shape still cannot be projected because
   the SCHEMA's column type disagrees with what the producer emits (measured
   below). It is pinned by EQUALITY, so this file goes red the moment it is
   fixed and the named-away entry must be re-measured — a permanent
   exclusion could otherwise go stale in silence, which is the class of defect
   this epic exists to remove.

WHAT IS NOT HERE, NAMED SO THE ABSENCE IS VISIBLE.

* The reconciler's production trigger (#9189), and (before it landed) the
  `journal_event.seq` ordinal
  (#9190) and the reconciler's own dirty-run behaviour are other lanes.
* ⚠️ **No CI job arms `TEST_RECORD_DB_URI`** (checked: `.github/` and `config/`
  never set it), so the two Postgres legs skip in CI and the DB-FREE wiring gate
  (`tools/record_wiring_check.py`) is what guards this seam on the PR path. The
  Postgres legs are the operator-run acceptance command from #9078
  ("a throwaway Postgres with only the RECORD migrations"), not a CI check.
  Wiring them into a CI service container is its own change.
* The fold defect that keeps captured Points out of the record today is pinned
  CONDITIONALLY (see `_fold_refuses_captured_points`), so this file neither
  hides it nor reddens the day it is fixed.
"""
from __future__ import annotations

import ast
import asyncio
import fnmatch
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tortoise import monitoring, record_projection, record_writer  # noqa: E402
from tortoise.record_writer import use_record_tenant  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402

#: The env var a lane points at a throwaway Postgres with the record migrations
#: (the `TEST_AUDIT_DB_URI` convention). Unset ⇒ the DB legs SKIP, never pass.
RECORD_DB_ENV = "TEST_RECORD_DB_URI"

#: A **run-unique** tenant (#9307 review F4). `public.journal_event` is
#: APPEND-ONLY by trigger, so `_seed_tenant` cannot clear it — which makes a
#: fixed pair unusable for attribution: on a reused database (a shape
#: `_ensure_record_schema` explicitly supports) a second run re-numbers a fresh
#: journal from 1, `_journal_row`'s `ON CONFLICT (org_id, graph_id, seq) DO
#: NOTHING` absorbs every row, and the count is the PREVIOUS run's maximum. A
#: revert of the live door would then pass on rows it never wrote. With a pair
#: this process has never used, the rows can only be this run's: the door wrote
#: them, or the leg has nothing to count.
#:
#: Per TEST as well as per run (#9307 review F2): two capture legs under ONE
#: pair each number their own fresh journal from 1, so the second one's position
#: rows are absorbed by the same `DO NOTHING` and its delta reads 0 — a false red
#: the moment those legs run in the other order. `_new_tenant` is therefore
#: called per test through the `tenant` fixture, and the constants below serve
#: only the DB-FREE legs, which need *a* pair in scope and count nothing.
def _new_tenant() -> tuple[str, str]:
    token = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    return (f"org-9188-production-path-{token}",
            f"graph-9188-production-path-{token}")


ORG, GRAPH = _new_tenant()


@pytest.fixture()
def tenant() -> tuple[str, str]:
    """A tenant pair no other test in this PROCESS has used.

    `journal_event` is append-only and every capture numbers its own fresh
    journal from 1, so a shared pair makes the second capture's rows collide
    with the first's and its delta read 0 — the false red this fixture removes
    by construction (#9307 review F2).
    """
    return _new_tenant()


#: Journal keys whose emitted VALUE the record's declared column type cannot
#: hold — a schema/producer DISAGREEMENT, not a folding bug (`_jsonb_or_scalar`
#: deliberately refuses to coerce, so the disagreement stays visible instead of
#: being papered over). Derived from the RECORD ITSELF, never hard-coded by
#: record type: the moment the schema or the producer is fixed, the record stops
#: matching and the expectation flips to "must project cleanly" on its own — so
#: nothing here can go stale in silence.
#:
#:   * ``search_keys`` — an EXTRACTED point carries the graph's flattened string
#:     (`_flatten_search_keys_prop`, #8006/#5482), while
#:     `public.point.search_keys` is `jsonb`: ``invalid input syntax for type
#:     json``. Two record types re-project the same snapshot
#:     (``PointAdded``/``PointPromoted``), so the TYPE is not the unit.
#:   * ``capture_redactions`` — the capture receipt's integer COUNT, while
#:     `public.session.capture_redactions` was `jsonb`: ``column
#:     "capture_redactions" is of type jsonb but expression is of type integer``.
#:     **FIXED (#9264)** by the forward migration
#:     `20261022000001_record_session_capture_redactions_integer.sql`, so the
#:     entry and its detection are gone: every other counter on
#:     `public.session` is `integer`, which made `jsonb` the anomaly rather than
#:     a decision.
#:
#: Measured at #9200's head (`62ec432a8`) + this diff, against a real Postgres.
#: Reported on #9200 (the producer-accuracy class it belongs to): the fix is a
#: schema/placement decision, not a wiring one.
_SCHEMA_DISAGREEMENTS = {
    "search_keys": "invalid input syntax for type json",
}


def _disagreement(record: dict) -> str | None:
    """Which declared-column disagreement this journal record carries, if any."""
    snapshot = record.get("point") or {}
    keys = snapshot.get("search_keys")
    if isinstance(keys, str) and keys.strip():
        return "search_keys"
    return None


#: The record migrations, read from the fold's own registry (never restated).
RECORD_MIGRATION_FILES = [REPO_ROOT / "supabase" / "migrations" / name
                          for name in record_projection.RECORD_MIGRATIONS]

#: The minimum Supabase-shaped substrate the record migrations need: the three
#: roles they GRANT to, and the two control-plane tables their tenancy FKs
#: point at (`public.organizations`, `public.graphs`). Deliberately minimal —
#: the record is the subject here, not the control plane.
_PREAMBLE = """
DO $$ BEGIN
  CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE service_role NOLOGIN BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
    id text PRIMARY KEY,
    name text NOT NULL,
    graph_name text
);
CREATE TABLE IF NOT EXISTS public.graphs (
    id text PRIMARY KEY,
    org_id text NOT NULL REFERENCES public.organizations(id),
    name text NOT NULL,
    namespace text,
    UNIQUE (org_id, id)
);
"""


@pytest.fixture(scope="session")
def wiring():
    """The wiring gate, loaded by path (it is a tool, not an installed module).

    Session-scoped: `check()` imports the SDK (the fold's registry lives behind
    `tortoise/__init__.py`) and the tool memoises it, so the import is paid once.
    """
    path = REPO_ROOT / "tools" / "record_wiring_check.py"
    spec = importlib.util.spec_from_file_location("record_wiring_check", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    code, report = module.check()
    return module, code, report


@pytest.fixture(autouse=True)
def _no_ambient_record_env(monkeypatch):
    """Hermetic against the record's OWN arming surfaces (#9231 review P2).

    A developer lane configured exactly the way this PR's single-tenant
    instructions say — ``TORTOISE_RECORD_DSN`` + the ``_ORG_ID``/``_GRAPH_ID``
    pair — would otherwise arm and attribute every capture here, so the
    ``no_tenant`` refusal test would connect, count ``write_failed`` and fail
    with a message ("the tenant was guessed") that misdiagnoses an
    env-configured machine as a code defect. The sibling
    ``tests/test_record_writer_seam.py`` clears the DSN for the same reason;
    this clears all FOUR arming keys (the pair, the DSN, and — since #9192 — the
    ``TORTOISE_RECORD_SINGLE_TENANT`` declaration that is what makes the pair
    reachable), because this PR is what introduced the pair.
    """
    for name in (record_writer.RECORD_DSN_ENV, record_writer.RECORD_ORG_ENV,
                 record_writer.RECORD_GRAPH_ENV,
                 record_writer.RECORD_SINGLE_TENANT_ENV):
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture()
def capture_sdk(tmp_path):
    """A real SDK with its own graph and journal. No record DSN is set here."""
    def _make(*, journal: bool = True):
        path = str(tmp_path / "journal.jsonl")
        sdk = TortoiseSDK(
            str(tmp_path / "graph.db"),
            event_log_path=path if journal else None,
        )
        return sdk, Path(path)
    return _make


@pytest.fixture()
def no_record_dsn(monkeypatch):
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    return record_writer.RECORD_DSN_ENV


# ════════════════════════════════════════════════════════════════════════════
# 1. The call site — static, DB-free, and the one that would have caught #9078
# ════════════════════════════════════════════════════════════════════════════


def test_the_write_half_has_a_production_caller(wiring):
    """`tools/record_wiring_check.py` exits 0 only on the real tree.

    Before this change it exits 1 with "no module inside tortoise/ imports AND
    calls the record's write half" — the defect #9078 measured.
    """
    _, code, report = wiring
    assert code == 0, report
    assert report["verdict"] == "WIRED"
    modules = [caller["module"] for caller in report["production_callers"]]
    assert "tortoise/sdk.py" in modules, modules


def test_only_the_shipped_package_can_satisfy_the_wiring_gate(wiring):
    """The gate's caller set is EXACTLY the three shipped doors — no probe can join.

    Production is the installed package, not a path a probe could join, so the
    claim is that the caller list is the expected door set and nothing else.
    Asserting `module.startswith("tortoise/")` alone is true by construction:
    `find_production_callers` builds every path from `PACKAGE = REPO_ROOT/
    "tortoise"`, so it could never fail and proved nothing (#9231 review P2).
    """
    _, _, report = wiring
    modules = {caller["module"] for caller in report["production_callers"]}
    assert modules == {"tortoise/api.py", "tortoise/mining.py", "tortoise/sdk.py"}, (
        "the door set moved — a door was added or removed without this gate "
        "learning the new place: " + repr(sorted(modules))
    )


def test_every_layer_has_a_writer_in_the_one_registry(wiring):
    """The four layers are derived from RECORD_MUTATIONS, never a second list."""
    check, _, report = wiring
    writers = report["layer_writers"]
    for layer, tables in check.LAYERS.items():
        assert any(writers.get(table) for table in tables), (
            f"{layer} has no writer among {tables}: {sorted(writers)}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 1b. The gate's own guards: the position rule must FIRE, and its one exemption
#     must be pinned (a green gate that cannot fail is the defect it guards)
# ════════════════════════════════════════════════════════════════════════════

#: Every ordinary way to write a FOURTH door — each omitting `position`, whose
#: signature default is `True`, which is the invented `journal_event.seq`
#: (#9190). The earlier rule matched only `record_writer.write_records(…)`, so
#: four of these six were invisible to it (#9231 review P2).
_DOOR_SPELLINGS = {
    "attribute": (
        'from . import record_writer\n'
        'def f(rec):\n'
        '    record_writer.write_records([rec], org_id="o", graph_id="g")\n'),
    "from-import": (
        'from .record_writer import write_records\n'
        'def f(rec):\n'
        '    write_records([rec], org_id="o", graph_id="g")\n'),
    "module-alias": (
        'from . import record_writer as rw\n'
        'def f(rec):\n'
        '    rw.write_records([rec], org_id="o", graph_id="g")\n'),
    "dotted": (
        'import tortoise.record_writer\n'
        'def f(rec):\n'
        '    tortoise.record_writer.write_records([rec], org_id="o", graph_id="g")\n'),
    "writer-object": (
        'from . import record_writer\n'
        'def f(rec):\n'
        '    record_writer.RecordWriter().write([rec], org_id="o", graph_id="g")\n'),
    "replay": (
        'from . import record_projection\n'
        'def f(rec, conn):\n'
        '    record_projection.replay([rec], conn, org_id="o", graph_id="g")\n'),
    "replay-plan": (
        'from . import record_projection\n'
        'def f(rec):\n'
        '    record_projection.replay_plan([rec], org_id="o", graph_id="g")\n'),
}


@pytest.mark.parametrize("spelling", sorted(_DOOR_SPELLINGS))
def test_the_position_rule_fires_on_every_spelling_of_a_new_door(wiring, spelling):
    """The rule must FIRE on a door that does not exist yet (#9231 review P2).

    `position_problems == []` on the real tree only proves the three existing
    doors COMPLY — a rule mutated to `return []` stayed green, because nothing
    ever fed it a violating call. Each spelling below is a real way to write a
    fourth door, and each omits `position`: the invented `journal_event.seq`
    that collides with a later replay's numbering on `(org_id, graph_id, seq)`
    in silence. `replay` is the function the parameter actually lives on, so a
    door spelled that way used to escape BOTH claims of the gate.
    """
    check, _, _ = wiring
    rel = "tortoise/a_new_door.py"
    tree = ast.parse(_DOOR_SPELLINGS[spelling], filename=rel)
    problems = check.position_problems_for_module(tree, rel)
    assert problems, f"{spelling}: the position rule did not fire"
    assert "position=False" in problems[0], problems[0]

    compliant = _DOOR_SPELLINGS[spelling].replace(
        'graph_id="g"', 'graph_id="g", position=False')
    assert check.position_problems_for_module(
        ast.parse(compliant, filename=rel), rel) == [], (
        f"{spelling}: a compliant call must not be reported")

    if spelling != "replay-plan":
        # Claim 1 must SEE the same call: a spelling invisible to both checks is
        # how a fourth door writes rows while the gate reports WIRED.
        assert check._record_calls(tree), (
            f"{spelling}: claim 1 is blind to this spelling too")


def test_the_declared_gap_is_pinned_and_a_stale_one_fails_the_gate(wiring,
                                                                   monkeypatch):
    """`DECLARED_GAPS` is the ONE lever that can turn this gate from exit 1 to 0.

    Exempting a genuinely missing writer (simulated: `point`) removed the only
    failure the epistemic layer produced, and the tool then printed `WIRED`
    with every test green (#9231 review P2). So the exempted set is pinned
    exactly, each entry must name its tracker, and a STALE exemption — a table
    that has since gained a writer — is a gate FAILURE rather than a permanent
    licence.
    """
    check, _, report = wiring
    assert set(check.DECLARED_GAPS) == {"journal_event"}, sorted(check.DECLARED_GAPS)
    assert "#9190" in check.DECLARED_GAPS["journal_event"]
    assert [gap["table"] for gap in report["declared_gaps"]] == ["journal_event"]

    stale = dict(check.DECLARED_GAPS)
    stale["point"] = "a hurried exemption that would hide the epistemic layer"
    monkeypatch.setattr(check, "DECLARED_GAPS", stale)
    code, stale_report = check.check()
    assert code == 1, f"a stale exemption was accepted as {stale_report['verdict']}"
    assert any("STALE" in gap for gap in stale_report["gaps"]), stale_report["gaps"]


# ════════════════════════════════════════════════════════════════════════════
# 1c. The schema the writer PARSES must exist where the writer is INSTALLED
# ════════════════════════════════════════════════════════════════════════════


def test_the_record_schema_ships_with_the_wheel():
    """A wheel has no `supabase/` tree, so the migrations must ship IN the package.

    `record_projection.table_columns()` READS the schema rather than restating
    the columns (a second declaration is the one that goes stale), and a wheel
    ships no `supabase/`. Measured consequence before this: in a wheel install
    `table_columns()` raised before the first statement, so EVERY armed live
    write failed `write_failed` — the record stayed empty while this PR's own
    static gate certified it green (#9231 review P2).

    Verified against the real build too: `uv build --wheel` then
    `zipfile.namelist()` contains the seven
    `tortoise/record_migrations/*_record_*.sql` files.

    The MANIFEST line is the SAME trap one level down, and it was measured
    missing here (#9231 review P2): the discovery stub is owned by no package
    (`package-dir` remaps the package's sources to `supabase/migrations`), so an
    sdist built without `include tortoise/record_migrations/__init__.py` carries
    the 7 SQL files and NOT the stub — and a wheel rebuilt from that sdist finds
    no `tortoise.record_migrations` package and ships ZERO schema. That is the
    `tortoise/packs` G1 silent-empty class, on the path whose only consumer is
    the writer itself. Mutation check: deleting the MANIFEST line fails here.
    """
    with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
        conf = tomllib.load(fh)["tool"]["setuptools"]
    assert conf["package-dir"]["tortoise.record_migrations"] == "supabase/migrations"
    patterns = conf["package-data"]["tortoise.record_migrations"]
    for name in record_projection.RECORD_MIGRATIONS:
        assert any(fnmatch.fnmatch(name, pattern) for pattern in patterns), (
            f"{name} is not matched by {patterns} — a wheel install would not "
            "ship the schema its own writer parses")
    assert (REPO_ROOT / "tortoise" / "record_migrations" / "__init__.py").exists(), (
        "the discovery stub is what makes `packages.find` see the mapped package")
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "include tortoise/record_migrations/__init__.py" in manifest, (
        "without the MANIFEST include the sdist drops the discovery stub, and a "
        "wheel rebuilt from that sdist ships NO record schema at all")


def test_the_schema_resolves_without_the_repo_checkout(monkeypatch, tmp_path):
    """`table_columns()` must work where `supabase/` does not exist (`site-packages`).

    Before this, `_migrations_dir()` was `REPO_ROOT/supabase/migrations` and
    nothing else, so the wheel's copy was never read even when present.
    """
    repo_dir = REPO_ROOT / "supabase" / "migrations"
    site = tmp_path / "site-packages"
    packaged = site / "tortoise" / "record_migrations"
    packaged.mkdir(parents=True)
    for name in record_projection.RECORD_MIGRATIONS:
        shutil.copy(repo_dir / name, packaged / name)

    monkeypatch.setattr(record_projection, "REPO_ROOT", site)
    monkeypatch.setattr(record_projection, "PACKAGED_MIGRATIONS", packaged)
    assert record_projection._migrations_dir() == packaged
    columns = record_projection.table_columns()
    assert "point" in columns and "props" in columns["point"], sorted(columns)

    # The CHECKOUT still wins where it is complete: a migration edit must be
    # read as edited, never as the copy an install happens to carry.
    monkeypatch.setattr(record_projection, "REPO_ROOT", REPO_ROOT)
    assert record_projection._migrations_dir() == repo_dir


# ════════════════════════════════════════════════════════════════════════════
# 2. Unarmed is byte-identical — the deployment that has not armed the record
# ════════════════════════════════════════════════════════════════════════════


def test_an_unarmed_capture_opens_no_record_connection(capture_sdk, no_record_dsn,
                                                       monkeypatch):
    """No DSN ⇒ no connection, no plan, no exception: §2.0's sequence."""
    def _must_not_connect(dsn):                      # pragma: no cover - asserted
        raise AssertionError(f"the record is unarmed but connected to {dsn!r}")

    monkeypatch.setattr(record_writer, "_default_connect", _must_not_connect)
    sdk, _journal = capture_sdk()
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    # A DELTA, not process-global equality (#9231 review P2): the counter is a
    # module-level Prometheus Counter, so `== {}` would only hold while no other
    # file in the suite arms the record — an accident, not the claim.
    after = dict(monitoring.record_write_failure_counts())
    assert after == before, (before, after)


# ════════════════════════════════════════════════════════════════════════════
# 3. Armed but unattributed — refused, counted, never guessed (#9192)
# ════════════════════════════════════════════════════════════════════════════


def test_an_armed_capture_without_a_tenant_is_refused_and_counted(
        capture_sdk, monkeypatch):
    """Armed with no tenant ⇒ REFUSED (counted `no_tenant`), never filed blind.

    The DSN is syntactically valid and deliberately unreachable: a refusal that
    had gone looking for a tenant's rows would first TRY to connect, so "no
    connection attempted" is what proves the refusal happened before the guess.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")

    def _must_not_connect(dsn):                      # pragma: no cover - asserted
        raise AssertionError(
            "an armed write with no tenant in scope connected to the record — "
            "the tenant was guessed"
        )

    monkeypatch.setattr(record_writer, "_default_connect", _must_not_connect)
    sdk, _journal = capture_sdk()
    # A DELTA, not a process-global `>=` (#9231 review P2): the counter is a
    # module-level Prometheus Counter, so `>= 1` would pass on some OTHER
    # test's refusal — an accident, not this claim.
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_tenant", 0) > before.get("no_tenant", 0), (before, after)
    assert after.get("write_failed", 0) == before.get("write_failed", 0), after


def test_the_single_tenant_environment_pair_resolves_a_tenant(capture_sdk,
                                                              monkeypatch):
    """The single-tenant arming surface (#9078 scope decision 3, #9192).

    Without it, arming the record needs a code change at every call site — which
    is how a capability ends up with no production caller again. A request scope
    always wins over the pair: env is process-global, so it is the same single
    value for every tenant the process serves (#9192).

    ⛔ AND the pair only counts on a deployment that DECLARED it serves one
    graph: undeclared (the default) the pair is ignored, so a multi-tenant
    process cannot arm the record by setting two environment variables. The
    undeclared leg is pinned first, and by its own test too
    (``tests/test_9192_tenant_armed_by_request.py``) — a default that silently
    flipped would make this one pass while the hazard came back.
    """
    monkeypatch.delenv(record_writer.RECORD_ORG_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_GRAPH_ENV, raising=False)
    monkeypatch.delenv(record_writer.RECORD_SINGLE_TENANT_ENV, raising=False)
    assert record_writer.record_tenant() is None
    monkeypatch.setenv(record_writer.RECORD_ORG_ENV, "org-from-env")
    assert record_writer.record_tenant() is None, "a HALF pair must not be a tenant"
    monkeypatch.setenv(record_writer.RECORD_GRAPH_ENV, "graph-from-env")
    assert record_writer.record_tenant() is None, (
        "a process that has not declared itself single-tenant must not be "
        "armable by a process-global env pair")
    monkeypatch.setenv(record_writer.RECORD_SINGLE_TENANT_ENV, "1")
    assert record_writer.record_tenant() == ("org-from-env", "graph-from-env")
    with use_record_tenant("org-scoped", "graph-scoped"):
        assert record_writer.record_tenant() == ("org-scoped", "graph-scoped")
    assert record_writer.record_tenant() == ("org-from-env", "graph-from-env")
    # ⛔ `current_record_tenant()` is the SCOPE only — its `None` is NOT the
    # refusal an armed write reads (#9231 review P2). It returned None while
    # `record_tenant()` resolved the env pair, so the docstring that called its
    # None "a REFUSAL for an armed write" was false: a caller asking it "would
    # my armed write be refused?" got the wrong answer while the write
    # proceeded under the env tenant. `record_tenant()` is the one whose None
    # means refusal.
    assert record_writer.current_record_tenant() is None
    assert record_writer.record_tenant() is not None


def test_env_example_declares_the_record_tenant_pair():
    """``.env.example`` IS this repo's env contract (``AGENTS.md``).

    #9231 review P2: the record block documents the DSN across 28 lines and
    named NOTHING about the tenant pair — the surface that decides whether an
    armed write is filed or refused. `git grep` for the pair over `.env.example`,
    `.github/`, `docs/` and `config/` returned nothing, so the only prose
    describing it was the `no_tenant` warning an operator reads AFTER the
    refusal. The PR's own argument for the pair is discoverability ("without it,
    arming the record would require a code change at every call site"), which
    the DSN-only block defeated. Same pin shape as
    ``test_bm25_lexical_8488.py::test_env_example_declares_the_bm25_arm_switch``.

    #9192: the block must also carry the DECLARATION that makes the pair a
    valid answer. Documenting only the pair is actively wrong here now — an
    operator who copies it onto a multi-tenant process would expect rows and
    get refusals, with the explanation living only in a warning they have not
    read yet.
    """
    env = (REPO_ROOT / ".env.example").read_text()
    for name in (record_writer.RECORD_ORG_ENV, record_writer.RECORD_GRAPH_ENV,
                 record_writer.RECORD_SINGLE_TENANT_ENV):
        assert name in env, (
            f"{name} (the #9078 single-tenant arming surface) decides whether an "
            "armed record write is filed or refused, and is not declared in "
            ".env.example")
        declared = [line for line in env.splitlines()
                    if line.strip() in (f"# {name}=", f"{name}=")
                    or line.strip().startswith((f"# {name}=", f"{name}="))]
        assert declared, (
            f"{name} must be shown as a commented assignment (the example form "
            "an operator copies), not just named in prose")
        # #9192 review D4: showing a VALUE that resolves to the OPPOSITE of the
        # instruction two lines above it is worse than showing none — `=0`/`=no`
        # pass a startswith check and mean "multi-tenant" to `env_flag`. So the
        # shipped example's value is resolved by the SAME reader the code uses.
        for line in declared:
            value = line.strip().split("=", 1)[1].strip()
            if name == record_writer.RECORD_SINGLE_TENANT_ENV:
                from tortoise.env_truthy import env_flag_from_value
                assert env_flag_from_value(value, False) is True, (
                    f".env.example ships {name}={value!r}, which the code reads "
                    "as NOT single-tenant — the example would declare the "
                    "opposite of the instruction beside it")
            else:
                assert value == "", (
                    f"{name} must be shown empty in .env.example: an example "
                    f"tenant id is a value an operator can ship by accident "
                    f"(read {value!r})")
    # The seam's connect bound is named where the DSN is documented; the block
    # must not go back to claiming the DSN is handed over untouched.
    assert "CONNECT_TIMEOUT_SECONDS" in env, (
        ".env.example must name the seam's own connect bound — the DSN block "
        "otherwise reads as if libpq's wait-forever default still applied")
    assert "sets no\n# timeout of its own" not in env, (
        ".env.example claims the seam sets no timeout of its own; it sets "
        "record_writer.CONNECT_TIMEOUT_SECONDS")


def test_an_armed_capture_without_a_journal_is_counted_not_silent(
        capture_sdk, monkeypatch):
    """A journal-less deployment with an armed record is a NAMED misconfiguration.

    `tortoise/config.py` supports the journal-less lane by design, so an
    operator can arm the record and forget the journal — and then nothing is
    written and NOTHING could heal it later, because the record's sustainable
    source is the journal. Silent would be the #9078 hole wearing a green
    health check.
    """
    monkeypatch.setattr(record_writer, "_default_connect",
                        lambda dsn: pytest.fail("connected with no journal"))
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    sdk, _journal = capture_sdk(journal=False)
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_journal", 0) > before.get("no_journal", 0), after


# ════════════════════════════════════════════════════════════════════════════
# 4. The live path writes layer 1 from the JOURNAL'S OWN position (#9190)
# ════════════════════════════════════════════════════════════════════════════


def test_the_live_path_writes_the_journals_own_position(wiring, tmp_path):
    """Layer 1 is written by production traffic, keyed on the append's ordinal.

    #9190 landed the ordinal (`EventLog.append` returns the record's position;
    `EventLog.position()` exposes the count), which closes the premise this PR
    was branched on: that the live chokepoint had no number to record. So the
    live door must CARRY it (`write_live_record(seq=…)`) and bind
    `position=seq is not None` — a literal `False` (the old deferral) leaves
    `public.journal_event` and the `public.record_event_stream` view over it
    empty on every live write, and a literal `True` invents a number for a log
    that reported none and collides with a later replay's numbering in silence.

    Three surfaces, because each alone is weak: the append really returns a
    position, the seam really threads it into the plan, and the gate refuses a
    door that drops either half.
    """
    from tortoise.log import EventLog

    _, code, report = wiring
    assert report["position_problems"] == [], report["position_problems"]
    assert code == 0, (code, report)
    assert record_writer.POSITION_ORDINAL_SOURCE == "#9190"

    # (a) the journal really reports an ordinal, and it is the replay's seq.
    #     `EventLog.scratch(tmp_path/…)`, never a fixed path: this file asserts
    #     ABSOLUTE positions, so a journal at a world-shared location (the
    #     previous `/tmp/w9078-position.jsonl`) let two concurrent lanes append
    #     into one file and read each other's numbering — and the unlink a peer
    #     lane performed mid-test deleted the file under this one. `scratch()`
    #     is also this file's own convention for a throwaway log, and it is what
    #     the detector at `SCRATCH_JOURNAL_ROOTS` would demand of `tortoise/`.
    log = EventLog.scratch(tmp_path / "position.jsonl")
    first = log.append({"type": "SourceCreated", "id": "s1"})
    second = log.append({"type": "SourceCreated", "id": "s2"})
    assert (first, second) == (1, 2), (first, second)
    assert log.position() == 2, log.position()

    # (b) the seam's `seq` is the row's key: the SAME record gets the number the
    # append gave it, and omitting the ordinal writes no row at all
    record = {"type": "PointAdded", "ts": "2026-10-19T00:00:00Z", "id": "p1",
              "point": {"id": "p1", "content": "a claim", "pointKind": "claim"}}
    plan = record_projection.replay_plan([record], org_id="o", graph_id="g",
                                        start_seq=7)
    position_rows = [s for s in plan if "journal_event" in s.sql]
    assert len(position_rows) == 1, position_rows
    assert 7 in position_rows[0].params, position_rows[0].params
    without = record_projection.replay_plan(
        [record], org_id="o", graph_id="g", position=False)
    assert not [s for s in without if "journal_event" in s.sql]
    # The layer rows are unaffected — the position row is bookkeeping ABOUT them.
    assert [s for s in without if "journal_event" not in s.sql] == [
        s for s in plan if "journal_event" not in s.sql
    ]


def test_the_live_door_rule_fires_when_the_ordinal_is_dropped(wiring):
    """The gate reds a live door that defers, invents, or drops the ordinal.

    Driven through the REAL rule on a SUPPLIED source, because that is the only
    shape that proves it fires: the rule's body reduced to `return []` (or
    either `problems.append` block deleted) leaves every assertion about the
    COMPLYING tree green, so a leg that merely asserts `[]` there — or that
    re-derives the condition from `_is_position_from_ordinal` /
    `_references_ordinal` — certifies nothing about the gate (#9307 review F1).
    The sibling `test_the_position_rule_fires_on_every_spelling_of_a_new_door`
    drives `position_problems_for_module` for the same reason.
    """
    module, _, _ = wiring
    source = (
        "from tortoise import record_writer\n"
        "def write_live_record(record, *, durable=False, seq=None):\n"
        "    return record_writer.write_records([record], org_id='o', "
        "graph_id='g', %s)\n"
    )
    good = "position=seq is not None, first_seq=seq if seq is not None else 1"
    # the real tree complies, through the same code path
    assert module.live_door_writes_the_journals_position() == []
    assert module.live_door_writes_the_journals_position(
        _live_door_source()) == [], (
        "the injected seam and the real-tree default disagree — the rule must "
        "read the same door both ways")
    problems = module.live_door_writes_the_journals_position(source % good)
    assert problems == [], (good, problems)

    for spelling, why, names in (
        ("position=False, first_seq=seq", "the pre-#9190 deferral", "position="),
        ("position=True, first_seq=seq", "an invented number", "position="),
        ("position=seq is not None, first_seq=1", "a dropped ordinal",
         "first_seq="),
        ("position=seq is not None", "a missing ordinal", "first_seq="),
        # mentions the ordinal without USING it: `any(Name(id='seq'))` accepted
        # this, and it keys EVERY position row at seq 1 — one row for a whole
        # journal under `ON CONFLICT DO NOTHING`
        ("position=seq is not None, first_seq=1 if seq is not None else 1",
         "an expression that only mentions the ordinal", "first_seq="),
    ):
        fired = module.live_door_writes_the_journals_position(source % spelling)
        assert fired, f"{why}: the rule accepted {spelling!r}"
        assert names in fired[0], (
            f"{why}: the rule fired but blamed the wrong half — expected "
            f"{names!r} in {fired[0]!r}")


def _live_door_source() -> str:
    """The real live-door module's text, for the injected-seam equivalence leg."""
    return (REPO_ROOT / "tortoise" / "record_writer.py").read_text(encoding="utf-8")


#: Ways a `write_live_record(…)` call can carry — or fail to carry — the
#: ordinal. Every entry but `honest` must be reported (#9307 review F2).
_CALL_SITE_SPELLINGS = {
    "dropped": (
        'from . import record_writer\n'
        'def _emit_event(self, record, log):\n'
        '    seq = log.append(record)\n'
        '    record_writer.write_live_record(record, durable=True)\n'),
    "none": (
        'from . import record_writer\n'
        'def _emit_event(self, record, log):\n'
        '    seq = log.append(record)\n'
        '    record_writer.write_live_record(record, durable=True, seq=None)\n'),
    "literal": (
        'from . import record_writer\n'
        'def _emit_event(self, record, log):\n'
        '    seq = 1\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    "optional-parameter": (
        'from . import record_writer\n'
        'def _write_record_projection(self, record, seq=None):\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    "unreadable": (
        'from . import record_writer\n'
        'def _emit_event(self, record, seq):\n'
        '    def inner():\n'
        '        record_writer.write_live_record(record, seq=seq)\n'
        '    return inner\n'),
    "honest": (
        'from . import record_writer\n'
        'def _emit_event(self, record, log):\n'
        '    seq = log.append(record)\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    "honest-forward": (
        'from . import record_writer\n'
        'def _write_record_projection(self, record, seq):\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    # The append's RECEIVER has to be the journal (#9307 review I1). A list's
    # `append` also matches the name, and returns `None` at runtime — which this
    # door turns into `position=False`, i.e. layer 1 silently empty, with no
    # error and no counter. Accepted by the rule before the receiver is checked.
    "list-append": (
        'from . import record_writer\n'
        'def _emit_event(self, record):\n'
        '    seq = self._pending.append(record)\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    # ... and the ASSIGNMENT scan has to stay in the door's own scope. A nested
    # helper that happens to append must not make an outer COMPUTED `seq` look
    # like the journal's ordinal (#9307 review I1).
    "computed-nested-append": (
        'from . import record_writer\n'
        'def _emit_event(self, record):\n'
        '    seq = self._counter + 1\n'
        '    def inner():\n'
        '        seq = log.append(record)\n'
        '        return seq\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    # The three receiver spellings the SHIPPED doors actually use
    # (`log` in sdk.py, `self.log` in api.py, `api.log` in mining.py), so the
    # receiver allow-list cannot tighten into a false red on the real tree.
    "honest-self-log": (
        'from . import record_writer\n'
        'def _emit_event(self, record):\n'
        '    seq = self.log.append(record)\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
    "honest-api-log": (
        'from . import record_writer\n'
        'def _emit_event(self, api, record):\n'
        '    seq = api.log.append(record)\n'
        '    record_writer.write_live_record(record, durable=True, seq=seq)\n'),
}


@pytest.mark.parametrize("spelling", sorted(_CALL_SITE_SPELLINGS))
def test_the_ordinal_argument_rule_fires_on_every_way_a_door_drops_it(
        wiring, spelling):
    """`seq` defaults to None, so a call site that omits it is SILENT (#9190).

    `write_live_record(record)` is valid Python: it takes `position=False`, writes
    no position row, and leaves layer 1 empty on a live write — the defect this
    change closes, with `record_wiring_check` still printing WIRED, because the
    body-only rule above only certifies that the door USES what it is given
    (#9307 review F2). Every spelling here is a way to lose it; the `honest`
    ones prove the rule is not simply refusing all call sites — including the
    three receiver spellings the shipped doors use.

    Two of them are fail-OPENS the rule admitted until it checked the append's
    receiver and the assignment's scope (#9307 review I1): a list `append`
    (returns `None`) and an outer computed `seq` shadowed by a nested helper's
    append. Both certified WIRED over an empty layer 1.
    """
    module, _, _ = wiring
    rel = "tortoise/a_new_door.py"
    tree = ast.parse(_CALL_SITE_SPELLINGS[spelling], filename=rel)
    problems = module.ordinal_argument_problems(tree, rel)
    if spelling.startswith("honest"):
        assert problems == [], (spelling, problems)
        return
    assert problems, f"{spelling}: the ordinal-argument rule did not fire"
    assert "write_live_record" in problems[0], problems[0]


def test_every_real_door_still_passes_the_ordinal(wiring):
    """The three shipped doors, measured — the revert the rule exists to catch.

    Each door's real source is mutated to drop `seq=`, and the rule must fire for
    that exact file. This is the non-vacuity half of the leg above: it proves the
    rule sees the call sites that actually ship, not only a synthetic module.
    """
    module, _, report = wiring
    assert report["position_problems"] == [], report["position_problems"]
    for rel in ("tortoise/sdk.py", "tortoise/api.py", "tortoise/mining.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel)
        assert module.ordinal_argument_problems(tree, rel) == [], (
            f"{rel}: the shipped door is reported — the rule is wrong, not the door")
        # Locate the SEAM call itself (a module may pass `seq=seq` onward before
        # it reaches the seam, e.g. the SDK's `_write_record_projection` hop),
        # then drop the keyword from that call's OWN arguments. The call spans
        # lines once the door passes more than `durable`/`seq` (#9188's tenant
        # carrier is the first third argument), so the mutation works on the
        # call's source SEGMENT, not on its last line: a single-line mutation
        # would silently stop mutating anything the day a keyword was wrapped.
        call = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and module._call_name(node) == "write_live_record")
        lines = source.splitlines(keepends=True)
        first, last = call.lineno - 1, call.end_lineno - 1
        segment = "".join(lines[first:last + 1])
        assert "seq=seq" in segment, (
            f"{rel} no longer spells the ordinal the way this leg mutates it — "
            f"update the mutation, do not delete the leg: {segment!r}")
        # `seq=seq` may be the LAST argument (`seq=seq)`) or followed by another
        # keyword across the wrap (`seq=seq,\n    tenant=...)`), so drop the
        # keyword together with the separator it leaves behind — either side.
        if re.search(r"seq=seq,\s*", segment):
            mutated = re.sub(r"seq=seq,\s*", "", segment, count=1)
        else:
            mutated = re.sub(r",\s*seq=seq", "", segment, count=1)
        dropped = "".join(lines[:first]) + mutated + "".join(lines[last + 1:])
        assert dropped != source, rel
        fired = module.ordinal_argument_problems(
            ast.parse(dropped, filename=rel), rel)
        assert fired and any(f"{rel}:" in problem for problem in fired), (rel, fired)


def test_the_sdk_forwarding_hop_requires_the_ordinal(wiring):
    """The SDK's inner hop must not default `seq` to None (#9307 review F2).

    `_emit_event` hands the ordinal to `_write_record_projection`, which hands it
    to the seam — three links, one of them able to swallow it. With a default, a
    caller that forgets it takes `position=False` on a live write with no error
    and no count; required, the omission is a `TypeError` at the call site. Both
    halves are pinned: the signature, and the gate rule that made it required.
    """
    import inspect

    module, _, _ = wiring
    parameter = inspect.signature(
        TortoiseSDK._write_record_projection).parameters["seq"]
    assert parameter.default is inspect.Parameter.empty, (
        "`_write_record_projection(seq=...)` regained a default — a caller that "
        "omits it then writes no position row, silently (#9190)")
    source = (REPO_ROOT / "tortoise" / "sdk.py").read_text(encoding="utf-8")
    with_default = source.replace("seq: int | None) -> None:",
                                 "seq: int | None = None) -> None:", 1)
    assert with_default != source, "the signature spelling changed — re-point this leg"
    fired = module.ordinal_argument_problems(
        ast.parse(with_default, filename="tortoise/sdk.py"), "tortoise/sdk.py")
    assert fired and "WITH A DEFAULT" in fired[0], fired


# ════════════════════════════════════════════════════════════════════════════
# 5. Only a JOURNAL feeds the record — a scratch buffer must never file a row
# ════════════════════════════════════════════════════════════════════════════


def test_the_seam_writes_nothing_from_a_scratch_buffer(monkeypatch):
    """`durable=False` ⇒ no connection, no row, one named DECLINE (#9231 P1/P2).

    The record is `replay(journal)`: a row whose record the journal never
    carried can be neither reproduced nor healed, so the seam must refuse it
    BEFORE opening a connection — that is what `_default_connect` raising
    proves. The count is the signal that a lane filed nothing — on the SKIP
    counter, not on `record_write_failures`, because a lane with no journal by
    design lost nothing a replay could restore (#9231 review P2).
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "a non-durable record opened a record connection"))
    before = dict(monitoring.record_non_journal_skip_counts())
    failures_before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record(
            {"type": "PointAdded", "id": "p1"}, durable=False) is None
    after = dict(monitoring.record_non_journal_skip_counts())
    failures_after = dict(monitoring.record_write_failure_counts())
    assert after.get("no_durable_journal", 0) > before.get(
        "no_durable_journal", 0), after
    assert failures_after == failures_before, (
        "a DECLINE on a no-journal-by-design lane was counted as a FAILURE — "
        f"`record_write_failures` must stay zero-meaningful: {failures_after}")


def test_the_sdk_door_reads_durability_off_its_log(capture_sdk, monkeypatch):
    """A door READS `durable`; it must not assume every `EventLog` is a journal.

    `EventLog` now declares `durable = True` explicitly rather than relying on
    the doors' `getattr(log, "durable", True)` fallback, so a log constructed
    onto a SCRATCH path can say so — which is exactly what the MCP mining
    lane's temp-file journal does (`mcp_server`, `tempfile.mkdtemp`). Before
    this, `sdk.py` and `mining.py` passed no `durable` at all, so such a buffer
    filed rows whose journal was a directory nothing references, and no replay
    could ever heal them (#9231 review P1).
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "the SDK door filed a record from a log that declared itself a "
        "scratch buffer"))
    sdk, _journal = capture_sdk()
    log = sdk._get_event_log()
    assert log is not None, "the fixture configures a journal"
    assert log.durable is True, "a real journal is durable by default"
    log.durable = False
    before = dict(monitoring.record_non_journal_skip_counts())
    sdk.capture_session([{"role": "user", "content": "We chose FalkorDB."}])
    after = dict(monitoring.record_non_journal_skip_counts())
    assert after.get("no_durable_journal", 0) > before.get(
        "no_durable_journal", 0), after


def test_the_m2_capture_lane_never_files_a_row_its_journal_never_carried(        capture_sdk, monkeypatch):
    """The M2 extraction lane appends to a SCRATCH buffer, so it never writes.

    `TortoiseSDK._extract_session_llm` builds `EventAPI(_InMemoryEventLog())`
    ("No file I/O, no temp files", by design) and the extractor emits through
    `EventAPI._emit`. Those records are journaled NOWHERE, so with the record
    armed they used to land as rows no replay could ever correct — and the
    in-capture dedup even DELETEs a repeated Point from the graph, leaving the
    row asserting a Point the graph does not hold. Pre-fix this test fails on
    the connect assertion AND on the missing `no_durable_journal` count.

    `TORTOISE_SESSION_LLM_MOCK=1` is the existing, keyless test seam for this
    lane, so the REAL `_extract_session_llm` runs (no stub of its own body).
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "the M2 capture lane opened a record connection for a record its "
        "journal never carried"))
    sdk, _journal = capture_sdk()
    failures_before = dict(monitoring.record_write_failure_counts())
    skips_before = dict(monitoring.record_non_journal_skip_counts())
    with use_record_tenant(ORG, GRAPH):
        extracted, meta = sdk._extract_session_llm(
            [{"role": "user", "content": "We chose FalkorDB for the hot cache."},
             {"role": "assistant",
              "content": "Noted: FalkorDB holds the hot cache."}],
            "sess-m2-scratch", "2026-10-19T00:00:00+00:00")
    assert extracted, "the M2 lane must really have emitted"
    assert meta.get("mode") != "empty", meta
    skips_after = dict(monitoring.record_non_journal_skip_counts())
    failures_after = dict(monitoring.record_write_failure_counts())
    assert skips_after.get("no_durable_journal", 0) > skips_before.get(
        "no_durable_journal", 0), skips_after
    # THE (#9231 review P2) PIN: a healthy armed capture — the SDK's headline
    # API, emitted THROUGH this by-design non-journal lane — must not move the
    # FAILURE counter. Pre-fix it moved once per extracted record, so
    # `record_write_failures` could never be zero on a working armed deployment
    # and the one channel the seam adds taught operators to ignore it.
    assert failures_after == failures_before, (
        "a by-design non-journal lane counted a LOSS: the failure counter no "
        f"longer reads zero on the healthy capture path — {failures_after}")


def test_a_real_journal_is_still_durable_for_the_door(monkeypatch):
    """The gate is the log's own answer: an `EventLog` keeps writing.

    Stated so the non-durable fix cannot be read as "the API door stopped
    writing": a log with no `durable` marker (every duck-typed double, and the
    SDK's `EventLog`) is treated as a journal.
    """
    from tortoise.api import EventAPI
    from tortoise.sdk import _InMemoryEventLog

    class _Log:
        """A duck-typed log with no `durable` marker (the real `EventLog`
        shape as the door sees it: the attribute is absent, not True)."""

        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)

    assert _InMemoryEventLog.durable is False
    assert getattr(_Log(), "durable", True) is True
    seen = []
    monkeypatch.setattr(record_writer, "write_live_record",
                        lambda record, **kw: seen.append(kw.get("durable")))
    api = EventAPI(_Log(), initiated_by="extractor")
    api._emit("SourceCreated", id="s1")
    assert seen == [True], seen
    assert len(api.log.events) == 1, "the append still happened"


def test_the_demo_cli_lane_files_nothing_from_a_journal_it_deletes(monkeypatch):
    """`tortoise demo` writes a temp journal and then UNLINKS it — scratch.

    `_cmd_demo` is a shipped CLI lane, and its `EventLog` points at a
    `NamedTemporaryFile` the command deletes on the next line, so a record row
    filed from it is a row no replay can ever reproduce or heal (`record !=
    replay(journal)`). It was the second scratch-journal construction site
    (the MCP mining lane's `mkdtemp()` journal is the other), and it was the
    one still answering `durable=True` because the marker lives on the LOG, not
    on the class of log the door happens to see (#9231 review P2). The writes
    happen through `EventAPI._emit`, so pre-fix this test fires the connect
    tripwire instead of counting.
    """
    from tortoise import __main__ as cli

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "the demo lane filed a record from the journal file it deletes"))
    before = dict(monitoring.record_non_journal_skip_counts())
    with use_record_tenant(ORG, GRAPH):
        cli._cmd_demo(None)
    after = dict(monitoring.record_non_journal_skip_counts())
    assert after.get("no_durable_journal", 0) > before.get(
        "no_durable_journal", 0), after


def test_a_scratch_event_log_is_born_non_durable(tmp_path):
    """`EventLog.scratch()` is the ONE home of the marker (#9231 review P2).

    The MCP mining lane was correct and had NO detector: deleting its
    ``log.durable = False`` line reddened nothing, so `EventLog.durable = True`
    silently took the lane back to filing rows from a directory nothing
    references — the P1 that marker exists to prevent. A construction-time
    method gives the decision one tested home instead of an inline assignment a
    refactor can drop.
    """
    from tortoise.log import EventLog

    assert EventLog(tmp_path / "journal.jsonl").durable is True
    scratch = EventLog.scratch(tmp_path / "scratch.jsonl")
    assert scratch.durable is False
    assert scratch.path == tmp_path / "scratch.jsonl"


#: The roots the scratch-journal pin WALKS. Declared here, and pinned by
#: `test_the_scratch_journal_pin_walks_every_declared_root` below, because a
#: detector whose corpus is NARROWER than its claim is how the
#: `graph-scripts/smoke_test.py` site survived: the pin said "every temp-file
#: journal" while walking `tortoise/**` only (#9231 review P2).
SCRATCH_JOURNAL_ROOTS = ("tortoise", "graph-scripts")

#: Spellings that produce a path (or a directory) nothing holds after the run.
_SCRATCH_FACTORY_NAMES = (
    "tempfile", "mkdtemp", "temporarydirectory", "temporaryfile",
)


def _scope_nodes(scope: ast.AST):
    """Every node of ONE scope, without descending into a nested scope.

    The taint below is deliberately per-scope: a name is scratch because THIS
    function bound it from `tempfile`, and a same-named local in another
    function must not inherit that (a false positive would delete a durable
    journal's marker, the opposite error).
    """
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                             ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        out: set[str] = set()
        for element in node.elts:
            out |= _target_names(element)
        return out
    return set()


def _scratch_mentions(node: ast.AST | None) -> bool:
    """Does this expression name a temp-path factory? (`tempfile.mkdtemp(…)`)"""
    if node is None:
        return False
    text = ast.unparse(node).lower()
    return any(factory in text for factory in _SCRATCH_FACTORY_NAMES)


def _references(node: ast.AST | None, names: set[str]) -> bool:
    if node is None or not names:
        return False
    return any(isinstance(sub, ast.Name) and sub.id in names
               for sub in ast.walk(node))


def _scratch_names_in_scope(scope: ast.AST) -> set[str]:
    """Names bound to a temp path in ONE scope, transitively.

    `os.path.join(tmpdir, …)` is the spelling that walked past the old textual
    rule (`"tempfile" in ast.unparse(call)`) even though the directory it joins
    came from `TemporaryDirectory()`: the call names `tmpdir`, not `tempfile`.
    So bindings are propagated to a fixpoint instead of pattern-matched — the
    alias, the `os.path.join`, and the `Path(tmpdir)` wrapper are all the same
    journal.
    """
    tainted: set[str] = set()
    bindings: list[tuple[list[ast.AST], ast.AST | None]] = []
    for node in _scope_nodes(scope):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and _scratch_mentions(
                        item.context_expr):
                    tainted |= _target_names(item.optional_vars)
        elif isinstance(node, ast.Assign):
            bindings.append((list(node.targets), node.value))
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and node.value is not None:
            bindings.append(([node.target], node.value))
    changed = True
    while changed:
        changed = False
        for targets, value in bindings:
            if _scratch_mentions(value) or _references(value, tainted):
                for target in targets:
                    fresh = _target_names(target) - tainted
                    if fresh:
                        tainted |= fresh
                        changed = True
    return tainted


def _scratch_journal_offenders(rel: str, source: str) -> list[str]:
    """Every `EventLog(<temp path>)` construction in ONE module.

    `EventLog.scratch(…)` is an attribute call, so it is never an offender —
    that method IS the remedy.
    """
    offenders: list[str] = []
    tree = ast.parse(source, filename=rel)
    scopes = [tree] + [node for node in ast.walk(tree) if isinstance(
        node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    seen: set[int] = set()
    for scope in scopes:
        tainted = _scratch_names_in_scope(scope)
        for node in _scope_nodes(scope):
            if not isinstance(node, ast.Call) or id(node) in seen:
                continue
            func = node.func
            if getattr(func, "id", None) != "EventLog":
                continue
            if _scratch_mentions(node) or any(
                    _references(arg, tainted) for arg in node.args):
                seen.add(id(node))
                offenders.append(f"{rel}:{node.lineno} {ast.unparse(node)}")
    return offenders


def _scratch_journal_corpus() -> list[Path]:
    """The globbed roots ∩ `git ls-files` — the tracked, reviewable scope.

    Intersecting with the index is what makes the claim checkable: an untracked
    draft under `graph-scripts/` cannot silently widen the corpus, and a
    directory that stops existing reds the reach pin below instead of quietly
    shrinking the pin's reach.
    """
    proc = subprocess.run(
        ["git", "ls-files", "--", *SCRATCH_JOURNAL_ROOTS],
        cwd=REPO_ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, f"git ls-files failed:\n{proc.stderr}"
    tracked = {rel for rel in proc.stdout.splitlines() if rel.endswith(".py")}
    corpus: list[Path] = []
    for root in SCRATCH_JOURNAL_ROOTS:
        corpus.extend(sorted((REPO_ROOT / root).rglob("*.py")))
    return [p for p in corpus
            if p.relative_to(REPO_ROOT).as_posix() in tracked]


def test_the_scratch_journal_pin_walks_every_declared_root():
    """The pin's CORPUS, not just its rule, is asserted (#9231 review P2).

    The rule was right and the corpus was `tortoise/**` only, so a live
    counterexample (`graph-scripts/smoke_test.py`, a `TemporaryDirectory()`
    journal feeding `EventAPI`, whose rows nothing can replay) sat outside the
    claim the test's own message made. Reach is part of the assertion now: each
    declared root must contribute files, and the unsorted package root cannot
    be the only one.
    """
    corpus = _scratch_journal_corpus()
    per_root = {
        root: [p for p in corpus
               if p.relative_to(REPO_ROOT).as_posix().startswith(f"{root}/")]
        for root in SCRATCH_JOURNAL_ROOTS
    }
    empty = [root for root, files in per_root.items() if not files]
    assert empty == [], (
        f"declared scratch-journal root(s) {empty} contributed NO tracked "
        f"files — a corpus that silently shrank reads as CLEAN; "
        f"corpus={sorted(str(p) for p in corpus)[:5]}…")


def test_the_scratch_journal_detector_catches_the_alias_spelling():
    """The rule itself is pinned, positively AND negatively.

    `os.path.join(tmpdir, "events.jsonl")` names `tmpdir`, not `tempfile`, so a
    textual rule reads it as durable. These are the two live spellings (the
    MCP lane's `mkdtemp`, `graph-scripts/smoke_test.py`'s `TemporaryDirectory`)
    plus the negatives that keep the rule from swallowing a durable journal.
    """
    offenders = _scratch_journal_offenders("synthetic.py", '''
import os
import tempfile
from tortoise.log import EventLog


def smoke(tmpdir):
    with tempfile.TemporaryDirectory(prefix="tortoise_smoke_") as tmpdir:
        log = EventLog(os.path.join(tmpdir, "events.jsonl"))
        return log
''')
    assert len(offenders) == 1, offenders
    assert "events.jsonl" in offenders[0], offenders

    assert _scratch_journal_offenders("synthetic.py", '''
import tempfile
from tortoise.log import EventLog

log = EventLog(tempfile.mkdtemp() + "/events.jsonl")
'''), "a direct `mkdtemp()` argument must be red"

    # Negatives: the remedy, a durable path, and a same-named local in another
    # scope (the taint is per-scope, so this must NOT be flagged).
    assert _scratch_journal_offenders("synthetic.py", '''
import tempfile
from pathlib import Path
from tortoise.log import EventLog


def scratch_ok():
    with tempfile.TemporaryDirectory() as tmpdir:
        return EventLog.scratch(Path(tmpdir) / "events.jsonl")


def durable_ok(tmpdir):
    return EventLog("/var/lib/tortoise/events.jsonl")
''') == [], "`scratch()` and a durable path must both stay green"


def test_every_tempfile_backed_event_log_is_built_with_scratch():
    """AST pin: a `tempfile` journal site cannot file rows from a scratch file.

    The behavioural legs above drive the demo lane and the in-memory lane; this
    one catches the NEXT temp-file site, in every root the shipped code and the
    repo's runnable scripts live in (`SCRATCH_JOURNAL_ROOTS`) — the spelling the
    MCP lane had before `scratch()` existed
    (`EventLog(os.path.join(tempfile.mkdtemp(…), …))`) AND the aliased one
    `graph-scripts/smoke_test.py` had (`EventLog(os.path.join(tmpdir, …))`
    inside a `TemporaryDirectory` block, which names no `tempfile` at the call
    and walked past the old textual rule).

    LIMIT, stated so it is not read as more than it is: this is a per-scope
    NAME-TAINT rule, not interprocedural. A temp path handed through a helper
    call (`EventLog(_journal_path(tmpdir))`) is not caught; closing that needs a
    resolver, not a bigger word list.
    """
    offenders: list[str] = []
    for path in _scratch_journal_corpus():
        source = path.read_text(encoding="utf-8")
        if "EventLog(" not in source:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        offenders.extend(_scratch_journal_offenders(rel, source))
    assert offenders == [], (
        "a temp-file journal is a SCRATCH buffer: build it with "
        f"`EventLog.scratch(…)`, not `EventLog(…)` — {offenders}")


def test_an_armed_live_write_commits_and_closes_exactly_once(monkeypatch):
    """The SHIPPED composition had no CI coverage at all (#9231 review P2).

    Both Postgres legs SKIP without `TEST_RECORD_DB_URI`, and every DB-free leg
    asserts a REFUSAL (`no_tenant`, `no_durable_journal`, `malformed_dsn`) — so
    `write_live_record → write_records(position=False) → commit() → close()` was
    only ever exercised by an operator with a database. Deleting the `commit()`
    left CI green and every layer row uncommitted on an armed deployment. This
    drives the real door against a fake seam and counts the promises it makes:
    the record reaches the seam unchanged, the position is deferred, and the
    connection this write opened is committed once and closed once.
    """

    class _Conn:
        commits = 0
        closes = 0

        def commit(self):
            self.commits += 1

        def close(self):
            self.closes += 1

    conn = _Conn()
    seen: dict[str, object] = {}

    def _fake_write_records(records, **kwargs):
        seen["records"] = list(records)
        seen["kwargs"] = kwargs
        return record_writer.RecordWrite(
            outcome=record_writer.RecordWriteOutcome.APPLIED,
            events=len(records), statements=1, first_seq=1, last_seq=1,
            connection=conn)

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "write_records", _fake_write_records)
    record = {"type": "PointAdded", "id": "p1"}
    with use_record_tenant(ORG, GRAPH):
        write = record_writer.write_live_record(record)

    assert write is not None and write.applied
    assert seen["records"] == [record], seen["records"]
    kwargs = seen["kwargs"]
    assert kwargs["position"] is False, "the live door invented a position"
    assert (kwargs["org_id"], kwargs["graph_id"]) == (ORG, GRAPH), kwargs
    assert (conn.commits, conn.closes) == (1, 1), (
        "the live write must commit AND close the connection it opened, once")


def test_the_live_door_threads_the_ordinal_into_the_seam(monkeypatch):
    """The ORDINAL half of the seam, DB-free — the half CI could not see.

    Both Postgres legs SKIP without `TEST_RECORD_DB_URI` and no CI job arms it,
    so a door could satisfy every AST rule in this file and still hand the seam
    `position=False` / `first_seq=1` — the empty-layer-1 defect, on the one path
    the static rules cannot execute (#9307 review advisory). This drives the
    real door against the same fake seam as the leg above: the ordinal the
    journal reported arrives AS the row's number, one call, one number.
    """
    seen: list[dict] = []

    def _fake_write_records(records, **kwargs):
        seen.append(kwargs)
        return record_writer.RecordWrite(
            outcome=record_writer.RecordWriteOutcome.APPLIED,
            events=len(records), statements=1,
            first_seq=kwargs["first_seq"], last_seq=kwargs["first_seq"],
            connection=None)

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "write_records", _fake_write_records)
    record = {"type": "PointAdded", "id": "p1"}
    with use_record_tenant(ORG, GRAPH):
        for seq in (1, 7):
            assert record_writer.write_live_record(
                record, seq=seq) is not None, (seq, seen)

    assert [k["position"] for k in seen] == [True, True], (
        "the door received the journal's ordinal and still told the seam to "
        f"defer the position — layer 1 stays empty on a live write: {seen}")
    assert [k["first_seq"] for k in seen] == [1, 7], (
        "the row's number is not the append's ordinal — a live write and a "
        f"full replay would disagree about the same record's seq: {seen}")


def test_an_unproven_position_is_counted_warned_and_never_claimed(
        monkeypatch, caplog):
    """The `seq is None` branch is a NAMED skip, not the in-memory lane (#9307 F1).

    `EventLog.append` returns ``None`` when it appended the record but could not
    PROVE its position (an unsizable tail, an unusable cross-process lock, an
    uncountable journal, a peer writing inside the window, an inode swap). Two of
    those are documented REPEATABLE windows (#9252), and before this leg existed
    the door took `position=False` with no counter, no log line and a `WIRED`
    gate verdict — so "layer 1 was written for this record" and "layer 1 skipped
    because the append proved nothing" were indistinguishable on every
    observable surface. That is #9078's invisible-gap class re-entering.

    The SAME paragraph also said this branch was the in-memory capture lane. It
    is not: that lane is declined earlier on `durable=False` and counted
    `no_durable_journal`, which this leg pins separately, because a docstring
    that names the wrong population is what sends the next maintainer looking in
    the wrong place for the missing rows.
    """
    def _fake_write_records(records, **kwargs):
        return record_writer.RecordWrite(
            outcome=record_writer.RecordWriteOutcome.APPLIED,
            events=len(records), statements=1,
            first_seq=kwargs["first_seq"], last_seq=kwargs["first_seq"],
            connection=None)

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "write_records", _fake_write_records)
    record = {"type": "PointAdded", "id": "p1"}

    # --- the population: a DURABLE journal, and the layer rows still written ---
    before = monitoring.record_position_unproven_count()
    with use_record_tenant(ORG, GRAPH):
        write = record_writer.write_live_record(record, seq=None)
    assert write is not None and write.applied, write
    assert write.events == 1 and write.statements == 1, write
    after = monitoring.record_position_unproven_count()
    assert after == before + 1, (
        "an unproven journal position wrote no position row and reported "
        f"nothing: record_position_unproven {before} -> {after}")
    # The watermark must not claim a position this call did not consume. The
    # seam was handed the default `first_seq=1`; reporting `last_seq=1` says
    # "position 1 was written", which is the row that is missing.
    assert write.last_seq == write.first_seq - 1, (
        "the door reported a consumed journal position it never wrote: "
        f"first_seq={write.first_seq} last_seq={write.last_seq}")

    # --- it is also SAID OUT LOUD, once per window (not once per record) ------
    monkeypatch.setattr(record_writer, "_unproven_position_warned_at",
                        float("-inf"))
    monkeypatch.setattr(record_writer, "POSITION_UNPROVEN_WARN_SECONDS", 3600.0)
    with caplog.at_level(logging.WARNING, logger=record_writer.__name__):
        caplog.clear()
        with use_record_tenant(ORG, GRAPH):
            for _ in range(3):
                record_writer.write_live_record(record, seq=None)
        told = [r for r in caplog.records if "position_unproven" in r.message]
    assert len(told) == 1, (
        "the unproven-position warning did not fire exactly once inside the "
        f"window: {[r.message for r in caplog.records]}")

    # --- and the NON-journal lane is NOT this branch ---------------------------
    # `durable=False` returns before the position decision; if this counter moved
    # here, the docstring's population would be right after all and the two
    # signals an operator reads ("no journal by design" vs "layer 1 is behind")
    # would be indistinguishable.
    skipped_before = dict(monitoring.record_non_journal_skip_counts())
    unproven_before = monitoring.record_position_unproven_count()
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record(record, durable=False) is None
    assert monitoring.record_position_unproven_count() == unproven_before, (
        "the non-durable lane was counted as an unproven POSITION — it has no "
        "journal at all and belongs on no_durable_journal")
    skipped_after = dict(monitoring.record_non_journal_skip_counts())
    assert skipped_after.get("no_durable_journal", 0) == \
        skipped_before.get("no_durable_journal", 0) + 1, skipped_after


def test_the_unproven_position_skip_is_on_the_health_surface(monkeypatch):
    """A counter nothing reads is #3677's "signal with no channel" (#9307 F1).

    The hosted app exposes no `/metrics` route, so `monitoring.metrics()` — the
    payload `tortoise_health` and `serve_health` serve — is where an operator can
    see that layer 1 is behind the journal. Pinned by presence, because the
    counter is only worth having if it is reachable from that surface.
    """
    assert "record_position_unproven" in monitoring.metrics(), (
        "the unproven-position counter rides no served surface, so the skip is "
        "still invisible to an operator")


def test_a_dead_record_costs_a_capture_one_connect_not_one_per_record(monkeypatch):
    """A connect timeout bounds ONE connect, not a CAPTURE (#9231 review P1).

    The live door is called once per journal record, on the caller's thread: on
    a blackholed host each call paid `CONNECT_TIMEOUT_SECONDS` again, so a
    25-record capture spent ~4 minutes inside one `capture_session` — a wedged
    capture, while the seam went on reporting success. A failed connect now
    trips the breaker, so the REST of that capture is refused locally and
    counted `circuit_open`; a success closes it again.
    """
    attempts: list[str] = []

    def _dead(dsn):
        attempts.append(dsn)
        raise OSError("no route to host")

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "_default_connect", _dead)
    before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(ORG, GRAPH):
        for _ in range(5):
            assert record_writer.write_live_record({"type": "PointAdded"}) is None
    after = dict(monitoring.record_write_failure_counts())

    assert len(attempts) == 1, (
        f"5 records paid the connect timeout {len(attempts)} times — the bound "
        "is per capture, not per record")
    assert after.get("write_failed", 0) - before.get("write_failed", 0) == 1, after
    assert after.get("circuit_open", 0) - before.get("circuit_open", 0) == 4, after

    # A REFUSAL is not an unreachable record: a totality/statement failure means
    # the connection was fine, so it must not silence the record for a minute.
    record_writer._close_live_breaker()

    def _refuse(records, **kwargs):
        raise record_writer.RecordWriteError("totality refused this record")

    monkeypatch.setattr(record_writer, "write_records", _refuse)
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record({"type": "PointAdded"}) is None
    assert record_writer._breaker_is_open() is False, (
        "a refused record tripped the breaker — the connection was fine")

    # The breaker also HEALS: a success closes it, so a recovered record writes
    # again without a restart.
    monkeypatch.setattr(record_writer, "write_records",
                        lambda records, **kwargs: record_writer.RecordWrite(
                            outcome=record_writer.RecordWriteOutcome.APPLIED,
                            events=len(records)))
    record_writer._trip_live_breaker()
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record({"type": "PointAdded"}) is None
    record_writer._close_live_breaker()
    with use_record_tenant(ORG, GRAPH):
        assert record_writer.write_live_record({"type": "PointAdded"}) is not None
    assert record_writer._breaker_is_open() is False


# ════════════════════════════════════════════════════════════════════════════
# 6. Resilience — the seam's "never raises" is ENFORCED, and bounded
# ════════════════════════════════════════════════════════════════════════════


def test_a_metrics_failure_cannot_escape_the_live_seam(monkeypatch):
    """A counting failure must not raise out of a committed write (#5148).

    `TortoiseSDK._record_write_failure` already suppressed this; the function it
    delegates to for every other reason did not, so a broken metrics registry
    turned a REPORTED loss into an exception thrown at `EventAPI._emit` / the
    miner AFTER the journal append committed. Pre-fix this test raises.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")

    def _boom(reason):
        raise RuntimeError("the metrics registry is broken")

    monkeypatch.setattr(monitoring, "record_record_write_failure", _boom)
    # No tenant in scope ⇒ the seam takes its counting path, which must swallow.
    assert record_writer.write_live_record({"type": "PointAdded"}) is None


def test_the_record_connection_is_bounded(monkeypatch):
    """The live door's connect is capped — libpq's default is "wait forever".

    One connection is opened per journal record, on the caller's thread, AFTER
    the append committed; an armed-but-unreachable record would otherwise stall
    every capture for as long as the kernel takes. A DSN that already names a
    `connect_timeout` keeps ITS number.
    """
    import types

    calls = []

    def _connect(dsn, **kwargs):
        calls.append((dsn, kwargs))
        return object()

    monkeypatch.setitem(sys.modules, "psycopg2",
                        types.SimpleNamespace(connect=_connect))
    record_writer._default_connect("postgresql://u:p@127.0.0.1:1/db")
    # Not `== record_writer.CONNECT_TIMEOUT_SECONDS`: that compares the
    # implementation's constant to itself and cannot fail (#9231 review P2).
    # The bound is the independent claim; the DSN-override below is the other.
    assert "connect_timeout" in calls[0][1], calls[0][1]
    assert isinstance(calls[0][1]["connect_timeout"], int), calls[0][1]
    assert 0 < calls[0][1]["connect_timeout"] <= 30, calls[0][1]
    record_writer._default_connect(
        "postgresql://u:p@127.0.0.1:1/db?connect_timeout=3")
    assert "connect_timeout" not in calls[1][1], (
        "an explicit connect_timeout on the DSN must win over the default")
    # A QUERY KEY, not a substring: the DSN is the whole URI, so a password
    # that merely CONTAINS the characters must not read as the operator's
    # bound — that branch would restore libpq's wait-forever default on the
    # per-record connect this constant exists to cap (#9231 review P2).
    record_writer._default_connect(
        "postgresql://svc:connect_timeout@127.0.0.1:1/db")
    assert "connect_timeout" in calls[2][1], (
        "a password containing `connect_timeout` is not the operator's bound: "
        f"the seam must still cap the connect, got {calls[2][1]!r}")
    assert 0 < calls[2][1]["connect_timeout"] <= 30, calls[2][1]
    record_writer._default_connect(
        "postgresql://u:p@127.0.0.1:1/db?connect_timeout_extra=3")
    assert "connect_timeout" in calls[3][1], (
        "a similarly-spelled query key is not `connect_timeout`")


def test_a_malformed_dsn_on_the_journal_less_lane_is_counted(
        capture_sdk, monkeypatch):
    """A typo'd DSN with no journal produced ZERO signal before this fix.

    On the journal-less lane the seam is never called (there is no append to
    hook), so `write_live_record`'s own `malformed_dsn` count could never fire:
    the bare `except Exception: return` swallowed the misconfiguration. The
    armed-but-malformed shape is the one an operator hits first when arming.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "mysql://not-postgres/nope")
    sdk, _journal = capture_sdk(journal=False)
    before = dict(monitoring.record_write_failure_counts())
    receipt = sdk.capture_session([
        {"role": "user", "content": "We chose FalkorDB for the hot cache."},
        {"role": "assistant", "content": "Noted."},
    ])
    assert receipt["ok"] is True
    after = dict(monitoring.record_write_failure_counts())
    assert after.get("malformed_dsn", 0) > before.get("malformed_dsn", 0), after


# ════════════════════════════════════════════════════════════════════════════
# 7. The four-layer proof — a real Postgres, the PUBLIC capture path
# ════════════════════════════════════════════════════════════════════════════


def _record_db():
    """A connection to a Postgres carrying the record migrations, or skip."""
    dsn = os.environ.get(RECORD_DB_ENV)
    if not dsn:
        pytest.skip(f"{RECORD_DB_ENV} not set — no Postgres for the four-layer proof")
    psycopg2 = pytest.importorskip("psycopg2",
                                   reason="psycopg2-binary is the record's driver")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    return conn


def _ensure_record_schema(conn) -> None:
    """Apply the record migrations if the database does not carry them yet.

    Idempotent by inspection (`journal_event` present ⇒ done), so it works both
    on a throwaway container and on one a previous run already migrated.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.journal_event')")
        if cur.fetchone()[0] is not None:
            return
        # ONE statement: the preamble's role guards are `DO $$ … $$` blocks, and
        # splitting on ';' would shred them into unterminated dollar quotes.
        cur.execute(_PREAMBLE)
        for path in RECORD_MIGRATION_FILES:
            cur.execute(path.read_text(encoding="utf-8"))


def _seed_tenant(conn, tenant: tuple[str, str]) -> None:
    org, graph = tenant
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.organizations (id, name) VALUES (%s, %s) "
            "ON CONFLICT (id) DO NOTHING", (org, org))
        cur.execute(
            "INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s) "
            "ON CONFLICT (id) DO NOTHING", (graph, org, graph))
        # `journal_event` is APPEND-ONLY by trigger (a tenant erasure sets
        # `app.record_journal_purge=on`), so this seed CANNOT clear it — and it
        # no longer tries. Deleting here was itself the #9078 assumption (layer 1
        # never written) baked into the fixture. What makes layer 1 assertable
        # instead is a tenant only THIS test has used (`tenant`, #9307 review
        # F2/F4): on a pair no previous run and no sibling test has touched, the
        # only rows the table can hold are the ones this leg wrote, so its DELTA
        # is attributable to this capture and a reverted door reds it.
        for table in ("event", "object", "subject", "point", "relation",
                      "session", "source"):
            cur.execute(
                f"DELETE FROM public.{table} WHERE org_id=%s AND graph_id=%s",
                (org, graph))


def _count(conn, table: str, tenant: tuple[str, str]) -> int:
    org, graph = tenant
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM public.{table} WHERE org_id=%s "
                    f"AND graph_id=%s", (org, graph))
        return int(cur.fetchone()[0])


#: Fold refusals that are PRE-EXISTING defects of the projection — not of this
#: wiring. PR #9200 fixes them; until it lands, a live record can be REFUSED by
#: the fold before a single statement runs. They are tolerated ONLY while the
#: fold still refuses the live Point shape (see `_fold_refuses_captured_points`),
#: so the tolerance DELETE-ITSELF when #9200 lands: no stale pin, no red on main
#: the day it merges, and a NEW refusal (no fragment here) fails loudly.
#:
#:   * "no usable 'id' key for Point" — `_key_value` reads the envelope while the
#:     producer puts the id in `payload["point"]` (#9200 row 1);
#:   * "carries no `point` snapshot" — `EntityBindingRefused` is FLAT
#:     (`subject_binding._journal_refusal`) while `RECORD_MUTATIONS` declares a
#:     `point` snapshot for it; #9200 row 4 turns it into `NoRecordEffect`.
_ENGINE_REFUSAL_FRAGMENTS = {
    "no usable 'id' key for Point": "#9200 row 1 — the id rides the snapshot",
    "carries no `point` snapshot": "#9200 row 4 — EntityBindingRefused is flat",
}

#: The engine dependency the epistemic-layer claim cannot satisfy on its own.
_ENGINE_DEPENDENCY = (
    "the fold cannot project a captured Point yet — its id rides the snapshot "
    "and `record_projection._key_value` reads the envelope (PR #9200 fixes it; "
    "it also turns the flat EntityBindingRefused into NoRecordEffect). Measured "
    "at #9200's head + this diff: with those fixes applied the epistemic leg "
    "passes for real, so this marker self-disables (the condition is the "
    "MEASURED defect) and reddens nothing on the day #9200 merges."
)


def _fold_refuses_captured_points() -> bool:
    """Does the fold still refuse a Point whose id rides the snapshot?

    The exact shape `TortoiseSDK._emit_event` produces for `PointAdded` /
    `PointPromoted` (`event["point"] = clean`, no envelope `id`). Measured, not
    asserted from a date: DB-free and cheap.
    """
    live_shape = {"type": "PointAdded", "ts": "2026-10-19T00:00:00Z",
                  "point": {"id": "p1", "content": "a claim",
                            "pointKind": "claim"}}
    try:
        record_projection.mutations_for(
            live_shape, org_id="probe", graph_id="probe",
            columns=record_projection.table_columns(),
            refused=record_projection.refused_props_names())
    except record_projection.RecordProjectionError:
        return True
    return False


def _engine_refusal(message: str) -> str | None:
    """Is this refusal one of the PRE-EXISTING fold defects (#9200's class)?

    Empty unless the fold still refuses the live Point shape, so the tolerance
    disappears on its own when #9200 lands.
    """
    if not _fold_refuses_captured_points():
        return None
    return next((fragment for fragment in _ENGINE_REFUSAL_FRAGMENTS
                 if fragment in message), None)


def _journal_records(journal_path: Path) -> list[dict]:
    return [json.loads(line) for line in
            journal_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _capture(capture_sdk, monkeypatch,
             tenant: tuple[str, str]) -> tuple[object, Path, int]:
    """Seed, arm, capture through the PUBLIC API, return (sdk, journal, losses)."""
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, os.environ[RECORD_DB_ENV])
    sdk, journal_path = capture_sdk()
    before = dict(monitoring.record_write_failure_counts())
    with use_record_tenant(*tenant):
        receipt = sdk.capture_session([
            {"role": "user", "content": "We chose FalkorDB for the hot cache."},
            {"role": "assistant", "content": "Noted: FalkorDB holds the hot cache."},
        ])
    lost = (monitoring.record_write_failure_counts().get("write_failed", 0)
            - before.get("write_failed", 0))
    assert receipt["ok"] is True, receipt
    return sdk, journal_path, lost


def test_the_public_capture_path_writes_the_record(capture_sdk, monkeypatch,
                                                  tenant):
    """The record, written by the PUBLIC capture path — and the residue NAMED.

    Layer by layer, from the schema's own shape:

    * **event stream** — `event` / `source` / `session` (migration 1's episodic
      tables) AND `journal_event`, the position row layer 1 IS (exposed as
      `public.record_event_stream`). The row is keyed on the ordinal
      `EventLog.append` reported (#9190), so the live write and a later replay
      give one record one number;
    * **semantic** — `object` / `subject`, asserted against the objects the
      capture actually emitted (counted from the journal, so the claim is
      non-vacuous either way: a capture that emits no Object record must produce
      no Object row);
    * **procedural** — a VIEW, never a table ("the procedural layer owns no
      table", migration 4): `object_current_status` folds the `object` rows.

    Then the RESIDUE, per RECORD and unconditionally: every journal record is
    replayed on its own, and a refusal must name a known cause — either a
    schema/producer disagreement (the emitted value does not fit the declared
    column) or one of the pre-existing fold refusals PR #9200 fixes. Anything
    else is a NEW defect and fails here. Every allowance is derived from what
    the record itself carries or from a MEASURED fold behaviour, so nothing can
    go stale in silence.
    """
    conn = _record_db()
    try:
        _ensure_record_schema(conn)
        _seed_tenant(conn, tenant)
        # The layer-1 count is a property of THIS run (#9307 review F4).
        # `journal_event` is append-only by trigger, so nothing clears it; taken
        # as a DELTA against a tenant only this test has used, it stays correct
        # on a database a previous run migrated, and the count can only move in
        # the direction the door writes it.
        before = _count(conn, "journal_event", tenant)
        _sdk, journal_path, lost = _capture(capture_sdk, monkeypatch, tenant)
        after = _count(conn, "journal_event", tenant)
        written = after - before

        # 1. the event stream, from the production path
        for table in ("event", "source", "session"):
            assert _count(conn, table, tenant) >= 1, (
                f"{table} received no row from the public capture path")
        # 2. layer 1 is written from the journal's OWN ordinal (#9190).
        #    `journal_event` is `public.record_event_stream`'s source, and it is
        #    the acceptance sentence of the epic: before the ordinal was
        #    threaded it stayed EMPTY on every live write (the deferral this PR
        #    shipped), and the row count must therefore be at least one per
        #    record the journal carried — never zero, and never more than the
        #    journal holds (an invented number would invent rows).
        records = _journal_records(journal_path)
        assert written >= 1, (
            "the live door wrote no position row — layer 1 "
            "(`public.journal_event`, exposed as `public.record_event_stream`) "
            "is empty, so the epic's event-stream claim is unmet by the very "
            "path that is supposed to meet it (#9190's ordinal)")
        # One row per record, at most: a row numbered beyond the journal's own
        # length would be an invented seq (the collision #9190 removes). The
        # journal file is the ground truth for that length.
        assert written <= len(records), (
            f"the record gained {written} position row(s) for "
            f"{len(records)} journal record(s) — a row numbered past the "
            "journal is an invented seq")

        # 3. the semantic + procedural layers, counted against the journal.
        #    `assert objects` FIRST and unconditionally: without it the count
        #    comparison below is satisfiable by `0 >= 0`, i.e. by a capture that
        #    emitted no ObjectRegistered record at all — which is the pre-fix
        #    world (nothing written) reading as a pass. Measured: the public
        #    capture emits ObjectRegistered, so this is a real assertion.
        objects = [r for r in records if r.get("type") == "ObjectRegistered"]
        assert objects, (
            "the capture emitted no ObjectRegistered record, so the semantic- "
            f"layer claim would be vacuous — journal types: "
            f"{sorted({r.get('type') for r in records})}")
        assert _count(conn, "object", tenant) >= len(objects), (
            f"the record holds {_count(conn, 'object', tenant)} Object row(s) for "
            f"the {len(objects)} ObjectRegistered record(s) the journal carried")
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM public.object_current_status "
                        "WHERE org_id=%s", (tenant[0],))
            assert int(cur.fetchone()[0]) >= len(objects), (
                "the procedural layer's view returns fewer rows than the "
                "objects the record holds")

        # 4. the residue, per record
        refused = 0
        for record in records:
            try:
                record_projection.replay([record], conn, org_id=tenant[0],
                                         graph_id=tenant[1], position=False)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                message = str(exc)
                if _engine_refusal(message) is not None:
                    # A pre-existing fold refusal: the record never reached
                    # Postgres, so it cannot be judged on its values yet.
                    refused += 1
                    continue
                disagreement = _disagreement(record)
                assert disagreement is not None, (
                    f"{record.get('type')} cannot be projected and the refusal "
                    f"is not a schema/producer disagreement: {message}. That is "
                    "a NEW defect, not a widened allowance."
                )
                assert _SCHEMA_DISAGREEMENTS[disagreement] in message, (
                    f"{record.get('type')} carries a {disagreement} "
                    f"disagreement but the error does not name it: {message}"
                )
                refused += 1
            else:
                assert _disagreement(record) is None, (
                    f"{record.get('type')} now projects cleanly, so its "
                    f"{_disagreement(record)!r} disagreement is FIXED — delete "
                    "it from _SCHEMA_DISAGREEMENTS. A pin that outlives its "
                    "defect hides the next one."
                )
        # 5. the capture lost exactly those records and nothing else
        assert lost == refused, (
            f"the capture counted {lost} lost record(s); replaying the journal "
            f"shows {refused} refused record(s)")
    finally:
        conn.close()


@pytest.mark.xfail(condition=_fold_refuses_captured_points(), strict=True,
                   reason=_ENGINE_DEPENDENCY)
def test_the_public_capture_path_writes_the_epistemic_layer(capture_sdk,
                                                            monkeypatch, tenant):
    """`point` / `relation` receive rows from the production path.

    THE claim the epic makes about a user's data ("a user's Points … exist in
    Postgres"). It is the ONLY assertion gated on the engine, because it is the
    only one the pre-existing fold defect makes unreachable: on a tree without
    #9200 every captured Point is refused, so `point` would be empty.
    """
    conn = _record_db()
    try:
        _ensure_record_schema(conn)
        _seed_tenant(conn, tenant)
        _capture(capture_sdk, monkeypatch, tenant)
        assert _count(conn, "point", tenant) >= 1, (
            "the epistemic layer received no row from the capture")
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════════════════
# 9. The TENANT on the REST/hosted lane — carried by value, resolved once
# ════════════════════════════════════════════════════════════════════════════
#
# #9188's remaining half. The MCP lane installs the record's tenant as an
# ambient `use_record_tenant` scope around `call_next` (#9192), which works
# because its auth is middleware. The hosted REST lane's auth is a plain FastAPI
# dependency TODAY (a `yield`-dependency could hold a scope; none does), so no
# `with` exists to enter, and the tenant is resolved inside `_data_sdk` — which
# the capture path submits to `_graph_offload`, so a scope entered there dies
# with the context copy — and setting it loop-side would mean setting it WITH NO
# EXIT (one unreleased value inherited by every detached task and every other
# write door in that context). So that lane carries the tenant BY VALUE on the
# SDK that performs the write. Confinement is the reason, NOT propagation: a
# contextvar set on the loop IS copied into `asyncio.to_thread` and
# `_graph_offload` (measured).
#
# Until this half landed, EVERY armed write on that lane was refused
# `no_tenant` — including a graph-bound key whose `graph_id` is a valid
# `public.graphs` id, i.e. the request had told us the tenant and the record
# refused to hear it.


def test_the_record_tenant_is_resolved_from_the_credential_once():
    """One declaration of "which `(org, graph)` is a record tenant".

    Both lanes call `record_writer.record_tenant_for`; this pins its answers,
    including the two shapes it must REFUSE rather than guess. A third
    resolution written into either lane is how one request's rows end up filed
    under a different graph than the one its data went to.
    """
    bound = {"org_id": "org-a", "graph_id": "g_control_plane_row", "tier": "team"}
    assert record_writer.record_tenant_for(bound, control_plane_ids=True) == (
        "org-a", "g_control_plane_row")

    # The SELF-HOST registry lane: `graph_id` is a Falkor `:Graph` node id, and
    # registry graphs have no `public.graphs` row, so the record's FK cannot
    # take it. Naming it would turn a recording deployment into `write_failed`
    # on every write with a cause nobody could attribute (#9192).
    assert record_writer.record_tenant_for(bound, control_plane_ids=False) is None

    # An org-wide key / session auth names no graph. The DEFAULT graph need not
    # have a `public.graphs` row (it is derived from `organizations.graph_name`),
    # so "the default" is a control-plane decision — never a constant invented
    # here. Refused, counted `no_tenant`, replay-healable: the fail-closed side.
    for no_graph in ({"org_id": "org-a"}, {"org_id": "org-a", "graph_id": None},
                     {"org_id": "org-a", "graph_id": ""}):
        assert record_writer.record_tenant_for(
            no_graph, control_plane_ids=True) is None, no_graph

    # A half-configured credential is not a tenant, and a non-dict (an
    # anonymous request's `{"org_id": None}` shape, or `None` itself) is not
    # either — no exceptions, no completion by guesswork.
    for bad in ({"graph_id": "g_x"}, {"org_id": "", "graph_id": "g_x"},
                {"org_id": "org-a", "graph_id": 7}, None, "org-a"):
        assert record_writer.record_tenant_for(
            bad, control_plane_ids=True) is None, bad


def test_both_tenant_lanes_agree_because_there_is_one_declaration():
    """The MCP scope and the REST carrier must not answer differently.

    `mcp_auth._record_tenant_scope` wraps `record_tenant_for`; a lane that
    re-derived the pair (or re-implemented the control-plane id-space rule)
    would sail through its own tests and silently file the same request's rows
    under a different tenant on the other transport.
    """
    from tortoise import mcp_auth

    for org, control_plane in (
            ({"org_id": "org-a", "graph_id": "g_row"}, True),
            ({"org_id": "org-a", "graph_id": "g_hex"}, False),
            ({"org_id": "org-a"}, True),
            ({}, True)):
        expected = record_writer.record_tenant_for(
            org, control_plane_ids=control_plane)
        with mcp_auth._record_tenant_scope(org, control_plane_ids=control_plane):
            assert record_writer.current_record_tenant() == expected, (
                org, control_plane)


def test_the_rest_data_lane_binds_the_resolved_tenant_onto_its_sdk(monkeypatch):
    """`_data_sdk` is the data-plane tenancy resolver every REST data route uses.

    So it is the one place the REST lane can hand the record a tenant, and it
    must do it from the SAME credential dict that chose the graph — otherwise
    the record and the graph disagree. Measured through the public seam it
    calls: the returned SDK carries the pair, and carries NOTHING for the two
    shapes that must stay refused.

    ⚠️ NOT covered here, and deliberately: the onboarding/starter seed runners
    open the org's DEFAULT graph by construction (`_make_sdk(namespace=org_id)`,
    not this resolver). The default graph need not have a `public.graphs` row,
    so binding them to a graph-bound key's id would file their rows under a
    graph they never wrote; naming the default-graph tenant is the open
    control-plane decision on #9188.
    """
    from tortoise import hosted_api, supabase_control

    bound: list[tuple[str, str]] = []

    class _FakeSDK:
        def _bind_record_tenant(self, org_id, graph_id):
            bound.append((org_id, graph_id))

    monkeypatch.setattr(hosted_api, "_make_sdk", lambda **kw: _FakeSDK())
    monkeypatch.setattr(hosted_api, "_assert_graph_owned",
                        lambda org, gid, ns: None)

    graph_bound = {"org_id": "org-a", "graph_id": "g_row",
                   "graph_namespace": "org_org-a_g_row"}
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: True)
    hosted_api._data_sdk(dict(graph_bound))
    assert bound == [("org-a", "g_row")], bound

    # Default graph (org-wide key / session auth): no graphs row is guaranteed,
    # so nothing is bound and the write stays refused `no_tenant`. This is the
    # control-plane decision #9188 escalated, NOT an oversight.
    hosted_api._data_sdk({"org_id": "org-a"})
    assert bound == [("org-a", "g_row")], (
        f"a default-graph request bound a tenant nobody named: {bound}")

    # The self-host registry lane's ids are not `public.graphs` ids.
    monkeypatch.setattr(supabase_control, "is_supabase_enabled", lambda: False)
    hosted_api._data_sdk({"org_id": "org-a", "graph_id": "g_hex",
                          "graph_namespace": "org_org-a_g_hex"})
    assert bound == [("org-a", "g_row")], (
        f"a registry `g_<hex>` id was bound as a record tenant: {bound}")


def test_the_live_door_files_the_sdks_carried_tenant(capture_sdk, monkeypatch):
    """The load-bearing leg: the carried tenant reaches `write_records`.

    DB-free and executed, because the Postgres legs SKIP without
    `TEST_RECORD_DSN` and no CI job arms it. It drives the REAL chokepoint
    (`_write_record_projection`) with a faked `write_records`, so dropping the
    `tenant=` hop — or the SDK dropping it before the seam — fails here with
    `no_tenant` and no call at all, which is exactly the defect (an armed REST
    write refused while the credential had named its graph).
    """
    seen: list[dict] = []

    def _fake_write_records(records, **kwargs):
        seen.append(kwargs)
        return record_writer.RecordWrite(
            outcome=record_writer.RecordWriteOutcome.APPLIED,
            events=len(records), statements=1,
            first_seq=kwargs["first_seq"], last_seq=kwargs["first_seq"],
            connection=None)

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "write_records", _fake_write_records)
    monkeypatch.setattr(record_writer, "_default_connect", lambda dsn: pytest.fail(
        "write_records was faked; the seam must not open a real connection"))

    sdk, _journal = capture_sdk()
    # The chokepoint reads `durable` off the SDK's log; without a REAL journal
    # the seam declines the record earlier (`no_durable_journal`) and this leg
    # would be measuring the wrong refusal.
    assert sdk._get_event_log() is not None, "the fixture configures a journal"
    record = {"type": "PointAdded", "id": "p1", "payload": {}}

    # No carried tenant, no scope, no env pair ⇒ refused `no_tenant`, and the
    # seam never reaches the fold. This is the PRE-FIX behaviour of the REST
    # lane on EVERY request, so the leg below cannot pass vacuously.
    failures_before = dict(monitoring.record_write_failure_counts())
    sdk._write_record_projection(record, seq=1)
    assert seen == [], (
        "an armed write with NO tenant reached the fold — a row was filed "
        "under a tenant nobody named")
    assert dict(monitoring.record_write_failure_counts()).get(
        "no_tenant", 0) > failures_before.get("no_tenant", 0)

    # Now the carrier: the same SDK, bound the way `_data_sdk` binds it.
    sdk._bind_record_tenant("org-rest", "g_rest_row")
    sdk._write_record_projection(record, seq=1)
    assert len(seen) == 1, f"the carried tenant did not reach the seam: {seen}"
    assert (seen[0]["org_id"], seen[0]["graph_id"]) == ("org-rest", "g_rest_row")

    # And a CARRIED tenant outranks ambient state, so a lane that off-loads its
    # writes cannot have some other request's scope applied to its record.
    with use_record_tenant("org-other", "g_other_row"):
        sdk._write_record_projection(record, seq=2)
    assert (seen[1]["org_id"], seen[1]["graph_id"]) == ("org-rest", "g_rest_row"), (
        f"ambient state overrode the tenant carried by this SDK: {seen}")


def test_binding_a_record_tenant_refuses_an_unnamed_half():
    """A half pair must fail at the bind, not be discovered a drained record later."""
    sdk = TortoiseSDK.__new__(TortoiseSDK)  # no graph: the bind is pure state
    for bad in (("org-a", ""), ("", "g_row"), (None, "g_row"), ("org-a", None)):
        with pytest.raises(ValueError):
            sdk._bind_record_tenant(*bad)


def test_the_seam_refuses_a_malformed_carried_tenant(monkeypatch):
    """The carrier is the seam's ONLY injection point — it must not be its weakest.

    `use_record_tenant` and `SDK._bind_record_tenant` both refuse a half pair,
    so a `tenant=` that unpacked `("", "")` (or a bare string, or a 1-tuple)
    straight into `write_records` would be the one door through which a row
    lands under a tenant nobody named, with no error at any layer (#9188
    review F4). Refused with the same `no_tenant` count as no tenant at all.
    """
    calls: list[dict] = []

    def _must_not_write(records, **kwargs):
        calls.append(kwargs)
        raise AssertionError(f"a malformed carried tenant reached the fold: {kwargs}")

    monkeypatch.setenv(record_writer.RECORD_DSN_ENV,
                       "postgresql://record:record@127.0.0.1:1/record")
    monkeypatch.setattr(record_writer, "write_records", _must_not_write)

    before = dict(monitoring.record_write_failure_counts()).get("no_tenant", 0)
    malformed = (("", ""), ("org-a", ""), ("", "g_row"), ("org-a",),
                 ("org-a", "g_row", "extra"), ("org-a", None), "org-a", 7)
    for bad in malformed:
        assert record_writer.write_live_record(
            {"type": "PointAdded", "id": "p1"}, seq=1, tenant=bad) is None, bad
    assert calls == [], calls
    after = dict(monitoring.record_write_failure_counts()).get("no_tenant", 0)
    assert after - before == len(malformed), (
        f"a malformed carried tenant was not counted as a refusal: {before} → {after}")


def test_the_dream_drain_carries_the_enqueueing_requests_tenant(monkeypatch):
    """The REST lane's SECOND writer: the dream drain, which opens its own SDK.

    `_enqueue_dream` is called by the point-write handler with the request's
    resolved graph, and the drain (`_open_sdk`) opens that graph by NAME — a
    namespace is not a tenant, so without the carry every record the pass emits
    (`ConfidenceChanged`) is refused `no_tenant` on the very graph whose
    credential named it, while the request's own writes are filed correctly.
    Measured as review finding N1; this pins both halves: the enqueue records
    the answer against its key, and the drain's SDK receives it.
    """
    from tortoise import hosted_api

    class _FakeSDK:
        def __init__(self):
            self.bound: list[tuple[str, str]] = []

        def _bind_record_tenant(self, org_id, graph_id):
            self.bound.append((org_id, graph_id))

    graph_ns = "org_org-a_g_dream"
    key = hosted_api._dream_key("org-a", graph_ns)
    default_key = hosted_api._dream_key("org-a", None)

    async def _drive() -> None:
        try:
            hosted_api._enqueue_dream("org-a", ["root-1"],
                                     graph_namespace=graph_ns,
                                     record_tenant=("org-a", "g_dream"))
            assert hosted_api._DREAM_RECORD_TENANTS.get(key) == (
                "org-a", "g_dream")

            sdk = _FakeSDK()
            hosted_api._bind_dream_record_tenant(sdk, key)
            assert sdk.bound == [("org-a", "g_dream")], sdk.bound

            # …AND that the drain actually calls it. The helper passing on its
            # own is worth nothing if `_open_sdk` never asks: this is the wire
            # between the enqueue's answer and the SDK that emits the records.
            import inspect
            assert "_bind_dream_record_tenant(sdk, key)" in inspect.getsource(
                hosted_api._dream_worker), (
                "the dream drain opens an SDK that never receives the tenant "
                "the enqueueing request named — every pass-emitted record is "
                "refused `no_tenant` on an armed lane (review finding N1)")

            # An org-wide/session enqueue names NO graph, so nothing is carried
            # and the drain's writes stay refused `no_tenant` (the default-graph
            # decision — never a guess).
            hosted_api._enqueue_dream("org-a", ["root-2"], record_tenant=None)
            assert default_key not in hosted_api._DREAM_RECORD_TENANTS
            default_sdk = _FakeSDK()
            hosted_api._bind_dream_record_tenant(default_sdk, default_key)
            assert default_sdk.bound == [], default_sdk.bound
        finally:
            for k in (key, default_key):
                hosted_api._DREAM_QUEUES.pop(k, None)
                hosted_api._DREAM_RECORD_TENANTS.pop(k, None)
                task = hosted_api._DREAM_TASKS.pop(k, None)
                if task is not None:
                    task.cancel()

    asyncio.run(_drive())
