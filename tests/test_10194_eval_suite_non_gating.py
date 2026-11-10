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


def _strip_shell_comment(line: str) -> str:
    """Drop a shell comment, whole-line or trailing.

    Quote-aware, and deliberately not a parser: `#` starts a comment only at the
    start of a line or preceded by whitespace, and never inside `'…'` / `"…"`.
    That is enough for workflow `run:` bodies, and it is what makes the arms below
    about EXECUTION rather than text: a commented-out command, or a trailing
    `# was: …` note quoting the argv, must not satisfy a pin (measured — a fully
    commented-out line left two arms in this file green).
    """
    quote = ""
    for index, char in enumerate(line):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "'\"":
            quote = char
            continue
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            return line[:index].rstrip()
    return line


def _logical_commands(body: str) -> list[str]:
    """A step body's shell commands: comments dropped, `\\`-continuations joined.

    Needed because "the body mentions this" is not "the body RUNS this": the
    resolve step's own comments mention `pytest` while the run step is the one
    that must consume the list (measured — with the run step hardcoded, a
    body-level arm stayed green because those comments were in a different
    step). Joining continuations keeps a wrapped command a single command, so a
    rewrap cannot turn a correct workflow red.
    """
    logical: list[str] = []
    buf = ""
    for raw in body.splitlines():
        line = _strip_shell_comment(raw.strip()).strip()
        if not line:
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        logical.append(buf + line)
        buf = ""
    if buf:
        logical.append(buf)
    return logical


def _workflow_run_bodies(path: Path) -> list[str]:
    """Every `run:` body in a workflow, PARSED — never substring-scanned.

    A substring arm over a whole workflow file is how a guard silently stops
    guarding: the token it searches for also appears in comments (measured — the
    report flag occurs 2,000 lines below its step, and the selector name occurs
    in four comments of `evals-on-demand.yml`), so the arm stays green with the
    step deleted. Trying to fix that by scanning `run:` LINES is the mirror
    failure: the canonical `run: |` block and a wrapped command both red a
    command that is present and correct (measured, four ways).

    Parsing keys the arm on the command itself, so a step rename, a rewrap or a
    comment that happens to quote the command changes nothing.
    """
    doc = yaml.safe_load(path.read_text())
    return [
        step["run"] for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or []) if step.get("run")
    ]


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



#: The `push_legs()` keys `_gating_sets()` walks. Every OTHER key the producer
#: returns must be named in `_NON_GATING_LEG_KEYS`, or the pin below is silent
#: about a gating set nobody measured — a producer-side leg (say `"telemetry":
#: []`) is invisible to a check that only compares the sets `_gating_sets()`
#: itself built (measured: all 8 tests in this file stayed green when one was
#: added). Two literals, because deriving either side from the other makes the
#: check tautological.
_WALKED_LEG_KEYS = frozenset({"shards", "slow_shards", "carve_shards",
                             "slow", "env_broken", "carve_out"})

#: Producer keys that are deliberately NOT walked, and why: `on_demand` is the
#: manual `eval` lane's own selection — not a gating leg, so the set it produces
#: is asserted elsewhere (reachability is pinned by
#: `test_the_eval_workflow_is_manual_and_reachable_by_every_subtree_file`).
_NON_GATING_LEG_KEYS = frozenset({"on_demand"})


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


#: The changed-file probes for the tier-2 (PR) selection — one surface-owner of
#: each subtree, plus a direct eval-test edit.
_TIER2_CHANGES = ("tests/eval/ask/test_ask_corpus.py",
                  "tests/longmem_eval/test_vector_arm.py",
                  "tools/longmem_eval/run.py",
                  "tortoise/embeddings.py")


#: The leg KINDS, which stay LITERAL — the DECLARED side of the equality, so a
#: kind `_gating_sets()` starts producing without a matching entry here reds.
#: ⚠️ The reverse cases are guarded elsewhere, not here: a `push_legs()` key the
#: selector gains is refused by `_WALKED_LEG_KEYS`/`_NON_GATING_LEG_KEYS` in the
#: leak test, and a leg whose CONTENT is emptied, filtered or SWAPPED with
#: another while its name survives is refused there too — every leg family (all
#: FIVE declared flat legs, all three shard families, both tier-2 families) is
#: read back against the producer's own set, per family, so any map-side mutation
#: of a leg's membership reds. Two classes this file does NOT enforce (and does
#: not claim anyone else does): a filter applied to the PRODUCER itself moves both
#: sides together, and a new key `select()` RETURNS is enumerated by nothing.
_LEG_KINDS = frozenset({"fast_pool", "slow", "env_broken", "carve_out",
                        "push slow_selected"})


