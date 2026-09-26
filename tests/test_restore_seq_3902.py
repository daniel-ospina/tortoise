"""Hermetic regression for #3902 — a restore lost the per-graph event-log
counter (``:GraphEventMeta.last_seq``).

``dump_graph`` excludes the ``:GraphEventMeta`` label from its node set
(#1625 — runtime bookkeeping must not inflate ``node_count``), so the
per-graph monotonic ``seq`` handed to ``event_store.next_seq`` was dropped at
the backup boundary. ``next_seq`` then MERGEs a fresh counter at
``last_seq = 1`` and returns ``seq = 1`` — which the restored ``:GraphEvent``
log already uses.

Why it matters: ``seq`` is the event-log ORDERING key. ``events_poll``
cursors, subscription delivery, and the ``first_seq`` purge watermark all
assume it is per-graph monotonic and unique. After a restore the first
``next_seq`` collides with an existing event and every later event is
under-counted by the number of restored events — a subscriber that polled
past ``seq = N`` before the disaster re-receives colliding seqs after it.
This is the disaster-recovery path, so it is exactly when it matters least to
discover it.

Docker/server lane only: this exercises the real dump → encrypt →
``create_backup`` → ``restore_backup`` temp-graph restore + ``GRAPH.COPY``
swap, so it needs a real server. Skips cleanly when no non-embedded
FalkorDB is reachable (mirrors tests/test_graphcopy_boolean_index_3154.py's
#522 non-embedded gate).
"""
from __future__ import annotations

import os
import uuid

import pytest

# ── Non-embedded (docker/server) gate ────────────────────────────────────
FALKORDB_AVAILABLE = False
_WORKING_URI: str | None = None


def _probe_falkordb(candidates: list[str | None]) -> tuple[bool, str | None]:
    """Probe candidate URIs for a live non-embedded FalkorDB."""
    _env_uri = os.environ.get("TORTOISE_DB_URI")
    for _uri in candidates:
        if not _uri:
            continue
        _proj = None
        try:
            from tortoise.projection import FalkorProjection
            _proj = FalkorProjection.from_uri(_uri)
            _proj.g.query("RETURN 1")
            return True, _uri
        except Exception:
            if _uri == _env_uri and _uri:
                break  # env-specified DB unreachable — don't fall through
            continue
        finally:
            if _proj is not None:
                try:  # noqa: SIM105
                    _proj.close()
                except Exception:
                    pass
    return False, None


FALKORDB_AVAILABLE, _WORKING_URI = _probe_falkordb([
    os.environ.get("TORTOISE_DB_URI"),
    "docker://:falkordb@localhost:6380/tortoise_test_b5_3902",
    "docker://:falkordb@localhost:6379/tortoise_test_restore_seq_3902",
])

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="FalkorDB not available")


def _uri() -> str:
    return os.environ.get("TORTOISE_DB_URI") or (_WORKING_URI or "")


def _name(stem: str) -> str:
    """Guard-passing test namespace — never a production ``org_*`` name."""
    return f"test_restore3902_{stem}_{uuid.uuid4().hex[:8]}"


class _Proj:
    """Minimal projection shim — ``event_store`` reads only ``proj.g``."""

    def __init__(self, g):
        self.g = g


def _seed_event_log(db, graph_name: str, n: int = 3):
    """Seed ``:GraphEvent`` seq 1..n + the counter, via the REAL
    ``next_seq``/``append_event`` path so ``last_seq`` lands exactly as in
    production (``next_seq`` is what the fix restores). Returns the projection
    shim bound to the graph."""
    from tortoise.event_store import append_event, next_seq
    from tortoise.projection import FalkorProjection

    proj = FalkorProjection.from_uri(_uri(), graph_name=graph_name)
    proj.g.query("MATCH (n) DETACH DELETE n")
    shim = _Proj(proj.g)
    for i in range(n):
        seq = next_seq(shim)
        append_event(shim, seq, "test.event", {"i": i}, f"ev-{uuid.uuid4().hex}")
    return proj, shim


@pytest.fixture
def db():
    """A FalkorDB connection handle + cleanup for every graph a test names."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(_uri())
    created: list[str] = []
    try:
        yield proj.db, created
    finally:
        for graph_name in created:
            try:  # noqa: SIM105
                proj.db.select_graph(graph_name).delete()
            except Exception:
                pass
        proj.close()


# ── half 1: NEW dumps carry the counter ──────────────────────────────────


def test_dump_carries_event_meta_counter_without_re_including_the_label(db):
    """A dump records the counter, and #1625's node-set exclusion stands."""
    from tortoise.hosted_backup import dump_graph

    graph_db, created = db
    src = _name("dump_src")
    created.append(src)
    proj, _ = _seed_event_log(graph_db, src, n=3)

    dump = dump_graph(proj.g, graph_name=src)
    assert dump["event_meta"] == {"last_seq": 3, "first_seq": 1}, (
        "#3902: the dump must carry the :GraphEventMeta counter — without it "
        "a restore re-issues seq 1 over the restored log"
    )
    # #1625 is NOT reversed: the counter node stays out of the node set.
    assert dump["node_count"] == 3  # the three :GraphEvent nodes only
    assert all("GraphEventMeta" not in n["labels"] for n in dump["nodes"])
    proj.close()


