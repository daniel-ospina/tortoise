"""#13829 — the derived cache's cap binds at the CACHE FOLD, not at the write door.

THE DEFECT THIS PINS. ``graph_storage`` measures the DERIVED hot cache
(``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0, owner 2026-10-08: *"Postgres
is the system of record for every layer; FalkorDB is a derived hot cache"*, §3:
*"deleting FalkorDB loses nothing"*, *"the derived layer must be physically
DROPPABLE"*). It was compared at the **write door** — above
``tortoise/live_write.py``, the chokepoint that already tolerates a cache
projection which did not apply — so an over-cap cache refused the journal row and
the record projection: the thing being refused was the TRUTH, and the cap's own
subject (the cache) was not written at all. A tenant at 244 MB of cache against a
102 MB cache cap was refused a memory write while its RECORD dimension (the one
they purchased, pro: 1.3 GB) sat nearly empty.

WHAT IS PINNED, in the order the fix is built:

* the gate's **two arms** — a door that declares its write truth-carried
  (``truth_carried=True``) is SERVED and gets a named ``CacheFoldDenial``;
  every other door keeps today's ``QuotaExceededError`` (that arm is the
  protection and must never flip: on an unproven door the derived store may be
  the write's only durable copy, which is the one exception the standards name);
* the **chokepoint** — ``live_write.write`` consumes that denial, commits the
  journal and the record, and skips the cache fold, recording the outcome;
* the **declaration's narrowness** — only ``graph_storage`` declares
  ``derived_layer``, ``postgres_storage`` still gates the record doors, and NO
  dimension's ``resources`` door set moves (that is #13134's reviewed decision).

⚠️ What is NOT here, deliberately: any eviction, delete, census or licence
(#10976 is untouched — no byte is dropped, the cache stops growing), any
allowance NUMBER (``product/pricing.json`` is untouched) and any change to
``postgres_storage``'s gauge (the open seam on #13526).
"""
from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from tortoise import live_write
from tortoise.allowance_gate import (
    DIMENSIONS,
    AllowanceDimension,
    CacheFoldDenial,
    MeterReading,
    cache_fold_denial,
    clear_cache_fold_denial,
    enforce_allowances,
    reset_signal_state_for_tests,
)
from tortoise.quota import QuotaExceededError

ORG = "team-13829"


def _limits(**overrides) -> dict:
    """A resolved solo-tier limits dict, the shape ``quota`` hands the gate."""
    return {"org_id": ORG, "tier": "solo", "max_points": None,
            "max_api_keys": None, "max_sessions": None, "max_users": None,
            "max_graphs": None, **overrides}


def _meter(used: float):
    """A meter override (the gate's ``meters=`` seam takes a CALLABLE).

    The reading is the graph instrument's own honesty: an ESTIMATE from a single
    repeat, which is what every production write records (so no dispersion, and
    the hard stop is the declared 1.5x band).
    """
    def _read(_org: str) -> MeterReading:
        return MeterReading(
            used=used,
            measured_at=datetime.now(UTC).isoformat(),
            estimated=True,
            note="SAMPLING ESTIMATE that excludes the HNSW vector index",
        )

    return _read


#: 244 MB of cache against solo's 77 MB cap — 3.2x, well past the 1.5x hard stop
#: (115.5 MB). The figures are the ones #13526 measured on the live tenant.
OVER_CAP = {"graph_storage": _meter(244.0)}


@pytest.fixture(autouse=True)
def _clean_verdict():
    """No verdict may leak between tests: the ContextVar is per-context state."""
    reset_signal_state_for_tests()
    clear_cache_fold_denial()
    yield
    clear_cache_fold_denial()
    reset_signal_state_for_tests()


# ── 1. the gate's two arms ──────────────────────────────────────────────────


def test_an_over_cap_derived_cache_no_longer_refuses_a_truth_carried_write():
    """FAILS ON MAIN: there, this reading raises ``QuotaExceededError``.

    The write reaches the record first (the door declared it), so the cap of a
    store that is DERIVED from the record must not refuse it. What the tenant
    gets instead is a NAMED cache-fold decline carrying the reading's honesty —
    not a silently unenforced cap.
    """
    armed = _limits(max_graph_storage_mb=77)

    # The assertion that does not exist on main: the call RETURNS.
    enforce_allowances(armed, resource="points", meters=OVER_CAP,
                       truth_carried=True)

    denial = cache_fold_denial()
    assert isinstance(denial, CacheFoldDenial)
    # The dimension is NAMED, so the outcome is not a generic "cache skipped".
    assert denial.dimension == "graph_storage"
    # The figures are the ones the gate COMPARED (#13102's discipline), not the
    # nameplate cap and not a floored copy of the reading.
    assert denial.used == 244.0
    assert denial.limit == pytest.approx(115.5)  # 1.5x of the 77 MB cap
    assert denial.allowance == pytest.approx(77.0)
    # An estimate must never present itself as exact.
    assert denial.estimated is True
    assert "graph_storage" in denial.detail
    assert "LOWER BOUND" in denial.detail
    assert denial.as_dict()["outcome"] == "not_applied"


