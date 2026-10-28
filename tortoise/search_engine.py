"""Hybrid search engine for Tortoise — RRF fusion of FTS + vector + structural queries.

Phase 0 (#7748): Foundation — FalkorDB indexes, RRF fusion, degradation chain,
3-tier query classifier, EP batch annotation, and tortoise_fts_query() API.
"""
from __future__ import annotations  # noqa: I001

import logging
import math
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, asdict, field
from typing import Any, Literal

from .env_truthy import is_truthy
from .security import entity_label, validate_entity_type
from .validity import window_read_fields  # #2350: ONE read-projection home
# #8316: the READ half of the stored-embedding shape seam. A caller-supplied
# embedding is stored VERBATIM as a plain Cypher `List` (PR #3018's recorded
# decision) and a read spelled `<alias>.embedding` makes
# `vec.euclideanDistance` raise for the WHOLE `MATCH` — one such node degrades
# the entire dense leg. Every dense read that can MEET a stored `List` must
# normalise through this helper (the HNSW index arm cannot meet one: a `List`
# node is not in the index at all — that cohort gap is #8429).
from .vector_write import stored_embedding_vector_expr

# #1391: terminal (no-longer-current) Point statuses EXCLUDED from every
# default read surface (FTS/vector/structural/operator + sdk query paths).
# #689 excluded only 'retracted'; supersede_point writes 'superseded' +
# 'outdated' (terminal per ONTOLOGY §512), 'deprecated' is used in practice
# (recall_state STATE_EXCLUDED_STATUS, EP criteria) and 'archived' is the
# same family — none must be served as current. Audit/history queries opt in
# via include_terminal (tortoise_fts_query), sdk.query/paginated_query
# (include_retracted=True) or an explicit status= filter.
#
# #2490: the POINT terminal VOCABULARY + WHERE composition live in
# tortoise/live.py (single source of truth — live.py:44
# TERMINAL_EXCLUDED_STATUSES + live.py `_terminal_excluded`). This module
# REBINDS the exported name so downstream consumers (fallback_snapshot) keep
# importing from here while the vocabulary itself never has a second literal
# definition (grep: no second literal terminal vocabulary in this file).
# #3301: the OBJECT family's canonical set is a DIFFERENT vocabulary
# (commit_ops.OBJECT_TERMINAL_STATUSES — no `outdated` member and no Point
# `draft`); `_status_vocab_for` below is the ONE dispatcher between the two
# families, so neither leg re-states the mapping.
from .commit_ops import OBJECT_TERMINAL_STATUSES
from .commit_ops import SUBJECT_TERMINAL_STATUSES  # #1423: the Subject family
# #3359: the capture-level graph-op meter is a ContextVar, and ContextVars do
# not cross into the ThreadPoolExecutor this module runs its retrieval legs on
# — without binding the legs to the caller's metering context, a capture's
# off-thread FTS/vector/structural reads are never counted.
from .graph_ops import bind_metering_context
# #4489: the per-leg retrieval meter. The legs note their OWN executions here
# (trace-INDEPENDENT by design), so a read that never opted into ``leg_trace``
# still reports which legs ran. Stdlib-only at import, so no cycle.
from . import retrieval_metering as _retrieval_metering
from .live import (
    TERMINAL_EXCLUDED_STATUSES,
    _terminal_excluded,
    _terminal_expression,
    _alive_flag,
    epistemic_disjunction,  # #6976: the traversal-position widening
    epistemic_label_queries,  # #6976: the id-anchored per-label sweep
    is_terminal_status,
)


def _exclude_status_clause(alias: str,
                           excluded=TERMINAL_EXCLUDED_STATUSES,
                           *,
                           include_outdated_flag: bool = True) -> str:
    """Cypher WHERE fragment excluding ``excluded`` on ``alias`` (plus the
    ``outdated=true`` legacy flag unless ``include_outdated_flag=False``).
    ``excluded=()`` produces the empty string (no exclusion — the
    audit/full-scan opt-in).

    #2490/#3301: this is a thin SHIM. It chooses nothing and composes nothing;
    the vocabulary + flag decision lives in ``_status_vocab_for`` (the ONE
    place the family rule lives) and the composition in
    ``live._terminal_excluded``. The default path is byte-identical to the
    pre-change implementation, so the Point legs do not change.
    """
    if not excluded:
        return ""
    return _terminal_excluded(f"{alias}.status", excluded,
                              include_outdated_flag=include_outdated_flag)


def _status_vocab_for(label: str) -> tuple[frozenset, bool]:
    """#3301: the ONE place the family -> (excluded vocabulary, whether the
    legacy ``outdated`` conjunct applies) decision lives. Every leg calls
    this; none re-states the mapping.

    * ``Point`` -> the canonical ``TERMINAL_EXCLUDED_STATUSES`` and the
      legacy ``outdated`` flag conjunct (Points have both).
    * ``Object`` -> ``commit_ops.OBJECT_TERMINAL_STATUSES`` (the canonical
      OBJECT vocabulary — superseded / deprecated / archived / retracted)
      and NO flag conjunct: no Object writer sets ``outdated``, so the Point
      flag would hide an Object nobody can mark.
    * ``Subject`` -> #1423: ``commit_ops.SUBJECT_TERMINAL_STATUSES`` (the
      terminal members of ONTOLOGY §4.2's Subject vocabulary — superseded /
      deprecated / archived) and no flag conjunct. Before #1423 a Subject
      fell through to the OBJECT set by ACCIDENT of the fallback below while
      every leg's ``label in ("Point", "Object")`` gate skipped it entirely,
      so a superseded team/role/person stayed search-visible.

    The non-Point, non-Subject branch stays a fallback for a future family and
    returns the Object vocabulary — a family added without its own arm is
    therefore filtered with the WRONG vocabulary, which is why the two
    families above are named explicitly rather than inherited.

    #2598: ``draft`` is deliberately in NEITHER set. A draft is CURRENT, not
    dead (``live.CURRENT_POINT_STATUS_VALUES``), so every retrieval leg — and
    therefore ``recall_state``/``tortoise_recall``/``tortoise_search`` — RETURNS
    a draft. Recall never hides one; what #2598 fixed is how it RENDERS: the
    row is badged (``status`` + ``recall_ranking.draft``), carries no computed
    belief while EP has not measured it (``confidence: null`` /
    ``confidence_source: "uncomputed"``), and is ranked after every live row.
    Adding ``draft`` here would silently HIDE staged material instead — the
    opposite of what was asked for.
    """
    if label == "Point":
        return TERMINAL_EXCLUDED_STATUSES, True
    if label == "Subject":
        return SUBJECT_TERMINAL_STATUSES, False
    # #3301: the OBJECT family's canonical set, imported at MODULE level —
    # commit_ops imports only ``.live`` and never ``search_engine``, so this
    # edge is acyclic (a module-level import, not a defensive lazy one).
    return OBJECT_TERMINAL_STATUSES, False


#: #1423: the canonical labels whose retrieval legs apply a terminal-status
#: exclusion. ONE declaration, read by all four legs AND the scope probe — the
#: per-site `("Point", "Object")` literal this replaces is the #2901 class
#: (a hand-written subset at a call site silently omits a family), and it was
#: exactly how `:Subject` stayed unfiltered while `_status_vocab_for` already
#: answered correctly for it. A family added to `_status_vocab_for` must be
#: added HERE too, or its terminal nodes stay search-visible.
_STATUS_FILTERED_LABELS: tuple[str, ...] = ("Point", "Object", "Subject")


# ── Circuit breaker (#249) ──────────────────────────────────────────────────
# Per-strategy breaker: trips after consecutive slow/failed queries so a
# wedged FalkorDB (or a pathological query) stops consuming caller time and
# DB resources. While OPEN the strategy short-circuits to degradation
# (empty results → RRF falls through to the next strategy / TF-IDF fallback).
# HALF_OPEN after cooldown lets exactly one probe through; success closes.
# Thread-safe: strategies run concurrently (degradation_chain's executor)
# and search calls share the module-level breakers.


