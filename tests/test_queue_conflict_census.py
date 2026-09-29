"""Hermetic tests for `tools/queue_conflict_census.py` (#6138).

No network, no DB, no gh: every live read is injected through a fixture or a
monkeypatched seam. The census is a MEASUREMENT, so the tests pin the parts
that can silently produce a wrong NUMBER:

  * `mergeable` is tri-state and `null` is its own bucket — the list endpoint
    returned `null` for 136/136 open PRs on 2026-09-28, so folding it into
    either resolved bucket is the false-confidence failure this file guards;
  * the share arithmetic, including the 0/0 case (a clean queue must report
    UNKNOWN, not 0.0);
  * the generated-vs-hand-written classifier, including the two false
    positives an earlier rule produced (`tortoise/sdk.py` and
    `beta-sdk-surface.md`, both mentioned-and-read by real generators);
  * the `git merge-tree` path extraction and the aggregation over it.

EVERY TEST STATES, IN ITS DOCSTRING, (a) THE EXACT VALUE/STATE THAT MAKES IT
FAIL and (b) WHY THAT STATE IS REACHABLE in the fixture below. A test that
cannot fail is not evidence.

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file; `test_merge_throughput.py` is the precedent).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import queue_conflict_census as q  # noqa: E402

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)  # noqa: UP017 - python3 is 3.9 here


def _record(number, *, mergeable=None, mergetree="clean", **extra):
    """One assembled-report record with the census's own field names."""
    record = {
        "number": number,
        "mergeable_bucket": q.classify_mergeable(mergeable),
        "mergetree": mergetree,
        "idle_days": 1.0,
        "draft": False,
        "conflict_age": {"upper_bound_days": None},
    }
    record.update(extra)
    return record


# ==========================================================================
# `mergeable` is TRI-STATE — null is its own bucket
# ==========================================================================

def test_null_is_not_counted_as_mergeable():
    """(a) FAILS if `classify_mergeable(None)` returns mergeable or conflicting.
    (b) Reachable: the LIST endpoint returns `mergeable: null` (measured
    136/136 on 2026-09-28), which is exactly the population this census reads.
    """
    assert q.classify_mergeable(None) == "unknown"
    assert q.classify_mergeable(None) not in ("mergeable", "conflicting")


@pytest.mark.parametrize("value", [0, 1, "", "true", "True", "false", {}, [], "unknown"])
def test_only_boolean_identity_resolves_a_bucket(value):
    """(a) FAILS for any value whose truthiness is used as the predicate — `1`
    and `"true"` would both read MERGEABLE.
    (b) Reachable: the API is JSON, so `1`/`"true"` are one malformed field
    away, and a truthiness test is the natural implementation to regress to.
    """
    assert q.classify_mergeable(value) == "unknown"


def test_boolean_identity_still_resolves_both_real_states():
    """(a) FAILS if the strictness above went too far (e.g. `is True` against a
    JSON int), leaving every PR unknown.
    (b) Reachable: True/False are the API's only two resolved values.
    """
    assert q.classify_mergeable(True) == "mergeable"
    assert q.classify_mergeable(False) == "conflicting"


# ==========================================================================
# Share arithmetic — the headline
# ==========================================================================

def test_share_buckets_reconcile_to_the_population():
    """(a) FAILS if any record is dropped or double-counted — e.g. an
    unclassifiable record that is skipped instead of bucketed.
    (b) Reachable: three records, one with a `None` mergeable.
    """
    share = q.summarize_share([
        _record(1, mergeable=True),
        _record(2, mergeable=False),
        _record(3, mergeable=None),
    ])
    assert (share["mergeable"], share["conflicting"], share["unknown"]) == (1, 1, 1)
    assert share["population"] == 3
    assert share["known"] == 2
    assert share["share_of_known"] == 0.5


def test_zero_open_prs_is_unknown_not_a_division_by_zero():
    """(a) FAILS with ZeroDivisionError if the `if known else None` guard is
    removed, and fails an `== 0.0` assertion if the guard returns 0.0 — 0/0 is
    UNKNOWN, and 0.0 there is a fabricated clean queue.
    (b) Reachable: an empty queue (or an exhausted filter) is a real read.
    """
    share = q.summarize_share([])
    assert share["population"] == 0
    assert share["share_of_known"] is None
    assert share["share_of_population"] is None
    assert q.share_percent(share) == q.UNKNOWN


def test_nonempty_population_with_no_known_state_has_no_share():
    """(a) FAILS if `share_of_known` is computed over the population rather than
    the known set: 0 conflicting / 5 unknown must be UNKNOWN, not 0%.
    (b) Reachable: the LIST endpoint state — every PR `null`.
    """
    share = q.summarize_share([_record(n, mergeable=None) for n in range(5)])
    assert share["known"] == 0
    assert share["share_of_known"] is None
    assert share["share_of_population"] == 0.0


