"""Issue #5010 — the adopted per-entity fan-out cap (§11.5).

The owner adopted a fan-out cap of 200 (``STORAGE-ARCHITECTURE.md`` §11.5).
§11.4 rule 2 defines it as *"bounds how many edge pages one hub query pulls"*
and §11.5 states it is a **working-set bound** — it limits how many link rows
one entity's expansion pulls into memory at once. These tests pin the two
things that make that real:

* the declared value has ONE home (``tortoise/fanout.py``) and cannot be
  raised by a caller;
* the entity-hub expansion actually returns at most the cap — the acceptance
  test uses a synthetic 250-claim hub, which **fails on the pre-change code
  (250 rows) and passes after (200)**.

Backend-agnostic: under a supported ``TORTOISE_DB_URI`` the SDK redirects to
the server (per-test namespace), otherwise it runs embedded.
"""

from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import tortoise.subgraph as sg
from tortoise.assembly import SubjectCandidate, docker_walker_port
from tortoise.fanout import PER_ENTITY_FANOUT_CAP, bounded_fanout
from tortoise.sdk import TortoiseSDK


def _subject_candidate(object_id: str) -> SubjectCandidate:
    return SubjectCandidate(
        subject_index=0,
        object_id=object_id,
        name=object_id,
        confidence="high",
        source="exact",
        term=object_id,
    )


def _uri_set() -> bool:
    from tortoise.config import is_db_uri

    return is_db_uri(os.environ.get("TORTOISE_DB_URI"))


@contextmanager
def fresh_graph():
    """A fresh, isolated graph handle (embedded, or a redirect-namespaced one)."""
    base = tempfile.mkdtemp(prefix="tt_5010_")
    db_path = os.path.join(base, "test.db")
    ns = f"test_suite_{os.urandom(4).hex()}" if _uri_set() else None
    sdk = TortoiseSDK(db_path, namespace=ns)
    try:
        yield sdk
    finally:
        try:  # noqa: SIM105
            sdk.close()
        except Exception:
            pass


# ── the declared value has one home and is a hard ceiling ────────────────


def test_cap_value_is_the_adopted_200():
    assert PER_ENTITY_FANOUT_CAP == 200


def test_bounded_fanout_defaults_to_the_cap():
    assert bounded_fanout() == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(None) == PER_ENTITY_FANOUT_CAP


def test_bounded_fanout_clamps_above_the_cap():
    # A caller may lower the bound; it may NOT raise it past the adopted cap.
    assert bounded_fanout(5000) == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(PER_ENTITY_FANOUT_CAP + 1) == PER_ENTITY_FANOUT_CAP


def test_bounded_fanout_keeps_a_lower_request():
    assert bounded_fanout(25) == 25


def test_bounded_fanout_floors_at_one():
    assert bounded_fanout(0) == 1
    assert bounded_fanout(-7) == 1


def test_bounded_fanout_unusable_value_uses_the_cap():
    assert bounded_fanout("nonsense") == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(object()) == PER_ENTITY_FANOUT_CAP
    # bool is not the bound "1" (repo convention: _sanitize_cap)
    assert bounded_fanout(True) == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(False) == PER_ENTITY_FANOUT_CAP
    # a non-finite float is not an unbounded licence (int(inf) raises)
    assert bounded_fanout(float("inf")) == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(float("-inf")) == PER_ENTITY_FANOUT_CAP
    assert bounded_fanout(float("nan")) == PER_ENTITY_FANOUT_CAP


# ── the hub expansion is actually bounded ────────────────────────────────


def _hub(graph, hub_id: str, n_claims: int, prefix: str) -> None:
    """``n_claims`` Points wired to one Object hub, plus the anchor Point."""
    graph.query(
        "MERGE (h:Object {id:$hub}) ON CREATE SET h.name = $hub", params={"hub": hub_id}
    )
    graph.query(
        "MERGE (a:Point {id:'anchor'}) ON CREATE SET a.content='seed', "
        "a.is_operator=false"
    )
    graph.query(
        "MATCH (a:Point {id:'anchor'}), (h:Object {id:$hub}) "
        "MERGE (a)-[:aboutObject]->(h)",
        params={"hub": hub_id},
    )
    graph.query(
        "UNWIND range(1, $n) AS i "
        "CREATE (p:Point {id: $prefix + toString(i), "
        "                 content: 'c' + toString(i), is_operator: false})",
        params={"n": n_claims, "prefix": prefix},
    )
    graph.query(
        "MATCH (h:Object {id:$hub}), (p:Point) WHERE p.id STARTS WITH $prefix "
        "MERGE (p)-[:aboutObject]->(h)",
        params={"hub": hub_id, "prefix": prefix},
    )


def test_hub_expansion_is_capped_per_entity():
    """ACCEPTANCE: a 250-claim hub yields at most the cap, not 250 rows."""
    with fresh_graph() as sdk:
        g = sdk._get_proj().g
        _hub(g, "big-hub", 250, "s")
        rows = sg._collect_raw(g, "anchor")["siblings"]
        assert len(rows) == PER_ENTITY_FANOUT_CAP, len(rows)
        # deterministic (ORDER BY sib.id) — not engine-order dependent
        first = sg._collect_raw(g, "anchor")["siblings"]
        assert [r[2] for r in rows] == [r[2] for r in first]


def test_small_hub_is_untouched_by_the_cap():
    """The cap binds nothing at today's real degrees (worst measured hub: 123)."""
    with fresh_graph() as sdk:
        g = sdk._get_proj().g
        _hub(g, "small-hub", 3, "t")
        rows = sg._collect_raw(g, "anchor")["siblings"]
        assert len(rows) == 3


def test_spine_rows_clamps_a_caller_above_the_cap():
    """``spine_rows`` may be lowered by a caller, never raised past the cap."""
    with fresh_graph() as sdk:
        g = sdk._get_proj().g
        _hub(g, "spine-hub", 250, "u")
        port = docker_walker_port(sdk)
        rows = port.spine_rows(["spine-hub"], per_subject_cap=1000)
        assert len(rows) == PER_ENTITY_FANOUT_CAP, len(rows)
        lowered = port.spine_rows(["spine-hub"], per_subject_cap=5)
        assert len(lowered) == 5


def test_collect_slices_accounts_against_the_clamped_cap():
    """Regression: an over-cap request must be clamped ONCE, so
    ``rows_requested`` / ``truncated`` describe the bound the port actually
    applied — otherwise rows are dropped while ``truncated`` reports False."""
    from tortoise.assembly import collect_slices

    with fresh_graph() as sdk:
        g = sdk._get_proj().g
        _hub(g, "acct-hub", 250, "v")
        port = docker_walker_port(sdk)
        cand = _subject_candidate("acct-hub")
        for requested in (PER_ENTITY_FANOUT_CAP, 500, 1000):
            out = collect_slices(
                port, [cand], shape=None, per_subject_cap=requested
            )
            adm = out.admission
            assert adm["rows_requested"] == PER_ENTITY_FANOUT_CAP, adm
            assert adm["rows_admitted"] == PER_ENTITY_FANOUT_CAP, adm
            assert adm["truncated"] is True, adm