class _CircuitBreaker:
    """Minimal thread-safe circuit breaker for a search strategy.

    State machine: CLOSED → (fail_threshold failures) → OPEN →
    (cooldown_seconds elapsed) → HALF_OPEN → (probe success) → CLOSED
    or (probe failure) → OPEN. At most one HALF_OPEN probe is in flight.
    """

    __slots__ = ("fail_threshold", "cooldown_seconds", "_fails", "_open_until",  # noqa: RUF023
                 "_probing", "_lock")

    def __init__(self, fail_threshold: int = 3, cooldown_seconds: float = 30.0):
        self.fail_threshold = fail_threshold
        self.cooldown_seconds = cooldown_seconds
        self._fails = 0
        self._open_until = 0.0  # 0.0 == CLOSED; monotonic deadline while OPEN
        self._probing = False
        self._lock = threading.Lock()

    def allow(self) -> bool:
        """True if a query may run: CLOSED, or the single HALF_OPEN probe."""
        with self._lock:
            if self._open_until == 0.0:
                return True  # CLOSED
            now = time.monotonic()
            if now < self._open_until:
                return False  # OPEN
            # Cooldown expired → HALF_OPEN; exactly one caller probes.
            if self._probing:
                return False
            self._probing = True
            return True

    def is_open(self) -> bool:
        """True if the breaker is currently OPEN (read-only status check)."""
        with self._lock:
            return 0.0 < self._open_until and time.monotonic() < self._open_until  # noqa: SIM300

    def record_success(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0
            self._probing = False

    def record_failure(self) -> None:
        with self._lock:
            self._fails += 1
            self._probing = False
            if self._fails >= self.fail_threshold:
                self._open_until = time.monotonic() + self.cooldown_seconds
                logger.warning(
                    "Circuit breaker OPEN for search strategy (%d consecutive failures, "
                    "%.0fs cooldown)",
                    self._fails, self.cooldown_seconds,
                )

    def reset(self) -> None:
        with self._lock:
            self._fails = 0
            self._open_until = 0.0
            self._probing = False


_BREAKERS: dict[str, _CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def _breaker(name: str) -> _CircuitBreaker:
    """Get (creating if needed) the circuit breaker for a strategy."""
    with _BREAKERS_LOCK:
        b = _BREAKERS.get(name)
        if b is None:
            b = _CircuitBreaker()
            _BREAKERS[name] = b
        return b


def reset_circuit_breakers() -> None:
    """Reset all breaker state (used by tests and after DB recovery)."""
    for b in _BREAKERS.values():
        b.reset()


def _breaker_allow(name: str) -> bool:
    """True if the strategy may run (breaker CLOSED or the single HALF_OPEN
    probe)."""
    return _breaker(name).allow()


def _breaker_record(name: str, success: bool) -> None:
    """Record an outcome. Success closes the breaker; failure counts toward
    tripping (a HALF_OPEN probe that fails re-opens immediately)."""
    b = _breaker(name)
    if success:
        b.record_success()
    else:
        b.record_failure()

logger = logging.getLogger(__name__)

# ── Data model ───────────────────────────────────────────────────────────────

@dataclass
class SearchScores:
    fts: float | None = None
    vector: float | None = None
    structural: float | None = None
    rrf: float = 0.0


# ── #3276: honest EP measurement state ──────────────────────────────────
# ``has_ep`` must mean "EP has MEASURED this claim", not merely "a persisted
# prior exists". The two came apart when #2199 began stamping a kind-derived
# baseline (ep_alpha/ep_beta, baseline_set=true, baseline_source=
# 'system-default') on decide parts at CREATE: every never-measured decision
# then read has_ep=True at the prior mean (0.75), so the #2206 relevance gate
# could not tell "we decided this" from "nobody measured this".
#
# A claim is EP-MEASURED when EP flushed a posterior (posterior_alpha — the
# one column only a real EP run writes), or when it carries a persisted
# ep_alpha that is NOT a #2199 baseline (legacy EP-prior back-compat).
# PRIOR-ONLY (has_ep=False, baseline=True): a declared baseline prior — the
# value is the prior mean, explicitly NOT a measured confidence. UNMEASURED
# (has_ep=False, baseline=False): neutral Beta(1,1) mean 0.5.

def ep_measured_cypher(alias: str) -> str:
    """Cypher boolean: EP has MEASURED the :Point bound to ``alias`` (#3276)."""
    return (f"({alias}.posterior_alpha IS NOT NULL "
            f"OR ({alias}.ep_alpha IS NOT NULL "
            f"AND NOT coalesce({alias}.baseline_set, false)))")


def ep_baseline_cypher(alias: str) -> str:
    """Cypher boolean: ``alias`` carries a #2199 baseline prior (#3276)."""
    return f"coalesce({alias}.baseline_set, false)"


def ep_prior_only_cypher(alias: str) -> str:
    """Cypher boolean: ``alias`` is PRIOR-ONLY — the ``'baseline'`` state of
    :func:`ep_measurement_state` (#3415).

    The third predicate of the three-state vocabulary: a declared #2199
    baseline with NO EP measurement. NOT the same as "unmeasured" — an
    unmeasured claim carries no persisted prior at all (Beta(1,1) neutral),
    while a prior-only claim's confidence_mean IS the declared PRIOR mean.

    Kept textually in step with the Python twin: for every combination of
    (posterior_alpha, ep_alpha, baseline_set) this helper is true iff
    ``ep_measurement_state(...) == 'baseline'`` — including the rows where
    ``baseline_set`` is true and ``ep_alpha`` is NULL (the twin calls those
    prior-only too, since ``baseline_set`` alone disqualifies "measured").
    """
    return (f"({alias}.posterior_alpha IS NULL "
            f"AND coalesce({alias}.baseline_set, false))")


def ep_measurement_state(*, posterior_alpha, ep_alpha,
                         baseline_set: bool) -> str:
    """Python twin of :func:`ep_measured_cypher` / :func:`ep_prior_only_cypher`:
    'measured' | 'baseline' | 'unmeasured' (#3276).

    Pure measurement predicate — it does NOT know about the #2490 terminal
    override, which each read surface applies on top (a terminal claim is
    forced to has_ep=False/measured=False/baseline=False regardless of the
    persisted columns)."""
    if posterior_alpha is not None or (ep_alpha is not None and not baseline_set):
        return "measured"
    return "baseline" if baseline_set else "unmeasured"


@dataclass
class EpEvidence:
    impl_count: int = 0
    nand_count: int = 0
    total: int = 0


@dataclass
class EpBreakdown:
    # THE point confidence (issue #2206 contract): the belief mean α/(α+β)
    # of the PERSISTED posterior when EP has run on the claim
    # (posterior_alpha/beta), else the persisted prior mean (ep_alpha/beta),
    # else the neutral Beta(1,1) mean 0.5 — identical to the number
    # TortoiseEP.compute_confidence / sdk.get_confidence return for the same
    # point (coalesce(n.posterior_alpha, n.ep_alpha, 1.0)). Historically this
    # field was the structural edge-ratio impl/(impl+nand); it is NOT that
    # quantity anymore — a claim EP converged at 0.88 with no incoming IMPL
    # edges reads 0.88 here, and 0.0 only for a genuinely impossible store.
    confidence_mean: float = 0.5
    evidence: EpEvidence | None = None
    # Structural ratio nand/(impl+nand) — a DIFFERENT quantity from
    # confidence_mean (issue #2206): it answers "how much of the incoming
    # evidence is contradiction" (edge-ratio family, alongside
    # evidence.impl_count/total), never a belief. A claim with 1 IMPL + 1 NAND
    # that EP converged tightly still reads contention 0.5.
    contention: float = 0.0
    # True EP posterior variance v = αβ/((α+β)²(α+β+1)) from the persisted
    # posterior_alpha/ep_alpha — the epistemically correct "is this claim
    # destabilized" signal (same formula as TortoiseEP.get_contested_claims).
    variance: float = 0.0
    # variance > CONTESTED_VARIANCE_THRESHOLD → the claim's posterior is
    # contested: competing evidence is actively destabilizing it. Surface this
    # as a first-class flag so agents treat the claim as disputed, not merely
    # high/low probability.
    contested: bool = False
    # #3276: whether EP has MEASURED this point — True iff a real EP flush
    # persisted a posterior (or a non-baseline legacy ep_alpha prior). A
    # #2199 baseline prior alone does NOT set it: a never-measured decision
    # with the kind-derived system-default baseline reads has_ep=False here
    # (it used to read True at the 0.75 prior mean — the #3276 leak).
    # False = not measured; confidence_mean is then the declared prior mean
    # when ``baseline`` is True, else the neutral Beta(1,1) mean 0.5. Neither
    # is a signal of contestation.
    # #2490 has_ep overload: TERMINAL claims (terminal vocab status OR the
    # legacy outdated=true flag) are also gated to False — their posterior
    # decays to vacuity at the terminalizing write, so a terminal claim's
    # has_ep does NOT mean "EP ran" but "not serving a dead posterior to
    # include-terminal surfaces". Consumers (topic disputed-pair gate,
    # volunteer, mcp) must not read terminal=unmeasured.
    has_ep: bool = False
    # #3276 aliases of the same measurement state, explicit and unambiguous:
    #   measured=True                     → EP measured (has_ep True)
    #   measured=False, baseline=True     → prior-only (declared baseline)
    #   measured=False, baseline=False    → unmeasured (neutral 0.5)
    # A TERMINAL claim (#2490 gate) reads measured=False, baseline=False even
    # when it was measured-and-baseline'd pre-terminalization: its 0.5 is the
    # decayed vacuity posterior, not the prior, so it is neither prior-only
    # nor a live measurement (the per-row terminal flag owns that state).
    measured: bool = False
    baseline: bool = False

    def __post_init__(self):
        if self.evidence is None:
            self.evidence = EpEvidence()


# ── Provenance enrichment flag (objectives 5/9, owner decision #3837) ───────
# The default read path carries SOURCE + WHEN LEARNED + CONFIDENCE; confidence
# already rides ``ep``. Source (``extractedFrom``) and capture time
# (``createdAt``) are additive and OFF by default: the search point fetch reads
# those columns only when this flag is set, so a default call is byte-identical
# (the #3986 freeze carve-out — a default-off response field is not a surface
# change).
#
# ⛔ AND IT IS RECORDED — the recording surface DOES exist, and this note used
# to assert the opposite ("``config/surface-manifest.yml`` has no
# ``response_fields`` key and ``tools/surface_manifest.py`` derives none");
# both clauses were FALSE, and ``CONTRIBUTING.md``"s "And it must still be
# recorded … an unrecorded field has no approval behind it" is the rule they
# were invoked to escape. The block exists (``response_fields:`` in the
# manifest), ``tools/surface_manifest.py`` carries it across a re-cut
# (``_carried_response_fields``), validates it (non-empty, every entry anchored
# to a row) and renders it into ``docs/product/mcp-sdk-surface.md``. This
# carve-out's two fields ride it as the ``provenance.source`` /
# ``provenance.captured_at`` rows — so the carve-out is RECORDED, not merely
# asserted here. (The four default-ON #8658 keys below have their own rows.)
SEARCH_PROVENANCE_FLAG_ENV = "TORTOISE_SEARCH_PROVENANCE"


def search_provenance_enabled() -> bool:
    """Resolve the additive search-provenance flag. Default OFF.

    Delegates to the single declared env-truthiness contract (#4097):
    :func:`tortoise.env_truthy.is_truthy` — unset, blank, and garbage all read
    OFF. A second copy of the vocabulary here is exactly the drift #4097 exists
    to prevent (and is build-red in ``tests/test_env_truthy.py``).

    ⛔ KNOWN LIMITATION — the flag is a NO-OP on the degraded fallback path AND
    on every non-Point entity type.
    The two fallback tiers (``fallback_snapshot.search_snapshot`` and
    ``fallback_tfidf``) build their ``SearchResult``s and return from
    ``TortoiseSDK`` BEFORE the flag-gated point fetch runs, so they carry
    neither ``source_ref`` nor ``captured_at`` and ``to_dict`` emits no
    ``provenance`` block however the flag is set. This is a boundary, not an
    oversight: the snapshot keeps a deliberately LEAN projection
    (``fallback_snapshot._SNAPSHOT_QUERY`` — id/content/pointKind/status/
    outdated/search_keys/has_answer, no provenance columns) and adding those to
    it is a separate, policy-governed change to the corpus it caches. A
    degraded run therefore gets no provenance enrichment. The enrichment is
    also POINT-ONLY: the flag-gated fetch and the two columns it reads
    (``n.extractedFrom`` / ``n.createdAt``) sit inside the project search's
    ``if entity_type == "point":`` branch, while ``SearchResult`` is constructed
    for every entity type — so a normal (non-degraded) ``document`` / ``event``
    / ``subject`` search carries no ``source_ref``/``captured_at`` either, and
    ``to_dict`` emits no ``provenance`` block. The flag enriches the
    non-degraded POINT query path only.

    #5581 extends this same block with the Point-level §4.6 currency verdict
    and its sourced disclosure (``currency`` / ``fyi``), so every clause above
    applies to them unchanged: the two fallback tiers carry neither, and the
    ``if entity_type == "point":`` branch is where the extra batch read of the
    Point's ``extractedFrom`` LINKS runs. That read is ONE query for the whole
    hit batch, taken ONLY under the flag, so an unflagged call pays nothing and
    its rows stay byte-identical.
    """
    return is_truthy(os.environ.get(SEARCH_PROVENANCE_FLAG_ENV))


#: #5581: the disclosure's reason tag — the ONE spelling (``SearchResult``
#: emits it; ``tools/source_currency.sourced_fyi`` builds it). Named so a
#: consumer can branch on WHY a fact was disclosed rather than sniffing the
#: payload's shape, and defined HERE because the read path may not import from
#: ``tools/`` (that tree is not in the wheel).
FYI_REASON_SOURCE_DRIFT = "source-version-drift"


def version_operand(value: object) -> str:
    """A version operand, or ``""`` when the value is not a recorded version.

    BLANK-IS-ABSENT, **STRIP-TESTED** — not truthiness. ``"   "`` is truthy, so a
    whitespace-only ``sourceVersion`` (reachable: ``create_source(url, kind,
    contentHash="   ")`` anchors the edge with it — ``_anchor_on_create`` nulls
    only the empty string) would otherwise be compared as a real version: equal
    to itself it reads ``current``, i.e. a FALSE FRESH on a source that recorded
    nothing, and unequal it reads ``stale`` with a bogus re-inference target.
    That is the same rule the write side already states — *"a blank hash is not a
    version"* (`tests/test_source_version_extractedfrom_5038.py`) — applied to
    the READ.

    Non-strings are absent too: a version is a content hash, so a number or a
    node object is a shape the caller got wrong, and silently stringifying it
    would invent an operand. :func:`currency_status` stays an exact comparison of
    two prepared operands; this is the ONE place a raw graph value becomes one.
    """
    return value.strip() if isinstance(value, str) else ""


def currency_status(remembered: str, current: str) -> str:
    """#5199 — the currency of a ``sourceVersion`` note, as a **READ**.

    ``current``  the note names the version the source holds NOW
    ``stale``    it names a DIFFERENT version — the thing built from it may be
                 out of date
    ``unknown``  nothing was noted, or the source has no version. This is the
                 HONEST answer and is deliberately NOT a synonym for ``current``:
                 an absent note must never read as fresh, because "no recorded
                 version" silently rendering as "up to date" is the precise
                 false-current the note exists to expose.

    A pure function of two strings, never a stored status field: the #5199
    ruling keeps currency a read, so a source edit needs no backfill and no two
    readers can disagree about the same **pair** (a later source edit moves the
    pair, so the same link can legitimately read differently across time). The
    comparison is exact string
    equality — the versions are content hashes, so there is no ordering to get
    wrong and no "newer" to infer.

    ⚠ IT COMPARES **ONE LINK'S** PAIR. A Point's own currency is an AGGREGATE
    across its links (ONTOLOGY §4.6: stale if ANY link is behind, current only
    when EVERY link is), so a Point-level verdict is NOT derivable from a single
    pair — a caller that collapses several links into one call here can report
    ``current`` for a Point another link makes stale. Aggregate per §4.6 at the
    call site; never assume this helper speaks for the Point.
    """
    if not remembered or not current:
        return "unknown"
    return "current" if remembered == current else "stale"


def aggregate_currency(links: Iterable[tuple[str, str]]) -> str:
    """#5581 — the §4.6 **Point-level** verdict, over the Point's OWN links.

    ``links`` is one ``(remembered, current)`` pair per ``extractedFrom`` edge —
    the recorded ``r.sourceVersion`` and the Source's current ``contentHash``.
    This is the aggregate the per-link :func:`currency_status` explicitly does
    NOT make (see its ⚠ note): ONTOLOGY §4.6 — *"the Point is **stale if any**
    of those links is behind its source's current version, and **current only
    when every** link is"*.

    Three states, and the precedence is the whole point:

    * ``stale`` — **ANY** link is behind. A verifiable change is proof the
      Point's recorded read no longer matches its source, and one such link is
      enough; a sibling link cannot rehabilitate it. ``stale`` therefore
      OUTRANKS ``unknown``: the links are independent, so an unfilled pair on
      one edge says nothing about a pair that is filled and differs on another.
    * ``current`` — **EVERY** link is current **and there is at least one**.
    * ``unknown`` — no link is stale and not every link is current, i.e. some
      pair was unfillable (an absent note, or a Source with no version). This
      is the HONEST answer, never a synonym for ``current`` — the same rule
      :func:`currency_status` applies to one pair, lifted to the set.

    ⛔ **ZERO LINKS IS ``unknown``, NOT ``current``.** Read literally, "current
    only when every link is" is VACUOUSLY true of an empty link set, so a Point
    with no ``extractedFrom`` link at all — the ordinary orphan write — would
    read ``current``. That is precisely the false-current §4.6 exists to
    prevent (it would report a Point with no recorded read as verified-fresh),
    so the rule is refuted by the section's own intent and refused here. The
    aggregate answers *"is the recorded read still current?"*; with no recorded
    read there is nothing to answer and the verdict is ``unknown``.

    A pure function of the pairs: currency stays a READ (§4.6 *"a read, never a
    stored flag"*), so no backfill is needed and two readers cannot disagree
    about the same set of pairs. The caller owns the GRAPH read that produces
    the pairs — see ``tools/source_currency.py`` for the shared derivation used
    by in-repo read paths.
    """
    pairs = list(links)
    # ⛔ Anti-vacuity FIRST — see the docstring. Not reachable from the set
    # comprehension below, which would answer `current` for an empty set.
    if not pairs:
        return "unknown"
    # ⚠ NOT normalised here: `aggregate_currency` is a pure function of PAIRS, and
    # its contract (pinned by `test_aggregate_borrows_the_one_pair_verdict`) is
    # that it delegates to `currency_status`, whose comparison is EXACT. The
    # blank-is-absent rule belongs one layer out, where a raw graph value becomes
    # an operand — `version_operand` in the readers.
    verdicts = {currency_status(remembered or "", current or "")
                for remembered, current in pairs}
    if "stale" in verdicts:
        return "stale"
    if verdicts == {"current"}:
        return "current"
    return "unknown"


def read_currency_links(graph, point_ids: Iterable[str]) -> dict[str, list[dict]]:
    """#5581 — the ``(remembered, current)`` pair behind every ``extractedFrom``
    link of every requested Point, as one query.

    Returns ``{point_id: [link, ...]}`` where a link is
    ``{"source", "recorded", "current", "currency"}``. A Point with no link is
    ABSENT from the dict (``.get(pid, [])`` at the call site) — never present
    with an empty list, so "this Point has no versioned provenance" and "this
    Point was not in the batch" cannot be confused by a caller that checks
    membership.

    ONE row per EDGE, not one per Point: §4.6 aggregates over the links, and the
    disclosure the ruling requires must name *which* source moved (*"older fact
    from source X"*), which a collapsed single verdict cannot carry.

    The two operands, and why each is read where it is:

    * ``sourceVersion`` comes from the **EDGE** — the authoritative per-link
      scalar (#5256). The Point's ``sourceVersionTransit`` node prop is the
      replay transit, not a read.
    * ``contentHash`` comes from the **Source node** — the version the source
      holds NOW. It is the comparison's moving side by construction: a source
      edit is exactly what makes a previously-current link read ``stale``.

    Either operand absent (a bare edge, or a Source whose hash is ``''`` — a
    minted stub, a connector, the capture path before materialization) leaves
    the pair unfillable and its verdict ``unknown``, never ``current``: a
    missing operand cannot verify anything.

    Rows are sorted by ``(source, recorded)`` so two reads of an unchanged graph
    agree. The sort is deterministic but NOT total — two edges to one Source
    whose notes agree are engine-ordered — and nothing downstream may depend on
    row POSITION: select a link by its source identity, never by index.
    """
    ids = list(point_ids)
    if not ids:
        return {}
    rows = graph.query(
        "MATCH (n:Point)-[ef:extractedFrom]->(s:Source) "
        "WHERE n.id IN $ids "
        "RETURN n.id, s.url, ef.sourceVersion, s.contentHash",
        params={"ids": ids},
    ).result_set
    out: dict[str, list[dict]] = {}
    for pid, url, remembered, current in rows:
        recorded = version_operand(remembered)
        now = version_operand(current)
        out.setdefault(pid, []).append({
            "source": url or "",
            "recorded": recorded,
            "current": now,
            "currency": currency_status(recorded, now),
        })
    for links in out.values():
        links.sort(key=lambda lk: (lk["source"], lk["recorded"]))
    return out


def read_reference_currency_links(
        graph, entity_ids: Iterable[str]) -> dict[str, list[dict] | None]:
    """#5199 — the same pair, read for an **ENTITY** (`:Object` / `:Event`).

    ``read_currency_links`` answers for a **Point**, over its ``extractedFrom``
    links. A derived ``:Object`` / ``:Event`` has no such link: it reaches its
    source through ``(Source)-[:references]->(entity)`` (§3.4), which is the
    link that carries the ``sourceVersion`` note for the DERIVATION classes
    (owner ruling 2026-09-25, option A — ``Event``/``Document`` annotate, an
    identity/mention ``Object`` link carries no ``sourceVersion``). Without this
    read the question *"are the entities we reason about still about the content
    we hold?"* is unanswerable for every class except ``:Point``, which is the
    gap #5199 names.

    Returns ``{entity_id: [link, ...]}`` — the same row shape as
    :func:`read_currency_links` (``source``, ``recorded``, ``current``,
    ``currency``) — keyed by the id the CALLER used. THREE states, and they are
    deliberately distinguishable:

    * ``[link, ...]`` — the covered node's links, in ``(source, recorded)``
      order;
    * ABSENT — no inbound ``references`` link, or not in the batch. The caller's
      verdict is :func:`aggregate_currency`'s anti-vacuity ``unknown``;
    * ``None`` — the address named MORE THAN ONE node, so it is REFUSED rather
      than answered (see the identity paragraph below). Absence must never be
      read as "nothing references this entity" when the truth is "this read
      declined to pick a node", so the refusal keeps its key.

    ⛔ AN ``Object`` READS ``unknown``, AND THAT IS THE ANSWER, NOT A BUG. Its
    link carries no ``sourceVersion`` by the same ruling, so the pair is
    unfillable and :func:`currency_status` returns ``unknown`` — deliberately
    never ``current``. Do not "fix" this by stamping an anchor onto an identity
    link: that reverses the ruling and is pinned against in
    ``tests/test_source_version_references_5199.py``.

    Identity: a row is attributed to the COORDINATES THE CALLER USED, never to
    a derived value. Three coordinates are legal because the provenance writers
    mint three node shapes, and each is addressed differently:

    * a ``:Source`` (D10: a document IS a source) is addressed by ``url``
      (``projection/edges.py``'s ``key_clause = "{url:$eid}" if entity_label ==
      "Source"``). ``_upsert_document`` happens to also write
      ``s.id=coalesce(s.id, $id)``, but ``_mint_source_stub`` writes NO ``id``
      and a Source's ``id`` is not guaranteed to equal its ``url`` — so a read
      that matched ``id`` alone would report ``unknown``, with no links, for
      every stub-minted document provenance while the note sits on the edge;
    * a legacy raw-Cypher ``:Event`` carries only ``eventId``;
    * ``id`` is the ordinary coordinate for an ``:Event`` / ``:Object``.

    ⛔ ONE ADDRESS, ONE REFERENCED NODE — OR NO ANSWER. A single address can
    name more than one node (``entity_identity.py`` names this fold as the
    historic wrong answer: *MATCH returning several rows … folds two identities
    into one value*). Rather than union several nodes' links under one verdict —
    which can accuse the wrong entity and can downgrade a current node on a
    stranger's link — an address that resolves to more than one node keeps its
    key with a ``None`` value: the read REFUSED it (see the three states above),
    and :func:`aggregate_currency` is never asked.

    ⚠️ THE REFUSAL COUNTS *REFERENCED* NODES — the honest limit of an
    edge-anchored read, stated rather than implied. The query walks
    ``references`` edges, so a node with no inbound link is invisible to the
    check: an address naming one LINKED and one UNLINKED node is answered for
    the linked one. Making the refusal TOTAL needs a node-anchored pre-read
    (``MATCH (n) WHERE n.id IN $ids OR …``), which is a label-less scan of the
    graph and therefore a cost decision, not a free addition. Until then:
    uniqueness is ``entity_identity._require_unique_id``'s job, and an address
    that names a linked node plus an unlinked twin reads as the linked node's
    verdict.

    This function owns the graph read itself (one query, as
    :func:`read_currency_links` does), the pair extraction and the ordering. The
    §4.6 **aggregate** is what it leaves to its caller — that is
    :func:`aggregate_currency`'s job, and passing it the pairs is the call site's.
    """
    ids = list(entity_ids)
    if not ids:
        return {}
    wanted = set(ids)
    rows = graph.query(
        "MATCH (s:Source)-[ref:references]->(n) "
        "WHERE n.id IN $ids OR n.eventId IN $ids OR n.url IN $ids "
        "RETURN id(n), n.id, n.eventId, n.url, s.url, "
        "ref.sourceVersion, s.contentHash",
        params={"ids": ids},
    ).result_set
    out: dict[str, list[dict] | None] = {}
    idents: dict[str, set] = {}
    for node_uid, nid, neid, nurl, url, remembered, current in rows:
        recorded = version_operand(remembered)
        now = version_operand(current)
        row = {
            "source": url or "",
            "recorded": recorded,
            "current": now,
            "currency": currency_status(recorded, now),
        }
        # The NODE's identity — used only to detect an address that names more
        # than one node, never to key a row. It is the engine's own node id
        # (`id(n)`, stable within this query snapshot, which is all that is
        # needed), NOT a fingerprint of the node's VALUES: two distinct nodes
        # can carry the same coordinates (`id:'x'` twice is exactly the raw
        # duplicate corruption `entity_identity._require_unique_id` refuses
        # first), and a value fingerprint would call them one node — the very
        # fold this check exists to catch. A derived property key would do the
        # same, which is why the row returns no derived identity at all.
        ident = node_uid
        # Attribute to EVERY coordinate the caller actually asked for. An empty
        # string is not a coordinate, so a `id: ''` node is reached through its
        # `eventId`/`url` rather than skipped. The coordinates are DEDUPED: the
        # ordinary production shape carries ``id`` and ``eventId`` with the SAME
        # value, and appending once per coordinate would double that row.
        for key in dict.fromkeys((nid, neid, nurl)):
            if key and key in wanted:
                out.setdefault(key, []).append(dict(row))
                # `ident` goes in AS the engine returned it. Coercing it through
                # `or ""` would turn node id 0 into the empty string and fold it
                # with any other falsey id — an accidental VALUE fingerprint,
                # which is exactly what this set must never be.
                idents.setdefault(key, set()).add(ident)
    # ONE ADDRESS, ONE NODE — OR NO ANSWER (see the docstring). An address that
    # named more than one node keeps its KEY with a ``None`` value: a verdict
    # assembled from a node the caller may not have addressed is not an answer,
    # but silently dropping the key would make a REFUSAL indistinguishable from
    # "no inbound reference" — and a caller that cannot tell those apart could
    # read absence as "nothing references this entity".
    for key in [k for k, seen in idents.items() if len(seen) > 1]:
        out[key] = None
    for links in out.values():
        if links is not None:  # a refused address keeps its `None` (see above)
            links.sort(key=lambda lk: (lk["source"], lk["recorded"]))
    return out


@dataclass
class SearchResult:
    id: str
    content: str
    point_kind: str
    scores: SearchScores | None = None
    match_source: Literal["fts", "vector", "structural", "rrf", "tfidf",
                         "session"] = "rrf"
    ep: EpBreakdown | None = None
    relationships: list[dict] = field(default_factory=list)  # SDK compat (sdk.py passes it; non-point = empty)
    # #6971: the fail-loud counterpart to an all-empty ``relationships`` list.
    # The bounded decoration seeds every requested point with ``[]`` and fills
    # it afterwards; when the decoration query RAISES, the caller used to get
    # that seeded dict back verbatim — so "this point has no IMPL/NAND edges"
    # and "the edges could not be read" were the same wire shape. Set by the
    # search path when ``RelationshipDecoration.decoration_error`` is non-empty
    # (the unavailability is a property of the BATCH, so every hit carries it).
    # Empty ⇒ absent from the wire (additive, byte-identical default).
    relationships_unavailable: str = ""
    # #125 capture metadata (document entity_type) — optional, empty for non-docs
    topics: list = field(default_factory=list)
    summary: str = ""
    session_id: str = ""
    event_id: str = ""
    source_path: str = ""  # #167: file path for agent to open/search
    # #1353 promoted epistemic state (D8) — first-class, absent when unknown:
    status: str = ""  # live / superseded / deprecated / retracted / draft
    # #2294: the legacy flag-based dead marker. ``invalidate_point`` sets
    # ``outdated=true`` and leaves ``status`` untouched, so a status-only
    # terminal test cannot see it — the reader must combine the two through
    # ``live.is_terminal_status`` (#2901: ONE declaration, imported by every
    # reader that must skip non-current Points). Additive in ``to_dict``
    # (emitted only when true), so live hits stay byte-identical.
    outdated: bool = False
    superseded_by: dict | None = None  # {id, content_snippet, created_at} | None
    supersedes: list[dict] = field(default_factory=list)  # [{id, content_snippet, created_at}]
    # E6 (#1538) D5/D7: promoted validity-window fields — additive, emitted
    # only when present (undated points ⇒ empty string, no marker). Written
    # by supersede_point/invalidate_point (D2).
    valid_from: str = ""
    valid_to: str = ""
    expired_at: str = ""
    # #2350 (E-prec): the additive per-end precision layer + the 3-state end
    # (``open`` | ``known`` | ``unknown``). Empty means the precision was not
    # declared (legacy window — ``valid_from``/``valid_to`` read as day
    # precision, the pre-#2350 default); ``end_state`` is emitted whenever the
    # point carries ANY window information, so an ended-unknown window is
    # distinguishable from a still-true one on the wire.
    valid_from_precision: str = ""
    valid_to_precision: str = ""
    end_state: str = ""
    # #9201: each END's PRESENCE, separate from its precision. A present-but-
    # unorderable bound (``validFrom=""``/``"TBD"``) resolves to precision
    # ``None`` — it names no instant — but the point still carries that bound,
    # and the block must still report ``null`` so this surface agrees with
    # ``assembly``/``get_historical_knowledge`` (both of which always carry the
    # null). The presence datum is what a truthiness gate cannot infer once the
    # precision stops standing in for it. Never emitted themselves.
    valid_from_present: bool = False
    valid_to_present: bool = False
    subject: dict | None = None  # {id, name, kind} | None — ≤1 hop, fail-closed (D10)
    # #4889: the fail-loud counterpart to an absent ``subject``. Emitted only
    # when the whole batch resolved no subject AND the graph has no
    # ``aboutSubject`` producer at all — so it says "this field cannot be
    # populated here", never "these points happen to have none". Empty ⇒
    # absent from the wire (additive, byte-identical default).
    subject_unavailable: str = ""
    # A5 (#2070): stored evidence mark (``has_answer`` — written by the
    # eval ingest / fixture seeding; the product extractor does not write it
    # yet, so production hits are False). Carried so the ask lane's
    # ``apply_evidence_boost`` stored-mark fallback has material. Additive
    # in to_dict (emitted only when True — the wire shape stays clean for
    # the 99% unmarked majority).
    has_answer: bool = False
    # Provenance enrichment (objectives 5/9; owner decision #3837 — the default
    # path carries source + when learned + confidence; confidence already rides
    # ``ep``). ADDITIVE and OFF by default: the point fetch reads these columns
    # ONLY when ``search_provenance_enabled()``, and ``to_dict`` emits the
    # ``provenance`` block only when a value is present, so a default call is
    # byte-identical to pre-change output.
    source_ref: Any = None  # Point.extractedFrom — the Source/document link
    captured_at: str = ""   # Point.createdAt — when the fact entered memory
    # #5581 (the #5038 read-path half): the Point's OWN §4.6 currency verdict —
    # the AGGREGATE over its ``extractedFrom`` links, never one link's pair —
    # and the links that make it ``stale``, whose sources are what the ruling
    # requires the row to DISCLOSE ("older fact from source X"). Both ride
    # INSIDE the provenance block and only under the same flag that fetched
    # them, so an unflagged call stays byte-identical; empty ⇒ absent.
    currency: str = ""  # current | stale | unknown
    outdated_sources: list = field(default_factory=list)  # the behind links
    # #8658: the Point's VERBATIM PROVENANCE — the anchor Slice A's
    # ``retrieval.package_evidence_pool`` (#2683) collapses a distilled point's
    # own source chunk/turn onto (``quote`` + ``source_turn_id``) and the
    # address #2684 surfaces (``span_start``/``span_end``). The eval lane's
    # ingest supplies all four (``tools/longmem_eval/ingest.py::
    # point_props_for_hits``); the PRODUCT point fetch read none of them, so
    # ``_is_own_source`` returned False on its first line (empty quote) and
    # both provenance legs were INERT on real graphs.
    #
    # ADDITIVE and PRESENT-ONLY in ``to_dict``: each key is emitted only when
    # the Point actually carries the datum, so an unprovenanced hit — every row
    # written before E3/E4, and every row whose writer never had a verbatim
    # source — stays byte-identical to pre-change output. A default that
    # emitted four empty fields would make every legacy hit look provenanced to
    # the package's own truthiness gates.
    #
    # ⚠️ This is a DEFAULT-ON additive response field, and that IS a departure
    # from the #3986 carve-out recorded on ``search_provenance_enabled`` above
    # ("a default-off response field is not a surface change") — the shape that
    # does not gate as an addition is by definition the one that is OFF by
    # default and leaves the response unchanged when off
    # (``CONTRIBUTING.md``). ⛔ So the response shape DOES change here, and that
    # is stated plainly rather than argued away: what is NOT novel is the
    # INFORMATION — the four values are the point's OWN stored verbatim anchor,
    # already on the node, already emitted to any caller of the eval lane, and
    # already named by ``package_evidence_pool``'s docstring as the key it
    # collapses on — but a default-on key is still a response addition, so it
    # is RECORDED as one. A default-off gate was rejected because it would keep
    # the read path's own documented contract inert by default, which is the
    # defect #8658 exists to remove. ⛔ It IS recorded, in the one place that
    # records response fields: four `response_fields` rows on `tortoise_search`
    # in ``config/surface-manifest.yml``, rendered into the "Recorded response
    # fields" table in ``docs/product/mcp-sdk-surface.md``.
    # (An earlier draft of this comment claimed no such surface existed — it
    # does: the block, its `check` validation, and its `render` are all in
    # ``tools/surface_manifest.py``, and ``CONTRIBUTING.md``"s "it must still be
    # recorded" applies to exactly this shape. The `outdated` row is the
    # non-flag-gated precedent.)
    quote: str = ""
    source_turn_id: Any = None
    span_start: Any = None
    span_end: Any = None

    def to_dict(self) -> dict:
        """Convert to JSON-safe dict for API responses."""
        d = {
            "id": self.id,
            "content": self.content,
            "point_kind": self.point_kind,
            "match_source": self.match_source,
            "relationships": self.relationships,
            "topics": self.topics,
            "summary": self.summary,
            "sessionId": self.session_id,
            "eventId": self.event_id,
            "sourcePath": self.source_path,
            # Backward-compat aliases (Phase 0 migration from old search() API).
            # IMPORTANT: "similarity" was cosine (0-1) in Phase 0; now it's the
            # RRF fusion score (rank-based, typically 0.01-0.05). Clients that
            # threshold on similarity must recalibrate. (#23)
            "similarity": self.scores.rrf if self.scores else 0.0,
            "snippet": self.content[:200] if len(self.content) > 200 else self.content,
        }
        if self.scores:
            d["scores"] = asdict(self.scores)
        if self.ep and self.ep.evidence is not None:
            d["ep"] = asdict(self.ep)
        # #1353 promoted state — additive keys, emitted only when known (#1353 D8)
        if self.status:
            d["status"] = self.status
        # #2294: the flag half of the terminal vocabulary, emitted only when
        # set (the same additive D8 rule as ``status`` above). Without it the
        # ``invalidate_point`` terminal state is INVISIBLE to consumers that
        # only see the wire — e.g. the ask lane's decoration gate, which must
        # never render a terminal hit as current.
        if self.outdated:
            d["outdated"] = True
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        if self.supersedes:
            d["supersedes"] = self.supersedes
        # E6 (#1538) D7: window fields — additive keys, emitted only when
        # present (#1353 D8 rule).
        if self.valid_from:
            d["valid_from"] = self.valid_from
        if self.valid_to:
            d["valid_to"] = self.valid_to
        if self.expired_at:
            d["expired_at"] = self.expired_at
        # #2350 (E-prec): the precision block, emitted only when the point
        # carries window information at all — undated points stay byte-
        # identical (no keys), a dated point states its end state so a
        # still-true window (``open``) is never confused with one whose end is
        # un-datable (``unknown``). ``valid_from_precision`` is INCLUDED in the
        # gate: a falsey-but-present start (``""``) is a real bound for the
        # ONE read-projection home, so the resolved value must reach the
        # wire — gating on a truthy ``valid_from`` alone silently dropped the
        # block on that surface only (the cross-surface drift of #2350).
        #
        # #9201: the presence halves. A present-but-unorderable bound resolves
        # to precision ``None`` (it names no instant), so the precision can no
        # longer stand in for presence: the key is emitted with ``None``, which
        # is what ``assembly`` / ``get_historical_knowledge`` report for it.
        # An ABSENT bound (the open-end shape) still emits no key at all.
        if (self.valid_from_present or self.valid_from or self.valid_to
                or self.valid_to_present
                or self.valid_to_precision or self.valid_from_precision):
            if self.valid_from_precision or self.valid_from_present:
                d["valid_from_precision"] = self.valid_from_precision or None
            if self.valid_to_precision or self.valid_to_present:
                d["valid_to_precision"] = self.valid_to_precision or None
            if self.end_state:
                d["end_state"] = self.end_state
        if self.subject:
            d["subject"] = self.subject
        # #4889: loud counterpart to the absent ``subject`` key above — only
        # set when the Subject layer's producer is missing entirely.
        if self.subject_unavailable:
            d["subject_unavailable"] = self.subject_unavailable
        # #6971: the same contract for ``relationships`` — emitted only when
        # the bounded decoration query actually raised, so the seed-and-fill
        # all-empty shape can never again read as "genuinely no edges".
        if self.relationships_unavailable:
            d["relationships_unavailable"] = self.relationships_unavailable
        # A5 (#2070): additive evidence mark — emitted ONLY when known
        # (unmarked hits stay byte-identical on the wire).
        if self.has_answer:
            d["has_answer"] = True
        # Provenance (#3837 owner decision: source + when learned). Additive —
        # emitted only when a value is present, so an unflagged call and an
        # unflagged empty-provenance hit both stay byte-identical.
        if self.source_ref or self.captured_at or self.currency:
            prov: dict[str, Any] = {}
            if self.source_ref:
                prov["source"] = self.source_ref
            if self.captured_at:
                prov["captured_at"] = self.captured_at
            # #5581: the §4.6 verdict, and — only when it is ``stale`` — the
            # SOURCED FYI the owner's ruling requires. Emitted only when the
            # flag fetched it (``currency`` is ``""`` otherwise), so the block's
            # pre-#5581 shape is unchanged for every unflagged caller.
            if self.currency:
                prov["currency"] = self.currency
            if self.currency == "stale" and self.outdated_sources:
                prov["fyi"] = {"reason": FYI_REASON_SOURCE_DRIFT,
                               "sources": self.outdated_sources}
            d["provenance"] = prov
        # #8658: verbatim provenance — additive keys, each emitted ONLY when the
        # Point carries it (present-only, so unprovenanced hits stay
        # byte-identical). ``span_start == 0`` is a REAL address and must
        # survive, so the gate is presence (``is not None``), never truthiness;
        # ``source_turn_id`` arrives as ``int|None`` and the graph drops a null
        # prop (#2949), so an absent link arrives as None or "".
        if self.quote:
            d["quote"] = self.quote
        if self.source_turn_id is not None and self.source_turn_id != "":
            d["source_turn_id"] = self.source_turn_id
        if self.span_start is not None:
            d["span_start"] = self.span_start
        if self.span_end is not None:
            d["span_end"] = self.span_end
        return d


# ── Query classification ────────────────────────────────────────────────────

def classify_query(
    query: str | None,
    kind: str | None,
) -> dict[str, bool]:
    """Determine which retrieval strategies to activate.

    - No text query → structural only (kind-filtered)
    - Text query present → all available strategies (FTS + vector + structural)
    """
    if not query or not query.strip():
        return {"fts": False, "vector": False, "structural": True}
    return {"fts": True, "vector": True, "structural": True}


# ── FalkorDB query runners ──────────────────────────────────────────────────

#: R3 (#1542) D4 leg-trace entry shape (the R2 #1541 shared contract):
#: {"leg", "ran", "degraded", "reason", "count"}. ``reason`` is never null
#: when ``degraded`` is true (shape rule). #4999 adds one OPTIONAL key —
#: ``mechanism`` — present only on an entry whose leg records WHICH path it
#: took. #4199 adds a second OPTIONAL key — ``scope``
#: (:data:`VECTOR_SCOPE_KEY`, value :data:`VECTOR_SCOPE_EMPTY`) — present
#: only on a vector entry whose ``scope_kinds`` predicate selected NO nodes.
#: Legs that do not pass any optional key are byte-identical to the shared
#: shape. #8488 adds a third OPTIONAL key — ``declined``
#: (:data:`LEXICAL_DECLINED_KEY`) — present only on a lexical-arm entry that
#: RAN and then declined to rank (see :data:`DEFAULT_BM25_CORPUS_CAP`).
def _trace_entry(leg: str, *, ran: bool, degraded: bool,
                 reason: str | None, count: int,
                 mechanism: str | None = None,
                 declined: bool = False) -> dict:
    entry = {"leg": leg, "ran": ran, "degraded": degraded,
             "reason": reason, "count": count}
    if mechanism is not None:
        entry[VECTOR_MECHANISM_KEY] = mechanism
    if declined:
        entry[LEXICAL_DECLINED_KEY] = True
    return entry


#: #4028 — leg-trace reason recorded when the vector leg RAN but the
#: relevance floor removed every hit. Distinct from `empty_results` (the
#: graph simply had no near neighbour): this says "there were hits and none
#: of them was relevant", which the search surface must NOT mistake for a
#: leg failure and answer from the TF-IDF fallback.
BELOW_RELEVANCE_FLOOR = "below_relevance_floor"

#: #5412 — leg-trace reason recorded when the caller supplied NO query vector
#: at all. The dense leg was never asked a question, so it neither ran nor
#: failed (``ran=False``, ``degraded=False``). Its own term is required:
#: without an entry the declaration could only infer ``leg_absent`` (a leg
#: blamed for not existing) or ``leg_trace_unavailable``, and
#: ``require_hybrid_read``'s refusal named that wrong cause. It must never
#: read as a healthy run — a keyword-only read is not a hybrid read (#2952).
NO_QUERY_VECTOR = "no_query_vector"


# ── #4999: the vector leg's MECHANISM (index vs scan) ───────────────────────
# Both the HNSW-index path and the brute-force full scan recorded an
# IDENTICAL leg-trace entry (``ran=True, degraded=False, reason="ok"``), so a
# caller could not tell a 4.97 ms indexed query from a 16.96 ms full scan of
# 7,859 Object vectors without reading this source. ``degraded`` deliberately
# does NOT carry the signal: per #2952 it means "the vector leg contributed no
# semantic results", and a brute-force scan DOES return semantic rows —
# flipping it would make :func:`require_hybrid_read` refuse a valid semantic
# read (see the #2952 tests). The path is reported additively instead.
#:
#: Trace-entry key carrying the mechanism (written by :func:`_trace_entry`,
#: read by callers); the value is one of the three constants below, and the
#: key is absent when no path was taken (e.g. ``breaker_open``).
#:
#: ⛔ PER-LEG (#8488): the key's value set depends on WHICH leg carries it —
#: ``vector`` → the three constants below; ``fts`` → ``"bm25"``
#: (:data:`LEXICAL_MECHANISM_BM25`) when the lexical slot was scored
#: in-process by Okapi BM25, absent otherwise. A consumer must therefore
#: read the value together with ``entry["leg"]``; ``mechanism in
#: (MECHANISM_INDEX, MECHANISM_SCAN, MECHANISM_SCAN_FALLBACK)`` remains the
#: right test for "a vector entry took a known path" and is NOT a test for
#: "the entry is a vector entry".
VECTOR_MECHANISM_KEY = "mechanism"
#: Index-accelerated ``CALL db.idx.vector.queryNodes`` produced the rows.
MECHANISM_INDEX = "index"
#: Brute-force scan and NO index was attempted (embedded mode — the design).
MECHANISM_SCAN = "scan"
#: Brute-force scan AFTER an index attempt failed (docker mode): the silent
#: fallback #4999 is about, now reportable on the trace rather than only in a
#: log line.
MECHANISM_SCAN_FALLBACK = "scan_fallback"


# ── #4199: the dense leg's material for the READ's own scope ────────────────
# The vector leg's health used to be judged against the WHOLE entity label:
# the zero-row guard counted every embedded Point, and a row-returning run was
# recorded ``ok`` — so a corpus that is embedded only OUTSIDE the requested
# kind (the measured production state: captured turn Points un-embedded,
# extracted Points embedded) was reported ``hybrid`` while the dense leg
# contributed NOTHING to the read's own material. The projection below is the
# entity_type → kind property mapping the read's KIND filter uses (mirrors
# ``sdk.recall_state``'s ``kind_field``); a scope-aware count over it is what
# lets :func:`run_vector_query` declare ``no_embeddings`` for a hollow scope.
_KIND_FIELD_BY_ENTITY = {
    "point": "pointKind",
    "operator": "op_type",
    "source": "sourceKind",
    "document": "documentKind",
    "object": "objectKind",
    "event": "eventKind",
    "subject": "subjectKind",
}

#: #4199 — trace-entry key recording that the arm measured an EMPTY scope
#: (its ``scope_kinds`` predicate selected NO nodes). The value is
#: :data:`VECTOR_SCOPE_EMPTY`. Written only by the measurement path: a FAILURE
#: record is never neutral, so the key is absent there (see the ``failure``
#: argument of :func:`run_vector_query`). Do not infer a non-empty scope from
#: the key's ABSENCE — absence is not evidence that the scope was non-empty.
VECTOR_SCOPE_KEY = "scope"
#: The arm's ``scope_kinds`` selected no nodes. An empty scope is a category
#: error for ``no_embeddings`` (#2952's deliberate rule — see
#: :func:`test_empty_scope_is_not_declared_un_embedded`), so such an entry
#: neither proves the read hybrid nor declares it degraded: it is NEUTRAL in
#: the aggregation. It exists so one arm measuring a DIFFERENT (empty) scope
#: cannot launder another arm's genuine degradation — the ``object_centric``
#: Point+Object laundering #4199's review found.
VECTOR_SCOPE_EMPTY = "empty"


# ── (C) #2952: declared degraded reads ──────────────────────────────────────
# The leg trace records WHAT each leg did; a consumer that would otherwise
# label the result "hybrid" needs an explicit DECLARATION that the vector
# (semantic) leg did not contribute. ``declared_degraded_read`` derives that
# marker from the trace; ``require_hybrid_read`` turns it into a fail-loud
# refusal for real-lane measurement (#2985 / PR #3005 posture). Both are
# additive and opt-in: default callers (leg_trace=None) see byte-identical
# behavior and never pay for either.

#: (C) #2952 — marker key identifying a declared vector-leg-unavailable read.
VECTOR_LEG_UNAVAILABLE = "vector_leg_unavailable"


def _vector_leg_healthy(entries: list[dict]) -> bool:
    """True when at least one IN-SCOPE vector entry did run, undegraded (#2952).

    #4199: an entry whose arm measured an EMPTY scope
    (:data:`VECTOR_SCOPE_EMPTY`) is not evidence the read was served — it is
    skipped. ``recall_state(object_centric=True)`` submits one vector arm per
    entity, and an arm whose kind scope holds no nodes scans the whole label
    and can return out-of-scope rows recorded ``ok``; that healthy entry must
    not launder another arm's genuine degradation.
    """
    return any(
        e.get("leg") == "vector" and e.get("ran") and not e.get("degraded")
        and e.get(VECTOR_SCOPE_KEY) != VECTOR_SCOPE_EMPTY
        for e in entries)


def _all_vector_arms_measured_empty_scope(entries: list[dict]) -> bool:
    """True when EVERY vector entry measured an EMPTY scope (#4199 review P1).

    A FAILURE record is deliberately NOT counted as an empty-scope
    measurement: it carries no :data:`VECTOR_SCOPE_KEY` and keeps its own
    ``reason``, so an empty scope cannot neutralise a failed leg (the
    ``failure`` argument of :func:`run_vector_query`'s ``_record``). A trace
    whose every arm failed therefore returns ``False`` and still fails
    closed.

    ``#2952``'s empty-scope rule is shared by C1 and C2. What counts as an
    empty scope, and the failure carve-out above, are stated once in
    :func:`run_vector_query`.
    """
    vecs = [e for e in entries if e.get("leg") == "vector"]
    return bool(vecs) and all(
        e.get(VECTOR_SCOPE_KEY) == VECTOR_SCOPE_EMPTY for e in vecs)


def _degraded_read_marker(reason: str, entries: list[dict]) -> dict:
    """The single marker shape (one construction site, #2952).

    ``vector_leg_unavailable`` / ``missing_legs`` are derived from the
    trace: a results-bearing TF-IDF fallback is a keyword-only read even
    when a healthy-but-empty vector entry is present, so the marker must not
    claim the embedder was unavailable (review P2 fix).
    """
    available = not _vector_leg_healthy(entries)
    return {
        "degraded_read": True,
        "hybrid": False,
        VECTOR_LEG_UNAVAILABLE: available,
        "missing_legs": ["vector"] if available else [],
        "reason": reason,
        "leg_trace": entries,
    }


def _results_bearing_fallback(entries: list[dict]) -> dict | None:
    """The TF-IDF fallback entry when it actually produced the rows (#2952).

    ``tortoise_fts_query`` runs the fallback only when EVERY primary leg
    returned zero rows, so a fallback entry with ``count > 0`` proves the
    returned rows are keyword-only — the read is NOT hybrid even if a
    vector entry recorded a healthy-but-empty run. ``count == 0``
    (``no_fallback_applicable``) returns no rows and is not disqualifying.
    """
    for e in entries:
        if e.get("leg") == "fallback" and (e.get("count") or 0) > 0:
            return e
    return None


def declared_degraded_read(leg_trace: list[dict] | None) -> dict | None:
    """(C1) #2952 — explicit single-leg (vector-unavailable) declaration.

    Returns the marker dict when the trace shows a TEXT read whose vector
    leg did not run (``ran=False``) or ran degraded (``degraded=True`` — e.g.
    ``no_embedder``, ``encode_failed``, ``breaker_open``, ``query_failed``,
    ``index_missing``, ``no_embeddings``, ``timeout``), else ``None``.
    A leg the read never asked — ``no_query_vector``, recorded
    ``ran=False``/``degraded=False`` (#5412) — is also declared: it
    contributed no semantic rows, and the ``reason`` names that instead of
    the misleading ``leg_absent``.

    Shape: ``{"degraded_read": True, "hybrid": False,
    "vector_leg_unavailable": <bool — False for a keyword-only fallback whose
    vector entry ran healthy-but-empty>, "missing_legs": <["vector"] | []>,
    "reason": <leg-trace reason | "leg_absent" | "leg_trace_unavailable" |
    "tfidf_fallback">, "leg_trace": [...]}``.

    ``leg_trace=None`` returns ``None`` — an absent trace is not a
    declaration (a caller that wants the fail-closed treatment calls
    :func:`require_hybrid_read`, which refuses on it). A structural-only
    trace (no text leg — no fts/vector/fallback entry; e.g. a ``query=None``
    full scan) is a single-leg read BY DESIGN and also returns ``None``; an
    EMPTY trace, by contrast, reports nothing and is declared
    ``leg_trace_unavailable`` (fail closed). A fallback-only (TF-IDF) trace
    reports a text leg whose vector leg is absent → ``leg_absent``.

    #4199 empty-scope aggregation: ``recall_state(object_centric=True)``
    submits one vector arm per entity, and an arm whose ``scope_kinds``
    selected NO nodes (:data:`VECTOR_SCOPE_EMPTY`) is NEUTRAL — it neither
    proves the read hybrid nor declares it degraded. An arm that measured a
    different, empty scope therefore cannot launder another arm's genuine
    degradation.
    """
    if leg_trace is None:
        return None
    entries = [e for e in leg_trace if isinstance(e, dict)]
    # Positive structural evidence only: a trace whose ONLY leg is the
    # structural kind-scan is a single-leg read BY DESIGN (query=None full
    # scan). An unknown/malformed leg vocabulary does NOT earn this escape
    # hatch — it falls through and is declared (C1/C2 consistency, review P2).
    if entries and all(e.get("leg") == "structural" for e in entries):
        return None
    # A results-bearing TF-IDF fallback is definitive proof the rows are
    # keyword-only — checked BEFORE the healthy-vector early return (a
    # healthy-but-empty vector entry must not launder a fallback read).
    if _results_bearing_fallback(entries) is not None:
        return _degraded_read_marker("tfidf_fallback", entries)
    vecs_all = [e for e in entries if e.get("leg") == "vector"]
    # #4199: an arm that measured an EMPTY scope is NEUTRAL — it neither
    # proves the read hybrid nor declares it degraded. Drop it before the
    # aggregation so it cannot launder (or be blamed for) another arm's
    # coverage: ``recall_state(object_centric=True)`` submits a Point arm and
    # an Object arm, and the Object arm's kind scope is often empty while its
    # unscoped scan still returns rows.
    vecs = [e for e in vecs_all
            if e.get(VECTOR_SCOPE_KEY) != VECTOR_SCOPE_EMPTY]
    # ``recall_state(object_centric=True)`` appends one entry per query
    # (Point + Object), so a single healthy IN-SCOPE vector entry proves the
    # leg ran. NOTE: stricter than the #3005 battery submission gate (ran AND
    # NOT degraded) — a degraded vector leg contributes no semantic results.
    if _vector_leg_healthy(entries):
        return None
    if _all_vector_arms_measured_empty_scope(entries):
        # EVERY vector arm measured an EMPTY scope: the read's scope holds no
        # nodes at all, so the dense leg is not to blame (#2952's empty-scope
        # rule — ``no_embeddings`` would be a category error there).
        return None
    vec = vecs[0] if vecs else None
    if vec is not None:
        reason = vec.get("reason") or "leg_absent"
    elif entries:
        # a text leg exists (fts/fallback) but no vector entry was recorded
        reason = "leg_absent"
    else:
        reason = "leg_trace_unavailable"
    return _degraded_read_marker(reason, entries)


def require_hybrid_read(leg_trace: list[dict] | None,
                        *, lane: str | None = None) -> dict:
    """(C2) #2952 — fail-loud gate: refuse to label a single-leg read hybrid.

    Capability for real-lane measurement (aligns with the #2985 / PR #3005
    fail-loud pattern): returns ``{"hybrid": True, "vector_leg_unavailable":
    False}`` only when at least one NOT-degraded, IN-SCOPE vector entry RAN
    (:func:`_vector_leg_healthy` skips an arm that measured an EMPTY scope),
    or when EVERY vector arm measured an empty scope (#2952's empty-scope
    rule; ``declared_degraded_read`` is consulted first, so a marker it does
    return refuses here too). It raises
    :class:`~tortoise.exceptions.HybridReadUnavailableError` otherwise —
    including for ``leg_trace=None`` / empty / structural-only traces (a
    surface that cannot positively prove the vector leg fails closed).

    This proves the VECTOR (semantic) leg specifically — the #2952 failure
    class: at least one IN-SCOPE vector entry RAN and was NOT degraded (a
    ``degraded=True`` vector leg contributed no semantic results, so the
    read is keyword-only). This is deliberately STRICTER than the #3005
    battery capability gate, which asks only whether the vector strategy was
    SUBMITTED (``ran`` regardless of ``degraded``); a battery lane that
    records a score should delegate to this predicate so the capability it
    reports cannot be looser than this one. It is not a both-legs health check
    (the fts leg's own degrade is surfaced separately in the trace).

    Opt-in only: nothing in the product calls this by default, so healthy-
    path ranking and default result bytes are unchanged.
    """
    from .exceptions import HybridReadUnavailableError
    if leg_trace is None:
        marker = _degraded_read_marker("leg_trace_unavailable", [])
    else:
        entries = [e for e in leg_trace if isinstance(e, dict)]
        declared = declared_degraded_read(leg_trace)
        if declared is not None:
            marker = declared
        elif _vector_leg_healthy(entries):
            return {"hybrid": True, VECTOR_LEG_UNAVAILABLE: False}
        elif _all_vector_arms_measured_empty_scope(entries):
            # #4199 review P1: on THIS trace every vector arm measured an
            # EMPTY scope, and C1 declared nothing (``declared`` is None
            # here, or its marker would have been raised above) — applying
            # the same rule keeps the two gates aligned on this trace: a read
            # whose dense leg RAN over a scope with no nodes is not a refusal.
            return {"hybrid": True, VECTOR_LEG_UNAVAILABLE: False}
        else:
            # Positive proof required: an absent marker on a non-hybrid trace
            # (e.g. structural-only) must NOT pass as hybrid.
            marker = _degraded_read_marker("leg_absent", entries)
    raise HybridReadUnavailableError(marker, lane=lane)


# ── #10121: the tie-break every ranking leg shares ─────────────────
def recency_tie_clause(alias: str, id_field: str) -> str:
    """The secondary sort every ranking leg appends after its own primary key.

    Three keys, three jobs: the leg's score orders, **recency** breaks the tie
    toward the most recently written row, and the id makes the order TOTAL
    (#3019's requirement — a rank-based fusion downstream cannot tolerate an
    unstable tie: it would pick a different pool every call).

    ⚠️ "Total" is NOT the same claim as "recency decides", and nowhere is the
    difference easier to lose than here. Where a stamp is CARRIED the recency
    key is real and the order is total. Where it is NOT, the key is the
    constant `''`, the engine silently defers to the next key (FalkorDB:
    "for equal values, it will go to the next property in the ORDER BY
    clause"), and the leg is ordered by `coalesce(<id_field>, '') DESC` —
    total, but recency-free. Which population you are in is a **per-WRITER**
    fact, not a per-label one (read off the writers, round-10 review):

    * `:Point` — carries both: `createdAt`/`updatedAt` are seeded by
      `create_point`'s `_create_map`, and a revision bumps `updatedAt`.
    * `:Object` / `:Subject` — `createdAt` stamped ON CREATE by
      `_upsert_object` / `_upsert_subject` (`coalesce($ca, $now)`).
    * `:Event` — **the exception, and the one to get right**: stamping is a
      per-DOOR fact, and ONLY the RECORD door stamps.
      `TortoiseSDK._apply_and_journal` (pre-built records — the provenance /
      indexer writes) synthesizes `createdAt` under
      `type in _JOURNALED_ENTITY_EVENT_TYPES` before the fold, and
      `_EVENT_HANDLED` omits the key on purpose so `_persist_extra_props`
      carries it onto the node (#2788,
      `tests/test_event_state_carrier_2788.py`);
      `tests/test_github_indexer.py::test_7951_indexer_journals_createdAt_for_events`
      pins the RECORD, and the node half was measured in round 10.
      `_create_entity` (the public `create_event` /
      `create_entity(type='event')`) does NOT: its only `createdAt` synthesis
      is gated on the Object/Subject REGISTRATION probes and is "provably
      Object/Event-inert" in its own words, so a `create_event` node carries
      NEITHER stamp (measured). `EventAPI.add_event` builds its payload
      without `createdAt` as well, and the session-indexer / connector writers
      set `startedAt` only. On the PRIMARY public Event door, therefore, the
      recency key is the constant `''` and the order falls to
      `eventId DESC` — which for `session_<id>` ids is not a clock.
    * `:Source` — `ingestedAt` is what these writers always stamp; a document
      `:Source` carries `createdAt` only when its producer emits one (an
      allowed extra prop, `_SOURCE_HANDLED`), and the structural stub
      (`projection/edges.py`) carries `ingestedAt` alone.
    * `:Object` stubs (hosted) — neither stamp AND no id: the original #3019
      residual. `coalesce(id, '')` is a constant there too, so the order
      inside that population is still engine row order; what the guard changes
      is that those rows move from the HEAD of the window to its TAIL, so they
      stop displacing addressable rows.

    ⛔ Widening the key to the stamp the undated families DO carry
    (`startedAt` for session/connector Events, `ingestedAt` for `:Source`)
    would change the DECIDED expression on #10121 and is therefore NOT part of
    this change — it is a reopen on that issue. Nothing here claims more than
    the table above.

    ``coalesce(..., '')`` is load-bearing, not defensive. The engine sorts NULL
    as the LARGEST value, so a bare ``updatedAt DESC`` puts every UNDATED row at
    the HEAD of the window — measured: an id-less ``:Object`` outranks every
    dated one, and the leg's ``LIMIT`` then spends the whole pool on rows the
    caller discards. ``''`` is the smallest string, so an undated row sorts as
    OLDEST — which is what "no timestamp" means — and a dated match keeps its
    slot.

    **Why recency, and not the id direction** (#10121's measured defect): the
    graph-side lexical scorer returns ONE score for a whole corpus of look-alike
    documents (2.0 across 131 documents, both engines), so the tie IS the
    ranking. #3019's ``id ASC`` resolved it oldest-first, and because ``id`` is
    minted from a millisecond timestamp (``ids.ulid()`` -> ``<ts-hex>-<uuid>``)
    the row created LAST took the last pool slot and was truncated out of the
    leg entirely — past the pool depth it carried no score at all, so no fusion
    weight, reranker or MMR could recover it. ``updatedAt`` before
    ``createdAt``: a tie should surface the LATEST REVISION (a dedup hit or
    ``update_point`` bumps ``updatedAt``; a fresh row stamps both), so one
    expression serves every family that carries EITHER stamp — and only those
    (see the per-writer table above). Live operators
    carry NEITHER property (``projection/__init__.py``: "probe: None"), so they
    fall through to the id key — which for minted ids is creation order.

    The id key is ``DESC`` on EVERY label. It is only the determinism key, so
    its direction carries no meaning; branching it per label is precisely how
    the first attempt at this fix got the id-less ``:Object`` case wrong.
    """
    # ``coalesce(<id>, '')`` on the id key, for the same reason the recency key
    # has one: the engine sorts NULL as the LARGEST value, so a BARE
    # ``<id> DESC`` puts every ID-LESS row at the HEAD of the window — and
    # ``:Object`` stubs are minted id-less (``MERGE (o:Object {name:$name})``),
    # so those head slots are spent on rows the resolver then discards
    # (``assembly.py``: ``if oid``). Measured on BOTH engines: bare
    # ``ORDER BY n.id DESC`` → ``[None, None, 'obj-aaa']``; with the coalesce →
    # ``['obj-aaa', None, None]``. This is also what makes the cypher half and
    # the Python half (:func:`recency_tie_key`) agree — both send an absent
    # identity to the TAIL.
    return (
        f"{recency_key_expr(alias)} DESC, "
        f"coalesce({alias}.{id_field}, '') DESC"
    )


def recency_tie_key(recency: object, ident: object) -> tuple[str, str]:
    """The PYTHON-side twin of :func:`recency_tie_clause`'s secondary keys.

    Two legs sort in Python rather than in cypher — this one's vector index
    branch and ``bm25.bm25_rank`` — and #10121's review found them re-expressing
    the rule by hand (a three-pass sort here, a tuple key there), i.e. the same
    drift seam the cypher helper was created to close. Both use THIS, sorted
    DESCENDING.

    ⚠️ Scope, stated with its DIRECTION rather than as "not our problem": the
    comparison is STRING-based, as the cypher clause's is. A non-string stamp (a
    numeric epoch) sorts as the NEWEST here: ``str(20261201)`` is
    ``'20261201'`` and the discriminating character is the fifth one — ``'1'``
    against every ISO stamp's ``'-'`` — so it wins under ``DESC``. What the
    ENGINE does with a numeric stamp is type-precedence-dependent and is NOT
    asserted here (no measurement in this repo); the two sides are therefore
    known to agree for every string stamp and unverified for a numeric one. No writer emits one today (the
    writer test pins the stamps of ``create_point``; a props passthrough could
    still carry one), and making that impossible is its own change, not a silent
    guess made here.

    ``None`` is normalised to ``''`` on both components: ``''`` sorts as the
    OLDEST stamp and as the LAST identity, which is what the clause means by an
    undated or id-less row — and it makes the tuple comparable, which the raw
    values are not (``sorted([None, None])`` raises, and an id-less ``:Object``
    is a live population — that TypeError was swallowed by the index branch's
    ``except Exception`` and silently demoted every such read to a full scan).
    """
    return (
        "" if recency is None else str(recency),
        "" if ident is None else str(ident),
    )


def recency_key_expr(alias: str) -> str:
    """The recency EXPRESSION alone — the one a projection must RETURN.

    Split out of :func:`recency_tie_clause` because #10121's review found the
    window and the ranking disagreeing: the ORDER BY used the CASE below while
    the BM25 arm's corpus read and both vector index signatures projected a
    plain ``coalesce``, so a row stamped ``updatedAt: ''`` was admitted as the
    NEWEST by the window and then sorted as OLDEST — or dropped — by the
    ranking. One expression now decides both for every string stamp. (A
    non-string stamp is out of scope and stays that way: no writer emits one,
    and mixing engine type precedence with ``str()`` would need its own
    decision — see :func:`recency_tie_key`.)

    The CASE (not a plain ``coalesce``) is load-bearing: ``coalesce`` stops at
    the first NON-NULL, so ``updatedAt: ''`` would SUPPRESS a valid
    ``createdAt``. Measured on both engines, the CASE falls through and orders
    ``['2026-09-01', '2026-01-01', '']``. (``nullif`` is not registered on
    FalkorDB 4.20.6, hence the explicit comparison.)
    """
    return (
        f"CASE WHEN coalesce({alias}.updatedAt, '') = '' "
        f"THEN coalesce({alias}.createdAt, '') ELSE {alias}.updatedAt END"
    )


def run_fts_query(
    graph, query: str, entity_type: str = "point", limit: int = 20,
    timeout_ms: int = 500, excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
    keep_numeric: bool = False,
    expansion_terms: list[str] | None = None,
) -> list[tuple[str, float]]:
    """Run full-text search via FalkorDB FTS index.

    Falls back gracefully if index doesn't exist or query fails.
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
    'source', or 'operator'. Document FTS searches the _searchText index
    (#125) which concatenates title+summary+topics.
    Returns n.url for source and document (the :Source canonical key, #448 — a
    document IS a :Source), n.eventId for event, n.id for all other entity
    types.

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at EVERY exit branch (the FTS leg is recorded AT THE SOURCE, never
    reconstructed from the results dict — an absent strategy is
    indistinguishable from one that ran-and-returned-empty). The index-
    missing catch is promoted to ``reason="index_missing"`` so surface 11's
    owned negative ("fts skipped (no index)" vs "fts ran, no results") is
    truthful from R3 forward. Default None = no trace (byte-identical).

    R2 (#1541) D1: the ``queryNodes`` path runs ``build_or_query`` — the
    shared OR-union tokenizer (tortoise.sparse) replaces the strict-AND raw
    passthrough so a point differing from the question by ONE token is no
    longer zeroed (RediSearch AND requires every whitespace term present).
    The operator path (label CONTAINS) is structural and unchanged; full-
    scan mode (query=None) never reaches this function. Single-token and
    stopword-only queries return the raw string — byte-identical to the
    pre-R2 behavior for inputs without RediSearch-special characters
    (escaped via escape_redisearch_literal, issue #1791).

    Note: the connection-level `timeout` is passed straight to the FalkorDB
    driver (Graph.query(timeout=...)) so a slow query is killed server-side,
    not merely observed. The post-hoc check below remains as a safety net
    for drivers that ignore the timeout. (#249) The per-strategy circuit
    breaker additionally short-circuits after consecutive slow/failed
    queries so a wedged DB stops eating caller latency.
    """
    # #5404: entity_type is caller-controlled, and this leg interpolates its
    # capitalized form into a Cypher LABEL — the label is query STRUCTURE.
    # Guard at the ENTRY, not at the label: the breaker short-circuit below
    # returns [] before any label exists, so a guard placed there would let an
    # invalid type through silently whenever the breaker is open.
    validate_entity_type(entity_type)

    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int) -> None:
        # #4489: the ledger note is UNCONDITIONAL (unarmed → no-op), placed
        # ahead of the opt-in trace so a default product read still records the
        # leg. Mutation: moving it inside the ``leg_trace`` gate → the read
        # funnel's leg count stays 0.
        _retrieval_metering.note_leg("fts", ran=ran, degraded=degraded)
        if leg_trace is not None:
            leg_trace.append(_trace_entry("fts", ran=ran, degraded=degraded,
                                          reason=reason, count=count))

    if entity_type == "operator":
        # Operators are Points with is_operator=true — match label via CONTAINS
        if not _breaker_allow("fts"):
            logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
            _record(ran=False, degraded=True, reason="breaker_open", count=0)
            return []
        try:
            start = time.monotonic()
            cypher = (
                "MATCH (n:Point) "
                "WHERE n.is_operator = true"
                + ("" if excluded_statuses == ()
                   else f" AND {_exclude_status_clause('n', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)}")
                + "  AND toLower(n.label) CONTAINS toLower($query) "
                "RETURN n.id, 1.0 AS score "
                # #3019: every row scores a constant 1.0, so this leg is ONE giant
                # tie and DB row order would otherwise decide each operator's RRF
                # rank. The secondary key makes the order a function of the data.
                # #10121: the shared recency tie — the newest operator first.
                f"ORDER BY score DESC, {recency_tie_clause('n', 'id')} "
                "LIMIT $limit"
            )
            rows = graph.query(
                cypher, params={"query": query, "limit": limit}, timeout=timeout_ms
            ).result_set
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                # #561: post-hoc latency warning only — a driver that ignored
                # the timeout returned rows; keep them (real hangs are killed
                # server-side by the driver-level timeout, surfacing as an
                # exception → breaker failure below).
                logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            _breaker_record("fts", True)
            _record(ran=True, degraded=False, reason="ok", count=len(rows))
            return [(row[0], float(row[1])) for row in rows]
        except Exception as e:
            logger.warning("Operator FTS query failed: %s", e)
            _breaker_record("fts", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
            return []
    if not _breaker_allow("fts"):
        logger.warning("FTS circuit breaker OPEN — skipping FTS strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
        return []
    # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source, so the FTS leg reads
    # the Source label (`_searchText`) — there is no :Document index. The
    # caller-facing entity_type stays "document".
    # point→Point, event→Event, subject→Subject
    # NOTE: this leg keeps its own derivation rather than entity_label() —
    # migrating all three legs onto that mapping is #5407's scope.
    label = "Source" if entity_type == "document" else entity_type.capitalize()
    # #448: three-way id_field — source→url (canonical key, #149),
    # event→eventId, else→id. D10: a document Source resolves by url too.
    #
    # #3019 KNOWN RESIDUAL — NARROWED by #10121, and closed exactly this far.
    # `object` has the same NULL-key shape: a :Object is merged by its `name`,
    # and the live write path mints it id-less (`MERGE (o:Object {name:$name})`,
    # hosted_api), so the `else` below resolves `id` and every :Object row sorts
    # on a NULL identity there. #10121 APPLIED that coalesce KEY — #3019's own
    # named remedy, not the field rename it ruled out — because
    # `recency_tie_clause` emits `coalesce(<alias>.<id_field>, '') DESC` at
    # every site, so those rows no longer take the HEAD of the window from a
    # NULL-as-largest sort; they land at its TAIL (pinned by
    # `test_10121_recency_tie.py::test_an_id_less_row_does_not_lead_a_tie_group
    # _without_timestamps`). What REMAINS residual is the order INSIDE that
    # population: `coalesce(id, '')` is a constant for all-id-less rows, so
    # their relative order is still engine row order. Evidence on #3019.
    if entity_type in ("source", "document"):
        id_field = "url"
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"
    # R2 (#1541) D1: OR-union tolerance for the text path — all labels
    # share the tokenizer (single-token/degenerate inputs pass the raw
    # string through backslash-escaped). A1/A4 (#2070): ``keep_numeric``
    # (the ask-lane numeric-token policy) and ``expansion_terms`` (additive
    # pseudo-relevance-feedback aliases) thread into ``build_or_query`` —
    # both default off, so the search lane's query bytes are unchanged.
    from .sparse import build_or_query
    fts_query = build_or_query(
        query, keep_numeric=keep_numeric,
        expansion_terms=expansion_terms)
    # #689/#1391: terminal-status Points must not leak into FTS results
    # (skipped when the caller opts in via include_terminal — audit/history).
    # D10 (#5026): the :Source label holds BOTH the document node
    # (documentKind non-NULL) and the corpus/provenance Source (documentKind
    # NULL), and the fulltext index is on Source._searchText for EVERY Source.
    # Without a discriminator, entity_type="document" retrieves non-document
    # Sources (and entity_type="source" retrieves documents) and they occupy
    # pool slots before LIMIT. The predicate lands in the post-YIELD WHERE —
    # i.e. at the retrieval layer, ahead of ORDER BY/LIMIT — on the SAME axis
    # as quota.py's `documents` meter (documentKind IS NOT NULL) and
    # sdk.list_sources() (documentKind IS NULL). All three legs mean one
    # thing: document ⟺ documentKind non-NULL, source ⟺ documentKind NULL.
    if label in _STATUS_FILTERED_LABELS:
        # #3301: the OBJECT family shares this leg's exclusion — before it,
        # a retracted/superseded/deprecated/archived Object was SEARCH-VISIBLE
        # while recall_state hid it. #1423 adds the SUBJECT family (same
        # doctrine: a superseded team/role/person is a dead entity, so it is
        # not current state). The family rule (vocabulary + whether the
        # Point ``outdated`` flag applies) lives in ``_status_vocab_for``.
        _vocab, _flag = _status_vocab_for(label)
        status_filter = ("" if excluded_statuses == ()
                         else f"WHERE {_exclude_status_clause('node', excluded_statuses or _vocab, include_outdated_flag=_flag)} ")
    elif entity_type == "document":
        status_filter = "WHERE node.documentKind IS NOT NULL "
    elif entity_type == "source":
        status_filter = "WHERE node.documentKind IS NULL "
    else:
        status_filter = ""
    try:
        start = time.monotonic()
        cypher = (
            f"CALL db.idx.fulltext.queryNodes('{label}', $query) "
            "YIELD node, score "
            + status_filter +
            f"RETURN node.{id_field}, score "
            # #3019: rank alone does not order a tie, and RRF is rank-based — see
            # the operator path above. #10121: shared recency tie (the newest
            # match keeps its slot instead of being truncated oldest-last).
            f"ORDER BY score DESC, {recency_tie_clause('node', id_field)} "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query": fts_query, "limit": limit}, timeout=timeout_ms
        ).result_set
        # Post-hoc timeout check (see docstring for rationale) — #561: log
        # latency, return the results (the driver-level timeout is the real
        # hang-killer; results received = normal completion for the breaker).
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("FTS query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
        _breaker_record("fts", True)
        # R3 (#1542) D4: a clean zero-hit run is ``empty_results`` (the leg
        # RAN and found nothing) — distinct from ``index_missing`` below.
        _record(ran=True, degraded=False,
                reason=("ok" if rows else "empty_results"), count=len(rows))
        return [(row[0], float(row[1])) for row in rows]
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            # Benign — expected in embedded FalkorDBLite (no FTS index) and for
            # labels without an index. Degrades quietly; does NOT count toward
            # tripping the breaker (a healthy deployment with one index-less
            # label must not disable FTS for all labels). (#249 review P1-1)
            logger.info("FTS index not available — skipping FTS strategy")
            _breaker_record("fts", True)
            # R3 (#1542) D4: the promoted index-missing trace (surface 11's
            # owned negative — "fts skipped (no index)" is recorded, never
            # silently conflated with "fts ran, no results").
            _record(ran=True, degraded=True, reason="index_missing", count=0)
        else:
            logger.warning("FTS query failed: %s", e)
            _breaker_record("fts", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
        return []


#: #8488 — how many nodes the in-process BM25 leg will read to build its
#: corpus. BM25 is a CORPUS statistic (idf needs document frequency, ``b``
#: needs the average document length), so unlike the other legs it cannot be
#: answered by an indexed top-k: it has to see the candidate population.
#:
#: The cap is a memory/latency bound, not a ranking parameter, and it is
#: LOUD **and DECLINED**: the leg asks for one row more than the cap, so it can
#: tell "exactly cap" (a complete ranking) from "more than cap" (a window) —
#: and on a WINDOW it returns NO rows at all, recording ``declined=True``
#: (NOT ``degraded`` — see below). The
#: shipped graph-side leg answers over the whole label (it is index-backed), so
#: replacing its rows with a ranking over only ``cap`` of the label's rows
#: would make the read a WINDOW masquerading as a complete ranking — silently,
#: on a store the operator never measured. Declining keeps the shipped rows
#: (the same fail-safe the empty arm gets) and leaves the signal in the trace.
#: Raise the cap before enabling the arm on a store that large.
#:
#: #10121 fixed the half that made this decline look like a ranking verdict:
#: the window used to be the first ``cap`` ids in id order — OLDEST-first on
#: this tree's ULID ids, blind to the newest documents, which are the likeliest
#: lexical answer. It is now the shared recency order
#: (:func:`recency_tie_clause`), so the rows that survive the cap are the
#: NEWEST ones. The decline itself stands, and for a narrower reason than
#: before: a window is still not the whole-label answer the shipped leg gave,
#: so the arm still declines rather than silently answering over a subset. The
#: cap's remaining job is purely a memory/latency bound.
#:
#: ⛔ What the decline must NOT cost the read (#8488 follow-up). The decline
#: used to record ``degraded=True`` so that a measurement could not read a
#: truncated corpus as a clean BM25 result. That carrier was wrong, because
#: ``degraded`` is NOT a leg-local flag: ``read_status.classify_leg_trace``
#: reports the whole READ as ``degraded`` when any entry carries it, and
#: ``ask_lane`` sets ``retrieval_degraded`` the same way. So on a store at the
#: cap, enabling the arm relabelled EVERY successful read "the memory layer is
#: IMPAIRED" — the recorded term for a store that could not be reached or a
#: retrieval leg that did not run, NOT a leg-local "this scorer returned
#: nothing" — while nothing here was impaired: the shipped lexical leg had in
#: fact answered over the WHOLE label and every row was returned. The
#: arm is an alternative SCORER for a SAME leg slot, not a leg, so its
#: declining cannot mean "a retrieval leg did not run". (The decline's own entry
#: adds no impairment; it says nothing about the OTHER legs, which report
#: themselves — a vector leg that failed is degraded on its own entry.)
#:
#: The measurement's need — "a truncated corpus must not read as a clean BM25
#: measurement" — is what :data:`LEXICAL_DECLINED_KEY` makes ANSWERABLE, and
#: it is not yet answered by any consumer in this tree: the entry records
#: ``ran=True, declined=True, degraded=False, count=0`` with NO ``mechanism``
#: key (BM25 produced no rows, so it may not claim to have), so a baseline can
#: count declines exactly instead of inferring them from a blanket
#: degradation — but no in-tree fold counts it yet, and the TWO folds that
#: read the old carrier now record a declining arm as a RUN, not an impairment,
#: deliberately:
#:
#: * ``retrieval_preflight.merge_leg_trace`` — the shared interpreter both
#:   ``arms/a4_tortoise`` and ``parity/mabench_tortoise`` record through — folds
#:   only ``degraded``, so the run record's (and the parity cell's)
#:   ``retrieval_degraded`` no longer fires on a decline, and a decline is
#:   therefore NOT distinguishable there from the arm being OFF.
#: * ``retrieval_metering`` tallies the decline as a leg run, not a degraded one.
#:
#: That is the correct polarity for both (neither claims an impairment, which is
#: what this whole note is about), but it does mean the decline's measurement
#: signal is carried ONLY by the trace entry: a measurement that wants it must
#: read :data:`LEXICAL_DECLINED_KEY` off the trace — which is exactly what
#: #8486's baseline has to do, and why the key exists. Whoever wires it must not
#: route it back through ``degraded``; the parity lane already persists the raw
#: entries (``capability_gate.leg_trace``), so the signal survives in that
#: artifact even while the boolean does not carry it.
#:
#: ⛔ Cost, stated and NOT measured. Unlike ``run_vector_query``'s embedded
#: brute force — which scores inside the engine and returns ``limit`` rows —
#: this leg pulls up to ``cap`` rows *including* ``content`` and
#: ``search_keys`` into Python and tokenises all of them on the caller's
#: thread, OUTSIDE ``degradation_chain``'s ``as_completed`` deadline (only the
#: driver-side query timeout bounds it). That is why the arm is gated off, is
#: embedded-lane-only, and why its latency must be measured (#8486) before it
#: is enabled anywhere.
DEFAULT_BM25_CORPUS_CAP = 20000

#: #8488 — trace-entry key marking a lexical ARM that ran and then DECLINED to
#: rank (``ran=True, count=0``): the corpus it could read was a window
#: (:data:`DEFAULT_BM25_CORPUS_CAP`), so it returned nothing and left the
#: shipped leg's whole-label rows in place. PER-LEG: written only on an
#: ``fts`` entry produced by the in-process BM25 arm; no other leg passes it.
#:
#: Why it exists instead of ``degraded``: ``degraded`` is aggregated into the
#: READ's recorded status (``read_status.classify_leg_trace``) and into
#: ``ask_lane``'s ``retrieval_degraded``, so carrying a leg-local decline there
#: reported a complete read as an impaired one. ``mechanism`` cannot carry it
#: either — that key answers "which path produced these rows", and here there
#: are no rows, which is why a declined entry writes no ``mechanism`` at all.
LEXICAL_DECLINED_KEY = "declined"

#: #8488 — the value of the leg trace's ``mechanism`` key when the lexical leg
#: was scored IN-PROCESS by Okapi BM25 (``run_bm25_query``) rather than by the
#: graph's fulltext engine (``run_fts_query``). Written under the ``fts`` leg
#: name — the trace vocabulary has three leg slots and the lexical one is that
#: name; ``mechanism`` is the additive key #4999 introduced for exactly this
#: question (which path produced these rows).
LEXICAL_MECHANISM_BM25 = "bm25"


def clamp_lexical_fusion_weight(weights: dict[str, float] | None) -> dict[str, float]:
    """Apply the #8488 lexical bound to a resolved fusion-weight map.

    It IS a no-op for the recorded production default: with
    ``{"vector": 1.5}`` (owner decision 2026-08-25) and the fts leg taking
    ``rrf_fusion``'s implicit 1.0, the lexical weight is already at the bound
    and the map comes back with the same numbers.

    It is NOT a no-op for an operator who has explicitly tuned the lexical leg
    ABOVE that bound — ``TORTOISE_FUSION_WEIGHTS='{"fts": 2.0}'`` becomes 1.0.
    That is deliberate, and it is the one behaviour this clamp changes: an
    unbounded lexical weight lets the lexical leg decide a fused order by
    itself, which is the #7704 defect class, and the reason #1657 weighted the
    legs in the first place. The cap is therefore enforced on every source of
    the number — the env knob, a kwarg caller, a future default — rather than
    trusted per source. The bound and its justification live in
    ``tortoise/bm25.py`` (ONE home for the constant); this is only where the
    read path applies it.
    """
    from .bm25 import DEFAULT_LEXICAL_WEIGHT, resolve_lexical_weight

    out = dict(weights or {})
    out["fts"] = resolve_lexical_weight(
        out.get("fts", DEFAULT_LEXICAL_WEIGHT),
        out.get("vector", 1.0),
    )
    return out


#: Entity families this leg CAN serve (#8488) — and the ONLY ones it may
#: attempt. Its corpus text is ``index_text(coalesce(content, _searchText),
#: search_keys)``, i.e. fields that actually exist on the label:
#:
#: * ``point``    → ``content`` ∪ ``search_keys`` — the ``Point`` fulltext
#:   index's declared fields, byte-for-byte;
#: * ``document`` / ``source`` → ``_searchText`` (a ``:Source`` stores no raw
#:   bytes, D10 — the text lives on ``_searchText``, the field its own FTS
#:   index is declared on).
#:
#: The three refused families are refused for the SAME reason — the leg could
#: not score them, so attempting them would be a full-label scan that returns
#: nothing while the trace claimed it "ran and found nothing":
#:
#: * ``object``  — merged by ``name`` and minted id-less (the #3019 note
#:   above), so an id-keyed corpus read returns nothing;
#: * ``operator`` — carries no ``content``; its own FTS leg matches on
#:   ``label CONTAINS``, a different axis entirely;
#: * ``event`` / ``subject`` — their declared FTS fields are ``subject``/
#:   ``name`` and ``name`` respectively, and NEITHER ``content`` nor
#:   ``_searchText`` is written on those labels by the canonical writers
#:   (``_EVENT_HANDLED`` / ``_SUBJECT_HANDLED`` in ``projection/entities.py``),
#:   so every row would tokenise to ``[]``. Serving them would mean a
#:   per-family text projection, which breaks this leg's parity claim ("the
#:   SAME index text the FTS index builds") and has no measured demand — the
#:   reopen, if one is ever needed, should add that projection WITH its
#:   parity test rather than resurrect this arm.
#:
#: Refused rather than silently scanned for nothing: the refusal is the early
#: return in :func:`run_bm25_query`, so this tuple is the ONE place that
#: decides which families the leg serves.
BM25_ENTITY_TYPES = ("point", "document", "source")


def run_bm25_query(
    graph, query: str, entity_type: str = "point", limit: int = 20,
    timeout_ms: int = 500, excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None, keep_numeric: bool = False,
    corpus_cap: int = DEFAULT_BM25_CORPUS_CAP, meter: bool = True,
) -> list[tuple[str, float]]:
    """The in-process Okapi BM25 lexical leg (#8488).

    The sibling of :func:`run_fts_query`, scored by
    :func:`tortoise.bm25.bm25_rank` instead of the server's fulltext scorer. It
    exists because the graph-side scorer prices neither rarity nor length (see
    ``tortoise/bm25.py``'s module docstring for the measurements): a document
    holding the RARE term of a query earns exactly what each document holding
    only a ubiquitous one earns, and a 30x repetition scores the same as a
    single occurrence.

    The indexed text is ``index_text(coalesce(content, _searchText),
    search_keys)`` — the SAME fields each served label's own FTS index is
    declared on (``tortoise.sparse``; see ``BM25_ENTITY_TYPES`` for the
    per-family mapping), so the lexical stacks cannot disagree about what a
    document says. A family outside ``BM25_ENTITY_TYPES`` is REFUSED at entry
    rather than scanned for nothing — see that constant.

    Returns ``[(id, score)]`` ranked descending, ties broken by id (#3019: RRF
    is rank-based, so an unstable tie would make the fused order a function of
    dict order). Empty list on every failure — the leg DEGRADES, never fails a
    read (the ``run_*_query`` contract).

    R3 (#1542) D4: ``leg_trace`` — an entry is appended at EVERY exit branch.
    R3's reason vocabulary only uses terms the read path already declares
    (``ok`` / ``empty_results`` / ``query_failed`` / ``breaker_open``); a query
    whose tokens all get dropped by the caller's numeric policy is
    ``empty_results`` (the leg ran, nothing matched), never a new term the
    declaration layer does not know.

    WHO RAN, and what the ``mechanism`` key therefore means. Three exit
    branches return ``[]`` and they do NOT claim the same thing:

    * REFUSED family (``BM25_ENTITY_TYPES``) — ``ran=False``, so NO
      ``mechanism`` key is written: nothing was read, and the leg cannot
      search that family at all. A consumer counting ``mechanism == "bm25"``
      must not count it (that is ``_record``'s own contract).
    * corpus WIDER than ``corpus_cap`` — ``ran=True``, ``declined=True``,
      ``degraded=False``, ``count=0``: the leg DID read, and it declined to
      return a windowed ranking (see ``DEFAULT_BM25_CORPUS_CAP``), so the
      shipped whole-label rows survive. It writes NO ``mechanism`` key for the
      same reason the refusal does not: BM25 produced no rows for this read,
      so a baseline counting ``mechanism == "bm25"`` must not count it — it
      counts :data:`LEXICAL_DECLINED_KEY` instead. It is NOT ``degraded``:
      that flag is aggregated into the READ's recorded status, and this decline
      impairs nothing (the shipped lexical leg answered over the whole label).
      Marking it degraded relabelled healthy reads as impaired memory.
    * a query that matched nothing — ``ran=True``, ``degraded=False``,
      ``empty_results``, ``count=0``.

    ``degraded`` is recorded only where the leg's OWN result is incomplete
    AND the read is impaired by it (a failed read, an open breaker), never on
    a clean empty result and never on a decline.

    ``keep_numeric`` is threaded, never decided: the search lane's
    byte-identical control is a RECORDED decision and its reopen belongs to the
    owner (#8933). With the default, ``ERR-4032`` contributes ``err`` only —
    the leg is honest about the policy it was given.

    ``meter=False`` suppresses the retrieval-tally note while keeping the trace
    entry. The read path uses it when it runs this leg ALONGSIDE the shipped
    lexical leg (which has already counted the lexical slot once): one slot,
    one tally, two distinguishable trace entries.
    """
    # #5404: same entry guard as the other legs — entity_type is interpolated
    # into a Cypher LABEL below, which is query STRUCTURE.
    validate_entity_type(entity_type)

    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int, declined: bool = False) -> None:
        # The leg NAME stays "fts": the leg trace's vocabulary has three slots
        # and the lexical one is that name (``read_status``, the metering fold
        # and the eval lane all key on it). WHICH scorer produced the rows is
        # carried by the trace's already-declared ``mechanism`` key — the same
        # additive seam #4999 introduced for index-vs-scan, so a measurement
        # can tell a BM25-scored lexical leg from a server-scored one without
        # a fourth leg name anywhere in the tree. The key is written ONLY when
        # the leg ran AND did not decline (a declined arm produced no rows, so it
        # may not claim the path that produced them): ``_trace_entry``'s contract
        # leaves it absent on a ``breaker_open`` skip, on the refusal branch, and
        # on the corpus-cap decline, and a consumer counting ``mechanism ==
        # "bm25"`` must count neither a skip nor a decline as a BM25-scored read.
        if meter:
            _retrieval_metering.note_leg("fts", ran=ran, degraded=degraded)
        if leg_trace is not None:
            leg_trace.append(_trace_entry(
                "fts", ran=ran, degraded=degraded, reason=reason, count=count,
                mechanism=(LEXICAL_MECHANISM_BM25
                           if (ran and not declined) else None),
                declined=declined))

    if not _breaker_allow("bm25"):
        logger.warning("BM25 circuit breaker OPEN — skipping BM25 strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
        return []
    if not query or not query.strip():
        _record(ran=True, degraded=False, reason="empty_results", count=0)
        return []
    if entity_type not in BM25_ENTITY_TYPES:
        # See BM25_ENTITY_TYPES: an id-less :Object, a content-less :Operator
        # and the two families whose text lives on fields this leg does not
        # read (:Event/:Subject) would each scan the whole label and score
        # nothing — refused here rather than reported as a clean "ran, found
        # nothing" (there is no second, per-family implementation of "how
        # operators/events are served"; this early return IS the refusal).
        #
        # ``ran=False``: nothing was read, so NO ``mechanism`` key may be
        # written. #8486's baseline counts ``mechanism == "bm25"``, and a
        # refusal tallied as a run would inflate it with reads that never
        # happened (the shape ``_record`` itself calls out).
        _record(ran=False, degraded=False, reason="empty_results", count=0)
        return []

    from .bm25 import bm25_rank
    from .sparse import index_text

    # Label via the ONE declaration (#4997) rather than a per-leg derivation
    # (#5407 has not migrated run_fts_query off its own); a document IS a
    # :Source (D10, ONTOLOGY v3.15 §4.4).
    label = entity_label(entity_type)
    id_field = "url" if entity_type in ("source", "document") else "id"

    where = [f"n.{id_field} IS NOT NULL"]
    if label in _STATUS_FILTERED_LABELS:
        _vocab, _flag = _status_vocab_for(label)
        if excluded_statuses != ():
            where.append(_exclude_status_clause(
                "n", excluded_statuses or _vocab,
                include_outdated_flag=_flag))
    elif entity_type == "document":
        where.append("n.documentKind IS NOT NULL")
    elif entity_type == "source":
        where.append("n.documentKind IS NULL")

    try:
        start = time.monotonic()
        # #10121: the ORDER BY is the SHARED recency tie — newest first, id last
        # as the determinism key. This read used to be `n.<id> ASC`, which is
        # OLDEST-first on ULID ids, so the `cap` window dropped the NEWEST
        # documents first (the #10121 defect at its cap variant). What the cap
        # contract in DEFAULT_BM25_CORPUS_CAP requires is DETERMINISM, not that
        # direction; ``coalesce`` lets a node family whose text lives on
        # ``_searchText`` (the multi-field FTS index's field) be scored rather
        # than skipped. One row over the cap is requested on purpose — see
        # ``truncated`` below.
        cypher = (
            f"MATCH (n:{label}) WHERE {' AND '.join(where)} "
            f"RETURN n.{id_field}, coalesce(n.content, n._searchText, ''), "
            "n.search_keys, "
            f"{recency_key_expr('n')} "
            f"ORDER BY {recency_tie_clause('n', id_field)} LIMIT $cap"
        )
        rows = graph.query(
            cypher, params={"cap": int(corpus_cap) + 1}, timeout=timeout_ms
        ).result_set
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            logger.warning("BM25 corpus read exceeded timeout: %.0fms > %dms",
                           elapsed, timeout_ms)
        # ONE row past the cap, so the leg can tell "exactly cap" (the whole
        # corpus fits — a complete ranking) from "more than cap" (a window).
        # Without the extra row the two are indistinguishable and a store whose
        # size happens to equal the cap loses its lexical leg for no reason.
        truncated = len(rows) > int(corpus_cap)
        if truncated:
            # DECLINE, do not return a window. The shipped graph-side leg
            # answered over the whole label; a ranking over only ``cap`` of its
            # rows is not the same answer and is
            # often a weaker one — so the caller keeps the shipped rows, the
            # same fail-safe an empty arm gets. ``declined`` records WHY the
            # slot was left alone; ``count=0`` because nothing was returned.
            #
            # NOT ``degraded``: that flag propagates to the READ's recorded
            # status (``read_status.classify_leg_trace``) and to
            # ``ask_lane``'s ``retrieval_degraded``, and this decline impairs
            # nothing — the shipped lexical leg answered over the whole label and
            # the rows are returned. The decline is a property of THIS ARM, not
            # an impairment of the memory layer (see ``DEFAULT_BM25_CORPUS_CAP``).
            logger.warning(
                "BM25 corpus exceeds the %d-row cap — declining to replace "
                "the shipped lexical rows with a ranking over only %d of "
                "the label's rows; raise %s or measure on a smaller corpus "
                "(#8486)",
                corpus_cap, corpus_cap, "DEFAULT_BM25_CORPUS_CAP")
            _breaker_record("bm25", True)
            _record(ran=True, degraded=False, reason="empty_results",
                    count=0, declined=True)
            return []
        docs = [
            (row[0], index_text(row[1], row[2])) for row in rows if row[0]
        ]
        # #10121: the arm's own tie must follow the SAME recency-then-id rule as
        # every other leg. It sorts its whole corpus itself, so fixing only the
        # corpus-read ORDER BY would leave the newest match truncated at this
        # sort (measured before this fix: the just-created point absent from
        # ``run_bm25_query(limit=5)`` on a 21-row tie, rank 20 of 21).
        recency = {
            row[0]: ("" if len(row) <= 3 or row[3] is None else str(row[3]))
            for row in rows if row[0]
        }
        ranked = bm25_rank(
            query, docs, limit=limit, keep_numeric=keep_numeric,
            recency=recency)
        _breaker_record("bm25", True)
        _record(ran=True, degraded=False,
                reason=("ok" if ranked else "empty_results"),
                count=len(ranked))
        return ranked
    except Exception as e:
        logger.warning("BM25 query failed: %s", e)
        _breaker_record("bm25", False)
        _record(ran=True, degraded=True, reason="query_failed", count=0)
        return []


def run_vector_query(
    graph, query_vec: list[float], limit: int = 20, timeout_ms: int = 500,
    is_embedded: bool = True, entity_type: str = "point",
    vector_index_api: str | None = None, excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
    min_similarity: float | None = None,
    scope_kinds: tuple[str, ...] | None = None,
) -> list[tuple[str, float]]:
    """Run vector similarity search via FalkorDB vector index.

    ``query_vec``'s required width is the STORE's, never a literal. The index
    path needs a vector matching the width the index was created with — the
    one ``FalkorProjection.required_embedding_dim`` declares (#4280) — while
    the brute-force branch below (the embedded lane, an old FalkorDB, or any
    lane whose index query fails) is DIMENSION-AGNOSTIC: it is
    ``vec.euclideanDistance`` over ``vecf32``-wrapped values with no width
    literal anywhere. An index-less store therefore reads back whatever width
    its own encoder produced, and that is DELIBERATE — pinning the
    brute-force lane to 384 re-introduces the #4280 regression (an
    index-less store silently loses usable vectors) rather than fixing
    anything. ``required_embedding_dim`` is the ONE home of the width rule;
    do not mint a second one here.

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at every exit branch. The empty-embedding case is an EXPLICIT zero-row
    guard on the success path (an all-no-embedding graph returns [] WITHOUT
    raising — the exception-only catch never fires for the real empty case):
    {0-row result set with zero embedded points → ``no_embeddings``},
    {0-row result set with embedded points present → ``empty_results``},
    {the caller supplied NO query vector → ``no_query_vector``, with
    ``ran=False``/``degraded=False`` — the leg was never asked (#5412)},
    {an embedding the distance expression cannot read — a non-numeric value, or
    a width the query vector cannot pair with (a plain ``List`` of the RIGHT
    width is normalised by ``vector_write.stored_embedding_vector_expr``, #8316)
    — or a driver exception → ``query_failed``}. Default None = no trace.

    In Docker/server mode (is_embedded=False), uses the HNSW vector index
    via CALL db.idx.vector.queryNodes — with both known argument orders
    (#1359): signature A (label, attr, vec, k) for the RediSearch-style
    repo-pinned docker image, signature B (label, attr, k, vecf32(vec))
    for Cypher-native engines (falkordblite 0.10.0's bundled module).
    Falls back to brute-force vec.euclideanDistance if the index is
    unavailable (embedded mode, old FalkorDB, or index creation failed).

    #4999: because both paths return rows, the leg-trace entry carries a
    ``mechanism`` key naming the path taken — ``"index"`` | ``"scan"`` |
    ``"scan_fallback"`` (:data:`MECHANISM_INDEX` / :data:`MECHANISM_SCAN` /
    :data:`MECHANISM_SCAN_FALLBACK`, key :data:`VECTOR_MECHANISM_KEY`). A
    caller can therefore tell an index-accelerated query from a full table
    scan without reading this source, and can alert on ``scan_fallback``
    (an index was expected and was not used). ``degraded`` is intentionally
    left untouched: a scan still returns SEMANTIC rows, and #2952 defines
    ``degraded`` as "contributed no semantic results".

    vector_index_api: 'procedure' | 'cypher' | None — the API that
    succeeded at index-creation time, recorded on the projection as
    FalkorProjection._vector_index_api (#1359). When 'cypher', the
    signature-B form is attempted FIRST (an engine that created the index
    via `CREATE VECTOR INDEX` rejects signature A — skipping it saves a
    failed round trip per query). 'procedure' or None keeps the historical
    behavior: signature A first, retry B on signature failure. Brute-force
    remains the fallback in all cases.

    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
    'source', or 'operator'. The vector index is queried against the label
    matching the entity_type (Event/Subject/Document/Object/...), and results
    return the entity's id field: url for source and document (the :Source
    canonical key, #448), eventId for event, id for all other entity types.
    Operators are Points with is_operator=true — they query the Point label.
    (#172)

    scope_kinds (#4199): the KIND values the READ's own post-retrieval kind
    filter selects (``sdk.recall_state``'s ``expanded_kinds``). When given
    and a ``leg_trace`` is being recorded, the leg's material is judged
    against THAT scope rather than the whole label: a scope that HAS nodes
    but NONE of them embedded means this read's dense leg can contribute
    nothing: the record is degraded, with whatever reason the read's own path
    assigns it (the #2952 vocabulary — no term is minted). The zero-row
    guard's count is likewise taken over the scope, so
    a corpus whose only embeddings are out of scope no longer reports
    ``empty_results`` ("there are embeddings, just no near neighbour").
    Retrieval itself is UNCHANGED: the rows the unscoped query returns are
    still returned (the read's kind filter owns dropping them), and a scope
    with no embedded nodes is still searched. Default None = pre-#4199
    behavior, byte-identical. A scope that selects NO nodes is recorded
    NEUTRAL on the trace entry (:data:`VECTOR_SCOPE_KEY` →
    :data:`VECTOR_SCOPE_EMPTY`): its ``ok``/degraded record stands, but
    :func:`declared_degraded_read` skips it so it can neither prove the read
    hybrid nor declare it degraded (the empty-scope rule). ONE EXCEPTION:
    a FAILURE (:func:`_record` called with ``failure=True`` -
    ``index_missing``, a raised ``no_embeddings``, ``query_failed``) is
    never tagged neutral and keeps its own reason, because an empty scope
    must not launder a failed leg into "no declaration at all" (#4199
    review P2).

    Note: the connection-level `timeout` is passed straight to the FalkorDB
    driver (Graph.query(timeout=...)) so a slow query is killed server-side.
    The post-hoc check remains as a safety net for drivers that ignore the
    timeout, and the per-strategy circuit breaker short-circuits after
    consecutive slow/failed queries. (#249)
    """
    # #5404: entity_type is caller-controlled, and this leg interpolates its
    # capitalized form into a Cypher LABEL — the label is query STRUCTURE.
    # Guard at the ENTRY: the empty-query_vec return and the breaker
    # short-circuit below both precede the label derivation, so a guard placed
    # at the label would be skipped by either.
    validate_entity_type(entity_type)

    #: #4199 — the read's OWN scope carries no dense material. Resolved once
    #: below (before the query) and applied to every HEALTHY outcome record.
    _scope_hollow = False
    #: #4199 — the scope probe selected NO nodes. Such an entry is NEUTRAL in
    #: the declaration (neither healthy nor degraded): an arm measuring a
    #: different, empty scope must not launder another arm's degradation.
    _scope_empty = False
    _scope_embedded: int | None = None

    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int, mechanism: str | None = None,
                failure: bool = False) -> None:
        # #4489: noted with the RAW ran/degraded the leg reports, BEFORE the
        # opt-in trace's ``_scope_hollow`` re-labelling below — the ledger is a
        # fact about the leg execution, not a derived declaration.
        _retrieval_metering.note_leg("vector", ran=ran, degraded=degraded)
        if leg_trace is None:
            return
        if _scope_hollow and ran and not degraded:
            # #4199: the leg ran, but the READ's scope holds no dense
            # material — this record must not present the read as hybrid.
            if reason == BELOW_RELEVANCE_FLOOR:
                # #4028 keeps its own reason: the search surface reads it to
                # distinguish "no relevant neighbour" from a leg FAILURE and
                # must not answer from the TF-IDF fallback. Flip only the
                # verdict — the read is still declared degraded.
                degraded = True
            else:
                degraded, reason, count = True, "no_embeddings", 0
        entry = _trace_entry("vector", ran=ran, degraded=degraded,
                             reason=reason, count=count,
                             mechanism=mechanism)
        if _scope_empty and not failure:
            # #4199: mark the entry NEUTRAL — the empty-scope rule keeps its
            # ``ok``/degraded record, but the declaration skips it (see
            # :func:`declared_degraded_read`).
            # #4199 review P2: a FAILURE is never neutral. An empty scope
            # cannot launder ``query_failed`` / ``index_missing`` into "no
            # scope, nothing to say" — that is the same laundering the
            # empty-scope rule exists to stop, keyed on scope instead of arm.
            entry[VECTOR_SCOPE_KEY] = VECTOR_SCOPE_EMPTY
        leg_trace.append(entry)

    if not query_vec:
        # #5412: this is an EXIT BRANCH, so it records one. Nothing ran and
        # nothing failed — the caller asked the dense leg nothing — and the
        # entry says exactly that (``ran=False``/``degraded=False``) instead of
        # leaving a gap the declaration had to read as ``leg_absent``.
        _record(ran=False, degraded=False, reason=NO_QUERY_VECTOR, count=0)
        return []
    # #4028 — the retrieval relevance floor. Expressed in COSINE terms
    # (embeddings.VECTOR_RELEVANCE_FLOOR); each return branch below converts
    # it into that branch's own score unit. A nearest neighbour below the
    # floor is not evidence of relevance (short generic strings sit near the
    # embedding centroid and score ~0.5 against anything), so it is dropped
    # rather than fused at rank-1 weight. Default None = no floor, i.e.
    # byte-identical to pre-#4028 for every caller that does not pass one.
    _floor_distance: float | None = None
    if min_similarity is not None:
        # L2-normalized embeddings: cos = 1 - d^2/2  =>  d = sqrt(2(1-cos)).
        _floor_distance = (2.0 * (1.0 - min_similarity)) ** 0.5
    if not _breaker_allow("vector"):
        logger.warning("Vector circuit breaker OPEN — skipping vector strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
        return []

    # Operators are Points with is_operator=true — match the Point label
    # (consistent with run_fts_query / run_structural_query). (#172)
    # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source — the vector leg
    # reads the Source label, never a Document label. The caller-facing
    # entity_type stays "document".
    # #4997: the entity_type -> label mapping is ONE declaration
    # (tortoise/security.py::ENTITY_TYPE_LABELS, read via entity_label)
    # rather than a derivation repeated per query leg. Behaviour is
    # unchanged: entity_label reproduces the expression it replaces for
    # every valid entity_type, for an unknown str ("<Capitalized>") and for
    # a non-str (AttributeError), so this is a provenance change, not a
    # semantic one. #5407 migrates the FTS/structural legs onto it.
    label = entity_label(entity_type)
    # #448: three-way id_field — source→url (canonical key, #149),
    # event→eventId, else→id. D10: a document Source resolves by url too.
    if entity_type in ("source", "document"):
        id_field = "url"
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"

    # ── #4199: measure the READ's own dense scope ────────────────────────
    # One count over the kind predicate the read's kind filter applies, run
    # ONLY when a trace is being recorded (a default caller sees no extra
    # query and byte-identical behavior). ``total`` and ``embedded`` are read
    # together so a scope with NO nodes is not mistaken for an un-embedded
    # one. The probe applies the SAME kind + per-label status predicate the
    # retrieval legs apply (``_status_vocab_for``), so the material it counts
    # is exactly the material the leg may return — #5287: without the status
    # half, a terminal-but-embedded in-scope Object was counted by the probe
    # and then excluded by the leg. An UNMEASURABLE scope fails CLOSED: a read
    # path that cannot prove its dense material must not report itself healthy
    # (retrieval is unaffected either way — this only decides the declaration).
    if leg_trace is not None and scope_kinds:
        try:
            _scope_field = _KIND_FIELD_BY_ENTITY.get(entity_type, "pointKind")
            _scope_where = [f"n.{_scope_field} IN $scope_kinds"]
            # #5287: use the SAME per-label status predicate the retrieval
            # legs use (:1213 index, :1375 scan) — a terminal Object is
            # excluded by them, so the probe must exclude it too or an
            # in-scope-but-terminal embedding is counted as material the leg
            # then refuses to return (``hybrid`` declared over a keyword-only
            # read — the #4199 class via the status axis). ``_status_vocab_for``
            # is the ONE family→vocabulary dispatcher; calling it here (never
            # re-stating the mapping) is what keeps probe and leg from drifting.
            if label in _STATUS_FILTERED_LABELS and excluded_statuses != ():
                _vocab, _flag = _status_vocab_for(label)
                _scope_where.append(_exclude_status_clause(
                    "n", excluded_statuses or _vocab,
                    include_outdated_flag=_flag))
            if entity_type == "operator":
                _scope_where.append("n.is_operator = true")
            _scope_rows = graph.query(
                f"MATCH (n:{label}) WHERE {' AND '.join(_scope_where)} "
                "RETURN count(*), count(n.embedding)",
                params={"scope_kinds": list(scope_kinds)},
                timeout=timeout_ms,
            ).result_set
            if _scope_rows:
                _scope_total = int(_scope_rows[0][0] or 0)
                _scope_embedded = int(_scope_rows[0][1] or 0)
                _scope_hollow = _scope_total > 0 and _scope_embedded == 0
                _scope_empty = _scope_total == 0
            else:
                # #5287: an aggregate ``RETURN count(*), count(...)`` always
                # yields one row, so an EMPTY result set is a probe that
                # answered nothing — not a measured scope. Treat it exactly
                # like the raised-probe path below: UNMEASURABLE, fail CLOSED.
                # Pre-fix it fell through to the unscoped whole-label count
                # (line ~1427) and could record ``empty_results``/healthy,
                # contradicting the comment above.
                _scope_hollow = True
        except Exception as e:  # noqa: BLE001, RUF100 — fail CLOSED
            logger.debug("dense scope probe failed (%s) — declaring the "
                         "scope un-embedded", e)
            _scope_hollow = True

    # #4999: did THIS call attempt an index query? Embedded mode never does
    # (the scan is the design, not a fallback); docker mode always does, and a
    # failure falls through to the scan below. That distinction is what makes
    # ``scan`` and ``scan_fallback`` tellable apart.
    index_attempted = False

    # Docker/server mode → try index-accelerated vector search (#7777)
    if not is_embedded:
        index_attempted = True
        # #689: retracted Points must not leak into vector results.
        # D10 (#5026): the Source label holds both the document node
        # (documentKind non-NULL) and the corpus/provenance Source
        # (documentKind NULL); the predicate lands post-YIELD, ahead of the
        # outer LIMIT, so a document query never fuses a non-document Source
        # (and vice versa). Same axis on all three legs.
        if label in _STATUS_FILTERED_LABELS:
            _vocab, _flag = _status_vocab_for(label)
            vec_status_filter = ("" if excluded_statuses == ()
                                 else f"WHERE {_exclude_status_clause('node', excluded_statuses or _vocab, include_outdated_flag=_flag)} ")
        elif entity_type == "document":
            vec_status_filter = "WHERE node.documentKind IS NOT NULL "
        elif entity_type == "source":
            vec_status_filter = "WHERE node.documentKind IS NULL "
        else:
            vec_status_filter = ""
        try:
            start = time.monotonic()

            def _sig_cypher(sig: str) -> str:
                if sig == "B":
                    # Docs form: k first, vecf32-wrapped query — verified on
                    # falkordblite 0.10.0's bundled module ("Invalid arguments
                    # for procedure" on A, works on B).
                    return (
                        f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $limit, vecf32($query_vec)) "
                        "YIELD node, score "
                        + vec_status_filter +
                        f"RETURN node.{id_field}, score, "
                        f"{recency_key_expr('node')} AS recency "
                        "LIMIT $limit"
                    )
                # Signature A (RediSearch-style): repo-pinned docker image
                # falkordb/falkordb-server:v4.16.7.
                #
                # #6214: A yields NO score column, and the pre-#6214 code
                # substituted the row POSITION for one — so ANY re-sort was
                # unsafe for it (it would have reversed the ranking, not
                # merely made it deterministic). Obtain the distance from the
                # yielded node instead, so BOTH signatures hand the ONE
                # ordering pass below a comparable value: a cosine distance,
                # the same quantity signature B's engine score carries. If
                # the engine lacks vec.cosineDistance the query raises and the
                # outer handler degrades to the brute-force scan as before.
                return (
                    f"CALL db.idx.vector.queryNodes('{label}', 'embedding', $query_vec, $limit) "
                    "YIELD node "
                    + vec_status_filter +
                    f"RETURN node.{id_field}, "
                    "vec.cosineDistance(node.embedding, vecf32($query_vec)) AS score, "
                    f"{recency_key_expr('node')} AS recency "
                    "LIMIT $limit"
                )

            def _query_nodes(sig: str):
                return graph.query(
                    _sig_cypher(sig),
                    params={"query_vec": query_vec, "limit": limit},
                    timeout=timeout_ms,
                ).result_set

            def _signature_failure(msg: str) -> bool:
                return (
                    "invalid arguments" in msg
                    or "not registered" in msg
                    or "type mismatch" in msg
                    or "unknown procedure" in msg
                )

            # #1359: `queryNodes` argument order varies by engine version.
            # The projection records which API succeeded at index-creation
            # time (FalkorProjection._vector_index_api, threaded in by the
            # caller): 'cypher' engines go straight to signature B, skipping
            # the guaranteed-failing sig-A attempt (one failed round trip
            # saved per query); 'procedure' engines and unrecorded (None)
            # keep sig A first. A signature failure on the preferred form
            # retries the other; anything else falls through to brute-force.
            preferred = "B" if vector_index_api == "cypher" else "A"
            fallback = "A" if preferred == "B" else "B"
            try:
                rows = _query_nodes(preferred)
            except Exception as e:
                if not _signature_failure(str(e).lower()):
                    raise  # non-signature failure → brute-force below
                rows = _query_nodes(fallback)
            elapsed = (time.monotonic() - start) * 1000
            if elapsed > timeout_ms:
                # #561: latency warning only — keep the rows.
                logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
            _breaker_record("vector", True)
            # #6214: EQUAL-DISTANCE ROWS MUST COME BACK IN ONE ORDER. This
            # index leg feeds rank-based RRF fusion, so a tie-order flip
            # changes the fused ranking. Both signatures now RETURN a cosine
            # DISTANCE — B yields the engine's own score, A computes
            # vec.cosineDistance over the yielded node (the pre-#6214 A
            # substituted the row POSITION for a score, so ANY re-sort would
            # have reversed its ranking) — and the engine's tie order is
            # replaced here, ONCE, by (distance ASC, then the shared
            # recency/id key DESC — #3019, #10121). There is deliberately no
            # second ordering predicate (no per-signature Cypher ORDER BY):
            # one Python sort, over the one shape both signatures now produce,
            # so the two cannot drift apart.
            #
            # ⛔ RESIDUAL (`#10121` review): this replaces the tie order **of
            # the rows the engine chose**, not the CHOICE. `queryNodes` picks
            # its `$limit` rows by distance with an arbitrary order WITHIN a
            # distance tie, and the sort below can only reorder what it was
            # handed — so on an INDEXED store whose tie group is larger than
            # the caller's `limit`, a member of that group (in principle the
            # NEWEST) can sit outside the window and no sort here can recover
            # it. That is NOT the `#10121` defect: it needs an exact distance
            # tie across more than `limit` rows on an indexed store, where
            # `#10121` is the lexical scorer's constant score across the whole
            # corpus. It is stated rather than over-fetched because a bigger k
            # only shrinks the window (any tie group above it still
            # truncates) and would change this leg's cost and its
            # `min_similarity` semantics for a case no pin covers yet. A
            # remedy belongs here as its own change — over-fetch or an ordered
            # scan — with a docker-lane pin that ties MORE rows than the
            # caller's `limit`; `tests/test_search_engine_gaps.py::
            # TestRunVectorQuery`'s `#6214` case ties THREE rows at `limit=10`
            # and cannot see it.
            #
            # #5583: the engine's value is a DISTANCE (lower is better), NOT a
            # similarity. `db.idx.vector.queryNodes` returns `1 - cosine` for a
            # `similarityFunction: 'cosine'` index: a PERFECT match comes back
            # as 0.0 and an orthogonal one as 1.0. Passing that through as a
            # similarity inverted this leg exactly — the WORST row scored
            # highest, and every `min_similarity` floor discarded the rows it
            # exists to keep. Measured on the docker lane (falkordb-server,
            # module ver 42004): a query identical to the stored vector scored
            # 0.0, an orthogonal one scored 1.0.
            #
            # The conversion mirrors this function's own scan fallback below
            # (`1.0 / (1.0 + distance)`): both derive a similarity from a
            # distance, so they agree on polarity. Cosine distance lies in
            # [0, 2], so `1 - d` lies in [-1, 1] and the [0, 1] clamp still
            # maps a perfect match to 1.0.
            # #10121: the equal-distance tie is the SAME recency-then-id rule the
            # other legs use. Before this, the index branch sorted
            # ``(distance, id ASC)`` while the scan branch used the recency tie,
            # so on an indexed store the dense and sparse legs handed DIFFERENT
            # twins rank 0 — and RRF is rank-based, so the fused order depended
            # on whether an HNSW index happened to exist. The recency column
            # rides the cypher RETURN above; a row shape without it (an older
            # signature, a degraded path) falls back to ``''``, which sorts as
            # OLDEST exactly as the shared clause does.
            annotated: list[tuple[float | None, tuple[str, str], object]] = []
            for row in rows:
                try:
                    distance = float(row[1])
                except (IndexError, TypeError, ValueError):
                    distance = None
                recency = row[2] if len(row) > 2 else None
                annotated.append((distance, recency_tie_key(recency, row[0]),
                                  row[0]))
            # (distance ASC, then the SHARED recency/id key DESCENDING). An
            # unreadable distance carries no evidence of similarity — rank it
            # LAST; `inf` is the sort key, not the reported score. Two stable
            # passes: the secondary key first, then the primary one.
            annotated.sort(key=lambda item: item[1], reverse=True)
            annotated.sort(key=lambda item: (
                item[0] if item[0] is not None else float("inf")))
            out = [
                (pid, 0.0 if dist is None else max(0.0, min(1.0, 1.0 - dist)))
                for dist, _key, pid in annotated
            ]
            if min_similarity is not None and out:
                # Score is a true cosine similarity now, for BOTH signatures —
                # filter directly. Only claim the FLOOR when there was
                # something to filter: a zero-row index result is
                # `empty_results`, not a relevance verdict (claiming the floor
                # there would suppress the caller's legitimate degraded
                # fallback, #4028 review P1).
                kept = [(pid, s) for pid, s in out if s >= min_similarity]
                if not kept:
                    _record(ran=True, degraded=False,
                            reason=BELOW_RELEVANCE_FLOOR, count=0,
                            mechanism=MECHANISM_INDEX)
                    return []
                out = kept
            _record(ran=True, degraded=False, reason="ok", count=len(out),
                    mechanism=MECHANISM_INDEX)
            return out
        except Exception as e:
            msg = str(e).lower()
            if "index" in msg or "not found" in msg or "does not exist" in msg:
                logger.info("Vector index not available — falling back to brute-force")
            else:
                # #1359: log the fallback at debug (not warning) when the
                # index-accelerated path merely isn't supported — brute-force
                # is the documented degradation, not an error.
                logger.debug("Vector index query failed, falling back to brute-force: %s", e)
            # Fall through to brute-force below — the OUTCOME is recorded by
            # whichever path finishes (success or the brute-force except), so
            # one logical query counts at most once. (#249 review P1-2)

    # Brute-force (embedded mode or index query failed)
    # #4999: `scan_fallback` ONLY when an index attempt actually failed on
    # this call — an embedded scan is the design and reports plain `scan`.
    scan_mechanism = (MECHANISM_SCAN_FALLBACK if index_attempted
                      else MECHANISM_SCAN)
    try:
        start = time.monotonic()
        # #244: vec.euclideanDistance rejects plain-list query params
        # ("expected Null or Vectorf32 but was List") in embedded FalkorDBLite
        # and whenever the HNSW index is unavailable — wrap the param in
        # vecf32() so vector search actually runs. Stored embeddings must be
        # readable too (one unreadable node poisons the whole MATCH) — the
        # STORED side is normalised by the shared read seam rather than assumed
        # to be typed, because a caller-supplied embedding is stored VERBATIM
        # as a plain `List` by a recorded decision (PR #3018) — see
        # `stored_embedding_vector_expr` (#8316).
        # #689: retracted Points must not leak into vector results.
        # D10 (#5026): discriminate the document Source from the
        # corpus/provenance Source in the MATCH's WHERE — the brute-force
        # retrieval layer, ahead of the ORDER BY/LIMIT. Same axis as the FTS
        # and structural legs (documentKind IS NOT NULL ⟺ document).
        if label in _STATUS_FILTERED_LABELS:
            _vocab, _flag = _status_vocab_for(label)
            bf_status_clause = ("" if excluded_statuses == ()
                                else f" AND {_exclude_status_clause('n', excluded_statuses or _vocab, include_outdated_flag=_flag)}")
        elif entity_type == "document":
            bf_status_clause = " AND n.documentKind IS NOT NULL"
        elif entity_type == "source":
            bf_status_clause = " AND n.documentKind IS NULL"
        else:
            bf_status_clause = ""
        cypher = (
            f"MATCH (n:{label}) "
            "WHERE n.embedding IS NOT NULL" + bf_status_clause +
            " WITH vecf32($query_vec) AS _qv, n "
            f"WITH n, vec.euclideanDistance({stored_embedding_vector_expr('n')}, _qv) AS distance "
            "WHERE distance IS NOT NULL "
            f"RETURN n.{id_field}, 1.0 / (1.0 + distance) AS score "
            # #3019: a distance tie (equal values, or repeated rows) must not fall
            # through to DB row order. #10121: shared recency tie.
            f"ORDER BY score DESC, {recency_tie_clause('n', id_field)} "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"query_vec": query_vec, "limit": limit}, timeout=timeout_ms
        ).result_set
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            # #561: latency warning only — return the results.
            logger.warning("Vector query exceeded timeout: %.0fms > %dms", elapsed, timeout_ms)
        _breaker_record("vector", True)
        if rows:
            out = [(row[0], float(row[1])) for row in rows]
            if _floor_distance is not None:
                # Brute-force score is 1/(1+euclidean distance); invert it
                # exactly to the distance so the cosine floor compares on the
                # same quantity the index scored.
                out = [(pid, s) for pid, s in out
                       if s > 0 and (1.0 / s - 1.0) <= _floor_distance]
                if not out:
                    _record(ran=True, degraded=False,
                            reason=BELOW_RELEVANCE_FLOOR, count=0,
                            mechanism=scan_mechanism)
                    return out
            _record(ran=True, degraded=False, reason="ok", count=len(out),
                    mechanism=scan_mechanism)
            return out
        # R3 (#1542) D4: the explicit zero-row guard — an all-no-embedding
        # graph returns [] WITHOUT raising (the except catch below never
        # fires for the real empty case). Cheap count(p.embedding) guard
        # distinguishes no_embeddings (zero embedded points) from
        # empty_results (embedded points present, no matches).
        # #4199: when the read's SCOPE was measured above, that count IS the
        # guard — a corpus embedded only outside the requested kind must not
        # report empty_results ("there are embeddings, no near neighbour").
        if _scope_embedded is not None:
            embedded = _scope_embedded
        else:
            try:
                cnt_rows = graph.query(
                    f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL "
                    "RETURN count(*)", timeout=timeout_ms).result_set
                embedded = cnt_rows[0][0] if cnt_rows else 0
            except Exception:  # noqa: BLE001, RUF100
                embedded = 0
        _record(ran=True, degraded=(embedded == 0),
                reason=("no_embeddings" if embedded == 0 else "empty_results"),
                count=0, mechanism=scan_mechanism)
        return []
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Vector index not available — skipping vector strategy")
            _breaker_record("vector", True)
            _record(ran=True, degraded=True, reason="index_missing", count=0,
                    mechanism=scan_mechanism, failure=True)
        elif "embedding" in msg and "null" in msg:
            logger.info("No Points with embeddings — skipping vector strategy")
            _breaker_record("vector", True)
            _record(ran=True, degraded=True, reason="no_embeddings", count=0,
                    mechanism=scan_mechanism, failure=True)
        else:
            logger.warning("Vector query failed: %s", e)
            _breaker_record("vector", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0,
                    mechanism=scan_mechanism, failure=True)
        return []


def run_structural_query(
    graph, kind: str | None,
    entity_type: str = "point", limit: int = 20, timeout_ms: int = 500,
    excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
    context: str | None = None,
) -> list[tuple[str, float]]:
    """Run structural/kind query via range indexes.

    entity_type: 'point' (filters on pointKind), 'event' (eventKind),
                 'subject' (subjectKind), 'operator' (op_type, Point nodes with is_operator=true),
                 'source' (sourceKind), 'document' (documentKind), 'object' (objectKind).
    Returns matching entities with a score of 1.0 (exact match) or 0.5 (partial match).

    R3 (#1542) D4: ``leg_trace`` — when provided, appends a per-leg entry
    at every exit branch (ok / empty_results / index_missing / query_failed /
    breaker_open). Default None = no trace.

    #249: the driver-level timeout is passed through so a wedged DB cannot
    hang the structural strategy (the third leg of the degradation chain);
    the per-strategy breaker short-circuits after consecutive failures.
    """
    # #5404: entity_type is caller-controlled, and this leg interpolates its
    # capitalized form into a Cypher LABEL — the label is query STRUCTURE.
    # Guard at the ENTRY so the rejection does not depend on which branch runs.
    validate_entity_type(entity_type)

    def _record(*, ran: bool, degraded: bool, reason: str | None,
                count: int) -> None:
        # #4489: unconditional ledger note (see the fts leg's comment).
        _retrieval_metering.note_leg("structural", ran=ran,
                                     degraded=degraded)
        if leg_trace is not None:
            leg_trace.append(_trace_entry("structural", ran=ran,
                                          degraded=degraded,
                                          reason=reason, count=count))

    if entity_type == "operator":
        # Operators are Points with is_operator=true, kind=op_type
        label_str = "Point"
        kind_field = "op_type"
    elif entity_type == "source":
        label_str = "Source"
        kind_field = "sourceKind"
    elif entity_type == "document":
        # D10 (ONTOLOGY v3.15 §4.4): a document is a :Source. The structural
        # leg reads the Source label with the documentKind genre filter.
        label_str = "Source"
        kind_field = "documentKind"
    elif entity_type == "object":
        label_str = "Object"
        kind_field = "objectKind"
    else:
        label_str = entity_type.capitalize()
        kind_field = {"point": "pointKind", "event": "eventKind", "subject": "subjectKind"}[entity_type]
    if entity_type in ("source", "document"):
        # #149: a :Source's canonical key is url, not id. #3019: `document`
        # belongs in this branch for the same D10 reason the label block above
        # gives — a document IS a :Source (`_searchText` is indexed on the
        # Source label), so the ordering key is the :Source key. This leg was
        # the inconsistent one: the FTS leg's id_field block and the vector
        # leg's already map document->url.
        #
        # The id state on that path, read from the code rather than assumed:
        # `_mint_source_stub` (projection/edges.py — the stub every provenance
        # link mints) sets url/sourceKind/contentHash/ingestedAt and never sets
        # `id`; `_upsert_source` sets `s.id` only in its ON CREATE clause; and a
        # document write that adopts the stub REPAIRS the id (`_upsert_document`
        # runs `SET s.id=coalesce(s.id, $id)`, as does the hosted session
        # commit). So `id` is NULL for the window between minting and adoption,
        # and ordering on it there resolves NOTHING: the tie fell back to DB row
        # order — the defect this PR exists to close — and every returned pid
        # was None. url is the key that is correct in BOTH states.
        #
        # `object` is a NARROWED residual (#10121): its canonical key is `name`
        # and the live write path mints it id-less, so the `else` below resolves
        # `id` and those rows carry no identity — but the ORDER BY now routes
        # every key through `recency_tie_clause`, whose `coalesce(..., '')`
        # sends the id-less rows to the TAIL of the window instead of its head
        # (a bare DESC sorted the NULL as the LARGEST value). Their order INSIDE
        # that tail is still engine row order. See the same note where the key is
        # first resolved (the FTS leg's id_field block).
        id_field = "url"
    elif entity_type == "event":
        id_field = "eventId"
    else:
        id_field = "id"
    if not _breaker_allow("structural"):
        logger.warning("Structural circuit breaker OPEN — skipping structural strategy")
        _record(ran=False, degraded=True, reason="breaker_open", count=0)
        return []
    try:
        conditions = []
        params = {}
        if entity_type == "operator":
            conditions.append("n.is_operator = true")
        if kind:
            conditions.append(f"n.{kind_field} = $kind")
            params["kind"] = kind

        if not conditions:
            # No filters — caller should use full-scan path instead. Normal
            # completion: record success so a HALF_OPEN probe never latches.
            _breaker_record("structural", True)
            _record(ran=True, degraded=False, reason="empty_results", count=0)
            return []

        # D10 (#5026): entity_type="source" is the PROVENANCE Source — the
        # same partition as sdk.list_sources() (documentKind IS NULL). A D10
        # document node is a :Source too, so sourceKind alone does not
        # separate them; the discriminator must be part of the retrieval
        # WHERE. Placed AFTER the no-filters gate (exactly like the status
        # clause below) so a kind-less structural call keeps main's
        # early-return — this clause must never be the sole condition that
        # fires a full-label scan. The document arm needs no equivalent: its
        # kind_field IS documentKind, so `n.documentKind = $kind` already
        # implies non-NULL. All three legs now mean one thing.
        if entity_type == "source":
            conditions.append("n.documentKind IS NULL")
        # #1391: terminal-status exclusion (after the no-filters gate so a
        # kind-less broad scan keeps main's early-return behavior — the
        # status clause must not become the sole condition that fires the
        # scan). include_terminal opts out for audit/history queries.
        if excluded_statuses != () and label_str in _STATUS_FILTERED_LABELS:
            _vocab, _flag = _status_vocab_for(label_str)
            conditions.append(_exclude_status_clause(
                "n", excluded_statuses or _vocab,
                include_outdated_flag=_flag))
        where_clause = " AND ".join(conditions)
        cypher = (
            f"MATCH (n:{label_str}) "
            f"WHERE {where_clause} "
            f"RETURN n.{id_field} "
            # #3019: this leg scores every row a CONSTANT (1.0 / 0.5, below), so
            # with no secondary key the WHOLE leg is an unordered tie and the
            # caller's rank — hence the fused top-k — is DB row order.
            # #10121: shared recency tie — on a constant-scored leg the tie does
            # all the work, so "newest first" IS the whole ranking wherever the
            # rows carry a stamp. Not every family does (see
            # recency_tie_clause's per-writer table): a session/connector
            # `:Event` carries none and its `eventId` is not a clock, so there
            # the whole ranking is that id direction.
            f"ORDER BY {recency_tie_clause('n', id_field)} "
            f"LIMIT $limit"
        )
        params["limit"] = limit
        rows = graph.query(cypher, params=params, timeout=timeout_ms).result_set

        # Score: 1.0 if kind matched, 0.5 for no-kind broad scan
        results = []
        for row in rows:
            pid = row[0]
            match_score = 1.0 if kind else 0.5
            results.append((pid, match_score))
        _breaker_record("structural", True)
        _record(ran=True, degraded=False,
                reason=("ok" if results else "empty_results"), count=len(results))
        return results
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Structural index not available — skipping structural strategy")
            _breaker_record("structural", True)
            _record(ran=True, degraded=True, reason="index_missing", count=0)
        else:
            logger.warning("Structural query failed: %s", e)
            _breaker_record("structural", False)
            _record(ran=True, degraded=True, reason="query_failed", count=0)
        return []


def expand_structural_hops(
    graph, seed_ids: list[str],
    *,
    max_hops: int = 2,
    limit: int = 40,
    excluded_statuses: tuple | None = None,
    timeout_ms: int = 500,
) -> list[tuple[str, float]]:
    """Expand seed point ids over IMPL/NAND edges (1-2 hops) — the
    graph-as-recall-amplifier pass (R4; graphiti episode-mentions pattern).

    A text hit (FTS/vector) seeds traversal of its epistemic neighborhood:
    hop-1 neighbors score 1.0, hop-2 neighbors 0.5 (the partial-match score
    convention from run_structural_query). The eval materializes IMPL/NAND
    edges operator-mediated (seed-[:IMPL]->op{is_operator:true}-[:IMPL]->peer,
    so seed→op→peer is exactly 2 hops); legacy direct Point→Point edges are
    1 hop. The variable-length pattern covers both.

    Shares the "structural" circuit breaker with run_structural_query (same
    leg). Terminal-status peers are excluded by default (#1391 posture);
    pass excluded_statuses=() to include them. Operators are never returned
    (the traversal reaches them but the WHERE clause excludes them).
    """
    if not seed_ids:
        return []
    if not _breaker_allow("structural"):
        logger.warning("Structural circuit breaker OPEN — skipping hop expansion")
        return []
    try:
        start = time.monotonic()
        cypher = (
            "MATCH (seed:Point) WHERE seed.id IN $seeds "
            # #6976: load-bearing `WITH seed` — without it FalkorDB 6.0.0 drops
            # the id predicate at the re-binding MATCH below (foreign rows).
            "WITH seed "
            f"MATCH path = (seed)-[:IMPL|NAND*1..{max_hops}]-(n:Point) "
            "WHERE n.id <> seed.id AND n.is_operator <> true"
            + ("" if excluded_statuses == ()
               else f" AND {_exclude_status_clause('n', excluded_statuses or TERMINAL_EXCLUDED_STATUSES)}")
            + " WITH n, min(length(path)) AS hops "
            "RETURN n.id AS id, hops "
            # #3019: `hops ASC` alone leaves equal-hop candidates in engine row
            # order, so with more candidates than `limit` both MEMBERSHIP and
            # rank are DB-order dependent. #10121: hops stays primary (a real
            # ranking signal), recency breaks the equal-hop tie.
            f"ORDER BY hops ASC, {recency_tie_clause('n', 'id')} "
            "LIMIT $limit"
        )
        rows = graph.query(
            cypher, params={"seeds": list(seed_ids), "limit": limit},
            timeout=timeout_ms,
        ).result_set
        elapsed = (time.monotonic() - start) * 1000
        if elapsed > timeout_ms:
            # same post-hoc latency warning posture as run_fts_query (#249)
            logger.warning(
                "Structural hop expansion exceeded timeout: %.0fms > %dms",
                elapsed, timeout_ms)
        results = [(row[0], 1.0 if int(row[1]) == 1 else 0.5) for row in rows]
        _breaker_record("structural", True)
        return results
    except Exception as e:
        msg = str(e).lower()
        if "index" in msg or "not found" in msg or "does not exist" in msg:
            logger.info("Structural hop expansion not available — skipping structural expansion")
            _breaker_record("structural", True)
        else:
            logger.warning("Structural hop expansion failed: %s", e)
            _breaker_record("structural", False)
        return []


# ── RRF fusion ───────────────────────────────────────────────────────────────

def rrf_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = 60,
    *,
    strategy_names: list[str] | None = None,
    weights: dict[str, float] | None = None,
    recency_weights: dict[str, float] | None = None,
    recency_boost: float = 0.0,
) -> dict[str, float]:
    """Reciprocal Rank Fusion — combine multiple ranked lists.

    Formula: RRF(d) = Σ w_i / (k + rank_i(d))

    Args:
        ranked_lists: List of strategies, each is [(id, score), ...] ranked desc
        k: RRF constant (default 60 from Cormack et al. 2009)
        strategy_names (#1657): the per-list strategy label (fts/vector/
            structural/tfidf) — required to apply ``weights``; optional.
        weights (#1657): per-strategy multiplier w_i for the fusion term
            (weighted RRF — the fix for the measured fusion dilution where
            equal-weight RRF costs ~2 nDCG pts when a strong vector leg is
            diluted by weak FTS/structural legs). Optional; default None =
            equal weights (byte-identical to pre-#1657). When weights is
            None the arithmetic is untouched for all pre-#1657 callers.
        recency_weights (R5 #1544): {id: factor ∈ [0,1]} — rank-based
            recency percentile from ``_recency_factors`` (newest → 1.0,
            oldest → 0.0, undated → 0.0). Optional; default None = off.
        recency_boost (R5 #1544): multiplier strength. Final score per doc:
            RRF(d) × (1 + recency_boost × weight(d)). Default 0.0 = off.
            When weights are None or boost is 0.0 the arithmetic is
            untouched → byte-identical output for all pre-R5 callers.

    Returns:
        {id: combined_rrf_score} sorted by score descending
    """
    scores: dict[str, float] = {}
    for i, ranked in enumerate(ranked_lists):
        # #1657 weighted RRF: w_i scales the list's fusion contribution.
        # Default 1.0 keeps pre-#1657 callers byte-identical. When
        # strategy_names is provided, the weight is looked up by label
        # (weights defaults to None → all 1.0).
        w = 1.0
        if strategy_names is not None and weights:
            # PRECONDITION: `strategy_names` must be at least as long as
            # `ranked_lists` (both call sites build it alongside them). The
            # lookup below indexes `strategy_names[i]`, so a SHORTER list raises
            # IndexError here — a dead `... else i` fallback used to sit on the
            # warning line below, implying a tolerance that never existed
            # (#3019 review). Deliberately left failing loudly rather than
            # defaulting to 1.0: a misaligned list would silently misweight a leg.
            w = weights.get(strategy_names[i], 1.0)
            # #3019 part 2: a NaN weight makes every fused score from that leg
            # NaN, and tuple comparison against NaN is False in BOTH directions,
            # so the ``(-score, id)`` key below silently degrades to insertion
            # order for those candidates — losing the determinism #2952
            # established. An INFINITE weight does not damage the ORDER
            # (``inf == inf`` is True) but it does destroy the SCORE signal: one
            # leg's infinite scores swamp every other leg. The guard below is
            # therefore ``not isfinite``, deliberately collapsing BOTH NaN and
            # ±inf to 1.0 — and it warns, rather than substituting silently. A
            # candidate carried only by
            # another leg keeps a finite score, so the SCORE damage is per-leg —
            # but the ORDER damage is global: NaN compares False in BOTH
            # directions against everything, so the comparator is inconsistent
            # and the resulting order is arbitrary, not merely the NaN rows.
            # ``json.loads`` accepts bare ``NaN``/``Infinity``, so
            # TORTOISE_FUSION_WEIGHTS can carry one, and a kwarg caller can pass
            # one. Guarded at the ROOT so every entry point is covered, not just
            # the env parse.
            if not math.isfinite(w):
                # #3019: `json.loads` accepts bare NaN/Infinity, and a NaN
                # weight makes every score from that leg NaN — the
                # `(-score, id)` tie-break then compares False both ways and
                # degrades to insertion order. Warn rather than substitute
                # silently: the recorded default is not equal weighting
                # (PRODUCTION DEFAULT, tortoise/sdk.py).
                logger.warning(
                    "non-finite RRF weight for %r (%r) — using 1.0",
                    strategy_names[i], w,
                )
                w = 1.0
        for rank, (pid, _score) in enumerate(ranked):
            rrf_score = w / (k + rank + 1)
            scores[pid] = scores.get(pid, 0.0) + rrf_score
    # #7704 (MEASURED MECHANISM — a description, not a contract; read THE
    # RULING at the end of this note before changing anything above): the rank
    # consumed above is POSITIONAL, so a leg's own tie order becomes a fusion
    # RELEVANCE difference between documents whose evidence is identical. Two
    # rows tied on every leg —
    #
    #     rrf_fusion([[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
    #                strategy_names=["fts", "vector"], weights={"vector": 1.5})
    #     -> {'b': 0.04098360655737705, 'a': 0.04032258064516129}
    #
    # — land 1.61% apart, ordered by whatever broke the leg's tie. In the search
    # legs that is the shared recency-then-id key the legs interpolate
    # (``recency_tie_clause`` / ``recency_key_expr`` / ``recency_tie_key`` —
    # #10121; the id half was #3019's whole rule): recency first, so a tie
    # surfaces the latest revision, then the leg's own id field —
    # ``url`` for source/document, ``eventId`` for events, and for points the
    # node id — a ULID on the create paths (chronological) but
    # ``pt_<sha256>`` on the extraction/commit paths
    # (``commit_schema.point_content_id``), so the ID half's order is
    # chronological for one population and content-hash for the other, and the
    # two id spaces sort as blocks ('p' after the digits). That id half is
    # arbitrary-but-stable by design — it is the total order that makes the key
    # deterministic, never a relevance proxy; the RECENCY half is the one that
    # carries meaning, and it is why a tie is no longer "whichever id sorts
    # first". The FUSED score's own tie at the end of this function remains
    # ``(-score, id)`` — fusion consumes leg POSITIONS, so it has no recency
    # signal of its own (#10121 scopes itself to the legs). The single-leg path
    # does not fuse (``sdk.py``'s
    # ``len(raw_results) == 1`` shortcut), so the same two rows keep the SAME
    # score there: the two paths disagree about identical evidence.
    #
    # This is the mechanism behind #7704's product symptom, but the symptom is
    # NOT a property of the mechanism. The fused gap above only becomes a
    # FINAL-score gap after ``GraphRanker`` min-max normalizes the PAGE, so
    # whether the W4-b contested boost (``W4_CONTESTED_BOOST`` — 0.0175 of
    # final score at ``graph_boost_weight`` 0.35) clears it is a function of the
    # caller's ``limit``. Name the FIXTURE, because the answers differ: it does
    # NOT clear on the 4-row E2E-1 twin fixture's pages (relevance gap 0.0189 >
    # 0.0175), while on the recorded 404-point sweep it clears from ``limit``
    # >= 40, where the contested twin ranks ABOVE its twin (delta -0.0041 —
    # ``tortoise/ranking.py``). The full page-scoped accounting is the
    # ``W4_CONTESTED_BOOST`` rationale in ``tortoise/ranking.py`` — do not
    # re-derive it here, and do not read a single page's outcome as the
    # mechanism's promise.
    #
    # ⛔ THE RULING (owner, 2026-10-25 — comment 6402104535 on #7704): the twin
    # case is a DUPLICATION defect, not a ranking one, and "Options A / B / C / D
    # are CLOSED as answers to this issue — none is authorised here … tie-averaged
    # ranks are not required once duplicate entries do not exist", with the armed
    # pin "must not be used to justify tie-averaging (option D)". So do NOT
    # implement tie-averaged ranks (RANK.AVG) on the strength of this note: that
    # IS option D, and it silently re-weights a production leg that carries no
    # score signal — ``structural`` scores every row a CONSTANT (1.0 / 0.5, above),
    # so on any hybrid text query its whole list becomes one tie group. The
    # fusion FORM is #4210's own open ruling; the measured defect closes with
    # dedup identity + provenance accumulation (#9998, #9437, #9879), not here.
    #
    # If you conclude tie-averaging is the right answer, the route is a REOPEN in
    # #7704's own home — evidence in front of the owner, argued — never a quiet
    # adoption. The recorded option set is the rationale on
    # ``W4_CONTESTED_BOOST`` in ``tortoise/ranking.py``: read it there, not as a
    # re-list here, because two of its members decide OTHER questions and are NOT
    # #7704 choices — replacing rank-based fusion with score-based fusion is
    # #4210's ruling, and tie-breaking on the contested signal reverses #3019's id
    # tie-break, which ``temporal_leg`` consumes positionally (#2976).
    #
    # The equal-evidence invariant itself is pinned by
    # ``tests/test_rrf_tie_rank_7704.py`` (``xfail(strict=True)``, ARMED: a
    # FUSION-side remedy XPASSes and reds the suite — option D first, which is
    # the point. Dedup never reaches that pin, which calls ``rrf_fusion``
    # directly on two DISTINCT ids with equal scores, so the marker comes off
    # deliberately: in the change that lands dedup, where the pin is replaced by
    # one asserting the intended state); the measured magnitudes live on #7704.
    # R5 (#1544): optional recency multiplier — a multiplier, NOT an additive
    # constant: the RRF score range is ~0.01–0.05 (the SearchScores rrf
    # docstring), so an additive offset would swamp fusion entirely.
    if recency_weights and recency_boost > 0:
        for pid in scores:
            w = recency_weights.get(pid, 0.0)
            if w > 0:
                scores[pid] = scores[pid] * (1.0 + recency_boost * w)
    # #2952: deterministic TOTAL order over ties. RRF is rank-based, so its
    # scores tie constantly on real corpora (a doc at the same rank in different
    # legs) — and a leg's OWN ranking, which RRF then consumes, is arbitrary
    # whenever that leg's rows tie on the leg's own score. A stable sort alone
    # keeps tie order at the mercy of the order the caller passed
    # ``ranked_lists`` in
    # — which in the SDK is ``as_completed`` (thread COMPLETION) order, i.e.
    # wall-clock/timing dependent. ``(-score, id)`` makes the fused order a pure
    # function of the leg CONTENTS, so the same (graph, query, params) always
    # truncates to the same top-k.
    return dict(sorted(scores.items(), key=lambda x: (-x[1], x[0])))


# ── Degradation chain ────────────────────────────────────────────────────────

def degradation_chain(
    graph,
    query: str | None,
    kind: str | None,
    query_vec: list[float] | None,
    strategies: dict[str, bool],
    entity_type: str = "point",
    limit: int = 20,
    is_embedded: bool = True,
    expanded_kinds=None,  # accepted for SDK compat; kind expansion is post-retrieval (sdk.py)
    elevated_timeout_ms: int | None = None,
    vector_index_api: str | None = None,
    excluded_statuses: tuple | None = None,
    leg_trace: list[dict] | None = None,
    keep_numeric: bool = False,
    min_vector_similarity: float | None = None,
    floored_legs: set[str] | None = None,
    scope_kinds: tuple[str, ...] | None = None,
) -> dict[str, list[tuple[str, float]]]:
    """Run retrieval strategies in parallel with per-strategy degradation.

    Each strategy wraps in try/except → on failure, skip and log.
    Supports partial failure: one strategy down, others continue.
    Strategies run in parallel via ThreadPoolExecutor (P0 fix: #7780).

    is_embedded: True for embedded/redislite mode (brute-force vector),
                 False for Docker/server mode (HNSW index-accelerated).
    entity_type: 'point' (default), 'event', 'subject', 'document', 'object',
                 'source', or 'operator' — forwarded to each retrieval
                 strategy to query the correct entity label/id field. (#172)

    elevated_timeout_ms: benchmark override (issue #316). Default None = the
        pre-registered 500ms collective cap (as_completed(timeout=0.5)). When
        set (e.g. 5000ms), it is threaded into all THREE spots — the per-runner
        driver timeout, the structural runner timeout, and the as_completed
        deadline — so a benchmark can measure true completion without the cap
        truncating the tail. Default-off: production callers never pass it and
        behavior is byte-identical.

    vector_index_api: 'procedure' | 'cypher' | None — recorded on the
        projection at index-creation time (FalkorProjection._vector_index_api,
        #1359); forwarded to run_vector_query so Cypher-native engines skip
        the failing signature-A attempt.

    Returns:
        {strategy_name: [(id, score), ...]} — only strategies that succeeded

    R3 (#1542) D4: ``leg_trace`` — when provided, per-strategy entries are
    merged into it in FIXED order (fts, vector, structural). Runner threads
    record into their own PRIVATE list (a cancelled-but-running worker can
    never append to the shared trace after the merge); the merge reads only
    ``future.done()`` futures via ``result(timeout=0)`` so it is
    non-blocking — a still-running worker is never joined past the deadline;
    on ``TimeoutError`` it is discarded and self-recorded as
    ``reason="timeout", ran=True, degraded=True`` (never absent). Default
    None = no trace (byte-identical behavior).

    #6851 — what the cap does NOT promise. ``elevated_timeout_ms`` (default
    500ms) bounds the WAIT, never the COLLECTION, so it is not a hard edge on
    what a call returns: a leg that finished AFTER the deadline can still be
    returned. Two measured windows admit one. (a) ``as_completed``'s own wait
    wakes on a future's completion as well as on the deadline, so it can
    overshoot the cap and then yield that future as if it had arrived on time.
    The overshoot is SCHEDULING noise, not a budget: measured over a 30-run
    sample against the 500ms cap, the ``TimeoutError`` raise landed a median
    ~100ms late, with a worst case of ~270ms (0.501s–0.772s). That is why
    #6842's 100ms stub margin was never enough — a 100ms margin sits at the
    median of the overshoot it has to outrun, and the red was this window, not
    a logic error. (b) The post-deadline rescan collects every
    future that is ``done()`` by the time it runs. Only a worker STILL running
    at that rescan is discarded, and only that one is recorded as ``timeout``.
    A leg discarded this way is not a relevance verdict (see ``floored_legs``).
    Pinned by ``tests/bench/test_degradation_chain.py::
    test_post_deadline_rescan_collects_a_late_leg``.

    min_vector_similarity (#4028): optional COSINE relevance floor for the
        vector leg, forwarded to :func:`run_vector_query`. A nearest
        neighbour below it carries no relevance signal and is dropped rather
        than fused at rank-1 weight. Default None = no floor (byte-identical
        for every pre-#4028 caller).
    floored_legs (#4028): optional set that receives the name of any leg
        which RAN but was emptied by ``min_vector_similarity`` — lets the
        caller distinguish "no relevant neighbour" from "leg failed" (the
        distinction the search surface needs to avoid answering from the
        TF-IDF fallback). Default None = not reported.
    scope_kinds (#4199): optional KIND values the read's own post-retrieval
        kind filter selects — forwarded verbatim to
        :func:`run_vector_query`, which then judges the dense leg's material
        against that scope instead of the whole entity label.
        Default None = pre-#4199 behavior.
    """
    # #5404: this is the public orchestration entry point, and the collection
    # loop below swallows every strategy exception by design (a failed leg
    # must DEGRADE, not crash the read). An unvalidated entity_type would
    # therefore be swallowed into an empty result — the fail-open symptom this
    # issue closes — so the guard belongs here, ahead of the workers.
    validate_entity_type(entity_type)

    import concurrent.futures

    results: dict[str, list[tuple[str, float]]] = {}
    futures: dict[concurrent.futures.Future, str] = {}
    trace_active = (leg_trace is not None
                    or min_vector_similarity is not None
                    or floored_legs is not None)
    #: per-strategy private lists (workers record here, never into the
    #: caller's shared trace — the data-race guard, review P1).
    private: dict[str, list[dict]] = {}
    if trace_active:
        for _name in ("fts", "vector", "structural"):
            private[_name] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        # Submit all enabled strategies in parallel
        runner_timeout = int(elevated_timeout_ms or 500)
        # R3 (#1542) D4: the per-strategy private list is passed ONLY when
        # tracing is active — a default (leg_trace=None) caller sees
        # byte-identical submit kwargs (the bench fakes in
        # tests/bench/test_degradation_chain.py pin this).
        def _runner_kwargs(name: str) -> dict:
            return ({"leg_trace": private.get(name)} if trace_active else {})

        # #1391: the caller's exclusion applies uniformly (best-match AND
        # full-scan). Full-scan completeness callers (the #1353 supersede-
        # structure surface) opt in via excluded_statuses=() (include_terminal).
        # #4489: the retrieval tally lives in a ContextVar too, and a NEW thread
        # does not inherit ContextVars (CPython) — so each leg is wrapped with
        # the retrieval binder AS WELL as the graph-ops one, or the leg half of
        # the retrieval figure would be permanently, silently zero. Both binders
        # are identity when their context is unarmed.
        if strategies.get("fts") and query:
            futures[executor.submit(
                bind_metering_context(_retrieval_metering.bind_worker_context(
                    run_fts_query)), graph, query,
                entity_type=entity_type, limit=limit,
                timeout_ms=runner_timeout, excluded_statuses=excluded_statuses,
                keep_numeric=keep_numeric,
                **_runner_kwargs("fts"),
            )] = "fts"

        if strategies.get("vector") and query_vec:
            # #4028: merge the floor kwarg ONLY when one is supplied — the
            # pinned contract is that a default caller sees byte-identical
            # submit kwargs (tests/bench/test_degradation_chain.py's doubles
            # do not accept the new kwarg).
            _vec_kwargs = _runner_kwargs("vector")
            if min_vector_similarity is not None:
                _vec_kwargs["min_similarity"] = min_vector_similarity
            if scope_kinds is not None:
                # #4199: merged ONLY when supplied — same pinned-contract
                # posture as ``min_similarity`` above (a default caller sees
                # byte-identical submit kwargs).
                _vec_kwargs["scope_kinds"] = scope_kinds
            futures[executor.submit(
                bind_metering_context(_retrieval_metering.bind_worker_context(
                    run_vector_query)), graph, query_vec,
                limit=limit, is_embedded=is_embedded,
                entity_type=entity_type, timeout_ms=runner_timeout,
                vector_index_api=vector_index_api, excluded_statuses=excluded_statuses,
                **_vec_kwargs,
            )] = "vector"

        if strategies.get("structural"):
            futures[executor.submit(
                bind_metering_context(_retrieval_metering.bind_worker_context(
                    run_structural_query)), graph, kind,
                entity_type=entity_type, limit=limit,
                timeout_ms=runner_timeout, excluded_statuses=excluded_statuses,
                **_runner_kwargs("structural"),
            )] = "structural"

        # Collect results with 500ms total timeout across all strategies.
        # as_completed(timeout=0.5) raises TimeoutError if any future
        # hasn't completed within 500ms from the start of iteration.
        # #316: elevated_timeout_ms threads the benchmark cap into the
        # deadline too (default-off — production callers keep 0.5s).
        deadline = (elevated_timeout_ms or 500) / 1000.0
        timed_out: set[str] = set()
        try:
            for future in concurrent.futures.as_completed(futures, timeout=deadline):
                strategy_name = futures[future]
                # #8450 (lane L4, #5096): PER-FUTURE, never the loop's outer
                # handler. `future.result()` used to run unguarded here, so a
                # RAISING worker escaped into the `except Exception` below —
                # which only logs — and every future `as_completed` had not yet
                # yielded was ABANDONED with its rows, while that leg's own
                # private trace still said ``reason="ok"``. The docstring's
                # contract is per-strategy ("one strategy down, others
                # continue"); the TimeoutError arm below already re-reads its
                # own stragglers for exactly this reason, and this is the same
                # salvage for a leg that raised instead of timing out.
                try:
                    strategy_results = future.result()  # already done — no timeout needed
                except Exception as e:  # a leg's failure is not the read's
                    logger.warning(
                        "%s strategy raised — degraded, other legs continue: %s",
                        strategy_name, e,
                    )
                    continue
                if strategy_results:
                    results[strategy_name] = strategy_results
                else:
                    logger.info("%s strategy returned no results — degraded", strategy_name)
        except concurrent.futures.TimeoutError:
            logger.warning(
                "Strategies timed out (500ms) — collected %d/%d, cancelling remaining",
                len(results), len(futures),
            )
            # R3 (#1542) D4 (#6851): THE CAP BOUNDS THE WAIT, NOT THE
            # COLLECTION. A future is discarded only if it is STILL not done
            # HERE. One that finished after the deadline but before this
            # rescan runs is COLLECTED, exactly like a leg that finished on
            # time, and its private-list entries are merged normally — it is
            # NOT recorded as ``timeout``. (The pre-#6851 wording claimed such
            # a future was "discarded AND self-recorded as timeout"; this loop
            # has never done that, and that claim is what let #6842 read the
            # cap as a deterministic boundary.) The read stays non-blocking:
            # result(timeout=0) on a done future, cancel() on the rest, never
            # a join — so a worker still running here is not waited for, and
            # its late private-list entries are discarded (timed_out skip
            # below). Note cancel() cannot stop a thread that has already
            # started, so a cancelled worker runs to completion regardless:
            # the ThreadPoolExecutor.__exit__ join below is what actually
            # bounds this call's wall-clock duration. The caller therefore
            # pays a straggler's own latency, never the cap.
            for f in list(futures):
                strategy_name = futures[f]
                if strategy_name in results:
                    continue
                if f.done():
                    try:
                        strategy_results = f.result(timeout=0)
                        if strategy_results:
                            results[strategy_name] = strategy_results
                    except Exception as e:
                        logger.warning("Strategy execution failed: %s — degraded", e)
                else:
                    timed_out.add(strategy_name)
                    f.cancel()
        except Exception as e:
            # #8450: now the LAST RESORT only — a leg that raises is absorbed
            # per-future above, so reaching here means the COLLECTOR itself
            # failed (e.g. ``as_completed``), which does bound the whole call.
            logger.warning("Strategy execution failed: %s — degraded", e)

        # R3 (#1542) D4: merge the private lists in FIXED order (fts, vector,
        # structural) — as_completed yields in COMPLETION order, so the merge
        # must not follow it. Timed-out strategies get their self-recorded
        # timeout entry here (fixed order too); a cancelled-but-running
        # worker's late append to its PRIVATE list cannot land in the trace.
        if trace_active and leg_trace is not None:
            for strategy_name in ("fts", "vector", "structural"):
                if strategy_name in timed_out:
                    leg_trace.append(_trace_entry(
                        strategy_name, ran=True, degraded=True,
                        reason="timeout", count=0))
                    continue
                entries = private.get(strategy_name)
                if entries:
                    leg_trace.extend(entries)

    # #4028: report which legs RAN but were emptied by the relevance floor.
    # The search surface uses this to avoid treating a floor-emptied vector
    # leg as a leg FAILURE and answering from the TF-IDF fallback (which
    # returns hits for any query — the very leak being fixed). Read from the
    # per-strategy PRIVATE traces, never the caller's shared list.
    if floored_legs is not None:
        for strategy_name in ("fts", "vector", "structural"):
            if strategy_name in timed_out:
                # A leg the collector discarded as TIMED OUT is not a floor
                # verdict, however its worker finished later (#4028 review P2).
                continue
            if any(e.get("leg") == strategy_name
                   and e.get("reason") == BELOW_RELEVANCE_FLOOR
                   for e in private.get(strategy_name, [])):
                floored_legs.add(strategy_name)

    # #2952: return the legs in FIXED strategy order (fts, vector, structural).
    # ``results`` above is populated inside ``as_completed`` — i.e. in thread
    # COMPLETION order — so the same leg contents could reach the fusion (and
    # every other consumer of this mapping) in a different order run to run.
    # Downstream RRF then inherited that order as its tie-break. Fixed order
    # makes the returned mapping a pure function of the leg contents (the trace
    # merge above already uses this order for the same reason).
    return {
        name: results[name]
        for name in ("fts", "vector", "structural")
        if name in results
    }


# ── EP annotation ────────────────────────────────────────────────────────────

# Variance threshold above which a claim's posterior is considered contested.
# Must match TortoiseEP.get_contested_claims (tortoise/ep.py).
CONTESTED_VARIANCE_THRESHOLD = 0.04

# Driver-level timeout for decoration queries (ms). The decoration runs OUTSIDE
# the per-strategy circuit breakers (those guard degradation_chain); a server-side
# kill per query bounds a hung FalkorDB's impact on search latency (#1353 review).
_DECORATION_TIMEOUT_MS = 200


#: A value that is a CALENDAR DATE with NO time-of-day component, optionally
#: followed by a timezone offset — the shape ``_created_sort_key`` must ANCHOR
#: rather than let ``fromisoformat`` resolve in the reader's zone (#3982).
#:
#: The offset belongs INSIDE the anchored pattern. The pattern must match the
#: WHOLE string (``\Z``, not ``$``), because an unanchored date alternative is
#: free to match only its own extent: against ``"2026-W24-05:00"`` the
#: week-with-day alternative matches ``"2026-W24-0"``, which would leave
#: ``"5:00"`` — not an offset — and the value would go unanchored. ``\Z`` and
#: not ``$``: ``$`` also matches immediately BEFORE a trailing newline, so
#: ``"2026-06-10\n"`` would be accepted as a date-only value and anchored,
#: laundering a malformed stored value into a valid instant where it used to
#: fall through to the unparseable bucket.
#:
#: Reachability: the caller's gate is ``s[0].isdigit() and len(s) >= 10 and
#: ("-" in s or "T" in s)``, so every DASHED date of 10+ characters arrives
#: (bare or with any offset, either sign), a COMPACT form arrives when it bears
#: a negative offset or a ``T``, and a bare compact date never arrives.
#:
#: ``YYYY-DDD`` (ordinal) is absent deliberately: the stdlib cannot parse it
#: (``date.fromisoformat("2026-161")`` raises), so such values are unparseable
#: either way and an alternative for them would be dead.
_DATE_ONLY_RE = re.compile(
    r"^(?P<date>"
    r"\d{4}-W\d{2}-\d"       # ISO week date, dashed, with day
    r"|\d{4}-W\d{2}"         # ISO week date, dashed, week only
    r"|\d{4}W\d{2}\d"        # ISO week date, compact, with day
    r"|\d{4}W\d{2}"          # ISO week date, compact, week only
    r"|\d{4}-\d{2}-\d{2}"    # calendar date
    r"|\d{8}"                # calendar date, compact
    r")"
    r"(?P<off>[+-][0-9]{2}(?::?[0-9]{2})?(?::?[0-9]{2})?)?"
    r"\Z"
)


def _created_sort_key(value):
    """Sortable key for createdAt values — safe across mixed formats.

    The codebase writes ISO-8601 strings (projection) AND numeric epoch
    (RedisGraph `timestamp()` in seeded corpora) — a graph can hold both.
    Comparing them directly raises TypeError in py3. ISO strings are parsed
    to epoch so mixed-format graphs compare by REAL instant (not format);
    unparseable values bucket last, deterministically.
    """
    if isinstance(value, (int, float)):
        return (0, float(value))
    s = str(value or "")
    if s and s[0].isdigit() and len(s) >= 10 and ("-" in s or "T" in s):
        try:
            from datetime import date, datetime
            iso = s.replace("Z", "+00:00")
            # #3982: a DATE-ONLY value carries no offset, and
            # ``datetime.fromisoformat`` returns a NAIVE datetime whose
            # ``.timestamp()`` reads it in the READER'S LOCAL ZONE — so the
            # instant a stored fact occupies, and therefore whether a query
            # finds it, depended on the machine doing the asking. Anchoring is
            # not a preference; it is settled normatively. ECMA-262 §21.4.3.2
            # (the ECMAScript standard, ``Date.parse``): "When the UTC offset
            # representation is absent, date-only forms are interpreted as a
            # UTC time and date-time forms are interpreted as a local time."
            # So a date-only value is pinned to UTC midnight below, while a
            # date-TIME with no offset keeps the LOCAL reading the same clause
            # prescribes. W3C XML Schema 1.1 corroborates from the other side:
            # a zone-less value is not one moment (a calendar day spans up to
            # 52 hours), so it must be anchored, not resolved against the
            # reader.
            #
            # Scope: this aligns DATE-ONLY with the repo's #153 rule ("no zone
            # given means UTC") and deliberately does NOT extend it to a
            # zone-less date-TIME, which stays local. The two therefore DISAGREE
            # for the same nominal string: this primitive reads
            # "2024-01-01T00:00:00" locally, while
            # ``source_credibility._parse_timestamp`` reads it as UTC (pinned by
            # tests/test_event_provenance.py::test_recency_timezone_naive_ingested_at).
            # That divergence is OPEN, recorded here so it is visible at the
            # primitive rather than rediscovered from a surprising ordering.
            #
            # An offset suffix needs an explicit time component. Python 3.11+
            # accepts ANY character as the date/time separator, so
            # ``fromisoformat("2026-06-10+05:00")`` reads the offset's digits
            # as a TIME-OF-DAY and returns ``2026-06-10 05:00:00`` with
            # ``tzinfo=None`` — never applying the offset. A date-only value is
            # therefore normalised to an explicit midnight (the stated offset
            # if it has one, UTC if not) before parsing.
            if _m := _DATE_ONLY_RE.match(iso):
                # Rebuild through ``date`` so every alternative (dashed,
                # compact, week) is anchored the same way: a stated offset
                # wins, a bare date means UTC midnight. Going through ``date``
                # rather than string surgery is what lets a WEEK date be
                # handled at all — a time cannot simply be appended to it.
                iso = (
                    date.fromisoformat(_m.group("date")).isoformat()
                    + "T00:00:00"
                    + (_m.group("off") or "+00:00")
                )
            return (0, datetime.fromisoformat(iso).timestamp())
        except ValueError:
            pass
    return (1, s)


def _recency_factors(entries: list[tuple[str, Any]]) -> dict[str, float]:
    """Rank-based recency factor per id, newest→1.0, oldest→0.0.

    R5 (#1544): the date-weight primitive fed into ``rrf_fusion``'s
    ``recency_weights``. Reuses ``_created_sort_key`` (ISO + epoch
    mixed-format safe, #1353). Undated/unparseable ids get 0.0 (no boost).
    Ties on the same date sort by id for determinism and share the highest
    factor in their rank group.

    Rank-based (not age-based) so it is format-agnostic and immune to
    out-of-range ages; with <2 dated entries there is no spread and every
    factor stays 0.0 (nothing to weight).
    """
    dated = [(pid, k) for pid, d in entries
             if (k := _created_sort_key(d))[0] == 0]  # parseable dates only
    factors: dict[str, float] = {pid: 0.0 for pid, _ in entries}
    if len(dated) < 2:
        return factors
    # Descending date order: index 0 = newest → highest factor (1.0),
    # last = oldest → 0.0 (the rank-based percentile, newest-first). Docs
    # on the SAME date form a rank group and SHARE the group's highest
    # factor (deterministic tie-break — same date ⇒ same recency).
    ordered = sorted(dated, key=lambda t: (t[1], t[0]), reverse=True)
    n = len(ordered)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        factor = (n - 1 - i) / (n - 1)
        for pid, _k in ordered[i:j + 1]:
            factors[pid] = factor
        i = j + 1
    return factors


def _beta_variance(alpha: float, beta: float) -> float:
    """Variance of the Beta(α, β) posterior: αβ/((α+β)²(α+β+1)).

    Max is 1/12 ≈ 0.0833 at α=β=1 (uninformative prior). Same formula as
    TortoiseEP.get_contested_claims.
    """
    s = alpha + beta
    # A sum that is not finite is not a usable pair either: an overflowing sum
    # gives a denominator of inf (0.0/0.0-free but meaningless), and a NaN alpha
    # would put a NaN variance on the wire (`json.dumps` emits a bare `NaN`
    # token). Report "no variance" instead of a bogus or unwritable number.
    if not math.isfinite(s) or s <= 0:
        return 0.0
    denom = s * s * (s + 1)
    # Underflow case: a subnormal pair (e.g. α=β=1e-200) makes `s*s` and
    # `α*β` both 0.0 → 0.0/0.0 raised ZeroDivisionError out of the batch
    # decoration, discarding every assembled point (the #8504 failure shape,
    # one line under the site this change repairs).
    if denom == 0.0 or not math.isfinite(denom):
        return 0.0
    return (alpha * beta) / denom


def _beta_mean(alpha: float, beta: float) -> float:
    """Mean of the Beta(α, β) belief: α/(α+β) — the issue #2206 contract
    resolution for a point's confidence.

    Same guard as TortoiseEP.compute_confidence / why._mean: degenerate
    (0,0) params fall back to the neutral Beta(1,1) mean 0.5 instead of
    ZeroDivisionError.
    """
    s = alpha + beta
    # `isfinite` as well as `> 0`: an overflowing sum (+inf) would otherwise
    # yield α/inf == 0.0 — a wrong belief (maximum disbelief) rather than the
    # neutral fallback this guard exists to serve.
    return alpha / s if math.isfinite(s) and s > 0 else 0.5


def annotate_ep_batch(graph, point_ids: list[str]) -> dict[str, EpBreakdown]:
    """Fetch EP confidence breakdown for a batch of Point IDs.

    #7927 KNOWN DIVERGENCE, deliberate: this read is ``:Point``-anchored (the
    id anchor below AND the evidence reads), while ``ep.get_contested_claims``
    and the rankers now sweep the four epistemic labels (#7813). A MEASURED
    :Subject/:Object/:Event belief is therefore reported there and reads as
    having NO EP state here. Its only search call site additionally gates on
    ``entity_type == "point"``. Widening it is a separate read surface outside
    #7927's six surfaces — do not "harmonize" it by analogy; decide first
    which surface an entity's belief belongs on.

    Single Cypher query — NOT N+1. Returns EpBreakdown per Point ID.

    Issue #2206 contract — ``EpBreakdown.confidence_mean`` is THE point's
    confidence and agrees with ``get_confidence``/``recall`` for the same
    point: it is the belief mean α/(α+β) of the persisted posterior
    (posterior_alpha/beta) when EP has run on the claim, else the persisted
    prior mean (ep_alpha/beta), else the neutral Beta(1,1) mean 0.5. It is
    NOT the structural edge-ratio impl/(impl+nand) that this field used to
    carry — that quantity lives on in ``evidence`` (impl_count/nand_count/
    total) and ``contention`` (the NAND fraction), which are legitimately
    different from a belief.

    Points with no EP data (no posterior_alpha AND no ep_alpha) get
    has_ep=False + confidence_mean=0.5 (neutral — absence of measurement is
    NOT low support, matching the recall/why-layer convention);
    contention=0.0. variance/contested are computed from the PERSISTED
    posterior (posterior_alpha/beta, falling back to ep_alpha/beta priors)
    (posterior stability), not from edge ratios.

    #2490 has_ep overload: TERMINAL claims (status in the terminal vocab or
    the legacy outdated=true flag) are gated to has_ep=False + contested=False
    even when EP actually measured them (their posterior decays to vacuity at
    the terminalizing write). has_ep therefore no longer strictly means "EP
    ran" — for a terminal claim it means "not serving its frozen/dead
    posterior to include-terminal surfaces". Consumers (topic disputed-pair
    gate, volunteer, mcp) must not read terminal=unmeasured.
    """
    if not point_ids:
        return {}

    try:
        cypher = (
            "MATCH (n:Point) "
            "WHERE n.id IN $ids "
            # #6871: `impl_count` is an INCOMING EVIDENCE count, and an
            # operator's typed edges are its input set — idx 0 is the
            # operator's SOURCE, idx >= 1 its targets. IMPL pairs are ALWAYS
            # source -> target (`ep.py::_update_nary_factor` takes the
            # `input_ids[0]` arm unconditionally for IMPL), so an idx=0 edge is
            # the operator's own input and never evidence pointing AT it.
            # Unguarded, a source with nothing supporting it reported
            # impl_count=1 and a diluted `contention` (nand/(impl+nand)) on the
            # wire. So the ONE edge excluded is the operator's input: idx = 0 ON
            # an operator. Everything else counts, including an operator-less
            # direct edge — `create_direct_edge` writes it with NO `idx`, so it
            # can never carry a role and is evidence by definition.
            #
            # `c.is_operator IS NULL` is NOT the same as "not an operator": the
            # canonical identity is `bool(is_operator or op_type)`
            # (graph-scripts/backfill_is_operator.py, which stamps exactly that
            # legacy class), and five sites match
            # `(is_operator = true OR op_type IS NOT NULL)`. Treating a NULL on
            # a legacy operator as operator-less fails OPEN — its idx=0 edge
            # counts again for its own source, the very symptom being fixed.
            #
            # ⛔ NAND is deliberately NOT guarded here: its pairs depend on the
            # operator's `direction` (`-` = no pairs, directed = source ->
            # targets, mutual — the `create_operator` DEFAULT — = all pairs),
            # which this count cannot express. Filed as its own root: #8460.
            "OPTIONAL MATCH (n)<-[r:IMPL]-(c:Point) "
            "WHERE NOT (coalesce(r.idx, -1) = 0 "
            "           AND (c.is_operator = true OR c.op_type IS NOT NULL)) "
            "WITH n, count(r) AS impl_count "
            "OPTIONAL MATCH (n)<-[r2:NAND]-(:Point) "
            "WITH n, impl_count, count(r2) AS nand_count "
            "RETURN n.id, impl_count, nand_count, "
            "  CASE WHEN impl_count + nand_count > 0 "
            "    THEN toFloat(nand_count) / (impl_count + nand_count) "
            "    ELSE 0.0 "
            "  END AS contention, "
            "  coalesce(n.posterior_alpha, n.ep_alpha, 1.0) AS alpha, coalesce(n.posterior_beta, n.ep_beta, 1.0) AS beta, "
            # #3276: has_ep == EP MEASURED (not merely "a prior exists").
            # baseline_set plumbs the prior-only distinction to Python.
            f"  {ep_measured_cypher('n')} AS has_ep, "
            "  n.status, coalesce(n.outdated, false), "
            f"  {ep_baseline_cypher('n')} AS baseline_set "
        )
        rows = graph.query(cypher, params={"ids": point_ids}).result_set

        breakdowns: dict[str, EpBreakdown] = {}
        for row in rows:
            pid, impl, nand, contention, alpha, beta, has_ep = row[0], int(row[1]), int(row[2]), float(row[3]), float(row[4]), float(row[5]), row[6]
            # #2490: terminal claims never surface a frozen/decayed posterior
            # as measured EP — has_ep=False + contested=False (the columns
            # are fetched so the terminal state is read, not guessed; the len
            # guard tolerates doubles mirroring the pre-#2490 7-col shape).
            terminal = len(row) > 8 and is_terminal_status(row[7], bool(row[8]))
            if terminal:
                has_ep = False
            # #3276: baseline_set rides the tail (index 9) so the pre-#3276
            # 7/9-col row shapes keep their index mapping under the len guard.
            baseline_set = bool(row[9]) if len(row) > 9 else False
            measured = bool(has_ep)
            total = impl + nand
            variance = _beta_variance(alpha, beta)
            breakdowns[pid] = EpBreakdown(
                confidence_mean=round(_beta_mean(alpha, beta), 4),
                evidence=EpEvidence(impl_count=impl, nand_count=nand, total=total),
                contention=contention,
                variance=round(variance, 6),
                # Contested only when EP actually ran: an uncalibrated point
                # (no persisted α/β → defaults to 1/1 → v=1/12) is NOT
                # contested, it's unmeasured. (#2490: terminal claims are
                # gated above, so their decayed (1,1) posterior never reads
                # contested either.)
                contested=measured and variance > CONTESTED_VARIANCE_THRESHOLD,
                has_ep=measured,
                measured=measured,
                # prior-only: a declared #2199 baseline on a LIVE claim with
                # no EP measurement — confidence_mean is the PRIOR mean, never
                # a measured one. A TERMINAL claim is neither measured nor
                # prior-only (its 0.5 is the #2490 decayed posterior), so the
                # terminal gate above also clears `baseline` — see the
                # EpBreakdown three-state note.
                baseline=baseline_set and not measured and not terminal,
            )

        # Fill in defaults for IDs that are not Point nodes (defaults match
        # the neutral contract: confidence_mean 0.5, has_ep False).
        for pid in point_ids:
            if pid not in breakdowns:
                breakdowns[pid] = EpBreakdown(
                    confidence_mean=0.5,
                    evidence=EpEvidence(impl_count=0, nand_count=0, total=0),
                    contention=0.0,
                )

        return breakdowns
    except Exception as e:
        logger.warning("EP batch annotation failed: %s", e)
        return {}


def get_relationships(graph, point_ids: list[str]) -> dict[str, list[dict]]:
    """Fetch the epistemic relationships of a batch of Point IDs.

    One id-anchored statement per epistemic label (NOT N+1 — the batch rides
    ``$ids``). Returns dict mapping point_id → list of relationship dicts with
    {predicate, mechanism, related_id, related_kind, related_content,
    direction, operator_id}.

    Points with no epistemic relationship get an empty list.

    Two LIMBS, and #6976 is the story of each being invisible in turn:

    * **operator-less DIRECT edges** (``sdk.create_direct_edge`` — ONTOLOGY
      v3.5 §8 calls plain IMPL/NAND connections direct edges, "NO operator
      node", and ``tortoise_list_batch`` reports them as ``direct_edge``). The
      read used to require an operator hop, so a graph written the way the
      ontology says plain connections ARE read as having no relationships at
      all — from BOTH endpoints. Leg 2 below is that half; a direct edge's own
      ``label`` becomes ``predicate`` and there is no operator to name
      (``operator_id`` is ``""``).
    * **operator-MEDIATED** relationships (the ``OperatorAdded`` shape): the
      point and its co-endpoints, reached through an operator node.

    **Every endpoint is an epistemic node, not necessarily a ``:Point``.**
    ONTOLOGY §5 / the #7813 owner ruling let a claim's endpoints carry any of
    the four labels (``:Point`` / ``:Subject`` / ``:Object`` / ``:Event``) —
    only an OPERATOR stays ``:Point`` — and the #8882 re-typing migration moves
    exactly those nodes onto their own layer. This read used to hard-code
    ``:Point`` on the anchor AND on the peer, so a hub whose edges run to
    re-typed nodes matched nothing, the seeds below were never filled, and
    ``sdk.expand_relationships`` returned the empty seed verbatim: a dense node
    read as isolated. The three predicates are therefore the sanctioned
    widening, not a loosening of WHAT is counted:

    * the ANCHOR is swept per label (``live.epistemic_label_queries``) rather
      than filtered in a WHERE, because the anchor is id-anchored and each
      label keeps its own ``(label, id)`` range index (#7853) — three of them
      do: ``:Point``/``:Subject``/``:Object`` carry an ``id`` RANGE index while
      ``:Event`` carries one on ``eventId`` only, so an Event addressed by
      ``id`` is an unindexed label scan under EITHER shape (``live.py``'s
      recorded #7853 caveat). The sweep is still the right shape; the cost of
      the ``:Event`` leg is the price of not reading that layer as isolated,
      and it is bounded by ``LIMIT``-free id-anchored use only when the batch
      is small — see R1 on #8996.
    * the PEER is widened by traversal (``live.epistemic_disjunction``) — it is
      reached from the operator, so no index applies — and its non-operator
      test admits an ABSENT ``is_operator`` (a ``:Object``/``:Event`` carries
      no such property; the ``= false`` spelling excluded every one of them).
      ``_is_claim`` is that non-operator test PLUS ``op_type IS NULL`` — the
      strict form ``sdk._operator_scope_clauses`` calls the CANONICAL
      non-operator predicate. A node carrying ``op_type`` is therefore an
      OPERATOR on both legs, and operator→operator is R4 on #8996, the same
      undecided question an operator anchor raises.
      The dedupe site diverges narrowly and deliberately:
      ``sdk._find_point_by_content`` documents a HYBRID that EXPLICITLY declares
      ``is_operator = false`` AND carries ``op_type`` (a shape nothing rejects on
      write) and keeps it a claim, because a dedupe must not drop a node it was
      asked about. An ABSENT ``is_operator`` hybrid is excluded by BOTH of those
      sites — that helper's second clause ``op_type IS NULL OR is_operator =
      false`` evaluates NULL there — so the two agree on that arm. The three
      sites therefore differ in three ways, and none of it is resolved here:
      this read admits NO hybrid, ``_find_point_by_content`` admits the
      explicit-false one, and ``_reachable_claim_count`` admits both.
    * the OPERATOR keeps its ``:Point`` label (ONTOLOGY §5 — a re-typing plan
      that moves an operator is refused) but is identified by
      ``is_operator = true OR op_type IS NOT NULL``, the #943 parity every other
      operator read uses, so an operator written without the flag is not
      invisible here alone.

    A node's own layer kind and text live under that layer's property
    (``pointKind``/``content`` for a Point, ``objectKind``/``name`` for an
    Object, …), so ``related_kind`` and ``related_content`` coalesce the four —
    otherwise the peers this widening admits come back with an empty kind and
    empty content, which is the same defect one layer down.

    Of the two limbs, the DIRECT-EDGE one is the reporter's (#6976's live repro
    is a ``direct_edge``); the non-``:Point`` endpoint limb is the latent one
    the earlier predicate-barrier fixes could not reach. The reporter's own
    ``INPUT × 209`` hub was not re-tested against their production graph — if it
    still reads empty, the candidate is a chain of single-endpoint operators
    (R4 on #8996), which is a decision about WHICH relation this read promises,
    not a further predicate.
    """
    if not point_ids:
        return {}

    rels: dict[str, list[dict]] = {pid: [] for pid in point_ids}

    # #6976: `WITH n` is LOAD-BEARING, not style. On FalkorDB 6.x a `WHERE` on
    # `n.id IN $ids` followed by a plain MATCH that re-binds `n` (or carries a
    # relationship pattern) is planned as a WHOLE-GRAPH read — the rows come
    # back for FOREIGN points or with a NULL `n`, every one of them is dropped
    # by the `if pid in rels` guard below, and the caller reads a densely
    # connected node as isolated. Same barrier as the other id-anchored readers
    # in this tree (`tests/test_id_predicate_barrier_6976.py`, #7162).
    #
    # The operator predicate rides the SECOND WHERE (after both MATCHes) so no
    # WHERE here is followed by a MATCH that re-binds what it constrains — the
    # `WITH n` is the only barrier this shape needs (each UNION branch carries
    # its own `WITH n`, and a UNION boundary ends the scope anyway).
    #
    # #6976 has TWO limbs, and they are two legs of one statement:
    #
    #   leg 1 — OPERATOR-MEDIATED (the ``OperatorAdded`` shape): the point and
    #           its co-endpoints, reached through an operator node;
    #   leg 2 — OPERATOR-LESS DIRECT (``sdk.create_direct_edge``, ONTOLOGY v3.5
    #           §8: "plain IMPL/NAND connections are direct edges … NO operator
    #           node"). Leg 1 structurally requires an operator hop, so a graph
    #           written the way the ontology says plain connections ARE — the
    #           shape ``tortoise_list_batch`` reports as ``direct_edge`` — read
    #           as having NO relationships at all, from both endpoints.
    #
    # `_is_claim` is the STRICT "is a claim" predicate — the form
    # `sdk._operator_scope_clauses` calls CANONICAL, carried by
    # `_window_closure`'s direct-edge hop. The siblings differ, and none of it
    # is resolved here: `_reachable_claim_count` (its anchor scan AND its edged
    # scan) is lenient on BOTH arms, admitting an `op_type` node whether the
    # flag is explicitly false or ABSENT; `sdk._dedup_match_clauses` /
    # `_find_point_by_content` is lenient ONLY on the explicit-false hybrid —
    # its second clause `op_type IS NULL OR is_operator = false` evaluates NULL
    # for an ABSENT flag, so it agrees with this read on that arm (its docstring
    # says the same: the `op_type` disjunct "excludes LEGACY operators"). This
    # read takes the strict form because an operator is not a peer, and an
    # operator's own typed edge `(op)-[:IMPL]->(claim)` would otherwise be
    # reported as a relationship between claims.
    #
    # It is applied to THREE positions, and the third is a NARROWING, not a
    # refactor:
    #
    #   leg 1 `other`  — a co-endpoint of the operator must not itself be an
    #                    operator. Before this, leg 1 tested only the
    #                    `is_operator` half, so a peer carrying `op_type` with
    #                    `is_operator` absent/false — the HYBRID shape
    #                    `sdk._dedup_match_clauses` documents as real and
    #                    nothing refuses on write — was reported as a peer.
    #                    Operator→operator is a CHAIN of single-endpoint
    #                    operators, which is R4 on #8996, not a peer relation.
    #                    So this read takes the STRICT answer to "is a hybrid a
    #                    claim?" while `_find_point_by_content` takes the lenient
    #                    one for the EXPLICIT-false shape only (they agree on the
    #                    ABSENT-flag one); the divergence is deliberate (dedupe
    #                    must not drop a node it was asked about) and pinned per
    #                    leg in `tests/test_expand_relationships_6976.py`.
    #   leg 2 `other`  — the direct-edge peer half (above).
    #   leg 2 `n`      — the ANCHOR half, so what leg 2 reports really are
    #                    OPERATOR-LESS edges: otherwise, asked about an operator,
    #                    it would report that operator's own typed edge and
    #                    claim `operator_id: ''` for a relationship that has one
    #                    (the operator itself). An operator anchor is therefore
    #                    still the one shape this read does not express; it is
    #                    R4 on #8996, a decision about WHICH relation
    #                    `expand_relationships` promises.
    _is_claim = (
        "({a}.is_operator IS NULL OR {a}.is_operator = false) "
        "AND {a}.op_type IS NULL"
    )
    _related_kind = (
        "coalesce(other.pointKind, other.objectKind, other.subjectKind, "
        "         other.eventKind)"
    )
    template = (
        "MATCH (n:{label}) WHERE n.id IN $ids "
        "WITH n "
        "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point) "
        "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other) "
        "WHERE (op.is_operator = true OR op.op_type IS NOT NULL) "
        f"  AND {epistemic_disjunction('other')} "
        "  AND other.id <> n.id "
        f"  AND {_is_claim.format(a='other')} "
        "RETURN n.id, op.op_type AS mechanism, "
        "  coalesce(op.label, '') AS predicate, "
        "  op.id AS operator_id, other.id AS related_id, "
        f"  {_related_kind} AS related_kind, "
        "  coalesce(other.content, other.name) AS related_content, "
        "  r.idx AS n_idx, r2.idx AS other_idx "
        "UNION ALL "
        "MATCH (n:{label}) WHERE n.id IN $ids "
        "WITH n "
        "MATCH (n)-[d:IMPL|NAND]-(other) "
        f"WHERE {epistemic_disjunction('other')} "
        "  AND other.id <> n.id "
        f"  AND {_is_claim.format(a='other')} "
        f"  AND {_is_claim.format(a='n')} "
        "RETURN n.id, type(d) AS mechanism, "
        "  coalesce(d.label, '') AS predicate, "
        "  '' AS operator_id, other.id AS related_id, "
        f"  {_related_kind} AS related_kind, "
        "  coalesce(other.content, other.name) AS related_content, "
        "  CASE WHEN startNode(d).id = n.id THEN 0 ELSE 1 END AS n_idx, "
        "  0 AS other_idx"
    )

    try:
        rows: list = []
        # A MULTI-labelled anchor (`:Point:Object`) IS returned once per
        # matching label — `dream.py` records it ("the per-label sweep returns
        # a multi-labelled node once per matching label"). Both legs project
        # label-independent columns, so such an anchor's rows are identical and
        # `seen` collapses them: the set is load-bearing, not belt-and-braces
        # for a misbehaving mock.
        #
        # The key is TOTAL: `mechanism`/`predicate` are graph-authored free-form
        # properties (`op.op_type`, `*.label`), so a replayed-JSONL diagnostic
        # can make them lists, and an unhashable key raises straight into the
        # `except` below — emptying the WHOLE batch (#6971's failure-reads-as-
        # emptiness class, on the read path #6976 exists for). Non-scalars are
        # rendered with `repr` rather than skipped: the row still counts.
        seen: set[tuple] = set()
        for cypher in epistemic_label_queries(template):
            try:
                batch = graph.query(
                    cypher, params={"ids": point_ids}).result_set
            except Exception:
                # ONE label raising must not discard the rows the others
                # returned. The sweep exists to WIDEN the read, so it is
                # monotone: a leg that fails can only fail to add. (Labelling
                # the failure instead of swallowing it is R1 on #8996.)
                logger.warning(
                    "Relationship query failed for one epistemic label — "
                    "the other labels' rows are kept", exc_info=True)
                continue
            for row in batch:
                key = tuple(
                    v if isinstance(v, (str, int, float, type(None))) else repr(v)
                    for v in (row[0], row[1], row[2], row[3], row[4],
                              row[7] if len(row) > 7 else None,
                              row[8] if len(row) > 8 else None))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)

        for row in rows:
            pid = row[0]
            mechanism = row[1] or "IMPL"
            predicate = row[2]
            operator_id = row[3]
            related_id = row[4]
            related_kind = row[5] or ""
            related_content = row[6] or ""
            if not isinstance(related_content, str):
                # A replayed-JSONL diagnostic can leave a non-string here — the
                # same class the dedupe key above is made total for. FalkorDB
                # stores a NUMBER as a property (arrays slice fine; a map is
                # refused on write), and `42[:200]` raises TypeError. That is
                # WORSE than the key's failure, not equal to it: the key raises
                # during collection (nothing appended yet → an empty answer),
                # but this raises during PROCESSING, so the rows before it are
                # kept and every row after it is lost — for every point in the
                # batch. A silent PARTIAL read, which reads as "this peer has no
                # relationship" rather than as a failure (#6971's class, in its
                # harder shape). Render it; do not drop the row.
                related_content = str(related_content)
            n_idx = row[7] if len(row) > 7 else None
            other_idx = row[8] if len(row) > 8 else None  # noqa: F841

            # Determine direction: idx=0 = source, idx>0 = target.
            # Our point is source → relationship is outgoing.
            # Our point is target + other is source → relationship is incoming.
            if n_idx is not None and n_idx == 0:  # noqa: SIM108
                direction = "outgoing"
            else:
                direction = "incoming"

            rel_entry = {
                "predicate": predicate if predicate else "",
                "mechanism": mechanism,
                "related_id": related_id,
                "related_kind": related_kind,
                "related_content": related_content[:200] if related_content else "",
                "direction": direction,
                "operator_id": operator_id,
            }
            if pid in rels:
                rels[pid].append(rel_entry)
    except Exception:
        logger.warning("Relationship query failed", exc_info=True)

    return rels


#: #6971 — the fail-loud reason a search hit's ``relationships`` list is empty
#: because the DECORATION FAILED, not because the point is sparse.
#:
#: ``get_relationships_bounded`` seeds ``{pid: []}`` for every requested point
#: and fills it afterwards. Before #6971 the ``except Exception`` returned that
#: seed untouched, so a caller could not tell "this point has no IMPL/NAND
#: edges" from "the decoration query raised" — a silent wrong answer
#: (contradicting the point's own ``ep.evidence`` counts in the same payload),
#: with only a ``logger.warning`` below the API surface. Decoration stays
#: default-on and MUST NOT break retrieval (D1), so the failure is not raised —
#: it is LABELLED, and the label rides the wire additively.
RELATIONSHIPS_UNAVAILABLE = (
    "the bounded relationship decoration query raised, so 'relationships' is "
    "empty because the edges could not be read — NOT because this point has "
    "none. Retrieval is otherwise intact. The failure and its traceback were "
    "logged; re-read the point (or inspect the graph) to recover the "
    "decoration."
)


class RelationshipDecoration(dict):
    """``{point_id: [entry, …]}`` plus the decoration's own health (#6971).

    A plain ``dict`` to every existing consumer — equality, ``.get``,
    ``set(out)``, iteration and ``values()`` are unchanged, so the one
    production caller (the SDK search assembly) and the 25 test call sites in
    ``tests/test_relationships_bounded.py`` need no rewriting. The one addition is
    ``decoration_error``: ``""`` on success, and ``"<ExcType>: <message>"``
    when the query body raised. This is what makes ``get_relationships_bounded``
    distinguishable from an empty result without changing its return CONTRACT
    (a mapping of requested id → entries), which is why a typed mapping was
    chosen over re-raising: decoration is default-on (D1) and must never turn
    a search into a failure, and it must never leave the caller guessing.

    ``decoration_error`` is a CLASS attribute default, so a caller can read it
    unconditionally (``getattr(rels, "decoration_error", "")`` also tolerates
    a plain ``dict`` from a stand-in/monkeypatch, e.g. tests).
    """

    decoration_error: str = ""


def get_relationships_bounded(
    graph,
    point_ids: list[str],
    per_point_cap: int = 10,
    global_budget: int = 140,
    raw_cap: int = 3000,
    per_op_cap: int = 10,
    expand_top_k: int = 14,
) -> dict[str, list[dict]]:
    """Bounded, state-centric relationship decoration (#1353, D1-D14).

    Replaces the unbounded 2-hop fan-out at the search-decoration call site:
    the old query re-expanded every operator's full neighborhood PER RESULT
    POINT (n_results × operator-degree → ~122K dicts dense @ limit=100).
    This decouples the hops and dedupes operators across result points:

      Q1a   point → its operator edges (bounded by point degree)
      Q1b   CORRECTS edges in/out (supersede structure, direct point→point)
      Q2b   mitigations per operator (ids also exclude mitigation points from Q2)
      Q2    endpoints per operator (deduped), NAND-first, raw-capped
      Q2f   per-operator family counts (family_size)

    Class-aware cap: NAND edges, contested peers, superseded/retracted peers,
    mitigated_by and CORRECTS are ALWAYS kept — exempt from the per-point cap
    AND the global budget. The per-point cap (default 10) and global budget
    (default 140) govern the deduped IMPL support-mass only; exhaustion
    degrades tail results to structure counts (no peer lists).

    Peer EP state is derived IN-QUERY from coalesced posterior/ep alpha+beta
    (annotate_ep_batch parity — never NULL α/β, never a second annotation
    pass over thousands of peers). confidence = posterior mean α/(α+β);
    variance = _beta_variance; contested = has_ep AND variance > threshold.

    Entries keep the legacy keys (predicate, mechanism, operator_id,
    related_id, related_kind, direction) and add role, peer, family_size,
    op_created_at. related_content is intentionally ABSENT in the list view
    (D5 — full content via expand_relationships, D14).

    Contested coverage: contested peers are computed EXACTLY in Q2-crit — the
    in-Cypher variance on the coalesced persisted α/β (same formula as
    `_beta_variance`/TortoiseEP), so contested is cap-exempt with no sampling.
    NAND, terminal-status, mitigated and CORRECTS classes are always complete.

    get_relationships() keeps the D12 intent below — it is UNBOUNDED (no
    per-point cap, no global budget, no `expand_top_k`), because
    topic_summarization needs full NAND completeness for disputed-pair
    detection. #6976 widened its SELECTION (epistemic-label endpoints and
    operator-less direct edges, see its own docstring) without bounding it — so
    the D12 property holds, and now over more of the graph than it did: a direct
    `NAND` edge is a disputed pair whatever writes it.

    Returns a ``RelationshipDecoration`` mapping (requested id → entries).
    #6971: when the query body raises, the mapping is RESET to the all-empty
    seed — so a consumer that ignores the extra attribute stays safe, and the
    marker's claim ("'relationships' is empty because the edges could not be
    read") is TRUE for every requested point — but ``.decoration_error`` is
    non-empty and the caller MUST NOT present the emptiness as "no
    relationships". The search assembly labels the batch through
    ``SearchResult.relationships_unavailable``.
    """
    if not point_ids:
        return RelationshipDecoration()

    rels: RelationshipDecoration = RelationshipDecoration(
        {pid: [] for pid in point_ids})

    try:
        # Q1a: operator edges per point (bounded by point degree).
        # Retracted operators excluded (operator terminal state — their edges
        # are not live epistemic structure).
        rows = graph.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "WITH n "
            "MATCH (n)-[r:IMPL|NAND|hasPart]-(op:Point {is_operator:true}) "
            f"WHERE {_exclude_status_clause('op')} "
            "RETURN n.id, type(r) AS et, r.idx AS n_idx, op.id AS op_id, "
            "  op.op_type AS mechanism, coalesce(op.label, '') AS predicate, "
            "  op.createdAt AS op_created",
            params={"ids": point_ids},
            timeout=_DECORATION_TIMEOUT_MS,
        ).result_set
        point_ops: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        op_ids: set[str] = set()
        for row in rows:
            pid = row[0]
            point_ops[pid].append((row[1], row[2], row[3], row[4] or "IMPL", row[5], row[6]))
            op_ids.add(row[3])

        # Q1b: CORRECTS edges (direct point→point supersede links).
        # Chained OPTIONAL MATCH can cartesian (m outgoing × k incoming rows)
        # — dedupe by id in Python below.
        corrects_out: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        corrects_in: dict[str, list[tuple]] = {pid: [] for pid in point_ids}
        if point_ids:
            rows = graph.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "OPTIONAL MATCH (n)-[r:CORRECTS]->(old:Point) "
                "OPTIONAL MATCH (new:Point)-[r2:CORRECTS]->(n) "
                "RETURN n.id, old.id, old.status, old.createdAt, "
                "  new.id, new.status, new.createdAt",
                params={"ids": point_ids},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                pid = row[0]
                old_id, old_status, old_created = row[1], row[2], row[3]
                new_id, new_status, new_created = row[4], row[5], row[6]
                if old_id and old_id not in {o[0] for o in corrects_out[pid]}:
                    corrects_out[pid].append((old_id, old_status, old_created))
                if new_id and new_id not in {n[0] for n in corrects_in[pid]}:
                    corrects_in[pid].append((new_id, new_status, new_created))

        # Q2b: mitigations per operator — ids are excluded from endpoint
        # expansion in Cypher (direction-agnostic: legacy inbound mitigated_by
        # graphs are covered by the NOT pattern).
        op_mitigations: dict[str, list[dict]] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "WITH op "
                "MATCH (op)-[:mitigated_by]->(m:Point) "
                "RETURN op.id, m.id, m.status, m.createdAt, m.content",
                params={"op_ids": list(op_ids)},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_id = row[0]
                op_mitigations.setdefault(op_id, []).append({
                    "id": row[1], "status": row[2] or "",
                    "created_at": row[3], "snippet": (row[4] or "")[:120],
                })

        # Q2-crit: critical-class endpoints — NAND edges, terminal-status peers,
        # AND contested peers (in-Cypher variance on the coalesced α/β — exact,
        # no per-op sampling). COMPLETE, bounded only by raw_cap (safety valve):
        # these classes must never be dropped by the per-point/global/per-op
        # caps (D3/D4). Low volume in practice.
        op_crit: dict[str, dict[str, tuple]] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "WITH op "
                "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "  AND NOT (op)-[:mitigated_by]->(other) "
                "  AND (type(r2) = 'NAND' "
                f"       OR {_terminal_expression('other.status')} "
                f"       OR ({ep_measured_cypher('other')} "
                "           AND (coalesce(other.posterior_alpha, other.ep_alpha, 1.0) * coalesce(other.posterior_beta, other.ep_beta, 1.0)) "
                "               / ((coalesce(other.posterior_alpha, other.ep_alpha, 1.0) + coalesce(other.posterior_beta, other.ep_beta, 1.0)) ^ 2 "
                "                  * (coalesce(other.posterior_alpha, other.ep_alpha, 1.0) + coalesce(other.posterior_beta, other.ep_beta, 1.0) + 1)) > $contested_threshold)) "
                "RETURN op.id, type(r2) AS et, r2.idx AS other_idx, other.id, "
                "  other.pointKind, other.status, "
                "  coalesce(other.posterior_alpha, other.ep_alpha, 1.0), "
                "  coalesce(other.posterior_beta, other.ep_beta, 1.0), "
                # #2490: the aligned has_ep boolean — measured AND NOT terminal
                # (a decayed terminal's (1,1) posterior is column-
                # indistinguishable from a measured (1,1), so the status/flag
                # gate lives INSIDE the projection). #3276: "measured" is
                # ep_measured_cypher — a #2199 baseline prior is NOT measurement.
                f"  ({ep_measured_cypher('other')} AND {_alive_flag('other.status')}), "
                "  other.createdAt, coalesce(other.outdated, false) "
                "LIMIT $raw_cap",
                params={"op_ids": list(op_ids), "raw_cap": raw_cap,
                        "contested_threshold": CONTESTED_VARIANCE_THRESHOLD},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_crit.setdefault(row[0], {})[row[3]] = (
                    row[1], row[2], row[4] or "", row[5] or "",
                    float(row[6]), float(row[7]), bool(row[8]), row[9],
                    bool(row[10]),
                )

        # The operators of the TOP-K result points are the ones that get
        # support endpoint expansion. The global budget (140 ≈ 14 × 10)
        # makes tail points' peer lists redundant — they degrade to structure
        # counts (D3), so their operators are not expanded (this is what keeps
        # limit=100 affordable).
        expand_ops: set[str] = set()
        for pid in point_ids[:expand_top_k]:
            for (_et, _idx, op_id, _mech, _pred, _created) in point_ops.get(pid, []):
                expand_ops.add(op_id)

        # Q2-support: per-op-capped support endpoints (CALL subquery = per-op
        # LIMIT, no global sort) for the expanded operators. Contested peers
        # are already covered EXACTLY by Q2-crit — this fetch is pure filler.
        op_support: dict[str, dict[str, tuple]] = {}
        if expand_ops:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "CALL { WITH op MATCH (op)-[r2:IMPL|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "  AND NOT (op)-[:mitigated_by]->(other) "
                "RETURN r2, other LIMIT $per_op } "
                "RETURN op.id, type(r2) AS et, r2.idx AS other_idx, other.id, "
                "  other.pointKind, other.status, "
                "  coalesce(other.posterior_alpha, other.ep_alpha, 1.0), "
                "  coalesce(other.posterior_beta, other.ep_beta, 1.0), "
                # #2490: aligned has_ep gate (see op_crit) — support peers are
                # also terminal-gated so a terminal peer's decayed posterior
                # never reads as measured EP in the assembly. #3276: measured
                # excludes the #2199 baseline prior (see ep_measured_cypher).
                f"  ({ep_measured_cypher('other')} AND {_alive_flag('other.status')}), "
                "  other.createdAt, coalesce(other.outdated, false)",
                params={"op_ids": list(expand_ops), "per_op": per_op_cap},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_support.setdefault(row[0], {})[row[3]] = (
                    row[1], row[2], row[4] or "", row[5] or "",
                    float(row[6]), float(row[7]), bool(row[8]), row[9],
                    bool(row[10]),
                )

        # Unified endpoint store: critical classes first, then support filler
        # (same tuple shape — dict-merge dedupes).
        op_endpoints: dict[str, dict[str, tuple]] = {}
        for op_id in op_ids:
            merged: dict[str, tuple] = {}
            for src in (op_crit, op_support):
                merged.update(src.get(op_id, {}))
            op_endpoints[op_id] = merged

        # Q2f: family sizes per (op, edge-type) for ALL result operators.
        # Plain aggregate WITHOUT the mitigation-exclusion pattern (the NOT
        # pattern cost ~50ms here) — family is approximate disclosure and
        # mitigations are rare (their count inflates family by exactly the
        # separately-listed mitigations, documented). Covers tail points'
        # private operators so the D3 structure-count degradation is real.
        op_family: dict[tuple[str, str], int] = {}
        if op_ids:
            rows = graph.query(
                "MATCH (op:Point {is_operator:true}) WHERE op.id IN $op_ids "
                "WITH op "
                "MATCH (op)-[r2:IMPL|NAND|hasPart]-(other:Point) "
                "WHERE (other.is_operator = false OR other.is_operator IS NULL) "
                "RETURN op.id, type(r2) AS et, count(other)",
                params={"op_ids": list(op_ids)},
                timeout=_DECORATION_TIMEOUT_MS,
            ).result_set
            for row in rows:
                op_family[(row[0], row[1])] = int(row[2])

        # ── Assembly: class-aware, epistemically-prioritized ────────────────
        # Priority (D4): NAND > contested > superseded/retracted >
        # mitigated_by/CORRECTS > recency > deduped IMPL support-mass.
        global_support_used = 0
        for pid in point_ids:
            criticals: list[dict] = []
            support: list[dict] = []

            for (et, n_idx, op_id, mechanism, predicate, op_created) in point_ops.get(pid, []):  # noqa: B007
                for other_id, ep in op_endpoints.get(op_id, {}).items():
                    if other_id == pid:
                        continue  # self-peer exclusion
                    et2, other_idx, kind, status, alpha, beta, has_ep, created, outdated = ep  # noqa: RUF059
                    variance = _beta_variance(alpha, beta)
                    contested = has_ep and variance > CONTESTED_VARIANCE_THRESHOLD
                    # #2490: terminal peers (full live.py vocab OR the legacy
                    # outdated=true flag — a flag-outdated live-status peer is
                    # in no status-set member, only the fetched column
                    # expresses it) stay a critical class and never read as
                    # measured/contested EP.
                    terminal = is_terminal_status(status, outdated)
                    role = "source" if (n_idx is not None and n_idx == 0) else "target"
                    direction = "outgoing" if role == "source" else "incoming"
                    entry = {
                        "predicate": predicate if predicate else "",
                        "mechanism": mechanism,
                        "operator_id": op_id,
                        "related_id": other_id,
                        "related_kind": kind,
                        "direction": direction,
                        "role": role,
                        "peer": {
                            "id": other_id,
                            "kind": kind,
                            "status": status,
                            # Unmeasured (no persisted EP) peers carry None —
                            # never a fabricated Beta(1,1) posterior (0.5/0.0833
                            # would be internally contradictory: variance above
                            # the contested threshold but contested=false).
                            #
                            # #8504: a MEASURED peer whose persisted pair is
                            # (0.0, 0.0) has no posterior mean either, and the
                            # raw division here raised ZeroDivisionError
                            # mid-assembly — discarding every point assembled
                            # before it (#330's guard, missed at this site).
                            # `_beta_mean` carries that guard, and its uniform
                            # fallback (0.5) is the SAME value the sibling
                            # signal surface reports for the pair
                            # (StateRanker._fetch_point_signals,
                            # ranking.py:1046 → NEUTRAL_CONFIDENCE: measured
                            # on the write-path shape, which mirrors
                            # `confidence`; the GraphRanker sibling has no
                            # guard and is untouched by this read).
                            # It is not the fabricated posterior above: for this
                            # pair `_beta_variance`'s own s <= 0 guard yields
                            # variance 0.0, so contested stays False and nothing
                            # above the threshold is claimed.
                            "confidence": round(_beta_mean(alpha, beta), 4) if has_ep else None,
                            "variance": round(variance, 6) if has_ep else None,
                            "contested": contested,
                            "created_at": created,
                        },
                        "family_size": op_family.get((op_id, et2), 0),
                        "op_created_at": op_created,
                    }
                    if et2 == "NAND" or contested or terminal:
                        criticals.append(entry)
                    else:
                        support.append(entry)

                for m in op_mitigations.get(op_id, []):
                    criticals.append({
                        "predicate": "",
                        "mechanism": "mitigated_by",
                        "operator_id": op_id,
                        "related_id": m["id"],
                        "related_kind": "mitigation",
                        "direction": "incoming",
                        "role": "target",
                        "peer": {
                            "id": m["id"], "kind": "mitigation",
                            "status": m["status"], "confidence": None,
                            "variance": None, "contested": False,
                            "created_at": m["created_at"],
                        },
                        "family_size": 0,
                        "op_created_at": op_created,
                    })

            # CORRECTS entries (direct edges — no operator_id/family_size).
            for (old_id, old_status, old_created) in corrects_out.get(pid, []):
                criticals.append({
                    "predicate": "", "mechanism": "CORRECTS", "operator_id": "",
                    "related_id": old_id, "related_kind": "point",
                    "direction": "outgoing", "role": "source",
                    "peer": {"id": old_id, "kind": "point", "status": old_status or "",
                              "confidence": None, "variance": None,
                              "contested": False, "created_at": old_created},
                    "family_size": 0, "op_created_at": None,
                })
            for (new_id, new_status, new_created) in corrects_in.get(pid, []):
                criticals.append({
                    "predicate": "", "mechanism": "CORRECTS", "operator_id": "",
                    "related_id": new_id, "related_kind": "point",
                    "direction": "incoming", "role": "target",
                    "peer": {"id": new_id, "kind": "point", "status": new_status or "",
                              "confidence": None, "variance": None,
                              "contested": False, "created_at": new_created},
                    "family_size": 0, "op_created_at": None,
                })

            # Support-mass: recency-ordered (mixed-format safe), deduped, capped
            # by BOTH budgets. Criticals are exempt from the per-point cap —
            # they do NOT consume per-point room (D3: caps govern support only).
            support.sort(key=lambda e: _created_sort_key(e["peer"].get("created_at")), reverse=True)
            seen: set[tuple] = set()
            deduped: list[dict] = []
            for e in support:
                key = (e["operator_id"], e["mechanism"], e["related_id"])
                if key not in seen:
                    seen.add(key)
                    deduped.append(e)
            support = deduped

            entries = list(criticals)
            per_point_room = per_point_cap  # criticals are exempt — D3
            global_room = max(0, global_budget - global_support_used)
            keep = min(per_point_room, global_room, len(support))
            entries.extend(support[:keep])
            global_support_used += keep

            # Tail degradation (beyond expand_top_k, or all support capped away):
            # no peer entries shown → disclose family counts per mechanism so the
            # agent still knows the structural weight (D3). Points WITH peer
            # entries disclose their full family via per-entry family_size.
            if not any("peer" in e for e in entries) and point_ops.get(pid):
                # family per mechanism across this point's operators
                mech_family = Counter()
                for (et, _idx, op_id, mechanism, _pred, _created) in point_ops.get(pid, []):
                    mech_family[mechanism] += op_family.get((op_id, et), 0)
                crit_mech = Counter(e["mechanism"] for e in criticals)
                for mech, fam in mech_family.items():
                    leftover = fam - crit_mech.get(mech, 0)
                    if leftover > 0:
                        entries.append({"predicate": "", "mechanism": mech, "count": leftover})

            rels[pid] = entries
    except Exception as exc:
        # #6971: before this the seed (which the fill loop below mutates in
        # place, ``rels[pid] = entries``) was returned as if it were a real
        # (empty) decoration. Record WHY on the result so the caller can label
        # the batch instead of asserting "no relationships". Decoration
        # failure must not break retrieval (D1), so the exception is still
        # absorbed — it is just no longer indistinguishable from a genuinely
        # sparse point.
        #
        # The fill loop assigns point-by-point INSIDE this ``try``, so an
        # exception raised while assembling one point leaves every point
        # BEFORE it filled. Returning that partial mapping would make the
        # relayed marker assert "'relationships' is empty because the edges
        # could not be read" on hits that carry real entries — the payload
        # contradicting itself. Reset to the all-empty seed the marker's
        # wording (and D15) describes: "the read failed" replaces the whole
        # batch, it never leaves a half-decorated one.
        rels = RelationshipDecoration({pid: [] for pid in point_ids})
        rels.decoration_error = f"{type(exc).__name__}: {exc}"
        logger.warning("Bounded relationship query failed", exc_info=True)

    return rels


