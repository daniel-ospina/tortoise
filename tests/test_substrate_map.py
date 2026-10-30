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

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import substrate_map_check as smc  # noqa: E402

DOC = REPO / "docs" / "architecture" / "SUBSTRATE-REBUILD-MAP.md"


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_the_shipped_map_is_clean() -> None:
    """Fails if the document and the tree disagree about any guarded claim.

    Reachable today: `measure()` reads the real document, the real
    `docs/00_index.md`, the real `tortoise/` tree and `git merge-base`. Dropping
    any one of them (e.g. unregistering the doc from the index) reds this.
    """
    result = smc.measure()
    assert result["verdict"] == "CLEAN", result["problems"]
    assert result["problems"] == []


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
    """
    doc = _doc().replace("`config/ci-surfaces.yml`", "`tests/*.py`", 1)
    assert smc.check_lane_families(doc) == []


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


def test_an_unresolvable_measured_at_commit_is_drift(monkeypatch) -> None:
    """Fails if a missing/typo'd sha is treated as clean.

    Reachable: `git merge-base --is-ancestor` exits **128** (not 1) on an object
    it does not have — a shallow/partial clone, or a sha copied from another
    repo. Falling through to 0 on 128 is the no-op-gate shape this asserts
    against. The probe is stubbed here on purpose: the REAL 128 path costs ~24 s
    on this box's partial clone (git attempts a lazy fetch and burns CPU in ref
    enumeration), and a hermetic test must not pay that. The real call is
    exercised by `test_the_shipped_map_is_clean`.
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
    problems = smc.check_measured_at_commit(doc)
    assert any("cannot be resolved" in p for p in problems), problems

    monkeypatch.setattr(
        smc.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
    problems = smc.check_measured_at_commit(doc)
    assert any("not an ancestor" in p for p in problems), problems


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
    problems = smc.check_measured_at_commit(_doc())
    assert any("could not be verified within" in p for p in problems), problems


def test_a_missing_measured_at_commit_is_drift() -> None:
    """Fails if the provenance field is optional.

    Reachable: a re-cut that forgets the frontmatter field would otherwise read
    as clean while naming no tree at all (`tools/finding_provenance.py`'s rule).
    """
    doc = "\n".join(
        line for line in _doc().splitlines() if not line.startswith("measured_at_commit:")
    )
    assert smc.check_measured_at_commit(doc) == [
        "frontmatter carries no `measured_at_commit: <sha>`"
    ]


def test_the_json_manifest_names_what_it_does_not_check() -> None:
    """Fails if the tool ever implies it guards the tracker half.

    Reachable: issue state needs the network and the doc records a dated
    measurement instead. A guard that claimed it and silently could not would be
    the false PASS the document exists to prevent.
    """
    result = smc.measure()
    assert any("issue_state" in entry for entry in result["not_checked"]), result
    assert "lane_and_hotspot_file_families" in result["checked"]


def test_cli_exit_codes() -> None:
    """0 clean · 1 drifted · 2 usage — and 2 is never 0.

    Reachable: all three arms are exercised on the shipped tree (clean) and on
    an unknown flag (usage). The drift arm is covered by the negative controls
    above; here it is only asserted that no arm collapses into another.
    """
    tool = REPO / "tools" / "substrate_map_check.py"
    clean = subprocess.run(
        [sys.executable, str(tool), "--check"], capture_output=True, text=True
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    usage = subprocess.run(
        [sys.executable, str(tool), "--nonsense"], capture_output=True, text=True
    )
    assert usage.returncode == 2, usage.stdout + usage.stderr

    manifest = subprocess.run(
        [sys.executable, str(tool), "--json"], capture_output=True, text=True
    )
    assert manifest.returncode == 0, manifest.stdout + manifest.stderr
    payload = json.loads(manifest.stdout)
    assert payload["verdict"] == "CLEAN"
    assert payload["document"] == "docs/architecture/SUBSTRATE-REBUILD-MAP.md"