def _expected_legs(manifest: dict) -> set[str]:
    """The leg names `_gating_sets()` must produce, at THIS manifest's counts.

    The shard LABELS are DERIVED from the three reviewed literals the producers
    themselves read (`fast_shards` / `slow_shards` / `carve_shards`, through the
    selector's own accessors) rather than being spelled out here a second time.

    Why derived, and not `"abcdefghij"` (#11698): a hardcoded label list is a
    second copy of a config value whose own comment calls it a WALL-CLOCK LEVER
    — so bumping it is a legitimate, reviewed change. When `fast_shards` became
    11 (#11576 / `efc472108f6a`), that copy reddened `main` with "the leg list
    this test walks changed — a new gating set was added ... NOT covered here",
    which was false in both halves: no gating set was added, and the 11th shard
    WAS covered (the leak assertions walk every shard the manifest declares). A
    tripwire that fires on the config's own reviewed value accuses the selector
    of a change that did not happen, and a false red of this shape is repaired by
    editing the literal — which is how the drift is absorbed instead of reported.

    What the derivation does NOT give up: if the selector produces a shard set
    that differs from this derivation's — a real defect — the equality below
    still fails, and it now names the declared count rather than a stale one.
    What it does NOT catch is a rename that flows through `shard_labels()` itself:
    both sides read that accessor, so a renamed label arrives on both sides
    unchanged (measured: renaming label index 2, i.e. a shard named differently
    from the manifest's `a`..`k` sequence, leaves this file 8 passed). Label
    spelling is therefore pinned by the seams that would refuse a merge:
    `test_ci_selection.py::test_shard_labels_are_a_superset_when_s_changes` for
    the `test (a)`/`test (b)` names the rail subtracts (and NOT a rename past the
    first two labels — measured green), and
    `test_ci_selection.py::test_shard_count_is_stable_regardless_of_the_selection_size`
    for the `a`..`i` positional sequence it builds (a NINE-shard matrix, so a
    rename past the ninth label is caught by a THIRD seam, not this one —
    measured: index 10 renamed leaves both of the above green and reds
    `test_ci_selection.py::test_push_legs_partitions_every_classified_file`).
    What this pin refuses is the DECLARED side: a leg `_gating_sets()` produces
    that is not declared here (a gating set added or moved below without
    `_LEG_KINDS`/this derivation following) reds, and the message names both
    sides. The leg KINDS stay literal (`_LEG_KINDS`) so the declared side cannot
    drift along with the map — which is also why DELETING a leg from the map's
    body reds (as a `KeyError` from the per-family read-back, or in that
    read-back's own comparison; the per-family arms run first, so the
    `declared-but-missing` message is not always the one you see).

    ⚠️ Two gaps, named precisely, because this docstring has now been wrong twice
    in the same direction (each version claiming a guard for a class the guard
    could not see):

    1. A `push_legs()` KEY the selector gains that `_gating_sets()` never reads
       appears on neither side. Its measured counterexample was a new
       `"telemetry": []` key on `push_legs()`' return, which left all 8 tests in
       this file green. That class is now refused by the producer-key assertion in
       `test_no_subtree_file_is_in_any_gating_leg`, which diffs
       `set(push_legs(manifest))` against `_WALKED_LEG_KEYS |
       _NON_GATING_LEG_KEYS` (the same mutant now reds, naming `telemetry`), and
       both constants are pinned so neither can grow as a quiet one-liner.
    2. A leg that keeps its NAME but loses its CONTENT — `_gating_sets()` emptying
       a shard, or dropping a file from any leg — red an equality whose two sides
       are BOTH built from that map, i.e. nothing in this file (measured at
       #11675: 8 passed for every family, before the arms below existed).
       That one is now refused by TWO arms in the same test: every gating leg
       must be non-empty (only `tier-2 slow_selected [...]` is exempt, and for a
       stated reason), and every leg family is read back against the producer's
       own set — all FIVE flat legs (`fast_pool`, `slow`, `env_broken`,
       `carve_out`, `push slow_selected`), all three shard families, and both
       tier-2 families — so a filter inside the map reds even though the leg
       names survive. The arms are pinned to the DECLARED flat-leg set
       (`set(producer_by_family) == set(_LEG_KINDS)`), so a sixth flat leg cannot
       be declared without this test demanding its read-back. The two sides are
       different code paths (`push_legs()`/`select()` vs this map); an earlier
       version of this test compared two PRODUCER calls, which no map edit can
       reach, and its reviewer measured that.

       ⛔ What this file does NOT enforce, stated without naming anyone else as
       the owner — the earlier versions of this paragraph did, and every review of
       it found one of those attributions false (a function that is informational,
       a pin that reds only on synthetic fixtures, a universal that held for one
       dropped file and not the next). Who else guards a class is a separate
       question, answered by measuring, not by this docstring:

       (a) a filter applied to the PRODUCER itself (in `tools/ci_selection.py`)
           moves both sides together, so the read-backs above cannot see it;
       (b) nothing here enumerates the keys `select()` RETURNS, so a new gating
           key there is unguarded — including by the leak check below, which
           iterates only the keys this map built. The absence of such a key today
           is not a guard: a key that does not exist yet cannot be caught.

    ⚠️ One class this docstring used to leave unstated, now guarded rather than
    claimed away (`#11675` round-13 review): the tier-2 PROBE LIST. `_TIER2_CHANGES`
    was a second copy on `main` — `_gating_sets()` spelled the four paths inline
    while this derivation read the constant, so editing one side red the equality.
    Both sides (and the per-family read-back) now read the ONE constant, which means
    a shrunken probe list moves all three together and reads as a pass over a
    surface nobody selected for (measured: `_TIER2_CHANGES` cut to one entry left
    the equality 26 == 26 green). The list is therefore pinned against a literal in
    `test_no_subtree_file_is_in_any_gating_leg`, so dropping a probe is a second,
    deliberate edit with a reviewer-visible message.

    The count is the manifest's, never a second copy in this file: the two
    `core` rows #11642 added move no shard count at all (measured over
    `push_legs` — strip both and the names are still `a`..`k`; only the file
    count moves, and that number decays with every unrelated registration, so
    the claim here is the NAMES, never the total). Nor is it a pool-size
    threshold — `fast_shard_count` reads the manifest's `fast_shards` literal, and
    `split_fast_gate` says as much ("N comes from the manifest's `fast_shards` …
    NOT capped by the file count"), so a lane that unregisters files must not
    drop a label.
    """
    return {
        *_LEG_KINDS,
        *(f"test ({c})" for c in cs.shard_labels(cs.fast_shard_count(manifest))),
        *(f"test-slow ({c})"
          for c in cs.shard_labels(cs.slow_shard_count(manifest))),
        *(f"test-carve-out ({c})"
          for c in cs.shard_labels(cs.carve_shard_count(manifest))),
        *(f"tier-2 test_files [{c}]" for c in _TIER2_CHANGES),
        *(f"tier-2 slow_selected [{c}]" for c in _TIER2_CHANGES),
    }


