"""Hermetic tests for `tools/substrate_map_check.py` (#5086).

The substrate map (`docs/architecture/SUBSTRATE-REBUILD-MAP.md`) replaced an
issue-only artifact that went stale **three times**, each time silently. This
file pins the guard that makes the machine-checkable half of it falsifiable.

No network, no DB, no gh: the tracker half of the map is deliberately NOT
guarded (see the tool's docstring), so nothing here depends on it.

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT VALUE/STATE THAT MAKES IT
FAIL and (b) WHY THAT STATE IS REACHABLE. A guard whose negative case cannot be
reached is a test that cannot fail — it proves the code ran, not that the tree
agrees with the document. The negative controls below therefore mutate a COPY of
the real document / a tmp index / a tmp source tree, rather than asserting only
that the shipped tree is clean.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import substrate_map_check as smc  # noqa: E402

DOC = REPO / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _ancestry_is_verifiable_here() -> bool:
    """Whether THIS clone carries the object `measured_at_commit` names.

    Probed with its own `git` call on purpose — NOT via `smc._is_shallow_clone()`,
    so a bug in the helper cannot make the test agree with it. `actions/checkout`
    fetches one commit by default, so in the CI `test` job this is False and the
    ancestry claim is unprovable there; the REQUIRED `docs` job (fetch-depth 0)
    is where it is proved.
    """
    sha = smc._FRONTMATTER_SHA.search(_doc()).group(1)
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    return probe.returncode == 0


def test_the_shipped_map_is_clean() -> None:
    """Fails if the document and the tree disagree about any guarded claim.

    Reachable today: `measure()` reads the real document, the real
    `docs/00_index.md`, the real `tortoise/` tree and `git merge-base`. Dropping
    any one of them (e.g. unregistering the doc from the index) reds this.

    ⚠️ The ancestry claim is the ONE exception and it is stated, not silently
    skipped: a `fetch-depth: 1` checkout (the CI `test` job's) does not carry the
    object, so the question is unanswerable THERE. The offline half is asserted
    FIRST — in a shallow clone a real drift still reds this — and only then is the
    ancestry half handed to the job that has the history.
    """
    result = smc.measure()
    assert result["problems"] == [], result["problems"]
    if not _ancestry_is_verifiable_here():
        assert result["verdict"] == "UNVERIFIED", result
        assert len(result["unverified"]) == 1, result["unverified"]
        assert "cannot be verified here" in result["unverified"][0]
        pytest.skip(
            "shallow checkout: `measured_at_commit` is absent here, so the offline "
            "half is all this job proves; the ancestry claim is proved by the "
            "REQUIRED `docs` job, which checks out at fetch-depth: 0 and runs "
            "`tools/substrate_map_check.py --check` unconditionally"
        )
    assert result["verdict"] == "CLEAN", result["problems"]



def test_a_lane_family_that_does_not_resolve_is_drift() -> None:
    """Fails if a §3/§4 path is not checked for existence.

    Reachable: the mutated copy names `tortoise/does_not_exist.py` inside the
    lane table — a family a lane row could plausibly declare and a rename could
    plausibly break.
    """
    doc = _doc().replace("`tortoise/fanout.py`", "`tortoise/does_not_exist.py`", 1)
    problems = smc.check_lane_families(doc)
    assert any("tortoise/does_not_exist.py" in p for p in problems), problems


def test_the_lane_table_is_actually_enumerated() -> None:
    """Fails if §3's families stop being parsed, leaving only §4's checked.

    Reachable: §3 originally wrote its families WITHOUT the `tortoise/` prefix
    (`fanout.py`), so the checker silently saw zero of that table's paths and
    this guard would have gone green over an entirely unchecked lane table. The
    floor below is the measured count at the tree this was written against (20
    distinct cited paths, 18 of them code), so a table-shape change reds here
    rather than passing quietly.
    """
    doc = _doc()
    section = smc._section(doc, "## 3.") + smc._section(doc, "## 4.")
    cited = {raw.split(":")[0].rstrip("/") for raw in smc._BACKTICK_PATH.findall(section)}
    code = {
        p
        for p in cited
        if not p.endswith((".md", ".yml")) and "*" not in p and (REPO / p).exists()
    }
    assert len(code) >= 18, sorted(code)
    assert "tortoise/fanout.py" in code


def test_a_glob_is_not_a_family_path() -> None:
    """Fails if the checker treats `tests/*.py` (a rule) as a required file.

    Reachable: the hotspot section legitimately says `tests/*.py`, and an
    existence check over a glob would report a permanent false drift — a guard
    that reds on a correct document is the failure mode this asserts against.

    ⚠️ The substitution is only meaningful if its anchor is IN a section
    `check_lane_families` reads: a `== []` over a substitution that landed
    nowhere passes silently. So the anchor is asserted first (#5086 review P1
    cycle 1, closing the one vacuity the reviewer flagged).
    """
    doc = _doc()
    substituted = doc.replace("`config/ci-surfaces.yml`", "`tests/*.py`", 1)
    assert substituted != doc, "the §4 anchor `config/ci-surfaces.yml` is gone"
    in_tables = smc._section(doc, "## 3.") + smc._section(doc, "## 4.")
    assert "`config/ci-surfaces.yml`" in in_tables, (
        "the anchor must sit inside §3/§4, or this control substitutes nothing "
        "the checker reads and its `== []` proves nothing"
    )
    assert smc.check_lane_families(substituted) == []


def test_an_interface_symbol_missing_from_its_file_is_drift(
    monkeypatch,
) -> None:
    """Fails if §5's symbol placement is trusted rather than verified.

    Reachable: the retargeted pair below is a real file (`tortoise/fanout.py`)
    that genuinely does not declare `stub_key` — exactly what a move/rename in
    `projection/edges.py` would produce.
    """
    monkeypatch.setattr(smc, "INTERFACE_SYMBOLS", (("def stub_key", "tortoise/fanout.py"),))
    problems = smc.check_interface_symbols(_doc())
    assert any("stub_key" in p and "tortoise/fanout.py" in p for p in problems), problems


def test_an_interface_symbol_dropped_from_the_document_is_drift() -> None:
    """Fails if the doc can stop making a claim while the guard keeps checking it.

    Reachable: deleting the symbol's name from the document is a one-line edit
    that leaves the interface map claiming nothing while the tool still reports
    CLEAN — i.e. the guard silently widening past what anyone reads.
    """
    doc = _doc().replace("compile_value_brief", "compile_value_summary")
    problems = smc.check_interface_symbols(doc)
    assert any("compile_value_brief" in p and "dropped" in p for p in problems), problems


def test_the_d10_retirement_mechanism_is_checked(monkeypatch) -> None:
    """Fails if the retired-key allowlist / relabel migration stops being measured.

    Reachable: `migrate_kinds.py` does not declare `build_master_list`, so
    retargeting the pair is a real file that genuinely lacks the symbol — exactly
    what deleting the migration would leave behind. The `:Document` label itself
    is deliberately NOT regex-checked (17 prose mentions at this head), so the
    retirement is guarded through these two symbols instead.
    """
    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("def relabel_legacy_documents", "tortoise/migrate_kinds.py"),),
    )
    assert smc.check_interface_symbols(_doc()) == []

    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("def relabel_legacy_documents", "tortoise/extractor_v2.py"),),
    )
    problems = smc.check_interface_symbols(_doc())
    assert any("relabel_legacy_documents" in p for p in problems), problems


def test_an_unregistered_document_is_drift(monkeypatch, tmp_path: Path) -> None:
    """Fails if docs registration is assumed rather than checked.

    Reachable: an unindexed doc is unfindable from `docs/00_index.md`, which is
    how the map would return to existing only where someone remembers it.
    """
    index = tmp_path / "00_index.md"
    index.write_text("| Area | Path |\n| --- | --- |\n", encoding="utf-8")
    monkeypatch.setattr(smc, "INDEX", index)
    problems = smc.check_registered(_doc())
    assert any("not registered" in p for p in problems), problems


def test_an_absent_object_is_a_state_error_not_document_drift(monkeypatch) -> None:
    """Fails if an object this clone does NOT have is scored as document drift.

    Reachable, and MEASURED in CI before this fix: `actions/checkout` fetches one
    commit by default, so the `test` job's clone does not carry the
    `measured_at_commit` object and `git merge-base --is-ancestor` exits **128**
    (reproduced in a real depth-1 clone of this repo: `fatal: Not a valid commit
    name`, exit 128). Scoring that as `DRIFTED` redded three tests in the shard AND
    told the reader "the document and the tree disagree — do not delete the row to
    make this green", the wrong instruction for a shallow clone: nothing is wrong
    with the document. It must read as a STATE ERROR (exit 2) — never a pass, and
    never a drift. The probe is stubbed here on purpose: the real 128 path costs
    ~24 s on this box's partial clone (git attempts a lazy fetch).
    """
    doc = _doc().replace(
        "measured_at_commit: 4f68a2aa73be4572c3ea88367981ca874ca0e54b",
        "measured_at_commit: 0123456789abcdef0123456789abcdef01234567",
        1,
    )
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0], 128, "", "fatal: Not a valid commit name"
        ),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: True)
    problems, unverified = smc.check_measured_at_commit(doc)
    assert problems == [], problems
    assert len(unverified) == 1, unverified
    assert "cannot be verified here" in unverified[0]
    # The report must name the CAUSE, so the reader is not sent to the document.
    assert "SHALLOW" in unverified[0]
    assert "not a valid commit" in unverified[0].lower()


def test_a_sha_that_is_not_an_ancestor_is_drift(monkeypatch) -> None:
    """Fails if a genuinely unrelated tree is reported as merely unverified.

    Reachable: exit **1** is `git merge-base --is-ancestor`'s "no, not an
    ancestor" — a re-cut against a branch, a rebase that dropped the commit, or a
    sha from another repository. That IS a disagreement between the document and
    the tree, so it must never collapse into the state-error arm.
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
    problems, unverified = smc.check_measured_at_commit(_doc())
    assert any("not an ancestor" in p for p in problems), problems
    assert unverified == [], unverified


def test_an_unbounded_ancestry_probe_cannot_hang_the_gate(monkeypatch) -> None:
    """Fails if a slow/hanging git probe is allowed to read as clean.

    Reachable: measured on this box's partial clone, an absent sha makes
    `git merge-base --is-ancestor` spend ~24 s attempting a lazy fetch. Without
    the bound the gate blocks; with the bound as a PASS it would silently certify
    a tree nobody verified — so the timeout must surface as a problem.
    """
    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=smc.ANCESTRY_TIMEOUT_S)

    monkeypatch.setattr(smc.subprocess, "run", _boom)
    problems, _unverified = smc.check_measured_at_commit(_doc())
    assert any("could not be verified within" in p for p in problems), problems


