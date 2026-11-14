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
import os
import re
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
                    # ⛔ `yanked` IS PART OF A DOCUMENTED FILE RECORD (#12589 review round 12,
                    # P3): without it the entry is an UNREADABLE value to `judged_value`, so this
                    # "healthy" fixture was a shape the canary refuses to judge — and any future
                    # test that handed it to a judging path would silently get UNKNOWN.
                    "yanked": False,
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

    ⛔ As of #12525 that same shape is UNKNOWN (a document with no usable `releases` map is a
    NON-READING, never an answer), so it is now neither the healthy default NOR a FAIL — which
    is the stronger reason a fixture must carry files: a shape that cannot answer the question
    cannot certify the arm either.
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
    # ⛔ "HTTP 404" ALONE IS NOT A PIN (#12422 round-23 review P2): the PRE-fix text
    # ("could not read the tortoise-client index metadata: HTTP 404 from …") contains
    # that substring too, so restoring the 404-versus-timeout conflation on this branch
    # left the assertion green. Pin the branch's OWN sentence, which only the finding
    # spells.
    assert "is not resolvable on the index" in report.get("client-published").detail, report.get(
        "client-published"
    ).detail
    assert "could not read the" not in report.get("client-published").detail
    # ⛔ AND THE EVIDENCE ITSELF STILL HAS TO REACH THE HUMAN (#12422 round-24 review P2).
    # Replacing the old `assert "HTTP 404" in …` (too weak a conflation pin) silently
    # dropped the ONLY assertion that `client_error` survives into the detail: measured,
    # deleting `({client_error or 'no result'})` from both arms left all 72 green while
    # the detail named the finding and withheld the URL that justifies it — and this is
    # the sentence a human reads before an IRREVERSIBLE upload. The URL is the evidence,
    # not the verdict word, so it is pinned by hand here rather than inferred.
    assert "pypi.org/pypi/tortoise-client" in report.get("client-published").detail, report.get(
        "client-published"
    ).detail


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


def test_an_UNRECOGNISED_gated_status_is_never_green():
    """ALLOW-LIST polarity: only an explicit `OK` is green (#12422 review, P2).

    `Report.verdict` used to end in `return OK` — a DENY-list, so any gated status outside
    `{FAIL, UNKNOWN}` reported GREEN. Measured before the fix: `Arm("engine-init", s)` gave
    `"OK"/exit 0` for `s` in `{"PASS", "SKIPPED", "Ok", "ok", ""}`, and `PASS` is the word
    `render_summary` prints as the report's own heading — the spelling that greens the gate is
    the one this tool renders. AGENTS.md's polarity rule ("EVERY OTHER CONCLUSION IS RED —
    including one GitHub has not documented yet, and a null one") forbids the gap.

    Unreachable through today's producers; pinned anyway, because a structural guarantee must
    not depend on the discipline of every future call site.
    """
    for spelling in ("PASS", "SKIPPED", "Ok", "ok", "", "SUCCESS"):
        report = canary.Report()
        report.add(canary.Arm("engine-init", spelling, "an unrecognised status"))
        assert report.verdict != canary.OK, (
            f"a gated arm carrying {spelling!r} reported {report.verdict!r}/exit "
            f"{report.exit_code} — an unnamed status fell into the green gap"
        )
        assert report.exit_code != 0, spelling
    # …and the recognised all-clear is still green, so this is polarity and not a lockdown.
    ok_report = canary.Report()
    ok_report.add(canary.Arm("engine-init", canary.OK, "a real pass"))
    assert ok_report.verdict == canary.OK
    assert ok_report.exit_code == 0


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
    # ⛔ THE ARM'S OWN SENTENCE, NOT JUST ITS STATUS (#12422 round-23 review P2). The
    # FAIL/UNKNOWN split was corrected by prose alone and verified BY HAND — so a revert
    # of the two-branch detail restored "could not read the … index metadata: HTTP 404"
    # over a FINDING and all 72 tests stayed green. That is the "nothing reds on them"
    # disease this PR documents; a hand check is not a pin.
    assert "is not on the index" in absent.get("engine-index").detail, absent.get(
        "engine-index"
    ).detail
    assert "could not read the" not in absent.get("engine-index").detail
    # the underlying index error is the evidence for the finding, so it must survive
    # into the detail — see the sibling note in
    # `test_absent_client_distribution_is_red_not_absent_ok` (round-24 review P2).
    assert "pypi.org/pypi/tortoise-graph" in absent.get("engine-index").detail, absent.get(
        "engine-index"
    ).detail

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
    # and the no-answer side says so, in its own words — the same pin in the other
    # direction, so neither branch can be swapped for the other silently.
    # ⛔ THE OTHER DIRECTION IS AN ABSENCE, NOT A SECOND PHRASE (#12422 round-24 review
    # P2). Asserting only that "UNKNOWN, not absent" is PRESENT leaves the finding
    # sentence free to appear on this side too: measured, an engine UNKNOWN detail of
    # "tortoise-graph is not on the index (… ) — the distribution's presence is UNKNOWN,
    # not absent" passed every one of the 72 tests while telling a reader the project was
    # absent on a timeout. The finding phrase is therefore pinned ABSENT here, which is
    # what makes the sentence above true.
    assert "UNKNOWN, not absent" in unreadable.get("engine-index").detail, unreadable.get(
        "engine-index"
    ).detail
    assert "is not on the index" not in unreadable.get("engine-index").detail, unreadable.get(
        "engine-index"
    ).detail


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
    # the finding phrase must be ABSENT on the no-answer side — the mirror of the engine
    # arm's round-24 pin, so this branch cannot silently start speaking the FAIL sentence.
    assert "is not resolvable on the index" not in arm.detail, arm.detail
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


def test_a_client_version_with_no_INSTALLABLE_file_in_ANY_release_is_red():
    """The question is "does the UNPINNED requirement resolve", asked across the WHOLE release map (#12363 rounds 14 and 15).

    `pip install tortoise-client` is UNPINNED, so pip IGNORES a yanked release (PEP 592) and
    resolves to the highest release that still has an installable file. Two earlier readings
    of this arm were wrong, and both are pinned here:

      * `info.version` ALONE (round 14's predecessor): a release that advertises a version and
        ships no file read PASS, filed nothing, and — with the close gate on
        `exit_code == '0'` — RESOLVED both ledgers. The engine arm was never fooled only
        because pip has to install it.
      * `info.version`'s FILE LIST (round 14): wrong SCOPE in the other direction. With the
        latest release yanked and an older one installable, pip resolves fine, yet that arm
        FAILed with "README.md documents `pip install …`, which cannot resolve" — a false
        statement that orders an irreversible release, fired by a maintainer doing exactly what
        PyPI prescribes for a broken release. Measured with pip: the unpinned requirement
        resolves to the older release, and yanking is whole-release on PyPI.

    So: red only when NO release has a non-yanked file — the sole state where the sentence is
    true — and green when any release does, naming which. ⛔ AND UNKNOWN when a release VALUE could
    not be read: the FAIL sentence needs a COMPLETE parse, so an unreadable sibling withholds it
    (that third state is `#12589`'s whole subject; this docstring stated a two-valued rule until
    review round 18, contradicting the section header below).
    """

    def _report(releases: dict) -> object:
        return canary.build_report(
            engine_doc=_engine_doc(),
            client_doc={"info": {"version": "0.3.0"}, "releases": releases},
            tree_version="0.2.0",
            init_arm=_ok_init_arm(),
            now=NOW,
        )

    good = {
        "filename": "tortoise_client-0.2.0-py3-none-any.whl",
        "yanked": False,
        "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
    }
    yanked = {
        "filename": "tortoise_client-0.3.0-py3-none-any.whl",
        "yanked": True,
        "upload_time_iso_8601": "2026-09-01T00:00:00Z",
    }

    # RED: nothing installable anywhere — the documented command cannot resolve.
    for releases, why in (
        ({"0.3.0": []}, "empty release"),
        ({"0.3.0": [yanked]}, "only-yanked"),
        ({}, "no releases at all"),
    ):
        report = _report(releases)
        arm = report.get("client-published")
        assert arm.status == canary.FAIL, (why, arm.status, arm.detail)
        assert report.exit_code == 1, (why, report.exit_code)
        assert "any release" in arm.detail.lower(), (why, arm.detail)

    # ⛔ AND THE SENTENCE THE ALERT RENDERS IS PINNED IN FULL (#12589 review round 16, P3). The
    # bare `"any release"` substring above let the whole body drift: dropping the count, bumping it
    # by one, or dropping the version pointer all stayed green — and this is the text an operator
    # reads as **"cut a release"**.
    two_yanked = _report({"0.3.0": [yanked, {**yanked, "filename": "tortoise_client-0.3.0-2.whl"}]})
    detail = two_yanked.get("client-published").detail
    assert "(2 file(s) on 0.3.0, none usable)" in detail, detail
    assert "README.md documents `pip install tortoise-client`, which cannot resolve" in detail, (
        detail
    )

    # GREEN: the latest release is yanked but an older one is installable — pip resolves, so
    # the arm must NOT claim the documented install cannot resolve (round 15's false FAIL).
    report = _report({"0.3.0": [yanked], "0.2.0": [good]})
    arm = report.get("client-published")
    assert arm.status == canary.OK, (arm.status, arm.detail)
    assert report.exit_code == 0, report.exit_code
    assert "0.2.0" in arm.detail, arm.detail

    # GREEN: the ordinary healthy shape.
    assert _report({"0.3.0": [good]}).get("client-published").status == canary.OK


def test_an_ABSENT_releases_key_is_UNKNOWN_and_never_orders_a_release():
    """`{}` present and `releases` absent are DIFFERENT answers (#12525).

    PyPI documents `releases` as deprecated and removable — "In the future, each of these
    keys may be removed entirely from this API response" (docs.pypi.org/api/json). Before
    this pin, both shapes collapsed to `{}` at the client arm, so a deprecation landing (or a
    mirror/cache answering `info` only) produced `resolvable == []` and the arm reported
    "lists NO installable file in ANY release … README.md documents `pip install
    tortoise-client`, which cannot resolve" — a FAIL, i.e. exit 1, i.e. the text the alarm
    renders as **"cut a release"**, ordering an irreversible PyPI upload off a response that
    never answered the question.

    So the split is asserted on BOTH sides, because either half alone is satisfiable by the
    wrong code:
      * absent key  → UNKNOWN → exit 2 (no answer; never a release order);
      * present `{}` → FAIL → exit 1 (an ANSWER: no releases; pinned by the test above).
    """

    def _report(client_doc: dict) -> object:
        return canary.build_report(
            engine_doc=_engine_doc(),
            client_doc=client_doc,
            tree_version="0.2.0",
            init_arm=_ok_init_arm(),
            now=NOW,
        )

    # THE FIX: an `info`-bearing document that cannot supply a `releases` MAP cannot answer
    # the question. Absent, explicit JSON `null`, and a wrong-typed value are all the same
    # class: a non-reading. The wrong-typed case is the module's own allow-list doctrine
    # ("a state a later refactor introduces" is a NON-READING, never a verdict) — mapping it
    # to `{}` would turn it into the ANSWER "no releases" and so into a FAIL/exit 1.
    for absent in (
        {"info": {"version": "0.3.0"}},  # a deprecation landing / `info`-only cache
        {"info": {"version": "0.3.0"}, "releases": None},  # explicit JSON null
        {"info": {"version": "0.3.0"}, "releases": []},  # wrong type: list
        {"info": {"version": "0.3.0"}, "releases": "0.3.0"},  # wrong type: str
    ):
        report = _report(absent)
        arm = report.get("client-published")
        assert arm.status == canary.UNKNOWN, (absent, arm.status, arm.detail)
        assert report.verdict == canary.UNKNOWN, (absent, report.verdict)
        assert report.exit_code == 2, (absent, report.exit_code)
        # The reason must NAME the deprecation and must NOT carry the release-order clause.
        assert canary.RELEASES_ABSENT in arm.detail, arm.detail
        assert "deprecated" in arm.detail.lower(), arm.detail
        assert "not a reason to cut a release" in arm.detail.lower(), arm.detail
        assert "any release" not in arm.detail.lower(), arm.detail
        assert "cannot resolve" not in arm.detail.lower(), arm.detail

    # AND the state that must NOT move: `releases` PRESENT but empty is still a FAIL.
    empty = _report({"info": {"version": "0.3.0"}, "releases": {}})
    assert empty.get("client-published").status == canary.FAIL
    assert empty.exit_code == 1


def test_releases_map_separates_ABSENT_from_EMPTY():
    """The three-state truth table, tested at the helper both arms now share (#12525).

    A caller reading this helper is the only thing standing between one response and two
    arms disagreeing about it, so the distinction is pinned directly rather than only
    through `build_report`.
    """
    assert canary.releases_map({"info": {"version": "0.2.0"}}) is None
    assert canary.releases_map({"releases": None}) is None
    assert canary.releases_map({"releases": {}}) == {}
    assert canary.releases_map({"releases": {"0.2.0": []}}) == {"0.2.0": []}
    # A non-dict `releases` is NOT a map and NOT an empty answer — it is a NON-READING.
    # Routing it to `{}` would make it the ANSWER "no releases" and so a FAIL/exit 1: the
    # release order. The module's allow-list doctrine says an unnamed value is never a
    # verdict (#12525 review, P2).
    assert canary.releases_map({"releases": []}) is None
    assert canary.releases_map({"releases": "0.3.0"}) is None
    assert canary.releases_map({"releases": 0}) is None

    # The absent key must not raise on the informational path either (the `release-age` and
    # `tree-version` rows are gated=False, so a crash here would take down a run that has
    # nothing to report).
    # ⛔ CHANGED by #12589 (review round 2, P3): this was `== []`, i.e. an ABSENT map was spelled
    # as the ANSWER "no files listed" — the map-level version of the very class this issue removes,
    # and a disagreement with `releases_map` (which returns `None` for the same document). `None`
    # means "no usable listing"; `[]` is reserved for a READABLE map with no such version key.
    assert canary.release_files({"info": {"version": "0.2.0"}}, "0.2.0") is None
    assert canary.release_files({"releases": {}}, "0.2.0") == []
    assert canary.upload_time({"info": {"version": "0.2.0"}}, "0.2.0") is None


