"""Durable OAuth redemption state — issue #3027.

`oauth_codes.used_at` records that a request CLAIMED a code; these tests pin
what the claim's OUTCOME now records (`redemption_state`), the `code_id`
provenance link from minted tokens back to the code, and the reconciler that
resolves a claim whose outcome the process could not settle — the branch #2863's
in-process confirmation cannot reach.

Everything here drives the fake control plane (the issue's own instrument: "fake
CP applies the write then raises"), so the suite runs without a live PostgREST.
The migration itself is asserted separately, in the PGlite schema suite
(`supabase/tests/20260925000002_oauth_redemption_state.sql`).
"""
from __future__ import annotations

import pytest

import tortoise.oauth as oauth
from tests.fake_control_plane import FakeControlPlane
from tests.test_oauth_token_fault import (  # noqa: RUF100
    _live,
    _no_silent_faults,  # noqa: F401  (autouse stale-injector guard, this module too)
    _post_code,
    _post_refresh,
    _seed_access_token,
    _seed_code,
    _seed_refresh_token,
)
from tests.test_oauth_token_fault import (
    fault_client as fault_client,
)
from tortoise.oauth import _expires_iso, _sha256


def _code_row(cp, code: str) -> dict:
    """The stored oauth_codes row for a code's plaintext (fails loudly)."""
    want = _sha256(code)
    for row in cp.tables.get("oauth_codes", []):
        if row.get("code_hash") == want:
            return row
    raise AssertionError(f"no oauth_codes row for {code!r}")


def _force_claimed(cp, code: str, *, age_s: int) -> int:
    """Make a code look like an outcome-unknown claim of a given age (#3027),
    and return its id. This is the state the reconciler exists to resolve, so the
    row carries a `redemption_id` exactly as the real claim statement writes one."""
    row = _code_row(cp, code)
    cp.query("oauth_codes", method="PATCH", filters=[("id", "eq", row["id"])],
             json_body={"used_at": _expires_iso(-age_s),
                        "redemption_state": oauth.REDEMPTION_CLAIMED,
                        "redemption_id": f"rid-{code}"})
    return row["id"]


# ── State transitions ───────────────────────────────────────────────────────

def test_successful_exchange_records_minted_and_links_the_code(fault_client):
    """A delivered pair is terminal ('minted') and both minted rows carry the
    authorizing code — which is what makes 'did this code mint?' answerable."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "ok")
    code_id = _code_row(cp, "ok")["id"]
    assert _post_code(tc, cp, "ok", verifier).status_code == 200
    row = _code_row(cp, "ok")
    assert row["redemption_state"] == oauth.REDEMPTION_MINTED
    assert row["redemption_settled_at"] is not None
    assert row["redemption_id"] is not None
    assert cp.tables["oauth_access_tokens"][-1]["code_id"] == code_id
    assert cp.tables["oauth_refresh_tokens"][-1]["code_id"] == code_id


def test_verified_clean_abort_rearms_the_durable_state(fault_client):
    """#2863's re-arm, now durably recorded: a mint failure whose compensation is
    observed clean returns the code to 'unclaimed', so the retry provably works."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "rearm")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    r1 = _post_code(tc, cp, "rearm", verifier)
    assert r1.status_code == 503 and r1.json()["error"] == "temporarily_unavailable"
    row = _code_row(cp, "rearm")
    assert row["used_at"] is None
    assert row["redemption_state"] == oauth.REDEMPTION_UNCLAIMED
    r2 = _post_code(tc, cp, "rearm", verifier)
    assert r2.status_code == 200 and "access_token" in r2.json()


def test_terminal_signal_after_the_claim_burns_the_code(fault_client):
    """A wrong PKCE verifier claims the code and then fails. The code must be
    BURNED durably rather than left 'claimed'.

    The reason is not a re-arm (there is none — see
    `test_reconciler_burns_a_stale_claim_with_no_family`): a row left `claimed`
    has an UNRECORDED outcome, so it is answered terminally forever while its
    residue can only be resolved lazily, and the burn records the real outcome —
    the client's attempt is over, so nothing may ever mint from this code."""
    tc, cp = fault_client
    _seed_code(cp, "pkce")
    r = _post_code(tc, cp, "pkce", "v" * 60)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    row = _code_row(cp, "pkce")
    assert row["redemption_state"] == oauth.REDEMPTION_BURNED
    assert row["redemption_note"] == "terminal"
    # Long after the grace window it is still terminal — never re-armable.
    row["used_at"] = _expires_iso(-3600)
    assert _post_code(tc, cp, "pkce", "v" * 60).status_code == 400
    assert _code_row(cp, "pkce")["redemption_state"] == oauth.REDEMPTION_BURNED