def test_a_missing_measured_at_commit_is_drift() -> None:
    """Fails if the provenance field is optional.

    Reachable: a re-cut that forgets the frontmatter field would otherwise read
    as clean while naming no tree at all (`tools/finding_provenance.py`'s rule).
    """
    doc = "\n".join(
        line for line in _doc().splitlines() if not line.startswith("measured_at_commit:")
    )
    assert smc.check_measured_at_commit(doc) == (
        ["frontmatter carries no `measured_at_commit: <sha>`"],
        [],
    )


def test_a_constant_the_document_places_must_be_DECLARED_not_mentioned(
    monkeypatch, tmp_path: Path
) -> None:
    """Fails if "still declared" is a bare substring match over the file.

    Reachable on the SHIPPED tree: `_DOC_RETIRED_KEYS` is declared once and
    mentioned on six further lines of `projection/entities.py` (a docstring, two
    comments, three `self.` readers — `_SOURCE_HANDLED` has seven). So blanking
    the D10 retirement ALLOWLIST itself — the mechanism §5 of the document and the
    tool docstring both say this guard holds, on the one claim the doc compensates
    for by dropping the `:Document` regex — still returned `[]`: a false CLEAN.
    The mutation below is exactly that state: the declaration line replaced, every
    mention kept.
    """
    monkeypatch.setattr(
        smc,
        "INTERFACE_SYMBOLS",
        (("_DOC_RETIRED_KEYS", "tortoise/projection/entities.py"),),
    )
    # Positive control first: the shipped declaration satisfies the check, so the
    # failure below cannot come from the pair simply never resolving.
    assert smc.check_interface_symbols(_doc()) == []

    lines = (REPO / "tortoise/projection/entities.py").read_text(encoding="utf-8").splitlines(
        keepends=True
    )
    decl = next(
        i for i, line in enumerate(lines) if line.lstrip().startswith("_DOC_RETIRED_KEYS:")
    )
    lines[decl] = "    # D10 allowlist declaration REMOVED by this test\n"
    mutated = "".join(lines)
    assert "_DOC_RETIRED_KEYS" in mutated, "the mentions must survive the mutation"

    (tmp_path / "tortoise" / "projection").mkdir(parents=True)
    (tmp_path / "tortoise" / "projection" / "entities.py").write_text(
        mutated, encoding="utf-8"
    )
    monkeypatch.setattr(smc, "ROOT", tmp_path)
    problems = smc.check_interface_symbols(_doc())
    assert any("_DOC_RETIRED_KEYS declaration missing" in p for p in problems), problems


