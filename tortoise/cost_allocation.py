"""#4493 — fixed/shared SaaS cost ALLOCATION (showback, not a measurement).

WHAT THIS IS
------------
The fixed / shared monthly SaaS lines (Fly base, FalkorDB base, Supabase
base, PostHog, Sentry) arrive as **one bill with no per-org attribution**.
No measurement can split them: nothing observes "this org's share of the Fly
machine". What can be done — and what this module does — is declare a
mechanical **allocation rule** and apply it.

An allocation is **not a measurement**. The number published here is a POLICY
applied to a stated total, not an observation. It is named as such in the
metric help, in the snapshot, and in ``docs/ops/cost-allocation.md``. No price,
tier, cap, quota or recorded decision is decided here.

THE RULE
--------
Per line, declared in :data:`ALLOCATION_LINES`:

* ``total_cents`` — the stated **monthly** amount, with ``source``, ``as_of``
  and ``is_estimate``. The in-repo figures are rates/estimates, never a
  measured invoice (B7 audit: "invoice amounts — not in-repo"), so every line
  carries its provenance and an estimate flag.
* ``basis`` — how the total is split:

  - :data:`BASIS_EVEN` — indivisible base fees (one machine, one project):
    an equal share per org.
  - :data:`BASIS_PROPORTIONAL` — usage-driven lines: the share is proportional
    to the org's **measured** write-ops for its current metering window.

Rounding is integer Hamilton **largest-remainder**: the shares always sum to
the line total exactly, and anything that cannot be allocated lands in an
explicit residual bucket (:data:`RESIDUAL_ORG`) rather than being smeared.

FAIL-CLOSED
-----------
An unreadable input is **never** a zero. If the org enumeration is unavailable
(:func:`evaluate_allocation` is passed a falsy ``orgs``), or a proportional
line cannot read an org's usage, the line's state is
:data:`~tortoise.activation_scorecard.STATE_UNAVAILABLE` and **no share is
published for it**. A confident zero for an unreadable input would silently
redistribute that org's share to every other org, invisibly — the exact
failure mode ``docs/runbook/b7-activation-scorecard.md`` §"Zero vs no-signal"
exists to prevent. The state vocabulary is IMPORTED from
:mod:`tortoise.activation_scorecard` (one home for the three strings).

A MALFORMED ``TORTOISE_COST_*_CENTS`` override is unreadable input too. A
present-but-unusable value (non-integer, negative, present-but-empty, or above
:data:`MAX_LINE_CENTS`) is **not** silently replaced by the declared default —
the line is reported :data:`STATE_UNAVAILABLE` with a detail naming the
variable and the offending value. Falling back to the default would make a
misconfiguration indistinguishable from "unconfigured", and for four of the
five lines that default is 0.

WINDOW
------
The declared total is **monthly** (the vendor's window). The allocation window
is reported on every snapshot. The org's own metering window is
subscription-anchored (D10/D13, ``metering.py::_current_period``) and is
deliberately **not** used to prorate: the full declared total is allocated
across the orgs in the refresh. Proration across differing windows is deferred
to the #5045 substrate.

WHAT THIS IS NOT
----------------
* Not written into ``metering_records`` — that ledger's cost columns are
  "MEASURED metres, never billing estimates" (``metering.py:967``) and the
  #3665 cohort ceiling sums them. An allocation there would corrupt the cap.
* Not a price, a tier boundary, a cap or a quota.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from . import monitoring
from .activation_scorecard import (
    STATE_MEASURED,
    STATE_NOT_MEASURABLE,
    STATE_UNAVAILABLE,
)

logger = logging.getLogger("tortoise.cost_allocation")

#: Allocation bases. Declared per line, never assumed.
BASIS_EVEN = "even"
BASIS_PROPORTIONAL = "proportional"
_BASES = (BASIS_EVEN, BASIS_PROPORTIONAL)

#: Fixed child of the allocation metric: the explicit residual bucket. Never
#: org-derived. (There is deliberately no ``__unreadable__`` sentinel: an
#: unreadable input leaves the metric at LAST-KNOWN-GOOD rather than publishing
#: a synthetic child — see :func:`publish`.)
RESIDUAL_ORG = "__residual__"
#: Org-label overflow child (bounded cardinality — a per-org label is a
#: cardinality axis, so it is capped exactly like a request/tenant label is).
ORG_OVERFLOW = "__other__"
MAX_ORG_LABELS = 512

#: Sanity ceiling for an operator override (cents/month). A figure above this is
#: a misconfiguration (almost certainly a unit typo — dollars, or cents×100),
#: not a real monthly line: publishing it would allocate a nonsense amount with
#: no error. 100_000_000 cents = $1,000,000/month.
MAX_LINE_CENTS = 100_000_000


@dataclass(frozen=True)
class LineSpec:
    """One declared fixed/shared SaaS line — the RULE, not a measurement."""

    name: str
    basis: str
    total_cents: int
    source: str
    as_of: str
    is_estimate: bool
    env_var: str


#: The declared rule. ``total_cents`` defaults to the best in-repo figure where
#: one EXISTS and to 0 ("not configured") where none does — 0 is a deliberate
#: "this line must be configured by an operator", not a claim that the cost is
#: zero. ``fly_base`` defaults to the dated list-price estimate already
#: recorded in ``fly.toml`` (a LIST-PRICE ESTIMATE, not a quote — hence
#: ``is_estimate=True`` and the explicit ``as_of``).
ALLOCATION_LINES: tuple[LineSpec, ...] = (
    LineSpec(
        name="fly_base",
        basis=BASIS_EVEN,
        total_cents=2140,
        source="fly.toml COST note (iad list price snapshot; shared-cpu-2x:4096MB)",
        as_of="2026-09-10",
        is_estimate=True,
        env_var="TORTOISE_COST_FLY_BASE_CENTS",
    ),
    LineSpec(
        name="falkordb_base",
        basis=BASIS_EVEN,
        total_cents=0,
        source="not configured — supply the vendor figure via the env var",
        as_of="unset",
        is_estimate=True,
        env_var="TORTOISE_COST_FALKORDB_BASE_CENTS",
    ),
    LineSpec(
        name="supabase_base",
        basis=BASIS_EVEN,
        total_cents=0,
        source="not configured — supply the vendor figure via the env var",
        as_of="unset",
        is_estimate=True,
        env_var="TORTOISE_COST_SUPABASE_BASE_CENTS",
    ),
    LineSpec(
        name="posthog",
        basis=BASIS_PROPORTIONAL,
        total_cents=0,
        source="not configured — supply the vendor figure via the env var",
        as_of="unset",
        is_estimate=True,
        env_var="TORTOISE_COST_POSTHOG_CENTS",
    ),
    LineSpec(
        name="sentry",
        basis=BASIS_PROPORTIONAL,
        total_cents=0,
        source="not configured — supply the vendor figure via the env var",
        as_of="unset",
        is_estimate=True,
        env_var="TORTOISE_COST_SENTRY_CENTS",
    ),
)


def _assert_lines_valid(lines: Iterable[LineSpec]) -> None:
    """Module-load invariant: a malformed declaration must fail at IMPORT.

    An allocation rule that is silently malformed allocates MONEY wrongly with
    no error — so an unknown basis, a negative total, a blank name/source or a
    duplicate line is rejected before the module is usable.
    """
    seen: set[str] = set()
    for line in lines:
        if not line.name or not line.name.strip():
            raise ValueError("allocation line has a blank name")
        if line.name in seen:
            raise ValueError(f"duplicate allocation line: {line.name}")
        seen.add(line.name)
        if line.basis not in _BASES:
            raise ValueError(
                f"allocation line {line.name!r} has unknown basis {line.basis!r}; "
                f"expected one of {_BASES}")
        if not isinstance(line.total_cents, int) or isinstance(line.total_cents, bool):
            raise ValueError(
                f"allocation line {line.name!r} total_cents must be an int")
        if line.total_cents < 0:
            raise ValueError(
                f"allocation line {line.name!r} total_cents is negative")
        if not line.source or not line.source.strip():
            raise ValueError(f"allocation line {line.name!r} has no source")
        if not line.as_of or not line.as_of.strip():
            raise ValueError(f"allocation line {line.name!r} has no as_of")
        if not line.env_var or not line.env_var.strip():
            raise ValueError(f"allocation line {line.name!r} has no env_var")


_assert_lines_valid(ALLOCATION_LINES)


def declared_lines() -> tuple[LineSpec, ...]:
    """The declared allocation rule (the reviewable contract)."""
    return ALLOCATION_LINES


@dataclass(frozen=True)
class LineTotal:
    """One line's effective total, its provenance, and its override error.

    ``error`` is ``None`` when the effective total is usable. A PRESENT but
    unusable override yields a detail naming the variable and offending value;
    the line is then reported :data:`STATE_UNAVAILABLE` (see
    :func:`_resolve_line_total`).
    """

    cents: int
    source: str
    as_of: str
    is_estimate: bool
    error: str | None


def _resolve_line_total(line: LineSpec, *, observed_as_of: str) -> LineTotal:
    """Resolve *line*'s total AND its provenance, fail-closed on a bad override.

    The env override exists so an operator can supply a real invoice figure
    WITHOUT a code change. It is resolved as follows:

    * **absent** — the declared default, with the declaration's own
      ``source``/``as_of``/``is_estimate`` (it IS the declaration).
    * **present and usable** — the operator's figure. Provenance is the
      OVERRIDE, not the declaration: ``source`` names the env var, ``as_of`` is
      the observation date (the refresh date — the declaration's date would be
      a lie about where the number came from), and ``is_estimate`` is ``False``
      (an operator-supplied invoice is a real figure, not an estimate).
    * **present and unusable** (non-integer, negative, present-but-empty, or
      above :data:`MAX_LINE_CENTS`) — an explicit error, never a silent
      fallback to the declared default: ``error`` names the variable and value,
      and :func:`evaluate_allocation` reports the line ``unavailable``.
    """
    raw = os.environ.get(line.env_var)
    if raw is None:
        return LineTotal(line.total_cents, line.source, line.as_of,
                         line.is_estimate, None)
    text = str(raw).strip()
    if not text:
        reason = f"is present but empty ({raw!r})"
    else:
        try:
            value = int(text)
        except (TypeError, ValueError):
            reason = f"is not an integer number of cents ({raw!r})"
        else:
            if value < 0:
                reason = f"is negative ({raw!r})"
            elif value > MAX_LINE_CENTS:
                reason = (f"exceeds the sanity ceiling of {MAX_LINE_CENTS} "
                          f"cents/month ({raw!r})")
            else:
                return LineTotal(
                    value, f"env override {line.env_var}", observed_as_of,
                    False, None)
    detail = f"{line.env_var} {reason}"
    logger.error(
        "%s — line %s reported %s (fail-closed; the declared default is not "
        "used as an allocated total)", detail, line.name, STATE_UNAVAILABLE)
    return LineTotal(line.total_cents, line.source, line.as_of,
                     line.is_estimate, detail)


def line_total_cents(line: LineSpec) -> int:
    """The effective monthly total in cents for *line* (value-only accessor).

    FAIL-CLOSED: a present-but-unusable override **raises** ``ValueError``
    (carrying the same detail :func:`_resolve_line_total` logs) rather than
    returning the DECLARED DEFAULT. The default is not a usable total here —
    for four of the five lines it is 0, i.e. indistinguishable from
    "unconfigured", so silently returning it would make a misconfiguration look
    like an ordinary number. "Unconfigured" (the variable absent) still yields
    the declared default, and is not an error.

    Callers that must OBSERVE the state rather than handle an error should use
    :func:`evaluate_allocation`, which reports the line ``unavailable`` with no
    share emitted (the same fail-closed path).
    """
    resolved = _resolve_line_total(line, observed_as_of=line.as_of)
    if resolved.error is not None:
        raise ValueError(resolved.error)
    return resolved.cents


@dataclass(frozen=True)
class OrgShare:
    """One org's allocated slice of a line (in integer cents)."""

    org_label: str
    cents: int


