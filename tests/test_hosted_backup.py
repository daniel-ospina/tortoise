"""Hosted backup pipeline tests (#305) — encryption, logical dump/restore,
R2 storage (real + fake-boto3), retention pruning, integrity + tenant-isolation
failure paths.

Product-ization of the RDB-first restore (#171): the hosted platform backs up
the COMPLETE team graph (any writer — SDK, MCP, connectors), not just events.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from tortoise.hosted_backup import (
    DUMP_FORMAT,
    MemoryStorage,
    R2Storage,
    RestoreVerificationError,
    create_backup,
    decrypt_backup,
    dump_graph,
    encrypt_backup,
    list_backups,
    prune_backups,
    restore_backup,
    restore_graph,
)
from tortoise.projection import FalkorProjection


def _make_proj(tmpdir: str, name: str = "t.db") -> FalkorProjection:
    return FalkorProjection(os.path.join(tmpdir, name))


def _seed(g, n: int = 5) -> None:
    """Seed a graph with Points (incl. operators), Subjects, and edges."""
    for i in range(n):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:$k})",
            params={"id": f"pt-{i}", "c": f"content {i}", "k": "claim"},
        )
    for i in range(n - 1):
        g.query(
            "MATCH (a:Point {id:$a}), (b:Point {id:$b}) CREATE (a)-[:IMPL {weight:0.8}]->(b)",
            params={"a": f"pt-{i}", "b": f"pt-{i+1}"},
        )
    g.query("CREATE (s:Subject {id:'subj-1', name:'S1'})")
    g.query(
        "MATCH (p:Point {id:'pt-0'}), (s:Subject {id:'subj-1'}) "
        "CREATE (p)-[:ABOUT {kind:'subject'}]->(s)"
    )


def _freeze_clock(monkeypatch, fixed: datetime | None = None) -> None:
    """Pin the module's clock so date-sensitive tests are run-date independent."""
    from tortoise import hosted_backup as hb

    fixed = fixed or datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(hb, "datetime", _FixedDatetime)


def _make_key() -> bytes:
    return os.urandom(32)


def _set_env_key(monkeypatch) -> str:
    raw = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", raw)
    return raw


def _node_key(n: dict) -> tuple:
    """Stable node identity for edge comparisons (labels + props, no ids)."""
    return (tuple(sorted(n["labels"])), tuple(sorted(n["props"].items())))


def _norm_nodes(nodes: list[dict]) -> list[dict]:
    """Normalize dump nodes for comparison (labels + props, no internal ids)."""
    return sorted(
        ({"labels": sorted(n["labels"]), "props": dict(n["props"])} for n in nodes),
        key=lambda n: (n["labels"], str(sorted(n["props"].items()))),
    )


def _norm_edges(edges: list[dict], nodes: list[dict]) -> list[tuple]:
    """Normalize dump edges with endpoints resolved to stable node keys.

    Catches rewiring bugs (edge endpoints) that type+props-only comparison
    would miss (all IMPL edges carry identical props). Fails loudly on key
    collisions — identical-label+props nodes would make endpoint comparison
    ambiguous, silently losing detection power.
    """
    key = {n["dump_id"]: _node_key(n) for n in nodes}
    assert len(key) == len(nodes), "node key collision — endpoint comparison ambiguous"
    return sorted(
        (key[e["src"]], key[e["dst"]], e["type"], tuple(sorted(e["props"].items())))
        for e in edges
    )


# ── encryption ───────────────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    key = _make_key()
    payload = b"hello graph"
    blob = encrypt_backup(payload, key=key)
    assert decrypt_backup(blob, key=key) == payload


def test_decrypt_tamper_detected():
    key = _make_key()
    blob = bytearray(encrypt_backup(b"secret", key=key))
    blob[-1] ^= 0xFF
    with pytest.raises(ValueError, match="tampered|wrong key"):  # noqa: RUF043
        decrypt_backup(bytes(blob), key=key)


def test_decrypt_wrong_key():
    blob = encrypt_backup(b"secret", key=_make_key())
    with pytest.raises(ValueError, match="tampered|wrong key"):  # noqa: RUF043
        decrypt_backup(blob, key=_make_key())


def test_decrypt_bad_magic():
    with pytest.raises(ValueError, match="bad magic"):
        decrypt_backup(b"not-a-tortoise-blob", key=_make_key())


def test_decrypt_truncated_blob():
    blob = encrypt_backup(b"x", key=_make_key())
    with pytest.raises(ValueError, match="bad magic|truncated"):  # noqa: RUF043
        decrypt_backup(blob[:4], key=_make_key())


def test_key_required_from_env(monkeypatch):
    monkeypatch.delenv("TORTOISE_BACKUP_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TORTOISE_BACKUP_KEY"):
        encrypt_backup(b"x", key=None)
    with pytest.raises(RuntimeError, match="TORTOISE_BACKUP_KEY"):
        decrypt_backup(encrypt_backup(b"x", key=_make_key()), key=None)


def test_key_must_be_32_bytes(monkeypatch):
    monkeypatch.setenv("TORTOISE_BACKUP_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(RuntimeError, match="32 bytes"):
        encrypt_backup(b"x", key=None)


# ── logical dump / restore ───────────────────────────────────────────────────


def test_dump_restore_roundtrip_preserves_content():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        dump = dump_graph(proj.g, graph_name="tortoise")
        assert dump["format"] == DUMP_FORMAT
        assert dump["node_count"] == 6  # 5 points + 1 subject
        assert dump["edge_count"] == 5  # 4 IMPL + 1 ABOUT
        proj.close()

        proj2 = _make_proj(tmp, "t2.db")
        counts = restore_graph(proj2.g, dump)
        assert counts == {"nodes": 6, "edges": 5}
        # Full content equality incl. edge endpoints: re-dump the restored
        # graph and compare every node label/prop and edge src/dst/type/prop.
        dump2 = dump_graph(proj2.g, graph_name="tortoise2")
        assert _norm_nodes(dump2["nodes"]) == _norm_nodes(dump["nodes"])
        assert _norm_edges(dump2["edges"], dump2["nodes"]) == _norm_edges(
            dump["edges"], dump["nodes"]
        )
        # explicit wiring check (endpoint-level, beyond the multiset)
        target = proj2.g.query(
            "MATCH (a:Point {id:'pt-0'})-[:IMPL]->(b) RETURN b.id"
        ).result_set
        assert target[0][0] == "pt-1"
        # edge props survive
        weight = proj2.g.query(
            "MATCH (a)-[r:IMPL]->() WHERE a.id='pt-0' RETURN r.weight"
        ).result_set
        assert weight[0][0] == 0.8
        proj2.close()


def test_dump_restore_rich_props_multilabel_isolated_node():
    """Boundaries: multi-label nodes, int/bool/list/null props, isolated node."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        proj.g.query(
            "CREATE (r:Point:Source {id:'r1', count:42, active:true, tags:['a','b'], note:null})"
        )
        proj.g.query("CREATE (iso:Object {id:'iso'})")  # isolated: no edges
        dump = dump_graph(proj.g, graph_name="tortoise")
        assert dump["node_count"] == 2
        assert dump["edge_count"] == 0
        proj.close()

        proj2 = _make_proj(tmp, "t2.db")
        assert restore_graph(proj2.g, dump) == {"nodes": 2, "edges": 0}
        dump2 = dump_graph(proj2.g, graph_name="tortoise2")
        assert _norm_nodes(dump2["nodes"]) == _norm_nodes(dump["nodes"])
        assert proj2.g.query("MATCH (n:Point:Source) RETURN count(n)").result_set[0][0] == 1
        assert proj2.g.query("MATCH (n:Object {id:'iso'}) RETURN count(n)").result_set[0][0] == 1
        props = proj2.g.query("MATCH (n {id:'r1'}) RETURN properties(n)").result_set
        assert props[0][0]["count"] == 42
        assert props[0][0]["active"] is True
        assert props[0][0]["tags"] == ["a", "b"]
        # null prop round-trips faithfully (sentinel distinguishes absent vs None)
        _MISSING = object()
        src_note = [n for n in dump["nodes"] if n["props"].get("id") == "r1"][0]["props"].get("note", _MISSING)  # noqa: RUF015
        assert props[0][0].get("note", _MISSING) == src_note
        proj2.close()


def test_empty_graph_dump_restore():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        dump = dump_graph(proj.g, graph_name="tortoise")
        assert dump["node_count"] == 0
        assert dump["edge_count"] == 0
        proj.close()

        proj2 = _make_proj(tmp, "t2.db")
        assert restore_graph(proj2.g, dump) == {"nodes": 0, "edges": 0}
        assert proj2.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
        proj2.close()


def test_restore_rejects_bad_format_and_labels():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        with pytest.raises(ValueError, match="Unsupported dump format"):
            restore_graph(proj.g, {"format": "nope", "nodes": []})
        with pytest.raises(ValueError, match="Unsupported dump format"):
            restore_graph(proj.g, [])  # non-dict dump
        with pytest.raises(ValueError, match="Unsafe graph label"):
            restore_graph(proj.g, {
                "format": DUMP_FORMAT,
                "nodes": [{"dump_id": 1, "labels": ["Point; DROP"], "props": {}}],
                "edges": [],
            })
        with pytest.raises(ValueError, match="missing dump_id"):
            restore_graph(proj.g, {
                "format": DUMP_FORMAT,
                "nodes": [{"labels": ["Point"], "props": {}}],
                "edges": [],
            })
        with pytest.raises(ValueError, match="Malformed dump edge"):
            restore_graph(proj.g, {
                "format": DUMP_FORMAT,
                "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {}},
                           {"dump_id": 2, "labels": ["Point"], "props": {}}],
                "edges": [{"src": 1, "dst": 2, "props": {}}],  # no type
            })
        with pytest.raises(ValueError, match="Unsafe graph label"):
            restore_graph(proj.g, {
                "format": DUMP_FORMAT,
                "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {}},
                           {"dump_id": 2, "labels": ["Point"], "props": {}}],
                "edges": [{"src": 1, "dst": 2, "type": "IMPL; DROP", "props": {}}],
            })
        with pytest.raises(ValueError, match="reserved property"):
            restore_graph(proj.g, {
                "format": DUMP_FORMAT,
                "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"__dump_id": "user-value"}}],
                "edges": [],
            })
        proj.close()


def test_restore_rejects_dangling_edge():
    """A dump whose edge references a missing node must fail loudly — never a
    silent partial restore."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        dump = {
            "format": DUMP_FORMAT,
            "nodes": [{"dump_id": 1, "labels": ["Point"], "props": {"id": "p1"}}],
            "edges": [{"src": 1, "dst": 99, "type": "IMPL", "props": {}}],
        }
        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_graph(proj.g, dump)
        proj.close()