def test_restore_backup_swap_preserves_the_counter(db):
    """Acceptance 1 + 4: the full create→restore swap continues the seq.

    ``next_seq`` on the RESTORED live graph returns
    ``max(restored seq) + 1`` — observable on the graph, not on a source
    string — and the watermark survives the temp→live ``GRAPH.COPY``.
    """
    from tortoise.event_store import next_seq
    from tortoise.hosted_backup import MemoryStorage, create_backup, restore_backup

    graph_db, created = db
    src, live, registry_name = _name("swap_src"), _name("swap_live"), _name("swap_reg")
    created += [src, live, registry_name]
    proj, _ = _seed_event_log(graph_db, src, n=3)

    store = MemoryStorage()
    registry = graph_db.select_graph(registry_name)
    key = os.urandom(32)
    manifest = create_backup(
        proj, registry, store, org_id="team_3902", graph_name=src, key=key)
    dump_key = f"backups/{manifest['backup_id']}/dump.enc"

    restore_backup(
        graph_db, registry, store, dump_key,
        org_id="team_3902", graph_name=src, target_graph=live, drill=True,
        key=key,
    )

    restored_events = [
        int(r[0]) for r in graph_db.select_graph(live).query(
            "MATCH (e:GraphEvent) RETURN e.seq ORDER BY e.seq").result_set
    ]
    assert restored_events == [1, 2, 3]
    meta = graph_db.select_graph(live).query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    assert meta == [[3, 1]], (
        "#3902: the counter did not survive the temp→live GRAPH.COPY"
    )
    assert next_seq(_Proj(graph_db.select_graph(live))) == 4, (
        "#3902: next_seq after restore must be max(restored seq) + 1 = 4, "
        "not a colliding 1"
    )
    proj.close()


# ── half 2: OLD dumps without the counter must not collide ───────────────


def test_restore_old_dump_without_counter_rederives_from_the_event_log(db):
    """Acceptance 3 (the mutation-that-must-RED test).

    Every backup written before #3902 carries no ``event_meta`` key. Restoring
    one must re-derive the counter from the restored log rather than leaving
    ``next_seq`` to create a fresh counter at 1. Revert the re-derivation and
    this returns 1 over a restored log holding seq 1.
    """
    from tortoise.event_store import next_seq
    from tortoise.hosted_backup import _restore_into_temp_verify_swap, dump_graph

    graph_db, created = db
    src, live = _name("old_src"), _name("old_live")
    created += [src, live]
    proj, _ = _seed_event_log(graph_db, src, n=3)

    dump = dump_graph(proj.g, graph_name=src)
    dump.pop("event_meta", None)  # simulate a pre-#3902 artifact

    _restore_into_temp_verify_swap(graph_db, dump, live_name=live)

    live_g = graph_db.select_graph(live)
    seqs = [int(r[0]) for r in live_g.query(
        "MATCH (e:GraphEvent) RETURN e.seq ORDER BY e.seq").result_set]
    assert seqs == [1, 2, 3]
    meta = live_g.query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    assert meta == [[3, 1]]
    assert next_seq(_Proj(live_g)) == 4, (
        "#3902: an old dump (no counter) must re-derive last_seq from the "
        "restored log — next_seq returned a seq the log already uses"
    )
    proj.close()


# ── acceptance 2: the empty / fully-purged log contract ──────────────────


def test_restore_fully_purged_log_keeps_first_seq_contract(db):
    """A carried counter over an EMPTY log restores exactly, preserving
    ``_refresh_first_seq``'s ``first_seq = last_seq + 1`` semantics (a cursor
    at or below ``last_seq`` is expired; ``next_seq`` continues at
    ``first_seq``)."""
    from tortoise.event_store import _refresh_first_seq, next_seq
    from tortoise.hosted_backup import _restore_into_temp_verify_swap, dump_graph

    graph_db, created = db
    src, live = _name("purged_src"), _name("purged_live")
    created += [src, live]
    proj, _ = _seed_event_log(graph_db, src, n=3)

    # Purge the whole log, then refresh the watermark the way a real purge does.
    proj.g.query("MATCH (e:GraphEvent) DETACH DELETE e")
    _refresh_first_seq(_Proj(proj.g))
    src_meta = proj.g.query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    assert src_meta == [[3, 4]]

    dump = dump_graph(proj.g, graph_name=src)
    assert dump["event_meta"] == {"last_seq": 3, "first_seq": 4}

    _restore_into_temp_verify_swap(graph_db, dump, live_name=live)

    live_g = graph_db.select_graph(live)
    assert live_g.query("MATCH (e:GraphEvent) RETURN count(e)").result_set[0][0] == 0
    meta = live_g.query(
        "MATCH (m:GraphEventMeta) RETURN m.last_seq, m.first_seq").result_set
    assert meta == [[3, 4]], (
        "#3902: an empty-but-used log's watermark must survive the restore"
    )
    assert meta[0][1] == meta[0][0] + 1  # first_seq = last_seq + 1
    assert next_seq(_Proj(live_g)) == 4  # continues past the purged history
    proj.close()


def test_restore_empty_source_keeps_fresh_graph_semantics(db):
    """A graph that never emitted an event has no counter node; its dump
    carries no ``event_meta`` and the restore stays a fresh graph —
    ``next_seq`` creates the counter at 1 (nothing to collide with)."""
    from tortoise.event_store import next_seq
    from tortoise.hosted_backup import _restore_into_temp_verify_swap, dump_graph

    graph_db, created = db
    src, live = _name("empty_src"), _name("empty_live")
    created += [src, live]
    graph_db.select_graph(src).query("MATCH (n) DETACH DELETE n")

    dump = dump_graph(graph_db.select_graph(src), graph_name=src)
    assert "event_meta" not in dump

    _restore_into_temp_verify_swap(graph_db, dump, live_name=live)
    live_g = graph_db.select_graph(live)
    assert live_g.query(
        "MATCH (m:GraphEventMeta) RETURN count(m)").result_set[0][0] == 0
    assert next_seq(_Proj(live_g)) == 1