def test_a_state_error_exits_2_not_the_drift_code(monkeypatch, tmp_path: Path) -> None:
    """Fails if "the map is GONE" reports the exit code that means "the map DRIFTED".

    Reachable: `raise SystemExit("<message>")` prints the message and exits **1**
    — the DRIFTED code — so the documented tri-state (0 clean · 1 drifted · 2 a
    state error) was implemented for USAGE only, and an operator separating the
    two by exit code re-measured the document instead of fixing the checkout. Both
    state paths are asserted here: the absent document in-process, and the whole
    CLI end-to-end (a copy of the tool with nothing beside it — the same state a
    `mv` of the document produces).
    """
    missing = tmp_path / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"
    monkeypatch.setattr(smc, "ROOT", tmp_path)
    monkeypatch.setattr(smc, "DOC", missing)
    with pytest.raises(SystemExit) as excinfo:
        smc.measure()
    assert excinfo.value.code == 2, excinfo.value.code

    tool_dir = tmp_path / "tools"
    tool_dir.mkdir()
    tool = tool_dir / "substrate_map_check.py"
    tool.write_text(Path(smc.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    run = subprocess.run([sys.executable, str(tool), "--check"], capture_output=True, text=True)
    assert run.returncode == 2, run.stdout + run.stderr
    assert "state error" in run.stderr, run.stderr


def test_an_unverified_ancestry_probe_is_a_state_error_exit(monkeypatch) -> None:
    """Fails if UNVERIFIED becomes a pass (exit 0) or a drift (exit 1).

    Reachable in CI by construction: the `test` job's checkout is shallow, so
    `measure()` returns UNVERIFIED there. That must exit **2** — the documented
    "an input is unreadable/absent, a state error, NOT a pass" arm — so a caller
    that only checks for 0 cannot mistake it for a clean map.
    """
    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 128, "", "fatal: Not a valid commit name"),
    )
    monkeypatch.setattr(smc, "_is_shallow_clone", lambda: True)
    result = smc.measure()
    assert result["verdict"] == "UNVERIFIED", result
    assert result["problems"] == [], result["problems"]
    assert smc.main(["substrate_map_check.py", "--check"]) == 2