@dataclass(frozen=True)
class LineAllocation:
    """One line's allocation: the rule, its state, the shares and residual."""

    line: str
    basis: str
    total_cents: int
    source: str
    as_of: str
    is_estimate: bool
    state: str
    shares: tuple[OrgShare, ...]
    residual_cents: int
    detail: str


@dataclass(frozen=True)
class AllocationSnapshot:
    """The readable, state-carrying allocation figure.

    ``state`` is the whole-snapshot roll-up, NOT "the worst state across the
    lines": :data:`STATE_UNAVAILABLE` if the enumeration could not be read OR
    any single line could not be read; :data:`STATE_NOT_MEASURABLE` only when
    EVERY line is not_measurable (all declared totals 0); otherwise
    :data:`STATE_MEASURED`. Because the four zero-default lines report
    ``not_measurable``, a default snapshot is ``measured`` while most lines are
    unconfigured — the PER-LINE state carries that partiality. ``window`` names
    the allocation window; the unit is integer CENTS.
    """

    generated_at: str
    window_start: str
    window_end: str
    unit: str
    state: str
    enumeration_available: bool
    unallocated_cents: int
    lines: tuple[LineAllocation, ...]

    def to_dict(self) -> dict:
        """JSON-able form — the person-readable query (indicator 3)."""
        return {
            "generated_at": self.generated_at,
            "window": {"start": self.window_start, "end": self.window_end},
            "unit": self.unit,
            "state": self.state,
            "kind": "allocation",
            "enumeration_available": self.enumeration_available,
            "unallocated_cents": self.unallocated_cents,
            "lines": [
                {
                    "line": ln.line,
                    "basis": ln.basis,
                    "total_cents": ln.total_cents,
                    "source": ln.source,
                    "as_of": ln.as_of,
                    "is_estimate": ln.is_estimate,
                    "state": ln.state,
                    "residual_cents": ln.residual_cents,
                    "detail": ln.detail,
                    "shares": {s.org_label: s.cents for s in ln.shares},
                }
                for ln in self.lines
            ],
        }


