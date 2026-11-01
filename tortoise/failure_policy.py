"""§4.4 G3 — S3's FAILURE POLICY: an unresolved identity is never asserted `new` (#5095).

WHY THIS MODULE EXISTS
======================
``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`` §4.4 declares invariant
**I1**:

    "An unresolved identity is NEVER asserted ``new``."

and says of itself, in its closing paragraph, that it *"is a SPEC, not a
description of what runs"*. The shipped code asserted the **inverse** in two
places, and this module is the decision both of them now consult:

* the **S2/S4 prompt** (`_render_search_results`) told the model *"no existing
  items available; assume everything is new"* whenever S3 degraded;
* the **commit record** (`execute_embed`'s ``link_before_create``) said
  ``found: false`` — *"no match — created"* — for every created candidate,
  even when the lookup had never run.

Both are the same defect one layer apart: **a claim about a check that did not
happen.** §4.4 names the harm: the natural fail-open *manufactures duplicates*,
the exact failure S3 exists to prevent.

WHAT IS DECIDED HERE, AND WHAT IS NOT
=====================================
DECIDED (pure: no I/O, no clock, no model, no graph):

* **the lookup's state** — ``resolved`` / ``partial`` / ``unresolved``, read off
  what ``search_graph`` reports about the queries it *actually ran*
  (``lookup_state``);
* **the prompt clause** the unresolved/partial states render
  (``lookup_note``);
* **the record polarity** for a candidate the run creates
  (``created_record``) — ``found: false`` only when THAT candidate's own query
  ran and matched nothing, ``found: None`` when it did not;
* **the run-level report** of how much is unresolved and why
  (``plan_identity``).

NOT DECIDED HERE (deliberately, each with its named owner):

* **whether the run writes at all.** I1's *"never mint it"* needs a **create
  gate inside S5**, and it is the next slice: ``execute_embed`` is the sole
  carrier for retractions (whose same-session leg needs no graph lookup at
  all), supersessions and chain notes, so suppressing it wholesale would drop
  memory I2 forbids dropping. This module therefore makes the run **honest**,
  never silent — and it does not suppress anything.
* **the carry-forward store.** §4.4's ``HOLD`` set is *"carried forward for a
  later run"*; nothing persists it and there is no store to persist it into.
* **a census class.** ``tools/longmem_eval/report.py`` grades a question
  ``hard`` for ANY census class outside ``RECOVERABLE_CENSUS_CLASSES``, so
  minting one for a *tolerated, documented* degradation (``#3679``: degrade,
  never raise) would turn the instrument into a verdict. The loud channel is
  the run's ``warnings`` entry plus the ``identity`` block.

THE THREE STATES — and why the middle one exists
================================================
``resolved``    every identity query the run derived actually ran and returned.
``partial``     the lookup ran, but it was **bounded**: the query budget
                (``search_graph``'s ``max_queries``, default 15) cut the tail, or
                a later query raised and was swallowed (``except: continue``),
                or the coverage report is malformed. A miss in an unrun leg is
                not evidence of absence, and a *run-level* claim cannot be
                resolved per candidate — so this state is what makes the
                per-candidate attribution in ``created_record`` possible at all.
``unresolved``  the lookup did not run: no searchable backend, no graph client,
                or the first query raised (``search_graph`` then returns zero
                priors).

⚠️ ``partial`` IS THE COMMON STATE, and pretending otherwise was a defect this
change measured: on a real S2 output the query derivation produced **18**
queries against the default budget of **15**, and because the legs are iterated
``object`` → ``event`` → ``point`` the tail that is cut is the **point** leg —
the leg whose queries are the dedup ones. A two-state reading would have to
choose between calling every substantial session "unknown" (making the signal a
constant) and calling it resolved (the false claim this module exists to remove).

⚠️ **Coverage absent ⇒ ``resolved``**, on purpose and stated: a ``search`` dict
built by anything other than ``search_graph`` (a test fixture, a tool) carries
no coverage report, and the pipeline's PRODUCER always supplies one. The
fallback keeps every such caller byte-identical to the pre-``#5095`` behaviour
instead of relabelling it; it cannot hide a pipeline miss, because the pipeline
never reaches it.

⚠️ **A degraded search returns ZERO priors** (measured, ``#5095``): ``_fts_rows``
materialises a list, so a failure cannot leak rows mid-query, and the outer
degrade handler is reachable only from the *first* query. The partial shape is
the **swallowed later failure**, which is why coverage — not ``degraded`` — is
the load-bearing input here.

⛔ **NOT a new VET outcome.** §4.4 keeps ``HOLD`` out of ``vet_gate.OUTCOMES``:
a verdict about validity and a statement about whether a lookup ran are
different facts, and ``tests/test_extractor_v4_failure_policy.py`` cross-checks
that the shipped vocabulary still has no member for it.

⛔ **No query-class input, no model, no graph.** This module reads the state of a
lookup, never a question's type (§12.4's rejected classifier, a different
mechanism in a different module).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = [
    "FOUND_ABSENT",
    "FOUND_UNRESOLVED",
    "LEGS",
    "LOOKUP_PARTIAL",
    "LOOKUP_RESOLVED",
    "LOOKUP_STATES",
    "LOOKUP_UNRESOLVED",
    "created_record",
    "lookup_note",
    "lookup_state",
    "plan_identity",
]

#: The three states of the identity lookup. ``partial`` is FIRST-CLASS, never a
#: synonym for either neighbour: an unassessed *candidate* is not a negative
#: finding, and a bounded lookup is not a complete one.
LOOKUP_RESOLVED = "resolved"
LOOKUP_PARTIAL = "partial"
LOOKUP_UNRESOLVED = "unresolved"

LOOKUP_STATES: tuple[str, ...] = (LOOKUP_RESOLVED, LOOKUP_PARTIAL, LOOKUP_UNRESOLVED)

#: The query legs ``search_graph`` derives, in ITS iteration order. The order is
#: load-bearing rather than cosmetic: the budget break is global, so the LAST leg
#: is the one a truncation starves, and that is the ``point`` leg.
LEGS: tuple[str, ...] = ("object", "event", "point")

#: The two values `link_before_create.found` may carry for a candidate the run
#: creates. Deliberately NOT a boolean pair: "looked and found nothing" and
#: "never looked" are different facts, and only the first licenses "no match".
FOUND_ABSENT = False
FOUND_UNRESOLVED = None

#: The candidate classes the run reports on, mapped to the query leg that
#: establishes their identity. ``operators`` and ``retractions`` are counted but
#: deliberately NOT attributed: both name OTHER items, so their identity is the
#: identity of what they reference, not of a query of their own.
LEG_BY_SECTION: dict[str, str] = {
    "entities": "object",
    "events": "event",
    "points": "point",
}

_UNRESOLVED_NOTE = (
    "existing-item lookup did not run for this candidate — identity UNRESOLVED "
    "(not 'absent'); created without an identity assertion")


def _coverage_count(value: Any) -> int | None:
    """A coverage counter, or ``None`` when the report cannot be trusted.

    ``None`` (rather than a default of 0) is what makes a malformed report read
    as UNKNOWN completeness instead of as a complete lookup: a default of 0 would
    let ``{"ran": "all"}``-style junk pass as a clean run.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def lookup_state(search: object) -> str:
    """The identity lookup's state for this run — §4.4's trigger.

    ``resolved``    every derived query ran and none failed.
    ``partial``     bounded or impaired: the budget cut the tail, a later query
                    raised and was swallowed, or the coverage report is
                    unreadable.
    ``unresolved``  the lookup did not run at all (``degraded``, or no search).

    A ``search`` that is not a mapping — or an empty one — is ``unresolved``: a
    caller that passed nothing established nothing, and asserting otherwise is
    the defect this module removes.
    """
    if not isinstance(search, Mapping) or not search:
        return LOOKUP_UNRESOLVED
    if search.get("degraded"):
        return LOOKUP_UNRESOLVED
    coverage = search.get("query_coverage")
    if not isinstance(coverage, Mapping):
        # No coverage report: built by something other than `search_graph`.
        # See the module docstring — the historical single bit is all there is,
        # and the PRODUCER always supplies coverage, so this cannot hide a miss.
        return LOOKUP_RESOLVED
    derived = coverage.get("derived")
    ran = coverage.get("ran")
    failed = coverage.get("failed")
    # ⛔ FAIL-CLOSED on an INCOMPLETE report, not only on an unreadable one: a
    # report with `ran` but no `derived`/`failed` otherwise defaults those to 0
    # per leg and reads `r >= d, f == 0` -> `resolved`, i.e. "the whole derived
    # set ran" asserted from a report that never said what was derived — the
    # unmeasured-absence claim this module exists to remove (#5095 review
    # round 3). All three parts are required; the producer always emits them.
    if not (isinstance(derived, Mapping) and isinstance(ran, Mapping)
            and isinstance(failed, Mapping)):
        return LOOKUP_PARTIAL
    legs = set()
    for part in (derived, ran, failed):
        if isinstance(part, Mapping):
            legs |= set(part)
    if not legs:
        # A coverage report with no legs: either nothing was derived (an empty
        # candidate set — nothing to establish) or the report is malformed.
        return LOOKUP_RESOLVED if isinstance(derived, Mapping) else LOOKUP_PARTIAL
    for leg in sorted(legs):
        d = _coverage_count((derived or {}).get(leg, 0)) if isinstance(derived, Mapping) else 0
        r = _coverage_count((ran or {}).get(leg, 0)) if isinstance(ran, Mapping) else None
        f = _coverage_count((failed or {}).get(leg, 0)) if isinstance(failed, Mapping) else 0
        if d is None or r is None or f is None:
            return LOOKUP_PARTIAL
        if f or r < d:
            return LOOKUP_PARTIAL
    return LOOKUP_RESOLVED


