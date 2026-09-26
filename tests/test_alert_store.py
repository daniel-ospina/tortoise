"""Tests for tortoise/alert_store.py — the per-incident dedup state machine."""

from __future__ import annotations

import json
from datetime import UTC

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
        self.fail_search = False
        self.fail_close = False
        self.search_impl = None
        self._next = 1

    def file_issue(self, title, body):
        if self.fail_file:
            raise RuntimeError("github down")
        n = self._next
        self._next += 1
        self.issues[n] = title
        return n

    def close_issue(self, number, comment=None):
        if self.fail_close:
            raise RuntimeError("github unreachable")
        self.closed.append(number)
        if comment:
            self.comments.append((number, comment))

    def search_open(self, kind, org_id=""):
        if self.fail_search:  # #3029: a transport/rate-limit failure
            raise RuntimeError("search: rate limit exceeded")
        self.search_calls.append((kind, org_id))
        return [
            n for n, t in self.issues.items()
            if f"[DR] {kind}" in t
            and (org_id == "" or t.endswith(f" — {org_id}"))
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
        "APP_DOWN",
        "WATCHER_DOWN",
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
        json.dumps({"kind": "STALE", "org_id": "team_b", "detail": {},
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


# ── #3029 fail-closed search ─────────────────────────────────────────────────


def test_search_failure_defers_filing_fail_closed():
    """#3029: a FAILED search is not an EMPTY search.

    The search is the only dedup left when the create-once object is a
    placeholder, so filing on a failed search risks a duplicate. Filing is
    deferred instead: the placeholder keeps ``issue_number: null`` and the next
    poll re-enters the same branch and retries.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()

    ch.fail_search = True
    store = _store(ch, storage)

    # #3029 fail-closed merged with #3820's tri-state: the placeholder IS the
    # record, so the store reports the on-record fact (FILED), not the old
    # bool's "this call filed an issue" reading. The #3029 guarantee is the
    # DEFERRAL asserted below — nothing is filed and the placeholder survives.
    from tortoise.alert_store import OpenOutcome
    assert store.open_incident_state("STALE") is OpenOutcome.FILED
    assert ch.issues == {}, "a failed search must never file"
    state = json.loads(storage.download("ops/alerts/STALE/_.json"))
    assert state["issue_number"] is None, "the placeholder must survive for the retry"

    # Next poll: the search is healthy again and finds nothing → we file.
    ch.fail_search = False
    assert store.open_incident("STALE") is True
    assert len(ch.issues) == 1


def test_a_filed_incident_is_adopted_from_the_object_without_any_search():
    """The create-once object is the AUTHORITY: once it carries an issue number,
    a repeat is a no-op that never consults GitHub. (Pinned because the review
    showed the earlier version of this test passed even with the fail-closed
    change reverted — the object short-circuited before the search, so the test
    proved nothing about the fail-closed path.)"""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    assert store.open_incident("STALE") is True
    filed = len(ch.issues)
    searches_before = len(ch.search_calls)

    ch.fail_search = True          # a dead/rate-limited search changes nothing
    assert store.open_incident("STALE") is False
    assert len(ch.issues) == filed, "no duplicate"
    assert len(ch.search_calls) == searches_before, "the object short-circuits the search"
    assert store.resolve_incident("STALE") is True
    assert ch.closed, "resolution still works while the search is down"


# ── #3033: the documented taxonomy must match the emitted one ─────────────────


_ALERT_SOURCES = ("tortoise/alert_store.py", "tortoise/backup_watcher.py",
                  "tortoise/backup_sweep.py", "tortoise/hosted_api.py",
                  # #3981's operator-facing kinds are emitted by these modules and
                  # documented in the runbook, so the #3033 drift scan must see
                  # their writers rather than read them as unemittable.
                  "tortoise/cohort_cost.py", "tortoise/operator_alert.py")
_DRIVER = ".github/scripts/registry-cron.sh"
def _dead_kinds_documented() -> set[str]:
    """The kinds the runbook itself declares writer-less (#3033).

    Parsed from the runbook rather than hardcoded: a revert of the runbook's
    "Kinds with no writer" section must fail the drift test below, not silently
    re-exempt the kinds.
    """
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent
           / "docs/ops/registry-backup-dr.md").read_text()
    section = doc.split("### Kinds with no writer", 1)
    assert len(section) == 2, "the 'Kinds with no writer' section must exist"
    return {m.group(1) for m in re.finditer(r"\|\s*`([A-Z][A-Z0-9_]+)`\s*\|", section[1])}


def _documented_kinds() -> set[str]:
    """Kinds in the runbook's triage table (first column, ALL_CAPS tokens)."""
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent
           / "docs/ops/registry-backup-dr.md").read_text()
    table = doc.split("## Alert taxonomy + triage", 1)[1].split("### How incidents CLOSE", 1)[0]
    return {m.group(1) for m in re.finditer(r"^\|\s*([A-Z][A-Z0-9_]{3,})\s*\|", table, re.M)}


def _emittable_kinds() -> set[str]:
    """Kinds any producer can actually open/resolve — scanned from the emitters.

    Deliberately mechanical: a kind that exists only in a doc or a test must not
    count as "monitored".
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    kinds: set[str] = set()
    for rel in _ALERT_SOURCES:
        text = (root / rel).read_text()
        kinds |= set(re.findall(r'(?:open_incident|resolve_incident)\(\s*"([A-Z][A-Z0-9_]+)"', text))
        # The watcher's per-leg containment helper takes the op as a string
        # (`_alert_leg("open_incident", "STALE", key)`) — the call-site pattern
        # above cannot see it, so a refactor into that helper must not silently
        # read as "this kind lost its writer".
        kinds |= set(re.findall(r'_alert_leg\(\s*"(?:open|resolve)_incident",\s*"([A-Z][A-Z0-9_]+)"', text))
        kinds |= set(re.findall(r'"kind":\s*"([A-Z][A-Z0-9_]+)"', text))
        # Kinds named by a module constant (e.g. `_DRILL_FAILED_KIND`), which the
        # call-site scan cannot see.
        kinds |= set(re.findall(r'_KIND\s*=\s*"([A-Z][A-Z0-9_]+)"', text))
    driver = (root / _DRIVER).read_text()
    kinds |= set(re.findall(r'\bfile_alert\s+([A-Z][A-Z0-9_]+)\s', driver))
    kinds |= set(re.findall(r'\bresolve_global\s+([A-Z][A-Z0-9_]+)\s', driver))
    return kinds


def test_documented_kinds_have_a_writer():
    """#3033: every kind in the runbook's triage table must have a writer.

    A documented-but-unemittable kind is corrosive: the operator believes a
    condition will page when it cannot, and the test suite gains a green
    assertion with no production referent.
    """
    documented = _documented_kinds()
    emittable = _emittable_kinds()
    dead = _dead_kinds_documented()
    assert dead == {"ALERTER_DOWN", "LIVENESS_NO_WORK"}, (
        f"the runbook's writer-less list changed: {dead} — update this test's expectation"
    )
    assert "STALE" in documented and "STALE" in emittable, "the scan itself must work"
    offenders = sorted(documented - emittable - dead)
    assert not offenders, (
        f"documented but never emitted: {offenders} — give them a writer or move "
        "them to the 'Kinds with no writer' section"
    )


def test_dead_kinds_are_documented_as_dead():
    """#3033: the two unemittable kinds must be marked NOT EMITTED, not left
    looking live in the triage table."""
    from pathlib import Path

    doc = (Path(__file__).resolve().parent.parent
           / "docs/ops/registry-backup-dr.md").read_text()
    dead_section = doc.split("### Kinds with no writer", 1)
    assert len(dead_section) == 2, "the 'Kinds with no writer' section must exist"
    body = dead_section[1]
    for kind in _dead_kinds_documented():
        assert f"`{kind}`" in body, f"{kind} must be listed as writer-less"
    table = doc.split("## Alert taxonomy + triage", 1)[1].split("### How incidents CLOSE", 1)[0]
    for kind in _dead_kinds_documented():
        row = [ln for ln in table.splitlines() if ln.startswith(f"| {kind} |")]
        assert row, f"{kind} must keep a triage row"
        assert "NOT EMITTED" in row[0], f"{kind}'s triage cell must say NOT EMITTED"


# ── #3030 review: the bounded-open-set API + resolve failure semantics ───────


def test_open_subjects_lists_open_incidents_without_a_read_per_candidate():
    """One LIST per kind, so the sweep endpoint never issues an R2 read per graph.

    Both platform spellings can appear: `_` (what `_key()` writes for an empty
    subject — the sweep's four kinds) and a literal `global` (the restore-drill
    path files that one). A caller matching a platform candidate must accept
    either; the sweep endpoint now does."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    assert store.open_incident("P0_GUARD_FAIL", "team_a") is True
    assert store.open_incident("P0_GUARD_FAIL", "team_b:g_x") is True
    assert store.open_incident("P0_GUARD_FAIL") is True          # platform → "_"
    assert store.open_incident("STALE", "team_a") is True

    assert store.open_subjects("P0_GUARD_FAIL") == {"team_a", "team_b:g_x", "_"}
    assert store.open_subjects("STALE") == {"team_a"}
    assert store.open_subjects("NEVER_BACKED_UP") == set()


def test_open_subjects_fails_safe_on_a_listing_error():
    """A listing we could not perform must resolve NOTHING (fail-closed): the
    empty set is the safe answer for a destructive follow-up."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    assert store.open_incident("STALE", "team_a") is True

    def _boom(prefix):
        raise RuntimeError("R2 list outage")

    storage.list = _boom  # type: ignore[method-assign]
    assert store.open_subjects("STALE") == set()


def test_resolve_failure_leaves_the_incident_open_and_announces_nothing():
    """REVIEW P1: a swallowed close used to push '✅ DR resolved' and delete the
    object while the issue stayed OPEN — the next poll re-files, adopts the still
    open issue and pushes '🚨 DR alert': a ✅/🚨 flip every poll and a false
    all-clear. A failed close now resolves NOTHING (retried next poll)."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    assert store.open_incident("STALE", "team_a") is True
    telegram_before = len(ch.telegram)

    ch.fail_close = True

    with pytest.raises(RuntimeError):
        store.resolve_incident("STALE", "team_a")   # RAISES: False ≠ failure
    assert storage.download("ops/alerts/STALE/team_a.json"), "the object must survive"
    assert len(ch.telegram) == telegram_before, "no false 'resolved' announcement"
    # The incident is still tracked, so a recurrence is not re-filed either.
    assert store.open_incident("STALE", "team_a") is False


def test_a_failed_delete_after_a_successful_close_does_not_swallow_the_recurrence():
    """Cycle-2 review P1: the close succeeded, so the issue is CLOSED — if the
    dedup object then survives carrying that issue_number, the next recurrence is
    adopted by the closed issue and silently swallowed (#2796/#2844 class). The
    object is tombstoned (issue_number cleared) so a recurrence re-files."""
    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)
    assert store.open_incident("STALE", "team_a") is True
    number = max(ch.issues)

    def _delete_boom(key):
        raise RuntimeError("R2 delete outage")

    storage.delete = _delete_boom  # type: ignore[method-assign]

    assert store.resolve_incident("STALE", "team_a") is True   # the close DID happen
    assert number in ch.closed
    tombstones = json.loads(storage.download("ops/alerts/STALE/team_a.json"))
    assert not tombstones.get("issue_number"), "the stale issue_number must be cleared"
    # A recurrence therefore re-files instead of being adopted by a closed issue.
    assert store.open_incident("STALE", "team_a") is True
    assert len(ch.issues) == 2


# ── final-cycle review P1: bounded retry after a failed close ───────────────


def _clocked_store(channels, storage, clock: list):
    return AlertStore(
        storage,
        file_issue=channels.file_issue,
        close_issue=channels.close_issue,
        search_open=channels.search_open,
        push_telegram=channels.push_telegram,
        repo="daniel-ospina/tortoise",
        assignee="daniel-ospina",
        now=lambda: clock[0],
        close_cooldown_min=60.0,
    )


def test_a_failed_close_is_not_retried_until_the_cooldown_expires():
    """Final-cycle review P1: without a backoff, a permanently failing close is
    retried every poll — two GitHub writes each, plus a duplicate audit comment
    (the comment POST precedes the state PATCH) — with no cap. The failure is
    recorded and the next attempt is skipped until the window passes, and the
    skip RAISES `CloseCooldown` so callers keep the subject pending."""
    from datetime import datetime, timedelta

    from tortoise.alert_store import CloseCooldown

    ch = _FakeChannels()
    storage = MemoryStorage()
    clock = [datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)]
    store = _clocked_store(ch, storage, clock)
    assert store.open_incident("STALE", "team_a") is True
    ch.fail_close = True
    closes = {"n": 0}
    real_close = ch.close_issue

    def _counting_close(number, comment=None):
        closes["n"] += 1
        return real_close(number, comment)

    store._close = _counting_close

    with pytest.raises(RuntimeError):
        store.resolve_incident("STALE", "team_a")
    assert closes["n"] == 1

    # Inside the window: skipped, and the close is NOT attempted again.
    with pytest.raises(CloseCooldown):
        store.resolve_incident("STALE", "team_a")
    assert closes["n"] == 1, "the cooldown must not re-attempt the close"

    # Past the window: attempted again.
    clock[0] = clock[0] + timedelta(minutes=61)
    with pytest.raises(RuntimeError):
        store.resolve_incident("STALE", "team_a")
    assert closes["n"] == 2

    # A successful close clears the incident (delete-to-resolve), and the
    # recorded failure goes with the object.
    ch.fail_close = False
    clock[0] = clock[0] + timedelta(minutes=61)
    assert store.resolve_incident("STALE", "team_a") is True
    with pytest.raises(KeyError):
        storage.download("ops/alerts/STALE/team_a.json")


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
    their own literal (this test; `registry-cron.test.sh` cases 29/64/65/67), so
    a rename on either side fails loudly instead of drifting back into two pens.
    """
    store = _store(_FakeChannels())
    assert store._key("R2_DOWN", "") == _CANONICAL_KEY
    assert set(store._keys("R2_DOWN", "")) == {_CANONICAL_KEY, _DRIVER_KEY}
    # Subject-scoped incidents have exactly ONE key — never an alias, and never
    # the platform sentinel (#2375).
    assert store._keys("STALE", "team_a") == ("ops/alerts/STALE/team_a.json",)
    assert store._key("STALE", "team_a") == "ops/alerts/STALE/team_a.json"
    # #2844 (round-7 P2): a REAL subject literally named `global` (or `_`) is
    # not the subject-less platform incident. It keeps its OWN single key and is
    # never an alias SET: aliasing it to the platform spelling would give one
    # condition two create-once points (bash `alert_key` -> `_.json` vs this
    # store -> `global.json`), consuming two sentinels and filing two issues.
    assert store._keys("STALE", "global") == ("ops/alerts/STALE/global.json",)
    assert store._key("STALE", "global") == "ops/alerts/STALE/global.json"
    assert store._keys("STALE", "_") == ("ops/alerts/STALE/_.json",)
    assert store._key("STALE", "_") == "ops/alerts/STALE/_.json"


def test_real_subject_named_global_never_cross_adopts_a_platform_incident():
    """A real subject `global` and the subject-less platform incident stay apart.

    #2844 (round-7 P2): the two writers must agree on the KEY, but that
    agreement must not be bought by merging two DIFFERENT incidents. A real
    subject literally named `global` and the subject-less platform incident are
    distinct: neither may adopt the other's sentinel, or recovery closes the
    wrong issue while one condition silently loses its incident.
    """
    # A real subject's sentinel (it carries its subject) must not be adopted by
    # the platform incident — that would swallow the real team's issue #42.
    real = MemoryStorage()
    real.upload(
        "ops/alerts/R2_DOWN/global.json",
        json.dumps({"kind": "R2_DOWN", "team_id": "global", "issue_number": 42}).encode(),
    )
    ch = _FakeChannels()
    store = _store(ch, storage=real)
    assert store.open_incident("R2_DOWN", "") is True
    assert list(ch.issues.values()) == ["[DR] R2_DOWN"]

    # …and the mirror: the real subject must not adopt the platform sentinel
    # (which predates the `team_id` field, so it carries none).
    platform = MemoryStorage()
    platform.upload(
        "ops/alerts/R2_DOWN/_.json",
        json.dumps({"kind": "R2_DOWN", "issue_number": 77}).encode(),
    )
    ch2 = _FakeChannels()
    store2 = _store(ch2, storage=platform)
    assert store2.open_incident("R2_DOWN", "global") is True
    assert list(ch2.issues.values()) == ["[DR] R2_DOWN — global"]


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


class _RefilingStorage(MemoryStorage):
    """MemoryStorage whose sentinel is RE-FILED by another writer mid-operation.

    The concurrent writer #2844 is about: after ``reads_before`` reads of
    ``key``, the stored body is replaced with one carrying a NEW
    ``issue_number`` and ``filed_at`` — the re-file landing between the state
    snapshot a compare-and-delete guard took and its confirming re-read.
    """

    def __init__(
        self,
        key: str,
        *,
        reads_before: int = 1,
        new_issue: int = 99,
        new_filed_at: str = "2026-09-11T00:00:00Z",
    ) -> None:
        super().__init__()
        self._watched = key
        self._reads_before = reads_before
        self._new_issue = new_issue
        self._new_filed_at = new_filed_at
        self.refiles = 0

    def download(self, key: str) -> bytes:
        data = super().download(key)
        if key == self._watched:
            self.refiles += 1
            if self.refiles > self._reads_before:
                body = json.loads(data)
                body["issue_number"] = self._new_issue
                body["filed_at"] = self._new_filed_at
                data = json.dumps(body).encode()
                self.upload(key, data)
        return data


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


def test_resolve_leaves_a_sentinel_another_writer_re_filed():
    """#2844 compare-and-delete: resolve must not delete a sentinel that was
    re-filed while it worked.

    `resolve_incident` reads the alias states, closes their issues and pushes
    Telegram — several GitHub round trips. A writer that re-files the incident
    with a NEW issue number during them would have its fresh sentinel deleted:
    the issue it names is orphaned (no sentinel references it — the #3030 class)
    and the next detection re-files a duplicate (#2844). The confirming re-read
    must see the change and leave the new sentinel alone.
    """
    ch = _FakeChannels()
    storage = _RefilingStorage(_DRIVER_KEY)
    _driver_sentinel(storage, issue_number=7)
    store = _store(ch, storage)

    assert store.resolve_incident("R2_DOWN") is True
    reads_during_resolve = storage.refiles
    # The issue the store legitimately read is closed...
    assert ch.closed == [7]
    # ...but the re-filed sentinel SURVIVES, still naming its (new) open issue,
    # and that new issue was never closed by this resolve.
    assert _DRIVER_KEY in storage.list("ops/alerts/")
    assert json.loads(storage.download(_DRIVER_KEY))["issue_number"] == 99
    assert 99 not in ch.closed
    assert reads_during_resolve == 2, "the guard must re-read the sentinel to compare"


def test_open_leaves_a_sentinel_another_writer_re_filed():
    """#2844 compare-and-delete: `open_incident` must not drop a sentinel that
    was re-filed while it checked the recorded issue's liveness.

    A stale sentinel (its recorded issue is not live) is cleared so the
    incident can re-file on the canonical key. But `_issue_is_live` is a GitHub
    round trip: a writer that re-files with a NEW issue number during it would
    have its fresh sentinel deleted, orphaning that issue (#3030) and re-filing
    a duplicate (#2844). `_forget` must leave the changed object alone.
    """
    ch = _FakeChannels()
    storage = _RefilingStorage(_DRIVER_KEY)
    _driver_sentinel(storage, issue_number=7)
    store = _store(ch, storage, issue_open=lambda n: False)  # the recorded issue is dead

    assert store.open_incident("R2_DOWN") is True
    # The re-filed sentinel survives with its NEW number...
    assert _DRIVER_KEY in storage.list("ops/alerts/")
    assert json.loads(storage.download(_DRIVER_KEY))["issue_number"] == 99
    # ...so the store does not treat the alias as free: it files under the
    # CANONICAL key, keeping one create-once point for the incident.
    assert _CANONICAL_KEY in storage.list("ops/alerts/")


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


def test_resolve_refuses_even_a_sentinel_the_caller_itself_opened():
    """#3127: ownership decides, and there is no provenance exception.

    The "but I filed it myself" carve-out was removed after three review rounds
    found three ways the self-asserted filed-by note went wrong — stamped by an
    adopter, surviving a placeholder that was never filed, outliving the issue
    it described — each letting a non-owner clear a kind it has no evidence
    about. The accepted cost: with the driver disabled, a watcher-filed R2_DOWN
    stays open until a driver run clears it. That is correct — if the driver is
    off, the evidence that storage recovered does not exist.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    _driver_sentinel(storage, 7, writer="watcher")
    store = _store(ch, storage, writer="watcher")

    assert store.resolve_incident("R2_DOWN") is False
    assert ch.closed == []
    assert storage.download(_DRIVER_KEY), "the sentinel survives the refusal"


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
    # This store created its own sentinel, so the incident is announced ONCE —
    # the create-then-die window must not leave it silent (round 3 P1). The
    # second poll short-circuits on the ADOPTION path (the sentinel now names
    # the still-open issue #50), so it does not announce again.
    assert len(ch.telegram) == 1
    store.open_incident("R2_DOWN")
    assert len(ch.telegram) == 1, "an already-open adopted incident is not re-announced"

    # ...and the authority check falls through to KIND_OWNERS: R2_DOWN is the
    # driver's, and the watcher cannot clear it.
    assert store.resolve_incident("R2_DOWN") is False
    assert ch.closed == []


def test_adopted_open_incident_is_not_re_announced_on_a_later_poll():
    """An adopted incident whose issue is still OPEN is not announced again.

    The sentinel carries a LIVE issue number, so the second `open_incident`
    short-circuits on the record that is actually load-bearing — the issue's
    state — and not on any announcement flag (that flag was removed; see
    `_become_filer`). The driver's issue is adopted, the store stays silent,
    and no second issue is filed.
    """
    ch = _FakeChannels()
    storage = MemoryStorage()
    ch.issues[50] = "[DR] R2_DOWN"
    storage.upload(_DRIVER_KEY, json.dumps({
        "kind": "R2_DOWN", "issue_number": 50,
        "filed_at": "2026-09-12T00:00:00Z", "writer": "driver",
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


# ── #3820 cycle-7: the tri-state resolve fact ──────────────────────────────

def test_resolve_state_reports_the_three_facts():
    """``resolve_incident_state`` reports WHICH fact holds (#3820 cycle-7).

    ABSENT (nothing stored), RESOLVED (closed + deleted), and SKIPPED_FRESH
    (an incident IS open but is at/after the caller's ``before`` bound, so it
    is left untouched). The analytics sink needs the distinction because its
    process state is driven by the fact, not by a guess about a boolean.

    RED mutation: return ``ABSENT`` (or ``RESOLVED``) at the ``before`` skip
    instead of ``SKIPPED_FRESH`` → this reds on the SKIPPED_FRESH assertion;
    or delete the object on the skip → it reds on
    ``storage.list("ops/alerts/") != []``.
    """
    from datetime import datetime, timedelta

    from tortoise.alert_store import ResolveOutcome

    ch = _FakeChannels()
    store = _store(ch)

    # ABSENT — no incident at all.
    assert store.resolve_incident_state("STALE", "team_x") is ResolveOutcome.ABSENT

    store.open_incident("STALE", "team_x")
    key = store._key("STALE", "team_x")
    filed_at = datetime.fromisoformat(
        json.loads(store._storage.download(key))["filed_at"])

    # SKIPPED_FRESH — filed at/after the bound: untouched (no close, no push,
    # and the dedup object is still there, so the incident is still open).
    assert store.resolve_incident_state(
        "STALE", "team_x", before=filed_at) is ResolveOutcome.SKIPPED_FRESH
    assert ch.closed == []
    assert store._storage.list("ops/alerts/") != []

    # RESOLVED — the same incident, now strictly before the bound.
    assert store.resolve_incident_state(
        "STALE", "team_x",
        before=filed_at + timedelta(seconds=1)) is ResolveOutcome.RESOLVED
    assert len(ch.closed) == 1
    assert store._storage.list("ops/alerts/") == []


def test_skipped_fresh_does_not_assert_the_sentinel_is_live():
    """#3127 + #3820 cycle-7: ``SKIPPED_FRESH`` also covers a REFUSED sentinel.

    The outcome means "an incident is ON RECORD and this attempt left it
    untouched", NOT "an incident is open". The #3127 authority refusal fires
    before — and independently of — any liveness read, and ``_alias_states``
    never consults the issue state, so a non-owner's refusal against a sentinel
    naming a CLOSED issue reports ``SKIPPED_FRESH`` exactly as a fresh one
    does. The caller must still stay pending (nothing was resolved), but must
    not read this outcome as proof of a live incident.

    RED mutation: return ``RESOLVED``/``ABSENT`` at the #3127 refusal → this
    reds on the ``is SKIPPED_FRESH`` assertion; delete the sentinel on refusal
    → it reds on ``storage.download(_DRIVER_KEY)``.
    """
    from tortoise.alert_store import ResolveOutcome

    ch = _FakeChannels()
    storage = MemoryStorage()
    # The driver's sentinel names issue 7 … which is already CLOSED.
    _driver_sentinel(storage, 7, writer="driver")
    ch.issues[7] = "[DR] R2_DOWN"
    ch.closed.append(7)
    # The watcher does not own R2_DOWN, so its resolve must refuse.
    store = _store(ch, storage, writer="watcher",
                   issue_open=lambda n: n not in ch.closed)

    assert store.resolve_incident_state("R2_DOWN") is ResolveOutcome.SKIPPED_FRESH
    assert ch.closed == [7], (
        "the refusal must not close anything — the sentinel already names a "
        "closed issue and a refusal resolves nothing")
    assert storage.download(_DRIVER_KEY), (
        "the sentinel must survive the refusal — SKIPPED_FRESH left it untouched")


def test_resolve_incident_stays_a_bool_for_a_skipped_fresh():
    """``resolve_incident`` keeps its bool contract (#3820 cycle-7).

    Eleven other callers branch on ``if store.resolve_incident(...)``, so a
    truthy tri-state enum returned from HERE would make SKIPPED_FRESH — and
    ABSENT — read as a successful resolve. The fact lives in
    ``resolve_incident_state``; this method is its ``is RESOLVED``.

    RED mutation: ``return self.resolve_incident_state(...)`` (hand back the
    enum) → the SKIPPED_FRESH call is truthy and this reds on ``is False``.
    """
    from datetime import datetime

    ch = _FakeChannels()
    store = _store(ch)
    store.open_incident("STALE", "team_x")
    key = store._key("STALE", "team_x")
    filed_at = datetime.fromisoformat(
        json.loads(store._storage.download(key))["filed_at"])

    assert store.resolve_incident("STALE", "team_x", before=filed_at) is False
    assert store._storage.list("ops/alerts/") != [], "SKIPPED_FRESH must not delete"
    # …and the same call without the bound still resolves it.
    assert store.resolve_incident("STALE", "team_x") is True


def test_open_incident_state_reports_the_three_facts_and_keeps_the_bool():
    """#3820 cycle-8 P2-2 — ``open_incident_state`` names WHICH fact holds.

    ``open_incident``'s ``False`` conflates a DEDUP hit (the object already
    exists — an incident IS on record) with a SUPPRESSED kind (no issue and NO
    object). The analytics alert gate needs the distinction, and a caller that
    re-asks ``suppression_active`` at a later instant can disagree with the
    decision the call already made. The tri-state is decided from ONE read of
    the predicate; the bool form stays its ``is FILED`` for the callers that
    only branch on it.

    RED mutation: collapse the suppression branch into ``DEDUP`` (or report
    the pause as ``FILED``) → the SUPPRESSED assertion below fails, and with
    ``FILED`` the ``ch.issues == {}`` assertion fails too.
    """
    from tortoise.alert_store import OpenOutcome

    ch = _FakeChannels()
    storage = MemoryStorage()
    store = _store(ch, storage)

    # 1) a PAUSED kind → SUPPRESSED: no issue, no dedup object, nothing on
    #    record. This is the fact the alert gate must not arm on.
    storage.upload(
        "ops/suppression.json",
        json.dumps({"STALE": {"until": "2999-01-01T00:00:00+00:00"}}).encode(),
        content_type="application/json")
    assert store.open_incident_state("STALE", "team_x") is OpenOutcome.SUPPRESSED
    assert ch.issues == {}, "a paused kind files nothing"
    assert storage.list("ops/alerts/") == [], "…and creates no dedup object"

    # 2) pause withdrawn → FILED (this call is the filer).
    storage.upload("ops/suppression.json", b"{}",
                   content_type="application/json")
    assert store.open_incident_state("STALE", "team_x") is OpenOutcome.FILED

    # 3) already open → DEDUP, and the BOOL stays its ``is FILED``.
    assert store.open_incident("STALE", "team_x") is False
    assert store.open_incident_state("STALE", "team_x") is OpenOutcome.DEDUP
    assert len(ch.issues) == 1, "a dedup hit must never re-file"
