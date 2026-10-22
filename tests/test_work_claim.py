"""The claim CAS on the graph — and the fail-closed refusal that makes it a gate.

The headline property is NOT "acquire works". It is:

    a claim store that cannot answer must REFUSE the dispatch, never report it free.

Every other test here is a CAS property copied from what `claims.sh` proved on git refs
(atomic create, read-back, lease-as-data, fencing). The one that is deliberately DIFFERENT
is the refusal: the merge path is fail-open on purpose (agent-infra#1709, 2026-10-14),
while dispatch is fail-closed because proceeding blind at dispatch is what produced the
duplicate PRs #2247/#2250. `tools/work_claim.py` carries the `OVERRIDES:` line for it.

Store seams: a `FakeStore` exercises the CAS and refusal logic with no graph, and one
URI-guarded test drives the real SDK end-to-end so the shipped path is not merely assumed.
"""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = REPO_ROOT / "tools" / "work_claim.py"


def _load_module():
    """Load `tools/work_claim.py` by path — `tools/` is not a package, and the module must
    be importable exactly as the CLI runs it. The `work-claim` kind is registered by an
    explicit `register_claim_kind()` call (`SDKClaimStore.__init__` makes it), never as an
    import-time side effect — see `test_the_claim_kind_is_registered_by_an_explicit_call`."""
    spec = importlib.util.spec_from_file_location("_work_claim_under_test", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wc = _load_module()

NOW = 1_800_000_000


class FakeStore:
    """In-memory stand-in for the graph. `create` NEVER overwrites an existing id and hands
    the caller back the EXISTING content — the behaviour the SHIPPED store has because it
    writes `MERGE … ON CREATE SET` (one contender's `ON CREATE` runs, the loser's statement
    matches the existing node). It is deliberately NOT the behaviour of `create_point`:
    measured on a live FalkorDB, two `create_point` calls with the same id left TWO `:Point`
    nodes, which is exactly why the store does not use it (evidence README §3). The read-back
    is the authority rather than the write's exit code either way."""

    def __init__(self) -> None:
        self.points: dict[str, str] = {}
        self.writes = 0
        self.unanswerable = False
        self.lose_next_write = False  # simulate a peer writing between our write and read-back
        self.on_replace: str | None = None

    def _check(self) -> None:
        if self.unanswerable:
            raise wc.StoreUnanswerable("connection refused (simulated)")

    def read(self, cid: str) -> str | None:
        self._check()
        return self.points.get(cid)

    def create(self, cid: str, record) -> str:
        self._check()
        self.writes += 1
        if cid not in self.points:
            self.points[cid] = record.content()
        got = self.points[cid]
        if self.lose_next_write:
            self.lose_next_write = False
            # A peer overwrote between our create and the read-back: the write echoed OUR
            # content, but the store now holds theirs. Writing `got` back (the old seam) made
            # this a no-op, so the read-back branch could not be pinned at all.
            self.points[cid] = wc.ClaimRecord(
                repo=record.repo, key=record.key, owner="peer:999", session="peer",
                lease_until=record.lease_until, claimed_at=record.claimed_at,
            ).content()
        return got

    def replace(self, cid: str, record) -> str:
        self._check()
        self.writes += 1
        if self.on_replace is not None:
            # A peer's reclaim lands first; ours is overwritten immediately.
            self.points[cid] = self.on_replace
            return self.points[cid]
        self.points[cid] = record.content()
        return self.points[cid]

    def replace_if_epoch(self, cid: str, record, *, expected_epoch: int) -> str | None:
        """The fenced reclaim: set ONLY if the stored epoch is still `expected_epoch`.
        `None` means the precondition failed — the caller lost the race."""
        self._check()
        self.writes += 1
        if self.on_replace is not None:
            self.points[cid] = self.on_replace
            return None
        current = self.points.get(cid)
        if current is None or wc._int_field(current, "epoch") != expected_epoch:
            return None
        self.points[cid] = record.content()
        return self.points[cid]

    def query_repo(self, repo: str) -> list[dict]:
        self._check()
        rows: list[dict] = []
        for content in self.points.values():
            rec = wc._parse(content)
            if rec.repo == repo:
                rows.append(
                    {
                        "key": rec.key,
                        "owner": rec.owner,
                        "lease_until": rec.lease_until,
                        "epoch": rec.epoch,
                        "session": rec.session,
                        # The shipped reader carries these through too; the fake mirrors the
                        # SHIPPED shape rather than a convenient subset (review of #9487).
                        "host": rec.host,
                        "ncpu": rec.ncpu,
                        "lpc": rec.lpc,
                        "lanes": rec.lanes,
                    }
                )
        return rows


def _record(owner: str = "box-a:111", key: str = "issue-5052", **kw):
    return wc.ClaimRecord(
        repo="daniel-ospina/tortoise",
        key=key,
        owner=owner,
        session=kw.pop("session", "cell-t1"),
        lease_until=kw.pop("lease_until", NOW + 3600),
        purpose=kw.pop("purpose", "pilot"),
        claimed_at=kw.pop("claimed_at", NOW),
        **kw,
    )


# ── the headline property: unanswerable is a REFUSAL, not a default ───────────────


def test_unset_store_is_refused_before_any_query():
    """An UNSET store URI must refuse. The SDK falls back to a default target when
    `TORTOISE_DB_URI` is unset, and an empty read from the wrong graph is indistinguishable
    from "nobody holds this" — #7871 gap 1 measured exactly that as the real read defect.
    The guard takes an explicit environment, so it is asserted directly rather than by
    mutating the session's os.environ."""
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc._require_store_configured({})
    msg = str(excinfo.value)
    assert "TORTOISE_DB_URI" in msg
    assert "refused" in msg.lower()


def test_api_url_alone_does_not_satisfy_the_store_guard():
    """Review of #9487 (P0), reproduced live: `TORTOISE_API_URL=… work_claim.py check` printed
    `ok: claim store reachable` and exited 0 while the SDK had silently resolved the BOX-LOCAL
    embedded database — the claim would be confirmed by its own read-back here and read FREE
    on every peer. The API URL is not a store this tool can reach, so it must not arm the
    guard; the message has to say WHY, or the next reader re-adds it as an obvious fix."""
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc._require_store_configured({"TORTOISE_API_URL": "https://api.premiselabs.co"})
    msg = str(excinfo.value)
    assert "TORTOISE_API_URL" in msg
    assert "embedded" in msg.lower()


def test_sdk_store_refuses_a_target_that_resolved_to_the_embedded_file():
    """Belt and braces: even if a caller reaches `SDKClaimStore` directly, an embedded target
    (`_db_uri is None`) is not a shared claim store and must refuse rather than write locally."""

    class EmbeddedSDK:
        _db_uri = None

        def query(self, **_kw):
            return []

    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        wc.SDKClaimStore(sdk=EmbeddedSDK())
    assert "EMBEDDED" in str(excinfo.value).upper()


def test_main_refuses_with_rc2_when_the_store_is_unset(monkeypatch):
    """The CLI contract: rc 2, and the reason says which variable is missing. This is the
    value a controller branches on — rc 0 here would be the fail-open this tool removes."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    rc = wc.main(["acquire", "--repo", "o/r", "--issue", "5052", "--session", "cell-t1"])
    assert rc == wc.EXIT_UNANSWERABLE == 2


def test_unanswerable_store_refuses_rather_than_proceeding():
    store = FakeStore()
    store.unanswerable = True
    out = wc.acquire(store, _record())
    assert out.rc == 2
    assert out.status == "REFUSED"
    assert "dispatch refused" in out.reason
    assert "simulated" in out.reason  # the reason names WHY, not just that it failed


def test_read_back_failure_after_a_write_refuses():
    """A write that "succeeded" but cannot be read back is UNKNOWN, not acquired."""

    class ReadBackDies(FakeStore):
        def __init__(self):
            super().__init__()
            self.reads = 0

        def read(self, cid):
            # `acquire` now reads BEFORE it creates, so the first read is the pre-check. Let
            # that one answer "free"; the SECOND read is the confirmation read-back, and that
            # is the one that must fail here.
            self.reads += 1
            if self.reads == 1:
                return None
            raise wc.StoreUnanswerable("read timed out")

    out = wc.acquire(ReadBackDies(), _record())
    assert out.rc == 2
    assert "read-back failed" in out.reason


def test_a_store_that_neither_creates_nor_returns_refuses():
    class Void(FakeStore):
        def create(self, cid, record):
            return ""

    out = wc.acquire(Void(), _record())
    assert out.rc == 2
    assert "neither created nor returned" in out.reason


# ── the CAS ────────────────────────────────────────────────────────────────────────


def test_first_acquire_wins_and_second_is_refused_naming_the_holder():
    store = FakeStore()
    first = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert first.rc == 0 and first.status == "acquired"

    second = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert second.rc == 1
    assert second.status == "held"
    assert second.owner == "cell-1:100"
    assert "cell-1:100" in second.reason
    # The loser's create attempt did not change the stored claim: the winner still holds it.
    stored = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    assert stored.owner == "cell-1:100"


def test_claim_id_is_content_addressed_and_never_names_a_box():
    """Two boxes must compute the SAME id, or this is a ledger and not a lock — the property
    `claims.sh` spells out ("a KEY CONTAINING THE BOX ID IS NOT A CAS")."""
    a = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    b = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    c = wc.claim_id("daniel-ospina/tortoise", "issue-5053")
    d = wc.claim_id("daniel-ospina/agent-infra", "issue-5052")
    assert a == b
    assert a != c and a != d  # the repo is part of the identity, not just the number
    # Pinned to a LITERAL (review of #9487 P3): the old assertions compared two calls inside
    # one process, so folding the hostname into the digest still passed — every box would
    # compute a different id and the "same id on every box" property would be silently gone.
    assert a == "d0ae66a64d3a8688208f-20ed8abdbc54"
    # the SDK's ULID shape, so an explicit id does not warn on every write
    import re

    assert re.match(r"^[0-9a-f]+-[0-9a-f]{12}$", a)


def test_issue_and_pr_keys_are_disjoint():
    assert wc.normalise_key("issue", 5052) == "issue-5052"
    assert wc.normalise_key("pr", 5052) == "pr-5052"
    with pytest.raises(ValueError):
        wc.normalise_key("branch", 1)
    with pytest.raises(ValueError):
        wc.normalise_key("issue", 0)


def test_lost_race_reported_as_held_not_acquired():
    """If the store hands back a payload that is not ours, we lost — even when the write
    reported success. Trusting the write here is the whole defect class."""
    store = FakeStore()
    store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")] = _record(
        owner="cell-4:999", lease_until=NOW + 60
    ).content()
    out = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert out.rc == 1
    assert out.owner == "cell-4:999"


def test_expired_claim_is_reclaimed_and_the_epoch_fences():
    store = FakeStore()
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    store.points[cid] = _record(owner="cell-1:100", lease_until=NOW - 1).content()

    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 0
    assert out.status == "acquired"
    assert out.epoch == 2  # fenced: the stale holder's epoch-1 record is superseded
    assert "reclaimed" in out.reason
    # the old holder can no longer release it
    released = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW)
    assert released.rc == 1


def test_reclaim_through_a_store_without_a_fence_is_refused_not_blindly_written():
    """Review of #9487 (P1): a store that cannot express "set only if the epoch is still E"
    lets the interleaving A.replace → A.read → B.replace → B.read report `acquired` twice, so
    `acquire` must REFUSE (rc 2) rather than fall back to a blind write that reintroduces it.

    `SDKClaimStore` DOES express the fence — `MATCH … WHERE n.claim_epoch = $expected SET …`,
    which touches 0 rows when a peer moved it — so this pins the capability CHECK, not a
    limitation of the shipped store (the earlier docstring said the shipped store could not
    express it; review of #9487 noted it was stale)."""

    class BlindStore(FakeStore):
        replace_if_epoch = None

    store = BlindStore()
    store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")] = _record(
        owner="cell-1:100", lease_until=NOW - 1
    ).content()
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 2 and out.status == "REFUSED"
    assert "cannot express the reclaim precondition" in out.reason
    # and the expired holder's record is untouched — no half-reclaim
    assert wc._field(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")], "owner") == "cell-1:100"


def test_reclaim_is_lost_when_the_fence_observes_a_moved_epoch():
    """The CAS itself: a peer reclaimed between our read and our fenced write, so the write is
    declined and we are told we lost — naming the new holder, never retrying into it."""
    store = FakeStore()
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    store.points[cid] = _record(owner="cell-1:100", lease_until=NOW - 1).content()
    store.on_replace = _record(owner="cell-5:555", lease_until=NOW + 3600, epoch=2).content()
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    assert out.rc == 1 and out.status == "held"
    assert out.owner == "cell-5:555"
    assert "lost the reclaim race" in out.reason


# ── listing: an unread store is not an empty ledger ───────────────────────────────


def test_listing_refuses_when_the_store_cannot_list():
    """Review of #9487 (P1): `hasattr(store, "query_repo")` turned "cannot list" into "nothing
    to list" and answered rc 0 with `0 claim record(s)` over a store that held claims. A fleet
    reader would conclude the board was empty."""

    class NoListStore(FakeStore):
        query_repo = None

    out = wc.status(NoListStore(), "daniel-ospina/tortoise", None, now=NOW)
    assert out.rc == 2 and out.status == "REFUSED"
    assert "cannot list" in out.reason


def test_listing_returns_the_claims_the_store_holds():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    wc.acquire(store, _record(owner="cell-1:100", key="issue-5053"), now=NOW)
    out = wc.status(store, "daniel-ospina/tortoise", None, now=NOW)
    assert out.rc == 0 and out.status == "listed"
    keys = sorted(row["key"] for row in out.extra["claims"])
    assert keys == ["issue-5052", "issue-5053"]


def test_cli_status_without_a_number_lists_and_acquire_without_a_number_is_a_usage_error():
    """Review of #9487 (P2): `status --repo` (no number) used to flow into `normalise_key(0)`
    and return rc 2 — which a controller reads as "the store is unanswerable" and retries.
    A malformed invocation is rc 3; a store that cannot answer stays rc 2."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    rc = wc.main(["status", "--repo", "daniel-ospina/tortoise"], store=store)
    assert rc == 0
    rc = wc.main(["acquire", "--repo", "daniel-ospina/tortoise", "--session", "cell-t1"], store=store)
    assert rc == 3 == wc.EXIT_USAGE
    # an EXPLICIT 0 is a usage error even for the listing verb (review P3)
    rc = wc.main(["status", "--repo", "daniel-ospina/tortoise", "--issue", "0"], store=store)
    assert rc == wc.EXIT_USAGE
    # and an acquire with no reproducible identity is refused too: its claim could never be released
    rc = wc.main(["acquire", "--repo", "daniel-ospina/tortoise", "--issue", "5053"], store=FakeStore())
    assert rc == wc.EXIT_USAGE


# ── release identity, force, re-entrancy ───────────────────────────────────────────


def test_release_accepts_the_session_identity_the_acquire_used():
    """Review of #9487 (P1): acquire and release are DIFFERENT processes, so a `box:<pid>`
    default made every claim unreleasable — the reported holder was always another pid. A
    session id is the identity the caller already has and that survives a retry."""
    store = FakeStore()
    owner = wc._owner_token("cell-t1")
    wc.acquire(store, _record(owner=owner, session="cell-t1"), now=NOW)
    out = wc.release(
        store, "daniel-ospina/tortoise", "issue-5052", owner=wc._owner_token("cell-t1"), session="cell-t1", now=NOW + 5
    )
    assert out.rc == 0 and out.status == "released"


def test_forced_release_by_a_non_holder_records_the_reason_in_the_claim():
    """The operator route for a crashed holder: legal, but AUDITED — the reason and the
    releasing identity live in the tombstone, so the next reader sees an override rather than
    a claim that quietly changed hands. Without a reason the CLI refuses the invocation."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(
        store,
        "daniel-ospina/tortoise",
        "issue-5052",
        owner="cell-9:999",
        force=True,
        reason="holder crashed, lease expired 2h ago",
        now=NOW + 5,
    )
    assert out.rc == 0, out.reason
    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    stored = wc._parse(store.points[cid])
    assert "holder crashed, lease expired 2h ago" in stored.purpose
    assert "forced by cell-9:999" in stored.purpose
    assert stored.lease_until == NOW + 5

    rc = wc.main(
        ["release", "--repo", "daniel-ospina/tortoise", "--issue", "5052", "--force"],
        store=FakeStore(),
    )
    assert rc == wc.EXIT_USAGE


def test_forced_release_keeps_the_reason_verbatim_even_with_a_bracket():
    """The audit reason lives inside the bracketed `[released: …]` marker, so an unescaped `]`
    closed it early — and the old fix (`reason.replace("]", ")")`) silently REWROTE the
    operator's words (`a]b` became `a)b`) in the one field that records WHY a claim was taken
    from its holder (review of #9487 nit). Escaping keeps the text and the fence."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    reason = "holder crashed [see run 42] — closing bracket ] inside"
    out = wc.release(
        store,
        "daniel-ospina/tortoise",
        "issue-5052",
        owner="cell-9:999",
        force=True,
        reason=reason,
        now=NOW + 5,
    )
    assert out.rc == 0, out.reason
    stored = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    # the operator's words survive, character for character (escape aside):
    assert reason.replace("]", "\\]") in stored.purpose
    assert ")" not in stored.purpose.split("[released:", 1)[1].split("]", 1)[0].replace("(", "").replace("[", "")
    # the marker still opens once and the audit actor survives
    assert stored.purpose.count("[released:") == 1
    assert "forced by cell-9:999" in stored.purpose


def test_reentrant_acquire_is_already_held_rather_than_held_by_a_peer():
    """Review of #9487 (P2): a retry by the SAME caller must not be told a peer holds the
    unit. `claims.sh` never expressed "already mine", so a resumed lane refused work it owned."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    again = wc.acquire(store, _record(owner="cell-1:100"), now=NOW + 10)
    assert again.rc == 0 and again.status == "already-held"
    assert again.epoch == 1
    assert again.lease_until == NOW + 3600


def test_acquire_documents_the_fourth_shape_it_can_return():
    """`acquire` returns FOUR statuses — `already-held` (rc 0) is the re-entrancy one — while
    its docstring said "three shapes only" and the CALLER CONTRACT's rc-0 row omitted it. A
    controller branching on the enumerated vocabulary (rather than on rc) therefore refused
    its own retry, which is the behaviour the re-entrancy arm exists to remove (review of
    #9487 nit)."""
    assert "already-held" in wc.acquire.__doc__
    contract_rc0 = wc.__doc__.split("CALLER CONTRACT", 1)[1].split("rc 1", 1)[0]
    assert "already-held" in contract_rc0, contract_rc0
    # and the status it names really is what the code returns on a re-entrant acquire
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert wc.acquire(store, _record(owner="cell-1:100"), now=NOW + 10).status == "already-held"


def test_release_cannot_overwrite_a_claim_a_peer_reclaimed_meanwhile():
    """The release is conditional on the epoch the caller observed; when a peer's reclaim lands
    first the write must not erase it (the residual window a blind `update_point` leaves)."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    store.on_replace = _record(owner="cell-5:555", lease_until=NOW + 3600, epoch=2).content()
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW + 5)
    assert out.rc == 1
    assert out.owner == "cell-5:555"


def test_released_claim_is_reacquirable_through_a_fenced_store():
    """The hand-off path MUST close: acquire → release → acquire. A fence that refuses every
    expired record without distinguishing "released" would brick a key on its first hand-off
    (the first fix on this branch did exactly that, on the shipped store)."""
    store = FakeStore()
    first = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert first.rc == 0
    released = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW + 5)
    assert released.rc == 0
    second = wc.acquire(store, _record(owner="cell-4:200"), now=NOW + 10)
    assert second.rc == 0, second.reason
    assert second.epoch == 2


def test_box_local_target_is_refused_unless_acknowledged():
    """A loopback docker URL passes the env guard and is exactly as invisible to a peer as the
    embedded file — the same duplicate-dispatch failure one variable away."""

    class LoopbackSDK:
        _db_uri = "docker://:s3cret@127.0.0.1:16379/tortoise"

        def _get_proj(self):
            raise AssertionError("must refuse before touching the graph")

    store = wc.SDKClaimStore(sdk=LoopbackSDK())
    out = wc.acquire(store, _record())
    assert out.rc == 2 and out.status == "REFUSED"
    assert "BOX-LOCAL" in out.reason
    # the target is printed with the password REDACTED
    assert store.target() == "docker://***@127.0.0.1:16379/tortoise"
    assert "s3cret" not in store.target()
    # an acknowledged single-box trial is allowed through the guard
    assert wc.SDKClaimStore(sdk=LoopbackSDK(), allow_box_local=True).require_shared() is None


def test_redaction_hides_a_password_that_contains_an_at_sign():
    """The userinfo ends at the LAST `@` before the path, not the first. The old
    `//[^/@\\s]*@` stopped at the first one, so `docker://:p@ssw0rd@host:6379/g` was printed
    as `docker://***@ssw0rd@host:6379/g` — most of the password, in the exact output `check`
    emits for pasting into issues and controller logs (review of #9487)."""

    class HostedSDK:
        _db_uri = "docker://:p@ssw0rd@shared.host:6379/g"

        def _get_proj(self):
            raise AssertionError("redaction is a pure string operation")

    cases = {
        "docker://:s3cret@127.0.0.1:16379/tortoise": "docker://***@127.0.0.1:16379/tortoise",
        "docker://:p@ssw0rd@shared.host:6379/g": "docker://***@shared.host:6379/g",
        "bolt://user:p@ss@h:7687/g": "bolt://***@h:7687/g",
        # no userinfo at all must survive untouched (nothing to redact)
        "docker://localhost:6379/g": "docker://localhost:6379/g",
    }
    for uri, expected in cases.items():
        assert wc._redact_uri(uri) == expected, uri
    # not one character of the password survives — the tail was the leak
    assert "ssw0rd" not in wc._redact_uri("docker://:p@ssw0rd@shared.host:6379/g")
    # the SAME leak was reachable through the caller-visible `store.target()`
    assert wc.SDKClaimStore(sdk=HostedSDK()).target() == "docker://***@shared.host:6379/g"
    # ... and the host is still the real one (redaction must not eat the address)
    assert wc._uri_host("docker://:p@ssw0rd@shared.host:6379/g") == "shared.host"


def test_owner_token_uses_a_real_hostname_not_just_the_env_var(monkeypatch):
    """With HOSTNAME unset the STORED `host` field was empty (the token already had a socket
    fallback), so the record asserted no capacity provenance under cron/systemd — and the
    "claim map and capacity map are one record" promise silently held nothing."""
    monkeypatch.delenv("HOSTNAME", raising=False)
    import socket as _socket

    store = FakeStore()
    out = wc.acquire(store, _record(owner="cell-1:100", host=""), now=NOW)
    assert out.rc == 0
    stored = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    assert stored.host == _socket.gethostname()
    assert wc._owner_token("cell-t1").startswith(_socket.gethostname() + ":")


def test_status_and_release_refuse_a_box_local_store():
    """The loopback refusal belongs on EVERY verb that consults the store: `status` answering
    `free` from a graph no peer can read is what a controller then dispatches on, and a
    `release` there frees a claim nobody could see."""

    class LoopbackSDK:
        _db_uri = "docker://:@127.0.0.1:16379/tortoise"

        def _get_proj(self):
            raise AssertionError("must refuse before touching the graph")

    store = wc.SDKClaimStore(sdk=LoopbackSDK())
    single = wc.status(store, "o/r", "issue-1", now=NOW)
    listing = wc.status(store, "o/r", None, now=NOW)
    released = wc.release(store, "o/r", "issue-1", owner="box:1", now=NOW)
    # The REASON must be the box-local refusal, not "unanswerable": a store that raises for
    # any other cause would otherwise satisfy an rc-only assertion and the guard could be
    # deleted without a test failing (measured by mutation).
    for out in (single, listing, released):
        assert out.rc == 2, out.reason
        assert "BOX-LOCAL" in out.reason, out.reason
    allowed = wc.SDKClaimStore(sdk=LoopbackSDK(), allow_box_local=True)
    assert allowed.require_shared() is None


def test_reclaim_uses_the_window_the_caller_asked_for():
    """`--ttl` is the caller's statement of how long the unit is theirs; a reclaim that swapped
    in DEFAULT_TTL silently gave the next holder a different lease than it requested."""
    store = FakeStore()
    store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")] = _record(
        owner="cell-1:100", lease_until=NOW - 1
    ).content()
    out = wc.acquire(store, _record(owner="cell-4:200", lease_until=NOW + 600), now=NOW)
    assert out.rc == 0, out.reason
    assert out.lease_until - NOW == 600


def test_release_json_reports_the_tombstone_it_wrote():
    """`release --json` reported `epoch: 0, lease_until: 0` for the record it had just written,
    so a controller logging the release logged zeros."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW + 5)
    assert out.rc == 0
    payload = json.loads(json.dumps(out.as_dict(), sort_keys=True))
    assert payload["epoch"] == 1
    assert payload["lease_until"] == NOW + 5


@pytest.mark.parametrize("flag", ["ncpu", "lpc", "lanes"])
def test_negative_capacity_fields_are_a_usage_error(flag):
    """The record doubles as the fleet's capacity map; `ncpu=-8` there asserts something
    impossible, so a negative measurement is refused rather than persisted. One flag per case,
    because a single invocation naming all three lets one of the three checks be deleted with
    the suite still green (measured in review of #9487 P3)."""
    rc = wc.main(
        [
            "acquire", "--repo", "o/r", "--issue", "5052", "--session", "cell-t1",
            f"--{flag}", "-1",
        ],
        store=FakeStore(),
    )
    assert rc == wc.EXIT_USAGE


def test_a_malformed_invocation_is_rc3_never_the_retryable_rc2():
    """⛔ `argparse` exits **2** from `error()` — the code this tool reserves for "the store
    could not answer", which a controller RETRIES. Every parse failure used to return it: a
    missing `--repo` (required on all three verbs), a bad `--kind` choice, and
    `--allow-box-local` written BEFORE the subcommand (registered on the subcommand's parent).
    A retry of a command that can never succeed is the same defect the `status --repo` /
    `--ttl 0` fixes closed on the validated path (review of #9487 P2)."""
    for argv in (
        # `--repo` omitted: required on `acquire`, `status` and `release`.
        ["acquire", "--issue", "5052", "--session", "cell-t1"],
        ["status", "--issue", "5052"],
        ["release", "--issue", "5052", "--session", "cell-t1"],
        # a `choices` violation
        ["acquire", "--repo", "o/r", "--issue", "5052", "--session", "s", "--kind", "bogus"],
        # `--allow-box-local` belongs to the SUBCOMMAND, not the root parser
        ["--allow-box-local", "acquire", "--repo", "o/r", "--issue", "5052", "--session", "s"],
        # an unknown verb
        ["claim", "--repo", "o/r"],
    ):
        rc = wc.main(argv, store=FakeStore())
        assert rc == wc.EXIT_USAGE, argv
        assert rc != wc.EXIT_UNANSWERABLE, argv


def test_help_is_still_rc0_not_the_usage_error():
    """The parse-failure remap must not turn `--help` (argparse exits 0) into rc 3."""
    assert wc.main(["--help"]) == wc.EXIT_OK
    assert wc.main(["acquire", "--help"]) == wc.EXIT_OK


def test_check_survives_an_injected_store_without_a_target_method(monkeypatch):
    """`check` called the injected store's `target()` unconditionally; the test seam has none."""
    monkeypatch.setattr(wc, "_require_store_configured", lambda *a, **k: "docker://example.invalid/g")
    rc = wc.main(["check"], store=FakeStore())
    assert rc == 0


def test_check_refuses_a_box_local_target_instead_of_printing_ok(monkeypatch, capsys):
    """⛔ `check` is what a dispatch flow GATES on ("rc 2 = no"), and it used to print
    `ok: claim store reachable (docker://***@127.0.0.1:16379/…)` on STDOUT and exit **0** for
    a target `acquire`/`status`/`release` all refuse — the disqualification was only a stderr
    `WARNING`. That is the one-variable-away repeat of the failure this tool exists to
    prevent: the claim lands locally, reads `free` on every peer, and the preflight said go
    (review of #9487 P2). The verdict is now the siblings' verdict."""

    class BoxLocalStore(FakeStore):
        def require_shared(self):
            return (
                "the claim store target is BOX-LOCAL (127.0.0.1) — a claim written there reads "
                "as acquired here and free on every peer"
            )

        def target(self):
            return "docker://***@127.0.0.1:16379/tortoise_test_matrix"

    monkeypatch.setattr(wc, "_require_store_configured", lambda *a, **k: "docker://127.0.0.1:16379/tm")
    rc = wc.main(["check"], store=BoxLocalStore())
    err = capsys.readouterr()
    assert rc == wc.EXIT_UNANSWERABLE and rc != wc.EXIT_OK
    assert "ok:" not in err.out, "a box-local target must not be announced as ok on stdout"
    assert "BOX-LOCAL" in err.err

    # ...and the acknowledgement flag still lets a single-box trial proceed. ⛔ This arm alone
    # was VACUOUS (see the test below): `FakeStore` has no `require_shared`, so nothing was
    # refused and rc 0 came back whether or not the flag reached the store.
    rc = wc.main(["check", "--allow-box-local"], store=FakeStore())
    assert rc == wc.EXIT_OK


def test_the_allow_box_local_flag_actually_reaches_the_store_main_builds(monkeypatch, capsys):
    """⛔ The arm above pinned `check --allow-box-local` against an INJECTED `FakeStore`,
    which defines no `require_shared` — so `check`'s `getattr(store, "require_shared", None)`
    returned `None`, no refusal was produced, and rc 0 came back whether or not the flag was
    threaded into the store `main` constructs. Mutation proof: deleting
    `allow_box_local=getattr(args, "allow_box_local", False)` from BOTH `SDKClaimStore(...)`
    calls in `main` left the whole suite green while, in production, the documented single-box
    remedy (module USAGE, evidence README §6) became impossible: acquire/status/release/check
    all rc 2 while telling the operator to pass the flag they already passed (review of #9487
    P2).

    So this arm builds the store THROUGH `main` (store=None) and asserts the flag reaches the
    constructor on BOTH call sites — `check`'s own construction and the read/write verbs'.
    """
    captured: list[dict] = []

    class BoxLocalFakeStore(FakeStore):
        """A store whose shared-ness DEPENDS on the flag — the capability the vacuous arm
        lacked. It refuses exactly like `SDKClaimStore.require_shared` does."""

        def __init__(self, **kwargs) -> None:
            super().__init__()
            captured.append(kwargs)
            self._allow_box_local = bool(kwargs.get("allow_box_local"))

        def require_shared(self):
            if self._allow_box_local:
                return None
            return (
                "the claim store target is BOX-LOCAL (127.0.0.1) — pass --allow-box-local to "
                "acknowledge a single-box trial"
            )

    monkeypatch.setattr(
        wc, "_require_store_configured", lambda *a, **k: "docker://:pw@shared.host:6379/g"
    )
    monkeypatch.setattr(wc, "SDKClaimStore", BoxLocalFakeStore)

    # `check` builds its own store: without the flag the box-local refusal IS the verdict…
    assert wc.main(["check"], store=None) == wc.EXIT_UNANSWERABLE
    assert captured[-1].get("allow_box_local") is False, captured[-1]
    # …and with it, the same store is accepted — which can only happen if the flag was passed.
    assert wc.main(["check", "--allow-box-local"], store=None) == wc.EXIT_OK
    assert captured[-1].get("allow_box_local") is True, captured[-1]
    capsys.readouterr()

    # The read/write verbs have their OWN construction site; it must carry the flag too.
    argv = ["--json", "acquire", "--repo", "daniel-ospina/tortoise", "--issue", "5052"]
    assert wc.main([*argv, "--session", "cell-t1"], store=None) == wc.EXIT_UNANSWERABLE
    assert captured[-1].get("allow_box_local") is False, captured[-1]
    assert wc.main([*argv, "--session", "cell-t1", "--allow-box-local"], store=None) == wc.EXIT_OK
    assert captured[-1].get("allow_box_local") is True, captured[-1]


def test_check_json_carries_the_target_and_the_verdict(monkeypatch, capsys):
    """`--json` was accepted (it rides the shared parent parser) and silently IGNORED by
    `check`, so a controller parsing the other three verbs got prose here (review of #9487
    nit). It now emits one object, on the refusal too."""

    class BoxLocalStore(FakeStore):
        def require_shared(self):
            return "the claim store target is BOX-LOCAL (127.0.0.1)"

        def target(self):
            return "docker://***@127.0.0.1:16379/tm"

    monkeypatch.setattr(wc, "_require_store_configured", lambda *a, **k: "docker://127.0.0.1:16379/tm")

    assert wc.main(["check", "--json"], store=FakeStore()) == wc.EXIT_OK
    ok = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert ok == {"status": "ok", "reachable": True, "target": "unknown target"}

    assert wc.main(["check", "--json"], store=BoxLocalStore()) == wc.EXIT_UNANSWERABLE
    refused = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert refused["status"] == "REFUSED" and refused["reachable"] is False
    assert "BOX-LOCAL" in refused["reason"]


def test_read_back_that_differs_from_our_write_is_held_not_acquired():
    """Property 3 ("read back, never trust the write"): a store that echoes OUR content but
    holds a PEER's record is a lost race. The fake's seam for this used to write the same
    content back, so the branch could not be pinned at all (measured in review of #9487 P3)."""
    store = FakeStore()
    store.lose_next_write = True
    out = wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    assert out.rc == 1 and out.status == "held"
    assert "read-back differs" in out.reason
    assert out.owner == "peer:999"


def test_quoted_value_without_a_space_roundtrips():
    """Review of #9487 (P3): the conditional quote left a value containing a `"` but no space
    unbalanced, and `_parse` then swallowed every field after it."""
    rec = _record(purpose='says "hi"')
    back = wc._parse(rec.content())
    assert back.purpose == 'says "hi"'
    assert back.owner == rec.owner and back.lease_until == rec.lease_until
    assert back.content() == rec.content()


# ── release + status ──────────────────────────────────────────────────────────────


def test_release_by_the_holder_expires_the_claim_and_keeps_the_history():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-1:100", now=NOW + 5)
    assert out.rc == 0
    assert out.status == "released"

    cid = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    assert cid in store.points, "the record must survive the release — a deleted claim has no history"
    assert wc.status(FakeStoreWith(store.points), "daniel-ospina/tortoise", "issue-5052", now=NOW + 10).status == "expired"

    # ...and the next box can take it
    again = wc.acquire(store, _record(owner="cell-4:200"), now=NOW + 20)
    assert again.rc == 0


class FakeStoreWith(FakeStore):
    def __init__(self, points):
        super().__init__()
        self.points = points


def test_release_by_a_non_holder_is_refused_not_ignored():
    """Releasing someone else's claim is how a peer's lane gets duplicated, so it is a
    refusal with the real holder named — never a silent no-op."""
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.release(store, "daniel-ospina/tortoise", "issue-5052", owner="cell-4:200", now=NOW)
    assert out.rc == 1
    assert out.owner == "cell-1:100"
    assert wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW).status == "held"


def test_status_free_is_a_real_answer_and_unanswerable_is_not():
    """The distinction the whole tool rests on: a reachable store with no claim is FREE
    (rc 0); a store that cannot answer is rc 2. They must never collapse into one."""
    free = wc.status(FakeStore(), "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert free.rc == 0 and free.status == "free"

    dead = FakeStore()
    dead.unanswerable = True
    unknown = wc.status(dead, "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert unknown.rc == 2 and unknown.status == "REFUSED"


def test_status_rc_reports_that_it_answered_not_that_the_unit_is_free():
    """`status` is the verb whose job is "who holds this?", so its rc is the ANSWER's
    deliverability: `free`, `expired` and `held` are all rc 0 and the JSON `status` field says
    which. That is the OPPOSITE polarity to `claims.sh`'s status verb, which exits 1 when the
    unit is UNHELD (`scripts/orchestrator-harness/claims.sh::_claim_status_at`) — so the
    difference is named in the module's CALLER CONTRACT instead of leaving a silent gap
    (review of #9487). Pinned here so a later "align it with claims.sh" change has to break
    this test and read the reason."""
    store = FakeStore()
    free = wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert (free.rc, free.status) == (0, "free")

    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    held = wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW)
    assert (held.rc, held.status) == (0, "held")
    assert held.owner == "cell-1:100"

    expired = wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW + 4000)
    assert (expired.rc, expired.status) == (0, "expired")

    # the branchable exit code for "a live holder exists" is `acquire`'s (and release's):
    blocked = wc.acquire(store, _record(owner="cell-4:200"), now=NOW + 10)
    assert (blocked.rc, blocked.status) == (wc.EXIT_HELD, "held")


# ── the content line is the identity ───────────────────────────────────────────────


def test_content_roundtrips_through_fields_including_quoted_values():
    rec = _record(purpose="claim registry pilot, phase one")
    reparsed = wc._parse(rec.content())
    assert reparsed.content() == rec.content()
    assert reparsed.purpose == "claim registry pilot, phase one"
    assert "\x00" not in rec.content()


def test_capacity_fields_are_persisted_with_the_claim_not_measured():
    """`capacity.sh` measures; this tool only stores what it was handed — so the fleet's
    capacity map and claim map are one record, not two probes."""
    store = FakeStore()
    rec = _record(host="cell-1", ncpu=8, lpc=372, lanes=3)
    out = wc.acquire(store, rec, now=NOW)
    assert out.rc == 0
    back = wc._parse(store.points[wc.claim_id("daniel-ospina/tortoise", "issue-5052")])
    assert (back.host, back.ncpu, back.lpc, back.lanes) == ("cell-1", 8, 372, 3)


def test_a_claim_taken_without_capacity_flags_stores_absent_not_zero():
    """⛔ `content()` emitted `ncpu=0 lpc=0 lanes=0` unconditionally and the CLI defaulted all
    three to 0, so a claim taken without the capacity flags was INDISTINGUISHABLE from a
    measured idle box — the one thing a capacity map must not assert. Passing them is
    optional, so "not supplied" has to be its own value (review of #9487)."""
    store = FakeStore()
    rc = wc.main(
        ["acquire", "--repo", "daniel-ospina/tortoise", "--issue", "5052",
         "--session", "cell-t1"],
        store=store,
    )
    assert rc == wc.EXIT_OK
    (content,) = store.points.values()
    assert "ncpu=absent" in content and "lpc=absent" in content and "lanes=absent" in content
    back = wc._parse(content)
    assert (back.ncpu, back.lpc, back.lanes) == (None, None, None)
    # ...and the props omit them rather than seeding the graph's capacity map with a zero.
    assert "ncpu" not in back.props()
    assert back.content() == content, "an absent field must round-trip byte-identically"


def test_an_absent_capacity_field_is_not_a_measured_zero():
    """`0` and `absent` are different facts and must stay different in content and in props."""
    absent = _record().content()
    measured_idle = _record(ncpu=0, lpc=0, lanes=0).content()
    assert absent != measured_idle
    assert "ncpu=absent" in absent and "ncpu=0" in measured_idle
    assert wc._parse(absent).ncpu is None
    assert wc._parse(measured_idle).ncpu == 0
    assert "ncpu" not in _record().props()
    assert _record(ncpu=0).props()["ncpu"] == 0


def test_the_shipped_repo_listing_carries_host_and_capacity():
    """The paragraph above promises a reader can answer "who holds this, and on how loaded a
    box" from one store — but `SDKClaimStore.query_repo` returned only key/owner/lease/epoch/
    session and dropped `host`/`ncpu`/`lpc`/`lanes`, so that promise held only for a caller
    writing its own Cypher (review of #9487). Pinned on the SHIPPED reader, not the fake."""

    class _Result:
        def __init__(self, rows):
            self.result_set = rows

    class _Graph:
        def __init__(self, rows):
            self._rows = rows

        def query(self, cypher, params=None):  # the seam's shape, not its Cypher
            return _Result(self._rows)

    class _Proj:
        def __init__(self, rows):
            self.g = _Graph(rows)

    class _SDK:
        _db_uri = "docker://:pw@shared.example:6379/tortoise_test_matrix"

        def __init__(self, rows):
            self._rows = rows

        def _get_proj(self):
            return _Proj(self._rows)

    repo = "daniel-ospina/tortoise"
    measured = _record(key="issue-1", host="cell-1", ncpu=8, lpc=372, lanes=3)
    unmeasured = _record(key="issue-2", host="cell-2")
    store = wc.SDKClaimStore(
        sdk=_SDK(
            [
                (wc.claim_id(repo, "issue-1"), measured.content(), 3),
                (wc.claim_id(repo, "issue-2"), unmeasured.content(), 1),
            ]
        )
    )
    listed = {row["key"]: row for row in store.query_repo(repo)}
    assert (listed["issue-1"]["host"], listed["issue-1"]["ncpu"],
            listed["issue-1"]["lpc"], listed["issue-1"]["lanes"]) == ("cell-1", 8, 372, 3)
    assert listed["issue-2"]["host"] == "cell-2"
    assert listed["issue-2"]["ncpu"] is None, "an unmeasured claim is not a measured idle box"


def test_status_repo_json_exposes_the_capacity_fields():
    """`status --repo --json` is the shipped reader a controller branches on, so the fields
    the record holds have to reach it."""
    store = FakeStore()
    wc.acquire(store, _record(host="cell-1", ncpu=8, lpc=372, lanes=3), now=NOW)
    out = wc.status(store, "daniel-ospina/tortoise", None, now=NOW)
    assert out.rc == wc.EXIT_OK
    row = out.as_dict()["extra"]["claims"][0]
    assert (row["host"], row["ncpu"], row["lpc"], row["lanes"]) == ("cell-1", 8, 372, 3)


def test_the_claim_kind_is_registered_by_an_explicit_call():
    """The comment on `CLAIM_KIND` said the kind "is registered at import"; importing the
    module registered NOTHING (only `SDKClaimStore.__init__` did), so the forgotten-
    registration reader the comment warned about was exactly the one it did not cover. It is
    now an explicit, idempotent `register_claim_kind()` — and deliberately NOT an import-time
    side effect, which would print the domain loader's pack warnings onto `--json`'s stdout.

    Measured in a FRESH interpreter: in this process the module is already loaded and other
    tests have constructed the store, so the import-order property is not observable here.
    """
    script = "\n".join(
        [
            "import importlib.util, sys",
            f"spec = importlib.util.spec_from_file_location('_wc_probe', {str(_MODULE_PATH)!r})",
            "module = importlib.util.module_from_spec(spec)",
            "sys.modules['_wc_probe'] = module",
            "spec.loader.exec_module(module)",
            "from tortoise.domain_loader import kind_is_known",
            "before = kind_is_known(module.CLAIM_KIND)",
            "module.register_claim_kind()",
            "after = kind_is_known(module.CLAIM_KIND)",
            "module.register_claim_kind()  # idempotent, must not raise",
            "print('KIND', before, after, file=sys.stderr)",
        ]
    )
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0, proc.stderr
    marker = [ln for ln in proc.stderr.splitlines() if ln.startswith("KIND ")]
    assert marker, proc.stderr
    assert marker[-1].split()[1:] == ["False", "True"], marker[-1]


def test_usage_block_advertises_the_explicit_interpreter_form():
    """⛔ The file is committed `100644`, so `tools/work_claim.py acquire …` is
    `Permission denied` — shell exit 126, outside the tool's own documented rc 0/1/2/3 — and
    the USAGE block advertised exactly that bare form for all six examples. The siblings
    (`collision_preflight.py`, `who_is_on.py`, `branch_reaper.py`) all document
    `uv run python tools/<tool>.py`, as does this module's own #5128 guard message and the
    evidence README §6 (review of #9487)."""
    usage = wc.__doc__ or ""
    assert "USAGE" in usage
    invocations = [
        line.strip()
        for line in usage.splitlines()
        if re.search(r"(^|\s)tools/work_claim\.py (acquire|status|release|check)\b", line)
    ]
    assert len(invocations) >= 6, invocations
    bad = [
        line
        for line in invocations
        if not line.startswith("uv run python tools/work_claim.py ")
    ]
    assert not bad, bad


def test_json_outcome_carries_the_decision_a_controller_logs():
    store = FakeStore()
    wc.acquire(store, _record(owner="cell-1:100"), now=NOW)
    out = wc.acquire(store, _record(owner="cell-4:200"), now=NOW)
    payload = json.loads(json.dumps(out.as_dict(), sort_keys=True))
    assert payload["status"] == "held"
    assert payload["owner"] == "cell-1:100"
    assert payload["lease_until"] == NOW + 3600


# ── the shipped store, end to end ───────────────────────────────────────────────────
#
# ⛔ EVERY live leg keys on a PER-RUN claim key. A fixed key (`issue-999999` and friends) made
# two concurrent runs of this file — this box runs many lanes against one container by design
# (#5084) — read and fence each other's records: the second run's `replace_if_epoch(…,
# expected_epoch=1)` lost to a peer that had already bumped the epoch, so the assertions
# below went red for a reason that had nothing to do with the code under test (review of
# #9487). Each leg deletes its own node in `finally`, so the per-run key leaves no residue.


def _probe_key(body: str | None = None) -> str:
    # CANONICAL at the MINT (review round 4). `acquire` canonicalises `record.key` IN PLACE
    # (`record.key = canonical_key(record.key)`), and `canonical_key` collapses an all-digit
    # body (`issue-012345678901` → `issue-12345678901`) — which a 12-hex body hits in ~1 run in
    # 2.8k. Returning the minted spelling left the legs below comparing it against the
    # CANONICAL spelling the store holds, so `[…].count(key) == 1` redded for a reason that had
    # nothing to do with the code under test: the exact class this file's comment above already
    # records. The `body` seam makes that deterministic to pin without drawing a UUID.
    return wc.canonical_key(f"issue-{body if body is not None else uuid.uuid4().hex[:12]}")



@pytest.mark.skipif(
    not os.environ.get("TORTOISE_DB_URI"),
    reason="needs a live graph (TORTOISE_DB_URI) — the fake-store tests above cover the CAS",
)
def test_live_sdk_store_acquire_status_release():
    """The real path, end to end: the atomic `MERGE`-token create, the epoch-conditional write,
    the repo listing, and the hand-off that must close (acquire → release → acquire).

    Skips visibly (never green) with no URI, so an embedded/carve-out run cannot claim this was
    exercised. `allow_box_local`: the lane's own container is loopback by design.
    """
    store = wc.SDKClaimStore(allow_box_local=True)
    key = _probe_key()
    repo = "daniel-ospina/tortoise"
    cid = wc.claim_id(repo, key)
    record = wc.ClaimRecord(
        repo=repo,
        key=key,
        owner="pytest:1",
        session="pytest",
        lease_until=NOW + 3600,
        purpose="live store smoke",
        claimed_at=NOW,
    )
    try:
        acquired = wc.acquire(store, record, now=NOW)
        assert acquired.rc == 0, acquired.reason

        # ⛔ `create_point` DOES mint a second node for an existing id (measured); the store's
        # MERGE must not, or the ledger reports one unit twice with two owners. Call `create`
        # a SECOND time directly (one create for a fresh id leaves one node even under the old
        # primitive, so the assertion above alone cannot detect a regression).
        store.create(
            cid,
            wc.ClaimRecord(
                repo=repo, key=key, owner="pytest:intruder", lease_until=NOW + 3600, claimed_at=NOW
            ),
        )
        count = store._rows("MATCH (n:Point {id:$id}) RETURN count(n) AS c", id=cid)[0][0]
        assert count == 1, f"the claim store holds {count} nodes for one id"

        # the second box must be refused, naming the holder. ⛔ `now=NOW` is NOT decoration:
        # the stored `lease_until` is `NOW + 3600`, and passing no clock made `acquire` compare
        # it against `time.time()` — so this leg was a DATE BOMB that would flip to "expired",
        # take the reclaim path, return rc 0 and red the suite on 2027-01-15T09:00Z, for a
        # reason unrelated to any change (review of #9487 P2). An explicit clock makes the
        # whole leg deterministic at any wall time.
        held = wc.acquire(
            store,
            wc.ClaimRecord(
                repo=repo, key=key, owner="pytest:2", lease_until=NOW + 3600, claimed_at=NOW
            ),
            now=NOW,
        )
        assert held.rc == 1, held.reason
        assert held.owner == "pytest:1"

        # the listing answers, per unit of work, from the real graph — and it answers for a
        # DIFFERENT SPELLING of the same repo (review of #9487 P2: an exact `n.repo = $repo`
        # match made `status --repo` report `0 claim record(s)` over a store that held the
        # claim, which is indistinguishable from "nobody holds anything").
        rows = store.query_repo(repo)
        assert [r["key"] for r in rows].count(key) == 1, rows
        shouted = store.query_repo(repo.upper())
        assert [r["key"] for r in shouted].count(key) == 1, shouted
        listed = wc.status(store, repo, None)
        assert listed.rc == 0 and listed.status == "listed"

        released = wc.release(store, repo, key, owner="pytest:1", now=NOW + 5)
        assert released.rc == 0, released.reason

        # ⛔ the hand-off must CLOSE. A fence with no way past a released record bricked the
        # key on its first release (measured on this branch before the epoch-CAS): every later
        # acquire returned rc 2 and `release --force` did not clear it.
        again = wc.acquire(
            store,
            wc.ClaimRecord(
                repo=repo, key=key, owner="pytest:2", lease_until=NOW + 3600, claimed_at=NOW
            ),
            now=NOW + 10,
        )
        assert again.rc == 0, again.reason
        assert again.epoch == 2  # fenced: the released epoch-1 record is superseded
    finally:
        # The suite's own residue: best-effort, and never a test failure.
        with contextlib.suppress(Exception):
            store._rows("MATCH (n:Point {id:$id}) DETACH DELETE n", id=cid)


@pytest.mark.skipif(
    not os.environ.get("TORTOISE_DB_URI"),
    reason="needs a live graph (TORTOISE_DB_URI) — the fake-store tests cover the CAS logic",
)
def test_live_fence_admits_exactly_one_of_two_contenders():
    """The fence is the STATEMENT, so it must be pinned against the real graph and not only
    against `FakeStore` (which implements the fence itself). Mutation-measured in review:
    deleting `WHERE n.claim_epoch = $expected` left the whole suite green, and two contenders
    observing the same expired epoch then BOTH won."""
    store = wc.SDKClaimStore(allow_box_local=True)
    repo, key = "daniel-ospina/tortoise", _probe_key()
    cid = wc.claim_id(repo, key)
    try:
        base = wc.ClaimRecord(
            repo=repo, key=key, owner="pytest:1", lease_until=NOW + 3600, claimed_at=NOW
        )
        assert wc.acquire(store, base).rc == 0

        a = wc.ClaimRecord(
            repo=repo, key=key, owner="pytest:a", lease_until=NOW + 7200, epoch=2, claimed_at=NOW
        )
        b = wc.ClaimRecord(
            repo=repo, key=key, owner="pytest:b", lease_until=NOW + 7200, epoch=2, claimed_at=NOW
        )
        # Both believed the stored epoch was 1 and both try to write epoch 2.
        first = store.replace_if_epoch(cid, a, expected_epoch=1)
        second = store.replace_if_epoch(cid, b, expected_epoch=1)
        assert first is not None, "the first contender must win"
        assert second is None, "the second contender must lose — the epoch moved"
        stored = wc._parse(store.read(cid) or "")
        assert stored.owner == "pytest:a"
        assert stored.epoch == 2
    finally:
        with contextlib.suppress(Exception):
            store._rows("MATCH (n:Point {id:$id}) DETACH DELETE n", id=cid)


@pytest.mark.skipif(
    not os.environ.get("TORTOISE_DB_URI"),
    reason="needs a live graph (TORTOISE_DB_URI) — the fake-store tests cover the CAS logic",
)
def test_live_legacy_duplicate_nodes_read_as_one_claim_and_are_refused_on_write():
    """A graph written by the pre-MERGE version can hold two nodes for one id (that version
    used `create_point`, which does not enforce uniqueness — measured: 2 nodes). Reads must
    answer with one claim, and a write must REFUSE rather than pick one."""
    store = wc.SDKClaimStore(allow_box_local=True)
    repo, key = "daniel-ospina/tortoise", _probe_key()
    cid = wc.claim_id(repo, key)
    old = wc.ClaimRecord(
        repo=repo, key=key, owner="pytest:old", lease_until=NOW + 60, epoch=1, claimed_at=NOW
    )
    new = wc.ClaimRecord(
        repo=repo, key=key, owner="pytest:new", lease_until=NOW + 3600, epoch=2, claimed_at=NOW
    )
    try:
        for rec in (old, new):  # two nodes, one id — exactly the legacy shape
            store._rows(
                "CREATE (n:Point {id:$id, pointKind:$kind, repo:$repo, claim_epoch:$epoch, "
                "content:$content})",
                id=cid,
                kind=wc.CLAIM_KIND,
                repo=repo,
                epoch=rec.epoch,
                content=rec.content(),
            )
        assert wc._parse(store.read(cid) or "").owner == "pytest:new"
        listed = [r for r in store.query_repo(repo) if r["key"] == key]
        assert len(listed) == 1, listed
        assert listed[0]["epoch"] == 2
        with pytest.raises(wc.StoreUnanswerable) as excinfo:
            store.create(cid, old)
        assert "2 nodes" in str(excinfo.value)
    finally:
        with contextlib.suppress(Exception):
            store._rows("MATCH (n:Point {id:$id}) DETACH DELETE n", id=cid)


def test_ttl_zero_is_a_usage_error_not_an_instantly_expired_claim():
    """`--ttl 0` produced a claim that was already expired yet reported `acquired` rc 0, so a
    peer reclaimed it at once and also reported `acquired` — a double grant from one flag."""
    rc = wc.main(
        ["acquire", "--repo", "o/r", "--issue", "5052", "--session", "cell-t1", "--ttl", "0"],
        store=FakeStore(),
    )
    assert rc == wc.EXIT_USAGE


def test_the_documented_case_counts_match_what_this_file_collects():
    """The count of this file's cases is stated in several artefacts: the evidence README's
    inventory row and its two reproduction commands, the `durations` comment in
    `config/ci-surfaces.yml`, and the two carve-out comments that cite it — and every one of
    them was carried forward by hand: the PR shipped 40 / 46 / 49 at once (and a `52` here)
    against a file whose real count is COMPUTED below, in documents whose whole purpose is to
    be reproduced (review of #9487).

    So the numbers are COMPUTED here from this module's own test functions, and EVERY artefact
    above is asserted against them — including both carve-out comments, which the first
    version of this pin claimed to cover and did not (review of #9487 P2). Adding a test
    without re-deriving the docs fails this pin instead of shipping another stale count; the
    docstrings deliberately carry no literal case count so they cannot rot.

    The §6 pin also checks the ENV each command needs (review of #9487 P2, corrected in the
    review of PR #9537): the reproduction used to set `TORTOISE_DB_URI` via `test_lane.py uri`
    and then promise "the live legs skip" — unreachable from that command, since a set URI makes
    the legs RUN. Both forms are pinned now: the hermetic one must UNSET the URI (`env -u`) and
    carry `TORTOISE_TEST_CARVE_OUT=1`, and the full one must state the `total`."""
    import inspect

    module = sys.modules[__name__]
    funcs = [
        obj
        for obj in vars(module).values()
        if inspect.isfunction(obj)
        and obj.__name__.startswith("test_")
        and obj.__module__ == __name__
    ]
    assert funcs, "enumeration found no tests — the pin would be vacuous"

    def cases(fn: object) -> int:
        n = 1
        for mark in getattr(fn, "pytestmark", []):
            if mark.name == "parametrize":
                n *= len(mark.args[1])
        return n

    live = [fn for fn in funcs if any(m.name == "skipif" for m in getattr(fn, "pytestmark", []))]
    live_cases = sum(cases(fn) for fn in live)
    total = sum(cases(fn) for fn in funcs)
    hermetic = total - live_cases

    readme = (REPO_ROOT / "docs" / "evidence" / "2026-10-21-shared-coordination-memory" / "README.md").read_text()
    surfaces = (REPO_ROOT / "config" / "ci-surfaces.yml").read_text()

    # §3 — the inventory row
    row = re.findall(r"(\d+) fake-store tests \+ (\d+) live tests, (\d+)\s+collected", readme)
    assert row == [(str(hermetic), str(live_cases), str(total))], row
    # §6 — the reproduction's EXPECTED output, which a reader compares their own run against.
    # Two commands, two claims: the hermetic one must UNSET the store and set the carve-out, the
    # full one runs every leg. The env prefix is asserted, not just the count (review of #9487
    # P2) — and the NEGATIVE is asserted too (review of PR #9537): the carve-out only relaxes
    # the session URI GATE, it does not clear `TORTOISE_DB_URI`, and the live legs skip on URI
    # absence ALONE. So a hermetic command that merely inherits an exported URI reproduces
    # "90 passed", not "87 passed, 3 skipped" — the pin asserted the flag and stayed green on
    # exactly the state where the documented output cannot occur. The SKIP clause is asserted
    # as well: without it a line narrowed by `-k "not live"` satisfies the pin while no longer
    # proving the skip.
    expected = [ln for ln in readme.splitlines() if "test_work_claim.py -q" in ln]
    assert expected, "the reproduction command is gone"
    hermetic_lines = [ln for ln in expected if f"# {hermetic} passed at this head" in ln]
    assert hermetic_lines, expected
    assert any(
        "env -u TORTOISE_DB_URI" in ln
        and "TORTOISE_TEST_CARVE_OUT=1" in ln
        and f"{live_cases} skipped" in ln
        for ln in hermetic_lines
    ), expected
    assert not any("TORTOISE_DB_URI=" in ln for ln in expected), (
        "a §6 command EXPORTS a store URI — no URI may be set on either reproduction line, or "
        "the '3 skipped' line is unreachable and the live legs run on the SHARED container"
    )
    assert any(f"# {total} passed" in ln for ln in expected), expected
    # the timings row's own description of this file
    assert f"{total} tests: {hermetic} in-memory double + {live_cases} live-SDK" in surfaces
    # The TWO CARVE-OUT COMMENTS claim this count as their reason to buy the full matrix. The
    # first version of this pin named them and asserted neither, so all three stale figures
    # survived a round in which the file grew by eighteen arms (review of #9487 P2). Asserted
    # here — in BOTH files that carry the claim — so the claim and the pin cannot disagree again.
    selection_src = (REPO_ROOT / "tools" / "ci_selection.py").read_text()
    ci_pin_src = (REPO_ROOT / "tests" / "test_ci_selection.py").read_text()
    assert f"{hermetic} hermetic arms" in selection_src, (
        "the TOOL_CARVEOUTS comment in tools/ci_selection.py states a stale arm count"
    )
    assert f"{hermetic} hermetic arms" in ci_pin_src, (
        "the ci_selection pin's own comment states a stale arm count"
    )


# --------------------------------------------------------------------------------------
# The SHIPPED Cypher, spied on. The store seam IS the statement (there is no ORM to sit
# between the record and the graph), so the properties that no fake can model — what the
# `SET` does to a prop a PREVIOUS holder wrote, and what a fence that matches 0 rows MEANS —
# are pinned by capturing the statement the store emits and the params it binds.
# --------------------------------------------------------------------------------------
def _spy_store(rows: list):
    """An `SDKClaimStore` wired to a projection that records every (cypher, params) pair.

    `rows` is consumed one result_set per query; running out yields `[[1]]` (a harmless
    "one row moved"). `_db_uri` is a SHARED host, so no verb refuses it and no probe is made.
    """

    class _Result:
        def __init__(self, result_set):
            self.result_set = result_set

    class _Graph:
        def __init__(self, queued):
            self.queued = list(queued)
            self.calls: list[tuple[str, dict]] = []

        def query(self, cypher, params=None):
            self.calls.append((cypher, params or {}))
            return _Result(self.queued.pop(0) if self.queued else [[1]])

    class _Proj:
        def __init__(self, graph):
            self.g = graph

    class _SDK:
        _db_uri = "docker://:@shared.host:6379/g"

        def __init__(self, graph):
            self._graph = graph

        def _get_proj(self):
            return _Proj(self._graph)

    graph = _Graph(rows)
    return wc.SDKClaimStore(sdk=_SDK(graph)), graph


def test_a_capacity_field_the_new_record_does_not_carry_is_removed_not_left_stale():
    """Review of #9487 P1. `props()` OMITS an unsupplied capacity field on purpose (a
    fabricated `0` reads as a measured idle box), but `SET n += $props` can only ADD keys —
    so a reclaim or release over a node the previous holder wrote KEPT that holder's
    `ncpu`/`lpc`/`lanes` while the content line this same statement writes says
    `ncpu=absent`. The prop half then asserted a peer's measurement as this claim's capacity,
    and the record is the fleet's capacity map. `REMOVE` is what makes the abstention hold
    for both readers (verified on a live FalkorDB: `MATCH … REMOVE … SET n += $props` is
    accepted and the props are gone)."""
    store, graph = _spy_store([[[1]]])
    record = _record(epoch=3)
    assert (record.ncpu, record.lpc, record.lanes) == (None, None, None)
    store.replace_if_epoch("cid", record, expected_epoch=2)
    cypher, params = graph.calls[0]
    assert "REMOVE n.ncpu, n.lpc, n.lanes" in cypher, cypher
    assert not ({"ncpu", "lpc", "lanes"} & set(params["props"])), params["props"]
    # the removal happens in the SAME statement as the write, so no reader sees a half-record
    assert cypher.index("REMOVE") < cypher.index("SET n +="), cypher


def test_a_capacity_field_the_new_record_carries_is_kept_and_only_the_absent_ones_are_removed():
    """The other half: a supplied measurement must survive the transition, and must not be
    REMOVEd by a blanket rule."""
    store, graph = _spy_store([[[1]]])
    store.replace_if_epoch("cid", _record(epoch=3, ncpu=8), expected_epoch=2)
    cypher, params = graph.calls[0]
    removed = cypher.split("REMOVE ", 1)[1].split(" SET", 1)[0]
    assert removed == "n.lpc, n.lanes", cypher
    assert params["props"]["ncpu"] == 8, params["props"]


def test_a_fence_that_matches_nothing_because_the_epoch_prop_is_absent_refuses_rc2():
    """Review of #9487 P2. 0 rows is normally "a peer moved it" (rc 1, retryable). But a node
    whose `claim_epoch` prop is ABSENT, or still equal to the epoch we fenced on, cannot be
    explained by a peer's write — our own `WHERE` would have matched. Reporting it as a lost
    race told the caller to retry a command that can never succeed (rc 1, and `--force` goes
    through the same fence). It is a store this tool refuses to interpret."""
    store, _graph = _spy_store([[[0]], [[None]]])
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.replace_if_epoch("cid", _record(), expected_epoch=4)
    assert "fence cannot" in str(excinfo.value), str(excinfo.value)


def test_a_fence_that_matches_nothing_because_a_peer_moved_the_epoch_is_still_a_lost_race():
    """The distinction must not swallow the REAL race: a node now at a higher epoch is the
    peer write the fence exists to detect, and it stays rc 1."""
    store, _graph = _spy_store([[[0]], [[9]]])
    assert store.replace_if_epoch("cid", _record(), expected_epoch=4) is None


def test_a_fence_that_matches_nothing_because_the_node_is_gone_is_a_lost_race():
    """A released-then-deleted (or missing) node is a genuine move, not an inconsistent
    store: no row, no refusal."""
    store, _graph = _spy_store([[[0]], []])
    assert store.replace_if_epoch("cid", _record(), expected_epoch=4) is None


def test_a_listing_row_the_tool_cannot_read_refuses_instead_of_crashing_into_rc1():
    """Review of #9487 P2. The `query_repo` walk sat outside every handler, so a row of
    unexpected arity (a foreign/legacy `pointKind=work-claim` node) raised ValueError, which
    escaped `main()` as a traceback — exit 1, the rc this contract defines as EXIT_HELD. A
    `status --repo` crash then read as "busy" and an `acquire` crash as "held by a live
    claim": an undocumented rc no caller can tell from a real answer."""
    store, _graph = _spy_store([[["only-two", "columns"]]])
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.query_repo("daniel-ospina/tortoise")
    assert "listing row" in str(excinfo.value), str(excinfo.value)


def test_a_refusal_redacts_a_dsn_the_driver_quoted():
    """Review of #9487 P2. `store.target()` was the ONLY site that redacted, so the ERROR path
    — `_rows` wraps the driver exception, and a connection error routinely quotes the DSN it
    was handed — printed the password. Redaction now happens at the exception boundary, so no
    interpolation of `str(exc)` has to remember."""
    exc = wc.StoreUnanswerable("could not connect to docker://:p@ssw0rd@shared.host:6379/g")
    assert "p@ssw0rd" not in str(exc), str(exc)
    assert "docker://***@shared.host:6379/g" in str(exc), str(exc)


def test_the_json_output_cannot_carry_a_password_a_store_put_in_its_error(capsys):
    """The boundary half: even a store whose error text carries the DSN cannot get the
    password onto stdout, which is the stream a controller captures."""

    class LeakyStore(FakeStore):
        def read(self, cid):
            raise wc.StoreUnanswerable("connect failed: docker://:s3cret@shared.host:6379/g")

    rc = wc.main(
        ["status", "--repo", "daniel-ospina/tortoise", "--issue", "5052", "--json"],
        store=LeakyStore(),
    )
    out = capsys.readouterr().out
    assert rc == wc.EXIT_UNANSWERABLE, rc
    assert "s3cret" not in out, out
    assert "docker://***@shared.host" in out, out


def test_force_without_a_reason_is_a_usage_error_even_when_the_store_is_unset(monkeypatch):
    """Review of #9487 P2. The `--force`/`--reason` shape used to be validated AFTER the
    store was opened, so with an unset/unreachable/box-local store it returned rc 2 ("the
    store could not answer", RETRYABLE) instead of rc 3 (malformed invocation, never retry).
    A controller would retry a command that can never succeed. Invocation shape is decided
    before any store work."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    rc = wc.main(["release", "--repo", "o/r", "--issue", "5052", "--force", "--session", "cell-t1"])
    assert rc == wc.EXIT_USAGE == 3, rc


# ── the repo half of the claim identity (review of #9487 P2) ─────────────────────


@pytest.mark.parametrize(
    "repo",
    [
        "tortoise",  # a bare name: no owner half
        "TORTOISE",  # ...and a case variant of it
        "daniel-ospina/",  # empty name half
        "/tortoise",  # empty owner half
        "daniel-ospina/tortoise/extra",  # two slashes: not a repo
        " ",  # nothing at all
    ],
)
def test_a_repo_that_is_not_owner_name_is_a_usage_error_not_an_empty_board(repo):
    """⛔ `--repo` is HALF THE CLAIM IDENTITY and used to be taken verbatim (`required=True`,
    nothing else). A bare name or a case variant then computed a DIFFERENT `claim_id` for the
    same unit of work, so two boxes both won their `MERGE` and both reported `acquired` — the
    duplicate dispatch this tool exists to prevent — and `status --repo` answered
    `0 claim record(s)` at rc 0 over a store that held the claim, indistinguishable from
    "nobody holds anything" (review of #9487 P2). The key half already refuses rather than let
    two units collide; the repo half now does the same, as EXIT_USAGE (3) — never the
    retryable rc 2."""
    store = FakeStore()
    rc = wc.main(["status", "--repo", repo], store=store)
    assert rc == wc.EXIT_USAGE == 3, (repo, rc)
    assert rc != wc.EXIT_OK, f"{repo!r} was accepted and answered an empty ledger at rc 0"


def test_the_repo_case_variant_is_the_same_claim_through_the_cli(capsys):
    """End to end over the CLI: acquire under `Daniel-Ospina/TORTOISE`, read it back under the
    canonical `daniel-ospina/tortoise`. Before the fix these were two ids — the second box
    acquired the same unit of work at rc 0 and the listing saw neither — so both the `held`
    assertion and the `1 claim record(s)` assertion red on the pre-fix code."""
    store = FakeStore()
    assert (
        wc.main(
            ["acquire", "--repo", "Daniel-Ospina/TORTOISE", "--issue", "5052", "--session", "cell-t1"],
            store=store,
        )
        == wc.EXIT_OK
    )
    # a second box, canonical spelling: it must be REFUSED as held (one id, not two)
    rc = wc.main(
        ["--json", "acquire", "--repo", "daniel-ospina/tortoise", "--issue", "5052", "--session", "cell-t2"],
        store=store,
    )
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == wc.EXIT_HELD == 1, (rc, out)
    assert out["status"] == "held"
    # the repo-wide listing sees exactly ONE record, not zero and not two
    rc = wc.main(["status", "--repo", "daniel-ospina/tortoise", "--json"], store=store)
    listed = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == wc.EXIT_OK, (rc, listed)
    assert listed["reason"].startswith("1 claim record(s)"), listed
    # ...and `release` by the holder (canonical spelling) closes it
    assert (
        wc.main(
            ["release", "--repo", "daniel-ospina/tortoise", "--issue", "5052", "--session", "cell-t1"],
            store=store,
        )
        == wc.EXIT_OK
    )


def test_claim_id_is_one_id_per_unit_of_work_whatever_the_spelling():
    """The CAS is only a CAS if every box derives the SAME id. `claim_id` digested the repo as
    typed, so this was a spelling contest, not a fence — pinned here at the pure-function
    level so the invariant cannot be quietly dropped from the CLI path later.

    The KEY half gets the identical treatment (review of PR #9537): it was digested as typed
    too, so a case variant or a trailing space was a second id for the same unit of work."""
    canonical = wc.claim_id("daniel-ospina/tortoise", "issue-5052")
    assert wc.claim_id("Daniel-Ospina/TORTOISE", "issue-5052") == canonical
    assert wc.claim_id("  daniel-ospina/tortoise  ", "issue-5052") == canonical
    # ...and the key half no longer is either
    assert wc.claim_id("daniel-ospina/tortoise", "ISSUE-5052") == canonical
    assert wc.claim_id("daniel-ospina/tortoise", "issue-5052 ") == canonical
    assert wc.claim_id("daniel-ospina/tortoise", " Issue-5052\t") == canonical
    # ...including the one spelling class that is objectively decidable: a DECIMAL body.
    # `issue-7` vs `issue-0007` was two ids — and two grants — for issue 7 (review round 3).
    assert wc.claim_id("daniel-ospina/tortoise", "issue-0007") == wc.claim_id(
        "daniel-ospina/tortoise", "issue-7"
    )
    assert wc.claim_id("daniel-ospina/tortoise", "PR-0005052") == wc.claim_id(
        "daniel-ospina/tortoise", "pr-5052"
    )
    # ...while a NON-decimal body stays opaque: the live legs mint `issue-<12 hex>` per run and
    # that uniqueness is what stops two concurrent runs fencing each other (#5084).
    assert wc.canonical_key("issue-0a3f5c9d1b2e") == "issue-0a3f5c9d1b2e"
    # ...and the live legs' per-run key is CANONICAL AT THE MINT, so the all-digit-body collapse
    # cannot make the stored spelling differ from the one a leg compares against (round 4).
    assert _probe_key("012345678901") == "issue-12345678901"
    # the digest is still a real digest: a DIFFERENT unit is a different id
    assert wc.claim_id("daniel-ospina/tortoise", "issue-5053") != canonical
    assert wc.claim_id("daniel-ospina/tortoise", "pr-5052") != canonical
    assert wc.claim_id("daniel-ospina/agent-infra", "issue-5052") != canonical
    assert re.fullmatch(r"[0-9a-f]{20}-[0-9a-f]{12}", canonical), canonical


@pytest.mark.parametrize(
    "key",
    [
        "",  # nothing at all
        "5052",  # no kind prefix
        "branch-1",  # a kind this tool does not key on
        "issue-",  # empty body
        "-5052",  # empty kind
        "issue-50 52",  # a space INSIDE the key: two spellings of one unit
        "issue-50/52",  # a slash would make the key a path
        "issue-" + "9" * wc.MAX_KEY_LEN,  # longer than the stored-key ceiling
    ],
)
def test_a_key_that_is_not_issue_or_pr_shaped_is_a_usage_error_not_a_second_claim(key):
    """The key is half of the claim identity, so a malformed one must REFUSE rather than mint
    its own id (review of PR #9537). Before the fix every one of these produced a perfectly
    valid, perfectly distinct `claim_id` — the same unit of work under an unvalidated spelling,
    which is the double grant the repo half of this PR removes."""
    with pytest.raises(ValueError):
        wc.canonical_key(key)
    with pytest.raises(ValueError):
        wc.claim_id("daniel-ospina/tortoise", key)


def test_the_key_half_is_the_same_claim_through_the_library_entry_points():
    """Review of PR #9537. `main()` canonicalised the key (`normalise_key`), but `acquire`,
    `release` and `status` are PUBLIC entry points — a store split or a peer script calls them
    directly — and they digested `record.key` / `key` as typed. Measured pre-fix: keys
    `issue-5052`, `ISSUE-5052`, `issue-5052 ` and `""` produced FOUR ids and FOUR
    `rc 0 / acquired` outcomes for one unit of work. Every assertion below reds on the pre-fix
    code, and the first one reds as a SECOND GRANT rather than as an exception."""
    store = FakeStore()
    first = wc.acquire(store, _record(owner="cell-1:100", key="ISSUE-5052"), now=NOW)
    assert first.rc == wc.EXIT_OK == 0, first
    assert first.status == "acquired", first
    # a different SPELLING of the same key is the same claim: refused as held, not re-granted
    second = wc.acquire(store, _record(owner="cell-4:200", key=" issue-5052 "), now=NOW)
    assert second.rc == wc.EXIT_HELD == 1, second
    assert second.status == "held", second
    assert second.owner == "cell-1:100", second
    # ...and so is a ZERO-PADDED DECIMAL spelling of the same key (review round 3): pre-fix
    # this was a second id, so it re-granted the unit and split the board in two.
    padded = wc.acquire(store, _record(owner="cell-5:300", key="issue-0005052"), now=NOW)
    assert padded.rc == wc.EXIT_HELD == 1, padded
    assert padded.owner == "cell-1:100", padded
    # exactly one row exists, addressed by the canonical id
    assert list(store.points) == [wc.claim_id("daniel-ospina/tortoise", "issue-5052")]
    # `status` under a variant spelling reads that row (pre-fix: `free` — the answer a
    # controller dispatches on)
    seen = wc.status(store, "daniel-ospina/tortoise", "Issue-5052", now=NOW)
    assert seen.rc == wc.EXIT_OK == 0, seen
    assert seen.status == "held", seen
    assert seen.owner == "cell-1:100", seen
    # ...and `release` under a variant spelling releases IT (pre-fix: "nothing to release"
    # at rc 0 while the claim lived on)
    closed = wc.release(
        store, "daniel-ospina/tortoise", "ISSUE-5052", owner="cell-1:100", now=NOW + 10
    )
    assert closed.rc == wc.EXIT_OK == 0, closed
    assert closed.status == "released", closed
    after = wc.status(store, "daniel-ospina/tortoise", "issue-5052", now=NOW + 10)
    assert after.status == "expired", after


def test_a_malformed_key_is_refused_before_the_store_is_touched():
    """A key that is not `<issue|pr>-<body>` is the caller's contract, so it raises from the
    entry points rather than being written: `acquire` canonicalises before it reads or writes,
    so the store sees nothing at all."""
    store = FakeStore()
    with pytest.raises(ValueError):
        wc.acquire(store, _record(key=""), now=NOW)
    with pytest.raises(ValueError):
        wc.release(store, "daniel-ospina/tortoise", "5052", owner="box-a:111", now=NOW)
    with pytest.raises(ValueError):
        wc.status(store, "daniel-ospina/tortoise", "branch-1", now=NOW)
    assert store.points == {}, store.points
    assert store.writes == 0, store.writes


def test_a_fence_that_matches_nothing_on_an_unreadable_epoch_refuses_rc2():
    """Review of #9487 P2. The fence's follow-up read does `int(seen)` on a value the STORE
    chose the type of. A list-valued or textual `claim_epoch` from a foreign writer raised
    ValueError, which escaped `main()` as exit 1 — the rc this contract defines as
    EXIT_HELD, i.e. "held by a live claim", on the one branch whose whole job is refusing to
    guess. An unreadable epoch is a refusal (rc 2), never a race."""
    store, _graph = _spy_store([[[0]], [["not-a-number"]]])
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.replace_if_epoch("cid", _record(), expected_epoch=4)
    assert "unreadable claim_epoch" in str(excinfo.value), str(excinfo.value)


def test_a_content_value_that_is_not_a_string_refuses_on_the_read_path():
    """The single-key read path had the same gap the listing guard closes: `read()` returned
    whatever the store held, and `_tokens` then iterated it — a non-string `content` raised
    outside every handler and exited 1 = EXIT_HELD instead of refusing (review of #9487 P2)."""
    store, _graph = _spy_store([[[123]]])
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.read("cid")
    assert "int content" in str(excinfo.value), str(excinfo.value)


def test_a_listing_content_value_that_is_not_a_string_refuses():
    """The listing guard checked arity and the epoch but not `content`, so a foreign node
    whose `content` is an int reached `_parse` and crashed the same way."""
    store, _graph = _spy_store([[[["cid", 123, 1]]]])
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.query_repo("daniel-ospina/tortoise")
    assert "listing row" in str(excinfo.value), str(excinfo.value)


def test_a_long_driver_message_cannot_leak_a_password_past_the_truncation():
    """Review of #9487 P2. `_redact_uri` needs the `@` that ends the userinfo, so redacting
    AFTER the 200-char cut leaked the password's head whenever the DSN ended past the cut
    (`docker://:S3cr3t-Pa55word-Tha`). Redaction now happens BEFORE truncation."""
    password = "S3cr3t-Pa55word-That-Is-Far-Too-Long-To-Survive-Truncation"

    class ExplodingGraph:
        def query(self, cypher, params=None):
            raise RuntimeError(
                "x" * 150 + f" connect failed: docker://:{password}@shared.host:6379/g"
            )

    class ExplodingProj:
        g = ExplodingGraph()

    class ExplodingSDK:
        _db_uri = "docker://:@shared.host:6379/g"

        def _get_proj(self):
            return ExplodingProj()

    store = wc.SDKClaimStore(sdk=ExplodingSDK())
    with pytest.raises(wc.StoreUnanswerable) as excinfo:
        store.read("cid")
    message = str(excinfo.value)
    assert "S3cr3t" not in message, message
    assert "Pa55word" not in message, message
