"""#9189 / §17.2 — the record↔cache reconciler has a PRODUCTION TRIGGER.

``tortoise/reconciliation.py`` compares and refuses; it had **no production
caller at all** measured at ``f96a18da1`` (tests and the #7950 evidence probes
only), so §17.2's *"divergence must be OBSERVED, not merely recoverable"* had a
mechanism and no observer. ``tortoise/record_reconcile.py`` is the observer and
``python -m tortoise record-reconcile`` is its caller.

Six things are pinned here, and each is a way the observer could be worthless:

1. **A clean projection reconciles CLEAN** — ``divergent == 0``,
   ``field_mismatches == 0``, ``missing_in_cache == 0``. Uses the shipped
   writer (``cache_projection.project_records``) and the shipped reader
   (``read_cache_view``), not a fixture shaped to agree.
2. **The observer is NOT VACUOUS** — a deliberate second writer, a REAL raw
   graph-handle write that appends nothing to any journal, raises
   ``extra_in_cache > 0``. The count is not a mock's arithmetic (#9189
   indicator 2); it is read back out of the cache.
3. **The guarded lanes REFUSE the same write, and the raw handle does not** —
   which is why the guard alone cannot be the answer and an observer is needed.
   ⚠️ Stated as it is: the §17.1 gate closes ``FalkorProjection.apply`` and
   ``FalkorProjection.query``, and the raw graph handle (``proj.g.query``)
   survives — the surviving second-writer seam this count exists to see. Both
   halves are asserted on ONE **journal-backed** projection, and the write the
   handle accepts is then COUNTED by the shipped read path, because a change
   that closed ``proj.g.query`` too would otherwise keep this file green while
   ``python -m tortoise record-reconcile`` reported exit 2 forever.
4. **An unreadable side is a REFUSAL, and nothing compared is NOT clean** — an
   unreadable cache, a node with no contract key, two nodes at one coordinate,
   and a pair of placeholder property maps all refuse; an empty pair verdicts
   ``unanswerable`` (exit 2), never ``clean``.
5. **A PARTIAL declaration's unwritten keys are RESIDENCY, not divergence**
   (§17.3) — counted as ``not_hot``, never as ``missing_in_cache`` — while a
   ``complete`` declaration's missing key still counts as divergence.
6. **The trigger exists in production, AND IT COUNTED SOMETHING** — an AST
   pin that ``_cmd_record_reconcile`` CALLS the composition (not merely imports
   it), and a live drive of ``main(["record-reconcile", …])`` over a real cache
   graph whose verdict the verb has to REPORT: a divergent cache must exit
   ``3`` with the counts printed. An import-only pin is satisfied by a verb
   whose body is ``return EXIT_CLEAN`` — a check with a caller that observes
   nothing is #9189's own defect, one level up. The documented exit-code
   contract, including the usage case argparse answers with ``2``, is pinned
   rather than narrated.

⚠️ This file lives in BOTH the `api`/`core` surfaces and `carve_out:`: item 3
is embedded-only (§17.1's journal arm exists only on the embedded lane), and a
carve-out-less registration would leave it collected-and-SKIPPED on every full
selection — a permanently green, permanently unexecuted gate (#4047).

⚠️ The trigger is a SWEEP (the CLI verb), not a call inside the capture turn:
detection latency is the sweep interval, and divergence created AND repaired
between two sweeps is not observed. Migrating the trigger onto the capture
boundary is #9188's, whose writer does not exist yet.
"""
from __future__ import annotations

import ast
import contextlib
import functools
import json
import os
import sys
import types
import uuid
import warnings
from pathlib import Path
from typing import ClassVar

import pytest

from tortoise import cache_projection as cp
from tortoise import record_reconcile as rr
from tortoise.projection import DirectCacheWriteRefused
from tortoise.reconciliation import ReconciliationUnavailable, reconcile_views
from tortoise.residency import (
    COVERAGE_COMPLETE,
    COVERAGE_PARTIAL,
    ResidencyDeclaration,
    read_declaration,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: The trigger module this suite is about. Named once so the import-graph pin
#: and the positive control cannot drift apart.
_TRIGGER_MODULE = "tortoise.record_reconcile"


# ── fixtures: an isolated cache graph per test ─────────────────────────────


@pytest.fixture
def cache_graph(shared_proj):
    """A per-test cache graph, so the comparison sees ONLY this test's nodes.

    A shared graph would make ``extra_in_cache`` a function of whatever another
    test left behind, which is the one assertion in this file that must be
    exact. ``select_graph`` on the session client costs no second server, so
    this is isolation without a new embedded construction (and so without the
    carve-out / redirect registries a fresh server would need).
    """
    if shared_proj is None:
        pytest.skip("no graph engine available on this lane")
    graph = shared_proj.db.select_graph(f"test_9189_{uuid.uuid4().hex[:8]}")
    try:
        yield graph
    finally:
        with contextlib.suppress(Exception):
            graph.delete()


def _record_view() -> cp.RecordView:
    """Two Points, in the record's own vocabulary (real Point columns)."""
    return cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-1",
                      props={"id": "p-9189-1", "content": "alpha",
                             "status": "live"}),
        cp.RecordNode(label="Point", key="p-9189-2",
                      props={"id": "p-9189-2", "content": "beta",
                             "status": "live"}),
    ))


def _project(view: cp.RecordView, graph, *, limit: int | None = None):
    """Project the record with the SHIPPED writer, and declare what was written."""
    nodes = tuple(view.nodes) if limit is None else tuple(
        sorted(view.nodes, key=lambda n: (n.label, n.key))[:limit])
    plan = cp.record_plan(nodes, view.relations)
    cp.project_records(graph, plan)
    declaration = cp.declaration_for(view.record_keys, plan.node_keys())
    cp.write_declaration(graph, declaration)
    return declaration


def _run(view: cp.RecordView, graph) -> rr.RecordReconcileResult:
    return rr.reconcile_record_and_cache(
        rr.record_key_map(view),
        rr.read_cache_view(graph),
        declaration=read_declaration(graph),
    )


