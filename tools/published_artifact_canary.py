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

#: pip's "this distribution does not exist on the index" vocabulary. Used to tell a
#: genuinely absent artifact (FAIL) from a pip run that could not be evaluated at all
#: (network/proxy/config trouble → UNKNOWN). Matching is on pip's own wording because
#: that is the only signal a subprocess gives us; the cost of being wrong in the
#: UNKNOWN direction is a non-zero exit with a clear detail, not a silent pass.
_INSTALL_ABSENCE_MARKERS = (
    "no matching distribution found",
    "could not find a version that satisfies",
    "from versions: none",
)


def _install_failure_is_absence(stderr: str, wheel: Path | None = None) -> bool:
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
    """
    text = (stderr or "").lower()
    if wheel is not None:
        return Path(wheel).name.lower() in text
    return any(marker in text for marker in _INSTALL_ABSENCE_MARKERS)


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
    uninstallable release. UNKNOWN is reserved for the one state that genuinely has no
    answer: INDEX_UNREADABLE.

    ⛔ Do NOT "restore" a `state != INDEX_ABSENT -> UNKNOWN` test here or at the caller:
    that is the defect, not the protection. The round-3 protection it was meant to give —
    pip prints its absence vocabulary for an UNREACHABLE index too — is preserved by
    keeping UNREADABLE (and only UNREADABLE) on the UNKNOWN path.
    """
    _doc, _reason, state = fetch_index(package, timeout)
    return state


OK = "OK"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

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
        return OK

    @property
    def exit_code(self) -> int:
        return {OK: 0, FAIL: 1, UNKNOWN: 2}[self.verdict]


def latest_version(index_doc: dict[str, Any]) -> str:
    """The version the index serves as latest (``info.version``), else ""."""
    info = index_doc.get("info") or {}
    version = info.get("version")
    return version if isinstance(version, str) else ""


def release_files(index_doc: dict[str, Any], version: str) -> list[dict[str, Any]]:
    releases = index_doc.get("releases") or {}
    files = releases.get(version) or []
    return [f for f in files if isinstance(f, dict)]