#: #4889 — the fail-loud reason for a structurally-empty ``SearchResult.subject``.
#:
#: ``subject`` is read from ``aboutSubject`` edges. When the graph has no
#: ``aboutSubject`` producer reachable, the field is silently *absent* on every
#: hit — indistinguishable from "this point has no subject". Measured
#: read-only 2026-09-23: the whole edge inventory of the dogfood graph
#: (37,535 Points) holds **0** ``aboutSubject`` edges and **1** ``:Subject``
#: node, because the capture entity spine stores SUBJECT-kind entities as
#: ``:Object`` (issue #4934) and the document-path Subject writer is ON by
#: default since #4938 — but it writes ``(document :Source)-[:aboutSubject]->
#: (:Subject)`` edges, which this probe deliberately does NOT count because
#: they cannot resolve a Point's ``subject`` field. Tracked producers for the
#: Point/Event-sourced shapes: #1370, #1509. The marker self-clears as soon as
#: any Point/Event-sourced ``aboutSubject`` edge exists.
SUBJECT_BINDING_UNAVAILABLE = (
    "aboutSubject has no reachable producer for Points or Events on this "
    "graph, so 'subject' is structurally empty rather than unknown: the "
    "capture entity spine writes SUBJECT-kind entities as :Object (#4934), "
    "and the document extractor's Subject writer — on by default since "
    "#4938 — writes document-Source-sourced edges only, which cannot "
    "resolve a Point's subject. "
    "Tracked producers: #1370, #1509."
)