def test_index_mode_installs_the_DOCUMENTED_unpinned_spec(tmp_path):
    """`pip install tortoise-graph`, not `tortoise-graph==info.version` (#12363 round 15).

    The pinned form is a different question from the documented command in exactly the case
    that matters: PyPI yanks WHOLE RELEASES and `info.version` keeps naming the yanked one, so
    pip INSTALLS the yanked file for an exact `==` pin and IGNORES it — resolving to the
    previous release, or failing — for the unpinned requirement. Measured with pip against a
    local PEP 503 page whose only release is yanked: unpinned → "Ignored the following yanked
    versions … No matching distribution found"; `==<yanked>` → resolves it.

    Consequence of getting this wrong, measured on the pre-fix code: a fully-yanked latest
    release installed and ran, the verdict was PASS / exit 0, and `report`'s close gate
    RESOLVED both alert ledgers on a run whose documented install is impossible.
    """
    captured = {}

    def runner(cmd, **kwargs):
        if cmd[1:3] == ["-m", "venv"]:
            venv = Path(cmd[3])
            (venv / "bin").mkdir(parents=True, exist_ok=True)
            (venv / "bin" / "pip").write_text("")
            return _completed(cmd, 0)
        if str(Path(cmd[0]).name) == "pip":
            captured["cmd"] = cmd
        return _completed(cmd, 0)

    canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=runner,
    )
    pip_call = captured.get("cmd")
    assert pip_call, "the install never ran"
    assert "tortoise-graph" in pip_call, pip_call
    assert not any(arg.startswith("tortoise-graph==") for arg in pip_call), (
        "index mode must install the DOCUMENTED unpinned requirement — a `==` pin installs a "
        "yanked release that no unpinned user can get, and reports PASS over bytes nobody "
        f"receives: {pip_call}"
    )


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
    version will not install), and every other value is UNKNOWN — UNREADABLE, and every
    UNNAMED state (``None``, ``""``, a future one): a non-zero exit that never sends a
    human to cut an irreversible release (#12363 rounds 3 → 4 → 11; the allow-list is
    #12422 round 19)."""
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
    # ⛔ AN UNNAMED PROBE STATE IS NEVER THE RELEASE CALL (#12422 round-19 review P2).
    # This branch used to DENY-LIST: only INDEX_UNREADABLE reached UNKNOWN, so `None` — no
    # reading at all — fell into BOTH the FAIL and the "the index still serves the project
    # (None)" sentence, i.e. it told a human to perform an IRREVERSIBLE upload on a detector
    # that had produced nothing. Every value that is not one of the two NAMED states must
    # route to UNKNOWN, whatever a future refactor decides to call a state.
    for unnamed in (None, "", "some-future-state"):
        unnamed_arm = _run(unnamed)
        assert unnamed_arm.status == canary.UNKNOWN, (
            f"probe state {unnamed!r} is not a reading about the artifact, so it must not "
            f"become a release call (got {unnamed_arm.status}: {unnamed_arm.detail})"
        )
        assert "users cannot even install" not in unnamed_arm.detail, unnamed_arm.detail
        assert "detector outage" in unnamed_arm.detail, unnamed_arm.detail
    # ⛔ A DEPENDENCY'S ABSENCE IS NOT THIS ARTIFACT'S ABSENCE (#12422 round-21 review P2).
    # `pip install tortoise-graph` prints the SAME absence vocabulary when a TRANSITIVE
    # dependency cannot be resolved, and in index mode the probe then answers `ok` about
    # `tortoise-graph` — which this allow-list routes to FAIL. Measured before the fix:
    # FAIL / exit 1, "users cannot even install it; a release is owed", i.e. an
    # irreversible PyPI release ordered for a defect no release can fix. Wheel mode already
    # refused this attribution
    # (`test_the_wheel_mode_install_failure_is_never_attributed_to_the_index`); index mode
    # now does too — the failure must NAME the package under test.
    #
    # ⛔ THE FIXTURE IS THE MEASURED REAL OUTPUT, including pip's `(from tortoise-graph)`
    # clause (#12422 round-21 review, P3). Without that clause a near-miss implementation
    # — "the package name appears ANYWHERE in the failure" — passes this test and is wrong
    # on the only input that matters, because real pip puts our name in the dependency's
    # line. Measured: the near-miss returned True for the real bytes while this fixture
    # stayed green.
    dependency_marks = (
        "ERROR: Could not find a version that satisfies the requirement "
        "falkordblite>=0.4 (from tortoise-graph) (from versions: none)\n"
        "ERROR: No matching distribution found for falkordblite>=0.4"
    )
    dependency_arm = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({"pip": _completed(["pip", "install"], 1, "", dependency_marks)}),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert dependency_arm.status == canary.UNKNOWN, (
        f"a missing THIRD-PARTY dependency is not a finding about tortoise-graph, so it "
        f"must not order a release (got {dependency_arm.status}: {dependency_arm.detail})"
    )
    assert "users cannot even install" not in dependency_arm.detail, dependency_arm.detail
    # it takes the "could not be evaluated" UNKNOWN arm, not a verdict arm — and the
    # detail must still SHOW the evidence, so a human can see WHOSE absence pip reported.
    assert "could not be evaluated" in dependency_arm.detail, dependency_arm.detail
    assert "falkordblite" in dependency_arm.detail, dependency_arm.detail

    # ⛔ AND THE NEGATIVE DIRECTION OF THE SAME CLAUSE, MEASURED (#12422 round-21 delta
    # review, P1). `Failed to build '<us>' when installing build dependencies for <us>` is
    # pip's wrapper naming THE CANDIDATE BEING PREPARED, and the cause is the line above
    # it: the subprocess installs OTHER distributions. So this exact byte sequence — our
    # page 200, our sdist downloaded, a transient failure only on the build dependency's
    # page — is a pip run that tells us NOTHING about whether our artifact is absent, and
    # attributing it to the artifact orders an irreversible release a retry would have made
    # unnecessary. The previous cut graded it FAIL (measured); it must be UNKNOWN.
    build_deps_marks = (
        "  × installing build dependencies for tortoise-graph did not run successfully.\n"
        "  ╰─> [3 lines of output]\n"
        "      ERROR: Could not find a version that satisfies the requirement "
        "not-a-real-build-backend>=1 (from versions: none)\n"
        "      ERROR: No matching distribution found for not-a-real-build-backend>=1\n"
        "ERROR: Failed to build 'tortoise-graph' when installing build dependencies for "
        "tortoise-graph"
    )
    build_deps_arm = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({"pip": _completed(["pip", "install"], 1, "", build_deps_marks)}),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert build_deps_arm.status == canary.UNKNOWN, (
        f"a build DEPENDENCY's fetch failure is not a finding about tortoise-graph's own "
        f"absence, so it must not order a release (got {build_deps_arm.status}: "
        f"{build_deps_arm.detail})"
    )
    assert "users cannot even install" not in build_deps_arm.detail, build_deps_arm.detail
    assert "build dependencies" in build_deps_arm.detail, (
        "the detail must still SHOW the evidence, so a human can see this was a step that "
        f"installs OTHER distributions, not our absence from the index (got "
        f"{build_deps_arm.detail})"
    )

    # ⛔ …and the arm is NOT disabled: the step that DOES reach this wrapper is still a
    # release call, so an unbuildable sdist at that step is caught.
    # ⚠️ THERE IS EXACTLY ONE SUCH STEP, and the list is short because pip's source is
    # short (#12422 round-30 review, P1). The wrapper is raised from one `except
    # InstallationSubprocessError` in `resolution/resolvelib/candidates.py::_prepare`, and
    # the other two steps a reader expects never reach it: `operations/build/metadata.py`
    # re-raises `MetadataGenerationFailed` (NOT an `InstallationSubprocessError`), and
    # `operations/build/wheel.py` catches bare `Exception` and returns `None`. The two rows
    # that used to sit here asserted FAIL on messages pip does not print — a test pinning a
    # fiction is worse than no test, because it certifies the fiction.
    own_build_arm = canary.install_and_run(
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
                    "ERROR: Failed to build 'tortoise-graph' when getting requirements "
                    "to build wheel",
                )
            }
        ),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert own_build_arm.status == canary.FAIL, (
        f"the artifact failing to build ITSELF at `getting requirements to build wheel` "
        f"IS a finding about the artifact, so it must stay a release call (got "
        f"{own_build_arm.status}: {own_build_arm.detail})"
    )

    # ⛔ AND THE TWO STEPS THAT DO **NOT** REACH THE WRAPPER ARE PINNED AS THE BLIND SPOT
    # THEY ARE, not as defects they are not (#12422 round-30 review, P1; #12584). These are
    # the shapes pip actually prints for our own metadata/wheel build failing — the
    # `throw`/`logger.error` sites in `operations/build/metadata.py` and
    # `operations/build/wheel.py`, plus pip's `«<step>» did not run successfully.` framing
    # (`exceptions.py`) — and today they read UNKNOWN. ⚠️ If a future change makes either
    # one a FAIL, this assertion REDS ON PURPOSE: the residual has closed, and the next lane
    # must update #12584 rather than assume the blind spot still exists.
    for blind_step, marks in (
        (
            "preparing metadata",
            "  × Preparing metadata (pyproject.toml) did not run successfully.\n"
            "  × Encountered error while generating package metadata.",
        ),
        (
            "building wheel for",
            "  × Building wheel for tortoise-graph (pyproject.toml) did not run successfully.\n"
            "ERROR: Failed building wheel for tortoise-graph",
        ),
    ):
        blind_arm = canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=tmp_path,
            timeout=10,
            runner=_Runner({"pip": _completed(["pip", "install"], 1, "", marks)}),
            absent_probe=lambda package: canary.INDEX_OK,
        )[0]
        assert blind_arm.status == canary.UNKNOWN, (
            f"`{blind_step}` is a release-owed defect this predicate CANNOT see (pip "
            f"converts or swallows it before the wrapper), so it must stay UNKNOWN rather "
            f"than pretend — if this now reads FAIL the blind spot has closed and #12584 "
            f"must be updated (got {blind_arm.status}: {blind_arm.detail})"
        )

    # ⛔ AND AN UNRECOGNISED STEP IS NOT A VERDICT. The allow-list is what buys this: a
    # step pip adds later must fall to UNKNOWN, never to FAIL, because FAIL is the one
    # verdict that orders an irreversible release. A deny-list would have graded it FAIL.
    unknown_step_arm = canary.install_and_run(
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
                    "ERROR: Failed to build 'tortoise-graph' when some future pip step",
                )
            }
        ),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert unknown_step_arm.status == canary.UNKNOWN, (
        f"a step this predicate does not know must fall to UNKNOWN, never to a release "
        f"call (got {unknown_step_arm.status}: {unknown_step_arm.detail})"
    )
    # ⛔ AND THE SECOND BUILD-DEPENDENCY STEP IS EXCLUDED TOO, not just the first
    # (#12422 round-31 review, P3). `distributions/sdist.py` installs the static
    # `build-system.requires` as kind="build dependencies" and the backend's DYNAMIC
    # requirements as kind="backend dependencies", and `build_env/installer.py` embeds OUR
    # name in both step descriptions while the subprocess installs OTHER distributions.
    # Same fail-closed reasoning, same verdict — pinned so a future widening has to notice.
    backend_deps_arm = canary.install_and_run(
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
                    "ERROR: Failed to build 'tortoise-graph' when installing backend "
                    "dependencies for tortoise-graph",
                )
            }
        ),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert backend_deps_arm.status == canary.UNKNOWN, (
        f"a BACKEND dependency's fetch failure names us in the step description and still "
        f"says nothing about our own absence, so it must not order a release (got "
        f"{backend_deps_arm.status}: {backend_deps_arm.detail})"
    )

    # …and the SAME sentence naming a DEPENDENCY's build is still not ours.
    dep_build_arm = canary.install_and_run(
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
                    "ERROR: Failed to build 'falkordblite' when installing build "
                    "dependencies for falkordblite",
                )
            }
        ),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert dep_build_arm.status == canary.UNKNOWN, (
        f"a DEPENDENCY's unbuildable metadata is not a finding about tortoise-graph (got "
        f"{dep_build_arm.status}: {dep_build_arm.detail})"
    )
    # …and the arm is NARROWED, not disabled: the attribution is PEP 503-normalised, so a
    # failure that names this package in another spelling still reaches the probe verdict.
    respelled_arm = canary.install_and_run(
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
                    "ERROR: No matching distribution found for Tortoise_Graph==0.2.0",
                )
            }
        ),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert respelled_arm.status == canary.FAIL, (
        f"a failure that names THIS package must still reach the probe verdict, or the "
        f"dependency fix has disabled the arm it narrowed (got {respelled_arm.status}: "
        f"{respelled_arm.detail})"
    )
    # ⛔ AND A SIBLING'S ABSENCE IS NOT THIS PACKAGE'S ABSENCE EITHER. The attribution is an
    # EQUALITY on the PEP 503-normalised name, and this case is what makes that boundary
    # load-bearing rather than incidental: `tortoise-graph-extra` CONTAINS
    # `tortoise-graph`, so a regression to a prefix/substring matcher accepts it and orders
    # an irreversible release for a defect that release cannot fix. Measured: with
    # `== wanted` relaxed to `.startswith(wanted)` all other cases stay green and only this
    # one reds, which is the whole reason it is here (`falkordblite` above binds no name
    # matcher at all, so it cannot pin the equal-vs-prefix boundary).
    sibling_marks = (
        "ERROR: Could not find a version that satisfies the requirement "
        "tortoise-graph-extra==1.0 (from versions: none)\n"
        "ERROR: No matching distribution found for tortoise-graph-extra==1.0"
    )
    sibling_arm = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({"pip": _completed(["pip", "install"], 1, "", sibling_marks)}),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert sibling_arm.status == canary.UNKNOWN, (
        f"`tortoise-graph-extra` is a DIFFERENT distribution whose name merely contains "
        f"this one — its absence must not order a release (got {sibling_arm.status}: "
        f"{sibling_arm.detail}); a prefix/substring matcher here re-opens the exact "
        f"mis-order this arm was narrowed to close"
    )
    assert "users cannot even install" not in sibling_arm.detail, sibling_arm.detail
    # ⛔ …AND THE MIRROR (round-24 review P2). The case above pins `named` being LONGER
    # than `wanted`, so a `.startswith(wanted)` regression reds. It does NOT pin `named`
    # being SHORTER — i.e. pip naming a distribution that `wanted` CONTAINS — so the
    # opposite one-token regression, `== wanted` → `named in wanted` (or
    # `wanted.startswith(named)`), reproduces the round-21 defect with every other case
    # still green: a missing dependency called `tortoise` would be graded as
    # `tortoise-graph`'s absence and order an irreversible release for a defect that
    # release cannot fix. That is not hypothetical — `tortoise` is exactly the kind of
    # name a meta-package depends on. Measured: with `== wanted` relaxed to
    # `named in wanted`, this case reds and the suite is otherwise unchanged.
    shorter_marks = (
        "ERROR: Could not find a version that satisfies the requirement "
        "tortoise==0.9 (from versions: none)\n"
        "ERROR: No matching distribution found for tortoise==0.9"
    )
    shorter_arm = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner({"pip": _completed(["pip", "install"], 1, "", shorter_marks)}),
        absent_probe=lambda package: canary.INDEX_OK,
    )[0]
    assert shorter_arm.status == canary.UNKNOWN, (
        f"`tortoise` is a DIFFERENT distribution that `tortoise-graph`'s name merely "
        f"contains — its absence must not order a release (got {shorter_arm.status}: "
        f"{shorter_arm.detail}); a substring matcher in this direction re-opens the same "
        f"mis-order the LONGER-name case closes"
    )
    assert "users cannot even install" not in shorter_arm.detail, shorter_arm.detail
    # ⛔ AND THE REST OF THE BOUNDARY, AS A TABLE (#12422 round-25 review P2). Rounds 23–24
    # closed this boundary one mutation at a time — first `named in wanted`, then
    # `named.startswith(wanted)` — and each round found ANOTHER one-token relaxation the
    # cases above could not see: `_canonical_dist_name(named).endswith(wanted)` keeps the
    # whole file green, and pip naming `x-tortoise-graph` is then graded as
    # `tortoise-graph`'s absence while the probe answers `ok` — the release call again.
    # So the neighbouring matchers are pinned TOGETHER here, and the ONE row that MUST
    # still attribute is the DOTTED spelling (`tortoise.graph`), which is a real spelling
    # `_canonical_dist_name`'s docstring names. ⛔ THE EXTRAS ROW IS DEFENSIVE, NOT A SHAPE
    # PIP EMITS (#12422 round-28 review P2): measured with real pip
    # (`pip install 'tortoise-graph-doesnotexist-xyz[embeddings]==99.9.9'`), pip DROPS the
    # extras before reporting — "No matching distribution found for
    # tortoise-graph-doesnotexist-xyz==99.9.9" — because the resolver path applies
    # `install_req_drop_extras`, so an `[extras]` specifier never reaches this matcher from
    # a real install. The row stays anyway: it is the only row that reds if
    # `_ABSENCE_NAMED` is ever widened to capture the bracket, and being wrong here orders
    # a release — but it must not be described as pip's own output.
    boundary = (
        ("x-tortoise-graph", canary.UNKNOWN),  # suffix matcher
        # ⛔ `foo-tortoise-graph` used to sit here and was REMOVED: it had zero unique
        # discriminating power over the row above — every matcher in the family treats the
        # two identically, so no mutant redded it that did not red `x-tortoise-graph`
        # first. A row nothing can fail alone is a row that documents nothing.
        # ⛔ AND THE MIRROR SUFFIX (#12422 round-26 review P2): `graph` is a proper suffix of
        # `tortoise-graph`, so `wanted.endswith(named)` accepts it — the last of the four
        # one-token relaxations to stay green. Narrow exposure (only a capturable proper
        # suffix reaches it) but the same verdict, so it is pinned with the rest.
        ("graph", canary.UNKNOWN),  # named is a proper SUFFIX of wanted
        # T6: the OTHER spelling `_canonical_dist_name`'s docstring names. The suite pinned
        # `Tortoise_Graph` (case + `_`) but never the DOTTED form, so relaxing `[-_.]+` to
        # `[-_]+` survived the whole file — and a dotted absence would then grade UNKNOWN
        # instead of FAIL. Suppression rather than a false release, but the function contract
        # states all three spellings, so all three are pinned (#12422 round-27 review P2).
        ("tortoise.graph", canary.FAIL),  # dotted spelling normalises to the same name
        # ⛔ AND THE NORMALISER ITSELF (#12422 round-28 review P2): every row above tests the
        # COMPARISON, so a `_canonical_dist_name` that ERASES separators instead of
        # collapsing them to `-` survived the whole file green — and `tortoisegraph` is a
        # DIFFERENT project, so accepting it re-opens the release call the table exists to
        # close (`re.sub(r"[-_.]+", "", …)` measured: 72 passed, this row reds).
        ("tortoisegraph", canary.UNKNOWN),  # separators ERASED, not collapsed
        ("tortoise-graph[embeddings]", canary.FAIL),  # extras: defensive, see above
    )
    for named, expected in boundary:
        boundary_marks = (
            f"ERROR: Could not find a version that satisfies the requirement {named}==1.0 "
            "(from versions: none)\n"
            f"ERROR: No matching distribution found for {named}==1.0"
        )
        boundary_arm = canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=tmp_path,
            timeout=10,
            runner=_Runner({"pip": _completed(["pip", "install"], 1, "", boundary_marks)}),
            absent_probe=lambda package: canary.INDEX_OK,
        )[0]
        assert boundary_arm.status == expected, (
            f"pip naming {named!r} must be {expected} against package 'tortoise-graph' "
            f"(got {boundary_arm.status}: {boundary_arm.detail}); the attribution is an "
            f"EQUALITY on the PEP 503-normalised name — every neighbouring matcher "
            f"(prefix, substring, prefix-suffix, proper-suffix) re-opens the release call "
            f"this arm was narrowed to close"
        )
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


def test_the_absence_name_match_NORMALISES_the_name_it_compares(tmp_path):
    """`_install_failure_is_absence` compares PEP 503-normalised names, and that
    normaliser is load-bearing (#12422 round-21 review, P3).

    Measured before this test existed: removing `.strip("-")` reddened NOTHING, and the
    first two attempts to justify the line were both wrong — one described an unquoted
    spelling, the other claimed the quoted form cannot carry trailing punctuation. The
    capture class includes `.` and `-`, so it can: `Failed to build 'tortoise-graph.'`
    captures `tortoise-graph.`. The pin is the direct-call assertions below, and the
    docstring says plainly that pip's own `package_name` does not end in punctuation today.
    """
    assert canary._canonical_dist_name("Tortoise_Graph") == "tortoise-graph"
    assert canary._canonical_dist_name("tortoise.graph") == "tortoise-graph"
    assert canary._canonical_dist_name("tortoise-graph.") == "tortoise-graph", (
        "a name carrying trailing punctuation normalised to a DIFFERENT name, so pip's "
        "wording would stop matching the package under test"
    )
    # ⚠️ This uses the ONE step that reaches the wrapper, on purpose: coupling the strip's
    # pin to an unreachable step made the assertion blame the strip for a removed tuple
    # entry (#12422 round-30 review, P3).
    assert canary._ABSENCE_NAMED.search(
        "Failed to build 'tortoise-graph.' when getting requirements to build wheel"
    ), (
        "the quoted form DOES capture trailing punctuation — the capture class includes "
        "`.` — so the strip is what keeps `tortoise-graph.` from normalising to "
        "`tortoise-graph-`; if this stops matching, the strip is dead code and the "
        "docstring says otherwise"
    )
    assert canary._install_failure_is_absence(
        "ERROR: No matching distribution found for tortoise-graph.", None, "tortoise-graph"
    ), "the punctuation-attached spelling no longer matches its own package"
    # …and the CORROBORATION-ONLY half stays corroboration: `from versions: none` names
    # nobody, so on its own it is not evidence about this artifact.
    assert not canary._install_failure_is_absence("from versions: none", None, "tortoise-graph")


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

    # ⛔ EVERY GATE step of `build` is a gate on the artifact `publish` uploads, so none of them
    # may carry the two defusers (#12422 review cycle 6, P2, MEASURED). Hand-listing one gate
    # left the other two free: `continue-on-error: true` on `Smoke-test the wheel in a clean
    # venv` (`:55`, the #1929 pack-catalog smoke) and on `sdist presence check (#1929)`
    # (`:130`) each left `78 passed`, exit 0 — the wheel/sdist still uploads, so a one-line
    # “don't let this block the release” reads green over a wheel missing its pack catalog.
    #
    # ⛔ THE CLASS IS "A STEP THAT CAN FAIL THE JOB AND MUST", not "a step". The EVIDENCE
    # uploads legitimately carry `if: always()` (a cancelled job runs no other step, so the
    # transcript would be lost) — measured, and that is why a blanket sweep over every step
    # red a CORRECT workflow on the first cut. So: every `run:` step (the bash gates), plus
    # the `dist` artifact the publishing job reads, which is asserted separately above.
    def gates(steps: list[dict]) -> list[dict]:
        return [
            step
            for step in steps
            if "run" in step
            or (
                str(step.get("uses", "")).startswith("actions/upload-artifact")
                and str((step.get("with") or {}).get("name", "")).startswith("dist")
            )
        ]

    for step in gates(build_steps):
        undefused(step, f"publish-pypi build / the `{step.get('name') or step.get('uses')}` step")

    # …and the client build job is the same one-way door: `publish-client` runs on `needs:`
    # alone, so its acceptance gate must not be swallowable either — and, for parity with
    # `publish` below, neither may its own enforcement link (#12422 round-20 review, P3:
    # the comment claimed parity while only the gate half was pinned — `if: always()` on
    # `publish-client`, or deleting its `needs:`, left this suite green. Measured bound on
    # that gap: it cannot produce a false PASS on its own, because a failed `build-client`
    # uploads no `dist-client` artifact and the defused job then reds at download-artifact;
    # the COMPOUND that would publish a broken client is caught by the `dist-client`
    # upload clause above. So this is comment-vs-pin asymmetry, closed here rather than
    # left as a claim a reader would over-trust.)
    client_steps = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"][
        "build-client"
    ]["steps"]
    for step in gates(client_steps):
        undefused(
            step, f"publish-pypi build-client / the `{step.get('name') or step.get('uses')}` step"
        )

    publish_client_job = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"][
        "publish-client"
    ]
    assert publish_client_job.get("needs") == "build-client", (
        "`publish-client` no longer declares `needs: build-client`, so it can run without "
        "the client acceptance gate ever having executed"
    )
    assert "if" not in publish_client_job, (
        f"`publish-client` carries `if: {publish_client_job['if']!r}` — a condition on the "
        f"publishing job can let it run when the gate's job failed"
    )

    canary_steps = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())["jobs"][
        "canary"
    ]["steps"]
    canary_step = [step for step in canary_steps if step.get("id") == "canary"]
    assert len(canary_step) == 1, "the `id: canary` step is missing"
    undefused(canary_step[0], "release-artifact-canary / the `id: canary` step")

    # ── THE ENFORCEMENT LINK, not just the gate step (#12422 review cycle 5, P1).
    # `undefused()` on the GATE says the gate can fail its job; it says nothing about the
    # two things that must then STOP the upload. Both were free, MEASURED green:
    #   * `if: always()` on the `dist` upload-artifact → a rejected wheel is still uploaded;
    #   * `if: always()` on `publish`, or deleting `publish`'s `needs: build` → it publishes
    #     whatever it finds in `dist/`, gate or no gate.
    # Together those turn a red pre-upload gate into an IRREVERSIBLE upload with the whole
    # suite green (`444 passed`, exit 0 measured) — the exact outcome this pin's docstring
    # says the gate exists to prevent, reached past the gate instead of through it.
    publish_job = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"]["publish"]
    assert publish_job.get("needs") == "build", (
        "`publish` no longer declares `needs: build`, so it can run without the pre-upload "
        "gate ever having executed"
    )
    assert "if" not in publish_job, (
        f"`publish` carries `if: {publish_job['if']!r}` — a condition on the publishing job "
        f"can let it run when the gate's job failed"
    )

    dist_upload = [
        step
        for step in build_steps
        if str(step.get("uses", "")).startswith("actions/upload-artifact")
        and str((step.get("with") or {}).get("name")) == "dist"
    ]
    assert len(dist_upload) == 1, "the `dist` artifact upload is missing from the build job"
    undefused(dist_upload[0], "publish-pypi build / the `dist` upload (the wheel `publish` reads)")

    # ── THE PUBLISH ACTIONS THEMSELVES (#12422 round-20 review, P3). `gates()` sweeps
    # `run:` steps and the `dist*` uploads, so it stops one job short of the two steps whose
    # failure IS the release not happening: `continue-on-error: true` on
    # `pypa/gh-action-pypi-publish` left all 72 green, in BOTH jobs — the same defuser class,
    # one job up. `if:` is deliberately NOT asserted here: a step-level `if:` on a publish
    # action is not load-bearing while `needs:` and the job's own `if` are pinned above.
    all_jobs = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"]
    publish_actions = [
        (job_name, step)
        for job_name, job in all_jobs.items()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("pypa/gh-action-pypi-publish")
    ]
    assert {job for job, _ in publish_actions} == {"publish", "publish-client"}, (
        "the upload steps are no longer where this pin looks for them: "
        f"{sorted({job for job, _ in publish_actions})}"
    )
    for job_name, step in publish_actions:
        assert "continue-on-error" not in step, (
            f"{job_name} / the upload step carries `continue-on-error: "
            f"{step['continue-on-error']!r}` — the step whose failure IS the release not "
            f"happening is now swallowable, so a failed upload reads as a green run with "
            f"nothing published"
        )

    # ── THE ALERT'S OWN TWO LEGS (#12422 review cycle 5, P2). The filing and the close are
    # the workflow's durable record; `continue-on-error: true` on either makes a failed
    # filing read as a green run while the issue this workflow exists to file is never
    # filed. MEASURED green before this clause (`125 passed` / `71 passed`).
    # ⛔ `continue-on-error` ONLY, not `undefused()`: these legs legitimately carry an `if:`
    # — that is WHEN the alert fires, it is load-bearing, and it is already pinned by its
    # exact expression elsewhere in this file. Forbidding `if` here would red a correct
    # workflow (measured on the first cut of this clause). The transcript fetch's
    # `continue-on-error: true` is DELIBERATE and is not asserted here.
    report_steps = yaml.safe_load((workflows / "release-artifact-canary.yml").read_text())["jobs"][
        "report"
    ]["steps"]
    for label, needle in (
        ("the FILING leg", "File or refresh the alert issue"),
        ("the CLOSE leg", "Close the alert issue on a green run"),
    ):
        matched = [step for step in report_steps if needle in str(step.get("name") or "")]
        assert len(matched) == 1, f"{label} ({needle!r}) is missing from the report job"
        assert "continue-on-error" not in matched[0], (
            f"{label} carries `continue-on-error: {matched[0]['continue-on-error']!r}` — a "
            f"filing failure (no GH_TOKEN, no `gh`, a substrate refusal) is then swallowed, "
            f"`report` reads SUCCESS, and the issue this workflow exists to file is never "
            f"filed while the run looks green"
        )


