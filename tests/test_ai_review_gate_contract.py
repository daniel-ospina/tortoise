"""ai-review-gate ↔ record-review.sh signing-contract guard (#3076).

The ``ai-review-gate`` check (``.github/workflows/ai-review-gate.yml``)
accepts a PR only when the body — or, since #1224, a repo-affiliated PR
comment — carries a signed evidence marker whose HMAC
verifies against the ``AI_REVIEW_GATE_KEY`` secret::

    review recorded: reviews/<PR>.json verdict=clean @ <40-hex-sha>[ diff=<64-hex>] (<owner/repo>) sig=<64-hex>

The optional ``diff=<64-hex>`` segment was added producer-side by #2982, where
it became part of the SIGNED text. The gate's candidate regex was not updated
with it, so every correctly-signed post-#2982 marker failed the regex, never
reached the HMAC check, and was reported with the misleading
"not signed or not for <repo>" message — which drove ``--admin`` merges on
#2991/#3069/#3078 (#3076).

These tests execute the workflow's ACTUAL ``run:`` block (parsed out of the
YAML — no duplicated copy to drift) against fixture markers signed with a
fixture key, and pin BOTH ends of the contract:

* the two marker shapes record-review.sh can emit are ACCEPTED;
* the three failure causes are DISTINGUISHED (no signature / wrong repo /
  HMAC mismatch) and the mismatch path prints sha256 prefixes, never the key.

The block is executed offline: ``gh`` is stubbed to fail BY DEFAULT, so the run block
falls back to the ``PR_BODY`` env var (its documented fallback) — that is the
``event-snapshot`` path. ``_run_gate(live_body=…)`` opts into the OTHER source: the
stub then answers the REST body fetch (``body=rest-live``) while still failing the diff
fetch. Both paths are hermetic and fast.


#1224 adds the COMMENT channel: ``record-review.sh`` posts the same signed
marker as a PR comment as well, because the body is a mutable field any later
legitimate edit rewrites. The gate must therefore accept a marker from EITHER
channel, while every security property is preserved — the HMAC over the marker
text is what makes it unforgeable (a forged comment FAILS), and the binding
rules are untouched (a stale comment FAILS). The #1224 cases below drive a
second stub that serves the PR-comment endpoint through the caller's own
``--jq`` expression, so the gate's admission filter is genuinely exercised.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

_WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ai-review-gate.yml"
_GATE_STEP_NAME = "Validate signed AI review evidence in PR body"

# ── Fixtures ───────────────────────────────────────────────────────────────
# A fixture key, NOT the repo secret (which must never appear in the repo).
_FIXTURE_KEY = "fixture-key-not-the-repo-secret"
_HEAD = "9d05b01f8f187915fc5d1048dec72604793bbd46"
_PR = "3069"
_REPO = "daniel-ospina/tortoise"
_DIFF = "8b96e47622011603a31a6b7a59bd096fc7c5850c898a887d528811ff7806a7fd"
_STALE_HEAD = "a" * 40

# Offline ``gh`` stub for the #1224 comment-channel cases. It serves ONLY the
# PR-comment endpoint, applying the caller's ``--jq`` expression with the REAL
# jq — so a gate that stops passing the affiliation filter is genuinely
# observable instead of being handed the unfiltered payload by the stub.
#
# Every other call exits 1, which keeps the PR body on its documented PR_BODY
# fallback and leaves BOTH live diff digests empty (the markers used by these
# cases are head-bound, so acceptance rule (a) is the path under test).
_COMMENT_STUB = """#!/usr/bin/env bash
payload="__PAYLOAD__"
text="__TEXT__"
url=""; jq_expr=""
args=("$@"); i=0
while [ "$i" -lt "${#args[@]}" ]; do
  a="${args[$i]}"
  case "$a" in
    --jq) jq_expr="${args[$((i+1))]:-}"; i=$((i+2));;
    -H|--header|-X|--input|-f|--field|-F|--raw-field) i=$((i+2));;
    --paginate|--silent) i=$((i+1));;
    repos/*) url="$a"; i=$((i+1));;
    *) i=$((i+1));;
  esac
done
case "$url" in
  */issues/*/comments)
    if [ -n "__FAIL__" ]; then
      # Model the WORST-CASE failure: the call exits non-zero but its stdout
      # still carries marker text. A gate that captures stdout on the failure
      # path (``|| true``, or an unguarded assignment) accepts it — the
      # fail-open this pins shut. A real ``gh api`` prints its HTTP error body
      # (JSON) there instead; the marker-line form is strictly harder.
      cat "$text"
      exit 1
    fi
    exec jq -r "$jq_expr" "$payload"
    ;;
esac
exit 1
"""


@lru_cache(maxsize=1)
def _gate_run_block() -> str:
    """The ai-review-gate verification shell, straight from the workflow file.

    Selected by STEP NAME, not position: the point of these tests is that the
    real block is exercised, so inserting/reordering steps must fail loudly
    here rather than silently point the tests at a different step.
    """
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["ai-review-gate"]["steps"]
    for step in steps:
        if step.get("name") == _GATE_STEP_NAME:
            return step["run"]
    raise AssertionError(
        f"ai-review-gate step {_GATE_STEP_NAME!r} not found — the tests would drift"
    )


def _marker(text: str, key: str = _FIXTURE_KEY) -> str:
    """Sign ``text`` the way record-review.sh does — HMAC-SHA256 + ' sig='."""
    sig = hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
    return f"{text} sig={sig}"


def _legacy_marker(key: str = _FIXTURE_KEY) -> str:
    return _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} ({_REPO})", key)


def _diff_marker(key: str = _FIXTURE_KEY) -> str:
    return _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} ({_REPO})",
        key,
    )


def _stale_marker(key: str = _FIXTURE_KEY) -> str:
    return _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_STALE_HEAD} ({_REPO})",
        key,
    )


def _comment(body: str, association: str = "MEMBER") -> dict[str, str]:
    """A GitHub issue-comment object as the REST API returns it.

    ``MEMBER`` is the affiliation the recording machine posts with; the
    affiliation filter the gate applies is ``PR_EVIDENCE_COMMENTS_JQ``.
    """
    return {"body": body, "author_association": association}


def _require_jq() -> None:
    """The comment cases serve jq-filtered bodies; a missing jq must FAIL.

    Never skip: a skipped test is not evidence.
    """
    if shutil.which("jq") is None:
        pytest.fail("the #1224 comment-channel cases need jq on PATH")


def _run_gate(
    body: str,
    tmp_path: Path,
    *,
    head_sha: str = _HEAD,
    repo: str = _REPO,
    pr: str = _PR,
    secret: str = _FIXTURE_KEY,
    comments: list[dict[str, str]] | None = None,
    comments_fail: bool = False,
    live_body: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Execute the workflow's run block offline (stubbed ``gh``) on ``body``.

    ``live_body`` switches which of the gate's TWO body sources is exercised: by
    default the stub fails and the run falls back to the ``PR_BODY`` event payload
    (``body=event-snapshot``, the truncated ~48KB path). Passing ``live_body`` makes
    the stub answer the PR fetch with those bytes, which is the path production
    normally takes (``body=rest-live``) — and the only way to reach it, since a
    snapshot body and a live body are otherwise indistinguishable to these tests.

    ``comments`` serves the PR-comment endpoint (#1224) as GitHub issue-comment
    objects (``body`` + ``author_association``); ``comments_fail`` makes that
    call exit non-zero while still printing marker text on stdout. The comment
    channel takes precedence: with ``comments``/``comments_fail`` set, every other
    ``gh`` call still fails, so the body comes from the ``PR_BODY`` event payload.
    Without any of these, every ``gh`` call fails — the original stub, unchanged.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    if comments is None and not comments_fail:
        if live_body is None:
            gh.write_text("#!/usr/bin/env bash\nexit 1\n")  # force the PR_BODY fallback
        else:
            # Answer ONLY the body fetch (`--jq .body`); the live diff fetch carries the
            # `Accept: …v3.diff` header and still fails, which is the fail-closed arm a
            # `diff=` marker must trip.
            (tmp_path / "live-body.txt").write_text(live_body)
            gh.write_text(
                "#!/usr/bin/env bash\n"
                "case \"$*\" in\n"
                "  *v3.diff*) exit 1 ;;\n"
                f'  *.body*) cat "{tmp_path}/live-body.txt" ;;\n'
                "  *) exit 1 ;;\n"
                "esac\n"
            )
    else:
        payload = tmp_path / "comments.json"
        payload.write_text(json.dumps(comments or []), encoding="utf-8")
        text = tmp_path / "comments.txt"
        text.write_text("\n".join(c["body"] for c in (comments or [])), encoding="utf-8")
        gh.write_text(
            _COMMENT_STUB.replace("__PAYLOAD__", str(payload))
            .replace("__TEXT__", str(text))
            .replace("__FAIL__", "1" if comments_fail else "")
        )
    gh.chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(_gate_run_block())

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
            "PR_BODY": body,
            "HEAD_SHA": head_sha,
            "PR_NUMBER": pr,
            "REPO_NAME": repo,
            "GATE_SECRET": secret,
        }
    )
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env)


@pytest.fixture(scope="module", autouse=True)
def _require_tools() -> None:
    missing = [t for t in ("bash", "openssl") if shutil.which(t) is None]
    if missing:
        pytest.fail(f"ai-review-gate contract tests need {missing} on PATH")


# ── The contract: both signed shapes must be accepted ──────────────────────
def test_gate_accepts_legacy_sha_only_marker(tmp_path: Path) -> None:
    """Pre-#2982 markers (no diff=) stay valid — no regression for old records."""
    proc = _run_gate(_legacy_marker(), tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout


def test_gate_accepts_diff_bearing_marker(tmp_path: Path) -> None:
    """#3076 regression: the diff= segment is inside the signed text.

    A regex that omits the optional `` diff=<64hex>`` segment rejects this
    marker before the HMAC check and reports the misleading "not signed"
    message — the exact failure that forced --admin merges on #3069/#3078.
    """
    proc = _run_gate(_diff_marker(), tmp_path)
    assert proc.returncode == 0, (
        f"the gate must accept a correctly-signed diff-bearing marker (#3076); got:\n{proc.stdout}"
    )
    assert "AI review gate passed" in proc.stdout
    assert f"diff={_DIFF}" in proc.stdout


def test_gate_passes_when_any_marker_verifies(tmp_path: Path) -> None:
    """A decoy marker with a bad signature must not shadow a valid one."""
    body = "\n".join([_diff_marker(key="attacker-key"), _diff_marker()])
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout, proc.stdout
    assert f"diff={_DIFF}" in proc.stdout


# ── The contract: each failure cause is named, and the key never leaks ─────
def test_gate_rejects_unsigned_marker_as_unsigned(tmp_path: Path) -> None:
    """An unsigned marker fails, and says it is UNSIGNED (editable body)."""
    body = f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} ({_REPO})"
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "is UNSIGNED" in proc.stdout, proc.stdout


def test_gate_rejects_wrong_repo_and_names_it(tmp_path: Path) -> None:
    """A signed marker for another repo fails, naming the OTHER repo."""
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} (someone-else/other-repo)"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "was recorded for 'someone-else/other-repo'" in proc.stdout, proc.stdout
    assert f"not '{_REPO}'" in proc.stdout, proc.stdout


def test_gate_rejects_hmac_mismatch_with_hash_prefixes(tmp_path: Path) -> None:
    """A wrong key fails as an HMAC mismatch, printing sha256 prefixes only.

    The two prefixes shown for a diff-bearing marker are the text the gate
    reconstructs from the body and the legacy (diff=-stripped) image of the
    same fields: equal prefixes with a failing HMAC means a key mismatch,
    different prefixes means producer/gate marker-shape drift. Neither leaks
    the secret.
    """
    proc = _run_gate(_diff_marker(key="some-other-key"), tmp_path)
    assert proc.returncode == 1
    assert "HMAC mismatch" in proc.stdout, proc.stdout
    prefixes = re.findall(r"sha256=([0-9a-f]{12})", proc.stdout)
    assert len(prefixes) >= 2, proc.stdout
    assert prefixes[0] != prefixes[1], (
        "the diff-bearing text and its legacy image must hash differently"
    )
    assert _FIXTURE_KEY not in proc.stdout
    assert "some-other-key" not in proc.stdout


def test_gate_reports_malformed_marker_without_calling_it_unsigned(tmp_path: Path) -> None:
    """A signed-but-wrong-shape marker is MALFORMED, never mislabelled unsigned.

    Producer/gate shape drift is the failure this whole contract exists to
    catch, so it must not be reported as "carries no signature".
    """
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff=deadbeef ({_REPO})"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "malformed marker" in proc.stdout, proc.stdout
    assert "is UNSIGNED" not in proc.stdout, proc.stdout
    assert re.search(r"sha256=[0-9a-f]{12}", proc.stdout), proc.stdout


def test_gate_calls_a_whitespace_damaged_signed_marker_malformed(tmp_path: Path) -> None:
    """Trailing whitespace on a signed marker must not be reported as UNSIGNED.

    record-review.sh posts evidence idempotently by substring, so a damaged
    line is never auto-repaired; mislabelling it "unsigned" sends the operator
    after the wrong cause (review finding on #3076).
    """
    proc = _run_gate(_diff_marker() + "  \n", tmp_path)
    assert proc.returncode == 1
    assert "is UNSIGNED" not in proc.stdout, proc.stdout
    assert "malformed marker" in proc.stdout, proc.stdout


def test_gate_reports_stale_marker(tmp_path: Path) -> None:
    """Evidence for a different head fails as STALE, and the verdict says what it READ.

    #4776: the old message was ``latest recorded <sha>`` — but that value is the last
    sha-bearing line, not the newest record, so a body carrying a marker for the head
    could still be told "your record is stale/missing". A stale verdict must instead
    (a) state that no marker is bound to the expected head, (b) list every candidate
    binding so the reader can see the whole body was considered, and (c) name the body
    bytes it judged, so the verdict stays falsifiable after the PR moves on.
    """
    other = "a" * 40
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {other} diff={_DIFF} ({_REPO})"
    )
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    # This run's comment fetch FAILED (the stub fails every call), so the verdict
    # names only the body: it must not assert an inspection that never happened.
    assert f"NO marker in this PR's body is bound to {_HEAD}" in proc.stdout, proc.stdout
    assert "comments=NOT-READ" in proc.stdout, proc.stdout
    assert other[:12] in proc.stdout, "the candidate list must name the marker that IS there"
    assert f"head expected: {_HEAD}" in proc.stdout, proc.stdout
    assert "candidate marker(s): 1" in proc.stdout, proc.stdout
    assert "body=event-snapshot" in proc.stdout, proc.stdout
    assert "latest recorded" not in proc.stdout, (
        "the label that overclaimed 'newest record' must be gone (#4776)"
    )


def test_gate_stale_verdict_admits_a_snapshot_may_miss_a_later_record(tmp_path: Path) -> None:
    """When the live body could not be fetched the run judged a SNAPSHOT.

    A snapshot is fixed at the event that queued the run, so it cannot see a record
    posted afterwards. The verdict must say so and name the action (re-run), rather
    than assert staleness about a body it never read. This is the state the contract
    harness always runs in (its ``gh`` stub fails), so it is also the state that made
    the old wording ambiguous.
    """
    other = "b" * 40
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {other} diff={_DIFF} ({_REPO})"
    )
    proc = _run_gate(body, tmp_path)
    assert "the LIVE body could not be used (the REST read failed)" in proc.stdout, proc.stdout
    assert "Re-run this check before acting on this verdict" in proc.stdout, proc.stdout


def test_gate_calls_an_empty_live_body_a_snapshot_not_a_live_read(tmp_path: Path) -> None:
    """A SUCCESSFUL read returning an empty body must not be labelled `rest-live`.

    The failure this pins: the source was flipped only when `gh` exited non-zero, so a read
    that returned an empty string (or `null`) left ``body`` on the event snapshot while
    ``body_source`` still said ``rest-live`` — the provenance line then certified bytes the
    run never fetched, which is precisely what it exists to prevent (#4776). The live body
    here is empty and the snapshot is not, so the two are distinguishable.
    """
    other = "e" * 40
    body = _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {other} ({_REPO})")
    proc = _run_gate(body, tmp_path, live_body="")
    assert proc.returncode == 1
    assert "body=event-snapshot" in proc.stdout, proc.stdout
    assert "body=rest-live" not in proc.stdout, (
        "an empty live read does not make the snapshot the live body"
    )
    assert "the REST read returned an empty body" in proc.stdout, proc.stdout


def test_gate_stale_diagnostics_survive_the_live_body_path(tmp_path: Path) -> None:
    """The WHOLE stale verdict prints when the live body was read (#4776).

    The live-body path is the one production normally takes and it had NO coverage: every
    other test here stubs ``gh`` to fail, so all of them exercise the ``event-snapshot``
    branch. The failure this pins is that branch blindness: the byte-count diagnostic is
    only reachable through this path, and a regression that truncates the verdict
    there (an early exit, or a conditional wrapping the diagnostics) would otherwise pass
    the entire suite. Asserted on the lines that FOLLOW the provenance hint.
    """
    other = "c" * 40
    body = _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {other} diff={_DIFF} ({_REPO})"
    )
    proc = _run_gate(body, tmp_path, live_body=body)
    assert proc.returncode == 1
    assert "body=rest-live" in proc.stdout, proc.stdout
    assert "the LIVE body could not be used" not in proc.stdout, proc.stdout
    assert "candidate marker(s): 1" in proc.stdout, proc.stdout
    assert f"head expected: {_HEAD}" in proc.stdout, proc.stdout
    assert "the live diff hash could not be computed" in proc.stdout, proc.stdout
    assert f"record-review.sh {_PR} {_HEAD} clean {_REPO}" in proc.stdout, proc.stdout


def test_gate_live_body_sha_digest_matches_the_bytes_it_judged(tmp_path: Path) -> None:
    """The provenance line names the bytes, so the verdict can be re-checked later."""
    other = "d" * 40
    # The em-dash is load-bearing: it makes the character count differ from the byte
    # count, so a `${#body}` (characters) mislabelled as `bytes` REDS this test.
    body = "Reviewed — no blockers.\n\n" + _marker(
        f"review recorded: reviews/{_PR}.json verdict=clean @ {other} ({_REPO})"
    )
    proc = _run_gate(body, tmp_path, live_body=body)
    digest = hashlib.sha256(body.encode()).hexdigest()[:12]
    assert f"sha256={digest}" in proc.stdout, proc.stdout
    assert f"bytes={len(body.encode())}" in proc.stdout, proc.stdout
    assert f"bytes={len(body)}" not in proc.stdout, "the count must be BYTES, not characters"


def test_gate_lists_every_candidate_not_only_the_last(tmp_path: Path) -> None:
    """The candidate list is evidence that the WHOLE body was examined (#4776).

    The failure this pins: the verdict could name one sha (the old `latest recorded`
    shape) and still satisfy every other test here, because they all carry exactly one
    candidate — so a regression back to "the last sha-bearing line" would pass. Two
    stale sha-bearing markers plus a sha-less one must all appear, in body order, with
    the sha-less count reported separately.
    """
    first, second = "1" * 40, "2" * 40
    body = "\n".join(
        [
            _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {first} ({_REPO})"),
            _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {second} ({_REPO})"),
            _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ not-a-sha ({_REPO})"),
        ]
    )
    proc = _run_gate(body, tmp_path, live_body=body)
    assert proc.returncode == 1
    assert "candidate marker(s): 2" in proc.stdout, proc.stdout
    assert f"bound to: {first[:12]} {second[:12]}" in proc.stdout, (
        "every candidate must be listed, in body order — not only the last"
    )
    assert "1 with no 40-hex sha" in proc.stdout, proc.stdout


def test_gate_reports_missing_wellformed_sha(tmp_path: Path) -> None:
    """A marker with a non-sha @ field is not mislabelled STALE with a blank value."""
    body = _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ not-a-sha (someone/other)")
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert "carries no well-formed 40-hex recorded sha" in proc.stdout, proc.stdout
    assert "is stale" not in proc.stdout, proc.stdout


def test_gate_reports_no_evidence_at_all(tmp_path: Path) -> None:
    proc = _run_gate("just a PR description, no marker", tmp_path)
    assert proc.returncode == 1
    assert "No AI review evidence found" in proc.stdout, proc.stdout


# ── #1224: the signed marker may be carried by a PR COMMENT ────────────────
# The body is a mutable field the recording party routinely rewrites, so the
# SAME signed text is also carried by an append-only PR comment. The
# gate must accept it from EITHER channel — and must preserve EVERY security
# property while doing so: the HMAC over the marker text is what makes a marker
# unforgeable, and the head/diff binding rules are unchanged.


def test_gate_accepts_head_bound_marker_from_a_comment(tmp_path: Path) -> None:
    """#1224 case 1: a comment-carried marker with a VALID HMAC, head-bound, passes.

    The body carries NO marker, so a pass here is only possible if the comment
    channel is actually read. The pass message must name the channel it used.
    The marker carries the optional ``diff=`` segment, so the comment path also
    exercises the #3076 shape.
    """
    _require_jq()
    proc = _run_gate(
        "A PR description with no evidence marker in it.",
        tmp_path,
        comments=[_comment(_diff_marker())],
    )
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout, proc.stdout
    assert "marker found in PR comment" in proc.stdout, proc.stdout


def test_gate_rejects_forged_hmac_in_a_comment(tmp_path: Path) -> None:
    """#1224 case 2 (SECURITY): a comment with a FORGED HMAC must fail.

    This must genuinely exercise the HMAC path — not fall out at the shape
    filter — so it asserts the HMAC-mismatch diagnostic fired AND that it names
    the comment channel. The channel is not a trust boundary; the signature is.
    """
    _require_jq()
    proc = _run_gate(
        "An edited PR description with no marker.",
        tmp_path,
        comments=[_comment(_diff_marker(key="attacker-key"))],
    )
    assert proc.returncode == 1, proc.stdout
    assert "HMAC mismatch" in proc.stdout, proc.stdout
    assert "found in PR comment" in proc.stdout, proc.stdout
    assert "attacker-key" not in proc.stdout, proc.stdout


def test_gate_rejects_stale_marker_in_a_comment(tmp_path: Path) -> None:
    """#1224 case 3: a STALE comment marker still fails.

    Channel membership must not weaken the binding: this marker is for another
    head and carries no ``diff=``, so neither rule (a) nor rule (b) can carry
    it. It must be reported as stale, and say WHERE it was found.
    """
    _require_jq()
    proc = _run_gate(
        "An edited PR description with no marker.",
        tmp_path,
        comments=[_comment(_stale_marker())],
    )
    assert proc.returncode == 1, proc.stdout
    # The comment channel WAS read here, so the verdict names both channels.
    assert f"NO marker in this PR's body or its admitted comments is bound to {_HEAD}" in proc.stdout, proc.stdout
    assert "found in PR comment" in proc.stdout, proc.stdout
    assert "comments=read sha256=" in proc.stdout, proc.stdout


def test_gate_classifies_an_unsigned_comment_marker_as_unsigned(tmp_path: Path) -> None:
    """#1224: the failure CLASSIFICATION works for a comment-carried marker too.

    If only the body were searched for markers-to-classify, a comment-carried
    failure would fall through to "no evidence found" and send the operator
    after the wrong cause — exactly the misleading-diagnostic trap #3076 is
    about. An unsigned comment marker must be reported as UNSIGNED, saying it
    was found in a comment.
    """
    _require_jq()
    unsigned = f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} ({_REPO})"
    proc = _run_gate(
        "An edited PR description with no marker.",
        tmp_path,
        comments=[_comment(unsigned)],
    )
    assert proc.returncode == 1, proc.stdout
    assert "is UNSIGNED" in proc.stdout, proc.stdout
    assert "found in PR comment" in proc.stdout, proc.stdout


def test_gate_body_path_unaffected_when_comment_fetch_fails(tmp_path: Path) -> None:
    """#1224 case 4: a failed comment fetch leaves the BODY path exactly as before.

    Fail closed does not mean fail the check: the body marker is still
    sufficient, the failure is warned about, and no comment text is invented.
    """
    proc = _run_gate(
        _legacy_marker(),
        tmp_path,
        comments=[_comment(_legacy_marker())],
        comments_fail=True,
    )
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout, proc.stdout
    assert "PR comment fetch via the REST API failed" in proc.stdout, proc.stdout


def test_gate_accepts_comment_marker_after_a_body_edit(tmp_path: Path) -> None:
    """#1224 case 5 (THE DEFECT): body rewritten, comment still carries the marker.

    This is the exact failure the issue is about: a legitimate edit rewrites the
    body and removes the body copy of the marker, while the append-only comment
    copy survives. The gate must pass on the comment alone.
    """
    _require_jq()
    edited_body = (
        "## Summary\n\nEdited after review to correct a claim (the marker below\n"
        "was removed by this edit when the tool last rewrote the body).\n"
    )
    proc = _run_gate(edited_body, tmp_path, comments=[_comment(_legacy_marker())])
    assert proc.returncode == 0, proc.stdout
    assert "AI review gate passed" in proc.stdout, proc.stdout
    assert "No AI review evidence found" not in proc.stdout, proc.stdout
    assert "marker found in PR comment" in proc.stdout, proc.stdout


def test_gate_fails_closed_when_failed_comment_fetch_prints_marker_text(
    tmp_path: Path,
) -> None:
    """#1224 (fail-closed): stdout of a FAILED comment fetch is never evidence.

    The stub exits non-zero AND prints a valid-looking marker line on stdout
    (the worst case; a real ``gh api`` prints its HTTP error body there). A gate
    that captured stdout on the failure path would accept unreviewed text —
    a network failure turned into a pass. The body carries no marker here, so
    the run must fail with "no evidence".
    """
    proc = _run_gate(
        "An edited PR description with no marker.",
        tmp_path,
        comments=[_comment(_legacy_marker())],
        comments_fail=True,
    )
    assert proc.returncode == 1, proc.stdout
    assert "No AI review evidence found" in proc.stdout, proc.stdout
    assert "comment-carried evidence could not be examined" in proc.stdout, proc.stdout
    # The provenance must say WHICH BYTES were judged, and that the comment side
    # was NOT among them — distinct from a successful read that found nothing.
    assert "comments=NOT-READ" in proc.stdout, proc.stdout


def test_gate_does_not_admit_a_non_affiliated_comment(tmp_path: Path) -> None:
    """#1224: comment admission is narrowed to repo-affiliated authors.

    On a public repo any user with read access can comment, so the gate applies
    ``PR_EVIDENCE_COMMENTS_JQ`` — the filter the producer side and the other
    readers must share — so a drive-by author's comment is not admitted, and a
    reader must not
    act on a comment the others ignore. The marker HMAC is valid: this fails on
    AUTHORSHIP, not on the signature.
    """
    _require_jq()
    proc = _run_gate(
        "An edited PR description with no marker.",
        tmp_path,
        comments=[_comment(_legacy_marker(), association="NONE")],
    )
    assert proc.returncode == 1, proc.stdout
    assert "No AI review evidence found" in proc.stdout, proc.stdout


@pytest.mark.parametrize(
    "label, body",
    [
        ("no evidence", "just a PR description, no marker"),
        (
            "unsigned",
            f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff={_DIFF} ({_REPO})",
        ),
        (
            "wrong repo",
            _marker(
                f"review recorded: reviews/{_PR}.json verdict=clean "
                f"@ {_HEAD} diff={_DIFF} (someone-else/other-repo)"
            ),
        ),
        (
            "malformed",
            _marker(
                f"review recorded: reviews/{_PR}.json verdict=clean @ {_HEAD} diff=deadbeef ({_REPO})"
            ),
        ),
        ("no well-formed sha", _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ not-a-sha (someone/other)")),
        (
            "stale",
            _marker(f"review recorded: reviews/{_PR}.json verdict=clean @ {'d' * 40} ({_REPO})"),
        ),
        # The `latest_repo` mismatch exit: a well-formed marker for another repo whose sha is
        # NOT this head. A different branch from "wrong repo" above (that case is head-bound),
        # and the one body-derived verdict no case here reached.
        (
            "stale marker for another repo",
            _marker(
                f"review recorded: reviews/{_PR}.json verdict=clean "
                f"@ {'d' * 40} (someone-else/other-repo)"
            ),
        ),
        ("hmac mismatch", _diff_marker(key="some-other-key")),
    ],
)
def test_every_body_derived_verdict_reports_which_bytes_it_judged(
    label: str, body: str, tmp_path: Path
) -> None:
    """Every body-derived verdict carries its provenance (#4776).

    A verdict is falsifiable only if the log says WHICH body it read: the same
    evidence reads present or absent depending on whether the live body or the
    queued snapshot was fetched. Two verdicts had this line and the rest did not, so
    a snapshot-derived "UNSIGNED" or "HMAC mismatch" was as unfalsifiable as the
    stale verdict this change set out to fix. Every branch that reads the body is a case
    here, so deleting the helper call from any one of them reds its own case.
    """
    proc = _run_gate(body, tmp_path)
    assert proc.returncode == 1
    assert re.search(
        r"body=(rest-live|event-snapshot) sha256=[0-9a-f]{12} bytes=\d+", proc.stdout
    ), (
        f"{label}: the verdict does not say which bytes it judged, so an operator cannot "
        f"tell a real negative from a snapshot that missed a later record.\n{proc.stdout}"
    )


# ── Static anti-drift guard ────────────────────────────────────────────────
def _workflow() -> dict:
    """The gate workflow, PARSED as YAML.

    PyYAML reads a bare ``on:`` key as the boolean ``True`` (YAML 1.1), so
    normalise it back to ``"on"``. Parsing — rather than grepping the text —
    is the point: see the skip-footgun test below.
    """
    doc = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    if True in doc and "on" not in doc:
        doc["on"] = doc.pop(True)
    return doc


def _gate_step() -> dict:
    workflow = _workflow()
    for step in workflow["jobs"]["ai-review-gate"]["steps"]:
        if step.get("name") == _GATE_STEP_NAME:
            return step
    raise AssertionError(f"ai-review-gate step {_GATE_STEP_NAME!r} not found")


def test_gate_step_env_and_shape_are_wired() -> None:
    """The gate's env binds the event/secret it needs, and the job cannot skip.

    The runtime tests inject env themselves, so they would still pass if the
    workflow stopped wiring GATE_SECRET to the repo secret or switched the
    head-sha source. Pin the wiring here. Also pin the skip-footgun: a job
    `if:`/path filter would report SKIPPED — i.e. Success — for the
    check with no evidence evaluated.
    """
    env = _gate_step()["env"]
    assert env["GATE_SECRET"] == "${{ secrets.AI_REVIEW_GATE_KEY }}", env
    assert env["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}", env
    assert env["PR_NUMBER"] == "${{ github.event.pull_request.number }}", env
    assert env["REPO_NAME"] == "${{ github.event.repository.full_name }}", env
    assert env["PR_BODY"] == "${{ github.event.pull_request.body }}", env
    # (#5426) The synthetic-merge-queue-batch guard reads these two. They are
    # NOT reachable by any runtime case (the harness injects them itself), so a
    # dropped or mis-sourced env line would silently disable the guard while
    # every test stayed green — pin the production wiring here.
    assert env["PR_AUTHOR"] == "${{ github.event.pull_request.user.login }}", env
    assert env["HEAD_REF"] == "${{ github.head_ref }}", env
    job = _workflow()["jobs"]["ai-review-gate"]
    assert "if" not in _gate_step() and "if" not in job, (
        "a conditional/skipped gate reports Success — never gate this job"
    )


def test_trigger_and_job_shape_are_pinned_from_parsed_yaml() -> None:
    """The skip-footgun, asserted against the PARSED document (cycle-3 review).

    The shell harness asserts these structural invariants with line-anchored
    greps, so a valid-but-unusual spelling slips past the text matcher. YAML
    explicit keys are the sharp case::

        ? if
        : false

    parse to ``{"if": False}`` with zero errors — GitHub's own workflow parser
    reads ``pair.key.value`` regardless of style — and a flow mapping on the
    trigger line hides a ``paths:`` filter the same way. A text tripwire can
    never enumerate the spellings the platform accepts; the parsed structure
    below holds for every one of them.
    """
    doc = _workflow()
    on = doc.get("on")
    assert isinstance(on, dict) and set(on) == {"pull_request_target"}, (
        "the gate must trigger on pull_request_target ONLY: under `pull_request` a "
        f"same-repo PR runs its own copy of the workflow and can self-certify "
        f"the check. Parsed trigger: {on!r}"
    )
    # `edited` is LOAD-BEARING since #5433 made this check required + a queue entry
    # condition: record-review.sh posts the marker by PATCHing the PR body, and that
    # body-edit event is the only thing that re-runs the gate afterwards. Without it,
    # a correctly recorded review leaves the gate red on its pre-record run forever.
    # Asserted on the PARSED types list, because the shell harness can only token-match.
    types = (on.get("pull_request_target") or {}).get("types")
    assert isinstance(types, list) and "edited" in types, (
        "the pull_request_target trigger must include the `edited` activity type: "
        f"recording a review edits the PR body, which is what re-runs this gate. Got: {types!r}"
    )
    trigger = on["pull_request_target"] or {}
    assert "paths" not in trigger and "paths-ignore" not in trigger, (
        f"a path-filtered check never runs, so it can never pass: {trigger!r}"
    )
    job = doc["jobs"]["ai-review-gate"]
    for banned in ("if", "needs", "continue-on-error"):
        assert banned not in job, (
            f"a {banned!r}-gated job reports Success without evaluating any "
            f"evidence — never make this job conditional or non-blocking: {job.get(banned)!r}"
        )
    assert "permissions" not in job, (
        "a job-level permissions: override supersedes the workflow grant for this "
        "job; one that drops pull-requests: read leaves the workflow-level grant "
        "looking fine while making the diff-match path dead in production"
    )
    perms = doc.get("permissions") or {}
    assert perms.get("contents") == "read" and perms.get("pull-requests") == "read", (
        f"the gate needs contents: read + pull-requests: read to read the live diff: {perms!r}"
    )


def test_candidate_regex_accepts_the_diff_segment() -> None:
    """The acceptance regex must carry the optional diff= segment (#3076).

    Belt-and-braces on top of the runtime tests: a future edit that drops
    ``diff=`` from the pattern fails here with a message that names the bug,
    instead of silently rejecting every post-#2982 marker again.
    """
    block = _gate_run_block()
    sha_ok_lines = [line for line in block.splitlines() if line.startswith("sha_ok_re=")]
    assert len(sha_ok_lines) == 1, f"expected exactly one sha_ok_re= assignment, got {sha_ok_lines}"
    assert "( diff=[0-9a-f]{64})?" in sha_ok_lines[0], (
        "the gate's marker regex must accept the optional ' diff=<64hex>' "
        "segment produced by record-review.sh since #2982 — without it every "
        "correctly-signed diff-bearing marker is rejected (#3076)"
    )


def test_gate_applies_the_producer_comment_admission_filter() -> None:
    """#1224: the gate applies the comment-admission jq the producer shares.

    ``PR_EVIDENCE_COMMENTS_JQ`` must stay byte-identical across the producer and
    every reader (record-review.sh, check-pipeline-compliance.sh,
    atomic-land.sh — agent-infra#1224): they must admit the SAME evidence, or
    one of them acts on a comment the others refuse. This pins that the tortoise
    reader carries the constant AND passes it at its own fetch (the runtime cases
    prove the fetch applies it; this pins the contract text so a rewrite is a
    visible diff).
    """
    block = _gate_run_block()
    constant = (
        '.[] | select(.author_association == "OWNER" or .author_association == "MEMBER"'
        ' or .author_association == "COLLABORATOR") | .body'
    )
    assert constant in block, (
        "the gate must narrow comment admission to repo-affiliated authors "
        "(agent-infra#1224) — dropping the filter admits a drive-by comment "
        "that every other reader of this evidence refuses"
    )
    assert re.search(r"--jq \"\$PR_EVIDENCE_COMMENTS_JQ\"", block), (
        "the constant must be APPLIED at the comment fetch, not merely defined"
    )