# ── 1. a clean capture reconciles clean ────────────────────────────────────


def test_a_clean_projection_reconciles_clean(cache_graph):
    view = _record_view()
    _project(view, cache_graph)

    result = _run(view, cache_graph)

    assert result.report.checked == 2
    assert result.report.divergent == 0
    assert result.report.missing_in_cache == 0
    assert result.report.extra_in_cache == 0
    assert result.report.field_mismatches == 0
    assert result.report.form_normalized == 0
    assert result.not_hot == 0
    assert result.verdict() == rr.VERDICT_CLEAN
    assert rr.exit_code_for(result) == rr.EXIT_CLEAN


def test_a_verbatim_point_reconciles_clean_end_to_end(cache_graph):
    """#9184's disposition, END TO END, through the shipped writer and reader.

    A verbatim Point's marker is ``text`` in the record and a BOOLEAN in the
    cache, so before the stored-form rule this projection reported a permanent
    ``field_mismatches=1`` and indicator (1) of #9189 was unreachable. The rule
    absorbs it and says so: ``form_normalized=1``, ``field_mismatches=0``.
    """
    view = cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-verbatim",
                      props={"id": "p-9189-verbatim", "content": "alpha",
                             "embedding": [0.1, 0.2],
                             "embedding_verbatim": "true"}),
    ))
    _project(view, cache_graph)

    # The two sides really do hold different FORMS — otherwise this proves
    # nothing (the marker the cache holds is a boolean, the record's is text).
    cache = rr.read_cache_view(cache_graph)
    assert cache["Point:p-9189-verbatim"]["embedding_verbatim"] is True
    assert rr.record_key_map(view)["Point:p-9189-verbatim"][
        "embedding_verbatim"] == "true"

    result = _run(view, cache_graph)

    assert result.report.field_mismatches == 0
    assert result.report.divergent == 0
    assert result.report.form_normalized == 1, result.report.render()
    assert result.verdict() == rr.VERDICT_CLEAN


def test_the_cache_reader_returns_the_real_property_maps(cache_graph):
    """Not placeholders: the maps are what the cache holds (#8609 finding 3)."""
    view = _record_view()
    _project(view, cache_graph)

    cache = rr.read_cache_view(cache_graph)

    assert cache["Point:p-9189-1"] == {
        "id": "p-9189-1", "content": "alpha", "status": "live"}
    assert all(cache.values()), "a placeholder map makes the field check vacuous"


# ── 2. the observer is not vacuous ────────────────────────────────────────


def test_a_second_writer_raises_extra_in_cache(cache_graph):
    """A REAL unjournalled write through the graph handle, read back as a count."""
    view = _record_view()
    _project(view, cache_graph)

    # The raw graph handle: the surviving second-writer seam. It takes no
    # journal, so this write bypasses the record entirely — no projection
    # statement ran, and the record view knows nothing about it.
    cache_graph.query("CREATE (:Point {id:'p-9189-direct'})")

    result = _run(view, cache_graph)

    assert result.report.extra_in_cache == 1, result.report.render()
    assert result.report.divergent == 1
    assert result.report.missing_in_cache == 0
    assert result.verdict() == rr.VERDICT_DIVERGENT
    assert rr.exit_code_for(result) == rr.EXIT_DIVERGENT


@pytest.mark.embedded_only
def test_the_guarded_lanes_refuse_the_same_write_while_the_handle_takes_it(tmp_path):
    """§17.1's gate closes the fold and raw-projection lanes, not the handle.

    Stated precisely, because the difference is the reason this observer
    exists: on a **journal-backed** projection ``FalkorProjection.apply`` and
    ``FalkorProjection.query`` refuse, while ``proj.g.query`` still writes the
    cache — so the cache can hold a node the record never had, and only a count
    can reveal it.

    Both halves run on ONE journal-backed projection, and the write the handle
    accepts is read back through the shipped reader the CLI uses
    (``read_cache_view(proj.g)``) and then COUNTED. Asserting them on two
    different objects would let a change that closed ``proj.g.query`` as well
    keep this file green while the verb reported exit 2 forever.
    """
    from tests._embedded import fresh_embedded_proj

    view = _record_view()
    with fresh_embedded_proj(tmp_path) as proj:
        proj._bind_journal(str(tmp_path / "events.jsonl"))
        try:
            with pytest.raises(DirectCacheWriteRefused):
                proj.apply({"type": "PointAdded", "id": "p-9189-fold"})
            with pytest.raises(DirectCacheWriteRefused):
                proj.query("CREATE (:Point {id:'p-9189-lane'})")

            # The READ lane the CLI drives is not what the gate closes: the
            # shipped reader walks the same guarded handle.
            _project(view, proj.g)
            assert rr.read_cache_view(proj.g)["Point:p-9189-1"]["content"] == "alpha"

            # ...and the raw handle STILL accepts an unjournalled write.
            proj.g.query("CREATE (:Point {id:'p-9189-direct'})")

            result = rr.reconcile_record_and_cache(
                rr.record_key_map(view),
                rr.read_cache_view(proj.g),
                declaration=read_declaration(proj.g),
            )
        finally:
            proj._bind_journal(None)

    assert result.report.extra_in_cache == 1, result.report.render()
    assert result.verdict() == rr.VERDICT_DIVERGENT


# ── 3. residency is not divergence (§17.3) ────────────────────────────────


def test_a_partial_declaration_counts_unwritten_keys_as_residency(cache_graph):
    """A bounded projection's unwritten keys are `not-hot`, never a lag."""
    view = _record_view()
    _project(view, cache_graph, limit=1)

    result = _run(view, cache_graph)

    assert result.declaration.coverage == COVERAGE_PARTIAL
    assert result.not_hot == 1
    assert result.report.missing_in_cache == 0, (
        "a residency miss was counted as a projection lag")
    assert result.report.checked == 1
    assert result.verdict() == rr.VERDICT_CLEAN


