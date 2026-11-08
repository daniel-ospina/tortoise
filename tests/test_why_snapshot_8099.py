"""#8099 — precomputed why-blocks on the dream cycle, served to the push path.

The phase-2 second half of #2081's indicator 2 (epic #2080 W4). The delivered
delivery layer pushes the raw durable ``:GraphEvent`` payload and calls no EP
function; this file pins the ENRICHMENT half:

* the dream cycle assembles the canonical §3.1.4 why-block for the points a
  pass touched (plus the points a pending event references) and persists it
  (``:WhySnapshot``) — ``why_snapshot.precompute*``;
* a push carries those blocks as a top-level ``why`` list whose entries are
  byte-equal to the ones ``POST /v1/context`` / ``volunteer_context()`` return
  for the same point (indicator 1), and the push path performs ZERO assembly
  and ZERO EP work (indicator 2) — it does one bounded single-node read;
* the numbers are POST-dream: the precompute runs after the EP pass, inside the
  same dream-pool item (indicator 3);
* a graph with no subscription, a killed switch, or a snapshot read failure
  degrades to the #2081 envelope byte-for-byte — the key is ABSENT, not empty.

Doctrine (TEST-DOCTRINE Class B): each test names the failing value it is
designed to catch in its own docstring.

EMBEDDED-ONLY (module-level ``pytest.mark.embedded_only``). Every test builds a
PATH-built store (``TortoiseSDK(<tmpdir>/test.db)``) and asserts on that store's
graph — ``_fresh_sdk`` even wipes it (``MATCH (n) DETACH DELETE n``). On a
URI-set lane the construction is REDIRECTED to the shared server (the
``FalkorProjection.__init__`` redirect block; an explicit ``db_path`` alone does
NOT win — the finding ``test_9209_handle_non_cypher_wipe`` records), so the wipe
would hit a shared graph and the assertions would grade a store this file never
seeded. Registered in all three homes — ``config/ci-surfaces.yml``'s
``carve_out:``, the surface registrations (``core`` + ``api``), and
``tests._embedded.TEST_NO_REDIRECT_STEMS`` — because a stem in only one of the
two registries is a silent hole (`test_ci_selection.py::
test_carve_out_mirrors_test_no_redirect_stems` and `tests/test_markers.py::
test_module_level_embedded_only_modules_are_carve_out`).

Seeding mirrors ``tests/test_w4_why_enrichment.py`` / ``test_volunteer_pipeline``
(persisted posterior α/β written the way ``compute_confidence`` writes them);
the delivery harness mirrors ``tests/test_webhook_delivery.py``.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

pytest.importorskip("cryptography")  # the subscription secret's at-rest encryption

#: embedded-only: see the module docstring's EMBEDDED-ONLY note — a URI-set lane
#: would redirect every path-built store below to the shared server, including
#: ``_fresh_sdk``'s wipe.
pytestmark = pytest.mark.embedded_only

from tortoise import webhook_delivery as wd  # noqa: E402
from tortoise import why_snapshot as ws  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.volunteer import why_entry  # noqa: E402
from tortoise.why import assemble_why_blocks  # noqa: E402


@pytest.fixture(autouse=True)
def _encryption_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _switches(monkeypatch):
    """Egress ON, precompute ON, W4 enrichment ON — the tests override each."""
    monkeypatch.delenv(wd.KILL_SWITCH_ENV, raising=False)
    monkeypatch.delenv(ws.SNAPSHOT_ENV, raising=False)


class FakeTransport:
    """Records every POST; replays a scripted status per call (default 200)."""

    calls: list[tuple[str, bytes, dict]]

    def __init__(self, script=()):
        self.script = list(script)
        self.calls = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if isinstance(item, Exception):
            raise item
        return item, None


def _fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_ws8099_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def _set_posterior(sdk, pid: str, alpha: float, beta: float):
    mean = round(alpha / (alpha + beta), 4) if (alpha + beta) > 0 else 0.5
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence = $c, "
        "n.posterior_alpha = $a, n.posterior_beta = $b",
        params={"id": pid, "a": alpha, "b": beta, "c": mean},
    )


def _plant_claim(sdk, topic: str, *, alpha: float = 12.0,
                 beta: float = 2.0) -> dict:
    """One MEASURED claim with an IMPL support (persisted α/β, not propagated)."""
    ev = sdk.create_point("evidence", f"{topic} supporting record alpha")
    claim = sdk.create_point("statement", f"{topic} belief statement beta")
    sdk.create_operator("IMPL", ev["id"], [claim["id"]])
    _set_posterior(sdk, claim["id"], alpha, beta)
    _set_posterior(sdk, ev["id"], 12.0, 1.0)
    return {"claim": claim["id"], "support": ev["id"]}


def _plant_contested(sdk, topic: str) -> dict:
    """A contested-but-believed claim (variance > threshold) + a NAND counter."""
    planted = _plant_claim(sdk, topic, alpha=2.0, beta=0.7)
    counter = sdk.create_point("statement", f"{topic} counterargument gamma")
    sdk.create_operator("NAND", counter["id"], [planted["claim"]])
    _set_posterior(sdk, counter["id"], 10.0, 1.0)
    return {**planted, "counter": counter["id"]}


def _subscribe(sdk):
    return wd.create_subscription(sdk._get_proj(),
                                  url="https://hooks.example.com/tortoise")


def _snapshot_count(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (w:WhySnapshot) RETURN count(w)").result_set
    return int(rows[0][0]) if rows else 0


def _bodies(transport) -> list[dict]:
    return [json.loads(body) for _url, body, _headers in transport.calls]


# ── the precompute ────────────────────────────────────────────────────────

def test_snapshot_block_is_the_pull_surface_block():
    """FAILING VALUE: a snapshot whose block differs from what the pull surface
    would compute for the same point at the same instant — i.e. the push and
    ``/v1/context`` disagree about the evidence behind one belief.

    Both sides come from the SAME assembly (``why.assemble_why_blocks``); this
    pins that the persisted copy is that output, not a re-derivation and not a
    projection that lost a dimension.
    """
    sdk = _fresh_sdk()
    planted = _plant_contested(sdk, "Vendor risk review was deferred")
    proj = sdk._get_proj()
    pid = planted["claim"]

    summary = ws.precompute(proj, [pid])
    assert summary["written"] == 1 and summary["reason"] is None

    stored = ws.read_blocks(proj, [pid])[pid]
    fresh = assemble_why_blocks(proj, [pid])[pid]
    assert stored == fresh
    # The dimensions the issue names explicitly are all carried.
    assert stored["support_chain"] and stored["ep"]["has_ep"] is True
    assert stored["conflicts"]["contested"] is True
    assert stored["supersession"]["status"] == "live"
    # ... and the wire projection is the ONE the pull surface uses.
    assert ws.read_why_entries(proj, [pid])[pid] == why_entry(fresh)


def test_pushed_why_equals_the_context_endpoint_why():
    """Indicator 1. FAILING VALUE: a pushed ``why`` entry that is not the entry
    ``POST /v1/context`` returns for the same point — two shapes of one claim.

    The comparison is against a REAL ``volunteer_context`` call (the in-process
    half of ``/v1/context``'s canonical pipeline), not against a second call to
    the assembly: if either surface ever grows its own projection, this fails.
    """
    sdk = _fresh_sdk()
    planted = _plant_contested(sdk, "Acme security review was due May first")
    proj = sdk._get_proj()
    ws.precompute(proj, [planted["claim"]])
    _subscribe(sdk)

    window = [
        {"role": "user", "content": "What's the status of the Acme security "
                                    "review?"},
        {"role": "user", "content": "Has it shipped?"},
    ]
    pulled = sdk.volunteer_context(window, session_id="sess_ws8099", why=True)
    assert pulled["degraded_reason"] is None
    pulled_entries = {w["point_id"]: w for w in pulled["why"]}
    assert planted["claim"] in pulled_entries, pulled["pointers"]

    transport = FakeTransport()
    assert wd.dispatch_pending(proj, transport=transport)["delivered"] >= 1
    pushed_entries = {}
    for body in _bodies(transport):
        for entry in body.get("why", []):
            pushed_entries[entry["point_id"]] = entry
    assert pushed_entries, "no event carried a why block"
    assert pushed_entries[planted["claim"]] == pulled_entries[planted["claim"]]


def test_push_path_assembles_nothing_and_computes_no_beliefs(monkeypatch):
    """Indicator 2. FAILING VALUE: any ``assemble_why_blocks`` or
    ``compute_confidence`` call on the push path — the push would then cost a
    fixed batch of graph reads per event and would re-run EP for a subscriber.

    Both entry points are booby-trapped, so the run also proves the ``why``
    actually came from the SNAPSHOT (the booby trap is not merely unvisited).
    """
    import tortoise.ep as ep_mod
    import tortoise.why_snapshot as ws_mod

    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Ledger reconciliation closed on time")
    proj = sdk._get_proj()
    ws.precompute(proj, [planted["claim"]])
    _subscribe(sdk)

    def _boom_assembly(*a, **k):
        raise AssertionError("the push path assembled why-blocks")

    def _boom_ep(*a, **k):
        raise AssertionError("the push path called the EP engine")

    # ⛔ BOTH HOMES (#8099 review F3). The alias only intercepts callers that
    # resolve through ``why_snapshot``'s namespace (i.e. ``precompute``, already
    # run). The mutation this test exists to catch is a NEW
    # ``from .why import assemble_why_blocks`` inside ``webhook_delivery`` —
    # `_page_why_entries` already imports its sibling that way two lines up —
    # and that resolves through ``tortoise.why`` at CALL time, so trapping the
    # defining module is the half that can actually red for it.
    monkeypatch.setattr("tortoise.why.assemble_why_blocks", _boom_assembly)
    monkeypatch.setattr(ws_mod, "assemble_why_blocks", _boom_assembly)
    monkeypatch.setattr(ep_mod.TortoiseEP, "compute_confidence", _boom_ep)
    transport = FakeTransport()
    summary = wd.dispatch_pending(proj, transport=transport)
    assert summary["delivered"] >= 1
    assert any(body.get("why") for body in _bodies(transport)), (
        "the booby traps could be unvisited because no why was pushed at all")


# ── the envelope ──────────────────────────────────────────────────────────

def test_envelope_is_byte_identical_when_no_snapshot_exists():
    """Contract 2/3. FAILING VALUE: an envelope that grows a ``why: []`` (or any
    other key) when nothing was precomputed — an empty list is a claim
    ("computed, nothing to say"), an absent key is the pre-#8099 envelope a
    subscriber's parser already handles.
    """
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Quarterly close ran two days late")
    proj = sdk._get_proj()
    _subscribe(sdk)                      # no precompute pass at all
    assert _snapshot_count(sdk) == 0

    transport = FakeTransport()
    assert wd.dispatch_pending(proj, transport=transport)["delivered"] >= 1
    bodies = _bodies(transport)
    assert bodies
    for body in bodies:
        assert set(body) == {"subscription_id", "delivery_id", "event"}, body
        assert set(body["event"]) == {"event_id", "seq", "type", "ts", "payload"}
    # The point still exists and is event-addressed — the absence is the
    # precompute's, not the payload's.
    assert any(
        planted["claim"] in json.dumps(body["event"]["payload"]) for body in bodies
    ), bodies


def test_snapshot_read_failure_degrades_to_no_why_but_still_delivers():
    """Contract 3. FAILING VALUE: a why read fault that fails (or re-delivers)
    a push — enrichment must never be able to cost a delivery.
    """
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Onboarding checklist was restructured")
    proj = sdk._get_proj()
    ws.precompute(proj, [planted["claim"]])
    sub = _subscribe(sdk)

    def _boom(proj_, ids):
        raise RuntimeError("snapshot read exploded")

    import tortoise.why_snapshot as ws_mod

    original = ws_mod.read_blocks
    ws_mod.read_blocks = _boom
    try:
        transport = FakeTransport()
        summary = wd.dispatch_pending(proj, transport=transport)
    finally:
        ws_mod.read_blocks = original
    assert summary["delivered"] >= 1 and summary["failed"] == 0
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] > 0
    assert all("why" not in body for body in _bodies(transport))


# ── the dream-cycle seam (indicator 3) ────────────────────────────────────

def test_no_subscribers_means_no_snapshot_write():
    """Contract 2. FAILING VALUE: a dream on a tenant with no subscriber
    changing the graph — every pre-#8099 deployment would pay a write per
    affected claim per dream for a feature it does not use.
    """
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Renewal notice went out early")
    proj = sdk._get_proj()

    def _counts():
        rows = proj.g.query(
            "MATCH (n) WHERE NOT n:GraphEventMeta RETURN count(n)").result_set
        return int(rows[0][0])

    before = _counts()
    summary = ws.precompute_for_dream(
        proj, {"affected_claims": [planted["claim"]]})
    assert summary["reason"] == "no_subscribers"
    assert summary["written"] == 0
    assert _counts() == before
    assert _snapshot_count(sdk) == 0


def test_kill_switch_disables_the_precompute():
    """FAILING VALUE: a falsy ``TORTOISE_WHY_SNAPSHOT`` that still writes —
    an operator's switch that only renamed the feature. The DELIVERY layer must
    keep working without why-blocks (they are enrichment, not the contract)."""
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Audit finding was accepted as a risk")
    proj = sdk._get_proj()
    _subscribe(sdk)
    os.environ[ws.SNAPSHOT_ENV] = "0"
    try:
        summary = ws.precompute_for_dream(
            proj, {"affected_claims": [planted["claim"]]})
        assert summary["reason"] == "disabled" and summary["written"] == 0
        assert _snapshot_count(sdk) == 0
        assert ws.precompute(proj, [planted["claim"]])["written"] == 0
        assert _snapshot_count(sdk) == 0
        transport = FakeTransport()
        assert wd.dispatch_pending(proj, transport=transport)["delivered"] >= 1
        assert all("why" not in body for body in _bodies(transport))
    finally:
        os.environ.pop(ws.SNAPSHOT_ENV, None)


def test_deleted_point_snapshot_is_dropped_so_a_retraction_carries_no_why():
    """FAILING VALUE: a ``PointRetracted`` push carrying a why-block that says
    the point is live with confidence 0.7 — the why would state the opposite of
    the event, and it is the one case where a stale snapshot is actively wrong
    rather than merely old.
    """
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Deprecated migration path was retired")
    proj = sdk._get_proj()
    _subscribe(sdk)
    pid = planted["claim"]
    ws.precompute(proj, [pid])
    assert _snapshot_count(sdk) == 1

    sdk.delete_point(pid)
    # The next dream pass asks for the same id and finds no block → drop.
    summary = ws.precompute(proj, [pid])
    assert summary["dropped"] == 1
    assert _snapshot_count(sdk) == 0

    transport = FakeTransport()
    wd.dispatch_pending(proj, transport=transport)
    retractions = [b for b in _bodies(transport)
                   if b["event"]["type"] == "PointRetracted"]
    assert retractions, "no PointRetracted event was pushed"
    for body in retractions:
        assert "why" not in body, body


def test_dream_pass_precomputes_and_the_push_carries_post_dream_numbers():
    """Indicators 2+3. FAILING VALUE: a snapshot assembled BEFORE the EP pass
    (so the push serves the pre-dream belief), or a precompute that never runs
    from the dream worker at all.

    Drives the REAL ``_dream_worker`` drain: mark → dream → precompute, then the
    real dispatcher. The fake SDK's ``dream`` moves the persisted posterior, so
    the confidence the subscriber receives is the only witness of the ordering.
    """
    import tortoise.hosted_api as ha

    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Pilot cohort renewal cleared review",
                           alpha=12.0, beta=2.0)
    proj = sdk._get_proj()
    pid = planted["claim"]
    _subscribe(sdk)
    # Pre-#8099 state: NO snapshot. The pass below must create it.
    assert _snapshot_count(sdk) == 0

    post_dream_mean = 0.6

    class _DreamSDK:
        """The worker's SDK: the real graph, a dream that MOVES the belief."""

        def _get_proj(self):
            return proj

        def _mark_dirty(self, roots):
            self.marked = list(roots)

        def dream(self, **kwargs):
            _set_posterior(sdk, pid, 3.0, 2.0)      # mean = 0.6
            return {"mode": "local", "affected_claims": [pid],
                    "budget_used": 1, "coverage": 1.0, "converged": True}

        def close(self):
            pass

    fake = _DreamSDK()
    transport = FakeTransport()

    async def _run():
        key = ha._dream_key("org-ws8099", None)
        debounce = ha._DREAM_DEBOUNCE_S
        ha._DREAM_DEBOUNCE_S = 0.0
        ha._DREAM_QUEUES.pop(key, None)
        ha._DREAM_TASKS.pop(key, None)
        orig_make, orig_pool = ha._make_sdk, ha._run_dream_on_pool
        orig_transport = wd.WebhookTransport

        def _make_sdk(**kwargs):
            return fake

        async def _run_on_pool(fn, sdk_factory, /, *args, **kwargs):
            # Skip the dedicated pool/thread: the pass is synchronous either way,
            # and the worker's ORDERING (dream → precompute → dispatch) is what
            # this test is about.
            return fn(sdk_factory(), *args, **kwargs)

        def _fake_transport(*a, **k):
            return transport

        ha._make_sdk, ha._run_dream_on_pool = _make_sdk, _run_on_pool
        wd.WebhookTransport = _fake_transport
        try:
            ha._DREAM_QUEUES.setdefault(key, asyncio.Queue()).put_nowait(pid)
            await ha._dream_worker("org-ws8099", key)
        finally:
            ha._DREAM_DEBOUNCE_S = debounce
            ha._make_sdk, ha._run_dream_on_pool = orig_make, orig_pool
            wd.WebhookTransport = orig_transport
            ha._DREAM_QUEUES.pop(key, None)
            ha._DREAM_TASKS.pop(key, None)

    asyncio.run(_run())

    assert _snapshot_count(sdk) >= 1, (
        "the dream drain did not precompute — the push below would carry no why")
    assert ws.read_blocks(proj, [pid]), (
        "the dreamed point has no snapshot — the precompute ran for some other "
        "point set")
    entries = [e for body in _bodies(transport) for e in body.get("why", [])]
    assert entries, "the push carried no why block"
    claim_entries = [e for e in entries if e["point_id"] == pid]
    assert claim_entries, f"no why entry for the dreamed point: {entries}"
    assert claim_entries[0]["ep"]["confidence_mean"] == pytest.approx(
        post_dream_mean, abs=1e-4), (
        "the pushed why carries the PRE-dream belief — the precompute ran "
        "before the EP pass (or not at all)")


def test_stale_snapshots_are_purged_but_fresh_ones_survive():
    """FAILING VALUE: a snapshot older than the retention window surviving a
    pass — an orphan whose point no pass will ever assemble again accumulates
    forever on a graph whose points churn."""
    from datetime import UTC, datetime, timedelta

    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Handler rotation completed without incident")
    proj = sdk._get_proj()
    old = datetime.now(UTC) - timedelta(days=ws.RETENTION_DAYS + 1)
    ws.precompute(proj, [planted["claim"]], now=old, purge=False)
    assert _snapshot_count(sdk) == 1
    assert ws.purge_stale(proj) == 1          # older than the window → reaped
    assert _snapshot_count(sdk) == 0

    ws.precompute(proj, [planted["claim"]], purge=False)
    assert ws.purge_stale(proj) == 0          # fresh → survives
    assert _snapshot_count(sdk) == 1


# ── §17.1: the write lane (review F1) ─────────────────────────────────────

def _journal_backed_sdk():
    """A journal-bound SDK — the configuration the hosted lane actually runs.

    `_fresh_sdk` builds a journal-LESS store, where §17.1's handle guard never
    registers the graph and therefore cannot see an undeclared writer at all.
    That is why the census case below needs its own constructor: a suite that
    only ever runs the unguarded shape cannot prove a lane is declared.
    """
    import tempfile as _tf

    base = _tf.mkdtemp(prefix="tortoise_ws8099_j_")
    db_path = os.path.join(base, "test.db")
    return TortoiseSDK(db_path,
                       event_log_path=os.path.join(os.path.dirname(db_path),
                                                   "journal.jsonl"))


def test_the_snapshot_writes_declare_their_lane():
    """FAILING VALUE (§17.1, #8099 review F1): a `:WhySnapshot` writer that
    reaches the guarded write handle with NO declared lane — the census that
    #9124 part 2 exists to drive to zero gains a permanent undeclared entry, and
    the moment the guard is ARMED the whole precompute dies silently.

    MEASURED at the un-declared head on a journal-bound store: three census
    entries (`CREATE INDEX …`, the `UNWIND … MERGE`, the retention `DELETE`).
    """
    import tortoise.projection as P

    assert ws.WHY_SNAPSHOT_WRITE_LANE == "why.snapshot-cache", (
        "the lane name is read back at the write handle by this pin — renaming "
        "it silently drops the declaration")
    sdk = _journal_backed_sdk()
    try:
        planted = _plant_claim(sdk, "Lane declaration is not optional")
        proj = sdk._get_proj()
        _subscribe(sdk)                       # so the precompute is not skipped
        census_before = dict(P.undeclared_handle_writes())
        summary = ws.precompute_for_dream(
            proj, {"affected_claims": [planted["claim"]]})
        assert summary["written"] >= 1, summary   # the pass actually wrote
        census_after = dict(P.undeclared_handle_writes())
        new = {k: v for k, v in census_after.items() if k not in census_before}
        assert new == {}, f"undeclared §17.1 handle writes: {new}"
        # …and the write is still readable, i.e. the declaration did not turn
        # into a no-op that silently skipped the store.
        assert ws.read_blocks(proj, [planted["claim"]])
    finally:
        sdk.close()


def test_a_section_17_1_refusal_is_not_swallowed(monkeypatch):
    """FAILING VALUE (§17.1, #8099 review F1): a `DirectCacheWriteRefused` folded
    into `reason="write_failed"` — the statement never landed, so reporting a
    best-effort miss makes a dead cache indistinguishable from a transient
    graph hiccup, and the push keeps serving whatever blocks happened to exist.

    This is the rule `webhook_delivery._run` states for its own writes (#9233).
    """
    from tortoise.projection import DirectCacheWriteRefused

    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "A refused write never landed")
    proj = sdk._get_proj()

    def _refuse(*a, **k):
        raise DirectCacheWriteRefused("declared-and-refused (test)")

    monkeypatch.setattr(ws, "_run", _refuse)
    try:
        with pytest.raises(DirectCacheWriteRefused):
            ws.precompute(proj, [planted["claim"]])
    finally:
        monkeypatch.undo()
    # The store was never touched, so nothing is served and nothing is stale.
    assert _snapshot_count(sdk) == 0


