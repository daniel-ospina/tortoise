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
- ``resolve_incident`` closes the issue, pushes a "resolved" Telegram message,
  then deletes the dedup object (delete-to-resolve). ``resolve_incident_state``
  is the same operation reporting WHICH fact it established
  (:class:`ResolveOutcome`: RESOLVED / ABSENT / SKIPPED_FRESH) — the bool form
  stays for the callers that only branch on it (#3820 cycle-7).
- ``incident_open`` is a read-only presence check on the same dedup object —
  no close, no delete — so a caller holding an armed alert window can ask
  whether the incident behind it still exists (#3820 cycle-9 P1).
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
_PENDING_PREFIX = "ops/pending-push/"
_SUPPRESSION_KEY = "ops/suppression.json"

FileIssue = Callable[[str, str], int]      # (title, body) -> issue number
CloseIssue = Callable[[int, str | None], None]
SearchOpen = Callable[[str, str], list[int]]  # (kind, org_id) -> open issue numbers
PushTelegram = Callable[[str], None]


def _read_json(storage, key: str) -> dict[str, Any]:
    try:
        parsed = json.loads(storage.download(key))
        return parsed if isinstance(parsed, dict) else {}
    except (KeyError, ValueError):
        return {}


def _write_json(storage, key: str, data: dict[str, Any]) -> None:
    storage.upload(key, json.dumps(data, indent=2).encode("utf-8"), content_type="application/json")


def _parse_iso(value: Any) -> datetime | None:
    """Best-effort ISO parse — a corrupt/missing timestamp never raises (it simply
    means "no cooldown recorded")."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # noqa: UP017


class CloseCooldown(RuntimeError):
    """Raised when a close is skipped because a recent attempt for the same
    incident failed (bounded-retry backoff, final-cycle review P1).

    Not an error condition: the incident is simply still open and the caller must
    keep treating the subject as unresolved. It exists as a distinct type so the
    containment layers can log it at INFO without a traceback.
    """


class ResolveOutcome(Enum):
    """The three facts a resolve attempt can establish (#3820 cycle-7).

    ``resolve_incident`` squashes these into ``bool`` for its existing
    callers. The analytics sink needs the distinction because its in-process
    state must be driven by WHICH fact holds, not by a guess about what a
    ``False`` meant.

    * ``RESOLVED``      — an incident was open and has been closed + deleted.
    * ``ABSENT``        — no incident to resolve.
    * ``SKIPPED_FRESH`` — an incident IS open, but was filed at/after the
                          caller's ``before`` bound, so this attempt left it
                          untouched. The caller must stay pending: an incident
                          is live and has NOT been resolved.
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
        self._repo = repo
        self._assignee = assignee
        self._now = now or (lambda: datetime.now(timezone.utc))  # noqa: UP017
        # Backoff after a failed close (final-cycle review P1: a permanently
        # failing close would otherwise be retried every poll — 2 GitHub writes
        # each, plus a duplicate audit comment because the comment POST precedes
        # the state PATCH — with no cap. One attempt per window bounds both the
        # comment spam and the write burst).
        self._close_cooldown_min = close_cooldown_min

    def _clock(self) -> datetime:
        """Current time — evaluated per call so time-based suppression expires
        correctly on a long-lived store (review P2-6)."""
        return self._now() if callable(self._now) else self._now

    # ── helpers ─────────────────────────────────────────────────────────────
    def _key(self, kind: str, org_id: str) -> str:
        # NOTE: the subject is used verbatim (only an EMPTY one becomes "_"), so
        # the literal subject "global" is a DIFFERENT object from the platform
        # "_" — the restore-drill path files exactly that, and a resolver asked to
        # clear a `global` object must pass "global", not "" (cycle-3 review P2).
        safe = org_id or "_"
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
        """
        return bool(_read_json(self._storage, self._key(kind, org_id)))

    def open_incident(self, kind: str, org_id: str = "", detail: dict | None = None) -> bool:
        """Open (or re-use) an incident. True if this call is the filer.

        The BOOL contract is preserved deliberately (#3820 cycle-8):
        ``backup_watcher.py`` and the drill endpoints branch on the boolean,
        and returning a truthy :class:`OpenOutcome` here would read every
        branch as success. The tri-state fact lives in
        :meth:`open_incident_state`; this is its ``is FILED``.
        """
        return (
            self.open_incident_state(kind, org_id, detail) is OpenOutcome.FILED
        )

    def open_incident_state(self, kind: str, org_id: str = "",
                            detail: dict | None = None) -> OpenOutcome:
        """Open (or re-use) an incident, reporting WHICH fact was established.

        ONE read of the time-dependent suppression predicate, so the reason a
        caller is told cannot disagree with the decision this call made
        (#3820 cycle-8 P2-2): the previous shape had the caller re-ask
        ``suppression_active`` at a LATER instant, so a pause withdrawn in
        between reported a dedup hit for an incident that was never created —
        cycle-6 P2-1 re-created in the opposite window.
        """
        detail = detail or {}
        if self._suppressed(kind):
            return OpenOutcome.SUPPRESSED
        key = self._key(kind, org_id)
        placeholder = {
            "kind": kind,
            "org_id": org_id,
            "detail": detail,
            "filed_at": self._clock().isoformat(),
            "issue_number": None,
            "telegram_pushed": False,
        }
        created = self._storage.create_if_not_exists(key, json.dumps(placeholder).encode())
        if created:
            self._become_filer(kind, org_id, detail, key, placeholder)
            return OpenOutcome.FILED
        # 412 — adopt the winner's object; never double-file.
        existing = _read_json(self._storage, key)
        if existing.get("issue_number"):
            return OpenOutcome.DEDUP  # already filed — nothing to do
        # Placeholder (winner died mid-filing): become the filer via GH-search
        # fallback to avoid duplicates.
        self._become_filer(kind, org_id, detail, key, existing)
        return OpenOutcome.FILED

    def _become_filer(self, kind, org_id, detail, key, state) -> bool:
        issue_number = None
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
            # #3029 fail-closed: a FAILED search is not an empty search. Filing
            # here would create a duplicate issue for an incident that may
            # already be tracked (the search is the only dedup left when the
            # create-once object is a placeholder). Leave the placeholder in
            # place — it carries no issue_number, so the next poll re-enters this
            # branch and retries; nothing is lost, only delayed.
            #
            # ERROR level, not warning (#3029 review): nothing human-visible
            # fires on this branch, so while it persists the ENTIRE app-side alert
            # path is deaf. A persistent search failure (e.g. the search quota
            # exhausting under a per-graph burst) must at least be visible in the
            # logs; wiring a deferred-filing signal into `/status` + an
            # ALERTER_DOWN writer is the tracked follow-up.
            logger.error(
                "incident search failed for %s/%s (%s): %s — filing DEFERRED "
                "(a failed search is not 'no incident'; the whole alert path is "
                "deaf until the search recovers)",
                kind, org_id or "global", type(e).__name__, e,
            )
            _write_json(self._storage, key, state)
            return False
        if issue_number is None:
            try:
                issue_number = self._file(
                    self._title(kind, org_id, detail), self._body(kind, org_id, detail)
                )
            except Exception as e:
                logger.warning("incident filing failed for %s: %s — will adopt on next poll", kind, e)
        state["issue_number"] = issue_number
        state["detail"] = detail
        _write_json(self._storage, key, state)
        if issue_number is not None:
            self._push_with_pending(key, self._telegram_text(kind, org_id, detail, issue_number))
            state["telegram_pushed"] = True
            _write_json(self._storage, key, state)
        return True

    def open_subjects(self, kind: str, *, strict: bool = False) -> set[str]:
        """The subjects with an OPEN dedup object for ``kind`` (#3030 review).

        One LIST instead of an R2 read per candidate: the sweep endpoint must not
        issue a GET per graph per run just to discover that nothing is open — at a
        few thousand graphs that adds minutes to a request held under the sweep
        lock. Returns the raw key segments.

        It returns dedup-KEY segments, of which there are three shapes (#3030
        cycle-3 review): a live incident (a truthy ``issue_number``), a
        **placeholder** (filing was deferred on a failed search — no issue yet),
        and a **tombstone** (a successful close whose delete failed —
        ``resolve_failed_at`` set, no ``issue_number``). All three are "open" for
        the purpose of resolution: clearing a placeholder or tombstone is exactly
        what a caller wants, and ``resolve_incident`` handles all three. A caller
        that reports what it cleared should not call these "incidents closed" —
        only the live shape closes an issue.

        Key-shape caveat: a platform subject is stored as ``"_"`` by ``_key()``,
        but a caller that passes the literal subject ``"global"`` gets a
        ``global.json`` object — the restore-drill path files exactly that. Both
        spellings can therefore appear here, and a matcher must accept either for
        a platform subject (and then RESOLVE THE SPELLING IT MATCHED: the two are
        different objects). The sweep's four kinds are only ever filed with
        ``org_id=""`` (→ ``"_"``), so this does not affect them today.

        Fails SAFE: a listing error returns the empty set, so nothing is resolved
        on a read the caller could not perform. With ``strict=True`` it re-raises
        instead, for a caller that must distinguish "nothing open" from "could not
        look" in its own report (the sweep endpoint does — a silent empty set made
        an R2 LIST outage read as a clean run).
        """
        prefix = f"{DEDUP_PREFIX}{kind}/"
        try:
            keys = self._storage.list(prefix)
        except Exception as e:
            logger.warning(
                "open_subjects(%s) list failed: %s — resolving nothing this run", kind, e
            )
            if strict:
                raise
            return set()
        return {
            k[len(prefix):-len(".json")]
            for k in keys
            if k.startswith(prefix) and k.endswith(".json")
        }

    def resolve_incident_state(self, kind: str, org_id: str = "",
                               *, before: datetime | None = None) -> ResolveOutcome:
        """Close + delete-to-resolve, reporting WHICH fact was established.

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

        Ordering matters (#3029/#3031 review): the issue close must SUCCEED
        before we announce a resolution and delete the dedup object. A
        swallowed close failure would push "✅ DR resolved", delete the object,
        and leave the issue open — so the next poll re-files, adopts the
        still-open issue, and pushes "🚨 DR alert" again: a ✅/🚨 flip every
        poll and a false all-clear in the channel whose whole job is
        truthfulness.

        So a failed close **RAISES** (cycle-2 review P1): nothing is announced
        and nothing is deleted, and the caller must treat the subject as still
        open. A raise is not an ``ABSENT`` result — ``ABSENT`` unambiguously
        means "nothing was open", the ordinary case, which must NOT be
        confused with failure (a caller that retried on every falsy return
        would spin forever). Callers that iterate a shrunken universe therefore
        keep a subject whose close RAISED pending for the next poll (see
        ``backup_watcher._resolve_vanished_graphs``), and the failure is
        recorded (``close_failed_at``/``close_failures``) so the retry backs
        off for ``close_cooldown_min`` instead of hammering GitHub.
        """
        key = self._key(kind, org_id)
        state = _read_json(self._storage, key)
        if not state:
            return ResolveOutcome.ABSENT
        if before is not None:
            filed_at = state.get("filed_at")
            if filed_at:
                try:
                    if datetime.fromisoformat(filed_at) >= before:
                        return ResolveOutcome.SKIPPED_FRESH
                except (TypeError, ValueError):
                    pass  # an uncomparable stamp falls through to resolve
        number = state.get("issue_number")
        if number:
            # Bounded retry (final-cycle review P1): skip the attempt while a
            # recent failure is cooling down. Raise the dedicated type so callers
            # keep the subject PENDING (a plain False would read as "nothing open"
            # and retire it).
            failed_at = _parse_iso(state.get("close_failed_at"))
            if failed_at is not None:
                age_min = (self._clock() - failed_at).total_seconds() / 60.0
                if age_min < self._close_cooldown_min:
                    raise CloseCooldown(
                        f"{kind} #{number}: last close attempt failed "
                        f"{age_min:.0f} min ago — retrying after "
                        f"{self._close_cooldown_min:.0f} min"
                        + (f" ({org_id})" if org_id else "")
                    )
            try:
                self._close(int(number), "Resolved — condition cleared.")
            except Exception as e:
                # Record the failure so the next attempts back off, then re-raise:
                # nothing is announced and the object is kept, so the incident
                # stays OPEN and is retried (after the cooldown).
                state["close_failed_at"] = self._clock().isoformat()
                state["close_failures"] = int(state.get("close_failures") or 0) + 1
                try:
                    _write_json(self._storage, key, state)
                except Exception:
                    logger.exception("could not record a close failure for %s", key)
                logger.warning(
                    "issue close failed for %s #%s: %s — leaving the incident OPEN "
                    "(no resolution announced, object kept); retrying after "
                    "%.0f min",
                    kind, number, e, self._close_cooldown_min,
                )
                raise
            self._push_with_pending(
                key, f"✅ DR resolved: {kind}" + (f" ({org_id})" if org_id else "") + f" — issue #{number}"
            )
        try:
            self._storage.delete(key)
        except Exception as e:
            # The close succeeded but the object survived carrying a live
            # issue_number. Left as-is, the next recurrence would be adopted by
            # an issue that is already CLOSED and silently swallowed (the
            # #2796/#2844 class). Clear the number so a recurrence re-files, and
            # report loudly rather than pretending the resolution was clean.
            state.pop("issue_number", None)
            state["resolve_failed_at"] = self._clock().isoformat()
            try:
                _write_json(self._storage, key, state)
            except Exception:
                logger.exception(
                    "could not delete NOR rewrite %s after a successful close — a "
                    "recurrence may be adopted by the already-closed issue #%s",
                    key, number,
                )
            logger.error(
                "dedup object %s could not be deleted after closing #%s: %s — "
                "cleared its issue_number so a recurrence re-files", key, number, e,
            )
        return ResolveOutcome.RESOLVED

    def resolve_incident(self, kind: str, org_id: str = "",
                         *, before: datetime | None = None) -> bool:
        """Close + delete-to-resolve. True if an incident was open, False if not.

        The BOOL contract is preserved deliberately (#3820 cycle-7): the
        callers (``backup_watcher.py``, ``hosted_api.py``,
        ``test_alert_store.py``) branch on ``if store.resolve_incident(...)``,
        and returning a truthy :class:`ResolveOutcome` from here would silently
        make ``SKIPPED_FRESH`` and ``ABSENT`` read as success. The tri-state
        fact lives in :meth:`resolve_incident_state`; this is its
        ``is RESOLVED``.

        ``before`` bounds WHICH incident may be resolved, and a failed close
        RAISES rather than reporting a clean resolution — see
        :meth:`resolve_incident_state`, which owns both semantics.
        """
        return (
            self.resolve_incident_state(kind, org_id, before=before)
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
