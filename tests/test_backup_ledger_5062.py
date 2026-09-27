"""#5062 — one verified per-object backup ledger: coverage is READ BACK.

The defect class this file pins: the DR path declared a backup good from a
number the **writing path itself** produced. Two shapes, both live at
``877fa52d1``:

* ``create_backup`` recorded a ``sha256`` computed from the **in-memory** blob,
  returned, and the sweep reported ``status: backed_up`` — nobody read the
  destination back. A store that dropped/corrupted the write read green (#4991).
* ``dump_graph`` read nodes+edges UNPAGED, so a graph larger than FalkorDB's
  server-global ``RESULTSET_SIZE`` exported a silently truncated artifact whose
  ``node_count`` was the writer's own ``len(nodes)`` (#4515).

Every test here is written Class-B: it states the value/state that makes it fail
and that value is reachable in the fixture.

RED proofs (pre-fix ``main``):

* :func:`test_dump_graph_reads_every_node_past_the_server_cap` — the cap
  simulator truncates non-aggregate reads exactly as ``RESULTSET_SIZE`` does; the
  pre-fix single read returns ``cap`` of ``total`` nodes and reports
  ``node_count = cap``.
* :func:`test_dump_graph_fails_closed_below_the_page_size` — a cap tuned under
  the page size is caught by the store's independent aggregate count; pre-fix the
  read returns a short dump with no failure.
* :func:`test_create_backup_refuses_a_store_that_dropped_the_blob` — pre-fix
  ``create_backup`` returns a manifest whose ``sha256`` is the in-memory blob's
  while the destination holds different bytes.
* :func:`test_backup_graph_does_not_report_backed_up_without_readback` — pre-fix
  the sweep's per-graph result is ``status: backed_up`` for a store that dropped
  the blob.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime

import pytest

from tortoise.backup_config import BackupConfig
from tortoise.backup_ledger import (
    BACKUP_OBJECT_SUFFIXES,
    COVERAGE_VERIFIED,
    LEDGER_FORMAT,
    MISSING,
    UNREPORTED,
    VERIFIED,
    BackupVerificationError,
    LedgerObject,
    build_ledger,
    serialize_ledger,
    verify_ledger,
)
from tortoise.backup_sweep import _backup_graph
from tortoise.hosted_backup import (
    _DUMP_PAGE_SIZE,
    MemoryStorage,
    count_data_nodes,
    create_backup,
    dump_graph,
    prune_backups,
)
from tortoise.projection import FalkorProjection

# ── fixtures / harness ─────────────────────────────────────────────────────


def _set_env_key(monkeypatch) -> None:
    monkeypatch.setenv(
        "TORTOISE_BACKUP_KEY", base64.b64encode(os.urandom(32)).decode()
    )


def _make_proj(tmpdir: str, name: str = "t.db") -> FalkorProjection:
    return FalkorProjection(os.path.join(tmpdir, name))


def _seed_points(g, n: int) -> None:
    """One query, ``n`` :Point nodes — cheap at the 1000+ scale the cap tests need."""
    g.query("UNWIND range(1,$n) AS i CREATE (:Point {id: toString(i)})",
            params={"n": n})


class _CappedResult:
    __slots__ = ("result_set",)

    def __init__(self, result_set):
        self.result_set = result_set


class _CappedGraph:
    """Wraps a real graph handle and truncates any non-aggregate result set to
    ``cap`` rows — exactly what FalkorDB's server-global ``RESULTSET_SIZE`` does.

    The aggregate count queries return ONE row, so they pass through untouched,
    which is precisely the asymmetry the fix relies on (#4233).
    """

    def __init__(self, g, cap: int) -> None:
        self._g = g
        self._cap = cap

    @property
    def name(self):
        return self._g.name

    def query(self, q, params=None):
        res = self._g.query(q, params=params or {})
        return _CappedResult(res.result_set[: self._cap])


class _DropsDumpStorage(MemoryStorage):
    """Accepts the upload and returns OK but persists a TRUNCATED dump.enc —
    the #4991 shape: the sweep says it wrote an archive; the destination
    disagrees."""

    def upload(self, key, data, content_type=None):
        if key.endswith("dump.enc"):
            super().upload(key, data[:-8])
        else:
            super().upload(key, data, content_type=content_type)


class _FailsLedgerUploadStorage(MemoryStorage):
    """Fails the ledger write — a backup with no read-back proof must not stand."""

    def upload(self, key, data, content_type=None):
        if key.endswith("ledger.json"):
            raise RuntimeError("R2 transient failure on ledger.json")
        super().upload(key, data, content_type=content_type)


def _config() -> BackupConfig:
    return BackupConfig(
        enabled=True,
        backup_key=b"k" * 32,
        registry_stream_key=b"r" * 32,
        r2_account_id="a", r2_access_key_id="b", r2_secret_access_key="c",
        r2_bucket="tortoise-backups",
        telegram_bot_token="t", telegram_chat_id="c",
        github_issues_pat="pat", alert_assignee="u",
        gh_repo="daniel-ospina/tortoise",
    )


# ── dump reads are total or fail (#4515 shape) ─────────────────────────────


def test_dump_graph_reads_every_node_past_the_server_cap(monkeypatch):
    """A graph bigger than the (simulated) RESULTSET_SIZE exports EVERY node.

    Class-B: the cap simulator truncates a 1010-row node read to 1000 — the
    exact state that makes the pre-fix writer report ``node_count = 1000`` for a
    1010-node graph.
    """
    _set_env_key(monkeypatch)
    total = _DUMP_PAGE_SIZE + 10
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, total)
        store_truth = count_data_nodes(proj.db, proj.graph_name)

        dump = dump_graph(_CappedGraph(proj.g, _DUMP_PAGE_SIZE),
                          graph_name=proj.graph_name)

        assert store_truth == total
        # The store's own count is the reference; the writer's len(nodes) is not.
        assert dump["source_node_count"] == total
        assert dump["node_count"] == total
        assert len(dump["nodes"]) == total
        proj.close()


def test_dump_graph_fails_closed_below_the_page_size(monkeypatch):
    """A cap BELOW the page size cannot be spotted from the page boundary — the
    independent aggregate count catches it and the read FAILS.

    Class-B: cap=100 returns a 100-row page for a 300-node graph; a page-size
    check alone would read that short page as "the end". Pre-fix, ``dump_graph``
    returns it silently.
    """
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 300)

        with pytest.raises(ValueError, match="TRUNCATED"):
            dump_graph(_CappedGraph(proj.g, 100), graph_name=proj.graph_name)
        proj.close()


def test_dump_graph_reports_the_stores_counts_as_the_reference(monkeypatch):
    """The ledger records the STORE's count, side by side with the artifact's —
    without it a ledger cannot prove the artifact is not short."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 7)
        dump = dump_graph(proj.g, graph_name=proj.graph_name)
        assert dump["node_count"] == 7
        assert dump["source_node_count"] == 7
        assert dump["source_edge_count"] == 0
        proj.close()


# ── the read-back (self-reported coverage: #4991 shape) ────────────────────


def test_create_backup_refuses_a_store_that_dropped_the_blob(monkeypatch):
    """A destination that does not hold the bytes the writer handed it must be
    refused — and the whole object set rolled back.

    Class-B: ``_DropsDumpStorage`` stores ``blob[:-8]`` — the destination bytes
    and the ledger's sha256 disagree, which is the state that makes it fail.
    """
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = _DropsDumpStorage()

        with pytest.raises(BackupVerificationError) as exc:
            create_backup(proj, registry, store, org_id="team_x",
                          graph_name=proj.graph_name)

        assert "mismatch" in str(exc.value)
        # Rollback is total: no dump, no manifest, and NO orphan ledger.
        assert store.list("backups/team_x/") == []
        proj.close()


def test_create_backup_rolls_back_when_the_ledger_write_fails(monkeypatch):
    """A backup whose ledger never landed has no read-back coverage — it must
    not stand as a restorable artifact with no proof."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = _FailsLedgerUploadStorage()

        with pytest.raises(RuntimeError, match=r"ledger\.json"):
            create_backup(proj, registry, store, org_id="team_x",
                          graph_name=proj.graph_name)

        assert store.list("backups/team_x/") == []
        proj.close()


def test_create_backup_reports_verified_coverage_from_the_destination(monkeypatch):
    """The returned coverage is the destination's answer: every ledger object is
    read back and hashed, and the ledger itself is on the store."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()

        manifest = create_backup(proj, registry, store, org_id="team_x",
                                 graph_name=proj.graph_name)

        coverage = manifest["coverage"]
        assert coverage["state"] == COVERAGE_VERIFIED
        assert coverage["shortfall"] is None
        assert coverage["unreported"] == []
        assert {o["state"] for o in coverage["objects"]} == {VERIFIED}
        # The ledger object is really on the destination.
        ledger_key = f"backups/{manifest['backup_id']}/{BACKUP_OBJECT_SUFFIXES[2]}"
        ledger = json.loads(store.download(ledger_key))
        assert ledger["format"] == LEDGER_FORMAT
        # And the recorded dump sha256 matches what the destination holds.
        dump_obj = next(o for o in ledger["objects"] if o["key"].endswith("dump.enc"))
        assert dump_obj["sha256"] == hashlib.sha256(
            store.download(dump_obj["key"])
        ).hexdigest()
        proj.close()


# ── the third state: "did not report" ≠ verified ≠ failed ─────────────────


def test_verify_ledger_third_state_is_distinct_from_verified_and_failed():
    """An object the destination holds but the ledger never named is
    ``unreported``; a named-but-absent object is ``missing``. Neither is
    ``verified``, and the two are distinguishable.

    Class-B: the extra object is uploaded under the backup prefix, which is the
    reachable state that makes coverage fail.
    """
    store = MemoryStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    dump_key = f"backups/{backup_id}/dump.enc"
    manifest_key = f"backups/{backup_id}/manifest.json"
    self_key = f"backups/{backup_id}/ledger.json"
    blob = b"encrypted"
    mbytes = b"{}"
    store.upload(dump_key, blob)
    store.upload(manifest_key, mbytes)
    # An extra object the ledger does not name — "did not report".
    store.upload(f"backups/{backup_id}/stray.bin", b"stray")

    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="2026-01-01T00:00:00+00:00",
        written_at="2026-01-01T00:00:00+00:00",
        objects=[
            LedgerObject(dump_key, len(blob), hashlib.sha256(blob).hexdigest(),
                         "2026-01-01T00:00:00+00:00"),
            LedgerObject(manifest_key, len(mbytes),
                         hashlib.sha256(mbytes).hexdigest(),
                         "2026-01-01T00:00:00+00:00"),
            # This one is NOT there — a plain failure, distinct from unreported.
            LedgerObject(f"backups/{backup_id}/gone.bin", 1, "0" * 64,
                         "2026-01-01T00:00:00+00:00"),
        ],
        source_node_count=1, source_edge_count=0,
        dump_node_count=1, dump_edge_count=0, read_edge_count=0,
    )
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    assert v.state != COVERAGE_VERIFIED
    assert v.unreported == (f"backups/{backup_id}/stray.bin",)
    states = {o.key: o.state for o in v.objects}
    assert states[f"backups/{backup_id}/gone.bin"] == MISSING
    assert states[dump_key] == VERIFIED
    assert UNREPORTED not in states.values()  # unreported is NOT an object state