def test_the_publish_time_smoke_resolves_EXACTLY_ONE_wheel_before_it_runs():
    """`--wheel "$(ls dist/*.whl)"` tests the PREVIOUSLY PUBLISHED release (#12363 review E3).

    The pre-upload gate must read the wheel THIS RUN built. Bash does not abort on a failing
    substitution inside an argument, so an empty `dist/*.whl` glob reaches the tool as an
    empty string; the tool refuses that (pinned next door), but the step must ALSO fail
    closed on its own terms, with the BUILD defect named — otherwise a `whl=$(ls dist/*.whl
    | head -1)`-shaped edit silently tests an arbitrary wheel and reports PASS over bytes
    that are not the ones about to be uploaded.

    ⛔ Why this pin exists at all (#12422 review, P1, MEASURED): the resolution block was
    correct and NOTHING read it. Reverting it to the exact pre-fix `whl="$(ls dist/*.whl)"`
    left `113 passed`, exit 0 — a guard the PR body presented as covered, whose removal the
    whole suite watched happen. That is the agent-infra#3442 "assertion whose value cannot
    vary" defect. The clauses below are asserted separately — `(i)` the glob and its arity,
    `(i-a)` the empty-glob half of the same `if`, `(i-b)` the no-pipeline rule, `(ii)` the
    refusal, `(iii)` the resolved variable — and each names a half that a plausible "tidy"
    would drop. `(i-a)` is the newest: a count-only clause leaves the empty glob free
    (#12422 round-18 review P3, MEASURED: dropping `[ -z "$whl" ] ||` left this pin green
    before it existed).
    """
    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"
    build_steps = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"]["build"][
        "steps"
    ]
    preupload = [
        step for step in build_steps if "published_artifact_canary.py" in str(step.get("run") or "")
    ]
    assert len(preupload) == 1, "the pre-upload gate step is missing"
    # ⛔ COMMENTS COME OFF FIRST. The step's own comment quotes the defect it documents
    # (`` `--wheel "$(ls dist/*.whl)"` ``), so scanning the raw `run:` text makes clause (iii)
    # below red on the DOCUMENTATION instead of on the code — measured on the first cut of
    # this pin. Assertions read executable lines only.
    code = "\n".join(
        line for line in str(preupload[0]["run"]).splitlines() if not line.lstrip().startswith("#")
    )
    run = code

    # (i) the glob's ARITY is checked, not assumed: a count guard has to exist.
    assert "dist/*.whl" in run, "the step no longer resolves the built wheel at all"
    assert "-ne 1" in run, (
        "the pre-upload step no longer asserts that the glob matched EXACTLY ONE wheel — with "
        "no count guard `--wheel` receives whatever the glob produced, including nothing"
    )

    # ⛔ (i-a) the EMPTY-GLOB half of the same `if`, asserted on its own (#12422 round-18
    # review P3, MEASURED). `-ne 1` cannot see it: `printf '%s\n' "" | wc -l` is 1, so a
    # dist/ with no wheel passes the arity guard and reaches `--wheel ""` — which the TOOL
    # refuses (exit 2, "was passed with an empty path"), i.e. bounded and still blocked, but
    # the diagnosis a human reads becomes the tool's refusal instead of the BUILD defect the
    # step exists to name. Deleting `[ -z "$whl" ] ||` left this pin green before this clause.
    assert '-z "$whl"' in run, (
        "the pre-upload step's guard no longer names the EMPTY glob on its own — `-ne 1` "
        "cannot see an empty `whl` (printf of an empty string is one line), so a dist/ with "
        'no wheel reaches `--wheel ""` and only the tool refusal stops it'
    )

    # ⛔ (i-b) AND THE GUARD MUST NOT BE DEFANGABLE BY A PIPELINE (#12422 review cycle 3,
    # P1, MEASURED). The first cut of this pin asserted only `"-ne 1" in run`, and
    # `whl=$(ls dist/*.whl | head -1)` keeps that literal while making it VACUOUS: the
    # pipeline always emits exactly one line, so `wc -l` is always 1 and the guard can never
    # fire. Measured: that edit left `61 passed`, exit 0 — the pin stayed green under the
    # very edit its own docstring names as the threat (`head -1`smuggles an arbitrary wheel
    # into `--wheel` while `publish` uploads both). A count taken from a possibly-truncated
    # variable cannot police arity, so the glob line must be pipeline-free.
    glob_lines = [line for line in run.splitlines() if "dist/*.whl" in line]
    # A READER GUARD, not coverage: it is implied by the `assert "dist/*.whl" in run` above,
    # so it can never attribute a failure alone. The behavioural clause at the end of this
    # file is what closes the class.
    assert glob_lines, "the step no longer mentions the wheel glob"
    for line in glob_lines:
        # ⛔ A PIPE, not shell's `||`: `whl=$(ls dist/*.whl 2>/dev/null) || whl=""` is the
        # CORRECT line and carries a `|`. Only a single, unpaired `|` is a pipeline (measured
        # on the first cut of this clause, which red on the correct code).
        assert not re.search(r"(?<!\|)\|(?!\|)", line), (
            f"the wheel-glob line takes a pipeline ({line.strip()!r}) — a truncating pipe "
            f"(| head -1) makes the `-ne 1` count guard vacuous while leaving it in the "
            f"text, so exactly one arbitrary wheel is smoked and every wheel is uploaded"
        )

    # (ii) it is a REFUSAL, not a warning: the step must exit non-zero.
    assert re.search(r"^\s*exit 1\s*$", run, re.MULTILINE), (
        "the pre-upload step's count guard does not `exit 1`, so a zero-or-multi wheel dist/ "
        "warns and the smoke runs anyway"
    )

    # (iii) `--wheel` receives the RESOLVED VARIABLE, never an inline substitution — the
    #       inline form is the defect itself.
    assert re.search(r'--wheel\s+"?\$\{?whl\}?"?', run), (
        "`--wheel` is not handed the resolved variable $whl"
    )
    assert not re.search(r'--wheel\s+"?\$\(', run), (
        "`--wheel` takes an inline `$(...)` substitution: an empty `dist/*.whl` glob collapses "
        "it to an empty argument and the smoke tests a DIFFERENT release than the one uploaded"
    )


def test_the_pre_upload_gate_step_EXITS_with_the_tool_VERDICT(tmp_path):
    """The step's exit must BE the tool's verdict — not a shell spelling of it (#12422 review cycle 6, P1).

    `undefused()` forbids the two workflow KEYS, and that is not the same property. The run
    block's last command is the tool invocation, so appending `|| true` — the idiomatic
    "don't let the smoke block the release" edit, and byte-for-byte the same outcome as
    `continue-on-error: true` — turns a FAIL verdict into exit 0. MEASURED: with `|| true`
    appended, the whole suite stayed green (`78 passed`, exit 0); `build` greens, the `dist`
    upload and `publish` (both pinned by the cycle-5 commit) then run, and an uninstallable
    wheel is uploaded irreversibly.

    ⛔ EXECUTED, not grepped — and it closes `|| true`, `; true`, a trailing `exit 0` and a
    `set +e` that is followed by another command, in ONE row, instead of enumerating shell
    spellings a future edit can always avoid. Same technique as
    `test_an_INTERPRETER_level_failure_…` next door: the step's own `run:` block, a stub
    interpreter, and the observed exit code.

    ⚠️ STATED BOUND (#12422 round-20 review, P3): `set +e` ALONE, with the tool invocation
    still last, is NOT caught here and does not need to be — with the verdict as the block's
    last command, `set +e` cannot swallow it, and this harness runs the block under
    `bash -c` without `-e`, so errexit is not in play either way. The dangerous form is
    `set +e` PLUS a trailing command that returns 0, which is the row above. This paragraph
    used to claim the pin closed `set +e` flatly, which measured FALSE (`72 passed` with
    `set +e` inserted after `set -o pipefail`).
    """
    import subprocess

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "publish-pypi.yml").read_text()
    )
    run = next(
        step["run"]
        for step in workflow["jobs"]["build"]["steps"]
        if "published_artifact_canary.py" in str(step.get("run") or "")
    )

    def execute(tool_exit: int) -> int:
        bindir = tmp_path / f"bin{tool_exit}"
        bindir.mkdir(exist_ok=True)
        stub = bindir / "python"
        # The step shells out to `python tools/…`; the stub IS that command, so its exit is
        # exactly the tool's verdict — whatever the block does with it afterwards.
        stub.write_text(f"#!/bin/sh\nexit {tool_exit}\n")
        stub.chmod(0o755)
        cwd = tmp_path / f"cwd{tool_exit}"
        (cwd / "dist").mkdir(parents=True)
        (cwd / "dist" / "pkg-0.1-py3-none-any.whl").write_bytes(b"")
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md"),
        }
        return subprocess.run(
            ["bash", "-c", run], cwd=str(cwd), env=env, capture_output=True, text=True
        ).returncode

    assert execute(0) == 0, "a PASSING wheel failed the pre-upload step (a false block)"
    assert execute(1) != 0, (
        "the pre-upload step exited 0 on a FAIL verdict — `build` greens, `publish` runs, and "
        "an uninstallable wheel is uploaded irreversibly. A shell-level swallow (`|| true`, "
        "`; true`, a trailing `exit 0`) is enough; the workflow KEY assertions cannot see it"
    )
    assert execute(2) != 0, "the pre-upload step swallowed a detector-fault exit 2"


def test_the_wheel_arity_guard_REFUSES_zero_and_two_wheels_by_RUNNING_it(tmp_path):
    """Execute the step's own resolution block, rather than grepping it (#12422 review cycle 4, P2).

    Every text assertion above is LINE-SCOPED to a line containing `dist/*.whl`, so the class
    it names survives two spellings that keep `dist/*.whl`, `-ne 1` and `exit 1` in the text
    while making `wc -l` always 1 — both MEASURED GREEN against the text-only cut:

        whl=${whl%%$'\n'*}                                    # no pipe at all
        whl=$(printf '%s\n' "$whl" | cut -d' ' -f1)             # a pipe, on an unscanned line

    Shell text cannot be pinned against "any transformation of the glob output"; the surface
    is what the block DOES. So the block is lifted out of the workflow and RUN under `bash -e`
    in a temp dir seeded with 0, 1 and 2 fake wheels, and the arity verdict is observed.
    """
    import subprocess

    import yaml

    workflows = REPO_ROOT / ".github" / "workflows"
    build_steps = yaml.safe_load((workflows / "publish-pypi.yml").read_text())["jobs"]["build"][
        "steps"
    ]
    preupload = [
        step for step in build_steps if "published_artifact_canary.py" in str(step.get("run") or "")
    ]
    assert len(preupload) == 1, "the pre-upload gate step is missing"
    lines = str(preupload[0]["run"]).splitlines()

    # The resolution block: from the `whl=` assignment through its OWN closing `fi`. Lifted
    # verbatim, so whatever the step actually executes is what this test executes.
    start = next(
        i
        for i, line in enumerate(lines)
        if "dist/*.whl" in line and not line.lstrip().startswith("#")
    )
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "fi")
    block = "\n".join(lines[start : end + 1])

    def run_block(wheel_count: int) -> subprocess.CompletedProcess:
        case = tmp_path / f"wheels{wheel_count}"
        (case / "dist").mkdir(parents=True)
        for i in range(wheel_count):
            # Empty files are enough: the block resolves NAMES. `publish` is not executed.
            (case / "dist" / f"pkg-0.{i}-py3-none-any.whl").write_bytes(b"")
        script = case / "resolve.sh"
        script.write_text(
            "set -o pipefail\n" + block + '\nprintf "WHL=%s\\n" "$whl"\n',
            encoding="utf-8",
        )
        return subprocess.run(
            ["bash", "-e", str(script)], cwd=str(case), capture_output=True, text=True
        )

    zero = run_block(0)
    assert zero.returncode != 0, (
        "the guard PASSED with an empty dist/ — `--wheel` would receive an empty string and the "
        f"publish step would smoke a different release. stdout={zero.stdout!r}"
    )
    assert "expected exactly one wheel" in (zero.stdout + zero.stderr), (
        "the empty-dist refusal does not NAME the build defect it found: "
        f"{zero.stdout + zero.stderr!r}"
    )

    two = run_block(2)
    assert two.returncode != 0, (
        "the guard PASSED with TWO wheels in dist/ — one arbitrary wheel is smoked while every "
        f"wheel is uploaded. stdout={two.stdout!r}"
    )

    one = run_block(1)
    assert one.returncode == 0, (
        f"the guard REFUSED a single-wheel dist/ (a false block): {one.stdout + one.stderr!r}"
    )
    assert "WHL=dist/pkg-0.0-py3-none-any.whl" in one.stdout, (
        f"the resolved wheel was not handed on intact: {one.stdout!r}"
    )


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


def test_a_timeout_FLUSHES_the_recovered_output_to_the_transcript_FILE(tmp_path):
    """The timeout arm's `flush()` is the only thing that lands the recovered output (#12422 review cycle 4, P2).

    `_main` writes `log_path` ONLY when the returned transcript is empty, so on the timeout
    path the FILE this test reads is written by the handler's own `flush()` and by nothing
    else. Deleting that one call left the whole suite green (measured: `66 passed`) while the
    uploaded `publish-canary-log` artifact — the artefact the pre-upload gate exists to make
    diagnosable — stopped carrying the output the command had already produced.
    """

    class _Hangs(_Runner):
        def __call__(self, cmd, **kwargs):
            result = super().__call__(cmd, **kwargs)
            if Path(cmd[0]).name == "tortoise":
                raise subprocess.TimeoutExpired(cmd, 7, output="Collecting numpy==1.26.4\n")
            return result

    log = tmp_path / "logs" / "canary.log"
    arm, _transcript = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Hangs({}, creates_db_for=None),
        transcript_path=log,
    )
    assert arm.status == canary.UNKNOWN, arm
    assert log.is_file(), (
        "no transcript FILE survived a timeout — the handler's flush() is the only writer on "
        "this path, so the uploaded artifact would be empty in exactly the state the "
        "diagnosis is worth most"
    )
    text = log.read_text()
    assert "Collecting numpy==1.26.4" in text, (
        f"the recovered output is not in the transcript FILE: {text!r}"
    )
    assert "killed at the 7s budget" in text, f"the timeout marker is absent: {text!r}"


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


def test_a_timeout_KEEPS_the_output_the_command_had_already_produced(tmp_path):
    """The hung-command state is the one the alarm most needs to explain, and it used to
    arrive as a bare `$ …` line — i.e. as silence (round 17).

    ⛔ MEASURED (3.12.3, round 18) — the OPPOSITE of what this docstring said until now:
    `subprocess.run(..., text=True, timeout=T)` DOES attach the killed child's output on
    POSIX, as **bytes** (`_check_timeout` joins the raw sequences), which is why the
    handler decodes it. There is no `_communicate_runner` and no drain: that runner was
    added on the false premise above and DELETED, because its post-kill `communicate()`
    reads to EOF — which never arrives while a grandchild inherits the pipe — so the
    per-command budget could be defeated (measured: a 2 s budget returned after 25 s, and
    never at all with a `start_new_session` daemon).

    A reader who trusts the old text removes the `isinstance(stream, bytes)` decode and
    gets `TypeError: expected str instance, bytes found` at `runner=subprocess.run`, or
    re-adds the drain and re-opens the hang. That is why the stale claim is corrected here
    rather than left as history.
    """

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd, kwargs.get("timeout", 1), output="Collecting numpy\nDownloading numpy-2.1.0.whl\n"
        )

    arm, transcript = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=7.0,
        runner=boom,
    )
    assert arm.status == canary.UNKNOWN
    assert "Collecting numpy" in transcript, transcript
    assert "killed at the 7.0s budget" in transcript, transcript
    assert "the output above is what it produced first" in transcript, transcript


def test_a_timeout_with_NO_recoverable_output_SAYS_so_instead_of_implying_silence(tmp_path):
    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    _arm, transcript = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=boom,
    )
    assert "no output was recovered" in transcript, transcript
    assert "the output above is what it produced first" not in transcript, transcript


def test_a_timeout_with_BYTES_output_is_DECODED_not_crashed(tmp_path):
    """`runner=subprocess.run` is the documented seam, and it attaches the killed command's
    output as BYTES — even with `text=True` (`_check_timeout` joins the raw sequences).

    ⛔ Measured on 3.12.3 (#12363 round 18): the first cut of the timeout handler appended
    `exc.stdout` straight into `"\n".join(transcript)`, so exactly this shape raised
    `TypeError: sequence item 1: expected str instance, bytes found` — the arm and the
    recovered evidence were both lost and `main()`'s backstop turned a timeout into exit 2.
    The str case above cannot see that; this one does.
    """

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd, kwargs.get("timeout", 1), output=b"Collecting numpy\nERROR: disk full\n"
        )

    arm, transcript = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=5,
        runner=boom,
    )
    assert arm.status == canary.UNKNOWN
    assert "Collecting numpy" in transcript, transcript
    assert "ERROR: disk full" in transcript, transcript
    assert "the output above is what it produced first" in transcript, transcript


def test_the_DEFAULT_runner_is_plain_subprocess_run_and_keeps_the_output(tmp_path):
    """Two halves, both load-bearing (#12363 rounds 17 and 18).

    1. The DEFAULT must stay `subprocess.run`. A custom `Popen`-based drain was tried and
       reverted: it can only recover the tail by calling `communicate()` a second time,
       and that reads to EOF — which never arrives while a surviving grandchild (the
       FleetDB/pip build-backend daemon that INHERITS the pipe) still holds it. Measured:
       a 2 s budget returned after **25 s**, and with a `start_new_session` daemon the
       call never returned at all — so `--timeout` becomes unbounded and the handler (and
       its marker, and its flush) never runs. The premise it was built on was also false:
       `run()` does attach the output, as bytes.
    2. A REAL killed child's output IS recovered, through that default.
    """
    import inspect

    default = inspect.signature(canary.install_and_run).parameters["runner"].default
    assert default is subprocess.run, (
        f"install_and_run defaults to {default!r}: a re-implemented runner re-opens the "
        "unbounded-drain hazard and its output recovery is already provided by run()"
    )
    # And the recovery is real: run() attaches the killed child's bytes, which the handler
    # decodes, so the diagnosis survives the state where it is worth most.
    with pytest.raises(subprocess.TimeoutExpired) as raised:
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import time; print('Collecting numpy', flush=True); time.sleep(60)",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=1,
        )
    assert b"Collecting numpy" in (raised.value.stdout or b""), raised.value.stdout


