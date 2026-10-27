"""#10121 — a boundary tie must not truncate the NEWEST lexical match.

The shipped graph-side lexical scorer returns ONE score for a whole corpus of
look-alike documents (measured: 2.0 across 131 documents on both engines). The
tie is therefore the whole ranking, and #3019's determinism clause
(`ORDER BY score DESC, node.id ASC`) resolved it OLDEST-first — so the record
created last took the last pool slot and, past the pool depth, was truncated
out of the leg entirely and carried no score at all. That is the production
reading on #8488 (`"fts": null` for the point that had just been written).

Mutation that this file pins: restore `node.id ASC` on the minted-id path
(or make the tie direction non-directional) and `test_fresh_match_survives...`
reds — the fresh point leaves the result set.

Fixture note: the scorer's degenerate uniformity is what makes this test
portable — no embedder, no ranking tuning, just N documents that all match the
query's OR-terms, plus one created last.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import run_fts_query

pytestmark = pytest.mark.embedded_only

_SDKS: list[TortoiseSDK] = []

#: The identifier-shaped query, reduced by the shipped `keep_numeric=False`
#: policy to `capstone|write|probe` — the #8933 control, untouched here.
QUERY = "capstone-9664 write probe 2026-10-26"

#: Look-alikes: same shape as the target, differing only in the digits the
#: tokenizer drops. Every one of them matches the same OR-terms, so every one
#: of them scores identically.
LOOKALIKES = [f"capstone-{9000 + i} write probe 2026-10-2{i % 9}" for i in range(20)]


@pytest.fixture(autouse=True)
def _reclaim_sdks():
    yield
    while _SDKS:
        sdk = _SDKS.pop()
        with contextlib.suppress(Exception):
            sdk.close()
        tree = os.path.dirname(sdk._db_path) if sdk._db_path else None
        if tree:
            shutil.rmtree(tree, ignore_errors=True)


def _graph_with_lookalikes() -> tuple[object, str]:
    db_path = os.path.join(tempfile.mkdtemp(prefix="tie10121_"), "test.db")
    sdk = TortoiseSDK(db_path, namespace=f"tie10121_{os.urandom(6).hex()}")
    _SDKS.append(sdk)
    for content in LOOKALIKES:
        sdk.create_point("statement", content)
    fresh = sdk.create_point("statement", QUERY)["id"]
    return sdk._get_proj().g, fresh


def test_fresh_match_survives_the_tie_at_the_caller_limit():
    """The point created LAST is inside the leg's window, not truncated out.

    Mutation: `ORDER BY score DESC, node.id ASC` (the pre-#10121 clause) puts
    the fresh point at rank 21 of 21 — outside a 10-row window — and this
    assertion fails with `fresh point absent`.
    """
    graph, fresh = _graph_with_lookalikes()
    rows = run_fts_query(graph, QUERY, limit=10)
    ids = [pid for pid, _ in rows]
    assert len(rows) == 10, "guard: the leg must be filled, or the tie is not saturated"
    assert fresh in ids, (
        "a boundary tie must not truncate the NEWEST match out of the leg — "
        f"fresh point absent from {len(rows)} rows (#10121)"
    )


def test_the_tie_ranks_the_fresh_match_first_not_last():
    """Direction, not just presence: newest-first within the tie.

    Mutation: flip the tie back to ASC and the fresh point moves from rank 1 to
    the last row of the fully-fetched set.
    """
    graph, fresh = _graph_with_lookalikes()
    rows = run_fts_query(graph, QUERY, limit=len(LOOKALIKES) + 5)
    ranked = [pid for pid, _ in rows]
    assert ranked, "guard: the query must match the fixture corpus"
    assert set(ranked[0:1]) == {fresh}, (
        "within an all-tied corpus the newest match must rank first; got "
        f"{ranked.index(fresh) + 1} of {len(ranked)} (#10121)"
    )


def test_non_minted_keys_keep_their_existing_order():
    """The fix is scoped: labels keyed on `url`/`eventId` keep the ASC tie.

    #10121's direction argument rests on `id` being a MINTED timestamp key. A
    Source is keyed on `url`, which carries no recency meaning, so its tie
    order is deliberately unchanged — this test fails if the direction is
    applied unconditionally to every label.
    """
    import inspect

    from tortoise import search_engine

    src = inspect.getsource(search_engine.run_fts_query)
    assert 'tie_direction = "DESC" if id_field == "id" else "ASC"' in src, (
        "the tie direction must be conditional on the minted-id key, not "
        "applied to every label (#10121)"
    )
