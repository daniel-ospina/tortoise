"""#3809 — one-command behavioural verification of a harness capture install.

``tortoise session verify --harness <h>`` answers the question a human
otherwise answers by hand — *"does this machine's install actually capture a
session right now?"* — and exits non-zero on any broken link.  It walks the
whole chain for the four beta harnesses (claude, pi, cursor, codex):

1. **installed** — the seam is present AND the harness's own registration
   loader resolves it, AND the installed artifact actually FIRES (the
   registered command is executed with the harness's documented event
   payload).  This link measures the INSTALL leg ONLY: present + registered +
   fired rc=0 is ``PROVEN``, and it is ``INERT`` only when the hook left its
   own local breadcrumb (``kind: install-inert``) proving the install leg
   resolved nothing.  An API outage, a missing key, or a capture that files
   nothing (the ``captured`` link's business) must never rewrite a working
   install as INERT — those write ``kind: capture-failure``, which this link
   ignores.  The record is cleared immediately BEFORE the fire, so only a
   breadcrumb THIS fire produced can count;
2. **captured** — a ``session_capture_receipt_<harness>`` advanced and the
   session is retrievable by id with the expected turns.  Both legs are
   OBSERVED to one deadline (#4675): the receipt is written by a handler the
   transport bound ABANDONED rather than cancelled, so it lands *after* the
   session row is already visible (measured live: a seam fired 08:21:02 and
   the receipt was written 08:21:06).  Reading it once, on the first sighting
   of the row, FAILed a capture that had landed.  The predicate is unchanged
   ("receipt advanced AND expected turns retrievable"); only the observation
   window is.  A settled turn count that is NOT the expected one still breaks
   the window early, because it never becomes the expected one;
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
``verify-<harness>-<timestamp>`` and deleted whenever a fire was LAUNCHED —
keyed on the launch outcome, never on whether the fire *reported* success and
never on whether the read observed it.  The fire and the observation share ONE
``try``/``finally``, so cleanup runs on every path and there is no second site
that could drift.  The deletion is reported, and a failed deletion is surfaced
(exit non-zero), never swallowed.

LAUNCH OUTCOME, NOT A PROXY.  ``_fire`` reports which of three mutually
exclusive things happened to the registered command — it was NOT LAUNCHED (the
OS refused to execute it), it was launched and TIMED OUT (killed at the
bound), or it was launched and EXITED (with its return code) — and every
message, disclosure and cleanup decision keys on that value instead of on a
boolean success flag or a per-harness table.  A ``DELETE 404`` proves nothing
was left behind ONLY for ``NOT_LAUNCHED``: a launched command can still write,
because ``subprocess`` SIGKILLs only its DIRECT child (the shipped Claude hook
FORKS its capture step, which outlives the kill) and any seam may ``nohup … &
disown`` a worker and exit immediately (the shipped Codex and Cursor hooks
do).  So after any launch — and after a fire that never returned — the 404 is
reported as "may still be in flight", never as a clean delete the code cannot
honour, and there is deliberately no per-harness detach table to drift.

HONEST DISCLOSURE.  A harness whose seam this command cannot fire is NOT
faked.  Cursor's ``sessionEnd`` fires only from a local desktop-editor session
(its cloud agents have no editor-lifetime boundary), and Pi's seam is a
TypeScript extension the Pi process loads in-process — neither is a command
this verifier can execute and present as "the harness fired it", so their
links report ``UNVERIFIABLE-IN-CI``.  That ruling is about the INSTALL leg, not
the seam's testability: Pi's handler logic is exercised hermetically by
``tortoise/pi-hooks/tortoise-capture.test.ts``, and the artifact AS INSTALLED
is loaded and fired by the node probe in
``tests/test_pi_capture_hooks.py`` (into a temp ``HOME``); the residual (a real
``pi`` process loading the installed extension against the live API) is
manual-only.  A link is only ever ``PROVEN`` when the path actually ran.

UNVERIFIABLE is NOT "unjudgeable": the static leg still runs, and since #4680
Pi's installed artifact is graded by the same version contract as the shell
seams, so a missing / unmarkered / stale / edited Pi seam is a hard ``FAIL``
here — it is only the LIVE-FIRE leg that stays ``UNVERIFIABLE-IN-CI``.
"""
from __future__ import annotations

import atexit
import contextlib
import enum
import json as _json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from tortoise import capture_install, hook_install
from tortoise.capture_receipts import capture_receipt_key
from tortoise.hook_install import KIND_INSTALL_INERT