def test_replay_after_success_is_terminal_and_mints_no_second_family(fault_client):
    """The issue's third Indicator: a replayed code after a successful redemption
    is rejected without a second live family."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "replay")
    assert _post_code(tc, cp, "replay", verifier).status_code == 200
    assert len(_live(cp, "oauth_refresh_tokens")) == 1
    assert len(_live(cp, "oauth_access_tokens")) == 1
    r2 = _post_code(tc, cp, "replay", verifier)
    assert r2.status_code == 400 and r2.json()["error"] == "invalid_grant"
    assert len(_live(cp, "oauth_refresh_tokens")) == 1     # no second family
    assert len(_live(cp, "oauth_access_tokens")) == 1


def test_rotation_inherits_the_code_link(fault_client):
    """A rotated descendant still points at the authorizing code. Without
    inheritance the reconciler would look past a live descendant and re-arm a
    code whose family is live — the double-grant this issue exists to prevent."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "rot")
    code_id = _code_row(cp, "rot")["id"]
    r1 = _post_code(tc, cp, "rot", verifier)
    assert r1.status_code == 200
    r2 = _post_refresh(tc, cp, r1.json()["refresh_token"])
    assert r2.status_code == 200
    assert cp.tables["oauth_refresh_tokens"][-1]["code_id"] == code_id
    assert cp.tables["oauth_access_tokens"][-1]["code_id"] == code_id


# ── The "committed but compensation failed" branch ──────────────────────────