def monthly_allocation_window(now: datetime | None = None) -> tuple[str, str]:
    """The UTC calendar month containing *now*, half-open ``[start, end)``.

    ISO-8601 strings. This is the window the DECLARED monthly totals are stated
    over. It is deliberately not the org's subscription-anchored meter window
    (see the module docstring §WINDOW).
    """
    now = now or datetime.now(UTC)
    start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return start.isoformat(), end.isoformat()


def allocate_largest_remainder(
    total_cents: int,
    weights: Mapping[str, int],
    *,
    residual_key: str = RESIDUAL_ORG,
) -> dict[str, int]:
    """Split *total_cents* across *weights* — integer, exact, deterministic.

    Hamilton largest-remainder: every key gets ``floor(total * w_i / W)``, then
    the leftover cents go one each to the largest fractional remainders, ties
    broken lexicographically by key (so two runs over the same input are
    identical — a re-run must never reshuffle a cent).

    * A zero (or empty) weight sum parks the **whole** total in
      ``residual_key`` — the explicit, first-class residual bucket. It is never
      smeared over zero-weight keys and never dropped.
    * Negative weights, non-integer weights and a negative total raise
      ``ValueError``: a malformed allocation is a money bug, not a warning.
    * The result always sums to ``total_cents``.
    """
    if not isinstance(total_cents, int) or isinstance(total_cents, bool):
        raise ValueError("total_cents must be an int")
    if total_cents < 0:
        raise ValueError(f"total_cents is negative: {total_cents}")
    for key, weight in weights.items():
        if not isinstance(weight, int) or isinstance(weight, bool):
            raise ValueError(f"weight for {key!r} must be an int, got {weight!r}")
        if weight < 0:
            raise ValueError(f"weight for {key!r} is negative: {weight}")
    if total_cents == 0:
        return {}
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        return {residual_key: total_cents}

    floors: dict[str, int] = {}
    remainders: list[tuple[int, str]] = []
    assigned = 0
    for key, weight in weights.items():
        exact = total_cents * weight
        floor = exact // weight_sum
        floors[key] = floor
        assigned += floor
        remainders.append((exact % weight_sum, key))
    leftover = total_cents - assigned
    # Largest remainder first; lexicographic key breaks ties deterministically.
    remainders.sort(key=lambda pair: (-pair[0], pair[1]))
    for _, key in remainders[:leftover]:
        floors[key] += 1
    return floors