def upload_time(index_doc: dict[str, Any], version: str) -> datetime | None:
    """The oldest upload time on ``version`` — when the release first existed.

    Oldest, not newest: a later re-upload of one file does not make the release
    younger, and the question this answers is "how long has this release been
    what users get".
    """
    stamps = []
    for entry in release_files(index_doc, version):
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

    ``None`` plus an error string means the index could not be read; that is an
    UNKNOWN (fail-closed), never an OK. ``init_arm`` is None when the install
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
                f"could not read the {ENGINE_PACKAGE} index metadata: "
                f"{engine_error or 'no result'}",
            )
        )
    else:
        version = latest_version(engine_doc)
        if not version:
            report.add(
                Arm(
                    "engine-index",
                    FAIL,
                    f"index metadata for {ENGINE_PACKAGE} carries no latest version",
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
            report.add(
                Arm(
                    "client-published",
                    FAIL,
                    f"the {CLIENT_PACKAGE} index document carries no published version",
                )
            )
        else:
            # ⛔ `info.version` ALONE IS NOT "resolves" (#12363 round 14). `pip install
            # tortoise-client` (README's documented command, UNPINNED) resolves from the
            # latest release's FILES, not from the version string, and PEP 592 skips yanked
            # files for an unpinned requirement. So a release that advertises a version but
            # ships no installable file — a file deleted after publish, the release record
            # created before its upload, every file yanked — fails for 100% of users while
            # this arm reported PASS, filed nothing, and (because the close gate reads
            # `exit_code == '0'`) actively RESOLVED the release-owed ledger. Measured:
            # `build_report` with `releases: {"0.2.0": []}` and with the only file
            # `yanked: True` both returned `Verdict: PASS` / `exit_code 0` before this.
            # The ENGINE arm was never fooled the same way only because pip has to INSTALL
            # it; the client arm has no install and no probe, so the file list IS its only
            # evidence. Same defect class the module docstring names at "no files uploaded
            # for `info.version`, every file yanked", which it claimed to catch.
            installable = [
                entry
                for entry in release_files(client_doc, client_version)
                if not entry.get("yanked")
            ]
            if installable:
                report.add(
                    Arm(
                        "client-published",
                        OK,
                        f"{CLIENT_PACKAGE} resolves on the index ({client_version}, "
                        f"{len(installable)} installable file(s))",
                    )
                )
            else:
                listed = len(release_files(client_doc, client_version))
                report.add(
                    Arm(
                        "client-published",
                        FAIL,
                        f"the {CLIENT_PACKAGE} index advertises {client_version} but lists "
                        f"NO installable file for it ({listed} file(s), none usable) — README.md "
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
                relationship = (
                    "the tree carries the same version — a bump IS required: the "
                    "published build at this version predates the fix"
                    if report.verdict == FAIL
                    else "the tree's version (no bump pending)"
                )
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
    glyph = {OK: "PASS", FAIL: "FAIL", UNKNOWN: "CANNOT RUN"}
    lines = [
        "## Published-artifact canary (#12363)",
        "",
        f"**Verdict: {glyph[report.verdict]}**",
        "",
        "| arm | gated | reading |",
        "|---|---|---|",
    ]
    for arm in report.arms:
        detail = arm.detail.replace("|", "\\|")
        lines.append(f"| `{arm.name}` | {'yes' if arm.gated else 'no'} | {detail} |")
    if report.verdict == FAIL:
        if report.wheel_mode:
            # Branch on which arm actually failed. A substring test was wrong twice: the
            # console-script arm's detail says "the installed artifact provides no
            # `tortoise` console script", which contains "install" and rendered as "does
            # not install" — false, it installed cleanly. Match the LEADING clause, and
            # name the entry-point case separately (#12363 rounds 5 and 6).
            step = next((arm for arm in report.arms if arm.gated and arm.status == FAIL), None)
            detail = step.detail if step is not None else ""
            if detail.startswith("the BUILT wheel") and "failed to install" in detail:
                what = "does not install"
            elif "console script" in detail:
                what = "installs but ships no `tortoise` console script, so the documented first command cannot run"
            else:
                what = "fails its documented first command"
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
        lines += [
            "",
            "The canary could not read the index or could not exercise the "
            "artifact. Reported as RED on purpose: a detector that cannot run "
            "must never look like a pass.",
        ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# impure half — the only place that touches the network or a venv
# --------------------------------------------------------------------------


def fetch_index(package: str, timeout: float) -> tuple[dict[str, Any] | None, str, str]:
    """GET the index JSON. Returns (doc, reason, state).

    ``state`` separates the two ways a document can be missing: INDEX_ABSENT (a
    real 404/410 — the project is not on the index) and INDEX_UNREADABLE
    (transport/parse failure). They are different findings and route to different
    humans: absent is a product/release fact, unreadable is a detector outage.
    """
    url = INDEX_JSON.format(package=package)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
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
        return None, f"{type(exc).__name__} fetching {url}: {exc}", INDEX_UNREADABLE
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
    if wheel is not None:
        # WHEEL mode — the pre-publish gate. Install the exact bytes that are
        # about to be uploaded, by path: an index-fetched package here would
        # test the PREVIOUS release instead of this build.
        spec = str(Path(wheel).resolve())
    else:
        spec = f"{package}=={version}" if version else package
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
            installed.stderr or "", wheel
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
                    f"{(installed.stderr or '').strip().splitlines()[-1:] or ['no output']}",
                ),
                "\n".join(transcript),
            )
        if installed.returncode != 0 and wheel is None:
            # pip's absence vocabulary matched — but pip prints it for an UNREACHABLE
            # index too (measured), so probe before telling a human that users cannot
            # install the release. THREE answers, not two (#12363 round 11):
            #   INDEX_UNREADABLE → UNKNOWN. The probe got no answer, so the pip failure
            #     is unexplained: a detector outage, never a release call.
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
            if confirmed == INDEX_UNREADABLE:
                return (
                    Arm(
                        "engine-init",
                        UNKNOWN,
                        f"`pip install {spec}` failed and the index could not be re-read "
                        f"to establish whether the release exists ({confirmed}) — a "
                        f"detector outage, not a reading about the artifact",
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
        # The install never ran (unreadable index): say so in the log rather than leaving
        # an empty file the reader has to interpret.
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
                            "status": a.status,
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