def test_a_door_without_the_chokepoint_still_refuses_the_same_reading():
    """⛔ THE PROTECTIVE CASE — this assertion must NOT flip (#13829 scope 2).

    ``truth_carried`` is the door's PROOF that the record holds the write. Absent
    that proof, the derived store may be the write's only durable copy, and
    failing the write is then the honest behaviour — the one exception the
    standards name (pressure on a read model must not fail a command, *except*
    where the read model is part of the write's correctness invariant). So the
    default arm keeps the refusal verbatim, for the same reading.
    """
    armed = _limits(max_graph_storage_mb=77)

    with pytest.raises(QuotaExceededError):
        enforce_allowances(armed, resource="points", meters=OVER_CAP)

    # And nothing was published for a fold to consume: no door asked.
    assert cache_fold_denial() is None


def test_an_under_cap_reading_serves_and_lets_the_cache_fold():
    """The control: the deadband is not a blanket cache skip."""
    armed = _limits(max_graph_storage_mb=77)

    enforce_allowances(armed, resource="points",
                       meters={"graph_storage": _meter(10.0)},
                       truth_carried=True)

    assert cache_fold_denial() is None


# ── 2. the chokepoint consumes it, and the write still lands ────────────────


class _Log:
    """An EventLog-shaped journal that records what it was handed."""

    durable = True

    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, event: dict) -> int:
        self.events.append(dict(event))
        return len(self.events)


class _Projection:
    """The cache leg: it counts every fold it is asked to apply."""

    def __init__(self) -> None:
        self.applied: list[dict] = []

    def apply(self, event: dict) -> None:  # noqa: D401 - the Protocol's method
        self.applied.append(dict(event))


class _RecordWrite:
    """A ``record_writer.RecordWrite`` stand-in: the record TOOK the write."""

    applied = True
    refusal = None
    outcome = "applied"


def test_the_write_lands_in_the_journal_and_the_record_and_the_cache_declines():
    """The whole shape in one test: truth committed, cache declined, named.

    This is the defect's reversal at the layer that owns the ordering. Before
    #13829 the door refused the call, so neither the journal line nor the record
    row existed; here both exist and only the CACHE leg declines — projection
    lag, which ``derived = replay(journal)`` heals.
    """
    armed = _limits(max_graph_storage_mb=77)
    enforce_allowances(armed, resource="points", meters=OVER_CAP,
                       truth_carried=True)

    log, projection = _Log(), _Projection()
    projected: list[tuple] = []

    def _project(event, seq):
        projected.append((dict(event), seq))
        return _RecordWrite()

    outcome = live_write.write({"type": "PointAdded", "id": "p1"},
                               log=log, projection=projection,
                               project=_project)

    # ── the TRUTH landed ────────────────────────────────────────────────────
    assert outcome.appended is True
    assert len(log.events) == 1            # the journal holds the line
    assert len(projected) == 1             # the record was ASKED and took it
    assert outcome.applied is True
    assert outcome.unprojected == 0        # no disagreement about the truth

    # ── the CACHE declined, and SAID why ────────────────────────────────────
    assert projection.applied == []        # the fold did not run
    assert outcome.folded is False
    assert isinstance(outcome.cache_fold, CacheFoldDenial)
    assert outcome.cache_fold.dimension == "graph_storage"
    # The reported outcome carries it too, so a reader of the artifact (not the
    # dataclass) can tell "cache skipped for a reason" from "no fold asked".
    assert outcome.as_dict()["cache_fold"]["outcome"] == "not_applied"
    assert outcome.as_dict()["cache_fold"]["dimension"] == "graph_storage"


def test_a_write_with_no_verdict_folds_exactly_as_before():
    """No denial published → the fold leg is byte-for-byte the old behaviour."""
    projection = _Projection()
    outcome = live_write.write({"type": "PointAdded", "id": "p2"},
                               log=_Log(), projection=projection,
                               project=lambda event, seq: _RecordWrite())

    assert projection.applied == [{"type": "PointAdded", "id": "p2"}]
    assert outcome.folded is True
    assert outcome.cache_fold is None
    assert outcome.as_dict()["cache_fold"] is None