def test_the_json_manifest_names_what_it_does_not_check() -> None:
    """Fails if the tool ever implies it guards the tracker half.

    Reachable: issue state needs the network and the doc records a dated
    measurement instead. A guard that claimed it and silently could not would be
    the false PASS the document exists to prevent.
    """
    result = smc.measure()
    assert any("issue_state" in entry for entry in result["not_checked"]), result
    assert "lane_and_hotspot_file_families" in result["checked"]
    # The unverified channel is part of the manifest: a caller must be able to see
    # WHICH claim a state error stopped, instead of inferring it from the exit code.
    assert "unverified" in result, result


def test_cli_exit_codes() -> None:
    """0 clean · 1 drifted · 2 usage/state — and no arm collapses into another.

    Reachable: the clean arm on the shipped tree, the usage arm on an unknown
    flag, and (in a `fetch-depth: 1` checkout, which is what CI gives the `test`
    job) the state arm with 2 in place of 0. Asserting `0` unconditionally here is
    what redded this file in CI: the clone cannot answer the ancestry question.
    The drift arm is covered by the negative controls above.
    """
    tool = REPO / "tools" / "substrate_map_check.py"
    clean = subprocess.run(
        [sys.executable, str(tool), "--check"], capture_output=True, text=True
    )
    expected = 0 if _ancestry_is_verifiable_here() else 2
    assert clean.returncode == expected, clean.stdout + clean.stderr
    if expected == 2:
        assert "UNVERIFIED" in clean.stdout, clean.stdout
        assert "cannot be verified here" in clean.stdout, clean.stdout
    usage = subprocess.run(
        [sys.executable, str(tool), "--nonsense"], capture_output=True, text=True
    )
    assert usage.returncode == 2, usage.stdout + usage.stderr

    manifest = subprocess.run(
        [sys.executable, str(tool), "--json"], capture_output=True, text=True
    )
    assert manifest.returncode == expected, manifest.stdout + manifest.stderr
    payload = json.loads(manifest.stdout)
    assert payload["verdict"] == ("CLEAN" if expected == 0 else "UNVERIFIED"), payload
    assert payload["document"] == "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
