"""#9208: attribution for the change feed's PROCESS-GLOBAL latch.

`event_store._DEGRADED` is process memory keyed by `(store identity, graph)`, and
a refused `:GraphEvent` write latches it until an explicit `clear_degraded()`.
A latch that outlives its test refuses `poll_events` for that graph for the rest
of the session — the `[silent-loss]` failure MEMORY.md records, where a
process-global latch reddened 21 unrelated tests because nothing said which test
had set it.

`tests/conftest.py`'s `_change_feed_state_isolation` fixture clears before and
after every test. The AFTER half is where attribution lives, and this module
holds the decision so it can be pinned by a test instead of living unreachable
inside a fixture (`tests/test_9208_change_feed_degraded.py`): a pre-test check
can never name the polluter, because the same fixture's post-test clear runs
first — the ordering bug #9208's review found in the previous shape.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def leaked_state_message(rows: Iterable[dict[str, Any]], nodeid: str) -> str | None:
    """The teardown failure for a test that left a degraded feed, or ``None``.

    ``nodeid`` is the test that has just FINISHED — the polluter, not its
    successor. ``rows`` is `event_store.degraded_graphs()` sampled before the
    fixture cleared it.
    """
    rows = list(rows)
    if not rows:
        return None
    graphs = sorted({str(row.get("graph")) for row in rows})
    return (
        f"{nodeid} left {len(rows)} degraded change feed(s) behind: {graphs}. "
        "The state is PROCESS-GLOBAL and refuses `poll_events` for those graphs "
        "for the rest of the session (#9208), so a test that degrades a feed on "
        "purpose must `event_store.clear_degraded()` in a finally — otherwise it "
        "reddens unrelated tests later in the shard, which is the failure mode "
        "MEMORY.md records as `[silent-loss]`. It has been cleared for you here, "
        "so only this test is red.")
