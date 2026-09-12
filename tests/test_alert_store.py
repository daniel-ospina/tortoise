"""Tests for tortoise/alert_store.py — the per-incident dedup state machine."""

from __future__ import annotations

import json

import pytest

from tortoise.alert_store import AlertStore
from tortoise.hosted_backup import MemoryStorage


class _FakeChannels:
    def __init__(self):
        self.issues: dict[int, str] = {}      # number -> title
        self.closed: list[int] = []
        self.comments: list[tuple[int, str]] = []
        self.telegram: list[str] = []
        self.search_calls: list[str] = []
        self.fail_file = False
        self.fail_push = False
        self._next = 1

    def file_issue(self, title, body):
        if self.fail_file:
            raise RuntimeError("github down")
        n = self._next
        self._next += 1
        self.issues[n] = title
        return n

    def close_issue(self, number, comment=None):
        self.closed.append(number)
        if comment:
            self.comments.append((number, comment))

    def search_open(self, kind, team_id=""):
        self.search_calls.append((kind, team_id))
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t
            and (team_id == "" or t.endswith(f" — {team_id}"))
            and n not in self.closed
        ]

    def push_telegram(self, text):
        if self.fail_push:
            raise RuntimeError("telegram down")
        self.telegram.append(text)


def _store(channels, storage=None, issue_open=None, writer="unspecified") -> AlertStore:
    return AlertStore(
        storage or MemoryStorage(),
        file_issue=channels.file_issue,
        close_issue=channels.close_issue,
        search_open=channels.search_open,
        push_telegram=channels.push_telegram,
        issue_open=issue_open,
        default_writer=writer,
        repo="daniel-ospina/tortoise",
        assignee="daniel-ospina",
    )


def test_open_once_files_issue_and_pushes():
    ch = _FakeChannels()
    store = _store(ch)
    assert store.open_incident("STALE", "team_x") is True
    assert len(ch.issues) == 1
    assert "[DR] STALE" in list(ch.issues.values())[0]  # noqa: RUF015
    assert len(ch.telegram) == 1
    assert "STALE" in ch.telegram[0]


def test_repeat_open_reuses_incident():
    """While the incident is open, repeats never file again (per-incident dedup)."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    store.open_incident("STALE", "team_x")
    store.open_incident("STALE", "team_x")
    assert len(ch.issues) == 1
    assert len(ch.telegram) == 1


def test_resolve_closes_and_delete_to_resolve():
    """Recovery closes the issue + deletes the dedup object; a later recurrence
    is a NEW incident (new issue number)."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    first = list(ch.issues)[0]  # noqa: RUF015
    assert store.resolve_incident("STALE", "team_x") is True
    assert ch.closed == [first]
    assert len(ch.telegram) == 2  # open + resolved
    assert "resolved" in ch.telegram[1].lower()
    # Recurrence → new incident, new issue.
    store.open_incident("STALE", "team_x")
    assert len(ch.issues) == 2
    assert store.resolve_incident("STALE", "team_x") is True


def test_adopter_does_not_double_file():
    """A 412 loser adopts the winner's issue_number and never files again."""
    ch = _FakeChannels()
    s1 = _store(ch)
    s2 = _store(ch)
    s1.open_incident("STALE")
    s2.open_incident("STALE")  # loser — must adopt, not file
    assert len(ch.issues) == 1


def test_create_then_die_window_is_not_silent():
    """Winner died between create and backfill: the adopter finds a placeholder,
    becomes the filer (via GH-search fallback), and files exactly once."""
    ch = _FakeChannels()
    storage = MemoryStorage()

    # First actor opens but its filing fails (GH down) — placeholder remains.
    ch.fail_file = True
    s1 = _store(ch, storage)
    s1.open_incident("STALE")
    assert ch.issues == {}
    ch.fail_file = False

    # Second actor adopts the placeholder; GH-search finds nothing; becomes filer.
    s2 = _store(ch, storage)
    s2.open_incident("STALE")
    assert len(ch.issues) == 1


