"""Offline tests for `tools/published_artifact_canary.py` (#12363).

The canary's job is to report the PUBLISHED artifact, so its verdict logic must
be provable without the network — otherwise the only way to know what it would
say is to wait for a scheduled run, which is the state the repo was in for 97
days. Everything here exercises the pure half (`build_report`, the parsers) or
`install_and_run` through an injected runner; nothing here touches PyPI.

The discriminating property under test is FALSIFIABILITY: each arm must be able
to come back RED. A checker whose arms can only be green is the no-op-gate
failure this tool exists to remove, so most tests below assert a RED, not a
green.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_tool():
    """Import `tools/published_artifact_canary.py` by path.

    `tools/` is not a package (`tools/__init__.py` exists but the repo does not
    import tools as a library), and a plain `import tools.x` would couple this
    test to that. Loading by path keeps the tool runnable as a script and
    importable here without touching `sys.path`.
    """
    path = REPO_ROOT / "tools" / "published_artifact_canary.py"
    spec = importlib.util.spec_from_file_location("published_artifact_canary", path)
    module = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: `@dataclass` resolves the defining module through
    # `sys.modules[cls.__module__]`, and dataclasses in a never-registered module
    # raise AttributeError ('NoneType' has no __dict__) at import time.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


canary = _load_tool()
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


def _engine_doc(version: str = "0.2.0", uploaded: str = "2026-08-08T00:30:10.719404Z"):
    return {
        "info": {"version": version},
        "releases": {
            version: [
                {
                    "filename": f"tortoise_graph-{version}-py3-none-any.whl",
                    "upload_time_iso_8601": uploaded,
                }
            ]
        },
    }


def _ok_init_arm():
    return canary.Arm("engine-init", canary.OK, "`tortoise init` succeeds")


# --------------------------------------------------------------------------
# the verdict is driven by the gated arms, in the right precedence
# --------------------------------------------------------------------------


def test_all_gated_arms_green_is_the_only_green():
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert report.verdict == canary.OK
    assert report.exit_code == 0


def test_a_broken_first_command_is_red_and_wins_over_anything_else():
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "`tortoise init` exits 1"),
        now=NOW,
    )
    assert report.verdict == canary.FAIL
    assert report.exit_code == 1


def test_absent_client_distribution_is_red_not_absent_ok():
    """README.md documents `pip install tortoise-client` twice; a 404 is a
    broken documented path. The arm must be gated, or the 404 that has been
    live since 2026-08-08 could never red anything."""
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=None,
        client_error="HTTP 404 from https://pypi.org/pypi/tortoise-client/json",
        client_state=canary.INDEX_ABSENT,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert report.get("client-published").gated is True
    assert report.get("client-published").status == canary.FAIL
    assert report.verdict == canary.FAIL
    assert "HTTP 404" in report.get("client-published").detail


def test_an_unreadable_index_is_unknown_and_exits_nonzero():
    """Fail-closed: a detector that cannot read the index must never look like a
    pass (the no-op-gate class)."""
    report = canary.build_report(
        engine_doc=None,
        engine_error="URLError fetching https://pypi.org/pypi/tortoise-graph/json",
        engine_state=canary.INDEX_UNREADABLE,
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert report.get("engine-index").status == canary.UNKNOWN
    assert report.get("engine-init").status == canary.UNKNOWN
    assert report.verdict == canary.UNKNOWN
    assert report.exit_code == 2


def test_a_red_outranks_an_unknown():
    """Precedence: a completed red is final, and nothing unevaluable can
    soften it into 'we could not tell'."""
    report = canary.build_report(
        engine_doc=None,
        engine_error="URLError fetching the index",
        engine_state=canary.INDEX_UNREADABLE,
        client_doc=None,
        client_error="HTTP 404",
        client_state=canary.INDEX_ABSENT,
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert report.verdict == canary.FAIL
    assert report.exit_code == 1


def test_a_report_with_no_gated_arm_is_never_green():
    """An empty report must not pass by omission."""
    assert canary.Report().verdict == canary.UNKNOWN
    assert canary.Report().exit_code == 2


def test_index_metadata_without_a_version_is_red():
    report = canary.build_report(
        engine_doc={"info": {}, "releases": {}},
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert report.get("engine-index").status == canary.FAIL


def test_a_MISSING_engine_project_is_red_and_an_UNREADABLE_index_is_unknown():
    """`404` is an answer, a timeout is not.

    Conflating them let one PyPI timeout report a BROKEN ARTIFACT and route a
    human to an irreversible upload (#12363 review B1).
    """
    absent = canary.build_report(
        engine_doc=None,
        engine_error="HTTP 404 from https://pypi.org/pypi/tortoise-graph/json",
        engine_state=canary.INDEX_ABSENT,
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert absent.get("engine-index").status == canary.FAIL
    assert absent.verdict == canary.FAIL

    unreadable = canary.build_report(
        engine_doc=None,
        engine_error="URLError fetching the index: timed out",
        engine_state=canary.INDEX_UNREADABLE,
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert unreadable.get("engine-index").status == canary.UNKNOWN
    assert unreadable.verdict == canary.UNKNOWN
    assert unreadable.exit_code == 2


def test_an_unreadable_CLIENT_index_is_unknown_not_absent():
    """The client arm must distinguish the same two cases; a transport failure on
    the second fetch used to red the daily job with the "a release is owed" text
    for a network blip (#12363 review B1)."""
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=None,
        client_error="HTTPError 429 from https://pypi.org/pypi/tortoise-client/json",
        client_state=canary.INDEX_UNREADABLE,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    arm = report.get("client-published")
    assert arm.status == canary.UNKNOWN
    assert "UNKNOWN, not absent" in arm.detail
    assert report.verdict == canary.UNKNOWN
    assert report.exit_code == 2


def test_a_client_document_with_no_version_is_red_not_ok():
    """A readable-but-empty document (a caching proxy answering `{}`, a truncated
    body) must not certify that `pip install tortoise-client` resolves. The engine
    arm treats the identical shape as a FAIL (#12363 review B4)."""
    for doc in ({}, {"info": {}}, {"info": {"version": ""}, "releases": {}}):
        report = canary.build_report(
            engine_doc=_engine_doc(),
            client_doc=doc,
            tree_version="0.2.0",
            init_arm=_ok_init_arm(),
            now=NOW,
        )
        assert report.get("client-published").status == canary.FAIL, doc
        assert report.verdict == canary.FAIL, doc


# --------------------------------------------------------------------------
# wheel mode — the PRE-publish gate — gates only the run, never the index
# --------------------------------------------------------------------------


def test_wheel_mode_gates_the_run_and_does_not_consult_the_index():
    report = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    assert report.get("engine-index").gated is False
    assert report.get("client-published").gated is False
    assert report.get("engine-init").gated is True
    assert report.verdict == canary.OK


def test_wheel_mode_is_red_when_the_built_wheel_fails_its_first_command():
    """This is the arm that would have redded #2204 before the irreversible
    upload instead of 97 days after it."""
    report = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "`tortoise init` exits 1"),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    assert report.verdict == canary.FAIL


# --------------------------------------------------------------------------
# install_and_run — an injected runner, so the failure modes are exercised
# --------------------------------------------------------------------------


class _Runner:
    """Records commands; returns scripted results per command tail."""

    def __init__(self, results: dict[str, subprocess.CompletedProcess], creates_db_for=None):
        self.results = results
        self.calls: list[list[str]] = []
        self._creates_db_for = creates_db_for

    def __call__(self, cmd, **kwargs):
        # Faithful to the contract the tool relies on: without these the real
        # subprocess would send the child's output to the PARENT's terminal and
        # return `None`, emptying the transcript that is the red's only evidence —
        # while a permissive mock kept every assertion green (#12363 review B5).
        assert kwargs.get("capture_output") is True, kwargs
        assert kwargs.get("text") is True, kwargs
        assert "cwd" in kwargs and "env" in kwargs, kwargs
        assert kwargs.get("timeout"), kwargs
        self.calls.append(list(cmd))
        tail = Path(cmd[0]).name
        result = self.results.get(tail) or self.results.get(" ".join(cmd[1:]), None)
        if result is None:
            result = subprocess.CompletedProcess(cmd, 0, "", "")
        if self._creates_db_for is not None and tail == "tortoise":
            home = Path(kwargs["env"]["HOME"])
            db = home / ".tortoise" / "tortoise.db"
            db.parent.mkdir(parents=True, exist_ok=True)
            db.write_text("")
        return result


def _completed(cmd, rc, out="", err=""):
    return subprocess.CompletedProcess(cmd, rc, out, err)


def test_install_and_run_reports_the_2204_signature_as_red(tmp_path):
    """The exact published-artifact failure, replayed: install OK, `tortoise
    init` exits 1 with the raw redislite FATAL on a fresh HOME."""
    runner = _Runner(
        {
            "tortoise": _completed(
                ["tortoise", "init"],
                1,
                "Tortoise init — auto-detecting FalkorDB…",
                "*** FATAL CONFIG FILE ERROR (Redis 8.10.2) ***\n"
                ">>> 'dir '<home>/.tortoise''\nNo such file or directory",
            )
        }
    )
    arm, transcript = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=runner,
    )
    assert arm.status == canary.FAIL
    assert "exits 1" in arm.detail
    # The transcript is the evidence: a bare exit code cannot distinguish this
    # signature from an unrelated failure.
    assert "FATAL CONFIG FILE ERROR" in transcript


