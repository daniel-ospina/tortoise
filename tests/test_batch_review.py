"""Tests for tools/batch_review.py — the batch is transport, never a verdict.

The load-bearing property: NO path exists by which one batch-level answer becomes
a per-PR verdict. These tests aim at exactly that, plus the per-PR abort rules.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("batch_review", ROOT / "tools" / "batch_review.py")
br = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["batch_review"] = br  # dataclasses resolves the module by name
SPEC.loader.exec_module(br)

HEAD_A = "a" * 40
HEAD_B = "b" * 40
DIFF_A = "1" * 64
DIFF_B = "2" * 64


def manifest(*specs, status="queued"):
    return {
        "repo": "o/r",
        "batch_id": "batch0001",
        "prs": [
            {
                "pr": pr,
                "head_sha": head,
                "diff_sha256": digest,
                "est_tokens": 100,
                "diff_bytes": 350,
                "title": f"t{pr}",
                "status": status,
            }
            for pr, head, digest in specs
        ],
    }


def result(pr, head, digest, verdict="clean", findings=None, **extra):
    return {
        "pr": pr,
        "head_sha": head,
        "diff_sha256": digest,
        "verdict": verdict,
        "summary": "s",
        "findings": findings or [],
        **extra,
    }


# ── the anti-pattern: a batch verdict standing in for per-PR verdicts ────────


def test_a_top_level_batch_verdict_cannot_pass_as_per_pr_verdicts():
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(m, {"verdict": "clean", "reason": "batch looks fine"})
    assert [o["status"] for o in out] == ["invalid", "invalid"]
    assert all(o["verdict"] is None for o in out)
    assert "cannot stand in" in out[0]["reason"]


def test_a_results_list_without_the_prs_is_not_a_pass():
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(m, {"results": []})
    assert {o["status"] for o in out} == {"invalid"}
    assert all("no result" in o["reason"] for o in out)


def test_one_global_result_does_not_cover_the_other_prs():
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(m, {"results": [result(1, HEAD_A, DIFF_A)]})
    assert out[0]["status"] == "ok"
    assert out[1]["status"] == "invalid"
    assert "no result for this PR" in out[1]["reason"]


# ── per-PR isolation: a bad result fails THAT PR, never the batch ───────────


def test_a_malformed_result_does_not_invalidate_a_valid_sibling():
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(
        m,
        {
            "results": [
                result(1, HEAD_A, DIFF_A),
                {"pr": 2, "head_sha": HEAD_B, "diff_sha256": DIFF_B, "verdict": "LGTM"},
            ]
        },
    )
    assert out[0]["status"] == "ok" and out[0]["verdict"] == "clean"
    assert out[1]["status"] == "invalid"
    assert "not in" in out[1]["reason"]


@pytest.mark.parametrize(
    "mutate,needle",
    [
        (lambda r: r.update(head_sha="c" * 40), "head_sha mismatch"),
        (lambda r: r.update(diff_sha256="d" * 64), "diff_sha256 mismatch"),
        (lambda r: r.update(verdict="looks-fine"), "not in"),
        (lambda r: r.update(findings="nope"), "not a list"),
        (lambda r: r.update(findings=[{"severity": "P3", "file": "a", "claim": "x"}]), "severity"),
        (lambda r: r.update(findings=[{"severity": "P1", "file": "", "claim": "x"}]), "file"),
        (lambda r: r.update(findings=[{"severity": "P1", "file": "a", "claim": " "}]), "claim"),
        (
            lambda r: r.update(findings=[{"severity": "P1", "file": "a", "claim": "x", "line": 0}]),
            "line",
        ),
    ],
)
def test_schema_violations_are_per_pr(mutate, needle):
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    bad = result(2, HEAD_B, DIFF_B)
    mutate(bad)
    out = br.validate_answer(m, {"results": [result(1, HEAD_A, DIFF_A), bad]})
    assert out[0]["status"] == "ok"
    assert out[1]["status"] == "invalid"
    assert needle in out[1]["reason"]


def test_head_binding_is_per_pr_and_is_not_transferable():
    """A verdict echoed from the other PR's head is refused, not accepted."""
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(
        m,
        {
            "results": [
                result(1, HEAD_A, DIFF_A),
                # PR 2's result carries PR 1's head+digest: a batch verdict
                # standing in for a head it never read.
                result(2, HEAD_A, DIFF_A),
            ]
        },
    )
    assert out[0]["status"] == "ok"
    assert out[1]["status"] == "invalid"
    assert "head_sha mismatch" in out[1]["reason"]