def _short_legs(search: object) -> list[str]:
    """The legs whose queries did not all run, for the prompt clause."""
    coverage = (search or {}).get("query_coverage") if isinstance(search, Mapping) else None
    if not isinstance(coverage, Mapping):
        return []
    derived = coverage.get("derived") if isinstance(coverage.get("derived"), Mapping) else {}
    ran = coverage.get("ran") if isinstance(coverage.get("ran"), Mapping) else {}
    out: list[str] = []
    for leg in LEGS:
        d = _coverage_count(derived.get(leg, 0))
        r = _coverage_count(ran.get(leg, 0))
        if d is None or r is None or r < d:
            out.append(leg)
    return out


def _reason(search: object) -> str | None:
    """The producer's own reason string, when it gave one."""
    if not isinstance(search, Mapping):
        return None
    reason = search.get("reason")
    return reason if isinstance(reason, str) and reason.strip() else None


def lookup_note(search: object) -> str:
    """The prompt clause for an ``unresolved`` or ``partial`` lookup ('' when resolved).

    Three directions, and each is load-bearing:

    1. **What is true** — the existing-item check did not run (or did not
       complete), so existence is UNRESOLVED, not absent.
    2. **What NOT to infer** — do not assume a candidate is new;
    3. **What NOT to claim** — do not record a ``link_before_create`` entry for a
       check that did not happen: the honest report is an OMISSION.

    ⭐ The EMISSION requirement is stated explicitly, because this clause is read
    by the S4 **gap reviewer**, the pass whose job is to complete the candidate
    list. Forbidding the false claim must not read as forbidding the work: the
    clause says to emit every candidate it would have emitted anyway, so the only
    thing that changes is the identity claim.
    """
    state = lookup_state(search)
    if state == LOOKUP_RESOLVED:
        return ""
    if state == LOOKUP_UNRESOLVED:
        reason = _reason(search) or (
            "no graph search was run" if not search else "skipped")
        return (
            f"(graph search DEGRADED — {reason}; the existing-item lookup did "
            "NOT run, so whether a candidate already exists is UNRESOLVED — not "
            "absent. Emit EVERY candidate you would have emitted anyway "
            "(entities, events, points, operators) and complete the list as "
            "usual; the ONLY thing that changes is the identity claim. Do NOT "
            "treat any candidate as new — its identity was never checked — and "
            "do not record a link_before_create entry for a search that did not "
            "happen: omit the entry rather than report it.)")
    short = _short_legs(search)
    which = ", ".join(short) if short else "some"
    return (
        f"(graph search PARTIAL — the existing-item lookup ran but did not "
        f"complete ({which} queries were cut by the query budget or failed), so "
        "a missing match in an unrun leg is NOT evidence that the item does not "
        "exist. The results below are real and should be used. Emit EVERY "
        "candidate you would have emitted anyway; do NOT treat an item whose "
        "query did not run as new, and do not record a "
        "link_before_create entry for a search that did not happen — omit the "
        "entry rather than report it.)")


