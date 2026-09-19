"""#3809 — one-command behavioural verification of a harness capture install.

``tortoise session verify --harness <h>`` answers the question a human
otherwise answers by hand — *"does this machine's install actually capture a
session right now?"* — and exits non-zero on any broken link.  It walks the
whole chain for the four beta harnesses (claude, pi, cursor, codex):

1. **installed** — the seam is present AND the harness's own registration
   loader resolves it, AND the installed artifact actually FIRES (the
   registered command is executed with the harness's documented event
   payload);
2. **captured** — a ``session_capture_receipt_<harness>`` advanced and the
   session is retrievable by id with the expected turns;
3. **memory** — the session appears in the graph as a ``Source`` and its turns
   were extracted into memory Points.

BEHAVIOURAL, NOT A SOURCE-TEXT SCAN.  Every guard executes the path and
asserts the resolved outcome; nothing here greps an artifact.  The install
facts are resolved through the ONE shared mechanism — ``tortoise.hook_install``
(``get_layout`` / ``default_root`` / ``detect_install`` / ``registered_commands``)
and ``tortoise.capture_install`` — never re-derived here.  The receipt key
comes from ``tortoise.capture_receipts``, the same definition the server uses.

HERMETICITY.  The probe transcript and the event payload are synthesized; the
seam execution is real.  This module never writes the user's install.  The one
write is the probe SESSION, which is unmistakably named
``verify-<harness>-<timestamp>`` and deleted whenever a fire was ATTEMPTED —
keyed on the capture attempt, never on whether the fire *reported* success (a
timed-out fire has still launched the seam) and never on whether the read
observed it.  The fire and the observation share ONE ``try``/``finally``, so
cleanup runs on every path and there is no second site that could drift.  The
deletion is reported, and a failed deletion is surfaced (exit non-zero), never
swallowed.

DETACHING SEAMS.  Codex's (and Cursor's) seam hands its capture to a worker
that OUTLIVES the hook (``nohup … & disown``), so the capture can land after
this command has returned.  For those harnesses a ``DELETE 404`` is NOT proof
that nothing was left behind — it only proves nothing had landed yet — and the
report says the capture was still in flight rather than claiming a clean
delete the code cannot honour.

HONEST DISCLOSURE.  A harness whose seam cannot be fired headlessly is NOT
faked.  Cursor's ``sessionEnd`` fires only from a local desktop-editor session
(its cloud agents have no editor-lifetime boundary), and Pi's seam is a
TypeScript extension the Pi process loads in-process — neither can be fired by
this command, so their links report ``UNVERIFIABLE-IN-CI`` with the reason.  A
link is only ever ``PROVEN`` when the path actually ran.
"""
from __future__ import annotations

import atexit
import json as _json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tortoise import capture_install, hook_install
from tortoise.capture_receipts import capture_receipt_key

__all__ = [
    "EXIT_BROKEN",
    "EXIT_OK",
    "EXIT_UNVERIFIABLE",
    "HARNESSES",
    "STATUS_FAIL",
    "STATUS_PROVEN",
    "STATUS_UNVERIFIABLE",
    "render_report",
    "resolve_install_root",
    "verify_session_capture",
]

#: The beta harnesses this command covers — DERIVED from the capture seam's
#: own definition, never a second list that could drift as harnesses are added
#: or removed (the sibling-instance class that cost #3917/#4024 a cycle each).
HARNESSES: tuple[str, ...] = tuple(capture_install.CAPTURE_SEAM)

STATUS_PROVEN = "PROVEN"
STATUS_FAIL = "FAIL"
STATUS_UNVERIFIABLE = "UNVERIFIABLE-IN-CI"

#: Exit codes.  0 = every link PROVEN; 1 = a link is BROKEN (the install is
#: wrong, or the capture/memory leg failed); 2 = nothing is provably broken,
#: but at least one link could not be exercised in this environment (the
#: harness is not headlessly firable) — a CI caller must treat 2 as "not
#: verified", never as a pass.
EXIT_OK = 0
EXIT_BROKEN = 1
EXIT_UNVERIFIABLE = 2

