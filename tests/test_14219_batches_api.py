"""#14219 (F4): the `/v1/batches` REST surface — route shape, ACK latency, the
acknowledgement gate, per-item status and cancel.

The layer's own semantics are proved in `tests/test_14219_batch_ingest.py`
(no FastAPI). These tests pin the WIRE: what a client gets back, in
what order, and that acceptance does no work.

No DB is touched: the tenant store is replaced with the in-memory one and the
batch worker's `submit` is stubbed, so a test observes the handler's own
behaviour (the ordering of write-then-dispatch) rather than a worker's.
"""
from __future__ import annotations

import contextlib

import pytest

from tortoise import batch_ingest as bi
from tortoise import hosted_api as ha

ORG_ID = "org-14219-wire"


@pytest.fixture()
def store():
    return bi.InMemoryBatchStore()


@pytest.fixture()
def client(store, monkeypatch):
    """A TestClient whose batch routes touch only the in-memory store."""
    from fastapi.testclient import TestClient

    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    # The worker is stubbed: a test must observe that the handler HANDED OFF
    # the work, not race a worker thread doing it.
    dispatched: list[str] = []
    monkeypatch.setattr(ha._BATCH_WORKER, "submit",
                        lambda fn, *a, **k: dispatched.append(a[1]) or True)

    async def _no_allowance(org, resource, **kwargs):
        return {}

    monkeypatch.setattr(ha, "_carry_allowances_off_loop", _no_allowance)

    ha.app.dependency_overrides[ha.get_current_org_gated] = lambda: {
        "org_id": ORG_ID, "key_id": None, "legacy_full_access": True,
        "tier": "free", "max_points": 100000, "max_sessions": None,
        "graph_id": None,
    }
    try:
        with TestClient(ha.app) as c:
            c.dispatched = dispatched  # type: ignore[attr-defined]
            yield c
    finally:
        ha.app.dependency_overrides.pop(ha.get_current_org_gated, None)


def test_post_batches_accepts_and_returns_an_accepted_receipt(client, store):
    """The ACK: a batch id, a status url, the estimate and the owner's copy."""
    r = client.post("/v1/batches", json={"items": [
        {"ref": "/docs/a.md"}, {"ref": "/docs/b.md", "name": "b.md"},
    ]})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["protocol_version"] == "memory_write_v1"
    assert body["status"] == "accepted"
    assert body["batch_id"].startswith("batch_")
    assert body["status_url"] == f"/v1/batches/{body['batch_id']}"
    assert body["items_total"] == 2
    # The proactive estimate the owner requires (§4 of #14275).
    est = body["estimate"]
    assert est["tokens_per_item"] == bi.TOKENS_PER_MEDIAN_DOCUMENT
    assert est["estimated_tokens"] == 2 * bi.TOKENS_PER_MEDIAN_DOCUMENT
    # …and the copy rule: batch-processed wording, no provider mechanics.
    assert "batch processed" in body["message"]
    assert "cancel any time" in body["message"]
    for word in bi.PROVIDER_MECHANICS_WORDS:
        assert word not in body["message"].lower()
    # Durable rows exist for every item, all queued, none worked yet.
    rows = store.items(ORG_ID, body["batch_id"])
    assert len(rows) == 2
    assert all(i.state is bi.BatchItemState.QUEUED for i in rows)


def test_acceptance_does_no_work_and_hands_off_after_the_rows_land(client, store):
    """Decision (a): the request must not wait for any extraction.

    The order is the property: the rows are written BEFORE the worker is
    handed the batch, so a worker can never see a batch id whose items are not
    yet durable — and the ack never waits for the worker.
    """
    r = client.post("/v1/batches", json={"items": [{"ref": "/docs/a.md"}]})
    body = r.json()
    assert store.items(ORG_ID, body["batch_id"]), (
        "the rows must exist before the hand-off")
    assert client.dispatched == [body["batch_id"]]


def test_a_large_batch_is_refused_with_the_estimate_until_acknowledged(client):
    """Owner §4: the overage is shown BEFORE the work, and must be confirmed."""
    # 100 items ≈ 6.5M tokens, past the acknowledgement threshold (3M, one solo
    # month's included amount).
    items = [{"ref": f"/docs/{n}.md"} for n in range(100)]
    r = client.post("/v1/batches", json={"items": items})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "BATCH_ACKNOWLEDGEMENT_REQUIRED"
    assert detail["estimate"]["estimated_tokens"] == 100 * bi.TOKENS_PER_MEDIAN_DOCUMENT
    assert detail["estimate"]["acknowledgement_required"] is True
    # The remedy is named, and the numbers are in the refusal itself.
    assert "acknowledge_overage" in detail["message"]

    acked = client.post("/v1/batches",
                        json={"items": items, "acknowledge_overage": True})
    assert acked.status_code == 201, acked.text


def test_the_estimate_reports_the_same_allowance_sessions_draw_on(client,
                                                                  monkeypatch):
    """Decision (b): ONE budget. The estimate reads the token dimension."""
    async def _with_allowance(org, resource, **kwargs):
        return {"max_extraction_tokens": 3_000_000}

    monkeypatch.setattr(ha, "_carry_allowances_off_loop", _with_allowance)

    async def _offload(fn, **kwargs):
        return {"capture_tokens_total": 2_900_000}

    monkeypatch.setattr(ha, "_cp_offload", _offload)

    r = client.post("/v1/batches", json={"items": [{"ref": f"/d/{n}.md"}
                                                   for n in range(10)]})
    assert r.status_code == 201, r.text
    est = r.json()["estimate"]
    assert est["included_tokens"] == 3_000_000
    assert est["tokens_already_used"] == 2_900_000
    assert est["overage_tokens"] == 10 * bi.TOKENS_PER_MEDIAN_DOCUMENT - 100_000


def test_an_unreadable_allowance_does_not_refuse_the_batch(client, monkeypatch):
    """#3981: never refuse a paying org over our own bookkeeping fault."""
    async def _boom(org, resource, **kwargs):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(ha, "_carry_allowances_off_loop", _boom)
    r = client.post("/v1/batches", json={"items": [{"ref": "/d/a.md"}]})
    assert r.status_code == 201, r.text
    assert r.json()["estimate"]["included_tokens"] is None


def test_status_returns_every_item_with_a_reason_for_non_success(client, store):
    r = client.post("/v1/batches", json={"items": [
        {"ref": "/d/a.md"}, {"ref": "/d/b.md"}, {"ref": "/d/c.md"}]})
    batch_id = r.json()["batch_id"]
    items = store.items(ORG_ID, batch_id)
    coord = bi.BatchCoordinator(store)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.INDEXED)
    coord.advance(ORG_ID, items[1], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[1], bi.BatchItemState.FAILED,
                  reason="non-UTF-8 content (decode failure)")

    s = client.get(f"/v1/batches/{batch_id}")
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["counts"]["indexed"] == 1
    assert body["counts"]["failed"] == 1
    assert body["counts"]["queued"] == 1
    by_ref = {i["ref"]: i for i in body["items"]}
    assert by_ref["/d/b.md"]["reason"].startswith("non-UTF-8")
    # The status surface states the worker's own bound, so "why is nothing
    # moving" has an answer that is not "we do not know".
    assert body["worker"]["ceiling"] >= 1
    # Nothing claims completion: 2 of 3 items are terminal.
    assert body["complete"] is False


def test_status_is_404_for_an_unknown_batch(client):
    assert client.get("/v1/batches/batch_nope").status_code == 404


def test_cancel_stops_new_work_and_keeps_completed_work(client, store):
    r = client.post("/v1/batches", json={"items": [
        {"ref": "/d/done.md"}, {"ref": "/d/todo.md"}]})
    batch_id = r.json()["batch_id"]
    items = store.items(ORG_ID, batch_id)
    coord = bi.BatchCoordinator(store)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.INDEXED,
                  tokens_in=40_000, tokens_out=20_000)

    c = client.post(f"/v1/batches/{batch_id}/cancel")
    assert c.status_code == 200, c.text
    body = c.json()
    assert body["status"] == "cancelled"
    by_ref = {i["ref"]: i for i in body["items"]}
    assert by_ref["/d/done.md"]["status"] == "indexed"
    assert by_ref["/d/done.md"]["tokens_total"] == 60_000
    assert by_ref["/d/todo.md"]["status"] == "cancelled"
    assert by_ref["/d/todo.md"]["tokens_total"] == 0
    assert "not charged" in body["message"]


def test_cancel_is_404_for_an_unknown_batch(client):
    assert client.post("/v1/batches/batch_nope/cancel").status_code == 404