def test_a_complete_declaration_still_counts_a_missing_key_as_divergence(
        cache_graph):
    """The partition must not become a way to excuse a real gap."""
    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("MATCH (n:Point {id:'p-9189-2'}) DETACH DELETE n")

    result = _run(view, cache_graph)

    assert result.declaration.coverage == COVERAGE_COMPLETE
    assert result.not_hot == 0
    assert result.report.missing_in_cache == 1
    assert result.verdict() == rr.VERDICT_DIVERGENT


def test_an_undeclared_side_treats_a_missing_key_as_divergence(cache_graph):
    """`declaration=None` is the §2.0 default (complete), not "no opinion"."""
    view = _record_view()
    _project(view, cache_graph)

    result = rr.reconcile_record_and_cache(
        rr.record_key_map(view),
        {"Point:p-9189-1": {"id": "p-9189-1", "content": "alpha",
                            "status": "live"}},
    )

    assert result.not_hot == 0
    assert result.report.missing_in_cache == 1


# ── 4. refusals, and "nothing compared" is not "clean" ────────────────────


def test_an_unreadable_cache_side_refuses():
    class _Broken:
        def query(self, *a, **k):
            raise RuntimeError("falkordb is down")

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(_Broken(), labels=("Point",))
    assert "UNKNOWN, not zero" in str(excinfo.value)


def test_a_cache_node_with_no_contract_key_refuses():
    """Skipping it would UNDERCOUNT `extra_in_cache` — the second-writer count."""
    class _Keyless:
        class _Result:
            result_set: ClassVar[list] = [[{"content": "no id here"}]]

        def query(self, *a, **k):
            return self._Result()

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(_Keyless(), labels=("Point",))
    assert "contract key" in str(excinfo.value)


def test_a_label_outside_the_record_placement_is_refused(cache_graph):
    with pytest.raises(ValueError):
        rr.read_cache_view(cache_graph, labels=("Point", ") DETACH DELETE n //"))


def test_two_cache_nodes_at_one_coordinate_refuse(cache_graph):
    """Collapsing a duplicate would report `extra_in_cache=0` over a real node.

    The writer ``MERGE``s on the key and the graph has no uniqueness
    constraint, so a second writer's ``CREATE`` at an EXISTING id is reachable
    — and it is the one shape that leaves the two key SETS equal. Pre-fix the
    reader kept the last node and the run read clean; a new id (the test above)
    cannot discriminate this case.
    """
    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("CREATE (:Point {id:'p-9189-1', content:'impostor'})")

    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.read_cache_view(cache_graph, labels=("Point",))

    message = str(excinfo.value)
    assert "Point:p-9189-1" in message
    assert "UNKNOWN, not zero" in message


def test_a_vacuous_comparison_refuses():
    """Placeholder maps make `field_mismatches` structurally zero (#8609)."""
    with pytest.raises(ReconciliationUnavailable) as excinfo:
        rr.reconcile_record_and_cache(
            {"Point:p1": {}, "Point:p2": {}}, {"Point:p1": {}, "Point:p2": {}})
    assert "EMPTY property maps" in str(excinfo.value)


def test_nothing_compared_is_unanswerable_never_clean():
    result = rr.reconcile_record_and_cache({}, {})

    assert result.checked == 0
    assert result.verdict() == rr.VERDICT_UNANSWERABLE
    assert rr.exit_code_for(result) == rr.EXIT_UNAVAILABLE


def test_the_result_carries_its_counts_and_verdict():
    result = rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}}, {"Point:p1": {"id": "p1"}})
    payload = result.as_dict()

    assert payload["verdict"] == rr.VERDICT_CLEAN
    assert payload["checked"] == 1
    assert payload["not_hot"] == 0
    assert payload["declaration_coverage"] == COVERAGE_COMPLETE
    assert payload["divergent"] == 0


def test_a_partial_declaration_reports_its_coverage():
    declaration = ResidencyDeclaration(coverage=COVERAGE_PARTIAL,
                                       hot_keys=frozenset({"Point:p1"}))
    result = rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}, "Point:p2": {"id": "p2"}},
        {"Point:p1": {"id": "p1"}},
        declaration=declaration,
    )

    assert result.not_hot == 1
    assert result.report.missing_in_cache == 0
    assert result.as_dict()["declaration_coverage"] == COVERAGE_PARTIAL


# ── 5. the trigger exists in production ───────────────────────────────────


#: Never descend into these while looking for importers: vendored, cached,
#: and OTHER WORKTREES' checkouts of the same repo (a lane's sibling worktree is
#: not this tree's production code).
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", ".worktrees", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", "build", "dist",
    # The pin below asserts every importer is OUTSIDE these; pruning them from
    # the walk is what keeps it fast, and the assertion keeps the intent
    # visible (a test/probe importer can never be what satisfies the gate).
    "tests", "docs",
})


def _production_importers(module: str) -> set[Path]:
    """Every non-test, non-docs file that imports ``module`` (AST, not grep).

    ``tests/`` and ``docs/`` are PRUNED rather than filtered: the question this
    answers is *"is a probe or a test what wires this?"* (#9188's indicator 3,
    the same question for the record write), so a hit can only come from
    production code.

    Four spellings count, because all four are real imports of the same module:
    ``import tortoise.record_reconcile`` · ``from tortoise.record_reconcile
    import x`` · ``from .record_reconcile import x`` (the intra-package form the
    CLI uses) · ``from tortoise import record_reconcile``.
    """
    leaf = module.rsplit(".", 1)[1]
    importers: set[Path] = set()
    for dirpath, dirnames, filenames in os.walk(_REPO_ROOT):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for filename in sorted(filenames):
            if not filename.endswith(".py"):
                continue
            path = Path(dirpath) / filename
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            # Cheap, SOUND pre-filter: every import spelling of a module
            # contains its leaf name as a literal, so a file without the string
            # cannot import it (a false POSITIVE only costs an `ast.parse`, and
            # a comment hit is discarded by the AST pass below). Without it,
            # parsing the two 10k+ line production files dominates the suite.
            if leaf not in text:
                continue
            try:
                with warnings.catch_warnings():
                    # A dependency's file with an invalid escape is not this
                    # pin's subject; parsing is all it needs to survive.
                    warnings.simplefilter("ignore", SyntaxWarning)
                    tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            if _imports_module(tree, module, leaf):
                importers.add(path)
    return importers