def created_record(searched_for: str, *, query_ran: bool) -> dict:
    """The ``link_before_create`` record for a candidate the run CREATES.

    ``found`` is ``False`` **only** when THIS candidate's own identity query ran
    and matched nothing — the one case that licenses *"no match"*. Otherwise the
    honest value is ``None``: the run cannot say whether an existing item was
    there, and §4.4's I1 forbids asserting the answer it does not have.

    A run-level state is deliberately NOT enough here: with a bounded lookup some
    candidates' queries ran and others' did not, and folding them together is the
    same defect one layer up (a claim about a check that did not happen).
    """
    if not isinstance(query_ran, bool):
        raise TypeError(
            f"query_ran must be True or False, got {query_ran!r} — a non-bool "
            f"is not a statement about whether this candidate's lookup ran")
    if not isinstance(searched_for, str) or not searched_for.strip():
        raise ValueError(
            f"a link_before_create record needs what was searched for, got {searched_for!r}")
    if query_ran:
        return {"searched_for": searched_for,
                "found": FOUND_ABSENT,
                "note": "no match — created"}
    return {"searched_for": searched_for,
            "found": FOUND_UNRESOLVED,
            "note": _UNRESOLVED_NOTE}


def plan_identity(search: object, *, candidates: Mapping[str, int],
                  unresolved: Mapping[str, int] | None = None) -> dict:
    """The run-level identity report — always present on an extraction result.

    ``candidates``  counts per class (``entities`` / ``events`` / ``points`` /
                    ``operators`` / ``retractions``).
    ``unresolved``  the subset whose OWN identity query did not run, per class.

    ⭐ **The state is the RUN's, not the lookup's**, and that is a measured
    correction (#5095 review B1): `search_graph` runs over the **S2** list and S4
    then *completes* it, so a candidate S4 added has no derived query at all —
    its identity was never checked. With the state read off the lookup alone, a
    fully-covered lookup plus one S4-added candidate reported `resolved` and the
    caller's warning stayed silent while the block's own `unresolved` counter
    said 1. So a resolved lookup is elevated to `partial` when ANY candidate is
    unresolved; `resolved` means *nothing in this run is unchecked*.

    ``reason`` stays the PRODUCER's failure text (only an ``unresolved`` lookup
    has one); a `partial` state carries its evidence in the counts instead of
    naming a failure that did not happen.

    The block is the *measurement* I2 asks for (*"a failure never silently drops
    memory"*) and the input the next slice — I1's create gate — will read. It
    reports; it does not decide a write.
    """
    if not isinstance(candidates, Mapping):
        raise TypeError(f"candidates must be a mapping, got {type(candidates).__name__}")
    counts: dict[str, int] = {}
    for key, value in candidates.items():
        if not isinstance(key, str) or not isinstance(value, int) or isinstance(value, bool) \
                or value < 0:
            raise ValueError(
                f"candidate count {key!r}={value!r} is not a non-negative int")
        counts[key] = value
    pending: dict[str, int] = {}
    for key, value in (unresolved or {}).items():
        if key not in counts:
            raise ValueError(
                f"unresolved class {key!r} is not among the reported candidate "
                f"classes {sorted(counts)} — an unattributable count is not evidence")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(
                f"unresolved count {key!r}={value!r} is not a non-negative int")
        if value > counts[key]:
            raise ValueError(
                f"unresolved count for {key!r} ({value}) exceeds the candidate "
                f"count ({counts[key]}) — more unresolved items than items")
        pending[key] = value
    state = lookup_state(search)
    reason = _reason(search) if state == LOOKUP_UNRESOLVED else None
    if state == LOOKUP_RESOLVED and sum(pending.values()):
        # The lookup was complete, and something the run is about to commit was
        # still never checked against it — a candidate the S4 gap reviewer added
        # after the query derivation ran. Reporting `resolved` here would make
        # the block contradict its own counter (#5095 review B1).
        state = LOOKUP_PARTIAL
    return {
        "state": state,
        "reason": reason,
        "lookup": _lookup_report(search),
        "candidates": dict(counts),
        "unresolved": dict(pending),
        "unresolved_total": sum(pending.values()),
    }


def _lookup_report(search: object) -> dict:
    """The producer's coverage counters, or an empty block for a foreign dict."""
    coverage = (search or {}).get("query_coverage") if isinstance(search, Mapping) else None
    if not isinstance(coverage, Mapping):
        return {}
    out: dict[str, dict[str, int]] = {}
    for part in ("derived", "ran", "failed"):
        counts = coverage.get(part)
        if not isinstance(counts, Mapping):
            continue
        out[part] = {
            leg: value for leg, value in counts.items()
            if isinstance(leg, str) and isinstance(value, int) and not isinstance(value, bool)
        }
    return out