#: Whether the installed seam can be FIRED headlessly by this command.
#:
#: ``True`` means the harness registers an event → command seam that this
#: command can execute with the harness's own documented stdin payload and
#: observe.  ``False`` is a RULING, not a todo: Cursor's ``sessionEnd`` fires
#: only from a local desktop-editor session (its cloud agents have no
#: editor-lifetime session boundary), and Pi's seam is a TypeScript extension
#: loaded in-process by Pi — neither is a script this command may execute and
#: present as "the harness fired it".
HEADLESS_FIRABLE: dict[str, bool] = {
    "claude": True,
    "codex": True,
    "cursor": False,
    "pi": False,
}

#: Harnesses whose capture seam DETACHES a background worker that outlives the
#: hook — Codex's ``session-end.sh`` and Cursor's hand the slow capture to
#: ``nohup … & disown`` because the harness kills the hook before a network
#: POST could finish.  For a detaching harness the capture can land AFTER
#: ``verify`` has returned, so a cleanup ``DELETE 404`` is not a guarantee: it
#: proves only that nothing had landed *yet*.  The cleanup report must say the
#: capture is still in flight, never the false "nothing was left behind".
#: A per-harness fact table beside ``HEADLESS_FIRABLE``/``_CAPTURE_EVENT`` —
#: the detach is a property of the shipped hook script, which this module
#: deliberately never greps (behavioural verification, not a source scan).
DETACHING_HARNESSES: frozenset[str] = frozenset({"codex", "cursor"})

#: Why a non-firable harness's links are UNVERIFIABLE.  Named per harness so
#: the report says exactly what is missing, never a generic shrug.
UNVERIFIABLE_REASON: dict[str, str] = {
    "cursor": (
        "Cursor's sessionEnd hook is IDE-only — it fires from a local "
        "desktop-editor session; there is no headless trigger on this "
        "machine."),
    "pi": (
        "Pi's capture seam is a TypeScript extension loaded in-process by Pi "
        "(~/.pi/agent/extensions/tortoise-capture.ts); it is not a script and "
        "cannot be executed headlessly."),
}

#: The capture EVENT each harness registers (the SessionStart seam is Claude's
#: other half and is deliberately not the one fired here).
_CAPTURE_EVENT: dict[str, str] = {
    "claude": "SessionEnd",
    "codex": capture_install.CODEX_EVENT,
    "cursor": capture_install.CURSOR_EVENT,
}

#: A probe transcript that carries one unambiguous, extractable claim — the
#: memory leg needs an extraction to have something to produce.  Content is a
#: fixture; the EXECUTION that consumes it is real.
_PROBE_TURNS: tuple[tuple[str, str], ...] = (
    ("user",
     "We decided to use FalkorDB as the graph store for the verify probe, "
     "because it answers traversals quickly."),
    ("assistant",
     "Understood — FalkorDB is the graph store for the verify probe."),
)


class _ApiError(Exception):
    """A non-2xx / unreachable API response during verification."""

    def __init__(self, detail: str, status: int | None = None) -> None:
        super().__init__(detail)
        self.status = status


# ── root resolution (delegates to the shared mechanism) ───────────────────


def resolve_install_root(harness: str,
                         *,
                         home: str | os.PathLike[str],
                         install_dir: str | os.PathLike[str] | None = None,
                         ) -> Path:
    """Resolve a harness's install root.

    ``install_dir`` (the CLI's ``--dir``) always wins.  Without it the root is
    the harness's own default, resolved through the ONE shared resolver
    ``tortoise hook_install.default_root`` — Codex's ``$CODEX_HOME`` (default
    ``~/.codex``), Cursor's ``~/.cursor`` (no env override — Cursor has none),
    Claude's cwd (project-scoped).  Pi has no ``HarnessLayout`` (its seam is
    not a scripted hook), so its root is the extension directory
    ``~/.pi/agent/extensions``.
    """
    if install_dir is not None:
        return Path(install_dir)
    if harness == "pi":
        return Path(home) / ".pi" / "agent" / "extensions"
    layout = hook_install.get_layout(harness)
    return hook_install.default_root(layout, Path(home))


# ── link 1: installed (present + registered + fired) ───────────────────────