# ── the kill switch gates the READ too (review F2) ────────────────────────

def test_kill_switch_stops_serving_a_pre_existing_snapshot():
    """FAILING VALUE (#8099 review F2): a switch that stops the REFRESH but keeps
    serving the snapshots written before it was thrown. The module's own contract
    says a falsy switch means "a push carries no ``why``", and stopping the
    serving of blocks an operator mistrusts is the whole point of the lever.

    The case writes a snapshot FIRST, so it can red for a read-path mutation —
    the disabled-write assertions elsewhere cannot.
    """
    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "Serve nothing while the switch is off")
    proj = sdk._get_proj()
    _subscribe(sdk)
    ws.precompute(proj, [planted["claim"]])
    assert ws.read_blocks(proj, [planted["claim"]]), "precondition: it is served"

    os.environ[ws.SNAPSHOT_ENV] = "0"
    try:
        assert ws.read_blocks(proj, [planted["claim"]]) == {}, (
            "a pre-existing snapshot was still served with the switch off")
        assert ws.read_why_entries(proj, [planted["claim"]]) == {}
        transport = FakeTransport()
        assert wd.dispatch_pending(proj, transport=transport)["delivered"] >= 1
        bodies = _bodies(transport)
        assert bodies
        assert all("why" not in body for body in bodies), (
            "the push carried a precomputed why with the switch off")
    finally:
        os.environ.pop(ws.SNAPSHOT_ENV, None)
    # Turning it back on serves the SAME snapshot again — the switch gates
    # serving, it does not destroy the cache.
    assert ws.read_blocks(proj, [planted["claim"]])