def _even_weights(orgs: Iterable[str]) -> dict[str, int]:
    return {org: 1 for org in orgs}


def evaluate_allocation(
    orgs: Iterable[str] | None,
    *,
    now: datetime | None = None,
    weights_by_org: Mapping[str, int] | None = None,
) -> AllocationSnapshot:
    """Apply the declared rule to *orgs* and return the state-carrying result.

    ``orgs`` is the registered org set. A **falsy** value (``None`` or ``[]``)
    means the enumeration could not be confirmed — ``_iter_registered_orgs``
    returns ``[]`` on ANY control-plane failure — so the whole snapshot is
    :data:`STATE_UNAVAILABLE` and no share is emitted. It is NEVER read as
    "a fleet with no orgs" and NEVER as zero cost.

    ``weights_by_org`` supplies the measured per-org basis for proportional
    lines. ``None`` means the proportional basis could not be read: those lines
    are :data:`STATE_UNAVAILABLE`. A missing key for one org makes that line
    unavailable too — a partial basis would silently redistribute the missing
    org's share.
    """
    start, end = monthly_allocation_window(now)
    moment = now or datetime.now(UTC)
    generated = moment.isoformat()
    # The date an env OVERRIDE was observed: an override's ``as_of`` must be
    # the observation date, never the declaration's date (that would claim the
    # operator's invoice was stated by the in-repo declaration).
    observed_as_of = moment.date().isoformat()
    org_list = [o for o in (orgs or []) if o]
    if not org_list:
        return AllocationSnapshot(
            generated_at=generated, window_start=start, window_end=end,
            unit="cents", state=STATE_UNAVAILABLE, enumeration_available=False,
            unallocated_cents=0,
            lines=tuple(
                LineAllocation(
                    line=ln.name, basis=ln.basis,
                    total_cents=rt.cents, source=rt.source,
                    as_of=rt.as_of, is_estimate=rt.is_estimate,
                    state=STATE_UNAVAILABLE, shares=(), residual_cents=0,
                    detail="org enumeration unavailable or empty (fail-closed)",
                )
                for ln in ALLOCATION_LINES
                for rt in (_resolve_line_total(ln, observed_as_of=observed_as_of),)
            ),
        )

    lines: list[LineAllocation] = []
    for ln in ALLOCATION_LINES:
        rt = _resolve_line_total(ln, observed_as_of=observed_as_of)
        total, source, as_of, is_estimate = (
            rt.cents, rt.source, rt.as_of, rt.is_estimate)
        if rt.error is not None:
            # A present-but-unusable override is an unreadable input: fail
            # closed as `unavailable`, NEVER the declared default (which is 0
            # for four of the five lines, i.e. indistinguishable from
            # "unconfigured").
            lines.append(LineAllocation(
                line=ln.name, basis=ln.basis, total_cents=total,
                source=source, as_of=as_of, is_estimate=is_estimate,
                state=STATE_UNAVAILABLE, shares=(), residual_cents=0,
                detail=rt.error,
            ))
            continue
        if ln.basis == BASIS_PROPORTIONAL:
            if weights_by_org is None:
                lines.append(LineAllocation(
                    line=ln.name, basis=ln.basis, total_cents=total,
                    source=source, as_of=as_of, is_estimate=is_estimate,
                    state=STATE_UNAVAILABLE, shares=(), residual_cents=0,
                    detail="measured basis unreadable (fail-closed)",
                ))
                continue
            missing = [o for o in org_list if o not in weights_by_org]
            if missing:
                lines.append(LineAllocation(
                    line=ln.name, basis=ln.basis, total_cents=total,
                    source=source, as_of=as_of, is_estimate=is_estimate,
                    state=STATE_UNAVAILABLE, shares=(), residual_cents=0,
                    detail=f"basis missing for {len(missing)} org(s) (fail-closed)",
                ))
                continue
            weights = {o: int(weights_by_org[o]) for o in org_list}
        else:
            weights = _even_weights(org_list)

        split = allocate_largest_remainder(total, weights)
        shares = tuple(
            OrgShare(org_label=org, cents=cents)
            for org, cents in sorted(split.items())
            if org != RESIDUAL_ORG
        )
        residual = split.get(RESIDUAL_ORG, 0)
        if total == 0:
            state, detail = STATE_NOT_MEASURABLE, "declared total is 0 (unconfigured)"
        elif sum(weights.values()) <= 0:
            state, detail = STATE_NOT_MEASURABLE, "no org carries weight; total parked in residual"
        else:
            state, detail = STATE_MEASURED, ""
        lines.append(LineAllocation(
            line=ln.name, basis=ln.basis, total_cents=total, source=source,
            as_of=as_of, is_estimate=is_estimate, state=state,
            shares=shares, residual_cents=residual, detail=detail,
        ))

    unallocated = sum(ln.residual_cents for ln in lines)
    # Summary state: the enumeration is the whole-fleet input, so its absence
    # dominates; otherwise an unreadable LINE makes the figure partial
    # (honest), and only an all-unconfigured declaration is `not_measurable`.
    if any(ln.state == STATE_UNAVAILABLE for ln in lines):
        state = STATE_UNAVAILABLE
    elif all(ln.state == STATE_NOT_MEASURABLE for ln in lines):
        state = STATE_NOT_MEASURABLE
    else:
        state = STATE_MEASURED
    return AllocationSnapshot(
        generated_at=generated, window_start=start, window_end=end,
        unit="cents", state=state, enumeration_available=True,
        unallocated_cents=unallocated,
        lines=tuple(lines),
    )