def test_suppression_pauses_kind():
    ch = _FakeChannels()
    store = _store(ch)
    store._storage.upload(
        "ops/suppression.json",
        json.dumps({"STALE": {"until": "2099-01-01T00:00:00+00:00"}}).encode(),
    )
    assert store.open_incident("STALE") is False
    assert ch.issues == {}
    assert ch.telegram == []


def test_pending_push_retried_by_daemon():
    ch = _FakeChannels()
    ch.fail_push = True
    store = _store(ch)
    store.open_incident("STALE")  # issue filed, push parked
    assert ch.telegram == []
    pending = store._storage.list("ops/pending-push/")
    assert len(pending) == 1
    ch.fail_push = False
    assert store.retry_pending() == 1
    assert len(ch.telegram) == 1
    assert store._storage.list("ops/pending-push/") == []


# ── Channel independence (#673) ─────────────────────────────────────────────

def test_telegram_failure_does_not_block_github_issue():
    """When Telegram is down, the GitHub issue MUST still be filed.
    The incident lifecycle treats Telegram as best-effort — a failed push
    parks a pending-push blob and the issue filing proceeds normally."""
    ch = _FakeChannels()
    ch.fail_push = True  # Telegram down
    store = _store(ch)
    assert store.open_incident("STALE", "team_x") is True
    # Issue was filed.
    assert len(ch.issues) == 1
    assert "[DR] STALE" in list(ch.issues.values())[0]  # noqa: RUF015
    # Telegram was NOT pushed (parked instead).
    assert ch.telegram == []
    assert len(store._storage.list("ops/pending-push/")) == 1


def test_github_failure_does_not_block_telegram_push():
    """When GitHub is down, Telegram MUST still push.
    The issue filing fails but Telegram push still fires (best-effort)
    on the next polling cycle when the adopter picks up the placeholder."""
    ch = _FakeChannels()
    ch.fail_file = True  # GitHub down
    store = _store(ch)
    # open_incident tries to file → fails → still returns True (became filer
    # but couldn't file or push yet — placeholder state).
    store.open_incident("STALE")
    # Neither channel fired yet (both blocked by GH-down in this cycle).
    assert ch.issues == {}
    assert ch.telegram == []
    # On the next poll, a second call (adopter) picks up the placeholder,
    # GH-search finds nothing, files the issue, THEN pushes Telegram.
    ch.fail_file = False
    store.open_incident("STALE")
    assert len(ch.issues) == 1
    assert len(ch.telegram) == 1


def test_all_alert_kinds_push_telegram_on_open():
    """Every alert kind in the taxonomy sends a Telegram message on open."""
    # Alert kinds from the DR runbook taxonomy (docs/ops/registry-backup-dr.md).
    kinds = [
        "STALE",
        "NEVER_BACKED_UP",
        "METADATA_LOST",
        "BACKUP_SET_MISSING",
        "DRIVER_DOWN",
        "R2_DOWN",
        "ALERTER_DOWN",
        "APP_DOWN",
        "WATCHER_DOWN",
        "LIVENESS_NO_WORK",
        "SIZE_GUARD_ABORT",
        "DATA_LOSS_CANDIDATE",
    ]
    for kind in kinds:
        ch = _FakeChannels()
        store = _store(ch)
        assert store.open_incident(kind) is True
        assert len(ch.telegram) == 1, f"{kind} did not push Telegram"
        assert kind in ch.telegram[0], f"{kind} name missing from Telegram text"
        assert len(ch.issues) == 1, f"{kind} did not file GitHub issue"