def _static_findings(harness: str, root: Path) -> list[dict[str, Any]]:
    """The install's on-disk drift, through the shared detector.

    Claude/Codex/Cursor delegate to ``hook_install.detect_install`` (the same
    read-only detector ``tortoise hooks status`` uses).  Pi has no layout, so
    its single artifact is checked directly.
    """
    if harness == "pi":
        dst = root / capture_install.PI_EXTENSION_NAME
        if not dst.is_file():
            return [{
                "kind": "missing-extension",
                "detail": f"{dst} is not installed",
                "blocking": True,
            }]
        return []
    return [
        {"kind": f.kind, "detail": f.detail, "script": f.script,
         "event": f.event, "blocking": f.blocking}
        for f in hook_install.detect_install(root, harness)
    ]


def _registered_capture_command(harness: str,
                                root: Path) -> str | None:
    """The command the harness will RUN for the capture event, or None.

    Reuses ``hook_install.registered_commands`` — the same loader helpers
    ``detect_install`` classifies with — so this can never drift from what
    ``hooks status`` considers current.
    """
    event = _CAPTURE_EVENT.get(harness)
    for cmd_event, command in hook_install.registered_commands(root, harness):
        if cmd_event == event:
            return command
    return None


def _probe_payload(harness: str, session_id: str, transcript: Path,
                   root: Path) -> dict[str, Any]:
    """The stdin event the harness's seam documents (see each shipped hook)."""
    if harness == "claude":
        return {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(root),
            "hook_event_name": "SessionEnd",
        }
    if harness == "codex":
        return {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "cwd": str(root),
            "hook_event_name": "SessionEnd",
            "reason": "verify",
        }
    # cursor
    return {
        "session_id": session_id,
        "conversation_id": session_id,
        "transcript_path": str(transcript),
        "cwd": str(root),
        "hook_event_name": "sessionEnd",
        "reason": "window_close",
        "is_background_agent": False,
    }


def _write_probe_transcript(harness: str, directory: Path) -> Path:
    """Write a minimal but REAL-shaped transcript for ``harness``."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"verify-{harness}.jsonl"
    if harness == "claude":
        lines = [
            _json.dumps({
                "type": role,
                "message": {"role": role, "content": content},
            })
            for role, content in _PROBE_TURNS
        ]
    elif harness == "codex":
        lines = [
            _json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message", "role": role,
                    "content": [{"type": "input_text", "text": content}],
                },
            })
            for role, content in _PROBE_TURNS
        ]
    else:  # cursor
        lines = [
            _json.dumps({
                "role": role,
                "message": {"content": [{"type": "text", "text": content}]},
            })
            for role, content in _PROBE_TURNS
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _fire_env(base_env: dict[str, str] | None) -> dict[str, str]:
    """The environment the installed hook runs under.

    The fired hook must be able to resolve the CLI.  It prefers ``tortoise``
    on PATH (the normal installed case); when that is absent the shipped hooks
    fall back to ``$TORTOISE_SRC_DIR``.  Seed that to the package this command
    itself is running from, so ``python -m tortoise`` (no console script on
    PATH) still fires the seam — unless the operator pinned it.
    """
    env = dict(os.environ if base_env is None else base_env)
    env.setdefault(
        "TORTOISE_SRC_DIR",
        str(Path(capture_install.PACKAGE_DIR).resolve().parent))
    return env


def _fire(root: Path, command: str, payload: dict[str, Any],
          env: dict[str, str], timeout: float) -> dict[str, Any]:
    """Execute the REGISTERED command with the harness's event on stdin."""
    try:
        proc = subprocess.run(
            ["/bin/bash", "-c", command],
            input=_json.dumps(payload),
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "detail": (
            f"the registered command did not return within {timeout:g}s")}
    except OSError as e:
        return {"ok": False, "detail": f"cannot execute the seam: {e}"}
    detail = (
        f"executed {command!r} (rc={proc.returncode})")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"ok": False, "detail": (
            f"{detail}; stderr: {tail[-1] if tail else '<empty>'}")}
    return {"ok": True, "detail": detail, "returncode": proc.returncode,
            "stdout": proc.stdout}


# ── hosted API reads (receipt / session / delete) ─────────────────────────