def _imports_module(tree: ast.AST, module: str, leaf: str) -> bool:
    """Does this module-level AST import `module` by any of the four spellings?"""
    parent = module.rsplit(".", 1)[0]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(
                alias.name == module for alias in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            if node.module == module:
                return True
            if node.level and node.module == leaf:
                return True
            if node.module == parent and any(
                    alias.name == leaf for alias in node.names):
                return True
    return False


@functools.cache
def _cached_production_importers(module: str) -> frozenset[str]:
    """The walk is repo-wide; do it ONCE for every pin that needs it."""
    return frozenset(
        str(p.relative_to(_REPO_ROOT))
        for p in _production_importers(module))


def test_the_reconciler_has_a_non_test_non_docs_caller():
    importers = sorted(_cached_production_importers(_TRIGGER_MODULE))

    assert importers, (
        f"no production importer of {_TRIGGER_MODULE} — the observer is again "
        f"reachable only from tests and evidence probes")
    # (`tests/` and `docs/` are PRUNED from the walk in `_production_importers`,
    # so an `all(not r.startswith(("tests/", "docs/")))` here could never fail.
    # The discriminating assertion is the non-empty one above; the CALL-level
    # pin is `test_the_verb_CALLS_the_composition_not_merely_imports_it`.)


def test_the_trigger_path_is_the_cli_verb():
    """The chosen trigger is the operator-invoked sweep, wired at the CLI."""
    assert "tortoise/__main__.py" in _cached_production_importers(_TRIGGER_MODULE)


def test_the_cli_verb_refuses_without_a_record_dsn(monkeypatch, capsys):
    """A scheduling signal that cannot read the record is NOT a clean run."""
    from tortoise import __main__ as cli

    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--org", "o", "--graph", "g"])

    assert rc == rr.EXIT_UNAVAILABLE
    assert "UNKNOWN, not zero" in capsys.readouterr().err


def test_a_malformed_record_dsn_is_a_MISCONFIGURATION_not_an_outage(capsys):
    """The record module OWNS the arming surface; this verb must not re-spell it.

    `tortoise/record_writer.record_dsn` exists to draw exactly this
    distinction: a value outside `postgresql://`/`postgres://` is a
    *misconfiguration*, never "the record is unreadable" (an outage). Reading
    the DSN out of the environment here instead (`args.record_dsn or
    os.environ.get("TORTOISE_RECORD_DSN")`) dropped that check, so the operator
    was told an outage had happened and the value was handed to the driver.

    Pre-fix this fails: the message is either `psycopg2 is not installed` or
    `record is unreadable`, never the fingerprint-only misconfiguration text.
    """
    from tortoise import __main__ as cli

    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "mysql://user:pw@db.example/record",
                   "--org", "o", "--graph", "g"])
    err = capsys.readouterr().err

    assert rc == rr.EXIT_UNAVAILABLE, err
    assert "must start with postgresql:// or postgres://" in err, err
    assert "misconfiguration, not an unarmed record" in err, err
    assert "record is unreadable" not in err, err
    # Secret hygiene: `record_dsn` fingerprints the value, so the DSN itself
    # must never reach the operator's terminal.
    assert "mysql://" not in err, err
    assert "pw@db.example" not in err, err


def test_the_verb_reads_the_record_env_name_the_writer_declares(
        monkeypatch, capsys):
    """A rename of `record_writer.RECORD_DSN_ENV` must not silently disarm.

    The verb's own literal `os.environ.get("TORTOISE_RECORD_DSN")` was a SECOND
    spelling of the resolution, so renaming the constant left `RecordWriter`
    armed and this verb permanently unarmed (exit 2 forever, no verdict).
    """
    from tortoise import __main__ as cli
    from tortoise import record_writer

    monkeypatch.setattr(record_writer, "RECORD_DSN_ENV",
                        "TORTOISE_RENAMED_RECORD_DSN")
    monkeypatch.delenv("TORTOISE_RECORD_DSN", raising=False)
    monkeypatch.setenv("TORTOISE_RENAMED_RECORD_DSN", "mysql://renamed/record")

    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--org", "o", "--graph", "g"])
    err = capsys.readouterr().err

    assert rc == rr.EXIT_UNAVAILABLE, err
    # The renamed key IS the arming surface: it was read (and refused as
    # malformed), rather than reported as "no record DSN".
    assert "must start with postgresql://" in err, err
    assert "no record DSN" not in err, err


def test_a_usage_error_exits_2_not_1(capsys):
    """The verb's documented exit contract, pinned where argparse decides it.

    ``--graph`` is required, so argparse's own ``error()`` answers ``2`` — the
    SAME code as ``EXIT_UNAVAILABLE`` — and ``main()`` re-raises that
    ``SystemExit``. The docstring used to promise ``1`` for usage, which no
    path returns; a scheduler keying on the documented code would read a
    malformed cron line as "the record is unreachable".
    """
    from tortoise import __main__ as cli

    with pytest.raises(SystemExit) as excinfo:
        cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                  "--org", "o"])

    assert excinfo.value.code == 2
    _cmd = cli._cmd_record_reconcile
    assert "``1`` is never returned" in (_cmd.__doc__ or "")


# ── 6. the verb does not merely IMPORT the observer ───────────────────────
#
# The import-graph pins above answer "is there a production caller?" — and an
# import is not a call. The mutation they cannot see is #9189's own defect one
# level up: keep the `from tortoise.record_reconcile import (…)` line and the
# DSN guard, replace the read/compare body with `return EXIT_CLEAN`. The verb
# then observes nothing, prints nothing, and reports **exit 0 = clean** to a
# scheduler while every import-level pin stays green. Two pins close it: this
# AST one (the composition must be CALLED) and the live drive below (the
# verdict must reach the exit code).

