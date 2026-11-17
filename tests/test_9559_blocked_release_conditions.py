"""A BLOCKED allowance dimension is a CLAIM, and this file re-checks it (#9559 R11).

**Why this file exists — the measured cost, not a preference.** R11's own rule is
*"the meter must be wired into the gate before any configuration is made"*, and
the gate enforces it by declaring a dimension BLOCKED: a configured allowance on
a blocked dimension refuses (fail-closed) instead of capping on a figure the
instrument itself says is unfit. That is right. What was missing is that the
block's **grounds** were prose that nothing re-read.

Measured consequence: ``postgres_storage``'s ``blocked_reason`` said *"no per-org
byte meter … BLOCKED until a per-org attribution exists"*. #12762 then landed
exactly that attribution — ``postgres_bytes.PerOrgPostgresBytes.for_org`` — and
the ONE registry (:mod:`tortoise.cost_dimensions`) declares the dimension
``state=measured`` with an explicit ``OVERRIDES:`` of *the #4492 sentence the
block kept quoting*. The block still said the attribution was missing, and the
sentence was restated in ``pricing.storage_limits``' docstring too. Five separate
lanes re-derived the falsified premise off this issue and none could land it.

So a block now carries :attr:`~tortoise.allowance_gate.AllowanceDimension.release`
— machine-readable conditions that would LIFT it — and this file evaluates every
one of them **against the artifact it names**, on every PR:

* a condition whose evidence is SATISFIED must not still be quoted as the live
  obstacle (that is the exact staleness above, and it REDs here);
* a block whose conditions have ALL fired REDs with "lift it", so a dimension
  cannot stay blocked forever on grounds that no longer exist;
* an unknown token REDs, so a typo never becomes a condition that silently never
  fires.

The predicates live HERE rather than in production on purpose: the gate pays
nothing for them, and the evidence is re-derived from the tree instead of being
trusted from a comment.

⚠️ LIMITS OF THE PHRASE GUARD (stated, because a guard read as stronger than it is
is the defect it exists to prevent). It is:

* **lexical** — a paraphrase defeats it. It catches the sentences this issue
  actually emitted, plus each condition's own wording in the artifact that owns
  it; it does not catch any possible restatement. The predicates are the
  substantive half; the phrase check is the cheap tripwire for the specific
  regression that happened.
* **scoped to named SURFACES** — live ``blocked_reason`` strings, plus
  ``postgres_storage.ATTRIBUTION_NOTE`` and the docstrings of the two functions a
  future arming lane reads (``metering.storage_overage_cost_usd`` and
  ``pricing.storage_limits``). Module-level comments that QUOTE the old sentence
  in order to supersede it are deliberately not scanned: quoting superseded
  wording to replace it is not the defect.
"""
from __future__ import annotations

import dataclasses
import re
import tokenize
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tortoise import (
    allowance_gate,
    cost_dimensions,
    graph_storage,
    metering,
    postgres_bytes,
    pricing,
)
from tortoise.allowance_gate import (
    DIMENSIONS,
    RELEASE_CONDITIONS,
)

_REPO = Path(__file__).resolve().parent.parent


def _dim(name: str) -> allowance_gate.AllowanceDimension:
    for d in DIMENSIONS:
        if d.dimension == name:
            return d
    raise AssertionError(f"no declared allowance dimension {name!r}")


def _registry(name: str) -> cost_dimensions.Dimension:
    return cost_dimensions.by_name(name)


# ── the predicates: every one reads an ARTIFACT, never a promise ──────────────
#
# Each returns (satisfied, evidence). ``evidence`` is printed on failure, so a
# RED tells the reader WHAT was measured rather than only that something drifted.

def _per_org_postgres_attribution() -> tuple[bool, str]:
    """A per-org Postgres BYTE attribution that is a MEASUREMENT, not a split."""
    has_reader = callable(getattr(postgres_bytes, "per_org_bytes", None))
    has_per_org = hasattr(postgres_bytes.PerOrgPostgresBytes, "for_org")
    declares_measured = "MEASURED at write time" in str(
        _registry("postgres_storage").attribution)
    return (
        has_reader and has_per_org and declares_measured,
        f"per_org_bytes={has_reader} PerOrgPostgresBytes.for_org={has_per_org} "
        f"registry declares 'MEASURED at write time'={declares_measured}",
    )


_UNSET: Any = object()


def _postgres_residency_gauge(meter: Any = _UNSET) -> tuple[bool, str]:
    """A per-org RESIDENCY GAUGE **this gate can afford on a write path**.

    ⛔ Not "the gate has a meter at all", and the difference is measured, not
    pedantic: reviewer round 1 showed that the weaker form reports SATISFIED when
    an inadequate reader is wired as the meter, which is the single most likely
    edit and exactly the figure the block named as insufficient.

    ⛔ AND NOT "some reader we do not recognise" EITHER. While the gauge did not
    exist this returned False on ``meter is None``; #9559 wired one, so the
    condition is now SATISFIED — and a predicate that reported that on the mere
    presence of an unknown callable would be the round-1 defect one edit over.
    So it revalidates the ARTIFACTS the claim rests on: the snapshot table and
    its refresh RPC are declared in a migration, the reader exists in
    ``metering``, and the dimension's meter IS the residency meter. A rename,
    a dropped migration or a re-wiring REDs here — and the two known-inadequate
    readers stay refused by name (``per_org_bytes`` is a whole-table audit,
    ``get_postgres_bytes_written`` is the window ADDEND).

    ``meter`` is an injection seam so the refusal arm can be EXECUTED while the
    live reading is whatever the registry holds; the no-argument call is the live
    one.
    """
    if meter is _UNSET:
        meter = _dim("postgres_storage").meter
    if meter is None:
        return False, "postgres_storage meter=None (nothing to read per org)"
    # The audit: a whole-table aggregation per call. The ledger column: a window
    # ADDEND, not a stored footprint. Neither is a gauge.
    from tortoise import metering

    inadequate = {
        getattr(postgres_bytes, "per_org_bytes", None): "the whole-table audit",
        getattr(metering, "get_postgres_bytes_written", None): "the window addend",
    }
    inadequate.pop(None, None)
    why = inadequate.get(meter)
    if why:
        return False, (
            f"postgres_storage meter={meter!r} is {why}, which the block names "
            f"as insufficient on a write path")

    # The artifact chain the SATISFIED answer stands on.
    reader = callable(getattr(metering, "get_postgres_residency", None))
    wired = meter is allowance_gate._postgres_stored_bytes
    migration = sorted((_REPO / "supabase" / "migrations").glob(
        "*_postgres_residency_gauge.sql"))
    declared = bool(migration) and all(
        token in migration[0].read_text(encoding="utf-8")
        for token in ("org_postgres_residency", "postgres_residency_snapshot"))
    return (reader and wired and declared), (
        f"metering.get_postgres_residency={reader}; meter is the residency "
        f"reader={wired}; the snapshot table + refresh RPC are declared={declared} "
        f"({migration[0].name if migration else 'NO MIGRATION'})")


def _storage_included_amounts_confirmed() -> tuple[bool, str]:
    """The free/anon included storage amounts are OWNER-CONFIRMED.

    ⛔ FAIL CLOSED on anything that is not POSITIVE evidence. Two holes were
    measured here before this shape: (a) an earlier version read the TOP level of
    ``pricing.json``, missed the real path, defaulted to ``""`` and reported the
    block's ground as SATISFIED; (b) a "not pending" test over two uppercase
    phrases reported SATISFIED for ``TBD-BY-MEASUREMENT``, ``PENDING owner
    signoff`` and a lowercase reword — any of which would have told a lane to
    lift a block whose ground was still true. So the question asked is the
    positive one: does the artifact SAY the amounts are confirmed?
    """
    storage = (pricing._load().get("billing") or {}).get("storage") or {}
    status = storage.get("_included_amounts_status")
    if not isinstance(status, str) or not status.strip():
        return False, (
            "product/pricing.json billing.storage._included_amounts_status is "
            f"absent or not text ({status!r}) — no evidence either way, so NOT "
            f"satisfied")
    flat = " ".join(status.split())
    confirmed = flat.upper().startswith("OWNER-CONFIRMED")
    return confirmed, (
        "product/pricing.json billing.storage._included_amounts_status: "
        f"confirmed={confirmed} · {flat[:120]}")


def _vector_index_measurable() -> tuple[bool, str]:
    """The HNSW vector index is measurable — NECESSARY, not sufficient, for a cap.

    ⛔ This condition alone never makes the graph reading cappable: an EMPTY
    graph reads ~346 KiB while it rents ~1.46 MB, so the parallel
    ``per_graph_overhead_attributed`` token is the other half of the same block.
    A lane that clears this one and lifts the block arms an evadable cap.
    """
    note = str(graph_storage.PRECISION_NOTE)
    forbids = "until the vector index is measurable" in note
    excluded = graph_storage.VECTOR_INDEX_EXCLUDED in graph_storage.EXCLUDED_OVERHEAD
    return not (forbids or excluded), (
        f"graph_storage.PRECISION_NOTE still forbids a cap={forbids}; "
        f"VECTOR_INDEX_EXCLUDED still declared as excluded overhead={excluded}")