def test_an_unclassifiable_record_lands_in_unknown_not_nowhere():
    """(a) FAILS if an unrecognised `mergeable_bucket` is skipped (population
    would read 1) or raises (the census crashes on one bad record).
    (b) Reachable: a record assembled before a bucket rename, or a hand-built
    record — the population must still reconcile.
    """
    share = q.summarize_share([{"mergeable_bucket": "deferred"}, {"mergeable_bucket": "unknown"}])
    assert share["population"] == 2
    assert share["unknown"] == 2


def test_the_list_endpoint_distribution_is_recorded_but_is_not_the_headline():
    """THE TRAP TEST. (a) FAILS if the headline share is taken from the list
    endpoint (`mergeable` on every pull is `None` -> unknown=1, mergeable=0)
    instead of the per-PR GET in `details` (-> mergeable=1, unknown=1).
    (b) Reachable and MEASURED: on 2026-09-28 the list endpoint returned
    `null` for 136/136 open PRs, so the two surfaces genuinely disagree.
    """
    pulls = [{"number": 1, "mergeable": None}, {"number": 2, "mergeable": None}]
    details = {1: {"mergeable": True, "updated_at": "2026-09-28T10:00:00Z"},
               2: {"mergeable": None, "updated_at": "2026-09-28T10:00:00Z"}}
    report = q.assemble_report(pulls, details, {}, NOW, _opts())
    assert report["share"]["mergeable"] == 1
    assert report["share"]["unknown"] == 1
    assert report["list_endpoint_mergeable"] == {"mergeable": 0, "conflicting": 0, "unknown": 2}


# ==========================================================================
# The classifier
# ==========================================================================

def test_a_generated_banner_is_detected_from_the_origin_main_header():
    """(a) FAILS if the marker scan is dropped, is case-sensitive (`Generated`
    spells it with a capital, `GENERATED — do not edit` with the run together),
    or reads the working tree instead of the snapshot header.
    (b) Reachable: `docs/product/sdk-rename-table.md` line 3 is exactly
    `**GENERATED — do not edit.**`.
    """
    kind, evidence = q.classify_path(
        "docs/product/sdk-rename-table.md",
        "# Phase 0.3b\n\n**GENERATED — DO NOT EDIT.** run tools/sdk_rename_table.py",
        None,
    )
    assert kind == "generated"
    assert "GENERATED" in evidence


def test_a_declared_write_output_is_generated():
    """(a) FAILS if only the banner signal is honoured.
    (b) Reachable: `config/ci-surfaces.yml` has NO banner but IS written by
    `tools/ci_selection.py --register` (13 of the 53 conflicted PRs on
    2026-09-28 conflict on it).
    """
    kind, evidence = q.classify_path("config/ci-surfaces.yml", "version: 1\nsurfaces:", "tools/ci_selection.py")
    assert kind == "generated"
    assert "ci_selection.py" in evidence


@pytest.mark.parametrize(
    "path,header",
    [
        # MEASURED false positive of the earlier bare-substring rule: this is
        # hand-written benchmark source, not a generated artifact.
        ("benchmarks/bench_core.py",
         '"""bench_core.\n\nPre-registered numbers (issue #316 scoping, v5.1 — do NOT edit without a\nscoping revision):\n'),
        # MEASURED: the marker `autogenerated` matched inside a hyphenated word.
        ("tests/test_reaper_ownership.py",
         "`embedded_reaper.py` admitted, as a kill **candidate**, any autogenerated-named\n"),
        # The word `generated` without a prohibition or attribution is prose.
        ("tortoise/x.py", "# The generated targets are listed below.\n"),
    ],
)
def test_prose_that_is_not_a_generated_banner_is_hand_written(path, header):
    """(a) FAILS if the banner signal is a bare case-insensitive substring over
    the first lines — the four paths above are hand-written or explanatory
    prose, and labelling them GENERATED aims the structural remedy ("stop
    tracking it") at a file no script writes.
    (b) Reachable and MEASURED on this repo (`bench_core.py`,
    `test_reaper_ownership.py`); any conflicted path can enter the top-N.
    """
    kind, _evidence = q.classify_path(path, header, None)
    assert kind == "hand-written"


def test_an_unreadable_file_is_unknown_not_hand_written():
    """(a) FAILS if `header_text is None` falls through to hand-written — "we
    did not look" is not "we looked and found no banner".
    (b) Reachable: a conflicted path deleted at `origin/main` (a modify/delete
    conflict) has no readable header.
    """
    kind, evidence = q.classify_path("gone/file.py", None, None)
    assert kind == "unknown"
    assert "not readable" in evidence


def test_a_binary_path_is_unreadable_not_a_crash(monkeypatch):
    """(a) FAILS if `read_header` returns decoded binary garbage (which then
    classifies as hand-written) or if it raises — a PNG in the top-N would
    abort the whole census.
    (b) Reachable: a conflicted image/asset path; `git show` on a PNG emits a
    NUL in its first bytes.
    """
    monkeypatch.setattr(q, "_run", lambda *a, **k: (0, "\x89PNG\x00\x1a\nmore", ""))
    assert q.read_header("docs/prototypes/assets/logo-icon.png") is None