__all__ = [
    "EXIT_BROKEN",
    "EXIT_OK",
    "EXIT_UNVERIFIABLE",
    "HARNESSES",
    "STATUS_FAIL",
    "STATUS_INERT",
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
#: The seam is present, registered, and FIRED with rc=0 — but no downstream
#: effect (no receipt advance, no retrievable session) was observed.  rc=0 is
#: not evidence: a hook that resolves nothing takes its own silent ``exit 0``
#: and captures nothing (#4314), so this verdict is deliberately NOT PROVEN.
STATUS_INERT = "INERT"

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
#:
#: The ruling is about THIS COMMAND's inability to execute the harness's
#: registration — it is not a claim that the seam is untestable.  Pi's handler
#: logic is exercised hermetically by its own suite
#: (``tortoise/pi-hooks/tortoise-capture.test.ts``), and the artifact AS
#: INSTALLED is loaded and fired by the node probe in
#: ``tests/test_pi_capture_hooks.py`` (into a temp ``HOME``); see
#: ``UNVERIFIABLE_REASON['pi']``.
HEADLESS_FIRABLE: dict[str, bool] = {
    "claude": True,
    "codex": True,
    "cursor": False,
    "pi": False,
}

#: Why a non-firable harness's links are UNVERIFIABLE.  Named per harness so
#: the report says exactly what is missing, never a generic shrug.
#:
#: Pi's entry is deliberately SCOPED ("not firable by this command") and
#: PLAIN TEXT (it is printed verbatim into a report line).  The over-broad
#: absolutes — each denying that this seam could be executed or fired
#: headlessly, or that any headless entry point existed — are false about Pi:
#: the seam's handlers are fired headlessly by its own suite, and `pi -p` is
#: non-interactive.  A test pins the absence of those phrases from the PI
#: RULING's own text — the report string, this ruling, the enum, and the module
#: docstring — and deliberately NOT from the whole module: one of the phrases is
#: TRUE of Cursor (this dict's ``cursor`` entry says so)
#: (`tests/test_session_verify.py::test_pi_is_honestly_unverifiable`).
UNVERIFIABLE_REASON: dict[str, str] = {
    "cursor": (
        "Cursor's sessionEnd hook is IDE-only — it fires from a local "
        "desktop-editor session; there is no headless trigger on this "
        "machine."),
    "pi": (
        "Pi's capture seam is a TypeScript extension loaded in-process by Pi "
        f"({capture_install.pi_home('~')}/{capture_install.PI_EXTENSION_NAME}), "
        "not a command this verifier can execute; the install leg is therefore "
        "not firable by this command. The seam's handler logic is exercised "
        "hermetically by tortoise/pi-hooks/tortoise-capture.test.ts (run by "
        "tests/test_pi_capture_hooks.py), and the installed artifact is "
        "loaded and fired by that test file's node probe (into a temp HOME); the "
        "residual — a real pi process loading the installed extension "
        "against the live API — is manual-only."),
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
    Claude's cwd (project-scoped).  A harness with no ``HarnessLayout`` (its
    seam is not a scripted hook) is looked up in ``hook_install``'s
    ``ARTIFACT_CONTRACTS`` instead and resolved through
    ``hook_install.artifact_root`` — the SAME registry entry ``_static_findings``
    and ``doctor`` grade it by, so root resolution cannot be registry-driven in
    one place and literal in another (#4680 review).
    """
    if install_dir is not None:
        return Path(install_dir)
    artifact = hook_install.artifact_root(harness, Path(home))
    if artifact is not None:
        return artifact
    layout = hook_install.get_layout(harness)
    return hook_install.default_root(layout, Path(home))


# ── link 1: installed (present + registered + fired) ───────────────────────


def _static_findings(harness: str, root: Path) -> list[dict[str, Any]]:
    """The install's on-disk drift, through the shared detector.

    Claude/Codex/Cursor delegate to ``hook_install.detect_install`` (the same
    read-only detector ``tortoise hooks status`` uses).  A harness whose seam
    is a non-shell artifact (Pi) has no layout to hand that detector, so it
    delegates to the ARTIFACT half — ``hook_install.detect_artifact_install``
    — which is why a stale Pi seam is now reportable rather than only its
    absence (#4680).  Keyed on the registry, never on a literal ``"pi"``, so a
    seam registered in ``ARTIFACT_CONTRACTS`` is graded here with no edit; a
    seam class the registry does not know still falls through to
    ``detect_install`` (and its repair path may equally carry its own
    hard-coded harness names — ``resolve_install_root`` does that for ``pi``
    today — so registry membership is what keeps THIS branch generic, not a
    guarantee about every branch downstream).
    """
    if harness in hook_install.ARTIFACT_CONTRACTS:
        return [
            {"kind": f.kind, "detail": f.detail, "script": f.script,
             "event": f.event, "blocking": f.blocking}
            for f in hook_install.detect_artifact_install(root, harness)
        ]
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

    Deliberately does NOT seed ``TORTOISE_SRC_DIR``.  The install's own
    resolution path — the ``hook-src-dir`` record the installer writes under
    the harness HOME — is the thing whose behaviour must be exercised, so an
    install that cannot resolve reads ``INERT`` instead of being propped up
    by a variable no production harness sets (#4314).  A caller may still pin
    it explicitly through ``base_env``; verify only stops supplying it.
    """
    return dict(os.environ if base_env is None else base_env)


class LaunchOutcome(enum.Enum):
    """Whether the registered command actually RAN — the only launch fact.

    The three members are mutually exclusive and exhaustive, and they are the
    one thing every message, disclosure and cleanup decision may key on.  An
    ``ok: bool`` collapsed all of them (plus rc≠0) into one value, which let a
    message claim a seam "did not run" while it was running and a cleanup
    claim "nothing was left behind" while a descendant was still alive
    (#3809 rr3).  A fourth outcome may be added only with its own entry in
    :data:`_OUTCOME_PROVES_NOTHING_CAN_LAND` and its own message in
    :func:`_capture_failed_detail` / :func:`_not_yet_disclosure`, which raise
    on an unclassified member rather than inheriting a sibling's semantics.
    """

    #: The OS refused to CREATE the process (``OSError`` out of the spawn,
    #: ``Popen``).  No process ever ran, so no capture was made and none can
    #: follow: the ONE case where "nothing was left behind" is provable rather
    #: than merely hoped for.  The catch that produces this outcome is scoped
    #: to the SPAWN — an OS error from the wait proves nothing about whether
    #: the process ran (#3809 rr4).
    NOT_LAUNCHED = "not-launched"

    #: Launched, then killed at the timeout.  ``subprocess`` SIGKILLs only the
    #: DIRECT child, so a forked descendant can outlive it and write later —
    #: the shipped Claude hook forks its capture step (its line ends
    #: ``|| exit 0``, which forbids bash from ``exec``-ing it).
    TIMED_OUT = "timed-out"

    #: Launched and RETURNED.  The return code travels separately, because
    #: rc≠0 is a different axis from "did it run", and an exit does not bound
    #: the descendants either: a seam may ``nohup … & disown`` a worker and
    #: exit immediately (the shipped Codex and Cursor hooks do exactly that).
    EXITED = "exited"


#: What each outcome PROVES about a write that can still land.  Keyed
#: exhaustively on the members: a new outcome with no entry raises ``KeyError``
#: at the first fire rather than silently inheriting a sibling's certainty —
#: the collapse this table exists to prevent.
_OUTCOME_PROVES_NOTHING_CAN_LAND: dict[LaunchOutcome, bool] = {
    LaunchOutcome.NOT_LAUNCHED: True,
    LaunchOutcome.TIMED_OUT: False,
    LaunchOutcome.EXITED: False,
}


def _proves_nothing_can_land(launch: LaunchOutcome | None) -> bool:
    """Whether the launch fact PROVES no write can still land.

    The ONE place that certainty is decided.  ``None`` (the fire never
    returned) is unknown, never proof.  Keyed on
    :data:`_OUTCOME_PROVES_NOTHING_CAN_LAND`, so an outcome added without its
    own decision raises here instead of defaulting into a sibling's.
    """
    return launch is not None and _OUTCOME_PROVES_NOTHING_CAN_LAND[launch]


@dataclass(frozen=True)
class FireResult:
    """One ``_fire``: the launch outcome plus the evidence for it.

    ``succeeded`` is derived (launched AND rc=0) and is used ONLY to pick the
    PROVEN/FAIL branch; no message, disclosure or cleanup decision may key on
    it, because success/failure is orthogonal to whether a descendant can
    still write.
    """

    outcome: LaunchOutcome
    detail: str
    returncode: int | None = None
    stdout: str = ""
    timeout: float | None = None

    @property
    def succeeded(self) -> bool:
        return (self.outcome is LaunchOutcome.EXITED
                and self.returncode == 0)

    @property
    def proves_nothing_can_land(self) -> bool:
        return _proves_nothing_can_land(self.outcome)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready form for the report (``--json`` serializes it)."""
        return {
            "outcome": self.outcome.value,
            "detail": self.detail,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "timeout": self.timeout,
        }


def _capture_failed_detail(result: FireResult) -> str:
    """What is KNOWABLE about the capture after a fire that did not succeed.

    Keyed on the launch outcome, never on a success flag: only a seam that
    never launched is a definite "no capture was made"; a launched one may
    have filed it before dying (timed out) or exiting (rc≠0).
    """
    if result.outcome is LaunchOutcome.NOT_LAUNCHED:
        return ("the seam could not be launched, so no capture was made and "
                "none can follow — the session definitely does not exist")
    if result.outcome is LaunchOutcome.TIMED_OUT:
        return ("not observed — the seam was launched and killed at the "
                f"{result.timeout:g}s timeout, so it may have filed the "
                "capture before the kill, and a forked child can still file "
                "one")
    if result.outcome is LaunchOutcome.EXITED:
        return ("not observed — the seam ran and exited "
                f"(rc={result.returncode}), so it may or may not have filed "
                "the capture")
    raise AssertionError(
        f"unhandled launch outcome {result.outcome!r} — a new outcome must "
        "decide its own fire-failure message, never inherit a sibling's")


def _not_yet_disclosure(launch: LaunchOutcome | None, probe_id: str) -> str:
    """Why a ``DELETE 404`` is not proof after a launch — keyed on the fact.

    One explanation per launch outcome (plus "the fire never returned"), so a
    new outcome cannot reuse an existing one.
    """
    if launch is LaunchOutcome.TIMED_OUT:
        why = ("the seam was launched and killed at the timeout, and the kill "
               "reaches only the direct child — a forked capture step "
               "survives it and can still file the capture")
    elif launch is LaunchOutcome.EXITED:
        why = ("the seam ran and exited, and a fired seam is free to hand its "
               "capture to a worker that outlives it (the shipped Codex and "
               "Cursor hooks disown one), so a capture may or may not follow")
    elif launch is None:
        why = ("the fire did not return, so whether the seam is still "
               "running is unknown")
    else:
        raise AssertionError(
            f"unhandled launch outcome {launch!r} — a new outcome must decide "
            "its own cleanup disclosure, never inherit a sibling's")
    return (f"no probe session {probe_id} had landed at cleanup time "
            f"(DELETE 404) — {why}; the capture may still be in flight and "
            "the probe session may appear after verify returned.")


def _fire(root: Path, command: str, payload: dict[str, Any],
          env: dict[str, str], timeout: float) -> FireResult:
    """Execute the REGISTERED command with the harness's event on stdin.

    Returns a :class:`FireResult` whose ``outcome`` is the LAUNCH fact — what
    every caller must key on.  The three outcomes are produced here and
    nowhere else, so there is exactly one place that tells them apart.

    The ``OSError`` -> ``NOT_LAUNCHED`` arm is scoped to the SPAWN
    (``Popen``), never to the wait that follows it: an OS error raised after
    the process EXISTS (out of ``communicate``) does not prove the seam never
    ran — it may already have filed a capture — so it is left to propagate.
    ``verify_session_capture`` then reaches its ``finally`` with ``launch``
    still ``None``, which cleanup reads as "may have written" and never as
    "nothing was left behind" (#3809 rr4).
    """
    try:
        proc = subprocess.Popen(
            ["/bin/bash", "-c", command],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(root),
            env=env,
            text=True,
        )
    except OSError as e:
        return FireResult(
            LaunchOutcome.NOT_LAUNCHED, f"cannot execute the seam: {e}")
    try:
        stdout, stderr = proc.communicate(
            _json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        return FireResult(
            LaunchOutcome.TIMED_OUT,
            f"the registered command did not return within {timeout:g}s "
            "(it was launched and killed at the timeout)",
            timeout=timeout)
    except BaseException:
        # The process was CREATED: kill and reap the direct child exactly as
        # ``subprocess.run`` would, then let the error propagate — the launch
        # fact is unknown, never NOT_LAUNCHED (see the docstring).
        proc.kill()
        with contextlib.suppress(OSError):
            proc.wait()
        raise
    finally:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    detail = f"executed {command!r} (rc={proc.returncode})"
    if proc.returncode != 0:
        tail = (stderr or stdout or "").strip().splitlines()
        return FireResult(
            LaunchOutcome.EXITED,
            f"{detail}; stderr: {tail[-1] if tail else '<empty>'}",
            returncode=proc.returncode)
    return FireResult(LaunchOutcome.EXITED, detail,
                      returncode=proc.returncode, stdout=stdout)


# ── hosted API reads (receipt / session / delete) ─────────────────────────


def _api(api_url: str, api_key: str, path: str, *,
         method: str = "GET", timeout: float = 30.0) -> dict[str, Any]:
    # `urlopen` is imported HERE, not at module scope, for the same reason
    # `tortoise.__main__._session_post` does it: the transport must be
    # patchable per-call (`urllib.request.urlopen`) so a capture client's tests
    # exercise the real refusal path instead of the network (#4675). A
    # module-level binding would freeze the real transport for every caller
    # that shares this reader — including the post-commit confirmation.
    from urllib.request import Request, urlopen

    req = Request(
        f"{api_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {api_key}"},
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
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
                    *, timeout: float = 30.0,
                    ) -> dict[str, Any] | None:
    """GET a session by id; None on 404."""
    try:
        return _api(api_url, api_key, f"/v1/sessions/{session_id}",
                    timeout=timeout)
    except _ApiError as e:
        if e.status == 404:
            return None
        raise


#: Public spelling of the ONE reader of ``GET /v1/sessions/<id>``. The
#: "post-commit timeout" confirmation (``tortoise/session_confirm.py``, #4675)
#: reads through THIS definition so the 404→None / other-status-raises contract
#: cannot drift between the verifier and the capture client.
session_detail = _session_detail


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
    # Fire + observation live in this single ``try``, and the ``finally`` below
    # is the ONE cleanup site — reached on every exit: fire failure,
    # observation failure, ``_ApiError``, or an unexpected raise.  The launch
    # outcome is recorded the moment ``_fire`` returns and is what cleanup
    # keys on; if ``_fire`` itself raises, ``launch`` stays ``None``, which
    # cleanup reads as "unknown — assume it may have run" and never as proof.
    launch: LaunchOutcome | None = None
    detail: dict[str, Any] | None = None
    # The env the installed hook RUNS under, resolved ONCE.  The fire and the
    # breadcrumb read/write key on the SAME env: a caller passing a non-default
    # HOME must have verify read the breadcrumb the hook wrote under that HOME,
    # never this process's own home (#4314 P2).
    fire_env = _fire_env(env)
    # P1-B: clear the install-inert evidence IMMEDIATELY BEFORE the fire, so
    # only a breadcrumb THIS fire produced can be read after it.  Without this,
    # a hook that was inert ONCE leaves a permanent record and every later
    # `session verify` reports INERT even after the install is repaired.
    _clear_install_inert_breadcrumb(harness, fire_env)
    try:
        fired = _fire(root, command, payload, fire_env, timeout)
        launch = fired.outcome
        report["fire"] = fired.as_dict()
        if not fired.succeeded:
            report["links"]["installed"] = _link(
                STATUS_FAIL, fired.detail)
            # The message is derived from the LAUNCH fact, never from a
            # success flag: a launched-but-timed-out seam may already have
            # filed the capture, while a seam that never launched definitely
            # did not (#3809 rr3).
            report["links"]["captured"] = _link(
                STATUS_FAIL, _capture_failed_detail(fired))
            report["links"]["memory"] = _link(
                STATUS_FAIL,
                "not reachable — the capture was not observed on this run")
            return report
        # The INSTALL leg is present + registered + fired rc=0. It is PROVEN
        # unless the hook's OWN local breadcrumb proves the install leg
        # resolved nothing (#4314). The capture outcome is the `captured`
        # link's business, never this one — an API outage must not rewrite a
        # working install as INERT.
        report["links"]["installed"] = _install_link(harness, fired, fire_env)

        # ── link 2: captured ─────────────────────────────────────────────
        # The PREDICATE is #3809's: the per-harness receipt advanced AND the
        # session is retrievable with its expected turns. What this run fixes
        # is the OBSERVATION, not the predicate.
        #
        # It used to read the session row once (breaking on the first sighting)
        # and then read the receipt exactly once. The server keeps running a
        # handler the transport bound abandoned, so the receipt is written
        # AFTER the session row becomes visible — measured live: a seam fired
        # 08:21:02 and `session_capture_receipt_cursor` was written 08:21:06.
        # A single read taken in that window reported "did not advance" for a
        # capture that had landed. Both legs are therefore polled to the same
        # deadline, and the early break is kept for the two shapes that cannot
        # converge: the expected turn count WITH the receipt, and a non-empty
        # turn count that has stopped changing.
        deadline = time.monotonic() + max(1.0, timeout)
        expected_turns = len(_PROBE_TURNS)
        receipt_after: str | None = None
        turns_seen = 0
        previous_turns: int | None = None
        while True:
            detail = _session_detail(api_url, api_key, probe_id)
            try:
                receipt_after = _read_receipt(api_url, api_key, harness)
            except _ApiError:
                receipt_after = None
            receipt_advanced = (receipt_after is not None
                                and receipt_after != receipt_before)
            turns_seen = len(detail.get("turn_points") or []) \
                if detail is not None else 0
            if (detail is not None and turns_seen == expected_turns
                    and receipt_advanced):
                break
            if turns_seen and turns_seen == previous_turns \
                    and turns_seen != expected_turns:
                # A settled, WRONG turn count never becomes right — do not
                # spend the rest of the window on it. The expected count must
                # keep the window open: the receipt is still to come.
                break
            previous_turns = turns_seen
            if time.monotonic() >= deadline:
                break
            time.sleep(0.5)

        if detail is None:
            report["links"]["captured"] = _link(
                STATUS_FAIL,
                f"no session {probe_id!r} appeared within {timeout:g}s "
                f"(receipt {'advanced' if receipt_advanced else 'did not advance'})",
                receipt_before=receipt_before, receipt_after=receipt_after,
                receipt_advanced=receipt_advanced)
        else:
            turn_points = detail.get("turn_points") or []
            turn_count_ok = len(turn_points) == expected_turns
            if not receipt_advanced:
                report["links"]["captured"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} exists but "
                    f"{capture_receipt_key(harness)} did not advance within "
                    f"{timeout:g}s",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    receipt_advanced=False,
                    turns=len(turn_points))
            elif not turn_count_ok:
                report["links"]["captured"] = _link(
                    STATUS_FAIL,
                    f"session {probe_id!r} has {len(turn_points)} turns, "
                    f"expected {expected_turns}",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    receipt_advanced=True,
                    turns=len(turn_points))
            else:
                report["links"]["captured"] = _link(
                    STATUS_PROVEN,
                    f"receipt advanced ({receipt_before!r} → {receipt_after!r}); "
                    f"session {probe_id!r} retrievable with "
                    f"{len(turn_points)} turns",
                    receipt_before=receipt_before, receipt_after=receipt_after,
                    receipt_advanced=True,
                    turns=len(turn_points))

        # Re-evaluate the INSTALL leg now that the observation window has
        # closed. It is PROVEN on present + registered + fired rc=0, and INERT
        # only when the hook's OWN breadcrumb proves it resolved nothing
        # (#4314). The capture outcome belongs to `captured`, never here: an
        # API outage must not rewrite a working install as INERT. Re-reading
        # also catches a detached worker (Codex/Cursor) whose breadcrumb lands
        # asynchronously, after the synchronous hook already returned.
        report["links"]["installed"] = _install_link(harness, fired, fire_env)

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
        # catch-all (exit 1) — cleanup is never skipped either way.  The
        # INSTALL leg is still decided by its own breadcrumb, never by the
        # read that failed.
        report["links"]["installed"] = _install_link(harness, fired, fire_env)
        report["links"]["captured"] = _link(
            STATUS_FAIL, f"the session read failed after the seam fired: {e}")
        report["links"]["memory"] = _link(
            STATUS_FAIL, "not reachable — the session read failed")
    finally:
        # ── cleanup: the ONE site, reached on EVERY path above ───────────
        # The probe session is deleted whenever the seam was launched, and
        # conservatively when the fire never returned (nothing then proves the
        # seal did not run).  The exit code is settled here too so the early
        # fire-failure return gets it.  There is deliberately no other cleanup
        # call to drift from this one.
        report["cleanup"] = _cleanup(
            api_url, api_key, probe_id, keep=keep, launch=launch,
            env=fire_env)
        report["exit_code"] = _exit_code(report)
    return report


def _install_link(harness: str, fired: Any,
                  env: dict[str, str]) -> dict[str, Any]:
    """The INSTALL leg's verdict: present + registered + fired rc=0.

    ``PROVEN`` unless the hook left its OWN install-inert breadcrumb, which is
    the install leg's own evidence that it resolved nothing and captured
    nothing (#4314).  Only a record whose ``kind`` is
    :data:`hook_install.KIND_INSTALL_INERT` counts — the ``sessions import``
    capture path writes the SAME file with ``kind: capture-failure``, and an
    API outage must never rewrite a working install as ``INERT``.  The
    breadcrumb is read under the SAME env the hook was fired with (a
    non-default HOME reads what the hook wrote there).
    """
    breadcrumb = _local_capture_error(harness, env)
    if breadcrumb is not None and breadcrumb.get("kind") == KIND_INSTALL_INERT:
        return _link(
            STATUS_INERT,
            f"fired (rc=0) but the hook's own breadcrumb shows the install "
            f"resolved nothing and captured nothing ({fired.detail})",
            breadcrumb=breadcrumb)
    return _link(
        STATUS_PROVEN,
        f"present, registered, and fired with rc=0: {fired.detail}")


def _local_capture_error_file(harness: str,
                              env: dict[str, str]) -> Path:
    """The breadcrumb path for ``harness`` under the HOOK's env, not ours.

    Mirrors ``tortoise.__main__._capture_error_file`` — the same location and
    the same ``TORTOISE_IMPORT_RECEIPT_DIR`` override — but resolves the
    receipt dir and ``HOME`` from the env the hook was FIRED with (``env``),
    never from this process's ``os.environ``: a caller passing a non-default
    HOME must read the breadcrumb the hook wrote under that HOME (#4314 P2).
    When ``env`` supplies neither, the fallback is this process's home.
    """
    receipt_dir = env.get("TORTOISE_IMPORT_RECEIPT_DIR")
    if receipt_dir:
        base = Path(receipt_dir)
    else:
        home = env.get("HOME")
        base = ((Path(home) if home else Path.home())
                / ".tortoise" / "import-receipts")
    return base.parent / "capture-errors" / f"{harness}.json"


def _local_capture_error(harness: str,
                         env: dict[str, str]) -> dict[str, Any] | None:
    """The local breadcrumb from a capture that never landed, or None.

    Any well-formed dict is returned so the report can show WHAT was found;
    the caller (:func:`_install_link`) accepts it as install-inert evidence
    only when its ``kind`` marker matches.
    """
    try:
        data = _json.loads(
            _local_capture_error_file(harness, env).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clear_install_inert_breadcrumb(harness: str,
                                    env: dict[str, str]) -> None:
    """Remove a PRIOR fire's install-inert record, before the next fire.

    The read condition after a fire is "a record produced by THIS fire".
    Clearing the install-inert evidence immediately before the fire is what
    makes that true: a hook that was inert once can no longer fail every later
    ``session verify`` forever.  A ``capture-failure`` record is left alone —
    it is different evidence and does not affect the install leg.
    """
    record = _local_capture_error(harness, env)
    if record is None or record.get("kind") != KIND_INSTALL_INERT:
        return
    with contextlib.suppress(OSError):
        _local_capture_error_file(harness, env).unlink()


def _probe_id(harness: str) -> str:
    """An unmistakable probe-session id (the task's naming contract).

    A short random suffix keeps two concurrent verify runs from colliding on
    the same idempotency key — a collision would make the second capture a
    zero-node replay, which would read as "the receipt did not advance".
    """
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"verify-{harness}-{stamp}-{os.urandom(3).hex()}"


def _cleanup(api_url: str, api_key: str, probe_id: str, *,
             keep: bool,
             launch: LaunchOutcome | None,
             env: dict[str, str] | None = None) -> dict[str, Any]:
    """Delete the probe session, then drop its machine-local residue.

    The residue tidy-up is a WRAPPER, not the tail of the DELETE path: it must
    run on every exit, including `--keep` and the 404 arms — `--keep` is the
    one path that can otherwise exit 0 with a probe still queued (#4714).
    """
    result = _cleanup_probe(api_url, api_key, probe_id, keep=keep, launch=launch)
    _tidy_local_probe(result, probe_id, env)
    return result


def _cleanup_probe(api_url: str, api_key: str, probe_id: str, *,
                   keep: bool,
                   launch: LaunchOutcome | None) -> dict[str, Any]:
    """Delete the probe session (and its local import receipt).

    Deletion is keyed on the LAUNCH OUTCOME — whether the registered command
    actually ran — never on a success flag and never on a per-harness table.
    The DELETE is always attempted (so even a mis-classified "not launched"
    fails safe: a real write is deleted by the 200 branch), ``keep`` skips it
    by operator request and the report says so, and a failed deletion is
    REPORTED and turns the command non-zero (it left a write in the graph);
    it is never swallowed.

    A ``DELETE 404`` means the probe session does not exist *now*.  That is
    proof nothing was left behind ONLY when the seam was never LAUNCHED.
    After a launch it is only "not yet": ``subprocess`` SIGKILLs only the
    direct child (the shipped Claude hook's capture step is FORKED and
    survives the timeout) and a seam may ``nohup … & disown`` a worker and
    exit at once (the shipped Codex and Cursor hooks do), so the capture may
    land after this command has returned.  For any launch — and for a fire
    that never returned — the report says so and marks ``in_flight`` (which
    turns the command non-zero) instead of claiming a clean delete it cannot
    honour (#3809 rr3).
    """
    result: dict[str, Any] = {
        "attempted": False, "deleted": False, "session_id": probe_id,
        "kept": False,
        "launch": launch.value if launch is not None else "unknown",
    }
    if keep:
        result["kept"] = True
        result["detail"] = "kept by --keep (the probe session was NOT deleted)"
        return result
    result["attempted"] = True
    try:
        body = _api(api_url, api_key, f"/v1/sessions/{probe_id}",
                    method="DELETE")
    except _ApiError as e:
        if e.status == 404:
            if _proves_nothing_can_land(launch):
                result["detail"] = (
                    f"no probe session {probe_id} to delete (DELETE 404) — "
                    "the seam was never launched, so no capture was made and "
                    "nothing was left behind")
                return result
            # Launched (or the fire never returned): the 404 is "not yet".
            result["in_flight"] = True
            result["detail"] = _not_yet_disclosure(launch, probe_id)
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
    # Local 2xx receipt written by `sessions import` (Codex/Cursor).
    return result


def _tidy_local_probe(result: dict[str, Any], probe_id: str,
                      env: dict[str, str] | None) -> None:
    """Drop the probe's machine-local residue, and report what remains.

    Called on EVERY `_cleanup` exit, including the early ones. It used to live
    at the tail of the DELETE path, so `--keep` and both 404 arms returned
    without ever looking — and `--keep` is the one path that can still exit 0
    with a probe left queued (#4714 review).

    Two kinds of residue, deliberately treated differently: a leftover receipt
    is inert, while a leftover SPOOL ENTRY is content queued for the tenant
    graph — so only the latter is fatal.
    """
    from tortoise.capture_spool import is_spooled, remove_spool_entry, spool_dir

    # An inert leftover receipt — reported, but never fatal on its own. Resolved
    # against the SAME env the seam ran under, for the same reason the spool arm
    # is: a caller pinning the receipt dir must not have verify look elsewhere
    # and report "none" (#4714 review).
    local = _local_import_receipt(probe_id, env)
    if local is not None:
        try:
            local.unlink()
            result["local_receipt"] = f"removed {local}"
        except OSError as e:
            result["local_receipt"] = f"could not remove {local}: {e}"

    try:
        # The seam ran under `env` (see `_fire_env`), and this file's invariant
        # is that the fire and everything verifying it key on the SAME env — a
        # caller pinning HOME or the spool root must not have verify look
        # somewhere else and report a false "removed". `spool_dir(env)` resolves
        # the override, the pytest guard AND HOME from that env.
        spool_root = spool_dir(env)
        # Whether the probe is GONE — not whether an unlink was issued. An
        # unlink can fail, and a caller reporting "removed" on a failed one
        # would claim a clean run while synthetic content sat queued.
        was_present = is_spooled(spool_root, probe_id)
        remove_spool_entry(spool_root, probe_id)
        if not is_spooled(spool_root, probe_id):
            result["local_spool"] = "removed" if was_present else "none"
        else:
            result["local_spool"] = (
                f"the probe is STILL SPOOLED at {spool_root} — a drain may file "
                "synthetic content")
    except Exception as e:      # pragma: no cover - defensive, mirrors the hook
        result["local_spool"] = f"could not check the spool: {e}"
    # Fail CLOSED on residue that is not provably gone, including an error
    # while checking (the drain's structural probe refusal is the first line).
    if result.get("local_spool") not in (None, "none", "removed"):
        result["error"] = True


def _local_import_receipt(probe_id: str,
                          env: dict[str, str] | None = None) -> Path | None:
    source = os.environ if env is None else env
    base = source.get("TORTOISE_IMPORT_RECEIPT_DIR") or str(
        Path(source.get("HOME") or Path.home()) / ".tortoise" / "import-receipts")
    path = Path(base) / f"{probe_id}.json"
    return path if path.exists() else None


def _exit_code(report: dict[str, Any]) -> int:
    statuses = [link["status"] for link in report["links"].values()]
    # INERT is a BROKEN install: the seam fired yet captured nothing, which is
    # exactly the failure `verify` exists to catch (#4314).
    if STATUS_FAIL in statuses or STATUS_INERT in statuses:
        return EXIT_BROKEN
    # A leaked write is a BROKEN link, not an unverifiable one: exit 2 means
    # "nothing provably broken", and a failed DELETE proves the opposite.  A
    # launched fire whose probe had not landed is the same class — it may land
    # after verify returned, so the caller must not read the run as clean.
    # Both are checked BEFORE the UNVERIFIABLE branch so an old API build
    # (memory UNVERIFIABLE) plus a failed/uncertain cleanup still exits 1.
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
        STATUS_INERT: "⛔",
        STATUS_UNVERIFIABLE: "⚠️ ",
    }
    for link in ("installed", "captured", "memory"):
        entry = report["links"].get(link)
        if entry is None:
            continue
        lines.append(
            f"  {icons.get(entry['status'], '?')} {link}: "
            f"{entry['status']} — {entry['detail']}")
        breadcrumb = entry.get("breadcrumb")
        if breadcrumb:
            detail = (breadcrumb.get("detail")
                      if isinstance(breadcrumb, dict) else breadcrumb)
            lines.append(f"      ↳ hook breadcrumb: {detail}")
    cleanup = report.get("cleanup") or {}
    if cleanup.get("detail"):
        lines.append(f"  cleanup: {cleanup['detail']}")
        # Residue is surfaced, not swallowed: a leftover import receipt or a
        # leftover SPOOL entry (synthetic content the drain would file) has to
        # be visible in the default output, not only under --json.
        if cleanup.get("local_receipt"):
            lines.append(f"  receipt: {cleanup['local_receipt']}")
        if cleanup.get("local_spool"):
            lines.append(f"  spool: {cleanup['local_spool']}")
    code = report.get("exit_code", EXIT_BROKEN)
    verdict = {
        EXIT_OK: "all links PROVEN",
        EXIT_BROKEN: "a link is BROKEN",
        EXIT_UNVERIFIABLE: "nothing provably broken, but a link could not be "
                           "exercised in this environment",
    }.get(code, "unknown")
    lines.append(f"  exit {code} — {verdict}")
    return "\n".join(lines)