def _bounded_org_labels(labels: Iterable[str]) -> dict[str, str]:
    """Map org ids onto the metric's bounded label space.

    A per-org Prometheus label is a cardinality axis: at most
    :data:`MAX_ORG_LABELS` org children are admitted (in sorted order, so the
    admitted set is deterministic) and every other org folds into the fixed
    :data:`ORG_OVERFLOW` child. Traffic and org creation can therefore never
    grow the child set without bound.
    """
    unique = sorted({label for label in labels if label})
    admitted = {label: label for label in unique[:MAX_ORG_LABELS]}
    for label in unique[MAX_ORG_LABELS:]:
        admitted[label] = ORG_OVERFLOW
    return admitted


_lock = threading.Lock()
_last_snapshot: AllocationSnapshot | None = None
#: The window of the values CURRENTLY in the metric — i.e. of the last
#: SUCCESSFUL publish. Deliberately distinct from ``_last_snapshot`` (the last
#: ATTEMPT, which may itself be ``unavailable``): on an unavailable refresh the
#: metric keeps last-known-good values, so only a successful publish moves this.
#: It exists so a log line can name the period a figure actually belongs to
#: instead of the period that was attempted (a reader correlating logs with
#: values would otherwise be misled across a month rollover).
_last_published_window: tuple[str, str] | None = None