def test_list_batches_is_newest_first_and_scoped(client, store, monkeypatch):
    first = client.post("/v1/batches", json={"items": [{"ref": "/a.md"}]}).json()
    second = client.post("/v1/batches", json={"items": [{"ref": "/b.md"}]}).json()
    listed = client.get("/v1/batches")
    assert listed.status_code == 200
    ids = [b["batch_id"] for b in listed.json()["batches"]]
    assert set(ids) == {first["batch_id"], second["batch_id"]}


def test_the_route_family_is_batches_never_packs(client):
    """/v1/packs is the ONTOLOGY pack surface and must stay untouched.

    A "pack" is a business/kind pack in this product (#318/#9583, #1935/epic
    #1891). Overloading that live public noun for a bundle of documents would
    confuse every generated client, so the bulk surface is `/v1/batches` and
    this test fails the day someone renames it back.
    """
    paths = {r.path for r in ha.app.routes if hasattr(r, "path")}
    assert "/v1/batches" in paths
    assert "/v1/batches/{batch_id}" in paths
    assert "/v1/batches/{batch_id}/cancel" in paths
    # The existing pack routes are still exactly the pack routes.
    assert "/v1/packs" in paths
    assert "/v1/packs/manifests" in paths
    assert not any(p.startswith("/v1/packs/") and "batch" in p for p in paths)


def test_an_empty_batch_is_refused_by_the_boundary(client):
    r = client.post("/v1/batches", json={"items": []})
    assert r.status_code == 422


# ── read-path isolation (brief item 5) ─────────────────────────────────────
def test_a_batch_drain_takes_no_capture_slot_and_uses_its_own_pool(store,
                                                                   monkeypatch):
    """The isolation BOUND, as a property rather than a promise.

    Batch work must not consume the capture lane's admission slots (a saturated
    batch would then refuse customer captures with `capture_capacity_saturated`)
    and must not run on the capture pool (a slow item would sit ahead of a
    capture in the same queue).

    Stated with the number this relies on: **2 concurrent batch items**
    (`batch_ingest.BATCH_ITEMS_IN_FLIGHT`), on a pool that is not
    `_CAPTURE_EXECUTOR`, so the extra concurrent graph pressure a batch can add
    is bounded at 2 statements in flight while the capture ceiling (8, up to 16)
    and the read path's own pool stay entirely available. The failure mode that
    bound exists for is measured, not hypothetical: a second instance on the
    shared graph produced a **1,505 ms DB probe timeout** and 9/12 failed probes
    (#14219 item 5).
    """
    slots_taken: list[str] = []
    monkeypatch.setattr(ha, "_reserve_capture_slot",
                        lambda key: slots_taken.append(key))
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_batch_item_runner", lambda sdk, corpus, org_id: (
        lambda item: bi.ItemOutcome(bi.BatchItemState.INDEXED)))

    coord = bi.BatchCoordinator(store)
    batch, _items = coord.submit(ORG_ID, ["/d/a.md", "/d/b.md"])
    in_flight_before = ha._CAPTURE_IN_FLIGHT

    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    rows = store.items(ORG_ID, batch.batch_id)
    assert [i.state for i in rows] == [bi.BatchItemState.INDEXED] * 2
    assert slots_taken == [], "batch work must never take a capture slot"
    assert in_flight_before == ha._CAPTURE_IN_FLIGHT
    assert ha._BATCH_EXECUTOR is not ha._CAPTURE_EXECUTOR
    assert bi.BATCH_ITEMS_IN_FLIGHT <= ha._CAPTURE_MAX_IN_FLIGHT, (
        "the batch ceiling is a SMALL share of the capture lane's, so batch "
        "work can never out-schedule captures")


def test_a_drain_that_raises_leaves_the_rows_recoverable(store, monkeypatch):
    """An escaped worker failure must not lose work: `recover()` requeues it."""
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())

    def _explode(sdk, corpus, org_id):
        def _run(item):
            raise RuntimeError("the indexer pool is gone")
        return _run

    monkeypatch.setattr(ha, "_batch_item_runner", _explode)
    coord = bi.BatchCoordinator(store)
    batch, _ = coord.submit(ORG_ID, ["/d/a.md"])
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    # An escaped RUNNER exception is a FAILED ITEM, not a lost row (AX): the
    # reason names the cause and the batch stays usable.
    rows = store.items(ORG_ID, batch.batch_id)
    assert rows[0].state is bi.BatchItemState.FAILED
    assert "indexer pool" in rows[0].reason


def test_a_drain_that_dies_mid_item_leaves_it_recoverable(store, monkeypatch):
    """A KILLED process (not a raised exception) leaves `extracting` rows —
    which is exactly what `recover()` exists to requeue."""
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    rows = store.items(ORG_ID, batch.batch_id)
    assert rows[0].state is bi.BatchItemState.EXTRACTING
    # A killed process leaves the claim looking LIVE for one window; a restart
    # by definition has no process worth honouring, so it takes it back at once.
    assert bi.BatchCoordinator(store).recover(ORG_ID, stale_after_s=0) == [
        batch.batch_id]
    assert store.items(ORG_ID, batch.batch_id)[0].state is bi.BatchItemState.QUEUED


# ── the production runner, driven through the REAL indexer ──────────────────
def _corpus(tmp_path):
    """``(deployment_base, org_root)`` — the batch reads ONLY the org's subtree.

    The deployment base is shared by every tenant (``/data/ingest``) and its
    layout is ``<base>/<org>/…``; a batch resolves every path under
    ``<base>/<THIS org>``, so the fixture has to build that shape or it would be
    testing a boundary that does not exist.
    """
    base = tmp_path / "corpus"
    root = base / ORG_ID
    root.mkdir(parents=True)
    (root / "a.md").write_text("# Doc A\n\nbody one\n", encoding="utf-8")
    (root / "b.md").write_text("# Doc B\n\nbody two\n", encoding="utf-8")
    return base, root


def test_the_production_runner_indexes_through_the_existing_per_file_door(
        sdk_factory, tmp_path, monkeypatch):
    """End-to-end at the ITEM level, on a real graph: accept → indexed.

    This is the runner `_drain_batch` uses. It is `sdk._index_process_unit`, the
    per-file body of the `index_sources_from_directory` walk, so a batch item is
    filed through the SAME door as any other document — not a second pipeline.
    """
    base, root = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219a")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)

    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)
    _batch, items = coord.submit(ORG_ID, [str(root / "a.md")])
    out = coord.run_item(ORG_ID, items[0], runner)

    assert out.state is bi.BatchItemState.INDEXED, out.reason
    assert out.tokens_in == 0 and out.tokens_out == 0, (
        "indexing spends no extraction tokens — the layer must not claim any")


def test_a_re_submitted_file_is_a_no_op_through_the_real_gate(
        sdk_factory, tmp_path, monkeypatch):
    """ACCEPTANCE, on the real indexer: duplicate-submit costs nothing.

    The indexer's own gate (stored `contentHash` vs the file's) reports
    `unchanged`, which this layer surfaces as `skipped-duplicate` — one meaning
    of "duplicate" across the two layers.
    """
    base, root = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219b")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    _, first = coord.submit(ORG_ID, [str(root / "a.md")])
    assert coord.run_item(ORG_ID, first[0], runner).state is (
        bi.BatchItemState.INDEXED)

    # A DIFFERENT batch, the same bytes.
    _, second = coord.submit(ORG_ID, [str(root / "a.md")])
    dup = coord.run_item(ORG_ID, second[0], runner)
    assert dup.state is bi.BatchItemState.SKIPPED_DUPLICATE, dup.reason
    assert dup.tokens_in == 0 and dup.tokens_out == 0
    assert "no tokens spent" in dup.reason


def test_a_changed_file_is_re_written_not_skipped(sdk_factory, tmp_path,
                                                  monkeypatch):
    """#13451's trap, on the real gate: CHANGED text must not be called a dup.

    The indexer takes its WRITE path (status `updated`), which re-writes the
    Source and re-embeds — the stale vector is refreshed rather than left
    describing text that no longer exists.
    """
    base, root = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219c")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    _, first = coord.submit(ORG_ID, [str(root / "a.md")])
    assert coord.run_item(ORG_ID, first[0], runner).state is (
        bi.BatchItemState.INDEXED)

    (root / "a.md").write_text("# Doc A\n\nbody ONE CHANGED\n", encoding="utf-8")
    _, second = coord.submit(ORG_ID, [str(root / "a.md")])
    changed = coord.run_item(ORG_ID, second[0], runner)
    assert changed.state is bi.BatchItemState.INDEXED, changed.reason
    assert changed.state is not bi.BatchItemState.SKIPPED_DUPLICATE