#: The names the verb's body must CALL. `exit_code_for` is in the set on
#: purpose: a constant returned from the verb is the mutation above.
_VERB_MUST_CALL = frozenset({
    "scan_cache_view",
    "read_record_view",
    "reconcile_record_and_cache",
    "record_key_map",
    "log_result",
    "exit_code_for",
})


def _called_names(func_node: ast.AST) -> set[str]:
    """Every name invoked by a ``Call`` inside ``func_node`` (bare or dotted)."""
    called: set[str] = set()
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            called.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def test_the_verb_CALLS_the_composition_not_merely_imports_it():
    """An import satisfies reachability, not observation."""
    tree = ast.parse((_REPO_ROOT / "tortoise" / "__main__.py").read_text(
        encoding="utf-8"))
    verb = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_cmd_record_reconcile"),
        None)
    assert verb is not None, (
        "`_cmd_record_reconcile` is gone — the trigger has no CLI home")

    missing = sorted(_VERB_MUST_CALL - _called_names(verb))
    assert not missing, (
        f"`_cmd_record_reconcile` never CALLS {missing}: it imports the "
        f"observer and does not run it, so the verb would report a verdict it "
        f"did not compute")


class _FakeConn:
    """Stands in for the record connection — the VERB owns closing it."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeProjection:
    """A projection over a REAL cache graph, with a close() the verb must call."""

    def __init__(self, graph) -> None:
        self.g = graph
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _drive_the_verb(monkeypatch, graph, view, *, extra_args=(), graph_wrapper=None):
    """Run ``python -m tortoise record-reconcile`` over a REAL cache graph.

    Only the two HANDLES are faked (the record connection and the projection's
    ``from_uri``): the cache reader, the record key map, the comparison and the
    verdict→exit-code mapping are the shipped ones, so what this asserts is
    that the verb really composes them and really reports the result.

    ``graph_wrapper`` lets a caller observe the READ the verb makes (the
    timeout knob's test); ``extra_args`` appends CLI flags.
    """
    from tortoise import __main__ as cli
    from tortoise.projection import FalkorProjection

    conn = _FakeConn()
    connect_kwargs: dict = {}

    def _connect(dsn, **kw):
        connect_kwargs.update(kw)
        return conn

    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2.connect = _connect                   # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setattr(
        cp, "read_record_view",
        lambda conn, *, org_id, graph_id: view, raising=True)

    projection = _FakeProjection(graph_wrapper if graph_wrapper is not None
                                 else graph)
    monkeypatch.setattr(
        FalkorProjection, "from_uri",
        staticmethod(lambda uri, graph_name=None: projection))

    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "o", "--graph", "g",
                   "--cache-graph", "org_o_g", *extra_args])
    return rc, conn, projection, connect_kwargs


def test_the_cli_verb_COUNTS_a_divergent_cache(monkeypatch, cache_graph, capsys):
    """The verdict the verb reports is the one it COMPUTED (exit 3 + the count).

    Pre-fix this test fails if the body returns a constant: `return EXIT_CLEAN`
    reports 0 over a cache holding a node the record does not have — a
    permanently green observer with a caller, which is the defect #9189 exists
    to close.
    """
    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("CREATE (:Point {id:'p-9189-cli-direct'})")

    rc, conn, projection, _ = _drive_the_verb(monkeypatch, cache_graph, view)
    printed = capsys.readouterr()

    assert rc == rr.EXIT_DIVERGENT, printed.out + printed.err
    assert "extra_in_cache=1" in printed.out, printed.out
    assert "verdict=divergent" in printed.out, printed.out
    assert conn.closed and projection.closed, "the verb must close both handles"


def test_the_cli_verb_reports_clean_only_when_it_compared(
        monkeypatch, cache_graph, capsys):
    """The positive control: a faithful cache exits 0 — with a nonzero denominator.

    Pinned beside the divergent drive so `0` can only come from a comparison
    that ran (the vacuity refusal makes an empty comparison exit 2, never 0).
    """
    view = _record_view()
    _project(view, cache_graph)

    rc, _, _, _ = _drive_the_verb(monkeypatch, cache_graph, view)
    printed = capsys.readouterr()

    assert rc == rr.EXIT_CLEAN, printed.out + printed.err
    assert "0 divergent of 2 checked" in printed.out, printed.out


# ── 7. the verb's OPERATOR contract (#9189 review findings) ───────────────
#
# The exit code is the scheduler's only signal, so everything that decides
# WHICH verdict is produced has to be pinned as tightly as the verdict itself:
# the cache graph it reads, the bounds it puts on both stores, the machine
# output a dashboard reads, and the sentence an operator reads. Each test below
# covers a way the trigger could produce a CONFIDENT WRONG number or state an
# equality it did not establish.


class _RecordingGraph:
    """A real cache graph that records the READ options the verb passed."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.read_kwargs: list[dict] = []

    def query(self, *args, **kwargs):
        self.read_kwargs.append(dict(kwargs))
        return self._inner.query(*args, **kwargs)


def test_the_verb_refuses_without_a_cache_graph(monkeypatch, capsys):
    """A derived cache-graph name is a COHERENT CACHE reported as divergent.

    The record's ``--graph`` is a ``graph_id`` scope COLUMN; the cache graph is
    named ``org_<org_id>`` or ``org_<org_id>_<graph_id>``. FalkorDB opens a
    graph on first touch, so the old ``--cache-graph`` default (``--graph``)
    silently read an EMPTY graph: every record key became ``missing_in_cache``
    and a coherent cache exited ``3``. A refusal that names both conventions is
    the fail-closed direction.
    """
    from tortoise import __main__ as cli

    fake = types.ModuleType("psycopg2")
    fake.connect = lambda dsn, **kw: _FakeConn()        # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "psycopg2", fake)

    rc = cli.main(["record-reconcile", "--db", "docker://:x@localhost:6379/g",
                   "--record-dsn", "postgresql://fake/record",
                   "--org", "acme", "--graph", "g1"])
    err = capsys.readouterr().err

    assert rc == rr.EXIT_UNAVAILABLE, err
    assert "org_<org_id>" in err and "org_<org_id>_<graph_id>" in err, err
    assert "UNKNOWN, not zero" in err, err


def test_the_verb_bounds_BOTH_stores(monkeypatch, cache_graph):
    """A wedged store must not hang a scheduled sweep — on either side.

    The cache read was bounded and the record was not, so a wedged Postgres
    blocked the sweep forever. Both bounds are asserted here because the pair
    is the contract: a session ``statement_timeout`` (the server aborts) AND a
    connect timeout (the handshake cannot block either).
    """
    view = _record_view()
    _project(view, cache_graph)

    _, _, _, connect_kwargs = _drive_the_verb(monkeypatch, cache_graph, view)

    assert connect_kwargs.get("connect_timeout") == rr.RECORD_CONNECT_TIMEOUT_S
    options = connect_kwargs.get("options", "")
    assert f"statement_timeout={rr.RECORD_STATEMENT_TIMEOUT_MS}" in options, options


def test_the_verb_passes_the_cache_timeout_knob(monkeypatch, cache_graph, capsys):
    """`--timeout-ms` must reach the reader that applies it, not just the parser.

    The bound was a constant with no knob, and the reader's docstring called the
    read unbounded — a large cache would refuse (exit 2) forever on the
    deployments that need the observer most, with nothing an operator could do.
    """
    view = _record_view()
    _project(view, cache_graph)
    recorder = _RecordingGraph(cache_graph)

    rc, _, _, _ = _drive_the_verb(monkeypatch, cache_graph, view,
                                  graph_wrapper=recorder,
                                  extra_args=("--timeout-ms", "12345"))
    printed = capsys.readouterr()

    assert rc == rr.EXIT_CLEAN, printed.out + printed.err
    assert recorder.read_kwargs, "the verb never read the cache"
    # One bounded read per record label — the residency declaration's own read
    # keeps its own (sibling) bound, so the assertion is on the COUNT of reads
    # that carried this knob, not on the set of every timeout seen.
    bounded = [kw for kw in recorder.read_kwargs if kw.get("timeout") == 12345]
    assert len(bounded) == len(cp.NODE_TABLES), (
        f"{len(bounded)} of {len(recorder.read_kwargs)} cache reads carried "
        f"--timeout-ms: {recorder.read_kwargs}")


def test_a_non_positive_timeout_is_refused(monkeypatch, cache_graph, capsys):
    """0 is not a bound — the driver reads it as "wait forever".

    The flag exists to close an UNBOUNDED read. Accepting `--timeout-ms 0` would
    re-open exactly that while looking bounded, so a non-positive value refuses
    before any store is touched.
    """
    view = _record_view()
    _project(view, cache_graph)

    rc, _, _, _ = _drive_the_verb(monkeypatch, cache_graph, view,
                                  extra_args=("--timeout-ms", "0"))
    err = capsys.readouterr().err

    assert rc == rr.EXIT_UNAVAILABLE, err
    assert "must be positive" in err, err
    assert "UNKNOWN, not zero" in err, err


def test_the_verb_prints_the_json_a_dashboard_reads(monkeypatch, cache_graph,
                                                   capsys):
    """`--json` is a landing place for the number — it must be driven, not narrated.

    No test in this file passed `--json`, so the branch could be deleted with
    every pin green while §17.2's third destination ("a dashboard can graph it
    instead of grepping a log") quietly stopped existing.
    """
    import json

    view = _record_view()
    _project(view, cache_graph)
    cache_graph.query("CREATE (:Point {id:'p-9189-json-direct'})")

    rc, _, _, _ = _drive_the_verb(monkeypatch, cache_graph, view,
                                  extra_args=("--json",))
    printed = capsys.readouterr()

    assert rc == rr.EXIT_DIVERGENT, printed.err
    payload = json.loads(printed.out)
    assert payload["verdict"] == rr.VERDICT_DIVERGENT
    assert payload["extra_in_cache"] == 1
    assert payload["checked"] == 3
    assert payload["declaration_coverage"] == COVERAGE_COMPLETE


def test_the_verb_does_not_claim_equality_for_keys_it_did_not_compare(
        monkeypatch, cache_graph, capsys):
    """`not_hot > 0` means a `partial` declaration REMOVED keys from the truth side.

    The run then established equality over the compared keys only, and the old
    `✓ record == cache … (N keys checked)` sentence stated an equality the run
    did not establish — the §17.2 class ("a check that cannot see its evidence
    must not report success") one line above the counts.
    """
    view = _record_view()
    _project(view, cache_graph, limit=1)

    rc, _, _, _ = _drive_the_verb(monkeypatch, cache_graph, view)
    out = capsys.readouterr().out

    assert rc == rr.EXIT_CLEAN, out
    assert "1 not-hot key(s) were NOT compared" in out, out
    assert "✓ record == cache for" not in out, out


def test_log_result_severity_follows_the_verdict():
    """Three verdicts, three levels — an `unanswerable` run must not be quieter.

    The module docstring promised "WARNING when anything is divergent and INFO
    otherwise", which logged a run that compared NOTHING (exit 2) at WARNING —
    indistinguishable from a clean run at INFO for anyone filtering the log.
    """
    import logging

    class _Rec:
        def __init__(self) -> None:
            self.levels: list[int] = []

        def log(self, level, msg, *args) -> None:
            self.levels.append(level)

    rec = _Rec()
    rr.log_result(rr.reconcile_record_and_cache({}, {}), rec)
    rr.log_result(rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}}, {"Point:p1": {"id": "p1"}}), rec)
    rr.log_result(rr.reconcile_record_and_cache(
        {"Point:p1": {"id": "p1"}}, {}), rec)

    assert rec.levels == [logging.ERROR, logging.INFO, logging.WARNING]