#: The ``aboutSubject`` shapes ``fetch_point_epistemic_state`` actually reads:
#: a Point's own edge, or its source Event's edge (the ≤1-hop fallback). The
#: availability probe must be scoped to these — an ``Object``-sourced
#: ``aboutSubject`` edge (the GitHub connector writes
#: ``(o:Object)-[:aboutSubject]->(s:Subject)``) can never resolve the advertised
#: Point field, so an unscoped count would report "available" on a graph where
#: every Point hit still carries no subject.
#:
#: Written as a UNION ALL of two label-anchored counts, NOT the single
#: ``MATCH (n) … WHERE n:Point OR n:Event`` form: FalkorDB does not push a
#: disjunctive label test into the scan, so that form compiles to an ``All Node
#: Scan`` over the whole graph (~5.9 ms at 9k nodes) while this one is
#: Subject-anchored (~0.5 ms). Each leg aggregates without a grouping key, so
#: each yields exactly one integer row (0 when it matches nothing) and the probe
#: SUMS them.
_SUBJECT_SOURCE_SCOPED_PROBE = (
    "MATCH (n:Point)-[r:aboutSubject]->(:Subject) RETURN count(r) AS c "
    "UNION ALL "
    "MATCH (m:Event)-[r2:aboutSubject]->(:Subject) RETURN count(r2) AS c")