def test_one_bad_file_does_not_fail_the_batch_on_the_real_indexer(
        sdk_factory, tmp_path, monkeypatch):
    """ACCEPTANCE, AX: one unreadable file leaves the rest of the batch usable."""
    base, root = _corpus(tmp_path)
    (root / "bad.md").write_bytes(b"# Bad\n\n\xff\xfe not utf-8\n")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219d")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    batch, items = coord.submit(ORG_ID, [str(root / "a.md"),
                                         str(root / "bad.md"),
                                         str(root / "b.md")])
    for it in items:
        coord.run_item(ORG_ID, it, runner)
    settled = coord.settle(ORG_ID, batch.batch_id)
    rows = {i.ref: i for i in store.items(ORG_ID, batch.batch_id)}

    assert rows[str(root / "a.md")].state is bi.BatchItemState.INDEXED
    assert rows[str(root / "b.md")].state is bi.BatchItemState.INDEXED
    bad = rows[str(root / "bad.md")]
    assert bad.state is bi.BatchItemState.FAILED
    assert bad.reason, "a failure must carry a reason a customer can act on"
    assert settled.state is bi.BatchState.PARTIAL


def test_a_ref_outside_the_ingest_base_is_refused_before_any_read(
        sdk_factory, tmp_path, monkeypatch):
    """#329: the path bound is a REFUSAL, never a read (and never a loss)."""
    base, _root = _corpus(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Nope\n\nsecret\n", encoding="utf-8")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219e")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)
    _, items = coord.submit(ORG_ID, [str(outside)])
    out = coord.run_item(ORG_ID, items[0], runner)
    assert out.state is bi.BatchItemState.FAILED
    assert "refused before any read" in out.reason


def test_a_second_drain_of_the_same_batch_does_not_double_run_items(
        store, monkeypatch):
    """The one race this layer can cause: two drains of one batch.

    Without the claim both would see a `queued` row and both call the runner —
    double work and double spend. The claim is process-local and that limit is
    declared (see `_BATCH_DRAINING`).
    """
    calls: list[str] = []

    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())

    def _runner(sdk, corpus, org_id):
        def _run(item):
            calls.append(item.item_id)
            # The second drain, if it were allowed, would enter here too.
            ha._drain_batch({"org_id": ORG_ID}, item.batch_id)
            return bi.ItemOutcome(bi.BatchItemState.INDEXED)
        return _run

    monkeypatch.setattr(ha, "_batch_item_runner", _runner)
    coord = bi.BatchCoordinator(store)
    batch, _ = coord.submit(ORG_ID, ["/d/a.md", "/d/b.md"])
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)
    assert len(calls) == 2, f"each item must run exactly once, got {calls}"
    assert len(set(calls)) == 2


def test_a_graph_bound_key_is_refused_on_every_batch_surface(client, store,
                                                            monkeypatch):
    """The batch rows live on the tenant's DEFAULT graph, so a per-graph key
    must be refused rather than read a different graph's absence as 404."""
    ha.app.dependency_overrides[ha.get_current_org_gated] = lambda: {
        "org_id": ORG_ID, "key_id": None, "legacy_full_access": True,
        "tier": "free", "graph_id": "graph-other",
    }
    for method, path in (("post", "/v1/batches"),
                         ("get", "/v1/batches"),
                         ("get", "/v1/batches/batch_x"),
                         ("post", "/v1/batches/batch_x/cancel")):
        call = getattr(client, method)
        r = call(path) if method == "get" and path == "/v1/batches" else (
            call(path, json={"items": [{"ref": "/a.md"}]})
            if path == "/v1/batches" else call(path))
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
        assert r.json()["detail"]["error_code"] == "GRAPH_SCOPED_TEAM_SURFACE"


def test_inline_text_is_accepted_and_staged_under_the_ingest_base(client, store,
                                                                 tmp_path,
                                                                 monkeypatch):
    base = tmp_path / "corpus"
    base.mkdir()
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    r = client.post("/v1/batches", json={"items": [
        {"ref": "contract.md", "text": "# Contract\n\nterms\n"}]})
    assert r.status_code == 201, r.text
    batch_id = r.json()["batch_id"]
    row = store.items(ORG_ID, batch_id)[0]
    # ⛔ Under THIS ORG's subtree, never the deployment root: the staged file is
    # the tenant's own document and another tenant must not be able to name it.
    assert row.staged_path.startswith(str(base / ORG_ID))
    from pathlib import Path

    assert Path(row.staged_path).read_text(encoding="utf-8") == (
        "# Contract\n\nterms\n")
    # …and the identity was derived from the text at acceptance, so a duplicate
    # is visible before any read (no tokens spent on it later).
    assert row.content_key == bi.content_key("# Contract\n\nterms\n")


def test_inline_text_without_configured_storage_is_a_503_not_a_fake_ack(
        client, monkeypatch):
    """A server missing its storage config is 503, never a 400 that a client
    with a valid body would retry for ever (#14219 review, NB5)."""
    monkeypatch.delenv("TORTOISE_INGEST_BASE_DIR", raising=False)
    r = client.post("/v1/batches", json={"items": [
        {"ref": "contract.md", "text": "# Contract\n\nterms\n"}]})
    assert r.status_code == 503, r.text
    assert "storage" in r.json()["detail"].lower()
    assert "paths under" in r.json()["detail"]


# ── the review's BLOCKING findings, pinned ─────────────────────────────────
def test_a_ref_naming_another_tenants_file_is_refused(client, store, tmp_path,
                                                      monkeypatch):
    """BLOCKING 1: the ingest base is deployment-wide; the BOUND is per org.

    `TORTOISE_INGEST_BASE_DIR` is one directory for every tenant (its documented
    layout is `<base>/<org>/…`), so resolving a caller's `ref` against it would
    let any tenant index any other tenant's file — a cross-tenant read with an
    ordinary write key. Both the org's own file and another org's are laid out
    here, and only the first may be read.
    """
    base = tmp_path / "corpus"
    (base / ORG_ID).mkdir(parents=True)
    (base / "victim-org").mkdir(parents=True)
    (base / ORG_ID / "mine.md").write_text("# Mine\n\nmine\n", encoding="utf-8")
    (base / "victim-org" / "secret.md").write_text(
        "# Victim\n\nsecret\n", encoding="utf-8")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))

    r = client.post("/v1/batches", json={"items": [
        {"ref": str(base / ORG_ID / "mine.md")},
        {"ref": str(base / "victim-org" / "secret.md")},
    ]})
    assert r.status_code == 201, r.text
    batch_id = r.json()["batch_id"]

    from tortoise import hosted_api as _ha
    sdk = object()  # never reached: both items are settled by the path bound
    runner = _ha._batch_item_runner(sdk, "corpus", ORG_ID)
    coord = bi.BatchCoordinator(store)
    out = [coord.run_item(ORG_ID, i, runner) for i in store.items(ORG_ID, batch_id)]

    assert out[0].state is bi.BatchItemState.FAILED, (
        "an object() sdk cannot index — the first item's refusal is the sdk, "
        "not the bound")
    assert out[1].state is bi.BatchItemState.FAILED
    assert "this team's document storage" in out[1].reason, (
        f"the other tenant's path must be refused by the TENANCY bound, not by "
        f"accident (got {out[1].reason!r})")


def test_bookkeeping_does_not_queue_behind_a_running_drain(client, store,
                                                           monkeypatch):
    """BLOCKING 3: the status surface must not wait for a drain (~15 h/batch).

    Two drains hold every worker of the DRAIN pool for the whole batch; a status
    read that shared that pool would wait behind them and the 10 s transport
    bound would turn it into a 504 for a handler that had already done its work.
    """
    import time

    r = client.post("/v1/batches", json={"items": [{"ref": "/d/a.md"}]})
    batch_id = r.json()["batch_id"]

    release = __import__("threading").Event()
    try:
        held = [ha._BATCH_EXECUTOR.submit(release.wait, 10)
                for _ in range(ha._BATCH_EXECUTOR._max_workers)]
        started = time.monotonic()
        s = client.get(f"/v1/batches/{batch_id}")
        elapsed = time.monotonic() - started
    finally:
        release.set()
        for f in held:
            f.result(timeout=5)

    assert s.status_code == 200, s.text
    assert elapsed < 3.0, (
        f"the status route waited {elapsed:.2f}s behind the drain pool — it must "
        "run on its own pool")
    assert ha._BATCH_DB_EXECUTOR is not ha._BATCH_EXECUTOR