def test_resolved_telegram_carries_issue_number():
    """The resolved Telegram message references the GitHub issue number."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    issue_num = list(ch.issues)[0]  # noqa: RUF015
    ch.telegram.clear()  # clear the open message
    store.resolve_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    resolved_text = ch.telegram[0]
    assert "resolved" in resolved_text.lower()
    assert str(issue_num) in resolved_text


def test_dedup_no_repeat_telegram_same_incident():
    """While an incident is open, repeated open_incident calls do NOT
    send duplicate Telegram messages."""
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    # Repeated calls — no new messages.
    for _ in range(5):
        store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 1
    # Resolve and reopen → new incident, new message.
    store.resolve_incident("STALE", "team_x")
    assert len(ch.telegram) == 2  # open + resolved
    store.open_incident("STALE", "team_x")
    assert len(ch.telegram) == 3  # new incident = new open message


def test_telegram_and_github_independent_resolve():
    """On resolve, both channels fire independently — a Telegram push failure
    does not prevent the GitHub issue close, and vice versa."""
    # Case A: Telegram fails on resolve — issue still closes.
    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE")
    ch.telegram.clear()
    ch.fail_push = True
    assert store.resolve_incident("STALE") is True
    assert len(ch.closed) == 1  # Issue closed.
    assert ch.telegram == []     # Telegram parked.
    assert len(store._storage.list("ops/pending-push/")) == 1

    # Case B: GitHub fails on resolve — Telegram still sends.
    ch2 = _FakeChannels()
    store2 = _store(ch2)
    store2.open_incident("STALE")
    ch2.telegram.clear()
    # Simulate GitHub close failure by monkeypatching the close callable.
    original_close = ch2.close_issue  # noqa: F841
    def failing_close(number, comment=None):
        raise RuntimeError("github api down")
    ch2.close_issue = failing_close
    assert store2.resolve_incident("STALE") is True  # still resolves
    assert len(ch2.telegram) == 1
    assert "resolved" in ch2.telegram[0].lower()
    # Dedup object is deleted even if issue close failed (delete-to-resolve
    # runs regardless).
    assert store2._storage.list("ops/alerts/STALE/") == []


# ── #673 review P2: pending-push TTL + incident check ─────────────────────

def test_stale_pending_push_discarded_on_ttl():
    """A pending push older than 24h is discarded, not retried (#673 P2)."""
    from datetime import datetime, timedelta, timezone

    ch = _FakeChannels()
    storage = MemoryStorage()
    # park a push at 25h ago
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)  # noqa: UP017
    import hashlib
    digest = hashlib.sha256(b"ops/alerts/STALE/_.json").hexdigest()[:16]
    storage.upload(
        f"ops/pending-push/{digest}.json",
        json.dumps({
            "key": "ops/alerts/STALE/_.json",
            "text": "stale alert",
            "created_at": old_ts.isoformat(),
        }).encode(),
    )
    store = _store(ch, storage)
    # retry_pending must discard it (TTL expired), not push.
    assert store.retry_pending() == 0
    assert ch.telegram == []
    assert storage.list("ops/pending-push/") == []


