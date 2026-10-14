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