def _per_graph_overhead_attributed() -> tuple[bool, str]:
    """Per-graph OVERHEAD is charged to the graph, so a cap cannot be evaded.

    ⛔ THE SECOND BLINDNESS, and the reason it is its own token rather than a
    footnote on the index one. Measured on production 2026-11-16: the instance
    holds **841 MB** while the sum of per-graph readings over **182** graphs is
    **419 MB** — the meter sees about HALF — and deleting **148 EMPTY** tenant
    graphs freed **216 MB**, i.e. **~1.46 MB** of rent per graph that
    ``GRAPH.MEMORY USAGE`` attributes to **NO** graph (a newly-created graph
    reads ~**346 KiB**).

    That is not a rounding error; it is an EVASION. A tenant that spreads its
    storage over many near-empty graphs pays ~1.46 MB of real rent each while the
    reading sees a few hundred KiB, so a cap summed from these readings never
    fires for the tenant who grows that way — the ``#1178`` "a cap that can be
    evaded" class. Clearing the INDEX condition does nothing about it: the two
    numbers have different causes.

    ⛔ BEHAVIOURAL, NOT TEXTUAL. A source/docstring probe would be satisfied by a
    sentence, and ``EXCLUDED_OVERHEAD`` alone by deleting an entry. So this asks
    the module to DO it: remove the exclusion AND charge overhead when readings
    are combined. Two synthetic zero-byte graphs must come back with a positive
    total, because two empty graphs are 2 x 1.46 MB of rent, not zero.
    """
    excluded = any(
        str(entry).startswith("per-graph overhead")
        for entry in graph_storage.EXCLUDED_OVERHEAD)
    note_excludes = "EXCLUDES per-graph" in str(graph_storage.PRECISION_NOTE)
    charged = False
    try:
        stamps = "2026-11-16T00:00:00+00:00"
        synthetic = [
            graph_storage.GraphStorageReading(
                graph_name=f"probe-{i}", ok=True, total_mb=0.0,
                indices_mb=0.0, samples=graph_storage.SAMPLES_DEFAULT,
                repeats=1, readings_mb=(0.0,), min_mb=0.0, max_mb=0.0,
                spread_mb=0.0, measured_at=stamps)
            for i in range(2)
        ]
        combined = graph_storage.combine_graph_storage_readings(synthetic)
        # ⛔ A MAGNITUDE, not "some positive number": review round 7 measured a
        # +1e-9 MB per-graph charge as SATISFIED, which would leave the evasion
        # intact while ticking the box. The measurement is ~1.46 MB/graph, so two
        # graphs must combine to at least 1 MB each (2.0) — deliberately below
        # 1.46 so a conservative per-graph charge still passes, and far above any
        # token amount.
        total = float(combined.total_mb or 0.0) if combined else 0.0
        charged = bool(combined and combined.ok and total >= 2.0)
    except Exception as e:
        return False, (
            f"the combined-reading probe could not be built ({type(e).__name__}: "
            f"{e}), so overhead attribution is UNPROVEN — failing closed")
    return (not excluded and not note_excludes and charged), (
        f"EXCLUDED_OVERHEAD still declares per-graph overhead={excluded}; "
        f"PRECISION_NOTE still says it EXCLUDES per-graph={note_excludes}; "
        f"two EMPTY graphs combine to >= 1 MB each (overhead charged)={charged}; "
        f"combined total={total:.4f} MB")


_PREDICATES = {
    "per_org_postgres_attribution": _per_org_postgres_attribution,
    "postgres_residency_gauge": _postgres_residency_gauge,
    "storage_included_amounts_confirmed": _storage_included_amounts_confirmed,
    "vector_index_measurable": _vector_index_measurable,
    "per_graph_overhead_attributed": _per_graph_overhead_attributed,
}

#: Phrases a SATISFIED condition must NOT appear as, in a live ``blocked_reason``.
#: This is the staleness guard itself: while ``per_org_postgres_attribution`` was
#: satisfied, the block still said "no per-org byte meter" — one line below would
#: have caught it, and does now.
_RELEASED_PHRASES = {
    "per_org_postgres_attribution": (
        "no per-org byte meter",
        "no per-org figure exists",
        "no per-org byte ownership",
        "BLOCKED until a per-org attribution exists",
    ),
    "postgres_residency_gauge": (
        # ⛔ The LIVE wording, not an older paraphrase. This arm used to read
        # "no per-org byte meter", which appears nowhere in the block it guards:
        # on the day a gauge exists, the still-quoted "no per-org RESIDENCY
        # GAUGE" would not have been flagged — the one sentence this issue
        # rewrote would have had no covering phrase.
        "no per-org RESIDENCY GAUGE",
        "no per-org figure this gate can read ON A WRITE PATH",
    ),
    "storage_included_amounts_confirmed": (
        # ⛔ THE LIVE WORDING, for the same measured reason as the residency entry
        # above — and this one was still in the old shape. The previous single
        # phrase, "free/anon included amounts are still undecided", occurs
        # NOWHERE in the tree except this list: on the day the amounts are
        # confirmed, the still-quoted "the included amounts are not confirmed"
        # would not have been flagged by the per-dimension arm. The empty-list
        # assert above catches a token with NO phrase; only liveness catches a
        # token with a phrase over nothing, and
        # ``test_an_unsatisfied_condition_phrase_is_live_against_its_block``
        # makes that a class-level guarantee rather than this one fix.
        "the included amounts are not confirmed",
        "NOT OWNER-CONFIRMED",
        # ⛔ NO THIRD ENTRY "just in case". Reviewer round 7 measured a candidate
        # third phrase, "included amounts are still undecided", as inert against
        # the live block — a dead entry is the exact shape this commit removed one
        # row over, and the liveness arm needs only one live phrase while the
        # satisfied arm is covered by both above.
    ),
    "vector_index_measurable": (
        "cap MUST NOT be wired",
        "until the vector index is measurable",
    ),
    "per_graph_overhead_attributed": (
        # ⛔ The LIVE wording of the SECOND ground, not a summary of it. The
        # measured shape a stale reason would quote is the per-graph rent being
        # invisible; both phrasings below occur in the block today.
        "attributes to NO graph",
        "~1.46 MB of rent per graph",
    ),
}


def test_every_release_token_has_a_predicate_and_vice_versa():
    """A token nobody evaluates is a condition that can never fire.

    Mutation this REDs: add a token to ``RELEASE_CONDITIONS`` without a predicate
    here (it would then be declared as a release with no evidence behind it), or
    delete a token the dimensions still cite. ⛔ ``_RELEASED_PHRASES`` is in the
    same assertion on purpose: a token with a predicate but NO phrase list makes
    the staleness guard vacuous for it, which is a silent hole rather than a
    missing guard.
    """
    assert set(_PREDICATES) == set(RELEASE_CONDITIONS), (
        "RELEASE_CONDITIONS and the revalidating predicates have drifted: "
        f"declared-only={sorted(set(RELEASE_CONDITIONS) - set(_PREDICATES))} "
        f"predicate-only={sorted(set(_PREDICATES) - set(RELEASE_CONDITIONS))}")
    assert set(_RELEASED_PHRASES) == set(RELEASE_CONDITIONS), (
        "a release token has no phrase list, so its staleness guard is "
        f"vacuous: {sorted(set(RELEASE_CONDITIONS) - set(_RELEASED_PHRASES))}")
    # ⛔ NON-EMPTY PER TOKEN, not merely the KEY's presence. Reviewer round 6
    # measured the hole: `"storage_included_amounts_confirmed": ()` keeps the key,
    # so the set comparison above passes while that token's staleness arm covers
    # NOTHING — exactly the silent hole the sentence above says this assertion
    # prevents. Key granularity cannot see it; the entry's own length can.
    empty_phrases = sorted(t for t in RELEASE_CONDITIONS
                           if not _RELEASED_PHRASES.get(t))
    assert not empty_phrases, (
        "release token(s) carry an EMPTY phrase list, so the staleness guard is "
        f"vacuous for them: {empty_phrases} — declare the wording the condition "
        "would appear as, or the arm protects nothing")


def test_the_residency_meter_is_the_gauge_and_never_the_addend(monkeypatch):
    """★ THE WIRING: the meter reads RESIDENCY, and NO ROW is NO READING (#9559).

    The block on ``postgres_storage`` named the difference this test pins — "the
    ledger reader is the window ADDEND, not the org's stored footprint" — because
    a storage cap held against bytes-written-this-window does not cap what the
    org stores (a tenant that wrote 10 GB last window and nothing this one reads
    as zero). So there are three facts to hold:

    1. the meter CALLS the residency reader, not the ledger reader;
    2. no snapshot row is NO READING (``None``), never a zero — the fail-open
       this file documents for every other dimension's absent-value case;
    3. a snapshot row becomes a reading in the dimension's OWN unit (BYTES, not
       MB) carrying the audit's ``measured_at`` so the age bound can bite.
    """
    calls: list[str] = []

    def fake_reader(org_id: str) -> dict:
        calls.append(org_id)
        return {"measured": True, "unmeasured_reason": None,
                "stored_bytes": 12_345_678, "rows": 42,
                "measured_at": "2026-11-17T00:00:00+00:00", "source": "audit"}

    monkeypatch.setattr(metering, "get_postgres_residency", fake_reader, raising=True)
    reading = allowance_gate._postgres_stored_bytes("org_wired")
    assert calls == ["org_wired"], (
        "the meter did not call metering.get_postgres_residency — it is wired to "
        f"something else (called {calls!r})")
    assert reading is not None and reading.used == 12_345_678, (
        f"a snapshot row must yield a reading in BYTES ({reading!r}) — the "
        f"dimension's limit key is max_postgres_storage_bytes")
    assert reading.measured_at == "2026-11-17T00:00:00+00:00", (
        "the reading must carry the AUDIT's measured_at, so stale_after_seconds "
        f"ages the measurement rather than the row write ({reading!r})")
    assert reading.estimated is False, (
        "residency is a measured figure (the audit's own per-org row bytes), not "
        "an estimate — the note must not claim otherwise")

    # ⛔ "NO ROW" IS THREE ANSWERS, and the meter must map them to three DIFFERENT
    # outcomes — this is the review finding that came out of the owner's own
    # measurement (#13084/#13102), not from reading the code:
    #
    #   absent      -> ABSENT  (the gauge HAS reported; this org stores nothing)
    #   unreported  -> refuse  (the table is EMPTY: a zero would be fleet-wide)
    #   read_error  -> refuse  (our fault, still fail-closed)
    def _unmeasured(reason: str) -> dict:
        return {"measured": False, "unmeasured_reason": reason,
                "stored_bytes": None, "rows": None,
                "measured_at": None, "source": None}

    monkeypatch.setattr(
        metering, "get_postgres_residency",
        lambda org_id: _unmeasured("absent")
        | {"gauge_measured_at": "2026-11-17T00:00:00+00:00"}, raising=True)
    fresh = allowance_gate._postgres_stored_bytes("org_fresh")
    assert isinstance(fresh, allowance_gate.MeterReading) and fresh.used == 0.0, (
        "an org with NO row while the gauge HAS reported stores nothing: that is "
        "a TRUE ZERO reading, not a refusal — the snapshot row is derived from "
        "rows only a served write can create, so refusing here deadlocks a fresh "
        f"tenant's first write forever (#13084 measured it as HTTP 500) ({fresh!r})")
    # ⛔ AND IT IS STAMPED. An unstamped zero escapes the dimension's age bound:
    # the snapshot table only ever gains rows, so one refresh would serve every
    # never-snapshotted org at 0 bytes for good (review round 5, P1, measured).
    assert fresh.measured_at == "2026-11-17T00:00:00+00:00", (
        "the zero-by-absence must carry the GAUGE's own instant so "
        f"stale_after_seconds can age it ({fresh!r})")
    monkeypatch.setattr(
        metering, "get_postgres_residency",
        lambda org_id: _unmeasured("absent"), raising=True)
    try:
        allowance_gate._postgres_stored_bytes("org_fresh")
    except allowance_gate.MeterUnavailable:
        pass
    else:
        raise AssertionError(
            "an absence the gauge cannot STAMP must be refused, not served: a "
            "zero nobody can age is the unaged bypass this dimension's evidence "
            "bound exists to prevent")

    for reason in ("unreported", "read_error", "some_future_reason"):
        monkeypatch.setattr(
            metering, "get_postgres_residency",
            lambda org_id, r=reason: _unmeasured(r), raising=True)
        try:
            got = allowance_gate._postgres_stored_bytes("org_x")
        except allowance_gate.MeterUnavailable:
            continue
        raise AssertionError(
            f"reason {reason!r} must be a NAMED refusal (MeterUnavailable), not "
            f"{got!r} — 'unreported' read as a zero is a FLEET-WIDE fail-open "
            "(every org passes any cap), and an unknown reason folded into "
            "'absent' would silently disarm an allowance the day the reader "
            "grows one")

    # …and a row whose measured_at is missing cannot be aged, so it is NO READING
    # too (the gate treats an unageable reading as stale on a bounded dimension).
    monkeypatch.setattr(
        metering, "get_postgres_residency",
        lambda org_id: {"measured": True, "unmeasured_reason": None,
                        "stored_bytes": 5, "rows": 1,
                        "measured_at": None, "source": "audit"}, raising=True)
    try:
        allowance_gate._postgres_stored_bytes("org_no_timestamp")
    except allowance_gate.MeterUnavailable:
        pass
    else:
        raise AssertionError(
            "a snapshot with no measured_at cannot prove freshness on a "
            "dimension with stale_after_seconds, so it must not become a reading")