def test_a_deferred_batch_is_remembered_and_retried(client, store, monkeypatch):
    """BLOCKING 4: an ACKed batch the ceiling/gate deferred must not be orphaned.

    `submit` returning False (ceiling full, or the off-peak window closed) used
    to be discarded: the batch was ACKed with an ETA and never worked. The
    deferral is now remembered, and the retry tick hands it over once it can.
    """
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    monkeypatch.setattr(ha, "_BATCH_WORKER", type("W", (), {
        "ceiling": 2,
        "submit": staticmethod(lambda *a, **k: False),
    })())

    r = client.post("/v1/batches", json={"items": [{"ref": "/d/a.md"}]})
    assert r.status_code == 201
    batch_id = r.json()["batch_id"]
    deferred = [bid for _org, bid in ha._BATCH_RETRY]
    assert batch_id in deferred, (
        "a refused submit must be REMEMBERED, not dropped on the floor")

    # The window opens: the retry tick hands it to the worker and forgets it.
    submitted: list[str] = []
    monkeypatch.setattr(ha, "_BATCH_WORKER", type("W", (), {
        "ceiling": 2,
        "submit": staticmethod(lambda fn, *a, **k: submitted.append(a[1]) or True),
    })())
    ha._batch_retry_once()

    assert submitted == [batch_id]
    assert [bid for _org, bid in ha._BATCH_RETRY] == []


def test_the_first_batch_touch_after_a_boot_resumes_unfinished_work(
        client, store, monkeypatch):
    """BLOCKING 5: `recover()` needs a CALLER, or "a restart resumes" is prose."""
    monkeypatch.setattr(ha, "_BATCH_RECOVERED", set())
    # The claim the dead process left behind is older than the reclaim window.
    monkeypatch.setattr(bi, "BATCH_RECLAIM_AFTER_S", 0.0)
    submitted: list[str] = []
    monkeypatch.setattr(ha, "_BATCH_WORKER", type("W", (), {
        "ceiling": 2,
        "submit": staticmethod(lambda fn, *a, **k: submitted.append(a[1]) or True),
    })())
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    # …the process died mid-item.
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)

    listed = client.get("/v1/batches")
    assert listed.status_code == 200, listed.text

    rows = store.items(ORG_ID, batch.batch_id)
    assert rows[0].state is bi.BatchItemState.QUEUED, (
        "the interrupted item must be requeued by the tenant's first touch")
    assert batch.batch_id in submitted, (
        "resumed work must be handed to a worker, not just relabelled")
    recovered = store.get_batch(ORG_ID, batch.batch_id)
    assert recovered.state is bi.BatchState.RESUMING


def test_the_advertised_inline_bound_is_reachable(client, store, tmp_path,
                                                  monkeypatch):
    """BLOCKING 6: the route's body cap and its own bound are ONE number.

    The default 256 KiB body cap made the handler's 4 MiB bound dead code, so a
    customer uploading real documents got the generic cap message and the
    "send it as several batches" guidance could never fire.
    """
    from tortoise import body_limits

    assert bi.BATCH_MAX_INLINE_BYTES > body_limits.BODY_MAX_BYTES

    base = tmp_path / "corpus"
    (base / ORG_ID).mkdir(parents=True)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    # 400 KiB of text: past the DEFAULT cap, inside the batch route's own.
    big = "# Doc\n\n" + ("x" * 400_000)
    r = client.post("/v1/batches", json={"items": [{"ref": "big.md", "text": big}]})
    assert r.status_code == 201, (
        f"a 400 KiB document must be accepted on this route (got "
        f"{r.status_code}: {r.text[:200]})")

    # …and past the batch's own bound it is a 413 that says what to do.
    huge = "# Doc\n\n" + ("x" * (bi.BATCH_MAX_INLINE_BYTES + 1024))
    too_big = client.post("/v1/batches",
                          json={"items": [{"ref": "huge.md", "text": huge}]})
    assert too_big.status_code == 413, too_big.text


def _age_claim(store, org_id, batch_id, item_id,
               seconds=bi.BATCH_RECLAIM_AFTER_S + 1):
    """Age a row's claim clock so the reclaim window treats it as DEAD.

    ⛔ Aged, not switched off: `BATCH_RECLAIM_AFTER_S` is the rule under test, and
    a test that sets the window to 0 is testing a different rule (and would pass
    even if the guard did not exist). This leaves the real window in place and
    makes the claim genuinely old.
    """
    from datetime import UTC, datetime, timedelta

    item = store.get_item(org_id, batch_id, item_id)
    item.updated_at = (
        datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()
    store.put_item(org_id, item)
    return item


def test_an_embedded_item_can_be_finished_not_re_claimed(store):
    """NB10: `embedded` is a legal starting point, not an illegal re-entry."""
    coord = bi.BatchCoordinator(store)
    _batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EMBEDDED)
    # The claim must be OLD before this pass may re-enter it (a fresh one may
    # belong to a live peer — #14415 review round 5).
    _age_claim(store, ORG_ID, items[0].batch_id, items[0].item_id)
    out = coord.run_item(ORG_ID, items[0],
                         lambda i: bi.ItemOutcome(bi.BatchItemState.INDEXED))
    assert out.state is bi.BatchItemState.INDEXED


def test_the_list_surface_reports_real_item_counts(client, store):
    """NB3: a list saying `items_total: 0` for a 500-item batch is a lie."""
    r = client.post("/v1/batches", json={"items": [
        {"ref": "/d/a.md"}, {"ref": "/d/b.md"}]})
    batch_id = r.json()["batch_id"]
    items = store.items(ORG_ID, batch_id)
    coord = bi.BatchCoordinator(store)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.INDEXED)

    listed = client.get("/v1/batches").json()["batches"]
    row = next(b for b in listed if b["batch_id"] == batch_id)
    assert row["items_total"] == 2
    assert row["counts"]["indexed"] == 1
    assert row["counts"]["pending"] == 1
    assert "items" not in row, "the list must not ship every item payload"


def test_a_staged_path_is_never_put_on_the_wire(client, store, tmp_path,
                                               monkeypatch):
    """NB4: the server's absolute filesystem path is not a customer field."""
    base = tmp_path / "corpus"
    base.mkdir()
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    r = client.post("/v1/batches", json={"items": [
        {"ref": "x.md", "text": "# X\n\nx\n"}]})
    assert r.status_code == 201
    batch_id = r.json()["batch_id"]
    detail = client.get(f"/v1/batches/{batch_id}").json()
    assert all("staged_path" not in it for it in detail["items"])
    assert str(base) not in str(detail)


def test_the_estimate_reads_the_door_that_declares_the_token_dimension():
    """NB7: pin the resource label, or the regression it fixed returns silently.

    `capture_extraction` DECLARES `resources={"sessions"}`, and
    `_carry_allowances` returns the org UNCHANGED for a label no dimension
    declares — so an estimate carrying any other label silently reports "no
    included amount" for ever.
    """
    from tortoise.allowance_gate import DIMENSIONS

    dim = next(d for d in DIMENSIONS if d.dimension == "capture_extraction")
    assert ha._BATCH_ALLOWANCE_RESOURCE in dim.resources, (
        f"the estimate reads {ha._BATCH_ALLOWANCE_RESOURCE!r} but the dimension "
        f"is declared on {sorted(dim.resources)} — the carry would return the org "
        "unchanged and the estimate would report 'no included amount'")