def subject_binding_available(graph) -> bool:
    """True when this graph can carry ``aboutSubject`` edges at all (#4889).

    Counts the Point- and Event-sourced ``aboutSubject`` edges
    ``fetch_point_epistemic_state`` reads, so an Object-sourced edge (the
    GitHub connector's shape) cannot make the marker lie (see
    ``_SUBJECT_SOURCE_SCOPED_PROBE``). The query is label-anchored and BOUNDED
    (``_DECORATION_TIMEOUT_MS``, the same bound the state fetch uses) — an
    unbounded full-graph scan on the hot search path is the defect this probe
    must not reintroduce.

    **Fail-OPEN**: a probe error, an empty result, or a non-integer row
    returns True. A broken probe must never invent an unavailability claim, so
    the failure direction that withholds the marker is the safe one.
    """
    try:
        rows = graph.query(_SUBJECT_SOURCE_SCOPED_PROBE,
                           timeout=_DECORATION_TIMEOUT_MS).result_set
        if not rows:
            # Each UNION ALL leg aggregates without a grouping key, so the
            # probe always yields rows; an empty result is anomalous and must
            # not be read as "no edges".
            logger.warning(
                "aboutSubject availability probe returned no rows — assuming "
                "available")
            return True
        total = 0
        for row in rows:
            if not row or row[0] is None:
                logger.warning(
                    "aboutSubject availability probe returned an anomalous "
                    "row (%r) — assuming available", row)
                return True
            total += int(row[0])
        return total > 0
    except Exception:  # fail-open, see docstring
        logger.warning(
            "aboutSubject availability probe failed — assuming available",
            exc_info=True)
        return True


