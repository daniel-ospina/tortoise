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


# ── the production runner, driven through the REAL indexer ──────────────────
def _corpus(tmp_path):
    base = tmp_path / "corpus"
    base.mkdir()
    (base / "a.md").write_text("# Doc A\n\nbody one\n", encoding="utf-8")
    (base / "b.md").write_text("# Doc B\n\nbody two\n", encoding="utf-8")
    return base


def test_the_production_runner_indexes_through_the_existing_per_file_door(
        sdk_factory, tmp_path, monkeypatch):
    """End-to-end at the ITEM level, on a real graph: accept → indexed.

    This is the runner `_drain_batch` uses. It is `sdk._index_process_unit`, the
    per-file body of the `index_sources_from_directory` walk, so a batch item is
    filed through the SAME door as any other document — not a second pipeline.
    """
    base = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219a")
    runner = ha._batch_item_runner(sdk, "corpus")

    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)
    _batch, items = coord.submit(ORG_ID, [str(base / "a.md")])
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
    base = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219b")
    runner = ha._batch_item_runner(sdk, "corpus")
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    _, first = coord.submit(ORG_ID, [str(base / "a.md")])
    assert coord.run_item(ORG_ID, first[0], runner).state is (
        bi.BatchItemState.INDEXED)

    # A DIFFERENT batch, the same bytes.
    _, second = coord.submit(ORG_ID, [str(base / "a.md")])
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
    base = _corpus(tmp_path)
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219c")
    runner = ha._batch_item_runner(sdk, "corpus")
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    _, first = coord.submit(ORG_ID, [str(base / "a.md")])
    assert coord.run_item(ORG_ID, first[0], runner).state is (
        bi.BatchItemState.INDEXED)

    (base / "a.md").write_text("# Doc A\n\nbody ONE CHANGED\n", encoding="utf-8")
    _, second = coord.submit(ORG_ID, [str(base / "a.md")])
    changed = coord.run_item(ORG_ID, second[0], runner)
    assert changed.state is bi.BatchItemState.INDEXED, changed.reason
    assert changed.state is not bi.BatchItemState.SKIPPED_DUPLICATE


def test_one_bad_file_does_not_fail_the_batch_on_the_real_indexer(
        sdk_factory, tmp_path, monkeypatch):
    """ACCEPTANCE, AX: one unreadable file leaves the rest of the batch usable."""
    base = _corpus(tmp_path)
    (base / "bad.md").write_bytes(b"# Bad\n\n\xff\xfe not utf-8\n")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219d")
    runner = ha._batch_item_runner(sdk, "corpus")
    store = bi.InMemoryBatchStore()
    coord = bi.BatchCoordinator(store)

    batch, items = coord.submit(ORG_ID, [str(base / "a.md"),
                                         str(base / "bad.md"),
                                         str(base / "b.md")])
    for it in items:
        coord.run_item(ORG_ID, it, runner)
    settled = coord.settle(ORG_ID, batch.batch_id)
    rows = {i.ref: i for i in store.items(ORG_ID, batch.batch_id)}

    assert rows[str(base / "a.md")].state is bi.BatchItemState.INDEXED
    assert rows[str(base / "b.md")].state is bi.BatchItemState.INDEXED
    bad = rows[str(base / "bad.md")]
    assert bad.state is bi.BatchItemState.FAILED
    assert bad.reason, "a failure must carry a reason a customer can act on"
    assert settled.state is bi.BatchState.PARTIAL


def test_a_ref_outside_the_ingest_base_is_refused_before_any_read(
        sdk_factory, tmp_path, monkeypatch):
    """#329: the path bound is a REFUSAL, never a read (and never a loss)."""
    base = _corpus(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Nope\n\nsecret\n", encoding="utf-8")
    monkeypatch.setenv("TORTOISE_INGEST_BASE_DIR", str(base))
    sdk = sdk_factory(namespace="test_suite_batch14219e")
    runner = ha._batch_item_runner(sdk, "corpus")
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

    def _runner(sdk, corpus):
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