# ── #14415 review round 1: the caller halves of B1/B2/B4 ──────────────────
def test_the_first_touch_does_not_requeue_a_batch_a_live_drain_owns(
        client, store, monkeypatch):
    """⛔ B1 (caller half): "on boot" must not claim a LIVE drain's batch.

    `_recover_org_batches` runs on the first touch of the tenant's batch surface,
    and in the documented flow that touch is the customer's status poll — mid
    drain. The live set is passed to `recover()` so a claim belonging to a
    process that is still running is not treated as a dead one. Without this the
    live runner's `indexed` outcome became an illegal edge and was discarded.
    """
    monkeypatch.setattr(ha, "_BATCH_RECOVERED", set())
    submitted: list[str] = []
    monkeypatch.setattr(ha, "_BATCH_WORKER", type("W", (), {
        "ceiling": 2,
        "submit": staticmethod(lambda fn, *a, **k: submitted.append(a[1]) or True),
    })())
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)

    # The drain for THIS batch is live in this process.
    ha._BATCH_DRAINING.add(batch.batch_id)
    try:
        assert ha._recover_org_batches({"org_id": ORG_ID}) == []
    finally:
        ha._BATCH_DRAINING.discard(batch.batch_id)

    row = store.items(ORG_ID, batch.batch_id)[0]
    assert row.state is bi.BatchItemState.EXTRACTING, (
        "a live drain's claim must survive the first status poll")
    assert submitted == [], "a live batch must not be re-submitted by recovery"

    # With no live drain the same row IS recoverable — the hook still works.
    # (The org was marked recovered by the first call, so it must be cleared:
    # that idempotence is `recover()`'s own one-scan-per-tenant contract.) The
    # claim must also be STALE: `BATCH_RECLAIM_AFTER_S` is what stops a poll from
    # taking a claim a PEER process holds, so a dead-in-this-process claim that is
    # one second old is deliberately left alone by default.
    monkeypatch.setattr(bi, "BATCH_RECLAIM_AFTER_S", 0.0)
    ha._BATCH_RECOVERED.discard(ORG_ID)
    assert ha._recover_org_batches({"org_id": ORG_ID}) == [batch.batch_id]
    assert store.items(ORG_ID, batch.batch_id)[0].state is (
        bi.BatchItemState.QUEUED)


def test_a_drain_that_leaves_an_item_non_terminal_re_drives_it(store,
                                                              monkeypatch):
    """⛔ B2 (caller half): nothing re-scheduled a non-terminal leftover.

    `_BATCH_RETRY` is filled only when a SUBMIT is refused, and `recover()` runs
    once per org per process — so an item left `queued` by a retryable skip (or
    `extracting` by a swallowed `IllegalTransition`) sat non-terminal for the
    process lifetime: the customer's status surface read `pending > 0` with no
    error and no progress.
    """
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    monkeypatch.setattr(ha, "_batch_item_runner",
                        lambda sdk, corpus, org_id: (
                            lambda item: bi.ItemOutcome(
                                bi.BatchItemState.QUEUED,
                                reason="index lock held")))

    coord = bi.BatchCoordinator(store)
    batch, _items = coord.submit(ORG_ID, ["/d/a.md"])
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert [bid for _org, bid in ha._BATCH_RETRY] == [batch.batch_id], (
        "the leftover must be queued for another pass, not stranded")
    assert store.items(ORG_ID, batch.batch_id)[0].state is (
        bi.BatchItemState.QUEUED)

    # ⛔ …and the loop is BOUNDED: once the ceiling is spent the same drain
    # settles the item FAILED, so the retry stops instead of spinning for ever.
    for _ in range(bi.BATCH_MAX_ATTEMPTS):
        ha._BATCH_RETRY.clear()
        ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)
    assert store.items(ORG_ID, batch.batch_id)[0].state is (
        bi.BatchItemState.FAILED)
    assert [bid for _org, bid in ha._BATCH_RETRY] == []


def test_both_suites_are_selected_by_a_hosted_api_only_diff():
    """⛔ B4: registration that no diff class reaches is a guard that never runs.

    Selection is surface -> membership. Both suites pin the `/v1/batches`
    handlers in `tortoise/hosted_api.py` (an EXACT `api` SOURCE_PATTERNS match,
    which REPLACES the `core` fallback), so registering them on `core` alone left
    a `hosted_api.py`-only diff running NEITHER — the route layer could regress
    behind a green required check. `--integrity` reads classification, not
    reachability, so it stayed green over the gap.
    """
    from tools.ci_selection import load_manifest, select

    selected = set(select(["tortoise/hosted_api.py"], "pull_request",
                          load_manifest())["test_files"])
    for name in ("test_14219_batch_ingest.py", "test_14219_batches_api.py"):
        assert name in selected, (
            f"{name} is not selected by a tortoise/hosted_api.py diff — the "
            "suite that pins the route layer would not run on the change it "
            "guards")

    layer = set(select(["tortoise/batch_ingest.py"], "pull_request",
                       load_manifest())["test_files"])
    for name in ("test_14219_batch_ingest.py", "test_14219_batches_api.py"):
        assert name in layer, (
            f"{name} is not selected by a tortoise/batch_ingest.py diff")


# ── #14415 review round 2: the caller halves, pinned ──────────────────────
def test_a_drain_that_fails_before_it_can_read_the_rows_still_re_drives(store,
                                                                      monkeypatch):
    """⛔ ROUND-2 BLOCKING 1 arm 1: the re-drive was the try block's last line.

    One transient failure BEFORE it — `_batch_store_for`, a `store.items()`,
    `settle()` — and nothing re-drove the batch: `_BATCH_RETRY` stayed empty and
    `recover()` is one-shot per org, so the status surface reported
    `pending > 0` for the process lifetime with no error and no progress. The
    re-drive lives in `finally` now, and a pass that cannot even build a store
    still re-drives blind — the next tick re-reads the rows.
    """
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])

    def _no_store(org):
        raise RuntimeError("credential refresh failed")

    monkeypatch.setattr(ha, "_batch_store_for", _no_store)
    coord = bi.BatchCoordinator(store)
    batch, _items = coord.submit(ORG_ID, ["/d/a.md"])

    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert [bid for _org, bid in ha._BATCH_RETRY] == [batch.batch_id], (
        "a pass that failed before it could read the rows must still re-drive")
    assert batch.batch_id not in ha._BATCH_DRAINING, (
        "the process-local claim must be released even on that path, or the "
        "batch could never be worked again")


def test_cancelling_an_already_finished_batch_does_not_claim_it_was_stopped(
        client, store, monkeypatch):
    """⛔ ROUND-2 NB5: the copy contradicted the payload.

    The message was set unconditionally, so cancelling a settled batch returned
    `status: completed` beside "Cancelled. … not charged" over work that
    completed and WAS charged — the same lie B3 exists to prevent, one layer up.
    """
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    for it in items:
        coord.advance(ORG_ID, it, bi.BatchItemState.EXTRACTING, attempt=True)
        coord.advance(ORG_ID, it, bi.BatchItemState.INDEXED, tokens_in=9)
    coord.settle(ORG_ID, batch.batch_id)

    r = client.post(f"/v1/batches/{batch.batch_id}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "completed"
    assert not body["cancelled_at"]
    assert "Cancelled." not in body["message"], (
        "the copy must not tell the customer work was stopped and not charged "
        f"over a completed batch: {body['message']!r}")
    assert "already finished" in body["message"]


# ── #14415 review round 3: the three blocking findings, pinned ────────────
def test_a_breakdown_that_hides_the_leftover_still_re_drives(store,
                                                            monkeypatch):
    """⛔ ROUND-3 BLOCKING 1: the re-drive was conditional on the read that broke.

    One graph outage spans `settle()`'s read and the `finally`'s own read
    back-to-back, so `any(not item.terminal ...)` never evaluated and nothing was
    enqueued — the round-2 wedge, restored. An unreadable leftover is not
    evidence of completion: it re-drives blind.
    """
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_batch_item_runner",
                        lambda sdk, corpus, org_id: (
                            lambda item: bi.ItemOutcome(bi.BatchItemState.QUEUED,
                                                        reason="index lock held")))

    coord = bi.BatchCoordinator(store)
    batch, _items = coord.submit(ORG_ID, ["/d/a.md"])
    # The batch's first read succeeds and every LATER one fails: the drain claims
    # the item, then its settle read and the finally's read both blow up.
    real_items = store.items
    calls = {"n": 0}

    def flaky_items(org_id, batch_id):
        calls["n"] += 1
        if calls["n"] <= 1:
            return real_items(org_id, batch_id)
        raise RuntimeError("graph outage")

    monkeypatch.setattr(store, "items", flaky_items)
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert [bid for _org, bid in ha._BATCH_RETRY] == [batch.batch_id], (
        "a leftover we could not READ must be re-driven, never assumed finished")
    assert batch.batch_id not in ha._BATCH_DRAINING