def test_double_fault_stays_claimed_so_the_reconciler_can_resolve_it(fault_client):
    """access INSERT fails AND the cleanup fails → `recovered=False`. The outcome
    is UNKNOWN, so the durable state must stay 'claimed' — burning it here would
    hide the orphan from the only mechanism that can revoke it."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "df")
    cp.fail_query(table="oauth_access_tokens", method="POST", times=1)
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)   # rollback
    r = _post_code(tc, cp, "df", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    row = _code_row(cp, "df")
    assert row["redemption_state"] == oauth.REDEMPTION_CLAIMED
    assert row["used_at"] is not None


def test_reconciler_revokes_an_undelivered_orphan_family_and_burns_the_code(fault_client):
    """The branch #2863's confirmation cannot reach: the mint committed, the
    response was lost, and the compensating write also failed. Once the claim is
    older than the grace window the durable `code_id` link proves the family was
    never delivered — revoke it, burn the code, mint nothing new."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "orph")
    code_id = _code_row(cp, "orph")["id"]
    _force_claimed(cp, "orph", age_s=120)
    _rid, _ = _seed_refresh_token(cp, "orph-rt", code_id=code_id)
    _seed_access_token(cp, refresh_id=cp.tables["oauth_refresh_tokens"][-1]["id"],
                       code_id=code_id)
    r = _post_code(tc, cp, "orph", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert _live(cp, "oauth_refresh_tokens") == []      # the orphan is gone
    assert _live(cp, "oauth_access_tokens") == []
    assert _code_row(cp, "orph")["redemption_state"] == oauth.REDEMPTION_BURNED
    assert _code_row(cp, "orph")["redemption_note"] == "orphan-revoked"


def test_reconciler_burns_a_stale_claim_with_no_family(fault_client):
    """A stale claim that provably left no family is BURNED, never re-armed.

    A cross-request re-arm is a bet that the owner is dead, and the owner has no
    wall-clock bound (the mutating grant is awaited with `timeout=inf`, and the CP
    timeout is per-phase). If it is still running, the re-arm lets a second request
    claim and mint, and the late owner mints too — TWO live families for one
    single-use code. Fail safe instead: the client re-runs authorization."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "stale")
    _force_claimed(cp, "stale", age_s=120)
    r = _post_code(tc, cp, "stale", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    row = _code_row(cp, "stale")
    assert row["redemption_state"] == oauth.REDEMPTION_BURNED
    assert row["redemption_note"] == "unresolved"
    assert _post_code(tc, cp, "stale", verifier).status_code == 400   # never redeems


def test_a_fresh_claim_is_terminal_and_untouched(fault_client):
    """An in-flight sibling is neither re-armed NOR reported retryable. A
    retryable signal on an outcome-unknown claim is the untruthful "retry" #2863
    removed — the retry can terminate, because the sibling may settle `minted`."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "fresh")
    _force_claimed(cp, "fresh", age_s=5)
    before = dict(_code_row(cp, "fresh"))
    r = _post_code(tc, cp, "fresh", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert _code_row(cp, "fresh") == before


def test_reconciler_read_failure_touches_nothing(fault_client):
    """A failed reconcile read must neither settle nor revoke — nothing has been
    written, and the caller still answers terminally."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "blind")
    _force_claimed(cp, "blind", age_s=120)
    # Scope the injector to the RECONCILER's link probe. A shape-only match
    # (table+method+select) is shared with the boot retention sweep's eligibility
    # read, which runs on a daemon worker at startup and can consume it — so match
    # the `code_id` filter, which only this probe carries.
    cp.fail_query(table="oauth_refresh_tokens", method="GET", select=["id"],
                  match=lambda t, m, sel, f: (m == "GET" and sel == ["id"]
                                              and any(c == "code_id"
                                                      for c, _, _ in (f or []))),
                  times=1)
    before = dict(_code_row(cp, "blind"))
    r = _post_code(tc, cp, "blind", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert _code_row(cp, "blind") == before


# ── The CAS fence: exactly one of {owner, reconciler} settles ───────────────

def test_a_late_settle_cannot_resurrect_a_reconciled_claim(fault_client):
    """The CAS fence. Once a reconciler takes the claim over, the owner's late
    `minted` write must LOSE — otherwise the reconciler's revoke and the owner's
    delivery both land, which is the two-live-families outcome."""
    _tc, cp = fault_client
    _seed_code(cp, "fence")
    _force_claimed(cp, "fence", age_s=120)
    stale = dict(_code_row(cp, "fence"))          # the owner's view of its claim
    assert oauth._reconcile_claimed_redemption(cp, stale) == "burned-clean"
    assert oauth._settle_redemption(cp, stale, oauth.REDEMPTION_MINTED) is False
    assert _code_row(cp, "fence")["redemption_state"] == oauth.REDEMPTION_BURNED


def test_reconciler_never_revokes_a_family_whose_owner_settled_minted(fault_client):
    """The other half of the fence: if the owner records delivery FIRST, the
    reconciler loses the CAS and must not revoke the family it was about to
    deliver (`_settle_redemption` gates delivery, so the owner DID deliver)."""
    _tc, cp = fault_client
    _seed_code(cp, "delivered")
    code_id = _code_row(cp, "delivered")["id"]
    _force_claimed(cp, "delivered", age_s=120)
    owner = dict(_code_row(cp, "delivered"))
    _seed_refresh_token(cp, "delivered-rt", code_id=code_id)
    assert oauth._settle_redemption(cp, owner, oauth.REDEMPTION_MINTED) is True
    assert oauth._reconcile_claimed_redemption(cp, owner) == "lost-race"
    assert _live(cp, "oauth_refresh_tokens")      # the delivered family survives


def test_restore_code_cannot_undo_a_settled_state():
    """`_restore_code` carries the same fence: an in-process re-arm must not
    resurrect a claim a reconciler has already burned."""
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at="T1", redemption_state=oauth.REDEMPTION_BURNED)
    assert oauth._restore_code(cp, "c", "T1") is False
    assert _code_row(cp, "c")["redemption_state"] == oauth.REDEMPTION_BURNED


def test_a_superseded_claim_view_cannot_settle_the_current_claim(fault_client):
    """The `redemption_id` clause of the CAS is load-bearing, and this is the
    only test that exercises it.

    A request holding an OLD view of a claim must not settle the claim a
    DIFFERENT request now owns. Sequence: B claims → B's claim is re-armed
    in-process (verified-clean) → C claims the same code → B wakes with its stale
    row. Without the `redemption_id` filter, B's stale settle would BURN C's
    legitimate live claim (the state filter alone cannot tell them apart), and C
    would then be compensated out of a grant it legitimately holds."""
    _tc, cp = fault_client
    _seed_code(cp, "superseded")
    view_b = oauth._consume_code(cp, "superseded")
    assert oauth._restore_code(cp, "superseded", view_b["used_at"]) is True
    view_c = oauth._consume_code(cp, "superseded")
    assert view_c["redemption_id"] != view_b["redemption_id"]
    assert oauth._settle_redemption(cp, view_b, oauth.REDEMPTION_BURNED) is False
    row = _code_row(cp, "superseded")
    assert row["redemption_state"] == oauth.REDEMPTION_CLAIMED     # C's claim lives
    assert row["redemption_id"] == view_c["redemption_id"]
    assert oauth._settle_redemption(cp, view_c, oauth.REDEMPTION_MINTED) is True


def test_a_lost_minted_settle_compensates_instead_of_delivering(fault_client, monkeypatch):
    """The CALLER-side half of the delivery gate — the branch `exchange_auth_code`
    must take when a reconciler takes the claim over while this request is minting.

    Driven through the endpoint, not through `_settle_redemption`'s return value:
    the fake settle burns the claim exactly as a winning reconciler would, returns
    a loss, and the endpoint must compensate the pair it just minted rather than
    hand the client a family that is about to be revoked."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "race")
    real = oauth._settle_redemption
    seen: list[str] = []

    def losing_settle(c, row, state, *, note=None):
        if state == oauth.REDEMPTION_MINTED:
            seen.append("minted")
            real(c, row, oauth.REDEMPTION_BURNED, note="unresolved")
            return False                    # a reconciler won the claim first
        return real(c, row, state, note=note)

    monkeypatch.setattr(oauth, "_settle_redemption", losing_settle)
    r = _post_code(tc, cp, "race", verifier)
    assert seen == ["minted"], seen
    assert r.status_code == 400, r.text                  # no pair delivered
    assert "access_token" not in r.text
    assert _live(cp, "oauth_refresh_tokens") == []       # compensated, not delivered
    assert _live(cp, "oauth_access_tokens") == []
    assert _code_row(cp, "race")["redemption_state"] == oauth.REDEMPTION_BURNED


def test_a_lost_minted_settle_capture_events_when_compensation_fails(
        fault_client, monkeypatch):
    """The delivery-gate-loss path owns its ONE Sentry capture (I4).

    Nothing has captured yet (`_issue_tokens` succeeded), and the handler only
    logs — so when the compensation ALSO fails, a live never-delivered token row
    must not vanish silently. `capture=False` here is the observability hole a
    review caught; this pins the fix."""
    from tortoise import sentry
    calls: list[BaseException] = []
    monkeypatch.setattr(sentry, "capture_exception",
                        lambda exc, **kw: calls.append(exc))
    tc, cp = fault_client
    verifier = _seed_code(cp, "racecap")
    real = oauth._settle_redemption

    def losing_settle(c, row, state, *, note=None):
        if state == oauth.REDEMPTION_MINTED:
            real(c, row, oauth.REDEMPTION_BURNED, note="unresolved")
            return False
        return real(c, row, state, note=note)

    monkeypatch.setattr(oauth, "_settle_redemption", losing_settle)
    # Both compensation rows fail -> the rollback must capture exactly once.
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH", times=1)
    cp.fail_query(table="oauth_access_tokens", method="PATCH", times=1)
    r = _post_code(tc, cp, "racecap", verifier)
    assert r.status_code == 400, r.text
    assert len(calls) == 1, calls


def test_a_failed_reconciler_revoke_is_captured_and_the_code_still_burns(
        fault_client, monkeypatch):
    """The reconciler's revoke is best-effort, and that must be VISIBLE.

    `_rollback_minted` never raises, so a failed revoke would otherwise leave a
    live row under a `burned` code (note `'orphan-revoked'`) with no signal at
    all. The revoke passes `capture=True` for exactly this path; this pins the
    observability and records the residual rather than pretending the revocation
    cannot fail."""
    from tortoise import sentry
    calls: list[BaseException] = []
    monkeypatch.setattr(sentry, "capture_exception",
                        lambda exc, **kw: calls.append(exc))
    tc, cp = fault_client
    verifier = _seed_code(cp, "orphan-revoke")
    code_id = _code_row(cp, "orphan-revoke")["id"]
    _seed_refresh_token(cp, "orphan-rt", code_id=code_id)
    _force_claimed(cp, "orphan-revoke", age_s=120)
    # A select-LESS PATCH is the rollback shape (the claim sends a select).
    cp.fail_query(table="oauth_refresh_tokens", method="PATCH",
                  match=lambda t, m, sel, f: m == "PATCH" and not sel, times=1)
    r = _post_code(tc, cp, "orphan-revoke", verifier)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    row = _code_row(cp, "orphan-revoke")
    assert row["redemption_state"] == oauth.REDEMPTION_BURNED
    assert row["redemption_note"] == "orphan-revoked"
    # The escapee is live — inert (its plaintext was never delivered), TTL-reaped.
    assert len(_live(cp, "oauth_refresh_tokens")) == 1
    assert len(calls) == 1, calls


def test_an_unreadable_code_state_is_retryable_and_writes_nothing(fault_client):
    """A failed CLASSIFICATION read is a retryable 503 — and it must be, because
    the claim PATCH was observed to match ZERO rows, so this request provably
    wrote nothing (the retry re-runs the same claim CAS, which is what decides).

    This is deliberately NOT the #2863 hazard: there the WRITE was unobserved
    (the PATCH raised, so it may have committed) and the answer is terminal. Both
    halves are asserted here so the distinction cannot be silently collapsed — the
    in-code contract on `OAuthTemporarilyUnavailable` states which is which."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "unreadable", used_at="T1")
    # Only `_observe_code`'s read carries `redemption_state` in its select list.
    cp.fail_query(table="oauth_codes", method="GET",
                  match=lambda t, m, sel, f: (m == "GET" and sel is not None
                                              and "redemption_state" in sel),
                  times=1)
    before = dict(_code_row(cp, "unreadable"))
    r = _post_code(tc, cp, "unreadable", verifier)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "temporarily_unavailable"
    assert _code_row(cp, "unreadable") == before          # nothing was written
    # The unobserved-WRITE counterpart (the claim PATCH itself raises) stays
    # terminal — see `tests/test_oauth_token_fault.py`'s claim-fault cases.


# ── Primitives ──────────────────────────────────────────────────────────────

def test_link_probe_refuses_a_codeless_row():
    """`code_id = NULL` matches every UNLINKED token row, so a row with no id
    must be refused rather than probed — otherwise an unrelated family would be
    reported as this code's and revoked."""
    with pytest.raises(ValueError):
        oauth._live_family_for_code(FakeControlPlane(), {"id": None})


def test_settle_redemption_without_an_id_is_a_no_op():
    """A best-effort state write: a row with no id cannot be settled, and that is
    a False return — never an exception."""
    assert oauth._settle_redemption(FakeControlPlane(), {"id": None},
                                    oauth.REDEMPTION_BURNED) is False
    assert oauth._settle_redemption(FakeControlPlane(), None,
                                    oauth.REDEMPTION_BURNED) is False


@pytest.mark.parametrize("used_at,expires_in,expected", [
    (None, 600, "unclaimed"),
    ("2026-01-01T00:00:00+00:00", 600, "claimed"),
    ("2026-01-01T00:00:00+00:00", 600, "minted"),
    ("2026-01-01T00:00:00+00:00", 600, "burned"),
    (None, -10, "expired"),
])
def test_observe_code_classifies_the_state(used_at, expires_in, expected):
    cp = FakeControlPlane()
    _seed_code(cp, "c", used_at=used_at, expires_in=expires_in,
               redemption_state=None if used_at is None else expected)
    assert oauth._observe_code(cp, "c")["state"] == expected


def test_observe_code_missing_and_unreadable():
    assert oauth._observe_code(FakeControlPlane(), "nope")["state"] == "missing"
    cp = FakeControlPlane()
    _seed_code(cp, "c")
    cp.fail_query(table="oauth_codes", method="GET", times=1)
    assert oauth._observe_code(cp, "c")["state"] == "unobservable"


def test_grace_window_parse_is_directional(monkeypatch):
    """Same strict parse as the retention windows: malformed / non-positive falls
    back to the default (never to zero, which would reconcile a live sibling),
    and an over-long value clamps."""
    monkeypatch.delenv("TORTOISE_OAUTH_REDEMPTION_GRACE_S", raising=False)
    assert oauth._redemption_grace_s() == oauth.REDEMPTION_CLAIM_GRACE_S
    for bad in ("0", "-1", "abc", "1_0"):
        monkeypatch.setenv("TORTOISE_OAUTH_REDEMPTION_GRACE_S", bad)
        assert oauth._redemption_grace_s() == oauth.REDEMPTION_CLAIM_GRACE_S
    monkeypatch.setenv("TORTOISE_OAUTH_REDEMPTION_GRACE_S", "120")
    assert oauth._redemption_grace_s() == 120
    monkeypatch.setenv("TORTOISE_OAUTH_REDEMPTION_GRACE_S", "9" * 30)
    assert oauth._redemption_grace_s() == oauth._MAX_RETENTION_S