def test_the_module_records_the_labels_this_trigger_cannot_see():
    """`extra_in_cache` is not a whole-graph census — say so where a lane reads it.

    The reader walks the six record `NODE_TABLES` labels, so an unjournalled node
    under a label of a second writer's own (or the cache's `:Meta` bookkeeping
    label) is invisible to the count. Counting or refusing unknown labels is not
    an option (`:Meta` is always present, and the residency declaration lives
    there), so the LIMITATION is recorded on the module that claims the count —
    the alternative is a number read as more than it measures.
    """
    doc = rr.__doc__ or ""
    assert "label OUTSIDE the record placement" in doc, doc
    assert "extra_in_cache" in doc


def test_a_falsy_marker_point_reports_the_WRITER_half_as_divergence(cache_graph):
    """`'false'` end to end: the CHECK is fixed, the WRITER half is #9184's.

    `cache_projection.node_statement` decides the storage form with
    ``stored_form.is_verbatim``, whose truthiness reads the string `'false'` as
    verbatim — so a faithful projection of that record stores the marker as
    ``True`` (and the vector un-narrowed). The comparison then reports the
    MARKER as one divergence, forever, until #9184's writer half lands. Pinning
    the real output keeps the residue visible: the hand-built cache in
    `tests/test_reconciliation.py` is what a CORRECTED writer will produce, and
    a test that only ever feeds that cache certifies a fix the shipped writer
    cannot reach.

    Pre-fix (with `is_verbatim`'s truthiness in the CHECK's vector rule too) the
    vector ALSO diverged and ``form_normalized`` was 0 — so this asserts the
    split precisely: the vector agrees, the marker is the only mismatch.
    """
    view = cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-falsy",
                      props={"id": "p-9189-falsy", "content": "alpha",
                             "embedding": [0.1, 0.2],
                             "embedding_verbatim": "false"}),
    ))
    _project(view, cache_graph)

    cache = rr.read_cache_view(cache_graph)["Point:p-9189-falsy"]
    assert cache["embedding_verbatim"] is True, (
        "the shipped writer stores the falsy spelling truthily (#9184's writer "
        "half) — if that changed, this pin has served its purpose: the "
        "comparator's falsy agreement is now reachable end to end")
    assert cache["embedding"] == [0.1, 0.2], (
        "the writer stores the vector VERBATIM for the falsy spelling")

    result = _run(view, cache_graph)

    assert result.report.field_mismatches == 1, result.report.render()
    assert result.report.divergent_keys == ("Point:p-9189-falsy",)
    assert "form_normalized" not in result.report.render()
    assert result.verdict() == rr.VERDICT_DIVERGENT


