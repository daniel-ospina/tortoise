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


def _client_doc(version: str = "0.2.0", yanked: bool = False):
    """A HEALTHY published client doc — a version AND an installable file for it.

    ⛔ The file list is load-bearing, not decoration (#12363 round 14): `pip install
    tortoise-client` is unpinned, so pip resolves from the release's files, and PEP 592
    skips a yanked file. Before this helper existed the fixtures here passed
    `{"info": {"version": "0.2.0"}}` with no `releases` entry — the precise
    advertised-but-uninstallable shape — and `client-published` certified it OK. A fixture
    that cannot be installed must not be the healthy default.
    """
    return {
        "info": {"version": version},
        "releases": {
            version: [
                {
                    "filename": f"tortoise_client-{version}-py3-none-any.whl",
                    "yanked": yanked,
                    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
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
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert report.verdict == canary.OK
    assert report.exit_code == 0


def test_a_broken_first_command_is_red_and_wins_over_anything_else():
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "`tortoise init` exits 1"),
        now=NOW,
    )
    assert report.verdict == canary.FAIL
    assert report.exit_code == 1


def test_absent_client_distribution_is_red_not_absent_ok():
    """README.md documents `pip install tortoise-client`; a 404 is a
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
        client_doc=_client_doc(),
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
        client_doc=_client_doc(),
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
        client_doc=_client_doc(),
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
        client_doc=_client_doc(),
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


def test_a_client_version_with_no_INSTALLABLE_file_is_red_not_ok():
    """`info.version` alone is not "resolves": the FILES are what pip resolves (#12363 round 14).

    `pip install tortoise-client` is UNPINNED, so it resolves from the latest release's file
    list, and PEP 592 makes pip skip yanked files for an unpinned requirement. A release that
    advertises a version but ships no usable file — a file deleted after publish, the release
    record created before its upload, every file yanked — fails for 100% of users.

    Measured BEFORE the fix: both shapes below returned `Verdict: PASS` / `exit_code 0`, filed
    nothing, and — because the close gate now reads `exit_code == '0'` — actively RESOLVED the
    release-owed ledger. The engine arm cannot be fooled the same way only because pip has to
    INSTALL it; this arm has no install and no probe, so the file list is its only evidence.
    """
    empty_release = {"info": {"version": "0.2.0"}, "releases": {"0.2.0": []}}
    all_yanked = {
        "info": {"version": "0.2.0"},
        "releases": {
            "0.2.0": [
                {
                    "filename": "tortoise_client-0.2.0-py3-none-any.whl",
                    "yanked": True,
                    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
                }
            ]
        },
    }
    for doc, why in ((empty_release, "no files"), (all_yanked, "only-yanked")):
        report = canary.build_report(
            engine_doc=_engine_doc(),
            client_doc=doc,
            tree_version="0.2.0",
            init_arm=_ok_init_arm(),
            now=NOW,
        )
        arm = report.get("client-published")
        assert arm.status == canary.FAIL, (why, arm.status, arm.detail)
        assert report.verdict == canary.FAIL, (why, report.verdict)
        assert report.exit_code == 1, (why, report.exit_code)
        assert "installable" in arm.detail, (why, arm.detail)

    # NON-VACUITY: one real file certifies the arm, so the pin cannot be satisfied by a
    # blanket FAIL. (Without this arm a `return FAIL` would pass every case above.)
    healthy = {
        "info": {"version": "0.2.0"},
        "releases": {
            "0.2.0": [
                {
                    "filename": "tortoise_client-0.2.0-py3-none-any.whl",
                    "yanked": False,
                    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
                }
            ]
        },
    }
    ok_report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=healthy,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert ok_report.get("client-published").status == canary.OK, ok_report.get(
        "client-published"
    ).detail


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
# cycle-2 findings: the details and the exception surface
# --------------------------------------------------------------------------


def test_a_truncated_index_body_is_UNREADABLE_not_a_bare_traceback(monkeypatch):
    """`IncompleteRead`/`BadStatusLine` are `http.client.HTTPException`, NOT
    `OSError`, and 3.12 does not wrap them in `URLError` — so an uncaught one
    escaped `fetch_index`, exited 1 with a traceback and produced NO report at all,
    which the workflow summary then presents as "a release is owed" (cycle-2
    review B-1)."""
    import http.client

    class _Truncated:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            raise http.client.IncompleteRead(b"partial", 100)

    monkeypatch.setattr(
        canary, "_INDEX_OPENER", type("_O", (), {"open": lambda self, *a, **k: _Truncated()})()
    )
    doc, reason, state = canary.fetch_index("tortoise-graph", 5)
    assert doc is None
    assert state == canary.INDEX_UNREADABLE, state
    assert "IncompleteRead" in reason


def test_a_forgotten_index_state_falls_on_the_SAFE_side():
    """A caller that omits `*_state` while passing `*_doc=None` must land on
    UNKNOWN/exit 2. The default `INDEX_OK` is the trap: flip it to `INDEX_ABSENT`
    (or invert the branch) and every such caller reports a broken artifact and
    routes a human to an irreversible upload — with nothing going red."""
    report = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert report.get("engine-index").status == canary.UNKNOWN
    assert report.get("client-published").status == canary.UNKNOWN
    assert report.verdict == canary.UNKNOWN
    assert report.exit_code == 2


def test_wheel_mode_fail_details_do_not_claim_the_index_or_a_user(tmp_path):
    """The arm DETAILS must branch on the mode too, not just the summary
    paragraph: a blocked publish used to read "… from the index failed — users
    cannot even install it" directly above "Nothing has been uploaded" (cycle-2
    review B-4/E-4)."""
    wheel = tmp_path / "dist" / "tortoise_graph-0.2.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    wheel.write_bytes(b"")

    init_fail, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({"tortoise": _completed(["tortoise", "init"], 1, "", "boom")}),
        wheel=wheel,
    )
    assert init_fail.status == canary.FAIL
    assert "from the index" not in init_fail.detail
    assert "BUILT wheel" in init_fail.detail
    assert wheel.name in init_fail.detail, init_fail.detail
    assert "{wheel.name}" not in init_fail.detail

    install_fail, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner(
            {
                "pip": _completed(
                    ["pip", "install"], 1, "", f"ERROR: {wheel.name} is not a valid wheel"
                )
            }
        ),
        wheel=wheel,
    )
    assert install_fail.status == canary.FAIL
    assert "from the index" not in install_fail.detail
    assert "users cannot even install" not in install_fail.detail
    # …and the WHEEL NAME must actually render: an escaped `{{wheel.name}}` emits a
    # literal placeholder, and asserting only the ABSENCE of the wrong phrases let it
    # through the suite (#12363 review round 3, caught by the verifier).
    assert wheel.name in install_fail.detail, install_fail.detail
    assert "{wheel.name}" not in install_fail.detail


def test_the_install_cannot_resolve_from_a_configured_MIRROR(tmp_path, monkeypatch):
    """`PIP_` is stripped by PREFIX, not by a member list, AND the install names
    `--index-url https://pypi.org/simple` explicitly — env vars were only the
    highest-precedence half of pip's config surface, and `/etc/pip.conf` is loaded
    unconditionally (a CLI flag outranks it). Without both, a box with a mirror grades
    the MIRROR's copy instead of what PyPI serves (#12363 cycle-2 B-3, round 3)."""
    for name in (
        "PIP_INDEX_URL",
        "PIP_EXTRA_INDEX_URL",
        "PIP_NO_INDEX",
        # NOT in this stripped list: `PIP_CONFIG_FILE` is REPLACED with `os.devnull`
        # rather than dropped (dropping it re-opens the GLOBAL-config class), and the
        # exact-survivor assertion below is what proves the parent's value did not
        # survive (#12363 review round 6).
        "PIP_REQUIRE_VIRTUALENV",
        "PIP_CONSTRAINT",
        # EVERY proxy spelling: pip has no `trust_env=False`, so proxies are consumed by
        # its vendored requests OUTSIDE pip's option system and survive `--isolated`.
        # MEASURED (#12363 round 5): with only `all_proxy` set, `pip download --isolated
        # --index-url https://pypi.org/simple six` still dialled the fake proxy and then
        # printed pip's ABSENCE vocabulary — the string the tool has to re-check. Two of
        # three spellings stripped left that door open.
        "https_proxy",
        "all_proxy",
        "ALL_PROXY",
        "HTTP_PROXY",
        # ⛔ Spellings that are NOT in the strip list: pip's vendored requests LOWERCASES
        # every env name, so a case-SENSITIVE strip walks past these while the child
        # honours them (#12363 round 8).
        "Http_Proxy",
        "All_Proxy",
        "HtTpS_pRoXy",
    ):
        monkeypatch.setenv(name, "https://mirror.internal/simple")
    monkeypatch.setenv("pythonPath", "/some/checkout")  # case-bLIND strip: this IS PYTHONPATH
    captured = {}

    class _Capture(_Runner):
        def __call__(self, cmd, **kwargs):
            captured["env"] = kwargs["env"]
            if Path(cmd[0]).name == "pip":
                captured["cmd"] = list(cmd)
            return super().__call__(cmd, **kwargs)

    canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Capture({}, creates_db_for="tortoise"),
    )
    assert not [
        k
        for k in captured["env"]
        if k.startswith("PIP_") and k not in ("PIP_DISABLE_PIP_VERSION_CHECK", "PIP_CONFIG_FILE")
    ]
    assert "pythonPath" not in captured["env"], (
        "the strip is case-BLIND: `pythonPath` IS PYTHONPATH to the child's import machinery"
    )
    assert not [
        k
        for k in captured["env"]
        if k.lower() in ("http_proxy", "https_proxy", "all_proxy")
        and k not in ("https_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY")
    ], "a mixed-case proxy spelling survived — pip lowercases names, so it IS obeyed"
    for spelling in ("https_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        assert spelling not in captured["env"], (
            f"{spelling} survived into the child — pip obeys every proxy spelling it "
            f"finds and --isolated suppresses none of them (#12363 round 5)"
        )
    assert "--isolated" in captured["cmd"], (
        "`--index-url` alone does not close the config-file class: a config-file "
        "`extra-index-url` MERGES with it, so a runner with a global mirror can resolve "
        "the release from the mirror and report PASS over bytes PyPI never served"
    )
    assert "--index-url" in captured["cmd"], captured["cmd"]
    assert canary.PYPI_SIMPLE in captured["cmd"], captured["cmd"]
    # `--isolated` narrows the config-file class but does not close it: the GLOBAL
    # `/etc/pip.conf` still loads, and its `extra-index-url` MERGES with the CLI
    # `--index-url`. `PIP_CONFIG_FILE=os.devnull` is the closure (pip's
    # `_load_config_files` early-returns for every kind), so the parent's value must be
    # REPLACED — not merely dropped — and no other `PIP_` may survive except the one this
    # tool sets itself. Asserted as an exact set: a survivor list that grows silently is
    # how a later `PIP_*` reintroduces the mirror (#12363 review round 6).
    pip_survivors = {key for key in captured["env"] if key.startswith("PIP_")}
    assert pip_survivors == {"PIP_DISABLE_PIP_VERSION_CHECK", "PIP_CONFIG_FILE"}, pip_survivors
    assert captured["env"]["PIP_CONFIG_FILE"] == canary.os.devnull


def test_an_install_failure_is_only_RED_when_the_INDEX_confirms_the_absence(tmp_path):
    """pip's absence vocabulary is not proof of absence — MEASURED: against an
    unreachable index pip prints the same "Could not find a version that satisfies …
    (from versions: none)" it prints for a genuinely missing release. So the FAIL needs
    an INDEPENDENT re-read of the index, and only its answer routes the arm:
    ABSENT and OK are both FAIL (the project is gone / the project is served but the
    version will not install), and UNREADABLE alone is UNKNOWN — a non-zero exit that
    never sends a human to cut an irreversible release (#12363 rounds 3 → 4 → 11)."""
    marks = (
        "ERROR: Could not find a version that satisfies the requirement "
        "tortoise-graph==0.2.0 (from versions: none)\n"
        "ERROR: No matching distribution found for tortoise-graph==0.2.0"
    )

    def _run(probe_state):
        return canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=tmp_path,
            timeout=10,
            runner=_Runner({"pip": _completed(["pip", "install"], 1, "", marks)}),
            absent_probe=lambda package: probe_state,
        )[0]

    assert _run(canary.INDEX_ABSENT).status == canary.FAIL
    # the real unreachable-index shape: pip's wording, but the index answers nothing
    assert _run(canary.INDEX_UNREADABLE).status == canary.UNKNOWN
    # ⛔ INDEX_OK IS A FAIL, AND THIS ARM IS WHY THE PIN EXISTS (#12363 round 11). It used
    # to be UNKNOWN — and because `_main` only reaches `install_and_run` after a SUCCESSFUL
    # fetch of this same URL, `ok` is what the REAL probe answers, so the FAIL arm was
    # unreachable in production: an advertised-but-uninstallable release (no files for
    # `info.version`, all files yanked, a wheel pip rejects) was reported as exit 2 and
    # read by both consumers as "detector outage — do NOT cut a release", the exact
    # opposite of the finding. Tested THROUGH the injected probe, so the state is reachable
    # in the pin even though the un-injected path was measured dead.
    ok_arm = _run(canary.INDEX_OK)
    assert ok_arm.status == canary.FAIL, (
        f"a reachable index that serves the project while pip refuses the version is a "
        f"RELEASE finding, not a detector outage (got {ok_arm.status}: {ok_arm.detail})"
    )
    assert "users cannot even install" in ok_arm.detail, ok_arm.detail
    assert "detector outage" not in ok_arm.detail, ok_arm.detail
    # and a failure pip does not attribute to a missing distribution is never a FAIL
    arm = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner(
            {
                "pip": _completed(
                    ["pip", "install"],
                    1,
                    "",
                    "ERROR: Could not install packages due to an OSError: "
                    "[Errno -3] Temporary failure in name resolution",
                )
            }
        ),
    )[0]
    assert arm.status == canary.UNKNOWN, arm.detail