def test_duplicate_result_invalidates_only_that_pr():
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    out = br.validate_answer(
        m,
        {
            "results": [
                result(1, HEAD_A, DIFF_A),
                result(2, HEAD_B, DIFF_B),
                result(2, HEAD_B, DIFF_B, verdict="issues"),
            ]
        },
    )
    assert out[0]["status"] == "ok"
    assert out[1]["status"] == "invalid"
    assert "duplicate" in out[1]["reason"]


def test_a_result_for_an_unknown_pr_is_ignored_and_never_recorded():
    m = manifest((1, HEAD_A, DIFF_A))
    out = br.validate_answer(
        m, {"results": [result(1, HEAD_A, DIFF_A), result(99, "e" * 40, "f" * 64)]}
    )
    assert len(out) == 1 and out[0]["status"] == "ok"


# ── fail-closed coercion: clean + a blocking finding is never "clean" ───────


@pytest.mark.parametrize("verdict", ["clean", "clean-micro", "clean-low"])
def test_clean_contradicted_by_a_blocking_finding_is_refused(verdict):
    m = manifest((1, HEAD_A, DIFF_A))
    out = br.validate_answer(
        m,
        {
            "results": [
                result(
                    1,
                    HEAD_A,
                    DIFF_A,
                    verdict=verdict,
                    findings=[
                        {"severity": "P1", "file": "tools/x.py", "line": 3, "claim": "swallowed rc"}
                    ],
                )
            ]
        },
    )
    assert out[0]["status"] == "ok"
    assert out[0]["verdict"] == "issues"
    assert "contradicted" in out[0]["reason"]
    assert out[0]["findings"][0]["severity"] == "P1"


def test_p2_only_is_allowed_to_stay_clean():
    m = manifest((1, HEAD_A, DIFF_A))
    out = br.validate_answer(
        m,
        {
            "results": [
                result(
                    1,
                    HEAD_A,
                    DIFF_A,
                    verdict="clean",
                    findings=[{"severity": "P2", "file": "docs/a.md", "claim": "stale line"}],
                )
            ]
        },
    )
    assert out[0]["verdict"] == "clean"
    assert out[0]["findings"][0]["severity"] == "P2"


# ── caps derived from measured tokens, not from a round number ─────────────


def _entries(sizes):
    return [
        br.PRInput(pr=i + 1, head_sha=HEAD_A, diff_sha256=DIFF_A, est_tokens=s, status="queued")
        for i, s in enumerate(sizes)
    ]


def test_budget_cap_excludes_the_tail_and_keeps_order():
    es = br.select_batch(_entries([400, 400, 400]), budget_tokens=1000, max_prs=8)
    assert [e.status for e in es] == ["queued", "queued", "excluded"]
    assert "budget" in es[2].reason


def test_max_prs_is_an_attention_cap_independent_of_size():
    es = br.select_batch(_entries([1] * 10), budget_tokens=10**6, max_prs=3)
    assert [e.status for e in es] == ["queued"] * 3 + ["excluded"] * 7
    assert "max_prs" in es[3].reason


def test_a_single_oversized_diff_falls_out_instead_of_crowding_the_batch():
    es = br.select_batch(_entries([10, 90_000, 10]), budget_tokens=100_000, max_prs=8)
    assert es[1].status == "excluded" and "too_large" in es[1].reason
    assert es[0].status == "queued" and es[2].status == "queued"


def test_excluded_prs_are_never_queued_twice():
    es = _entries([10, 10])
    es[0].status, es[0].reason = "excluded", "not_open (closed)"
    out = br.select_batch(es)
    assert out[0].status == "excluded" and "not_open" in out[0].reason


