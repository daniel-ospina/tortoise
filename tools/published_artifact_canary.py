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
and ``pip install tortoise-client`` — documented twice in README.md — 404s
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
                          offers it twice as a supported install ("Scripting /
                          integration / agent tooling"), so a 404 is a broken
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
  2  the canary could not be evaluated (unreadable index, no venv/network, a
     failed install) — fail-closed, reported as RED because a detector that
     cannot run must never look like a pass (the no-op-gate class)

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

import argparse
import http.client
import json
import os
import subprocess
import sys
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

OK = "OK"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"

#: Every env var named here is stripped from the child's environment before the
#: documented command runs. The canary must measure the DEFAULT path a new user
#: gets — an ambient ``TORTOISE_DB_URI``/``TORTOISE_DB_PATH`` inherited from the
#: runner would point the command at some other target and turn a broken
#: artifact green (the "measures something other than what it claims" class).
_STRIPPED_ENV_PREFIXES = ("TORTOISE_", "FALKORDB_", "REDIS_")

#: Named explicitly, not by prefix: these decide WHICH BYTES the child imports,
#: so a ``PYTHONPATH`` entry pointing at a checkout puts that tree's ``tortoise/``
#: ahead of the fresh venv's site-packages and the documented command executes the
#: TREE instead of the artifact under test — the #2204 shape (``grep -c makedirs``:
#: 1 in the tree, 0 in the 0.2.0 wheel) would report PASS. ``VIRTUAL_ENV``/
#: ``PIP_TARGET``/``PIP_PREFIX`` do the same by redirecting the install or the
#: interpreter, and the pip INDEX variables choose WHICH INDEX the install resolves
#: from: on a box with a mirror configured, the canary would grade the mirror's copy
#: instead of what PyPI serves. Same reasoning as the prefix list above, one layer
#: lower. (``PIP_DISABLE_PIP_VERSION_CHECK`` is NOT here — the tool sets it itself.)
_STRIPPED_ENV_NAMES = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "VIRTUAL_ENV",
    "PIP_TARGET",
    "PIP_PREFIX",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_NO_INDEX",
    "PIP_CONFIG_FILE",
    "PIP_FIND_LINKS",
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
                    f"`pip install {CLIENT_PACKAGE}` twice"
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
            report.add(
                Arm(
                    "client-published",
                    OK,
                    f"{CLIENT_PACKAGE} resolves on the index ({client_version})",
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
            lines += [
                "",
                "The BUILT wheel fails its documented first command. Nothing has been "
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
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(_STRIPPED_ENV_PREFIXES) and key not in _STRIPPED_ENV_NAMES
    }
    env["HOME"] = str(home)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
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
) -> tuple[Arm, str]:
    """Install the PUBLISHED wheel and run the documented first command.

    Returns (arm, transcript). The transcript is the evidence — a bare
    "exit 1" without the command's own output cannot distinguish the #2204
    signature from an unrelated failure.
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

    def run(cmd: list[str], label: str) -> subprocess.CompletedProcess:
        transcript.append(f"$ {' '.join(cmd)}   # {label}")
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
                spec,
            ],
            f"install {spec}" if wheel is not None else f"install the PUBLISHED artifact {spec}",
        )
        if installed.returncode != 0:
            return (
                Arm(
                    "engine-init",
                    FAIL,
                    (
                        f"the BUILT wheel {wheel.name} failed to install (exit "
                        f"{installed.returncode}) — the artifact about to be "
                        f"uploaded is not installable"
                        if wheel is not None
                        else f"`pip install {spec}` from the index failed (exit "
                        f"{installed.returncode}) — users cannot even install it"
                    ),
                ),
                "\n".join(transcript),
            )
        swept = run(
            [str(venv / "bin" / "tortoise"), "init"],
            "the documented first command, fresh HOME",
        )
    except subprocess.TimeoutExpired as exc:
        return (
            Arm("engine-init", UNKNOWN, f"timed out after {exc.timeout}s"),
            "\n".join(transcript),
        )
    except OSError as exc:
        return (Arm("engine-init", UNKNOWN, f"could not run: {exc}"), "\n".join(transcript))

    db_path = home / ".tortoise" / "tortoise.db"
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
    published = latest_version(engine_doc or {})
    if wheel is not None or (engine_doc is not None and published):
        with tempfile.TemporaryDirectory(prefix="artifact-canary-") as tmp:
            init_arm, transcript = install_and_run(
                package=args.engine_package,
                version=published,
                workdir=Path(tmp),
                timeout=args.timeout,
                wheel=wheel,
            )

    report = build_report(
        engine_doc=engine_doc,
        client_doc=client_doc,
        engine_error=engine_error,
        client_error=client_error,
        tree_version=source_version((REPO_ROOT / "pyproject.toml").read_text()),
        init_arm=init_arm,
        now=datetime.now(UTC),
        wheel_path=wheel,
        engine_state=engine_state,
        client_state=client_state,
    )

    if args.transcript_dir:
        target = Path(args.transcript_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "published-artifact-canary.log").write_text(transcript or "(no run)\n")

    summary = render_summary(report)
    if args.summary_file:
        with open(args.summary_file, "a", encoding="utf-8") as handle:
            handle.write(summary)

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