def test_the_wheel_mode_install_failure_is_never_attributed_to_the_index(tmp_path):
    """Wheel mode has no index in play: only pip naming the BUILT wheel is a FAIL.
    A dependency fetch that failed is the network's problem — telling the reader "the
    artifact about to be uploaded is not installable" would block a good release
    (#12363 round 4)."""
    wheel = tmp_path / "dist" / "tortoise_graph-0.3.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)

    def _run(stderr):
        return canary.install_and_run(
            package="tortoise-graph",
            version="",
            workdir=tmp_path,
            timeout=10,
            runner=_Runner({"pip": _completed(["pip", "install"], 1, "", stderr)}),
            wheel=wheel,
        )[0]

    assert _run(f"ERROR: {wheel.name} is not a valid wheel").status == canary.FAIL
    assert (
        _run(
            "ERROR: Could not find a version that satisfies the requirement "
            "falkordblite==0.9 (from versions: none)"
        ).status
        == canary.UNKNOWN
    )


def test_both_workflows_keep_the_transcript_and_budget_the_fetches():
    """The evidence pairing and the budget arithmetic are pinned HERE because a comment
    is what drifted — twice (#12363 cycle-2 E-1, round 3).

    The arithmetic is PARSED out of each workflow and compared against that job's OWN
    `timeout-minutes`. An earlier version of this test hard-coded `3 * 300` and never read
    the canary job's budget at all, so four mutations that each restored the "the job
    timeout kills the run before the report is written" loss passed green (#12363 round 3).
    """
    import re

    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"
    canary_wf = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())
    publish_wf = yaml.safe_load((workflows / "publish-pypi.yml").read_text())

    def run_text(steps) -> str:
        return " ".join(
            str(step.get("run", ""))
            for step in steps
            if "published_artifact_canary" in str(step.get("run", ""))
        )

    def num(text: str, flag: str, default: float) -> float:
        found = re.search(rf"{flag}\s+([\d.]+)", text)
        return float(found.group(1)) if found else default

    def upload_without_condition(steps, name: str) -> None:
        # `if: always()` — a job CANCELLED at its timeout boundary runs no `failure()`
        # step, and that is exactly when the incremental transcript is the only evidence.
        uploads = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("actions/upload-artifact")
        ]
        assert uploads, f"{name}: nothing uploads the transcript the tool tells you to read"
        assert any(step.get("if") == "always()" for step in uploads), (
            f"{name}: the transcript upload must be `if: always()`"
        )

    canary_job = canary_wf["jobs"]["canary"]
    canary_run = run_text(canary_job["steps"])
    assert "--transcript-dir" in canary_run
    assert "--fetch-timeout" in canary_run, (
        "`--timeout` alone also bounds the two index fetches: the tool then spends its "
        "command budget five times and the report is never written"
    )
    canary_budget = canary_job.get("timeout-minutes")
    assert canary_budget, "the canary job declares no timeout-minutes"
    # THREE fetches: engine, client, and the absence-confirmation probe after a failed
    # install — the workflow's own arithmetic says so (#12363 round 7).
    nominal = 3 * num(canary_run, "--fetch-timeout", 60.0) + 3 * num(canary_run, "--timeout", 600.0)
    assert nominal <= canary_budget * 60, (
        f"nominal worst case {nominal}s exceeds the canary job's {canary_budget * 60}s — "
        f"the job timeout would kill the run before the report and the transcript"
    )
    upload_without_condition(canary_job["steps"], "canary")

    build_job = publish_wf["jobs"]["build"]
    publish_run = run_text(build_job["steps"])
    assert "--transcript-dir" in publish_run
    build_budget = build_job.get("timeout-minutes")
    assert build_budget, "the build job declares no timeout-minutes for the smoke to fit inside"
    assert 3 * num(publish_run, "--timeout", 600.0) <= build_budget * 60, (
        "3 x the per-command budget exceeds the build job's own timeout"
    )
    upload_without_condition(build_job["steps"], "build")