def test_est_tokens_scales_with_bytes_and_errs_high():
    assert br.est_tokens(3500) == 1001
    assert br.est_tokens(0) == 1
    assert br.est_tokens(7000) > 2 * br.est_tokens(3500) - 3


# ── the record plan: the only mint path, and its refusals ──────────────────


def _ok(pr, verdict="clean"):
    return {
        "pr": pr,
        "head_sha": HEAD_A,
        "diff_sha256": DIFF_A,
        "verdict": verdict,
        "status": "ok",
        "reason": "",
        "findings": [],
    }


def test_plan_records_binds_head_and_evidence_per_pr():
    plan = br.plan_records(manifest((1, HEAD_A, DIFF_A)), [_ok(1)], {1: "comment:42"})
    assert plan == [
        [
            "record-review.sh",
            "1",
            HEAD_A,
            "clean",
            "o/r",
            "--evidence",
            "comment:42",
            "--expect-head",
            HEAD_A,
        ]
    ]


def test_plan_records_never_mints_an_issues_verdict():
    assert (
        br.plan_records(manifest((1, HEAD_A, DIFF_A)), [_ok(1, "issues")], {1: "comment:42"}) == []
    )


def test_plan_records_never_mints_without_its_own_evidence():
    assert br.plan_records(manifest((1, HEAD_A, DIFF_A)), [_ok(1)], {}) == []
    # evidence for a DIFFERENT PR does not carry.
    assert (
        br.plan_records(
            manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B)), [_ok(1)], {2: "comment:9"}
        )
        == []
    )


def test_plan_records_refuses_an_outcome_for_an_excluded_manifest_entry():
    m = manifest((1, HEAD_A, DIFF_A))
    m["prs"][0]["status"] = "excluded"  # e.g. too_large — never reviewed in this batch
    assert br.plan_records(m, [_ok(1)], {1: "comment:42"}) == []


def test_plan_records_refuses_a_digest_that_is_not_the_manifest_digest():
    m = manifest((1, HEAD_A, DIFF_A))
    skewed = {**_ok(1), "diff_sha256": DIFF_B}
    assert br.plan_records(m, [skewed], {1: "comment:42"}) == []


def test_plan_records_refuses_a_pr_that_fell_out():
    dropped = {**_ok(1), "status": "head_moved"}
    assert br.plan_records(manifest((1, HEAD_A, DIFF_A)), [dropped], {1: "comment:42"}) == []


def test_plan_records_does_not_use_the_head_from_the_outcome_if_it_drifted():
    # ONLY the head can refuse this one: the outcome carries the MANIFEST digest, so
    # a plan that lost the head-equality guard (and substituted the manifest head
    # for the drifted one) would emit a record instead of refusing.
    skewed = {**_ok(1), "head_sha": HEAD_A, "diff_sha256": DIFF_B}
    m = manifest((1, HEAD_B, DIFF_B))
    # The manifest head governs; the plan must not emit a record at all here
    # because the outcome's head is not the manifest's (defence in depth: the
    # validator already refuses this, the plan must not reintroduce it).
    plan = br.plan_records(m, [skewed], {1: "comment:42"})
    assert plan == []


# ── diff digest: the ONE shared normalizer, and the empty-body refusal ─────


def test_empty_or_non_diff_body_carries_no_digest():
    assert br.normalized_diff_sha256(b"") == ""
    assert br.normalized_diff_sha256(b"Not Found") == ""


RAW_DIFF = (
    b"diff --git a/x b/x\nindex 1111111..2222222 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
)
RAW_DIFF_INDEX_MOVED = (
    b"diff --git a/x b/x\nindex 3333333..4444444 100644\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
)


def test_digest_is_stable_for_the_same_bytes():
    d1 = br.normalized_diff_sha256(RAW_DIFF)
    d2 = br.normalized_diff_sha256(RAW_DIFF)
    assert d1 == d2 and len(d1) == 64