def _retained_window() -> tuple[str, str]:
    """The window the metric's current values belong to (or placeholders)."""
    return _last_published_window or ("unknown", "unknown")


def publish(snapshot: AllocationSnapshot) -> None:
    """Project *snapshot* onto the per-org cost metric (the SINGLE writer path).

    The metric carries the org's TOTAL allocated fixed/shared cost across all
    lines, in cents.

    THE RULE: a snapshot whose whole-snapshot ``state`` is
    :data:`STATE_UNAVAILABLE` — whether the ORG ENUMERATION failed
    (``enumeration_available`` is False) or a single LINE could not be read
    (the enumeration succeeded) — leaves the metric at **last-known-good**: it
    is neither cleared nor partially re-recorded. A partial re-record would
    silently DROP every org's published total (the unreadable line's share
    simply vanishes, with no state on the metric to say so), which reads
    exactly like "this org's cost fell" — the failure this module exists to
    avoid. The warning distinguishes the two shapes via
    ``enumeration_available`` AND names the window the retained values belong
    to (the PREVIOUS successful one) — the attempted window is explicitly NOT
    the one the metric carries.

    On success the new children are recorded FIRST and only the children no
    longer present are pruned, so a concurrent ``/metrics`` scrape served by
    another thread can never observe an empty or partial family (a
    clear-then-set would expose exactly that gap on every refresh).
    """
    global _last_snapshot, _last_published_window
    if snapshot.state == STATE_UNAVAILABLE:
        retained_start, retained_end = _retained_window()
        logger.warning(
            "cost allocation refresh unavailable (enumeration_available=%s) — "
            "metric left at last-known-good (values still belong to the "
            "PREVIOUS successful window %s→%s; attempted window %s→%s was NOT "
            "published)",
            snapshot.enumeration_available,
            retained_start, retained_end,
            snapshot.window_start, snapshot.window_end)
        with _lock:
            _last_snapshot = snapshot
        return

    totals: dict[str, int] = {}
    for line in snapshot.lines:
        if line.state == STATE_UNAVAILABLE:
            continue
        for share in line.shares:
            totals[share.org_label] = totals.get(share.org_label, 0) + share.cents
        if line.residual_cents:
            totals[RESIDUAL_ORG] = totals.get(RESIDUAL_ORG, 0) + line.residual_cents

    bounded = _bounded_org_labels(totals)
    merged: dict[str, int] = {}
    for label, cents in totals.items():
        merged[bounded[label]] = merged.get(bounded[label], 0) + cents

    # Record first, prune second: never clear the whole family up front. A
    # reader on the /metrics thread must not be able to observe a gap between
    # the clear and the re-record.
    for label, cents in sorted(merged.items()):
        monitoring.record_cost(label, cents)
    monitoring.prune_team_cost(set(merged))
    with _lock:
        _last_published_window = (snapshot.window_start, snapshot.window_end)


