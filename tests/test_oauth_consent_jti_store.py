"""The consent token's SHARED single-use register — #3524 §9 step 1, spec §2.2/§2.3.

WHY THIS FILE EXISTS
--------------------
PR #10096 shipped the consent-token ARM with a register that is **process-local**
(`consent_token.ProcessLocalJtiStore`), and said so in the module docstring: *"Until step 1
lands, the token arm is not fit for a multi-instance AS."* The hosted API runs several Fly
machines, so a token replayed against a machine that never saw its `jti` mints a **second
authorization code** — the replay §2.2's `jti` exists to bound, un-bounded in production.

Step 1 replaces that register with shared state. This file proves the REPLACEMENT, and it
proves the properties the replacement must not lose:

  * single-use still holds — including ACROSS "processes", which is the whole point and the
    one property a process-local store cannot have;
  * a replay is a typed `401 invalid_token`, not a 500 and not a pass;
  * an unreachable register is a typed `503 temporarily_unavailable` that **publishes no
    driver detail** — the contract `main` deliberately kept in PR #10096's P2-1 (the store's
    message is the operator's remedy channel, so it must never carry a DSN, URL or schema
    detail). The strict variant that silenced the channel was rejected and is archived as
    `archive/consent-token-round2-strict-p2-1-not-adopted`; silencing it is NOT the fix;
  * a register that answers something other than a boolean is "cannot answer" — never read
    as a pass, and never read as a replay (which would tell a first-time visitor their
    token was already used);
  * the row store stays bounded, and a row is never pruned while its token can still verify.

The end-to-end test at the bottom is the one that matters most: it runs the SAME three calls
the `/oauth/consent` handler makes, in the same order, against real minted tokens.

TOOLCHAIN — no network, no database. `tests/fake_control_plane.FakeControlPlane` implements
the PostgREST surface over in-memory rows and now emulates
`consume_oauth_consent_ticket` (the migration's single `INSERT … ON CONFLICT DO NOTHING
RETURNING`). The SQL's own atomicity is Postgres's and is stated in the migration; what is
proved here is the STORE's contract around it.
"""

from __future__ import annotations

import asyncio
import inspect
import time
from datetime import UTC, datetime

import pytest

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tortoise import consent_token
from tortoise.consent_token import (
    CONSENT_TOKEN_LEEWAY_S,
    ControlPlaneJtiStore,
    JtiStoreUnavailable,
    ProcessLocalJtiStore,
    configure_consent_jti_store,
    consume_consent_jti,
    mint_consent_token,
    verify_consent_token,
)
from tortoise.oauth import OAuthError

_TEST_SECRET = "test-consent-secret-0123456789abcdef"      # 39 bytes, above the floor
_BIND = {
    "client_id": "client-abc",
    "redirect_uri": "https://client.example/cb",
    "code_challenge": "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    "state": "state-xyz",
}