def _stub_normalizer(tmp_path):
    """A stand-in for agent-infra's `scripts/lib/diff-normalize.py`.

    The real normalizer is HOST STATE: the CI runners carry no `scripts/lib/`
    (the workflow is self-contained on purpose, and `scripts/` is gitignored
    since #9475), so `find_normalizer()` returns None there and the digest falls
    back to the raw sha256. A digest assertion must therefore INJECT the
    normalizer rather than depend on whether this machine happens to resolve one.
    """
    norm = tmp_path / "diff-normalize.py"
    norm.write_text(
        "import sys\n"
        "for line in sys.stdin.buffer.read().splitlines(keepends=True):\n"
        "    if not line.startswith(b'index '):\n"
        "        sys.stdout.buffer.write(line)\n",
        encoding="utf-8",
    )
    return norm


def test_digest_drops_the_derived_index_line_through_the_normalizer(tmp_path):
    norm = _stub_normalizer(tmp_path)
    d1 = br.normalized_diff_sha256(RAW_DIFF, normalizer=norm)
    # The stub actually ran — a fallback to the raw sha256 would move with `index`.
    assert d1 != br.sha256_bytes(RAW_DIFF)
    # The index line is derived rendering: dropping it must not move the digest.
    assert br.normalized_diff_sha256(RAW_DIFF_INDEX_MOVED, normalizer=norm) == d1


def test_digest_falls_back_to_the_raw_sha_when_no_normalizer_resolves(monkeypatch):
    # The documented fallback (`normalized_diff_sha256` says it matches
    # `record-review.sh`): with no normalizer the digest IS the raw sha256, so the
    # derived `index` line DOES move it. Pinned deterministically, because this is
    # exactly the branch a CI runner takes.
    monkeypatch.setattr(br, "find_normalizer", lambda: None)
    assert br.normalized_diff_sha256(RAW_DIFF) == br.sha256_bytes(RAW_DIFF)
    assert br.normalized_diff_sha256(RAW_DIFF_INDEX_MOVED) != br.normalized_diff_sha256(RAW_DIFF)


# ── end-to-end prepare with a stub gh (hermetic, no network) ───────────────


