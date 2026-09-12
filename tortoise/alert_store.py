"""Alert store — the per-incident lifecycle behind the dual-channel sink.

GitHub issue (agent-visible) + Telegram push (human-visible, absorbed #673),
with the R2 create-once object as the dedup LINEARIZATION POINT:

- ``open_incident`` attempts ``create_if_not_exists`` FIRST. The winner files
  the issue + pushes Telegram and backfills. A 412 loser ADOPTS the winner's
  object: if it carries an issue_number it skips; if it is a placeholder
  (winner died between create and backfill), the adopter becomes the filer via
  the GH-search fallback. The create-then-die window can never leave an
  incident permanently silent.
- Stable key per (kind, subject): ``ops/alerts/{KIND}/{subject-or-underscore}.json``
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
  then deletes the dedup object.
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
SearchOpen = Callable[[str, str], list[int]]  # (kind, team_id) -> open issue numbers
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

    def _clock(self) -> datetime:
        """Current time — evaluated per call so time-based suppression expires
        correctly on a long-lived store (review P2-6)."""
        return self._now() if callable(self._now) else self._now

    # ── helpers ─────────────────────────────────────────────────────────────
    def _keys(self, kind: str, team_id: str) -> tuple[str, ...]:
        """Every sentinel key that can hold THIS incident (#2844).

        Subject-scoped: exactly one key — never a sibling subject's, and never
        the platform-scoped sentinel (the #2375 subject-scoping invariant).
        Platform-scoped: one per writer spelling.
        """
        if team_id:
            return (f"{DEDUP_PREFIX}{kind}/{team_id}.json",)
        return tuple(f"{DEDUP_PREFIX}{kind}/{alias}.json" for alias in PLATFORM_ALIASES)

    def _key(self, kind: str, team_id: str) -> str:
        """The canonical key this store WRITES (the first alias)."""
        return self._keys(kind, team_id)[0]

    def _alias_states(self, kind: str, team_id: str) -> list[tuple[str, dict[str, Any]]]:
        """Every sentinel that currently exists for this incident."""
        out: list[tuple[str, dict[str, Any]]] = []
        for k in self._keys(kind, team_id):
            state = _read_json(self._storage, k)
            if not state:
                continue
            # A PLATFORM spelling must not adopt a sentinel belonging to a real
            # subject literally named "global"/"_" — `hosted_api` opens
            # RESTORE_DRILL_FAILED with that subject. A subject-scoped sentinel
            # carries its subject in the body; the driver's platform sentinel
            # does not (it predates the field).
            if team_id == "" and state.get("team_id"):
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

    def _title(self, kind: str, team_id: str, detail: dict) -> str:
        team = f" — {team_id}" if team_id else ""
        age = detail.get("age") or detail.get("age_minutes") or ""
        return f"[DR] {kind}{team}" + (f" — last backup {age}" if age else "")

    def _body(self, kind: str, team_id: str, detail: dict) -> str:
        return (
            f"**Incident kind:** {kind}\n"
            f"**Team:** {team_id or '(platform)'}\n"
            f"**Detail:** ```{json.dumps(detail, indent=2)}```\n\n"
            f"Runbook: `docs/ops/registry-backup-dr.md` — triage table by kind."
        )

    def _telegram_text(self, kind: str, team_id: str, detail: dict, issue_number: int | None) -> str:
        team = f" ({team_id})" if team_id else ""
        issue = f" — issue #{issue_number}" if issue_number else ""
        return f"🚨 DR alert: {kind}{team}{issue}"

    # ── incident lifecycle ──────────────────────────────────────────────────
    def open_incident(
        self,
        kind: str,
        team_id: str = "",
        detail: dict | None = None,
        writer: str | None = None,
    ) -> bool:
        """Open (or re-use) an incident. True if this call is the filer."""
        writer = self._writer if writer is None else writer
        detail = detail or {}
        if self._suppressed(kind):
            return False
        key = self._key(kind, team_id)
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
        adopted = self._alias_states(kind, team_id)
        for sibling, state in adopted:
            number = state.get("issue_number")
            if number and self._issue_is_live(kind, team_id, number):
                logger.info(
                    "dedup: %s already tracked via %s (issue #%s) — no-op",
                    kind, sibling, number,
                )
                return False
        if adopted:
            self._forget(adopted, kind)
            # `key` may itself have just been dropped — fall through to create.
        placeholder = {
            "kind": kind,
            "team_id": team_id,
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
            for sibling, state in self._alias_states(kind, team_id):
                if sibling == key:
                    continue
                number = state.get("issue_number")
                if number and self._issue_is_live(kind, team_id, number):
                    try:
                        self._storage.delete(key)
                    except Exception as e:
                        logger.warning("dedup: could not drop our own sentinel %s: %s", key, e)
                    return False
            return self._become_filer(kind, team_id, detail, key, placeholder, writer)
        # 412 — adopt the winner's object; never double-file.
        existing = _read_json(self._storage, key)
        number = existing.get("issue_number")
        if number and self._issue_is_live(kind, team_id, number):
            return False  # already filed and still live — nothing to do
        # Placeholder (winner died mid-filing) or a stale closed-issue sentinel:
        # become the filer on the CANONICAL key, so the incident keeps ONE
        # linearization point. Backfilling into an adopted legacy spelling would
        # leave the canonical key free for the other writer's `r2_put_once` to
        # succeed on — two create-once points again (#3127).
        return self._become_filer(kind, team_id, detail, key, existing, writer)

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

    def _issue_is_live(self, kind: str, team_id: str, number: Any) -> bool:
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
        self, kind, team_id, detail, key, state, writer: str = WRITER_UNSPECIFIED,
    ) -> bool:
        issue_number = None
        filed_here = False
        try:
            # Subject-scoped search (#2313 Task 4): the query must match the
            # incident's OWN title (kind + team/graph subject). A kind-only
            # search lets a same-kind incident of a DIFFERENT subject adopt
            # this one's issue number — and recovery would then close the
            # other subject's issue (silent-loss cross-talk).
            hits = self._search(kind, team_id)  # GH-search fallback dedup
            if hits:
                issue_number = hits[0]
        except Exception as e:
            logger.warning("incident search failed for %s: %s", kind, e)
        if issue_number is None:
            try:
                issue_number = self._file(
                    self._title(kind, team_id, detail), self._body(kind, team_id, detail)
                )
                filed_here = issue_number is not None
            except Exception as e:
                logger.warning("incident filing failed for %s: %s — will adopt on next poll", kind, e)
        state["issue_number"] = issue_number
        state["detail"] = detail
        # Provenance records who FILED this issue. Adopting someone else's issue
        # through the GH-search fallback must NOT claim it: that would let a
        # non-owner clear a kind on its own weaker evidence (#3127 round 2). A
        # sentinel with no issue number stays ours — we opened it and nobody
        # else holds an issue for it, so we may still clear it ourselves.
        if writer != WRITER_UNSPECIFIED and (filed_here or issue_number is None):
            state["writer"] = writer
        _write_json(self._storage, key, state)
        # The announcement is gated on the PERSISTED flag, never on who filed.
        # In the create-then-die window the filer creates the issue and dies
        # before pushing; the adopter finds that issue via search and must then
        # announce it. Gating on `filed_here` left the incident filed but never
        # shown to a human, with nothing retrying (round 3 P1).
        if issue_number is not None and not state.get("telegram_pushed"):
            self._push_with_pending(key, self._telegram_text(kind, team_id, detail, issue_number))
            state["telegram_pushed"] = True
            _write_json(self._storage, key, state)
        return True

    def resolve_incident(
        self, kind: str, team_id: str = "", writer: str | None = None,
    ) -> bool:
        """Close + delete-to-resolve across EVERY spelling of the sentinel.

        #2844: deleting only the key this store spells stranded the DR driver's
        `global.json` carrying a CLOSED issue's number. The driver's `file_alert`
        412 branch reads that object, sees a non-open issue, finds no open issue
        and files a fresh one — a resolved incident pages again.

        If both writers filed before the alias fix shipped, the incident has two
        issues; both are closed, or the orphan outlives its sentinel and can
        never be resolved by any surface (#3030).

        #3127 resolution authority: only the kind's OWNER (the writer whose
        probes cover its recovery condition) may clear it — or a caller
        clearing a sentinel that IT opened, whose own probe observed the
        condition it is clearing. A caller with neither refuses: its evidence
        does not cover the failing dependency, so the "recovery" may be false
        and the owner re-files on its next run (one duplicate pair per cycle).
        The sentinel is left intact. Declaring no writer keeps the historical
        behaviour for callers outside the DR watcher/driver pair.
        """
        states = self._alias_states(kind, team_id)
        if not states:
            return False
        writer = self._writer if writer is None else writer
        if writer != WRITER_UNSPECIFIED:
            owner = kind_owner(kind)
            if owner != WRITER_UNSPECIFIED and owner != writer \
                    and not all(s.get("writer") == writer for _, s in states):
                logger.warning(
                    "resolution refused: %s is owned by the %s but %s is clearing "
                    "it (sentinel opened by: %s) — the caller's evidence does not "
                    "cover this kind's recovery condition (#3127)",
                    kind, owner, writer,
                    ", ".join(sorted({str(s.get("writer")) for _, s in states})),
                )
                return False
        numbers: list[int] = []
        for _, state in states:
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
            if number not in numbers:
                numbers.append(number)
        for number in numbers:
            try:
                self._close(number, "Resolved — condition cleared.")
            except Exception as e:
                logger.warning("issue close failed for %s #%s: %s", kind, number, e)
        if numbers:
            if len(numbers) > 1:
                logger.warning(
                    "incident %s carries %d issues (%s) — both spellings filed "
                    "before the #2844 alias fix; closing all",
                    kind, len(numbers), ", ".join(f"#{n}" for n in numbers),
                )
            self._push_with_pending(
                states[0][0],
                f"✅ DR resolved: {kind}" + (f" ({team_id})" if team_id else "")
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
        return True

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