def test_run_never_raises_on_undecodable_stdout():
    """(a) FAILS with UnicodeDecodeError if `_run` decodes strictly — a
    ValueError that escapes the OSError/SubprocessError guard and aborts the
    census.
    (b) Reachable: `git show` of a binary path through `_run`.
    """
    rc, out, _err = q._run(
        # A RAW string: the argv must carry the ESCAPE (so `python -c` parses
        # to a bytes literal) and not the undecodable bytes themselves.
        [sys.executable, "-c", r"import sys; sys.stdout.buffer.write(b'\xff\xfe')"], 60
    )
    assert rc == 0
    assert isinstance(out, str)


def test_hand_written_needs_neither_signal():
    """(a) FAILS if a path with a readable header and no generator defaults to
    generated.
    (b) Reachable: `tortoise/hosted_api.py` — 9 conflicted PRs, no banner, no
    writing script declares it.
    """
    kind, _evidence = q.classify_path("tortoise/hosted_api.py", "import logging\n", None)
    assert kind == "hand-written"


def test_declared_outputs_resolves_a_write_target_through_name_bindings():
    """(a) FAILS if the write-target expression is not resolved (e.g. only
    top-level string literals are scanned).
    (b) Reachable: `tools/ci_selection.py` binds
    `MANIFEST = REPO / "config" / "ci-surfaces.yml"`.
    """
    source = 'MANIFEST = ROOT / "config" / "ci-surfaces.yml"\nMANIFEST.write_text("x")\n'
    outputs = q.declared_outputs(source)
    assert "ci-surfaces.yml" in outputs
    assert "config" in outputs


def test_a_path_merely_mentioned_by_a_writing_script_is_not_generated():
    """(a) FAILS if the rule is "any path literal in a script that writes" —
    the regression that classified `tortoise/sdk.py` as a generated artifact.
    (b) Reachable: `tools/ci_selection.py` writes `config/ci-surfaces.yml` AND
    names `tortoise/sdk.py` in a shared-module LIST.
    """
    source = (
        'MANIFEST = ROOT / "config" / "ci-surfaces.yml"\n'
        'SHARED = ("tortoise/sdk.py", "tortoise/hosted_api.py")\n'
        "MANIFEST.write_text(\"x\")\n"
    )
    outputs = q.declared_outputs(source)
    assert "tortoise/sdk.py" not in outputs
    assert "tortoise/hosted_api.py" not in outputs


def test_a_read_only_path_constant_is_not_a_declared_output():
    """(a) FAILS if "any module-level path constant" is used as the signal —
    the regression that would classify the owner-approved INPUT docs as
    generated artifacts.
    (b) Reachable: `tools/sdk_rename_table.py` READS
    `docs/product/beta-sdk-surface.md` and also writes its own doc.
    """
    source = (
        'BETA = ROOT / "docs" / "product" / "beta-sdk-surface.md"\n'
        'OUT = ROOT / "docs" / "product" / "sdk-rename-table.md"\n'
        'text = BETA.read_text()\n'
        'OUT.write_text("doc")\n'
    )
    outputs = q.declared_outputs(source)
    assert "sdk-rename-table.md" in outputs
    assert "beta-sdk-surface.md" not in outputs


def test_declared_outputs_follows_a_parameter_into_the_helper_that_writes_it():
    """(a) FAILS if only direct writes count: `register()` never writes its own
    parameter, so `config/ci-surfaces.yml` would read as hand-written.
    (b) Reachable: `tools/ci_selection.py` is exactly this shape —
    `register(MANIFEST, ...)` -> `register_tests(manifest_path, ...)` ->
    `manifest_path.write_text(...)`.
    """
    source = (
        'MANIFEST = ROOT / "config" / "ci-surfaces.yml"\n'
        "def register_tests(manifest_path, tests_dir, surface):\n"
        '    manifest_path.write_text("x")\n'
        "def register(manifest_path, tests_dir, surface):\n"
        "    return register_tests(manifest_path, tests_dir, surface)\n"
        "register(MANIFEST, TESTS, 'core')\n"
    )
    assert "ci-surfaces.yml" in q.declared_outputs(source)


def test_declared_outputs_follows_a_renamed_parameter_into_the_writer():
    """(a) FAILS if the fixed-point pairs the callee's positional args against
    the CALLER's parameter names instead of the callee's — the propagation then
    only works while helper and callee happen to share a parameter name, so a
    rename silently drops the output (fresh-context review finding).
    (b) Reachable: `w(dest)` written through `r(target)` — logically identical
    to `test_declared_outputs_follows_a_parameter_into_the_helper_that_writes_it`
    with different parameter names.
    """
    source = (
        'MANIFEST = ROOT / "config" / "ci-surfaces.yml"\n'
        "def writes(dest):\n"
        '    dest.write_text("x")\n'
        "def register(target):\n"
        "    return writes(target)\n"
        "register(MANIFEST)\n"
    )
    assert "ci-surfaces.yml" in q.declared_outputs(source)