def fetch_point_epistemic_state(graph, point_ids: list[str]) -> dict[str, dict]:
    """Fetch promoted epistemic state for a batch of Points (#1353 D8/D10).

    Returns {pid: {status, superseded_by, supersedes, subject}} where:
      status         — n.status (live/superseded/deprecated/retracted/draft)
      superseded_by  — {id, content_snippet, created_at} of the newest superseding
                       claim (incoming CORRECTS) or None
      supersedes     — [{id, content_snippet, created_at}] of replaced claims
                       (outgoing CORRECTS)
      subject        — {id, name, kind} from the point's OWN aboutSubject edge, or
                       (fallback) its source-event's aboutSubject edge — ≤1 hop only.
                       #1417: the event hop is resolved via the point's eventId
                       PROPERTY (the provenance surface), never via an aboutEvent
                       edge (that edge is content-aboutness, ONTOLOGY §3.4).
                       NEVER derived through operator chains (fail-closed, D10):
                       absent = honestly unknown, never wrong-via-chain.

    Chained OPTIONAL MATCHes can cartesian — deduped in Python. Rows are small
    (per-point CORRECTS/subject counts are low-volume).
    """
    if not point_ids:
        return {}

    out: dict[str, dict] = {}
    try:
        rows = graph.query(
            "MATCH (n:Point) WHERE n.id IN $ids "
            "OPTIONAL MATCH (n)-[:aboutSubject]->(s:Subject) "
            # #1417: provenance hop via the point's eventId property (the
            # provenance surface) — NOT an aboutEvent edge, which is reserved
            # for content-aboutness (ONTOLOGY §3.4). Legacy aboutEvent-only
            # points (no eventId property) no longer resolve — the fallback
            # now requires the eventId property (review P2).
            "OPTIONAL MATCH (ev:Event) WHERE ev.eventId = n.eventId "
            "OPTIONAL MATCH (ev)-[:aboutSubject]->(es:Subject) "
            "OPTIONAL MATCH (n)-[r:CORRECTS]->(old:Point) "
            "OPTIONAL MATCH (new:Point)-[r2:CORRECTS]->(n) "
            "RETURN n.id, n.status, "
            "  n.validFrom, n.validTo, n.expiredAt, "
            "  old.id, old.content, old.createdAt, "
            "  new.id, new.status, new.content, new.createdAt, "
            "  s.id, s.name, s.subjectKind, "
            "  es.id, es.name, es.subjectKind, "
            # #2294: the legacy flag half of the terminal vocabulary, appended
            # LAST so the 0-17 index mapping above is untouched. The reader
            # that EXCLUDES on it (``_terminal_excluded``) and the reader that
            # must MARK it (the ask lane's D8 gate / ``to_dict``) now read the
            # same fact.
            "  coalesce(n.outdated, false), "
            # #1423: the Subject's own lifecycle status, appended AFTER #2294's
            # index-18 flag so neither addition shifts the other's mapping.
            "  s.status, es.status, "
            # #2350 (E-prec): the per-end precision declaration, appended
            # LAST so every index above is untouched. Read into the additive
            # precision block by ``window_read_fields`` below.
            "  n.validFromPrecision, n.validToPrecision",
            params={"ids": point_ids},
            timeout=_DECORATION_TIMEOUT_MS,
        ).result_set

        for row in rows:
            pid = row[0]
            st = out.setdefault(pid, {
                "status": row[1] or "",
                "outdated": bool(row[18]) if len(row) > 18 else False,
                "superseded_by": None,
                "supersedes": [],
                "subject": None,
            })
            # E6 (#1538) D5/D7: promoted window fields — additive, absent
            # when undated (open window ⇒ no marker, byte-identical
            # rendering). Written by supersede_point/invalidate_point (D2).
            st.setdefault("valid_from", row[2] or None)
            st.setdefault("valid_to", row[3] or None)
            st.setdefault("expired_at", row[4] or None)
            # #2350 (E-prec): the additive precision block, resolved by the ONE
            # read-projection home so search/recall annotation cannot drift
            # from ``get_historical_knowledge``/``assembly``. The bounds are
            # passed RAW: ``window_read_fields``' presence gate is
            # ``is not None``, so a falsey-but-present bound — ``""``, ``0`` —
            # is a REAL bound and its key is still emitted. Since #9201 a bound
            # that cannot be ordered reports ``None`` on BOTH ends — and since
            # #9244 that holds for a caller-DECLARED token too: a declaration
            # ANNOTATES a bound, it cannot summon one, so a ``year`` declared on
            # ``"TBD"`` reads ``None`` rather than resurrecting a start the
            # #6140 ruling reads as never recorded (the write boundary still
            # STORES the pair; refusing it there is the owner's contract call
            # #9244 leaves open). That is how this surface stops disagreeing
            # with the #6140 start gate in ``sdk._covers`` (an unorderable start
            # is an OPEN start there): neither derives a precision from a value
            # it cannot order.
            # Pre-normalizing with
            # ``or None`` here made the same stored window read ``day`` in
            # assembly / ``get_historical_knowledge`` and ``None`` here — the
            # exact cross-surface drift this module exists to prevent.
            _wf = window_read_fields(
                row[2], row[3],
                row[21] if len(row) > 21 else None,
                row[22] if len(row) > 22 else None,
            )
            st.setdefault("valid_from_precision", _wf["valid_from_precision"])
            st.setdefault("valid_to_precision", _wf["valid_to_precision"])
            st.setdefault("end_state", _wf["end_state"])
            # #9201: each end's presence, carried separately from its
            # precision. A present-but-unorderable bound resolves to precision
            # ``None``, so the resolved precision can no longer double as "this
            # point has that bound" — the surface's emit gate would then drop
            # the key here only (the #2350 review-P2 drift, now on both ends).
            # The raw column is the presence datum; ``row[2] or None`` below
            # normalises a falsey bound for the promoted WINDOW field only.
            st.setdefault("valid_from_present", row[2] is not None)
            st.setdefault("valid_to_present", row[3] is not None)
            old_id, old_content, old_created = row[5], row[6], row[7]
            new_id, new_status, new_content, new_created = row[8], row[9], row[10], row[11]
            if old_id and old_id not in {s["id"] for s in st["supersedes"]}:
                st["supersedes"].append({
                    "id": old_id,
                    "content_snippet": (old_content or "")[:120],
                    "created_at": old_created,
                })
            if new_id:
                cand = {
                    "id": new_id,
                    "content_snippet": (new_content or "")[:120],
                    "created_at": new_created,
                }
                # A retracted correcting point is NOT superseding authority.
                if new_status == "retracted":
                    pass
                elif st["superseded_by"] is None or _created_sort_key(new_created) > _created_sort_key(
                        st["superseded_by"].get("created_at")):
                    st["superseded_by"] = cand
            # Subject: own aboutSubject wins; event's is the ≤1-hop fallback.
            # #1423: the Subject's own `status` rides the decoration so a
            # consumer can see that the subject has since been superseded
            # WITHOUT the point's provenance disappearing — the ONTOLOGY
            # §4.2/§12 doctrine is `superseded, never deleted`, and a point
            # about a reorganized team remains a true record of that turn.
            # Additive: the key is present on every row ("" when unset), so
            # existing consumers reading id/name/kind are unaffected.
            s_id, s_name, s_kind = row[12], row[13], row[14]
            es_id, es_name, es_kind = row[15], row[16], row[17]
            s_status = row[19] if len(row) > 19 else ""
            es_status = row[20] if len(row) > 20 else ""
            if st["subject"] is None and s_id:
                st["subject"] = {"id": s_id, "name": s_name or "",
                                 "kind": s_kind or "",
                                 "status": s_status or ""}
            elif st["subject"] is None and es_id:
                st["subject"] = {"id": es_id, "name": es_name or "",
                                 "kind": es_kind or "",
                                 "status": es_status or ""}
    except Exception:
        logger.warning("Epistemic-state fetch failed", exc_info=True)

    return out