def test_a_settle_write_that_failed_leaves_the_row_repaired_not_lagging(
        store, monkeypatch):
    """⛔ ROUND-3 BLOCKING 1 arm B: all items terminal, `settle()` lost.

    `any(not item.terminal)` is False, so nothing re-drove and nothing repaired:
    the row sat `accepted` over finished, charged work for the process lifetime —
    `complete: false`, `pending: 0` — while ONE pass repairs it to `completed`.
    """
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_batch_item_runner",
                        lambda sdk, corpus, org_id: (
                            lambda item: bi.ItemOutcome(bi.BatchItemState.INDEXED,
                                                        tokens_in=5)))

    coord = bi.BatchCoordinator(store)
    batch, _items = coord.submit(ORG_ID, ["/d/a.md"])
    # Every item lands; only the batch row's write fails. Both writers are
    # faulted: `settle()` writes through `put_batch_if_state` (the batch row's
    # compare-and-set, #14415 review round 5), so faulting `put_batch` alone
    # would let the settle land and the test would prove nothing.
    real_put_batch = store.put_batch
    real_put_batch_if_state = store.put_batch_if_state
    state = {"fail": True}

    def flaky_put_batch(row):
        if state["fail"]:
            raise RuntimeError("put_batch timeout")
        return real_put_batch(row)

    def flaky_put_batch_if_state(row, expected):
        if state["fail"]:
            raise RuntimeError("put_batch_if_state timeout")
        return real_put_batch_if_state(row, expected)

    monkeypatch.setattr(store, "put_batch", flaky_put_batch)
    monkeypatch.setattr(store, "put_batch_if_state", flaky_put_batch_if_state)
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)
    state["fail"] = False
    # The pass could not write the row, so it must remember the batch…
    assert [bid for _org, bid in ha._BATCH_RETRY] == [batch.batch_id]
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    # …and the repair pass, with the write working again, settles it.
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)
    row = store.get_batch(ORG_ID, batch.batch_id)
    assert row.state is bi.BatchState.COMPLETED, (
        f"a lagging row over finished work must be repaired, not left {row.state}")
    assert [bid for _org, bid in ha._BATCH_RETRY] == []


def test_a_retry_pass_never_runs_work_on_a_cancelled_batch(store, monkeypatch):
    """⛔ ROUND-3 BLOCKING 2: the re-drive could spend AFTER a cancel.

    A drain whose terminal write failed left an item `extracting` with
    attempts=1; `cancel()` leaves such a row alone (it may be a live runner); the
    retry tick then re-entered the drain and started a THIRD runner call after
    the cancel the customer was told would not be charged — measured: three
    runner calls, 3,000 tokens, row `cancelled`, both items `indexed`.
    """
    calls: list[str] = []

    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())

    def _runner(sdk, corpus, org_id):
        def _run(item):
            calls.append(item.item_id)
            return bi.ItemOutcome(bi.BatchItemState.INDEXED, tokens_in=1000)
        return _run

    monkeypatch.setattr(ha, "_batch_item_runner", _runner)
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md", "/d/b.md"])
    # Item 2 is mid-flight when the process's bookkeeping write fails and the
    # customer, seeing a stuck batch, cancels.
    coord.advance(ORG_ID, items[1], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.cancel(ORG_ID, batch.batch_id, reason="stuck, stop it")
    before = list(calls)

    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert calls == before, (
        f"the retry tick ran {len(calls) - len(before)} runner call(s) after a "
        "cancel — nothing may run and nothing may be charged after it")
    after = store.items(ORG_ID, batch.batch_id)
    assert all(i.terminal for i in after), (
        f"the dead claim must be settled, not stranded non-terminal: "
        f"{[(i.ref, str(i.state)) for i in after]}")
    assert all(i.state is bi.BatchItemState.CANCELLED for i in after)
    assert store.get_batch(ORG_ID, batch.batch_id).state is bi.BatchState.CANCELLED
    assert [bid for _org, bid in ha._BATCH_RETRY] == [], (
        "a cancelled batch must not be re-driven at all")


def test_a_claim_re_entered_from_extracting_counts_towards_the_ceiling(store,
                                                                     monkeypatch):
    """⛔ ROUND-3 BLOCKING 3: the ceiling never fired on a re-entered claim.

    It lived inside `if fresh.state is QUEUED`, so an item re-driven from a dead
    `extracting` claim never incremented `attempts`: every retry tick called the
    runner again, spending real tokens the failed advance never recorded.
    """
    calls: list[int] = []

    def _runner(item):
        calls.append(item.attempts)
        return bi.ItemOutcome(bi.BatchItemState.INDEXED, tokens_in=7)

    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)

    # The terminal write keeps failing, so the item stays `extracting` and is
    # re-entered by each pass. The ceiling must bound it. ⛔ BOTH write paths are
    # faulted (#14415 review round 4): the terminal write is a COMPARE-AND-SET
    # (`put_item_if_state`) now, so patching `put_item` alone injected the fault
    # into a path this test no longer exercises — the item reached `indexed` and
    # the ceiling was never asked the question.
    real_put_item = store.put_item
    real_put_item_if_state = store.put_item_if_state

    def failing_terminal_write(org_id, item):
        if item.state in (bi.BatchItemState.INDEXED,):
            raise RuntimeError("put_item timeout")
        return real_put_item(org_id, item)

    def failing_terminal_write_if_state(org_id, item, expected,
                                        expected_attempts=None):
        if item.state in (bi.BatchItemState.INDEXED,):
            raise RuntimeError("put_item_if_state timeout")
        return real_put_item_if_state(org_id, item, expected)

    monkeypatch.setattr(store, "put_item", failing_terminal_write)
    monkeypatch.setattr(store, "put_item_if_state",
                        failing_terminal_write_if_state)
    for _ in range(bi.BATCH_MAX_ATTEMPTS + 2):
        row = store.items(ORG_ID, batch.batch_id)[0]
        if row.state in (bi.BatchItemState.EXTRACTING, bi.BatchItemState.EMBEDDED):
            _age_claim(store, ORG_ID, batch.batch_id, row.item_id)
            row = store.items(ORG_ID, batch.batch_id)[0]
        # The drain's own per-item guard: a bookkeeping failure must not
        # abandon the rest, and the item keeps whatever state it reached.
        with contextlib.suppress(RuntimeError):
            coord.run_item(ORG_ID, row, _runner)

    assert len(calls) <= bi.BATCH_MAX_ATTEMPTS, (
        f"the runner was called {len(calls)} times for a bound of "
        f"{bi.BATCH_MAX_ATTEMPTS} — an unbounded re-entry spends invisibly")
    final = store.items(ORG_ID, batch.batch_id)[0]
    assert final.state is bi.BatchItemState.FAILED
    assert "gave up" in final.reason


def test_a_relative_ref_resolves_under_the_org_root_not_the_server_cwd(
        sdk_factory, tmp_path, monkeypatch):
    """⛔ ROUND-4 REVIEW P1: the documented `ref` form was UNREACHABLE.

    The model's own docstring promised a path "under TORTOISE_INGEST_BASE_DIR",
    which every ordinary caller supplies relative (`a.md`) — but the resolver
    resolved a relative candidate against the process CWD and refused it, so no
    customer could use it (they cannot spell an absolute server path, and the API
    never tells them the base). Measured on the pre-fix tree: `a.md` -> refused.

    The join under the ORG root must not weaken the tenancy bound: `..` still
    escapes the root and is still refused before any read.
    """
    base, root = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219rel")
    runner = ha._batch_item_runner(sdk, "corpus", ORG_ID)
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    _, items = coord.submit(ORG_ID, ["a.md"])
    out = coord.run_item(ORG_ID, items[0], runner)
    assert out.state is bi.BatchItemState.INDEXED, (
        f"a relative ref must resolve under <base>/<org_id>; got {out.reason}")

    # The bound still holds: a traversal out of the org root is refused, and
    # (victim org file exists) it is refused BEFORE any read.
    victim = base / "org_victim"
    victim.mkdir()
    (victim / "secret.md").write_text("another tenant\n", encoding="utf-8")
    _, escaped = coord.submit(ORG_ID, ["../org_victim/secret.md"])
    bad = coord.run_item(ORG_ID, escaped[0], runner)
    assert bad.state is bi.BatchItemState.FAILED
    assert "not a path under this team's document storage" in bad.reason


