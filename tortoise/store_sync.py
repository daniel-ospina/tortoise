"""#3515 piece 5 — the recovery sweep, as one command: ``tortoise store-sync``.

Two substrates hold a captured-but-not-yet-filed session, and they are drained
TOGETHER because a sweep that drains one and not the other leaves half the
death modes unhandled:

1. **The capture spool** (``~/.tortoise/session-events/`` + the per-session
   ``*.turns.jsonl`` entries) — written by the in-process hooks, including the
   turns they managed to observe before the process died. Drained by
   :func:`tortoise.capture_spool.flush_spool`, the SAME function
   ``tortoise session drain`` calls: this module adds no second drainer.
2. **Pi's own session log** (``~/.pi/agent/sessions/**/*.jsonl``) — the only
   copy that exists when the hook never ran at all. Drained by the store-sync
   worker (``tortoise/pi-hooks/store-sync.ts``) through its per-file cursor.

The worker is TypeScript because that is what the epic pins (``§4 #3515``:
"the Pi leg, ``tortoise/pi-hooks/store-sync.ts``") and because it must share
the hook's payload builder rather than re-implement it. This Python entry point
exists for the two reasons §C pins: it is the artifact the tests drive
(``subprocess.run``, so a cursor/restart property is proven at PROCESS level),
and it gives self-hosted users a manual sync command.

An unavailable ``node`` is not a capture failure to hide: the spool half still
runs and the receipt says the log half was skipped and why. A Pi user always
has node (Pi is a node program); a non-Pi user has no Pi session log to ship.

TWO DELIBERATE ASYMMETRIES between the halves, both stated so neither reads as
an oversight:

* **Consent.** The spool leg is a TRANSMISSION primitive and carries the #3615
  consent gate (``capture_consent_enabled``), exactly like ``session capture``,
  ``sessions import`` and ``session drain`` — this is the FOURTH upload path,
  and a host whose capture is off must not have its spool POSTed by a command
  it did not think of as an upload. The LOG leg keeps the Pi seam's own
  posture (per-install consent, ``pi-hooks/README.md``): the extension is
  installed only when the user consented to that install.
* **Credential.** The credential is resolved ONCE, at the CLI, and handed to
  BOTH halves (``_cmd_store_sync`` → ``api_key`` → :func:`run_worker`, which
  passes it to the worker in its ENVIRONMENT, never on argv). The worker still
  falls back to its own chain when no key is handed to it, so it stays runnable
  on a machine with no Python tortoise install — but a caller that already
  holds a credential never leaves the worker reading a DIFFERENT store, which
  would pair a key from one chain with a URL from another (#2369 D1.1).
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

__all__ = [
    "RECEIPT_SENTINEL",
    "default_cursor_dir",
    "default_session_dir",
    "run_sweep",
    "worker_path",
]

RECEIPT_SENTINEL = "RECEIPT_JSON:"
#: Must EXCEED the worker's own ceiling, or the enclosing kill discards a run's
#: whole cursor progress (it is saved per-file AND at the end). Ceiling =
#: MAX_REQUESTS_PER_RUN (200) × REQUEST_TIMEOUT_MS (10 s) + per-file backoff.
WORKER_TIMEOUT_S = 3600


def worker_path() -> Path:
    """The shipped worker artifact (the object the artifact-guard test pins)."""
    return Path(__file__).resolve().parent / "pi-hooks" / "store-sync.ts"


def default_session_dir() -> Path:
    return Path.home() / ".pi" / "agent" / "sessions"


def default_cursor_dir() -> Path:
    """Where the backstop keeps its cursor — BESIDE the spool, never under ``HOME``.

    Derived from the spool root's PARENT (``<spool-parent>/store-sync``) because
    that is where EVERY other party already looks: ``capture_spool._store_sync_dir``
    reads the status file and the dead-letter count there, and the Pi extension
    spawns its sweep with exactly that ``--cursor-dir``. A ``~/.tortoise`` default
    made a manual ``tortoise store-sync`` write its cursor and its verdict where
    neither health-line reader looks — so a failed manual sweep's verdict was
    invisible (the line kept rendering the previous one), AND the manual run's
    cursor was a SECOND cursor over the same files, re-reading them from 0 and
    re-shipping turns the extension's cursor already covered.

    ``$HOME`` remains only the fallback for a spool root that cannot be derived —
    the point is that both parties derive ONE directory, from one input.
    """
    try:
        from tortoise.capture_spool import _store_sync_dir, spool_dir

        return _store_sync_dir(spool_dir())
    except Exception:  # a broken spool derivation must not disarm the sweep
        return Path.home() / ".tortoise" / "store-sync"


def _parse_receipt(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        if line.startswith(RECEIPT_SENTINEL):
            try:
                return json.loads(line[len(RECEIPT_SENTINEL):])
            except json.JSONDecodeError:
                return None
    return None


def run_worker(
    *,
    session_dir: Path | str | None = None,
    cursor_dir: Path | str | None = None,
    files: list[str] | None = None,
    max_files: int | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
    api_url: str | None = None,
    api_key: str | None = None,
    exclude_session_id: str | None = None,
    node: str | None = None,
    timeout: int = WORKER_TIMEOUT_S,
) -> dict[str, Any]:
    """Run the store-sync worker as a PROCESS and return its receipt.

    Process-level on purpose: the cursor's crash-consistency claim is about a
    process that exits and a later process that resumes, which an in-process
    call cannot produce.

    ``api_key``/``api_url`` are the credential the CALLER resolved (env →
    ``~/.tortoise/credentials.json``). They are handed to the worker in its
    ENVIRONMENT — never argv, which every process on the box can read — so the
    worker cannot pair a key from one store with a URL from another. When both
    are absent the worker resolves its own chain, which is what keeps this
    module runnable on a machine that has no Python install.
    """
    artifact = worker_path()
    if not artifact.exists():  # pragma: no cover - the artifact guard covers this
        return {
            "ok": False,
            # `worker_status` is the NOT-RUN reason; `skipped` on a real receipt
            # is a COUNT (files already up to date), so overloading it made a
            # failed half report ok whenever any file was current.
            "worker_status": "worker_missing",
            "detail": f"the store-sync worker is absent at {artifact}",
            "files": [],
        }
    exe = node or shutil.which("node")
    if not exe:
        return {
            "ok": False,
            "worker_status": "node_unavailable",
            "detail": "no `node` on PATH — the Pi log half of the sweep did not run",
            "files": [],
        }
    argv = [exe, str(artifact)]
    if session_dir:
        argv += ["--session-dir", str(session_dir)]
    if cursor_dir:
        argv += ["--cursor-dir", str(cursor_dir)]
    for f in files or []:
        argv += ["--file", str(f)]
    if max_files is not None:
        argv += ["--max-files", str(max_files)]
    if max_requests is not None:
        argv += ["--max-requests", str(max_requests)]
    if api_url:
        argv += ["--api-url", str(api_url)]
    if exclude_session_id:
        argv += ["--exclude-session-id", str(exclude_session_id)]
    if dry_run:
        argv += ["--dry-run"]
    argv += ["--json"]
    env = None
    if api_key:
        # The worker's OWN resolution reads these, so the key and the URL come
        # from the SAME chain by construction (co-source, #2369 D1.1).
        env = {**os.environ, "TORTOISE_API_KEY": api_key}
        if api_url:
            env["TORTOISE_API_URL"] = api_url
    try:
        proc = subprocess.run(  # fixed argv, no shell
            argv, capture_output=True, text=True, timeout=timeout, check=False, env=env
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "worker_status": "timeout",
                "detail": f"worker exceeded {timeout}s", "files": []}
    except OSError as exc:
        # An unusable `node` (a path that exists in PATH but not on disk, a
        # permission refusal) must be a NAMED not-run reason, not a traceback:
        # the sweep is spawned detached with stdio ignored, so an exception
        # here would silently end the recovery sweep (#3963's shape).
        return {"ok": False, "worker_status": "node_unavailable",
                "detail": f"cannot execute {exe}: {exc}", "files": []}
    receipt = _parse_receipt(proc.stdout or "")
    if receipt is None:
        return {
            "ok": False,
            "worker_status": "no_receipt",
            "detail": (proc.stderr or proc.stdout or "worker produced no receipt").strip()[-2000:],
            "files": [],
        }
    receipt["ok"] = proc.returncode == 0 and not receipt.get("errors")
    if proc.returncode != 0:
        receipt["detail"] = (proc.stderr or "").strip()[-2000:]
    return receipt


def run_sweep(
    *,
    api_key: str | None = None,
    api_url: str | None = None,
    spool: bool = True,
    session_dir: Path | str | None = None,
    cursor_dir: Path | str | None = None,
    files: list[str] | None = None,
    max_files: int | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
    exclude_session_id: str | None = None,
    node: str | None = None,
    post=None,
) -> dict[str, Any]:
    """The recovery sweep: drain the SPOOL, then the Pi session LOG.

    ``post``/``spool=False`` exist so tests can drive either half in isolation
    (a spool drain with a stubbed transport must not reach the network).

    ``dry_run`` is honoured by BOTH halves. The log half already reported
    without moving a cursor; the spool half used to be drained regardless, so a
    command advertised as "Plan only — POST nothing, move no cursor" uploaded
    every spooled transcript and stamped `filed_key` / backoff / terminal
    markers into the spool — an irreversible mutation of the very substrate the
    plan was asked to REPORT on, which made the plan unreproducible.
    """
    receipt: dict[str, Any] = {"spool": None, "session_events": None,
                              "log": None, "ok": True}
    # `--file` names ONE session file for the LOG half; for the spool half the
    # equivalent is that session's spool entry. Both filters are forwarded so
    # neither flag is silently half-applied (#3515 review: `--exclude-session-id`
    # applied only to the log half still filed the LIVE session from the spool,
    # which makes the server skip extraction for every later turn of it).
    only_session_id: str | None = None
    if files and len(files) == 1:
        only_session_id = _session_id_from_filename(files[0])
    if spool and dry_run:
        # No transport, no consent read, no spool mutation: a plan-only run must
        # leave the spool byte-identical (`flush_spool` POSTs each entry and then
        # stamps the entry's outcome, so merely "filing nothing on success" is
        # not enough — the stamp IS the mutation). The entries a real run would
        # file are still there afterwards; nothing is counted as drained.
        receipt["spool"] = {
            "status": "dry_run",
            "detail": (
                "plan only — the spool half was not drained; nothing was posted "
                "and no spool entry was stamped"
            ),
        }
    elif spool:
        try:
            # #3615: this command POSTs spooled turns, so it carries the SAME
            # consent gate as `session capture` / `session drain` /
            # `sessions import`. Without it a host that never consented to
            # capture had its locally-spooled (deliberately ungated) turns
            # uploaded by a command nobody classified as an upload path.
            from tortoise.capture_consent import (
                capture_consent_enabled,
                capture_declined_reason,
            )
            from tortoise.capture_spool import flush_spool, spool_dir

            if not capture_consent_enabled():
                receipt["spool"] = {
                    "status": "declined",
                    "detail": capture_declined_reason()
                    or "capture is off — the spool half was not drained",
                }
            elif post is None:
                if not api_key:
                    receipt["spool"] = {
                        "status": "no_credential",
                        "detail": "no API key — the spool half was not drained",
                    }
                    # A substrate that was NOT drained is not a success.
                    receipt["ok"] = False
                else:
                    from tortoise.__main__ import _session_post

                    summary_obj = flush_spool(
                        spool_dir(),
                        _session_post(api_key, api_url or ""),
                        only_session_id=only_session_id,
                        exclude_session_id=exclude_session_id,
                    )
                    receipt["spool"] = _summary_dict(summary_obj)
            else:
                summary_obj = flush_spool(
                    spool_dir(),
                    post,
                    only_session_id=only_session_id,
                    exclude_session_id=exclude_session_id,
                )
                receipt["spool"] = _summary_dict(summary_obj)
        except Exception as exc:  # a broken spool must not kill the log half
            receipt["spool"] = {"error": f"{type(exc).__name__}: {exc}"}
            receipt["ok"] = False
    # ── #6388: the THIRD substrate ───────────────────────────────────────────
    # `~/.tortoise/session-events/` — the day-addressed JSONL `reflect-hook`
    # appends on shutdown. This module's own docstring has named it as item 1
    # since #3515 while the spool leg only ever walked `<spool>/entries/`, so the
    # substrate had a writer and NO drain (#6388: 61,997 records / 516 MB, and
    # `~/.tortoise/store-sync/` never existed as proof that nothing consumed it).
    #
    # It is a LEG of this sweep, not a second drainer (#1178): the same consent
    # gate, the same credential, and the same transport callable the spool leg
    # files through. A dry run reports without moving its cursor, exactly like
    # the spool leg above.
    try:
        from tortoise.capture_consent import (
            capture_consent_enabled,
            capture_declined_reason,
        )
        from tortoise.session_events_drain import drain_session_events

        if dry_run:
            # A PLAN still answers "how big is the backlog": the leg counts what
            # it would drain and moves nothing.
            receipt["session_events"] = drain_session_events(
                lambda _payload: None, dry_run=True,
                store=cursor_dir if cursor_dir is not None else default_cursor_dir(),
                only_session_id=only_session_id)
        elif not capture_consent_enabled():
            receipt["session_events"] = {
                "status": "declined",
                "detail": capture_declined_reason()
                or "capture is off — the session-events leg was not drained",
            }
        else:
            transport = post
            if transport is None:
                if not api_key:
                    receipt["session_events"] = {
                        "status": "no_credential",
                        "detail": ("no API key — the session-events leg was not "
                                   "drained"),
                    }
                    # A substrate that was NOT drained is not a success — the
                    # same verdict the spool leg above records.
                    receipt["ok"] = False
                else:
                    from tortoise.__main__ import _session_post

                    transport = _session_post(api_key, api_url or "")
            if transport is not None:
                summary_events = drain_session_events(
                    transport,
                    store=cursor_dir if cursor_dir is not None
                    else default_cursor_dir(),
                    exclude_session_id=exclude_session_id,
                    # BOTH filters, for the reason the comment above gives: a
                    # `--file <session>` run that forwarded `exclude` only would
                    # drain every session's records from this substrate — the
                    # flag silently half-applied, which is the class that
                    # comment was written for (#3515 review).
                    only_session_id=only_session_id,
                )
                receipt["session_events"] = summary_events
                if summary_events.get("cursor_error"):
                    # The backlog moved but its position could not be saved: the
                    # next sweep re-POSTs it. Reported, never swallowed.
                    receipt["ok"] = False
                # A record that can never be filed is dead-lettered, not a lost
                # capture and not a failure of the sweep; a TRANSIENT refusal
                # that stopped the leg is the same class as the log half's
                # `withheld` and does not fail the sweep either (the cursor did
                # not move, so nothing was lost).
    except Exception as exc:  # a broken substrate must not kill the other legs
        receipt["session_events"] = {"error": f"{type(exc).__name__}: {exc}"}
        receipt["ok"] = False

    receipt["log"] = run_worker(
        session_dir=session_dir if session_dir is not None else default_session_dir(),
        cursor_dir=cursor_dir if cursor_dir is not None else default_cursor_dir(),
        files=files,
        max_files=max_files,
        max_requests=max_requests,
        dry_run=dry_run,
        api_url=api_url,
        api_key=api_key,
        exclude_session_id=exclude_session_id,
        node=node,
    )
    # Gate on the worker's OWN verdict, not on a key that also carries a count.
    if receipt["log"] is not None and receipt["log"].get("ok") is False:
        receipt["ok"] = False
    # A batch the server RETAINED and a window REFUSED as shrunk both exit 0 with
    # no ``errors[]``, so the worker's own verdict is TRUE for a sweep that
    # delivered nothing. That made ``capture: OK`` the report over an inert
    # backstop — the false PASS this seam exists to end, one level down: a
    # rotated/expired key (401/403/407 are retained on purpose — crossing the
    # cursor would destroy the sessions), an exhausted quota (402), a 429/5xx
    # outage, or a recording toggle switched off (the policy 409). The turns are
    # SAFE either way (the cursor did not move); what must not happen is the only
    # user-visible surface calling that healthy.
    #
    # ALSO when ``errors[]`` already failed the sweep: the reason precedence in
    # ``write_sweep_status`` prefers ``withheld`` over the error token, so without
    # this the first error (often an unfixable ``no_header_id``) would mask the
    # actionable refusal filed in the same sweep.
    #
    # And a cursor that could not be PERSISTED is the same class: the turns posted
    # but the offset did not move, so the next sweep re-POSTs the same window. That
    # signal is named by the worker (``cursorWriteError``) and was only printed on
    # stderr, which the production caller discards.
    if receipt["log"] is not None and not receipt["log"].get("worker_status"):
        withheld = _withheld_token(receipt["log"])
        if not withheld and receipt["log"].get("cursorWriteError"):
            withheld = "cursor_write_failed"
        if withheld:
            receipt["log"]["withheld"] = withheld
            receipt["ok"] = False
    return receipt


#: Refusals that are NOT the backstop being inert, so they must not fail the
#: sweep.
#:
#: ``window_shrunk`` refuses ONE dead file by design (shipping the shorter window
#: would let the turn store hard-delete past it), and its refusal writes no cursor
#: row, so the same file is refused on every sweep for as long as it exists — 55 of
#: 292 real session files carry a `compaction`. Failing the sweep for it would paint
#: a PERMANENT red over a backstop working exactly as designed, and a permanent red
#: is how a line stops being read.
#:
#: ``no_header_id`` is already reported by the worker through ``errors[]``, so the
#: sweep is ``ok: false`` with that reason whether or not this function names it;
#: keeping it out of the withheld set means it cannot mask an actionable refusal
#: that is filed in the SAME sweep.
#:
#: ``409`` is deliberately NOT here. `POST /v1/sessions` answers 409 for TWO
#: different things and the worker cannot tell them apart (`postCapture` keeps no
#: headers, and its own rule forbids matching prose): the benign in-flight duplicate
#: (`_CAPTURE_SESSION_IN_FLIGHT_DETAIL`, carries `Retry-After`) AND "Session
#: recording is disabled for this team" — an org policy the user CAN reverse and
#: which never clears by retrying. Treating the code as benign would report
#: `capture: OK` for ever on a box whose recording is switched off, which is the
#: exact false PASS this verdict exists to end. Surfacing it is the fail-CLOSED
#: choice and it is self-clearing: an in-flight collision clears on the next sweep,
#: and a disabled toggle clears when the user enables it.
_NON_ACTIONABLE_REFUSALS = frozenset({"window_shrunk", "no_header_id"})


def _withheld_token(log: dict) -> str | None:
    """Why the log half HELD BACK turns it could not deliver (``None``: it held none).

    A not-run reason is its own verdict (``worker_status``) and is not "withheld",
    so it is not re-labelled here.

    A file is withheld when the worker recorded a ``failed`` outcome for it — the
    batch was RETAINED (401/402/403/407/409/429/5xx, whose ``status`` names the
    code) or refused. Non-actionable refusals are SKIPPED PER FILE and the loop keeps
    looking, because an ACTIONABLE refusal (a rotated key, an exhausted quota, a
    disabled recording toggle, an outage) must win on the same box: reporting the
    one thing nobody can act on would hide the thing they can.

    This is consulted whenever the log half RAN (``worker_status`` absent) — even
    when ``errors[]`` already failed the sweep — because ``errors[0]`` would
    otherwise mask the actionable refusal: one stray header-less ``.jsonl`` is enough
    to turn every sweep's reason into ``no_header_id`` while a rotated key goes
    unreported on every user-visible surface.
    """
    if not log or log.get("worker_status"):
        return None
    for outcome in log.get("files") or []:
        if not isinstance(outcome, dict) or outcome.get("action") != "failed":
            continue
        status = outcome.get("status")
        if isinstance(status, int) and status > 0:
            token = f"http_{status}"
        else:
            reason = outcome.get("reason")
            token = str(reason) if reason else "withheld"
        if token in _NON_ACTIONABLE_REFUSALS:
            continue
        return token
    return None


def _session_id_from_filename(path: str) -> str | None:
    """The session id a Pi/Claude session filename carries, or ``None``.

    Pi names the file after the session UUID (``2026-05-01T10-00-00Z_<uuid>.jsonl``)
    and the header ``id`` equals that UUID, so the filename is the spool key
    without reading the file. A name that carries no plausible id returns
    ``None`` rather than a guess: a wrong filter would drain the wrong entry.
    """
    stem = Path(str(path)).name
    for suffix in (".jsonl", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    tail = stem.rsplit("_", 1)[-1]
    return tail if len(tail) >= 8 and all(c.isalnum() or c in "-" for c in tail) else None


def _summary_dict(summary) -> dict[str, Any]:
    return {
        "filed": getattr(summary, "filed", 0),
        "deferred": getattr(summary, "deferred", 0),
        "skipped": getattr(summary, "skipped", 0),
        "held_back": getattr(summary, "held_back", 0),
        "held_by_backoff": getattr(summary, "held_by_backoff", 0),
        "terminal": getattr(summary, "terminal", 0),
        "discarded": len(getattr(summary, "discarded", []) or []),
    }


#: Known failure classes -> TOKENS. The health line embeds the reason inside a
#: COMMA-DELIMITED field, so a token is what keeps its grammar intact.
_REASON_TOKENS = (
    ("TORTOISE_API_KEY", "no_credential"),
    ("no session header id", "no_header_id"),
    ("EACCES", "permission_denied"),
    ("EPERM", "permission_denied"),
    ("ENOSPC", "no_space"),
    ("Unexpected token", "bad_json"),
)


#: Punctuation that would break the health line's grammar. The line renders the
#: reason inside a COMMA-DELIMITED field, so a raw `,` forges a sibling term and
#: an unquoted `—` clause reads as prose in a list of counts.
_REASON_PUNCT = set(",;—–\"'()\n\t")


def _sanitise_reason(text: str) -> str:
    """Strip the punctuation that breaks `capture: … , store-sync FAILED (<reason>)`.

    Applied ONCE, at the single write site, so no future reason source can
    bypass it — a per-producer fix was the generation-13 shape, and `spool.error`
    (a raw exception message) walked straight past it.
    """
    cleaned = "".join("-" if ch in _REASON_PUNCT else ch for ch in text)
    return " ".join(cleaned.split())[:80]


#: The ``status`` values the session-events leg reports when it did NOT drain
#: anything — the leg's own verdict, not an exception. Read by
#: :func:`_events_failure_reason` so a non-zero sweep can NAME this leg.
_EVENTS_NOT_DRAINED = frozenset({"declined", "no_credential"})


def _events_failure_reason(events: Any) -> str | None:
    """Why the SESSION-EVENTS leg did not drain, as a token — or ``None``.

    The reason chain in :func:`write_sweep_status` could only see the LOG half
    (``worker_status``/``withheld``/``errors``) and the SPOOL half
    (``spool.error``), so a failure that came ONLY from the events leg fell
    through to the generic ``"worker exited non-zero"`` — naming the node
    worker, a component that exited 0 and delivered its turns.

    Under ``--no-spool`` (the Pi extension's own cadence) the spool half is
    ``None`` entirely, so the generic fallback was the ONLY case there was: an
    events-leg failure read as a worker crash, and the leg that actually failed
    was named nowhere on the health line.

    ``status`` is consulted only for the NOT-DRAINED verdicts: a real pass
    reports ``"drained"`` / ``"dry_run"``, which are not failures. A real
    failure is the exception arm (``error``) or a cursor that could not be
    written (``cursor_error``).
    """
    if not isinstance(events, dict):
        return None
    return (events.get("cursor_error") or events.get("error")
            or (events.get("status") if events.get("status") in _EVENTS_NOT_DRAINED
                else None))


def _reason_token(errors: list[Any]) -> str | None:
    """The first error, as a known-class TOKEN or as its raw text (sanitised later)."""
    if not errors:
        return None
    text = str(errors[0])
    for needle, token in _REASON_TOKENS:
        if needle in text:
            return token
    return text


def _status_tmp_path(target: Path) -> Path:
    """The status write's temp path — UNIQUE PER PROCESS.

    TWO writers share this directory: this CLI, and the extension's own
    ``noteFailure`` (a detached sweep that never got as far as running). Both
    used the plain name ``sweep-status.json.tmp``, so the rename of one could
    publish the OTHER's payload: the extension records ``{ok: false,
    reason: "cli_missing"}``, a concurrent sweep's healthy write lands on the
    same temp, and the rename publishes the healthy content — the permanent
    failure is DROPPED and the line reads ``capture: OK``. The mirror interleave
    makes this side's ``replace`` raise ``ENOENT`` (swallowed, by contract) and
    the exit-0 corroboration then reports a hole on a box that swept fine.
    The pid is the same discriminator ``cursor-store.ts`` already uses for
    ``cursor.json``.
    """
    return target / f"sweep-status.json.tmp.{os.getpid()}"


def write_sweep_status(receipt: dict, cursor_dir: Path | str | None) -> None:
    """Persist the last sweep's outcome where a caller that DISCARDED our output can read it.

    The production caller is the Pi extension, which spawns this as a detached
    child with ``stdio="ignore"`` — so ``stdout``/``stderr``, where every
    diagnosis in ``main`` is printed, go nowhere. Without this file a worker that
    RAN AND FAILED (a ``node`` too old for the shipped ``.ts`` worker, a missing
    artifact, a non-zero exit) is indistinguishable from one that never ran, and
    the box reports ``capture: OK`` while the crash/kill recovery it was
    installed for is inert. A status file is the smallest seam that closes it:
    the health line already reads this directory for the dead-letter count.

    Best-effort by contract: a sweep must never fail because bookkeeping could
    not be written.
    """
    try:
        target = Path(cursor_dir) if cursor_dir else default_cursor_dir()
        # `0o700`, like every other `~/.tortoise` subdirectory this tree creates
        # (and like the worker's own `mkdirSync(dir, { mode: 0o700 })`).
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        # `mkdir`'s `mode` applies ONLY when it creates the directory. An
        # upgraded box already has it, created 0755 by an earlier
        # generation — so without this the 0700 intent stays unmet forever
        # on any box whose sweeps SUCCEED (this writer is then the only one
        # that runs). Best-effort: bookkeeping never fails a sweep.
        with contextlib.suppress(OSError):
            target.chmod(0o700)
        log = receipt.get("log") or {}
        errors = log.get("errors") or []
        # The persisted reason is a TOKEN, because the health line embeds it in a
        # comma-delimited field: a free-form sentence there duplicated the line's
        # own "no TORTOISE_API_KEY" and put an unquoted em-dash clause inside a
        # comma-separated list. The full text is still on stderr and in --json.
        reason = log.get("worker_status") or log.get("withheld") or _reason_token(errors)
        if not reason and receipt.get("ok") is False:
            spool = receipt.get("spool") or {}
            reason = spool.get("error") if isinstance(spool, dict) else None
        if not reason and receipt.get("ok") is False:
            # #6388: the third leg, BEFORE the generic fallback — otherwise an
            # events-only failure is persisted as a worker crash it is not.
            reason = _events_failure_reason(receipt.get("session_events"))
        if not reason and receipt.get("ok") is False:
            reason = "worker exited non-zero"
        # ONE place, after all three sources are resolved: `errors[]`,
        # `worker_status`, and `spool.error` (a raw exception message, which is
        # how a comma and an em-dash reached the line in generation 13).
        if reason:
            reason = _sanitise_reason(str(reason))
        # #6388: how far the session-events backlog moved in this sweep. The
        # absence of a per-run count is why tens of thousands of records
        # accumulated invisibly; `queued` is the number an operator watches.
        events = receipt.get("session_events")
        events_counts = events if isinstance(events, dict) else {}
        payload = {
            "version": 1,
            "session_events": {
                "filed": events_counts.get("filed", 0),
                "dead_lettered": events_counts.get("dead_lettered", 0),
                "deferred": events_counts.get("deferred", 0),
                # `held` (a live session, left queued by design) and
                # `unselected` (another session under `--file`) were computed
                # every pass and persisted NOWHERE, so the health line could not
                # distinguish "nothing left" from "everything withheld".
                "held": events_counts.get("held", 0),
                "unselected": events_counts.get("unselected", 0),
                "degraded": events_counts.get("degraded", 0),
                "queued": events_counts.get("queued", 0),
                "stuck": events_counts.get("stuck", 0),
                "stopped": events_counts.get("stopped"),
                "cursor_error": events_counts.get("cursor_error"),
                "unreadable": len(events_counts.get("unreadable") or []),
            },
            # `at` is consumed for PRECEDENCE, not for freshness: the extension
            # reads it to decide whether a record is the CLI's own name for THIS
            # sweep (and must not be replaced by its exit code). What it does
            # NOT do is age an entry out. A sweep killed before it reaches this
            # write leaves the PREVIOUS verdict standing, so one `capture: OK`
            # line can survive a backstop that died with its parent (OOM,
            # shutdown) until the next sweep completes. Named rather than
            # papered over: closing it needs a `started` marker with no matching
            # completion, which is a heartbeat, not a status.
            "ok": bool(receipt.get("ok")),
            "reason": reason,
            "shipped": log.get("shipped", 0),
            "at": time.time(),
        }
        tmp = _status_tmp_path(target)
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        # `write_text` uses the umask (0644), while the extension writes the
        # same file 0600 — so the SAME filename carried two modes, and the
        # COMMON one was the loose one. The directory is 0700 either way, so
        # this is consistency rather than exposure.
        with contextlib.suppress(OSError):
            tmp.chmod(0o600)
        tmp.replace(target / "sweep-status.json")
    except OSError:
        pass


def main(args) -> int:
    """``tortoise store-sync`` — print a receipt; never raise into the CLI."""
    receipt = run_sweep(
        api_key=getattr(args, "api_key", None),
        api_url=getattr(args, "api_url", None),
        spool=not getattr(args, "no_spool", False),
        session_dir=getattr(args, "session_dir", None),
        cursor_dir=getattr(args, "cursor_dir", None),
        files=getattr(args, "file", None),
        max_files=getattr(args, "max_files", None),
        max_requests=getattr(args, "max_requests", None),
        dry_run=getattr(args, "dry_run", False),
        exclude_session_id=getattr(args, "exclude_session_id", None),
    )
    if getattr(args, "json", False):
        print(json.dumps(receipt, indent=1, sort_keys=True))
    else:
        log = receipt.get("log") or {}
        print(
            "store-sync: "
            f"{log.get('shipped', 0)} shipped, "
            f"{log.get('skipped', 0)} up to date, "
            f"{log.get('noTurns', 0)} awaiting a turn, "
            f"{log.get('shrunk', 0)} refused (shrunk window), "
            f"{log.get('deadLettered', 0)} dead-lettered, "
            f"{log.get('dropped', 0)} dropped"
        )
        for err in (log.get("errors") or [])[:10]:
            print(f"store-sync error: {err}", file=sys.stderr)
        # The durability signals must be VISIBLE on the human path too: a
        # production caller spawns this with stdio ignored, so a count that only
        # exists under --json is a count nobody reads.
        if log.get("cursorRecovered"):
            print(f"store-sync: cursor — {log['cursorRecovered']}", file=sys.stderr)
        if log.get("cursorWriteError"):
            print(f"store-sync: cursor write FAILED — {log['cursorWriteError']} "
                  "(the next run re-ships)", file=sys.stderr)
        if log.get("worker_status"):
            print(f"store-sync: log half not run — {log.get('detail', log.get('worker_status'))}",
                  file=sys.stderr)
        if log.get("withheld"):
            # The human path must name it too: the production caller discards
            # stdout, and a withheld batch that is only visible under `--json`
            # is a batch nobody is told about.
            print(f"store-sync: withheld — {log['withheld']} "
                  "(cursor retained; the turns ship when the server accepts them)",
                  file=sys.stderr)
        if receipt.get("spool"):
            print(f"store-sync: spool — {receipt['spool']}", file=sys.stderr)
        # #6388: the third leg reports on the human path too. It was persisted
        # in `sweep-status.json` and printed by `session drain`, but NEVER here —
        # so the Pi cadence (`store-sync --no-spool`, stdio ignored) and any
        # human `tortoise store-sync` got no line at all, including for `stuck`,
        # `held`, `unreadable` and `cursor_error`. A count that reaches only
        # `--json` is a count nobody reads.
        if receipt.get("session_events"):
            events = receipt["session_events"]
            if not isinstance(events, dict):
                events = {}
            if events.get("error"):
                print(f"store-sync: session-events FAILED — {events['error']}",
                      file=sys.stderr)
            elif events.get("status") in _EVENTS_NOT_DRAINED:
                print(f"store-sync: session-events not drained — "
                      f"{events.get('status')}: {events.get('detail', '')}",
                      file=sys.stderr)
            else:
                from tortoise.session_events_drain import format_progress

                print(format_progress(events), file=sys.stderr)
    if not getattr(args, "dry_run", False):
        # A `--dry-run` is a PLAN, not an outcome. `main` used to write the
        # status unconditionally, so a human running `tortoise store-sync
        # --dry-run` on a box whose real sweep had recorded `ok: false`
        # (no credential, no worker) turned it back into a HEALTHY status and
        # erased a failure that was still true and unaddressed.
        write_sweep_status(receipt, getattr(args, "cursor_dir", None))
    return 0 if receipt.get("ok") else 1