# ── pipeline (MemoryStorage) ─────────────────────────────────────────────────


def test_create_backup_list_and_restore_swap(monkeypatch):
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        source_dump = dump_graph(proj.g, graph_name="tortoise")
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")

        store = MemoryStorage()
        manifest = create_backup(
            proj, registry, store, team_id="team_x", graph_name="tortoise"
        )
        assert manifest["node_count"] == 6
        assert manifest["edge_count"] == 5
        assert manifest["team_id"] == "team_x"
        assert manifest["graph_name"] == "tortoise"
        assert manifest["backup_id"].startswith("team_x/")
        # both objects uploaded
        objs = store.list("backups/team_x/")
        assert any(k.endswith("dump.enc") for k in objs)
        assert any(k.endswith("manifest.json") for k in objs)
        # sha256 matches the stored blob (not just key presence)
        dump_key = [k for k in objs if k.endswith("dump.enc")][0]  # noqa: RUF015
        assert manifest["sha256"] == hashlib.sha256(store.download(dump_key)).hexdigest()
        # registry stamped
        row = registry.query(
            "MATCH (t:Team {id:'team_x'}) RETURN t.backup_latest_at"
        ).result_set
        assert row and row[0][0]

        # list
        listed = list_backups(store, "team_x")
        assert len(listed) == 1
        assert listed[0]["node_count"] == 6

        # Mutate the live graph AFTER backup — restore must prove the swap
        # replaced content, not just that counts happen to match.
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'mutated'})")
        proj.g.query("MATCH (p:Point {id:'pt-0'}) SET p.content = 'corrupted'")

        # restore → swap
        result = restore_backup(
            proj.db, registry, store, dump_key,
            team_id="team_x", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        assert result["restored"]["edges"] == manifest["edge_count"]
        # live graph (re-selected — handle may be stale after swap) holds the
        # BACKUP content: full content equality vs the source dump
        live = proj.db.select_graph("tortoise")
        live_dump = dump_graph(live, graph_name="tortoise")
        assert _norm_nodes(live_dump["nodes"]) == _norm_nodes(source_dump["nodes"])
        assert _norm_edges(live_dump["edges"], live_dump["nodes"]) == _norm_edges(
            source_dump["edges"], source_dump["nodes"]
        )
        assert live.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 0
        # staging graph cleaned up on success (no temp leak) — exact set, not
        # naming-coupled substring
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        # registry restore stamp
        row = registry.query(
            "MATCH (t:Team {id:'team_x'}) RETURN t.backup_restored_at"
        ).result_set
        assert row and row[0][0]
        proj.close()


def test_dump_restore_unlabeled_node():
    """FalkorDB allows unlabeled nodes — restore must preserve them."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        proj.g.query("CREATE (n {id:'bare'})")
        dump = dump_graph(proj.g, graph_name="tortoise")
        assert dump["node_count"] == 1
        assert dump["nodes"][0]["labels"] == []
        proj.close()

        proj2 = _make_proj(tmp, "t2.db")
        counts = restore_graph(proj2.g, dump)
        assert counts == {"nodes": 1, "edges": 0}
        row = proj2.g.query("MATCH (n) RETURN labels(n), n.id").result_set
        assert row[0][1] == "bare"
        proj2.close()


def test_dump_restore_embedding_prop():
    """Embedding props survive dump/restore (restore re-encodes vecf32 like the
    SDK write path — sdk.py SET n.embedding = vecf32($embedding))."""
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        proj.g.query("CREATE (p:Point {id:'p1'})")
        proj.g.query(
            "MATCH (p:Point {id:$i}) SET p.embedding = vecf32($v)",
            params={"i": "p1", "v": [0.1, 0.2, 0.3]},
        )
        dump = dump_graph(proj.g, graph_name="tortoise")
        proj.close()

        proj2 = _make_proj(tmp, "t2.db")
        restore_graph(proj2.g, dump)  # must not raise
        emb = proj2.g.query(
            "MATCH (p {id:'p1'}) RETURN p.embedding"
        ).result_set[0][0]
        assert list(emb) == pytest.approx([0.1, 0.2, 0.3])  # float32 rounding
        proj2.close()


def test_empty_graph_full_pipeline(monkeypatch):
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_e', tier:'pro'})")
        store = MemoryStorage()
        manifest = create_backup(proj, registry, store, team_id="team_e", graph_name="tortoise")
        assert manifest["node_count"] == 0
        dump_key = [k for k in store.list("backups/team_e/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        result = restore_backup(
            proj.db, registry, store, dump_key,
            team_id="team_e", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 0, "edges": 0}
        live = proj.db.select_graph("tortoise")
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        proj.close()


def test_restore_empty_backup_over_live_rejected(monkeypatch):
    """Issue #101 class: an empty backup must never replace live data."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_e', tier:'pro'})")
        store = MemoryStorage()
        # empty backup first
        create_backup(proj, registry, store, team_id="team_e", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_e/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        # then seed live data
        _seed(proj.g)
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(RestoreVerificationError, match="empty backup"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_e", graph_name="tortoise",
            )
        # live data untouched
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 7
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_layer1_key_prefix_guard(monkeypatch):
    """Isolate layer 1 (key-prefix guard): manifest passes its own check, only
    the key prefix can reject the request."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        # manifest claims team_y (so the MANIFEST guard would PASS for team_y);
        # the key lives under backups/team_x/ — only the key-prefix guard fires
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        manifest = json.loads(store.download(manifest_key))
        manifest["team_id"] = "team_y"
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-team"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_y", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_cross_graph(monkeypatch):
    """Graph isolation within a team: backup of graph A cannot restore into graph B."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-graph"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="other_graph",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        # the target graph was never created
        assert "other_graph" not in set(proj.db.list_graphs())
        proj.close()


def test_restore_rejects_manifest_missing_sha256(monkeypatch):
    """Fail-closed integrity: manifest without sha256 is rejected."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        manifest = json.loads(store.download(manifest_key))
        del manifest["sha256"]
        store.upload(manifest_key, json.dumps(manifest).encode())

        with pytest.raises(ValueError, match="missing sha256"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_cross_team_backup(monkeypatch):
    """P0 tenant isolation layer 1: key-prefix guard."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-team"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_y", graph_name="tortoise",
            )
        # live graph untouched (marker proves no wrongful swap — backup and
        # live both have 6 nodes, so a count assert would be ambiguous)
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_manifest_team_mismatch(monkeypatch):
    """P0 tenant isolation layer 2: manifest team_id guard (key prefix passes,
    manifest claims another team)."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        # rewrite manifest under the same key to claim a DIFFERENT team
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        manifest = json.loads(store.download(manifest_key))
        manifest["team_id"] = "team_y"
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-team"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_unreadable_manifest(monkeypatch):
    """A corrupt manifest must NOT silently disable verification."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        store.upload(manifest_key, b"{not json")
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="manifest unreadable"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_verify_count_mismatch_keeps_live_graph(monkeypatch):
    """#1625: verification keys off the AUTHENTICATED payload node LIST, not
    the forgeable plaintext manifest node_count. Forge the manifest's
    node_count (the plaintext a naive verifier would trust) → restore must
    still succeed and swap (the guard derives expected from the payload's
    non-skip nodes, which are sha256-authenticated)."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        # Forge the MANIFEST's node_count to 999 (plaintext, not part of the
        # payload sha256 chain) — a verifier that trusted the manifest would
        # reject this restore; the guard must key off the payload instead.
        manifest = json.loads(store.download(manifest_key))
        manifest["node_count"] = 999
        store.upload(manifest_key, json.dumps(manifest).encode())

        # mark the live graph so a successful swap is provable
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        # restore must SUCCEED (verification derives from the authenticated
        # payload nodes list, not the forgeable manifest node_count)
        restore_backup(
            proj.db, registry, store, dump_key,
            team_id="team_x", graph_name="tortoise",
        )
        proj.close()


def test_restore_integrity_failure_keeps_live_graph(monkeypatch):
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015

        # corrupt the stored blob → sha256 mismatch
        corrupt = bytearray(store.download(dump_key))
        corrupt[5] ^= 0x01
        store.upload(dump_key, bytes(corrupt))
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="integrity"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        # live graph untouched (marker proves no wrongful swap)
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_copy_failure_leaves_temp_intact(monkeypatch):
    """Swap failure: live graph deleted, verified temp graph remains recoverable."""
    from falkordb import Graph

    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015

        def _boom_copy(self, clone):
            if clone == "tortoise":  # only the temp→live promotion fails
                raise RuntimeError("copy boom")
            return real_copy(self, clone)

        real_copy = Graph.copy
        monkeypatch.setattr(Graph, "copy", _boom_copy)
        with pytest.raises(RuntimeError, match="copy boom"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        # live graph is GONE (deleted before copy) and the verified temp graph
        # survives with full content — the documented recovery copy. The
        # pre-restore safety copy of the original live graph also survives.
        graphs = proj.db.list_graphs()
        assert "tortoise" not in graphs
        extras = [g for g in graphs if g != "registry_tortoise"]
        assert len(extras) == 2  # _restore_ (verified) + _pre_restore_ (original)
        for g in extras:
            assert proj.db.select_graph(g).query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        proj.close()


def test_restore_verify_edge_count_mismatch(monkeypatch):
    """Second verification guard: payload edge_count mismatch → no swap."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        payload = json.loads(decrypt_backup(store.download(dump_key)))
        payload["edge_count"] = 99
        new_blob = encrypt_backup(json.dumps(payload).encode())
        store.upload(dump_key, new_blob)
        manifest = json.loads(store.download(manifest_key))
        manifest["sha256"] = hashlib.sha256(new_blob).hexdigest()
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")
        pre_graphs = set(proj.db.list_graphs())

        with pytest.raises(RuntimeError, match="verification failed"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        assert set(proj.db.list_graphs()) == pre_graphs
        proj.close()


def test_restore_rejects_non_dump_payload(monkeypatch):
    """Decrypted payload that isn't a logical dump → ValueError, live untouched."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        # replace with a validly-encrypted but non-dump payload
        new_blob = encrypt_backup(json.dumps({"format": "nope"}).encode())
        store.upload(dump_key, new_blob)
        manifest = json.loads(store.download(manifest_key))
        manifest["sha256"] = hashlib.sha256(new_blob).hexdigest()
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="not a tortoise logical dump"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_missing_object(monkeypatch):
    """Nonexistent dump.enc → clean ValueError, live graph untouched."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="not found"):
            restore_backup(
                proj.db, registry, store, "backups/team_x/nonexistent/dump.enc",
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_rejects_manifest_missing_team_id(monkeypatch):
    """Fail-closed tenant isolation: manifest WITHOUT team_id is rejected."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        manifest = json.loads(store.download(manifest_key))
        del manifest["team_id"]
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-team"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_registry_stamp_failure_is_best_effort(monkeypatch):
    """Restore succeeds even when the registry stamp fails (best-effort)."""
    from falkordb import Graph  # noqa: F401

    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()
        manifest = create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")  # noqa: F841
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015

        class RegistryDown:
            def query(self, *a, **k):
                raise RuntimeError("registry down")

        # marker proves the swap executed despite the registry failure — planted
        # on the live graph BEFORE restore, gone after
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")
        result = restore_backup(
            proj.db, RegistryDown(), store, dump_key,
            team_id="team_x", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        live = proj.db.select_graph("tortoise")
        assert live.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 0
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        assert live.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0] == 5
        proj.close()


def test_create_backup_distinct_ids_within_second(monkeypatch):
    """Two backups in the same second must not collide on object keys."""
    from tortoise import hosted_backup as hb

    _set_env_key(monkeypatch)

    class _TickDatetime(datetime):
        _n = 0

        @classmethod
        def now(cls, tz=None):
            _TickDatetime._n += 1
            return datetime(2026, 8, 7, 12, 0, 0, microsecond=_TickDatetime._n * 1000, tzinfo=timezone.utc)  # noqa: UP017

    monkeypatch.setattr(hb, "datetime", _TickDatetime)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        m1 = create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        m2 = create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        # same second (timestamp portion before the random suffix), distinct ids
        assert m1["backup_id"].split("/")[1].split("_")[0][:15] == \
            m2["backup_id"].split("/")[1].split("_")[0][:15]
        assert m1["backup_id"] != m2["backup_id"]
        dumps = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")]
        assert len(dumps) == 2  # first backup not silently overwritten
        proj.close()


def test_restore_payload_missing_counts_rejected(monkeypatch):
    """Fail-closed verification: payload without node_count/edge_count is rejected."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        payload = json.loads(decrypt_backup(store.download(dump_key)))
        # #1625: verification derives expected_nodes from the payload's non-skip
        # nodes list — a payload with no nodes list has nothing to verify
        # against and must be rejected (fail-closed).
        del payload["nodes"]
        new_blob = encrypt_backup(json.dumps(payload).encode())
        store.upload(dump_key, new_blob)
        manifest = json.loads(store.download(manifest_key))
        manifest["sha256"] = hashlib.sha256(new_blob).hexdigest()
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="missing nodes list"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        proj.close()


def test_restore_payload_graph_name_guard(monkeypatch):
    """Authenticated graph isolation: forged payload graph_name rejected even
    when the (unauthenticated) manifest passes."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        payload = json.loads(decrypt_backup(store.download(dump_key)))
        payload["graph_name"] = "other_graph"
        new_blob = encrypt_backup(json.dumps(payload).encode())
        store.upload(dump_key, new_blob)
        manifest = json.loads(store.download(manifest_key))
        manifest["sha256"] = hashlib.sha256(new_blob).hexdigest()
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="cross-graph"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_staging_cleaned_on_validation_failure(monkeypatch):
    """restore_graph raising (dangling edge) must not leave staging garbage."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")

        payload = json.loads(decrypt_backup(store.download(dump_key)))
        payload["edges"].append({"src": 0, "dst": 99999, "type": "IMPL", "props": {}})
        payload["edge_count"] = len(payload["edges"])
        new_blob = encrypt_backup(json.dumps(payload).encode())
        store.upload(dump_key, new_blob)
        manifest = json.loads(store.download(manifest_key))
        manifest["sha256"] = hashlib.sha256(new_blob).hexdigest()
        manifest["edge_count"] = payload["edge_count"]
        store.upload(manifest_key, json.dumps(manifest).encode())
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="Edge restore incomplete"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        # live untouched AND no staging residue
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        proj.close()


def test_create_backup_orphan_rollback_on_manifest_failure(monkeypatch):
    """Failed manifest upload rolls back the already-stored blob (no orphan)."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")

        class FailingManifestStore(MemoryStorage):
            def upload(self, key, data, content_type=None):
                if key.endswith("manifest.json"):
                    raise RuntimeError("R2 transient failure")
                super().upload(key, data, content_type=content_type)

        store = FailingManifestStore()
        with pytest.raises(RuntimeError, match="transient"):
            create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        # no orphaned blob left behind
        assert store.list("backups/team_x/") == []
        proj.close()


def test_prune_skips_foreign_backup_id(monkeypatch):
    """Delete-path trust: a manifest under this team's prefix whose backup_id
    claims another team must NOT trigger deletion of foreign objects."""
    store = MemoryStorage()
    team_a, team_b = "team_a", "team_b"
    _seed_old_backups(store, team_a, [40])
    _seed_old_backups(store, team_b, [40])
    # plant a forged manifest under team_a's prefix claiming team_b's backup_id
    ids_b = [k for k in store.list(f"backups/{team_b}/") if k.endswith("manifest.json")]
    foreign_bid = ids_b[0].split("/")[1] + "/" + ids_b[0].split("/")[2] if "/" in ids_b[0] else ""
    # backup_id = team_b/<ts>
    forged = {
        "backup_id": foreign_bid,
        "team_id": "team_b",
        "graph_name": "tortoise",
        "created_at": (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(),  # noqa: UP017
        "node_count": 1, "edge_count": 0, "sha256": "0" * 64, "format": DUMP_FORMAT,
    }
    store.upload(f"backups/{team_a}/forged/manifest.json", json.dumps(forged).encode())
    store.upload(f"backups/{team_a}/forged/dump.enc", b"blob")

    prune_backups(store, team_a)  # must not crash or delete team_b's objects
    assert len([k for k in store.list(f"backups/{team_b}/") if k.endswith("manifest.json")]) == 1
    # forged manifest itself is skipped (not deleted under team_a)
    assert any("forged" in k for k in store.list(f"backups/{team_a}/"))


def test_restore_rejects_non_dict_manifest(monkeypatch):
    """Valid JSON that isn't an object (list/string) → clean ValueError, not 500."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        manifest_key = dump_key.replace("/dump.enc", "/manifest.json")
        store.upload(manifest_key, b'[1,2,3]')
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        with pytest.raises(ValueError, match="manifest unreadable"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        proj.close()


def test_restore_wrong_key_keeps_live_graph(monkeypatch):
    """Key rotation / env mismatch at restore: decrypt fails, live untouched."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        proj.g.query("CREATE (x:Point {id:'pt-x', content:'marker'})")

        wrong_key = os.urandom(32)
        with pytest.raises(ValueError, match="Cannot restore"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise", key=wrong_key,
            )
        assert proj.g.query("MATCH (p:Point {id:'pt-x'}) RETURN count(p)").result_set[0][0] == 1
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        proj.close()


def test_prune_intra_team_forged_backup_id(monkeypatch):
    """Delete-path trust: a forged manifest claiming a NEWER backup's id under
    an old key must not delete the newer backup's objects."""
    store = MemoryStorage()
    team_id = "team_p"
    ids = _seed_old_backups(store, team_id, [30, 1])  # old + newest
    newest = ids[1]
    # forge: manifest under the OLD key claiming the NEWEST backup's id
    old_key = f"backups/{ids[30]}/manifest.json"
    forged = json.loads(store.download(old_key))
    forged["backup_id"] = newest
    store.upload(old_key, json.dumps(forged).encode())

    prune_backups(store, team_id)
    # the newest backup's objects survive (deletion is key-derived, not
    # manifest-declared)
    assert any(newest in k for k in store.list(f"backups/{team_id}/"))


def test_team_id_validation(monkeypatch):
    """Path-injecting team_ids are rejected before they touch object keys."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()
        with pytest.raises(ValueError, match="Invalid team_id"):
            create_backup(proj, registry, store, team_id="x/y", graph_name="tortoise")
        with pytest.raises(ValueError, match="Invalid team_id"):
            restore_backup(proj.db, registry, store, "backups/x/y/a/dump.enc",
                           team_id="x/y", graph_name="tortoise")
        with pytest.raises(ValueError, match="Invalid team_id"):
            prune_backups(store, "x/y")
        proj.close()


def test_restore_live_delete_failure_leaves_recovery_copy(monkeypatch):
    """live_g.delete() raising: live graph untouched, verified temp survives."""
    from falkordb import Graph

    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015

        real_delete = Graph.delete

        def _boom_delete(self):
            if self.name == "tortoise":
                raise RuntimeError("delete boom")
            return real_delete(self)

        monkeypatch.setattr(Graph, "delete", _boom_delete)
        with pytest.raises(RuntimeError, match="Restore swap failed"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        # live graph intact (delete failed → copy onto existing key fails) and
        # the verified temp recovery copy + pre-restore snapshot exist
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        extras = [g for g in proj.db.list_graphs() if g not in ("tortoise", "registry_tortoise")]
        assert len(extras) == 2  # _restore_ + _pre_restore_
        for g in extras:
            assert proj.db.select_graph(g).query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        proj.close()


def test_restore_into_missing_live_graph(monkeypatch):
    """Disaster recovery: restore into a DROPPED/lost live graph succeeds —
    delete is best-effort, the verified temp seeds the missing graph."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        # simulate the DR incident: the live graph is gone
        proj.g.delete()

        result = restore_backup(
            proj.db, registry, store, dump_key,
            team_id="team_x", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        live = proj.db.select_graph("tortoise")
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        assert live.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0] == 5
        # no staging residue
        assert set(proj.db.list_graphs()) == {"tortoise", "registry_tortoise"}
        proj.close()


def test_r2storage_env_construction(monkeypatch):
    """Production constructor path: R2Storage() derives endpoint + creds from
    env (hosted_api._backup_storage uses exactly this)."""
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak_env")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk_env")
    monkeypatch.setenv("R2_BUCKET", "bkt_env")
    fake = _FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(fake))

    store = R2Storage()  # no kwargs — the hosted API path
    store.upload("k", b"d")
    assert fake.objects["k"] == b"d"
    assert fake.client_kwargs["endpoint_url"] == "https://acct123.r2.cloudflarestorage.com"
    assert fake.client_kwargs["aws_access_key_id"] == "ak_env"
    assert fake.client_kwargs["aws_secret_access_key"] == "sk_env"


def test_prune_leaves_unpaired_orphan(monkeypatch):
    """Documented behavior: a manifest-less dump.enc (crash artifact) is not
    touched by prune — new orphans are prevented by create_backup rollback."""
    store = MemoryStorage()
    team_id = "team_o"
    _seed_old_backups(store, team_id, [40])
    orphan_key = f"backups/{team_id}/orphan/dump.enc"
    store.upload(orphan_key, b"blob")

    prune_backups(store, team_id)  # no crash
    assert any(k == orphan_key for k in store.list(f"backups/{team_id}/"))


def test_registry_stamp_lands_in_canonical_registry(monkeypatch):
    """The API wiring (registry-namespaced SDK) must stamp the canonical
    registry_control_plane graph — not a per-team phantom graph. This is the
    wiring the code-review flagged: team-namespaced sdk._get_registry() resolves
    to {team}_control_plane, which never contains Team nodes."""
    from tortoise.sdk import TortoiseSDK

    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "t.db")
        reg_sdk = TortoiseSDK(db_path=db_path, namespace="registry")
        team_sdk = TortoiseSDK(db_path=db_path, namespace="team_x")
        # seed a Team node in the canonical registry (as provision does)
        reg_sdk._get_registry().query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        _seed(team_sdk._get_proj().g)
        store = MemoryStorage()

        manifest = create_backup(
            team_sdk._get_proj(), reg_sdk._get_registry(), store,
            team_id="team_x", graph_name="team_team_x",
        )
        assert manifest["node_count"] == 6
        # stamp lands in the canonical registry graph
        row = reg_sdk._get_registry().query(
            "MATCH (t:Team {id:'team_x'}) RETURN t.backup_latest_at"
        ).result_set
        assert row and row[0][0]
        team_sdk.close()
        reg_sdk.close()


def test_restore_live_check_fail_closed_when_list_graphs_fails(monkeypatch):
    """Live-count query AND list_graphs both failing → RestoreVerificationError
    (fail closed), temp graph cleaned, live untouched — never a raw 500."""
    from falkordb import Graph

    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_x', tier:'pro'})")
        store = MemoryStorage()
        create_backup(proj, registry, store, team_id="team_x", graph_name="tortoise")
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015

        real_query = Graph.query
        real_list = type(proj.db).list_graphs

        def _boom_live_query(self, cypher, *a, **k):
            if "RETURN count(n)" in cypher and self.name == "tortoise":
                raise ConnectionError("connection died")
            return real_query(self, cypher, *a, **k)

        def _boom_list_graphs(self):
            raise ConnectionError("connection died")

        monkeypatch.setattr(Graph, "query", _boom_live_query)
        monkeypatch.setattr(type(proj.db), "list_graphs", _boom_list_graphs)
        with pytest.raises(RestoreVerificationError, match="fail closed"):
            restore_backup(
                proj.db, registry, store, dump_key,
                team_id="team_x", graph_name="tortoise",
            )
        # restore the real methods, then assert: live untouched, no staging residue
        monkeypatch.setattr(Graph, "query", real_query)
        monkeypatch.setattr(type(proj.db), "list_graphs", real_list)
        assert "tortoise" in set(proj.db.list_graphs())
        assert not any("_restore_" in g or "_pre_restore_" in g for g in proj.db.list_graphs())
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        proj.close()


def test_restore_rejects_non_dump_key():
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()
        with pytest.raises(ValueError, match="dump.enc"):  # noqa: RUF043
            restore_backup(
                proj.db, registry, store, "backups/team_x/x/manifest.json",
                team_id="team_x", graph_name="tortoise",
            )
        proj.close()


def test_create_backup_default_graph_name(monkeypatch):
    """graph_name=None → manifest records the projection's actual graph name."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")
        registry.query("CREATE (t:Team {id:'team_t', tier:'pro'})")
        store = MemoryStorage()
        manifest = create_backup(proj, registry, store, team_id="team_t")
        assert manifest["graph_name"] == proj.g.name  # falls back to the real graph name
        proj.close()


def test_create_backup_registry_stamp_failure_is_best_effort(monkeypatch):
    """Registry stamp is best-effort — backup stays durable if it fails."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)

        class RegistryDown:
            def query(self, *a, **k):
                raise RuntimeError("registry down")

        store = MemoryStorage()
        manifest = create_backup(proj, RegistryDown(), store, team_id="team_x", graph_name="tortoise")
        assert manifest["node_count"] == 6
        assert any(k.endswith("dump.enc") for k in store.list("backups/team_x/"))
        proj.close()


# ── R2 storage ───────────────────────────────────────────────────────────────


def test_r2storage_download_missing_key_normalized(monkeypatch):
    """R2Storage.download normalizes botocore NoSuchKey → KeyError so the
    pipeline maps it to a clean ValueError (Backup object not found)."""
    pytest.importorskip("botocore.exceptions")
    store, fake = _r2_with_fake_boto3(monkeypatch)  # noqa: RUF059
    with pytest.raises(KeyError):
        store.download("backups/nope/dump.enc")


def test_r2storage_non_missing_error_not_swallowed(monkeypatch):
    """Only NoSuchKey is normalized — other S3 errors surface as RuntimeError
    (the pipeline's 503 mapping), never as a silent empty result."""
    pytest.importorskip("botocore.exceptions")
    from botocore.exceptions import ClientError

    store, fake = _r2_with_fake_boto3(monkeypatch)

    def _access_denied(self, Bucket, Key):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject")

    monkeypatch.setattr(type(fake), "get_object", _access_denied)
    with pytest.raises(RuntimeError, match="R2 download failed"):
        store.download("backups/x/dump.enc")


def test_r2storage_requires_config(monkeypatch):
    for var in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="R2 not configured"):
        R2Storage()