@pytest.fixture(autouse=True)
def _arm(monkeypatch):
    """Every test starts PROVISIONED, with the default (process-local) register reset.

    The register is process-global, so a `jti` left behind by one test makes the next
    test's "first use" a replay — a false red that depends on collection order.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    monkeypatch.delenv("CONSENT_TOKEN_ISSUER", raising=False)
    consent_token.reset_consent_jti_store()
    yield
    consent_token.reset_consent_jti_store()


def _minted(bind: dict | None = None, *, exp_in: float = 60.0) -> str:
    return mint_consent_token(
        user_id="user-1", email="a@example.com", bind=bind or dict(_BIND), ttl_s=int(exp_in))


def _presented(bind: dict | None = None) -> dict:
    b = bind or _BIND
    return {
        "client_id": b["client_id"],
        "redirect_uri": b["redirect_uri"],
        "code_challenge": b["code_challenge"],
        "state": b["state"],
    }


def _consume_sync(jti: str, expires_at: float) -> None:
    asyncio.run(consume_consent_jti(jti, expires_at))


# ── the guarantee step 1 exists for ────────────────────────────────────────

def test_single_use_holds_ACROSS_processes_not_just_within_one():
    """THE test for step 1. Two store instances over ONE row store = two Fly machines.

    A process-local register passes the in-process half and fails this one: machine B never
    saw machine A's `jti`, so it answers "first use" and a second code is minted. The
    register is shared, so the CONSTRAINT decides and exactly one machine wins.
    """
    shared = FakeControlPlane()

    async def run():
        machine_a = ControlPlaneJtiStore(shared)
        machine_b = ControlPlaneJtiStore(shared)
        first = await machine_a.consume("jti-cross-machine", time.time() + 60)
        replay_here = await machine_a.consume("jti-cross-machine", time.time() + 60)
        replay_there = await machine_b.consume("jti-cross-machine", time.time() + 60)
        return first, replay_here, replay_there

    first, replay_here, replay_there = asyncio.run(run())
    assert first is True, "the first redemption must be accepted"
    assert replay_here is False, "a replay on the SAME machine must be refused"
    assert replay_there is False, (
        "a replay on ANOTHER machine was accepted — the register is not shared, so one "
        "consent can yield two authorization codes (§2.2 `jti`)")
    # Exactly one row: the loser did not even write.
    assert len(shared.tables["oauth_consent_tickets"]) == 1


def test_a_replay_through_the_handlers_own_seam_is_a_typed_401():
    """`consume_consent_jti` is what `/oauth/consent` awaits. A replay is RFC 6749 §5.2.

    Typed 401 `invalid_token` — not a 500, and not a silent pass. The description must name
    the cause ("already been redeemed") because the visitor's recovery is a RE-MINT, and a
    body that only said "invalid" would send them to sign in again.
    """
    shared = FakeControlPlane()
    consent_token.install_shared_consent_jti_store(shared)

    consume_consent_jti_sync = _consume_sync
    consume_consent_jti_sync("jti-1", time.time() + 60)

    with pytest.raises(OAuthError) as refused:
        consume_consent_jti_sync("jti-1", time.time() + 60)
    assert refused.value.status == 401
    assert refused.value.error == "invalid_token"
    assert "already been redeemed" in refused.value.error_description


def test_the_installer_installs_a_store_the_seam_accepts():
    """`install_shared_consent_jti_store` is what startup calls; the seam validates async."""
    shared = FakeControlPlane()
    consent_token.install_shared_consent_jti_store(shared)
    installed = consent_token.consent_jti_store()
    assert isinstance(installed, ControlPlaneJtiStore)
    # The seam's own guard (§2.3): a blocking `consume` would record the jti and THEN blow
    # up at the await, spending a nonce in a request that already failed.
    with pytest.raises(TypeError):
        configure_consent_jti_store(type("Sync", (), {
            "consume": lambda self, jti, exp: True})())


def test_the_seams_declared_type_is_the_seam_not_the_fallback():
    """`consent_jti_store()` must be annotated with what it CAN return, not with the fallback.

    The deployed AS installs a `ControlPlaneJtiStore` on every boot (`hosted_api._lifespan`), so
    a return annotation of `ProcessLocalJtiStore` was false on the only path that runs in
    production: a caller type-checking against it reaches for `_seen`/`_max_entries`, which the
    deployed store does not have. `ruff` cannot see that and no other test disagreed with it, so
    the annotation is pinned here — both stores must satisfy the seam's protocol, and the
    annotated return type must be the protocol itself, which is what makes it honest for both.
    """
    import typing

    declared = typing.get_type_hints(consent_token.consent_jti_store).get("return")
    assert declared is consent_token.ConsentJtiStore, (
        f"consent_jti_store() is annotated -> {declared!r}, not the seam's protocol; the deployed "
        "store is a ControlPlaneJtiStore, so a narrower annotation is false where it matters"
    )
    assert typing.get_type_hints(consent_token.configure_consent_jti_store)["store"] == (
        consent_token.ConsentJtiStore | None
    )
    # Both implementations satisfy the declared contract — and this asserts the CONTRACT, not just
    # that the attribute exists: `isinstance` against a `runtime_checkable` Protocol checks MEMBER
    # PRESENCE only, so on its own it would pass a store whose `consume` had become a blocking
    # `def` or lost a parameter. The signature is therefore introspected explicitly, which is what
    # makes the message below true.
    expected = ["jti", "expires_at"]      # BOUND method: `self` is already consumed
    for store in (ProcessLocalJtiStore(), ControlPlaneJtiStore(FakeControlPlane())):
        assert isinstance(store, consent_token.ConsentJtiStore), (
            f"{type(store).__name__} has no `consume` — it does not satisfy ConsentJtiStore"
        )
        consume = store.consume
        assert inspect.iscoroutinefunction(consume), (
            f"{type(store).__name__}.consume is not `async def` — a blocking store RECORDS the jti "
            "and only then fails at the await, spending a nonce in a request that already failed"
        )
        assert list(inspect.signature(consume).parameters) == expected, (
            f"{type(store).__name__}.consume takes "
            f"{list(inspect.signature(consume).parameters)}, not {expected} — the three call sites "
            "(consume_consent_jti, the seam's install check, and ProcessLocalJtiStore) pass "
            "positionally, so a renamed parameter silently changes what `expires_at` means"
        )


# ── every refusal is typed, and the 503 publishes no driver detail ─────────

def test_an_unreachable_register_is_a_typed_503_that_names_no_driver_detail():
    """The contract `main` deliberately kept (PR #10096 P2-1), proved from the store side.

    The store's message is passed into the `error_description` of an UNAUTHENTICATED 503, so
    the condition that makes that passthrough safe is that it carries NO driver internals.
    A control-plane error routinely carries the upstream URL, a query string or a schema
    detail; none of it may reach the body.
    """
    leaky = ErrorControlPlane(RuntimeError(
        "httpx.ConnectError: https://db.example.supabase.co/rest/v1/oauth_consent_tickets"
        "?select=jti&apikey=sk-live-SHOULD-NOT-APPEAR"))
    consent_token.install_shared_consent_jti_store(leaky)

    with pytest.raises(OAuthError) as refused:
        asyncio.run(consume_consent_jti("jti-2", time.time() + 60))
    assert refused.value.status == 503
    assert refused.value.error == "temporarily_unavailable"
    description = refused.value.error_description
    for secret in ("db.example.supabase.co", "apikey", "sk-live-SHOULD-NOT-APPEAR",
                   "oauth_consent_tickets", "ConnectError"):
        assert secret not in description, (
            f"{secret!r} reached the unauthenticated 503 body — the store's message must "
            "carry no driver internals (§2.3 / PR #10096 P2-1)")
    # It must still be ACTIONABLE, which is the whole reason the channel was kept.
    assert "consent-ticket register could not answer" in description


def _answer(value):
    class _Answering:
        def rpc_value(self, fn, body=None):
            return value

    return _Answering()


@pytest.mark.parametrize("answer", [None, 0, 1, "true", [], {}])
def test_a_non_boolean_answer_is_cannot_answer_never_pass_and_never_replay(answer):
    """Coercing a non-boolean is wrong in BOTH directions, so neither is done.

    `None`/`0` → `False` would answer a FIRST redemption with "already redeemed" (the
    visitor's recovery becomes a re-mint that also fails); a truthy value → `True` would make
    every replay succeed. PostgREST returns a boolean or nothing; anything else is a fault.
    """
    consent_token.install_shared_consent_jti_store(_answer(answer))
    with pytest.raises(OAuthError) as refused:
        asyncio.run(consume_consent_jti("jti-3", time.time() + 60))
    assert refused.value.status == 503
    assert refused.value.error == "temporarily_unavailable"


def test_the_store_raises_its_own_typed_exception_and_the_seam_maps_it(monkeypatch):
    """The documented exception is `JtiStoreUnavailable`, and the seam owns the mapping."""
    store = ControlPlaneJtiStore(ErrorControlPlane(RuntimeError("boom")))
    with pytest.raises(JtiStoreUnavailable) as unavailable:
        asyncio.run(store.consume("jti-4", time.time() + 60))
    assert "consent-ticket register could not answer" in str(unavailable.value)


# ── the register stays bounded, and never forgets a live token ─────────────

def test_a_row_is_pruned_only_once_its_token_can_no_longer_verify():
    """Exercise the ACTUAL boundary, not a value on either side of it.

    The property is `exp + LEEWAY` (§2.2: the verifier refuses only when `exp + LEEWAY < now`),
    and the first version of this test placed its rows at `now + 60` and `now - 1` — trivially
    correct whatever the store stamped, which is why it stayed green while the store stamped the
    RAW `exp`. That mattered: the RPC prunes `expires_at < now()` BEFORE inserting, so a row
    stamped with the raw `exp` is deleted by the very replay it must fence, and the replay
    SUCCEEDS for the whole 30 s window (measured: one consent yields N codes). This test walks
    the clock across the boundary instead.
    """
    shared = FakeControlPlane()
    store = ControlPlaneJtiStore(shared)
    exp = time.time() + 10

    stamped = asyncio.run(store.consume("jti-edge", exp))
    assert stamped is True
    stamped_at = datetime.fromisoformat(
        shared.tables["oauth_consent_tickets"][0]["expires_at"]).timestamp()

    # ⛔ The stamp must be the LAST VALID INSTANT. Stamping the raw `exp` re-enables replay
    # inside the leeway window, because the prune then deletes the fence before the insert.
    assert stamped_at > exp, (
        "the stored expiry is not `exp + leeway` — inside the leeway window the prune would "
        "delete the row that fences this nonce and the replay would succeed")
    assert abs(stamped_at - (exp + CONSENT_TOKEN_LEEWAY_S)) < 1, (
        f"stamped {stamped_at - exp:.1f}s past exp, expected the "
        f"{CONSENT_TOKEN_LEEWAY_S}s leeway")

    # One second BEFORE the stamp, the row must still fence the nonce.
    shared.tables["oauth_consent_tickets"][0]["expires_at"] = datetime.fromtimestamp(
        stamped_at - 1, tz=UTC).isoformat()
    assert asyncio.run(store.consume("jti-edge", exp)) is False, (
        "a nonce whose token can still verify was forgotten — it is replayable again")

    # ── the DEAD side, driven rather than described ────────────────────────────────
    # A row whose token can no longer verify MUST be collected, or the register grows
    # without bound. Two separate facts, both asserted: the prune removes the stale row,
    # and the fresh nonce is then claimed (so the prune cannot collect the row it is
    # inserting in the same call — the ordering the RPC depends on).
    shared.tables["oauth_consent_tickets"].append({
        "jti": "jti-collectable",
        "expires_at": datetime.fromtimestamp(time.time() - 1, tz=UTC).isoformat(),
        "redeemed_at": None,
    })
    assert len(shared.tables["oauth_consent_tickets"]) == 2
    assert asyncio.run(store.consume("jti-after", time.time() + 60)) is True
    survivors = [row["jti"] for row in shared.tables["oauth_consent_tickets"]]
    assert "jti-collectable" not in survivors, (
        "a row whose token can no longer verify was kept — the register grows unbounded")
    assert "jti-after" in survivors, (
        "the prune collected the row this same call had just inserted — the ordering is "
        "backwards and the nonce would be replayable")
    assert "jti-edge" in survivors, "a row still inside its acceptance window was collected"


def test_the_prune_is_bounded_so_one_call_cannot_drain_the_backlog():
    """Same argument as `sweepExpiredSessions` (`FLOW_SWEEP_LIMIT`): request-path cost is
    bounded independently of how far behind the backlog has fallen."""
    shared = FakeControlPlane(tables={"oauth_consent_tickets": [
        {"jti": f"old-{i}", "expires_at": datetime_past(60)} for i in range(700)]})
    store = ControlPlaneJtiStore(shared)
    asyncio.run(store.consume("jti-new", time.time() + 60))
    remaining = len(shared.tables["oauth_consent_tickets"])
    assert remaining == 700 - 500 + 1, (
        f"the prune removed {700 - remaining} rows in one call — it must stop at 500")


def datetime_past(seconds: float) -> str:
    from datetime import datetime, timedelta

    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


# ── the end-to-end proof: mint → verify → consume → replay refused ─────────

def test_the_three_calls_the_consent_handler_makes_accept_a_valid_token_once():
    """This is the sequence in `/oauth/consent`'s token arm, against a REAL minted token.

    `verify_consent_token` → `consume_consent_jti` → (the handler then mints the code). Two
    unit tests that agree with each other would not prove the bridge; this drives the same
    contract the handler does, and then proves the second redemption is refused — which is
    what makes the token single-use rather than merely short-lived.
    """
    shared = FakeControlPlane()
    consent_token.install_shared_consent_jti_store(shared)

    token = _minted()
    assertion = verify_consent_token(token, presented=_presented())
    assert assertion["sub"] == "user-1"
    assert assertion["bind"] == _BIND
    asyncio.run(consume_consent_jti(assertion["jti"], assertion["exp"]))   # first: accepted

    # Replayed verbatim — the same bytes a captured request would carry.
    second = verify_consent_token(token, presented=_presented())
    with pytest.raises(OAuthError) as refused:
        asyncio.run(consume_consent_jti(second["jti"], second["exp"]))
    assert refused.value.status == 401
    assert "already been redeemed" in refused.value.error_description


# ── the properties step 1 must NOT lose (§2.2) ─────────────────────────────

def test_the_shared_register_does_not_weaken_the_binding_checks():
    """A shared register must not become a shortcut past `bind`.

    These refusals are the verifier's and stay BEFORE the spend, so a wrong-client or
    wrong-identity token is refused without consulting — or burning — the register.
    """
    shared = FakeControlPlane()
    consent_token.install_shared_consent_jti_store(shared)
    token = _minted()

    wrong_client = dict(_presented(), client_id="client-OTHER")
    with pytest.raises(OAuthError) as wrong:
        verify_consent_token(token, presented=wrong_client)
    assert wrong.value.status == 401 and "client_id" in wrong.value.error_description

    wrong_flow = dict(_presented(), state="state-OTHER")
    with pytest.raises(OAuthError) as cross_flow:
        verify_consent_token(token, presented=wrong_flow)
    assert cross_flow.value.status == 401 and "state" in cross_flow.value.error_description

    assert shared.tables.get("oauth_consent_tickets", []) == [], (
        "a refused assertion reached the register — a refusal must not spend the nonce")