def test_declared_outputs_resolves_an_argparse_default_written_through_args():
    """(a) FAILS if the write target `args.out` is left unresolved.
    (b) Reachable: `tools/sdk_rename_table.py` writes
    `args.out.write_text(doc)` with `add_argument("--out", default=OUT)`.
    """
    source = (
        'OUT = ROOT / "docs" / "product" / "sdk-rename-table.md"\n'
        'ap.add_argument("--out", type=Path, default=OUT)\n'
        "args = ap.parse_args()\n"
        'args.out.write_text("doc")\n'
    )
    assert "sdk-rename-table.md" in q.declared_outputs(source)


def test_generator_index_lookup_matches_a_basename_and_joins_the_declarers():
    """(a) FAILS if only exact full-path keys match, or if multiple declarers
    are collapsed to one (the evidence must name every script that writes it).
    (b) Reachable: the index is keyed by the literal the script wrote, which is
    often a basename.
    """
    index = {"ci-surfaces.yml": ["tools/ci_selection.py", "tools/ci_timing.py"]}
    found = q.lookup_generator(index, "config/ci-surfaces.yml")
    assert "ci_selection.py" in found
    assert "ci_timing.py" in found


# ==========================================================================
# merge-tree: the conflicted path set
# ==========================================================================

def test_merge_tree_paths_are_read_between_the_tree_oid_and_the_blank_line(monkeypatch):
    """(a) FAILS if the whole stdout is split as paths (the tree OID and the
    `CONFLICT` prose would be counted as conflicted files), or if the scan does
    not stop at the blank line.
    (b) Reachable: this IS the documented `--write-tree --name-only` format.
    """
    stdout = (
        "1234abcdtreeoid\n"
        "config/ci-surfaces.yml\n"
        "tortoise/sdk.py\n"
        "\n"
        "Auto-merging tortoise/sdk.py\n"
        "CONFLICT (content): Merge conflict in tortoise/sdk.py\n"
    )
    monkeypatch.setattr(q, "_run", lambda *a, **k: (1, stdout, ""))
    state, paths, reason = q.merge_tree_probe("deadbeef")
    assert state == "conflicted"
    assert paths == ["config/ci-surfaces.yml", "tortoise/sdk.py"]
    assert reason is None


def test_merge_tree_rc_zero_is_clean_with_no_paths(monkeypatch):
    """(a) FAILS if rc 0 is read as unresolved, which would inflate the
    unresolved count on the majority of the queue.
    (b) Reachable: a non-conflicting PR.
    """
    monkeypatch.setattr(q, "_run", lambda *a, **k: (0, "", ""))
    assert q.merge_tree_probe("deadbeef") == ("clean", [], None)


def test_merge_tree_failure_is_unresolved_never_clean(monkeypatch):
    """(a) FAILS if any non-{0,1} return is folded into clean — a probe that
    could not run must not be counted as "no conflict".
    (b) Reachable: MEASURED — a shallow clone's head has no local merge-base
    and `git merge-tree` exits 128 with "refusing to merge unrelated
    histories".
    """
    monkeypatch.setattr(
        q, "_run", lambda *a, **k: (128, "", "fatal: refusing to merge unrelated histories")
    )
    state, paths, reason = q.merge_tree_probe("deadbeef")
    assert state == "unresolved"
    assert paths == []
    assert reason == "no-merge-base"


def test_merge_tree_without_a_head_sha_is_unresolved(monkeypatch):
    """(a) FAILS if an absent sha short-circuits to clean.
    (b) Reachable: a pull payload with no `head.sha`.
    """
    def _boom(*_a, **_k):  # must not be reached
        raise AssertionError("git must not be invoked without a sha")

    monkeypatch.setattr(q, "_run", _boom)
    assert q.merge_tree_probe(None)[0] == "unresolved"


def test_path_aggregation_counts_distinct_prs_not_occurrences():
    """(a) FAILS if a repeated path inside one PR is counted twice (pr_count
    2 for a single PR), or if the ordering is unstable.
    (b) Reachable: a PR with two conflicted hunks in one file — the path list
    is not deduplicated by `git merge-tree`.
    """
    rows = q.aggregate_paths({10: ["a.py", "a.py", "b.py"], 11: ["a.py"], 12: ["c.py"]})
    assert rows[0] == {"path": "a.py", "pr_count": 2, "prs": [10, 11]}
    assert [(r["path"], r["pr_count"]) for r in rows] == [("a.py", 2), ("b.py", 1), ("c.py", 1)]