def test_the_transcript_DIRECTORY_is_paired_at_every_site_that_names_it():
    """The transcript's PATH — not just the artifact's name — is a paired constant (#12363 round 11).

    Five places must agree, and four of them are silent when they stop agreeing:

      1. the tool invocation's `--transcript-dir` (where the log is WRITTEN),
      2. that job's `upload-artifact` `path:` (what is COLLECTED — `if-no-files-found: warn`,
         so a rename here keeps the job GREEN while uploading nothing),
      3. the report job's `download-artifact` `path:` (`continue-on-error: true`, same),
      4. `published-artifact-alert.sh`'s `local log=` (what the issue body TAILS), and
      5. the tool's own log BASENAME (`Path(args.transcript_dir) / …`).

    Measured, not argued: renaming only site 1 to `$RUNNER_TEMP/canary-logs` left every
    existing pin green (`test_both_workflows_keep_the_transcript_and_budget_the_fetches`
    asserts only that the substring `--transcript-dir` is PRESENT), the upload matched no
    file, and every filed issue body silently degraded to "(no transcript reached this
    job …)" — the exact evidence loss round 6 fixed by making the log incremental, one
    level out. This is the same class the authors already recognised for the artifact
    NAME and left unpinned for the PATH.

    ⛔ SOLE OWNER OF THIS CONTRACT, on purpose (#12363 round 13). A peer lane's round-10 pin
    (`test_the_transcript_RELAY_is_one_agreement_across_four_sites`) asserted the same
    agreement, and the two DISAGREED on the location axis: it captured ONE component from
    `--transcript-dir` and compared it to the upload path's last segment, so a fully
    consistent rename to `$RUNNER_TEMP/logs/canary` at all five sites passed HERE and failed
    THERE (measured). Two artifacts asserting one fact, disagreeing, is the failure mode
    where neither is authoritative — so that pin was consolidated into this one rather than
    left beside it. Do not reintroduce a second copy. If a site is added, register it at one
    of the FIVE assertion points in the body below — the canary `paired(...)` call, the
    `publish-pypi.yml`/`build` `paired(...)` call, the `downloads` list, the `log_ref`
    regex, and the basename comparison against the tool — and update the count in the
    failure messages. (Five today; there is no single tuple to extend, and an earlier
    version of this docstring said there was — #12363 final review, P3.)
    """
    import re

    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"

    def normalised(value: str) -> str:
        """Runner-temp spellings collapsed to ONE token, quotes and trailing slash stripped.

        The WHOLE path is compared, not its last component (#12363 round 12). An earlier
        cut of this pin compared only `basename()` and was blind to the LOCATION axis:
        `--transcript-dir "$RUNNER_TEMP/logs/canary"` against an upload path of
        `${{ runner.temp }}/canary/` compares `canary == canary` and passes, while the
        upload matches no file — measured, and the docstring above claimed the PATH was
        pinned, which was false for three of the five sites.
        """
        text = str(value).strip().strip('"')
        for spelling in ("${{ runner.temp }}", "${RUNNER_TEMP:-/tmp}", "$RUNNER_TEMP"):
            text = text.replace(spelling, "TEMP")
        # Collapse `a//b` and a trailing slash so a cosmetic difference is not a red.
        parts = [part for part in text.split("/") if part not in ("", ".")]
        return "/".join(parts)

    def paired(wf_file: str, job_name: str, artifact_name: str) -> str:
        job = yaml.safe_load((workflows / wf_file).read_text())["jobs"][job_name]
        steps = job["steps"]
        runs = [str(step.get("run") or "") for step in steps]
        invocations = [text for text in runs if "published_artifact_canary.py" in text]
        assert len(invocations) == 1, (wf_file, job_name, "expected exactly one canary call")
        found = re.search(r'--transcript-dir\s+"?([^\s"]+)"?', invocations[0])
        assert found, (wf_file, job_name, "the canary call passes no --transcript-dir")
        written = normalised(found.group(1))

        uploads = [
            s
            for s in steps
            if str(s.get("uses", "")).startswith("actions/upload-artifact")
            and s.get("with", {}).get("name") == artifact_name
        ]
        assert len(uploads) == 1, (
            wf_file,
            job_name,
            f"expected exactly one transcript upload named {artifact_name!r} (found "
            f"{[s.get('with', {}).get('name') for s in steps if s.get('uses', '').startswith('actions/upload-artifact')]})",
        )
        collected = normalised(uploads[0]["with"]["path"])
        assert written == collected, (
            f"{wf_file}/{job_name}: the tool writes the transcript to `{written}` but the "
            f"upload collects `{collected}` — `if-no-files-found: warn` keeps this job GREEN "
            f"while it uploads NOTHING, so every issue body silently loses its TAIL"
        )
        return written

    canary_dir = paired("release-artifact-canary.yml", "canary", "published-artifact-canary-log")
    paired("publish-pypi.yml", "build", "publish-canary-log")

    # 3 + 4: the report job DOWNLOADS into the directory the alert script then reads.
    report = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())["jobs"][
        "report"
    ]
    downloads = [
        step
        for step in report["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact")
    ]
    assert len(downloads) == 1, "the report job must fetch the canary transcript exactly once"
    assert downloads[0]["with"]["name"] == "published-artifact-canary-log", (
        "the report job asks for an artifact name no upload declares"
    )
    assert normalised(downloads[0]["with"]["path"]) == canary_dir, (
        "the report job unpacks the transcript into a different directory than the canary's "
        "upload path, so `download-artifact` succeeds and the alert still finds nothing"
    )

    alert = (REPO_ROOT / ".github" / "scripts" / "published-artifact-alert.sh").read_text()
    log_ref = re.search(r'^\s*local log="([^"]+)"', alert, re.MULTILINE)
    assert log_ref, "the alert script no longer names a transcript path to TAIL"
    alert_path = normalised(log_ref.group(1))
    assert "/" in alert_path, ("the alert's log path carries no directory", log_ref.group(1))
    alert_dir, _, alert_base = alert_path.rpartition("/")
    assert alert_dir == canary_dir, (
        f"the alert reads `{alert_dir}` but the canary uploads `{canary_dir}` — the body's "
        f'TAIL would be unreachable and every issue would degrade to "(no transcript …)"'
    )

    # 5: the BASENAME, in the tool itself.
    tool = (REPO_ROOT / "tools" / "published_artifact_canary.py").read_text()
    assert f'"{alert_base}"' in tool, (
        f"the alert TAILs `{alert_base}` but the tool writes a different filename"
    )