def test_an_unreadable_usage_ledger_refuses_the_estimate_instead_of_zeroing_it(
        client, monkeypatch):
    """⛔ ROUND-4 REVIEW P2: an unreadable ledger used to read as "nothing spent".

    `get_capture_usage` never raises — it degrades to a zero view and NAMES the
    fault (`read_error`/`period_error`), whose contract says those must REFUSE
    while `absent` is a real zero. Reading `capture_tokens_total or 0` therefore
    UNDERSTATED the overage: the one outcome owner §4 forbids ("so the overage is
    never discovered on the bill"). A fault now refuses with a retryable 503
    rather than inventing a zero; `absent` still prices as a zero.
    """
    async def _with_allowance(org, resource, **kwargs):
        return {"max_extraction_tokens": 3_000_000}

    monkeypatch.setattr(ha, "_carry_allowances_off_loop", _with_allowance)

    async def _fault(fn, **kwargs):
        return {"measured": False, "unmeasured_reason": "read_error",
                "capture_tokens_total": 0}

    monkeypatch.setattr(ha, "_cp_offload", _fault)
    items = [{"ref": f"/docs/{n}.md"} for n in range(100)]
    r = client.post("/v1/batches", json={"items": items})
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["error_code"] == "BATCH_ESTIMATE_UNAVAILABLE"

    async def _absent(fn, **kwargs):
        return {"measured": False, "unmeasured_reason": "absent",
                "capture_tokens_total": 0}

    monkeypatch.setattr(ha, "_cp_offload", _absent)
    ok = client.post("/v1/batches", json={"items": items})
    assert ok.status_code == 402, (
        "an ABSENT ledger is a legitimate zero, not a fault — it must still be "
        f"priced and refused for acknowledgement, got {ok.status_code}")
    assert ok.json()["detail"]["estimate"]["tokens_already_used"] == 0


def test_the_acknowledgement_copy_does_not_invent_an_overage_it_cannot_price(
        client, monkeypatch):
    """⛔ ROUND-4 REVIEW P2: the 402 told the customer to confirm an overage the
    same payload reports as $0.00 (no tier publishes `max_extraction_tokens`).

    The owner's disclosure stands — the batch is still refused until
    acknowledged — but the sentence must say which situation it is: an unpriced
    allowance is not a $0.00 overage, and an instruction nobody can act on is how
    a spend surface loses its credibility.
    """
    async def _with_allowance(org, resource, **kwargs):
        return {}          # no published included amount, the real production shape

    monkeypatch.setattr(ha, "_carry_allowances_off_loop", _with_allowance)
    items = [{"ref": f"/docs/{n}.md"} for n in range(100)]
    r = client.post("/v1/batches", json={"items": items})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    assert detail["estimate"]["included_tokens"] is None
    assert detail["estimate"]["overage_usd"] == 0.0
    assert "confirm the expected overage" not in detail["message"], (
        "the copy names an expected overage beside a $0.00 estimate")
    assert "not priced against your plan yet" in detail["message"]
    assert "acknowledge_overage" in detail["message"]


# ── round 2 blockers ───────────────────────────────────────────────────────
def test_a_failed_acceptance_leaves_NO_rows_for_a_resume_to_adopt(
        client, store, monkeypatch):
    """BLOCKING 2: a 503 that says "nothing was accepted" must be TRUE.

    The items are written before the batch row, so a graph timeout mid-loop used
    to leave them behind: `pending_batches()` listed them and `recover()` adopted
    them ("the items ARE the record of accepted work") — so documents the
    customer was told were refused got ingested and paid for, and the retry the
    503 invited ingested them a second time.
    """
    real_put = store.put_item
    calls = {"n": 0}

    def _flaky(org_id, item):
        calls["n"] += 1
        if calls["n"] == 3:
            raise TimeoutError("the graph did not answer")
        return real_put(org_id, item)

    monkeypatch.setattr(store, "put_item", _flaky)

    r = client.post("/v1/batches",
                    json={"items": [{"ref": f"/docs/{n}.md"} for n in range(5)]})
    assert r.status_code == 503, r.text
    assert "nothing was accepted" in r.text

    assert store.pending_batches(ORG_ID) == [], (
        "the rejected batch's rows survived — the resume scan would adopt and "
        "drain documents the customer was told were not accepted")


def test_the_drain_charges_the_one_meter_when_an_item_reports_tokens(
        store, monkeypatch):
    """BLOCKING 1: a batch that spends must move `capture_extraction`."""
    from tortoise import metering

    charged: list[dict] = []
    monkeypatch.setattr(metering, "record_capture_usage",
                        lambda org_id, **kw: charged.append({"org": org_id, **kw}))
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())

    def _runner(sdk, corpus, org_id, *_a, **_k):
        return lambda item: bi.ItemOutcome(bi.BatchItemState.INDEXED,
                                           tokens_in=700, tokens_out=30)

    monkeypatch.setattr(ha, "_batch_item_runner", _runner)
    coord = bi.BatchCoordinator(store)
    batch, _ = coord.submit(ORG_ID, ["/d/a.md"])
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert charged == [{"org": ORG_ID, "tokens_in": 700, "tokens_out": 30}], (
        "an item that spent tokens did not reach the capture meter — the budget "
        "the owner mandated would be reported as unused forever")


def test_a_zero_token_item_makes_no_ledger_call(monkeypatch):
    """The current indexer spends nothing: a call would mint a phantom capture.

    `capture_calls` is the denominator that keeps a genuinely-free capture
    distinguishable from an unmeasured one (#3359), so a no-op item must not
    burn it.
    """
    from tortoise import metering

    called: list[dict] = []
    monkeypatch.setattr(metering, "record_capture_usage",
                        lambda org_id, **kw: called.append(kw))
    item = bi.BatchItem(batch_id="b", item_id="b-0", ref="/a.md")
    assert bi.record_item_spend(ORG_ID, item) == 0
    assert called == []


def test_a_drain_that_leaves_an_item_queued_is_put_back_on_the_retry_tick(
        store, monkeypatch):
    """BLOCKING 3: a retryable verdict must not strand the batch forever."""
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_batch_item_runner",
                        lambda *a, **k: (lambda item: bi.ItemOutcome(
                            bi.BatchItemState.QUEUED, reason="lock-held")))
    monkeypatch.setattr(ha, "_BATCH_RETRY", [])
    monkeypatch.setattr(ha, "_BATCH_RETRY_THREAD", None)

    coord = bi.BatchCoordinator(store)
    batch, _ = coord.submit(ORG_ID, ["/d/a.md"])
    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    rows = store.items(ORG_ID, batch.batch_id)
    assert rows[0].state is bi.BatchItemState.QUEUED, "retryable, so still work"
    assert [bid for _org, bid in ha._BATCH_RETRY] == [batch.batch_id], (
        "the drain left pending work and queued no re-sweep — the batch would "
        "sit `running` until the process restarted")
    assert store.get_batch(ORG_ID, batch.batch_id).state is bi.BatchState.RUNNING


def test_the_402_sentence_matches_the_numbers_beside_it(client):
    """NB9: with no configured allowance there IS no overage to confirm."""
    r = client.post("/v1/batches",
                    json={"items": [{"ref": f"/d/{n}.md"} for n in range(100)]})
    assert r.status_code == 402, r.text
    detail = r.json()["detail"]
    est = detail["estimate"]
    assert est["included_tokens"] is None
    assert est["overage_usd"] == 0.0
    assert "expected spend" in detail["message"], detail["message"]
    assert "expected overage" not in detail["message"]
    assert "usage_measured" in est, (
        "the receipt must say whether the spend figure beside it is measured")


def test_an_unmeasured_usage_read_is_reported_not_read_as_zero(client,
                                                              monkeypatch):
    """NB3: `get_capture_usage` never raises — a fault is a ZERO view.

    Reading that zero as "this team has spent nothing" under-reports the spend in
    the exact figure the owner mandated, so the receipt carries the flag.
    """
    from tortoise import metering

    monkeypatch.setattr(metering, "get_capture_usage",
                        lambda org_id: {"measured": False, "capture_tokens_total": 0,
                                        "unmeasured_reason": "read_error"})
    r = client.post("/v1/batches", json={"items": [{"ref": "/d/a.md"}]})
    assert r.status_code == 201, r.text
    assert r.json()["estimate"]["usage_measured"] is False


# ── #14415 review round 5: the reclaim window's two holes, pinned ─────────
def test_a_restart_defers_a_fresh_claim_instead_of_discarding_it(store,
                                                                monkeypatch):
    """⛔ ROUND-5 BLOCKING 1: a fresh claim deferred is fine; DISCARDED is not.

    The staleness window's premise is "a young claim may belong to a live peer".
    Applying it by SKIPPING the whole batch threw the decision away: an item in
    flight when the process died is SECONDS old at the restart that recovers it —
    the ordinary redeploy — and `_BATCH_RECOVERED` makes that scan one-shot, so
    the item stayed `extracting` for the process lifetime with the batch reporting
    `pending > 0` and nothing to re-drive it.
    """
    from tortoise import batch_ingest as bi

    monkeypatch.setattr(ha, "_BATCH_RECOVERED", set())
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    submitted: list[str] = []
    monkeypatch.setattr(ha, "_BATCH_WORKER", type("W", (), {
        "ceiling": 2,
        "submit": staticmethod(lambda fn, *a, **k: submitted.append(a[1]) or True),
    })())
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/a.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    # …the process dies here, one second later the replacement boots.

    resumed = ha._recover_org_batches({"org_id": ORG_ID})

    assert resumed == [batch.batch_id], (
        "a batch holding only a FRESH claim must still be resumed, or the claim "
        "is stranded for ever")
    assert batch.batch_id in submitted
    assert store.items(ORG_ID, batch.batch_id)[0].state is (
        bi.BatchItemState.EXTRACTING), (
        "the fresh claim itself must NOT be requeued — a peer may hold it")

    # Once the claim is genuinely OLD the same batch becomes reclaimable — the
    # window is aged, not switched off (see `_age_claim`).
    _age_claim(store, ORG_ID, batch.batch_id, items[0].item_id)
    stale = bi.BatchCoordinator(store).recover(ORG_ID)
    assert stale == [batch.batch_id]
    assert store.items(ORG_ID, batch.batch_id)[0].state is (
        bi.BatchItemState.QUEUED), "a stale claim must be requeued for a retry"