def test_the_top_n_classification_is_applied_at_the_report_level():
    """(a) FAILS if the top-N rows skip `classify_path` (every row would read
    hand-written), or if the header is read for a path not in the top-N.
    (b) Reachable: two conflicting PRs sharing one generated and one
    hand-written path.
    """
    pulls = [{"number": 1}, {"number": 2}]
    details = {1: {"mergeable": False}, 2: {"mergeable": False}}
    probes = {
        1: {"state": "conflicted", "paths": ["docs/product/sdk-rename-table.md", "tortoise/sdk.py"]},
        2: {"state": "conflicted", "paths": ["docs/product/sdk-rename-table.md"]},
    }
    headers = {
        "docs/product/sdk-rename-table.md": "# x\n\n**GENERATED — do not edit.**\n",
        "tortoise/sdk.py": "from __future__ import annotations\n",
    }
    opts = _opts(read_header=lambda path: headers.get(path))
    report = q.assemble_report(pulls, details, probes, NOW, opts)
    rows = {r["path"]: r for r in report["top_conflicting_paths"]}
    assert rows["docs/product/sdk-rename-table.md"]["class"] == "generated"
    assert rows["docs/product/sdk-rename-table.md"]["pr_count"] == 2
    assert rows["tortoise/sdk.py"]["class"] == "hand-written"
    assert report["top_summary"]["generated"] == 1
    assert report["top_summary"]["hand_written"] == 1


# ==========================================================================
# API vs merge-tree, unresolved, reconciliation
# ==========================================================================

def test_api_under_and_over_report_are_named_not_averaged():
    """(a) FAILS if the delta is computed but not attributed to specific PRs —
    the named set is what makes it actionable, and a swapped under/over pair is
    a silent sign error.
    (b) Reachable: MEASURED — the sibling instrument saw the API under-report
    conflicts (36 vs a true 44); the reverse (a `mergeable: false` PR that
    merges clean) is a race on `mergeable` going stale.
    """
    records = [
        _record(1, mergeable=True, mergetree="conflicted"),
        _record(2, mergeable=False, mergetree="clean"),
        _record(3, mergeable=False, mergetree="conflicted"),
        _record(4, mergeable=False, mergetree="unresolved"),
    ]
    delta = q.api_vs_mergetree(records)
    assert delta["under_reported"] == [1]
    assert delta["over_reported"] == [2]
    assert delta["unresolved"] == [4]
    assert delta["api_conflicting"] == 3
    assert delta["mergetree_conflicting"] == 2


def test_a_null_bucket_conflicted_pr_is_not_an_api_under_report():
    """(a) FAILS if `under_reported` is written `mergeable_bucket !=
    "conflicting"` — the negation also admits `unknown`, so a PR the API never
    resolved is scored as the API being WRONG.
    (b) Reachable: the per-PR GET can still return `mergeable: null` (the
    computation is in flight), and the merge-tree probe can independently
    report `conflicted`; the artifact's own `unknown` bucket is 0 today, so
    this is a latent conflation, pinned here so it cannot become a live one
    silently. The PR is not dropped either — it stays counted in the buckets
    and in `mergetree_conflicting`.
    """
    records = [
        _record(1, mergeable=None, mergetree="conflicted"),
        _record(2, mergeable=True, mergetree="conflicted"),
        _record(3, mergeable=False, mergetree="conflicted"),
    ]
    delta = q.api_vs_mergetree(records)
    assert delta["under_reported"] == [2]
    assert delta["over_reported"] == []
    assert delta["mergetree_conflicting"] == 3
    assert delta["api_conflicting"] == 1


def test_an_unresolved_probe_is_named_and_excluded_from_the_conflict_set():
    """(a) FAILS if an unresolved PR is counted as conflicted (inflating the
    path aggregation) or dropped from `unresolved_prs` (hiding the hole).
    (b) Reachable: the shallow-clone no-merge-base failure.
    """
    pulls = [{"number": 7}]
    details = {7: {"mergeable": False}}
    probes = {7: {"state": "unresolved", "paths": [], "unresolved_reason": "no-merge-base"}}
    opts = _opts(unresolved_reason={7: "no-merge-base"})
    report = q.assemble_report(pulls, details, probes, NOW, opts)
    assert report["unresolved_prs"] == [7]
    assert report["unresolved_count"] == 1
    assert report["api_vs_mergetree"]["mergetree_conflicting"] == 0
    assert report["top_conflicting_paths"] == []
    # NOT a tautology: the reason is threaded from the caller and must survive
    # into the artifact — dropping the key from `assemble_report` fails here.
    assert report["unresolved_reason"] == {7: "no-merge-base"}


def test_reconciliation_names_a_population_entry_missing_from_a_surface():
    """(a) FAILS if a PR present in the population but absent from the per-PR
    GET or the merge-tree probe map is silently reconciled away.
    (b) Reachable: a failed GET or a probe that never ran.
    """
    pulls = [{"number": 1}, {"number": 2}, {"number": 3}]
    report = q.reconcile_population(pulls, {1: {}, 2: {}}, {1: {}, 2: {}})
    assert report["population"] == 3
    assert report["detail_missing"] == [3]
    assert report["probe_missing"] == [3]


# ==========================================================================
# Age and abandonment
# ==========================================================================