def test_the_transcript_STEPS_are_ordered_so_the_evidence_can_be_collected():
    """Order, not just content: every step in the evidence chain must FOLLOW the thing it reads (#12363 round 12).

    Path and name equality is not enough, and each of these three inversions is silent —
    measured at `b3b1ac4763`: all 53 pins in this file plus the alert harness stayed GREEN
    with the step moved, because nothing compared step POSITION. What each inversion costs
    at runtime:

      * the canary's / the build job's `upload-artifact` placed ABOVE the tool invocation
        collects a log that does not exist yet (`if-no-files-found: warn`), so the evidence
        is absent on EVERY run, including the reds that need it most;
      * the report job's `download-artifact` placed BELOW the alert steps leaves
        `published-artifact-alert.sh` reading a file that is not there, and every filed
        issue body degrades to "(no transcript reached this job …)" — `continue-on-error:
        true` keeps the job green while the diagnosis is gone from the durable record.

    This is the same class round 3 closed for the exit status, in the two remaining places.
    """
    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"

    def index_of(steps, predicate, label: str) -> int:
        found = [i for i, step in enumerate(steps) if predicate(step)]
        assert len(found) == 1, (label, f"expected exactly one match, got {len(found)}")
        return found[0]

    for wf_file, job_name in (
        ("release-artifact-canary.yml", "canary"),
        ("publish-pypi.yml", "build"),
    ):
        steps = yaml.safe_load((workflows / wf_file).read_text())["jobs"][job_name]["steps"]
        invoked = index_of(
            steps, lambda s: "published_artifact_canary.py" in str(s.get("run") or ""), wf_file
        )
        uploaded = index_of(
            steps,
            lambda s: (
                str(s.get("uses", "")).startswith("actions/upload-artifact")
                and s.get("with", {}).get("name")
                in ("publish-canary-log", "published-artifact-canary-log")
            ),
            wf_file,
        )
        assert invoked < uploaded, (
            f"{wf_file}/{job_name}: the transcript is uploaded at step {uploaded} but only "
            f"written at step {invoked} — `if-no-files-found: warn` keeps this GREEN while it "
            f"collects nothing, so the evidence is missing on every run including a red"
        )

    report_steps = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())["jobs"][
        "report"
    ]["steps"]
    downloaded = index_of(
        report_steps,
        lambda s: str(s.get("uses", "")).startswith("actions/download-artifact"),
        "report",
    )
    alert_steps = [
        i
        for i, step in enumerate(report_steps)
        if "published-artifact-alert.sh" in str(step.get("run") or "")
    ]
    assert alert_steps, "the report job no longer runs the alert script"
    assert downloaded < min(alert_steps), (
        f"the transcript is downloaded at step {downloaded} but the alert runs at step "
        f"{min(alert_steps)} — the alert would TAIL a file that is not there yet and every "
        f'filed issue body would say "(no transcript reached this job …)", silently '
        f"(`continue-on-error: true`)"
    )


def test_the_gate_steps_cannot_be_DEFUSED_into_a_green_job():
    """A gate step that cannot fail its job is a no-op, and here it also RESOLVES the ledger (#12363 round 13).

    This repo already names the class: `tests/test_runner_tool_contract.py`'s
    `_STEP_DEFUSERS = ("if", "continue-on-error")` — the two step keys that turn a failing
    gate into a passing job. It was applied to `ensure gh` only, so the two steps whose
    verdict is the whole point were never checked against it.

    Measured at `d3d5782152`, each mutation independently and with all 55 pins GREEN:

      * `continue-on-error: true` on publish-pypi's `Documented first command on the built
        wheel (#12363)`: the step fails, `build` reports SUCCESS, and `publish`
        (`needs: build`) runs `pypa/gh-action-pypi-publish` on the uninstallable wheel —
        irreversibly. That is the #2204 regression this gate exists to stop, shipped by a
        one-line tightening that reads like caution.
      * `continue-on-error: true` (or `if: false`) on the canary job's `id: canary` step:
        the job reads SUCCESS with `steps.canary.outputs.exit_code` `1`, so the alarm and
        `File or refresh the alert issue` (both `needs.canary.result != 'success'`) are
        SKIPPED — and, since #12363 round 12 removed the close gate's `result == 'success'`
        disjunct, `Close the alert issue on a green run` (`exit_code == '0'`) is skipped too
        (measured at `3b32ddb458`; before that commit the close DID run, `af_resolve` called
        with "Green: the published artifact now passes its documented first command",
        falsifying the release-owed issue). Either way the run is green and the ledger is
        wrong: a daily silent run that files nothing, or one that closes on a broken
        artifact. A gate step that cannot fail its job is not a gate.

    ⛔ Asserting the mechanism (`exit "$rc"` present) is NOT this pin: that assertion stays
    true under `continue-on-error`, which is why the property is asserted separately. This is
    the pin that makes the two mechanisms agree instead of each looking correct alone.
    """
    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"

    def undefused(step: dict, label: str) -> None:
        for key in ("if", "continue-on-error"):
            assert key not in step, (
                f"{label} carries `{key}: {step[key]!r}` — a step that cannot fail its job is "
                f"not a gate: the run reads green and the verdict (or the ledger close) goes "
                f"with it"
            )

    build_steps = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"]["build"][
        "steps"
    ]
    preupload = [
        step for step in build_steps if "published_artifact_canary.py" in str(step.get("run") or "")
    ]
    assert len(preupload) == 1, "the pre-upload gate step is missing"
    undefused(preupload[0], "publish-pypi build / the pre-upload gate")

    canary_steps = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())["jobs"][
        "canary"
    ]["steps"]
    canary_step = [step for step in canary_steps if step.get("id") == "canary"]
    assert len(canary_step) == 1, "the `id: canary` step is missing"
    undefused(canary_step[0], "release-artifact-canary / the `id: canary` step")


def test_the_alert_job_keeps_the_write_scope_the_alert_RUNS_under():
    """JOB-level `permissions` REPLACES the workflow's for that job, so the job block is the one that counts (#12363 round 13).

    The existing pin reads `workflow["permissions"]["issues"]`, i.e. the WORKFLOW-level
    block. GitHub's syntax reference: `jobs.<job_id>.permissions` "modif[ies] the default
    permissions granted to the GITHUB_TOKEN" for that job — and the alert legs run inside
    `report`, which declares its own block. Measured: setting
    `report.permissions.issues: read` (a plausible minimum-privilege tightening, and exactly
    the drift the neighbouring pin's message claims to prevent) leaves every pin green while
    `gh issue create`/`close` 403 in `auto-file-issue.sh`: the durable issue is never filed
    and both ledgers are never resolved, on a job that reads green.

    So BOTH blocks are asserted: the job block because it is the one in force, the workflow
    block because removing it silently re-opens the same hole the moment the job block is
    dropped.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release-artifact-canary.yml").read_text()
    )
    assert workflow["permissions"].get("issues") == "write", (
        "the workflow-level grant must keep issues: write (the job block is additive to it in "
        "intent; dropping this re-opens the hole if the job block is ever removed)"
    )
    report = workflow["jobs"]["report"]
    assert report.get("permissions", {}).get("issues") == "write", (
        "the `report` job declares its own permissions block, and a JOB-level block REPLACES "
        "the workflow's for that job — the alert legs run under THIS one, so `issues: read` "
        "here makes `gh issue create` 403 while the run reads green"
    )
    # ⛔ The `canary` job is deliberately NOT asserted (#12363 round 13). An earlier cut of
    # this pin wrote `workflow["jobs"]["canary"].get("permissions", {}).get("issues",
    # "write") == "write"`, which is VACUOUS: the canary declares no `permissions` block at
    # all, so the `.get` default supplied the answer and the assertion could not fail — while
    # adding a legitimate `permissions: {contents: read}` to that job (which, per the rule
    # above, sets every unlisted scope to none) stayed green too. It also modelled the
    # mechanism backwards: no step in `canary` files, comments, or closes anything (it has no
    # `gh`; the alert legs live in `report`, which IS asserted). A pin that can only fire on a
    # harmless change and that names a dependency which does not exist is the "a default
    # makes the pin vacuous" defect this suite refuses elsewhere.


def test_the_transcript_is_written_incrementally_not_only_at_the_end(tmp_path):
    """A job cancelled at its timeout boundary never reaches the final write, so the
    transcript must land as each command finishes (#12363 round 3)."""

    class _Dies(_Runner):
        def __call__(self, cmd, **kwargs):
            result = super().__call__(cmd, **kwargs)
            if Path(cmd[0]).name == "tortoise":
                raise KeyboardInterrupt("cancelled mid-run")
            return result

    log = tmp_path / "logs" / "canary.log"
    with pytest.raises(KeyboardInterrupt):
        canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=tmp_path,
            timeout=10,
            runner=_Dies({}, creates_db_for=None),
            transcript_path=log,
        )
    assert log.is_file(), "no transcript survived the cancellation"
    text = log.read_text()
    assert "pip install" in text, text
    assert "[exit 0]" in text, text


# --------------------------------------------------------------------------
# install_and_run — an injected runner, so the failure modes are exercised
# --------------------------------------------------------------------------