# ── 8. a JOURNAL-projected cache — the shape production actually runs ─────
#
# Every clean-path assertion ABOVE builds the cache with the RECORD-sourced
# writer (`cp.project_records`), i.e. a cache whose field set was built from the
# record and therefore agrees with it by construction. Production runs the
# OTHER writer: the journal fold (`FalkorProjection.rebuild_all`), which also
# writes the graph's own recomputed scalars — `content_hash`, `ep_dirty`,
# `ep_dirty_at`, and once EP has run `ep_alpha`/`ep_beta`/`confidence`/… .
# Those are the CACHE's by decision (§17.5) and the record schema REFUSES them
# by name (`chk_point_props_no_derived`), so the record can never hold them —
# and a presence-aware field comparison counted every one as a mismatch. On
# that population the clean verdict was unreachable: `record-reconcile` would
# have reported `divergent`, exit 3, forever, on a perfectly healthy pair
# (#9196 review, P1 — "a signal indistinguishable from noise is not
# observability", the failure this module exists to prevent).

#: The point the journal below creates.
_JOURNAL_POINT = "p-9189-journal"

#: The RECORD's own properties for it — written out by hand rather than derived
#: from the graph, so the record side is not a filtered copy of the cache (a
#: fixture built that way agrees by construction and would prove nothing).
_JOURNAL_RECORD_PROPS = {
    "id": _JOURNAL_POINT,
    "content": "alpha",
    "pointKind": "statement",
    "is_operator": False,
    "status": "live",
    "createdAt": "2026-10-19T00:00:00+00:00",
    "updatedAt": "2026-10-19T00:00:00+00:00",
}


def _journal_point_added() -> dict:
    """One ``PointAdded`` record in the SDK's own journal spelling."""
    return {
        "event_id": "e-9189-journal-1",
        "ts": "2026-10-19T00:00:00+00:00",
        "type": "PointAdded",
        "initiated_by": "sdk",
        "projection_version": 2,
        "point": {**_JOURNAL_RECORD_PROPS, "ep_dirty": True,
                  "ep_dirty_at": 1, "embedding": None},
    }


def _write_journal(tmp_path) -> Path:
    events = tmp_path / "events"
    events.mkdir()
    (events / "events.jsonl").write_text(
        json.dumps(_journal_point_added()) + "\n", encoding="utf-8")
    return events


def _journal_record_view(graph_props: dict | None = None) -> cp.RecordView:
    """The record's view of the journal point.

    ``graph_props`` supplies the ONE field the fold re-stamps rather than
    carries (see :func:`_replay_owned_updated_at`); every other record field is
    written out by hand, so the record side is not a filtered copy of the cache.
    """
    props = dict(_JOURNAL_RECORD_PROPS)
    if graph_props is not None:
        props["updatedAt"] = _replay_owned_updated_at(graph_props)
    return cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key=_JOURNAL_POINT, props=props),
    ))


def _replay_owned_updated_at(graph_props: dict) -> str:
    """The cache's ``updatedAt``, which the fold STAMPS with replay-now.

    ``_upsert_point_props`` writes ``n.updatedAt`` unconditionally and the
    comment on the replay tail says so in as many words (*"``updatedAt`` is
    replay-owned"*): a journal rebuild does not carry the record's value for it.
    That is a genuine cache-vs-record difference of a RECORD field — reported by
    the comparison, not swallowed — and it is outside this pin's subject (the
    properties the record CANNOT hold). The assertion keeps the provenance
    honest: if the fold ever stops re-stamping, this helper's premise is gone
    and the test says so rather than quietly comparing nothing.
    """
    stamped = graph_props["updatedAt"]
    assert stamped != _JOURNAL_RECORD_PROPS["updatedAt"], (
        "the fold no longer re-stamps updatedAt — drop this override and give "
        "the record view the journalled value")
    return stamped