def test_conflict_age_is_the_tighter_of_the_two_upper_bounds():
    """(a) FAILS if the two bounds are combined with max instead of min — the
    conflict cannot be older than EITHER bound, so the estimate is the min.
    (b) Reachable: branch head 1 day old, main's last touch of a conflicted
    path 3 days ago.
    """
    bounds = q.conflict_age_bounds(NOW, NOW - timedelta(days=1), NOW - timedelta(days=3))
    assert bounds["upper_bound_days"] == pytest.approx(1.0)
    assert bounds["from_head_commit_days"] == pytest.approx(1.0)
    assert bounds["from_main_path_touch_days"] == pytest.approx(3.0)


def test_conflict_age_is_unknown_without_any_timestamp():
    """(a) FAILS if a missing timestamp yields 0.0 (a fabricated "brand new
    conflict") instead of None.
    (b) Reachable: PR 5327-type probe where the head object could not be read.
    """
    bounds = q.conflict_age_bounds(NOW, None, None)
    assert bounds["upper_bound_days"] is None


def test_abandonment_threshold_is_inclusive_and_reconciles():
    """(a) FAILS if the comparison is strict (`>` not `>=`) — a PR idle exactly
    14.0 days flips to in-progress; and fails if in-progress is derived without
    subtracting unknown_idle (the split would exceed the population).
    (b) Reachable: idle exactly at the threshold, and a record whose
    `updated_at` was unreadable.
    """
    records = [
        _record(1, idle_days=14.0),
        _record(2, idle_days=13.999),
        _record(3, idle_days=None),
    ]
    split = q.abandonment_split(records, NOW, 14.0)
    assert split["abandoned"] == 1
    assert split["in_progress"] == 1
    assert split["unknown_idle"] == 1
    assert split["abandoned"] + split["in_progress"] + split["unknown_idle"] == 3


def test_percentile_is_the_nearest_rank_not_the_first_element():
    """(a) FAILS if `_percentile` returns `ordered[0]` (or any fixed element):
    the p90 the report publishes would be the minimum.
    (b) Reachable: the report publishes p90 for `idle_days` and for the
    conflict-age upper bound.
    """
    assert q._percentile([float(n) for n in range(1, 11)], 0.9) == 9.0
    assert q._percentile([], 0.9) is None


def test_abandonment_reports_a_sensitivity_ladder():
    """(a) FAILS if only one threshold is reported — the split is a judgement
    and its sensitivity must be visible.
    (b) Reachable: any non-empty idle set.
    """
    records = [_record(n, idle_days=float(n)) for n in (1, 5, 10, 20)]
    split = q.abandonment_split(records, NOW, 14.0)
    assert split["sensitivity"]["3"] == 3
    assert split["sensitivity"]["7"] == 2
    assert split["sensitivity"]["14"] == 1
    assert split["sensitivity"]["30"] == 0


# ==========================================================================
# Pagination completeness — a partial list is not a census
# ==========================================================================

def _patch_pages(monkeypatch, pages, link_last=None, numbers=None):
    monkeypatch.setattr(q, "gh_api", lambda path, paginate=False, timeout=120: pages)
    link = f'<https://api.github.com/x?page={link_last}>; rel="last"' if link_last else ""
    monkeypatch.setattr(q, "gh_api_headers", lambda path, timeout=60: {"link": link})
    if numbers is not None:
        for page, nums in zip(pages, numbers, strict=False):
            for pull, number in zip(page, nums, strict=False):
                pull["number"] = number


def test_a_full_final_page_is_complete(monkeypatch):
    """(a) FAILS if the completeness check requires a short final page, which a
    population that is an exact multiple of the page size never has.
    (b) Reachable: 200 open PRs — page sizes [100, 100], Link last=2.
    """
    pages = [[{"number": n} for n in range(100)], [{"number": n} for n in range(100, 200)]]
    _patch_pages(monkeypatch, pages, link_last=2)
    _pulls, pagination, complete = q.enumerate_open_prs()
    assert complete is True
    assert pagination["total"] == 200
    assert pagination["page_full_invariant"] is True


def test_a_short_non_final_page_is_incomplete(monkeypatch):
    """(a) FAILS if a page shortened by a truncated response is accepted. The
    page-full invariant is the only signal available when the API returns no
    total; a partial list must be exit 2, never a census.
    (b) Reachable: a truncated transfer mid-pagination (measured failure mode
    for this fleet's reads).
    """
    pages = [[{"number": n} for n in range(3)], [{"number": n} for n in range(3, 5)]]
    _patch_pages(monkeypatch, pages, link_last=2)
    _pulls, pagination, complete = q.enumerate_open_prs()
    assert complete is False
    assert pagination["page_full_invariant"] is False


def test_a_link_header_disagreeing_with_the_pages_read_is_incomplete(monkeypatch):
    """(a) FAILS if the independent Link-header cross-check is not consulted —
    two pages read while the API says there are three.
    (b) Reachable: a dropped page between the two requests.
    """
    pages = [[{"number": n} for n in range(100)], [{"number": n} for n in range(100, 150)]]
    _patch_pages(monkeypatch, pages, link_last=3)
    _pulls, pagination, complete = q.enumerate_open_prs()
    assert complete is False
    assert pagination["link_agrees"] is False