class _Runner:
    """Records commands; returns scripted results per command tail."""

    def __init__(
        self,
        results: dict[str, subprocess.CompletedProcess],
        creates_db_for=None,
        creates_console: bool = True,
    ):
        self.results = results
        self.calls: list[list[str]] = []
        self._creates_db_for = creates_db_for
        self._creates_console = creates_console

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
        if tail == "pip" and result.returncode == 0 and self._creates_console:
            # A successful install is what puts a console script in the venv. The tool
            # now READS that file: an artifact that installs but ships no entry point is
            # a FAIL, not a detector outage. Without this the fake installs would look
            # like entry-point-less wheels (#12363 round 5).
            script = Path(cmd[0]).parent / "tortoise"
            script.parent.mkdir(parents=True, exist_ok=True)
            script.write_text("#!/bin/sh\n")
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
    runner = _Runner(
        {
            "pip": _completed(
                ["pip", "install"],
                1,
                "",
                "ERROR: No matching distribution found for tortoise-graph==0.2.0",
            )
        }
    )
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=runner,
        absent_probe=lambda package: canary.INDEX_ABSENT,
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
        client_doc=_client_doc(),
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


def test_an_EMPTY_wheel_argument_is_refused_not_downgraded_to_index_mode(monkeypatch, capsys):
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
    # ⛔ The MESSAGE is what makes this discriminating: `Path("")` is `.`, so the
    # `is_file()` check alone also exits 2 — the named regression ("falls back to index
    # mode") cannot be observed from the exit code, and the test passed with the guard
    # deleted (#12363 round 7).
    stderr = capsys.readouterr().err
    assert "empty path" in stderr and "refusing" in stderr, stderr
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


# --------------------------------------------------------------------------
# fetch_index — the ONLY thing that turns an index read into a verdict
# --------------------------------------------------------------------------
# These were unpinned: replacing the 404/410 → INDEX_ABSENT mapping with either constant
# left the suite green, because every other test injects `engine_state=` and never reaches
# `fetch_index`. The two directions cost very different money — 404 → UNREADABLE means a
# genuinely absent project never names the release action, while 500 → ABSENT tells a human
# to cut an IRREVERSIBLE upload on a transient server error (#12363 round 5).


class _Response:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fetch_with(monkeypatch, *, payload=None, raises=None):

    def fake_open(request, timeout=None):
        if raises is not None:
            raise raises
        return _Response(payload)

    monkeypatch.setattr(
        canary,
        "_INDEX_OPENER",
        type("_O", (), {"open": lambda self, *a, **k: fake_open(*a, **k)})(),
    )
    return canary.fetch_index("tortoise-graph", 5)


def test_a_real_404_is_ABSENT_so_the_release_action_is_named(monkeypatch):
    import urllib.error

    _doc, reason, state = _fetch_with(
        monkeypatch, raises=urllib.error.HTTPError("u", 404, "Not Found", {}, None)
    )
    assert state == canary.INDEX_ABSENT, state
    assert "404" in reason


def test_a_server_error_is_UNREADABLE_never_ABSENT(monkeypatch):
    """A 500/429 is an index outage. Mapping it to ABSENT tells a human to cut a release
    for a transient error — an irreversible action on a false premise."""
    import urllib.error

    for code in (500, 429, 503):
        _doc, _reason, state = _fetch_with(
            monkeypatch, raises=urllib.error.HTTPError("u", code, "boom", {}, None)
        )
        assert state == canary.INDEX_UNREADABLE, (code, state)


def test_a_transport_failure_or_a_bad_body_is_UNREADABLE(monkeypatch):
    import urllib.error

    _doc, _reason, state = _fetch_with(monkeypatch, raises=urllib.error.URLError("name resolution"))
    assert state == canary.INDEX_UNREADABLE
    _doc, _reason, state = _fetch_with(monkeypatch, raises=TimeoutError("slow"))
    assert state == canary.INDEX_UNREADABLE
    _doc, _reason, state = _fetch_with(monkeypatch, payload=b"<html>not json</html>")
    assert state == canary.INDEX_UNREADABLE
    _doc, _reason, state = _fetch_with(monkeypatch, payload=b"[1, 2, 3]")
    assert state == canary.INDEX_UNREADABLE, "a non-object body is not an index document"


# --------------------------------------------------------------------------
# main() — the CLI seam both workflows depend on
# --------------------------------------------------------------------------
# `--transcript-dir` is what makes the `if: always()` upload non-empty and `--summary-file`
# is what puts the verdict on the run's page. Both were wiring-only and untested: hardcoding
# `log_path = None` left the whole suite green (#12363 round 5).


def _stub_main(monkeypatch, arm):
    monkeypatch.setattr(
        canary,
        "fetch_index",
        lambda package, timeout: (
            {"releases": {"0.2.0": []}, "info": {"version": "0.2.0"}},
            "",
            canary.INDEX_OK,
        ),
    )

    def fake_install(**kwargs):
        # Write at the path MAIN passes in: the wiring under test is
        # `--transcript-dir` → `transcript_path`, not the tool's own flush.
        path = kwargs.get("transcript_path")
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("$ fake transcript\n[exit 1]\n")
        return arm, "$ fake transcript\n[exit 1]"

    monkeypatch.setattr(canary, "install_and_run", fake_install)


def test_the_cli_writes_the_transcript_the_workflows_upload(tmp_path, monkeypatch, capsys):
    _stub_main(monkeypatch, canary.Arm("engine-init", canary.FAIL, "tortoise init exits 1"))
    logs = tmp_path / "canary"
    rc = canary.main(["--transcript-dir", str(logs)])
    written = logs / "published-artifact-canary.log"
    assert written.is_file(), "no transcript at the path the workflow uploads"
    assert "fake transcript" in written.read_text()
    assert rc == 1
    assert "FAIL" in capsys.readouterr().out


def test_the_cli_writes_the_summary_file_and_the_json_shape(tmp_path, monkeypatch, capsys):
    """`--summary-file` is the only thing that puts the verdict on the run page; `--json`
    is the machine-readable shape a future alert step would read."""
    import json

    _stub_main(monkeypatch, canary.Arm("engine-init", canary.FAIL, "tortoise init exits 1"))
    summary = tmp_path / "summary.md"
    canary.main(["--summary-file", str(summary)])
    text = summary.read_text()
    assert "Verdict: FAIL" in text, text
    assert "engine-init" in text

    capsys.readouterr()
    canary.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == canary.FAIL
    assert payload["exit_code"] == 1
    assert {"name", "status", "gated", "detail"} <= set(payload["arms"][0])


def test_a_wheel_that_installs_without_the_console_script_is_a_FAIL_not_UNKNOWN(tmp_path):
    """An artifact that installs but ships no `tortoise` entry point cannot run the
    documented command — that is a defect IN THE ARTIFACT. Classifying it as a detector
    outage ("could not run") files it under the wrong owner (#12363 round 5)."""
    wheel = tmp_path / "dist" / "tortoise_graph-0.3.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="",
        workdir=tmp_path,
        timeout=10,
        # an artifact that installs cleanly but ships no `tortoise` entry point
        runner=_Runner({}, creates_db_for=None, creates_console=False),
        wheel=wheel,
    )
    assert arm.status == canary.FAIL, arm.detail
    assert "console script" in arm.detail


