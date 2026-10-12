"""Reconciliation of the second divergence surface — Postgres (truth) ↔ FalkorDB (cache).

**§17.2 / #7952, epic #7879.** ``STORAGE-ARCHITECTURE.md`` §3 covers ONE
divergence surface — journal ↔ projection — with five mechanisms. The 2026-10-08
hybrid ruling (§2.0) adds a SECOND: the record is the truth and the graph is a
derived cache, so **``truth == cache`` must be checkable, not assumed**. This
module is that check.

**WHY A NUMBER AND NOT A BOOLEAN.** A boolean answer is strictly weaker in the
one direction that matters: ``False`` cannot distinguish *"the cache is behind
the record"* (a projection lag, cheap to repair) from *"the cache holds a node
the record does not"* (a **second writer** — the chokepoint §17.1/#7951 exists
to forbid, and the signature that the cache is no longer derived). It also
cannot distinguish either from "nothing was compared". Divergence is therefore
reported as **integer counts**, and the counts are separated by direction:

``checked``           node keys present in either side (the denominator)
``missing_in_cache``  in the record, absent from the cache  → the cache is behind
``extra_in_cache``    in the cache, absent from the record  → ⭐ a chokepoint
                      violation: something wrote the cache directly
``field_mismatches``  present on both sides, value differs   → a stale value
``divergent``         node keys differing in any of the three ways above

**FAIL-CLOSED, because a reconciliation that cannot see both sides is not a
reconciliation.** :func:`reconcile` raises :class:`ReconciliationUnavailable`
when either side cannot be read, rather than returning a zero-divergence
report: an unreadable store reported as ``divergent == 0`` is the #1382 class —
a gate that cannot read its evidence reporting success. Callers that hold plain
views in hand use :func:`reconcile_views`, which has no such state.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

#: How many divergent keys a report carries verbatim. The COUNTS are the
#: contract; the sample exists so an operator can look, and is bounded so a
#: wholesale divergence does not build an unbounded payload.
SAMPLE_LIMIT = 20


class ReconciliationUnavailable(RuntimeError):
    """A side of the comparison could not be read — divergence is UNKNOWN.

    Raised rather than reported, so that "the record is unreadable" can never
    be rendered as "the two stores agree" (the #1382 class).
    """


def _canonical(value: object) -> object:
    """A comparable form for one property value.

    ``dict`` keys and ``set`` members have no order, so both are sorted; tuples
    and lists are compared by position. Numbers are compared by exact value —
    a tolerant comparator is a separate decision with its own recorded bounds
    (``tortoise/consistency.py``'s ``_values_equal`` is the precedent for the
    journal half), and silently adopting a tolerance here would hide small
    drifts this check exists to surface.
    """
    if isinstance(value, dict):
        return tuple(sorted((str(k), _canonical(v)) for k, v in value.items()))
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(repr(_canonical(v)) for v in value))
    if isinstance(value, (list, tuple)):
        return tuple(_canonical(v) for v in value)
    return value


@dataclass(frozen=True)
class ReconciliationReport:
    """One reconciliation result. Every field but ``divergent_keys`` is a COUNT.

    There is deliberately no ``coherent`` / ``ok`` boolean: the decision to
    call a divergence acceptable (and at what threshold) belongs to the caller,
    and a convenience boolean is how a directional count stops being read.
    """

    checked: int
    divergent: int
    missing_in_cache: int
    extra_in_cache: int
    field_mismatches: int
    divergent_keys: tuple[str, ...] = ()
    truth_side: str = "record"
    cache_side: str = "cache"

    def as_dict(self) -> dict:
        """The machine-readable shape (JSON-safe — tuples become lists)."""
        return {
            "truth_side": self.truth_side,
            "cache_side": self.cache_side,
            "checked": self.checked,
            "divergent": self.divergent,
            "missing_in_cache": self.missing_in_cache,
            "extra_in_cache": self.extra_in_cache,
            "field_mismatches": self.field_mismatches,
            "divergent_keys": list(self.divergent_keys),
        }

    def render(self) -> str:
        """A one-line operator summary — divergence as a NUMBER, in context."""
        return (
            f"{self.truth_side} vs {self.cache_side}: "
            f"{self.divergent} divergent of {self.checked} checked "
            f"(missing_in_cache={self.missing_in_cache}, "
            f"extra_in_cache={self.extra_in_cache}, "
            f"field_mismatches={self.field_mismatches})"
        )


def reconcile_views(truth: Mapping[str, Mapping], cache: Mapping[str, Mapping],
                    *, sample_limit: int = SAMPLE_LIMIT,
                    truth_side: str = "record",
                    cache_side: str = "cache") -> ReconciliationReport:
    """Compare two keyed views of the same scope and COUNT the divergences.

    Both sides are ``{key: {property: value}}``. A missing side of a key is a
    divergence, not an absence to be skipped — that is the entire point (an
    unpresent key on one side is exactly what a second writer produces).

    A per-key value that is NOT a property mapping is compared WHOLE (the same
    comparison :func:`divergent_key_count` uses) rather than coerced to ``{}``:
    the coercion made two differing values — or a ``None``-valued property
    against an absent one — compare equal and report agreement, an impossible
    comparison reported as success (the #1382 class :func:`reconcile` refuses
    at the top level). ``field_mismatches`` counts such a value as ONE field.

    ``sample_limit`` bounds only the reported key sample; the counts are exact.
    """
    if sample_limit < 0:
        raise ValueError(f"sample_limit must be >= 0, got {sample_limit}")
    truth_keys = set(truth)
    cache_keys = set(cache)
    checked = len(truth_keys | cache_keys)

    missing_in_cache = 0
    extra_in_cache = 0
    field_mismatches = 0
    value_divergent = 0
    divergent_keys: list[str] = []

    def _sample(key: object) -> None:
        if len(divergent_keys) < sample_limit:
            divergent_keys.append(str(key))

    for key in truth_keys - cache_keys:
        missing_in_cache += 1
        _sample(key)
    for key in cache_keys - truth_keys:
        extra_in_cache += 1
        _sample(key)
    for key in truth_keys & cache_keys:
        truth_props = truth[key]
        cache_props = cache[key]
        if isinstance(truth_props, Mapping) and isinstance(cache_props, Mapping):
            # Field-by-field, and PRESENCE-AWARE: a property present with
            # value ``None`` on one side and absent on the other is a
            # divergence (`set(keys)` differs), not `None == None`.
            for prop in set(truth_props) | set(cache_props):
                if (prop not in truth_props or prop not in cache_props
                        or _canonical(truth_props[prop])
                        != _canonical(cache_props[prop])):
                    field_mismatches += 1
        elif not _equal_view(truth_props, cache_props):
            # An uncomparable per-key value cannot be counted field by field;
            # the whole value is one mismatch.
            field_mismatches += 1
        # Divergence is decided by the SAME comparison the focused count uses,
        # so `reconcile_views` and `divergent_key_count` cannot drift apart.
        if not _equal_view(truth_props, cache_props):
            value_divergent += 1
            _sample(key)

    return ReconciliationReport(
        checked=checked,
        divergent=missing_in_cache + extra_in_cache + value_divergent,
        missing_in_cache=missing_in_cache,
        extra_in_cache=extra_in_cache,
        field_mismatches=field_mismatches,
        divergent_keys=tuple(divergent_keys),
        truth_side=truth_side,
        cache_side=cache_side,
    )


def _equal_view(a: object, b: object) -> bool:
    """Whole-view equality, on the same canonical form ``reconcile_views`` uses."""
    if not isinstance(a, Mapping) or not isinstance(b, Mapping):
        return a == b
    if set(a) != set(b):
        return False
    return all(_canonical(a[k]) == _canonical(b[k]) for k in a)


def reconcile(record_reader: Callable[[], Mapping[str, Mapping]],
              cache_reader: Callable[[], Mapping[str, Mapping]],
              *, sample_limit: int = SAMPLE_LIMIT,
              truth_side: str = "record",
              cache_side: str = "cache") -> ReconciliationReport:
    """Read BOTH sides and reconcile them — or raise.

    The two readers are the seam: until the record's own read path exists
    (#7970) and the cache is projected one way from it (#7971), the callers are
    free to supply any keyed view of the same scope. A reader that raises, or
    that returns something which is not a mapping, makes the comparison
    impossible — and an impossible comparison must not be reported as
    agreement.
    """
    def _read(reader: Callable[[], Mapping[str, Mapping]], side: str):
        try:
            view = reader()
        except Exception as e:
            raise ReconciliationUnavailable(
                f"{side} is unreadable ({type(e).__name__}: {e}) — divergence "
                f"is UNKNOWN, not zero") from e
        if not isinstance(view, Mapping):
            raise ReconciliationUnavailable(
                f"{side} did not return a keyed view "
                f"({type(view).__name__}) — divergence is UNKNOWN, not zero")
        return view

    truth = _read(record_reader, truth_side)
    cache = _read(cache_reader, cache_side)
    return reconcile_views(
        truth, cache, sample_limit=sample_limit,
        truth_side=truth_side, cache_side=cache_side)


def divergent_key_count(keys: Iterable[str], truth: Mapping[str, Mapping],
                        cache: Mapping[str, Mapping]) -> int:
    """A focused count over a NAMED key set — the continuous-check primitive.

    A full reconciliation over a large scope is expensive; a continuous check
    normally watches a declared sample (the nodes a write just touched). This
    counts how many of ``keys`` disagree, on the same canonical comparison, so
    the sampled check and the full one cannot drift apart.
    """
    count = 0
    for key in keys:
        if key not in truth or key not in cache or not _equal_view(truth[key], cache[key]):
            count += 1
    return count
