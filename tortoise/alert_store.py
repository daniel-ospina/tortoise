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
        repo: str,
        assignee: str | None = None,
        now: datetime | None = None,
    ) -> None:
        self._storage = storage
        self._file = file_issue
        self._close = close_issue
        self._search = search_open
        self._push = push_telegram
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
        return [(k, state) for k in self._keys(kind, team_id) if (state := _read_json(self._storage, k))]

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
    def open_incident(self, kind: str, team_id: str = "", detail: dict | None = None) -> bool:
        """Open (or re-use) an incident. True if this call is the filer."""
        detail = detail or {}
        if self._suppressed(kind):
            return False
        key = self._key(kind, team_id)
        # #2844: a sibling spelling (the DR driver's `global.json`) may already
        # own this incident. Adopt it before creating our own sentinel, or one
        # condition gets two create-once points and two issues.
        adopted = self._alias_states(kind, team_id)
        if adopted:
            return self._adopt_alias(kind, team_id, detail, adopted)
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
            # creating. If its sentinel recorded an issue, it IS the filer —
            # drop ours rather than file a second issue.
            if any(s.get("issue_number") for k, s in self._alias_states(kind, team_id) if k != key):
                self._storage.delete(key)
                return False
            return self._become_filer(kind, team_id, detail, key, placeholder)
        # 412 — adopt the winner's object; never double-file.
        existing = _read_json(self._storage, key)
        if existing.get("issue_number"):
            return False  # already filed — nothing to do
        # Placeholder (winner died mid-filing): become the filer via GH-search
        # fallback to avoid duplicates.
        return self._become_filer(kind, team_id, detail, key, existing)

    def _adopt_alias(self, kind, team_id, detail, adopted) -> bool:
        """Adopt the issue a SIBLING spelling already recorded (#2844).

        If the sibling carries an issue number the incident is already tracked —
        stay silent. If it is a placeholder (its writer died between create and
        backfill, or the driver's object is created before its issue lands),
        become the filer ON THAT KEY so the incident keeps ONE linearization
        point instead of the two spellings racing.
        """
        for key, state in adopted:
            if state.get("issue_number"):
                logger.info(
                    "dedup: %s already tracked via alias %s (issue #%s) — no-op",
                    kind, key, state["issue_number"],
                )
                return False
        key, state = adopted[0]
        return self._become_filer(kind, team_id, detail, key, state)

    def _become_filer(self, kind, team_id, detail, key, state) -> bool:
        issue_number = None
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
            except Exception as e:
                logger.warning("incident filing failed for %s: %s — will adopt on next poll", kind, e)
        state["issue_number"] = issue_number
        state["detail"] = detail
        _write_json(self._storage, key, state)
        if issue_number is not None:
            self._push_with_pending(key, self._telegram_text(kind, team_id, detail, issue_number))
            state["telegram_pushed"] = True
            _write_json(self._storage, key, state)
        return True

    def resolve_incident(self, kind: str, team_id: str = "") -> bool:
        """Close + delete-to-resolve across EVERY spelling of the sentinel.

        #2844: deleting only the key this store spells stranded the DR driver's
        `global.json` carrying a CLOSED issue's number. The driver's `file_alert`
        412 branch reads that object, sees a non-open issue, finds no open issue
        and files a fresh one — a resolved incident pages again.

        If both writers filed before the alias fix shipped, the incident has two
        issues; both are closed, or the orphan outlives its sentinel and can
        never be resolved by any surface (#3030).
        """
        states = self._alias_states(kind, team_id)
        if not states:
            return False
        numbers: list[int] = []
        for _, state in states:
            number = state.get("issue_number")
            if number and int(number) not in numbers:
                numbers.append(int(number))
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
                f"✅ DR resolved: {kind}" + (f" ({team_id})" if team_id else "") + f" — issue #{numbers[0]}",
            )
        # delete-to-resolve across the whole alias set: a surviving spelling
        # would adopt the next recurrence and swallow it (the #2796 class).
        for key, _ in states:
            self._storage.delete(key)
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