# ── the drop is driven by EXISTENCE, never by a missing block (review F4) ──

@pytest.mark.parametrize("failure", ["empty", "raise"])
def test_a_blockless_assembly_never_drops_a_live_points_snapshot(failure):
    """FAILING VALUE (#8099 review F4): dropping a snapshot because the ASSEMBLY
    produced no block. A transient assembly failure (and a partial one) is also
    blockless, so that mutation deletes good snapshots — the exact harm
    `_vanished` exists to prevent. Only a proven-missing Point may drop one.

    The forbidden implementation (`gone = [pid for pid in ids if pid not in
    blocks]`) passed the deleted-point case, because that case cannot tell the
    two apart; this one can.
    """
    import tortoise.why_snapshot as ws_mod

    sdk = _fresh_sdk()
    planted = _plant_claim(sdk, "A live belief keeps its snapshot")
    proj = sdk._get_proj()
    pid = planted["claim"]
    ws.precompute(proj, [pid])
    assert _snapshot_count(sdk) == 1

    def _no_blocks(*a, **k):
        if failure == "raise":
            raise RuntimeError("assembly blew up (test)")
        return {}

    original = ws_mod.assemble_why_blocks
    ws_mod.assemble_why_blocks = _no_blocks
    try:
        summary = ws.precompute(proj, [pid])
    finally:
        ws_mod.assemble_why_blocks = original

    assert summary["dropped"] == 0, (
        "a blockless assembly dropped a live point's snapshot — the drop must "
        f"be driven by an existence read: {summary}")
    assert _snapshot_count(sdk) == 1, "the good snapshot was deleted"
    assert ws.read_blocks(proj, [pid]), "the surviving snapshot is no longer served"