def test_the_canary_files_a_durable_alert_and_reads_its_own_exit_code():
    """A step summary lives inside ONE run. The sibling detectors this workflow is
    modelled on file an issue and close it on recovery — without that, a red the canary
    produces is a red nobody opens, which is the 97-day-invisible class it exists to end
    (#12363 round 5).

    ⛔ The report runs in its OWN job, because `if: failure()` is JOB-scoped and cannot
    express a cancelled or timed-out terminal state, and because a gate that reports on a
    job should not live inside the job it reports on. (Do NOT justify this with "a job
    killed at `timeout-minutes` runs no remaining step" — that claim was retracted in round
    9: the server re-evaluates unfinished steps' `if` conditions on cancellation, so an
    in-job `always()` step does get a chance inside the grace window.)
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release-artifact-canary.yml").read_text()
    )
    assert workflow["permissions"].get("issues") == "write", (
        "filing the alert issue needs issues: write — the repo's default token is "
        "restricted (#2140), so without it the detector is deaf while looking green"
    )
    jobs = workflow["jobs"]
    assert "report" in jobs, "the alert must live in a DOWNSTREAM job (a killed job runs no steps)"
    report = jobs["report"]
    assert report.get("needs") == "canary"
    # EQUALITY, not a substring: `always() && needs.canary.result == 'failure'` passes an
    # `in` check while re-silencing the timeout/cancelled state this structure exists for
    # (#12363 round 9).
    report_if = str(report.get("if")).replace("${{", "").replace("}}", "").strip()
    assert report_if == "always()", (
        f"the report job must run on ANY canary terminal state, got {report_if!r}"
    )
    assert "always()" in str(report.get("if")), (
        "the report job must run however the canary ended, else a timed-out canary is silent"
    )
    assert jobs["canary"].get("outputs", {}).get("exit_code"), (
        "the canary job must PUBLISH its exit code — it is the only severity signal"
    )

    steps = {step.get("name"): step for step in report["steps"]}
    alarm = steps["What a red here means"]
    # The comparison, not just the words: only an explicit 1 means "the artifact is
    # broken". Reverting `!= "1"` to `= "2"` left the suite green, after which a cancelled
    # canary (no exit code) rendered "### This is a RELEASE finding" (#12363 round 8).
    assert '!= "1"' in str(alarm), "the alarm must treat anything other than exit 1 as an outage"
    assert '= "2"' not in str(alarm), (
        "matching only 2 lets a cancelled canary read as a release finding"
    )
    assert "needs.canary.outputs.exit_code" in str(alarm.get("env", {}))
    assert "DETECTOR outage" in str(alarm)
    alarm_text = str(alarm.get("run"))
    assert "RUNNER fault" in alarm_text, (
        "the alarm needs its own branch for a run that REACHED a verdict (exit 0) and then "
        "hit a runner fault, or that state stays unexplained"
    )
    # ⛔ The BODY must be bound to its own PREDICATE, and each predicate to the variable the
    # step DECLARES (#12363 round 12; cycle-2 and cycle-3 findings, each measured green
    # before its fix):
    #   * `'= "0"'` is a SUBSTRING of `'!= "0"'`, so a spelling pin passed with the
    #     runner-fault predicate INVERTED — exit 1 and exit 2 then both entered the runner-
    #     fault branch and a REAL release finding was explained as "nothing is owed";
    #   * swapping the two branch BODIES (predicates and order untouched) kept every literal
    #     assertion true while the exit-0 branch printed "detector outage — do NOT cut a
    #     release";
    #   * a POSITION-only pin accepted a branch testing a variable nothing sets
    #     (`${CANARY_RC:-}`): dead code, 56 tests green, and every exit-0 runner fault fell
    #     through to the detector-outage text. The variable is derived from the step's own
    #     `env:` block now, so a consistent rename still passes and a wrong variable does not.
    # The predicates are read as LINES, not as one spelling: `[ x = y ]`, `[[ x == y ]]` and
    # the Yoda form `[ y = x ]` all parse the same way here.
    env_key = next(
        (
            key
            for key, value in (alarm.get("env") or {}).items()
            if "needs.canary.outputs.exit_code" in str(value)
        ),
        None,
    )
    assert env_key, "the alarm step must take the canary's exit code in its `env:` block"
    predicates = [
        line.strip()
        for line in alarm_text.splitlines()
        if line.strip().startswith(("if [", "elif [", "if [[", "elif [["))
    ]
    assert len(predicates) == 2, (
        f"the alarm must be one if/elif over two predicates, got {predicates!r} — a third test "
        "changes which exit codes reach which explanation"
    )
    runner_pred, outage_pred = predicates
    assert f"${{{env_key}" in runner_pred, (
        f"the runner-fault predicate must test ${{{env_key}}} — the variable the step itself "
        f"declares; testing anything else is dead code that never fires: {runner_pred!r}"
    )
    assert f"${{{env_key}" in outage_pred, (
        f"the outage predicate must test ${{{env_key}}} — testing a variable nothing sets "
        f"makes the branch unreachable: {outage_pred!r}"
    )
    assert '"0"' in runner_pred and "!" not in runner_pred, (
        'the runner-fault branch must be a POSITIVE test for "0" — `!=` inverts it, and then '
        "every non-zero exit (a real release finding included) is explained as a runner "
        f"fault that owes nothing: {runner_pred!r}"
    )
    assert '"1"' in outage_pred and "!" in outage_pred, (
        "the outage branch must test that the exit code is NOT 1, or an exit 2 / a cancelled "
        f"canary reads as a release finding: {outage_pred!r}"
    )

    def heading_position(heading: str) -> int:
        found = alarm_text.find(heading)
        assert found != -1, f"the alarm no longer says {heading!r} — its branch structure is gone"
        return found

    runner_at = heading_position("RUNNER fault")
    outage_at = heading_position("DETECTOR outage")
    release_at = heading_position("RELEASE finding")
    runner_pred_at = alarm_text.find(runner_pred)
    outage_pred_at = alarm_text.find(outage_pred)
    assert runner_pred_at < runner_at < outage_pred_at < outage_at < release_at, (
        "the three branch HEADINGS are not inside their own predicates: the RUNNER-fault text "
        "must sit between the `= 0` and `!= 1` tests, and DETECTOR outage after `!= 1` — "
        "swapping the branch BODIES kept every literal assertion green while an exit-0 "
        "runner fault was explained as a detector outage"
    )

    file_step = steps["File or refresh the alert issue"]
    assert "!= 'success'" in str(file_step.get("if")), file_step.get("if")
    assert "needs.canary.result" in str(file_step.get("if")), (
        "filing must key on the canary JOB's result, not a step outcome"
    )
    for name in ("File or refresh the alert issue", "Close the alert issue on a green run"):
        assert "published-artifact-alert.sh" in str(steps[name].get("run")), (
            f"{name} must go through the shared script — inlined shell here is what drifts"
        )
    # ⛔ The close step is the ONE gate that must NOT key on `needs.canary.result` (round 12):
    # a step that ends on an `echo` reads success while the tool exited 1, so a result-keyed
    # close resolves the release-owed ledger over a broken artifact. Its gate is `exit_code`
    # alone — asserted positively below.
    close_if = str(steps["Close the alert issue on a green run"].get("if"))
    assert "needs.canary.result" not in close_if, (
        'close must not key on the job result: a lost `exit "$rc"` makes the job read '
        f"success while the tool exited 1, resolving the release-owed ledger — got {close_if!r}"
    )
    # ⛔ `!cancelled()` on each of the FOUR GATE STEPS (round 12): the implicit `success()`
    # otherwise skips a step whose predecessor failed, so a failed `Runner tool contract` step
    # silently skipped the transcript fetch, or filed nothing, or never resolved the ledgers on
    # a green run. The two SETUP steps above them deliberately carry no `if:` — a checkout or
    # tool-provisioning step that ran on a cancelled job would be worse, not better.
    for name in (
        "Fetch the canary transcript",
        "What a red here means",
        "File or refresh the alert issue",
        "Close the alert issue on a green run",
    ):
        assert "!cancelled()" in str(steps[name].get("if")), (
            f"{name} must carry !cancelled(): without it the implicit success() skips it "
            "when an earlier report step failed"
        )
    # ── the three gates and the transcript pair ──────────────────────────────────────
    # A post-reading runner failure (`result: failure`, `exit_code: 0`) is NOT a finding:
    # filing an outage for it asserts "could not evaluate" about a run that evaluated fine.
    for name in ("What a red here means", "File or refresh the alert issue"):
        gate = str(steps[name].get("if"))
        if name == "File or refresh the alert issue":
            assert "needs.canary.outputs.exit_code != '0'" in gate, (
                f"{name} must skip a run that REACHED a verdict: {gate}"
            )
    # ⛔ EXACT EQUALITY, not containment (#12363 round 13). The containment form
    # (`"needs.canary.outputs.exit_code == '0'" in close_if`) is satisfied by a
    # DISJUNCTION that ALSO closes on an ABSENT reading:
    #   `!cancelled() && (… == '0' || … == '')`  →  measured, every pin GREEN.
    # That is the false close this gate was rewritten to prevent, one level down: a canary
    # that was cancelled, timed out, or never started (checkout failed) publishes no
    # `exit_code`, so BOTH ledgers would be resolved with "Green: the published artifact now
    # passes its documented first command" while `File or refresh the alert issue` stays
    # skipped (`exit_code != '0'` is false) — nothing is filed AND the release-owed issue is
    # falsified by a run that measured nothing. The residual stated a few lines above declares
    # that state impossible ("an ABSENT reading closes nothing"); containment cannot enforce
    # it. Normalised and compared for EQUALITY, the same way the report job's `always()` gate
    # is pinned — a substring is not the expression, and this is the gate's only input.
    close_expr = close_if.replace("${{", "").replace("}}", "").strip()
    assert close_expr == "!cancelled() && needs.canary.outputs.exit_code == '0'", (
        "the close gate must be EXACTLY `!cancelled() && needs.canary.outputs.exit_code == "
        f"'0'` — close must follow the READING and nothing else, got {close_if!r}"
    )
    # The canary JOB's published output is the ONLY thing the close gate now reads, so the
    # expression that publishes it is pinned: repointing `outputs.exit_code` at a renamed or
    # nonexistent step id leaves it empty forever while the job reads success, and with the
    # `result == 'success'` disjunct gone nothing then resolves the release-owed ledger on a
    # green artifact. Measured: that mutation left the whole suite green (#12363 round 12).
    assert "steps.canary.outputs.exit_code" in str(jobs["canary"]["outputs"]["exit_code"]), (
        "the canary job must publish the CANARY STEP's own exit code — anything else leaves "
        "`exit_code` empty while the job reads success, so the close gate never fires"
    )
    # the upload ↔ download pair: `continue-on-error: true` means a rename here can never
    # red anything, it just silently reverts every issue body to "no transcript".
    upload = next(
        s for s in jobs["canary"]["steps"] if str(s.get("uses", "")).startswith("actions/upload")
    )
    fetch = next(
        s for s in report["steps"] if str(s.get("uses", "")).startswith("actions/download")
    )
    assert fetch["with"]["name"] == upload["with"]["name"], (
        f"download {fetch['with']['name']!r} does not match upload "
        f"{upload['with']['name']!r} — the report job would always find no transcript"
    )
    assert fetch.get("continue-on-error") is True, (
        "a job killed before its upload genuinely has no artifact; that must not fail the report"
    )
    # `!cancelled()` is asserted on the fetch step in the loop above, together with the three
    # gates it shares its rationale with (#12363 round 12).
    assert "published-artifact-alert.sh" in str(
        steps["File or refresh the alert issue"].get("run", "")
    )
    assert "needs.canary.outputs.exit_code" in str(
        steps["File or refresh the alert issue"].get("env", {})
    ), (
        "EXIT_CODE is the ONLY carrier of severity into the script: without it every "
        "exit-2 detector outage renders as 'it clears when a release lands'"
    )

    script = REPO_ROOT / ".github" / "scripts" / "published-artifact-alert.sh"
    assert script.is_file()
    text = script.read_text()
    assert "EXIT_CODE" in text and "not** evidence the artifact is broken" in text, (
        "the alert body must separate a detector outage from a broken artifact"
    )
    # It must DELEGATE to the shared substrate (#3907) rather than re-implementing
    # dedupe/Recurrence/close-then-comment inline — a local copy is how the fleet ended up
    # with three different auto-file behaviours in the first place (#12363 round 6).
    assert "auto-file-issue.sh" in text, "the alert must ride the shared auto-file substrate"
    assert "gh issue create" not in text and "gh issue close" not in text, (
        "inline filing is the drift the substrate exists to prevent"
    )
    assert "tail -n 40" in text, "the body must carry the transcript's TAIL (the failure)"
    assert "TITLE_OUTAGE=" in text and "TITLE_RELEASE=" in text, (
        "the two severities need SEPARATE stable titles, or one severity's recurrence lands "
        "on the other's issue with no severity in the text"
    )
    harness = REPO_ROOT / ".github" / "scripts" / "published-artifact-alert.test.sh"
    assert harness.is_file(), "an alert script with no executable harness is unpinned"
    # ⛔ THE STEP, NOT A SUBSTRING (#12363 round 11). This assertion used to be
    # `'published-artifact-alert' in ci`, which the ops-harnesses PATHS FILTER alone
    # satisfies (ci.yml's `startswith(".github/scripts/published-artifact-alert")` row) —
    # so deleting the step that actually RUNS the harness left this pin green and all six
    # properties inside the harness (delegation, the exit-code branch, the transcript
    # TAIL, the no-token fail-closed, the no-op close, the usage error) silently unrun
    # forever. Measured by mutation: with the `run:` step removed and the filter entry
    # kept, the substring form stayed green. Both halves are asserted, so removing either
    # reds: the filter (or the job is never selected on a harness change) and the step (or
    # nothing executes the harness even when selected).
    import yaml as _yaml

    ci_jobs = _yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text())["jobs"]
    harness_runs = [
        (job, step.get("name"))
        for job, body in ci_jobs.items()
        for step in (body.get("steps") or [])
        if ".github/scripts/published-artifact-alert.test.sh" in str(step.get("run") or "")
    ]
    assert harness_runs, (
        "no job in ci.yml RUNS .github/scripts/published-artifact-alert.test.sh — the "
        "harness would never execute, and every property it pins stops being checked "
        "while this suite stays green (a filter/paths entry is not a step)"
    )
    ci_text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert 'startswith(".github/scripts/published-artifact-alert")' in ci_text, (
        "the ops-harnesses paths filter must also list this harness, or the job that runs "
        "it is not selected when the harness changes"
    )


def test_the_canary_cannot_be_SILENCED_without_reding_this_file():
    """The four "cannot be starved into silence" properties, pinned.

    The workflow header calls them "the load-bearing property (#10109)" and NOTHING
    measured them: deleting `on.schedule`, flipping `cancel-in-progress: true`, adding a
    `push: main` trigger, or dropping the tool invocation each left the suite green while
    the canary stopped firing — the 97-day silence this PR exists to end could return
    without a single red (#12363 round 5). Pinning the alert (above) is not enough: a
    detector that no longer RUNS has no red to deliver.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release-artifact-canary.yml").read_text()
    )
    # PyYAML resolves the bare key `on` to the boolean True (YAML 1.1).
    triggers = workflow.get("on", workflow.get(True))
    assert isinstance(triggers, dict), triggers
    schedule = triggers.get("schedule")
    assert schedule and schedule[0].get("cron"), (
        "the canary lost its `schedule:` — a detector that never fires cannot notice a "
        "97-day-old broken artifact"
    )
    assert "push" not in triggers, (
        "a `push:` trigger lets a merge create (and its concurrency group cancel) the "
        "canary's run — the exact starvation #10109 removed"
    )
    assert workflow["concurrency"]["cancel-in-progress"] is False, (
        "a superseded run is a reading nobody got"
    )
    job = workflow["jobs"]["canary"]
    assert job.get("timeout-minutes"), "the run needs a bound it can finish inside"
    run_text = " ".join(str(step.get("run", "")) for step in job["steps"])
    assert "published_artifact_canary.py" in run_text, (
        "the job no longer invokes the tool — the red would be about nothing"
    )
    assert "--transcript-dir" in run_text and "--summary-file" in run_text, (
        "the evidence and the rendered verdict must both be produced by the run"
    )
    smoke = next(step for step in job["steps"] if step.get("id") == "canary")
    assert "published_artifact_canary.py" in str(smoke.get("run")), (
        "the `canary` step id must be the step that runs the tool: the alert steps key "
        "on `steps.canary.outputs.exit_code`"
    )
    # ⛔ THE STEP MUST PROPAGATE THE TOOL'S EXIT STATUS (#12363 round 12). The tool is
    # invoked as `… || rc=$?`, so on a FAIL the NEXT command decides the step's status. If
    # the trailing `exit "$rc"` is dropped, the step's last command is the
    # `echo … >> $GITHUB_OUTPUT` redirect: the step exits 0 and the `canary` JOB reports
    # SUCCESS while `exit_code=1` is published — so the alarm and `File or refresh the alert
    # issue` (`needs.canary.result != 'success'`) are both SKIPPED, `Close …` skips too
    # (`1 != '0'`), and the report job does NOTHING: a broken artifact, a green run, and not
    # one word anywhere. (Before round 12 the same mutation CLOSED the release-owed ledger,
    # because the close step also keyed on `result == 'success'`; that disjunct is gone, so
    # the failure mode is now SILENCE rather than a false close.) Measured: deleting the line
    # left this whole suite green.
    smoke_run = str(smoke.get("run"))
    assert "|| rc=$?" in smoke_run, (
        "the canary call must capture the tool's exit status (the `|| rc=$?` idiom)"
    )
    assert 'exit "$rc"' in smoke_run, (
        'the canary step must END with `exit "$rc"` — without it the step exits 0 whatever '
        "the tool said, the job reports success, and the alert goes SILENT on a FAIL: "
        "nothing filed, nothing closed"
    )
    # ORDER: the output must be published BEFORE the exit, or the redirect never runs and
    # `steps.canary.outputs.exit_code` is empty (which the report job reads as "detector
    # outage" for every severity, including a real release finding).
    assert smoke_run.index("GITHUB_OUTPUT") < smoke_run.index('exit "$rc"'), (
        '`echo exit_code=… >> $GITHUB_OUTPUT` must come BEFORE `exit "$rc"`'
    )
    # ⛔ The PRODUCER end of the chain the close gate now depends on: ONE LIVE LINE must write
    # the KEY the job output READS, to the output sink. Measured green before this pin's two
    # fixes, each on its own (#12363 round 12, cycle-2 and cycle-3):
    #   * `echo "rc=$rc" >> $GITHUB_OUTPUT` — the key renamed; and
    #   * `# echo "exit_code=$rc" >> $GITHUB_OUTPUT` — the write COMMENTED OUT, which a plain
    #     substring test cannot see because the text is still there.
    # Both leave `exit_code` empty forever: nothing closes the release-owed ledger, and every
    # red files as a detector outage. The line is filtered on being live, so a commented-out
    # writer cannot satisfy it; the fragment is only the key + `$rc` + the sink, so `echo`,
    # `printf` and a `tee` append all pass.
    output_key = (
        str(workflow["jobs"]["canary"]["outputs"]["exit_code"])
        .split("steps.canary.outputs.")[1]
        .strip(" }")
    )
    live_lines = [line for line in smoke_run.splitlines() if not line.lstrip().startswith("#")]
    assert any(
        f"{output_key}=" in line and "$rc" in line and "GITHUB_OUTPUT" in line
        for line in live_lines
    ), (
        f"no live line in the canary step writes `{output_key}=…$rc` to $GITHUB_OUTPUT: the "
        f"job output reads `steps.canary.outputs.{output_key}`, so a renamed key (or a "
        "commented-out write) leaves it permanently empty — nothing then closes the "
        "release-owed ledger and every red reads as a detector outage (#12363 round 12)"
    )


