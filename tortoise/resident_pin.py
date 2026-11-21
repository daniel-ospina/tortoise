"""*Keep this resident* — the user's explicit pin, and its BOUND (#14012).

WHY THIS EXISTS
---------------
The working-set window (`docs/architecture/SEARCH-ARCHITECTURE.md` §7) demotes what
nobody searches for. That is the right default and it is what makes the pricing work
— but it leaves the customer with no answer to *"I search for **this** every day"*
when the scorer disagrees. The owner-approved capacity notices (`#13998`) and the
public docs page they link to (`#14013`) promise exactly one answer:

    **Keep this resident.** The things you rely on can be kept in the fast layer,
    together with the things connected to them, so they do not have to be fetched
    back when you return to them. Keeping things resident has a limit, so it can
    never take over the fast layer.

Two sentences, and every mechanism here exists for one of them:

* *"kept in the fast layer, **together with the things connected to them**"* — the
  unit of residency is a CLOSED neighbourhood, not a node (§7: *"A pinned hub whose
  neighbours were evicted pays for the node and still cannot complete the walk"*). So
  a pin resolves to its 1-hop closure and the **closure** is what the eviction gate
  holds back. Storing the closure would be a second bookkeeping copy that goes stale
  the moment an edge is added, so the closure is DERIVED from the graph on every read.
* *"has a limit, so it can never take over the fast layer"* — the budget. Pinning is
  **capped**, and the cap is enforced in two places: :func:`pin` refuses a declaration
  that would exceed it, and :func:`resolve` reports a set that already exceeds it so
  the sweep can refuse rather than either overshoot silently or drop the user's
  declaration silently.

⛔ WHAT THIS IS NOT, AND WHY EACH ONE IS LOAD-BEARING
-----------------------------------------------------
* **Not a tag system.** One declaration, one budget, no labels, no hierarchies.
* **Not a per-user control.** The storage shape is per ACCOUNT (`#11758`): a pin set
  is graph-scoped, and a pin is not an escape from the account's allowance.
* **Not survival.** §2.0: the record keeps everything and the graph is a derived hot
  cache. A pin buys *speed*, never *retention* — and it is cache state, so a
  `rebuild_from_record` re-materialises the graph without the pins and this module is
  re-applied from the declaration it reads. The declaration lives in the graph's keyed
  ``:Meta`` node (the ``config_reset`` / `residency.declare_residency` idiom), which is
  operator metadata: no ``Point``, no edge, no EP weight.
* **Not a score.** The pin does not re-rank anything; it is an exclusion the evictor
  applies *before* the score can take a node (§7's policy: an unconditional pin is a
  measured anti-pattern, so this is a small, capped, explicit set — never the ordering).

THE DEGREE CEILING, AND WHY A HUB IS REFUSED RATHER THAN PINNED
---------------------------------------------------------------
§7 records the measurement: unconditional hub pinning scored **62.4 % miss against LRU's
60.2 %**, and one huge hub can eat a 77 MB window. A pin's closure is what makes it
useful *and* what makes a hub dangerous — so a key whose own degree exceeds
:data:`DEFAULT_MAX_DEGREE` is refused with its degree named. The refusal is per KEY and
the caller may pin the neighbours it actually needs instead.

⚠️ **The degree check is a DECLARATION-time guard; the CLOSURE BUDGET is the invariant,
and that one is re-checked on every read.** A node that gains neighbours after it is
pinned therefore does not silently become unbounded: the closure it now pulls in is
measured against the budget on the next :func:`read` (:meth:`PinReading.over_budget`),
and a set that no longer fits refuses the sweep. Re-checking degree itself would refuse a
pin for growing, which is not what §7's ceiling is about — it exists to stop one
declaration FROM pulling in a neighbourhood the size of the window.

⚠️ **THE FAILURE DIRECTION IS ASYMMETRIC ON PURPOSE, AND THE TWO HALVES POINT OPPOSITE
WAYS.** A pin set that cannot be READ must stop a sweep (:attr:`PinReading.readable`
``False`` — a sweep that cannot tell what is pinned must not delete), while a pin set
that cannot be read must NOT block a WRITE. Those are the same fact answering two
questions, which is why the reader reports the state and the two callers refuse in
their own direction rather than this module choosing one for both.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .cache_evictor import EVICTION_MAX_NODES_ENV, Candidate

#: Where the declaration lives: a keyed ``:Meta`` node, the `residency.py` idiom.
PINS_META_KEY = "pins"

#: The pin budget — how many fast-layer slots pins may claim, CLOSURE INCLUDED.
#:
#: ⛔ ABSENT IS NOT "UNBOUNDED", and it is not a refusal either: the default is a
#: FRACTION of the window the operator already declared (:data:`DEFAULT_SHARE_OF_WINDOW`),
#: so the bound is derived from the one number that defines the cache rather than
#: invented here. An explicit value is accepted only when it is *strictly smaller* than
#: the window — a pin budget equal to the window is "no eviction", which is the state
#: the docs page promises can never happen.
PIN_MAX_NODES_ENV = "TORTOISE_PIN_MAX_NODES"

#: The degree ceiling — a key with more neighbours than this is refused (§7).
PIN_MAX_DEGREE_ENV = "TORTOISE_PIN_MAX_DEGREE"

#: What a pin may claim, as a share of the residency window. One tenth: large enough
#: that a working set of a few hundred nodes is expressible at any realistic window,
#: small enough that the pinned closure cannot crowd out the working set it exists to
#: protect. ⚠️ It is a BOUND, not a target — the cap is what the promise needs.
DEFAULT_SHARE_OF_WINDOW = 10

#: §7's degree ceiling. A hub above this is refused rather than pinned: the measured
#: anti-pattern is exactly a pin whose closure dominates the window.
DEFAULT_MAX_DEGREE = 32

#: How many resident keys one :func:`closure` read may ask the graph for. The window is
#: bounded by construction, so this is a runaway guard, not a tuning knob — and hitting
#: it is REPORTED (:attr:`PinReading.truncated`) rather than silently shortening the set.
DEFAULT_KEY_LIMIT = 50_000

#: The bare-identifier shape a label or key property must have before it is interpolated
#: into Cypher. The census's own strict rule, restated for the same reason: `str.isidentifier`
#: accepts Unicode identifiers the engine reads differently, and a trailing newline.
_BARE_SAFE = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_"
)


class PinConfigError(RuntimeError):
    """A budget that cannot be honoured — a REFUSAL, never a widened default."""


class PinRefused(RuntimeError):
    """A declaration the budget will not carry — the cap's DECLARED refusal.

    ⛔ The cap must refuse, not trim. A pin that silently became "most of what you
    asked for" is the user's declaration misreported as honoured, and the docs page
    promises the opposite ("it has a limit") — a limit nobody is ever told about.
    """

    def __init__(self, reason: str, *, code: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.code = code


def bare_identifier(value: str, *, what: str) -> str:
    """Validate an identifier before it is interpolated into Cypher.

    Public because it is the ONE shape check for a value this package interpolates:
    `outside_view` reads a label and a key property the same way, and a second copy of
    this predicate is a second thing to get wrong (the census and `cache_evictor` each
    carry their own, pre-existing, for their own interpolations).
    """
    if not isinstance(value, str) or not value or not set(value) <= _BARE_SAFE:
        raise PinConfigError(
            f"{what} {value!r} is not a bare Cypher identifier ([A-Za-z_][A-Za-z0-9_]*) "
            f"— it is interpolated into the pin query, so it may not carry quotes, "
            f"spaces or punctuation"
        )
    if value[0].isdigit():
        raise PinConfigError(
            f"{what} {value!r} starts with a digit, so it is not a bare Cypher identifier"
        )
    return value


@dataclass(frozen=True)
class PinBudget:
    """How much of the fast layer pins may claim, and how large a hub may be.

    ``max_nodes`` bounds the CLOSURE (the pinned nodes plus their 1-hop neighbours),
    because the closure is what actually occupies the window — bounding the declared
    count alone would let a single pin resolve to an arbitrary footprint.
    """

    max_nodes: int
    max_degree: int = DEFAULT_MAX_DEGREE
    window_nodes: int | None = None
    source: str = "declared"

    def __post_init__(self) -> None:
        for name, value in (("max_nodes", self.max_nodes),
                            ("max_degree", self.max_degree)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise PinConfigError(f"{name} must be an integer, got {value!r}")
            if value < 1:
                raise PinConfigError(
                    f"{name} {value} is below its floor of 1 — a budget of zero "
                    f"refuses every pin, which is a disabled control, not a budget"
                )
        # ⛔ STRICTLY SMALLER, not "<=": a pin budget equal to the window means the
        # pinned closure may fill the cache entirely, i.e. "no eviction" — the one
        # outcome the docs page promises cannot happen.
        if self.window_nodes is not None and self.max_nodes >= self.window_nodes:
            raise PinConfigError(
                f"max_nodes {self.max_nodes} is not smaller than the residency window "
                f"({self.window_nodes}) — pins could then claim the whole fast layer, "
                f"which is 'no eviction' by another name. Pin at most "
                f"{max(1, self.window_nodes - 1)} node(s)."
            )

    @classmethod
    def resolve(
        cls,
        *,
        max_nodes: int | None = None,
        max_degree: int | None = None,
        window_nodes: int | None = None,
    ) -> PinBudget:
        """Read the budget from the arguments, then the environment, then the window.

        Every branch either produces a valid budget or raises. There is no path that
        widens it, and none that silently disables the cap.
        """
        window = window_nodes
        if window is None:
            raw_window = os.environ.get(EVICTION_MAX_NODES_ENV)
            if raw_window is not None and raw_window.strip():
                try:
                    window = int(raw_window)
                except ValueError as exc:
                    raise PinConfigError(
                        f"{EVICTION_MAX_NODES_ENV}={raw_window!r} is not an integer "
                        f"node count, so the pin budget's own share cannot be derived"
                    ) from exc

        declared = max_nodes
        source = "declared"
        if declared is None:
            raw = os.environ.get(PIN_MAX_NODES_ENV)
            if raw is not None and raw.strip():
                try:
                    declared = int(raw)
                except ValueError as exc:
                    raise PinConfigError(
                        f"{PIN_MAX_NODES_ENV}={raw!r} is not an integer node count"
                    ) from exc
                source = f"env:{PIN_MAX_NODES_ENV}"
            elif window is not None:
                # A tenth of the window, floored at 1 so a tiny window still permits one
                # pin — and never >= the window (checked in `__post_init__`).
                declared = max(1, window // DEFAULT_SHARE_OF_WINDOW)
                source = (
                    f"derived:1/{DEFAULT_SHARE_OF_WINDOW} of "
                    f"{EVICTION_MAX_NODES_ENV}={window}"
                )
            else:
                raise PinConfigError(
                    f"no pin budget can be derived: set {PIN_MAX_NODES_ENV} (or pass "
                    f"max_nodes=), or declare the residency window via "
                    f"{EVICTION_MAX_NODES_ENV} — with neither, a pin would be an "
                    f"unbounded claim on the fast layer, which is the one thing the "
                    f"control promises it can never be"
                )

        degree = max_degree
        if degree is None:
            raw_degree = os.environ.get(PIN_MAX_DEGREE_ENV)
            if raw_degree is None or not raw_degree.strip():
                degree = DEFAULT_MAX_DEGREE
            else:
                try:
                    degree = int(raw_degree)
                except ValueError as exc:
                    raise PinConfigError(
                        f"{PIN_MAX_DEGREE_ENV}={raw_degree!r} is not an integer degree"
                    ) from exc

        return cls(max_nodes=declared, max_degree=degree,
                   window_nodes=window, source=source)

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_nodes": self.max_nodes,
            "max_degree": self.max_degree,
            "window_nodes": self.window_nodes,
            "source": self.source,
        }

    def explain(self) -> str:
        """The cap, in the words a refusal or a report carries."""
        if self.window_nodes is not None:
            return (
                f"a pin may claim at most {self.max_nodes} fast-layer slot(s) "
                f"(closure included) of the {self.window_nodes}-node window"
            )
        return f"a pin may claim at most {self.max_nodes} fast-layer slot(s)"


@dataclass(frozen=True)
class PinReading:
    """What the graph says is pinned — DERIVED, never authoritative.

    ``keys``      the keys the user declared (the declaration itself).
    ``closure``   the keys that must therefore stay resident: ``keys`` plus their
                  1-hop neighbours, recomputed from the graph on every read.
    ``degrees``   per declared key, how many distinct neighbours it has.
    ``missing``   declared keys the graph does not currently hold. Not an error: a
                  pinned node may be outside the graph right now (it was demoted before
                  the pin, or the graph was rebuilt) — the declaration stays, and saying
                  so beats pretending the pin now holds something it does not.
    ``readable``  ``False`` when the declaration or the closure could not be read. A
                  sweep that cannot tell what is pinned must REFUSE (:func:`gate_reason`),
                  never assume an empty set.
    ``truncated`` ``True`` when the key read hit :data:`DEFAULT_KEY_LIMIT`. The closure is
                  then a PARTIAL one, and its size is a lower bound — reported, never
                  silently used as if complete.
    """

    label: str
    key_prop: str
    keys: frozenset[str] = frozenset()
    closure: frozenset[str] = frozenset()
    degrees: dict[str, int] = field(default_factory=dict)
    missing: frozenset[str] = frozenset()
    readable: bool = True
    truncated: bool = False
    source: str | None = None
    budget: PinBudget | None = None

    # ── the budget question, answered by STATE rather than by the writer ──

    @property
    def closure_size(self) -> int:
        return len(self.closure)

    def over_budget(self) -> str | None:
        """Why this pin set may not be honoured, or ``None`` when it may.

        ⛔ This is the SECOND half of the cap, and it exists because the first half
        (:func:`pin`) only refuses at DECLARATION time. A budget lowered after the fact,
        or a closure that grew as edges were added, reaches the same state without any
        declaration being refused — and the two "obvious" responses are both wrong:
        evicting a pinned node silently drops the user's declaration, and honouring an
        over-budget one lets pins take over the fast layer. So it is a REFUSAL that
        names both ways out.
        """
        if self.budget is None:
            return None
        if self.truncated:
            return (
                "the pin set's closure could not be read in full (the key read hit its "
                f"{DEFAULT_KEY_LIMIT}-key bound), so how much of the fast layer it claims "
                "is UNKNOWN — a sweep may not proceed on an unmeasured pin footprint"
            )
        if self.closure_size > self.budget.max_nodes:
            return (
                f"the pinned closure is {self.closure_size} node(s) but "
                f"{self.budget.explain()} — pinning would then take over the fast "
                f"layer, which it may never do. Unpin something, or raise "
                f"{PIN_MAX_NODES_ENV} while keeping it smaller than the window."
            )
        return None

    def gate_reason(self) -> str | None:
        """Why a SWEEP may not proceed, or ``None`` when it may."""
        if not self.readable:
            return (
                f"the pin declaration could not be read ({self.source or 'unreadable'}) "
                f"— a sweep that cannot tell what is pinned may not delete anything"
            )
        return self.over_budget()

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "key_prop": self.key_prop,
            "keys": sorted(self.keys),
            "closure": sorted(self.closure),
            "closure_size": self.closure_size,
            "degrees": dict(sorted(self.degrees.items())),
            "missing": sorted(self.missing),
            "readable": self.readable,
            "truncated": self.truncated,
            "source": self.source,
            "budget": self.budget.as_dict() if self.budget else None,
            "over_budget": self.over_budget(),
        }

    def gate(self) -> Callable[[Candidate], bool]:
        """The eviction gate: is this candidate inside the pinned residency?

        ⛔ Keyed on ``Candidate.key`` — the RECORD key — because that is the identity a
        declaration can name and the identity the node survives a rebuild under. A
        candidate with no key cannot be matched, and is left to the evictor's own
        undeletable rule rather than guessed at here.
        """
        held = self.closure
        return lambda candidate: candidate.key is not None and candidate.key in held


@dataclass(frozen=True)
class PinResult:
    """What one declaration did — the surface an operator or a UI reports."""

    added: frozenset[str] = frozenset()
    already: frozenset[str] = frozenset()
    refused: tuple[dict[str, Any], ...] = ()
    reading: PinReading | None = None
    budget: PinBudget | None = None
    written: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": sorted(self.added),
            "already": sorted(self.already),
            "refused": list(self.refused),
            "written": self.written,
            "budget": self.budget.as_dict() if self.budget else None,
            "set": self.reading.as_dict() if self.reading else None,
        }


def _rows(graph: Any, statement: str, params: dict[str, Any]) -> list[list[Any]]:
    return list(graph.query(statement, params=params).result_set)


def _declaration(graph: Any) -> tuple[str, str, list[str]] | str:
    """Read ``(label, key_prop, keys)`` from the ``:Meta`` node, or a reason string.

    A reason string is the UNREADABLE case: the node exists but its payload cannot be
    trusted. A missing node is NOT unreadable — "nothing is pinned" is a fact, and it is
    the normal state of every deployment today.
    """
    try:
        rows = _rows(
            graph,
            "MATCH (n:Meta {key:$key}) RETURN n.label, n.key_prop, n.keys",
            {"key": PINS_META_KEY},
        )
    except Exception as exc:  # unreadable store
        return f"unreadable:{type(exc).__name__}: {exc}"
    if not rows:
        return ("Point", "id", [])
    row = rows[0]
    label = row[0] if len(row) > 0 else None
    key_prop = row[1] if len(row) > 1 else None
    raw_keys = row[2] if len(row) > 2 else None
    if not isinstance(label, str) or not label:
        return f"malformed:label={label!r}"
    if not isinstance(key_prop, str) or not key_prop:
        return f"malformed:key_prop={key_prop!r}"
    if raw_keys is None:
        keys: list[str] = []
    elif isinstance(raw_keys, (list, tuple)):
        keys = [k for k in raw_keys if isinstance(k, str) and k]
    else:
        return f"malformed:keys={type(raw_keys).__name__}"
    return (label, key_prop, keys)


def read(graph: Any, *, budget: PinBudget | None = None) -> PinReading:
    """Read the declaration AND resolve its closure from the graph.

    ⛔ THE CLOSURE IS DERIVED, NOT STORED. A stored closure is a second bookkeeping copy
    — it goes stale the moment an edge is added or removed, and the stale copy would
    then *under*-protect (a new neighbour is evicted out from under a pin the docs page
    says covers it) or *over*-protect (a removed neighbour stays pinned forever). The
    graph already knows its own edges; this reads them.
    """
    declared = _declaration(graph)
    if isinstance(declared, str):
        return PinReading(label="", key_prop="", readable=False, source=declared,
                          budget=budget)
    label, key_prop, keys = declared
    if not keys:
        return PinReading(label=label, key_prop=key_prop, readable=True,
                          source=f"meta:{PINS_META_KEY}", budget=budget)

    try:
        bare_identifier(label, what="label")
        bare_identifier(key_prop, what="key property")
    except PinConfigError as exc:
        return PinReading(label=label, key_prop=key_prop, readable=False,
                          source=f"malformed:{exc}", budget=budget)

    asked = sorted(keys)[: DEFAULT_KEY_LIMIT + 1]
    truncated = len(keys) > DEFAULT_KEY_LIMIT
    try:
        rows = _rows(
            graph,
            f"UNWIND $keys AS k "
            f"MATCH (n:{label} {{{key_prop}: k}}) "
            f"OPTIONAL MATCH (n)--(m) "
            f"RETURN k, collect(DISTINCT id(m)), collect(DISTINCT m.{key_prop})",
            {"keys": asked},
        )
    except Exception as exc:
        return PinReading(label=label, key_prop=key_prop, readable=False,
                          source=f"unreadable:{type(exc).__name__}: {exc}",
                          budget=budget)

    held: set[str] = set()
    neighbours: set[str] = set()
    degrees: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        key = row[0]
        if not isinstance(key, str):
            continue
        seen.add(key)
        held.add(key)
        nbr_ids = row[1] if len(row) > 1 and row[1] is not None else []
        nbr_keys = row[2] if len(row) > 2 and row[2] is not None else []
        degrees[key] = len({i for i in nbr_ids if i is not None})
        for neighbour in nbr_keys:
            # A neighbour with no key in this key space (another label, or a node whose
            # key property is unset) cannot be gated by key. It is NOT counted into the
            # closure: counting a node nothing can protect would inflate the footprint
            # and make the budget lie in the safe-looking direction.
            if isinstance(neighbour, str) and neighbour:
                neighbours.add(neighbour)

    missing = frozenset(k for k in asked if k not in seen)
    closure = frozenset(held | (neighbours - held))
    return PinReading(
        label=label,
        key_prop=key_prop,
        # ⛔ THE DECLARED SET IS COMPLETE, THE CLOSURE MAY NOT BE. The payload is read
        # whole (`_declaration` returns every key on the `:Meta` node); the bound exists
        # only on the `UNWIND` probe, which resolves closures. Reporting the PROBED
        # subset as `keys` made the declaration look truncated to a reader that then
        # wrote it back — the tail-loss this module exists to forbid — and it also made
        # `unpin`'s only remedy unreachable, because the writer saw the same bound the
        # reader did. `truncated` therefore describes the CLOSURE alone.
        keys=frozenset(keys),
        closure=closure,
        degrees=degrees,
        missing=missing,
        readable=True,
        truncated=truncated or len(asked) > DEFAULT_KEY_LIMIT,
        source=f"meta:{PINS_META_KEY}",
        budget=budget,
    )


def _write(graph: Any, *, label: str, key_prop: str, keys: Sequence[str]) -> None:
    """Replace the declaration — a full REPLACE, never an additive merge.

    ``SET n += $props`` is additive, so an unset field would leave the PREVIOUS value on
    the node and the reader would report a declaration nobody made (the same defect
    `residency.declare_residency` documents for ``hot_keys``). Keys are always written.
    """
    graph.query(
        "MERGE (n:Meta {key:$key}) SET n += $props",
        params={
            "key": PINS_META_KEY,
            "props": {"label": label, "key_prop": key_prop, "keys": list(keys)},
        },
    )


def pin(
    graph: Any,
    keys: Iterable[str],
    *,
    label: str = "Point",
    key_prop: str = "id",
    budget: PinBudget | None = None,
) -> PinResult:
    """Declare these keys resident. Refuses — loudly — at the cap or the degree ceiling.

    ⛔ ALL OR NOTHING, and every refusal names its number. A partially applied
    declaration is the worst of the three outcomes: the user is told the control worked,
    the budget says otherwise, and nothing in the state distinguishes "you asked for ten
    and got six" from "you asked for six".
    """
    resolved = budget
    if resolved is None:
        resolved = PinBudget.resolve()

    wanted = sorted({k for k in keys if isinstance(k, str) and k})
    if not wanted:
        raise PinRefused(
            "no keys were given, so there is nothing to keep resident",
            code="empty",
        )

    current = read(graph, budget=resolved)
    if not current.readable:
        raise PinRefused(
            f"the existing pin declaration could not be read "
            f"({current.source or 'unreadable'}), so adding to it would silently "
            f"replace a set nobody could see",
            code="unreadable",
        )
    if current.truncated:
        # ⛔ THE WRITER MAY NOT ADD FROM AN UNMEASURED FOOTPRINT. `read` bounds the
        # CLOSURE probe at `DEFAULT_KEY_LIMIT + 1` keys and REPORTS it (`truncated`)
        # instead of silently shortening the closure, so `current.closure` is a LOWER
        # BOUND — and the budget below is measured over it. Admitting an addition on a
        # lower bound would let a pin that is already over the cap grow. The declared
        # KEYS are complete (`PinReading.keys` is the whole payload), so `unpin` stays
        # available and is lossless — that is the way out this message names.
        raise PinRefused(
            f"the pinned closure could not be measured in full (one probe may resolve "
            f"{DEFAULT_KEY_LIMIT} keys), so how much of the fast layer this set already "
            f"claims is UNKNOWN and the budget cannot admit another pin. Nothing was "
            f"pinned; the declaration is unchanged. `unpin` still works and is lossless "
            f"— the declared key list is read whole — so releasing keys is the way back "
            f"under the read bound.",
            code="truncated",
        )
    if (current.label, current.key_prop) != (label, key_prop) and current.keys:
        raise PinRefused(
            f"the existing pin set addresses {current.label!r} nodes by "
            f"{current.key_prop!r}, but this declaration addresses {label!r} by "
            f"{key_prop!r} — one pin set, one population; unpin it first",
            code="population-mismatch",
        )

    already = frozenset(k for k in wanted if k in current.keys)
    fresh = [k for k in wanted if k not in current.keys]

    # ⛔ Probe the NEW keys against the graph BEFORE writing anything: a declaration that
    # names a node the graph does not hold would resolve to an empty closure and pin
    # nothing while reporting success — the exact "the control is on" lie this module
    # exists to prevent. `current.closure` already carries the existing set, so the
    # budget below is measured over the UNION and never double-counts.
    if fresh:
        fresh_reading = _closure_for(graph, fresh, label=label, key_prop=key_prop,
                                     budget=resolved)
        if not fresh_reading.readable:
            raise PinRefused(
                f"the graph could not be asked which nodes these keys are "
                f"({fresh_reading.source}), so nothing was pinned",
                code="unreadable",
            )
        if fresh_reading.missing:
            raise PinRefused(
                f"{len(fresh_reading.missing)} key(s) are not in the fast layer at all "
                f"({', '.join(sorted(fresh_reading.missing)[:5])}"
                f"{'…' if len(fresh_reading.missing) > 5 else ''}) — a pin can only keep "
                f"resident what the fast layer holds. Nothing was pinned.",
                code="unknown-node",
            )
        hubs = [k for k in sorted(fresh_reading.degrees)
                if fresh_reading.degrees[k] > resolved.max_degree]
        if hubs:
            named = ", ".join(
                f"{k} ({fresh_reading.degrees[k]} connections)" for k in hubs[:5]
            )
            raise PinRefused(
                f"{len(hubs)} key(s) have more connections than the degree ceiling of "
                f"{resolved.max_degree}: {named}{'…' if len(hubs) > 5 else ''} — a hub "
                f"that large pulls a neighbourhood big enough to take the fast layer, "
                f"which is the measured anti-pattern §7 records. Pin the neighbours it "
                f"joins instead. Nothing was pinned.",
                code="degree",
            )

        merged = sorted(current.closure | fresh_reading.closure)
        if len(merged) > resolved.max_nodes:
            raise PinRefused(
                f"keeping these resident would need {len(merged)} fast-layer slot(s) "
                f"(the pinned {len(current.keys) + len(fresh)} node(s) plus the things "
                f"connected to them), but {resolved.explain()} — it can never take over "
                f"the fast layer. Nothing was pinned. Unpin "
                f"{max(1, len(merged) - resolved.max_nodes)} slot(s) or raise "
                f"{PIN_MAX_NODES_ENV} while keeping it smaller than the window.",
                code="budget",
            )

    _write(graph, label=label, key_prop=key_prop,
           keys=sorted(current.keys | set(fresh)))

    after = read(graph, budget=resolved)
    return PinResult(
        added=frozenset(fresh),
        already=already,
        refused=(),
        reading=after,
        budget=resolved,
        written=True,
    )


def _closure_for(
    graph: Any,
    keys: Sequence[str],
    *,
    label: str,
    key_prop: str,
    budget: PinBudget | None,
) -> PinReading:
    """Resolve one key batch's closure, without reading the existing declaration.

    Split out so :func:`pin` can size the NEW keys before deciding, and so the existing
    declaration is never counted twice.
    """
    bare_identifier(label, what="label")
    bare_identifier(key_prop, what="key property")
    try:
        rows = _rows(
            graph,
            f"UNWIND $keys AS k "
            f"MATCH (n:{label} {{{key_prop}: k}}) "
            f"OPTIONAL MATCH (n)--(m) "
            f"RETURN k, collect(DISTINCT id(m)), collect(DISTINCT m.{key_prop})",
            {"keys": list(keys)},
        )
    except Exception as exc:
        return PinReading(label=label, key_prop=key_prop, readable=False,
                          source=f"unreadable:{type(exc).__name__}: {exc}",
                          budget=budget)
    held: set[str] = set()
    neighbours: set[str] = set()
    degrees: dict[str, int] = {}
    seen: set[str] = set()
    for row in rows:
        key = row[0]
        if not isinstance(key, str):
            continue
        seen.add(key)
        held.add(key)
        nbr_ids = row[1] if len(row) > 1 and row[1] is not None else []
        nbr_keys = row[2] if len(row) > 2 and row[2] is not None else []
        degrees[key] = len({i for i in nbr_ids if i is not None})
        for neighbour in nbr_keys:
            if isinstance(neighbour, str) and neighbour:
                neighbours.add(neighbour)
    return PinReading(
        label=label,
        key_prop=key_prop,
        keys=frozenset(held),
        closure=frozenset(held | (neighbours - held)),
        degrees=degrees,
        missing=frozenset(k for k in keys if k not in seen),
        readable=True,
        budget=budget,
    )


def unpin(
    graph: Any,
    keys: Iterable[str],
    *,
    label: str | None = None,
    key_prop: str | None = None,
    budget: PinBudget | None = None,
) -> PinResult:
    """Withdraw these keys. Idempotent; an unknown key is reported, never invented.

    ⛔ ``label``/``key_prop`` default to the DECLARATION's own pair, not to
    ``Point``/``id``. A rewrite that took the caller's defaults would silently move a
    ``Source``-keyed set to ``Point`` — the remaining keys would then address nodes that
    do not exist, so every one of them would stop being resident while the declaration
    still claimed them. The set is the set; releasing keys from it cannot change what it
    addresses.
    """
    resolved = budget
    if resolved is None:
        resolved = PinBudget.resolve()
    current = read(graph, budget=resolved)
    if not current.readable:
        raise PinRefused(
            f"the pin declaration could not be read ({current.source or 'unreadable'}), "
            f"so removing from it would silently replace a set nobody could see",
            code="unreadable",
        )
    # ⛔ `truncated` is deliberately NOT a refusal HERE, and that asymmetry with `pin` is
    # the design: `current.keys` is the WHOLE declared payload even when the closure
    # probe hit its bound, so `keys - drop` is lossless. Refusing in both writers made a
    # >`DEFAULT_KEY_LIMIT`-key declaration a one-way door — `pin` refusing and `unpin`
    # refusing the only way out of it. A truncated reading only forbids what needs the
    # FOOTPRINT (an addition, which the budget must size); releasing keys needs the SET.
    eff_label = current.label if current.keys else (label or "Point")
    eff_key_prop = current.key_prop if current.keys else (key_prop or "id")
    drop = {k for k in keys if isinstance(k, str) and k}
    if not current.keys:
        # ⛔ NOTHING IS DECLARED, so there is nothing to release and nothing to write.
        # Reporting `written=True` here (the shape before this guard) told an operator
        # that a declaration changed when none existed — and the empty-set branch below
        # would have issued a `DETACH DELETE` for a node that is not there.
        return PinResult(already=frozenset(), reading=current, budget=resolved,
                         written=False)
    remaining = sorted(current.keys - drop)
    if remaining == sorted(current.keys):
        return PinResult(already=frozenset(drop & current.keys), reading=current,
                         budget=resolved, written=False)
    if remaining:
        _write(graph, label=eff_label, key_prop=eff_key_prop, keys=remaining)
    else:
        # ⛔ REMOVE THE NODE, do not write an empty list: `read` treats an absent node and
        # an empty enumeration identically, so keeping the node would leave a declaration
        # behind that claims nothing — the state a reader cannot distinguish from "never
        # pinned", which is what makes a rebuilt graph quiet instead of confusing.
        graph.query(
            "MATCH (n:Meta {key:$key}) DETACH DELETE n",
            params={"key": PINS_META_KEY},
        )
    after = read(graph, budget=resolved)
    return PinResult(added=frozenset(), already=frozenset(drop & current.keys),
                     reading=after, budget=resolved, written=True)


__all__ = [
    "DEFAULT_KEY_LIMIT",
    "DEFAULT_MAX_DEGREE",
    "DEFAULT_SHARE_OF_WINDOW",
    "PINS_META_KEY",
    "PIN_MAX_DEGREE_ENV",
    "PIN_MAX_NODES_ENV",
    "PinBudget",
    "PinConfigError",
    "PinReading",
    "PinRefused",
    "PinResult",
    "bare_identifier",
    "pin",
    "read",
    "unpin",
]
