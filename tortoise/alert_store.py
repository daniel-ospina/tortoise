"""Alert store — the per-incident lifecycle behind the dual-channel sink.

GitHub issue (agent-visible) + Telegram push (human-visible, absorbed #673),
with the R2 create-once object as the dedup LINEARIZATION POINT:

- ``open_incident`` attempts ``create_if_not_exists`` FIRST. The winner files
  the issue + pushes Telegram and backfills. A 412 loser ADOPTS the winner's
  object: if it carries an issue_number it skips; if it is a placeholder
  (winner died between create and backfill), the adopter becomes the filer via
  the GH-search fallback. The create-then-die window can never leave an
  incident permanently silent.
- Stable key per (kind, org): ``ops/alerts/{KIND}/{org-or-underscore}.json``
  — while the incident is open, repeats reuse it (one issue + one Telegram);
  recovery DELETES it (delete-to-resolve ⇒ a later recurrence is a new
  incident with a new issue number).
- **Alias set for subject-less incidents (#2844):** a platform-scoped incident
  is written by TWO implementations with two spellings — this store uses ``_``,
  the bash DR driver (``.github/scripts/registry-cron.sh``) uses ``global``.
  The R2 object is the dedup LINEARIZATION POINT, so two spellings would mean
  two linearization points: each writer wins its own create-once and files its
  own issue, and a resolve that deletes only one spelling strands the other as
  a stale sentinel carrying a closed issue's number. Every read and delete path
  therefore consults ``PLATFORM_ALIASES``. Subject-scoped incidents keep exactly
  one key — never a sibling subject's (#2375).
- ``resolve_incident`` closes the issue, pushes a "resolved" Telegram message,
  then deletes the dedup object (delete-to-resolve).
  ``resolve_incident_state`` is the same operation reporting WHICH fact it
  established (:class:`ResolveOutcome`: RESOLVED / ABSENT / SKIPPED_FRESH) —
  the bool form stays for the callers that only branch on it (#3820 cycle-7).
  A close that FAILS is neither announced nor deleted-to-resolve: the failure
  is recorded on the sentinel, the incident stays OPEN, and the failure is
  propagated (``CloseCooldown`` once the bounded retry window is active) so a
  caller cannot read it as a clean resolution (#5143, ADR-011).
- ``incident_open`` is a read-only presence check on the same dedup object —
  no close, no delete — so a caller holding an armed alert window can ask
  whether the incident behind it still exists (#3820 cycle-9 P1). It consults
  the whole alias set, so a platform incident recorded under the legacy
  ``global.json`` spelling (#2844) still reads as on record.
- ``open_incident_state`` is the mirror image on the open side
  (:class:`OpenOutcome`: FILED / DEDUP / SUPPRESSED) — one read of the
  suppression predicate, so a caller that must report WHY nothing was filed
  cannot disagree with the decision the call made (#3820 cycle-8 P2-2).
- Suppression: ``ops/suppression.json`` ``{kind: {until: ISO}}`` pauses a kind.
- Pending-push: a Telegram failure writes ``ops/pending-push/``; the daemon
  processes it on its next poll (``retry_pending``).

The store is decoupled from HTTP: the caller injects ``file_issue`` /
``close_issue`` / ``search_open`` / ``push_telegram`` callables (real impls in
``github_issue.py`` + ``telegram_push.py``), so tests use fakes and
MemoryStorage.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable  # noqa: UP035

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "ops/alerts/"
# #2844: the spellings of a SUBJECT-LESS (platform-scoped) sentinel, one per
# writer. The store writes the canonical first entry; the DR driver writes
# `global`. Both are read and both are deleted — see the module docstring.
PLATFORM_ALIASES = ("_", "global")
_PENDING_PREFIX = "ops/pending-push/"
_SUPPRESSION_KEY = "ops/suppression.json"

FileIssue = Callable[[str, str], int]      # (title, body) -> issue number
CloseIssue = Callable[[int, str | None], None]
SearchOpen = Callable[[str, str], list[int]]  # (kind, org_id) -> open issue numbers
PushTelegram = Callable[[str], None]
IssueOpen = Callable[[int], bool]           # (issue number) -> still open?

# ── Resolution authority (#2844 → #3127) ─────────────────────────────────────
# Single-writer principle: for each incident kind, exactly ONE writer's probes
# cover that kind's recovery condition, and only that writer may declare it
# recovered. A writer whose probe does NOT cover the failing dependency must
# never clear it, because "the condition stopped looking true to me" is not
# recovery: it hides a live fault AND leaves no sentinel, so the owner re-files
# on its next run — one duplicate issue + Telegram pair per cycle. Recovery is
# evidence-gated (recovery thresholds / "only the probe that covers the failing
# dependency may clear it" — see docs/adr/ADR-011).
#
# `R2_DOWN` is the case that forced this: the driver tests its OWN head-bucket
# preflight + `/status.storage_error` + a measured pool, while the watcher tests
# only its own reachability probe. They are two different failure modes behind
# one kind ("cannot reach R2" vs "can reach it but the key cannot ListObjects"),
# so the watcher cannot distinguish them and must not clear the driver's.
#
# Mirrored in `.github/scripts/registry-cron.sh` (`kind_owner`); the two MUST
# agree — `test_kind_owner_contract_with_driver` pins them.
WRITER_DRIVER = "driver"
WRITER_WATCHER = "watcher"
WRITER_APP = "app"
WRITER_UNSPECIFIED = "unspecified"

KIND_OWNERS: dict[str, str] = {
    # driver: its own R2 preflight + storage_error + a measured pool.
    "R2_DOWN": WRITER_DRIVER,
    "APP_DOWN": WRITER_DRIVER,
    "WATCHER_DOWN": WRITER_DRIVER,
    "SWEEP_CONFIG_ERROR": WRITER_DRIVER,
    "SWEEP_OFF_STALE": WRITER_DRIVER,
    "SWEEP_NO_COVERAGE": WRITER_DRIVER,
    "LIVENESS_NO_WORK": WRITER_DRIVER,
    # watcher: archive/stamp freshness + the driver heartbeat, read in-process.
    "STALE": WRITER_WATCHER,
    "NEVER_BACKED_UP": WRITER_WATCHER,
    "METADATA_LOST": WRITER_WATCHER,
    "BACKUP_SET_MISSING": WRITER_WATCHER,
    "DRIVER_DOWN": WRITER_WATCHER,
    # app: the drill resolves on its own success signal.
    "RESTORE_DRILL_FAILED": WRITER_APP,
}


def kind_owner(kind: str) -> str:
    """The single writer whose probes cover recovery of ``kind``.

    Unlisted kinds return ``WRITER_UNSPECIFIED`` — anyone may clear them, which
    preserves the pre-#3127 behaviour where authority was never contested
    (``SIZE_GUARD_ABORT``, ``DATA_LOSS_CANDIDATE``, ``abuse_suspended``, ...).
    """
    return KIND_OWNERS.get(kind, WRITER_UNSPECIFIED)


def _read_json(storage, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(storage.download(key))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _write_json(storage, key: str, data: dict[str, Any]) -> None:
    storage.upload(key, json.dumps(data, indent=2).encode("utf-8"), content_type="application/json")


def _parse_iso(value: Any) -> datetime | None:
    """Parse a stored ISO timestamp, normalising it to aware UTC.

    A naive stamp (written by an older revision) is read as UTC so the close
    cooldown's age arithmetic cannot raise ``TypeError``.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # noqa: UP017