def test_the_tool_imports_only_the_STANDARD_LIBRARY():
    """The canary job has NO install step on purpose (a `pip install -e .` would let the
    tool grade the tree), so one third-party import makes it die at IMPORT time — and an
    interpreter-level failure exits 1, which both consumers read as "the published
    artifact is broken — cut a release" (#12363 round 18).

    ⛔ AST + `sys.stdlib_module_names`, not a substring scan: `from yaml import safe_load`,
    `import os, yaml` and `importlib.import_module("yaml")` all defeat the literal form
    this class of pin usually takes.
    """
    import ast

    tree = ast.parse(
        (REPO_ROOT / "tools" / "published_artifact_canary.py").read_text(encoding="utf-8")
    )
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])
    third_party = sorted(name for name in imported if name not in sys.stdlib_module_names)
    assert third_party == [], (
        f"tools/published_artifact_canary.py must be stdlib-only: the canary job installs "
        f"nothing, so {third_party} would kill the tool at import time and its exit 1 would "
        "be published as a release finding"
    )
    # The dynamic spellings the static import walk cannot see, walked explicitly: a
    # literal target is classified, a NON-literal one is reported rather than cleared.
    dynamic: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name not in {"__import__", "import_module"}:
            continue
        target = node.args[0] if node.args else None
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            dynamic.append(target.value.split(".")[0])
        else:
            dynamic.append(f"<unverifiable: {ast.dump(node)[:60]}>")
    third_party_dynamic = sorted(name for name in dynamic if name not in sys.stdlib_module_names)
    assert third_party_dynamic == [], (
        f"a dynamic import defeats the stdlib-only pin above: {third_party_dynamic}"
    )


def test_an_INTERPRETER_level_failure_is_not_published_as_a_release_finding(tmp_path):
    """Runs the canary step's OWN shell, twice, with a stub interpreter.

    exit 1 is the irreversible instruction ("the published artifact is broken — cut a
    release"), and BOTH consumers act on it. An interpreter-level failure exits 1 too — a
    SyntaxError at import, an uncaught exception outside `main()`'s backstop, a
    third-party import this job has no install step for — so the step must publish 1 only
    when the tool's own report says FAIL (#12363 round 18). Without that, adding one
    non-stdlib import orders an upload.

    ⛔ Executed, not grepped: a presence pin on the `if` would stay green while the guard
    compared the wrong variable or read the wrong path.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "release-artifact-canary.yml").read_text()
    )
    run = next(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "published_artifact_canary" in str(step.get("run", ""))
    )

    def execute(stub_body: str) -> tuple[int, str]:
        bindir = tmp_path / "bin"
        bindir.mkdir(exist_ok=True)
        python_stub = bindir / "python"
        # ⛔ The step PARSES the verdict with `python -c` (#12422 review cycle 5, P1), so this
        # stub must behave like the INTERPRETER for `-c`/`-` and like the CANARY only for a
        # script path. Without the delegation the stub would answer the parse too, and the
        # harness would pass for a reason that does not exist on a real runner.
        python_stub.write_text(
            f'#!/bin/sh\ncase "$1" in\n  -c|-) exec "$REAL_PYTHON" "$@" ;;\nesac\n{stub_body}\n'
        )
        python_stub.chmod(0o755)
        runner_temp = tmp_path / "runner"
        runner_temp.mkdir(exist_ok=True)
        out = tmp_path / "github_output"
        out.write_text("")
        summary = tmp_path / "summary.md"
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "REAL_PYTHON": sys.executable,
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(out),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        completed = subprocess.run(
            ["bash", "-c", run], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True
        )
        return completed.returncode, out.read_text()

    # (a) the interpreter dies before the tool can report: nothing is published.
    rc, output = execute("exit 1")
    assert rc == 2, "an interpreter-level crash was published as a RELEASE finding"
    assert "exit_code=2" in output, output

    # (b) the tool's own report says FAIL: 1 is published, which is the whole point.
    rc, output = execute('printf \'{"verdict": "FAIL", "exit_code": 1}\n\'; exit 1')
    assert rc == 1, "a genuine FAIL was downgraded to a detector fault"

    # (c) ⛔ THE VERDICT IS A VALUE, NOT A FORMATTING (#12422 review cycle 5, P1, MEASURED).
    # The step used to `grep '"verdict": "FAIL"'`, a literal that exists only because
    # `json.dumps(indent=2)` writes `": "` after the key. A compact payload — the obvious
    # "make the JSON smaller" tidy — therefore reported exit 2 for a genuine FAIL: the alarm
    # telling a human NOT to cut a release about a broken, uninstallable artifact. Both
    # spellings are the same report and must publish the same 1.
    rc, output = execute('printf \'{"verdict":"FAIL","exit_code":1}\n\'; exit 1')
    assert rc == 1, (
        "a COMPACT FAIL payload was downgraded to a detector fault — the classifier is "
        "keyed to json.dumps' formatting instead of to the verdict's value"
    )
    assert "exit_code=1" in output, output

    # (d) the other direction: a report that does NOT say FAIL never orders an upload, even
    #     though the interpreter also exited 1.
    rc, output = execute('printf \'{"verdict":"UNKNOWN","exit_code":2}\n\'; exit 1')
    assert rc == 2, "an UNKNOWN (or absent) verdict was published as a RELEASE finding"
    assert "exit_code=2" in output, output


def test_the_pip_failure_detail_is_the_LAST_stderr_LINE_not_a_list_repr(tmp_path):
    """`splitlines()[-1:]` interpolates a one-element LIST, so the arm detail — rendered
    verbatim in the step summary and the `--json` payload — read
    `['ERROR: something weird']` (#12363 round 17). ⛔ NOT in the alert issue's body:
    `published-artifact-alert.sh` builds that body from `$why` + `tail -n 40` of the
    TRANSCRIPT, so the detail reaches a human through the transcript's own pip output
    (measured, #12422 round-18 review P3)."""
    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=10,
        runner=_Runner(
            {
                "pip": _completed(
                    ["pip", "install"], 1, "", "a warning line\nERROR: something weird\n"
                )
            },
            creates_db_for=None,
        ),
    )
    assert arm.status == canary.UNKNOWN, arm
    assert "ERROR: something weird" in arm.detail, arm.detail
    assert "a warning line" not in arm.detail, arm.detail
    assert "['" not in arm.detail and "']" not in arm.detail, arm.detail


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
    # ⛔ AND THE TAIL'S CLASSIFICATION BELONGS TO THE ARM THAT ESTABLISHED THE CLAIM (#12589 review
    # round 13, P3): "This is a code defect" was attached to every branch, including the one that
    # says it could not attribute the failure. A wrong `named_it` must not be able to hide here.
    assert "re-dispatch this publish" in summary, summary
    assert "wheel-mode wiring problem" not in summary, summary
    # ⛔ AND THE CLAUSE THAT KEEPS A PRE-PUBLISH RED FROM READING AS A RELEASE EVENT (#12589 review
    # round 16, P3): deleting "Nothing has been uploaded." left the suite green, and that sentence
    # is the only thing distinguishing this tail from the index-mode "cut a release" tail.
    assert "Nothing has been uploaded." in summary, summary

    # ⛔ AND A GATED ARM THAT IS NOT THE WHEEL'S COMMAND MAY NOT INHERIT THE CLAIM (#12589 review
    # round 12, P3). The pre-fix tail published "fails its documented first command" on `else:`,
    # so any arm added later would be described as the built wheel failing a command it never ran.
    foreign = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm("documented-command", canary.FAIL, "the README example exits 1"),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    foreign_summary = canary.render_summary(foreign)
    assert "fails its documented first command" not in foreign_summary, foreign_summary
    assert "documented-command" in foreign_summary, foreign_summary
    assert "is NOT judged by this summary" in foreign_summary, foreign_summary
    # ⛔ THE FALLBACK'S OWN SENTENCE, not a substring the TABLE row already contains (#12589 review
    # round 15, P3): `"documented-command" in summary` was satisfied by the table, so replacing the
    # arm name in the sentence with "some arm" — or deleting the detail — stayed green.
    assert (
        "the failing arm is `documented-command` (the README example exits 1)"
    ) in foreign_summary, foreign_summary
    # ...and the clause that limits what that arm establishes: rewriting it to "failed to install"
    # would put the wheel's claim back into a sentence built for a foreign arm (#12589 round 17).
    assert (
        "that arm does not establish that the built wheel fails its documented command"
    ) in foreign_summary, foreign_summary
    # ...and the branch that could not attribute the failure may not CLASSIFY it either: no
    # "re-dispatch this publish" instruction (that sentence carries the code-defect verdict), and
    # the honest alternative instead.
    assert "re-dispatch this publish" not in foreign_summary, foreign_summary
    assert "wheel-mode wiring problem" in foreign_summary, foreign_summary


def test_the_FAIL_arm_refuses_to_print_a_count_it_could_not_read(monkeypatch):
    """⛔ THE GUARD'S FAIL-CLOSED DIRECTION WAS UNPINNED (#12589 review round 15, P3).

    `release_files` cannot return `None` on the client FAIL branch, which is why the `RuntimeError`
    is unreachable — but that is exactly why replacing the `raise` with `files = []` left all 89
    tests green. That mutant makes the arm print "0 file(s) on 0.3.0, none usable" and exit 1:
    a release order manufactured from a count nobody read, i.e. the defect this issue removes. The
    seam is narrow and deliberate: a patched `release_files` stands in for the future refactor the
    guard exists to survive.
    """
    import pytest

    def _cannot_read(index_doc, version):
        return None

    monkeypatch.setattr(canary, "release_files", _cannot_read)
    with pytest.raises(RuntimeError, match="COMPLETE reading"):
        _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": []}})


GOLDEN_GREEN = """## Published-artifact canary (#12363)

**Verdict: PASS**

| arm | gated | status | reading |
|---|---|---|---|
| `engine-index` | yes | PASS | tortoise-graph latest on the index: 0.2.0 |
| `engine-init` | yes | PASS | tortoise init succeeds |
| `client-published` | yes | PASS | tortoise-client resolves on the index (advertised latest 0.3.0 is installable; installable: 0.3.0) |
| `release-age` | no | — | tortoise-graph 0.2.0 uploaded 97d ago |
| `tree-version` | no | — | pyproject.toml version 0.2.0 vs published 0.2.0 — the tree's version (no bump pending) |
"""

GOLDEN_INDEX_FAIL = """## Published-artifact canary (#12363)

**Verdict: FAIL**

| arm | gated | status | reading |
|---|---|---|---|
| `engine-index` | yes | PASS | tortoise-graph latest on the index: 0.2.0 |
| `engine-init` | yes | PASS | tortoise init succeeds |
| `client-published` | yes | FAIL | the tortoise-client index lists NO installable file in ANY release (0 file(s) on 0.3.0, none usable) — README.md documents `pip install tortoise-client`, which cannot resolve |
| `release-age` | no | — | tortoise-graph 0.2.0 uploaded 97d ago |
| `tree-version` | no | — | pyproject.toml version 0.2.0 vs published 0.2.0 — the tree carries the same version — the failing arm above is NOT the engine build, so this version's engine artifact is not implicated; read the failing row before deciding what a bump would fix |

The artifact on the index is broken for the documented steps. This is **not** fixed by a code change in this repo: it needs a release (version bump → tag → `publish-pypi.yml`) by whoever holds the PyPI credentials. See #12363.
"""

GOLDEN_WHEEL_FAIL = """## Published-artifact canary (#12363)

**Verdict: FAIL**

| arm | gated | status | reading |
|---|---|---|---|
| `engine-index` | no | — | not consulted (wheel mode: testing the locally built w.whl) |
| `client-published` | no | — | not consulted (wheel mode) |
| `engine-init` | yes | FAIL | `tortoise init` exits 1 |

The BUILT wheel fails its documented first command. Nothing has been uploaded. Fix the tree and re-dispatch this publish: this is a code defect, not a release-owed finding. See #12363.
"""

GOLDEN_UNKNOWN = """## Published-artifact canary (#12363)

**Verdict: CANNOT RUN**

| arm | gated | status | reading |
|---|---|---|---|
| `engine-index` | yes | PASS | tortoise-graph latest on the index: 0.2.0 |
| `engine-init` | yes | PASS | tortoise init succeeds |
| `client-published` | yes | CANNOT RUN | could not read a file listing out of the tortoise-client release map (releases-unreadable) — a version value that is not an array, an entry that is not an object, or a file entry with no boolean `yanked` / no non-empty string `filename`. PyPI documents each version's value as an ARRAY of file objects, so this is a shape this client cannot parse, and whether `pip install tortoise-client` resolves is UNKNOWN, not false. This is NOT evidence the artifact is broken and is NOT a reason to cut a release |
| `release-age` | no | — | tortoise-graph 0.2.0 uploaded 97d ago |
| `tree-version` | no | — | pyproject.toml version 0.2.0 vs published 0.2.0 — the tree's version (no bump pending) |

The canary could not READ something it needs, or could not exercise the artifact: the row marked CANNOT RUN names which (`client-published`). Reported as RED on purpose: a detector that cannot run must never look like a pass.
"""


def _GOLDEN_ENGINE():
    return {
        "info": {"version": "0.2.0"},
        "releases": {
            "0.2.0": [
                {
                    "filename": "tortoise_graph-0.2.0-py3-none-any.whl",
                    "yanked": False,
                    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
                }
            ]
        },
    }


_GOLDEN_CLIENT_GOOD = {
    "filename": "tortoise_client-0.3.0-py3-none-any.whl",
    "yanked": False,
    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
}


def _GREEN_REPORT():
    return canary.build_report(
        engine_doc=_GOLDEN_ENGINE(),
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_GOLDEN_CLIENT_GOOD]}},
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.OK, "tortoise init succeeds"),
        now=NOW,
    )


def _INDEX_FAIL_REPORT():
    return canary.build_report(
        engine_doc=_GOLDEN_ENGINE(),
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": []}},
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.OK, "tortoise init succeeds"),
        now=NOW,
    )


def _WHEEL_FAIL_REPORT():
    return canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "`tortoise init` exits 1"),
        now=NOW,
        wheel_path=Path("dist/w.whl"),
    )


def _UNKNOWN_REPORT():
    return canary.build_report(
        engine_doc=_GOLDEN_ENGINE(),
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": {}}},
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.OK, "tortoise init succeeds"),
        now=NOW,
    )


GOLDEN_INFO_UNREADABLE_DETAIL = (
    "could not read the index metadata for tortoise-client — the document's `info` is PRESENT but "
    "is not an object (info-unreadable), so the version metadata this arm reads is UNPARSEABLE "
    "(any version the document may carry was never obtained). That is a NON-READING: it says "
    "nothing about whether the distribution resolves, and it is NOT a reason to cut a release"
)

GOLDEN_VERSION_UNREADABLE_DETAIL = (
    "could not read the version the index serves for tortoise-client — `info.version` is PRESENT "
    "but is not a string (version-unreadable), so the metadata this arm reads is UNPARSEABLE. That "
    "is a NON-READING: it says nothing about whether the distribution resolves, and it is NOT a "
    "reason to cut a release"
)


def test_the_ENGINE_side_non_reading_details_name_the_engine_package():
    """⛔ THE SAME SENTENCE ON THE OTHER ARM (#12589 review round 20, P3).

    Both non-reading details interpolate `{package}`, and the verbatim pins above cover the CLIENT
    text only; the engine side was checked by token alone. Replacing `{package}` with
    `{CLIENT_PACKAGE}` there made an `engine-index` CANNOT RUN say "could not read the index
    metadata for tortoise-client" — with the suite green.
    """
    info_side = canary.build_report(
        engine_doc={"info": 0},
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    ).get("engine-index")
    assert info_side.status == canary.UNKNOWN, info_side.detail
    assert (
        "could not read the index metadata for tortoise-graph — the document's `info` is PRESENT"
    ) in info_side.detail, info_side.detail
    assert "tortoise-client" not in info_side.detail, info_side.detail

    version_side = canary.build_report(
        engine_doc={"info": {"version": 3}},
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    ).get("engine-index")
    assert version_side.status == canary.UNKNOWN, version_side.detail
    assert (
        "could not read the version the index serves for tortoise-graph — `info.version` is PRESENT"
    ) in version_side.detail, version_side.detail
    assert "tortoise-client" not in version_side.detail, version_side.detail


def test_the_two_info_and_version_non_reading_details_are_PINNED_VERBATIM():
    """⛔ A NON-READING'S SCRAP OF TEXT IS WHERE A NEGATIVE FINDING SNEAKS IN (#12589 round 18, P2).

    These two arms were the last non-reading details guarded only by a PHRASE DENY-LIST
    (`assert "carries no" not in detail`). A deny-list cannot stop a claim worded differently: on a
    /tmp copy, appending ", so the documented install is not resolvable" after "That is a
    NON-READING" — an artifact-negative claim made by a non-reading, and self-contradictory inside
    one sentence — kept the suite green. Verbatim, as for their two siblings.
    """
    info_detail = (
        _client_report({"info": "x", "releases": {"0.3.0": [_CLIENT_GOOD]}})
        .get("client-published")
        .detail
    )
    assert info_detail == GOLDEN_INFO_UNREADABLE_DETAIL, info_detail

    version_detail = (
        _client_report({"info": {"version": 3}, "releases": {"0.3.0": [_CLIENT_GOOD]}})
        .get("client-published")
        .detail
    )
    assert version_detail == GOLDEN_VERSION_UNREADABLE_DETAIL, version_detail


GOLDEN_RELEASES_ABSENT_DETAIL = (
    "could not read the tortoise-client release map — the index document carries no usable "
    "`releases` map (the key is absent, null, or not an object). PyPI documents `releases` as "
    "DEPRECATED and may remove it (releases-absent), so whether `pip install tortoise-client` "
    "resolves is UNKNOWN, not false. This is NOT evidence the artifact is broken and is NOT a "
    "reason to cut a release"
)


def test_the_releases_absent_detail_is_PINNED_VERBATIM():
    """⛔ A NON-READING MAY NOT BE WORDED AS A NEGATIVE FINDING (#12589 review round 17, P2).

    The `releases-absent` detail ends "… resolves is UNKNOWN, not false". Inverting that one word
    to "FALSE" — a non-reading asserting non-resolution, the exact class this issue removes — left
    all 93 tests green, because only the sibling `releases-unreadable` arm's sentence was pinned
    (by GOLDEN_UNKNOWN). Same treatment for the other arm: the full sentence, verbatim.
    """
    detail = _client_report({"info": {"version": "0.3.0"}}).get("client-published").detail
    assert detail == GOLDEN_RELEASES_ABSENT_DETAIL, detail


def test_the_index_absent_client_detail_names_the_documented_install():
    """⛔ THE README POINTER IS WHAT MAKES A 404 A FINDING (#12589 review round 17, P3).

    The `client-published` arm reads FAIL on a 404 because README.md documents the install; the
    sentence that says so was deletable with all 93 tests green. Without it the FAIL is just "a
    package is not on the index".
    """
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=None,
        client_error="404 Not Found",
        client_state=canary.INDEX_ABSENT,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    arm = report.get("client-published")
    assert arm.status == canary.FAIL, (arm.status, arm.detail)
    assert "tortoise-client is not resolvable on the index (404 Not Found)" in arm.detail, (
        arm.detail
    )
    assert "README.md documents `pip install tortoise-client` as a supported install" in arm.detail