def test_install_and_run_reports_red_when_init_creates_no_database(tmp_path):
    """Exit 0 is not the criterion on its own: the documented command must
    actually HAVE produced the database, or a silently-no-op init passes."""
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({}, creates_db_for=None),
    )
    assert arm.status == canary.FAIL
    assert "no database" in arm.detail


def test_install_and_run_is_green_when_init_creates_the_database(tmp_path):
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({}, creates_db_for="tortoise"),
    )
    assert arm.status == canary.OK


def test_install_and_run_strips_ambient_tortoise_env(tmp_path, monkeypatch):
    """The canary must measure the DEFAULT path a new user gets. An ambient
    TORTOISE_DB_URI/DB_PATH inherited from the runner would point the command
    somewhere else and turn a broken artifact green."""
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:x@localhost:6379/other")
    monkeypatch.setenv("TORTOISE_DB_PATH", "/tmp/elsewhere.db")
    monkeypatch.setenv("FALKORDB_HOST", "elsewhere")
    # These decide WHICH BYTES the child imports: a PYTHONPATH entry pointing at a
    # checkout makes the command execute the TREE instead of the installed artifact,
    # which would report PASS over the exact #2204-shaped wheel this gate exists to
    # catch (#12363 review B6).
    monkeypatch.setenv("PYTHONPATH", "/some/checkout")
    monkeypatch.setenv("VIRTUAL_ENV", "/some/venv")
    captured = {}

    class _Capture(_Runner):
        def __call__(self, cmd, **kwargs):
            captured["env"] = kwargs["env"]
            return super().__call__(cmd, **kwargs)

    canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Capture({}, creates_db_for="tortoise"),
    )
    assert not [k for k in captured["env"] if k.startswith(("TORTOISE_", "FALKORDB_"))]
    assert "PYTHONPATH" not in captured["env"]
    assert "VIRTUAL_ENV" not in captured["env"]
    assert captured["env"]["HOME"] == str(tmp_path / "home")