def test_verify_ledger_refuses_a_ledger_without_source_counts():
    """A ledger with no independent source count can never read as verified —
    'unverifiable' is not 'covered'."""
    store = MemoryStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    self_key = f"backups/{backup_id}/ledger.json"
    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="c", written_at="c",
        objects=[], source_node_count=None, source_edge_count=None,
        dump_node_count=3, dump_edge_count=0,
    )
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    assert not v.ok
    assert v.shortfall is not None
    assert "source counts not reported" in v.shortfall["reason"]


# ── the sweep can only say backed_up through a verified read-back ──────────


def test_backup_graph_does_not_report_backed_up_without_readback(monkeypatch):
    """`_backup_graph` returns ``status: error`` (never ``backed_up``) when the
    destination contradicts the ledger — the end-to-end shape of #4991."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = _DropsDumpStorage()
        incidents: list[dict] = []

        result = _backup_graph(
            db=proj.db, registry=registry, storage=store, config=_config(),
            org_id="team_x",
            graph={"graph_id": "default", "graph_name": proj.graph_name},
            now=datetime.now(UTC), incidents=incidents,
        )

        assert result["status"] != "backed_up"
        assert result["status"] == "error"
        assert "read-back" in result["error"] or "ledger" in result["error"]
        assert store.list("backups/team_x/") == []
        proj.close()


def test_backup_graph_reports_readback_coverage_on_success(monkeypatch):
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()

        result = _backup_graph(
            db=proj.db, registry=registry, storage=store, config=_config(),
            org_id="team_x",
            graph={"graph_id": "default", "graph_name": proj.graph_name},
            now=datetime.now(UTC), incidents=[],
        )

        assert result["status"] == "backed_up"
        assert result["coverage"]["state"] == COVERAGE_VERIFIED
        proj.close()


# ── delete-path parity: no orphan ledger survives a prune ──────────────────


def test_prune_deletes_every_object_including_the_ledger(monkeypatch):
    """The object set has ONE home: a zero-window prune removes dump, manifest
    AND ledger — no orphan ledger a later sweep could read as coverage."""
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 3)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()
        manifest = create_backup(proj, registry, store, org_id="team_x",
                                 graph_name=proj.graph_name)
        assert any(k.endswith("ledger.json") for k in store.list("backups/team_x/"))

        deleted = prune_backups(store, "team_x", keep_daily=0, keep_weekly=0,
                                keep_hourly=0)

        assert manifest["backup_id"] in deleted
        assert store.list("backups/team_x/") == []
        proj.close()


def test_object_suffix_set_is_the_single_home():
    """The ledger names the complete object set — a suffix added to the writer
    without being added here would be unverifiable and unprunable."""
    assert set(BACKUP_OBJECT_SUFFIXES) == {"dump.enc", "manifest.json", "ledger.json"}
