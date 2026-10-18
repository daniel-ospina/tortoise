"""#7950 capstone — the e2e chain probe's RECONCILIATION READERS are non-vacuous.

The probe (``docs/evidence/7950-capstone/probes/e2e_chain_probe.py``) feeds
``reconciliation.reconcile`` a record reader and a cache reader. Both originally
returned ``{}`` for every key, which made ``field_mismatches`` STRUCTURALLY
zero: ``reconcile_views`` compares two per-key MAPPINGS field by field, and two
empty mappings always agree. The committed log's

    RECONCILE (after a record-sourced rebuild): record vs cache: 0 divergent of
    7 checked (missing_in_cache=0, extra_in_cache=0, field_mismatches=0)

therefore proved **key-set agreement only** — a second writer that MODIFIED an
existing node's property (rather than adding a node) was invisible, so the
evidence doc's *"the end-to-end run proves it on real data"* overstated what was
measured (PR #8609 review, finding 3).

The probe now maps the REAL property sets. These tests pin that contract
DB-free: if either reader regresses to placeholder maps, the field-mutation
assertion below goes red — which is exactly the failure the pre-fix code had.

Why a test for a probe: the probe is the ONLY artifact that runs the shipped
reconcile path against the shipped readers, and it needs live Postgres +
FalkorDB. The reader mapping itself is pure, so the non-vacuity half is pinned
here rather than left to a live run that CI cannot perform.
"""
from __future__ import annotations

import ast
import importlib.util as _ilu
from pathlib import Path

from tortoise.cache_projection import RecordNode, RecordView
from tortoise.reconciliation import reconcile_views

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROBE = (_REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "probes"
          / "e2e_chain_probe.py")


def _load_probe():
    """Load the probe module by path.

    The ``docs/evidence/7950-capstone/`` directory is hyphenated, so the module
    is unreachable with an ``import`` statement — the same spec-loader pattern
    ``tests/test_e2e_live_reconcile_scope_parity.py`` uses for
    ``graph-scripts/``. The probe keeps ``psycopg2`` (the optional ``postgres``
    extra) out of module scope on purpose, so importing it here needs no DB.
    """
    spec = _ilu.spec_from_file_location("capstone_e2e_chain_probe", str(_PROBE))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_PROBE_MODULE = _load_probe()


def _view() -> RecordView:
    return RecordView(nodes=(
        RecordNode(label="Point", key="p1",
                   props={"id": "p1", "text": "hello", "version": 3}),
        RecordNode(label="Source", key="https://x.example",
                   props={"url": "https://x.example"}),
    ))


# ── the record reader carries real property maps ──────────────────────────


def test_record_key_map_carries_the_actual_property_maps():
    mapped = _PROBE_MODULE.record_key_map(_view())
    assert mapped == {
        "Point:p1": {"id": "p1", "text": "hello", "version": 3},
        "Source:https://x.example": {"url": "https://x.example"},
    }


def test_record_key_map_values_are_never_placeholders():
    # the pre-fix defect, asserted directly: every value was `{}`
    assert all(_PROBE_MODULE.record_key_map(_view()).values())


# ── the cache reader carries real property maps ───────────────────────────


def test_cache_key_map_carries_the_actual_property_maps():
    mapped = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "hello", "version": 3}],
        "Source": [{"url": "https://x.example"}],
        "Session": [],  # a label with no rows contributes nothing
    })
    assert mapped == {
        "Point:p1": {"id": "p1", "text": "hello", "version": 3},
        "Source:https://x.example": {"url": "https://x.example"},
    }


def test_cache_key_map_skips_a_row_without_its_contract_key():
    # an unkeyed row cannot be addressed by `(label, key)` and is dropped,
    # rather than entering the comparison under a fabricated key
    assert _PROBE_MODULE.cache_key_map({"Point": [{"text": "no id"}]}) == {}


# ── the field comparison is therefore MEASURABLE ──────────────────────────