def test_resolved_incident_pending_push_not_fired():
    """A pending push whose incident dedup key is gone (resolved) is
    discarded, not fired (#673 P2)."""
    from datetime import datetime, timezone

    ch = _FakeChannels()
    storage = MemoryStorage()
    # park a fresh push referencing an incident key that does NOT exist
    import hashlib
    digest = hashlib.sha256(b"ops/alerts/STALE/team_x.json").hexdigest()[:16]
    storage.upload(
        f"ops/pending-push/{digest}.json",
        json.dumps({
            "key": "ops/alerts/STALE/team_x.json",
            "text": "orphan push",
            "created_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        }).encode(),
    )
    store = _store(ch, storage)
    # retry_pending must discard it (incident resolved), not push.
    assert store.retry_pending() == 0
    assert ch.telegram == []
    assert storage.list("ops/pending-push/") == []


def test_search_fallback_is_subject_scoped():
    """#2313 Task 4: create-then-die adoption via GH-search must match the
    incident's OWN subject. A kind-only search would let team_b's adopter bind
    team_a's pre-existing open issue number — and resolving team_b would then
    close team_a's issue (silent-loss cross-talk)."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    # Pre-existing open STALE for team_a (issue #1 on the board).
    assert store.open_incident("STALE", "team_a") is True
    assert len(ch.issues) == 1
    # team_b's winner died between create and backfill: its dedup key exists
    # as an issue_number-less placeholder (the exact create-then-die window).
    storage.upload(
        "ops/alerts/STALE/team_b.json",
        json.dumps({"kind": "STALE", "team_id": "team_b", "detail": {},
                    "filed_at": "2026-08-08T00:00:00+00:00",
                    "issue_number": None, "telegram_pushed": False}).encode(),
    )
    # Adopter for team_b: the search must be scoped to team_b (no match — the
    # only open issue belongs to team_a), so team_b files its OWN issue.
    store.open_incident("STALE", "team_b")
    assert ("STALE", "team_b") in ch.search_calls
    titles = list(ch.issues.values())
    assert len(titles) == 2
    assert any("team_a" in t for t in titles)
    assert any("team_b" in t for t in titles)
    # Resolving team_b closes ONLY team_b's issue (#2); team_a's (#1) stays
    # open (the channels fake marks closed rather than popping issues).
    store.resolve_incident("STALE", "team_b")
    assert ch.closed == [2]
    store.resolve_incident("STALE", "team_a")
    assert ch.closed == [2, 1]


# ── #2844: the platform-scoped sentinel is written by TWO implementations ─────
# Canonical spelling is `ops/alerts/{KIND}/_.json` — written by BOTH this store
# and (since #2844) the bash DR driver, so one incident has ONE create-once
# point. `.../global.json` is the driver's PRE-#2844 spelling, kept as a legacy
# alias: every read path consults it and every resolve deletes it, so objects
# already in R2 are adopted and cleaned up rather than stranded holding a
# closed issue's number.

_CANONICAL_KEY = "ops/alerts/R2_DOWN/_.json"
_DRIVER_KEY = "ops/alerts/R2_DOWN/global.json"  # legacy alias spelling


def test_platform_incident_key_contract():
    """Pin the exact key contract (#2844).

    The fix is a string agreement between two languages: the bash driver's
    `alert_key()` and this store's `_key()` MUST produce the same object name,
    or the condition silently regains two create-once points. Both sides pin
    their own literal (this test; `registry-cron.test.sh` cases 29/57/58), so a
    rename on either side fails loudly instead of drifting back into two pens.
    """
    store = _store(_FakeChannels())
    assert store._key("R2_DOWN", "") == _CANONICAL_KEY
    assert set(store._keys("R2_DOWN", "")) == {_CANONICAL_KEY, _DRIVER_KEY}
    # Subject-scoped incidents have exactly ONE key — never an alias, and never
    # the platform sentinel (#2375).
    assert store._keys("STALE", "team_a") == ("ops/alerts/STALE/team_a.json",)
    assert store._key("STALE", "team_a") == "ops/alerts/STALE/team_a.json"


def _driver_sentinel(storage, issue_number, writer=None):
    """Seed the R2 object the bash DR driver's `file_alert` writes.

    Shape mirrors `.github/scripts/registry-cron.sh` (`kind`, `issue_number`,
    `filed_at` with a `date -u +%FT%TZ` timestamp, plus `writer` since #3127),
    under the legacy `global` alias so the alias-consulting paths are the ones
    exercised.
    """
    payload = {
        "kind": "R2_DOWN",
        "issue_number": issue_number,
        "filed_at": "2026-09-10T00:00:00Z",
    }
    if writer is not None:
        payload["writer"] = writer
    storage.upload(_DRIVER_KEY, json.dumps(payload).encode())


def test_resolve_deletes_the_driver_sentinel_alias():
    """#2844: resolving must delete EVERY sentinel for the incident, not just the
    one this store happens to spell.

    Asymmetric resolve is the defect: the driver's `global.json` outlived the
    close, so it still carried the number of an issue that was already closed.
    The driver's `file_alert` 412 branch reads that object, sees a non-open
    issue_number, finds no open issue and files a NEW one — an incident that was
    resolved pages again, and the duplicate is closed only on the next sweep.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    # An unrelated subject's sentinel must survive — otherwise `== []` below
    # would also pass if the resolve wiped the whole ops/alerts/ prefix.
    storage.upload(
        "ops/alerts/STALE/team_x.json",
        json.dumps({"kind": "STALE", "team_id": "team_x", "issue_number": 3}).encode(),
    )
    _driver_sentinel(storage, issue_number=7)

    assert store.resolve_incident("R2_DOWN") is True
    assert ch.closed == [7]
    assert storage.list("ops/alerts/") == ["ops/alerts/STALE/team_x.json"]


def test_open_adopts_the_driver_sentinel_instead_of_filing():
    """#2844: a store-side open must SEE the driver's sentinel.

    Without cross-spelling adoption each writer wins its own create-once, so a
    condition detected by both files two issues. Asserted on the OBSERVABLE
    consequences of adopting: no second sentinel is created, no issue is filed,
    the GH-search fallback is never consulted, and no Telegram is pushed.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    _driver_sentinel(storage, issue_number=11)

    assert store.open_incident("R2_DOWN") is False
    assert ch.issues == {}  # no second issue for one condition
    # The decisive assertion: our own canonical sentinel was never created. An
    # implementation that created it and only noticed the sibling afterwards
    # would leave a second create-once point — the defect itself.
    assert storage.list("ops/alerts/") == [_DRIVER_KEY]
    assert ch.search_calls == []  # adoption short-circuits the search fallback
    assert ch.telegram == []  # "stay silent" — adopting must not page again


def test_subject_scoped_resolve_never_touches_another_subject():
    """The #2375 invariant the alias set must NOT break.

    Aliasing exists only for subject-less incidents. A team-scoped resolve has
    exactly one key: it must not delete a sibling subject's sentinel, and it
    must not sweep the platform-scoped one either.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    storage.upload(
        "ops/alerts/STALE/team_a.json",
        json.dumps({"kind": "STALE", "team_id": "team_a", "issue_number": 3}).encode(),
    )
    storage.upload(
        "ops/alerts/STALE/team_b.json",
        json.dumps({"kind": "STALE", "team_id": "team_b", "issue_number": 4}).encode(),
    )
    storage.upload("ops/alerts/STALE/_.json", json.dumps({"kind": "STALE", "issue_number": 5}).encode())

    assert store.resolve_incident("STALE", "team_a") is True
    assert ch.closed == [3]
    assert "ops/alerts/STALE/team_b.json" in storage.list("ops/alerts/")
    assert "ops/alerts/STALE/_.json" in storage.list("ops/alerts/")
    assert "ops/alerts/STALE/team_a.json" not in storage.list("ops/alerts/")


def test_platform_resolve_closes_the_duplicate_filed_by_the_other_writer():
    """#2844: if BOTH writers filed before the aliasing fix shipped, the two
    issues are one condition and resolve must close both — otherwise the orphan
    stays open forever (no sentinel references it any more).
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    storage.upload(
        "ops/alerts/R2_DOWN/_.json",
        json.dumps({"kind": "R2_DOWN", "issue_number": 21}).encode(),
    )
    _driver_sentinel(storage, issue_number=22)

    assert store.resolve_incident("R2_DOWN") is True
    # Deterministic close order: _keys() iterates PLATFORM_ALIASES ("_", "global").
    assert ch.closed == [21, 22]
    assert storage.list("ops/alerts/") == []


def test_full_cross_writer_cycle_files_once_then_pages_again():
    """#2844 end-to-end: the ORDERED driver→store→resolve→recurrence cycle.

    The defect was an ordering disagreement between two writers, so per-half
    unit tests cannot catch drift between the halves. This sequences the real
    lifecycle and asserts the invariants at each stage: exactly one issue while
    the condition persists, one close on recovery, and — crucially — a genuine
    recurrence pages AGAIN after recovery (delete-to-resolve, the #2796 class).

    Direction B (this store opens, the driver later detects) is owned by
    `.github/scripts/registry-cron.test.sh` cases 21/22 — not duplicated here.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    # 1. The driver got there first.
    _driver_sentinel(storage, issue_number=11)
    # 2. The watcher detects the same condition — it must adopt, not file.
    assert store.open_incident("R2_DOWN") is False
    assert ch.issues == {}
    assert ch.telegram == []
    assert storage.list("ops/alerts/") == [_DRIVER_KEY]
    # 3. Recovery: close the driver's issue, drop every spelling.
    assert store.resolve_incident("R2_DOWN") is True
    assert ch.closed == [11]
    assert storage.list("ops/alerts/") == []
    # 4. A RECURRENCE is a NEW incident with a NEW issue — delete-to-resolve
    #    must not let the resolved sentinel swallow it.
    assert store.open_incident("R2_DOWN") is True
    assert list(ch.issues) == [1]  # the fake's first filing — the driver's #11 was seeded, not filed
    assert len(ch.telegram) == 2  # resolved (cycle 1) + reopened (cycle 2)


# ── #3127: sentinel liveness + resolution authority ─────────────────────────
#
# Two rules, both about not believing a sentinel more than the evidence allows:
#   1. The sentinel is the dedup LOCK; the ISSUE is the record of whether the
#      incident is still live. A sentinel naming a CLOSED issue is stale.
#   2. Recovery is evidence-gated: only the writer whose probes cover a kind's
#      recovery condition may declare it recovered (single-writer principle).


def test_open_refiles_when_the_sentinel_names_a_closed_issue():
    """#3127: a sentinel holding a CLOSED issue's number must not swallow the
    recurrence.

    Adopting on `issue_number` truthiness alone made `open_incident` a permanent
    no-op for any stranded sentinel naming a closed issue — a live fault that
    pages once and never again, on the one leg (driver disabled) where nobody
    else can repair it. Pre-fix `main` re-filed here.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7)
    store = _store(ch, storage, issue_open=lambda n: n != 7)

    assert store.open_incident("R2_DOWN") is True
    assert len(ch.issues) == 1, "the recurrence must be re-filed"
    assert ch.telegram, "the human must be told"


def test_open_adopts_while_the_recorded_issue_is_open():
    """The other half: an OPEN issue is still the dedup — no duplicate filing."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7)
    ch.issues[7] = "[DR] R2_DOWN"
    store = _store(ch, storage, issue_open=lambda n: n == 7)

    assert store.open_incident("R2_DOWN") is False
    assert list(ch.issues) == [7]
    assert ch.telegram == []


def test_open_trusts_the_sentinel_when_issue_state_cannot_be_read():
    """No positive evidence of closure, no re-file — an API blip is not a close."""
    def boom(number):
        raise RuntimeError("github 502")

    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7)
    assert _store(ch, storage, issue_open=boom).open_incident("R2_DOWN") is False
    assert len(ch.issues) == 0


def test_open_without_a_state_reader_never_refiles():
    """Unwired callers keep the historical adopt-on-number path (no re-filing)."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7)
    assert _store(ch, storage).open_incident("R2_DOWN") is False
    assert len(ch.issues) == 0


def test_become_filer_writes_the_canonical_key():
    """#3127: backfill the CANONICAL key, never the adopted legacy spelling.

    Writing the number back into `global.json` leaves `_.json` free, so the
    driver's `r2_put_once _.json` still succeeds and files a second issue —
    two create-once points again, in the create-then-die window the store
    exists to recover.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    storage.upload(_DRIVER_KEY, json.dumps({"kind": "R2_DOWN", "issue_number": None}).encode())
    store = _store(ch, storage)

    assert store.open_incident("R2_DOWN") is True
    canonical = json.loads(storage.download(_CANONICAL_KEY))
    assert canonical["issue_number"] == 1, "the canonical key must hold the number"
    # Provenance is absent when the caller declared none, so KIND_OWNERS governs
    # (an undeclared writer buys no authority).
    assert "writer" not in canonical
    with pytest.raises(KeyError):
        storage.download(_DRIVER_KEY)


def test_resolve_refuses_a_kind_owned_by_another_writer():
    """#3127: the watcher cannot clear R2_DOWN — its probe covers the wrong thing.

    A reachability check cannot distinguish "R2 unreachable" from "the key
    cannot ListObjects", so the watcher's all-clear may be false. Pre-fix it
    deleted only its own spelling; once both writers shared one key it would
    close the driver's issue and the driver would re-file — one duplicate pair
    per hour, with a real storage fault recorded as resolved.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7, writer="driver")
    ch.issues[7] = "[DR] R2_DOWN"
    store = _store(ch, storage, writer="watcher")

    assert store.resolve_incident("R2_DOWN") is False
    assert ch.closed == []
    assert storage.download(_DRIVER_KEY), "the sentinel must survive the refusal"


def test_resolve_allowed_for_the_declared_owner():
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7, writer="driver")
    store = _store(ch, storage, writer="driver")

    assert store.resolve_incident("R2_DOWN") is True
    assert ch.closed == [7]
    with pytest.raises(KeyError):
        storage.download(_DRIVER_KEY)


def test_resolve_allowed_for_a_sentinel_the_caller_itself_opened():
    """A writer may always clear its OWN observation.

    Without this, the driver-disabled leg (where the watcher is the only
    observer) would open R2_DOWN and strand it open forever, since no driver
    run would ever come along to close it.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7, writer="watcher")
    store = _store(ch, storage, writer="watcher")

    assert store.resolve_incident("R2_DOWN") is True
    assert ch.closed == [7]


def test_owned_kind_still_resolves_for_its_owner():
    """The guard must not break the watcher's own kinds (no false refusal)."""
    ch = _FakeChannels()
    store = _store(ch, MemoryStorage(), writer="watcher")
    store.open_incident("STALE", "team_a")
    assert store.resolve_incident("STALE", "team_a") is True
    assert store.resolve_incident("R2_DOWN") is False  # nothing open, still no raise


def test_resolve_tolerates_a_corrupt_issue_number():
    """#3127: a malformed sentinel must not abort the watcher poll.

    `int()` outside the per-close guard raised ValueError out of
    `resolve_incident`, skipping the heartbeat write and retry_pending on every
    cycle — a permanently wedged poll from one bad object.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    storage.upload(
        _DRIVER_KEY, json.dumps({"kind": "R2_DOWN", "issue_number": "abc"}).encode(),
    )
    store = _store(ch, storage, writer="driver")

    assert store.resolve_incident("R2_DOWN") is True  # must not raise
    assert ch.closed == []


def test_non_owner_that_adopts_an_issue_does_not_gain_authority():
    """#3127 round 2 (P1): adopting someone else's issue must not confer authority.

    The watcher creates its own placeholder, then the GH-search fallback finds
    the DRIVER's already-filed open issue and adopts its number. Stamping the
    watcher as the sentinel's `writer` made the provenance carve-out pass, so
    the watcher could close the driver's `R2_DOWN` on its weaker reachability
    probe — the false recovery the guard exists to stop, one layer deeper. This
    reproduced against the previous commit.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    ch.issues[50] = "[DR] R2_DOWN"  # filed by the driver, still open
    store = _store(ch, storage, issue_open=lambda n: True, writer="watcher")

    store.open_incident("R2_DOWN")
    sentinel = json.loads(storage.download(_CANONICAL_KEY))
    assert sentinel["issue_number"] == 50, "the driver's issue is adopted, not re-filed"
    assert len(ch.issues) == 1
    assert "writer" not in sentinel, "an adopter must not claim provenance"
    # No sentinel recorded an announcement for #50 (this store created the
    # sentinel itself, so `telegram_pushed` is False), therefore the incident is
    # announced ONCE — the create-then-die window must not leave it silent
    # (round 3 P1). A second poll must not announce it again.
    assert len(ch.telegram) == 1
    store.open_incident("R2_DOWN")
    assert len(ch.telegram) == 1, "the persisted flag stops a re-announcement"

    # ...and the authority check falls through to KIND_OWNERS: R2_DOWN is the
    # driver's, and the watcher cannot clear it.
    assert store.resolve_incident("R2_DOWN") is False
    assert ch.closed == []


def test_adopted_issue_already_announced_is_not_re_announced():
    """The other half: a sentinel that RECORDS the announcement is not repeated.

    The driver writes `telegram_pushed: true` alongside its backfill, so a store
    that adopts the driver's issue stays silent — the round-2 behaviour, now
    driven by persisted state instead of by who happened to file.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    ch.issues[50] = "[DR] R2_DOWN"
    storage.upload(_DRIVER_KEY, json.dumps({
        "kind": "R2_DOWN", "issue_number": 50,
        "filed_at": "2026-09-12T00:00:00Z", "writer": "driver",
        "telegram_pushed": True,
    }).encode())
    store = _store(ch, storage, issue_open=lambda n: True, writer="watcher")

    assert store.open_incident("R2_DOWN") is False
    assert ch.telegram == []
    assert len(ch.issues) == 1


def test_deleted_issue_is_treated_as_closed_not_as_a_blip():
    """#3127 round 2 (P1): 404/410 on the issue-state read means GONE.

    `issue_is_open_checked` returns False for 404/410 so a deleted issue cannot
    swallow the recurrence, while every other failure still counts as open so a
    blip never re-files a live incident. (The bash driver's `gh_issue_open`
    already treated 404 as definitive; the two now agree.)
    """
    from tortoise.github_issue import GithubApiError, issue_is_open_checked

    calls = []

    def _raise_404(method, url, token, payload=None, **kw):
        calls.append(url)
        raise GithubApiError(404, "gone")

    import tortoise.github_issue as gi

    original = gi._request
    gi._request = _raise_404
    try:
        assert issue_is_open_checked("r/r", "t", 7) is False
    finally:
        gi._request = original
    assert calls

    # A deleted-issue sentinel therefore re-files rather than being adopted.
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7)
    store = _store(ch, storage, issue_open=lambda n: False)
    assert store.open_incident("R2_DOWN") is True
    assert len(ch.issues) == 1


def test_kind_owner_contract_with_driver():
    """The bash `kind_owner` must agree with KIND_OWNERS (#3127).

    Two writers, two languages, one policy. If the maps drift, one side starts
    closing incidents its probes never covered — the defect the guard exists to
    prevent — so the mapping is pinned across the language boundary.
    """
    import re
    from pathlib import Path

    from tortoise.alert_store import KIND_OWNERS

    sh = (Path(__file__).resolve().parent.parent
          / ".github/scripts/registry-cron.sh").read_text(encoding="utf-8")
    body = sh.split("kind_owner() {", 1)[1].split("esac", 1)[0]
    parsed: dict[str, str] = {}
    pending: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if re.fullmatch(r"[A-Z0-9_|]+\)", line):
            pending = line[:-1].split("|")
            continue
        m = re.fullmatch(r"echo ([a-z]+) ;;", line)
        if m and pending:
            parsed.update(dict.fromkeys(pending, m.group(1)))
            pending = []
    assert parsed, "kind_owner() did not parse — the bash shape changed"
    assert parsed == KIND_OWNERS

    # No driver call site may resolve a kind the driver does not own: the
    # guard would silently no-op it, and the intent ("the driver has evidence
    # for this") would be wrong. The self-heal loop is the one variable-shaped
    # call site, so its kind list is checked explicitly.
    from tortoise.alert_store import kind_owner

    for kind in re.findall(r"resolve_global ([A-Z][A-Z0-9_]*)\b", sh):
        assert kind_owner(kind) in ("driver", "unspecified"), (
            f"registry-cron.sh resolves {kind}, which is owned by {kind_owner(kind)}")
    loops = re.findall(r"for kind in ([A-Z_ ]+); do", sh)
    assert loops, "the self-heal kind loop moved — re-verify the guard covers it"
    for loop in loops:
        for kind in loop.split():
            assert kind_owner(kind) in ("driver", "unspecified"), (
                f"the self-heal loop resolves {kind}, owned by {kind_owner(kind)}")