def test_an_unreadable_link_header_is_incomplete_not_agreeing(monkeypatch):
    """(a) FAILS if a failed header read is treated as "no Link header": the
    cross-check then reports agreement it never obtained, and a page set that
    merely LOOKS full — [100, 50] read while the API holds [100, 100, 50] —
    passes every remaining check and reports a share over a fraction of the
    queue. That is the truncated-census false confidence this module exists to
    prevent.
    (b) Reachable: the header request 403s (a secondary rate limit, live on
    this account) while the paginated read already succeeded, so only the
    cross-check is blind.
    """
    pages = [[{"number": n} for n in range(100)],
             [{"number": n} for n in range(100, 150)]]
    _patch_pages(monkeypatch, pages, link_last=3)
    # ...and then the independent header read fails outright.
    monkeypatch.setattr(q, "gh_api_headers", lambda path, timeout=60: q.UNKNOWN)

    _pulls, pagination, complete = q.enumerate_open_prs()
    assert complete is False
    assert pagination["link_header_read"] is False
    assert pagination["link_agrees"] is False


def test_duplicate_pr_numbers_are_incomplete(monkeypatch):
    """(a) FAILS if a paging overlap is silently deduplicated — the population
    would be off by the overlap count.
    (b) Reachable: GitHub paging shifts while commits land, and re-runs read
    overlapping pages.
    """
    pages = [[{"number": 1}], [{"number": 1}]]
    _patch_pages(monkeypatch, pages, link_last=1)
    _pulls, pagination, complete = q.enumerate_open_prs()
    assert complete is False
    assert pagination["duplicates"] == [1]


def test_an_empty_read_is_incomplete_not_an_empty_queue(monkeypatch):
    """(a) FAILS if the completeness check does not require a non-empty
    population — one empty page satisfies the page-full invariant vacuously,
    so an empty read would report a clean empty queue instead of exit 2.
    (b) Reachable: a rate-limited read that returns `[]` rather than erroring.
    """
    _patch_pages(monkeypatch, [[]], link_last=None)
    pulls, pagination, complete = q.enumerate_open_prs()
    assert pulls == []
    assert pagination["total"] == 0
    assert complete is False


def test_an_unreadable_population_is_incomplete(monkeypatch):
    """(a) FAILS if a failed pagination read returns an empty-but-complete
    population (an empty read is not an empty queue).
    (b) Reachable: a rate-limited or offline `gh api`.
    """
    monkeypatch.setattr(q, "gh_api", lambda path, paginate=False, timeout=120: q.UNKNOWN)
    pulls, _pagination, complete = q.enumerate_open_prs()
    assert pulls == []
    assert complete is False


# ==========================================================================
# End-to-end through the CLI on a fixture
# ==========================================================================

def _fixture():
    """A frozen snapshot. Every number the CLI test asserts is derived here."""
    pulls = [
        {"number": 1, "title": "generated doc", "draft": False, "mergeable": None},
        {"number": 2, "title": "conflicted source", "draft": True, "mergeable": None},
        {"number": 3, "title": "clean", "draft": False, "mergeable": None},
    ]
    details = {
        "1": {"mergeable": False, "mergeable_state": "dirty", "draft": False,
              "created_at": "2026-09-27T12:00:00Z", "updated_at": "2026-09-27T12:00:00Z",
              "head": {"sha": "aaa"}},
        "2": {"mergeable": False, "mergeable_state": "dirty", "draft": True,
              "created_at": "2026-09-26T12:00:00Z", "updated_at": "2026-09-27T12:00:00Z",
              "head": {"sha": "bbb"}},
        "3": {"mergeable": True, "mergeable_state": "blocked", "draft": False,
              "created_at": "2026-09-28T06:00:00Z", "updated_at": "2026-09-28T06:00:00Z",
              "head": {"sha": "ccc"}},
    }
    probes = {
        "1": {"state": "conflicted", "paths": ["docs/product/sdk-rename-table.md"],
              "head_commit_time": "2026-09-27T12:00:00Z",
              "main_path_touch_time": "2026-09-27T18:00:00Z"},
        "2": {"state": "conflicted", "paths": ["docs/product/sdk-rename-table.md", "tortoise/sdk.py"],
              "head_commit_time": "2026-09-26T12:00:00Z",
              "main_path_touch_time": "2026-09-28T06:00:00Z"},
        "3": {"state": "clean", "paths": []},
    }
    return {
        "pulls": pulls,
        "details": details,
        "probes": probes,
        "now": "2026-09-28T12:00:00Z",
        "origin_main_sha": "e12d676e8a5f8bbf29cddbe1cca7d98cbc2d5040",
        "headers": {
            "docs/product/sdk-rename-table.md": "# x\n\n**GENERATED — do not edit.**\n",
            "tortoise/sdk.py": "from __future__ import annotations\n",
        },
        "generator_index": {},
        "pagination": {"complete": True, "pages_read": 1, "total": 3},
        "rate_limit": {"core": {"limit": 5000, "remaining": 5000}},
    }