class CloseCooldown(RuntimeError):
    """Raised when a close is SKIPPED because a recent attempt failed (#5143).

    Not an error condition: the incident is simply still open and the caller
    must keep treating the subject as unresolved, retrying after the bounded
    backoff. A distinct type so containment layers can report it as
    "still open, backing off" rather than as a crash.
    """


class ResolveOutcome(Enum):
    """The three facts a resolve attempt can establish (#3820 cycle-7).

    ``resolve_incident`` squashes these into ``bool`` for its existing
    callers. The analytics sink needs the distinction because its in-process
    state must be driven by WHICH fact holds, not by a guess about what a
    ``False`` meant.

    * ``RESOLVED``      — an incident was open and has been closed + deleted.
    * ``ABSENT``        — no incident to resolve.
    * ``SKIPPED_FRESH`` — an incident is ON RECORD and this attempt left it
                          untouched, for one of two reasons: it was filed
                          at/after the caller's ``before`` bound (fresh), or it
                          is owned by another writer whose probes cover its
                          recovery (#3127), so this caller may not clear it.
                          The caller must stay pending — but the outcome does
                          NOT establish that the incident is LIVE: the
                          authority refusal runs before (and independently of)
                          the issue's liveness, and ``_alias_states`` does not
                          consult the issue state, so a sentinel naming a
                          CLOSED issue is refused as ``SKIPPED_FRESH`` too.
    """

    RESOLVED = "resolved"
    ABSENT = "absent"
    SKIPPED_FRESH = "skipped_fresh"