def _api(api_url: str, api_key: str, path: str, *,
         method: str = "GET") -> dict[str, Any]:
    req = Request(
        f"{api_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        method=method,
    )
    try:
        with urlopen(req, timeout=30) as resp:
            body = resp.read()
    except HTTPError as e:
        detail = e.read().decode() if e.fp else ""
        raise _ApiError(
            f"HTTP {e.code} for {method} {path}: {detail[:300]}",
            status=e.code) from None
    except URLError as e:
        raise _ApiError(
            f"cannot reach {api_url}: {e.reason}") from None
    if not body:
        return {}
    try:
        return _json.loads(body)
    except ValueError:
        raise _ApiError(f"{method} {path} returned non-JSON") from None


def _read_receipt(api_url: str, api_key: str, harness: str) -> str | None:
    """The per-harness capture receipt, or None when never set."""
    data = _api(api_url, api_key, "/v1/onboarding/state")
    onboarding = data.get("onboarding") or {}
    value = onboarding.get(capture_receipt_key(harness))
    return value if isinstance(value, str) else None


def _session_detail(api_url: str, api_key: str, session_id: str,
                    ) -> dict[str, Any] | None:
    """GET a session by id; None on 404."""
    try:
        return _api(api_url, api_key, f"/v1/sessions/{session_id}")
    except _ApiError as e:
        if e.status == 404:
            return None
        raise


# ── the chain ─────────────────────────────────────────────────────────────


def _link(status: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "detail": detail, **extra}


def _unverifiable_link(harness: str, link: str) -> dict[str, Any]:
    return _link(
        STATUS_UNVERIFIABLE,
        f"{link} not exercised: {UNVERIFIABLE_REASON[harness]}")