def test_an_UNWRITABLE_or_UNREADABLE_report_is_an_exit_2_not_a_traceback(monkeypatch, tmp_path):
    """Every I/O fault in `main` must say "detector", never borrow the release verdict.

    A traceback is exit 1, and exit 1 is what BOTH consumers render as "the published
    artifact is broken — a release is owed". Round 3 fixed that for the fetch path; the
    `pyproject.toml` read and the `--summary-file` append were still uncaught (#12363
    round 5).
    """
    offline = lambda package, timeout: (None, "offline", canary.INDEX_UNREADABLE)  # noqa: E731

    # (a) the tree's own pyproject.toml cannot be read
    monkeypatch.setattr(canary, "fetch_index", offline)
    monkeypatch.setattr(canary, "REPO_ROOT", tmp_path / "not-a-checkout")
    assert canary.main(["--json"]) == 2
    monkeypatch.undo()

    # (b) the summary file cannot be appended to
    monkeypatch.setattr(canary, "fetch_index", offline)
    assert canary.main(["--json", "--summary-file", str(tmp_path / "nope" / "s.md")]) == 2


def test_the_unexpected_failure_boundary_is_pinned(monkeypatch, capsys):
    """Deleting the `try/except` around `_main` left every test green: the specific I/O
    guards have their own tests, so nothing measured the BACKSTOP. Without it a later
    unexpected exception is a traceback → exit 1 → "the artifact is broken, cut a
    release" (#12363 round 7)."""

    def boom(**kwargs):
        raise RuntimeError("a bug nobody anticipated")

    monkeypatch.setattr(
        canary, "fetch_index", lambda package, timeout: (None, "x", canary.INDEX_UNREADABLE)
    )
    monkeypatch.setattr(canary, "build_report", boom)
    assert canary.main(["--json"]) == 2
    assert "a bug nobody anticipated" in capsys.readouterr().err