class OpenOutcome(Enum):
    """The three facts an open attempt can establish (#3820 cycle-8 P2-2).

    ``open_incident`` squashes these into ``bool`` for its existing callers.
    The analytics sink needs the distinction for the SAME reason it needs
    :class:`ResolveOutcome`: its in-process alert gate must be driven by WHICH
    fact holds — "an incident is on record" vs "the kind is paused, so nothing
    was created" — and reading a second, time-dependent predicate to guess it
    disagrees with the first under a concurrent pause withdrawal.

    * ``FILED``      — this call created the dedup object and/or became its
                       filer. An incident IS on record.
    * ``DEDUP``      — the dedup object already carried an issue number. An
                       incident IS on record (not this call's).
    * ``SUPPRESSED`` — ``kind`` is paused in ``ops/suppression.json``: no
                       issue and NO dedup object. Nothing is on record.
    """

    FILED = "filed"
    DEDUP = "dedup"
    SUPPRESSED = "suppressed"


class AlertStore:
    """Per-incident alert lifecycle over a BackupStorage (R2 create-once)."""

    def __init__(
        self,
        storage,
        *,
        file_issue: FileIssue,
        close_issue: CloseIssue,
        search_open: SearchOpen,
        push_telegram: PushTelegram,
        issue_open: IssueOpen | None = None,
        default_writer: str = WRITER_UNSPECIFIED,
        repo: str,
        assignee: str | None = None,
        now: datetime | None = None,
        close_cooldown_min: float = 60.0,
    ) -> None:
        self._storage = storage
        self._file = file_issue
        self._close = close_issue
        self._search = search_open
        self._push = push_telegram
        # #3127: authoritative per-issue state reader. Preferred over the
        # GH-search result: search is rate limited (~30/min) and matches titles
        # heuristically, so "absent from the results" is not proof of closure.
        # Absent → never distrust a sentinel (no re-filing on an unverifiable
        # sentinel), which keeps test/embedding callers unchanged.
        self._issue_open = issue_open
        # #3127: the writer this instance acts as. Declared once per construction
        # site (the watcher's store is built with writer="watcher") so every
        # resolve call inherits the authority check without a per-call argument.
        self._writer = default_writer
        self._repo = repo
        self._assignee = assignee
        self._now = now or (lambda: datetime.now(timezone.utc))  # noqa: UP017
        # #5143: bounded retry — how long a failed issue-close is left alone
        # before it is attempted again (ADR-011: the next healthy run closes it).
        self._close_cooldown_min = close_cooldown_min

    def _clock(self) -> datetime:
        """Current time — evaluated per call so time-based suppression expires
        correctly on a long-lived store (review P2-6)."""
        return self._now() if callable(self._now) else self._now

    # ── helpers ─────────────────────────────────────────────────────────────
    def _keys(self, kind: str, org_id: str) -> tuple[str, ...]:
        """Every sentinel key that can hold THIS incident (#2844).

        Subject-scoped: exactly one key — never a sibling subject's, and never
        the platform-scoped sentinel (the #2375 subject-scoping invariant).
        Platform-scoped: one per writer spelling.
        """
        if org_id:
            return (f"{DEDUP_PREFIX}{kind}/{org_id}.json",)
        return tuple(f"{DEDUP_PREFIX}{kind}/{alias}.json" for alias in PLATFORM_ALIASES)

    def _key(self, kind: str, org_id: str) -> str:
        """The canonical key this store WRITES (the first alias)."""
        return self._keys(kind, org_id)[0]

    def _alias_states(self, kind: str, org_id: str) -> list[tuple[str, dict[str, Any]]]:
        """Every sentinel that currently exists for this incident."""
        out: list[tuple[str, dict[str, Any]]] = []
        for k in self._keys(kind, org_id):
            state = _read_json(self._storage, k)
            if not state:
                continue
            # A PLATFORM spelling must not adopt a sentinel belonging to a real
            # subject literally named "global"/"_" — `hosted_api` opens
            # RESTORE_DRILL_FAILED with that subject. A subject-scoped sentinel
            # carries its subject in the body; the driver's platform sentinel
            # does not (it predates the field). `org_id` is that same field
            # under the name main's deployed writers used, so a sentinel from
            # either version is recognised.
            if org_id == "" and (state.get("team_id") or state.get("org_id")):
                continue
            out.append((k, state))
        return out

    def _suppressed(self, kind: str) -> bool:
        supp = _read_json(self._storage, _SUPPRESSION_KEY)
        until = supp.get(kind, {}).get("until")
        if not until:
            return False
        try:
            return self._clock() < datetime.fromisoformat(until)
        except ValueError:
            return False

    def _title(self, kind: str, org_id: str, detail: dict) -> str:
        org = f" — {org_id}" if org_id else ""
        age = detail.get("age") or detail.get("age_minutes") or ""
        return f"[DR] {kind}{org}" + (f" — last backup {age}" if age else "")

    def _body(self, kind: str, org_id: str, detail: dict) -> str:
        return (
            f"**Incident kind:** {kind}\n"
            f"**Team:** {org_id or '(platform)'}\n"
            f"**Detail:** ```{json.dumps(detail, indent=2)}```\n\n"
            f"Runbook: `docs/ops/registry-backup-dr.md` — triage table by kind."
        )

    def _telegram_text(self, kind: str, org_id: str, detail: dict, issue_number: int | None) -> str:
        org = f" ({org_id})" if org_id else ""
        issue = f" — issue #{issue_number}" if issue_number else ""
        return f"🚨 DR alert: {kind}{org}{issue}"

    # ── incident lifecycle ──────────────────────────────────────────────────
    def suppression_active(self, kind: str) -> bool:
        """Whether ``kind`` is currently paused (``ops/suppression.json``).

        A public, read-only view of the suppression gate ``open_incident``
        applies. It exists because ``open_incident``'s ``False`` return is
        AMBIGUOUS — a dedup hit (the object already exists) and a suppressed
        kind (no issue and no dedup object at all) both produce it — so a
        caller that must explain WHY nothing was filed asks the store instead
        of inferring a reason from the boolean (#3820 cycle-6 P2-1).

        Retained for compatibility, but a caller reporting the REASON should
        prefer :meth:`open_incident_state`: this predicate is time-dependent,
        so asking it at a SECOND instant can disagree with the decision
        ``open_incident`` already made (#3820 cycle-8 P2-2).
        """
        return self._suppressed(kind)

    def incident_open(self, kind: str, org_id: str = "") -> bool:
        """Whether a dedup object for ``(kind, org_id)`` is ON RECORD.

        A read-only PRESENCE check: no close, no delete, no GitHub/Telegram
        egress. It exists so a caller that must decide whether an armed window
        still has an incident behind it can ask the store instead of assuming
        one (#3820 cycle-9 P1). A placeholder (the winner died between create
        and backfill) counts as on record — the adopter will file it. Anything
        unreadable/absent is ``False``.

        Read across the whole ALIAS SET (#2844), so a platform incident the DR
        driver recorded under the legacy ``global.json`` spelling is still on
        record — a single-key read would report a live incident as absent.
        """
        return bool(self._alias_states(kind, org_id))

    def open_incident(self, kind: str, org_id: str = "", detail: dict | None = None,
                      writer: str | None = None) -> bool:
        """Open (or re-use) an incident. True if this call is the filer.

        The BOOL contract is preserved deliberately (#3820 cycle-8):
        ``backup_watcher.py`` and the drill endpoints branch on the boolean,
        and returning a truthy :class:`OpenOutcome` here would read every
        branch as success. The tri-state fact lives in
        :meth:`open_incident_state`; this is its ``is FILED``.
        """
        return (
            self.open_incident_state(kind, org_id, detail, writer) is OpenOutcome.FILED
        )

    def open_incident_state(self, kind: str, org_id: str = "",
                            detail: dict | None = None,
                            writer: str | None = None) -> OpenOutcome:
        """Open (or re-use) an incident, reporting WHICH fact was established.

        ONE read of the time-dependent suppression predicate, so the reason a
        caller is told cannot disagree with the decision this call made
        (#3820 cycle-8 P2-2): the previous shape had the caller re-ask
        ``suppression_active`` at a LATER instant, so a pause withdrawn in
        between reported a dedup hit for an incident that was never created —
        cycle-6 P2-1 re-created in the opposite window.

        Adoption is ALIAS-AWARE and evidence-gated (#2844 + #3127): a sibling
        spelling (the DR driver's legacy ``global.json``) may already hold this
        platform incident, so it is adopted rather than becoming a second
        create-once point — but only while its recorded issue is still OPEN
        (a sentinel naming a CLOSED issue is stale and is re-filed). An adopted
        or deduped incident is reported as :attr:`OpenOutcome.DEDUP`.
        """
        detail = detail or {}
        if self._suppressed(kind):
            return OpenOutcome.SUPPRESSED
        writer = self._writer if writer is None else writer
        key = self._key(kind, org_id)
        # #2844: one canonical sentinel per (kind, subject) — a sibling spelling
        # (the DR driver's legacy `global.json`) may already hold this incident,
        # so adopt it rather than becoming a second create-once point.
        # #3127: adoption is only valid while the recorded issue is still OPEN.
        # The sentinel is the dedup LOCK; the ISSUE's state is the authoritative
        # record of whether the incident is live. A sentinel holding a CLOSED
        # issue's number is stale — the #2844 defect itself, a manual close, or
        # a legacy cross-writer close — and trusting it swallows every
        # recurrence of a live fault (pre-fix `main` re-filed here). Clear it
        # and re-file.
        adopted = self._alias_states(kind, org_id)
        for sibling, state in adopted:
            number = state.get("issue_number")
            if number and self._issue_is_live(kind, org_id, number):
                logger.info(
                    "dedup: %s already tracked via %s (issue #%s) — no-op",
                    kind, sibling, number,
                )
                return OpenOutcome.DEDUP
        if adopted:
            self._forget(adopted, kind)
            # `key` may itself have just been dropped — fall through to create.
        placeholder = {
            "kind": kind,
            # The field the platform-scoping guard above reads (this PR), plus
            # the name main's deployed writer used for the same value — writing
            # both keeps a sentinel from either version recognisable.
            "team_id": org_id,
            "org_id": org_id,
            "detail": detail,
            "filed_at": self._clock().isoformat(),
            "issue_number": None,
            "telegram_pushed": False,
        }
        created = self._storage.create_if_not_exists(key, json.dumps(placeholder).encode())
        if created:
            # Re-check: the other writer may have won the race while we were
            # creating. If its sentinel recorded a LIVE issue it IS the filer —
            # drop ours rather than file a second issue.
            for sibling, state in self._alias_states(kind, org_id):
                if sibling == key:
                    continue
                number = state.get("issue_number")
                if number and self._issue_is_live(kind, org_id, number):
                    try:
                        self._storage.delete(key)
                    except Exception as e:
                        logger.warning("dedup: could not drop our own sentinel %s: %s", key, e)
                    return OpenOutcome.DEDUP
            self._become_filer(kind, org_id, detail, key, placeholder, writer)
            return OpenOutcome.FILED
        # 412 — adopt the winner's object; never double-file.
        existing = _read_json(self._storage, key)
        number = existing.get("issue_number")
        if number and self._issue_is_live(kind, org_id, number):
            return OpenOutcome.DEDUP  # already filed and still live — nothing to do
        # Placeholder (winner died mid-filing): become the filer via GH-search
        # fallback to avoid duplicates.
        # ...or a stale closed-issue sentinel (#3127). Either way the filer
        # becomes the CANONICAL key's holder, so the incident keeps ONE
        # linearization point. Backfilling into an adopted legacy spelling would
        # leave the canonical key free for the other writer's `r2_put_once` to
        # succeed on — two create-once points again.
        self._become_filer(kind, org_id, detail, key, existing, writer)
        return OpenOutcome.FILED

    def _forget(self, adopted: list[tuple[str, dict[str, Any]]], kind: str) -> None:
        """Best-effort delete of stale / unbackfilled sentinels.

        Never raises: one failed delete must not abort the poll, and a sentinel
        left behind is only a duplicate-report risk (the next poll re-checks),
        never a swallowed one.
        """
        for sibling, state in adopted:
            try:
                # Compare-and-delete: another writer may have re-filed (and
                # backfilled a DIFFERENT issue number) during the GitHub round
                # trips above. Deleting its fresh sentinel would file a third
                # issue for one condition — the #2844 defect in the recovery
                # path — so only delete the exact object we read.
                current = _read_json(self._storage, sibling)
                if current and (
                    current.get("issue_number") != state.get("issue_number")
                    or current.get("filed_at") != state.get("filed_at")
                ):
                    logger.warning(
                        "dedup: %s changed while we were checking it — leaving it"
                        " alone (the other writer re-filed)", sibling,
                    )
                    continue
                self._storage.delete(sibling)
                logger.warning(
                    "dedup: dropped stale sentinel %s for %s (recorded issue #%s "
                    "is not open) — re-filing",
                    sibling, kind, state.get("issue_number"),
                )
            except Exception as e:
                logger.warning("dedup: could not drop stale sentinel %s: %s", sibling, e)

    def _issue_is_live(self, kind: str, org_id: str, number: Any) -> bool:
        """Is the sentinel's recorded issue still OPEN? (#3127)

        Distrusting a sentinel requires POSITIVE evidence of closure — never the
        absence of a search hit. A state read that fails, or a non-numeric
        ``issue_number``, is treated as OPEN: refusing to adopt on a blip is the
        duplicate-issue defect #2844 exists to fix, whereas adopting a stale
        sentinel is the silent-outage defect #3127 exists to fix. Prefer the
        failure that pages.
        """
        try:
            parsed = int(number)
        except (TypeError, ValueError):
            logger.warning("dedup: %s sentinel has a non-numeric issue_number %r", kind, number)
            return True
        if self._issue_open is None:
            return True  # no state reader injected — never re-file unverified
        try:
            return bool(self._issue_open(parsed))
        except Exception as e:
            logger.warning(
                "dedup: issue-state read failed for %s #%s (%s) — trusting the "
                "sentinel; an API blip must not re-file a live incident",
                kind, parsed, e,
            )
            return True

    def _become_filer(
        self, kind, org_id, detail, key, state, writer: str = WRITER_UNSPECIFIED,
    ) -> bool:
        issue_number = None
        filed_here = False
        try:
            # Subject-scoped search (#2313 Task 4): the query must match the
            # incident's OWN title (kind + org/graph subject). A kind-only
            # search lets a same-kind incident of a DIFFERENT subject adopt
            # this one's issue number — and recovery would then close the
            # other subject's issue (silent-loss cross-talk).
            hits = self._search(kind, org_id)  # GH-search fallback dedup
            if hits:
                issue_number = hits[0]
        except Exception as e:
            logger.warning("incident search failed for %s: %s", kind, e)
        if issue_number is None:
            try:
                issue_number = self._file(
                    self._title(kind, org_id, detail), self._body(kind, org_id, detail)
                )
                filed_here = issue_number is not None
            except Exception as e:
                logger.warning("incident filing failed for %s: %s — will adopt on next poll", kind, e)
        state["issue_number"] = issue_number
        state["detail"] = detail
        # Diagnostic only: which writer filed this issue. It is NOT an authority
        # token — `resolve_incident` decides by KIND_OWNERS, never by this field
        # (see its docstring for why the provenance exception was removed).
        if filed_here and writer != WRITER_UNSPECIFIED:
            state["writer"] = writer
        _write_json(self._storage, key, state)
        # Announce whenever this call ends up holding the issue number. The
        # ANNOUNCEMENT STATE MACHINE (a persisted announcement flag with
        # resume-on-adoption) is deliberately NOT here: review rounds 3 and 4
        # each found P1s inside it — a flag that suppressed a genuine
        # recurrence, a gate that left an incident filed but never shown, a
        # clause that let an adopter claim provenance, and a hard-coded flag in
        # bash that marked an outage as announced. Until that is designed on its
        # own terms (issue #3338), prefer the failure that pages: an adopted
        # issue can be announced twice — noise — rather than not at all.
        if issue_number is not None:
            self._push_with_pending(key, self._telegram_text(kind, org_id, detail, issue_number))
            state["telegram_pushed"] = True
            _write_json(self._storage, key, state)
        return True

    def resolve_incident_state(self, kind: str, org_id: str = "",
                               *, before: datetime | None = None,
                               writer: str | None = None) -> ResolveOutcome:
        """Close + delete-to-resolve across EVERY spelling, reporting WHICH fact
        was established.

        The tri-state sibling of ``resolve_incident`` (#3820 cycle-7). The
        bool conflates three different situations, so a caller that must act
        on WHY (the analytics sink's process state) cannot be driven by it
        without guessing — and that guess is what re-absorbed a fresh
        incident episode four cycles running. Same read / ``before`` compare /
        close / delete as ``resolve_incident``, which now delegates here.

        ``before`` bounds WHICH incident may be resolved: when set, an incident
        whose ``filed_at`` is at or after ``before`` is left untouched and
        reported as ``SKIPPED_FRESH`` (an incident IS open — it is simply not
        the one this attempt may close). The #3820 startup resolve passes the
        instant it decided to resolve, so a FRESH incident that opens while
        the resolve is in flight is never swept away. The state carries
        ``filed_at`` from ``open_incident``; an absent/unparseable stamp cannot
        be compared, so it is treated as predating the bound and resolved (the
        pre-``filed_at`` behaviour).

        #2844: deleting only the key this store spells stranded the DR driver's
        `global.json` carrying a CLOSED issue's number. The driver's `file_alert`
        412 branch reads that object, sees a non-open issue, finds no open issue
        and files a fresh one — a resolved incident pages again. The whole
        ALIAS SET is therefore resolved, and each spelling is compare-and-deleted
        rather than merely dropped.

        If both writers filed before the alias fix shipped, the incident has two
        issues; both are closed, or the orphan outlives its sentinel and can
        never be resolved by any surface (#3030).

        #3127 resolution authority: only the kind's OWNER (the writer whose
        probes cover its recovery condition) may clear it. A caller that does
        not own the kind refuses: its evidence does not cover the failing
        dependency, so the "recovery" may be false and the owner re-files on its
        next run (one duplicate pair per cycle). The sentinel is left intact.
        The refusal is reported as ``SKIPPED_FRESH`` — an incident is ON RECORD
        and this attempt left it untouched, which is exactly the fact the caller
        must act on. The outcome does NOT assert liveness: this authority check
        runs before the issue state is consulted, so a refused sentinel naming a
        CLOSED issue is reported ``SKIPPED_FRESH`` too (the caller must still
        stay pending — nothing was resolved — but it must not read the outcome
        as proof of a live incident). Declaring no writer keeps the historical
        behaviour for callers outside the DR watcher/driver pair.

        There is deliberately NO provenance exception. Three review rounds
        found three ways a self-asserted "filed by" note went wrong — stamped by
        an adopter, surviving a placeholder that was never filed, outliving the
        issue it described — each letting a non-owner clear a kind it has no
        evidence about. Authority is decided by KIND_OWNERS alone; the `writer`
        field is diagnostic only.

        #5143 / ADR-011: a close that did NOT happen is not a resolution. If
        ``close_issue`` fails, the failure is recorded on the sentinel
        (``close_failed_at`` / ``close_failures``), nothing is announced, the
        object is NOT deleted, and the exception propagates so the caller keeps
        the subject pending. A retry inside ``close_cooldown_min`` is SKIPPED and
        raises :class:`CloseCooldown` (still open, backing off). ADR-011: "A blip
        must not re-file; a permanent failure must not be silent" and "the
        incident stays open with no writer able to close it. That is correct
        rather than a defect … The driver's next healthy run closes it."
        """
        states = self._alias_states(kind, org_id)
        if not states:
            return ResolveOutcome.ABSENT
        if before is not None:
            for _, state in states:
                filed_at = state.get("filed_at")
                if not filed_at:
                    continue
                try:
                    if datetime.fromisoformat(filed_at) >= before:
                        return ResolveOutcome.SKIPPED_FRESH
                except (TypeError, ValueError):
                    pass  # an uncomparable stamp falls through to resolve
        writer = self._writer if writer is None else writer
        if writer != WRITER_UNSPECIFIED:
            owner = kind_owner(kind)
            if owner != WRITER_UNSPECIFIED and owner != writer:
                logger.warning(
                    "resolution refused: %s is owned by the %s but %s is clearing "
                    "it — that writer's probes do not cover this kind's recovery "
                    "condition (#3127)",
                    kind, owner, writer,
                )
                return ResolveOutcome.SKIPPED_FRESH
        # number -> every sentinel that names it (a pre-#2844 incident has two
        # spellings and therefore two issues).
        numbers: dict[int, list[tuple[str, dict[str, Any]]]] = {}
        for key, state in states:
            try:
                number = int(state.get("issue_number"))
            except (TypeError, ValueError):
                # A corrupt sentinel must not abort the poll — raising here would
                # skip the heartbeat write and retry_pending on every cycle.
                logger.warning(
                    "dedup: %s sentinel has a non-numeric issue_number %r — "
                    "skipping its close", kind, state.get("issue_number"),
                )
                continue
            numbers.setdefault(number, []).append((key, state))
        for number, holders in numbers.items():
            # #5143: bounded retry. A close that failed recently is not
            # re-attempted until its cooldown passes, so a GitHub outage cannot
            # make every poll hammer the close endpoint. Raised as a distinct
            # type so a caller keeps the subject PENDING rather than reading a
            # falsy return as "nothing was open".
            failed_at = next(
                (t for t in (_parse_iso(s.get("close_failed_at")) for _, s in holders)
                 if t is not None),
                None,
            )
            if failed_at is not None:
                age_min = (self._clock() - failed_at).total_seconds() / 60.0
                if age_min < self._close_cooldown_min:
                    raise CloseCooldown(
                        f"{kind} #{number}: last close attempt failed "
                        f"{age_min:.0f} min ago — retrying after "
                        f"{self._close_cooldown_min:.0f} min"
                        + (f" ({org_id})" if org_id else "")
                    )
        for number, holders in numbers.items():
            try:
                self._close(number, "Resolved — condition cleared.")
            except Exception as e:
                # The close did NOT happen. Record the failure (so the retry backs
                # off) and RE-RAISE — the whole fix (#5143): nothing is announced
                # and nothing is deleted, so the incident stays OPEN until its
                # close is OBSERVED. This is ADR-011's "a blip must not re-file; a
                # permanent failure must not be silent" (and the runbook's
                # close-then-delete order: delete-to-resolve must never precede a
                # successful close). Pre-fix, this warned and fell through to the
                # push + delete, reporting RESOLVED for a still-open issue.
                for key, state in holders:
                    state["close_failed_at"] = self._clock().isoformat()
                    state["close_failures"] = int(state.get("close_failures") or 0) + 1
                    try:
                        _write_json(self._storage, key, state)
                    except Exception:
                        logger.exception("could not record a close failure for %s", key)
                logger.warning(
                    "issue close failed for %s #%s: %s — leaving the incident OPEN "
                    "(no resolution announced, sentinel kept); retrying after "
                    "%.0f min",
                    kind, number, e, self._close_cooldown_min,
                )
                raise
        if numbers:
            if len(numbers) > 1:
                logger.warning(
                    "incident %s carries %d issues (%s) — both spellings filed "
                    "before the #2844 alias fix; closing all",
                    kind, len(numbers), ", ".join(f"#{n}" for n in numbers),
                )
            self._push_with_pending(
                states[0][0],
                f"✅ DR resolved: {kind}" + (f" ({org_id})" if org_id else "")
                + " — issue " + ", ".join(f"#{n}" for n in numbers),
            )
        # delete-to-resolve across the whole alias set: a surviving spelling
        # would adopt the next recurrence (the #2796 class). Best-effort, and
        # compare-and-delete exactly like `_forget`: the closes and the push
        # above are GitHub round trips, and a writer that re-files this incident
        # with a NEW issue number during them would have its fresh sentinel
        # deleted — orphaning an open issue that no sentinel names (the #3030
        # class) and re-filing a duplicate on the next detection (#2844).
        for key, seen in states:
            try:
                current = _read_json(self._storage, key)
                if current and (
                    current.get("issue_number") != seen.get("issue_number")
                    or current.get("filed_at") != seen.get("filed_at")
                ):
                    logger.warning(
                        "dedup: %s was re-filed while resolving — leaving the new "
                        "sentinel in place (its issue stays open)", key,
                    )
                    continue
                self._storage.delete(key)
            except Exception as e:
                logger.warning("sentinel delete failed (%s): %s — spelling left behind", key, e)
        return ResolveOutcome.RESOLVED

    def resolve_incident(self, kind: str, org_id: str = "",
                         *, before: datetime | None = None,
                         writer: str | None = None) -> bool:
        """Close + delete-to-resolve. True if an incident was open.

        The BOOL contract is preserved deliberately (#3820 cycle-7): eleven
        callers (``backup_watcher.py``, ``hosted_api.py``,
        ``test_alert_store.py``) branch on ``if store.resolve_incident(...)``,
        and returning a truthy
        :class:`ResolveOutcome` from here would silently make ``SKIPPED_FRESH``
        and ``ABSENT`` read as success. The tri-state fact lives in
        :meth:`resolve_incident_state`; this is its ``is RESOLVED``.

        ``before`` bounds WHICH incident may be resolved, and a failed close
        RAISES rather than reporting a clean resolution — see
        :meth:`resolve_incident_state`, which owns both semantics.
        """
        return (
            self.resolve_incident_state(kind, org_id, before=before, writer=writer)
            is ResolveOutcome.RESOLVED
        )

    def _push_with_pending(self, key: str, text: str) -> None:
        """Push Telegram; on failure park a pending-push for the daemon to retry."""
        try:
            self._push(text)
        except Exception as e:
            logger.warning("telegram push failed (%s): %s — pending-push parked", key, e)
            digest = hashlib.sha256(key.encode()).hexdigest()[:16]
            _write_json(
                self._storage,
                f"{_PENDING_PREFIX}{digest}.json",
                {"key": key, "text": text, "created_at": self._clock().isoformat()},
            )

    def retry_pending(self) -> int:
        """Retry parked pushes (daemon calls this each poll). Returns count sent.

        Safety gates (#673 review P2):
        - **TTL**: pending pushes older than PENDING_TTL_HOURS are silently
          discarded — a stale push from a long-resolved incident must never
          fire and confuse a human.
        - **Incident check**: before retrying, the referenced dedup object
          must still exist (the incident is still open).  If it was deleted
          (incident resolved), discard the pending push — do not resurrect a
          stale alert.
        """
        PENDING_TTL_HOURS = 24
        sent = 0
        now = self._clock()
        for k in self._storage.list(_PENDING_PREFIX):
            state = _read_json(self._storage, k)
            text = state.get("text")
            if not text:
                self._storage.delete(k)
                continue
            # TTL gate: skip & delete pushes older than the TTL window.
            created_str = state.get("created_at")
            if created_str:
                try:
                    created = datetime.fromisoformat(created_str)
                    age_hours = (now - created).total_seconds() / 3600
                    if age_hours > PENDING_TTL_HOURS:
                        logger.warning(
                            "pending push TTL expired (%.1fh > %dh) — discarding: %s",
                            age_hours, PENDING_TTL_HOURS, k,
                        )
                        self._storage.delete(k)
                        continue
                except ValueError:
                    pass  # unparseable timestamp — keep retrying
            # Incident check: the dedup key MUST still exist; if gone,
            # the incident was resolved and we must NOT resurrect it.
            incident_key = state.get("key")
            if incident_key:
                existing = _read_json(self._storage, incident_key)
                if not existing:
                    logger.warning(
                        "pending push discarded — incident already resolved: "
                        "pending=%s incident=%s", k, incident_key,
                    )
                    self._storage.delete(k)
                    continue
            try:
                self._push(text)
                self._storage.delete(k)
                sent += 1
            except Exception as e:
                logger.warning("pending push retry failed (%s): %s", k, e)
        return sent