def verify_session_capture(harness: str,
                           *,
                           api_key: str,
                           api_url: str,
                           home: str | os.PathLike[str],
                           install_dir: str | os.PathLike[str] | None = None,
                           timeout: float = 90.0,
                           keep: bool = False,
                           env: dict[str, str] | None = None,
                           ) -> dict[str, Any]:
    """Run the installed → captured → memory chain for ``harness``.

    Returns the report dict (``render_report`` turns it into human text).  The
    caller exits with ``report["exit_code"]``.
    """
    if harness not in HARNESSES:
        known = ", ".join(HARNESSES)
        raise ValueError(f"unknown harness {harness!r} — known: {known}")

    root = resolve_install_root(harness, home=home, install_dir=install_dir)
    report: dict[str, Any] = {
        "harness": harness,
        "root": str(root),
        "api_url": api_url,
        "session_id": None,
        "links": {},
        "cleanup": {"attempted": False, "deleted": False},
        "keep": keep,
    }

    # ── link 1: installed ────────────────────────────────────────────────
    findings = _static_findings(harness, root)
    blocking = [f for f in findings if f.get("blocking")]
    if blocking:
        report["links"]["installed"] = _link(
            STATUS_FAIL,
            f"the install at {root} is not current: "
            + "; ".join(f"{f['kind']} ({f['detail']})" for f in blocking),
            findings=findings)
        report["links"]["captured"] = _link(
            STATUS_FAIL, "not run — the seam is not installed correctly")
        report["links"]["memory"] = _link(
            STATUS_FAIL, "not run — the seam is not installed correctly")
        report["exit_code"] = _exit_code(report)
        return report
    if not HEADLESS_FIRABLE[harness]:
        report["links"]["installed"] = _unverifiable_link(harness, "installed")
        report["links"]["installed"]["findings"] = findings
        report["links"]["captured"] = _unverifiable_link(harness, "captured")
        report["links"]["memory"] = _unverifiable_link(harness, "memory")
        report["exit_code"] = _exit_code(report)
        return report

    command = _registered_capture_command(harness, root)
    if not command:
        report["links"]["installed"] = _link(
            STATUS_FAIL,
            f"the harness's loader registers no command for "
            f"{_CAPTURE_EVENT.get(harness)} at {root} — nothing would fire")
        report["links"]["captured"] = _link(
            STATUS_FAIL, "not run — no capture command is registered")
        report["links"]["memory"] = _link(
            STATUS_FAIL, "not run — no capture command is registered")
        report["exit_code"] = _exit_code(report)
        return report

    probe_id = _probe_id(harness)
    report["session_id"] = probe_id
    # Scratch lives in its own temp dir, removed at process exit (covers every
    # early return AND a raise out of the observation) — a verify run never
    # leaves the probe transcript behind.
    probe_dir = Path(tempfile.mkdtemp(prefix=f"tortoise-verify-{probe_id}-"))
    atexit.register(shutil.rmtree, probe_dir, ignore_errors=True)
    transcript = _write_probe_transcript(harness, probe_dir)
    payload = _probe_payload(harness, probe_id, transcript, root)

    receipt_before: str | None
    try:
        receipt_before = _read_receipt(api_url, api_key, harness)
    except _ApiError as e:
        # Cannot even read the receipt — the chain is unverifiable, not
        # broken (the API is a precondition, not the seam).
        report["links"]["installed"] = _link(
            STATUS_UNVERIFIABLE,
            f"the registered command at {root} was not fired: {e}")
        report["links"]["captured"] = _link(
            STATUS_UNVERIFIABLE, f"not exercised: {e}")
        report["links"]["memory"] = _link(
            STATUS_UNVERIFIABLE, f"not exercised: {e}")
        report["exit_code"] = _exit_code(report)
        return report

    # ── the fire and the observation share ONE try/finally ───────────────
    # The capture ATTEMPT is recorded BEFORE ``_fire`` is invoked: a fire that
    # times out (or is killed) has still LAUNCHED the registered command, and
    # the seam may have POSTed before it died — so cleanup must run on that
    # path too.  Keying the attempt on ``fired["ok"]`` left the probe orphaned
    # on exactly that path (#3809 rr2, the same class as the read-keyed
    # cleanup).  Fire + observation therefore live in this single ``try``, and
    # the ``finally`` below is the ONE cleanup site — reached on every exit:
    # fire failure, observation failure, ``_ApiError``, or an unexpected raise.
    report["capture_attempted"] = True
    detail: dict[str, Any] | None = None
    try:
        fired = _fire(root, command, payload, _fire_env(env), timeout)
        report["fire"] = fired
        if not fired["ok"]:
            report["links"]["installed"] = _link(
                STATUS_FAIL, fired["detail"])
            # The command WAS launched — only the observation is missing.  The
            # message must not claim the seam "did not run" (false when it
            # timed out after POSTing): say exactly what is known.
            report["links"]["captured"] = _link(
                STATUS_FAIL,
                "not observed — the installed seam was launched but the fire "
                "did not complete, so the capture is unknown")
            report["links"]["memory"] = _link(
                STATUS_FAIL, "not reachable — the fire did not complete")
            return report
        report["links"]["installed"] = _link(
            STATUS_PROVEN,
            f"present, registered, and fired: {fired['detail']}")

        # ── link 2: captured ─────────────────────────────────────────────
        deadline = time.monotonic() + max(1.0, timeout)
        while time.monotonic() < deadline:
            detail = _session_detail(api_url, api_key, probe_id)
            if detail is not None:
                break
            time.sleep(0.5)

        receipt_after: str | None = None
        try:
            receipt_after = _read_receipt(api_url, api_key, harness)
        except _ApiError:
            receipt_after = None

        expected_turns = len(_PROBE_TURNS)
        if detail is None:
            report["links"]["captured"] = _link(
                STATUS_FAIL,
                f"no session {probe_id!r} appeared within {timeout:g}s "
                f"(receipt {'advanced' if receipt_after != receipt_before else 'did not advance'})",
                receipt_before=receipt_before, receipt_after=receipt_after)
        else:
            turn_points = detail.get("turn_points") or []
            turn_count_ok = len(turn_points) == expected_turns
            receipt_ok = (receipt_after is not None
                          and receipt_after != receipt_before)
            if not receipt_ok:
                report["links"]["captured"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} exists but "
                    f"{capture_receipt_key(harness)} did not advance",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    turns=len(turn_points))
            elif not turn_count_ok:
                report["links"]["captured"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} has {len(turn_points)} turns, "
                    f"expected {expected_turns}",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    turns=len(turn_points))
            else:
                report["links"]["captured"] = _link(
                    STATUS_PROVEN,
                    f"receipt advanced ({receipt_before!r} → {receipt_after!r}); "
                    f"session {probe_id!r} retrievable with {len(turn_points)} turns",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    turns=len(turn_points))

        # ── link 3: memory ───────────────────────────────────────────────
        if detail is None:
            report["links"]["memory"] = _link(
                STATUS_FAIL, "not reachable — the session was never captured")
        else:
            source = detail.get("source", "<absent>")
            extracted = detail.get("extracted")
            source_ok = (isinstance(source, dict)
                         and source.get("url") == f"session:{probe_id}")
            extracted_ok = isinstance(extracted, int) and extracted >= 1
            if source == "<absent>":
                report["links"]["memory"] = _link(
                    STATUS_UNVERIFIABLE,
                    "the API build does not expose the session Source node; "
                    "extraction was not asserted against it",
                    extracted=extracted)
            elif not source_ok:
                report["links"]["memory"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} has no Source node in the graph "
                    f"(url session:{probe_id})",
                    extracted=extracted, source=source)
            elif not extracted_ok:
                report["links"]["memory"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} appears as a Source but extraction "
                    f"produced no memory Point (extracted={extracted})",
                    extracted=extracted, source=source)
            else:
                report["links"]["memory"] = _link(
                    STATUS_PROVEN,
                    f"session {probe_id!r} is the Source {source.get('url')!r} "
                    f"and {extracted} memory Point(s) were extracted",
                    extracted=extracted, source=source)
    except _ApiError as e:
        # The API read broke AFTER the seam fired: the chain is BROKEN, but
        # the probe session may exist.  Record the failure and let the
        # `finally` delete what the fire may have written.  An unexpected
        # exception still runs the `finally` and then propagates to the CLI's
        # catch-all (exit 1) — cleanup is never skipped either way.
        report["links"]["captured"] = _link(
            STATUS_FAIL, f"the session read failed after the seam fired: {e}")
        report["links"]["memory"] = _link(
            STATUS_FAIL, "not reachable — the session read failed")
    finally:
        # ── cleanup: the ONE site, reached on EVERY path above ───────────
        # The probe session is deleted whenever a fire was attempted, and the
        # exit code is settled here too so the early fire-failure return gets
        # it.  There is deliberately no other cleanup call to drift from this
        # one.
        report["cleanup"] = _cleanup(
            api_url, api_key, probe_id, keep=keep,
            capture_attempted=report["capture_attempted"],
            detaching=harness in DETACHING_HARNESSES)
        report["exit_code"] = _exit_code(report)
    return report


