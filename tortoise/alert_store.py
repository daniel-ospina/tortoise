"""Alert store — the per-incident lifecycle behind the dual-channel sink.

GitHub issue (agent-visible) + Telegram push (human-visible, absorbed #673),
with the R2 create-once object as the dedup LINEARIZATION POINT:

- ``open_incident`` attempts ``create_if_not_exists`` FIRST. The winner files
  the issue + pushes Telegram and backfills. A 412 loser ADOPTS the winner's
  object: if it carries an issue_number it skips; if it is a placeholder
  (winner died between create and backfill), the adopter becomes the filer via
  the GH-search fallback. The create-then-die window can never leave an
  incident permanently silent.
- Stable key per (kind, team): ``ops/alerts/{KIND}/{team-or-underscore}.json``
  — while the incident is open, repeats reuse it (one issue + one Telegram);
  recovery DELETES it (delete-to-resolve ⇒ a later recurrence is a new
  incident with a new issue number).
- ``resolve_incident`` closes the issue, pushes a "resolved" Telegram message,
  then deletes the dedup object.
- Suppression: ``ops/suppression.json`` ``{kind: {until: ISO}}`` pauses a kind.
- Pending-push: a Telegram failure writes ``ops/pending-push/``; the daemon
  processes it on its next poll (``retry_pending``).

The store is decoupled from HTTP: the caller injects ``file_issue`` /
``close_issue`` / ``search_open`` / ``push_telegram`` callables (real impls in
``github_issue.py`` + a small urllib Telegram client), so tests use fakes and
MemoryStorage.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEDUP_PREFIX = "ops/alerts/"
_PENDING_PREFIX = "ops/pending-push/"
_SUPPRESSION_KEY = "ops/suppression.json"

FileIssue = Callable[[str, str], int]      # (title, body) -> issue number
CloseIssue = Callable[[int, str | None], None]
SearchOpen = Callable[[str], list[int]]    # kind -> open issue numbers
PushTelegram = Callable[[str], None]


def telegram_send(bot_token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    """Minimal Telegram Bot API sendMessage via stdlib urllib."""
    import urllib.parse
    import urllib.request

    url = (
        f"https://api.telegram.org/bot{bot_token}/sendMessage"
        f"?chat_id={urllib.parse.quote(str(chat_id))}"
        f"&text={urllib.parse.quote(text)}"
    )
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            resp.read()
    except Exception as e:
        raise RuntimeError(f"telegram send failed: {e}") from e


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
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _clock(self) -> datetime:
        """Current time — evaluated per call so time-based suppression expires
        correctly on a long-lived store (review P2-6)."""
        return self._now() if callable(self._now) else self._now

    # ── helpers ─────────────────────────────────────────────────────────────
    def _key(self, kind: str, team_id: str) -> str:
        safe = team_id or "_"
        return f"{DEDUP_PREFIX}{kind}/{safe}.json"

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
            return self._become_filer(kind, team_id, detail, key, placeholder)
        # 412 — adopt the winner's object; never double-file.
        existing = _read_json(self._storage, key)
        if existing.get("issue_number"):
            return False  # already filed — nothing to do
        # Placeholder (winner died mid-filing): become the filer via GH-search
        # fallback to avoid duplicates.
        return self._become_filer(kind, team_id, detail, key, existing)

    def _become_filer(self, kind, team_id, detail, key, state) -> bool:
        issue_number = None
        try:
            hits = self._search(kind)  # GH-search fallback dedup
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
        """Close + delete-to-resolve. True if an incident was open."""
        key = self._key(kind, team_id)
        state = _read_json(self._storage, key)
        if not state:
            return False
        number = state.get("issue_number")
        if number:
            try:
                self._close(int(number), "Resolved — condition cleared.")
            except Exception as e:
                logger.warning("issue close failed for %s #%s: %s", kind, number, e)
            self._push_with_pending(
                key, f"✅ DR resolved: {kind}" + (f" ({team_id})" if team_id else "") + f" — issue #{number}"
            )
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
        """Retry parked pushes (daemon calls this each poll). Returns count sent."""
        sent = 0
        for k in self._storage.list(_PENDING_PREFIX):
            state = _read_json(self._storage, k)
            text = state.get("text")
            if not text:
                self._storage.delete(k)
                continue
            try:
                self._push(text)
                self._storage.delete(k)
                sent += 1
            except Exception as e:
                logger.warning("pending push retry failed (%s): %s", k, e)
        return sent
