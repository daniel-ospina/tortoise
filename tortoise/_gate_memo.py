"""A bounded, thread-safe memo keyed by the #2714 installed-pack gate.

One implementation for the five gate-keyed memos (#5163 / #5339):
``commit_schema._vocab_gate_cache``, ``value_extractor._VOCAB_CACHE`` and
``_KIND_SPEC_CACHE``, and ``extractor_v2._PACK_EVENT_FORMS`` /
``_PACK_OBJECT_FORMS``.

Every one of them is keyed by a frozenset of installed namespaces — a
**tenant-growable** key space (``pack_state.graph_kind_namespaces`` mines
namespaces from unvalidated graph data, and ``POST /v1/objects`` persists an
unvalidated ``objectKind``). Two properties follow, and both were defects in
the first cut of the cap:

* **Atomicity.** An inline ``if len(d) >= cap: d.pop(next(iter(d)))`` is a
  check-then-act: two worker threads (the capture pool runs up to 8) can read
  the same oldest key and the loser's ``pop`` raises ``KeyError`` — out of
  helpers documented *"the gate must never raise"*, i.e. a 500 on a write path.
  Every method here is lock-guarded.
* **Recency.** A bare dict is FIFO: once the working set exceeds the cap, a hot
  gate is evicted by tail insertions and every request recomputes. ``get`` and
  ``put_if_absent`` refresh recency, so eviction takes the least-recently-used
  entry.

Eviction is always a *recompute*, never a wrong answer — the value is a pure
function of the key.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

#: Cap on the number of distinct installed-namespace gates a process memoizes.
_MAX_GATE_MEMOS = 64


class GateMemo:
    """An LRU map keyed by a gate (``frozenset[str] | None``, or a tuple
    containing one). Thread-safe; a miss returns ``None``, never raises."""

    __slots__ = ("_cache", "_lock", "_max")

    def __init__(self, maxsize: int = _MAX_GATE_MEMOS) -> None:
        self._cache: OrderedDict[Any, Any] = OrderedDict()
        self._lock = threading.Lock()
        self._max = maxsize

    def get(self, key: Any) -> Any:
        """The value for ``key``, or ``None``; refreshes its recency."""
        with self._lock:
            try:
                self._cache.move_to_end(key)
            except KeyError:
                return None
            return self._cache[key]

    def put_if_absent(self, key: Any, value: Any) -> Any:
        """Store ``key``→``value`` unless already present; returns the stored
        value (the winner under a race). Evicts the LRU entry past the cap."""
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            self._cache[key] = value
            while len(self._cache) > self._max:
                self._cache.popitem(last=False)
            return value

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def discard(self, key: Any) -> None:
        """Remove ``key`` if present; never raises (targeted eviction, e.g.
        the degraded-index pop, needs this rather than ``clear``)."""
        with self._lock:
            self._cache.pop(key, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)
