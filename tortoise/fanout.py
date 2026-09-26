"""The adopted per-entity fan-out cap (issue #5010).

The owner adopted a fan-out cap of **200** on 2026-09-23
(``docs/architecture/STORAGE-ARCHITECTURE.md`` §11.5, reached through the
decision protocol; the same value appears in
``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`` §13.8, where the comparable's
``per_entity_limit`` also defaults to 200 and is paired with a timeout that
drops the whole expansion arm).

⚠️ **What the cap IS, and what it is not — §11.5 is explicit, and getting this
backwards is how the value gets misapplied:**

* It is a **working-set bound**: it limits how many link rows *one entity's
  expansion pulls into memory at once* (§11.4 rule 2 — *"bounds how many edge
  pages one hub query pulls"*).
* It is **NOT a write-side rule.** Do not cap what gets **stored**: the links
  are ~3 MB, a lower cap buys no meaningful storage, and a cap that drops
  stored edges *"risks losing a real connection, which is the product"*
  (§11.5, *"Set the value against the right quantity"*). A data-losing write
  cap is the exact misuse §11.5 warns against.
* It is a **guard rail, not an optimisation.** The worst hub measured on
  this graph is **123** (`config/ci-surfaces.yml`; then `durations map` 111,
  `the admin-merge rail` 70), so at 200 the cap **binds nothing today and
  cannot lose data now**.
* The value is an **initial value inherited from a comparable's join**, not a
  constant derived from our own measurements; it is refined with real usage
  (owner: *"we haven't launched yet … we optimise with users"*).

Both halves of the issue's framing point at the same side: the cap is the
**guard rail for keeping the connection layer** (queries must not explode while
the edges stay) *and* the **precondition for ever deriving `aboutObject`**
(a join needs a ``LATERAL LIMIT per_entity_limit``). Both are query-side.
"""

from __future__ import annotations

__all__ = ["PER_ENTITY_FANOUT_CAP", "bounded_fanout"]

#: The adopted per-entity expansion cap (STORAGE-ARCHITECTURE.md §11.5).
PER_ENTITY_FANOUT_CAP = 200


def bounded_fanout(requested: object = None) -> int:
    """Clamp a per-entity expansion bound to the adopted cap.

    ``None`` (or a non-integer / unusable value) means *"use the cap"*. A value
    below 1 is raised to 1 (an expansion that returns nothing is never what a
    caller means by a bound). A value above the cap is **clamped down**: the cap
    is a hard ceiling on the working set, not a default a caller may opt out of.

    Follows the repo's cap-sanitising convention (``retrieval._sanitize_cap``)
    on the one point that matters here: a ``bool`` is an **unusable** cap, not
    a number — ``True`` is not the bound "1" — and a non-finite float is not
    an unbounded licence.
    """
    if requested is None or isinstance(requested, bool):
        return PER_ENTITY_FANOUT_CAP
    try:
        value = int(requested)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return PER_ENTITY_FANOUT_CAP
    if value < 1:
        return 1
    return min(value, PER_ENTITY_FANOUT_CAP)