@pytest.fixture()
def stub_gh(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [ "$3" = "--jq" ]; then\n'
        '  case "$2" in\n'
        '    */pulls/1) printf "open\\taaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\tbase\\tfirst\\n" ;;\n'
        '    */pulls/2) printf "open\\tbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\\tbase\\tsecond\\n" ;;\n'
        '    */pulls/3) printf "closed\\tcccccccccccccccccccccccccccccccccccccccc\\tbase\\tmerged\\n" ;;\n'
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        'if printf "%s " "$@" | grep -q "vnd.github.v3.diff"; then\n'
        '  case "$4" in\n'
        "    */pulls/1) printf 'diff --git a/a.txt b/a.txt\\n--- a/a.txt\\n+++ b/a.txt\\n@@ -1 +1 @@\\n-x\\n+y\\n' ;;\n"
        "    */pulls/2) printf 'diff --git a/b.txt b/b.txt\\n--- a/b.txt\\n+++ b/b.txt\\n@@ -1 +1 @@\\n-p\\n+q\\n' ;;\n"
        "  esac\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return gh


def test_prepare_one_read_per_pr_and_a_prompt_that_delimits_each(stub_gh, tmp_path):
    out = tmp_path / "batch"
    rc = br.main(
        ["prepare", "--repo", "o/r", "--pr", "1", "--pr", "2", "--pr", "3", "--out", str(out)]
    )
    assert rc == 0
    m = json.loads((out / "manifest.json").read_text())
    status = {e["pr"]: e["status"] for e in m["prs"]}
    assert status == {1: "queued", 2: "queued", 3: "excluded"}
    assert "not_open" in next(e for e in m["prs"] if e["pr"] == 3)["reason"]
    # The diff was cached, and the manifest binds exactly that cache.
    cached = Path(m["prs"][0]["diff_path"])
    assert cached.is_file()
    assert br.sha256_bytes(cached.read_bytes()) != ""
    assert m["prs"][0]["diff_sha256"] == br.normalized_diff_sha256(cached.read_bytes())
    prompt = Path(m["prompt_path"]).read_text()
    assert m["nonce"] and m["nonce"] in prompt
    assert "===== BEGIN PR 1 @ " in prompt and f"nonce={m['nonce']}" in prompt
    assert "===== END PR 1 @ " in prompt
    assert prompt.count(f"nonce={m['nonce']}") >= 4  # BEGIN+END for each of 2 PRs
    assert "===== BEGIN PR 2 @" in prompt
    assert "BEGIN PR 3" not in prompt  # closed PR never enters the prompt
    assert "the batch is a transport optimisation" in prompt.lower()
    assert m["batch_id"] and m["batch_id"] == br.sha256_bytes(prompt.encode())[:16]


def test_prepare_with_nothing_reviewable_is_a_loud_error(stub_gh, tmp_path):
    rc = br.main(["prepare", "--repo", "o/r", "--pr", "3", "--out", str(tmp_path / "b")])
    assert rc == 2


def test_verify_surfaces_unknown_pr_warnings(tmp_path, capsys):
    m = manifest((1, HEAD_A, DIFF_A))
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(m))
    apath = tmp_path / "answer.json"
    apath.write_text(
        json.dumps({"results": [result(1, HEAD_A, DIFF_A), result(99, "e" * 40, "f" * 64)]})
    )
    rc = br.main(["verify", "--manifest", str(mpath), "--answer", str(apath)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "not in this batch" in err and "99" in err


def test_verify_returns_3_and_names_the_fallout(tmp_path, capsys):
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(m))
    apath = tmp_path / "answer.json"
    apath.write_text(json.dumps({"results": [result(1, HEAD_A, DIFF_A)]}))
    rc = br.main(["verify", "--manifest", str(mpath), "--answer", str(apath)])
    assert rc == 3
    err = capsys.readouterr().err
    assert "#2" in err and "do not reuse another PR's verdict" in err


# ── the posted claim is MEASURED: findings must be anchored to the diff ─────

CACHED_DIFF = (
    "diff --git a/tools/thing.py b/tools/thing.py\n"
    "--- a/tools/thing.py\n"
    "+++ b/tools/thing.py\n"
    "@@ -1 +1 @@\n"
    "-x\n"
    "+y\n"
)


def _manifest_with_cached_diff(tmp_path, body=CACHED_DIFF):
    m = manifest((1, HEAD_A, DIFF_A))
    p = tmp_path / "1.diff"
    p.write_text(body, encoding="utf-8")
    m["prs"][0]["diff_path"] = str(p)
    return m


def _finding(file, severity="P2", claim="c", **extra):
    return {"severity": severity, "file": file, "claim": claim, **extra}


def test_a_finding_anchored_outside_the_cached_diff_is_refused(tmp_path):
    # `render_comment` asserts the anchor; `validate_answer` must therefore have
    # measured it. Before this check, ANY non-empty `file` string passed, so the
    # posted sentence was a claim the code never tested.
    m = _manifest_with_cached_diff(tmp_path)
    out = br.validate_answer(
        m, {"results": [result(1, HEAD_A, DIFF_A, findings=[_finding("other/file.py")])]}
    )
    assert out[0]["status"] == "invalid"
    assert "not in this PR's diff" in out[0]["reason"]
    assert out[0]["verdict"] is None


def test_a_finding_anchored_in_the_cached_diff_passes_and_the_footer_says_checked(tmp_path):
    m = _manifest_with_cached_diff(tmp_path)
    out = br.validate_answer(
        m,
        {
            "results": [
                result(1, HEAD_A, DIFF_A, findings=[_finding("tools/thing.py", line=3)])
            ]
        },
    )
    assert out[0]["status"] == "ok"
    assert out[0]["anchors_checked"] is True
    comment = br.render_comment(m, m["prs"][0], out[0])
    assert "anchored to files in this PR's diff" in comment


def test_a_rename_anchors_either_side_of_the_cached_diff(tmp_path):
    # `diff --git a/old b/new` names two paths; a finding about either is anchored.
    m = _manifest_with_cached_diff(
        tmp_path,
        body="diff --git a/old.py b/new.py\n--- a/old.py\n+++ b/new.py\n@@ -1 +1 @@\n-x\n+y\n",
    )
    out = br.validate_answer(
        m,
        {
            "results": [
                result(1, HEAD_A, DIFF_A, findings=[_finding("old.py"), _finding("new.py")])
            ]
        },
    )
    assert out[0]["status"] == "ok", out[0]["reason"]


def test_the_anchoring_claim_is_withheld_when_no_cached_diff_exists():
    # A manifest with no readable cache cannot measure the anchor: the finding is
    # accepted (unknown != wrong) but the comment must not assert it was checked.
    m = manifest((1, HEAD_A, DIFF_A))
    out = br.validate_answer(
        m, {"results": [result(1, HEAD_A, DIFF_A, findings=[_finding("anything.py")])]}
    )
    assert out[0]["status"] == "ok"
    assert out[0]["anchors_checked"] is False
    comment = br.render_comment(m, m["prs"][0], out[0])
    assert "anchors were NOT checked" in comment
    assert "anchored to files in this PR's diff" not in comment


# ── record: the ONLY executor of a minted record, over a stub recorder ─────


@pytest.fixture()
def stub_recorder(tmp_path):
    """A stand-in for `record-review.sh`: journals its argv, and can fail one PR."""
    log = tmp_path / "recorder-argv.log"
    script = tmp_path / "record-review.sh"
    script.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" >> "{log}"\n'
        'echo "---" >> '
        f'"{log}"\n'
        'if [ "$1" = "2" ]; then exit 4; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script, log


def _record_files(tmp_path, m, outcomes, evidence):
    mpath = tmp_path / "manifest.json"
    mpath.write_text(json.dumps(m))
    apath = tmp_path / "verified.json"
    apath.write_text(json.dumps({"results": outcomes}))
    epath = tmp_path / "evidence.json"
    epath.write_text(json.dumps(evidence))
    return ["record", "--manifest", str(mpath), "--answer", str(apath), "--evidence", str(epath)]


def test_record_execs_the_recorder_once_per_clean_pr_with_the_planned_argv(
    tmp_path, stub_recorder
):
    script, log = stub_recorder
    m = manifest((1, HEAD_A, DIFF_A))
    outcome = br.validate_answer(m, {"results": [result(1, HEAD_A, DIFF_A)]})[0]
    argv = _record_files(tmp_path, m, [outcome], {1: "comment:42"})
    argv += ["--record-review", str(script)]
    assert br.main(argv) == 0
    lines = [ln for ln in log.read_text().splitlines() if ln != "---"]
    # The exact contract the plan builds: pr, head, verdict, repo, evidence, expect-head.
    assert lines == ["1", HEAD_A, "clean", "o/r", "--evidence", "comment:42", "--expect-head", HEAD_A]


def test_record_names_the_pr_and_fails_when_the_recorder_rejects(tmp_path, stub_recorder, capsys):
    script, _log = stub_recorder
    m = manifest((1, HEAD_A, DIFF_A), (2, HEAD_B, DIFF_B))
    outcomes = [
        br.validate_answer(manifest((1, HEAD_A, DIFF_A)), {"results": [result(1, HEAD_A, DIFF_A)]})[0],
        br.validate_answer(manifest((2, HEAD_B, DIFF_B)), {"results": [result(2, HEAD_B, DIFF_B)]})[0],
    ]
    argv = _record_files(
        tmp_path, m, outcomes, {1: "comment:42", 2: "comment:43"}
    ) + ["--record-review", str(script)]
    assert br.main(argv) == 1
    err = capsys.readouterr().err
    assert "#2" in err and "rc=4" in err and "NO record" in err


def test_record_refuses_when_no_clean_verdict_has_evidence(tmp_path, stub_recorder, capsys):
    script, _log = stub_recorder
    m = manifest((1, HEAD_A, DIFF_A))
    outcomes = [br.validate_answer(m, {"results": [result(1, HEAD_A, DIFF_A, verdict="issues")]})[0]]
    argv = _record_files(tmp_path, m, outcomes, {}) + ["--record-review", str(script)]
    assert br.main(argv) == 1
    err = capsys.readouterr().err
    assert "nothing to record" in err
    assert "must not land" in err