def test_a_RELOCATED_tortoise_store_under_the_fresh_HOME_is_accepted(tmp_path):
    """The default path is a second copy of `tortoise/config.py`'s decision. If a release
    relocates the store, a hardcoded expectation reds the canary forever and the summary
    says "a release is owed" when the fix belongs in this tool (#12363 round 7).

    The tolerance is bounded: the store must still be TORTOISE's (its file or directory
    names it). A relocation to a store with no tortoise identity in the path is
    indistinguishable from a foreign database, and the sibling case below pins that
    boundary rather than leaving it to chance (#12363 rounds 8–10)."""

    class _Relocated(_Runner):
        def __call__(self, cmd, **kwargs):
            result = super().__call__(cmd, **kwargs)
            if Path(cmd[0]).name == "tortoise":
                store = Path(kwargs["env"]["HOME"]) / ".local" / "share" / "tortoise"
                store.mkdir(parents=True, exist_ok=True)
                (store / "graph.db").write_text("")
            return result

    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Relocated({}, creates_db_for=None),
    )
    assert arm.status == canary.OK, arm.detail
    assert "graph.db" in arm.detail


def test_a_FOREIGN_database_under_HOME_does_not_count_as_initialised(tmp_path):
    """`tortoise init` exiting 0 while creating only an unrelated sqlite store must be a
    FAIL — otherwise the "reported success but created no database" arm can never fire
    (#12363 rounds 8 and 9). The filter matches the path RELATIVE to HOME, so an ancestor
    directory called `tortoise` (a dev checkout, a pool runner's TMPDIR) cannot launder a
    foreign file either."""

    class _Telemetry(_Runner):
        def __call__(self, cmd, **kwargs):
            result = super().__call__(cmd, **kwargs)
            if Path(cmd[0]).name == "tortoise":
                cache = Path(kwargs["env"]["HOME"]) / ".cache" / "telemetry"
                cache.mkdir(parents=True, exist_ok=True)
                (cache / "events.db").write_text("")
            return result

    # ⛔ The workdir is deliberately named `tortoise-checkout`: the pre-fix filter matched
    # the ABSOLUTE path, so on a plain `tmp_path` (no "tortoise" anywhere) the regression
    # was invisible and this test passed under it (#12363 round 10). With a tortoise-named
    # ANCESTOR, the absolute-path filter accepts the foreign file and this case reds.
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path / "tortoise-checkout",
        timeout=10,
        runner=_Telemetry({}, creates_db_for=None),
    )
    assert arm.status == canary.FAIL, arm.detail
    assert "created no database" in arm.detail


def test_the_index_read_ignores_the_ambient_proxy_the_child_strips(monkeypatch):
    """The parent and the child must share ONE transport policy (#12363 final review, P2).

    `_clean_env` strips every proxy spelling from the child, so no proxy can satisfy the
    requirement from a mirror. `urlopen` honours the AMBIENT `http_proxy`/`https_proxy`, so
    before this the two disagreed on a proxy-egress runner in the worst direction: the child
    could not reach the index and printed pip's ABSENCE vocabulary, while the parent answered
    INDEX_OK over the proxy — routed to FAIL/exit 1, "a release is owed", i.e. a request for
    an irreversible upload, on every daily run.

    Asserted on the OPENER's handler policy (an EMPTY `ProxyHandler` is "ignore the
    environment") plus the seam that every index read goes through that one object: a
    behavioural test here would need a proxy to exist, and a monkeypatched `urlopen` is
    exactly the seam this replaced.
    """
    import urllib.request

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")

    proxies = [
        h for h in canary._INDEX_OPENER.handlers if isinstance(h, urllib.request.ProxyHandler)
    ]
    # An EMPTY `ProxyHandler` passed to `build_opener` makes it SKIP the default one, and the
    # default is the only thing that reads `http_proxy`/`https_proxy` — so the assertion is
    # the ABSENCE of any ProxyHandler. One present with non-empty `.proxies` means the
    # environment is honoured again, which is the defect this pins.
    assert proxies == [], (
        f"the index opener carries a ProxyHandler {proxies!r}, so the ambient proxy environment "
        "is honoured — a proxy-egress runner would read INDEX_OK here while the child's "
        "proxy-less pip cannot reach the index at all"
    )

    # And every read must go through it: patching the module-level opener is enough to
    # intercept the fetch, which `urlopen`-based code would have bypassed.
    calls = {}

    class _Resp:
        def read(self):
            return b'{"info": {"version": "0.2.0"}}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Opener:
        def open(self, request, timeout=None):
            calls["hit"] = getattr(request, "full_url", None)
            return _Resp()

    monkeypatch.setattr(canary, "_INDEX_OPENER", _Opener())
    doc, reason, state = canary.fetch_index("tortoise-graph", timeout=1)
    assert state == canary.INDEX_OK, (reason, state)
    assert doc == {"info": {"version": "0.2.0"}}
    assert calls["hit"] and "pypi.org/pypi/tortoise-graph/json" in calls["hit"], calls