def _probe_id(harness: str) -> str:
    """An unmistakable probe-session id (the task's naming contract).

    A short random suffix keeps two concurrent verify runs from colliding on
    the same idempotency key — a collision would make the second capture a
    zero-node replay, which would read as "the receipt did not advance".
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"verify-{harness}-{stamp}-{os.urandom(3).hex()}"


def _cleanup(api_url: str, api_key: str, probe_id: str, *,
             keep: bool, capture_attempted: bool,
             detaching: bool = False) -> dict[str, Any]:
    """Delete the probe session (and its local import receipt).

    Deletion is keyed on whether a fire was ATTEMPTED (``capture_attempted``),
    NOT on whether the fire reported success and NOT on whether the GET
    observed a session: a failed read must never leave a write behind, and the
    report must never claim "nothing to delete" when the seam actually ran.
    ``keep`` skips the deletion by operator request, and the report then says
    so — an intentional keep is not a silent leak.  A failed deletion is
    REPORTED and turns the command non-zero (it left a write in the graph); it
    is never swallowed.

    A ``DELETE 404`` means the probe session does not exist *now*.  For a
    non-detaching seam that is proof nothing was left behind.  For a
    DETACHING seam (``detaching``) the capture may land after this command
    returns, so the 404 is only "not yet": the report says the capture is in
    flight and marks ``in_flight`` (which turns the command non-zero) instead
    of claiming a clean delete it cannot honour.
    """
    result: dict[str, Any] = {"attempted": False, "deleted": False,
                              "session_id": probe_id, "kept": False}
    if keep:
        result["kept"] = True
        result["detail"] = "kept by --keep (the probe session was NOT deleted)"
        return result
    if not capture_attempted:
        result["detail"] = "no capture was attempted — nothing to delete"
        return result
    result["attempted"] = True
    try:
        body = _api(api_url, api_key, f"/v1/sessions/{probe_id}",
                    method="DELETE")
    except _ApiError as e:
        if e.status == 404:
            if detaching:
                # The seam hands off to a worker that OUTLIVES the hook, so a
                # 404 proves only that nothing had landed YET.  An honest "in
                # flight" beats a "nothing was left behind" the code cannot
                # honour (#3809 rr2).
                result["in_flight"] = True
                result["detail"] = (
                    f"the seam detaches a worker that outlives the hook; no "
                    f"probe session {probe_id} had landed at cleanup time "
                    "(DELETE 404) — the capture was still in flight when "
                    "verify returned and the probe session may appear after it. "
                    "A bounded grace cannot fix this: any finite wait is "
                    "outlived by a slower worker.")
                return result
            result["detail"] = (
                f"no probe session {probe_id} to delete (DELETE 404) — "
                "nothing was left behind")
            return result
        result["detail"] = f"DELETE failed: {e} — the probe session remains"
        result["error"] = True
        return result
    result["deleted"] = bool(body.get("deleted"))
    result["detail"] = (
        f"deleted {probe_id} (server: {body})" if result["deleted"]
        else f"DELETE returned {body!r} — the probe session may remain")
    if not result["deleted"]:
        result["error"] = True
    # Local 2xx receipt written by `sessions import` (Codex/Cursor) — best
    # effort, reported, never fatal on its own.
    local = _local_import_receipt(probe_id)
    if local is not None:
        try:
            local.unlink()
            result["local_receipt"] = f"removed {local}"
        except OSError as e:
            result["local_receipt"] = f"could not remove {local}: {e}"
    return result


def _local_import_receipt(probe_id: str) -> Path | None:
    path = Path(os.environ.get(
        "TORTOISE_IMPORT_RECEIPT_DIR",
        str(Path.home() / ".tortoise" / "import-receipts"))) / \
        f"{probe_id}.json"
    return path if path.exists() else None


def _exit_code(report: dict[str, Any]) -> int:
    statuses = [link["status"] for link in report["links"].values()]
    if STATUS_FAIL in statuses:
        return EXIT_BROKEN
    # A leaked write is a BROKEN link, not an unverifiable one: exit 2 means
    # "nothing provably broken", and a failed DELETE proves the opposite.  An
    # in-flight capture for a detaching seam is the same class — the probe may
    # remain, so the caller must not read the run as clean.  Both are checked
    # BEFORE the UNVERIFIABLE branch so an old API build (memory UNVERIFIABLE)
    # plus a failed/uncertain cleanup still exits 1.
    cleanup = report.get("cleanup") or {}
    if cleanup.get("error") or cleanup.get("in_flight"):
        return EXIT_BROKEN
    if STATUS_UNVERIFIABLE in statuses:
        return EXIT_UNVERIFIABLE
    return EXIT_OK


def render_report(report: dict[str, Any]) -> str:
    """Human-readable rendering of a verification report."""
    lines: list[str] = []
    lines.append(
        f"tortoise session verify — harness={report['harness']} "
        f"root={report['root']}")
    if report.get("session_id"):
        lines.append(f"  probe session: {report['session_id']}")
    icons = {
        STATUS_PROVEN: "✅",
        STATUS_FAIL: "❌",
        STATUS_UNVERIFIABLE: "⚠️ ",
    }
    for link in ("installed", "captured", "memory"):
        entry = report["links"].get(link)
        if entry is None:
            continue
        lines.append(
            f"  {icons.get(entry['status'], '?')} {link}: "
            f"{entry['status']} — {entry['detail']}")
    cleanup = report.get("cleanup") or {}
    if cleanup.get("detail"):
        lines.append(f"  cleanup: {cleanup['detail']}")
    code = report.get("exit_code", EXIT_BROKEN)
    verdict = {
        EXIT_OK: "all links PROVEN",
        EXIT_BROKEN: "a link is BROKEN",
        EXIT_UNVERIFIABLE: "nothing provably broken, but a link could not be "
                           "exercised in this environment",
    }.get(code, "unknown")
    lines.append(f"  exit {code} — {verdict}")
    return "\n".join(lines)