def test_the_verdict_is_consumed_once():
    """A denial belongs to ONE write: it must not silently deny the next one."""
    armed = _limits(max_graph_storage_mb=77)
    enforce_allowances(armed, resource="points", meters=OVER_CAP,
                       truth_carried=True)

    projection = _Projection()
    _project = lambda event, seq: _RecordWrite()  # noqa: E731
    first = live_write.write({"id": "p3"}, log=_Log(), projection=projection,
                             project=_project)
    second = live_write.write({"id": "p4"}, log=_Log(), projection=projection,
                              project=_project)

    assert first.folded is False and first.cache_fold is not None
    assert second.folded is True and second.cache_fold is None
    assert len(projection.applied) == 1


# ── 3. the declaration is narrow: only the derived dimension moved ─────────


def _dim(name: str) -> AllowanceDimension:
    return next(d for d in DIMENSIONS if d.dimension == name)


def test_only_the_derived_dimension_declares_it_and_no_door_set_moves():
    """#13829 may not quietly re-scope the gate (#13134 is a separate decision).

    ``derived_layer`` is what routes the cap to the fold, so the declaration is
    the whole widening: exactly one dimension may carry it, the record's
    dimension must not, and every ``resources`` door set stays as it was —
    ``graph_storage`` on points+documents, ``capture_extraction`` on sessions
    alone. Adding ``sessions`` anywhere would be #13134's reviewed change.
    """
    assert _dim("graph_storage").derived_layer is True
    assert _dim("postgres_storage").derived_layer is False
    assert _dim("capture_extraction").derived_layer is False

    assert [d.dimension for d in DIMENSIONS if d.derived_layer] == [
        "graph_storage"]

    assert _dim("graph_storage").resources == frozenset({"points", "documents"})
    assert _dim("postgres_storage").resources == frozenset({"points", "documents"})
    assert _dim("capture_extraction").resources == frozenset({"sessions"})


def test_postgres_storage_still_gates_the_record_doors():
    """The record's dimension is untouched: an over-cap record still refuses.

    ⚠️ It is configured nowhere in production today (it is BLOCKED on the
    per-org byte meter, #13526), so this pins the ARM, not a live path: a
    ``truth_carried`` door must still be refused by the dimension measuring the
    layer that door writes.
    """
    from tortoise.quota import QuotaCheckError

    armed = _limits(max_graph_storage_mb=77, max_postgres_storage_bytes=1024)
    with pytest.raises((QuotaExceededError, QuotaCheckError)):
        enforce_allowances(
            armed, resource="points",
            meters={"graph_storage": _meter(244.0),
                    "postgres_storage": _meter(1_000_000.0)},
            truth_carried=True)

    # ⛔ AND NO DENIAL OUTLIVES A REFUSED WRITE. `graph_storage` is the FIRST
    # dimension in the registry, so it publishes before `postgres_storage`
    # refuses; a verdict left behind would be consumed by the next fold in this
    # context and silently deny a cache write nobody capped.
    assert cache_fold_denial() is None


# ── 4. the door opt-in is not free: the consumer must exist and precede ────


def test_create_point_asks_the_fold_before_it_touches_the_cache():
    """The opted-in door's own proof, pinned where it can actually be read.

    ``mcp_server.tortoise_create_point`` passes ``truth_carried=True``, which is
    only honest if the SDK method CONSUMES the verdict before its cache write —
    otherwise the cap would simply stop being enforced on that door and the cache
    would grow unbounded. This is a SOURCE-ORDER pin (there is no cheap way to
    drive the real graph write here), named as one rather than dressed up as a
    behavioural test: it fails if the consumption is removed, or if it is moved
    below the cache block that must respect it.
    """
    from tortoise.sdk import TortoiseSDK

    source = inspect.getsource(TortoiseSDK.create_point)
    assert "cache_fold_denial" in source, (
        "create_point no longer consumes the derived-cache verdict — the door "
        "opted into truth_carried=True in mcp_server and would then never skip "
        "the cache write (#13829)")
    assert source.index("cache_fold_denial") < source.index(
        '_live_write_scope("create_point")'), (
        "the verdict is consumed AFTER the cache write it must guard (#13829)")


def test_create_point_appends_before_it_writes_the_cache():
    """The other half of the opt-in's proof: the RECORD precedes the cache.

    ``_emit_event`` (journal append + record projection) must come before the
    ``CREATE (:Point)`` block, or skipping the fold would leave a write whose
    only durable copy is the cache we just declined.
    """
    from tortoise.sdk import TortoiseSDK

    source = inspect.getsource(TortoiseSDK.create_point)
    assert source.index("_emit_event") < source.index(
        '_live_write_scope("create_point")')


def test_the_mcp_gate_defaults_to_the_protective_arm():
    """`_quota_gated` must default to ``truth_carried=False``, never the inverse.

    An opt-IN is a deliberate act with a proof behind it; an opt-OUT default
    would make every future door silently depend on a cache that may be its only
    durable copy.
    """
    from tortoise.mcp_server import _quota_gated

    default = inspect.signature(_quota_gated).parameters["truth_carried"].default
    assert default is False