@pytest.mark.embedded_only
def test_a_JOURNAL_projected_cache_reconciles_clean(tmp_path):
    """§17.5's cache-owned scalars are NOT divergence (#9196 review, P1).

    The cache here is built by the SHIPPED fold from a real journal, so its
    property set is what the fold produces — not what a test chose. The
    assertions below are the two halves of the finding:

    * PRE-FIX (every property compared) the same pair reports one
      ``field_mismatches`` per cache-owned property and is permanently
      divergent — so the fix is not vacuous;
    * the shipped path reports zero mismatches, zero divergence, exit 0, AND
      names the properties it declined to judge (``graph_only``), so they are
      not silently swallowed either.
    """
    from tests._embedded import fresh_embedded_proj

    events = _write_journal(tmp_path)
    with fresh_embedded_proj(tmp_path) as proj:
        proj.rebuild_all(str(events), confirm_destructive=True)

        scan = rr.scan_cache_view(proj.g, labels=("Point",))
        key = f"Point:{_JOURNAL_POINT}"
        raw = scan.view[key]
        cache_owned = set(raw) - scan.placed[key]
        assert {"content_hash", "ep_dirty", "ep_dirty_at"} <= cache_owned, (
            f"the fold no longer writes cache-owned scalars — this pin has "
            f"served its purpose: {sorted(raw)}")

        truth = rr.record_key_map(_journal_record_view(raw))

        # The PRE-FIX comparison — every property, presence-aware.
        pre_fix = reconcile_views(truth, scan.view)
        assert pre_fix.field_mismatches == len(cache_owned), pre_fix.render()
        assert pre_fix.divergent == 1, pre_fix.render()

        result = rr.reconcile_record_and_cache(
            truth, scan.view, placed=scan.placed)

        assert result.report.checked == 1
        assert result.report.field_mismatches == 0, result.report.render()
        assert result.report.divergent == 0, result.report.render()
        assert dict(result.graph_only) == {name: 1 for name in cache_owned}
        assert result.graph_only_total == len(cache_owned)
        assert result.verdict() == rr.VERDICT_CLEAN
        assert rr.exit_code_for(result) == rr.EXIT_CLEAN
        assert "graph_only=" in rr.log_result(result)


@pytest.mark.embedded_only
def test_the_VERB_exits_0_on_a_JOURNAL_projected_cache(
        monkeypatch, tmp_path, capsys):
    """The operator signal on the production shape: exit 0, and it says why.

    Pre-fix this drive exits ``3`` — the fold's own cache-owned properties are
    counted as disagreements — so a scheduler would alert on a healthy pair,
    permanently, which is the same outcome as having no observer at all.
    """
    from tests._embedded import fresh_embedded_proj

    events = _write_journal(tmp_path)
    with fresh_embedded_proj(tmp_path) as proj:
        proj.rebuild_all(str(events), confirm_destructive=True)
        graph_props = rr.scan_cache_view(
            proj.g, labels=("Point",)).view[f"Point:{_JOURNAL_POINT}"]
        rc, conn, projection, _ = _drive_the_verb(
            monkeypatch, proj.g, _journal_record_view(graph_props))

    printed = capsys.readouterr()
    assert rc == rr.EXIT_CLEAN, printed.out + printed.err
    assert "0 divergent of 1 checked" in printed.out, printed.out
    assert "field_mismatches=0" in printed.out, printed.out
    assert "graph_only=3" in printed.out, printed.out
    assert conn.closed and projection.closed


def test_a_record_TAIL_property_is_still_compared(cache_graph):
    """The exclusion is narrow: outside the placement table AND absent from the record.

    The record's ``props`` tail is an OPEN set — a caller-supplied property the
    projection writes verbatim — so a blanket "compare only placed names"
    filter would drop it from the cache side and report a faithful projection as
    divergent. Here the tail property is present on both sides (clean) and then
    missing from a freshly projected cache (divergent), which is exactly the
    signal that must survive the cache-owned partition.
    """
    with_tail = cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-tail",
                      props={"id": "p-9189-tail", "content": "alpha",
                             "custom_note": "record side"}),
    ))
    without_tail = cp.RecordView(nodes=(
        cp.RecordNode(label="Point", key="p-9189-tail",
                      props={"id": "p-9189-tail", "content": "alpha"}),
    ))
    _project(with_tail, cache_graph)

    scan = rr.scan_cache_view(cache_graph, labels=("Point",))
    assert "custom_note" in scan.view["Point:p-9189-tail"]
    assert "custom_note" not in scan.placed["Point:p-9189-tail"], (
        "the placement table now names the tail property — if that is "
        "deliberate, this pin needs a different open-set name")

    clean = rr.reconcile_record_and_cache(
        rr.record_key_map(with_tail), scan.view, placed=scan.placed)
    assert clean.report.field_mismatches == 0, clean.report.render()
    assert clean.verdict() == rr.VERDICT_CLEAN
    assert clean.graph_only == ()

    cache_graph.query("MATCH (n:Point {id:'p-9189-tail'}) DETACH DELETE n")
    _project(without_tail, cache_graph)
    scan = rr.scan_cache_view(cache_graph, labels=("Point",))
    dropped = rr.reconcile_record_and_cache(
        rr.record_key_map(with_tail), scan.view, placed=scan.placed)

    assert dropped.report.field_mismatches == 1, dropped.report.render()
    assert dropped.report.divergent == 1
    assert dropped.graph_only == (), (
        "a record-held property was classified cache-owned — the partition "
        "swallowed real divergence")
    assert dropped.verdict() == rr.VERDICT_DIVERGENT