def test_a_failed_install_is_red_and_never_reaches_the_command(tmp_path):
    runner = _Runner({"pip": _completed(["pip", "install"], 1, "", "No matching distribution")})
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=runner,
    )
    assert arm.status == canary.FAIL
    assert "cannot even install" in arm.detail
    assert not any(Path(cmd[0]).name == "tortoise" for cmd in runner.calls)


def test_a_timeout_is_unknown_not_green(tmp_path):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=boom,
    )
    assert arm.status == canary.UNKNOWN


def test_wheel_mode_installs_the_local_file_not_the_index(tmp_path):
    """The pre-publish gate must install the exact bytes about to be uploaded;
    an index spec here would test the PREVIOUS release instead of this build."""
    wheel = tmp_path / "dist" / "tortoise_graph-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_bytes(b"")
    runner = _Runner({}, creates_db_for="tortoise")
    canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=runner,
        wheel=wheel,
    )
    pip_call = next(cmd for cmd in runner.calls if Path(cmd[0]).name == "pip")
    assert str(wheel.resolve()) in pip_call
    assert not any(arg.startswith("tortoise-graph==") for arg in pip_call)


# --------------------------------------------------------------------------
# parsers + rendering
# --------------------------------------------------------------------------


def test_upload_time_takes_the_oldest_file_not_the_newest():
    doc = {
        "releases": {
            "0.2.0": [
                {"upload_time_iso_8601": "2026-08-09T00:00:00Z"},
                {"upload_time_iso_8601": "2026-08-08T00:30:10.719404Z"},
            ]
        }
    }
    got = canary.upload_time(doc, "0.2.0")
    assert got == datetime(2026, 8, 8, 0, 30, 10, 719404, tzinfo=UTC)