def test_r2storage_missing_boto3(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("R2_BUCKET", "bkt")
    store = R2Storage()

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "boto3":
            raise ImportError("no boto3")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(RuntimeError, match="boto3 not installed"):
        store.upload("k", b"data")


class _FakeS3:
    """Recording S3 client — exercises R2Storage's real plumbing."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.client_kwargs: dict | None = None
        self.put_calls: list[tuple[str, dict]] = []

    def put_object(self, Bucket, Key, Body, **kw):
        self.objects[Key] = Body
        self.put_calls.append((Key, kw))

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            try:
                from botocore.exceptions import ClientError
                raise ClientError(
                    {"Error": {"Code": "NoSuchKey", "Message": "not found"}},
                    "GetObject",
                )
            except ImportError:
                raise KeyError(Key)  # noqa: B904
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        assert name == "list_objects_v2"

        class _Paginator:
            def __init__(self, s3):
                self.s3 = s3

            def paginate(self, **kwargs):
                keys = sorted(k for k in self.s3.objects if k.startswith(kwargs["Prefix"]))
                for i in range(0, len(keys), 2):  # 2-object pages — exercises the loop
                    yield {"Contents": [{"Key": k} for k in keys[i:i + 2]]}
                yield {}  # trailing page WITHOUT the Contents key — exercises the
                # real code's `page.get("Contents", [])` branch

        return _Paginator(self)


class _FakeBoto3:
    def __init__(self, fake):
        self._fake = fake

    def client(self, *args, **kwargs):
        self._fake.client_kwargs = kwargs
        return self._fake


def _r2_with_fake_boto3(monkeypatch) -> tuple[R2Storage, _FakeS3]:
    fake = _FakeS3()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(fake))
    store = R2Storage(
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        access_key_id="ak",
        secret_access_key="sk",
        bucket="tortoise-backups",
    )
    return store, fake


def test_r2storage_happy_path(monkeypatch):
    store, fake = _r2_with_fake_boto3(monkeypatch)
    store.upload("backups/a/b/dump.enc", b"blob", content_type="application/octet-stream")
    store.upload("backups/a/b/manifest.json", b"{}", content_type="application/json")
    store.upload("backups/c/d/manifest.json", b"{}")

    # put plumbing: content_type passthrough + endpoint wiring
    assert fake.objects["backups/a/b/dump.enc"] == b"blob"
    assert fake.client_kwargs["endpoint_url"] == "https://acct.r2.cloudflarestorage.com"
    ct = dict(fake.put_calls)["backups/a/b/manifest.json"]["ContentType"]
    assert ct == "application/json"

    # download roundtrip
    assert store.download("backups/a/b/dump.enc") == b"blob"

    # list filters by prefix (paginator loop exercised with 2-item pages)
    assert store.list("backups/a/") == ["backups/a/b/dump.enc", "backups/a/b/manifest.json"]
    assert store.list("backups/c/") == ["backups/c/d/manifest.json"]
    assert store.list("backups/none/") == []

    # delete removes the object
    store.delete("backups/c/d/manifest.json")
    assert store.list("backups/c/") == []


# ── listing / retention ──────────────────────────────────────────────────────


def _seed_old_backups(store: MemoryStorage, team_id: str, days_ago: list[int]) -> dict[int, str]:
    """Seed backups at day offsets; returns {days_ago: backup_id}.

    IDs are derived from the SAME timestamp as created_at (consistent test
    data), with a -1h margin so the `.days` floor never flips the boundary
    (day-7 → age 7.04d, day-6 → age 6.04d).
    """
    now = datetime.now(timezone.utc)  # noqa: UP017
    ids: dict[int, str] = {}
    for i, d in enumerate(days_ago):  # noqa: B007
        created = now - timedelta(days=d) - timedelta(hours=1)
        backup_id = f"{team_id}/{created.strftime('%Y%m%dT%H%M%SZ')}"
        ids[d] = backup_id
        manifest = {
            "backup_id": backup_id,
            "team_id": team_id,
            "graph_name": "tortoise",
            "created_at": created.isoformat(),
            "node_count": 10,
            "edge_count": 3,
            "sha256": "0" * 64,
            "format": DUMP_FORMAT,
        }
        store.upload(f"backups/{backup_id}/manifest.json",
                     json.dumps(manifest).encode())
        store.upload(f"backups/{backup_id}/dump.enc", b"blob")
    return ids


def test_prune_retention_keeps_daily_and_weekly():
    store = MemoryStorage()
    team_id = "team_y"
    # 15 backups: 7 inside daily window, 8 older (each a distinct ISO week)
    days_ago = [0, 1, 2, 3, 4, 5, 6, 7, 14, 21, 28, 35, 42, 49, 56]
    ids = _seed_old_backups(store, team_id, days_ago)

    deleted = prune_backups(store, team_id, keep_daily=7, keep_weekly=4)
    # Weekly anchors are the NEWEST of each distinct older ISO week:
    # days 7, 14, 21, 28 are kept; days 35, 42, 49, 56 are deleted.
    assert sorted(deleted) == sorted([ids[d] for d in (35, 42, 49, 56)])
    remaining = [k for k in store.list(f"backups/{team_id}/") if k.endswith("manifest.json")]
    assert len(remaining) == 11  # 7 daily + 4 weekly
    for d in (0, 1, 2, 3, 4, 5, 6, 7, 14, 21, 28):
        assert any(ids[d] in k for k in remaining), f"backup at day {d} should be kept"


def test_prune_keeps_newest_backup_of_each_week():
    """Weekly-anchor rule: when an older ISO week has 2 backups, only the
    newest survives."""
    now = datetime.now(timezone.utc)  # noqa: UP017
    monday_anchor = (now.date() - timedelta(days=now.date().weekday() + 21))  # 3 weeks back
    friday = datetime.combine(monday_anchor + timedelta(days=4), datetime.min.time(), tzinfo=timezone.utc)  # noqa: UP017
    sunday = datetime.combine(monday_anchor + timedelta(days=6), datetime.min.time(), tzinfo=timezone.utc)  # noqa: UP017
    assert friday.isocalendar()[:2] == sunday.isocalendar()[:2]  # same ISO week

    store = MemoryStorage()
    team_id = "team_t"
    ids: dict[str, str] = {}
    for label, created in (("old", friday), ("new", sunday)):
        bid = f"{team_id}/{created.strftime('%Y%m%dT%H%M%SZ')}"
        ids[label] = bid
        store.upload(
            f"backups/{bid}/manifest.json",
            json.dumps({
                "backup_id": bid, "team_id": team_id, "graph_name": "tortoise",
                "created_at": created.isoformat(), "node_count": 10, "edge_count": 3,
                "sha256": "0" * 64, "format": DUMP_FORMAT,
            }).encode(),
        )
        store.upload(f"backups/{bid}/dump.enc", b"blob")

    deleted = prune_backups(store, team_id, keep_daily=7, keep_weekly=4)
    assert ids["old"] in deleted
    assert ids["new"] not in deleted


def test_prune_deletes_corrupt_created_at():
    store = MemoryStorage()
    team_id = "team_w"
    _seed_old_backups(store, team_id, [20])  # valid old backup (weekly anchor)
    corrupt_id = f"{team_id}/20260801T000000Z"
    store.upload(
        f"backups/{corrupt_id}/manifest.json",
        json.dumps({
            "backup_id": corrupt_id,
            "team_id": team_id,
            "graph_name": "tortoise",
            "created_at": "not-a-date",
            "node_count": 1, "edge_count": 0, "sha256": "0" * 64, "format": DUMP_FORMAT,
        }).encode(),
    )
    store.upload(f"backups/{corrupt_id}/dump.enc", b"blob")

    deleted = prune_backups(store, team_id)
    assert corrupt_id in deleted
    assert not any(k.startswith(f"backups/{corrupt_id}") for k in store.list(f"backups/{team_id}/"))


def test_prune_keeps_only_own_team():
    """Pruning must never touch another tenant's prefix."""
    store = MemoryStorage()
    team_a, team_b = "team_a", "team_b"
    # team_a: 5 distinct older ISO weeks → 4 anchors kept, 5th deleted
    ids_a = _seed_old_backups(store, team_a, [30, 37, 44, 51, 58])
    # team_b: 1 old backup → kept as its own weekly anchor
    ids_b = _seed_old_backups(store, team_b, [30])

    prune_backups(store, team_a)
    # team_b's old backup untouched (prune is prefix-scoped)
    assert any(ids_b[30] in k for k in store.list(f"backups/{team_b}/"))
    # team_a: the 5th-week backup deleted, the 4 anchors + daily kept
    assert not any(ids_a[58] in k for k in store.list(f"backups/{team_a}/"))
    assert any(ids_a[30] in k for k in store.list(f"backups/{team_a}/"))


def test_prune_zero_windows_deletes_all():
    store = MemoryStorage()
    team_id = "team_u"
    ids = _seed_old_backups(store, team_id, [1, 8])
    deleted = prune_backups(store, team_id, keep_daily=0, keep_weekly=0)
    assert sorted(deleted) == sorted(ids.values())
    assert store.list(f"backups/{team_id}/") == []


def test_prune_fewer_than_window_keeps_all():
    store = MemoryStorage()
    team_id = "team_s"
    ids = _seed_old_backups(store, team_id, [0, 1, 2])  # noqa: F841
    assert prune_backups(store, team_id, keep_daily=7, keep_weekly=4) == []
    assert len([k for k in store.list(f"backups/{team_id}/") if k.endswith("manifest.json")]) == 3


def test_prune_handles_naive_created_at():
    """A parseable-but-naive created_at must not crash the whole team's prune."""
    store = MemoryStorage()
    team_id = "team_n"
    ids = _seed_old_backups(store, team_id, [1])  # valid recent backup (kept)
    naive_id = f"{team_id}/20260701T000000Z"
    store.upload(
        f"backups/{naive_id}/manifest.json",
        json.dumps({
            "backup_id": naive_id, "team_id": team_id, "graph_name": "tortoise",
            "created_at": "2026-07-01T00:00:00",  # naive — no tz
            "node_count": 1, "edge_count": 0, "sha256": "0" * 64, "format": DUMP_FORMAT,
        }).encode(),
    )
    store.upload(f"backups/{naive_id}/dump.enc", b"blob")

    deleted = prune_backups(store, team_id)  # must NOT raise TypeError
    # the valid daily backup is never touched
    assert any(ids[1] in k for k in store.list(f"backups/{team_id}/"))
    # the naive backup (2026-07-01, old) is deterministically kept as the
    # team's only weekly anchor — never dropped, never crashes pruning
    assert deleted == []


def test_prune_weekly_cap_drops_oldest_weeks(monkeypatch):
    """Weekly cap binding: when weeks exceed keep_weekly, the OLDEST weeks are
    dropped entirely (even if they hold multiple backups)."""
    from tortoise import hosted_backup as hb

    _freeze_clock(monkeypatch)  # run-date independent (daily/weekly boundary)
    store = MemoryStorage()
    team_id = "team_r"
    now = hb.datetime.now(timezone.utc)  # SAME clock prune_backups will use  # noqa: UP017
    monday_anchor = now.date() - timedelta(days=now.date().weekday() + 21)
    weeks = [
        ("w1a", monday_anchor + timedelta(days=4)),  # Fri (older W1 copy)
        ("w1b", monday_anchor + timedelta(days=6)),  # Sun (newer W1 copy)
        ("w2", monday_anchor + timedelta(days=11)),
        ("w3", monday_anchor + timedelta(days=18)),
    ]
    ids: dict[str, str] = {}
    for label, created in weeks:
        bid = f"{team_id}/{created.strftime('%Y%m%dT%H%M%SZ')}"
        ids[label] = bid
        store.upload(
            f"backups/{bid}/manifest.json",
            json.dumps({
                "backup_id": bid, "team_id": team_id, "graph_name": "tortoise",
                "created_at": datetime.combine(created, datetime.min.time(), tzinfo=timezone.utc).isoformat(),  # noqa: UP017
                "node_count": 10, "edge_count": 3, "sha256": "0" * 64, "format": DUMP_FORMAT,
            }).encode(),
        )
        store.upload(f"backups/{bid}/dump.enc", b"blob")

    deleted = prune_backups(store, team_id, keep_daily=7, keep_weekly=2)
    # W2 + W3 are the 2 newest weeks → kept; the entire oldest week (W1, both
    # copies) is beyond the cap → deleted
    assert sorted(deleted) == sorted([ids["w1a"], ids["w1b"]])
    assert ids["w2"] not in deleted
    assert ids["w3"] not in deleted
    # the same-week newest rule is pinned separately in
    # test_prune_keeps_newest_backup_of_each_week (no cap pressure)


def test_registry_team_node_absent(monkeypatch):
    """No Team node in registry: backup/restore still work; stamps no-op."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        registry = proj.db.select_graph("registry_tortoise")  # EMPTY — no Team node
        store = MemoryStorage()
        manifest = create_backup(proj, registry, store, team_id="team_ghost", graph_name="tortoise")
        assert manifest["node_count"] == 6
        dump_key = [k for k in store.list("backups/team_ghost/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        result = restore_backup(
            proj.db, registry, store, dump_key,
            team_id="team_ghost", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        # no stamp written (MATCH finds nothing, SET no-ops silently)
        assert registry.query("MATCH (t:Team {id:'team_ghost'}) RETURN count(t)").result_set[0][0] == 0
        proj.close()


def test_prune_and_list_empty_store():
    store = MemoryStorage()
    assert prune_backups(store, "team_q", keep_daily=7, keep_weekly=4) == []
    assert list_backups(store, "team_q") == []


def test_list_backups_skips_corrupt_manifest():
    store = MemoryStorage()
    team_id = "team_v"
    _seed_old_backups(store, team_id, [1])
    bad_id = f"{team_id}/20260701T000000Z"
    store.upload(f"backups/{bad_id}/manifest.json", b"{this is not json")
    store.upload(f"backups/{bad_id}/dump.enc", b"blob")

    listed = list_backups(store, team_id)
    assert len(listed) == 1  # corrupt manifest skipped, no exception
    assert listed[0]["node_count"] == 10


def test_list_backups_sorted_newest_first():
    store = MemoryStorage()
    team_id = "team_z"
    _seed_old_backups(store, team_id, [30, 1])
    listed = list_backups(store, team_id)
    assert len(listed) == 2
    assert listed[0]["created_at"] > listed[1]["created_at"]


def test_prune_keep_hourly_bounded_semantics(monkeypatch):
    """keep_hourly>0 REPLACES the daily keep-all rule with a bounded retention:

    keep ALL backups younger than keep_hourly hours; one anchor per UTC
    hour-bucket for ages within the daily horizon; then weekly anchors.

    This is the discriminating test: the stacked keep-all variant (retains
    every backup < keep_daily days) and the unbounded-anchor variant (one
    anchor per hour-bucket forever) must BOTH fail it.
    """
    _freeze_clock(monkeypatch)
    from datetime import timedelta as _td

    store = MemoryStorage()
    team_id = "team_hourly"
    fixed = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)  # noqa: UP017

    # (hours_ago, minutes_ago). NEGATIVE minutes = created AFTER the hour mark,
    # keeping both backups inside the SAME (day, hour) bucket — that is what
    # makes the anchor dedup bite (hour anchors keep one per bucket).
    seeds = [
        (1, 0), (23, 0),       # hourly window (< 24h) → kept
        (25, 0), (25, -5),     # same bucket (Aug 6, 11h) → newest kept, dup deleted
        (49, 0), (49, -10), (49, -20),  # same bucket (Aug 5, 11h) → newest kept, dups deleted
        (100, 0),              # hour anchor (< 7d) → kept
        (200, 0),              # weekly zone (8.3d) → weekly anchor kept
        (500, 0), (501, 0),    # weekly zone, same ISO week → newest kept
    ]
    ids: dict[str, str] = {}
    for h, m in seeds:
        created = fixed - _td(hours=h, minutes=m)
        backup_id = f"{team_id}/{created.strftime('%Y%m%dT%H%M%SZ')}"
        ids[f"{h}:{m}"] = backup_id
        manifest = {
            "backup_id": backup_id,
            "team_id": team_id,
            "graph_name": "tortoise",
            "created_at": created.isoformat(),
            "node_count": 10,
            "edge_count": 3,
            "sha256": "0" * 64,
        }
        store.upload(f"backups/{backup_id}/manifest.json", json.dumps(manifest).encode())
        store.upload(f"backups/{backup_id}/dump.enc", b"x")

    deleted = prune_backups(store, team_id, keep_daily=7, keep_weekly=4, keep_hourly=24)

    assert sorted(deleted) == sorted(
        [ids["25:0"], ids["49:0"], ids["49:-10"], ids["501:0"]]
    ), f"deleted={deleted}"
    remaining = [k for k in store.list(f"backups/{team_id}/") if k.endswith("manifest.json")]
    assert len(remaining) == 7
    for h, m in [(1, 0), (23, 0), (25, -5), (49, -20), (100, 0), (200, 0), (500, 0)]:
        assert any(ids[f"{h}:{m}"] in k for k in remaining), f"{h}:{m} should be kept"


def test_prune_keep_hourly_zero_preserves_legacy():
    """keep_hourly=0 (default) is byte-for-byte the legacy keep-all behavior."""
    store = MemoryStorage()
    team_id = "team_legacy"
    days_ago = [0, 1, 2, 3, 4, 5, 6, 7, 14, 21, 28, 35, 42, 49, 56]
    ids = _seed_old_backups(store, team_id, days_ago)

    deleted = prune_backups(store, team_id, keep_daily=7, keep_weekly=4, keep_hourly=0)
    assert sorted(deleted) == sorted([ids[d] for d in (35, 42, 49, 56)])
    remaining = [k for k in store.list(f"backups/{team_id}/") if k.endswith("manifest.json")]
    assert len(remaining) == 11


def test_memory_create_if_not_exists_once():
    """Create-once semantics: True on create, False on collision, content kept."""
    store = MemoryStorage()
    assert store.create_if_not_exists("ops/alerts/STALE-1.json", b"a") is True
    assert store.create_if_not_exists("ops/alerts/STALE-1.json", b"b") is False
    assert store.download("ops/alerts/STALE-1.json") == b"a"  # winner's content wins


def test_r2_create_if_not_exists_412_race(monkeypatch):
    """The 412 race: second creator sees PreconditionFailed → False (adopt)."""

    class _ConditionalS3:
        def __init__(self):
            self.objects: dict[str, bytes] = {}

        def put_object(self, Bucket, Key, Body, **kw):
            if "IfNoneMatch" in kw and Key in self.objects:
                from botocore.exceptions import ClientError

                raise ClientError(
                    {"Error": {"Code": "PreconditionFailed", "Message": "exists"}},
                    "PutObject",
                )
            self.objects[Key] = Body

        def head_object(self, Bucket, Key):
            if Key not in self.objects:
                raise KeyError(Key)
            return {}

    fake = _ConditionalS3()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(fake))
    store = R2Storage(
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        access_key_id="ak", secret_access_key="sk", bucket="tortoise-backups",
    )
    assert store.create_if_not_exists("ops/alerts/X.json", b"1") is True
    assert store.create_if_not_exists("ops/alerts/X.json", b"2") is False
    assert fake.objects["ops/alerts/X.json"] == b"1"


def test_r2_create_if_not_exists_fallback_head(monkeypatch):
    """Conditional writes unsupported → HEAD-check fallback (never blind-put)."""

    class _NoConditionalS3:
        def __init__(self):
            self.objects: dict[str, bytes] = {}
            self.put_calls = []

        def put_object(self, Bucket, Key, Body, **kw):
            if "IfNoneMatch" in kw:
                raise RuntimeError("IfNoneMatch not supported by this client")
            self.objects[Key] = Body
            self.put_calls.append(Key)

        def head_object(self, Bucket, Key):
            if Key not in self.objects:
                raise KeyError(Key)
            return {}

    fake = _NoConditionalS3()
    monkeypatch.setitem(sys.modules, "boto3", _FakeBoto3(fake))
    store = R2Storage(
        endpoint_url="https://acct.r2.cloudflarestorage.com",
        access_key_id="ak", secret_access_key="sk", bucket="tortoise-backups",
    )
    # Existing object via fallback → False, and never overwritten.
    fake.objects["ops/alerts/Y.json"] = b"original"
    assert store.create_if_not_exists("ops/alerts/Y.json", b"new") is False
    assert fake.objects["ops/alerts/Y.json"] == b"original"
    # Ambiguous HEAD (missing object, client without conditionals) → RAISES:
    # a blind-put would weaken the dedup linearization point (review P3).
    with pytest.raises(RuntimeError, match="could not confirm absence"):
        store.create_if_not_exists("ops/alerts/Z.json", b"new")


# ── #669 Supabase-seam stamps (plan Task 5 / P1-3: seam abstraction + fake) ──
# create_backup/restore_backup stamp the control plane through the seam: the
# FalkorDB registry handle (Cypher SET, pre-#669) or the Supabase control
# plane (PostgREST PATCH on the teams row, post-#669). The Supabase side is
# driven through the in-memory FakeControlPlane — zero network.

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane  # noqa: E402, F401


def test_backup_seam_dialect_detection():
    """The seam discriminates the FalkorDB registry dialect from PostgREST."""
    from tortoise.hosted_backup import _is_supabase_source

    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        registry = proj.db.select_graph("registry_tortoise")
        assert not _is_supabase_source(registry)  # falkordb Graph → Cypher
        assert _is_supabase_source(FakeControlPlane())
        assert _is_supabase_source(FakeControlPlane().seed("teams", []))

        class _VarArgsStub:  # *args stub → treated as the registry dialect
            def query(self, *a, **k):
                raise RuntimeError("down")

        assert not _is_supabase_source(_VarArgsStub())
        proj.close()


def test_backup_seam_real_supabase_query_signature_pinned():
    """The discriminator keys on the REAL SupabaseControlPlane.query first
    positional param being ``table`` — pin it so a rename can never silently
    break seam detection (all unit tests exercise the fake, which mirrors the
    real signature only by convention)."""
    import inspect

    from tortoise.supabase_control import SupabaseControlPlane

    params = list(inspect.signature(SupabaseControlPlane.query).parameters.values())
    assert params[1].name == "table"


def test_create_backup_stamps_supabase_teams_row(monkeypatch):
    """Supabase mode: create_backup PATCHes backup_latest_at on the RIGHT row."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        cp = FakeControlPlane().seed("teams", [
            {"id": "team_x", "graph_name": "tortoise", "tier": "pro",
             "backup_enabled": True},
            {"id": "team_y", "graph_name": "tortoise", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        manifest = create_backup(proj, cp, store, team_id="team_x", graph_name="tortoise")
        assert manifest["node_count"] == 6
        row = cp.query("teams", select=["backup_latest_at"],
                       filters=[("id", "eq", "team_x")])
        assert row[0]["backup_latest_at"]  # stamped on the target team
        other = cp.query("teams", select=["backup_latest_at"],
                         filters=[("id", "eq", "team_y")])
        assert other[0]["backup_latest_at"] is None  # untouched
        proj.close()


def test_create_backup_supabase_stamp_blip_best_effort(monkeypatch):
    """#669 P3: a Supabase stamp blip must not fail an otherwise-durable backup."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)

        class StampBlip(FakeControlPlane):
            # Seam-interface signature (table first) — dialect detection must
            # recognize it as a Supabase source.
            def query(self, table, *args, **kwargs):
                if kwargs.get("method") == "PATCH":
                    raise RuntimeError("Supabase blip (simulated)")
                return super().query(table, *args, **kwargs)

        store = MemoryStorage()
        manifest = create_backup(
            proj, StampBlip().seed("teams", [{"id": "team_x"}]), store,
            team_id="team_x", graph_name="tortoise",
        )
        assert manifest["node_count"] == 6
        assert any(k.endswith("dump.enc") for k in store.list("backups/team_x/"))
        assert any(k.endswith("manifest.json") for k in store.list("backups/team_x/"))
        proj.close()


def test_restore_backup_stamps_supabase_teams_row(monkeypatch):
    """Supabase mode: restore_backup PATCHes backup_restored_at on the RIGHT row."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)
        cp = FakeControlPlane().seed("teams", [
            {"id": "team_x", "graph_name": "tortoise", "tier": "pro",
             "backup_enabled": True},
        ])
        store = MemoryStorage()
        manifest = create_backup(proj, cp, store, team_id="team_x", graph_name="tortoise")  # noqa: F841
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        result = restore_backup(
            proj.db, cp, store, dump_key, team_id="team_x", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        row = cp.query("teams", select=["backup_restored_at"],
                       filters=[("id", "eq", "team_x")])
        assert row[0]["backup_restored_at"]
        proj.close()


def test_restore_supabase_stamp_blip_best_effort(monkeypatch):
    """Restore stays durable when the Supabase restore-stamp PATCH blips."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed(proj.g)

        class StampBlip(FakeControlPlane):
            # Seam-interface signature (table first) — dialect detection must
            # recognize it as a Supabase source.
            def query(self, table, *args, **kwargs):
                if kwargs.get("method") == "PATCH":
                    raise RuntimeError("Supabase blip (simulated)")
                return super().query(table, *args, **kwargs)

        store = MemoryStorage()
        manifest = create_backup(  # noqa: F841
            proj, StampBlip().seed("teams", [{"id": "team_x"}]), store,
            team_id="team_x", graph_name="tortoise",
        )
        dump_key = [k for k in store.list("backups/team_x/") if k.endswith("dump.enc")][0]  # noqa: RUF015
        result = restore_backup(
            proj.db, StampBlip().seed("teams", [{"id": "team_x"}]), store, dump_key,
            team_id="team_x", graph_name="tortoise",
        )
        assert result["restored"] == {"nodes": 6, "edges": 5}
        live = proj.db.select_graph("tortoise")
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 6
        proj.close()