def test_a_property_value_change_is_a_field_mismatch():
    """The signature the pre-fix readers could not produce.

    A second writer that sets an EXISTING node's property (rather than adding a
    node) is a `field_mismatch`, not an `extra_in_cache` — with `{}`-valued maps
    both sides compare equal and this reads as agreement.
    """
    truth = _PROBE_MODULE.record_key_map(_view())
    cache = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "MUTATED", "version": 3}],
        "Source": [{"url": "https://x.example"}],
    })
    report = reconcile_views(truth, cache)
    assert report.field_mismatches == 1
    assert report.divergent == 1
    assert report.missing_in_cache == 0
    assert report.extra_in_cache == 0
    assert report.divergent_keys == ("Point:p1",)


def test_a_foreign_property_on_an_existing_node_is_a_field_mismatch():
    """The exact mutation the probe's step 5c performs, without a live store."""
    truth = _PROBE_MODULE.record_key_map(_view())
    cache = _PROBE_MODULE.cache_key_map({
        "Point": [{"id": "p1", "text": "hello", "version": 3,
                   "second_writer_marker": "direct-cache-write"}],
        "Source": [{"url": "https://x.example"}],
    })
    report = reconcile_views(truth, cache)
    assert report.field_mismatches == 1
    assert report.extra_in_cache == 0  # the KEY is on both sides — value-only


# ── the probes' hardcoded record-migration lists stay in step ─────────────

_PROBE_MIGRATION_LISTS = (
    # (file, constant) — the two LIVE lists (both are applied to a throwaway
    # Postgres) plus the chain probe's dead duplicate.
    (_REPO_ROOT / "docs" / "evidence" / "7950-capstone" / "probes"
     / "e2e_postgres_bootstrap.py", "MIGRATIONS"),
    (_REPO_ROOT / "docs" / "evidence" / "7983-cost-magnitudes" / "probes"
     / "record_read_cost.py", "MIGRATIONS"),
    (_PROBE, "RECORD_MIGRATIONS"),
)


def _migration_list(path: Path, name: str) -> list[str]:
    """A probe's hardcoded list, read with `ast` rather than by importing it.

    `e2e_postgres_bootstrap.py` and `record_read_cost.py` import `psycopg2` at
    module scope (the optional `postgres` extra), so importing them here would
    put a DB driver in a pin that must stay DB-free. `ast.literal_eval` reads the
    constant without executing a line of the module.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(getattr(target, "id", None) == name for target in node.targets):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"{path} no longer defines {name}")


def test_every_probe_applies_the_record_migrations_the_shipped_read_uses():
    """The probes cannot silently apply a schema the projection has moved past.

    Both live probes apply their hardcoded list to a throwaway Postgres and then
    drive the SHIPPED projection, whose `table_columns()` is read from
    `record_projection.RECORD_MIGRATIONS` **on disk**. A forward migration
    missing from a probe list therefore stands up a database older than the
    INSERT the projection emits: `public.point` without #2782's
    `amount_minor`/`currency`/… fails every money-bearing point INSERT with
    `UndefinedColumn`, and until it does the probe measures a record one
    migration behind — the "record poorer than the graph" failure the migration
    exists to close. #8976 review P3: the migration header enumerates the schema
    readers that must SEE these columns, and these copies were not carried
    through.

    Equality, not containment: a stale EXTRA entry is the same defect from the
    other side (a migration the shipped read does not use).
    """
    from tortoise.record_projection import RECORD_MIGRATIONS

    for path, name in _PROBE_MIGRATION_LISTS:
        assert _migration_list(path, name) == list(RECORD_MIGRATIONS), (
            f"{path.relative_to(_REPO_ROOT)}::{name} disagrees with "
            "record_projection.RECORD_MIGRATIONS — the probe would apply a "
            "different record schema than the projection writes against"
        )


def test_placeholder_maps_cannot_see_a_field_mutation():
    """The pre-fix shape, kept as the counter-example the fix is measured from.

    Two `{}`-valued maps agree field by field, so the same mutation that
    `test_a_property_value_change_is_a_field_mismatch` detects reads as zero
    divergence. This is why the probe's non-vacuity guard exists.
    """
    placeholder = {"Point:p1": {}, "Source:https://x.example": {}}
    report = reconcile_views(placeholder, dict(placeholder))
    assert report.field_mismatches == 0
    assert report.divergent == 0