def test_the_index_UNREADABLE_detail_names_the_shape_it_could_not_read():
    """⛔ AND ITS SIBLING (#12589 review round 17, P3): the unreadable-index detail named the
    distribution's presence in two clauses; both were deletable with the suite green (the token and
    the trailing "NOT a reason to cut a release" were pinned, the body was not)."""
    report = canary.build_report(
        engine_doc=None,
        engine_error="timed out",
        engine_state=canary.INDEX_UNREADABLE,
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    arm = report.get("engine-index")
    assert arm.status == canary.UNKNOWN, arm.detail
    assert "could not read the tortoise-graph index metadata (timed out)" in arm.detail, arm.detail
    assert "the distribution's presence is UNKNOWN, not absent" in arm.detail, arm.detail

    # ...and the CLIENT arm's identical clause, which is a separate f-string (#12589 round 17).
    client_side = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=None,
        client_error="timed out",
        client_state=canary.INDEX_UNREADABLE,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    client_arm = client_side.get("client-published")
    assert client_arm.status == canary.UNKNOWN, client_arm.detail
    assert "could not read the tortoise-client index metadata (timed out)" in client_arm.detail
    assert "the distribution's presence is UNKNOWN, not absent" in client_arm.detail, (
        client_arm.detail
    )


def test_the_RENDERED_summaries_are_PINNED_VERBATIM():
    """⛔ THIS IS THE CLASS-CLOSING PIN FOR EVERY RENDERED LITERAL (#12589 review rounds 13-16).

    Six consecutive review rounds found the same defect shape: an operator-facing literal that no
    test could redden. Asserting one phrase at a time let the next clause drift, and the round-16
    round found the count, the version pointer, "Nothing has been uploaded.", the green
    same-version clause, the heading glyph, and the UNKNOWN tail's second mechanism all mutable in
    silence.

    A single verbatim expectation per TAIL closes the class instead of the instance: any change to
    any word, table cell, glyph or blank line in these four outputs fails here, so a text edit has
    to be made DELIBERATELY, with this expectation updated in the same commit. The four shapes are
    one per verdict path plus the wheel (pre-publish) path. ⚠️ NOT every branch: the wheel
    tail's other FOUR sub-branches (does-not-install, console-script, init-success-no-database, the
    foreign-arm fallback — the other TWO (does-not-install, init-success-no-database) are pinned
    end-to-end by `test_the_wheel_tail_reads_the_PRODUCERS_OWN_text_end_to_end`, the console-script
    shape by `test_the_wheel_tail_names_the_CONSOLE_SCRIPT_case_separately`, and the foreign-arm
    fallback on a hand-built `Report` from that same test) and
    the UNKNOWN tail's EMPTY-`marked` variant are pinned by
    `test_wheel_mode_fail_names_a_build_defect_not_a_release`,
    `test_the_wheel_tail_names_the_CONSOLE_SCRIPT_case_separately` and
    `test_the_UNREADABLE_reason_names_every_cause_the_code_can_produce` — this pin covers one
    shape per tail; those cover the siblings.
    """
    assert canary.render_summary(_GREEN_REPORT()) == GOLDEN_GREEN
    assert canary.render_summary(_INDEX_FAIL_REPORT()) == GOLDEN_INDEX_FAIL
    assert canary.render_summary(_WHEEL_FAIL_REPORT()) == GOLDEN_WHEEL_FAIL
    assert canary.render_summary(_UNKNOWN_REPORT()) == GOLDEN_UNKNOWN


def test_the_summary_heading_publishes_the_GLYPH_not_the_internal_status():
    """⛔ THE HEADING IS THE FIRST THING A HUMAN READS (#12589 review round 15, P3).

    `glyph[report.verdict]` was unpinned: rendering the internal `report.verdict` instead publishes
    `**Verdict: UNKNOWN**` / `**Verdict: OK**`, and both existing heading pins survived — `Verdict:
    FAIL` is identical either way, and `CANNOT RUN` still appeared from the UNKNOWN tail's "NO row is
    marked CANNOT RUN".
    """
    # ⛔ BOTH MECHANISMS (#12589 review round 16, P3): the tail names "could not READ" AND "could
    # not exercise". Round 4's defect was naming ONE for every CANNOT RUN, and deleting the second
    # clause — which then tells an operator whose `engine-init` FAILED TO RUN that the canary could
    # not read something — stayed green, because the only pins were the READ phrase and the
    # absence of a three-word substring.
    exercised = canary.Report()
    exercised.add(canary.Arm("engine-index", canary.OK, "reads"))
    exercised.add(canary.Arm("engine-init", canary.UNKNOWN, "the install could not run"))
    tail = canary.render_summary(exercised)
    assert "could not READ something it needs" in tail, tail
    assert "could not exercise the artifact" in tail, tail

    green = canary.Report()
    green.add(canary.Arm("engine-index", canary.OK, "reads"))
    assert "**Verdict: PASS**" in canary.render_summary(green)
    assert "**Verdict: CANNOT RUN**" in canary.render_summary(canary.Report())


def test_no_informational_rows_are_rendered_without_a_published_version():
    """⛔ "uploaded unknown ago" ABOUT A VERSION NOBODY READ IS A FALSE ROW (#12589 review round
    15, P3).

    `if published:` guards both informational rows. Forcing it truthy renders
    `| release-age | no | — | tortoise-graph  uploaded unknown ago |` and a `tree-version` row that
    compares the tree against an EMPTY published version — and all 89 tests stayed green, because
    nothing asserted the rows' ABSENCE on the shape where they cannot be true.
    """
    report = canary.build_report(
        engine_doc=None,
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert report.get("release-age") is None, [a.name for a in report.arms]
    assert report.get("tree-version") is None, [a.name for a in report.arms]
    rendered = canary.render_summary(report)
    assert "release-age" not in rendered, rendered
    assert "tree-version" not in rendered, rendered
    assert "uploaded  ago" not in rendered, rendered


def test_the_wheel_tail_names_the_CONSOLE_SCRIPT_case_separately():
    """⛔ "DOES NOT INSTALL" IS FALSE FOR A WHEEL THAT INSTALLED CLEANLY (#12589 review round 14,
    P3).

    #12363 rounds 5 and 6 fixed exactly this (a substring test rendered the console-script case as
    "does not install"), but nothing pinned the branch on the RENDERED summary: mutating its text
    back to "does not install", or dropping its `named_it = True`, left 88 tests green — the arm's
    own detail is pinned, the sentence it produces was not.
    """
    report = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm(
            "engine-init",
            canary.FAIL,
            "the installed artifact provides no `tortoise` console script at "
            "/usr/local/bin/tortoise",
        ),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    # ⛔ AND THE INSTALL-FAILURE SIBLING (found by this lane's own mutation sweep, #12589 round
    # 14): `named_it = True` on the "does not install" branch was droppable — the summary would
    # then print "is NOT judged by this summary" about a wheel the canary watched fail to install.
    install_fail = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm(
            "engine-init", canary.FAIL, "the BUILT wheel failed to install: `pip` exits 1"
        ),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    # ⛔ AND A FOREIGN ARM WHOSE DETAIL MERELY MENTIONS A CONSOLE SCRIPT (#12589 review round 17,
    # P3): the substring branches keyed on the PROSE alone, so this report rendered "The BUILT wheel
    # installs but ships no `tortoise` console script … this is a code defect" for an arm that never
    # installed anything.
    mentions = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm(
            "docs-smoke", canary.FAIL, "the docs reference the tortoise console script"
        ),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    # ...and the same for a foreign arm whose detail OPENS with the wheel's install vocabulary.
    opens_with = canary.build_report(
        engine_doc=None,
        client_doc=None,
        tree_version="0.2.0",
        init_arm=canary.Arm(
            "docs-smoke", canary.FAIL, "the BUILT wheel example in the docs failed to install"
        ),
        now=NOW,
        wheel_path=Path("dist/tortoise_graph-0.2.0-py3-none-any.whl"),
    )
    opens_summary = canary.render_summary(opens_with)
    assert "is NOT judged by this summary" in opens_summary, opens_summary
    assert "does not install" not in opens_summary, opens_summary

    mentions_summary = canary.render_summary(mentions)
    assert "is NOT judged by this summary" in mentions_summary, mentions_summary
    assert "ships no `tortoise` console script" not in mentions_summary, mentions_summary
    assert "`docs-smoke`" in mentions_summary, mentions_summary

    install_summary = canary.render_summary(install_fail)
    assert "BUILT wheel does not install" in install_summary, install_summary
    assert "re-dispatch this publish" in install_summary, install_summary
    assert "NOT judged by this summary" not in install_summary, install_summary

    summary = canary.render_summary(report)
    assert "BUILT wheel installs but ships no `tortoise` console script" in summary, summary
    assert "does not install" not in summary, summary
    # The classification belongs to this branch too — it DID establish that the wheel is at fault.
    assert "re-dispatch this publish" in summary, summary
    assert "wheel-mode wiring problem" not in summary, summary


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


def test_a_VALID_wheel_reaches_install_and_run_and_consults_no_index(tmp_path, monkeypatch, capsys):
    """The pre-publish gate's PASSING path — the only path `publish-pypi.yml` takes.

    Both existing `--wheel` cases are REFUSALS: they return before any install, so
    `main()`'s forwarding of the flag to `install_and_run` was unpinned (#12363 round 16).
    Dropping `wheel=wheel` there sends the gate into INDEX mode while `build_report`
    still renders wheel mode: it installs the PREVIOUSLY PUBLISHED release, and if that
    one is good the gate exits 0 and an uninstallable wheel is uploaded irreversibly —
    the exact regression the module's own comment at the `wheel` kwarg exists for. A
    healthy index here is therefore not decoration: reaching it at all is the failure.
    """
    wheel = tmp_path / "dist" / "tortoise_graph-0.3.0-py3-none-any.whl"
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(b"PK\x03\x04 not a real wheel, never installed")

    def _no_index(*_a, **_k):
        raise AssertionError("a wheel-mode run must not consult the index")

    monkeypatch.setattr(canary, "fetch_index", _no_index)

    seen: dict = {}

    def fake_install(**kwargs):
        seen.update(kwargs)
        return canary.Arm("engine-init", canary.OK, "`tortoise init` succeeds"), "$ fake\n"

    monkeypatch.setattr(canary, "install_and_run", fake_install)

    rc = canary.main(["--wheel", str(wheel), "--json"])
    assert rc == 0, capsys.readouterr().err
    assert seen["wheel"] == wheel, (
        "--wheel did not reach install_and_run: the gate graded the index"
    )
    # The budget the workflow's arithmetic counts must ride along with the flag.
    assert seen["package"] == canary.ENGINE_PACKAGE
    assert seen["transcript_path"] is None


def test_summary_says_unknown_is_red_on_purpose():
    summary = canary.render_summary(canary.Report())
    assert "CANNOT RUN" in summary
    assert "never look like a pass" in summary


def test_the_wheel_tail_reads_the_PRODUCERS_OWN_text_end_to_end(tmp_path):
    """⛔ THE PRODUCER → RENDERER CONTRACT (#12589 review round 21, P2).

    Every render pin before this fed a HAND-BUILT `Arm`, so the wheel tail's two prose-keyed branches
    were never exercised against the strings `install_and_run` actually produces. Rewording the
    producer silently reinstated the #12634/#12363 rounds 5/6 false claim: a wheel that installed
    cleanly and then failed `tortoise init` rendered "does not install".

    Driven through the `runner=` seam, one failing call per shape, then RENDERED — the detail and
    the sentence it must produce, together.
    """

    class _Done:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    wheel = Path("dist/tortoise_graph-0.2.0-py3-none-any.whl")

    def _report_for(runner, workdir):
        (workdir / "venv" / "bin").mkdir(parents=True, exist_ok=True)
        (workdir / "venv" / "bin" / "tortoise").write_text("#!/bin/sh\n")
        arm, _ = canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=workdir,
            timeout=60,
            runner=runner,
            wheel=wheel,
        )
        assert arm.status == canary.FAIL, arm.detail
        return canary.render_summary(
            canary.build_report(
                engine_doc=None,
                client_doc=None,
                tree_version="0.2.0",
                init_arm=arm,
                now=NOW,
                wheel_path=wheel,
            )
        )

    # (a) pip could not install the BUILT wheel.
    def _install_fails(cmd, **kwargs):
        if "install" in cmd:
            return _Done(returncode=1, stderr=f"ERROR: {wheel.name} is not a valid wheel")
        return _Done()

    install_summary = _report_for(_install_fails, tmp_path / "a")
    assert "The BUILT wheel does not install." in install_summary, install_summary
    assert "fails its documented first command" not in install_summary, install_summary

    # (b) it installed, and `tortoise init` exited non-zero.
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "b" / "venv" / "bin" / "tortoise").write_text("#!/bin/sh\n")

    def _init_fails(cmd, **kwargs):
        if "init" in cmd:
            return _Done(returncode=3)
        return _Done()

    init_summary = _report_for(_init_fails, tmp_path / "b")
    assert "The BUILT wheel fails its documented first command." in init_summary, init_summary
    assert "does not install" not in init_summary, init_summary

    # (c) it installed and `tortoise init` exited 0 but created nothing — the command RAN, so the
    # summary may not say it failed. (#12589 review round 21, P3: this shape rendered the false
    # "fails its documented first command" until the positive branch was added.)
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "venv" / "bin").mkdir(parents=True)
    (tmp_path / "c" / "venv" / "bin" / "tortoise").write_text("#!/bin/sh\n")

    def _init_succeeds_silently(cmd, **kwargs):
        return _Done(returncode=0)

    silent_summary = _report_for(_init_succeeds_silently, tmp_path / "c")
    # ⛔ THE SENTENCE VERBATIM, conclusion clause included (#12589 review round 22, P3): pinning
    # "creates no database" plus two absent phrases let the CONCLUSION be inverted — "… which is the
    # documented behaviour and needs no release" passed, because nothing asserted what the sentence
    # concludes about it.
    assert (
        "The BUILT wheel runs its documented first command successfully but creates no database, "
        "so the documented flow does not work. Nothing has been uploaded. Fix the tree and "
        "re-dispatch this publish: this is a code defect, not a release-owed finding."
    ) in silent_summary, silent_summary
    assert "fails its documented first command" not in silent_summary, silent_summary
    assert "does not install" not in silent_summary, silent_summary

    # ⛔ AND THE BRANCH'S ARM GATE (#12589 review round 22, P3): dropping `runs_the_wheel` from it
    # kept 104 green, and a gated foreign arm whose detail merely MENTIONS the phrase then
    # published the wheel's own claim — the round-17 defect shape, on the new branch.
    foreign = canary.Report(wheel_mode=True)
    foreign.add(
        canary.Arm(
            "docs-smoke",
            canary.FAIL,
            "the docs note that `tortoise init` created no database on a Windows box",
        )
    )
    foreign_summary = canary.render_summary(foreign)
    assert "is NOT judged by this summary" in foreign_summary, foreign_summary
    assert "creates no database, so the documented flow does not work" not in foreign_summary, (
        foreign_summary
    )
    # ⛔ THE FALLBACK'S OWN ARM NAMING (#12589 review round 23, P3): a bare "`docs-smoke`" is
    # satisfied by the TABLE row, so replacing the fallback's `{step.name}` with a placeholder
    # stayed green. Assert the clause.
    assert (
        "the failing arm is `docs-smoke` (the docs note that `tortoise init` created no database "
        "on a Windows box)"
    ) in foreign_summary, foreign_summary


def test_the_unknown_tail_only_names_GATED_rows():
    """⛔ THE TAIL'S POINTER MUST NAME A ROW THE TABLE MARKS (#12589 review round 25, P3).

    `marked` reads `rendered_status`, which renders a non-gated row as `—`. Reading the raw
    `arm.status` instead kept 104 green and made the tail name a row the table shows as `—` —
    telling an operator to look for a CANNOT RUN mark that is not on that row.
    """
    report = canary.Report()
    report.add(canary.Arm("engine-init", canary.UNKNOWN, "the install could not run"))
    report.add(canary.Arm("release-age", canary.UNKNOWN, "uploaded unknown ago", gated=False))
    summary = canary.render_summary(report)
    assert "the row marked CANNOT RUN names which (`engine-init`)" in summary, summary
    assert "`release-age`" not in summary.split("The canary could not READ")[1], summary


def test_the_wheel_tail_only_reads_GATED_arms():
    """⛔ A NON-GATED ROW MAY NOT DECIDE THE WHEEL'S TAIL (#12589 review round 19, P3).

    `step` is the first GATED red arm. Dropping `arm.gated and` kept 97/97 green, and on a report
    holding a non-gated `engine-init` FAIL plus a gated `wheel-smoke` FAIL the tail then took the
    non-gated row as `step`. `engine-init` is the arm that RUNS the built wheel, so it inherited the
    wheel's own claim and the tail rendered "The BUILT wheel fails its documented first command"
    for an arm that never decided the verdict. (Measured with the mutant: the console-script
    sentence is the OTHER branch and never appears in this shape, so an assertion on it would be
    inert — the assertion below pins the sentence this mutant actually renders.)
    """
    report = canary.Report(wheel_mode=True)
    report.add(canary.Arm("engine-init", canary.FAIL, "not consulted", gated=False))
    report.add(canary.Arm("wheel-smoke", canary.FAIL, "the built wheel is missing a README"))
    summary = canary.render_summary(report)
    # The sentence the mutant actually renders — pinned first, because MIS-ATTRIBUTING the wheel's
    # claim to a non-gated arm is the damage; "is NOT judged" is the fallback that replaced it.
    assert "fails its documented first command" not in summary, summary
    assert "is NOT judged by this summary" in summary, summary
    assert "`wheel-smoke`" in summary, summary


def test_the_index_corroborated_FAIL_detail_orders_a_release():
    """⛔ THE ARM DETAIL IS WHAT THE ALERT QUOTES (#12589 review round 19, P3).

    The renderer's clause is pinned by GOLDEN_INDEX_FAIL, the arm's own "; a release is owed" was
    not — dropping it left the FAIL with no instruction on the one path where the index CONFIRMS
    the absence, i.e. where a release genuinely is owed.
    """
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "x"),
        now=NOW,
    )
    assert report.get("engine-init").detail == "x"  # the arm is passed through verbatim

    # The producer of the sentence is `install_and_run`; drive it through the runner seam.
    class _Done:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _runner_install_fails(cmd, **kwargs):
        if cmd[:2] == ["-m", "venv"] or "venv" in cmd:
            return _Done()
        if "install" in cmd:
            return _Done(
                returncode=1,
                stderr=(
                    "ERROR: Could not find a version that satisfies the requirement "
                    "tortoise-graph==0.2.0 (from versions: none)\n"
                    "ERROR: No matching distribution found for tortoise-graph==0.2.0"
                ),
            )
        return _Done()

    # The index probe is what corroborates the absence, so stub it to the ABSENT verdict.
    # (Nothing needs to pre-exist under `workdir`: the venv is created first, `pip install` runs
    # second, and only a SUCCESSFUL install ever reaches the console-script check — so an install
    # failure here cannot be reached from, or changed by, a populated `venv/bin`.)
    def _probe(name):
        return canary.INDEX_ABSENT

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        arm, _ = canary.install_and_run(
            package="tortoise-graph",
            version="0.2.0",
            workdir=workdir,
            timeout=60,
            runner=_runner_install_fails,
            absent_probe=_probe,
        )
    assert arm.status == canary.FAIL, arm.detail
    assert "a release is owed" in arm.detail, arm.detail


def test_the_engine_init_non_reading_details_are_PINNED_VERBATIM(tmp_path):
    """⛔ THE PRODUCER TEXTS, not just the renderer (#12589 review round 19, P3).

    Rounds 16-18 pinned what `render_summary` PRINTS for hand-built arms; the details the tool
    itself PRODUCES for a detector outage were still free. Each of these substitutions kept 97/97
    green while wording a HARNESS fault as an artifact one:
    "could not create the clean venv" → "the published artifact is not installable",
    "timed out after {n}s" → "the documented command did not work",
    "could not run: {exc}" → "the artifact could not run".

    Driven through the `runner=` seam, one failing call per shape, so each sentence is produced by
    the real code path rather than by a hand-built `Arm`.
    """

    class _Done:
        def __init__(self, returncode=0):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def _runner_venv_fails(cmd, **kwargs):
        return _Done(returncode=1)

    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=60,
        runner=_runner_venv_fails,
    )
    assert arm.status == canary.UNKNOWN, arm.detail
    assert arm.detail == "could not create the clean venv", arm.detail

    # The console-script check runs BEFORE the first command, so the timeout and OSError shapes
    # are only reachable once the venv looks populated.
    (tmp_path / "venv" / "bin").mkdir(parents=True, exist_ok=True)
    (tmp_path / "venv" / "bin" / "tortoise").write_text("#!/bin/sh\n")

    def _runner_init_times_out(cmd, **kwargs):
        if "init" in cmd:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=42)
        return _Done()

    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=42,
        runner=_runner_init_times_out,
    )
    assert arm.status == canary.UNKNOWN, arm.detail
    assert arm.detail == "timed out after 42s", arm.detail

    def _runner_init_unrunnable(cmd, **kwargs):
        if "init" in cmd:
            raise OSError("no such binary")
        return _Done()

    arm, _ = canary.install_and_run(
        package="tortoise-graph",
        version="0.2.0",
        workdir=tmp_path,
        timeout=42,
        runner=_runner_init_unrunnable,
    )
    assert arm.status == canary.UNKNOWN, arm.detail
    assert arm.detail == "could not run: no such binary", arm.detail

    # ...and the arm `build_report` substitutes when no install was performed at all.
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=None,
        now=NOW,
    )
    assert report.get("engine-init").status == canary.UNKNOWN
    assert (
        report.get("engine-init").detail
        == "the published artifact was not exercised (install could not run)"
    ), report.get("engine-init").detail


def test_the_answered_NO_VERSION_details_name_the_right_distribution():
    """⛔ THE ENGINE ARM'S FAIL SENTENCE COULD NAME THE WRONG PACKAGE (#12589 review round 19, P3).

    `missing_version_arm` builds one sentence per arm from `package`. Nothing pinned the literals,
    so `index metadata for {ENGINE_PACKAGE}` could be swapped for `{CLIENT_PACKAGE}` — a FAIL about
    the wrong distribution, on the shape (`{{"info": {{}}}}`) this PR refers out to #12636.
    """
    engine_side = canary.build_report(
        engine_doc={"info": {}},
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    ).get("engine-index")
    assert engine_side.status == canary.FAIL, engine_side.detail
    assert engine_side.detail == "index metadata for tortoise-graph carries no latest version", (
        engine_side.detail
    )

    client_side = _client_report({"info": {}}).get("client-published")
    assert client_side.status == canary.FAIL, client_side.detail
    assert (
        client_side.detail == "the tortoise-client index document carries no published version"
    ), client_side.detail