def refresh_and_publish(
    orgs: Iterable[str] | None,
    *,
    now: datetime | None = None,
    weights_by_org: Mapping[str, int] | None = None,
) -> AllocationSnapshot:
    """The single production write path: evaluate, publish, log, reconcile.

    Returns the snapshot (also stored for :func:`current_snapshot`). Never
    raises for an ordinary allocation/DB problem: a cost refresh must not be
    able to take down the caller (it runs inside the hosted retention loop,
    which has no per-iteration guard). It DOES log loudly on the two states
    that would otherwise pass silently — an unavailable refresh, and a
    published set that does not reconcile to the declared totals.
    """
    global _last_snapshot
    snapshot = evaluate_allocation(orgs, now=now, weights_by_org=weights_by_org)
    publish(snapshot)
    _reconcile_and_log(snapshot)
    with _lock:
        _last_snapshot = snapshot
    return snapshot


def _reconcile_and_log(snapshot: AllocationSnapshot) -> None:
    """Log one line per refresh; WARN if the PUBLISHED metric does not reconcile.

    Reconciliation reads what was ACTUALLY published — :func:`allocation_by_org`,
    the metric itself — against the declared totals. The previous form summed
    the snapshot's OWN shares, so both sides came from the same sum-exact
    function and the mismatch branch could never fire (a dead hook). The
    published sum includes the explicit :data:`RESIDUAL_ORG` and
    :data:`ORG_OVERFLOW` children: both are part of what a reader sees, so both
    count. On an ``unavailable`` snapshot :func:`publish` left the metric at the
    previous window, so there is nothing to reconcile against this
    declaration — the check is skipped rather than reported as a false
    mismatch. Never raises: a cost refresh must not be able to take down the
    caller (it runs inside the hosted retention loop, which has no
    per-iteration guard).
    """
    published_by_org = allocation_by_org()
    published = sum(published_by_org.values())
    declared = sum(ln.total_cents for ln in snapshot.lines)
    residual = published_by_org.get(RESIDUAL_ORG, 0)
    overflow = published_by_org.get(ORG_OVERFLOW, 0)
    orgs = len([k for k in published_by_org
                if k not in (RESIDUAL_ORG, ORG_OVERFLOW)])
    # ``attempted_window`` is the window this refresh evaluated;
    # ``published_window`` is the window the values READ BACK from the metric
    # belong to. The two are equal whenever the last successful publish is in
    # the SAME window; they differ whenever the retained window differs from
    # the attempted one — including an ``unavailable`` refresh whose attempt
    # falls in a LATER window (the metric still carries the last-known-good
    # values then), and the never-published case where nothing has ever been
    # published and ``published_window=unknown..unknown``. Naming both means a
    # reader can never attribute a stale figure to the attempted period.
    attempted_start, attempted_end = snapshot.window_start, snapshot.window_end
    retained_start, retained_end = _retained_window()
    logger.info(
        "cost allocation refresh kind=allocation state=%s "
        "attempted_window=%s..%s published_window=%s..%s "
        "lines=%d orgs=%d published_cents=%d residual_cents=%d overflow_cents=%d",
        snapshot.state, attempted_start, attempted_end,
        retained_start, retained_end,
        len(snapshot.lines), orgs, published, residual, overflow,
    )
    if snapshot.state == STATE_UNAVAILABLE:
        return
    if published != declared:
        logger.warning(
            "cost allocation does NOT reconcile: published=%d declared=%d "
            "delta=%d residual=%d overflow=%d (an allocation bug — the "
            "published set must sum to the declared totals)",
            published, declared, published - declared, residual, overflow,
        )


def current_snapshot() -> AllocationSnapshot | None:
    """Last published snapshot (the in-process readable query)."""
    with _lock:
        return _last_snapshot


def allocation_by_org() -> dict[str, int]:
    """The metric's current per-org values (integer cents), by org label.

    Reads the Prometheus family directly (bare sample names — a Gauge's samples
    carry no ``_total`` suffix, so the counter-shaped helpers would read 0).
    """
    return monitoring.team_cost_cents()


def _reset_for_tests() -> None:
    """Test seam: clear the process-global metric and the snapshot cache."""
    global _last_snapshot, _last_published_window
    monitoring.clear_team_cost()
    with _lock:
        _last_snapshot = None
        _last_published_window = None
