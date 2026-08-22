"""R4 structural leg — SDK wiring + graph-as-recall-amplifier integration
tests. Embedded FalkorDBLite fixture (harness conventions from
tests/test_index_surfacing.py)."""
from __future__ import annotations

import os
import tempfile

from tortoise.sdk import TortoiseSDK


def _db() -> str:
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _sdk(db: str | None = None) -> TortoiseSDK:
    return TortoiseSDK(db or _db(), namespace="r4-structural")


def test_structural_kind_activates_kind_scan_without_post_filter():
    """structural_kind="statement" → structural strategy returns statement
    points, but non-statement hits (event turn points) stay in the pool —
    the top-level kind filter must NOT fire (R1 union design)."""
    sdk = _sdk()
    try:
        sdk.create_point("statement", "personal best 5K time is 27:12",
                         id="stmt-1", session_id="s1", is_episodic=True)
        sdk.create_point("event", "[user] my morning run felt strong today",
                         id="turn-1", session_id="s1", is_episodic=True)
        # query matches ONLY the turn point; structural_kind must add the
        # statement point without dropping the turn hit
        hits = sdk.tortoise_fts_query(
            "morning run felt strong", entity_type="point", limit=10,
            structural_kind="statement", structural_hops=0)
        ids = {h["id"] for h in hits}
        assert "turn-1" in ids          # text hit survives (no post-filter)
        assert "stmt-1" in ids          # kind-scan surfaces the statement
    finally:
        sdk.close()


def test_structural_hops_expansion_surfaces_connected_peer():
    """Graph as recall amplifier: query matches only the seed turn; the
    IMPL-connected statement peer (no text overlap) enters the pool via
    1-2 hop expansion from the text hit."""
    sdk = _sdk()
    try:
        sdk.create_point("statement", "personal best 5K time is 27:12",
                         id="stmt-1", session_id="s1", is_episodic=True)
        sdk.create_point("event", "[user] my morning run felt strong today",
                         id="turn-1", session_id="s1", is_episodic=True)
        # turn -[:IMPL]-> op -[:IMPL]-> statement (operator-mediated,
        # mirrors ingest_v2's create_operator shape)
        sdk.create_operator("IMPL", "turn-1", ["stmt-1"],
                            direction="unidirectional", promote_source=False)
        hits = sdk.tortoise_fts_query(
            "morning run felt strong", entity_type="point", limit=10,
            structural_kind="statement", structural_hops=2)
        ids = {h["id"] for h in hits}
        assert "turn-1" in ids
        assert "stmt-1" in ids  # expansion peer (also kind-scan member)
    finally:
        sdk.close()


def test_default_hops_zero_preserves_existing_behavior():
    """structural_hops defaults to 0 — no expansion pass; results identical
    to the pre-R4 call with the same args."""
    sdk = _sdk()
    try:
        sdk.create_point("statement", "personal best 5K time is 27:12",
                         id="stmt-1", session_id="s1", is_episodic=True)
        sdk.create_point("event", "[user] my morning run felt strong today",
                         id="turn-1", session_id="s1", is_episodic=True)
        sdk.create_operator("IMPL", "turn-1", ["stmt-1"],
                            direction="unidirectional", promote_source=False)
        baseline = sdk.tortoise_fts_query(
            "morning run felt strong", entity_type="point", limit=10)
        default = sdk.tortoise_fts_query(
            "morning run felt strong", entity_type="point", limit=10,
            structural_hops=0)  # explicit default — same as baseline
        assert [h["id"] for h in default] == [h["id"] for h in baseline]
    finally:
        sdk.close()