def test_the_CLI_returns_the_verdicts_exit_code_not_a_coarser_one(monkeypatch, capsys):
    """⛔ `main()`'s RETURN VALUE IS THE CONTRACT (#12589 review round 19, P2).

    `.github/scripts/published-artifact-alert.sh` reads the process exit: 0 closes the alert ledger,
    1 orders a release, 2 keeps it open. The suite pinned `report.exit_code` (the property) and the
    EARLY returns, but never the verdict path — so replacing `return report.exit_code` with
    `return 0 if report.verdict != FAIL else 1` made a CANNOT RUN exit **0** with all 97 tests
    green, i.e. a non-reading published as a pass. Assert the call, not the property.
    """
    _stub_main(monkeypatch, canary.Arm("engine-init", canary.OK, "tortoise init succeeds"))

    def _stub_report(verdict):
        probe = canary.Report()
        probe.add(canary.Arm("engine-index", verdict, "x"))
        return lambda **kwargs: probe

    for verdict, expected in (
        (canary.OK, 0),
        (canary.FAIL, 1),
        (canary.UNKNOWN, 2),
    ):
        monkeypatch.setattr(canary, "build_report", _stub_report(verdict))
        capsys.readouterr()
        assert canary.main(["--json"]) == expected, verdict


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
    # ⛔ The CLIENT document must be HEALTHY here, and that is load-bearing (#12363 round
    # 16). Returning ONE file-less document for both packages made `client-published`
    # FAIL on every stubbed run, so `rc == 1` and `"FAIL" in out` were satisfied by the
    # CLIENT arm: the CLI suite could not see the arm it exists to test. Measured by
    # mutation — deleting `main()`'s `init_arm=` hand-off (so `init_arm=None`) left every
    # assertion in both CLI cases still green, i.e. the canary could have stopped reading
    # the published artifact's documented first command with 2146 lines of pins none the
    # wiser. With a healthy client arm the only thing that can make this red is the arm
    # under test.
    def fake_fetch_index(package, timeout):
        if package == canary.CLIENT_PACKAGE:
            return _client_doc(), "", canary.INDEX_OK
        return ({"releases": {"0.2.0": []}, "info": {"version": "0.2.0"}}, "", canary.INDEX_OK)

    monkeypatch.setattr(canary, "fetch_index", fake_fetch_index)

    def fake_install(**kwargs):
        # Write at the path MAIN passes in: the wiring under test is
        # `--transcript-dir` → `transcript_path`, not the tool's own flush.
        path = kwargs.get("transcript_path")
        if path is not None:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text("$ fake transcript\n[exit 1]\n")
        return arm, "$ fake transcript\n[exit 1]"

    monkeypatch.setattr(canary, "install_and_run", fake_install)


def test_the_shared_engine_fixture_is_a_DOCUMENTED_file_record():
    """⛔ THE "HEALTHY" FIXTURE MUST BE A SHAPE THE CANARY CAN JUDGE (#12589 review round 12, P3).

    `_engine_doc()` is used as the healthy engine document throughout this module. Its only file
    entry originally had `filename` + `upload_time_iso_8601` and NO `yanked`, which `judged_value`
    classifies as an UNREADABLE value — so the healthy fixture was a record the canary refuses to
    judge, and a future test handing it to a judging path would silently get UNKNOWN. This pins the
    fixture itself: a documented record reads, and its release resolves.
    """
    # `judged_value` -> (any installable file, anything dropped as unreadable)
    assert canary.judged_value(_engine_doc()["releases"]["0.2.0"]) == (True, False)
    assert canary.installability(_engine_doc(), "0.2.0") == canary.INSTALLABLE
    assert canary.resolvable_releases(_engine_doc()) == (["0.2.0"], "")


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
    # ⛔ The arm UNDER TEST, not merely the verdict (#12363 round 16): the client arm is
    # healthy in this fixture, so a red may only come from `engine-init`.
    engine = next(a for a in payload["arms"] if a["name"] == "engine-init")
    assert engine["status"] == canary.FAIL, payload

    # ⛔ THE MACHINE SURFACE MUST NOT CARRY A VERDICT THE TABLE REFUSES TO PRINT (#12589 review
    # round 10, P3). `release-age` is informational and its own detail says the timestamp was not
    # obtained ("uploaded unknown ago"); the table renders `—` there, so `--json` must not stamp
    # "OK". One rendering of one fact, or the two surfaces disagree about whether a non-reading is
    # a verdict.
    # ⛔ THE `verdict` FIELD'S VOCABULARY IS PINNED (#12589 review round 16, P3): `verdict` is the
    # INTERNAL enum (`OK`/`FAIL`/`UNKNOWN`) and `status` the rendered glyph — a deliberate split,
    # because the workflow keys on `== "FAIL"`. Nothing pinned OK or UNKNOWN, so rendering the
    # verdict field too stayed green.
    assert payload["verdict"] == canary.FAIL

    def _stub_report(verdict):
        probe = canary.Report()
        probe.add(canary.Arm("engine-index", verdict, "reads"))
        return lambda **kwargs: probe

    for verdict in (canary.OK, canary.UNKNOWN):
        monkeypatch.setattr(canary, "build_report", _stub_report(verdict))
        canary.main(["--json"])
        got = json.loads(capsys.readouterr().out)
        assert got["verdict"] == verdict, got
        assert got["exit_code"] == {canary.OK: 0, canary.UNKNOWN: 2}[verdict], got
    monkeypatch.undo()

    age = next(a for a in payload["arms"] if a["name"] == "release-age")
    assert age["gated"] is False, payload
    assert age["status"] == "—", age


def test_the_table_and_the_json_publish_the_SAME_status_for_every_arm(
    tmp_path, monkeypatch, capsys
):
    """⛔ ONE RENDERING OF ONE STATUS, ON BOTH SURFACES (#12589 review round 11, P3).

    Round 10 gave `--json` its own copy of the non-gated half of the rule, so the gated half kept
    publishing a RAW status: an arm holding a status this module does not name read "CANNOT RUN" in
    the table (`glyph.get(..., UNKNOWN)`) and that spelling in the payload. Both surfaces now call
    `rendered_status`, so the pin is EQUALITY between them rather than a second copy of the rule.
    """
    import json

    _stub_main(monkeypatch, canary.Arm("engine-init", canary.OK, "tortoise init succeeds"))
    crafted = canary.Report()
    # "PASS" is deliberately NOT one of the module's status constants: an unnamed status must render
    # as the NON-READING, never as whatever spelling the arm happened to hold.
    crafted.add(canary.Arm("engine-index", "PASS", "a status nothing in this module names"))
    crafted.add(canary.Arm("release-age", canary.OK, "uploaded unknown ago", gated=False))
    monkeypatch.setattr(canary, "build_report", lambda **kwargs: crafted)

    canary.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    by_name = {arm["name"]: arm for arm in payload["arms"]}
    assert by_name["engine-index"]["status"] == "CANNOT RUN", by_name
    assert by_name["release-age"]["status"] == "—", by_name

    # ...and the table agrees, because it goes through the same function.
    rendered = canary.render_summary(crafted)
    engine_row = next(ln for ln in rendered.splitlines() if "`engine-index`" in ln)
    assert "| CANNOT RUN |" in engine_row, engine_row
    assert "PASS" not in engine_row, engine_row


def test_the_installability_sentence_is_an_ALLOW_list_not_a_deny_list(monkeypatch):
    """⛔ A FOURTH STATE MUST NAME ITSELF, NOT INHERIT THE DECISIVE NEGATIVE (#12589 review round
    11, P3). The round-9 code put "not resolvable for an unpinned install here" on `else:`, so a
    future reader returning anything unnamed would be rendered as an ARTIFACT finding. An
    allow-list cannot manufacture that claim.
    """
    seen: list[str] = []
    real = canary.installability

    def fourth_state(index_doc, version):
        seen.append(version)
        return "some-new-state"

    monkeypatch.setattr(canary, "installability", fourth_state)
    report = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    monkeypatch.undo()
    assert seen, "the OK arm must ask `installability` at all — otherwise this pin proves nothing"
    detail = report.get("client-published").detail
    assert "not resolvable" not in detail, detail
    assert "could not be READ here" in detail, detail
    assert "some-new-state" in detail, detail
    assert real is canary.installability