def test_the_reader_names_why_it_has_no_figure(monkeypatch):
    """★ `get_postgres_residency` must NAME the no-figure state, not collapse it.

    ⛔ The layer BELOW the meter, and reviewer round 1 measured it as entirely
    untested: turning the fault branch into `{"stored_bytes": 0,
    "measured_at": now}` kept the whole suite green. That is the fail-open this
    dimension's design exists to prevent — a PostgREST fault, an unapplied
    migration or a broken grant would make EVERY org read 0 bytes and pass any
    configured cap.

    ⛔ AND THE OPPOSITE COLLAPSE IS ALSO A DEFECT (the owner's `#13084`/`#13102`
    measurement, arrived at from the gate side): returning one `None` for every
    no-figure state refuses a fresh tenant's FIRST write forever, because the
    snapshot row is derived from rows that only a served write can create. That
    is HTTP 500 on `POST /v1/points` for a brand-new paying tenant, measured in
    CI twice. So the reader distinguishes:

    * ``absent``      — the gauge HAS reported (some org has a row) and this one
      holds nothing ⇒ a TRUE zero, consumed only by the dimension's declaration;
    * ``unreported``  — the table is EMPTY ⇒ nothing measured for anyone;
    * ``read_error``  — our fault.

    Mutation this REDs: fold any two of the three into one another (measured).
    """
    # 1. No org context (stdio/operator) — nothing to look up.
    assert metering.get_postgres_residency("")["unmeasured_reason"] == "read_error", (
        "an empty org_id establishes nothing; it must not be reported as a "
        "tenant-absent zero")

    # 2. The registry/embedded lane has no residency table — UNREPORTED, not zero:
    #    a zero there would arm a cap against a figure nobody measures.
    monkeypatch.setattr(metering, "_supabase_mode", lambda: False, raising=True)
    lane = metering.get_postgres_residency("org_registry")
    assert lane["measured"] is False and lane["unmeasured_reason"] == "unreported", (
        f"outside Supabase mode there is no residency gauge, so the answer must "
        f"be UNREPORTED — a zero would let every org pass a storage cap there "
        f"({lane!r})")

    # 3. A read FAULT is read_error (refused), never absent and never a zero.
    monkeypatch.setattr(metering, "_supabase_mode", lambda: True, raising=True)

    class _Boom:
        def query(self, *_a, **_k):
            raise RuntimeError("postgrest 503")

    monkeypatch.setattr(
        "tortoise.supabase_control.get_control_plane", lambda: _Boom(),
        raising=True)
    faulted = metering.get_postgres_residency("org_fault")
    assert faulted["measured"] is False         and faulted["unmeasured_reason"] == "read_error", (
        "a read FAULT must be read_error, never 'absent': an unreadable snapshot "
        f"reported as an absent org would read as 0 bytes and pass every cap "
        f"(got {faulted!r})")

    # 4. NO ROW FOR THIS ORG, but the gauge has reported → `absent` (a true zero).
    class _GaugeReported:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def query(self, _table, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("filters"):
                return []                      # this org has no row
            return [{"measured_at": "2026-11-17T00:00:00+00:00"}]  # others do

    plane = _GaugeReported()
    monkeypatch.setattr(
        "tortoise.supabase_control.get_control_plane", lambda: plane, raising=True)
    absent = metering.get_postgres_residency("org_fresh")
    assert absent["unmeasured_reason"] == "absent", (
        f"an org with no row while OTHER orgs have one stores nothing — that is "
        f"a true zero, not an unknown ({absent!r})")
    assert len(plane.calls) == 2, (
        "the absent answer must be ESTABLISHED by asking whether the gauge has "
        "ever reported (one extra, limit=1 probe), not assumed from an empty "
        f"result (calls={plane.calls!r})")
    assert plane.calls[1].get("limit") == 1, (
        "the gauge-state probe must be bounded — it runs on the write path, so "
        f"it may not read the whole table ({plane.calls[1]!r})")
    assert plane.calls[1].get("order") == "measured_at.desc", (
        "the probe must ask for the NEWEST report: 'has the gauge ever run' is "
        "true forever in a table that only gains rows, so an unordered probe "
        "hands the gate an arbitrary age and lets a 400-day-old refresh serve "
        f"every never-snapshotted org at 0 bytes ({plane.calls[1]!r})")
    assert absent["gauge_measured_at"] == "2026-11-17T00:00:00+00:00", (
        "the absence must carry the probe's instant so the gate can age it "
        f"against stale_after_seconds ({absent!r})")

    # 5. NO ROW AND the table is EMPTY → `unreported`: nothing measured for
    #    anyone, which a zero would turn into a fleet-wide fail-open.
    class _NeverRan:
        def query(self, _table, **_kwargs):
            return []

    monkeypatch.setattr(
        "tortoise.supabase_control.get_control_plane", lambda: _NeverRan(),
        raising=True)
    never = metering.get_postgres_residency("org_any")
    assert never["measured"] is False         and never["unmeasured_reason"] == "unreported", (
        "an EMPTY snapshot table means the refresh has never run, so a zero "
        "here would be a FLEET-WIDE fail-open — every org comparing as 0 bytes "
        f"and passing any cap (got {never!r})")

    # 6. A row IS found → measured, with the audit's own stamp.
    class _HasRow:
        def query(self, _table, **_kwargs):
            return [{"stored_bytes": 4096, "rows": 3,
                     "measured_at": "2026-11-17T00:00:00+00:00",
                     "source": "audit"}]

    monkeypatch.setattr(
        "tortoise.supabase_control.get_control_plane", lambda: _HasRow(),
        raising=True)
    measured = metering.get_postgres_residency("org_has_row")
    assert measured["measured"] is True and measured["stored_bytes"] == 4096         and measured["measured_at"] == "2026-11-17T00:00:00+00:00", (
        f"a snapshot row must be returned as a MEASURED reading ({measured!r})")


def test_a_fresh_org_is_served_at_zero_instead_of_deadlocking(monkeypatch):
    """★ THE DEADLOCK, end to end through the GATE (not just the meter).

    A brand-new tenant, a live gauge (other orgs have snapshots), no row of its
    own, and an allowance configured. The correct outcome is SERVED at 0 bytes —
    and this is the acceptance the issue's own Objective states ("rather than
    being inert or 500-ing").

    ⛔ Why serving is correct and not a fail-open: the snapshot row is DERIVED
    from the org's rows, which only a served write can create. Refusing here
    refuses the write that would make the row appear — forever. Measured for the
    sibling gauge in CI (`#13084`/`#13102`): `POST /v1/points` → HTTP 500 for a
    tenant far under its cap.

    Mutation this REDs: declare ``absent_is_zero=True`` on ``postgres_storage``
    (the ``dim.absent_is_zero is False`` assertion below), make ``_reading``
    treat a zero-valued reading as no-reading (the ``reading.used == 0.0``
    assertion), or serve this zero without its age (the stale half below). The
    ``ABSENT`` sentinel itself is not on this path: the injected meter hands back
    a ``MeterReading``, so what this pins is that a STAMPED zero is served while
    the same zero aged past the bound is refused.
    """
    # ⛔ The ARMED copy: the live dimension is BLOCKED, so the gate refuses
    # before any meter is consulted. This test is about the state that exists
    # AFTER the block lifts, which is the state the wiring must be right for —
    # the issue's whole ordering rule ("before any allowance is configured").
    dim = dataclasses.replace(
        _dim("postgres_storage"), blocked_reason=None,
        meter=lambda org_id: allowance_gate.MeterReading(
            used=0.0,
            measured_at=datetime.now(UTC).isoformat(),
            estimated=False,
            note="no rows attributed to this org at the gauge's last refresh"))
    assert dim.absent_is_zero is False, (
        "postgres_storage must NOT declare absent_is_zero: that routes the "
        "unageable ABSENT sentinel around _stale, and a GAUGE's absence does "
        "have an age (the refresh that reported it). Measured: with the "
        "declaration the same call served 0 bytes at a 400-day-old gauge age")

    reading = allowance_gate._reading(dim, "org_fresh", None, observed_only=False)
    assert reading is not None and reading.used == 0.0, (
        "a fresh org with no residency row must read as a TRUE ZERO, not "
        f"refuse: refusing deadlocks its first write (#13084) ({reading!r})")

    # ★ THE HALF THAT MAKES IT SAFE: the same zero, aged, is REFUSED. Without
    # this the deadlock fix would be a permanent bypass — in a table that only
    # ever gains rows, "the gauge has reported" is true forever after one run.
    stale_dim = dataclasses.replace(
        dim,
        meter=lambda org_id: allowance_gate.MeterReading(
            used=0.0,
            measured_at=(datetime.now(UTC)
                         - timedelta(seconds=400_000)).isoformat(),
            estimated=False,
            note=""))
    with pytest.raises(allowance_gate.QuotaCheckError) as stale_exc:
        allowance_gate._reading(stale_dim, "org_fresh", None, observed_only=False)
    assert "stale" in str(stale_exc.value).lower(), (
        "a zero-by-absence measured at a gauge age beyond the bound must be "
        f"REFUSED as stale, not served ({stale_exc.value})")


def test_a_never_scheduled_refresh_refuses_instead_of_reading_zero(monkeypatch):
    """★ The other half: `unreported` must NOT ride in on the same door as `absent`.

    If ``absent_is_zero`` consumed ``unreported`` too, arming a cap on a fleet
    whose refresh has never run would read EVERY org as 0 bytes — a cap that
    binds for nobody, i.e. the fail-open in its purest form. The dimension's
    declaration is only safe BECAUSE the reader separates the two.

    Mutation this REDs: make the reader report ``absent`` when the snapshot table
    is empty (one-line change, measured).
    """
    def _unreported(org_id: str):
        raise allowance_gate.MeterUnavailable(
            "the postgres residency gauge has never reported")

    dim = dataclasses.replace(
        _dim("postgres_storage"), blocked_reason=None, meter=_unreported)

    try:
        allowance_gate._reading(dim, "org_any", None, observed_only=False)
    except allowance_gate.QuotaCheckError as exc:
        assert "no reading" in str(exc) or "could not be measured" in str(exc), (
            f"the refusal must name a MISSING FIGURE, not a cap breach ({exc})")
    else:
        raise AssertionError(
            "a gauge that has never reported must REFUSE rather than read as 0 "
            "bytes — a zero there passes every org's cap (fleet-wide fail-open)")


def test_a_stale_snapshot_is_refused_rather_than_enforced(monkeypatch):
    """The evidence-age bound: a snapshot older than the bound REFUSES the write.

    A residency snapshot describes the table as of ``measured_at``. Enforcing a
    cap against a figure from before a large write would bill or refuse on rows
    that no longer describe the org — which is why the dimension carries
    ``stale_after_seconds`` at all, and why the answer to "too old" is a refusal
    rather than a silent pass.

    ⛔ THE GATE-LEVEL LEG DRIVES AN **UNBLOCKED** DIMENSION (review finding 5).
    It used to call ``enforce_allowances`` on the LIVE ``postgres_storage``,
    which still carries ``blocked_reason`` — so ``_reading``'s TOP guard raised
    *before any meter was consulted*, and the assert passed only because that
    BLOCKED message happens to contain the token ``stale_after_seconds``. The
    line meant "a stale snapshot is refused, naming staleness" and proved "a
    blocked dimension is refused": deleting ``_reading``'s stale arm left it
    GREEN (measured — round 5 recorded it, round 6 measured it still here).
    It now asserts a phrase staleness OWNS (``older than``, which the blocked
    reason does not contain) on a dimension the block cannot pre-empt.

    Mutation this REDs: drop ``stale_after_seconds`` from the dimension (the
    reading then enforces at any age), or ignore ``measured_at`` in ``_stale``,
    or delete ``_reading``'s stale raise.
    """
    dim = _dim("postgres_storage")
    assert dim.stale_after_seconds, (
        "postgres_storage must declare an evidence-age bound: a snapshot has an "
        "age, and enforcing one without a bound decides from a figure we cannot "
        "defend")

    def old_reader(org_id: str) -> dict:
        return {"measured": True, "unmeasured_reason": None,
                "stored_bytes": 1_000, "rows": 1,
                "measured_at": "2000-01-01T00:00:00+00:00", "source": "audit"}

    monkeypatch.setattr(metering, "get_postgres_residency", old_reader, raising=True)
    assert allowance_gate._stale(
        allowance_gate._postgres_stored_bytes("org_old"), dim) is True, (
        "a 2000-vintage snapshot is stale against a 24 h bound")

    # ⛔ THE ARMED COPY, exactly as the sibling "fresh org" test builds it: the
    # LIVE dimension is BLOCKED, so a gate-level call on it can never reach the
    # staleness arm. This is the state that exists AFTER the block lifts, which
    # is the state the arm has to be right for.
    armed = dataclasses.replace(dim, blocked_reason=None)
    monkeypatch.setattr(allowance_gate, "DIMENSIONS",
                        tuple(armed if d.dimension == dim.dimension else d
                              for d in DIMENSIONS))
    # The gate's own entry point (``quota`` reaches it through
    # ``enforce_org_limit``, which takes no meter seam). This is the same function
    # the configured-allowance path calls.
    with pytest.raises(allowance_gate.QuotaCheckError) as excinfo:
        allowance_gate.enforce_allowances(
            {dim.limit_key: 10_000}, org_id="org_old", resource="points")
    message = str(excinfo.value)
    assert "older than" in message and str(dim.stale_after_seconds) in message, (
        "a stale snapshot must be REFUSED with the message the STALENESS arm "
        "owns (the age and the bound), not with a blocked-dimension message "
        f"that merely quotes ``stale_after_seconds``, got: {message}")


def test_the_residency_refusal_still_fires():
    """★ The refusal arm, EXECUTED — not merely spelled in the predicate.

    ⛔ Reviewer round 5 measured this arm as UNREACHABLE while the live registry
    held ``postgres_storage.meter=None``: the predicate returned before the
    inadequate-reader dict was consulted, so BOTH of these mutants stayed GREEN —
    deleting the refusal entirely, and renaming
    ``metering.get_postgres_bytes_written`` so the ``getattr(..., None)`` lookup
    missed and the entry was ``pop``ped away. #9559 wires a real meter, and the
    seam below keeps the arm executable anyway; the reader NAMES are pinned by
    resolution so a rename REDs instead of going silent.

    Mutation this REDs: delete the ``why`` arm, or rename/isdelete either reader
    the dict matches, or make ``meter`` default to something other than the live
    registry reading.
    """
    # The two known-inadequate readers, by the names the predicate looks up. A
    # rename makes the dict entry vanish and the arm go silent, so the names are
    # asserted to RESOLVE before the arm is exercised over them.
    audit = getattr(postgres_bytes, "per_org_bytes", None)
    ledger = getattr(metering, "get_postgres_bytes_written", None)
    assert callable(audit), (
        "postgres_bytes.per_org_bytes no longer resolves — the residency refusal "
        "matches it by name, so a rename silently empties that dict entry and the "
        "arm stops guarding")
    assert callable(ledger), (
        "metering.get_postgres_bytes_written no longer resolves — same failure: "
        "the getattr(..., None) lookup returns None, the entry is popped, and the "
        "refusal covers nothing"
    )

    for reader, why in ((audit, "whole-table audit"),
                        (ledger, "window addend")):
        satisfied, evidence = _postgres_residency_gauge(meter=reader)
        assert satisfied is False, (
            f"wiring {reader!r} as the postgres_storage meter must NOT satisfy "
            f"the residency gauge — it is the {why}, which the block names as "
            f"insufficient on a write path:\n  {evidence}")
        assert why in evidence, (
            f"the refusal for {reader!r} does not say WHY it is insufficient "
            f"(expected {why!r}):\n  {evidence}")

    # …an UNRELATED callable is not a gauge either. Since #9559 the condition IS
    # satisfied, and the tempting shortcut is to satisfy it on any callable that
    # is not one of the two refusals — round 1's defect one edit over. The
    # artifact chain is revalidated, so a stranger is refused.
    satisfied, evidence = _postgres_residency_gauge(meter=lambda org_id: None)
    assert satisfied is False and "residency reader=False" in evidence, (
        "an unrelated meter must NOT satisfy the residency gauge — satisfaction "
        "rests on the snapshot reader being the one the gate calls:\n  "
        f"{evidence}")

    # …and the POSITIVE control: the wired residency reader DOES satisfy it, so
    # the predicate is not an always-False.
    satisfied, evidence = _postgres_residency_gauge(
        meter=allowance_gate._postgres_stored_bytes)
    assert satisfied is True, (
        "the residency reader the dimension is wired to must satisfy its own "
        f"condition, or the block can never end:\n  {evidence}")

    # …and the no-argument call reads the LIVE registry, which since #9559 holds
    # the residency meter — NOT either inadequate reader, which is the property
    # this row exists to keep true now that the gauge is wired.
    live, live_evidence = _postgres_residency_gauge()
    assert live is True, (
        "the no-argument call must read the LIVE registry, whose meter is now the "
        f"residency gauge (#9559):\n  {live_evidence}")
    assert allowance_gate._postgres_stored_bytes in (
        _dim("postgres_storage").meter, ) or (
            _dim("postgres_storage").meter is allowance_gate._postgres_stored_bytes), (
        "postgres_storage's meter is not the residency reader — the gauge this "
        "condition reports SATISFIED for is not the one the gate calls")


def test_the_graph_block_names_BOTH_grounds_the_meter_is_blind_to():
    """★ The graph block has TWO obstacles, and lifting one does not lift the block.

    Measured on production 2026-11-16: **841 MB** resident vs **419 MB** summed
    over **182** graphs — the meter sees about HALF. Two causes, and they are
    independent:

    * the **vector index** share is under-reported 8.5x-21.7x (its own token);
    * **per-graph overhead** (~1.46 MB each) is attributed to **NO** graph —
      deleting 148 EMPTY graphs freed 216 MB, while a newly-created graph reads
      ~346 KiB. That is the EVASION: spread storage over near-empty graphs and a
      cap summed from these readings never fires.

    So a lane that makes the index measurable and lifts the block on that alone
    would arm a cap the tenant can step around at ~1.46 MB a graph. The release
    MUST name both, which is why this asserts the pair rather than membership of
    one.

    Mutation this REDs: drop either token from ``release`` (measured).

    ⛔ **AMENDED at the `#13102` merge (`81956063cc` + the `origin/main` integration), and the
    amendment is stated here rather than made silently.** The invariant main's row encodes — *no lane
    may lift the graph block on ONE cleared blindness* — is kept and made STRONGER, because the block
    here is not merely worded: it is a check on the STATE the merged tree is in. `#13084` armed this
    dimension deliberately as a **LOWER-BOUND** cap (the estimate rule: all exclusions push the reading
    DOWN, so a refusal at `reading >= X` only fires when the true bytes are already `>= X`; sampling
    error is stated to the customer, and the hard stop is declared at a margin rather than claimed as a
    measured error). Arming is therefore allowed here ONLY while both blindnesses remain DECLARED and
    UNSATISFIED — i.e. nobody may report them cleared — and while the arming still discloses its
    direction. A lane that "cleared" either token to justify arming still REDs this row.
    """
    dim = _dim("graph_storage")
    tokens = ("vector_index_measurable", "per_graph_overhead_attributed")
    # Leg 1 — BOTH blindnesses stay declared. Not "were once": present in the
    # registry this tree exposes today, with a revalidating predicate each (the row
    # below pins predicate≈declaration coverage).
    assert set(tokens) <= set(RELEASE_CONDITIONS), (
        "the graph block's grounds are no longer DECLARED in RELEASE_CONDITIONS — "
        f"dropping a token retires a blindness nobody measured away: {sorted(RELEASE_CONDITIONS)}")
    # Leg 2 — and neither is SATISFIED. This is what makes the arming honest: the
    # meter is still blind, and the cap is denominated accordingly rather than on a
    # claim that the reading became exact.
    for token in tokens:
        satisfied, evidence = _PREDICATES[token]()
        assert satisfied is False, (
            f"{token!r} reports SATISFIED at this head, so the graph cap is being enforced "
            f"against a figure this tree just called usable — re-derive the claim or "
            f"re-point the arming: {evidence}")
    if dim.blocked:
        # The state main's row was written for: blocked, and the block names both.
        assert set(dim.release) >= set(tokens), (
            "the graph block must name BOTH blindnesses — the vector index AND the "
            f"per-graph overhead a tenant can multiply (got {dim.release!r})")
        reason = dim.blocked_reason or ""
        assert "1.46 MB" in reason and "841 MB" in reason, (
            "the reason must carry the PRODUCTION measurement the second ground "
            "rests on: a paraphrase is what let the first ground go stale (" +
            reason[:200] + ")")
        assert "BOTH" in reason.upper(), (
            "the reason must say the block waits on both conditions, or a lane "
            "reads it as a checklist and lifts on the cheaper one")
        return
    # The state #13084 ships: ARMED, with no block to name (the tokens are retired
    # FROM THE DIMENSION, not from the registry — legs 1 and 2 above prove that).
    # What replaces the block is the estimate rule, and it has to be checkable, not
    # merely commented: the arming refuses above a declared margin (a hard stop of
    # 1.0 would enforce the estimate as if it were exact) and the customer-facing
    # note says LOWER BOUND.
    assert dim.release == (), (
        "an armed dimension still citing release conditions is a half-lifted block: "
        f"either arm it with the estimate rule or keep it blocked (release={dim.release!r})")
    assert allowance_gate.HARD_STOP_MULTIPLE > 1.0, (
        "the armed graph cap must refuse ABOVE its declared margin: a hard stop of "
        f"{allowance_gate.HARD_STOP_MULTIPLE} enforces a sampled estimate as if it were "
        "an exact figure, which is the claim the arming's own docstring refuses")
    note = str(graph_storage.PRECISION_NOTE).upper()
    assert "LOWER BOUND" in note and "NEVER AN EXACT CAP INPUT" in note, (
        "the armed cap is denominated against a LOWER BOUND, so the note carried on "
        "every reading must say so — otherwise the arming is enforcing an estimate "
        f"the meter itself calls unusable: {note[:200]}")


@pytest.mark.parametrize("dim", [d for d in DIMENSIONS if d.blocked],
                         ids=lambda d: d.dimension)
def test_a_blocked_dimension_still_waits_on_something(dim):
    """★ THE LOAD-BEARING ONE: a block must not outlive all of its grounds.

    Mutation this REDs: leave a dimension blocked after the change that satisfies
    its last condition — the shape that kept ``postgres_storage`` (and five lanes)
    on a falsified premise. On that day the fix is to lift the block, or to add
    the NEW condition **with its evidence**; it is never to leave the block
    standing on grounds that have fired.
    """
    results = {t: _PREDICATES[t]() for t in dim.release}
    unsatisfied = [t for t, (ok, _) in results.items() if not ok]
    assert unsatisfied, (
        f"{dim.dimension!r} is BLOCKED but EVERY declared release condition is "
        f"already satisfied:\n"
        + "\n".join(f"  {t}: {ev}" for t, (_, ev) in results.items())
        + "\n  ⇒ lift the block, or add the condition that still holds WITH its "
          "evidence. A block with no live ground is the stale-flag class #9559 "
          "paid for.")


def test_the_included_amounts_predicate_needs_POSITIVE_evidence(monkeypatch):
    """Every non-confirming status reads UNSATISFIED — the fail-open class.

    Reviewer round 2 measured the hole: a "not <two uppercase phrases>" test called
    ``TBD-BY-MEASUREMENT``, ``PENDING owner signoff`` and a lowercase reword
    SATISFIED, which would tell a lane to lift a block whose ground was still
    true. The predicate asks the POSITIVE question now, so these must all fail.
    """
    cases = {
        "DERIVED, NOT OWNER-CONFIRMED. …": False,
        "TBD-BY-MEASUREMENT": False,
        "PENDING owner signoff": False,
        "the amounts are still not decided": False,
        "": False,
        "   ": False,
        None: False,
        "OWNER-CONFIRMED 2026-11-20 (comment 123456)": True,
        "owner-confirmed after review": True,
    }
    for status, expected in cases.items():
        doc = {"billing": {"storage": {"_included_amounts_status": status}}}
        monkeypatch.setattr(pricing, "_load", lambda doc=doc: doc)
        got, evidence = _storage_included_amounts_confirmed()
        assert got is expected, f"{status!r} -> {got} (expected {expected}): {evidence}"

    # …and a MOVED/absent path is no evidence, never a pass.
    monkeypatch.setattr(pricing, "_load", lambda: {"billing": {"storage": {}}})
    got, evidence = _storage_included_amounts_confirmed()
    assert got is False, evidence


@pytest.mark.parametrize("dim", [d for d in DIMENSIONS if d.blocked],
                         ids=lambda d: d.dimension)
def test_a_satisfied_condition_is_not_quoted_as_the_obstacle(dim):
    """A block may RECORD a satisfied condition; it may not LEAD with it.

    ``postgres_storage`` keeps ``per_org_postgres_attribution`` in its ``release``
    tuple on purpose — it is the finding ("this one is done", #12762), and the next
    lane should not re-derive it. What it may not do is repeat the phrase as the
    reason the dimension is still blocked.
    """
    reason = str(dim.blocked_reason)
    for token in dim.release:
        satisfied, evidence = _PREDICATES[token]()
        if not satisfied:
            continue
        for phrase in _RELEASED_PHRASES[token]:
            # Casfolded for the reason above: a stale premise quoted
            # sentence-initial is the same defect with a capital letter.
            assert " ".join(phrase.split()).casefold() not in " ".join(
                reason.split()).casefold(), (
                f"{dim.dimension!r}: release condition {token!r} is SATISFIED "
                f"({evidence}) but its phrase {phrase!r} is still quoted as the "
                f"current obstacle in blocked_reason:\n  {reason}")


@pytest.mark.parametrize(
    "dim,token",
    [(d, t) for d in DIMENSIONS if d.blocked for t in d.release],
    ids=lambda v: v.dimension if hasattr(v, "dimension") else v,
)
def test_an_unsatisfied_condition_phrase_is_live_against_its_block(dim, token):
    """★ THE PHRASE LIST MUST GUARD THE BLOCK IT NAMES — not merely be non-empty.

    Round 3 fixed this for ``postgres_residency_gauge`` ("the phrase appeared
    nowhere in the block it guards") and reviewer round 6 found the SAME defect
    one token over: ``storage_included_amounts_confirmed``'s only phrase occurred
    nowhere in the tree except the phrase list itself, so the day the amounts are
    confirmed the still-quoted "the included amounts are not confirmed" would not
    have been flagged. ⛔ The non-empty-per-token assert cannot see this — a list
    of one wrong phrase is non-empty — so the class-level fix is liveness.

    The invariant is the COMPLEMENT of
    ``test_a_satisfied_condition_is_not_quoted_as_the_obstacle``: for a condition
    that is NOT satisfied, at least one registered phrase must be PRESENT in the
    live ``blocked_reason`` of the dimension citing it. Unsatisfied ⇒ its wording
    is still the obstacle ⇒ the guard has something to match.

    Mutation this REDs: reword the block without updating the phrase list, or
    leave a token whose phrases exist only in this list.
    """
    satisfied, why = _PREDICATES[token]()
    if satisfied:
        pytest.skip(f"{token} is SATISFIED — the complementary arm covers it")
    assert dim.blocked_reason, f"{dim.dimension} cites {token} but has no reason"
    flat = " ".join(str(dim.blocked_reason).split())
    live = [p for p in _RELEASED_PHRASES[token]
            if " ".join(p.split()).casefold() in flat.casefold()]
    assert live, (
        f"{dim.dimension} is blocked on the UNSATISFIED condition {token!r} "
        f"({why}), but NONE of its registered phrases appears in the live "
        f"blocked_reason — so on the day that condition is satisfied the stale "
        f"sentence would not be flagged. Register the wording the block actually "
        f"uses, not a paraphrase. Phrases tried: "
        f"{list(_RELEASED_PHRASES[token])}\n  live reason: {flat[:300]}")


def test_the_staleness_guard_has_at_least_one_live_token():
    """★ The guard must be able to FIRE. Per-dimension arms skip on unsatisfied.

    Every assertion above sits behind ``if not satisfied: continue``, so the day
    every predicate reads False the whole file stays green with the staleness
    guard covering nothing — the "guard that stopped guarding" shape in its
    purest form. Aggregated across the registry, not per dimension: a dimension
    that legitimately waits on everything it names is fine as long as SOME
    condition somewhere is live.
    """
    checked = [t for d in DIMENSIONS if d.blocked for t in d.release
               if _PREDICATES[t]()[0]]
    assert checked, (
        "NO declared release condition is satisfied anywhere, so the phrase "
        "guard above checks nothing: either the predicates became false (re-derive "
        "them) or every block legitimately waits on all it names — and then this "
        "file's staleness coverage must be re-homed deliberately, not assumed")


def test_exactly_the_two_MEASUREMENT_conditions_are_satisfied_today():
    """The one SATISFIED condition is asserted POSITIVELY, so its proof cannot lapse.

    `per_org_postgres_attribution` is the finding of this issue (#12762 landed the
    attribution). Three other tests consume it through a `if not satisfied:
    continue`, so if the predicate quietly started returning False they would all
    stay green and the staleness guard would cover nothing. This pins it.

    When the remaining conditions fire — or if one of them is satisfied by
    accident — this test's job is to make the next lane look, not to fix it.
    """
    satisfied, evidence = _per_org_postgres_attribution()
    assert satisfied is True, (
        "the per-org Postgres byte attribution no longer reads as MEASURED — "
        "if #12762's artifact moved, re-point this predicate and re-derive "
        f"postgres_storage's release conditions:\n  {evidence}")
    assert _PREDICATES["per_org_postgres_attribution"] is _per_org_postgres_attribution
    # ⛔ AND "EXACTLY" MEANS EXACTLY. Reviewer round 6 measured the name
    # over-claiming: with the pricing artifact flipped to OWNER-CONFIRMED this
    # test stayed green, because it only ever asserted the one positive. The SET
    # is pinned, so a second condition firing is a lane's prompt to LIFT A BLOCK
    # (the docstring above) rather than a silent change in what this file proves.
    now = {t for t, fn in _PREDICATES.items() if fn()[0]}
    assert now == {"per_org_postgres_attribution", "postgres_residency_gauge"}, (
        f"the set of satisfied release conditions changed: {sorted(now)} — if a "
        f"condition genuinely fired, re-derive every blocked dimension that cites "
        f"it (lift the block, or add the NEW condition with its evidence); if it "
        f"only LOOKS satisfied, the predicate is reading the wrong artifact")


def test_the_falsified_premise_is_actually_recorded_as_released():
    """The correction itself, pinned — and the registry side of it.

    A reader who lands on ``postgres_storage``'s block must not be told the
    attribution is missing, and must not have to re-derive that it exists. This
    asserts BOTH halves: the registry still carries the ``OVERRIDES:`` that
    supersedes the #4492 sentence, and the gate's reason points AT the
    attribution rather than denying it.

    Mutation this REDs: reverting the reason to "no per-org byte meter … BLOCKED
    until a per-org attribution exists" (the pre-#12762 wording), or dropping the
    registry's OVERRIDES marker so the two modules silently disagree again.
    """
    attribution = str(_registry("postgres_storage").attribution)
    assert "OVERRIDES" in attribution, (
        "cost_dimensions' postgres_storage attribution no longer records the "
        "OVERRIDES of the #4492 'no per-org figure exists' position — without "
        "it the gate's correction cites nothing")
    assert "ALLOCATION, not a measurement" in attribution, (
        "the overridden sentence is no longer quoted by the registry, so this "
        "test's premise (a sentence was overridden) is stale — re-derive it")

    reason = str(_dim("postgres_storage").blocked_reason)
    # ⛔ THE QUALIFIED NAME, not a bare ``for_org``/``per_org_bytes``. Reviewer
    # round 5 measured the loose form as satisfiable by the RPC name
    # ``postgres_per_org_bytes`` alone: both genuine mentions could be stripped
    # from the reason and this assertion stayed GREEN, so it only reddened on a
    # full revert. A reader who is told the attribution exists has to be able to
    # find the METHOD.
    assert "PerOrgPostgresBytes.for_org" in reason, (
        "postgres_storage's blocked_reason no longer names the per-org "
        "attribution's entry point (PerOrgPostgresBytes.for_org) that now exists "
        "— a reader would conclude it is missing:\n"
        f"  {reason}")
    assert "NOT OWNER-CONFIRMED" in reason, (
        "blocked_reason no longer names the included-amount decision that is "
        f"genuinely outstanding, in the artifact's own words:\n  {reason}")
    # …and the artifact must still say it, so the sentence cannot become a lie
    # about a decision the owner has since made.
    satisfied, evidence = _storage_included_amounts_confirmed()
    assert not satisfied, (
        "blocked_reason cites the included amounts as outstanding but "
        f"product/pricing.json reports them confirmed — re-ground the block:\n  {evidence}")


# ── the falsified-premise scan, as a UNIT, so its GRANULARITY is testable ─────
#
# Phrasings that assert a RETIRED premise as current. ⛔ EVERY premise this issue
# has retired, not just the first: reviewer round 1 measured the scan as blind to
# the SECOND — #9559 wires the residency gauge, and "no per-org RESIDENCY GAUGE"
# was still asserted as current in two files outside this dict, including the ops
# doc the gate's own OVERRIDES line cites. A scan that covers one retired premise
# and not the next is the same defect one round later.
_RELEASED_ASSERTIONS = (
    "no per-org RESIDENCY GAUGE",
    "no per-org gauge exists that is affordable per write",
    "neither the audit nor",
    "no per-org figure at all",
    "HAS NO PER-ORG BYTE METER",
    "records no per-org byte ownership",
    "no per-org byte meter",
    "only once a per-org attribution exists",
    "no per-org split",
)

#: The marker a superseded position is quoted beside (``OVERRIDES:`` — the
#: repo's rule for a deliberate departure). It exempts the SENTENCE carrying it.
_OVERRIDES_MARKER = "OVERRIDES"

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def _sentences(flat: str) -> list[str]:
    """Split a flattened paragraph into sentences.

    Sentence granularity is a measurement, not taste: reviewer round 6 inserted an
    assertion into the OVERRIDES paragraph of
    ``metering.storage_overage_cost_usd.__doc__`` — ~700 characters from the
    marker — and the paragraph-wide exemption left all 16 tests GREEN.
    """
    return [s for s in _SENTENCE_END.split(flat) if s]


def _scannable(flat: str) -> list[str]:
    """The sentence(s) of a flattened paragraph the premise scan MUST read.

    ⛔ The exemption is marker-ADJACENT, never paragraph-wide. A paragraph skip
    was measured INERT-WHILE-BLIND: this very change added the word OVERRIDES to
    ``metering.storage_overage_cost_usd``'s ``used:`` block, so that surface —
    the one the PR names as a restatement site — became permanently unscannable,
    and re-asserting the falsified premise anywhere else in its ~2,000-character
    paragraph stayed GREEN. A sentence that QUOTES the superseded position beside
    the marker is an argument; anything else is the defect.
    """
    return [s for s in _sentences(flat) if _OVERRIDES_MARKER not in s]


def _premise_offenders(text: str) -> list[str]:
    """Released phrasings ``text`` still asserts as CURRENT, with their context."""
    hits: list[str] = []
    for para in text.split("\n\n"):
        # ⛔ NORMALISE FIRST: these are WRAPPED docstrings, so on the pre-fix tree
        # the phrase could carry a line break inside it and never match the text
        # it was written to catch. Whitespace is collapsed before splitting too.
        flat = " ".join(para.split())
        # ⛔ SKIP EMPTY PARAGRAPHS: ``str.split`` returns at least one element for
        # ANY string (``"".split("\n\n") == [""]``), so an empty paragraph is
        # always present and would otherwise be scanned as text. A genuinely
        # empty SURFACE is a separate, per-surface assertion in the caller — the
        # ``scanned``/``nonempty`` counters were each measured unfalsifiable.
        if not flat:
            continue
        for sentence in _scannable(flat):
            for phrase in _RELEASED_ASSERTIONS:
                # ⛔ CASEFOLDED. Reviewer round 2 measured the case-sensitive form
                # as blind to the most natural restatement of a premise: with
                # "No per-org RESIDENCY GAUGE exists that this gate can afford on
                # a write path." at the head of the live ``blocked_reason`` the
                # suite was COMPLETELY GREEN, while the identical sentence with a
                # lowercase ``n`` REDdened. That is not the documented
                # paraphrase limit — it is the SAME registered string, differing
                # only in the case of its first letter, i.e. the shape any lane
                # writes when it restates a premise as a sentence. Measured: zero
                # false-red candidates across the scanned surfaces under a
                # casefolded matcher.
                if " ".join(phrase.split()).casefold() in sentence.casefold():
                    hits.append(f"{phrase!r} in: {sentence[:90]}")
    return hits


def test_the_marker_exemption_covers_only_the_sentence_that_asks_for_it():
    """★ Marker-ADJACENCY, proved on synthetic text: the granularity IS the guard.

    Reviewer round 6 measured the paragraph-wide exemption as a false PASS on the
    one surface this change annotated — a falsified sentence inserted anywhere in
    the OVERRIDES paragraph stayed GREEN. This pins BOTH halves, so the next
    narrowing cannot quietly become a widening: a marker sentence quoting the
    superseded position is exempt, and the same words one sentence away, asserted
    as current, are reported.
    """
    argument = (
        "The per-org attribution exists (#12762). "
        'OVERRIDES: the older text claimed "no per-org figure at all" — that '
        "sentence is superseded, not current."
    )
    assert _premise_offenders(argument) == [], (
        "a sentence that QUOTES the superseded position beside the marker is an "
        "argument and must not be reported")
    smuggled = (
        "The per-org attribution exists (#12762). "
        "OVERRIDES: the older text claimed the figure did not exist. "
        "There is no per-org figure at all."
    )
    hits = _premise_offenders(smuggled)
    assert hits and "no per-org figure at all" in hits[0], (
        "the falsified premise was re-asserted OUTSIDE the marker's own sentence "
        "— in the very paragraph the exemption was meant to narrow — and the "
        f"scan did not fire: {hits!r}")


def test_the_two_artifacts_that_named_the_missing_gauge_now_name_it():
    """The sentence #9559 retires, pinned by FINGERPRINT in the two files that
    carried it — and pinned POSITIVELY, so the correction cannot be reverted by
    deleting a paragraph.

    Round 1 of this PR measured the premise scan (above) as blind to the SECOND
    retired premise: the ops doc the gate's own ``OVERRIDES:`` line cites still
    said arming needs *"a per-org gauge the gate can afford on a write path
    (neither the audit nor the ledger addend is one)"*, and the sibling test's
    docstring still said the block rested on two grounds. Both are the artifact a
    lane READS before re-deriving — the failure mode this issue exists to stop.

    ⛔ NOT a phrase scan. These files legitimately carry history and negative
    assertions, so the guard is the positive one: each file must NAME the gauge
    that now exists, and the retracted fingerprint must be gone.

    Mutation this REDs: revert either text (the ops doc to "neither the audit nor
    the ledger addend is one", the test docstring to "has no per-org RESIDENCY
    gauge"), or delete the gauge's name from either.
    """
    ops = (_REPO / "docs" / "ops" / "postgres-storage-measurement.md").read_text(
        encoding="utf-8")
    flat_ops = " ".join(ops.split())
    assert "postgres_residency_snapshot" in flat_ops, (
        "the ops doc does not name the refresh RPC — an operator reading the "
        "authoritative record cannot schedule what it never mentions")
    assert "org_postgres_residency" in flat_ops, (
        "the ops doc does not name the snapshot table the gate reads")
    # ⛔ CASEFOLDED, for round 2's reason applied to this guard too (round 3
    # measured it as still lowercase-only): a fingerprint is matched against
    # prose, and prose starts sentences with a capital. A sentence-initial
    # "Neither the audit nor the ledger addend is one." restates the retired
    # position and was invisible here while the round-2 matchers were fixed.
    assert "neither the audit nor the ledger addend is one".casefold() not in (
        flat_ops.casefold()), (
        "the ops doc still states the RETIRED position (that no affordable "
        "per-org figure exists) — #9559 wired one, and this is the artifact the "
        "gate's OVERRIDES line cites as the record")

    sibling = (_REPO / "tests" / "test_allowance_gate.py").read_text(
        encoding="utf-8")
    flat_sib = " ".join(sibling.split())
    # The one allowed form is the HISTORICAL "… existed"; casefold both sides so
    # an exemption cannot be defeated by a capital and the fingerprint cannot be
    # hidden by one.
    assert "no per-org RESIDENCY gauge".casefold() not in flat_sib.casefold().replace(
        "no per-org residency gauge existed", ""), (
        "tests/test_allowance_gate.py still asserts the gauge is missing as "
        "current (only the HISTORICAL form \"no per-org RESIDENCY gauge "
        "existed\" is allowed) — a docstring is what the next lane reads before "
        "editing the assertion below it")
    assert "#9559" in flat_sib, (
        "tests/test_allowance_gate.py's docstring does not record that #9559 "
        "wired the gauge, so the next reader concludes the block still waits on "
        "it")


def test_no_other_surface_still_asserts_a_released_ground_as_current():
    """The scan surface for the falsified premise, beyond ``blocked_reason``.

    Reviewer round 1 measured the hole: this file only checked ``blocked_reason``,
    while the change also had to correct the same premise in
    ``postgres_storage.ATTRIBUTION_NOTE`` — and restoring the old sentence there
    kept every test green. These are the surfaces an arming lane actually reads.
    """
    from tortoise import cost_dimensions, metering
    from tortoise.postgres_storage import ATTRIBUTION_NOTE

    surfaces = {
        "postgres_storage.ATTRIBUTION_NOTE": ATTRIBUTION_NOTE,
        "metering.storage_overage_cost_usd.__doc__":
            metering.storage_overage_cost_usd.__doc__ or "",
        "pricing.storage_limits.__doc__": pricing.storage_limits.__doc__ or "",
        # ⛔ The ONE registry's own header. Round 3 found it asserting the
        # falsified premise while the same registry's `postgres_storage` row
        # carried the OVERRIDES of it — the header disagreeing with its own row
        # is the drift this issue is about.
        "cost_dimensions module docstring": cost_dimensions.__doc__ or "",
    }
    # ⛔ TWO ARTIFACTS ARE DELIBERATELY NOT IN THIS DICT, and the reason is
    # measured, not squeamish: `docs/ops/postgres-storage-measurement.md` states
    # "Postgres records no per-org byte ownership" about the SERVER (still true —
    # the audit derives the figure from rows) and `tests/test_allowance_gate.py`
    # ASSERTS the absence of a retracted phrase. A phrase tripwire cannot tell a
    # true statement or a negative assertion from a restatement, so it false-reds
    # on both (measured). Their correction is pinned precisely instead, by
    # ``test_the_two_artifacts_that_named_the_missing_gauge_now_name_it``.
    # Phrasings and the exemption's granularity live in ``_premise_offenders``
    # above, so the GRANULARITY is itself asserted
    # (``test_the_marker_exemption_covers_only_the_sentence_that_asks_for_it``)
    # instead of only being described in a comment.
    offenders = []
    for name, text in surfaces.items():
        offenders.extend(f"{name}: {hit}" for hit in _premise_offenders(text))
    # ⛔ PER SURFACE, not aggregated. Reviewer round 5 measured the aggregate
    # form: emptying ONE surface left 21 non-empty paragraphs across 4, so
    # ``nonempty >= len(surfaces)`` stayed GREEN — the third round running in
    # which a vacuity guard could not fire for the case its own comment names.
    # An empty surface IS a renamed or removed docstring, and it is precisely the
    # surface the scan then covers nothing of, so each one is asserted on its own.
    empty = sorted(n for n, t in surfaces.items()
                   if not any(" ".join(x.split()) for x in t.split("\n\n")))
    assert not empty, (
        f"surface(s) with no non-empty paragraph: {empty} — a renamed or "
        f"removed docstring is not a passing surface, and the scan covers "
        f"nothing in an empty one")
    assert not offenders, (
        "a surface an arming lane reads still asserts the FALSIFIED premise "
        "(the per-org byte attribution exists — #12762, "
        "postgres_bytes.PerOrgPostgresBytes.for_org):\n  "
        + "\n  ".join(offenders))


def test_the_gate_import_check_rejects_all_three_shapes():
    """The import-time half, tested BEHAVIOURALLY — a block with no release, an
    unknown token, or a release on an unblocked dimension is loud.

    ⛔ This used to grep the module source for the three message strings, and
    reviewer round 1 measured the hole: replacing each `if` with `if False:`
    leaves the strings present, the test GREEN, and the check dead — an inert
    enforcer guarding an inert enforcer. It calls the function now.

    Mutation this REDs: delete any arm of ``_check_release_conditions``.
    """
    base = dict(dimension="capture_extraction", limit_key="max_extraction_tokens",
                unit="tokens")

    # 1. BLOCKED with no declared release.
    blocked_no_release = allowance_gate.AllowanceDimension(
        **base, blocked_reason="blocked for the test")
    with pytest.raises(RuntimeError, match="BLOCKED with no declared release"):
        allowance_gate._check_release_conditions((blocked_no_release,))

    # 2. NOT blocked, but still carrying release conditions.
    enforced_with_release = allowance_gate.AllowanceDimension(
        **base, release=("vector_index_measurable",))
    with pytest.raises(RuntimeError, match="is not BLOCKED but declares"):
        allowance_gate._check_release_conditions((enforced_with_release,))

    # 3. An UNKNOWN token — a typo must not become a condition that never fires.
    unknown_token = allowance_gate.AllowanceDimension(
        **base, blocked_reason="blocked for the test",
        release=("vector_index_measurabl",))
    with pytest.raises(RuntimeError, match="does not define"):
        allowance_gate._check_release_conditions((unknown_token,))

    # …and the real registry passes, so the check is not just always-raising.
    # ⛔ RECORDS, not names and not a count. Round 5 measured the COUNT forgeable
    # (``RELEASE_CHECKED = len(DIMENSIONS)``); round 6 then measured the NAMES
    # forgeable by a one-line comprehension over the same registry. The records
    # carry the validated tokens, so a release declaration is compared through
    # the same path a real run produces.
    records = allowance_gate._check_release_conditions(DIMENSIONS)
    assert [(r.dimension, r.release) for r in records] == [
        (d.dimension, tuple(d.release)) for d in DIMENSIONS]


def test_the_release_check_actually_RAN_at_import():
    """Existence is not wiring: the check must have RUN, and that is observable.

    ⛔ The arms above prove the function raises; they cannot tell whether the
    module ever calls it. Deleting the call site would leave every other test
    green — the same "inert enforcer guarding an inert enforcer" shape this file
    exists to catch. ``RELEASE_CHECKED`` is the import-time call's RETURN VALUE, so
    deleting the call makes the name absent (AttributeError here) and deleting the
    assignment makes the module fail to import.

    ⛔ IT PINS A RECORD PRODUCED BY THE FUNCTION, not a value that resembles one.
    Round 5 measured the count form forgeable (``RELEASE_CHECKED = len(
    DIMENSIONS)``); round 6 measured the NAMES form forgeable too —
    ``RELEASE_CHECKED = tuple(d.dimension for d in DIMENSIONS)`` is a one-line
    edit that never calls the check and reproduced the published value, staying
    GREEN. So three properties are asserted now, and no single line produces
    them: each element exposes the VALIDATED tokens, its type is created inside
    the function (a qualname no other code resolves), and the seal is per-call
    (a second call cannot share it). Reproducing all three is reimplementing the
    function — the honest limit of this pin, stated instead of overclaimed.
    """
    records = allowance_gate.RELEASE_CHECKED
    assert [getattr(r, "dimension", r) for r in records] == [
        d.dimension for d in DIMENSIONS], (
        "the import-time release check validated "
        f"{[getattr(r, 'dimension', r) for r in records]!r}, not the declared "
        f"dimensions {[d.dimension for d in DIMENSIONS]!r} — a dimension can now "
        "carry a block nothing revalidates, or the published value was replaced "
        "by something that only looks like a run")
    assert [getattr(r, "release", None) for r in records] == [
        tuple(d.release) for d in DIMENSIONS], (
        "RELEASE_CHECKED carries release tokens that are not the ones the "
        "dimensions declare — the published value did not come from validating "
        "this registry")
    # (2) The record TYPE is one the function creates (its qualname resolves to no
    #     module attribute), so a plain tuple/namespace comprehension cannot stand
    #     in for it.
    record_type = type(records[0])
    assert record_type.__qualname__ == (
        "_check_release_conditions.<locals>._ValidatedBlock"), (
        "RELEASE_CHECKED is not made of records produced inside "
        f"_check_release_conditions (got {record_type.__qualname__!r}) — the "
        "import-time check can be bypassed with the suite green")
    # (3) The seal is PER CALL: every element of one run shares it, and a second
    #     run cannot. A module constant or an enumeration has no such object.
    assert all(r.seal is records[0].seal for r in records), (
        "RELEASE_CHECKED's elements do not share one run's seal, so they were "
        "not produced by a single validation pass")
    fresh = allowance_gate._check_release_conditions(DIMENSIONS)
    assert fresh[0].seal is not records[0].seal, (
        "two calls share one seal, so the seal is a constant rather than proof "
        "that the check RAN")


def test_the_database_grain_reader_no_longer_denies_the_per_org_byte_figure():
    """The THIRD copy of the falsified premise, pinned (#9559 R11).

    ``postgres_storage.ATTRIBUTION_NOTE`` travels on every database-grain reading
    and used to say only *"Postgres records no per-org byte ownership … per-org
    attribution exists for ROW GROWTH"* — which read, to anyone consulting the
    reader that ``allowance_gate``'s block cited, as "there is no per-org byte
    figure". #12762 created one. A reader must now be pointed at it instead of
    being left to conclude it cannot exist.

    The module's OWN scope is unchanged and asserted too: this reading is still
    DATABASE/TABLE-grain, and a split of the database total is still refused.
    """
    from tortoise.postgres_storage import ATTRIBUTION_NOTE

    assert "postgres_bytes" in ATTRIBUTION_NOTE, (
        "ATTRIBUTION_NOTE no longer names the per-org byte module that exists "
        f"(#12762) — a reader concludes it cannot exist:\n  {ATTRIBUTION_NOTE}")
    assert "ALLOCATION" in ATTRIBUTION_NOTE, (
        "the scope caveat is gone: a per-org split of the database TOTAL is "
        f"still an allocation, not a measurement:\n  {ATTRIBUTION_NOTE}")


# ── #9556 G5: the restore/rebuild door is NOT a billable write ────────────────

def test_the_record_restore_door_never_consults_an_allowance():
    """G5, pinned AT THE DOOR — not merely in the package it calls.

    ``rebuild_from_record`` WRITES the graph, so a byte gate inherited on that
    path would let the **billing** path defeat the **durability** path mid-rebuild
    — the failure #9556 declared the exemption for and #9559 was told to honour
    ("and say so in the wiring"). The exemption holds today *by construction*, and
    that is exactly the property an unrelated refactor removes silently.

    Mutation this REDs: call ``enforce_org_limit``/``enforce_allowances`` (or alias
    and call them) from the projection package, from ``record_restore`` — the
    operator door whose own docstring owns this constraint — or from the CLI verb.

    Scope, stated: these SOURCES. A behavioural test would need a live armed record
    and would prove one call path, while the hazard is any call path on the lane.
    """
    targets = [
        _REPO / "tortoise" / "record_restore.py",          # the G5 door
        _REPO / "tortoise" / "__main__.py",                # the `record-restore` verb
    ]
    targets.extend(sorted((_REPO / "tortoise" / "projection").rglob("*.py")))
    for path in targets:
        assert path.is_file(), f"G5 pin target is missing: {path}"

    offenders = []
    for path in targets:
        # ⛔ CODE ONLY: comments and string literals are stripped with `tokenize`
        # before matching. A substring match over raw lines reds on a COMMENT that
        # names the forbidden call — and #9559 explicitly asks this lane to name
        # it ("say so in the wiring"), so the guard would punish the documentation
        # it requires and the natural response would be to weaken the guard.
        # `record_restore.py` already carries a `QUOTA (G5 on #9556)` prose block;
        # that block must stay writable. An aliased import still offends, because
        # the NAME survives in code even when the call does not follow it.
        code = _code_only(path)
        for lineno, line in enumerate(code.splitlines(), 1):
            if "enforce_allowances" in line or "enforce_org_limit" in line:
                offenders.append(f"{path.relative_to(_REPO)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "the record restore/rebuild lane now consults an allowance — G5 (#9556) "
        "exempts it because it is a restore, not a billable write, and a "
        "fail-closed cap here refuses a tenant's own rebuild mid-wipe:\n  "
        + "\n  ".join(offenders))


#: Token kinds that are NOT code: comments, string literals, and the whitespace
#: bookkeeping. ⛔ ``FSTRING_MIDDLE`` is in the set because of PEP 701 (Py3.12):
#: an f-string's LITERAL TEXT is ``FSTRING_MIDDLE``, not ``STRING``, so without it
#: a comment-shaped f-string (``log(f"we never call enforce_allowances {x}")``)
#: still offends — the same false RED one syntax form over, on a lane #9559
#: explicitly asks to NAME the constraint. The ``{…}`` EXPRESSION tokens stay
#: NAME/OP, so a real call inside an f-string is still caught: skipping the literal
#: opens no hole. ``getattr`` keeps this importable on a Python without it.
_SKIP_TOKENS = frozenset({
    tokenize.COMMENT, tokenize.STRING, tokenize.NL, tokenize.NEWLINE,
    tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING, tokenize.ENDMARKER,
    getattr(tokenize, "FSTRING_MIDDLE", -1),
})


def _code_only(path: Path) -> str:
    """The file's source with comments AND string literals blanked out.

    Line numbers are preserved (blanked tokens become empty, comments become
    empty) so a reported line number still points at the offending code.
    """
    keep: dict[int, list[str]] = {}
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in _SKIP_TOKENS:
                continue
            keep.setdefault(tok.start[0], []).append(tok.string)
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(" ".join(keep.get(i, [])) for i in range(1, len(lines) + 1))


def _dispatch_bindings(path: Path) -> list[str]:
    """Lines that BIND ``args.cmd == "record-restore"`` to a CALL of its handler.

    ⛔ Token-level, not text-level. A line grep is satisfied by a docstring (one
    STRING token) or an inline comment, so a renamed dispatch literal with the
    old text left in prose stayed GREEN — measured, with ``ruff`` also clean
    (reviewer round 7). Matching the token SEQUENCE
    ``args . cmd == "record-restore"`` cannot be forged by prose: a docstring's
    literal text is a single token, never six.

    ⛔ AND THE BRANCH BODY MUST CALL THE HANDLER — a NAME token immediately
    followed by ``(`` — not merely contain the name somewhere within one line of
    the comparison. Both weaker forms were measured:
      * comparison-only: a branch gutted to ``return None`` left the handler
        orphaned while the check stayed green (round 8);
      * one-line substring: a live verb that gained one statement inside its
        branch went RED with the message "the door moved", while a ``STRING``
        token naming the handler satisfied it for a dead verb (round 9).
    The body is the contiguous code lines indented past the comparison, so a
    notice line above the real call is fine and prose cannot stand in for the
    call.
    """
    found: list[str] = []
    per_line: dict[int, list[str]] = {}
    col: dict[int, int] = {}
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                            tokenize.INDENT, tokenize.DEDENT,
                            tokenize.ENCODING, tokenize.ENDMARKER):
                continue
            per_line.setdefault(tok.start[0], []).append(tok.string)
            col.setdefault(tok.start[0], tok.start[1])
    lines = sorted(per_line)
    for pos, lineno in enumerate(lines):
        toks = per_line[lineno]
        for i in range(len(toks) - 5):
            window = toks[i:i + 6]
            if not (window[0] == "args" and window[1] == "."
                    and window[2] == "cmd" and window[3] == "=="
                    and window[4] == '"record-restore"'):
                continue
            # The branch BODY: following code lines, indented deeper than the
            # comparison, up to the first line that is not.
            body: list[str] = []
            for nxt in lines[pos + 1:]:
                if col[nxt] <= col[lineno]:
                    break
                body.extend(per_line[nxt])
            called = any(
                body[i] == "_cmd_record_restore" and body[i + 1] == "("
                for i in range(len(body) - 1))
            if not called:
                continue
            found.append(f"{lineno}: {' '.join(toks)}")
    return found


def test_the_restore_verb_still_exists():
    """The G5 pin above is scoped by names; if one moves, the pin must move too.

    ⛔ BOTH ends are pinned by CONTENT, round 3's finding: the scan set includes
    ``__main__.py`` (a 20k-line file), so if the ``record-restore`` verb moved out
    of it the pin would keep scanning a file that no longer holds the door —
    green and uncovered. The verb's name is asserted here for the same reason
    ``rebuild_from_record`` is.
    """
    from tortoise.projection import FalkorProjection

    assert callable(getattr(FalkorProjection, "rebuild_from_record", None)), (
        "rebuild_from_record is gone or renamed — the G5 exemption pin above "
        "scans a package whose restore lane no longer lives where it names")

    # ⛔ CODE AND A BINDING, never a raw-substring grep. Reviewer round 6 measured
    # the raw-text form: `record-restore` occurs in that file THREE times inside
    # comments and twice in code, so renaming the actual subcommand literal left
    # this assertion GREEN — it claimed a code fact that COMMENTS satisfied, the
    # exact class this file exists to catch, in the pin meant to guard the pin.
    # Two checks, each blind to prose:
    #   1. the handler IDENTIFIER resolves in CODE (`_code_only` blanks comments);
    #   2. the subcommand is BOUND to that handler on a real dispatch line — the
    #      literal is a string, which `_code_only` blanks, so it is matched on
    #      non-comment source lines, where the binding is the fact.
    main_path = _REPO / "tortoise" / "__main__.py"
    # ⛔ A `def`, WORD-BOUNDED. Reviewer round 7 measured the bare-substring form:
    # renaming the handler to `_cmd_record_restore_v2` at BOTH its definition and
    # its call site stayed GREEN (the substring survives), so the assertion did
    # not test the thing its own comment claims. Requiring the `def` — and the
    # `\b` before the name — REDs on that refactor and on a rename of the `def`
    # alone, which leaves a name that is called and does not resolve.
    handler_def = re.compile(r"\bdef _cmd_record_restore\s*\(")
    assert handler_def.search(_code_only(main_path)), (
        "the `record-restore` CLI handler is no longer DEFINED in "
        "tortoise/__main__.py's code — the G5 pin keeps scanning that file, so "
        "the door would be uncovered. Add the verb's new home to the scan set "
        "above.")
    # ⛔ A TOKEN SUBSEQUENCE, not a substring on a non-`#` line. Reviewer round 7
    # measured the weaker form: a DOCSTRING line reading
    # `Dispatch contract: args.cmd == "record-restore" routes here.` satisfied it
    # while the dispatch compared against a renamed literal — a dead verb, green,
    # and `ruff` clean. Tokenising with STRING KEPT makes a docstring one STRING
    # token, which can never form the comparison's token sequence.
    bound = _dispatch_bindings(main_path)
    assert bound, (
        'no code line binds `args.cmd == "record-restore"` to its handler in '
        "tortoise/__main__.py — the subcommand is quoted only in prose, renamed, "
        "or its branch no longer CALLS the handler, so the G5 pin's premise (a "
        "door a tenant reaches) is false. Re-derive which file now holds the "
        "restore door and add it to the G5 scan set")



