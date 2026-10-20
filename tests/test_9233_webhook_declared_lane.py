"""#9233 §17.1 — the webhook delivery store **declares** its write lane, so
closing the guarded write handle (#9124) does not silently disable webhooks.

`tortoise/webhook_delivery.py` is a graph-native durable store: `:ContextSubscription`
rows are registrations and `:DeliveryReceipt` rows record outbound HTTP attempts.
It reaches the projection's write handle (`proj.g.query`), and **no** journal record
describes what it writes — the same class as the `:EpMeta` epoch (`#9123`) and the
onboarding metadata (`#9121`). `#9124` closes that handle, so without a declaration
the hosted webhook surface (`hosted_api.create_subscription` →
`_webhook_delivery.create_subscription(sdk._get_proj(), …)`) is refused the moment
the guard is armed.

⛔ WHAT THIS SUITE PINS, AND WHY IT IS NOT VACUOUS. `#9124`'s guard is not on
`main` yet (it lands last, after its declaration children — this issue among
them). So `_instrument` reproduces the third door's *predicate* verbatim —
journal-backed + no declaration in flight + a mutating statement ⇒
`DirectCacheWriteRefused` — and installs it on `_GuardedGraph.query`. That is the
predicate `#9124` pins, so the two cannot disagree about what a declaration is,
and this issue's contract is executable now instead of blocked on a sibling.
`test_the_instrument_refuses_an_undeclared_write` is the control: without it every
"declared" assertion below would also pass on a guard that refuses nothing.

⛔ THE DECLARATION MUST NOT HIDE A REFUSAL. `_run` opens the lane, but the handlers
on the delivery path exist for ORDINARY failures (the index DDL's "already
exists", the delivered receipt, the receipt prune, the dispatcher's cursor and
failure writes) and a broad `except Exception` there would count a refused
statement as a completed write: measured before the fix, a refused cursor write
returned `{'delivered': 1, …}` with `cursor == 0`, re-POSTing the same event on
every pass forever (and `max_attempts` can never trip, because the success path
never calls `_mark_failed`). Every one of those handlers re-raises a refusal
ahead of its broad clause, and `test_every_ordinary_failure_handler_on_the_write_path_re_raises_refusals`
+ `test_every_dispatch_write_is_refusal_armed` keep the set closed.

⛔ THE DECLARATION IS NOT A DURABILITY UPGRADE, and this suite says so rather
than implying it: `test_a_rebuild_does_not_restore_the_store` runs a REAL
`rebuild_all` and pins that subscriptions are gone afterwards. Recovery for this
class is re-registration, exactly as before — the declaration says *where the
write lives*, not that the write is now replayable.

MUTATION for the declaration tests: drop the `_cache_write_authorized` wrapper in
`webhook_delivery._run`. `test_every_webhook_write_declares_its_lane` fails with
the list of undeclared statements, and `test_the_armed_guard_does_not_refuse_the_product`
fails inside the write itself.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tests._declared_lane_probe import (
    LaneProbe,
    assert_no_foreign_undeclared,
)
from tortoise.projection import (
    DirectCacheWriteRefused,
    _statement_writes,
)

MODULE = Path(__file__).resolve().parent.parent / "tortoise" / "webhook_delivery.py"

#: The classes this lane declares. A label outside this set means the lane is
#: wider than the declaration claims — the class check, not the (deliberately
#: conservative) statement classifier, is what keeps it narrow.
_DECLARED_LABELS = frozenset({"ContextSubscription", "DeliveryReceipt"})

#: Every function of the module that issues a statement through the funnel
#: (`_run`), and how this suite covers it. `_EXERCISED_*` are called by the two
#: walks below; `_STATED_OTHERS` are reached from an exercised function, each
#: with the reason it needs no direct call.
_EXERCISED_WRITERS = frozenset({
    "ensure_subscription_schema", "create_subscription", "delete_subscription",
})
_EXERCISED_READERS = frozenset({
    "get_subscription", "list_subscriptions", "count_subscriptions",
    "read_receipts",
})
_STATED_OTHERS = {
    "_write_receipt": "reached from _mark_delivered/_mark_failed, both exercised",
    "_mark_delivered": "reached from dispatch_pending, exercised on a 200",
    "_mark_failed": "reached from dispatch_pending, exercised on a 500",
    "_due_subscriptions": "reached from dispatch_pending before every delivery",
}

#: Functions that query a RAW graph handle (`graph`, not `proj.g`) — the sweep's
#: injected handle, which may be `db.select_graph(<name>)` and therefore carries
#: no §17.1 door at all. All three are READS; the pin is that they stay reads.
_RAW_HANDLE_READERS = frozenset({"event_head_seq", "due_subscription_count",
                                 "_events_head"})

#: Every function that REFERENCES the projection's handle (`proj.g` / `proj.db`)
#: in any role — receiver of a call, or an argument handed to a helper (which is
#: how `event_head_seq(proj.g)` reaches it). Pinned so a new handle-touching site
#: is a decision here rather than an invisible second writer.
_HANDLE_TOUCHING_FUNCTIONS = frozenset({
    "_run",                      # the funnel itself (2 call sites)
    "_due_subscriptions",        # head read + the due query
    "dispatch_pending",          # reads the head before dispatching
})


@pytest.fixture
def journal_path(tmp_path) -> Path:
    return tmp_path / "journal.jsonl"


@pytest.fixture
def sdk_with_journal(tmp_path, monkeypatch, journal_path):
    """A real SDK with a configured journal, so §17.1's guard is ARMED.

    The same shape `tests/test_9079_onboarding_declared_lane.py` uses: an
    explicit `db_path` keeps the fixture embedded when no URI is configured, and
    `event_log_path` is what arms the guard.
    """
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("TORTOISE_WEBHOOK_DISPATCH", raising=False)
    sdk = TortoiseSDK(str(tmp_path / "webhook9233.db"),
                      event_log_path=str(journal_path))
    try:
        yield sdk
    finally:
        sdk.close()


class _ScriptedTransport:
    """The transport seam `dispatch_pending` takes: one scripted reply per POST."""

    def __init__(self, script=()):
        self.script = list(script)
        self.calls: list[tuple[str, bytes, dict]] = []

    def post(self, url, body, headers):
        self.calls.append((url, body, headers))
        item = self.script.pop(0) if self.script else 200
        if item == "err":
            return None, "connection refused"
        return item, None


def _instrument(monkeypatch, *, refuse: bool | str, owner) -> LaneProbe:
    """Install #9124's predicate on `_GuardedGraph.query`, ATTRIBUTED to `owner`.
    
    The recorder is `tests/_declared_lane_probe.LaneProbe` (#9256): it records
    `_CACHE_WRITE_AUTHORIZED` AT THE WRITE — read inside the handle dispatch,
    which is where #9124's guard will read it, so "the write ran inside a
    declaration" is observed, not inferred from source — together with the
    identity of the PROJECTION whose handle issued the statement.

    ⛔ WHY IDENTITY (#9256). This is the suite the defect was FOUND in: the patch
    is on the CLASS (the handle is `__slots__`-restricted and read-only), so the
    recorder sees every projection in the process, and a peer projection's
    `_ensure_indexes` burst was attributed to the delivery store's writers. The
    attribution assertions read `probe.own()`; `probe.foreign()` is what keeps
    that filter from being a blindfold.

    `refuse` selects which mutating statements the guard REFUSES:
    ``False`` (refuse nothing — the recording mode), ``True`` (refuse every
    UNDECLARED write — #9124 armed), or a substring (refuse that statement
    whether or not a declaration is in flight — used to aim a refusal at ONE
    handler and prove the caller does not swallow it; the swallow is what makes a
    missing declaration silent, so the refusal must be simulable independent of
    the declaration being present). The refusal is never scoped to `owner`: a
    foreign handle's undeclared write is a real §17.1 violation and is refused.
    """
    return LaneProbe(owner).install(monkeypatch, refuse=refuse)


def _seed_stream(proj, n: int = 2) -> list[int]:
    """Seed ``n`` durable ``:GraphEvent`` rows — the stream dispatch reads.

    Written BEFORE the instrument is installed, through `event_store` (whose
    declaration is `EventAPI._emit`'s, not this lane's). It is the stream this
    module READS; writing it is not under test here.
    """
    from tortoise import event_store

    seqs = []
    for i in range(n):
        seq = event_store.next_seq(proj)
        event_store.append_event(
            proj, seq, "PointAdded",
            {"id": f"p-{seq}", "seq": seq, "payload": {"i": i}},
            event_id=f"evt-{seq}-{i}")
        seqs.append(seq)
    return seqs


def _walk_the_write_set(proj) -> str:
    """Every MUTATING writer of this lane, in a plausible endpoint order.

    Returns the subscription id used, so a caller can re-read it.
    """
    from tortoise import webhook_delivery as wd

    sub = wd.create_subscription(
        proj, url="https://hooks.example.com/cb", types=["PointAdded"],
        description="9233")
    # a successful delivery: _mark_delivered + _write_receipt(CREATE) + the prune
    wd.dispatch_pending(proj, transport=_ScriptedTransport([200]), batch=1)
    # a failed delivery: _mark_failed + _write_receipt(retrying)
    wd.dispatch_pending(proj, transport=_ScriptedTransport([500]), batch=1)
    # the delete door: two DETACH DELETE statements
    assert wd.delete_subscription(proj, sub["sub_id"]) is True
    return sub["sub_id"]


def _walk_the_read_set(proj) -> None:
    """Every READER of the lane — the other half of the AST census."""
    from tortoise import webhook_delivery as wd

    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb2",
                                 types=[], description="9233 reads")
    wd.get_subscription(proj, sub["sub_id"])
    wd.list_subscriptions(proj)
    wd.count_subscriptions(proj, include_disabled=True)
    wd.read_receipts(proj, sub["sub_id"])
    wd.due_subscription_count(proj.g)
    wd.event_head_seq(proj.g)
    wd.delete_subscription(proj, sub["sub_id"])


# ── the control: the instrument is a real guard ───────────────────────────

def test_the_instrument_refuses_an_undeclared_write(sdk_with_journal,
                                                    monkeypatch):
    """Without this, every "declared" assertion below would also pass on a guard
    that refuses nothing (the #7951 round-2 fail-open lesson).

    The statement is issued straight at the handle, outside any funnel, which is
    exactly what #9124 refuses.
    """
    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True, (
        "the fixture did not arm §17.1 — an unarmed projection is outside the "
        "rule and every assertion in this file would be vacuous"
    )
    _instrument(monkeypatch, refuse=True, owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (x:Undeclared9233 {id: 'x'})")


# ── the declaration: every write of this lane runs inside it ──────────────

def test_every_webhook_write_declares_its_lane(sdk_with_journal, monkeypatch):
    """Was RED before this change: `_CACHE_WRITE_AUTHORIZED` was `None` at every
    one of these writes, so #9124's handle guard refuses the product's own
    webhook surface. MUTATION: drop the declaration in `_run` and this fails
    with a list of undeclared statements.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    assert proj._journal_backed is True
    _seed_stream(proj, 2)

    probe = _instrument(monkeypatch, refuse=False, owner=proj)
    _walk_the_write_set(proj)

    writes = [(c, r) for c, r, w in probe.own() if w]
    assert len(writes) >= 6, (
        f"the writers issued {len(writes)} mutating statement(s); the walk no "
        "longer covers the delivery store's write set, so this pin went stale"
    )
    undeclared = [c for c, r in writes if r is None]
    assert not undeclared, (
        "these webhook writes reached the cache handle OUTSIDE any §17.1 "
        "declaration; #9124's guard will refuse them and the hosted door "
        f"reports a refusal as an ordinary failure: {undeclared}"
    )
    assert {r for _c, r in writes} == {wd.WEBHOOK_WRITE_LANE}, (
        "the lane must declare ONE name, so the guard and the refusal message "
        f"point at it; saw {sorted({r for _c, r in writes})}"
    )
    # ⛔ Attribution is not a blindfold (#9256): scoping this census to THIS
    # lane must not silence a PEER projection's UNDECLARED write — that is a
    # §17.1 violation on product code (`hosted_api._acquire_probe_sdk`), not
    # another lane's declaration to judge.
    assert_no_foreign_undeclared(probe)


def test_the_lane_name_is_pinned():
    """The declaration REASON is what the pin reads back at the write handle.

    It is not rendered into a refusal (`_cache_write_authorized`: a refusal fires
    precisely when the var is None, so there is no reason in flight to print) —
    which is why this is a name a TEST cites, not a message an operator sees.
    Read against a LITERAL, not against `wd.WEBHOOK_WRITE_LANE`: an expectation
    read from the code under test cannot fail when that code is renamed (#9233
    review P2).
    """
    from tortoise import webhook_delivery as wd

    assert wd.WEBHOOK_WRITE_LANE == "webhook.delivery-store", (
        "the lane name changed; update §17.1's carve-out paragraph and the "
        "tests/test_9079_* ledger with it, deliberately"
    )
    source = MODULE.read_text(encoding="utf-8")
    assert 'WEBHOOK_WRITE_LANE = "webhook.delivery-store"' in source, (
        "the lane constant is gone from the module source — the declaration "
        "no longer exists where the writer reads it"
    )
    assert "#9233" in source, (
        "the declaration must cite the issue that made the decision, so the "
        "choice is reviewable where the writer reads it"
    )


def test_the_armed_guard_does_not_refuse_the_product(sdk_with_journal,
                                                     monkeypatch):
    """The same walk with #9124's predicate REFUSING: it must complete.

    This is the issue's own indicator — "with the guard armed, the store's
    writes still land" — and it fails *inside* the write, not on a set
    comparison, so it cannot be satisfied by widening an assertion.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    _instrument(monkeypatch, refuse=True, owner=proj)
    sub_id = _walk_the_write_set(proj)

    assert wd.get_subscription(proj, sub_id) is None, (
        "the walk's own delete did not land under the armed guard"
    )
    assert wd.list_subscriptions(proj) == [], (
        "a subscription survived the walk — the delete is the statement most "
        "likely to be refused after the write it depends on"
    )


def test_a_read_runs_unscoped(sdk_with_journal, monkeypatch):
    """The lane is opened for WRITES, not blanket-opened for the module.

    If `_run` declared every statement, the class check below would be the only
    thing keeping the lane narrow — and a read would be "declared", which is a
    claim this lane does not make.
    """
    proj = sdk_with_journal._get_proj()
    probe = _instrument(monkeypatch, refuse=False, owner=proj)
    _walk_the_read_set(proj)

    reads = [(c, r) for c, r, w in probe.own() if not w]
    assert reads, "the read walk issued no statement the classifier calls read-only"
    assert {r for _c, r in reads} == {None}, (
        "a read ran with a declaration in flight — `_run` must scope writes only"
    )


# ── the refusal must not be swallowed (P1) ────────────────────────────────

def test_a_refused_cursor_write_is_not_counted_as_delivered(
        sdk_with_journal, monkeypatch):
    """A refused cursor write must be LOUD, not a delivery.

    Measured before the fix: `dispatch_pending` caught the refusal in its
    `except Exception`, returned `{'delivered': 1, …}` with the cursor unmoved,
    and the same event was re-POSTed on every pass forever — `max_attempts` can
    never trip because the success path never calls `_mark_failed`. A §17.1
    refusal is not a subscriber failure, so it propagates.
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 refuse")

    transport = _ScriptedTransport([200])
    _instrument(monkeypatch, refuse="SET s.cursor=", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=transport)

    assert transport.calls, "the POST did not happen, so the refusal under test was not reached"
    assert wd.get_subscription(proj, sub["sub_id"])["cursor"] == 0, (
        "the fixture refused the wrong statement — the cursor moved, so this "
        "test proves nothing about a refused cursor write"
    )


def test_a_cursor_failure_is_reported_as_failed_not_delivered(
        sdk_with_journal, monkeypatch):
    """The same counter, for an ORDINARY cursor failure (a graph hiccup, not a
    refusal). At-least-once says re-deliver, so the pass must not report the
    event as delivered: the summary is what a caller logs, and `delivered` with
    the cursor unmoved is the infinite re-POST of #9233 review P1 with a
    non-refusal cause."""
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    wd.create_subscription(proj, url="https://hooks.example.com/cb",
                           types=["PointAdded"], description="9233 hiccup")

    def _boom(*_a, **_kw):
        raise RuntimeError("graph hiccup on the cursor write")

    monkeypatch.setattr(wd, "_mark_delivered", _boom)
    summary = wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))

    assert summary["delivered"] == 0, (
        "a pass whose cursor write failed reported a delivery — the caller's "
        f"log says the event landed when it will be re-POSTed: {summary}"
    )
    assert summary["failed"] >= 1, f"the failure is not counted at all: {summary}"


def test_a_refused_failure_write_is_not_swallowed(sdk_with_journal,
                                                  monkeypatch):
    """The FAILURE branch's refusal must be loud too.

    `_mark_failed` writes `SET s.attempts=…` and the next backoff. Swallowed, the
    attempt is never recorded: the subscription is immediately due again and
    `max_attempts` can never trip, so a dead endpoint is retried forever with no
    disable. The `refuse=True` walks cannot cover this (the declaration IS in
    flight there), so it is aimed here (#9233 review round 2).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 attempts")

    _instrument(monkeypatch, refuse="SET s.attempts=", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=_ScriptedTransport([500]))

    assert wd.get_subscription(proj, sub["sub_id"])["attempts"] == 0, (
        "the fixture refused the wrong statement — the attempt landed, so this "
        "test proves nothing about a refused failure write"
    )


def test_an_undecryptable_secret_does_not_abort_the_rest_of_the_pass(
        sdk_with_journal, monkeypatch, caplog):
    """The DECRYPT branch's failure write is contained like the rejection branch's.

    That branch called `_mark_failed` OUTSIDE any handler, and the `for sub in
    subs:` loop has no enclosing try — so an ORDINARY failure of that write (a
    graph hiccup, not a refusal) escaped `dispatch_pending` and aborted delivery
    for every REMAINING subscription in the pass. That contradicts contract 3
    ("never raises for a subscriber, transport or graph failure") one call site
    away from the branch that did get the containment (#9233 review round 4).
    """
    import logging
    from datetime import UTC, datetime, timedelta

    from tortoise import crypto
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    wd.create_subscription(proj, url="https://hooks.example.com/undecryptable",
                           types=["PointAdded"], description="9233 no secret",
                           now=t0)
    second = wd.create_subscription(
        proj, url="https://hooks.example.com/fine", types=["PointAdded"],
        description="9233 fine", now=t0 + timedelta(seconds=1))

    real_decrypt = crypto.decrypt_token
    calls = {"n": 0}

    def _first_decrypt_fails(token):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("cannot decrypt (key rotated or unset)")
        return real_decrypt(token)

    def _failure_write_hiccups(*_a, **_kw):
        raise RuntimeError("graph hiccup on the failure write")

    monkeypatch.setattr(crypto, "decrypt_token", _first_decrypt_fails)
    monkeypatch.setattr(wd, "_mark_failed", _failure_write_hiccups)

    transport = _ScriptedTransport()
    with caplog.at_level(logging.WARNING, logger="tortoise.webhook_delivery"):
        summary = wd.dispatch_pending(proj, transport=transport)

    assert calls["n"] >= 2, (
        "the pass never reached the second subscription — the undecryptable-"
        f"secret branch ended it, which is the abort under test: {calls}"
    )
    assert summary["delivered"] == 2 and summary["failed"] == 0, (
        f"a contained failure write distorted the summary: {summary}"
    )
    assert wd.get_subscription(proj, second["sub_id"])["cursor"] == 2, (
        "the remaining subscription was not drained — the pass aborted"
    )
    messages = [r.getMessage() for r in caplog.records]
    assert any("failure write failed" in m for m in messages), (
        f"the contained failure write was not logged at all: {messages}"
    )


def test_a_refused_failure_write_on_the_decrypt_branch_is_not_swallowed(
        sdk_with_journal, monkeypatch):
    """The containment added above must not be a broad clause only: a §17.1
    REFUSAL of that same failure write still propagates.

    Stated pin, not a regression: pre-fix the refusal propagated too (the call
    had no handler at all), so this test's job is to hold the ARM in place once
    the ordinary failure is contained — losing it would silently re-open the
    swallow (#9233 review round 4).
    """
    from tortoise import crypto
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 decrypt")

    def _no_decrypt(_token):
        raise RuntimeError("cannot decrypt (key rotated or unset)")

    monkeypatch.setattr(crypto, "decrypt_token", _no_decrypt)
    transport = _ScriptedTransport([200])
    _instrument(monkeypatch, refuse="SET s.attempts=", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=transport)

    assert transport.calls == [], (
        "the POST happened, so the refusal under test was not the decrypt "
        "branch's failure write"
    )
    assert wd.get_subscription(proj, sub["sub_id"])["attempts"] == 0, (
        "the fixture refused the wrong statement — the attempt landed"
    )


def test_a_receipt_failure_does_not_un_deliver_the_event(
        sdk_with_journal, monkeypatch, caplog):
    """The cursor is the COMMIT POINT; the receipt is its audit.

    `_mark_delivered` advances the cursor first, so a receipt failure must be
    diagnosed as a RECEIPT failure and the delivery still count — the pre-fix
    code logged it as "cursor write failed", which reads as the opposite
    diagnosis. The counters alone cannot tell the two apart (both trees report
    `delivered`), so the log record is asserted too (#9233 review round 3).
    """
    import logging

    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 3)
    wd.create_subscription(proj, url="https://hooks.example.com/cb",
                           types=["PointAdded"], description="9233 receipt")

    def _no_receipt(*_a, **_kw):
        raise RuntimeError("receipt write hiccup")

    monkeypatch.setattr(wd, "_write_receipt", _no_receipt)
    with caplog.at_level(logging.WARNING, logger="tortoise.webhook_delivery"):
        summary = wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))

    assert summary["failed"] == 0 and summary["delivered"] == 3, (
        "a receipt failure was reported as a failed delivery, so the batch "
        f"stalled on an already-committed event: {summary}"
    )
    messages = [r.getMessage() for r in caplog.records]
    assert any("delivered receipt not recorded" in m for m in messages), (
        f"the receipt failure was not diagnosed as one: {messages}"
    )
    assert not any("cursor write failed" in m for m in messages), (
        "a receipt failure is still logged as a CURSOR failure — the diagnosis "
        "is inverted, and a cursor failure is the one that must not be "
        f"swallowed: {messages}"
    )


def test_a_refused_delivered_receipt_write_is_not_swallowed(
        sdk_with_journal, monkeypatch):
    """The delivered-branch receipt CREATE re-raises a refusal.

    `_mark_delivered` tolerates an ORDINARY receipt failure (the cursor already
    committed) but a REFUSAL is not that: the statement never landed. Without the
    re-raise it becomes a warning + a counted delivery — the silent-loss shape
    #9233 exists to prevent, and a guard no test covered (#9233 review round 3).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    sub = wd.create_subscription(proj, url="https://hooks.example.com/cb",
                                 types=["PointAdded"], description="9233 rcp")

    _instrument(monkeypatch, refuse="CREATE (r:DeliveryReceipt", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))

    assert wd.read_receipts(proj, sub["sub_id"]) == [], (
        "the fixture refused the wrong statement — a receipt landed, so this "
        "test proves nothing about a refused receipt write"
    )


def test_a_refused_receipt_prune_is_not_swallowed(sdk_with_journal,
                                                  monkeypatch):
    """The prune's refusal re-raises too: it is the third handler on this path
    and the one `_run`'s docstring must be able to claim is covered."""
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    wd.create_subscription(proj, url="https://hooks.example.com/cb",
                           types=["PointAdded"], description="9233 prune")

    _instrument(monkeypatch, refuse="SKIP $keep DETACH DELETE r", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.dispatch_pending(proj, transport=_ScriptedTransport([200]))


def test_a_refused_index_ddl_does_not_cache_the_schema_flag(
        sdk_with_journal, monkeypatch):
    """`ensure_subscription_schema` swallows "the index already exists" on
    purpose; a REFUSAL is not that, and caching `_SCHEMA_ATTR` over one leaves
    the graph permanently unindexed behind a DEBUG line (#9233 review P1)."""
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    _instrument(monkeypatch, refuse="CREATE INDEX", owner=proj)
    with pytest.raises(DirectCacheWriteRefused):
        wd.create_subscription(proj, url="https://hooks.example.com/cb",
                               types=[], description="9233 ddl")

    assert getattr(proj, wd._SCHEMA_ATTR, False) is False, (
        "the schema flag was cached after a REFUSED DDL — the index will never "
        "be retried and nothing but a DEBUG line records it"
    )


# ── the class check: the lane is narrow ───────────────────────────────────

def _labels_written(statement: str) -> set[str]:
    """Every LABEL-POSITION token in a statement — node labels and relationship
    types, in the `(v:Label {…})`, `-[:REL]->` and backticked-variable spellings.

    Anchored on the label position (after `(` or `[`) so a property VALUE is not
    mistaken for a label; a backticked variable is accepted because an engine
    accepts it, and missing one is the fail-OPEN direction for a class check.
    """
    ident = r"(?:[A-Za-z_]\w*|`[^`]+`)"
    return {m.strip("`") for m in re.findall(
        rf"[(\[]\s*(?:{ident})?\s*:\s*[`\"']?({ident})", statement)}


def test_the_label_reader_reads_labels_and_not_property_values():
    """A class check is only as good as its reader, in both directions: a missed
    label is fail-OPEN; a property value read as a label is a false alarm that
    gets the check weakened."""
    cypher = (
        "MATCH (s:ContextSubscription {sub_id: $sid}) "
        "CREATE (r:DeliveryReceipt {status: 'delivered'}) "
        "MERGE (s)-[:DELIVERED_AS]->(r)"
    )
    assert _labels_written(cypher) == {
        "ContextSubscription", "DeliveryReceipt", "DELIVERED_AS"
    }, _labels_written(cypher)
    assert _labels_written("MATCH (`r`:DeliveryReceipt) RETURN 1") == {
        "DeliveryReceipt"}
    assert _labels_written("MATCH (Receipt {id: $i}) RETURN 1") == set()


def test_every_declared_statement_writes_only_declared_labels(
        sdk_with_journal, monkeypatch):
    """The class check, not the classifier, is what keeps this lane narrow: the
    classifier is deliberately conservative, so a statement it cannot read takes
    the declaration. Every label the DECLARED statements actually write must be
    one the declaration named."""
    proj = sdk_with_journal._get_proj()
    _seed_stream(proj, 2)
    probe = _instrument(monkeypatch, refuse=False, owner=proj)
    _walk_the_write_set(proj)

    declared = [c for c, r, w in probe.own() if w and r is not None]
    assert declared, "no declared statement was recorded — the walk is stale"

    # A LABEL-LESS write (`MATCH (n) DETACH DELETE n`, `CREATE (n {…})`) has an
    # empty label set, so the subset check below would pass it — the class check
    # is fail-open exactly where a bulk wipe lives (#9233 review round 2).
    label_less = [c for c in declared if not _labels_written(c)]
    assert not label_less, (
        "a declared statement writes NO named label (a bulk wipe or an "
        f"unlabelled create): {label_less}"
    )

    offenders = {c: _labels_written(c) for c in declared
                 if _labels_written(c) - _DECLARED_LABELS}
    assert not offenders, (
        "the webhook lane declares labels it does not own; either narrow the "
        f"declaration or name the class in it: {offenders}")
    # ⛔ The label check above is scoped to this lane by `probe.own()` (#9256);
    # this keeps a PEER projection's undeclared write from being filtered away
    # with it.
    assert_no_foreign_undeclared(probe)


# ── the census: nothing writes outside the funnel ─────────────────────────

def _module_tree() -> ast.Module:
    return ast.parse(MODULE.read_text())


def _enclosing_functions(tree: ast.Module) -> dict[int, str]:
    """Map every node id to the name of the INNERMOST function enclosing it.

    A plain `NodeVisitor` cannot answer this (it would have to record each child
    as it descends); the recursion carries the current name instead.
    """
    owner: dict[int, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        name = current
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            name = node.name
        owner[id(node)] = name
        for child in ast.iter_child_nodes(node):
            walk(child, name)

    walk(tree, "<module>")
    return owner


def _root_name(node: ast.AST) -> str | None:
    """The ROOT identifier of an attribute/call chain (`proj.g.query(…)` -> proj)."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
            continue
        if isinstance(node, ast.Attribute):
            node = node.value
            continue
        if isinstance(node, ast.Name):
            return node.id
        return None


def test_every_handle_call_is_the_pinned_verb_set():
    """The census. `#9233` asks for a SINGLE funnel "so the census can pin it",
    and this is that pin — over EVERY verb, not just `query`.

    A write reaches `proj.g` through `query`, `_query`, `ro_query`,
    `execute_command("GRAPH.QUERY", …)`, `create_*`/`drop_*` — so a census that
    matched only `.query` would go blind exactly when the module was rewritten to
    use a different verb (#9233 review P2). The verb set is pinned instead: today
    this module only ever calls `query`, at two call sites, both inside `_run`.
    """
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    verbs: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if _root_name(node.func) not in {"proj", "graph"}:
            continue
        chain = []
        cur: ast.AST = node.func
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        # only handle-rooted chains (`…g.verb(` / `…db.verb(`); `proj.rebuild_all()`
        # is the projection's own API, not the handle
        if not ({"g", "db"} & set(chain)):
            continue
        verbs.setdefault(node.func.attr, set()).add(owner.get(id(node), "<module>"))

    assert verbs == {"query": {"_run"}}, (
        "the handle is no longer reached only through `_run` with `query`: "
        f"{ {v: sorted(f) for v, f in verbs.items()} }. Every statement this "
        "module issues must be classified and (when mutating) declared — route "
        "it through `_run` or pin the new site with its reason"
    )


def test_every_handle_reference_is_pinned():
    """`proj.g` / `proj.db` can also travel as an ARGUMENT (`event_head_seq(proj.g)`),
    where the writing happens inside the callee and no receiver scan can see it.
    Pin every enclosing function that touches the handle at all."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    sites = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in {"g", "db"}
                and isinstance(node.value, ast.Name) and node.value.id == "proj"):
            sites.add(owner.get(id(node), "<module>"))

    assert sites == set(_HANDLE_TOUCHING_FUNCTIONS), (
        "the set of functions that touch the projection's handle changed: "
        f"{sorted(sites)} — `_HANDLE_TOUCHING_FUNCTIONS` is the pin; a new one "
        "must be routed through `_run` (or stated, if it only READS)"
    )


def _render_fstring(node: ast.expr) -> str | None:
    """Evaluate a Cypher f-string against the MODULE's own namespace.

    Without this, a site whose statement is built from `_DUE_PREDICATE` /
    `_BEHIND_PREDICATE` is skipped by the scan — and that is the site the sweep
    runs per tenant graph per interval, i.e. exactly the one a vacuous census
    must not lose (#9233 review P2).
    """
    from tortoise import webhook_delivery as wd

    try:
        return eval(compile(ast.Expression(body=node), "<census>", "eval"),
                    vars(wd))
    except Exception:  # a site we cannot render is reported, not skipped
        return None


def test_raw_handle_queries_are_the_pinned_read_only_set():
    """`graph.query` (the SWEEP's injected handle, which may be
    `db.select_graph(<name>)` and sits below the door entirely) is allowed — but
    every such site must stay a READ, and the set is pinned so a new one is a
    decision rather than a silent bypass.

    Every site's statement is classified, INCLUDING the f-string one: a site that
    cannot be rendered fails here rather than being skipped."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    sites: set[str] = set()
    unrendered: list[str] = []
    statements: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr != "query":
            continue
        if _root_name(node.func) != "graph":
            continue
        fn = owner.get(id(node), "<module>")
        sites.add(fn)
        literal = node.args[0] if node.args else None
        rendered = (literal.value if isinstance(literal, ast.Constant)
                    and isinstance(literal.value, str)
                    else _render_fstring(literal) if literal is not None else None)
        if rendered is None:
            unrendered.append(fn)
        else:
            statements.append(rendered)

    assert not unrendered, (
        "a raw-handle site's statement could not be rendered, so its read-only "
        f"claim is unverified — {unrendered}"
    )
    assert sites == set(_RAW_HANDLE_READERS), (
        f"raw-handle query sites changed: {sorted(sites)} — `_RAW_HANDLE_READERS` "
        "is the pin; a new one needs a reason and, if it MUTATES, it does not "
        "belong on a raw handle at all"
    )
    mutating = [s for s in statements if _statement_writes(s)]
    assert not mutating, (
        "a raw-handle query MUTATES; the raw handle carries no §17.1 door, so "
        f"this write is undeclarable by construction: {mutating}"
    )


def test_every_funnel_caller_is_covered_or_stated():
    """AST vs the two walks: a NEW `_run`-calling function fails here instead of
    being silently uncovered — which is what makes the declaration's "the class
    check is the counterweight" claim true rather than aspirational."""
    tree = _module_tree()
    owner = _enclosing_functions(tree)

    callers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_run":
            callers.add(owner.get(id(node), "<module>"))

    covered = (_EXERCISED_WRITERS | _EXERCISED_READERS | set(_STATED_OTHERS))
    assert callers == covered, (
        "the funnel's caller set drifted from what this suite covers/excuses: "
        f"unseen={sorted(callers - covered)} stale={sorted(covered - callers)}"
    )

def _statement_text(call: ast.Call) -> str | None:
    """The literal statement a `_run`-shaped call passes, or `None` if unknowable.

    The statement is the first ARGUMENT THAT IS A STRING — `_run(proj, cypher,
    params)` puts the projection first, so reading `args[0]` would render `proj`.
    A `JoinedStr` (the `_DUE_PREDICATE` statements) contributes its constant
    parts; an argument that is a variable (`ensure_subscription_schema`'s loop
    `stmt`) has no renderable text, and `None` is then treated as MUTATING — the
    pin fails closed rather than skipping a site it cannot read.
    """
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            return arg.value
        if isinstance(arg, ast.JoinedStr):
            parts = [v.value for v in arg.values
                     if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            return " ".join(parts)
    return None


def _write_path_functions(tree: ast.Module) -> frozenset[str]:
    """Functions that issue a MUTATING statement through `_run` — derived.

    Uses `_statement_writes`, the same predicate `_run` gates on, so the pin and
    the funnel cannot disagree about what a write is. Read-only funnel callers
    (`_due_subscriptions`, the readers) are excluded: there is no refusal for
    their statements to swallow, and the artifact that admits a read into the
    write set is a classifier change, which re-derives this set.
    """
    out = {"_run"}
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "_run"):
                continue
            text = _statement_text(node)
            if text is None or _statement_writes(text):
                out.add(fn.name)
                break
    return frozenset(out)


def _try_body_ids(node: ast.Try) -> set[int]:
    """Ids of every node in a `try`'s GUARDED body — not `else`/`finally`.

    A raise inside `finally:` is NOT caught by that `try`'s handlers, so a
    line-range containment test would let a call there count as guarded and pass
    the pin on code that violates its own contract (#9233 review round 4).
    """
    ids: set[int] = set()
    for stmt in node.body:
        for child in ast.walk(stmt):
            ids.add(id(child))
    return ids


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    """Does this clause catch (near-)everything, refusals included?

    A bare `except:`, `except Exception`, `except BaseException`, the exception
    GROUP names, and a tuple that contains any of them (`except (Exception,
    OSError):`) — the tuple shape is a swallow that a name-only check misses.
    """
    broad = {"Exception", "BaseException", "ExceptionGroup", "BaseExceptionGroup"}
    node = handler.type
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in broad
    if isinstance(node, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in broad for e in node.elts)
    return False


def _is_refusal_arm(handler: ast.ExceptHandler) -> bool:
    """`except DirectCacheWriteRefused: raise` — the arm contract 3 promises."""
    return (isinstance(handler.type, ast.Name)
            and handler.type.id == "DirectCacheWriteRefused"
            and any(isinstance(s, ast.Raise) for s in handler.body))


def _call_names(stmts) -> set[str]:
    names: set[str] = set()
    for stmt in stmts:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def test_every_ordinary_failure_handler_on_the_write_path_re_raises_refusals():
    """`_run`'s docstring claims EVERY ordinary-failure handler on this path
    re-raises a refusal ahead of its broad clause — executable here, not prose.

    The claim used to carry a count of two handlers while five existed, which is
    how an audit reads "covered" without looking at the prune / receipt /
    `_mark_failed` sites. The count is gone; this pin is what makes the
    replacement sentence checkable (#9233 review round 4). The write-path set is
    DERIVED from the classifier, so a new writer is inspected the moment it
    exists rather than when someone remembers to add it here.

    MUTATION: delete an `except DirectCacheWriteRefused: raise` arm (the prune's,
    or `_mark_delivered`'s receipt) — this test names the site. So does changing
    a broad clause to `except (Exception, OSError):` and dropping the arm.
    """
    tree = _module_tree()
    write_path = _write_path_functions(tree)
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Try):
                continue
            if not (_call_names(node.body) & write_path):
                continue        # not a write-path handler
            if not any(_is_broad_handler(h) for h in node.handlers):
                continue        # nothing swallowed — the failure propagates
            if not any(_is_refusal_arm(h) for h in node.handlers):
                offenders.append(f"{fn.name}:{node.lineno}")

    assert not offenders, (
        "a handler on the write path swallows a §17.1 refusal — it must re-raise "
        f"`DirectCacheWriteRefused` ahead of its broad clause: {offenders} "
        f"(derive-checked write path: {sorted(write_path)})"
    )


def test_every_dispatch_write_is_refusal_armed():
    """The dispatch LOOP must not let an ordinary failure end the pass, and must
    not let a refusal be swallowed: every `_mark_*` call in `dispatch_pending`
    sits inside a `try` BODY that carries the refusal arm (a call in the same
    try's `finally:` is not guarded — a raise there bypasses its handlers).

    RED before the fix: the undecryptable-secret branch's `_mark_failed` call had
    no `try` at all, so a graph hiccup there escaped the loop and aborted every
    remaining subscription (#9233 review round 4).
    """
    tree = _module_tree()
    dispatch = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "dispatch_pending")
    guarded: set[int] = set()
    for node in ast.walk(dispatch):
        if isinstance(node, ast.Try) and any(_is_refusal_arm(h) for h in node.handlers):
            guarded |= _try_body_ids(node)

    unguarded = [
        f"{node.func.id}:{node.lineno}"
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"_mark_failed", "_mark_delivered"}
        and id(node) not in guarded
    ]
    assert not unguarded, (
        "a dispatch write is outside every refusal-armed try: an ordinary "
        "failure there aborts the rest of the pass, and a refusal there is "
        f"swallowed: {unguarded}"
    )


# ── the never-raises CONTRACT statements name their one exception ──────────

HOSTED_API = Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"

#: Every place a caller READS this lane's best-effort contract, `(module, function)`.
#: `dispatch_pending` now has exactly ONE exception (§17.1 refusal), so each of
#: these statements must name it: a reader who trusts an unconditional
#: "never raises" adds a call site with no refusal arm, which is the drift the
#: `#9233` raise path exists to remove (review round 5 — the sweep pass kept the
#: stale "the same best-effort one `dispatch_pending` keeps").
_NEVER_RAISES_CONTRACT_SITES = (
    ("webhook_delivery", "dispatch_pending"),
    ("webhook_delivery", "_status_code"),
    ("hosted_api", "_dispatch_webhooks"),
    ("hosted_api", "_webhook_retry_sweep_pass"),
)


def _module_path(name: str) -> Path:
    return {"webhook_delivery": MODULE, "hosted_api": HOSTED_API}[name]


#: The UNCONDITIONAL half of the contract — a sentence that says a failure cannot
#: escape. Every paragraph carrying one of these must name the carve-out in the
#: SAME paragraph.
_UNCONDITIONAL_CONTRACT_PHRASES = (
    "never raise", "never propagate", "does not raise", "doesn't raise",
    "cannot raise", "cannot escape", "non-raising",
)

#: A POINTER, not an unconditional claim: `dispatch_pending`'s own first line is
#: "Best-effort: see the ONE exception." — it states the contract without
#: re-stating it, so demanding the carve-out there is a false red. It still
#: counts as "this docstring states a contract", which is what the census asserts.
_CONTRACT_POINTERS = ("best-effort",)

_CONTRACT_PHRASES = _UNCONDITIONAL_CONTRACT_PHRASES + _CONTRACT_POINTERS

#: The carve-out tokens: the lane (whitespace-tolerant — `§ 17.1` is the same
#: claim) and the exception's own name.
_CARVE_OUT_17_1_RE = re.compile(r"§\s*17\s*\.\s*1")
_CARVE_OUT_TOKENS = ("directcachewriterefused",)


def _paragraphs(doc: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", doc) if p.strip()]


def _has_any_phrase(paragraph: str, phrases) -> bool:
    lowered = paragraph.lower().replace("\u2019", "'")
    return any(phrase in lowered for phrase in phrases)


def _is_contract_paragraph(paragraph: str) -> bool:
    """Does this paragraph state the best-effort contract at all?"""
    return _has_any_phrase(paragraph, _CONTRACT_PHRASES)


def _is_unconditional_contract_paragraph(paragraph: str) -> bool:
    """Does this paragraph make the STRONG claim (a failure cannot escape)?"""
    return _has_any_phrase(paragraph, _UNCONDITIONAL_CONTRACT_PHRASES)


def _states_carve_out(paragraph: str) -> bool:
    return (bool(_CARVE_OUT_17_1_RE.search(paragraph))
            or _has_any_phrase(paragraph, _CARVE_OUT_TOKENS))


def _missing_carve_out(doc: str) -> bool:
    """True when `doc` makes an unconditional never-raises claim in a paragraph
    that does not name the §17.1 refusal.

    Paragraph-scoped on purpose: a document-wide search passes the exact shape
    this pin exists to catch — an unconditional "never raises" sentence whose
    carve-out sits in some OTHER paragraph (measured: "See the module header
    for the §17.1 write-lane declaration" beside it went green).

    `all()` over the STRONG paragraphs, not `any()` over all of them
    (#9233 review round 7): with the weak pointer phrases mixed in, `any()` let
    an unconditional contract paragraph coexist with a carve-out paragraph
    elsewhere — the same hole one paragraph over. A paragraph that merely points
    at the exception ("Best-effort: see the ONE exception.") is not held to the
    carve-out, because it does not make the claim — that is the split that keeps
    the pin honest without false-redding real prose.
    """
    strong = [p for p in _paragraphs(doc) if _is_unconditional_contract_paragraph(p)]
    if not strong:
        return False        # no strong claim here — the census asserts one exists
    return any(not _states_carve_out(p) for p in strong)


def _function_docstring(tree: ast.Module, name: str) -> str | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return ast.get_docstring(node)
    return None


def test_the_contract_pin_reads_paragraphs_not_the_whole_docstring():
    """The checker this census rests on, pinned POSITIVELY in both directions.

    Two failure modes had to be closed on the checker itself (#9233 review
    rounds 6-7): a document-wide search passes the pre-fix shape (an
    unconditional claim whose caveat is parked in another paragraph), and a
    hard-coded phrase list reds a correct reword. Each case below is asserted to
    take the branch it is named for — an assertion that can only pass through
    the "nothing stated" early return would prove nothing, which is exactly what
    the first cut of the middle case did.
    """
    # (a) the pre-fix SHAPE: claim stated, carve-out only in a NEIGHBOUR
    neighbour = (
        "Push pending events. NEVER raises.\n\n"
        "See the module header for the §17.1 write-lane declaration.\n")
    assert _is_unconditional_contract_paragraph(_paragraphs(neighbour)[0])
    assert _missing_carve_out(neighbour), (
        "a carve-out in a neighbouring paragraph must not satisfy the pin")

    # (b) a REWORD that keeps the carve-out where it belongs — must pass, and
    #     must have been a live case (it carries a strong phrase of its own)
    reword = (
        "This pass contains every delivery and graph failure per graph, including\n"
        "a §17.1 ``DirectCacheWriteRefused`` from the dispatch, so the loop\n"
        "cannot escape it.\n")
    assert _is_unconditional_contract_paragraph(reword), (
        "the reword case carries no unconditional contract phrase, so it can "
        "only pass through the nothing-stated branch — it would not prove the "
        "checker accepts a reword")
    assert not _missing_carve_out(reword), (
        "a correct reword that names the refusal in the same paragraph must pass")
    assert _missing_carve_out(
        reword.replace("§17.1 ", "").replace("DirectCacheWriteRefused", "it")), (
        "the same reword with its carve-out stripped must RED — otherwise the "
        "case above passes for the wrong reason")

    # (c) a strong claim in ONE paragraph and the carve-out in another (the
    #     residual hole `any()` left open) — must red even though a contract
    #     paragraph does name the refusal
    elsewhere = (
        "Never raises for a subscriber or transport failure.\n\n"
        "Best-effort containment: a §17.1 ``DirectCacheWriteRefused`` propagates\n"
        "out of the store, per graph.\n")
    assert _missing_carve_out(elsewhere), (
        "an unconditional claim whose carve-out lives in a DIFFERENT paragraph "
        "must red — that is the hole `any()` left open")

    # (d) a pointer without the strong claim is not held to the carve-out
    pointer = "Push pending events. Best-effort: see the ONE exception.\n"
    assert not _is_unconditional_contract_paragraph(pointer)
    assert not _missing_carve_out(pointer)

    # (e) a docstring that states no contract at all is not this pin's business
    assert not _missing_carve_out("Coerce a value.\n")


def test_every_never_raises_contract_statement_names_the_refusal_carve_out():
    """The raise path this lane added is not optional reading: every statement of
    the best-effort contract must carry the carve-out, not just the module header.

    RED before the fix: `_webhook_retry_sweep_pass`'s docstring justified its own
    containment with "the same best-effort one ``dispatch_pending`` keeps" — a
    contract `dispatch_pending` no longer keeps — so the one sentence a future
    call site author reads asserted the wrong invariant. The census is DERIVED
    from a stated list rather than grepped, it refuses to pass on a docstring
    that states no contract at all (an emptied docstring is not a fix), and it
    requires the carve-out in the CONTRACT'S OWN paragraph —
    `test_the_contract_pin_reads_paragraphs_not_the_whole_docstring` is what
    proves the checker really is paragraph-scoped.
    """
    trees = {name: ast.parse(_module_path(name).read_text())
             for name in {m for m, _ in _NEVER_RAISES_CONTRACT_SITES}}
    offenders: list[str] = []
    for module, fn in _NEVER_RAISES_CONTRACT_SITES:
        doc = _function_docstring(trees[module], fn)
        assert doc, f"{module}.{fn} lost its docstring — the contract is unstated"
        # BOTH liveness checks, and the STRONG one is the load-bearing one: a bare
        # pointer ("Best-effort: see the ONE exception.") satisfies
        # `_is_contract_paragraph`, so without the second assert a site could drop
        # its unconditional sentence, keep the pointer, and this census would go
        # green while guarding nothing there (#9233 review round 8, ISSUE 1).
        assert [p for p in _paragraphs(doc) if _is_contract_paragraph(p)], (
            f"{module}.{fn} no longer states a best-effort contract; if the "
            "contract moved, update _NEVER_RAISES_CONTRACT_SITES rather than "
            "leaving a stale sentence behind")
        assert [p for p in _paragraphs(doc)
                if _is_unconditional_contract_paragraph(p)], (
            f"{module}.{fn} makes no UNCONDITIONAL claim any more, so the "
            "carve-out pin guards nothing at this site: a bare pointer phrase "
            "(`best-effort`) satisfies the check above. Either restore the "
            "never-raises sentence or drop the site from "
            "_NEVER_RAISES_CONTRACT_SITES")
        if _missing_carve_out(doc):
            offenders.append(f"{module}.{fn}")

    assert not offenders, (
        "these statements assert an unconditional never-raises contract while "
        "`dispatch_pending` re-raises `DirectCacheWriteRefused`: "
        f"{offenders} — name the §17.1 refusal as the ONE exception"
    )


# ── what the declaration does NOT claim ───────────────────────────────────

def test_a_rebuild_does_not_restore_the_store(sdk_with_journal, journal_path,
                                              monkeypatch):
    """This is a DECLARATION, not a durability upgrade.

    A `:ContextSubscription` is graph-native operational state with no journal
    record describing it, so a `rebuild` from the record alone cannot restore it.
    Recovery for this class is re-registration. A REAL `rebuild_all` is run here:
    wiping the graph would pass whether or not anything was journalled, which is
    the property this test claims to pin (#9233 review P2).
    """
    from tortoise import webhook_delivery as wd

    proj = sdk_with_journal._get_proj()
    sub = wd.create_subscription(proj, url="https://hooks.example.com/keep",
                                 types=[], description="9233 rebuild")
    assert wd.get_subscription(proj, sub["sub_id"]) is not None

    _instrument(monkeypatch, refuse=False, owner=proj)
    proj.rebuild_all(str(journal_path.parent), confirm_destructive=True)

    assert wd.get_subscription(proj, sub["sub_id"]) is None, (
        "the store was restored by a rebuild, so this pin no longer describes "
        "recovery for this class — re-read the declaration before deleting it"
    )
