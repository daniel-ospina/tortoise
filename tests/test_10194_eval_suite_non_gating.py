"""#10194 — `tests/eval/**` + `tests/longmem_eval/**` do not gate a merge.

Owner decision A (2026-10-27): the eval/longmem subtrees leave the merge-gating
shard set and run from a separate, manual, non-gating workflow. The evidence is
in the issue (three `python-ci` shards were the top CPU consumers on a
self-hosted runner while mixing these files with ordinary correctness tests).

This file is the DURABLE half of that change: the config edit is one line and
would be silently reverted by the next person who reads "evals should run" as
"evals should gate". It asserts the contract in both directions, because the two
failure modes are opposite and equally silent:

  (a) a subtree file is back in the GATING set — the exclusion stopped
      excluding, and the gate is paying again; and
  (b) a subtree file is reachable from NO workflow — it is off the gate AND
      runs nowhere, so a calibration instrument silently stops being run while
      every guard reports green (the lane's own docstring names this).

The mutation each assertion is for is named in its test. Mutation M1 (put one
eval path back into the gating set) reddens
`test_no_subtree_file_is_in_any_gating_leg` with the file named in the message.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import ci_selection as cs  # noqa: I001,E402


#: The two subtrees the owner took out of the gate (#10194). Written here as
#: PREFIXES, not as 42 paths: the point of the change is that the subtree is the
#: unit, so a new file under it is covered without editing this list.
SUBTREES = ("eval/", "longmem_eval/")

#: The manual, non-gating runner the subtrees moved to.
EVAL_WORKFLOW = REPO / ".github" / "workflows" / "eval-suite-manual.yml"

#: The EMBEDDED-shape, non-gating runner that keeps the lane's LITERAL entries.
EMBEDDED_WORKFLOW = REPO / ".github" / "workflows" / "evals-on-demand.yml"

#: The gating workflow whose shard set the 42 files left.
GATING_WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"

#: The CLI verb the manual workflow must invoke, and the flag spelling itself.
#: One string, because the failure this pins is a MISMATCH between the workflow's
#: text and the CLI's argparse — a test that only string-matched the workflow
#: would pass green over it (measured: an earlier revision of this file did, and
#: the workflow called `--non-gating-subtree-files` while the CLI defined
#: `--non-gating-files`, so every dispatch would have run ZERO files under
#: `bash -e`).
RESOLVER_FLAG = "--non-gating-subtree-files"


def _resolver_output() -> list[str]:
    """RUN the resolver — the same command the workflow's step runs.

    Executed rather than imported on purpose: the workflow shells out, so the
    only honest check of "the workflow can resolve its list" is to run the
    identical argv. An in-process call to `on_demand_subtree_files()` would miss
    exactly the defect class this pins (a flag the CLI does not define, an exit
    status, a traceback).
    """
    proc = subprocess.run(
        [sys.executable, "tools/ci_selection.py", RESOLVER_FLAG],
        cwd=REPO, capture_output=True, text=True, timeout=300,
        check=False)
    assert proc.returncode == 0, (
        f"`tools/ci_selection.py {RESOLVER_FLAG}` exited {proc.returncode} — the "
        "manual eval workflow invokes exactly this argv, so a non-zero exit here "
        f"means every dispatch of it runs ZERO files.\nstderr:\n{proc.stderr[-3000:]}")
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]



def _manifest() -> dict:
    return cs.load_manifest()


def _subtree_files() -> list[str]:
    """Every `test_*.py` under the two subtrees on DISK (tests/-relative)."""
    found = []
    for prefix in SUBTREES:
        found.extend(str(p.relative_to(cs.TESTS_DIR))
                     for p in sorted((cs.TESTS_DIR / prefix).rglob("test_*.py")))
    return sorted(found)


def _bare(name: str) -> str:
    return name[:-3] if name.endswith(".py") else name


def _gating_sets(manifest: dict) -> dict[str, list[str]]:
    """Every GATING file set the selector produces, bare-named, per leg.

    Built from the producers themselves (never from a re-implementation): the
    push matrix's fast shards, its slow/carve-out/env-broken legs, the tier-2
    selection for a change in each relevant surface, and the full selection. A
    set added to the selector later is covered only if it is added here too —
    which is why `_leg_names()` below asserts this map's leg list, so a silent
    omission is impossible to mistake for a pass.
    """
    legs = cs.push_legs(manifest)
    out: dict[str, set[str]] = {
        "fast_pool": {_bare(f) for f in cs.fast_pool(manifest)},
        "slow": set(legs["slow"]),
        "env_broken": set(legs["env_broken"]),
        "carve_out": set(legs["carve_out"]),
    }
    for shard in legs["shards"]:
        out[f"test ({shard['name']})"] = set(shard["files"])
    for shard in legs["slow_shards"]:
        out[f"test-slow ({shard['name']})"] = set(shard["files"])
    for shard in legs["carve_shards"]:
        out[f"test-carve-out ({shard['name']})"] = set(shard["files"])
    # Tier-2 (PR) selection: the path that would re-select the eval files if the
    # lane were subtracted on `push` only. Driven by one changed file per
    # surface-owner of these subtrees, plus a direct eval-test edit.
    for changed in ("tests/eval/ask/test_ask_corpus.py",
                    "tests/longmem_eval/test_vector_arm.py",
                    "tools/longmem_eval/run.py",
                    "tortoise/embeddings.py"):
        sel = cs.select([changed], "pull_request", manifest)
        out[f"tier-2 test_files [{changed}]"] = {_bare(f) for f in sel["test_files"]
                                                 if f != "ALL"}
        out[f"tier-2 slow_selected [{changed}]"] = {_bare(f)
                                                    for f in sel["slow_selected"]}
    full = cs.select([], "push", manifest)
    out["push slow_selected"] = {_bare(f) for f in full["slow_selected"]}
    return out


EXPECTED_LEGS = {
    "fast_pool", "slow", "env_broken", "carve_out",
    *(f"test ({c})" for c in "abcdefghi"),
    *(f"test-slow ({c})" for c in "abcde"),
    *(f"test-carve-out ({c})" for c in "abc"),
    *(f"tier-2 test_files [{c}]" for c in
      ("tests/eval/ask/test_ask_corpus.py", "tests/longmem_eval/test_vector_arm.py",
       "tools/longmem_eval/run.py", "tortoise/embeddings.py")),
    *(f"tier-2 slow_selected [{c}]" for c in
      ("tests/eval/ask/test_ask_corpus.py", "tests/longmem_eval/test_vector_arm.py",
       "tools/longmem_eval/run.py", "tortoise/embeddings.py")),
    "push slow_selected",
}


def test_the_subtree_is_not_empty():
    """Non-vacuity: 42 files at f8970b53. An empty walk would make every
    assertion below pass while testing nothing — the classic dead guard."""
    files = _subtree_files()
    assert len(files) >= 40, (
        f"expected the eval subtrees to hold dozens of test files, walked "
        f"{len(files)}: {files[:5]} — a moved/renamed subtree would make every "
        f"assertion in this file vacuous, so it is asserted first")
    assert any(f.startswith("eval/") for f in files)
    assert any(f.startswith("longmem_eval/") for f in files)


def test_the_lane_resolves_the_whole_subtree_from_disk():
    """The lane must be a SUBTREE statement, not 42 literals.

    This is the property that makes the exclusion survive: a new test file under
    `tests/eval/` is off the gate with no config edit. A literal-only lane would
    put it straight back into a gating shard, and nobody would be told.
    """
    manifest = _manifest()
    lane = cs.on_demand_files(manifest)
    missing = sorted(set(_subtree_files()) - lane)
    assert not missing, (
        f"{len(missing)} subtree file(s) are NOT in the non-gating lane, so they "
        f"are still on a gating leg: {missing[:8]}")
    assert set(cs.on_demand_subtree_prefixes(manifest)) == set(SUBTREES), (
        "the lane must declare BOTH subtrees as `dir/**` entries — the "
        "expansion is what keeps a new eval file off the gate")


def test_no_subtree_file_is_in_any_gating_leg():
    """(a) — the exclusion actually reached EVERY leg producer.

    Mutation M1: put `eval/` back into the gating set (delete the `eval/**`
    entry from `config/ci-surfaces.yml::on_demand`, or drop the subtraction in
    `fast_pool`/`slow_gating_files`/`select`) and this reddens, naming the file
    and the leg it reappeared in.
    """
    manifest = _manifest()
    legs = _gating_sets(manifest)
    assert set(legs) == EXPECTED_LEGS, (
        "the leg list this test walks changed — a new gating set was added to "
        "the selector (or one renamed) and is NOT covered here, which reads as "
        "a pass over a set nobody checked")
    subtree = {_bare(f) for f in _subtree_files()}
    leaks = {leg: sorted(subtree & files) for leg, files in legs.items()
             if subtree & files}
    assert not leaks, (
        "subtree file(s) are in a GATING leg — #10194's exclusion did not reach "
        f"that producer: {leaks}")


def test_ordinary_tests_that_shared_those_shards_are_still_gating():
    """(b)-adjacent, proof 3: the exclusion is SUBTREE-scoped, not shard-wide.

    `tests/test_10017_record_carrier.py` sat in the same invocation as the eval
    files in the measured example. It must still gate, and the shard it lands in
    must still exist — otherwise "the eval files left" would have been achieved
    by emptying the shard they shared.
    """
    manifest = _manifest()
    legs = cs.push_legs(manifest)
    # The ordinary correctness test named in the measured invocation (#10194).
    name = "test_10017_record_carrier"
    shards = [s["name"] for s in legs["shards"] if name in s["files"]]
    assert shards, (
        f"{name} must still run in the gating fast matrix — it is the ordinary "
        "correctness test that shared a shard with the eval files in the "
        "measured example, and the exclusion is SUBTREE-scoped, not shard-wide")
    assert len(shards) == 1, f"{name} must run in exactly one shard, found {shards}"
    assert legs["shards"], "the fast matrix must not be empty"
    assert all(s["files"] for s in legs["shards"]), (
        "no gating shard may be empty — an empty shard greys out green having "
        "run nothing")


def test_the_eval_workflow_is_manual_and_reachable_by_every_subtree_file():
    """(b) — every subtree file is REACHABLE from the new workflow.

    Without this, (a) is satisfied by deleting the files from CI entirely. The
    workflow's list is resolved from the SAME lane accessor the exclusion reads
    (a hardcoded second list would be a second place to forget), and it is
    `workflow_dispatch`-only, which is what makes it unable to gate.
    """
    assert EVAL_WORKFLOW.is_file(), (
        f"the non-gating eval runner is missing ({EVAL_WORKFLOW}) — the subtrees "
        "would then be excluded from the gate and run NOWHERE")
    text = EVAL_WORKFLOW.read_text()
    doc = yaml.safe_load(text)
    triggers = doc.get("on", doc.get(True))
    assert list(triggers) == ["workflow_dispatch"], (
        f"the eval runner must be manual only, got {list(triggers)} — a `push`, "
        "`pull_request` or `schedule` trigger makes it a gate (or a periodic "
        "job the owner has not decided on)")
    assert "non-gating" in doc["name"], (
        "the workflow name must say it is not a gate, so a reader scanning the "
        f"Actions list cannot mistake it for one: {doc['name']!r}")
    assert RESOLVER_FLAG in text or "non_gating_subtree_files" in text, (
        "the workflow must resolve its file list from "
        "tools/ci_selection.py::on_demand_subtree_files() — a hardcoded list is "
        "a second place to forget")
    # The resolver is EXECUTED, not string-matched: the workflow shells out to
    # this argv, so "the flag appears in the file" is not the property that
    # matters — "the argv works" is.
    resolved = _resolver_output()
    # ⛔ EVERY emitted path must EXIST relative to the repo root, because that is
    # the cwd the run step invokes pytest from. This is the assertion whose
    # absence let the first cut ship a resolver that printed tests/-RELATIVE
    # paths (`eval/ask/test_ask_corpus.py`): every one of them is missing at the
    # repo root, pytest exits 4 on path validation, and the lane runs ZERO files
    # while this guard passed on a string match. Measured, not modelled: it
    # stats each path the workflow is about to hand pytest.
    missing = [f for f in resolved if not (REPO / f).exists()]
    assert not missing, (
        f"{len(missing)} path(s) the resolver emits do not exist from the repo "
        f"root, so pytest would exit 4 and the lane would run NOTHING: "
        f"{missing[:5]}")
    expected = [f"tests/{f}" for f in _subtree_files()]
    assert resolved == expected, (
        "the resolver must emit the subtree as pytest-pathable `tests/…` "
        "entries — the lane and the runner have drifted: "
        f"resolved-only={sorted(set(resolved) - set(expected))[:5]} "
        f"disk-only={sorted(set(expected) - set(resolved))[:5]}")
    # ... and the workflow's step must invoke THAT verb, not merely mention it.
    step = text.split("Resolve the non-gating", 1)[-1]
    assert f"tools/ci_selection.py {RESOLVER_FLAG}" in step, (
        "the workflow's resolve step must RUN `tools/ci_selection.py "
        f"{RESOLVER_FLAG}` — the argv verified above")
    assert "> /tmp/lane.txt" in step, (
        "the resolve step must capture the resolver's stdout to the file the run "
        "step reads")


def test_the_embedded_runner_keeps_the_literal_inside_the_subtree():
    """#10206 review: `on_demand: eval/**` must not eat the literal it contains.

    `eval/retrieval/test_integration.py` is BOTH a literal lane entry (the file
    `evals-on-demand.yml` was created for, #4711, 24.9% of all measured test
    time) AND a file inside `eval/**` (#10194's subtree, which moved to the
    docker-shaped runner). Resolving the embedded runner as
    `on_demand_files() - on_demand_subtree_files()` subtracted it with the
    subtree in which it merely happens to live, so a blank dispatch of
    `evals-on-demand.yml` ran `test_perf_lane_volunteer_context.py` — a file
    `perf-lane.yml` already runs — and none of the file the workflow, the config
    and `docs/ci` all name as its own.

    Nothing reddened, because the resolver is a `run:` heredoc no test can see.
    It is a selector function now, and this pins it.
    """
    lane = cs.on_demand_literal_runner_files(_manifest())
    assert "eval/retrieval/test_integration.py" in lane, (
        "the embedded runner must still run the #4711 file its own entry names")
    assert "test_perf_lane_volunteer_context.py" in lane
    assert {f for f in lane if f.startswith("eval/")} == {
        "eval/retrieval/test_integration.py"}, (
        "only the LITERAL survives from the eval subtree in this runner — the "
        "other 25 belong to eval-suite-manual.yml (docker shape), not here")
    # And the workflow must READ that function, not re-inline the subtraction.
    text = EMBEDDED_WORKFLOW.read_text()
    assert "on_demand_literal_runner_files" in text, (
        "evals-on-demand.yml must resolve its lane through the pinned selector "
        "function rather than re-deriving the subtraction in a heredoc")
    assert "on_demand_files(load_manifest())" not in text, (
        "the wholesale subtraction is the bug this test pins")


def test_the_gating_workflow_still_logs_what_it_does_not_run():
    """Observability (#10194 requirement 3).

    "the evals aren't running" and "the evals are excluded on purpose" are the
    same log line unless the gating run prints the exclusion. This pins that the
    report step exists in the gating workflow and reads the selector (not a
    hand-copied table).
    """
    text = GATING_WORKFLOW.read_text()
    assert "--non-gating-report" in text, (
        "the gating run must log the non-gating lane and the shard → file-count "
        "map, so the 42 absent files are visible rather than inferred")
    report = cs.non_gating_report(_manifest())
    assert "eval/**" in report and "longmem_eval/**" in report
    assert "test (i)" in report, "the report must list every gating fast shard"
    # The COUNT is not pinned as a literal: the lane's whole point is that the
    # subtree is the unit (``on_demand_subtree_files`` walks disk), so a new
    # ``tests/longmem_eval/test_*.py`` grows the lane without a config edit and
    # must not red an unrelated PR. What IS pinned is that the printed total is
    # the selector's own count and reconciles with the itemization below.
    expected = len(cs.on_demand_files(_manifest()))
    assert f"non-gating total: {expected} file(s)" in report, (
        f"the report must state the selector's lane size ({expected}): "
        f"got\n{report}")
    # #10206 review: the itemization must RECONCILE with that total. A literal
    # that also sits inside a subtree (`eval/retrieval/test_integration.py`
    # inside `eval/**`) used to be counted twice, and the per-entry lines summed
    # to 44 against a 43-file total — in the one feature whose purpose is an
    # auditable count.
    assert "does not reconcile" not in report, (
        f"the report must attribute every lane file to exactly one entry: "
        f"got\n{report}")
    assert "covered by eval/** below" in report, (
        "the literal inside the subtree must be shown as covered by it, not "
        f"counted a second time: got\n{report}")
    per_entry = sum(int(chunk.split(" file")[0])
                    for chunk in report.split("-> ")[1:] if " file(s)" in chunk)
    literal_only = report.count("-> 1 file")
    assert per_entry + literal_only == expected, (
        f"entries attribute {per_entry + literal_only} file(s) against a "
        f"{expected}-file lane: got\n{report}")


def _mutation_dropping_the_subtree_lane() -> dict:
    """M1: the lane WITHOUT `eval/**` — one eval path back on the gate."""
    manifest = _manifest()
    return {**manifest,
            "on_demand": [e for e in (manifest.get("on_demand") or [])
                          if e != "eval/**"]}


def test_mutation_m1_one_eval_path_back_on_the_gate_reddens_the_guard():
    """The mutation, run as a test so it is never merely asserted in a PR body.

    Dropping `eval/**` from the lane must make an eval file appear in a GATING
    leg. That is what makes `test_no_subtree_file_is_in_any_gating_leg` a live
    guard rather than a tautology — and the failing set is named here, so the
    next reader can see WHICH assertion fires.
    """
    mutant = _mutation_dropping_the_subtree_lane()
    legs = _gating_sets(mutant)
    subtree = {_bare(f) for f in _subtree_files()}
    leaks = {leg: sorted(subtree & files) for leg, files in legs.items()
             if subtree & files}
    assert leaks, (
        "with `eval/**` removed from the lane an eval file must reappear on a "
        "gating leg — if this passes, the guard test next door cannot fail for "
        "the regression it names")
    assert "eval/ask/test_ask_corpus" in {f for files in leaks.values() for f in files}, (
        f"the mutant must name the file, got {leaks}")