def test_cli_fixture_round_trip_reproduces_the_measured_share(tmp_path, capsys):
    """(a) FAILS if the fixture plumbing loses a surface (e.g. `probes` keys
    stay strings and every probe reads unresolved), or if the share is taken
    from the pulls' `mergeable: null` instead of the details.
    (b) Reachable: the fixture carries 3 pulls — 2 conflicting, 1 mergeable,
    0 unknown — so the share is exactly 2/3.
    """
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    code = q.main(["--fixture", str(path), "--no-write", "--out", str(tmp_path / "out.json")])
    captured = capsys.readouterr().out
    assert code == 0
    assert "conflicting=2" in captured
    assert "unknown=0" in captured
    assert "share_of_known=66.7%" in captured
    assert "docs/product/sdk-rename-table.md" in captured


def test_cli_fixture_writes_a_dated_artifact(tmp_path):
    """(a) FAILS if the artifact is not written, is not JSON, or loses the
    classifier rule / snapshot time the daily series is appended from.
    (b) Reachable: the fixture pins `now`, so the dated filename is exact.
    """
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    out = tmp_path / "census.json"
    q.main(["--fixture", str(path), "--out", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["schema"] == q.SCHEMA
    assert report["snapshot_at"].startswith("2026-09-28T12:00:00")
    assert report["origin_main_sha"] == "e12d676e8a5f8bbf29cddbe1cca7d98cbc2d5040"
    assert "rule" in report["classifier"]
    assert report["share"] == {
        "population": 3, "mergeable": 1, "conflicting": 2, "unknown": 0,
        "known": 3, "share_of_known": pytest.approx(2 / 3),
        "share_of_population": pytest.approx(2 / 3),
    }
    assert report["unresolved_count"] == 0
    assert report["age_and_abandonment"]["conflicted_prs"]["abandoned"] == 0
    assert report["age_and_abandonment"]["conflicted_drafts"] == 1


def test_cli_fixture_is_reproducible_across_runs(tmp_path):
    """(a) FAILS if any field is derived from the real clock, dict iteration
    order leaks into a list, or a set is serialised — two runs of the same
    fixture must be byte-identical.
    (b) Reachable: the fixture pins `now`; the only variance left is the tool.
    """
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(_fixture()), encoding="utf-8")
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    q.main(["--fixture", str(path), "--out", str(first)])
    q.main(["--fixture", str(path), "--out", str(second)])
    text_a = first.read_text(encoding="utf-8")
    text_b = second.read_text(encoding="utf-8")
    assert json.loads(text_a)["share"] == json.loads(text_b)["share"]
    assert json.loads(text_a)["top_conflicting_paths"] == json.loads(text_b)["top_conflicting_paths"]
    # The artifact's own `argv` differs between the two runs (different --out);
    # everything the measurement is made of must not.
    strip = lambda t: {k: v for k, v in json.loads(t).items() if k != "argv"}  # noqa: E731
    assert json.dumps(strip(text_a), sort_keys=True) == json.dumps(strip(text_b), sort_keys=True)


def test_the_cli_summary_does_not_fabricate_a_zero_percent(tmp_path, capsys):
    """(a) FAILS if the CLI formats a `None` share as `0.0%` — an empty queue
    would read as a measured clean bill, the exact fabrication the artifact's
    `null` and `share_percent()` forbid.
    (b) Reachable: an empty population (fixture with zero pulls).
    """
    fixture = {
        "pulls": [], "details": {}, "probes": {}, "now": "2026-09-28T12:00:00Z",
        "origin_main_sha": "deadbeef", "headers": {}, "generator_index": {},
    }
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    q.main(["--fixture", str(path), "--no-write"])
    out = capsys.readouterr().out
    assert "share_of_known=UNKNOWN" in out
    assert "share_of_population=UNKNOWN" in out
    assert "0.0%" not in out


def test_the_artifact_is_json_serialisable_with_no_sets():
    """(a) FAILS with `TypeError: Object of type set is not JSON serialisable`
    if a set escapes into the report — the aggregation stores sets internally.
    (b) Reachable: `aggregate_paths` builds `set()` per path.
    """
    report = q.assemble_report([{"number": 1}], {1: {"mergeable": False}},
                               {1: {"state": "conflicted", "paths": ["a.py", "b.py"]}},
                               NOW, _opts())
    json.dumps(report)


def _opts(**overrides):
    """Default `assemble_report` options; overridable per test."""
    opts = {
        "argv": [],
        "top": 20,
        "abandoned_idle_days": 14.0,
        "generator_index": {},
        "read_header": lambda _path: None,
        "notes": [],
        "origin_main_sha": "deadbeef",
        "pagination": {},
        "reconciliation": {},
        "rate_limit": {},
        "unresolved_reason": {},
    }
    opts.update(overrides)
    return opts