def test_the_tree_version_row_implicates_only_the_arm_that_failed():
    """⛔ A `FAIL` ROW MAY NOT BLAME AN ARTIFACT THAT PASSED (#12589 review round 10, P3).

    At the same version the pre-fix text said "the published build at this version predates the
    fix" for ANY FAIL. When the failing arm is `client-published` (a `tortoise-client` 404 here)
    while `engine-init` PASSes at that same version, that sentence claims the engine's published
    artifact is stale — the opposite of the row directly above it. Two sentences, one per arm.
    """
    # The engine is healthy and installs; the CLIENT answers with an empty release, so
    # `client-published` is the FAIL and `engine-init` is OK.
    report = _client_report(
        {"info": {"version": "0.2.0"}, "releases": {"0.2.0": []}},
    )
    assert report.verdict == canary.FAIL, [a.name for a in report.arms if a.status == canary.FAIL]
    failing = [a.name for a in report.arms if a.gated and a.status == canary.FAIL]
    assert failing == ["client-published"], failing
    row = next(a for a in report.arms if a.name == "tree-version")
    assert "predates the fix" not in row.detail, row.detail
    assert "NOT the engine build" in row.detail, row.detail

    # ⛔ AND A THIRD GATED ARM'S FAILURE DOES NOT IMPLICATE THE ENGINE EITHER (#12589 review round
    # 11, P3). Round 10 keyed `engine_failed` on the ABSENCE of the name "client-published", so any
    # new gated arm would inherit the engine-stale claim. This arm is deliberately NOT one of
    # `ENGINE_ARMS`.
    third_arm = canary.Report()
    third_arm.add(canary.Arm("engine-index", canary.OK, "the index reads"))
    third_arm.add(canary.Arm("documented-command", canary.FAIL, "the README command exits 1"))
    assert third_arm.verdict == canary.FAIL
    assert canary.engine_build_implicated(third_arm) is False, "a NON-engine arm's red"
    # ...and the predicate still fires for the arms it names.
    for name in sorted(canary.ENGINE_ARMS):
        named = canary.Report()
        named.add(canary.Arm(name, canary.FAIL, "red"))
        assert canary.engine_build_implicated(named) is True, name
    # A non-gated engine-named row must not count either — only gated arms carry the verdict.
    ungated = canary.Report()
    ungated.add(canary.Arm("engine-index", canary.FAIL, "red", gated=False))
    assert canary.engine_build_implicated(ungated) is False

    # ⛔ AND THE GREEN SAME-VERSION CLAUSE (#12589 review round 16, P3): the only report that
    # reaches "no bump pending" is all-OK at one version, and nothing pinned it — warning "a bump
    # IS required" on a wholly green report stayed green.
    clean = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    assert clean.verdict == canary.OK, [a.name for a in clean.arms if a.status != canary.OK]
    clean_row = next(a for a in clean.arms if a.name == "tree-version")
    assert "the tree's version (no bump pending)" in clean_row.detail, clean_row.detail
    assert "predates the fix" not in clean_row.detail, clean_row.detail

    # ⛔ AND THE "differs" HEDGE IS PINNED (#12589 review round 15, P3): stripping it to a flat
    # "a release is owed" is FALSE whenever the tree is AHEAD of the published version, and no test
    # asserted the hedge.
    ahead = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc=_client_doc(),
        tree_version="0.3.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    ahead_row = next(a for a in ahead.arms if a.name == "tree-version")
    assert "(a release is owed or in flight)" in ahead_row.detail, ahead_row.detail
    # ⛔ AND ITS ORIENTATION (#12589 review round 20, P3): every golden renders this row with
    # tree_version == published, which is degenerate in both directions, so swapping the two slots
    # was invisible — and the row would then say the opposite of which version is published.
    assert "pyproject.toml version 0.3.0 vs published 0.2.0" in ahead_row.detail, ahead_row.detail

    # ...and the engine FAIL at the same version keeps the sentence it earned.
    engine_fail = canary.build_report(
        engine_doc={"info": {"version": "0.2.0"}, "releases": {"0.2.0": [_CLIENT_GOOD]}},
        client_doc=_client_doc(),
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.FAIL, "tortoise init exits 1"),
        now=NOW,
    )
    assert engine_fail.get("engine-init").status == canary.FAIL
    row = next(a for a in engine_fail.arms if a.name == "tree-version")
    assert "predates the fix" in row.detail, row.detail


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
    # The comparison is read by `comparison()` below — ONE comparison against ONE literal,
    # on the variable the step declares. That is what replaced the spelling pins
    # (`'!= "1"' in str(alarm)`, `'= "2"' not in str(alarm)`) which could not tell a
    # comparison from a comment, and false-red a semantically identical rewrite.
    assert "needs.canary.outputs.exit_code" in str(alarm.get("env", {}))
    assert "DETECTOR outage" in str(alarm)
    alarm_text = str(alarm.get("run"))
    assert "RUNNER fault" in alarm_text, (
        "the alarm needs its own branch for a run that REACHED a verdict (exit 0) and then "
        "hit a runner fault, or that state stays unexplained"
    )
    # ⛔ The BODY must be bound to its own PREDICATE, and each predicate to the variable the
    # step DECLARES — read as a COMPARISON, never as a spelling. Four measured bypasses are
    # why; each one left the suite GREEN before the fix that closed it:
    #   * `'= "0"'` is a SUBSTRING of `'!= "0"'`, so an INVERTED runner-fault predicate
    #     passed and every non-zero exit was explained as "a runner fault … nothing is owed";
    #   * swapping the two branch BODIES (predicates and order untouched) kept every literal
    #     assertion true while the exit-0 branch printed "detector outage";
    #   * a POSITION-only pin accepted a branch testing a variable nothing sets
    #     (`${CANARY_RC:-}`) — dead code, every exit fell through to the outage text;
    #   * a COMPOUND predicate (`… = "0" ] || … = ""`) or a `:-0` default re-routed the
    #     ABSENT reading into the runner-fault branch ("nothing is owed"), and a trailing
    #     `# ${EXIT_CODE}` comment satisfied a substring test over dead code.
    env_key = next(
        (
            key
            for key, value in (alarm.get("env") or {}).items()
            if "needs.canary.outputs.exit_code" in str(value)
        ),
        None,
    )
    assert env_key, "the alarm step must take the canary's exit code in its `env:` block"

    def comparison(line: str, *, label: str) -> tuple[str, str, str]:
        """(subject, operator, literal) of ONE shell test on this line.

        Tolerant of what does not change meaning — quote style, `=`/`==`/`-eq`, `[[ ]]`, and
        the Yoda form (`[ "0" = "$x" ]`) — and strict about what does: a SINGLE comparison,
        whose subject is the variable the step declares with NO literal default. A trailing
        shell comment is stripped first, so naming the right variable in a comment cannot
        make a dead branch look alive.
        """
        code = line.split("#", 1)[0].strip()
        assert "&&" not in code and "||" not in code, (
            f"{label}: a branch predicate must be ONE comparison — a compound test reroutes "
            f"exit codes without changing the structure this pin reads: {line!r}"
        )
        match = re.fullmatch(r"(?:el)?if\s+\[\[?\s*(.+?)\s*\]\]?\s*;?\s*then", code)
        assert match, f"{label}: not readable as a single `[ … ]` test: {line!r}"
        for operator in ("!=", "==", "-eq", "="):
            parts = match.group(1).split(operator)
            if len(parts) == 2:
                break
        else:  # pragma: no cover - the assertion below is the real guard
            raise AssertionError(f"{label}: no comparison operator in {line!r}")
        left, right = (part.strip().strip("\"'") for part in parts)
        normalised = {"-eq": "=", "==": "="}.get(operator, operator)
        if "$" in left:
            return left, normalised, right
        return right, normalised, left

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
    declared = (f"${env_key}", f"${{{env_key}}}", f"${{{env_key}:-}}")

    subject, operator, literal = comparison(runner_pred, label="runner-fault branch")
    assert subject in declared, (
        f"the runner-fault branch must test the variable the step itself declares "
        f"({declared[1]}), with no literal default: a variable nothing sets is dead code, "
        f"and `:-<literal>` turns an ABSENT reading into that literal — {runner_pred!r}"
    )
    assert (operator, literal) == ("=", "0"), (
        f"the runner-fault branch must be a POSITIVE test for 0 — got `{operator} {literal}` "
        f"in {runner_pred!r}; `!=` there explains a real release finding as a runner fault"
    )
    subject, operator, literal = comparison(outage_pred, label="outage branch")
    assert subject in declared, (
        f"the outage branch must test the variable the step itself declares "
        f"({declared[1]}) with no literal default — testing anything else makes the branch "
        f"unreachable: {outage_pred!r}"
    )
    assert (operator, literal) == ("!=", "1"), (
        f"the outage branch must test that the exit code is NOT 1 — got `{operator} "
        f"{literal}` in {outage_pred!r}; any other literal lets a cancelled canary or an "
        "exit 2 read as a RELEASE finding"
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
    # ⛔ The PRODUCER end of the chain the close gate now depends on: EXACTLY ONE LIVE LINE
    # must write the KEY the job output READS, to the output sink, carrying the tool's `$rc`.
    # Each of these left the suite GREEN before the fix that closed it (#12363 round 12,
    # cycles 2-4):
    #   * `echo "rc=$rc" >> $GITHUB_OUTPUT` — the key renamed;
    #   * `# echo "exit_code=$rc" >> $GITHUB_OUTPUT` — the write COMMENTED OUT, invisible to a
    #     substring test because the text is still there;
    #   * `echo "canary_exit_code=$rc"` — a PREFIX rename, which a bare `in` test accepts;
    #   * a second live `echo "exit_code=0"` after the real one — GitHub keeps the LAST
    #     assignment, so the verdict is overridden after the pin has looked at the first one.
    # Every one of them leaves the gate reading something other than what the tool said:
    # a constant, or nothing at all. The key is matched on a token boundary, the line is
    # filtered on being live, and there must be exactly one.
    output_key = (
        str(workflow["jobs"]["canary"]["outputs"]["exit_code"])
        .split("steps.canary.outputs.")[1]
        .strip(" }")
    )
    live_lines = [line for line in smoke_run.splitlines() if not line.lstrip().startswith("#")]
    writers = [
        line
        for line in live_lines
        if "GITHUB_OUTPUT" in line
        and re.search(rf"(?<![A-Za-z0-9_]){re.escape(output_key)}=", line)
    ]
    assert len(writers) == 1, (
        f"the canary step must write `{output_key}` to $GITHUB_OUTPUT exactly ONCE — the "
        f"LAST assignment wins, so a second writer silently overrides the verdict the gates "
        f"read, and no writer leaves it empty forever (nothing then closes the release-owed "
        f"ledger and every red reads as a detector outage). Found: {writers!r}"
    )
    assert re.search(r"\$\{?rc\}?(?![A-Za-z0-9_])", writers[0]), (
        f"the single write of `{output_key}` must carry the tool's `$rc`, not a constant: "
        f"{writers[0]!r}"
    )


def test_an_UNWRITABLE_or_UNREADABLE_report_is_an_exit_2_not_a_traceback(
    monkeypatch, tmp_path, capsys
):
    """Every I/O fault in `main` must say "detector", never borrow the release verdict.

    A traceback is exit 1, and exit 1 is what BOTH consumers render as "the published
    artifact is broken — a release is owed". Round 3 fixed that for the fetch path; the
    `pyproject.toml` read and the `--summary-file` append were still uncaught (#12363
    round 5).

    ⛔ THE EXIT CODE ALONE IS AN INERT ASSERTION (#12422 review cycle 5, P1, MEASURED).
    `main()`'s own unexpected-failure backstop also returns 2, so `== 2` holds with BOTH
    guards deleted — measured: `71 passed`, exit 0, while the two guards and the round-5
    claim this docstring makes were gone. The only observable difference is the guard's
    OWN message, so that is what is asserted.
    """
    offline = lambda package, timeout: (None, "offline", canary.INDEX_UNREADABLE)  # noqa: E731

    # (a) the tree's own pyproject.toml cannot be read
    monkeypatch.setattr(canary, "fetch_index", offline)
    monkeypatch.setattr(canary, "REPO_ROOT", tmp_path / "not-a-checkout")
    assert canary.main(["--json"]) == 2
    err = capsys.readouterr().err
    assert "could not read" in err and "pyproject.toml" in err, (
        "the pyproject read is not guarded by its own handler — the backstop produced this "
        f"exit 2, so the round-5 guard is unobserved: {err!r}"
    )
    monkeypatch.undo()

    # (b) the summary file cannot be appended to
    monkeypatch.setattr(canary, "fetch_index", offline)
    assert canary.main(["--json", "--summary-file", str(tmp_path / "nope" / "s.md")]) == 2
    err = capsys.readouterr().err
    assert "could not append to --summary-file" in err, (
        "the --summary-file append is not guarded by its own handler — the backstop produced "
        f"this exit 2, so the round-5 guard is unobserved: {err!r}"
    )


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

    ⛔ THE OPENER IS BUILT HERE, AFTER THE ENVIRONMENT IS SET — reading
    `canary._INDEX_OPENER.handlers` cannot work (#12422 review, P1; measured). That opener
    is constructed at IMPORT, so a `monkeypatch.setenv` afterwards cannot affect it, and
    `build_opener(ProxyHandler({}))` and a bare `build_opener()` both yield an EMPTY
    handler list when no proxy is configured — a `ProxyHandler` with no proxies has only
    `proxy_open`, which `add_handler` skips, so it is never appended. The pre-fix cut of
    this test therefore passed with the fix REVERTED on every runner without a proxy
    (measured: `59 passed`, exit 0). Building through the factory after the env is set is
    what makes the two forms differ, and the second assertion below is what stops
    PRODUCTION from bypassing that factory.
    """
    import urllib.request

    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")

    opener = canary._build_index_opener()
    proxies = [h for h in opener.handlers if isinstance(h, urllib.request.ProxyHandler)]
    # An EMPTY `ProxyHandler` passed to `build_opener` makes it SKIP the default one, and the
    # default is the only thing that reads `http_proxy`/`https_proxy` — so the assertion is
    # the ABSENCE of any ProxyHandler. One present with non-empty `.proxies` means the
    # environment is honoured again, which is the defect this pins.
    assert proxies == [], (
        f"the index opener carries a ProxyHandler {proxies!r}, so the ambient proxy environment "
        "is honoured — a proxy-egress runner would read INDEX_OK here while the child's "
        "proxy-less pip cannot reach the index at all"
    )

    # ⛔ And PRODUCTION must go through the factory. Without this, the assertion above is a
    # statement about a function nothing calls: reverting `_INDEX_OPENER` to a bare
    # `build_opener()` leaves it green, which is the same dead-weight defect one level up.
    assert getattr(canary._INDEX_OPENER, "_tortoise_index_policy", None) == "no-proxy", (
        "`_INDEX_OPENER` was not built by `_build_index_opener()`, so the policy this test "
        "measures is not the policy the canary actually uses"
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


# --------------------------------------------------------------------------
# #12589 — the same class as #12525, one level DOWN
#
# #12525 split the `releases` MAP's type (absent/null/wrong-typed → UNKNOWN) from the ANSWER
# "no releases" (`{}` → FAIL). Two shapes one level below survived that fix, and both turned an
# UNPARSEABLE reading into a verdict:
#
#   1. a present, non-empty map whose VERSION VALUES are not arrays → every value failed
#      `isinstance(files, list)`, `resolvable == []`, and the arm printed "the index lists NO
#      installable file in ANY release … README.md documents `pip install …`, which cannot
#      resolve" → FAIL → exit 1 → the text the alert renders as **"cut a release"**.
#   2. a document whose `info` is not an object → `AttributeError` → `main()`'s backstop → exit
#      2. The exit code was right; the REASON was a traceback.
#
# The rule these pin is one-directional (#12589 review round 10, P3 — the earlier wording here
# said "read COMPLETELY or judge nothing", which the partial-read OK cases below deliberately
# reject): **a FAIL** may only assert what a COMPLETE reading established, while the OK claim is
# EXISTENTIAL — it may stand on a partial read, disclosed in its own detail.
# --------------------------------------------------------------------------

_CLIENT_GOOD = {
    "filename": "tortoise_client-0.2.0-py3-none-any.whl",
    "yanked": False,
    "upload_time_iso_8601": "2026-08-08T00:30:10.719404Z",
}
_CLIENT_YANKED = {
    "filename": "tortoise_client-0.3.0-py3-none-any.whl",
    "yanked": True,
    "upload_time_iso_8601": "2026-09-01T00:00:00Z",
}


def _client_report(client_doc: dict, engine_doc: dict | None = None):
    return canary.build_report(
        engine_doc=canary_engine_doc() if engine_doc is None else engine_doc,
        client_doc=client_doc,
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )


def canary_engine_doc() -> dict:
    return _engine_doc()


def test_resolvable_releases_reports_BOTH_facts_because_the_claims_differ_in_POLARITY():
    """The truth table, at the one helper the client arm reads (#12589).

    Two independent facts, and the polarity of the arm's two claims is what decides which of them
    gates which verdict:

      * the OK claim ("the unpinned requirement resolves") is EXISTENTIAL — one readable
        installable release settles it, so a resolvable version is returned even when a sibling
        value could not be read;
      * the FAIL claim ("NO installable file in ANY release") is UNIVERSAL — it needs a complete
        parse, so any unreadable value supplies a `reason` and the arm cannot say it.

    Both halves are asserted because either alone is satisfiable by the wrong code: a helper that
    always answers `([], something)` keeps the FAIL pins green, and one that never answers a
    reason keeps the UNKNOWN pins green.
    """
    doc = {"info": {"version": "0.3.0"}}

    # COMPLETE readings — the reason is empty even when the answer is "nothing".
    assert canary.resolvable_releases({**doc, "releases": {"0.3.0": [_CLIENT_GOOD]}}) == (
        ["0.3.0"],
        "",
    )
    assert canary.resolvable_releases({**doc, "releases": {}}) == ([], "")
    assert canary.resolvable_releases({**doc, "releases": {"0.3.0": []}}) == ([], "")
    assert canary.resolvable_releases({**doc, "releases": {"0.3.0": [_CLIENT_YANKED]}}) == (
        [],
        "",
    )
    # Latest yanked, an older release installable — pip resolves, so this is a complete POSITIVE.
    assert canary.resolvable_releases(
        {**doc, "releases": {"0.3.0": [_CLIENT_YANKED], "0.2.0": [_CLIENT_GOOD]}}
    ) == (["0.2.0"], "")

    # NON-READINGS, each named. Map level (already fixed in #12525) and VALUE level (#12589).
    assert canary.resolvable_releases(doc) == ([], canary.RELEASES_ABSENT)
    assert canary.resolvable_releases({**doc, "releases": None}) == (
        [],
        canary.RELEASES_ABSENT,
    )
    # ⛔ `[]` is NOT in this loop: an empty ARRAY is a COMPLETE empty answer (`{"0.3.0": []}` is
    # the pinned FAIL below). What is unreadable is a list of NON-OBJECTS, asserted separately.
    for unparseable in ({}, "0.3.0", 0, ["a.whl"]):
        versions, reason = canary.resolvable_releases({**doc, "releases": {"0.3.0": unparseable}})
        assert (versions, reason) == ([], canary.RELEASES_UNREADABLE), unparseable

    # ⛔ BOTH FACTS AT ONCE: a resolvable release is still reported when a sibling value is
    # unreadable, WITH the reason. Returning `([], reason)` here would be the over-strict reading
    # that loses a true PASS on a mirror quirk (the old code returned OK for this document);
    # returning `(versions, "")` would let the arm claim "no installable file in ANY release"
    # off a partial parse. The arm applies the polarity; this helper is not allowed to decide it.
    assert canary.resolvable_releases(
        {**doc, "releases": {"0.2.0": [_CLIENT_GOOD], "0.3.0": {}}}
    ) == (["0.2.0"], canary.RELEASES_UNREADABLE)

    # ⛔ ENTRY GRAIN, not just value grain (#12589 review round 2, P2). A list holding ONE
    # non-object used to make the whole value unreadable, so `{"0.3.0": [good, "junk"]}` went from
    # OK (before this change) to a permanent CANNOT-RUN — even though the readable wheel record
    # alone settles the existential claim. The reading reports the drop; the arm still greens.
    assert canary.readable_entries([_CLIENT_GOOD, "junk"]) == ([_CLIENT_GOOD], True)
    assert canary.resolvable_releases({**doc, "releases": {"0.3.0": [_CLIENT_GOOD, "junk"]}}) == (
        ["0.3.0"],
        canary.RELEASES_UNREADABLE,
    )

    # And the STRICT reader, so a future arm cannot re-spell an unreadable listing as `[]` — at
    # the VALUE level and at the MAP level (`releases: "x"` is #12525's non-reading, not a listing
    # with no files).
    assert canary.readable_entries([_CLIENT_GOOD]) == ([_CLIENT_GOOD], False)
    assert canary.readable_entries([]) == ([], False)
    assert canary.readable_entries({}) == ([], True)
    assert canary.readable_entries(["a.whl"]) == ([], True)
    for doc_unreadable in (
        {"info": {"version": "0.3.0"}},
        {"info": {"version": "0.3.0"}, "releases": None},
        {"info": {"version": "0.3.0"}, "releases": "0.3.0"},
    ):
        assert canary.release_files(doc_unreadable, "0.3.0") is None, doc_unreadable
    assert canary.release_files({"releases": {"0.3.0": {}}}, "0.3.0") is None
    assert canary.release_files({"releases": {"0.3.0": ["a.whl"]}}, "0.3.0") is None
    assert canary.release_files({"releases": {"0.3.0": [_CLIENT_GOOD, "junk"]}}, "0.3.0") is None
    # absent MAP is a non-reading (`None`); absent VERSION in a readable map is an answer (`[]`)
    assert canary.release_files({"info": {"version": "0.2.0"}}, "0.2.0") is None
    assert canary.release_files({"releases": {}}, "0.2.0") == []


def test_an_UNPARSEABLE_release_VALUE_is_UNKNOWN_and_never_orders_a_release():
    """A version value that is not an array must not become the claim "no installable file" (#12589).

    The FAIL sentence is the one `published-artifact-alert.sh` renders as "cut a release", so this
    test asserts the sentence's ABSENCE, not merely a non-zero exit: exit 1 and exit 2 both stop
    the ledger, and only one of them is allowed to say the artifact is broken.
    """
    for unparseable in (
        {},  # a bare object — the shape the issue names
        "0.3.0",  # a string
        0,  # a number
        {  # the file OBJECT at the wrong level — `[<object>]` is the right one
            "filename": "tortoise_client-0.3.0-py3-none-any.whl"
        },
    ):
        report = _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": unparseable}})
        arm = report.get("client-published")
        assert arm.status == canary.UNKNOWN, (unparseable, arm.status, arm.detail)
        assert report.verdict == canary.UNKNOWN, (unparseable, report.verdict)
        assert report.exit_code == 2, (unparseable, report.exit_code)
        assert canary.RELEASES_UNREADABLE in arm.detail, arm.detail
        assert "UNKNOWN, not false" in arm.detail, arm.detail
        assert "not a reason to cut a release" in arm.detail.lower(), arm.detail
        # The release-order clauses must be ABSENT.
        assert "any release" not in arm.detail.lower(), arm.detail
        assert "cannot resolve" not in arm.detail.lower(), arm.detail

    # A list of non-objects is the same class, one level down.
    report = _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": ["a.whl"]}})
    assert report.get("client-published").status == canary.UNKNOWN
    assert report.exit_code == 2

    # AND the states that must NOT move: a document that ANSWERED stays FAIL/exit 1 — including
    # the all-lists mixed case, whose reading is complete.
    for answered in (
        {},  # no releases at all
        {"0.3.0": []},  # an empty file list
        {"0.3.0": [_CLIENT_YANKED]},  # only-yanked
        {"0.3.0": [], "0.4.0": [_CLIENT_YANKED]},  # every value IS a list; nothing installable
    ):
        report = _client_report({"info": {"version": "0.3.0"}, "releases": answered})
        assert report.get("client-published").status == canary.FAIL, (answered, report.verdict)
        assert report.exit_code == 1, (answered, report.exit_code)

    # ⛔ AND THE DIRECTION A "COMPLETE-OR-NOTHING" RULE WOULD BREAK: a resolvable release settles
    # the EXISTENTIAL OK claim even when a SIBLING value is unreadable — the old code returned OK
    # for this document, and turning that into a permanent CANNOT-RUN red would trade a true PASS
    # for detail completeness. The detail must DISCLOSE the partial parse, so the green does not
    # imply it read the whole map.
    report = _client_report(
        {
            "info": {"version": "0.3.0"},
            "releases": {"0.2.0": [_CLIENT_GOOD], "0.3.0": {}},
        }
    )
    arm = report.get("client-published")
    assert arm.status == canary.OK, (arm.status, arm.detail)
    assert report.exit_code == 0, report.exit_code
    assert canary.RELEASES_UNREADABLE in arm.detail, arm.detail
    assert "READABLE subset" in arm.detail, arm.detail
    # ⛔ AND THE PREFIX THAT SAYS *PART* OF THE MAP WAS UNREAD (#12589 review round 18, P3): this
    # sentence is the only disclosure that an OK stands on a PARTIAL read, and inverting it to "this
    # ENTIRE release map could not be read" kept the arm OK/exit 0 with the suite green.
    assert "part of this release map could not be read" in arm.detail, arm.detail
    assert "ENTIRE" not in arm.detail, arm.detail
    # ⛔ THE NOTE'S CAUSE ENUMERATION IS PINNED HERE, at the one place that prints it (#12589 review
    # round 10, P3). Narrowing it to a single cause left all 83 tests green, so the invariant
    # ("all four sites say the SAME words") had drifted to three. The wording names what the
    # reader actually rejects, so the operator can tell which shape broke.
    for phrase in (
        "a value that is not an array",
        "an entry that is not an object",
        "no boolean `yanked`",
        "no non-empty string `filename`",
    ):
        assert phrase in arm.detail, (phrase, arm.detail)
    # ...and an UNREADABLE-only map still gets the disclosure-free UNKNOWN detail.
    assert (
        canary.RELEASES_UNREADABLE
        in _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": {}}})
        .get("client-published")
        .detail
    )

    # ⛔ AND THE SAME AT ENTRY GRAIN (#12589 review round 2, P2): one junk entry inside an
    # otherwise-readable release must not withdraw the OK — the code before this change returned OK
    # here, and the reading must not trade that true PASS away. The drop is disclosed.
    report = _client_report(
        {"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD, "junk"]}}
    )
    arm = report.get("client-published")
    assert arm.status == canary.OK, (arm.status, arm.detail)
    assert report.exit_code == 0, report.exit_code
    assert canary.RELEASES_UNREADABLE in arm.detail, arm.detail
    assert "READABLE subset" in arm.detail, arm.detail
    # ...while a release whose ONLY entries are unreadable has no OK to stand on: UNKNOWN, and the
    # FAIL sentence must stay absent.
    report = _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": ["junk"]}})
    arm = report.get("client-published")
    assert arm.status == canary.UNKNOWN, (arm.status, arm.detail)
    assert report.exit_code == 2
    assert "any release" not in arm.detail.lower(), arm.detail


def test_the_non_reading_tokens_are_pinned_by_their_PUBLISHED_TEXT():
    """⛔ A TOKEN PINNED ONLY BY REFERENCE TO ITS OWN CONSTANT IS A TAUTOLOGY (#12589 review round
    13, P3).

    Every assertion elsewhere reads `assert canary.INFO_UNREADABLE in arm.detail`, which holds for
    ANY value of the constant — including `""`. Mutating each constant to `""` (or renaming
    `RELEASES_UNREADABLE`) left all 87 tests green, so the four tokens an operator actually SEES —
    in the step summary and the `--json` payload, NOT in the alert issue's body (that body is a
    fixed `$why` sentence plus `tail -n 40` of the TRANSCRIPT, so it carries no token) — were never
    verified. One literal assertion per token closes that; the constant-form assertions stay as the
    mechanism check.
    """
    assert canary.INFO_UNREADABLE == "info-unreadable"
    assert canary.VERSION_UNREADABLE == "version-unreadable"
    assert canary.RELEASES_UNREADABLE == "releases-unreadable"
    assert canary.RELEASES_ABSENT == "releases-absent"


def test_a_non_object_info_is_a_LEGIBLE_NON_READING_on_BOTH_arms():
    """`info` present but not an object: no traceback, UNKNOWN — and the exit code SPLITS (#12589).

    ⛔ TWO DEFECTS, TWO EXIT-CODES STORIES, and conflating them was a review finding on this very
    test. Measured against the pre-diff expression `(index_doc.get("info") or {}).get("version")`:

      * a TRUTHY non-object (`"x"`) raised `AttributeError`, and `main()`'s backstop turned that
        into exit 2 — the right CODE by the wrong ROUTE (a traceback as the recorded reason);
      * the FALSY ones (`[]`, `0`, `null`, `""`, `false`) were swallowed by `or {}` into "", took the FAIL arm,
        and reported exit 1 — the "cut a release" text — off an `info` this client never parsed.
        Those documents carry no version at all, so "carries no latest version" was literally true
        of them; what was wrong was issuing a VERDICT off an unparsed field. (The sibling
        `version_unreadable` case is the one where the old sentence was false:
        `{"info": {"version": 0.3}}` does carry a version — measured, pre-diff `latest_version`
        returned `""` for it and the FAIL arm said "carries no latest version".)

    So the fix MOVES the exit code (1 → 2) for the falsy shapes, deliberately and fail-closed. The
    invariance that actually matters is the one a naive "don't raise" fix would break: a
    non-reading must never become an exit 1, on EITHER arm. That is pinned here, at both arms.
    """
    for index_doc in ({"info": "x"}, {"info": []}, {"info": 0}, {"info": None}):
        assert canary.latest_version(index_doc) == "", index_doc
        assert canary.info_unreadable(index_doc) is True, index_doc

    # The absence pin stays a FAIL — an ABSENT `info` is an empty ANSWER, not a non-reading.
    for answered in ({}, {"info": {}}, {"info": {"version": ""}}):
        assert canary.info_unreadable(answered) is False, answered
        assert canary.latest_version(answered) == "", answered

    for doc in ({"info": "x"}, {"info": []}, {"info": 0}, {"info": None}):
        # client arm
        report = _client_report({**doc, "releases": {"0.3.0": [_CLIENT_GOOD]}})
        arm = report.get("client-published")
        assert arm.status == canary.UNKNOWN, (doc, arm.status, arm.detail)
        assert canary.INFO_UNREADABLE in arm.detail, arm.detail
        assert "not a reason to cut a release" in arm.detail.lower(), arm.detail
        # ⛔ THE CLASS LABEL IS PINNED TOO (this lane's own mutation sweep, #12589 round 14): the
        # clause naming the shape a NON-READING was droppable while the closing sentence survived,
        # so the operator lost the words that say the reading never happened.
        assert "NON-READING" in arm.detail, arm.detail
        # ⛔ THE NEGATIVE ASSERTION BELONGS ON BOTH ARMS, AND IN THE GENERAL FORM (#12589 review
        # round 13, P3). The client arm's phrase-specific check let a shared UNKNOWN detail that
        # re-asserts the artifact claim ("… carries no latest version — the document's `info` is
        # …") pass 87/87.
        assert "carries no" not in arm.detail, arm.detail
        assert "carries no published version" not in arm.detail, arm.detail
        assert report.exit_code == 2, (doc, report.exit_code)
        # engine arm — the one a naive fix would have flipped from exit 2 to exit 1
        report = _client_report(
            {"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
            engine_doc=doc,
        )
        arm = report.get("engine-index")
        assert arm.status == canary.UNKNOWN, (doc, arm.status, arm.detail)
        assert canary.INFO_UNREADABLE in arm.detail, arm.detail
        assert "carries no" not in arm.detail, arm.detail
        assert report.exit_code == 2, (doc, report.exit_code)


def test_a_NON_BOOLEAN_yanked_flag_is_the_same_class_one_grain_further_in():
    """An entry is not consulted until its `yanked` field is a BOOLEAN (#12589 review round 3, P2).

    Reading the flag straight off `dict.get` put the FLAG outside the shape this issue exists to
    refuse, in both directions:

      * `{"0.3.0": [{}]}` — an entry a mirror stripped of `yanked` — read as "not yanked", so a
        value nothing was read FROM GREENS the arm (`OK`/exit 0, "installable: 0.3.0");
      * `{"0.3.0": [{"yanked": "false"}]}` — a truthy STRING — read as yanked, so the arm printed
        "lists NO installable file in ANY release" → `FAIL`/exit 1 → **"cut a release"**.

    PyPI documents `yanked` as a boolean, so anything else is a shape outside the contract: the
    entry is dropped and the reading is marked PARTIAL, which can neither green nor red the arm by
    itself.
    """
    doc = {"info": {"version": "0.3.0"}}
    for unreadable_flag in (
        {},  # no `yanked` and no `filename` at all
        {"yanked": "false"},  # a truthy STRING read as "yanked" -> the release order
        {"yanked": "true"},
        {"yanked": 0},  # an int is not the documented boolean
        {"yanked": 1},
        # ⛔ AND THE OTHER FIELD (#12589 review round 4, P2): `yanked: False` with no usable
        # `filename` is not a file record, yet it used to count as an INSTALLABLE file — a false
        # PASS off an object that is not a file.
        {"filename": ""},
        {"filename": None},
    ):
        entry = {**_CLIENT_GOOD, **unreadable_flag} if unreadable_flag else {}
        versions, reason = canary.resolvable_releases({**doc, "releases": {"0.3.0": [entry]}})
        assert (versions, reason) == ([], canary.RELEASES_UNREADABLE), unreadable_flag

        report = _client_report({**doc, "releases": {"0.3.0": [entry]}})
        arm = report.get("client-published")
        # ⛔ NEVER `exit 1`: the flag was not read, so the arm has nothing to say about whether the
        # documented install resolves.
        assert arm.status == canary.UNKNOWN, (unreadable_flag, arm.status, arm.detail)
        assert report.exit_code == 2, (unreadable_flag, report.exit_code)
        assert "any release" not in arm.detail.lower(), arm.detail
        assert "cannot resolve" not in arm.detail.lower(), arm.detail
        assert canary.RELEASES_UNREADABLE in arm.detail, arm.detail

    # A BOOLEAN flag is what the contract says, and it keeps both of its meanings.
    assert canary.resolvable_releases(
        {**doc, "releases": {"0.3.0": [{**_CLIENT_GOOD, "yanked": False}]}}
    ) == (["0.3.0"], "")
    assert canary.resolvable_releases(
        {**doc, "releases": {"0.3.0": [{**_CLIENT_GOOD, "yanked": True}]}}
    ) == ([], "")

    # MIXED at entry grain: one readable installable entry still settles the EXISTENTIAL claim,
    # and the drop is disclosed (the same rule as a junk entry, one field over).
    report = _client_report({**doc, "releases": {"0.3.0": [{**_CLIENT_GOOD, "yanked": False}, {}]}})
    arm = report.get("client-published")
    assert arm.status == canary.OK, (arm.status, arm.detail)
    assert report.exit_code == 0
    assert "READABLE subset" in arm.detail, arm.detail


def test_the_OK_detail_does_not_claim_a_RESOLUTION_the_reading_did_not_establish():
    """`unpinned → X` is a claim about pip; `info.version` is only what the index ADVERTISES (#12589 round 3, P3).

    On the partial path this PR newly reaches, the advertised version can be the very one whose
    listing was not read — and the detail used to print `unpinned → 0.3.0; installable: 0.2.0` in
    one breath, asserting a resolution to a version the same line said it could not read.
    """
    report = _client_report(
        {
            "info": {"version": "0.3.0"},
            "releases": {"0.2.0": [_CLIENT_GOOD], "0.3.0": {}},
        }
    )
    detail = report.get("client-published").detail
    assert "unpinned →" not in detail, detail
    # ⛔ THE ADVERTISED VERSION'S LISTING WAS NOT READ HERE, SO THE CLAUSE MAY NOT CLAIM A PROPERTY
    # OF IT (#12589 review round 6, P2). "not resolvable …" is an artifact claim manufactured from
    # a non-reading, and it would be byte-identical to the READABLE all-yanked case below — the
    # operator could not tell a shape refusal from a yanked release.
    assert "advertised latest 0.3.0 could not be READ here" in detail, detail
    assert "not resolvable for an unpinned install" not in detail, detail
    assert "installable: 0.2.0" in detail, detail

    # ⛔ AND THAT WORDING IS LOAD-BEARING ON THE CASE THAT MOTIVATED THE ARM (#12589 round 5, P3):
    # an ADVERTISED-BUT-YANKED release IS installable under a `==` pin — PEP 592 is what makes an
    # unpinned requirement skip it. Saying "not installable" about it would be false, not merely
    # unproven, so the negative branch says "not resolvable for an unpinned install".
    report = _client_report(
        {
            "info": {"version": "0.3.0"},
            "releases": {"0.3.0": [_CLIENT_YANKED], "0.2.0": [_CLIENT_GOOD]},
        }
    )
    arm = report.get("client-published")
    assert arm.status == canary.OK, (arm.status, arm.detail)
    assert "not installable" not in arm.detail, arm.detail
    assert "not resolvable for an unpinned install" in arm.detail, arm.detail

    # When the advertised version IS installable, say so — and still do not claim pip's target.
    report = _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}})
    detail = report.get("client-published").detail
    assert "advertised latest 0.3.0 is installable" in detail, detail
    assert "unpinned →" not in detail, detail

    # ⛔ MEMBERSHIP IS NOT MAXIMALITY (#12589 review round 4, P3): with 0.2.0 advertised AND
    # installable while 0.3.0 is also installable, an UNPINNED requirement resolves to 0.3.0 —
    # which the reading never computed. The old `unpinned → {info.version}` printed 0.2.0 here,
    # i.e. a resolution target the code did not establish. The detail now names only what it read.
    report = _client_report(
        {
            "info": {"version": "0.2.0"},
            "releases": {
                "0.2.0": [_CLIENT_GOOD],
                "0.3.0": [{**_CLIENT_GOOD, "filename": "tortoise_client-0.3.0-py3-none-any.whl"}],
            },
        }
    )
    detail = report.get("client-published").detail
    assert "unpinned →" not in detail, detail
    assert "advertised latest 0.2.0 is installable" in detail, detail
    assert "installable: 0.2.0, 0.3.0" in detail, detail


def test_the_UNREADABLE_reason_names_every_cause_the_code_can_produce():
    """One constant, one enumeration — round 3 added a cause and only two of four sites said so.

    (#12589 review round 4, P3.) An operator reads the arm's detail and the run summary against each
    other, so a cause that exists in the code but not in the text points them at the wrong shape.
    (⚠️ Not the filed alert issue: its body is `$why` + a transcript tail, and carries no token —
    see the pin below.) This pin fails if any site drifts back to the two-cause wording.
    """
    for releases in (
        {"0.3.0": {}},
        {"0.3.0": [{}]},
        {"0.3.0": [{**_CLIENT_GOOD, "yanked": "false"}]},
        {"0.3.0": [{"yanked": False}]},
    ):
        report = _client_report({"info": {"version": "0.3.0"}, "releases": releases})
        arm = report.get("client-published")
        # ⛔ THE TAIL'S POSITIVE POINTER IS PINNED HERE (#12589 review round 14, P3). The only
        # pins before this were the NEGATIVE branch (an empty / informational-only report), so
        # hard-coding `marked = []` — which makes the summary call a real non-reading "a defect in
        # the canary itself" — passed 88/88. On a shape that DOES mark a row, the pointer must name
        # it and must not fall back to the defect sentence.
        rendered = canary.render_summary(report)
        assert "the row marked CANNOT RUN names which (`client-published`)" in rendered, rendered
        assert "defect in the canary itself" not in rendered, rendered
        assert "no boolean `yanked`" in arm.detail, (releases, arm.detail)
        # ⛔ NOT "no filename": the filter also drops a filename that is PRESENT and unusable
        # (empty / wrong type), and the constant's own comment says "not of the documented type".
        # The two texts are read against each other, so they must agree (#12589 round 5, P3).
        assert "no non-empty string `filename`" in arm.detail, (releases, arm.detail)

    # ⛔ AND THE `releases-absent` DETAIL MUST STILL NAME EVERY SHAPE THAT PRODUCES IT (#12589
    # round 5, P3). #12589's comment 1 recorded that the token is defined as "the key is ABSENT"
    # while being emitted for present-but-wrong-typed maps too. The token reaches an operator in
    # the STEP SUMMARY table and the `--json` payload (⚠️ NOT the alert issue's body, which is
    # `$why` + `tail -n 40` of the transcript — measured; an earlier revision of this comment
    # claimed the alert rendered it verbatim, which that script does not do). The narrow wording
    # points an operator at PyPI's deprecation instead of the shape served, so this pin keeps the
    # detail from being narrowed back silently.
    for absent in (None, [], "0.3.0", 0):
        report = _client_report(
            {"info": {"version": "0.3.0"}, "releases": absent}
            if absent is not None
            else {"info": {"version": "0.3.0"}}
        )
        detail = report.get("client-published").detail
        assert canary.RELEASES_ABSENT in detail, (absent, detail)
        assert "absent, null, or not an object" in detail, (absent, detail)

    shape_refusal = _client_report({"info": {"version": "0.3.0"}, "releases": {"0.3.0": {}}})
    summary = canary.render_summary(shape_refusal)
    # ⛔ The UNKNOWN tail must not name ONE mechanism for every CANNOT RUN (#12589 round 4): on
    # this report every index row above reads OK and the non-reading is a shape the canary
    # refuses. The tail names both mechanisms and defers to the row.
    assert "could not READ something it needs" in summary, summary
    assert "could not read the index" not in summary, summary

    # ⛔ AND THE ROW IT DEFERS TO MUST ACTUALLY CARRY THE MARK (#12589 review round 8, P2). The
    # tail says "the row marked CANNOT RUN names which", so the table has to have a status column
    # — before this, the only occurrence of those words in a rendered summary was the header, and
    # the reader had to guess which row was the non-reader.
    # The HEADER must declare the column too, or the rows are not a table at all.
    assert "| arm | gated | status | reading |" in summary, summary

    # ⛔ AN INFORMATIONAL ROW HAS NO VERDICT (#12589 review round 9, P3). `release-age` is built
    # with status OK even when its own text says "uploaded unknown ago" — the arm read a value it
    # did not obtain — and stamping PASS beside that is the non-reading-as-a-verdict class this
    # issue removes, one row over. Rendered: `| \`release-age\` | no | — | …`.
    unknown_age = canary.build_report(
        engine_doc={"info": {"version": "0.2.0"}},  # no usable `releases` map -> no timestamp
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    rendered_age = canary.render_summary(unknown_age)
    assert "uploaded unknown ago" in rendered_age, rendered_age
    age_row = next(ln for ln in rendered_age.splitlines() if "`release-age`" in ln)
    assert "| no | — |" in age_row, age_row
    assert "PASS" not in age_row, age_row

    # ⛔ AND THE POINTER TO "the row marked CANNOT RUN" MUST POINT AT A ROW THAT EXISTS (#12589
    # review round 9, P3): an empty report, or one whose only arms are informational, renders no
    # such row — and the tail used to send the operator looking for it anyway.
    for bare in (canary.Report(),):
        rendered_bare = canary.render_summary(bare)
        assert "the row marked CANNOT RUN names which" not in rendered_bare, rendered_bare
        assert "defect in the canary itself" in rendered_bare, rendered_bare
    only_info = canary.Report()
    only_info.add(canary.Arm("release-age", canary.OK, "uploaded unknown ago", gated=False))
    rendered_info = canary.render_summary(only_info)
    assert "the row marked CANNOT RUN names which" not in rendered_info, rendered_info
    assert "defect in the canary itself" in rendered_info, rendered_info
    table_lines = [ln for ln in summary.splitlines() if ln.startswith("| `")]
    assert table_lines, summary
    for row in table_lines:
        assert "| yes |" in row or "| no |" in row, row
    assert any("| CANNOT RUN |" in row for row in table_lines), summary
    # ...and an UNKNOWN arm is exactly the row that carries it.
    unknown_rows = [row for row in table_lines if "| CANNOT RUN |" in row]
    assert any("client-published" in row for row in unknown_rows), summary

    # ⛔ AND AN UNNAMED STATUS RENDERS AS THE FAIL-CLOSED LABEL, not as itself and not as a
    # missing cell (#12589 review round 8, P2). "PASS" is the spelling THIS summary prints for a
    # green verdict, which is exactly why an arm status must never be rendered by vocabulary: an
    # arm carrying it must fall into the same gap the arms' allow-list polarity reserves.
    odd = canary.Report()
    odd.add(canary.Arm("odd", "PASS", "an unnamed arm status"))
    rendered_odd = canary.render_summary(odd)
    assert "| `odd` | yes | CANNOT RUN |" in rendered_odd, rendered_odd
    assert "| `odd` | yes | PASS |" not in rendered_odd, rendered_odd

    # ⛔ AND IT MUST NOT RULE THE TRANSPORT OUT EITHER (#12589 round 5, P2). A round-4 paragraph
    # claimed "this is NOT an unreadable index when every index row above reads OK" — which is
    # exactly backwards: when every index row reads OK, the CANNOT RUN arm is `engine-init`, i.e.
    # the install really did fail. Both shapes that reach that state are pinned here.
    timed_out = canary.build_report(
        engine_doc=_engine_doc(),
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
        tree_version="0.2.0",
        init_arm=canary.Arm("engine-init", canary.UNKNOWN, "timed out after 600s"),
        now=NOW,
    )
    rendered = canary.render_summary(timed_out)
    assert "NOT an unreadable index" not in rendered, rendered
    assert "could not READ something it needs" in rendered, rendered
    assert timed_out.get("engine-init").status == canary.UNKNOWN

    # ⛔ AND THE INFORMATIONAL ROW MUST NOT LOSE A TIMESTAMP IT DID READ (#12589 round 4, P3):
    # a timestamp needs neither `yanked` nor `filename`, so a partly-readable listing still says
    # when the release first existed. "uploaded unknown ago" for a stamp that was read is a lie,
    # and it is the same "the whole value was dropped" defect the helper change removed.
    engine = {"info": {"version": "0.2.0"}, "releases": {"0.2.0": [_CLIENT_GOOD, "junk"]}}
    assert canary.upload_time(engine, "0.2.0") is not None
    report = canary.build_report(
        engine_doc=engine,
        client_doc={"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
        tree_version="0.2.0",
        init_arm=_ok_init_arm(),
        now=NOW,
    )
    rendered = canary.render_summary(report)
    assert "uploaded unknown" not in rendered, rendered
    # NOW is 2026-11-13 and the fixture's only readable stamp is 2026-08-08 -> 97d.
    assert "97d" in rendered, rendered


def test_installability_is_ONE_definition_of_the_installable_question():
    """Three states, one reader — a caller cannot fold "could not read" into "read and negative".

    (#12589 review round 7, P2.) The OK arm asks "is the ADVERTISED release installable?" to decide
    which sentence to print. It first asked `release_files(...) is None`, which only sees
    STRUCTURAL unreadability — so a value whose entries were dropped for an unreadable
    `yanked`/`filename` (or an empty `filename`) looked READ and NEGATIVE, and the arm printed the
    decisive "not resolvable for an unpinned install here" about a listing it had never read.
    """
    doc = {"info": {"version": "0.3.0"}}
    good = {**_CLIENT_GOOD, "filename": "tortoise_client-0.3.0-py3-none-any.whl"}
    yanked = {**good, "yanked": True}

    assert canary.installability({**doc, "releases": {"0.3.0": [good]}}, "0.3.0") == (
        canary.INSTALLABLE
    )
    # ⛔ AND THE ORDERING IS LOAD-BEARING (#12589 review round 14, P3). An installable entry
    # OUTRANKS an unreadable sibling: `installable` is checked before `unreadable`, so a value
    # holding one good file and one unreadable one is INSTALLABLE. Swapping the two checks left
    # 88 tests green AND rendered the self-contradictory "advertised latest 0.3.0 could not be READ
    # here; installable: 0.3.0" — a sentence about a listing the arm had just resolved.
    mixed = {**doc, "releases": {"0.3.0": [good, {}]}}
    assert canary.installability(mixed, "0.3.0") == canary.INSTALLABLE
    # ⛔ AND A RECOGNISED STATE MUST NOT RENDER AS UNRECOGNISED (#12589 review round 15, P3).
    # Deleting the `INSTALLABILITY_UNKNOWN` branch fell through to the last `else:` and published
    # "unrecognised installability 'unreadable'" — with 89 tests green, because both pins were
    # substrings the fallback also contains. The `;` closes the recognised form's clause.
    recognised = _client_report(
        {
            "info": {"version": "0.3.0"},
            "releases": {"0.2.0": [good], "0.3.0": {}},
        }
    ).get("client-published")
    assert "advertised latest 0.3.0 could not be READ here;" in recognised.detail, recognised.detail
    assert "unrecognised" not in recognised.detail, recognised.detail

    mixed_arm = _client_report(mixed).get("client-published")
    assert mixed_arm.status == canary.OK, (mixed_arm.status, mixed_arm.detail)
    assert "advertised latest 0.3.0 is installable" in mixed_arm.detail, mixed_arm.detail
    assert "could not be READ here" not in mixed_arm.detail, mixed_arm.detail
    # ...the partial read is still DISCLOSED, so the green does not imply a complete parse.
    assert canary.RELEASES_UNREADABLE in mixed_arm.detail, mixed_arm.detail
    # ANSWERS.
    for answered in ({"0.3.0": []}, {"0.3.0": [yanked]}, {}):
        assert canary.installability({**doc, "releases": answered}, "0.3.0") == (
            canary.NOT_INSTALLABLE
        ), answered
    assert canary.installability({**doc, "releases": {"0.2.0": [good]}}, "0.3.0") == (
        canary.NOT_INSTALLABLE
    )  # absent version key in a readable map IS an answer
    # NON-READINGS — including the field-level drops that `release_files` cannot see.
    for unreadable in (
        {"0.3.0": {}},
        {"0.3.0": ["junk"]},
        {"0.3.0": [{**good, "yanked": "false"}]},
        {"0.3.0": [{**good, "filename": ""}]},
        {"0.3.0": [{"yanked": False}]},
    ):
        assert canary.installability({**doc, "releases": unreadable}, "0.3.0") == (
            canary.INSTALLABILITY_UNKNOWN
        ), unreadable
    assert canary.installability({**doc, "releases": "x"}, "0.3.0") == (
        canary.INSTALLABILITY_UNKNOWN
    )

    # AND THE ARM: a mixed map whose ADVERTISED value was dropped at field level must take the
    # "could not be READ" sentence, not the decisive negative (which is reserved for a complete
    # read). This is the shape the round-7 finding reproduced.
    for field_shape in (
        {"filename": "tortoise_client-0.3.0-py3-none-any.whl", "yanked": "false"},
        {"yanked": False},
        {"filename": ""},
    ):
        report = _client_report(
            {**doc, "releases": {"0.2.0": [_CLIENT_GOOD], "0.3.0": [field_shape]}}
        )
        arm = report.get("client-published")
        assert arm.status == canary.OK, (field_shape, arm.status, arm.detail)
        assert "advertised latest 0.3.0 could not be READ here" in arm.detail, (
            field_shape,
            arm.detail,
        )
        assert "not resolvable for an unpinned install" not in arm.detail, arm.detail


def test_a_non_STRING_version_is_the_same_class_one_key_further_in():
    """`{"info": {"version": 0.3}}` must not be a release order while `{"releases": {"0.3.0": {}}}` is not.

    ⛔ THE INCONSISTENCY THIS PINS was a review finding on the first revision of this change: the
    map's VALUES had just been made non-readings, while a version value two lines away was still
    coerced to "" and reported as "carries no latest version" — a FAIL, exit 1, an irreversible
    upload order — off a document that carries a version. Same class, same doctrine, same verdict.
    """
    for version in (0.3, True, ["0.3.0"], {"v": "0.3.0"}, None):
        doc = {"info": {"version": version}}
        assert canary.version_unreadable(doc) is True, version
        assert canary.latest_version(doc) == "", version
        # client arm
        report = _client_report({**doc, "releases": {"0.3.0": [_CLIENT_GOOD]}})
        arm = report.get("client-published")
        assert arm.status == canary.UNKNOWN, (version, arm.status, arm.detail)
        assert canary.VERSION_UNREADABLE in arm.detail, arm.detail
        assert "carries no published version" not in arm.detail, arm.detail
        assert "not a reason to cut a release" in arm.detail.lower(), arm.detail
        # engine arm
        report = _client_report(
            {"info": {"version": "0.3.0"}, "releases": {"0.3.0": [_CLIENT_GOOD]}},
            engine_doc={"info": {"version": version}},
        )
        arm = report.get("engine-index")
        assert arm.status == canary.UNKNOWN, (version, arm.status, arm.detail)
        assert canary.VERSION_UNREADABLE in arm.detail, arm.detail
        assert report.exit_code == 2, (version, report.exit_code)

    # The states that ANSWER stay on the FAIL arm: no `version` key at all, and an empty string.
    for answered in ({}, {"info": {}}, {"info": {"version": ""}}):
        assert canary.version_unreadable(answered) is False, answered
        report = _client_report({**answered, "releases": {"0.3.0": [_CLIENT_GOOD]}})
        assert report.get("client-published").status == canary.FAIL, answered
        assert report.exit_code == 1, (answered, report.exit_code)