# ── TF-IDF fallback (in-memory, from embeddings.py) ─────────────────────────

# ── Relationship / Traversal filters (#7846) ──────────────────────────────────

def filter_by_relationship(
    graph,
    point_ids: list[str],
    predicate: str,
    target_id: str,
    entity_type: str = "point",
    id_field: str = "id",
) -> list[str]:
    """Post-filter: keep only points connected to target_id via operator with label=predicate.

    Operators are middle entities — Product→(op:contains)→Feature = 2 graph hops.
    Traversal: point <-[IMPL]-(op {label:predicate})-[IMPL]-> target.
    Returns filtered list of point IDs (subset of input).
    """
    if not point_ids or not predicate or not target_id:
        return []
    # Relationship filters only make sense for Point-backed entities — the chain
    # always traverses operator edges, which only connect Points. Bail early
    # with a warning instead of silently returning empty for unsupported types.
    if entity_type not in ("point", "operator"):
        logger.warning("Relationship filters not supported for entity_type=%s", entity_type)
        return []
    try:
        # Operators are Points with is_operator=true — no Operator label exists (#148).
        # Map both point and operator to the Point label, mirroring
        # run_fts_query / run_structural_query label logic.
        label = "Point"
        is_operator_clause = " AND n.is_operator = true" if entity_type == "operator" else ""
        cypher = (
            # #6976: the id predicate is DROPPED by FalkorDB 6.0.0 when a later
            # plain MATCH re-binds `n` as a pattern endpoint — the query then
            # returns OTHER points' rows, a silent filter BYPASS rather than an
            # empty read (measured: 5 rows, 4 with foreign ids). The `WITH n`
            # below is LOAD-BEARING: it keeps the predicate bound. Do not remove.
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids{is_operator_clause} "
            f"WITH n "
            f"MATCH (n)<-[r1:hasPart|IMPL|NAND]-(op:Point {{is_operator:true, label:$pred}})"
            f"-[r2:hasPart|IMPL|NAND]->(t:{label} {{{id_field}: $tid}}) "
            f"RETURN DISTINCT n.{id_field}"
        )
        rows = graph.query(
            cypher,
            params={"ids": point_ids, "pred": predicate, "tid": target_id},
        ).result_set
        return [row[0] for row in rows]
    except Exception:
        logger.warning("Relationship filter failed — returning empty", exc_info=True)
        return []