def test_upload_time_is_none_when_no_file_carries_a_stamp():
    assert canary.upload_time({"releases": {"0.2.0": [{"filename": "x"}]}}, "0.2.0") is None


def test_source_version_reads_pyproject():
    assert (
        canary.source_version('[project]\nname = "tortoise-graph"\nversion = "0.2.0"\n') == "0.2.0"
    )
    assert canary.source_version("") == ""


def test_release_age_and_tree_version_are_reported_but_never_gated():
    """A version bump legitimately precedes its release, so gating on the tree
    version would red every PR between a bump and a tag."""
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc={"info": {"version": "0.2.0"}},
        tree_version="0.3.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert report.get("release-age").gated is False
    assert report.get("tree-version").gated is False
    assert "97d" in report.get("release-age").detail
    assert report.verdict == canary.OK


def test_summary_names_the_release_action_on_red():
    """A red that does not say what clears it sends the reader looking for a PR
    to fix, which is not what a broken published artifact needs."""
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=None,
        client_error="HTTP 404",
        client_state=canary.INDEX_ABSENT,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    summary = canary.render_summary(report)
    assert "Verdict: FAIL" in summary
    assert "needs a release" in summary
    assert "#12363" in summary


def test_wheel_mode_fail_names_a_build_defect_not_a_release():
    """In the publish pipeline the artifact under test is the wheel just built, so
    "cut a release to fix a release" is the opposite of the action (#12363
    review E4)."""
    report = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "`tortoise init` exits 1"),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    summary = canary.render_summary(report)
    assert "BUILT wheel fails" in summary
    assert "re-dispatch this publish" in summary
    assert "needs a release" not in summary


def test_an_EMPTY_wheel_argument_is_refused_not_downgraded_to_index_mode(monkeypatch):
    """`pwsh`/bash do not abort on a failing `$(...)` INSIDE an argument, so
    `--wheel ""` reached the tool and collapsed to index mode: the pre-publish gate
    silently tested the PREVIOUSLY PUBLISHED release and could exit 0 while a broken
    wheel was uploaded (#12363 review B2/E3)."""
    called = {}

    def _boom(*a, **k):
        called["fetched"] = True
        return None, "", canary.INDEX_UNREADABLE

    monkeypatch.setattr(canary, "fetch_index", _boom)
    assert canary.main(["--wheel", ""]) == 2
    assert not called, "--wheel '' fell through to the index instead of refusing"


def test_a_NONEXISTENT_wheel_path_is_refused(tmp_path):
    assert canary.main(["--wheel", str(tmp_path / "nope.whl")]) == 2


def test_summary_says_unknown_is_red_on_purpose():
    summary = canary.render_summary(canary.Report())
    assert "CANNOT RUN" in summary
    assert "never look like a pass" in summary


@pytest.mark.parametrize("rc,expected", [(0, canary.OK), (1, canary.FAIL), (2, canary.UNKNOWN)])
def test_matrix_exit_codes_match_the_documented_contract(rc, expected):
    """The workflow's `rc=$?` relay depends on this mapping staying fixed."""
    report = canary.Report()
    report.add(canary.Arm("engine-init", expected, "x"))
    assert report.exit_code == rc


# --------------------------------------------------------------------------
# the pin must actually RUN on the diff it guards (#12363 review E2)
# --------------------------------------------------------------------------


def test_a_tool_only_diff_selects_this_pin():
    """`tools/` is in `NON_PYTHON_PREFIXES`, so `_keep_changed` DROPS a tools-only
    path and `select()` takes the docs-only early return: measured before the
    `CORE_ALSO` entry, `--changed-files tools/published_artifact_canary.py` →
    `surfaces: []` and this file was NOT selected, so a PR that changed the tool
    alone (its 0/1/2 exit contract, the empty-`--wheel` refusal, the ambient-env
    strip) merged with every falsifiability pin unrun. This pins the entry that
    closes it, in the file it protects.
    """
    from tools import ci_selection

    manifest = ci_selection.load_manifest()
    selection = ci_selection.select(
        ["tools/published_artifact_canary.py"], "pull_request", manifest
    )
    assert "core" in (selection.get("surfaces") or []), selection.get("surfaces")
    files = selection.get("test_files")
    if files == "ALL":
        return
    assert "test_published_artifact_canary.py" in files, (
        "a diff to tools/published_artifact_canary.py no longer selects its own pin — "
        "the `CORE_ALSO` entry was dropped or the prefix behaviour changed"
    )
