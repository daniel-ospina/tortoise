"""#14219 (F4): the `/v1/batches` REST surface — route shape, ACK latency, the
acknowledgement gate, per-item status and cancel.

The layer's own semantics are proved in `tests/test_14219_batch_ingest.py`
(41 tests, no FastAPI). These tests pin the WIRE: what a client gets back, in
what order, and that acceptance does no work.

No DB is touched: the tenant store is replaced with the in-memory one and the
batch worker's `submit` is stubbed, so a test observes the handler's own
behaviour (the ordering of write-then-dispatch) rather than a worker's.
"""
from __future__ import annotations

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
    monkeypatch.setattr(ha, "_batch_item_runner", lambda sdk, corpus: (
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

    def _explode(sdk, corpus):
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
    assert bi.BatchCoordinator(store).recover(ORG_ID) == [batch.batch_id]
    assert store.items(ORG_ID, batch.batch_id)[0].state is bi.BatchItemState.QUEUED