def filter_by_traversal_predicate(
    graph,
    point_ids: list[str],
    predicate: str,
    entity_type: str = "point",
    id_field: str = "id",
) -> list[str]:
    """Post-filter: keep only points that participate in ANY operator with label=predicate.

    Points are matched if they are either the source or target of an operator
    carrying the given predicate label. Returns filtered list of point IDs.
    """
    if not point_ids or not predicate:
        return []
    # Traversal predicate filters only make sense for Point-backed entities —
    # the chain always traverses operator edges, which only connect Points.
    # Bail early with a warning instead of silently returning empty for
    # unsupported types.
    if entity_type not in ("point", "operator"):
        logger.warning(
            "Traversal predicate filters not supported for entity_type=%s", entity_type
        )
        return []
    try:
        # Operators are Points with is_operator=true — no Operator label exists (#148).
        # Map both point and operator to the Point label, mirroring
        # run_fts_query / run_structural_query label logic.
        label = "Point"
        is_operator_clause = " AND n.is_operator = true" if entity_type == "operator" else ""
        cypher = (
            # #6976: load-bearing `WITH n` — without it FalkorDB 6.0.0 drops the id
            # predicate at the re-binding MATCH below and the function returns
            # FOREIGN rows (measured: 170 rows, 169 foreign).
            f"MATCH (n:{label}) WHERE n.{id_field} IN $ids{is_operator_clause} "
            f"WITH n "
            f"MATCH (n)<-[r:hasPart|IMPL|NAND]-(op:Point {{is_operator:true, label:$pred}}) "
            f"RETURN DISTINCT n.{id_field}"
        )
        rows = graph.query(
            cypher,
            params={"ids": point_ids, "pred": predicate},
        ).result_set
        return [row[0] for row in rows]
    except Exception:
        logger.warning("Traversal predicate filter failed — returning empty", exc_info=True)
        return []


def fallback_tfidf(query: str, points: list[dict], limit: int = 10,
                   *, force_sparse: bool = False) -> list[dict]:
    """Last-resort TF-IDF fallback when all FalkorDB strategies fail.

    points should be dicts with at least 'id', 'content', 'pointKind'.

    R2 (#1541) D4: the indexed text is ``index_text(content, search_keys)``
    — content ∪ search_keys — the embedded-stack counterpart of the real
    backend's multi-field Point FTS index (surface-11 normalization is a
    shared code path via tortoise.sparse, not a magic parity layer). Points
    dicts from ``self.query`` carry ``search_keys`` when present (additive;
    absent → byte-identical to the pre-R2 path). The returned payload keeps
    the REAL content (the alias text is index-only, never surfaced).

    #2678: ``force_sparse=True`` forwards to ``search_points`` so the caller
    gets the DETERMINISTIC token-overlap scorer and the ``match_source="tfidf"``
    label is true even when an embedder is cached. The default (``False``) is
    the production contract and is UNCHANGED — ``search_points`` resolves the
    embedder and the #5275 armed refusal is raised while it does.

    #5275: under ``TORTOISE_EMBEDDING_MODEL_REQUIRED=1`` this function
    PROPAGATES :class:`~tortoise.exceptions.EmbedderUnavailableError` instead
    of logging it into ``[]`` — an empty result is a read result too, and the
    quietest possible outcome is indistinguishable from "nothing matched".
    With the flag unset the historical degrade is unchanged.
    """
    from .exceptions import EmbedderUnavailableError
    try:
        from tortoise.embeddings import search_points

        from .sparse import index_text
        meta = {p["id"]: p for p in points if p.get("id")}
        # R2: alias text is index-only — restore the real content on the
        # results (search_points echoes the dicts it was handed).
        real_content = {p["id"]: p.get("content", "") for p in points if p.get("id")}
        indexed = [
            {**p, "content": index_text(p.get("content"), p.get("search_keys"))}
            for p in points
        ]
        results = search_points(query, indexed, threshold=0.0, limit=limit,
                                force_sparse=force_sparse)
        for r in results:
            r["content"] = real_content.get(r["id"], r["content"])
        return [
            SearchResult(
                id=r["id"],
                content=r["content"],
                point_kind=meta.get(r["id"], {}).get("pointKind", ""),
                scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
                match_source="tfidf",
                ep=None,
                # A5 (#2070): the ask-lane evidence-boost mark rides the
                # embedded fallback hits too (``self.query`` nodes carry
                # ``has_answer``; absent = False).
                has_answer=bool(meta.get(r["id"], {}).get("has_answer")),
                # ⛔ No ``source_ref``/``captured_at`` here — this tier is a
                # no-op for TORTOISE_SEARCH_PROVENANCE; see the KNOWN
                # LIMITATION note on ``search_provenance_enabled`` above.
            ).to_dict()
            for r in results
        ]
    except EmbedderUnavailableError:
        # #5275: this tier's rows ARE a read result, so a REQUIRED process
        # must not be handed the quietest possible outcome — an empty list.
        # ``search_points`` resolves the embedder without a handler, so under
        # ``TORTOISE_EMBEDDING_MODEL_REQUIRED=1`` the named refusal lands here;
        # re-raise it ahead of the generic handler instead of logging it into
        # ``[]`` (which is indistinguishable from "nothing matched").
        raise
    except Exception as e:
        logger.error("TF-IDF fallback failed: %s", e)
        return []