def test_a_drain_does_not_re_enter_a_peer_s_fresh_claim(store, monkeypatch):
    """⛔ ROUND-5 BLOCKING 2: the window was per BATCH, the claim is per ITEM.

    With one `queued` sibling the batch was resumed and this drain walked EVERY
    non-terminal row — including an `extracting` row another instance was inside
    the runner call on. Two extractions of one document, two graph writes, and
    (once tokens are metered) charged twice.
    """
    from tortoise import batch_ingest as bi

    calls: list[str] = []

    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())

    def _runner(sdk, corpus, org_id):
        def _run(item):
            calls.append(item.item_id)
            return bi.ItemOutcome(bi.BatchItemState.INDEXED, tokens_in=1)
        return _run

    monkeypatch.setattr(ha, "_batch_item_runner", _runner)
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/live.md", "/d/queued.md"])
    claimed, queued = items[0], items[1]
    # A PEER is inside the runner call on item 0, right now.
    coord.advance(ORG_ID, claimed, bi.BatchItemState.EXTRACTING, attempt=True)

    ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert calls == [queued.item_id], (
        f"the drain ran {calls} — a peer's fresh claim must never be re-entered")
    rows = {i.item_id: i for i in store.items(ORG_ID, batch.batch_id)}
    assert rows[claimed.item_id].state is bi.BatchItemState.EXTRACTING, (
        "the peer's claim must be left exactly as the peer left it")
    assert rows[claimed.item_id].attempts == 1, (
        "and must not be counted as one of OUR attempts")
    assert rows[queued.item_id].state is bi.BatchItemState.INDEXED


def test_a_peer_s_fresh_claim_is_not_metered_by_this_process(store, monkeypatch):
    """⛔ ROUND-6 HIGH: the meter was keyed on the RETURNED row.

    `run_item` returns a fresh claim untouched when a peer may hold it, and that
    row can carry the peer's interim tokens (a staged runner's `embedded` write is
    a legal non-terminal write). Metering the return value charged 500 tokens per
    tick — five passes over a 900 s window — for work this process never ran. A
    non-terminal return means "I did nothing here", which owes the ledger nothing.
    """
    from tortoise import batch_ingest as bi

    charged: list[int] = []
    monkeypatch.setattr(bi, "record_item_spend",
                        lambda org_id, item: charged.append(int(item.tokens_in)))
    monkeypatch.setattr(ha, "_batch_store_for", lambda org: store)
    monkeypatch.setattr(ha, "_org_sdk_for_credential", lambda org: object())
    monkeypatch.setattr(ha, "_batch_item_runner",
                        lambda sdk, corpus, org_id: (
                            lambda item: bi.ItemOutcome(bi.BatchItemState.INDEXED,
                                                        tokens_in=1)))

    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/d/live.md"])
    # A PEER is mid-item and has already written interim tokens.
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING,
                  tokens_in=500, reason="interim spend by the peer")

    for _ in range(5):
        ha._drain_batch({"org_id": ORG_ID}, batch.batch_id)

    assert charged == [], (
        f"this process charged {charged} tokens for work a peer ran — the meter "
        "must fire only for items THIS call settled")


def test_an_item_that_names_a_url_is_refused_at_acceptance(client):
    """⛔ ROUND-5 REVIEW P2: the URL refusal was a claim with no code.

    The docstring promised a fetch, then promised a refusal; neither existed.
    `ref` was copied verbatim into `submit`, so `https://…` became a relative path
    under the org root — ACCEPTED with 201, then dead inside the indexer with a
    storage message the customer could not act on. A refusal has to be an HTTP
    answer at acceptance, and it must name the reason.
    """
    r = client.post("/v1/batches",
                    json={"items": [{"ref": "https://evil.example/x.md"}]})
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert detail["error_code"] == "BATCH_REF_URL_NOT_SUPPORTED"
    assert detail["refs"] == ["https://evil.example/x.md"]
    assert "does not fetch URLs" in detail["message"]
    assert "nothing was charged" in detail["message"]

    # A relative PATH is still accepted — the refusal is about the scheme, not
    # about "looks unusual".
    ok = client.post("/v1/batches", json={"items": [{"ref": "contracts/a.md"}]})
    assert ok.status_code == 201, ok.text


def test_a_settle_racing_a_cancel_never_overwrites_the_cancellation(store,
                                                                   monkeypatch):
    """⛔ ROUND-5 REVIEW P2: the BATCH row had no compare-and-set.

    `settle()` reads the batch, derives a state from the items, then writes — so a
    `cancel()` landing in that window was overwritten by `partial`, which is
    TERMINAL: `pending_batches()` no longer selected the row, so nothing ever
    repaired it and the customer's `cancelled_at`/`cancel_reason` were erased on a
    billing-visible surface, permanently.
    """
    coord = bi.BatchCoordinator(store)
    batch, items = coord.submit(ORG_ID, ["/done.md", "/todo.md"])
    coord.advance(ORG_ID, items[0], bi.BatchItemState.EXTRACTING, attempt=True)
    coord.advance(ORG_ID, items[0], bi.BatchItemState.INDEXED, tokens_in=10)

    real_items = store.items
    state = {"cancel": True}

    def items_then_cancel(org_id, batch_id):
        rows = real_items(org_id, batch_id)
        if state["cancel"]:
            state["cancel"] = False
            # The customer cancels EXACTLY between settle's row read and its
            # derived write — the window the row-level CAS closes.
            bi.BatchCoordinator(store).cancel(org_id, batch_id,
                                              reason="cancelled by the customer")
            return real_items(org_id, batch_id)
        return rows

    monkeypatch.setattr(store, "items", items_then_cancel)
    settled = coord.settle(ORG_ID, batch.batch_id)
    monkeypatch.undo()

    row = store.get_batch(ORG_ID, batch.batch_id)
    assert row.state is bi.BatchState.CANCELLED, (
        "a derived terminal state overwrote a customer cancel — the batch now "
        f"reads {row.state} with cancelled_at={row.cancelled_at!r}")
    assert row.cancelled_at, "the cancellation timestamp was erased"
    assert settled.state is bi.BatchState.CANCELLED


def test_the_413_detail_names_the_configured_cap_not_the_default(monkeypatch):
    """⛔ ROUND-5 REVIEW P3: the refusal lied about the number it enforced.

    The cap is env-overridable, but the detail was a static "(4 MiB)" — so an org
    running a 1 MiB cap was told its 2 MiB body exceeded 4 MiB, and an 8 MiB cap
    was understated. The message must name the RESOLVED bound.
    """
    from tortoise import batch_ingest as bi_mod

    assert "4 MiB" in bi_mod.inline_413_detail(4 * 1024 * 1024)
    assert "1 MiB" in bi_mod.inline_413_detail(1024 * 1024)
    assert "8 MiB" in bi_mod.inline_413_detail(8 * 1024 * 1024)
    assert "1024 bytes" in bi_mod.inline_413_detail(1024)

    monkeypatch.setenv("TORTOISE_BATCH_MAX_INLINE_BYTES", str(1024 * 1024))
    assert ha._batch_cap_bytes() == 1024 * 1024, (
        "the cap the middleware registers must follow the env, or the refusal "
        "names a bound it does not enforce")


def test_the_acceptance_copy_says_one_hour_not_1_hours(client):
    """⛔ ROUND-5 REVIEW P3: the string a single-document customer actually gets."""
    body = client.post("/v1/batches", json={"items": [{"ref": "/d/a.md"}]}).json()
    message = body.get("message") or body.get("copy") or ""
    assert "in 1 hour," in message, body
    assert "1 hours" not in message, body
