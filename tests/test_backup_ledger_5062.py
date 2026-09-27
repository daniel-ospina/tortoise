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

import tortoise.hosted_backup as hosted_backup
from tortoise.backup_config import BackupConfig
from tortoise.backup_ledger import (
    BACKUP_OBJECT_SUFFIXES,
    COVERAGE_VERIFIED,
    LEDGER_FORMAT,
    MANIFEST_SUFFIX,
    MISMATCH,
    MISSING,
    UNREADABLE,
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


class _SameLengthCorruptStorage(MemoryStorage):
    """Persists a SAME-LENGTH corruption of dump.enc — silent bit-rot. A
    byte-length check cannot see it; only the sha256 comparison can."""

    def upload(self, key, data, content_type=None):
        if key.endswith("dump.enc"):
            super().upload(key, data[:-1] + bytes([data[-1] ^ 0xFF]))
        else:
            super().upload(key, data, content_type=content_type)


class _DownloadRaisesStorage(MemoryStorage):
    """Download of one object raises a non-KeyError — the destination is
    present-but-unreadable, a state distinct from a missing object."""

    def download(self, key):
        if key.endswith("dump.enc"):
            raise RuntimeError("transient transport failure")
        return super().download(key)


class _ListRaisesStorage(MemoryStorage):
    """Prefix listing raises — coverage can then never certify the ABSENCE of
    unreported objects, so it must fail closed."""

    def list(self, prefix):
        raise RuntimeError("listing unavailable")


class _LedgerDeleteFailsStorage(MemoryStorage):
    """Delete of ledger.json raises while the flag is set — the transient
    failure that must not orphan the ledger. Flippable so the retry can
    succeed."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_ledger_delete = True

    def delete(self, key):
        if self.fail_ledger_delete and key.endswith("ledger.json"):
            raise RuntimeError("R2 transient failure deleting ledger.json")
        super().delete(key)


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


def test_dump_graph_fails_closed_when_only_the_edge_read_is_truncated(monkeypatch):
    """The EDGE totality guard is load-bearing on its own: a cap that leaves the
    NODE read complete but truncates the EDGE read must still fail.

    Class-B: cap=100, 50 nodes / 200 edges. The 50-row node read fits under the
    cap and is COMPLETE (so the node guard at the #5062 block passes); the edge
    read comes back 100 of 200, which is exactly the state the edge guard exists
    to catch. Remove the edge guard and this test fails with a returned dump of
    ``read_edge_count = 100`` for a 200-edge store instead of a ValueError.
    """
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 50)
        proj.g.query(
            "UNWIND range(1,200) AS i "
            "MATCH (a:Point {id: toString((i % 50) + 1)}) "
            "MATCH (b:Point {id: toString(((i * 7) % 50) + 1)}) "
            "CREATE (a)-[:REL {i: i}]->(b)"
        )
        store_edges = int(
            proj.g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
        )
        assert store_edges == 200  # the fixture really holds the truncated set

        with pytest.raises(
            ValueError, match=r"exported 100 edges but the graph holds 200"
        ):
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


def test_create_backup_refuses_a_same_length_corruption(monkeypatch):
    """Silent bit-rot: the destination holds the SAME NUMBER of bytes as the
    ledger records but different content. The byte-length check passes; the
    sha256 comparison (the commit's core "download every object, hash it"
    claim) is what refuses it.

    Class-B: ``_SameLengthCorruptStorage`` flips the last byte of dump.enc, so
    ``len(data) == ledger.bytes`` while the hashes differ — the reachable state
    that makes the sha comparison, not the length check, the failing one.
    """
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = _SameLengthCorruptStorage()

        with pytest.raises(BackupVerificationError) as exc:
            create_backup(proj, registry, store, org_id="team_x",
                          graph_name=proj.graph_name)

        assert "mismatch" in str(exc.value)
        assert store.list("backups/team_x/") == []
        proj.close()


def test_create_backup_fails_closed_when_the_readback_itself_raises(
    monkeypatch,
):
    """A read-back that cannot COMPLETE is not a read-back that verified: the
    failure is wrapped as ``BackupVerificationError`` and the object set is
    rolled back — never a self-reported success.

    Class-B: ``verify_ledger`` raising is the state that reaches the guard
    (the module-level import is the seam); without the guard the RuntimeError
    escapes unwrapped and nothing is rolled back.
    """
    _set_env_key(monkeypatch)

    def _boom(storage, ledger):
        raise RuntimeError("read-back blew up")

    monkeypatch.setattr(hosted_backup, "verify_ledger", _boom)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 5)
        registry = proj.db.select_graph("registry_tortoise")
        store = MemoryStorage()

        with pytest.raises(BackupVerificationError, match="could not complete"):
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


def test_verify_object_reaches_the_sha256_comparison_on_a_same_length_change():
    """The same-length case must fail on the SHA comparison, not the length
    check — a corruption the length check cannot see.

    Class-B: the stored object is the same length as the ledger records but one
    byte differs, so ``_verify_object`` passes the length branch and reaches the
    sha256 comparison (`backup_ledger.py` sha-mismatch return).
    """
    store = MemoryStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    dump_key = f"backups/{backup_id}/dump.enc"
    self_key = f"backups/{backup_id}/ledger.json"
    good = b"0123456789"
    store.upload(dump_key, good[:-1] + b"X")  # same length, different bytes
    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="c", written_at="c",
        objects=[LedgerObject(dump_key, len(good),
                              hashlib.sha256(good).hexdigest(), "c")],
        source_node_count=1, source_edge_count=0,
        dump_node_count=1, dump_edge_count=0, read_edge_count=0,
    )
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    obj = next(o for o in v.objects if o.key == dump_key)
    assert obj.state == MISMATCH
    # The length branch was NOT the one that fired — this is the sha comparison.
    assert obj.detail.startswith("sha256 read back")
    assert not v.ok


def test_verify_object_flags_an_object_with_no_key_and_a_non_mapping():
    """Malformed ledger entries are ``unreadable``, never silently skipped: an
    object with no key and an entry that is not a mapping.

    Class-B: the ledger is built valid, then the objects list is replaced with
    the two malformed shapes — the reachable states that make each return fire.
    """
    store = MemoryStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    self_key = f"backups/{backup_id}/ledger.json"
    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="c", written_at="c", objects=[],
        source_node_count=1, source_edge_count=0,
        dump_node_count=1, dump_edge_count=0, read_edge_count=0,
    )
    ledger["objects"] = [{"bytes": 1, "sha256": "x"}, "not-a-mapping"]
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    details = [o.detail for o in v.objects]
    assert "ledger object has no key" in details
    assert "ledger object is not a mapping" in details
    states = {o.detail: o.state for o in v.objects}
    assert states["ledger object has no key"] == UNREADABLE
    assert states["ledger object is not a mapping"] == UNREADABLE
    assert not v.ok


def test_verify_object_flags_an_unreadable_download_as_unreadable():
    """A download that raises a non-KeyError is ``unreadable`` — distinct from
    ``missing`` — and fails coverage.

    Class-B: ``_DownloadRaisesStorage`` raises only for dump.enc, so the object
    is present in the listing yet its bytes cannot be read.
    """
    store = _DownloadRaisesStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    dump_key = f"backups/{backup_id}/dump.enc"
    self_key = f"backups/{backup_id}/ledger.json"
    blob = b"encrypted"
    store.upload(dump_key, blob)
    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="c", written_at="c",
        objects=[LedgerObject(dump_key, len(blob),
                              hashlib.sha256(blob).hexdigest(), "c")],
        source_node_count=1, source_edge_count=0,
        dump_node_count=1, dump_edge_count=0, read_edge_count=0,
    )
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    obj = next(o for o in v.objects if o.key == dump_key)
    assert obj.state == UNREADABLE
    assert "RuntimeError" in obj.detail
    assert not v.ok


def test_verify_ledger_fails_closed_when_the_listing_fails():
    """A listing that cannot complete can never certify the absence of
    unreported objects — it is recorded as a distinct unreported marker and
    coverage FAILS.

    Class-B: ``_ListRaisesStorage`` raises on every prefix listing; without the
    fail-closed branch the read-back would treat the empty list as "no extra
    objects" and verify.
    """
    store = _ListRaisesStorage()
    backup_id = "team_x/20260101T000000Z_abcd"
    self_key = f"backups/{backup_id}/ledger.json"
    ledger = build_ledger(
        backup_id=backup_id, org_id="team_x", graph_name="tortoise",
        self_key=self_key, created_at="c", written_at="c", objects=[],
        source_node_count=1, source_edge_count=0,
        dump_node_count=1, dump_edge_count=0, read_edge_count=0,
    )
    store.upload(self_key, serialize_ledger(ledger))

    v = verify_ledger(store, ledger)

    assert v.unreported == (f"<list-failed:backups/{backup_id}/>",)
    assert not v.ok


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


def test_prune_retry_converges_when_only_the_ledger_delete_fails(monkeypatch):
    """A transient failure deleting ONLY ledger.json must not orphan it: the
    manifest (the enumeration key) is deleted last, so the next prune re-lists
    the archive and retries.

    Class-B: the store raises on the ledger delete (flag set) and then stops
    raising. Pre-fix the manifest was deleted BEFORE the ledger, so the retry
    could not see the archive — the ledger stayed forever (``prune #2 deleted:
    []``). Here prune #1 must leave the manifest, and prune #2 must converge to
    an empty prefix.
    """
    _set_env_key(monkeypatch)
    with tempfile.TemporaryDirectory() as tmp:
        proj = _make_proj(tmp)
        _seed_points(proj.g, 3)
        registry = proj.db.select_graph("registry_tortoise")
        store = _LedgerDeleteFailsStorage()
        manifest = create_backup(proj, registry, store, org_id="team_x",
                                 graph_name=proj.graph_name)
        backup_id = manifest["backup_id"]

        deleted1 = prune_backups(store, "team_x", keep_daily=0, keep_weekly=0,
                                 keep_hourly=0)

        # The failing delete is recorded, but nothing is reported deleted…
        assert deleted1 == []
        keys1 = store.list(f"backups/{backup_id}/")
        # …and crucially the MANIFEST survives, so a retry can still find it.
        assert f"backups/{backup_id}/{MANIFEST_SUFFIX}" in keys1
        assert f"backups/{backup_id}/{BACKUP_OBJECT_SUFFIXES[0]}" not in keys1

        # The transient failure clears — the retry converges.
        store.fail_ledger_delete = False
        deleted2 = prune_backups(store, "team_x", keep_daily=0, keep_weekly=0,
                                 keep_hourly=0)

        assert deleted2 == [backup_id]
        assert store.list(f"backups/{backup_id}/") == []
        proj.close()


def test_object_suffix_set_is_the_single_home():
    """The ledger names the complete object set — a suffix added to the writer
    without being added here would be unverifiable and unprunable."""
    assert set(BACKUP_OBJECT_SUFFIXES) == {"dump.enc", "manifest.json", "ledger.json"}