def _gating_sets(manifest: dict) -> dict[str, list[str]]:
    """Every GATING file set the selector produces, bare-named, per leg.

    Built from the producers themselves (never from a re-implementation): the
    push matrix's fast shards, its slow/carve-out/env-broken legs, the tier-2
    selection for a change in each relevant surface, and the full selection. A
    set added to the selector later is covered only if it is added here too —
    which is why the equality below (`assert set(legs) == expected`) compares this
    map's leg KIND list to `_expected_legs()`'s, so a KIND missing there reds.
    ⚠️ Two ways this map can go wrong, stated so a
    reader does not infer a guard that is not there: a leg whose CONTENT is
    emptied, filtered or swapped reds in the per-family read-backs — but a key
    `select()` newly RETURNS is enumerated by nothing (the leak check below walks
    only the keys THIS map built), and a filter applied to the PRODUCER moves both
    sides together. Neither is this file's to enforce. See `_expected_legs()`.
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
    for changed in _TIER2_CHANGES:
        sel = cs.select([changed], "pull_request", manifest)
        out[f"tier-2 test_files [{changed}]"] = {_bare(f) for f in sel["test_files"]
                                                 if f != "ALL"}
        out[f"tier-2 slow_selected [{changed}]"] = {_bare(f)
                                                    for f in sel["slow_selected"]}
    full = cs.select([], "push", manifest)
    out["push slow_selected"] = {_bare(f) for f in full["slow_selected"]}
    return out


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
    expected = _expected_legs(manifest)
    unaccounted = set(cs.push_legs(manifest)) - _WALKED_LEG_KEYS - _NON_GATING_LEG_KEYS
    assert set(_NON_GATING_LEG_KEYS) == {"on_demand"}, (
        "`_NON_GATING_LEG_KEYS` grew: it is the one way to silence the "
        "producer-key assertion, so growing it must be a SECOND edit that a "
        f"reviewer cannot miss (now {sorted(_NON_GATING_LEG_KEYS)}). A gating "
        "set declared non-gating here stops being walked with every guard "
        "green — the exact failure mode (a) this file exists to prevent. "
        "Update this assertion deliberately, in the same change, if the new "
        "key really gates nothing.")
    assert not unaccounted, (
        "`push_legs()` produces leg key(s) that `_gating_sets()` does not walk "
        f"and does not declare non-gating: {sorted(unaccounted)}. A gating set "
        "added on the PRODUCER side is invisible to the equality below, which "
        "compares only the sets `_gating_sets()` built itself — so it would "
        "read as a pass over a set nobody walked (failure mode (a)). A new "
        "gating leg needs THREE deliberate edits: walk it in `_gating_sets()`, "
        "add its key to `_WALKED_LEG_KEYS` (pinned immediately below, so it "
        "cannot be a quiet one-liner), and its legs then join the equality — "
        "or declare it in `_NON_GATING_LEG_KEYS` with the reason it gates "
        "nothing, which is pinned too.")
    assert set(_WALKED_LEG_KEYS) == {
        "shards", "slow_shards", "carve_shards", "slow", "env_broken",
        "carve_out"}, (
        "`_WALKED_LEG_KEYS` changed. It is the silent half of the assertion "
        "above: adding a key here without ALSO walking it in `_gating_sets()` "
        f"clears the check (now {sorted(_WALKED_LEG_KEYS)}). Change this list "
        "deliberately, in the same commit that walks the new leg.")
    # A leg that keeps its NAME but loses its CONTENT is invisible to the
    # equality above (`legs` and `expected` are both built from this map).
    # Measured at #11675: emptying a shard in `_gating_sets()`, or dropping a file
    # from every fast shard, left all 8 tests green.
    empty = sorted(k for k, v in legs.items()
                   if not v and not k.startswith("tier-2 slow_selected "))
    assert not empty, (
        f"gating leg(s) with NO files: {empty}. An empty leg is a lane that "
        "runs nothing while reading as coverage — and the declared-side "
        "equality cannot see it, because both sides are built from this map. "
        "(Only `tier-2 slow_selected [...]` is exempt: it is keyed by a "
        "SYNTHETIC changed file, so one that selects no slow set is the "
        "probe's ANSWER about that surface, not an empty lane. A "
        "`tier-2 test_files [...]` leg is a real gating file list and must "
        "not be empty.)")
    # Content that is FILTERED while the leg NAME survives is invisible to the
    # equality above (`legs` and `expected` are both built from this map), so
    # every leg family is read back against the producer's own set. Measured at
    # #11675: filtering one file out of ANY family in `_gating_sets()` left all
    # 8 tests green before these arms existed.
    #
    # The two sides are DIFFERENT code paths — the producer (`push_legs()` /
    # `select()`) vs this map — which is the whole point: an arm with both sides
    # from the producer (an earlier version of this test) cannot see a map edit.
    producer = cs.push_legs(manifest)

    def _shard_sets(entries):
        return {e["name"]: set(e["files"]) for e in entries}

    producer_by_family = {
        "fast_pool": {_bare(f) for f in cs.fast_pool(manifest)},
        "slow": set(producer["slow"]),
        "env_broken": set(producer["env_broken"]),
        "carve_out": set(producer["carve_out"]),
        "push slow_selected": {_bare(f)
                               for f in cs.select([], "push",
                                                  manifest)["slow_selected"]},
    }
    # Pinned as a SET, so a fifth flat leg cannot be added to the map without
    # someone deciding whether it gets a read-back here — an arm that is never
    # written is an arm that cannot fire (measured: dropping the
    # `push slow_selected` arm left this file 8 passed, and a filter on that leg
    # then went green too).
    # The arms must cover exactly the flat legs DECLARED in `_LEG_KINDS` — not a
    # third copy of that list. Comparing against `_LEG_KINDS` is what makes the
    # decision unavoidable: a sixth flat leg cannot be declared there (the
    # declared-side equality requires it) without this assertion then demanding
    # its read-back. An arm that is never written is an arm that cannot fire —
    # this file has already shipped that mistake once (`push slow_selected` had
    # no read-back, and only the fact that its arm happened to exist kept it
    # honest).
    assert set(producer_by_family) == set(_LEG_KINDS), (
        "the per-family read-back does not cover exactly the declared flat "
        f"legs: missing {sorted(set(_LEG_KINDS) - set(producer_by_family))}, "
        f"extra {sorted(set(producer_by_family) - set(_LEG_KINDS))}. A leg "
        "with no read-back has nothing checking its membership.")
    for family, producer_set in producer_by_family.items():
        assert legs[family] == producer_set, (
            f"the `{family}` leg does not carry the producer's own set — a "
            "filter applied inside `_gating_sets()` while the leg NAME survives "
            "reds here. It is invisible to the declared-side equality (both "
            f"sides are built from that map).\nonly-in-map: "
            f"{sorted(legs[family] - producer_set)[:8]}\nonly-in-producer: "
            f"{sorted(producer_set - legs[family])[:8]}")
    for family, prefix, entries in (
            ("shards", "test (", producer["shards"]),
            ("slow_shards", "test-slow (", producer["slow_shards"]),
            ("carve_shards", "test-carve-out (", producer["carve_shards"])):
        want = _shard_sets(entries)
        got = {k[len(prefix):-1]: v for k, v in legs.items()
               if k.startswith(prefix)}
        assert got == want, (
            f"the `{family}` legs do not carry the producer's own shard sets — "
            "a filter (or a dropped/renamed shard) applied inside "
            "`_gating_sets()` while the leg NAME survives reds here, and the "
            "declared-side equality cannot see it because both sides are built "
            f"from that map.\nshards-only-in-map: "
            f"{sorted(set(got) - set(want))[:8]}\nshards-only-in-producer: "
            f"{sorted(set(want) - set(got))[:8]}\ncontent-differs: "
            f"{sorted(k for k in set(got) & set(want) if got[k] != want[k])[:8]}")
    assert set(_TIER2_CHANGES) == {
            "tests/eval/ask/test_ask_corpus.py",
            "tests/longmem_eval/test_vector_arm.py",
            "tools/longmem_eval/run.py",
            "tortoise/embeddings.py",
    }, (
        "`_TIER2_CHANGES` changed. It is the probe list the tier-2 family is driven "
        "with — one surface-owner of each eval subtree plus a direct eval-test edit "
        "— and it is pinned here against a literal for the same reason "
        "`_LEG_KINDS` is: this file's map, its declared side AND its per-family "
        "read-back all read the one constant, so a shrunken list moves all three "
        "together and the equality stays green over surfaces nobody selected for "
        "(measured before this pin: cut to one entry, still 26 == 26). Dropping or "
        "replacing a probe is legitimate, but it must be a SECOND, deliberate "
        "edit here, with a reviewer reading the message — not a quiet narrowing "
        "of what the tier-2 legs cover."
    )
    for changed in _TIER2_CHANGES:
        sel = cs.select([changed], "pull_request", manifest)
        for key, field, drop_all in (
                (f"tier-2 test_files [{changed}]", "test_files", True),
                (f"tier-2 slow_selected [{changed}]", "slow_selected", False)):
            want = {_bare(f) for f in sel[field] if not (drop_all and f == "ALL")}
            assert legs[key] == want, (
                f"the `{key}` leg does not carry the producer's own `{field}` "
                "set — a filter applied inside `_gating_sets()` while the leg "
                "NAME survives reds here. The declared-side equality cannot "
                f"see it (both sides come from that map).\nonly-in-map: "
                f"{sorted(legs[key] - want)[:8]}\nonly-in-producer: "
                f"{sorted(want - legs[key])[:8]}")
    # The producer's own packing invariant, which no map edit can reach: the fast
    # shards carry `fast_pool()` PLUS any registered `push_extra` (that key is a
    # supported lever — `push_legs()` spreads it round-robin across the shards).
    shard_union = {_bare(f) for f in cs.all_shard_files(producer)}
    pool = {_bare(f) for f in cs.fast_pool(manifest)}
    extra = {_bare(f) for f in manifest.get("push_extra", [])}
    assert shard_union == pool | extra, (
        "the fast shards do not carry `fast_pool()` plus the registered "
        "`push_extra` files — this arm is about the PRODUCER's packing, and it "
        "reads two producer calls, so a map-side filter cannot reach it (the "
        "per-family arms above are what guard the map)."
        f"\nonly-in-shards: {sorted(shard_union - pool - extra)[:8]}\n"
        f"only-in-pool: {sorted((pool | extra) - shard_union)[:8]}")
    assert set(legs) == expected, (
        "the leg list this test walks changed — a new gating set was added to "
        "the selector (or one renamed) and is NOT covered here, which reads as "
        f"a pass over a set nobody checked.\ndeclared-but-missing: "
        f"{sorted(expected - set(legs))}\nproduced-but-declared-absent: "
        f"{sorted(set(legs) - expected)}")
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
    # ... and the workflow's resolve step must invoke THAT verb, not merely
    # mention it. Parsed steps, keyed on the command — a step rename or a rewrap
    # must not red a workflow that is correct, and a comment quoting the argv
    # must not satisfy the arm. `_logical_commands` is what makes the second half
    # true: the step body CONTAINS its comments, so a body-level substring test
    # stays green with the call commented out (measured, for both arms here).
    bodies = _workflow_run_bodies(EVAL_WORKFLOW)
    resolve_cmds = [
        cmd for b in bodies for cmd in _logical_commands(b)
        if f"ci_selection.py {RESOLVER_FLAG}" in cmd]
    assert resolve_cmds, (
        "the workflow must RUN `tools/ci_selection.py "
        f"{RESOLVER_FLAG}` — the argv verified above")
    assert any("> /tmp/lane.txt" in cmd for cmd in resolve_cmds), (
        "the resolve step must capture the resolver's stdout to the file the run "
        "step reads")
    # …and the RUN step must consume it: a resolve step whose output nobody reads
    # resolves nothing. Both tokens in the same step BODY, so a wrapped command
    # still passes and a file-level comment cannot satisfy it.
    consumers = [
        cmd for b in bodies for cmd in _logical_commands(b)
        if "pytest" in cmd and "< /tmp/lane.txt" in cmd]
    assert consumers, (
        "the run step must pass the RESOLVED list to pytest (`< /tmp/lane.txt`) — "
        "otherwise the resolver's list is bypassed and the lane runs whatever "
        "the workflow hardcodes, which is the exclusion silently ceasing to "
        "exclude")


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
        "only the LITERAL survives from the eval subtree in this runner — every "
        "other `eval/` file belongs to eval-suite-manual.yml (docker shape), not "
        "here. This assertion states the CLASS and deliberately not a total: it "
        "drifts with every registration (it read 'the other 25' when #10198 "
        "landed and 27 when measured at #11675) and the drift is unobservable.")
    # And the workflow must READ that function, not re-inline the subtraction.
    # The bare NAME occurs in four comments in that file, so a name-substring is
    # true with the call deleted — pin the CALL in a parsed step body, on its
    # EXECUTABLE commands: the parsed body still carries its comments, so a
    # body-level substring is satisfied by a commented-out call (measured).
    # Tolerating a hoisted manifest (the argument is a local, not a shape we care
    # about).
    bodies = _workflow_run_bodies(EMBEDDED_WORKFLOW)
    commands = [cmd for body in bodies for cmd in _logical_commands(body)]
    calls = [cmd for cmd in commands if "on_demand_literal_runner_files(" in cmd]
    assert calls, (
        "evals-on-demand.yml must resolve its lane through the pinned selector "
        "function rather than re-deriving the subtraction in a heredoc")
    reinline = [cmd for cmd in commands if "on_demand_files(" in cmd]
    assert not reinline, (
        f"the wholesale subtraction is the bug this test pins: {reinline[:3]}")


def test_the_gating_workflow_still_logs_what_it_does_not_run():
    """Observability (#10194 requirement 3).

    "the evals aren't running" and "the evals are excluded on purpose" are the
    same log line unless the gating run prints the exclusion. This pins that the
    report step exists in the gating workflow and RUNS the selector (not a
    hand-copied table).
    """
    # Parsed step bodies, not a line scan: the flag also appears in a comment
    # ~2,000 lines below (so a whole-file substring passes with the step
    # deleted), and a `run: |` block would red a command that is present.
    report_cmds = [
        cmd for b in _workflow_run_bodies(GATING_WORKFLOW)
        for cmd in _logical_commands(b) if "--non-gating-report" in cmd]
    assert report_cmds, (
        "the gating run must log the non-gating lane and the shard → file-count "
        "map, so the non-gating files are visible rather than inferred")
    assert all("tools/ci_selection.py" in cmd for cmd in report_cmds), (
        "the report must come from the selector, never a hand-copied table: "
        f"command(s) = {report_cmds}")
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
    assert "eval/ask/test_ask_corpus" in {
        f for files in leaks.values() for f in files
    }, f"the mutant must name the file, got {leaks}"
