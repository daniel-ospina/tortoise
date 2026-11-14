#!/usr/bin/env python3
"""published_artifact_canary — the missing reading on the PUBLISHED PyPI artifact (#12363).

Why this exists
---------------
2026-11-13: the only documented pip install path shipped a three-month-old
wheel whose FIRST documented command fails on a clean machine. Measured on a
clean venv + clean ``HOME``, ``pip install tortoise-graph`` (0.2.0, uploaded
2026-08-08) then ``tortoise init`` exits 1 with

    *** FATAL CONFIG FILE ERROR (Redis 8.10.2) ***
    >>> 'dir '<home>/.tortoise''
    No such file or directory

because the wheel predates #2204's ``os.makedirs`` in the embedded choke point,
and ``pip install tortoise-client`` — documented in README.md — 404s
(the project was never created on PyPI).

Nothing in this repo read the published artifact. Every existing instrument
reads the TREE: ``packaging-smoke`` and ``publish-pypi``'s wheel smoke install
the wheel they just BUILT and check pack content + ``--help``; neither ever
runs a documented command, and neither can see what PyPI is serving months
later. A wheel that is broken at upload time therefore stays broken, silently,
for as long as nobody performs the documented steps by hand. That is the same
class as deploy-staleness (#10109): a DECLINE channel cannot notice a thing
that never attempted to run.

What it reads (all four arms, none of them inferred)
---------------------------------------------------
1. ``engine-init``        install the LATEST PUBLISHED ``tortoise-graph`` from
                          PyPI into a fresh venv, with a fresh ``HOME``, and run
                          the documented first command ``tortoise init``. PASS
                          requires exit 0 AND the DB file to exist.
2. ``client-published``   ``tortoise-client`` resolves on the index. README.md
                          lists it in the package table and documents
                          `pip install tortoise-client`, so a 404 is a broken
                          documented path, not a missing nice-to-have.
3. ``engine-index``       (precondition) the ``tortoise-graph`` index metadata is
                          readable. Fail-closed: an index that cannot be read is
                          a RED, never a pass.
4. informational rows     published version, upload age in days, and whether the
                          tree's ``pyproject.toml`` version is the published one.
                          Reported, never gated: a version bump legitimately
                          precedes its release, so gating on it would red every
                          PR between a bump and a tag.

Exit codes
----------
  0  every gated arm passed
  1  at least one gated arm FAILED — the published artifact is broken
  2  the canary could not be evaluated (unreadable index, no venv/network, and a
     failed install that pip did NOT attribute to the artifact) — fail-closed,
     reported as RED because a detector that cannot run must never look like a
     pass (the no-op-gate class). ⛔ An install failure the INDEX corroborates is
     exit 1: pip's absence vocabulary is not proof of absence, but a confirmed
     absence means users cannot install the release, which is a release finding,
     not a detector outage (`install_and_run` / `index_absence_probe`).

Usage
-----
    uv run python tools/published_artifact_canary.py            # full run
    uv run python tools/published_artifact_canary.py --json     # machine output
    uv run python tools/published_artifact_canary.py --summary-file "$GITHUB_STEP_SUMMARY"

The pure half (``build_report``) takes the index documents as arguments and
does no I/O, so the verdict logic is testable offline; the impure half
(``install_and_run``) is the only thing that touches the network/venv.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below. `from datetime import UTC`
# is 3.11+-only, so on an older interpreter it would raise ImportError and the failure
# would read as "this tool is broken" rather than "your interpreter is too old". Same
# shape (D9) as every other `tools/` entry point; the class-level pin is
# `tests/test_entry_point_python_guard.py` (#12363 review round 6).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # The path is SPELLED OUT, not taken from `__file__`: the class-level pin compiles
    # and execs this node on its own, with no module globals, so `__file__` would raise
    # NameError instead of the refusal.
    print(
        f"published-artifact-canary (tools/published_artifact_canary.py): requires "
        f"Python >= 3.12 (got {sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/published_artifact_canary.py`",
        file=sys.stderr,
    )
    # ⛔ 2, not `collision_preflight`'s 3: both consumers branch on `exit_code == 2` to
    # render "a DETECTOR outage is not a release signal". An exit 3 would be published as
    # "the artifact is broken — cut a release", which is the mis-routing this tool exists
    # to prevent.
    raise SystemExit(2)

import argparse
import http.client
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

ENGINE_PACKAGE = "tortoise-graph"
CLIENT_PACKAGE = "tortoise-client"
INDEX_JSON = "https://pypi.org/pypi/{package}/json"
PYPI_SIMPLE = "https://pypi.org/simple"

#: ⛔ ONE PATTERN, NOT A MARKER GATE PLUS A NAME CHECK (#12422 round-21 review, P2).
#: `_INSTALL_ABSENCE_MARKERS` used to sit in front of this comparison, and that made the
#: `Failed to build '<pkg>' …` alternative CO-OCCUR instead of DECIDE: the line names our
#: artifact and contains no member of the marker tuple, so on its own it returned False and
#: only worked when an unrelated line happened to supply a marker (measured). A named
#: absence phrase IS pip's absence vocabulary, so the two halved predicates are one here.
#:
#: pip's "this distribution does not exist on the index" vocabulary — the half that NAMES
#: what it could not resolve, which is the half that can decide whose absence this is. The other
#: markers are corroboration only: `from versions: none` is printed on the same line and
#: names nothing, so it cannot say WHOSE absence this is. ⛔ THAT DISTINCTION IS THE
#: DIFFERENCE BETWEEN A VERDICT AND A RELEASE CALL — `pip install tortoise-graph` fails
#: with the same vocabulary when a TRANSITIVE DEPENDENCY is missing, and in index mode
#: the probe then answers `ok` (the project IS served), which the caller routes to FAIL.
#: Without this, a yanked third-party dependency tells a human to cut an irreversible
#: PyPI release that cannot possibly fix it (#12422 round-21 review P2).
#:
#: ⛔ THE SECOND ALTERNATIVE IS `Failed to build '<name>' when <step>`, AND ONLY THE STEPS
#: THAT RUN THIS ARTIFACT'S OWN BUILD BACKEND (#12422 round-21 delta review, P1, MEASURED).
#: `FailedToPrepareCandidate` (`pip/_internal/exceptions.py`) is raised at ONE site —
#: `pip/_internal/resolution/resolvelib/candidates.py::_prepare` — and that site wraps ONE
#: exception class. ⚠️ NOT "every way a candidate can fail to prepare": `_prepare` re-raises
#: `HashError` BARE, and `MetadataGenerationFailed`, `MetadataInconsistent`,
#: `MetadataInvalid` and `BackendUnavailable` propagate unwrapped. What the wrapper does
#: guarantee is that its `package_name` is **the candidate being prepared** — always OUR
#: name, whatever the cause — with the cause printed ABOVE the line, not in it. That is the
#: property that makes this alternative dangerous, and the allow-list below the answer to it.
#:
#: ⛔ SO THE STEP NAMES IN THIS TUPLE ARE AN ALLOW-LIST ON PURPOSE, AND IT HOLDS ONE ENTRY
#: ON PURPOSE TOO (#12422 round-30 review, P1/P2, MEASURED). The wrapper is raised from ONE
#: site — `resolution/resolvelib/candidates.py::_prepare`, catching
#: `InstallationSubprocessError` — so a step can only appear here if its failure reaches
#: that `except`. `getting requirements to build wheel` (`distributions/sdist.py`) does.
#: The other two steps a reader would expect are UNREACHABLE, by their own source:
#: * `preparing metadata (pyproject.toml)` — `operations/build/metadata.py` CATCHES the
#:   `InstallationSubprocessError` and re-raises `MetadataGenerationFailed`, which is a
#:   `DiagnosticPipError`/`InstallationError` and NOT an `InstallationSubprocessError`, so
#:   `_prepare` never sees it and no `Failed to build '…' when preparing metadata` exists.
#: * `building wheel for <name>` — `operations/build/wheel.py` catches bare `Exception`,
#:   logs, and returns `None`; nothing propagates to the wrapper.
#: Listing either one was a claim that pip prints a line it does not print. Both are the
#: FAIL this tool wants, and both read UNKNOWN today — recorded as residual 3 in
#: `_install_failure_is_absence` and tracked by #12584 (index mode).
#:
#: ⛔ BOTH BUILD-DEPENDENCY STEPS ARE DELIBERATELY ABSENT — there are TWO, not one, and a
#: deny-list would have caught neither. `distributions/sdist.py` installs the static
#: `build-system.requires` as `kind="build dependencies"` and then the backend's DYNAMIC
#: requirements as `kind="backend dependencies"`; `build_env/installer.py` turns each into
#: `f"installing {kind}{identify_requirement}"` with `f" for {for_req.name}"`, so BOTH step
#: descriptions embed our name while the subprocess installs OTHER distributions. Their
#: failure says nothing about ours. Measured, pip 26.2.1, index serving our page and our
#: sdist 200 and a transient failure only on the build dependency's page:
#:     ERROR: Could not find a version that satisfies the requirement not-a-real-build-backend>=1
#:     ERROR: No matching distribution found for not-a-real-build-backend>=1
#:     ERROR: Failed to build 'tortoise-graph' when installing build dependencies for tortoise-graph
#: and the same wrapper with `installing backend dependencies for tortoise-graph` when the
#: failure is in the DYNAMIC requirements. Attributing either to the artifact orders an
#: IRREVERSIBLE release that a retry would have made unnecessary — the exact failure this
#: module's own docstrings forbid. Both are also byte-indistinguishable from a genuinely
#: unsatisfiable requirement in OUR pyproject (pip names the dependency there too), so both
#: steps are UNKNOWN by construction — fail-closed, and consistent with the `Requires-Dist`
#: residual stated in `_install_failure_is_absence`.
#:
#: ⛔ AND AT THE PIP THIS CANARY ACTUALLY RUNS, THIS WHOLE ALTERNATIVE IS INERT — stated
#: here because a detector that cannot run must never look like a pass. The wrapper does
#: not exist before pip 25.3 (measured: absent in 24.0, 24.2, 24.3.1, 25.0, 25.2; present
#: in 25.3, 26.0, 26.2.1), while `install_and_run` builds its venv with
#: `sys.executable -m venv` and never upgrades pip, so it inherits ensurepip's bundle
#: (measured 24.0). Under the shipped configuration this alternative matches nothing and an
#: unbuildable sdist reads UNKNOWN. #12584 tracks closing that, either by reading pip's
#: ACTUAL per-step text or by pinning the pip the venv gets; until then the honest
#: description of what this predicate buys in production is "nothing yet", not "a fix".
#: ⚠️ An unrecognised step falls to UNKNOWN, never to FAIL: that is what an allow-list
#: buys, and it is the same reasoning that made the unnamed-probe-state arm an allow-list.
_ABSENCE_OWN_BUILD_STEPS = ("getting requirements to build wheel",)
_ABSENCE_NAMED = re.compile(
    r"(?:no matching distribution found for|could not find a version that satisfies the requirement)"
    r"\s+(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"|failed to build\s+['\"]?(?P<built>[A-Za-z0-9][A-Za-z0-9._-]*)['\"]?\s+when\s+(?P<step>"
    + "|".join(re.escape(step) for step in _ABSENCE_OWN_BUILD_STEPS)
    + r")",
    re.IGNORECASE,
)
#: ⛔ CORROBORATION ONLY, and no longer a GATE in front of the naming comparison: the
#: `Failed to build '<pkg>' …` line names the artifact and contains none of these, so a
#: marker gate made the naming predicate co-occur instead of decide (#12422 round-21, P2).


def _canonical_dist_name(name: str) -> str:
    """PEP 503 normalisation, so `Tortoise_Graph`, `tortoise-graph` and `tortoise.graph`
    are one name. pip echoes the requirement as the caller wrote it, which is not
    guaranteed to be the spelling the console script or the index uses.

    ⛔ The trailing `.strip("-")` IS load-bearing, and both earlier attempts to describe it
    were wrong in opposite directions. The capture class is `[A-Za-z0-9._-]*`, which
    INCLUDES `.` and `-`, so a name carrying trailing punctuation keeps it even inside the
    quotes: `Failed to build 'tortoise-graph.' when …` captures `tortoise-graph.`, and
    without the strip that normalises to `tortoise-graph-` — a name no package has, losing
    the match the alternative exists for (measured on this regex). pip's own
    `package_name` does not end in punctuation today, so this is defence against a near
    miss rather than something a current pip message exercises; the pin below states that
    instead of implying a real message covers it (#12422 round-30 review, P3).
    """
    return re.sub(r"[-_.]+", "-", name.strip().lower()).strip("-")


def _install_failure_is_absence(
    stderr: str, wheel: Path | None = None, package: str | None = None
) -> bool:
    """True when pip's output is about the ARTIFACT rather than pip's environment.

    ⛔ pip's "absence" vocabulary is NOT proof of absence. MEASURED: against an
    unreachable index (`--index-url http://127.0.0.1:9/simple`) and against a
    name-resolution failure, pip prints the SAME `Could not find a version that
    satisfies … (from versions: none)` it prints for a genuinely absent release — it
    collapses "I could not reach the index" into "there is nothing there". So the
    markers are a CANDIDATE, never a verdict.

    * WHEEL mode has no index in play at all: the only artifact-attributable failure is
      pip naming the wheel it could not install, and nothing else counts. A dependency
      fetch that failed is the network's problem, not the built wheel's.
    * INDEX mode is settled by the caller re-reading the index (``index_absence_probe``)
      and routing on its THREE answers — see that function's contract (#12363 rounds 4
      and 11). ⛔ It is NOT "an independent confirmation, not the same connection's
      opinion": `_main` only reaches this arm after a SUCCESSFUL read of the very URL
      the probe re-reads, so the probe shares the caller's view of the index. What it
      buys is a SECOND look at the index (did the project vanish, or does it still
      answer?), not a different vantage point — and reading it as independence is how
      the round-11 dead arm got written.

    ⛔ AND THE FAILURE MUST NAME **THIS** DISTRIBUTION (#12422 round-21 review P2).
    Index mode used to accept any absence marker, so `pip install tortoise-graph` failing
    because a TRANSITIVE dependency (`falkordblite`, measured) is unavailable was graded
    as `tortoise-graph`'s absence: the probe — which reports on `tortoise-graph` alone —
    answered `ok`, and `ok` is the FAIL that orders an irreversible release. Wheel mode
    already refused to attribute a dependency's failure to the artifact; index mode now
    does the same. A failure that does not POSITIVELY name this package is not evidence
    about this package, so it returns False and the caller takes the UNKNOWN path.

    ⚠️ STATED RESIDUALS, because they are real blind spots and pretending otherwise is how
    the next lane mistakes them for bugs. All three read as UNKNOWN ("detector outage",
    exit 2) instead of FAIL. That is fail-closed — it never orders an upload that cannot
    help — but none of them is invisible any more:

    1. A release whose OWN declared dependency is unsatisfiable (`Requires-Dist:
       falkordblite>=999`). Attributing it needs the release's own `requires_dist`.
    2. A release whose OWN build requirements are unsatisfiable, which pip reports as
       `Failed to build '<us>' when installing build dependencies for <us>` — or as
       `… when installing backend dependencies for <us>` for the DYNAMIC requirements. Both
       steps are excluded above because the same bytes are produced by a transient failure
       of SOMEONE ELSE'S distribution, and the two cannot be told apart from stderr.
    3. An artifact whose OWN metadata generation or wheel build fails, which IS a
       release-owed defect and which this predicate cannot see: pip converts those
       failures before they reach the wrapper (see `_ABSENCE_OWN_BUILD_STEPS`), and on the
       pip this canary's venv gets the wrapper does not exist at all. Tracked by #12584
       (index mode); #12530 is the `--wheel`-mode sibling and does NOT close this.

    The narrower claim this function now makes is exactly: a failure that names no package,
    names a different one, or names us only as the candidate whose build could not be
    prepared is not evidence about this artifact's absence from the index.
    """
    text = (stderr or "").lower()
    if wheel is not None:
        return Path(wheel).name.lower() in text
    if package is None:
        return False
    wanted = _canonical_dist_name(package)
    # ⛔ A NAMED ABSENCE PHRASE IS ITSELF THE MARKER — do not gate this on
    # a marker tuple first (#12422 round-21 review, P2, MEASURED). The
    # `Failed to build '<pkg>' …` form carries NO member of that tuple, so with the gate in
    # front it returned False on its own and only worked when an unrelated line happened to
    # supply a marker. That made the alternative co-occur rather than decide, contradicting
    # the sentence above it.
    #
    # Two alternatives, both of which NAME something: the requirement pip could not
    # resolve, and the artifact whose OWN build step failed (see the step allow-list at
    # `_ABSENCE_OWN_BUILD_STEPS` — the `when …` clause is what makes that alternative a
    # claim about OUR build instead of about whoever else's distribution pip was fetching).
    # Either may be absent from a given message, so read whichever matched and compare
    # THAT. A message with markers but no name (`from versions: none`) matches nothing and
    # is therefore still not evidence — the corroboration-only rule is preserved.
    return any(
        _canonical_dist_name(match.group("name") or match.group("built")) == wanted
        for match in _ABSENCE_NAMED.finditer(text)
    )


def index_absence_probe(package: str, timeout: float = 30.0) -> str:
    """Re-read the index after pip failed, and report WHAT THE INDEX SAYS NOW.

    Returns ``fetch_index``'s state: INDEX_OK, INDEX_ABSENT (a real 404/410) or
    INDEX_UNREADABLE (no answer obtained). ⛔ THREE values, and the CALLER routes all
    three — this docstring used to say "Returns one of INDEX_ABSENT / INDEX_UNREADABLE.
    Only INDEX_ABSENT justifies a FAIL: anything else means the detector could not get
    an answer, which is UNKNOWN", and BOTH halves of that were wrong in the code's own
    terms: the function returns INDEX_OK on success, and a FAIL is owed for ABSENT **and**
    for OK (#12363 round 11).

    Why OK is a FAIL: `_main` only reaches the pip arm after a SUCCESSFUL fetch of this
    same URL, so a probe that answers OK is the ordinary case — the index is reachable
    and still serves the project, and pip cannot resolve the exact version. That is a
    finding about the ARTIFACT (no files uploaded for ``info.version``, every file
    yanked, a wheel pip rejects), not about the detector. Routing OK to UNKNOWN is what
    made the caller's FAIL arm unreachable and reported "do NOT cut a release" for an
    uninstallable release. The caller therefore ALLOW-LISTS these two NAMED states: ABSENT
    and OK are the only values that justify a verdict, and every other value —
    INDEX_UNREADABLE, ``None``, ``""``, or a state a later refactor introduces — is
    UNKNOWN, because the probe produced no usable reading.

    ⛔ Do NOT "restore" a `state != INDEX_ABSENT -> UNKNOWN` test here or at the caller:
    that is the defect, not the protection. The round-3 protection it was meant to give —
    pip prints its absence vocabulary for an UNREACHABLE index too — is preserved by that
    ALLOW-LIST: only the two named states above reach a verdict, so an unnamed state stays
    on the UNKNOWN path whatever a future refactor decides to call it.
    """
    _doc, _reason, state = fetch_index(package, timeout)
    return state


OK = "OK"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

#: The ONE allow-list rendering of an arm status, for BOTH surfaces (#12589 review round 11,
#: P3). `.get(..., UNKNOWN)` is the allow-list: an arm carrying a status this module does not
#: name renders as the non-reading, never as whatever spelling it happened to hold. The table
#: applied this from round 8 and `--json` published the RAW value until round 11, so a gated arm
#: holding an unnamed status could read "CANNOT RUN" on screen and "PASS" in the payload. A
#: non-gated row has no verdict at all and renders `—` on both surfaces. Keep the two callers on
#: this function; a second copy of the rule is how they drifted apart.
GLYPH = {OK: "PASS", FAIL: "FAIL", UNKNOWN: "CANNOT RUN"}
NO_VERDICT = "—"


def rendered_status(arm: Arm) -> str:
    """The single rendering of ``arm``'s status — table and ``--json`` both call this."""
    return GLYPH.get(arm.status, GLYPH[UNKNOWN]) if arm.gated else NO_VERDICT


#: Every env var named here is stripped from the child's environment before the
#: documented command runs. The canary must measure the DEFAULT path a new user
#: gets — an ambient ``TORTOISE_DB_URI``/``TORTOISE_DB_PATH`` inherited from the
#: runner would point the command at some other target and turn a broken
#: artifact green (the "measures something other than what it claims" class).
#: ``PIP_`` is stripped by PREFIX on purpose. A member denylist of the pip variables
#: measured at one moment (``PIP_INDEX_URL``, ``PIP_CONFIG_FILE``, …) is a gate that
#: reports OK over a violated property: it cannot close the class, only today's members,
#: and a mirror/proxy/constraint that arrives by another name silently makes the canary
#: grade something other than what PyPI serves (#12363 review round 3). The tool's own
#: ``PIP_DISABLE_PIP_VERSION_CHECK`` is assigned AFTER the filter, so it survives.
_STRIPPED_ENV_PREFIXES = (
    "TORTOISE_",
    "FALKORDB_",
    "REDIS_",
    "PIP_",
    # EVERY proxy spelling, not the two that happen to be commonest. pip has no
    # `trust_env=False`, so a proxy is consumed by its vendored requests OUTSIDE pip's
    # option system — `--isolated` does not touch it. MEASURED (#12363 round 5): with
    # ONLY `all_proxy` set, `pip download --isolated --index-url
    # https://pypi.org/simple six` still dialled the fake proxy
    # (`HTTPSConnection(host='127.0.0.1', port=9): Connection refused`) and then printed
    # pip's ABSENCE vocabulary (`No matching distribution found … from versions: none`)
    # — the exact string this tool must never read as a verdict. Leaving one spelling
    # out is therefore not cosmetic: it is one of the two ways a run grades bytes nobody
    # asked for. urllib (this tool's OWN fetches) ignores `all_proxy` entirely, so the
    # child and the parent also end up on different transports unless it is stripped.
    "all_proxy",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
)

#: Named explicitly, not by prefix: these decide WHICH BYTES the child imports,
#: so a ``PYTHONPATH`` entry pointing at a checkout puts that tree's ``tortoise/``
#: ahead of the fresh venv's site-packages and the documented command executes the
#: TREE instead of the artifact under test — the #2204 shape (``grep -c makedirs``:
#: 1 in the tree, 0 in the 0.2.0 wheel) would report PASS. ``VIRTUAL_ENV`` does the
#: same by redirecting the interpreter. Only NON-pip names belong here: every ``PIP_``
#: variable is stripped by the prefix above, and a member list of them was a second
#: copy of that rule which could only ever drift from it (#12363 round 5).
_STRIPPED_ENV_NAMES = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "VIRTUAL_ENV",
)

#: Index readings, so "this project does not exist" and "I could not reach the
#: index" stay distinguishable. Conflating them let one PyPI timeout report a
#: BROKEN ARTIFACT (FAIL → "a release is owed") and send a human to perform an
#: irreversible upload (#12363 review B1).
INDEX_OK = "ok"
INDEX_ABSENT = "absent"  # a real 404/410 — the project is not on the index
INDEX_UNREADABLE = "unreadable"  # transport/parse failure — no answer was obtained


# --------------------------------------------------------------------------
# pure half — no I/O, no clock beyond the `now` the caller passes
# --------------------------------------------------------------------------


@dataclass
class Arm:
    """One reading. ``gated`` arms decide the exit code; the rest inform."""

    name: str
    status: str
    detail: str
    gated: bool = True


@dataclass
class Report:
    arms: list[Arm] = field(default_factory=list)
    #: True in ``--wheel`` mode (the pre-publish gate). The FAIL guidance differs:
    #: a red on the wheel about to be uploaded is a BUILD defect fixed by a fix +
    #: re-dispatch, not by cutting a release (#12363 review E4).
    wheel_mode: bool = False

    def add(self, arm: Arm) -> None:
        self.arms.append(arm)

    def get(self, name: str) -> Arm | None:
        for arm in self.arms:
            if arm.name == name:
                return arm
        return None

    @property
    def gated(self) -> list[Arm]:
        return [a for a in self.arms if a.gated]

    @property
    def verdict(self) -> str:
        gated = self.gated
        if not gated:
            # Never a pass by omission: a report with no gated arm is a bug in
            # the caller, and treating "nothing was checked" as green is the
            # no-op-gate failure this tool exists to prevent.
            return UNKNOWN
        if any(a.status == FAIL for a in gated):
            return FAIL
        if any(a.status == UNKNOWN for a in gated):
            return UNKNOWN
        # ⛔ ALLOW-LIST POLARITY: only an EXPLICITLY-`OK` all-clear is green (#12422 review,
        # P2, measured). This was a `return OK` — a DENY-list, so any gated status outside
        # {FAIL, UNKNOWN} reported GREEN: `Arm("engine-init", s)` returned `"OK"/exit 0` for
        # `s` in {`"PASS"`, `"SKIPPED"`, `"Ok"`, `"ok"`, `""`}, and the spelling that greens
        # it — `PASS` — is the very word `render_summary` prints as this report's heading.
        # AGENTS.md's "Decide the verdict by POLARITY, not by vocabulary" rule (adopted after a
        # real incident where an unrecognised conclusion let a merge through) says an unnamed
        # value must never fall into a gap; UNKNOWN is that gap's fail-closed landing place,
        # because it is non-zero at every consumer and renders as "detector outage", never as
        # a release order. Unreachable through today's producers — which is exactly why it must
        # be structural rather than a property of the current call sites.
        return OK if all(a.status == OK for a in gated) else UNKNOWN

    @property
    def exit_code(self) -> int:
        return {OK: 0, FAIL: 1, UNKNOWN: 2}[self.verdict]


#: ``info`` is PRESENT but is not an object (``"x"``, ``[]``, ``0``, ``null``) — the version
#: metadata could not be read at all (#12589).
INFO_UNREADABLE = "info-unreadable"


def info_unreadable(index_doc: dict[str, Any]) -> bool:
    """True when ``info`` is PRESENT but is not an object — a NON-READING.

    ⛔ An ABSENT ``info`` is deliberately NOT this case: an empty document is pinned as a FAIL
    (``test_a_client_document_with_no_version_is_red_not_ok``), because a caching proxy answering
    ``{}`` must not certify that the documented install resolves. What this predicate separates is
    the document that ANSWERED "no version" from the one whose ``info`` could not be parsed at all
    — the same absent-vs-unreadable split ``releases_map`` makes one level down (#12525/#12589).

    ⛔ It exists because the old ``latest_version`` raised on a TRUTHY non-object. The pre-diff
    expression was ``(index_doc.get("info") or {}).get("version")``: on ``{"info": "x"}`` that
    is an ``AttributeError``, which ``main()``'s exception backstop turned into exit 2 with a
    traceback as the recorded reason — the right CODE by the wrong ROUTE. The FALSY non-objects
    (``[]``, ``0``, ``null``) were swallowed by ``or {}`` into "" and reached the FAIL arm, i.e.
    exit 1 and the "cut a release" text, off a document that DID carry a version. So this
    predicate fixes two different defects at once, and the second one MOVES an exit code (1 → 2)
    on purpose — fail-closed, because the old sentence "carries no latest version" was false.
    #12589 review, P2: state the split rather than claiming an invariance that does not hold.
    """
    return "info" in index_doc and not isinstance(index_doc["info"], dict)


#: ``info`` is an object whose ``version`` is PRESENT but not a string (``0.3``, ``true``, ``[]``,
#: ``null``, an object) — the version metadata could not be READ (#12589 review, P2).
VERSION_UNREADABLE = "version-unreadable"


def version_unreadable(index_doc: dict[str, Any]) -> bool:
    """True when ``info`` is an object but ``info.version`` is present and not a string.

    The third predicate this module needs, for symmetry with the two above it: the same class, one
    key further in. Before it, ``{"info": {"version": 0.3}}`` was coerced to "" and reported as
    "carries no latest version" — a FAIL, exit 1, a release order — while the IDENTICAL shape one
    key across (``{"releases": {"0.3.0": {}}``) had just been made UNKNOWN. Two readers, one
    document, opposite verdicts about which shapes are answers.

    An ABSENT ``version`` and an EMPTY STRING ``version`` stay on the FAIL arm: both are pinned
    (``test_a_client_document_with_no_version_is_red_not_ok``), and both are the document
    ANSWERING that it serves nothing — which is exactly the claim the FAIL sentence makes.
    """
    info = index_doc.get("info")
    if not isinstance(info, dict):
        return False
    return "version" in info and not isinstance(info["version"], str)


def latest_version(index_doc: dict[str, Any]) -> str:
    """The version the index serves as latest (``info.version``), else "".

    Never raises: a non-object ``info`` yields "" (see ``info_unreadable``), and the ARM decides
    whether that empty reading is a FAIL (the document answered) or an UNKNOWN (it did not).
    """
    info = index_doc.get("info")
    if not isinstance(info, dict):
        return ""
    version = info.get("version")
    return version if isinstance(version, str) else ""


#: The ``releases`` map could not be READ: the key is absent, present as explicit JSON
#: ``null``, or present-but-wrong-typed (``[]``, ``"…"``, ``0``). PyPI documents ``releases``
#: as deprecated and REMOVABLE — "In the future, each of these keys may be removed entirely from
#: this API response" (docs.pypi.org/api/json) — so a missing key is the absence of an ANSWER
#: about installable files, not an answer that there are none (#12525).
#:
#: ⛔ THE NAME IS NARROWER THAN THE TOKEN'S USE, and the token is rendered to an operator in the
#: STEP SUMMARY table and the ``--json`` payload: someone reading ``(releases-absent)`` over
#: ``"releases": []`` who is pointed at PyPI's deprecation is being pointed AWAY from the shape
#: actually served. This comment is the definition the token is read against, so it names every
#: shape that produces it (#12590 review note, fixed here rather than left to drift).
#: ⚠️ NOT the alert issue's body: that body is `$why` + `tail -n 40` of the TRANSCRIPT file, so it
#: carries no TOKEN. An arm detail can reach it only by way of the transcript's own command output
#: (the pip case — see the measured note beside ``engine-init``), which this token is not part of.
RELEASES_ABSENT = "releases-absent"


def releases_map(index_doc: dict[str, Any]) -> dict[str, Any] | None:
    """The ``releases`` map, or ``None`` when the document carries no USABLE map.

    ``None`` covers every non-reading: the key absent, an explicit JSON ``null``, and a
    present-but-wrong-typed value (``[]``, ``"…"``, ``0``).

    ⛔ ``None`` is NOT ``{}`` (#12525). A PRESENT ``{}`` is an ANSWER — the project lists
    no releases — and stays a FAIL at the client arm. An absent key is "no answer was
    obtained": PyPI documents ``releases`` as deprecated and removable, so collapsing it to
    ``{}`` reported "the artifact is broken, cut a release" — an irreversible PyPI upload —
    off a response that never answered the question. This is the same absent-vs-unreadable
    split the tool already makes for a 404 (``INDEX_ABSENT`` → FAIL) against a timeout
    (``INDEX_UNREADABLE`` → UNKNOWN → exit 2), applied to the document's own shape.

    ⛔ A PRESENT BUT WRONG-TYPED value (``[]``, ``"…"``, ``0``) is ALSO ``None``. It is not an
    empty answer, it is a shape this client does not understand — "a state a later refactor
    introduces" — and the module's allow-list doctrine (see the absence probe in
    ``install_and_run``) says an unnamed value is a NON-READING, never a verdict. Mapping it
    to ``{}`` would turn it into the ANSWER "no releases" and so into a FAIL/exit 1, i.e. the
    release order this helper exists to prevent (#12525 review, P2).
    """
    releases = index_doc.get("releases")
    if not isinstance(releases, dict):
        return None
    return releases


#: A release map this client cannot read an INSTALLABLE-FILE reading from: a version value that
#: is not a list, a list holding an entry that is not an object, or a file entry with no boolean
#: `yanked` / no non-empty string `filename` (#12589). One constant, ONE enumeration of its causes —
#: the client arm's UNKNOWN detail, the OK-branch NOTE, the `resolvable_releases` docstring and
#: this comment all say the SAME words, because an operator reads them against each other and an
#: empty-string `filename` is of the documented TYPE while still being unusable (#12589 review
#: round 6, P3: "not of the documented type" understated it in two of the four sites).
RELEASES_UNREADABLE = "releases-unreadable"


def readable_entries(files: Any) -> tuple[list[dict[str, Any]], bool]:
    """One version's file OBJECTS, and whether anything in its value could not be read.

    Returns ``(entries, partial)``: the file objects this client could read, plus ``True`` when it
    had to DROP something — a value that is not a list at all (then ``entries == []``), or a list
    holding an entry that is not an object.

    ⛔ WHY IT DOES NOT DROP THE WHOLE VALUE (#12589 review, P2). The first revision returned
    ``None`` for the entire value as soon as ONE entry was not a dict, which turned a real green
    into a permanent CANNOT-RUN: ``{"0.3.0": [good_wheel, "junk"]}`` reported OK before the change
    and UNKNOWN after it, even though the readable wheel record alone settles the arm's EXISTENTIAL
    claim ("the unpinned requirement resolves"). The grain is the same one the value-level branch
    uses: read what you can, REPORT that something was dropped, and let the caller apply the
    polarity — OK may stand on what was read, FAIL may not.

    ⛔ And ``[]`` is not 'unreadable', for the same reason ``releases_map``'s ``{}`` is not 'absent':
    ``[]`` is the ANSWER "this release lists no files".
    """
    if not isinstance(files, list):
        return [], True
    entries = [entry for entry in files if isinstance(entry, dict)]
    return entries, len(entries) != len(files)


def resolvable_releases(index_doc: dict[str, Any]) -> tuple[list[str], str]:
    """Versions that make ``pip install <pkg>`` (UNPINNED) resolve, and what could not be read.

    TWO INDEPENDENT FACTS, because the two arms' claims differ in polarity:

    * ``versions`` — the releases holding a non-yanked FILE OBJECT. The arm's OK claim ("the
      unpinned requirement resolves") is EXISTENTIAL: one readable installable release settles it,
      so an unreadable value elsewhere cannot withdraw it (it could not make pip stop resolving).
      A PARTLY readable value counts too — the entries this client read can witness OK.
    * ``reason`` — ``""`` when every value and every entry was read, else the shape that was not:
      ``RELEASES_ABSENT`` (the map is absent / null / not an object) or ``RELEASES_UNREADABLE``
      (a value that is not a list, an entry that is not an object, or a file entry with no boolean
      ``yanked`` / no non-empty string ``filename``).

    ⛔ AND THE FAIL CLAIM IS UNIVERSAL, WHICH IS WHY ``reason`` EXISTS (#12589). "The index lists
    NO installable file in ANY release" is only TRUE once every value has been read. Emitting it
    from a partial parse orders the irreversible PyPI upload the #12525 split exists to prevent,
    one level below the map's own type: a cache emitting ``{"0.3.0": {}}`` (or
    ``{"0.3.0": ["name.whl"]}``) left ``resolvable`` empty and the arm RED, i.e. "cut a release",
    off a listing that was never understood. So ``reason`` gates FAIL, and only FAIL.


    The line is between an answer and a non-answer, NOT between "some files" and "no files":
    ``{}``, ``{"0.3.0": []}``, all-yanked files and a non-yanked file are all COMPLETE readings
    (``reason == ""``), because those documents did answer (#12525's pins keep their meaning).
    """
    releases = releases_map(index_doc)
    if releases is None:
        return [], RELEASES_ABSENT
    resolvable: list[str] = []
    reason = ""
    for version, files in releases.items():
        installable, unreadable = judged_value(files)
        if unreadable:
            # Not readable (in whole or in part): it can neither establish nor withdraw the
            # existential OK claim by itself, but the entries that WERE read still can.
            reason = RELEASES_UNREADABLE
        if installable:
            resolvable.append(version)
    return resolvable, reason


def judged_value(files: Any) -> tuple[bool, bool]:
    """``(has_installable_file, unreadable)`` for ONE version's value. THE one definition.

    ⛔ AN ENTRY IS NOT CONSULTED UNTIL IT CARRIES WHAT MAKES IT A FILE (#12589 review rounds 3, 4
    and 7). Round 3 caught the FLAG, round 4 the OTHER field (never read at all), and round 7
    caught that a caller had re-derived the question with a WEAKER reader:

      * an entry with no `yanked` (`{}`) read as "not yanked" and GREENED the arm on a value
        nothing was read FROM;
      * `"yanked": "false"` (a truthy STRING) read as yanked and reported "NO installable file in
        ANY release" — FAIL, exit 1, the release order;
      * `{"yanked": False}` with no `filename` was called installable — a false PASS off an object
        that is not a file record.

    PyPI documents each entry as a file object carrying `filename` + a boolean `yanked`, so
    anything else is a shape outside the contract: the entry is dropped and ``unreadable`` is set,
    which lets the value neither establish nor withdraw the existential OK claim by itself.

    ⛔ ONE DEFINITION, because the OK detail ALSO asks this question about ONE version ("is the
    advertised release installable?") and re-deriving it from ``release_files`` — which only sees
    STRUCTURAL unreadability — classified a field-level drop as the decisive "not resolvable",
    i.e. the exact artifact claim from a non-reading this issue removes. NOTE this is the only
    place that needs either field: ``release_files``/``upload_time`` enumerate and timestamp, and
    neither activity consults `yanked` or needs a filename.
    """
    entries, partial = readable_entries(files)
    judged = [
        entry
        for entry in entries
        if isinstance(entry.get("yanked"), bool)
        and isinstance(entry.get("filename"), str)
        and entry["filename"]
    ]
    return any(not entry.get("yanked") for entry in judged), partial or len(judged) != len(entries)


#: The arms whose failure means "the PUBLISHED ENGINE BUILD at this version is stale". Named
#: positively so the `tree-version` row can never blame the engine artifact on some other arm's
#: failure (#12589 review rounds 10 and 11).
ENGINE_ARMS = frozenset({"engine-index", "engine-init"})

#: The arms in WHEEL mode that exercise the wheel just built — the only ones whose red supports
#: "The BUILT wheel fails its documented first command" (#12589 review round 12, P3). An arm that
#: grows later cannot inherit that claim by failing.
WHEEL_COMMAND_ARMS = frozenset({"engine-init"})


def engine_build_implicated(report: Report) -> bool:
    """Whether an arm whose failure implicates the PUBLISHED ENGINE BUILD is red.

    ⛔ AN ALLOW-LIST OVER `ENGINE_ARMS`, not "any FAIL that is not the client" (#12589 review round
    11, P3). Round 10 keyed this on the ABSENCE of one name, so a THIRD gated arm (a
    documented-command smoke, say) failing would re-emit the engine-stale claim that round exists
    to remove — an arm set that grows cannot grow a false claim this way. Extracted from the row's
    construction so the property is testable with a report that has an arm `build_report` does not
    produce today.
    """
    return any(arm.gated and arm.status == FAIL and arm.name in ENGINE_ARMS for arm in report.arms)


#: ``installability`` results — three states, so a caller cannot fold "could not read" into
#: "read and negative" (#12589 review round 7, P2). Round 11's comment shuffle left these three
#: undocumented and the sentence attached to `ENGINE_ARMS`; the review caught it (#12589 round 12).
INSTALLABLE = "installable"
NOT_INSTALLABLE = "not-installable"
INSTALLABILITY_UNKNOWN = "unreadable"


def installability(index_doc: dict[str, Any], version: str) -> str:
    """Whether ``version`` has an installable file: INSTALLABLE / NOT_INSTALLABLE / UNKNOWN.

    The same question, asked about ONE version, through the SAME reader ``resolvable_releases``
    uses — see ``judged_value``. A caller that instead asks ``release_files(...) is None`` gets a
    weaker answer: that reader only sees STRUCTURAL unreadability, so a value whose entries were
    dropped for an unreadable ``yanked``/``filename`` looks READ and NEGATIVE, and the OK arm would
    print "not resolvable for an unpinned install here" about a listing it never read (#12589
    review round 7, P2).

    An ABSENT version key in a READABLE map is NOT_INSTALLABLE — that IS an answer ("no such
    release"), the same split ``release_files`` makes. An unreadable MAP is UNKNOWN.
    """
    releases = releases_map(index_doc)
    if releases is None:
        return INSTALLABILITY_UNKNOWN
    if version not in releases:
        return NOT_INSTALLABLE
    installable, unreadable = judged_value(releases[version])
    if installable:
        return INSTALLABLE
    return INSTALLABILITY_UNKNOWN if unreadable else NOT_INSTALLABLE


def missing_version_arm(
    name: str, index_doc: dict[str, Any], *, package: str, answered_detail: str
) -> Arm:
    """The arm for a document that yields no version: FAIL if it answered, UNKNOWN if it did not.

    Shared by the engine and client arms on purpose — the two readings must not be able to
    disagree about one shape (#12589), which is the same reason ``releases_map`` exists. Three
    input classes, two verdicts: a present-but-not-an-object ``info`` and a present-but-not-a-
    string ``version`` are NON-READINGS (UNKNOWN); an absent (or empty-string) version is the
    document ANSWERING that it serves nothing, which is the FAIL this arm already had.
    """
    if info_unreadable(index_doc):
        return Arm(
            name,
            UNKNOWN,
            # ⛔ NOT "the version it serves": `info` may be `[]`/`null`/`0`, which carries no
            # version at all — naming one would be the non-reading-as-property class this issue
            # removes, in the form the parallel `version_unreadable` arm already words correctly
            # (#12589 review round 8, P3).
            f"could not read the index metadata for {package} — the document's `info` is "
            f"PRESENT but is not an object ({INFO_UNREADABLE}), so the version metadata this "
            f"arm reads is UNPARSEABLE (any version the document may carry was never "
            f"obtained). That is a NON-READING: it says nothing about whether the "
            f"distribution resolves, and it is NOT a reason to cut a release",
        )
    if version_unreadable(index_doc):
        return Arm(
            name,
            UNKNOWN,
            f"could not read the version the index serves for {package} — `info.version` is "
            f"PRESENT but is not a string ({VERSION_UNREADABLE}), so the metadata this arm "
            f"reads is UNPARSEABLE. That is a NON-READING: it says nothing about whether the "
            f"distribution resolves, and it is NOT a reason to cut a release",
        )
    return Arm(name, FAIL, answered_detail)


def release_files(index_doc: dict[str, Any], version: str) -> list[dict[str, Any]] | None:
    """One version's file objects, ``[]`` when absent, ``None`` when the listing is unreadable.

    The STRICT reader: any drop (an unreadable map, an unreadable value, a non-object entry) is
    ``None``, because a caller reporting this as an ENUMERATION — the gated FAIL arm's count —
    must not print a count nobody read. The verdict itself does NOT come from here: the client arm
    reads ``resolvable_releases``, which applies the existential/universal polarity. That one
    VERDICT-path call site treats a ``None`` as an invariant failure that raises (fail-closed to
    exit 2) rather than printing a count it did not read.

    ⛔ AND IT IS NOT THE READER FOR A PARTIAL-TOLERANT ACTIVITY (#12589 review round 4, P3).
    ``upload_time`` wants timestamps, which need neither ``yanked`` nor ``filename`` — so it reads
    through ``readable_entries`` directly. This docstring previously said "its callers report this
    as an enumumeration, a count, a timestamp", which was wrong in the direction that cost a
    reading: the timestamps in a partly-readable listing WERE read, and this reader would have
    dropped them ("uploaded unknown ago").

    One source of truth for what an absent key means, so the two arms cannot disagree about one
    response (#12525) — and, since #12589, one source of truth for what an UNREADABLE map or value
    means too, INCLUDING at the map level (``releases_map`` returning ``None`` is ``None`` here,
    not an empty listing).
    """
    releases = releases_map(index_doc)
    if releases is None:
        return None  # no usable map at all — #12525's non-reading, not an empty listing
    if version not in releases:
        return []  # absent version key — an ANSWER ("no such release"), pinned as []
    entries, partial = readable_entries(releases[version])
    return None if partial else entries


def upload_time(index_doc: dict[str, Any], version: str) -> datetime | None:
    """The oldest upload time on ``version`` — when the release first existed.

    Oldest, not newest: a later re-upload of one file does not make the release
    younger, and the question this answers is "how long has this release been
    what users get".
    """
    stamps = []
    # ⛔ PARTIAL-TOLERANT, ON PURPOSE (#12589 review round 4, P3): a timestamp needs neither
    # `yanked` nor `filename`, so an entry this client could not judge as INSTALLABLE can still
    # tell us when the release first existed. Reading through the strict `release_files` would
    # drop a timestamp it had read and print "uploaded unknown ago" for a release whose listing
    # was readable in part. `None` from `releases_map` (no usable map) is the only total
    # non-reading here, and it means no timestamp — this row is informational and must never
    # crash or gate a run.
    releases = releases_map(index_doc)
    if releases is None or version not in releases:
        return None
    entries, _partial = readable_entries(releases[version])
    for entry in entries:
        raw = entry.get("upload_time_iso_8601") or entry.get("upload_time")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        stamps.append(parsed)
    return min(stamps) if stamps else None


def source_version(pyproject: str) -> str:
    """The tree's declared version, read without a TOML parser dependency."""
    for line in pyproject.splitlines():
        stripped = line.strip()
        if stripped.startswith("version =") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def build_report(
    *,
    engine_doc: dict[str, Any] | None,
    client_doc: dict[str, Any] | None,
    engine_error: str = "",
    client_error: str = "",
    tree_version: str,
    init_arm: Arm | None,
    now: datetime,
    wheel_path: Path | None = None,
    engine_state: str = INDEX_OK,
    client_state: str = INDEX_OK,
) -> Report:
    """Assemble the verdict from index documents + the install/run result.

    ``None`` for an index document means no document came back, and the state
    that accompanies it decides the verdict: ``INDEX_ABSENT`` (a real 404/410 —
    the project is not on the index) is a FAIL, because the documented install
    is then impossible; every OTHER state (a timeout, a 429, an unparseable
    body, or a state a later refactor introduces) is UNKNOWN, fail-closed. A 404
    is an answer and a timeout is not, so "I could not check" must never be
    reported as "the artifact is broken". ``init_arm`` is None when the install
    could not run at all, which is also UNKNOWN.

    ``wheel_path`` switches to WHEEL mode (the pre-publish gate): the artifact
    under test is a locally built wheel, so the index arms are not consulted and
    only the first-command arm is gated. One implementation for both readings on
    purpose — a publish-time smoke that is a different code path from the
    post-publish canary can drift from it, and then neither is evidence about
    the other.
    """
    report = Report(wheel_mode=wheel_path is not None)

    if wheel_path is not None:
        report.add(
            Arm(
                "engine-index",
                OK,
                f"not consulted (wheel mode: testing the locally built {wheel_path.name})",
                gated=False,
            )
        )
        report.add(
            Arm(
                "client-published",
                OK,
                "not consulted (wheel mode)",
                gated=False,
            )
        )
        report.add(init_arm or Arm("engine-init", UNKNOWN, "no wheel run was performed"))
        return report

    # 1. precondition — the engine index must be readable.
    if engine_doc is None:
        # A 404 is an answer (the project is not there → FAIL); anything else is
        # the absence of an answer (→ UNKNOWN, fail-closed). "I could not check"
        # must never be reported as "the artifact is broken".
        report.add(
            Arm(
                "engine-index",
                FAIL if engine_state == INDEX_ABSENT else UNKNOWN,
                (
                    f"{ENGINE_PACKAGE} is not on the index "
                    f"({engine_error or 'no result'}) — a 404/410 is an answer, "
                    f"so the documented install cannot succeed"
                    if engine_state == INDEX_ABSENT
                    else f"could not read the {ENGINE_PACKAGE} index metadata "
                    f"({engine_error or 'no result'}) — the distribution's "
                    f"presence is UNKNOWN, not absent"
                ),
            )
        )
    else:
        version = latest_version(engine_doc)
        if not version:
            # FAIL when the document ANSWERED (no version in a readable `info`) and UNKNOWN when
            # it did not (#12589). The exit code did NOT move for a TRUTHY non-object `info` —
            # that already raised into main()'s exit-2 backstop. It DID move (1 → 2) for the
            # falsy ones (`[]`, `0`, `null`), which `or {}` swallowed into the FAIL arm: a
            # deliberate fail-closed move, because "carries no latest version" was a false
            # claim about a document that carries one in a shape we refuse to parse.
            report.add(
                missing_version_arm(
                    "engine-index",
                    engine_doc,
                    package=ENGINE_PACKAGE,
                    answered_detail=(
                        f"index metadata for {ENGINE_PACKAGE} carries no latest version"
                    ),
                )
            )
        else:
            report.add(
                Arm(
                    "engine-index",
                    OK,
                    f"{ENGINE_PACKAGE} latest on the index: {version}",
                )
            )

    # 2. the documented first command, against the published wheel.
    if init_arm is None:
        report.add(
            Arm(
                "engine-init",
                UNKNOWN,
                "the published artifact was not exercised (install could not run)",
            )
        )
    else:
        report.add(init_arm)

    # 3. the other documented distribution.
    if client_doc is None:
        report.add(
            Arm(
                "client-published",
                FAIL if client_state == INDEX_ABSENT else UNKNOWN,
                (
                    f"{CLIENT_PACKAGE} is not resolvable on the index "
                    f"({client_error or 'no result'}) — README.md documents "
                    f"`pip install {CLIENT_PACKAGE}` as a supported install"
                    if client_state == INDEX_ABSENT
                    else f"could not read the {CLIENT_PACKAGE} index metadata "
                    f"({client_error or 'no result'}) — the distribution's presence "
                    f"is UNKNOWN, not absent"
                ),
            )
        )
    else:
        client_version = latest_version(client_doc)
        if not client_version:
            # A readable document carrying no version is not evidence the
            # distribution resolves: the engine arm treats the identical shape as a
            # FAIL, and a caching proxy answering `{}` must not turn the documented
            # install path green (#12363 review B4).
            # ⛔ A NON-OBJECT `info` IS NOT THAT SHAPE (#12589): it is present and unparseable, so
            # the arm cannot even ask the version question — UNKNOWN, not a FAIL that would order
            # a release. `missing_version_arm` makes this one decision for both arms.
            report.add(
                missing_version_arm(
                    "client-published",
                    client_doc,
                    package=CLIENT_PACKAGE,
                    answered_detail=(
                        f"the {CLIENT_PACKAGE} index document carries no published version"
                    ),
                )
            )
        else:
            # ⛔ THE CRITERION IS "DOES THE UNPINNED REQUIREMENT RESOLVE", not "does the latest
            # release have a file" (#12363 round 15). `pip install tortoise-client` is
            # UNPINNED, so pip ignores yanked releases (PEP 592 skips them for a requirement
            # that a non-yanked version can satisfy) and resolves to the highest release that
            # still has an installable file. Round 14 checked ONLY `info.version`'s file
            # list, which is the wrong scope in both directions:
            #   * latest yanked, an older release installable → pip RESOLVES (to the older
            #     release) while that arm FAILed with "README.md documents `pip install
            #     tortoise-client`, which cannot resolve" — a false statement that orders an
            #     irreversible release, fired by a maintainer doing exactly what PyPI
            #     prescribes for a broken release (yank it);
            #   * latest installable → OK either way.
            # So the question is asked across the WHOLE release map: any release with a
            # non-yanked file means the documented install resolves. Only when NO release has
            # one is the arm red, which is the one state where the sentence above is true.
            # ⛔ AN ABSENT `releases` KEY IS NOT AN EMPTY RELEASE MAP (#12525). PyPI
            # documents `releases` as deprecated and removable, so a deprecation landing —
            # or a mirror/cache answering `info` only — omits the key. Collapsing that to
            # `{}` left `resolvable` empty and reported "the client index lists NO
            # installable file in ANY release … README.md documents `pip install …, which
            # cannot resolve" — a FAIL, i.e. exit 1, i.e. an order to cut an IRREVERSIBLE
            # PyPI upload, off a response that never answered the question. `{}` PRESENT is
            # still a FAIL: that IS an answer, and it is pinned by
            # `test_a_client_version_with_no_INSTALLABLE_file_in_ANY_release_is_red`.
            resolvable, unreadable = resolvable_releases(client_doc)
            if resolvable:
                # ⛔ OK OUTRANKS AN UNREADABLE SIBLING, because the two claims differ in POLARITY
                # (#12589 review, P3). "The unpinned requirement resolves" is EXISTENTIAL — one
                # readable installable release settles it, and an unreadable value elsewhere
                # cannot make pip stop resolving. The FAIL below is UNIVERSAL ("NO installable
                # file in ANY release") and therefore does need a complete parse. Reading them
                # the same way would lose a true PASS: a mirror emitting one odd-shaped version
                # value turned a genuine green into a daily CANNOT-RUN red. The detail DISCLOSES
                # the partial parse so the reading is not overstated.
                partial = (
                    ""
                    if not unreadable
                    else f"; NOTE {unreadable}: part of this release map could not be read "
                    f"(a value that is not an array, an entry that is not an object, or a "
                    f"file entry with no boolean `yanked` / no non-empty string `filename`), "
                    f"so this list is the READABLE subset"
                )
                # ⛔ THE DETAIL NAMES WHAT WAS READ, NOT WHAT PIP RESOLVES TO (#12589 review
                # rounds 3, 4 and 5). `info.version` is what the index ADVERTISES; an unpinned
                # requirement resolves to the HIGHEST installable release, which membership in
                # `resolvable` does not establish (0.2.0 advertised + 0.3.0 also installable →
                # pip takes 0.3.0). Printing `unpinned → 0.2.0` there asserted a resolution the
                # reading never computed, and on the partial path it asserted one about the very
                # version whose listing was not read.
                # ⛔ AND THE CLAUSE DEPENDS ON WHETHER THE ADVERTISED VERSION WAS READ AT ALL
                # (#12589 review rounds 5 and 6). Three states, three sentences:
                #   * it is installable → say so;
                #   * its listing could NOT be read → say THAT (`could not be READ here`), because
                #     "not resolvable" would be an artifact claim manufactured from a non-reading,
                #     and it would be byte-identical to the READABLE all-yanked case below;
                #   * it was read and is not installable (yanked, empty, no usable file) → "not
                #     resolvable for an UNPINNED install here", which is what this arm tests. NOT
                #     "not installable": a yanked release installs fine under a `==` pin (PEP 592
                #     is what makes an unpinned requirement skip it).
                # ⛔ THE THREE STATES ARE BRANCHED ON, not folded (#12589 review round 9, P3).
                # The round-7 version asked `client_version in resolvable` FIRST and then
                # `installability(...) != NOT_INSTALLABLE`, i.e. it collapsed the enum back to two
                # states and relied on the membership test happening to be equivalent. The
                # three-state contract of `installability` is now visible in the code, and the
                # third state (UNKNOWN) names itself instead of arriving through a negation —
                # one definition of "read", `judged_value` behind `installability`, never
                # `release_files` (which cannot see a field-level drop; round 7, P2).
                # ⛔ AN ALLOW-LIST, NOT A DENY-LIST (#12589 review round 11, P3). Round 9 made
                # the three states visible but left the negative sentence on `else:`, so any
                # FOURTH state a future reader returns would be rendered as the decisive "not
                # resolvable" instead of naming itself — the same unearned-negative class this
                # issue exists to remove. The positive branch now has to be the one that speaks.
                state = installability(client_doc, client_version)
                if state == INSTALLABLE:
                    advertised = f"advertised latest {client_version} is installable"
                elif state == NOT_INSTALLABLE:
                    advertised = (
                        f"advertised latest {client_version} is not resolvable for an "
                        f"unpinned install here"
                    )
                elif state == INSTALLABILITY_UNKNOWN:
                    advertised = f"advertised latest {client_version} could not be READ here"
                else:
                    advertised = (
                        f"advertised latest {client_version} could not be READ here "
                        f"(unrecognised installability {state!r})"
                    )
                report.add(
                    Arm(
                        "client-published",
                        OK,
                        f"{CLIENT_PACKAGE} resolves on the index ({advertised}; installable: "
                        f"{', '.join(sorted(resolvable))}){partial}",
                    )
                )
            elif unreadable == RELEASES_ABSENT:
                report.add(
                    Arm(
                        "client-published",
                        UNKNOWN,
                        f"could not read the {CLIENT_PACKAGE} release map — the index "
                        f"document carries no usable `releases` map (the key is absent, "
                        f"null, or not an object). PyPI documents `releases` as "
                        f"DEPRECATED and may remove it ({RELEASES_ABSENT}), so whether "
                        f"`pip install {CLIENT_PACKAGE}` resolves is UNKNOWN, not false. "
                        f"This is NOT evidence the artifact is broken and is NOT a reason "
                        f"to cut a release",
                    )
                )
            elif unreadable:
                # ⛔ AN UNPARSEABLE FILE LISTING IS NOT AN EMPTY ONE (#12589). The FAIL below
                # asserts "NO installable file in ANY release"; a version value that is not a
                # list (or a list of non-objects) leaves that sentence unproven, and emitting it
                # would order an irreversible release off a listing this client never
                # understood. `{}` / empty lists / all-yanked stay COMPLETE readings and keep
                # their FAIL (#12525's pins).
                report.add(
                    Arm(
                        "client-published",
                        UNKNOWN,
                        f"could not read a file listing out of the {CLIENT_PACKAGE} release "
                        f"map ({unreadable}) — a version value that is not an array, an entry "
                        f"that is not an object, or a file entry with no boolean `yanked` / no "
                        f"non-empty string `filename`. PyPI documents each version's value as "
                        f"an ARRAY of file objects, so this is a shape this client cannot "
                        f"parse, and whether `pip install {CLIENT_PACKAGE}` resolves is "
                        f"UNKNOWN, not false. This is NOT evidence the artifact is broken and "
                        f"is NOT a reason to cut a release",
                    )
                )
            else:
                # ⛔ Reached ONLY when `unreadable` is "" — i.e. every value AND every entry was
                # read — so `release_files` cannot be `None` here. The guard is not decorative: if
                # that invariant is ever relaxed, raising fails CLOSED into main()'s exit-2
                # backstop instead of printing "0 file(s) … none usable", a count nobody read.
                # That sentence is the release order this issue exists to remove (#12589 review,
                # P3).
                files = release_files(client_doc, client_version)
                if files is None:
                    raise RuntimeError(
                        "the FAIL verdict on the client arm requires a COMPLETE reading of the "
                        "release map; `resolvable_releases` reported it complete but "
                        "`release_files` could not read it (#12589)"
                    )
                report.add(
                    Arm(
                        "client-published",
                        FAIL,
                        f"the {CLIENT_PACKAGE} index lists NO installable file in ANY release "
                        f"({len(files)} file(s) on {client_version}, none usable) — README.md "
                        f"documents `pip install {CLIENT_PACKAGE}`, which cannot resolve",
                    )
                )

    # 4. informational rows — reported, never gated (see module docstring).
    published = latest_version(engine_doc or {})
    if published:
        uploaded = upload_time(engine_doc or {}, published)
        age = "unknown" if uploaded is None else f"{(now - uploaded).days}d"
        report.add(
            Arm(
                "release-age",
                OK,
                f"{ENGINE_PACKAGE} {published} uploaded {age} ago",
                gated=False,
            )
        )
        if tree_version:
            if tree_version == published:
                # ⛔ At the same version a FAIL means the PUBLISHED BUILD is stale at
                # that version — today's exact state (0.2.0 published, 0.2.0 in the
                # tree, wheel missing #2204). Saying "no bump pending" here tells the
                # operator the opposite of what the summary row requires (#12363 round 5).
                # ⛔ AND ONLY THE ENGINE ARM IMPLICATES THE ENGINE BUILD (#12589 review round 10,
                # P3). "the published build at this version predates the fix" was emitted for ANY
                # FAIL, so a client-arm FAIL (e.g. `tortoise-client` 404) — with the engine build
                # at that version PASSing in the row above — claimed the engine artifact was stale.
                # Two sentences, each true of its arm.
                engine_failed = engine_build_implicated(report)
                if report.verdict == FAIL and engine_failed:
                    relationship = (
                        "the tree carries the same version — a bump IS required: the "
                        "published build at this version predates the fix"
                    )
                elif report.verdict == FAIL:
                    relationship = (
                        "the tree carries the same version — the failing arm above is NOT the "
                        "engine build, so this version's engine artifact is not implicated; "
                        "read the failing row before deciding what a bump would fix"
                    )
                else:
                    relationship = "the tree's version (no bump pending)"
            else:
                relationship = "the tree's version differs (a release is owed or in flight)"
            report.add(
                Arm(
                    "tree-version",
                    OK,
                    f"pyproject.toml version {tree_version} vs published {published} — "
                    f"{relationship}",
                    gated=False,
                )
            )
    return report


def render_summary(report: Report) -> str:
    """Markdown for a CI step summary / PR comment. No emoji-only status."""
    glyph = GLYPH
    lines = [
        "## Published-artifact canary (#12363)",
        "",
        f"**Verdict: {glyph[report.verdict]}**",
        "",
        # ⛔ THE STATUS COLUMN IS LOAD-BEARING, not decoration (#12589 review round 8, P2). The
        # UNKNOWN tail tells the operator "the row marked CANNOT RUN names which" — and with a
        # `| arm | gated | reading |` table the ONLY place the words CANNOT RUN appeared was the
        # verdict header, so the sentence pointed at a mark that did not exist and the reader had
        # to guess which row was the non-reader. `glyph.get(..., CANNOT RUN)` keeps this on the
        # allow-list polarity the arms obey: a status outside the three known ones renders as the
        # fail-closed label rather than vanishing.
        "| arm | gated | status | reading |",
        "|---|---|---|---|",
    ]
    for arm in report.arms:
        detail = arm.detail.replace("|", "\\|")
        # ⛔ AN INFORMATIONAL ROW HAS NO VERDICT, so it must not be stamped with one (#12589 review
        # round 9, P3). `release-age` is built with status OK even when its own text says "uploaded
        # unknown ago" — the arm reads a value it did not obtain — and rendering `PASS` beside that
        # is the non-reading-as-a-verdict class this issue removes, one row over. The verdict
        # property ignores non-gated arms, so this changes the TABLE only.
        status = rendered_status(arm)
        lines.append(f"| `{arm.name}` | {'yes' if arm.gated else 'no'} | {status} | {detail} |")
    if report.verdict == FAIL:
        if report.wheel_mode:
            # Branch on which arm actually failed. A substring test was wrong twice: the
            # console-script arm's detail says "the installed artifact provides no
            # `tortoise` console script", which contains "install" and rendered as "does
            # not install" — false, it installed cleanly. Match the LEADING clause, and
            # name the entry-point case separately (#12363 rounds 5 and 6).
            # ⛔ AN ALLOW-LIST, AND THE FALLBACK NAMES THE ARM (#12589 review round 12, P3). The
            # trailing `else:` published "fails its documented first command" for ANY gated arm
            # whose detail matched neither prefix, so an arm added later inherited the wheel's
            # claim — the same class round 11 removed from `engine_build_implicated`. Only the two
            # shapes that can NAME their claim keep it; anything else says which arm is red and
            # asks the operator to read it.
            step = next((arm for arm in report.arms if arm.gated and arm.status == FAIL), None)
            detail = step.detail if step is not None else ""
            if detail.startswith("the BUILT wheel") and "failed to install" in detail:
                what = "does not install"
            elif "console script" in detail:
                what = "installs but ships no `tortoise` console script, so the documented first command cannot run"
            elif step is not None and step.name in WHEEL_COMMAND_ARMS:
                # The arm that RUNS the built wheel's documented command — the only one whose
                # failure establishes this sentence (`test_wheel_mode_fail_names_a_build_defect_
                # not_a_release` is this shape: the wheel installs, `tortoise init` exits 1).
                what = "fails its documented first command"
            elif step is None:
                what = (
                    "is red, but no single arm names the failure — read the table above and "
                    "treat this as a canary defect"
                )
            else:
                what = (
                    f"is NOT judged by this summary: the failing arm is `{step.name}` "
                    f"({step.detail}) — read it, because that arm does not establish that the "
                    f"built wheel fails its documented command"
                )
            lines += [
                "",
                f"The BUILT wheel {what}. Nothing has been "
                "uploaded: fix the tree and re-dispatch this publish. This is a code "
                "defect, not a release-owed finding. See #12363.",
            ]
        else:
            lines += [
                "",
                "The artifact on the index is broken for the documented steps. This is "
                "**not** fixed by a code change in this repo: it needs a release "
                "(version bump → tag → `publish-pypi.yml`) by whoever holds the PyPI "
                "credentials. See #12363.",
            ]
    elif report.verdict == UNKNOWN:
        # ⛔ THE TAIL POINTS AT THE ROW, AND NOTHING MORE (#12589 review rounds 4 and 5). The
        # pre-fix text named ONE mechanism ("could not read the index") for every CANNOT RUN,
        # which is false when the non-reading is a SHAPE the canary refuses. The fix is the first
        # sentence: it covers both mechanisms and defers to the mark on the arm. A second
        # paragraph that tried to rule the transport OUT was added in round 4 and REMOVED in round
        # 5, because it fired exactly where its premise was wrong: whenever every index row reads
        # OK, the CANNOT RUN arm is `engine-init` — i.e. the install did fail in transport — and in
        # wheel mode both index rows read "not consulted" with status OK, so the paragraph denied
        # the transport on the two shapes where the transport IS the story. Do not reintroduce a
        # per-mechanism claim here without a condition that can actually see the arm.
        # ⛔ THE POINTER MUST POINT AT A ROW THAT EXISTS (#12589 review round 9, P3). A `Report`
        # with no arms, or with only informational ones, renders no CANNOT RUN row — and the
        # sentence still told the operator to read one. When nothing is marked, say so: a
        # verdict of CANNOT RUN with no unreadable arm is a CANARY defect, not an artifact one.
        marked = [arm.name for arm in report.arms if rendered_status(arm) == glyph[UNKNOWN]]
        where = (
            "the row marked CANNOT RUN names which ("
            + ", ".join(f"`{name}`" for name in marked)
            + ")"
            if marked
            else "NO row is marked CANNOT RUN, which is a defect in the canary itself rather "
            "than in the artifact — report it, do not cut a release"
        )
        lines += [
            "",
            "The canary could not READ something it needs, or could not exercise the "
            f"artifact: {where}. Reported as RED on purpose: a detector that cannot run "
            "must never look like a pass.",
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# impure half — the only place that touches the network or a venv
# --------------------------------------------------------------------------


#: One opener for every index read, with an EMPTY ProxyHandler so the ambient
#: `http_proxy`/`https_proxy` are IGNORED — the same transport policy `_clean_env` gives the
#: child. Module-level rather than per-call so the policy is a single object (and so tests
#: swap one seam instead of monkeypatching `urlopen`, which this no longer uses).
#: ⛔ Why it must match the child (#12363 final review, P2): a runner that needs a proxy to
#: reach PyPI would otherwise read INDEX_OK here while the child's proxy-less pip cannot
#: reach the index at all and prints pip's ABSENCE vocabulary — routed to FAIL/exit 1,
#: "a release is owed", an irreversible upload, on every daily run. Proxy-only egress must
#: read INDEX_UNREADABLE (exit 2, detector outage), which is the truth about it.
def _build_index_opener() -> urllib.request.OpenerDirector:
    """Build the index opener with an EMPTY `ProxyHandler`, stamped with its policy.

    ⛔ A FACTORY, not an inline expression, so the policy is MEASURABLE (#12422 review, P1).
    `OpenerDirector.add_handler` appends a handler only when it contributes an
    `*_open`-shaped method, and it SKIPS `proxy_open` by name — so a `ProxyHandler` whose
    `proxies` mapping is EMPTY contributes nothing and is never appended. Consequence:
    with no proxy environment, `build_opener()` and `build_opener(ProxyHandler({}))`
    produce the SAME empty handler list, and an assertion reading the module-level
    opener's handlers cannot tell them apart on a runner without a proxy — i.e. on CI,
    which is where the pin has to be able to fail. Built through this seam AFTER the
    environment is set, the two differ (measured: `build_opener()` ->
    `[{'http': 'http://127.0.0.1:9', ...}]`, `build_opener(ProxyHandler({}))` -> `[]`).

    The `_tortoise_index_policy` stamp is what lets the pin assert that PRODUCTION goes
    through this function: an assertion on the handler list alone passes for a bare
    `build_opener()` too, so a revert of the line below would leave it green.
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener._tortoise_index_policy = "no-proxy"
    return opener


_INDEX_OPENER = _build_index_opener()


def _proxy_hint() -> str:
    """Name the cause when the ambient proxy environment is present and the read failed.

    `_INDEX_OPENER` deliberately ignores `http_proxy`/`https_proxy` — the child's policy is
    "no proxy" (see `_clean_env`), and the two must not disagree. The cost is that a
    PROXY-ONLY egress path reads INDEX_UNREADABLE every day forever, and without this the
    reason string sends the operator to their network with no pointer to the one fact that
    explains it. Measured: on such a box the bare reason is
    `URLError fetching https://pypi.org/…: <urlopen error [Errno 111] Connection refused>`.
    """
    spellings = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    present = [name for name in spellings if os.environ.get(name)]
    if not present:
        return ""
    return (
        " — ambient proxy environment is DELIBERATELY ignored (parity with the child's "
        f"`_clean_env`; set: {', '.join(present)}), so a proxy-only egress path cannot be "
        "graded and this box needs a direct route to the index"
    )


def fetch_index(package: str, timeout: float) -> tuple[dict[str, Any] | None, str, str]:
    """GET the index JSON. Returns (doc, reason, state).

    ``state`` separates the two ways a document can be missing: INDEX_ABSENT (a
    real 404/410 — the project is not on the index) and INDEX_UNREADABLE
    (transport/parse failure). They are different findings and route to different
    humans: absent is a product/release fact, unreadable is a detector outage.
    """
    url = INDEX_JSON.format(package=package)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    # ⛔ THE PARENT HOLDS THE CHILD'S TRANSPORT POLICY. `_clean_env` strips every proxy
    # spelling from the child so no proxy can satisfy the requirement from a mirror — but
    # `urlopen` honours the AMBIENT `http_proxy`/`https_proxy`. On a box that needs a proxy
    # to reach PyPI the two then disagree in the worst direction: the child cannot reach the
    # index and prints pip's ABSENCE vocabulary ("No matching distribution found … (from
    # versions: none)"), while this function answers INDEX_OK over the proxy — which
    # `build_report` routes to FAIL ("users cannot even install it; a release is owed"),
    # exit 1, and a RELEASE-ledger issue asking a human for an irreversible upload, on every
    # daily run. With the proxy ignored here, that box reads INDEX_UNREADABLE instead: exit
    # 2, "a DETECTOR outage is not a release signal", which is the truth about it
    # (#12363 final review, P2). Measured: with `http_proxy=https_proxy=http://127.0.0.1:9`
    # this opener reaches the index while `_clean_env()` returns no `*proxy*` key at all.
    try:
        with _INDEX_OPENER.open(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        state = INDEX_ABSENT if exc.code in (404, 410) else INDEX_UNREADABLE
        return None, f"HTTP {exc.code} from {url}", state
    except http.client.HTTPException as exc:
        # NOT an OSError: `IncompleteRead` / `BadStatusLine` are raised out of
        # `read()`/`getresponse()` and are not wrapped in URLError on 3.12. Left
        # uncaught, a truncated body escaped `fetch_index`, the run exited 1 with a
        # traceback and NO report — which the workflow's own summary then presents as
        # "a release is owed" (#12363 review B2-1).
        return None, f"{type(exc).__name__} reading {url}: {exc}", INDEX_UNREADABLE
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, f"{type(exc).__name__} fetching {url}: {exc}{_proxy_hint()}", INDEX_UNREADABLE
    try:
        doc = json.loads(payload)
    except ValueError as exc:
        return None, f"unparseable JSON from {url}: {exc}", INDEX_UNREADABLE
    if not isinstance(doc, dict):
        return None, f"unexpected JSON shape from {url}", INDEX_UNREADABLE
    return doc, "", INDEX_OK


def _clean_env(home: Path) -> dict[str, str]:
    # ⛔ Compared CASE-INSENSITIVELY. pip's vendored requests calls
    # `getproxies_environment()`, which LOWERCASES every variable name before looking for
    # a proxy, so `Http_Proxy` / `ALL_PROXY` are honoured by the child while a
    # case-sensitive strip walks straight past them — leaving open the very channel (grade
    # a proxy's copy of the artifact) the strip exists to close (#12363 round 7).
    stripped = tuple(name.lower() for name in _STRIPPED_ENV_PREFIXES)
    exact = frozenset(name.lower() for name in _STRIPPED_ENV_NAMES)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.lower().startswith(stripped) and key.lower() not in exact
    }
    env["HOME"] = str(home)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    # ⛔ DO NOT DELETE. `--isolated` does NOT close the config-file class: it skips the
    # USER config, but a GLOBAL `/etc/pip.conf` still loads (measured: `pip config debug
    # --isolated` lists `/etc/pip.conf` and no `user:`), and its `extra-index-url` MERGES
    # with the CLI `--index-url` — so a mirror can satisfy the requirement and this gate
    # reports PASS over bytes PyPI never served. `os.devnull` is the documented kill
    # switch: pip's `Configuration._load_config_files` early-returns for EVERY kind when
    # the ENV entry is devnull, and `iter_config_files` reads `PIP_CONFIG_FILE` OUTSIDE
    # its `isolated` guard, which is precisely why this works where the flag does not
    # (#12363 review round 6).
    env["PIP_CONFIG_FILE"] = os.devnull
    # A fresh, empty database dir is the whole point: the documented path under
    # test is the FIRST run on a machine that has never run tortoise.
    env.pop("XDG_CONFIG_HOME", None)
    return env


def install_and_run(
    *,
    package: str,
    version: str,
    workdir: Path,
    timeout: float,
    runner=subprocess.run,
    wheel: Path | None = None,
    transcript_path: Path | None = None,
    absent_probe=None,
) -> tuple[Arm, str]:
    """Install the PUBLISHED wheel and run the documented first command.

    Returns (arm, transcript). The transcript is the evidence — a bare
    "exit 1" without the command's own output cannot distinguish the #2204
    signature from an unrelated failure.

    ``transcript_path`` is written INCREMENTALLY, as each command finishes. A job
    cancelled at its ``timeout-minutes`` boundary never reaches the final write, so a
    transcript produced only at the end is absent in exactly the terminal state where
    the diagnosis is worth most (#12363 review round 3).
    """
    venv = workdir / "venv"
    home = workdir / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = _clean_env(home)
    # WHEEL mode (`wheel is not None`) — the pre-publish gate — installs the exact bytes that
    # are about to be uploaded, by path: an index-fetched package here would test the
    # PREVIOUS release instead of this build.
    #
    # ⛔ INDEX mode is UNPINNED, because that is the DOCUMENTED command (#12363 round 15). This used to
    # run `f"{package}=={version}"` with `version = info.version`, which is a DIFFERENT
    # question from `pip install tortoise-graph` in exactly the case that matters.
    # PyPI yanks WHOLE RELEASES, and `info.version` keeps naming a yanked release. For
    # an exact `==` pin pip INSTALLS THE YANKED FILE (it prefers the pin over the yank
    # rule); for the unpinned requirement it IGNORES the yanked release and resolves to
    # the previous one, or fails outright if no other release has an installable file.
    # Measured with pip against a local PEP 503 page whose only release is yanked:
    #   `pip download <name>`           → "Ignored the following yanked versions: 0.3.0"
    #                                     "No matching distribution found"
    #   `pip download <name>==0.3.0`    → resolves the yanked release (accepted, then
    #                                     failed only on the fixture's empty file)
    # So the pinned form reported PASS over bytes no unpinned user could ever get, and —
    # because the close gate reads `exit_code == '0'` — RESOLVED both alert ledgers on a
    # run whose documented install is impossible. It cut the other way too: with the
    # latest release yanked-and-broken and an older good one, the pinned form tested bytes
    # nobody receives.
    # The arm's whole job is the documented path, so it now runs the documented spec.
    # ⛔ `version` is NOT read by this function and must not be threaded back into
    # `spec`: it is accepted only because every call site already passes it, and the
    # release-age row that mentions a version is recomputed in `build_report` from
    # the index document instead (informational, never gated). Re-pinning the spec to
    # `f"{package}=={version}"` reinstates the yanked-release defect this comment and
    # `test_index_mode_installs_the_DOCUMENTED_unpinned_spec` exist to forbid.
    spec = str(Path(wheel).resolve()) if wheel is not None else package

    transcript: list[str] = []

    def flush() -> None:
        if transcript_path is None:
            return
        try:
            transcript_path.parent.mkdir(parents=True, exist_ok=True)
            transcript_path.write_text("\n".join(transcript) + "\n")
        except OSError:
            # Evidence is best-effort; never let an unwritable log change the verdict.
            pass

    def run(cmd: list[str], label: str) -> subprocess.CompletedProcess:
        transcript.append(f"$ {' '.join(cmd)}   # {label}")
        flush()
        completed = runner(
            cmd,
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        for stream in (completed.stdout, completed.stderr):
            if stream:
                transcript.append(stream.rstrip())
        transcript.append(f"[exit {completed.returncode}]")
        flush()
        return completed

    try:
        created = run([sys.executable, "-m", "venv", str(venv)], "clean venv")
        if created.returncode != 0:
            return (
                Arm("engine-init", UNKNOWN, "could not create the clean venv"),
                "\n".join(transcript),
            )
        installed = run(
            [
                str(venv / "bin" / "pip"),
                "install",
                "--quiet",
                "--no-cache-dir",
                # ⛔ --index-url names PyPI EXPLICITLY, and --isolated suppresses the
                # USER config file and every PIP_* env var. Both are required: measured,
                # `--index-url` alone leaves a config-file `extra-index-url` MERGING
                # ("Looking in indexes: https://pypi.org/simple, http://mirror/…"), so a
                # runner with a mirror configured could resolve tortoise-graph==0.2.0
                # from the MIRROR and report PASS over bytes PyPI never served
                # (#12363 round 4).
                #
                # ⛔ WHAT --isolated DOES *NOT* COVER, stated because the comment here
                # used to claim it "IGNORES every config file" and that is false
                # (#12363 round 5). Read pip's source, not its help text:
                # `Configuration.iter_config_files` yields the GLOBAL config
                # UNCONDITIONALLY and guards only USER with `not self.isolated`, and
                # `load()` gates `_load_environment_vars` the same way. Measured:
                # `pip config debug --isolated` in a fresh venv prints `global:
                # /etc/xdg/pip/pip.conf, /etc/pip.conf` and NO `user:` entry. A
                # SYSTEM-level `extra-index-url` therefore still MERGES with this CLI
                # value: the mirror class is NARROWED, not closed — a residual bounded by
                # the fresh venv + fresh HOME, and real on any runner carrying a system
                # pip config (this repo's `ubuntu-latest` can route to a self-hosted
                # runner, #7867).
                #
                # ⛔ Proxies are not pip OPTIONS at all (pip has no `trust_env=False`), so
                # `http(s)_proxy`/`all_proxy` are consumed by its vendored requests and
                # survive --isolated. That is why `_STRIPPED_ENV_PREFIXES` is
                # LOAD-BEARING rather than belt-and-braces — do not delete it as
                # redundant with this flag.
                "--isolated",
                "--index-url",
                PYPI_SIMPLE,
                spec,
            ],
            f"install {spec}" if wheel is not None else f"install the PUBLISHED artifact {spec}",
        )
        if installed.returncode != 0 and not _install_failure_is_absence(
            installed.stderr or "", wheel, package
        ):
            # A pip failure is not evidence that the ARTIFACT is broken: a DNS blip,
            # an index outage or a proxy 502 all land here, and reporting those as
            # "users cannot even install it — a release is owed" is the B1 mis-routing
            # one layer down (#12363 review round 3).
            return (
                Arm(
                    "engine-init",
                    UNKNOWN,
                    f"`pip install` could not be evaluated (exit {installed.returncode}): "
                    # ⛔ The LAST LINE, not `[-1:]` — a one-element LIST interpolates its
                    # repr (`['ERROR: …']`) into the arm detail, which is rendered
                    # verbatim in the step summary and the `--json` payload (#12363 round 17;
                    # ⛔ NOT the alert issue's body — that body is `$why` + `tail -n 40` of the
                    # TRANSCRIPT file, so an arm detail reaches it only by way of the pip output
                    # the transcript already carries. Measured, #12422 round-18 review P3.)
                    f"{next(iter((installed.stderr or '').strip().splitlines()[-1:]), 'no output')}",
                ),
                "\n".join(transcript),
            )
        if installed.returncode != 0 and wheel is None:
            # pip's absence vocabulary matched — but pip prints it for an UNREACHABLE
            # index too (measured), so probe before telling a human that users cannot
            # install the release. THREE answers, not two (#12363 round 11):
            #   anything that is not one of the two NAMED states below → UNKNOWN — an
            #     ALLOW-LIST. INDEX_UNREADABLE is that case, and so is every unnamed value:
            #     the probe got no usable answer, so the pip failure is unexplained — a
            #     detector outage, never a release call.
            #   INDEX_ABSENT     → FAIL. The project is gone from the index.
            #   INDEX_OK         → FAIL. ⛔ THIS BRANCH USED TO RETURN UNKNOWN, WHICH MADE
            #     THE FAIL ARM BELOW UNREACHABLE. `_main` only calls this function after a
            #     SUCCESSFUL fetch of this very URL, so the probe — a second read of the
            #     same endpoint — answers `ok`; `confirmed != INDEX_ABSENT` then reported
            #     "the index could not confirm the release is absent", i.e. exit 2, which
            #     BOTH consumers render as "detector outage — do NOT cut a release"
            #     (`release-artifact-canary.yml`'s summary and
            #     `published-artifact-alert.sh`'s TITLE_OUTAGE branch). A release that is
            #     advertised but uninstallable (no files uploaded for `info.version`, every
            #     file yanked, a wheel pip rejects) therefore reached the human as the
            #     OPPOSITE of the finding, and only a race (the project 404ing between two
            #     consecutive reads of one URL) could ever reach the FAIL. `ok` means the
            #     index IS reachable and DOES serve the project while pip cannot resolve the
            #     exact version — a finding about the ARTIFACT, not about the detector.
            probe = absent_probe or (lambda name: index_absence_probe(name))
            confirmed = probe(package)
            # ⛔ AN ALLOW-LIST, not `== INDEX_UNREADABLE` (#12422 round-19 review P2).
            # The two NAMED states below are the only ones that justify a verdict; every
            # other value — `None`, `""`, or a state a later refactor introduces — means
            # the probe produced no reading, so it must not be rendered as "the index
            # still serves the project (None)" and must not return the FAIL that sends a
            # human to cut an IRREVERSIBLE release. Deny-listing the UNKNOWN put the
            # unknown side of the fork on the dangerous verdict — the mirror of the
            # round-11 defect this same block already fixed.
            if confirmed not in (INDEX_ABSENT, INDEX_OK):
                return (
                    Arm(
                        "engine-init",
                        UNKNOWN,
                        f"`pip install {spec}` failed and the index probe returned no "
                        f"usable state ({confirmed!r}) — a detector outage, not a reading "
                        f"about the artifact",
                    ),
                    "\n".join(transcript),
                )
            index_says = (
                "the project is ABSENT from the index"
                if confirmed == INDEX_ABSENT
                else f"the index still serves the project ({confirmed})"
            )
            return (
                Arm(
                    "engine-init",
                    FAIL,
                    f"`pip install {spec}` failed (exit {installed.returncode}) and "
                    f"{index_says} — users cannot even install it; a release is owed",
                ),
                "\n".join(transcript),
            )
        if installed.returncode != 0:
            # WHEEL mode only: an index-mode failure returned above (its probe always
            # answers, and every answer routes to a verdict), so a branch on `wheel`
            # here would be dead. Assert the invariant rather than carrying a
            # conditional that no run can reach.
            assert wheel is not None, (
                "an index-mode install failure reached the built-wheel arm without a probe "
                "verdict — the probe block above must return for every state"
            )
            return (
                Arm(
                    "engine-init",
                    FAIL,
                    (
                        f"the BUILT wheel {wheel.name} failed to install (exit "
                        f"{installed.returncode}) — the artifact about to be "
                        f"uploaded is not installable"
                    ),
                ),
                "\n".join(transcript),
            )
        console = venv / "bin" / "tortoise"
        if not console.exists():
            # The install SUCCEEDED, so this is not a detector outage: the artifact
            # ships no `tortoise` console script and the documented first command
            # cannot exist. Reporting it as UNKNOWN ("could not run") would file an
            # artifact defect under "the canary is broken" (#12363 round 5).
            return (
                Arm(
                    "engine-init",
                    FAIL,
                    f"the installed artifact provides no `tortoise` console script at "
                    f"{console} — the documented first command cannot be run"
                    + ("" if wheel is None else f" (BUILT wheel {wheel.name})"),
                ),
                "\n".join(transcript),
            )
        swept = run([str(console), "init"], "the documented first command, fresh HOME")
    except subprocess.TimeoutExpired as exc:
        # A command killed at its `--timeout` budget keeps whatever it printed BEFORE the
        # kill: the transcript is the red's only evidence, and a bare `$ …` line reads as
        # "the command was silent" in exactly the hung-command state the alarm exists to
        # explain (#12363 round 17).
        #
        # ⛔ `run()` DOES attach that output on POSIX — as **bytes**, even with
        # `text=True` (`_check_timeout` joins the raw sequences). The decode is
        # load-bearing: the first cut of this handler appended `exc.stdout` directly and
        # raised `TypeError: sequence item 1: expected str instance, bytes found` at the
        # documented `runner=` seam, turning a timeout into exit 2 (measured, 3.12.3).
        # A runner that attaches nothing leaves `None`, and THAT is named below rather
        # than left looking like silence.
        recovered = False
        for stream in (exc.stdout, exc.stderr):
            if stream:
                if isinstance(stream, bytes):
                    stream = stream.decode(errors="replace")
                transcript.append(stream.rstrip())
                recovered = True
        transcript.append(
            f"[killed at the {exc.timeout}s budget — the command did not finish; "
            + (
                "the output above is what it produced first]"
                if recovered
                else "no output was recovered]"
            )
        )
        flush()
        return (
            Arm("engine-init", UNKNOWN, f"timed out after {exc.timeout}s"),
            "\n".join(transcript),
        )
    except OSError as exc:
        return (Arm("engine-init", UNKNOWN, f"could not run: {exc}"), "\n".join(transcript))

    db_path = home / ".tortoise" / "tortoise.db"
    # ⛔ The DEFAULT path is a second copy of `tortoise/config.py`'s decision. If a release
    # relocates the default, a hardcoded expectation reds this canary FOREVER and the
    # summary then says "a release is owed" when the fix belongs in this tool. Requiring a
    # database SOMEWHERE under the fresh HOME keeps the property under test ("the command
    # really initialised storage") without freezing the location (#12363 round 7).
    if not db_path.is_file():
        # …but the candidate must still be TORTOISE's store: "any *.db under HOME" would
        # accept a cache/settings/telemetry sqlite file that some unrelated code path
        # created, turning the "reported success but created no database" arm into one that
        # can never fire (#12363 round 8).
        # ⛔ Matched on the path RELATIVE to HOME. `"tortoise" in str(p)` is satisfied by
        # any ANCESTOR named tortoise — a dev run inside the checkout, or a pool runner
        # whose TMPDIR sits under one — so a foreign telemetry `.db` would be accepted and
        # the "created no database" arm could never fire (#12363 round 9).
        created = sorted(
            p
            for p in home.rglob("*.db")
            if "tortoise" in p.name.lower()
            or "tortoise" in p.parent.name.lower()
            or "tortoise" in str(p.relative_to(home)).lower()
        )
        if created:
            db_path = created[0]
    if swept.returncode != 0:
        return (
            Arm(
                "engine-init",
                FAIL,
                (
                    f"`tortoise init` exits {swept.returncode} on a clean HOME with "
                    f"the BUILT wheel {wheel.name} (see the transcript)"
                    if wheel is not None
                    else f"`tortoise init` exits {swept.returncode} on a clean machine "
                    f"with {spec} from the index (see the transcript)"
                ),
            ),
            "\n".join(transcript),
        )
    if not db_path.exists():
        return (
            Arm(
                "engine-init",
                FAIL,
                f"`tortoise init` reported success but created no database at {db_path}",
            ),
            "\n".join(transcript),
        )
    return (
        Arm(
            "engine-init",
            OK,
            f"`tortoise init` succeeds on a clean HOME and creates {db_path.name} "
            f"({Path(spec).name})",
        ),
        "\n".join(transcript),
    )


def main(argv: list[str] | None = None) -> int:
    """Exit 0 green / 1 the artifact is broken / 2 the canary could not answer.

    ⛔ THE BACKSTOP for "an I/O fault must never borrow the release verdict". Exit 1 is
    reserved for "the published artifact is broken", and BOTH consumers read it that way
    — the workflow's alarm step and `published-artifact-alert.sh` — so any unexpected
    exception that escaped with 1 would tell a human to cut an irreversible release for a
    detector fault. `_main` guards its own I/O sites with specific messages (the
    `pyproject.toml` read, the log write, the `--summary-file` append); this wrapper
    catches everything else, including a bug nobody anticipated (#12363 rounds 5 and 6).
    """
    try:
        return _main(argv)
    except Exception as exc:  # the exit code IS the contract here — see the docstring
        print(
            f"the canary could not evaluate the artifact: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smoke the PUBLISHED PyPI artifacts (the documented first command, not the tree)."
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--summary-file",
        default="",
        help="append the markdown report here (e.g. $GITHUB_STEP_SUMMARY)",
    )
    parser.add_argument(
        "--transcript-dir",
        default="",
        help="write the install/run transcript here (evidence for a red)",
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="per COMMAND budget, seconds")
    parser.add_argument(
        "--fetch-timeout",
        type=float,
        default=60.0,
        help="per index FETCH budget, seconds (bounded separately: `--timeout` bounds "
        "the venv/pip/init commands, and a caller budgeting a CI job needs both)",
    )
    parser.add_argument(
        "--wheel",
        default=None,
        help="wheel mode: test a locally built wheel (the pre-publish gate) instead "
        "of the index artifact; the index arms are not consulted",
    )
    parser.add_argument("--engine-package", default=ENGINE_PACKAGE, help=argparse.SUPPRESS)
    parser.add_argument("--client-package", default=CLIENT_PACKAGE, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    # ⛔ The flag's PRESENCE is significant, not its truthiness. `--wheel ""` used to
    # collapse to index mode, so a release step whose `$(ls dist/*.whl)` matched nothing
    # silently tested the PREVIOUSLY PUBLISHED release and exited 0 while a broken new
    # wheel was uploaded (#12363 review B2/E3). An empty value is an error, never a
    # fallback.
    wheel = Path(args.wheel) if args.wheel is not None else None
    if wheel is not None and not str(args.wheel).strip():
        print(
            "--wheel was passed with an empty path — refusing to fall back to the index artifact",
            file=sys.stderr,
        )
        return 2
    if wheel is not None and not wheel.is_file():
        print(f"no such wheel: {wheel}", file=sys.stderr)
        return 2

    if wheel is not None:
        engine_doc, engine_error, engine_state = None, "", INDEX_OK
        client_doc, client_error, client_state = None, "", INDEX_OK
    else:
        engine_doc, engine_error, engine_state = fetch_index(
            args.engine_package, args.fetch_timeout
        )
        client_doc, client_error, client_state = fetch_index(
            args.client_package, args.fetch_timeout
        )

    init_arm: Arm | None = None
    transcript = ""
    log_path = (
        Path(args.transcript_dir) / "published-artifact-canary.log" if args.transcript_dir else None
    )
    published = latest_version(engine_doc or {})
    if wheel is not None or (engine_doc is not None and published):
        with tempfile.TemporaryDirectory(prefix="artifact-canary-") as tmp:
            init_arm, transcript = install_and_run(
                package=args.engine_package,
                version=published,
                workdir=Path(tmp),
                timeout=args.timeout,
                wheel=wheel,
                transcript_path=log_path,
                # The probe is a THIRD index fetch and must be bounded by the same
                # budget the workflow's arithmetic counts (#12363 round 5).
                absent_probe=lambda name: index_absence_probe(name, args.fetch_timeout),
            )

    # ⛔ Every I/O fault left in this function exits 2, never a traceback: a traceback
    # IS exit 1, and exit 1 is what BOTH consumers render as "the published artifact is
    # broken — a release is owed" (the run summary and the alert issue's alarm text).
    # An unreadable pyproject.toml or an unwritable `--summary-file` is a DETECTOR fault
    # and must not borrow the release verdict — the same class as the round-3
    # `IncompleteRead` escape, at the two call sites that were still unguarded (#12363
    # round 5).
    try:
        tree_version = source_version((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except OSError as exc:
        print(
            f"the canary cannot report: could not read {REPO_ROOT / 'pyproject.toml'} "
            f"({type(exc).__name__}: {exc}) — a detector fault, not a finding about the "
            f"published artifact",
            file=sys.stderr,
        )
        return 2

    report = build_report(
        engine_doc=engine_doc,
        client_doc=client_doc,
        engine_error=engine_error,
        client_error=client_error,
        tree_version=tree_version,
        init_arm=init_arm,
        now=datetime.now(UTC),
        wheel_path=wheel,
        engine_state=engine_state,
        client_state=client_state,
    )

    if log_path is not None and not transcript:
        # The install never ran: no engine index document came back, or the document came
        # back carrying no published version. NOT "an unreadable index" — a 404/410 is a
        # readable ANSWER that the project is not on the index, and a document that parses
        # but names no version was read perfectly well. The line below writes the actual
        # state, so it distinguishes a finding from the absence of an answer; this comment
        # must not re-assert the 404-versus-timeout equivalence that the verdict arms and
        # `build_report`'s docstring exist to keep apart.
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                f"(no install/run: engine index state={engine_state} "
                f"{'— ' + engine_error if engine_error else ''})\n",
                encoding="utf-8",
            )
        except OSError as exc:
            print(
                f"the canary cannot report: could not write {log_path} "
                f"({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return 2

    summary = render_summary(report)
    if args.summary_file:
        try:
            with open(args.summary_file, "a", encoding="utf-8") as handle:
                handle.write(summary)
        except OSError as exc:
            print(
                f"the canary cannot report: could not append to --summary-file "
                f"{args.summary_file} ({type(exc).__name__}: {exc}) — no report reached "
                f"the caller, so the verdict is UNKNOWN, not the artifact's",
                file=sys.stderr,
            )
            return 2

    if args.json:
        print(
            json.dumps(
                {
                    "verdict": report.verdict,
                    "exit_code": report.exit_code,
                    "arms": [
                        {
                            "name": a.name,
                            # ⛔ THE MACHINE SURFACE RENDERS IT THE SAME WAY THE TABLE DOES —
                            # by CALLING the table's renderer (#12589 review rounds 10 and 11).
                            # Round 10 gave this line its own copy of the rule, which fixed the
                            # non-gated half and left the gated half publishing a RAW status: an
                            # arm holding an unnamed status read "CANNOT RUN" on screen and that
                            # spelling in the payload. One function, both surfaces (P3).
                            "status": rendered_status(a),
                            "gated": a.gated,
                            "detail": a.detail,
                        }
                        for a in report.arms
                    ],
                },
                indent=2,
            )
        )
    else:
        print(summary)
        if transcript:
            print(transcript)
    return report.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
