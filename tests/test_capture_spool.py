"""#3963 — the durable capture spool (Python leg) + its CLI/hook wiring.

Every test names the mutation that REDs it. The acceptance evidence is
EXECUTION: a spool really written, really replayed, really discarded, with the
discard ledger read back from disk. Only the last two tests are source-level
wiring guards for the bash hooks (a call site, not behaviour) — labelled as
such.

The spool directory is always redirected to ``tmp_path``: leaving it at the
default ``~/.tortoise/capture-spool`` would make the suite read — and file —
the developer machine's real captures.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

from tortoise.capture_spool import (
    Bounds,
    PostOutcome,
    Snapshot,
    backoff_delay,
    capture_key,
    classify_failure,
    content_digest,
    flush_spool,
    read_discards,
    read_spool_meta,
    read_spool_turns,
    write_spool_entry,
)

REPO = Path(__file__).resolve().parent.parent
SESSION_START = REPO / "tortoise" / "claude-hooks" / "session-start.sh"


@pytest.fixture(autouse=True)
def _never_touch_real_breadcrumbs(tmp_path, monkeypatch):
    """No test in this file may READ or UNLINK the developer's real breadcrumb.

    `_clear_breadcrumb_for` (tortoise/capture_spool.py) resolves the path at
    CALL time from the ambient env and UNLINKS it when the recorded ``session_id``
    matches. A per-test pin is one test deep: every other success-path flush in
    this file reaches the same helper with the ambient environment and deletes
    the real ``~/.tortoise/capture-errors/<harness>.json`` that
    ``session verify`` reads to report INERT vs PROVEN. The env var redirects
    BOTH the read and the unlink, so pinning it here makes the whole file
    hermetic rather than one test.
    """
    monkeypatch.setenv(
        "TORTOISE_IMPORT_RECEIPT_DIR", str(tmp_path / "import-receipts")
    )
SESSION_END = REPO / "tortoise" / "claude-hooks" / "session-end.sh"
SESSION_TURN = REPO / "tortoise" / "claude-hooks" / "session-turn.sh"

TURNS = [
    {"role": "user", "content": "Ship the capture spool."},
    {"role": "assistant", "content": "On it."},
]


def _snapshot(session_id: str = "s1", turns: list[dict] | None = None, **kw) -> Snapshot:
    return Snapshot(
        session_id=session_id,
        turns=turns if turns is not None else list(TURNS),
        source="t.claude.jsonl",
        machine_id="m" * 64,
        harness="claude",
        **kw,
    )


class _Server:
    """A fake /v1/sessions that upserts by session_id and counts POSTs."""

    def __init__(self, outcome: PostOutcome | None = None):
        self.sessions: dict[str, dict] = {}
        self.posts = 0
        self._outcome = outcome

    def post(self, payload: dict) -> PostOutcome:
        if self._outcome is not None:
            return self._outcome
        self.posts += 1
        self.sessions[str(payload["session_id"])] = payload
        return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})


# ── (1) Interrupted mid-conversation → filed on the next opportunity ───────


def test_an_interrupted_session_is_filed_at_the_next_opportunity(tmp_path):
    """The SessionEnd hook never fired — only the spool write. `drain` is the
    next opportunity.

    MUTATIONS THAT RED THIS: make `flush_spool` ignore spooled entries (drain
    files zero). The ledger write below is a FIXTURE, so this test is about the
    DRAIN; the write side is RED'd separately by
    `test_cli_spool_writes_the_spool_and_NEVER_touches_the_network` (asserts the
    durable entry after `session spool`) and by
    `test_an_interrupted_claude_session_is_recovered_without_session_end`
    (the per-turn hook path alone, then a drain).
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-interrupted"))
    # The failed/never-attempted upload left no filed marker.
    assert read_spool_meta(root, "sess-interrupted").get("filed_key") is None

    server = _Server()
    summary = flush_spool(root, server.post, now=1000.0)

    assert summary.filed == 1
    assert server.posts == 1
    assert server.sessions["sess-interrupted"]["conversation"] == TURNS
    assert read_spool_meta(root, "sess-interrupted").get("filed_key") is not None


# ── (2) Replay twice → ONE session, ONE POST ───────────────────────────────


def test_replaying_a_spooled_session_twice_produces_one_session(tmp_path):
    """MUTATIONS THAT RED THIS: drop the `filed_key == capture_key`
    short-circuit (2 POSTs); make `capture_key` unstable across calls so the
    second flush sees a different key (2 POSTs)."""
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-replay"))
    server = _Server()

    first = flush_spool(root, server.post, now=1.0)
    second = flush_spool(root, server.post, now=2.0)

    assert first.filed == 1
    assert second.attempted == 0
    assert second.skipped == 1
    assert server.posts == 1, "replaying the SAME content must issue ONE POST"
    assert len(server.sessions) == 1, "and the server holds ONE session"


def test_capture_key_is_content_addressed():
    a = capture_key("s1", [{"role": "user", "content": "one"}])
    assert a == capture_key("s1", [{"role": "user", "content": "one"}])
    assert a != capture_key("s1", [{"role": "user", "content": "two"}])
    assert a != capture_key("s2", [{"role": "user", "content": "one"}])
    assert len(content_digest(TURNS)) == 64


# ── (3) A spool entry past its bound is discarded WITH A REASON ────────────


def test_entry_past_its_byte_bound_is_discarded_with_a_reason(tmp_path):
    """MUTATIONS THAT RED THIS: delete the per-entry bound check (the entry is
    written, `discards` empty, ledger empty); drop `record_discard` (silent
    drop)."""
    root = tmp_path
    big = [{"role": "assistant", "content": "x" * 4000} for _ in range(4)]
    bounds = Bounds(max_entries=100, max_total_bytes=10_000_000, max_entry_bytes=2000)

    result = write_spool_entry(root, _snapshot("sess-big", big), bounds)

    assert result["written"] is False
    assert len(result["discards"]) == 1
    assert result["discards"][0]["reason"] == "entry_too_large"
    assert "max_entry_bytes=2000" in result["discards"][0]["detail"]
    assert read_spool_meta(root, "sess-big") is None, "not on disk"

    # Observable: the append-only ledger names the session and the reason.
    ledger = read_discards(root)
    assert len(ledger) == 1
    assert ledger[0]["session_id"] == "sess-big"
    assert ledger[0]["reason"] == "entry_too_large"


def test_the_count_bound_evicts_the_oldest_with_a_recorded_reason(tmp_path):
    """MUTATION THAT REDS THIS: drop the count condition from `prune_spool`."""
    root = tmp_path
    bounds = Bounds(max_entries=2, max_total_bytes=10_000_000, max_entry_bytes=10_000)
    for sid in ("s1", "s2", "s3"):
        write_spool_entry(root, _snapshot(sid, [{"role": "user", "content": sid}]), bounds)

    assert read_spool_meta(root, "s1") is None, "oldest evicted"
    assert read_spool_meta(root, "s2") is not None
    assert read_spool_meta(root, "s3") is not None
    ledger = read_discards(root)
    assert ledger[-1]["session_id"] == "s1"
    assert ledger[-1]["reason"] == "spool_count_exceeded"


def test_the_byte_bound_evicts_with_its_own_recorded_reason(tmp_path):
    """MUTATION THAT REDS THIS: drop the byte condition from `prune_spool`."""
    root = tmp_path
    turns = [{"role": "user", "content": "y" * 2000}]
    bounds = Bounds(max_entries=100, max_total_bytes=6000, max_entry_bytes=10_000)
    for sid in ("b1", "b2", "b3"):
        write_spool_entry(root, _snapshot(sid, turns), bounds)

    assert read_spool_meta(root, "b1") is None, "oldest evicted under the byte bound"
    assert any(d["reason"] == "spool_total_bytes_exceeded" for d in read_discards(root))


# ── (4) Endpoint unreachable → retry with backoff, never a loss ────────────


def test_a_network_failure_defers_with_backoff_and_is_retried(tmp_path):
    """MUTATIONS THAT RED THIS: delete the entry on a transient failure (it is
    gone from the spool); mark it filed instead of deferring (the retry files
    nothing)."""
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-net"))
    down = _Server(PostOutcome(ok=False, status=None, detail="ECONNREFUSED"))

    first = flush_spool(root, down.post, now=1000.0)
    assert first.deferred == 1
    assert first.filed == 0
    assert first.discarded == []

    meta = read_spool_meta(root, "sess-net")
    assert meta is not None, "the entry survives a network failure"
    assert meta["attempts"] == 1
    assert meta["next_attempt_at_ms"] == 1000.0 + backoff_delay(1) * 1000
    assert read_spool_turns(root, "sess-net") == TURNS, "turns survive verbatim"

    # Inside the backoff window: skipped, NOT attempted (and NOT lost).
    window = _Server()
    inside = flush_spool(root, window.post, now=1000.0 + backoff_delay(1) * 1000 - 1)
    assert inside.attempted == 0
    assert inside.skipped == 1
    assert window.posts == 0

    # Next opportunity, after the window: the retry succeeds.
    retry = flush_spool(root, window.post, now=1000.0 + backoff_delay(1) * 1000)
    assert retry.filed == 1
    assert window.posts == 1


def test_backoff_grows_and_is_capped():
    assert backoff_delay(2) > backoff_delay(1)
    assert backoff_delay(3) > backoff_delay(2)
    assert backoff_delay(100) <= 6 * 60 * 60


# ── (5) Permanent 4xx → discard WITH a reason, never retried ───────────────


def test_a_permanent_4xx_discards_with_a_reason_and_is_never_retried(tmp_path):
    """MUTATIONS THAT RED THIS: classify 400 as retryable (no discard, entry
    stays); discard without `record_discard` (ledger stays empty)."""
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-400"))
    server = _Server(PostOutcome(ok=False, status=400, detail="bad payload"))

    first = flush_spool(root, server.post, now=1000.0)
    assert len(first.discarded) == 1
    assert first.discarded[0]["reason"] == "permanent_http_400"
    assert read_spool_meta(root, "sess-400") is None, "a permanent failure is not kept"
    assert len(read_discards(root)) == 1, "the discard is observable on disk"

    second = flush_spool(root, server.post, now=10**12)
    assert second.attempted == 0, "a permanent rejection must never be retried"


def _node_that_can_strip_ts() -> str:
    """The node on PATH, or a SKIP if it cannot run a TYPELESS `.ts` driver.

    Shared, because EVERY `--experimental-strip-types` driver in this file needs
    the same floor and a floor restated per call site is one that will be
    omitted: it was missing from two of the three invocations here, so on Node
    20-22.6 — a host the rest of the suite supports — the driver exits on an
    unknown flag and a test that should SKIP REDs instead (#4714 review).

    22.7, not 22.6: the driver is typeless with no `package.json`, so it needs
    ambient module-syntax DETECTION; at 22.6 the flag strips types and the
    import still fails.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — the node-free pins still ran")
    try:
        version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"node could not be probed ({exc}) — the node-free pins ran")
    match = re.match(r"v(\d+)\.(\d+)", version or "")
    if not match or (int(match.group(1)), int(match.group(2))) < (22, 7):
        pytest.skip(f"{version} cannot strip AND auto-detect TypeScript types")
    return node


def test_the_pi_and_python_classifiers_agree_on_every_status_parity(tmp_path):
    """#4714. The two capture legs classify THE SAME spool directory
    (``~/.tortoise/capture-spool``), so transient-vs-permanent must be ONE
    policy, not two independent ones that happen to agree today. The Pi leg is
    TypeScript and is the one a Python-only fix forgets — leaving 402 permanent
    there re-opens the data loss, because the next Pi drain unlinks what the
    Python drain correctly deferred.

    A runtime comparison, not a source-spelling scan: it calls the real Pi
    `classifyFailure` and compares it to the real Python `classify_failure` over
    a status matrix. That needs node, so `_assert_both_legs_carry_402` runs
    unconditionally FIRST — a guard that disappears with a missing runtime is
    not a guard.

    MUTATION THAT REDS THIS: drop 402 from EITHER classifier -> the maps
    disagree and the unrecoverable-from-one-leg case is exposed. The final 402
    pin also catches both legs drifting the SAME way, which a pure comparison
    cannot.
    """
    _assert_both_legs_carry_402()

    node = _node_that_can_strip_ts()

    ext = (REPO / "tortoise" / "pi-hooks" / "tortoise-capture.ts").as_uri()
    driver = tmp_path / "classifier-parity.mjs"
    # `null`/`NaN` are passed THROUGH, not converted to `undefined`: the legs
    # must agree on the values a real caller can produce, and the conversion
    # hid the one genuine divergence (JS `classifyFailure(null)` was
    # "permanent" because `null >= 300` is false).
    driver.write_text(
        f'import {{ classifyFailure }} from "{ext}";\n'
        "const matrix = [null, undefined, NaN, true, false, 0, 200, 301, 400, 402, 403, 408, 409, 422, 425, 429, 500, 503];\n"
        "const out = {};\n"
        "for (const s of matrix) out[String(s)] = classifyFailure(s);\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(driver)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO),
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    pi_map = json.loads(proc.stdout.strip().splitlines()[-1])

    # Faithful mapping, not a convenience one: Python has no `undefined`, so it
    # corresponds to `None` (both mean "no status"); `NaN` maps to an ACTUAL
    # nan so a non-finite status is genuinely compared. Mapping NaN onto None
    # would assert `TS(NaN) == Python(None)` and hide the live divergence it
    # used to cover (Python returned "permanent" for a nan).
    python_input = {"null": None, "undefined": None, "NaN": float("nan"),
                    "true": True, "false": False}
    for raw, verdict in pi_map.items():
        status = python_input[raw] if raw in python_input else int(raw)
        assert classify_failure(status) == verdict, (
            f"the Pi and Python classifiers disagree on status {raw}: "
            f"pi={verdict!r} python={classify_failure(status)!r}"
        )
    # The matrix must actually exercise the transient 4xx family, or the parity
    # is vacuous. This also catches both legs drifting the same way.
    assert classify_failure(402) == pi_map["402"] == "retry"
    # And the no-status rows, which is exactly where the legs diverged.
    assert pi_map["null"] == pi_map["undefined"] == pi_map["NaN"] == "retry"
    assert classify_failure(float("nan")) == "retry"
    # The BOOLEAN rows: in Python a bool IS an int, so `math.isfinite(True)` is
    # True and the value reached "permanent" (i.e. `_discard_entry`, i.e.
    # deletion) where `Number.isFinite(true)` is false and the Pi leg said
    # "retry". A real row, not a hypothetical: it is the one value in the int
    # domain the two legs classified differently, in the destructive direction.
    assert classify_failure(True) == pi_map["true"] == "retry"
    assert classify_failure(False) == pi_map["false"] == "retry"
    # An ABSOLUTE pin, not only a leg-vs-leg comparison: a same-direction drift
    # (both legs reclassifying 403's reversible policy state, #4895) would pass a
    # pure comparison. 403 is deliberately still permanent here.
    assert classify_failure(403) == pi_map["403"] == "permanent"
    assert classify_failure(422) == pi_map["422"] == "permanent"


def _assert_both_legs_carry_402() -> None:
    """The node-free half of the parity contract.

    The PYTHON half is checked BEHAVIOURALLY — the interpreter is right here, so
    there is nothing to forge and no source to misread. (An earlier version
    parsed the Python source with `ast`; any unreachable `in (...)` node forged
    it, which is exactly the silent-green failure the pin exists to prevent.)

    The TypeScript half is a SOURCE pin, because without node there is no way to
    call it: comments are stripped so a commented-out predicate cannot satisfy
    it. That half proves membership, not behaviour — the behavioural proof is
    the runtime parity test above and the Pi suite.
    """
    for code in (402, 408, 425, 429):
        assert classify_failure(code) == "retry", (
            f"Python leg does not treat {code} as transient"
        )
    # Pinned the other way too: a same-direction drift that widened the retry set
    # (e.g. deferring the reversible suspended-org 403, #4895) must red here as
    # well as in the runtime parity test.
    for code in (403, 422):
        assert classify_failure(code) == "permanent", (
            f"Python leg no longer treats {code} as permanent"
        )

    ts_raw = (REPO / "tortoise" / "pi-hooks" / "tortoise-capture.ts").read_text(
        encoding="utf-8"
    )
    # Strip TS comments before matching: without this, a COMMENTED-OUT predicate
    # (`// if (status === 402) return "retry";`) satisfies the pin while the real
    # classifier has dropped the status — a spelling guard a comment can forge is
    # worse than none. `//` (lookbehind preserves `://`) and `/* */`.
    ts_src = re.sub(r"/\*.*?\*/", "", ts_raw, flags=re.S)
    ts_src = re.sub(r"(?<!:)//[^\n]*", "", ts_src)

    ts_codes = {
        int(code)
        for group in re.findall(r'if \(([^)]*)\) return "retry";', ts_src)
        for code in re.findall(r"status === (\d+)", group)
    }
    assert ts_codes, "the Pi classifyFailure has no integer transient set"
    for code in (402, 408, 425, 429):
        assert code in ts_codes, f"Pi leg dropped {code} from its transient set"


def test_a_quota_refusal_defers_the_capture_instead_of_destroying_it(tmp_path):
    """#4714. The hosted quota gate refuses a capture whose ESTIMATED point cost
    would cross the org cap. That estimate is computed from the incoming
    capture, so the SAME capture lands once a node is freed — the refusal is
    transient by the definition this module uses.

    Classified permanent, `_flush_one` routed it to `_discard_entry`, which
    unlinks the turn log and meta: the spool's only copy of the user's session
    was deleted by the drain, and the shell hooks' own advice ("run `tortoise
    session drain` to file it") is what triggered the loss.

    MUTATION THAT REDS THIS: drop 402 from the transient set in
    `classify_failure` -> the entry is discarded, `read_spool_meta` returns
    None, and the capture is gone.
    """
    # The breadcrumb redirect is file-level (`_never_touch_real_breadcrumbs`),
    # so this test cannot read or unlink the developer's real state.
    root = tmp_path / "spool"
    write_spool_entry(root, _snapshot("sess-quota"))
    quota = _Server(PostOutcome(
        ok=False, status=402,
        detail='{"detail":"Team points limit reached: 24956 in use + 48 '
               'estimated for this capture exceeds 25000. Upgrade your plan."}',
    ))

    summary = flush_spool(root, quota.post, now=1000.0)

    # Deferred, not discarded — and the capture is still on disk.
    assert summary.deferred == 1, "a quota refusal must be retried, not dropped"
    assert summary.discarded == [], "a quota refusal must never discard"
    assert read_spool_meta(root, "sess-quota") is not None, (
        "the spool's only copy of the session was destroyed"
    )

    # And it is genuinely retryable: once the quota allows it, the SAME entry
    # files without a re-capture.
    now_ok = _Server(PostOutcome(ok=True, status=200))
    second = flush_spool(root, now_ok.post, now=10**12)
    assert second.filed == 1, "the deferred capture must file once the quota clears"


def test_a_new_turn_does_not_re_arm_the_retry_window(tmp_path):
    """#4714 review. The backoff belongs to the ENTRY, not to one snapshot.

    `write_spool_entry` used to hard-code attempts=0 / next_attempt_at_ms=0 into
    every write, so a session that kept growing re-armed its OWN retry window on
    every turn: a deferred 402 was re-POSTed at turn cadence with no backoff at
    all. That matters because the spool is shared by every harness on the box,
    so any OTHER session's drain fires it too — a retry storm whose rate is set
    by how fast the user types. The "capped cadence" the module promises held
    only for a STATIC entry, which is precisely the case a quota-bound org is
    not in.

    MUTATION THAT REDS THIS: reset attempts/next_attempt_at_ms in
    `write_spool_entry` -> the window collapses to 0 and the entry is POSTed
    again 1 ms later.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-grow", TURNS[:1]))
    refused = _Server(PostOutcome(ok=False, status=402, detail="quota"))
    flush_spool(root, refused.post, now=1000.0)

    after_refusal = read_spool_meta(root, "sess-grow")
    assert after_refusal["attempts"] == 1
    window = after_refusal["next_attempt_at_ms"]
    assert window > 1000.0, "the refusal must arm a backoff window"

    # ONE new turn arrives — the entry grows, and the window must survive it.
    write_spool_entry(root, _snapshot("sess-grow", TURNS + TURNS[:1]))
    grown = read_spool_meta(root, "sess-grow")
    assert grown["attempts"] == 1, "a new turn reset the attempt counter"
    assert grown["next_attempt_at_ms"] == window, "a new turn re-armed the backoff"

    # A drain 1 ms later must SKIP it, not re-POST.
    again = _Server(PostOutcome(ok=False, status=402, detail="quota"))
    summary = flush_spool(root, again.post, now=1000.0 + 1)
    assert summary.attempted == 0, "an entry inside its backoff window was re-POSTed"
    assert summary.skipped == 1
    assert again.posts == 0


def test_the_in_flight_409_is_retryable_not_a_lost_write(tmp_path):
    """#3713. MUTATION THAT REDS THIS: treat every 409 as permanent → the entry
    is discarded and the benign in-flight race becomes a lost capture."""
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-409"))
    inflight = _Server(PostOutcome(
        ok=False, status=409,
        detail="a capture for this session_id is already in flight — retry shortly",
    ))
    summary = flush_spool(root, inflight.post, now=1000.0)
    assert summary.deferred == 1
    assert summary.discarded == []
    assert read_spool_meta(root, "sess-409") is not None


def test_failure_classification():
    assert classify_failure(None) == "retry"
    assert classify_failure(503) == "retry"
    assert classify_failure(429) == "retry"
    # A 3xx (a redirect on a stored api_url) must never delete the capture.
    assert classify_failure(301) == "retry"
    # EVERY 409 is retryable — a recording-disabled session must not be
    # destroyed because recording was briefly off (prose matching is avoided:
    # the client ships independently of the server).
    assert classify_failure(409, "already in flight") == "retry"
    assert classify_failure(409, "Session recording is disabled for this team.") == "retry"
    assert classify_failure(409) == "retry"
    # 402 is TRANSIENT (#4714). The hosted quota gate refuses a capture whose
    # ESTIMATED cost would cross the org's cap; `est` is computed from the
    # INCOMING capture, so the identical capture succeeds once a node is freed
    # or the tier changes. Classified permanent, `_flush_one` routed it to
    # `_discard_entry`, which unlinked the spool's only copy of the session.
    assert classify_failure(
        402,
        "Team points limit reached: 24956 in use + 48 estimated "
        "for this capture exceeds 25000.",
    ) == "retry"
    # Status-only, never prose: the client ships independently of the wording.
    assert classify_failure(402) == "retry"
    assert classify_failure(422) == "permanent"
    # TOTAL over an unusable status — it must return a verdict, never raise.
    # `math.isfinite(10**400)` raises OverflowError (an int too large to convert
    # to a float) rather than returning inf, and at origin/main the same input
    # returned "retry"; an escaping error is STRICTLY worse than either verdict,
    # because `_spool_if_retryable` swallows it into "spool write failed" and the
    # capture is never spooled. The Pi leg pins the same magnitude to Infinity
    # and answers "retry", so an unguarded isfinite also broke cross-leg parity.
    assert classify_failure(10 ** 400) == "retry"
    assert classify_failure(float("nan")) == "retry"
    assert classify_failure(float("inf")) == "retry"
    assert classify_failure("500") == "retry"
    # A bool is an `int` in Python, so `math.isfinite(True)` is True and the
    # value reached "permanent" — i.e. `_discard_entry`, i.e. DELETION of the
    # capture — where the Pi leg says "retry" (`Number.isFinite(true)` is false
    # because `isFinite` requires `typeof === "number"`). `0`/`False` is the
    # same path. Unreachable from the four real call sites, but the one value in
    # the int domain the legs classified differently, in the DESTRUCTIVE
    # direction.
    assert classify_failure(True) == "retry"
    assert classify_failure(False) == "retry"
    # `0` is NOT a bool — `isinstance(0, bool)` is False — and BOTH legs read it
    # as "permanent" (`Number.isFinite(0)` is true, so the Pi leg falls through
    # too). Pinned here so the reading is deliberate rather than incidental: no
    # caller can produce a status of 0 (`PostOutcome.status` is an HTTP code or
    # None), and the two legs agree, which is what the parity test requires.
    assert classify_failure(0) == "permanent"


# ── (6) Dedup / consolidation ──────────────────────────────────────────────


def test_the_corrupt_input_helpers_match_the_pi_policy_pins():
    """The PINNED policy for a corrupt on-disk value — node-free, so it runs
    even where node is absent. These are the values the Pi leg produces; the
    runtime comparison in `test_the_corrupt_input_helpers_agree_with_the_pi_leg`
    is what checks that the Pi leg still produces them.

    The two legs share ONE spool directory, so a corrupt on-disk value must
    produce the SAME retry cadence on both — otherwise a session refused on the
    Pi leg waits 16 minutes while the Python leg would have tried it now.
    The safe reading of an unusable value is "retry now" (0 attempts, window
    0), which is what the Pi leg's `clampAttempts`/`clampWindow` do.

    MUTATION THAT REDS THIS: `int(meta.get('attempts') or 0)` without the type
    check -> a stored "5" yields attempt 5 (16 min) instead of 1; drop the
    `<= 0` arm in `_backoff_ms` -> a negative window round-trips instead of 0.
    """
    from tortoise.capture_spool import _attempts, _backoff_ms

    assert _attempts({}) == 0
    assert _attempts({"attempts": None}) == 0
    assert _attempts({"attempts": "5"}) == 0, "a numeric STRING is corrupt input"
    assert _attempts({"attempts": True}) == 0, "a bool is an int in Python"
    assert _attempts({"attempts": -3}) == 0
    assert _attempts({"attempts": 3.7}) == 3, "Math.floor, like the Pi leg"
    assert _attempts({"attempts": 10 ** 400}) == 0, (
        "the Pi leg reads the same 401-digit bytes as Infinity -> 0 attempts; a "
        "direct int() here meant 64 attempts, a SIX-HOUR wait")
    assert _attempts({"attempts": 1e308}) == 64, (
        "...but a magnitude JS still parses FINITE clamps to the bound")
    assert _attempts({"attempts": float("nan")}) == 0
    assert _attempts({"attempts": float("inf")}) == 0
    assert _attempts({"attempts": 5}) == 5

    assert _backoff_ms({}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": -1}) == 0.0, "clampWindow returns 0"
    assert _backoff_ms({"next_attempt_at_ms": 0}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": float("inf")}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": float("nan")}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": 10 ** 400}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": 31_000}) == 31_000.0
    # A numeric STRING is honoured by `float()` and ZEROED by `clampWindow`:
    # a future string window made the Python drain HOLD an entry the Pi drain
    # retried. A bool is the same class (`float(True)` is 1.0, a window of
    # 1 ms after the epoch).
    assert _backoff_ms({"next_attempt_at_ms": "1790000000000"}) == 0.0, (
        "a string window is corrupt input; the Pi leg's clampWindow zeroes it")
    assert _backoff_ms({"next_attempt_at_ms": True}) == 0.0


def test_the_corrupt_input_helpers_agree_with_the_pi_leg(tmp_path):
    """The RUNTIME half: the same corrupt values through the REAL
    `clampAttempts`/`clampWindow`, compared against the Python helpers.

    Without this, the policy pins above are a table of constants asserting that
    Python equals today's Pi behaviour while nothing checks the Pi side — so
    renaming `MAX_ATTEMPTS`, or changing `Math.floor` to `Math.round`, would
    leave a test called "agree" green while the legs diverge on shared state.
    That is the failure class this branch exists to close, so it is compared the
    same way the status classifier is (a Node driver importing the real
    module) rather than trusted.

    MUTATION THAT REDS THIS: change `MAX_ATTEMPTS` in the Pi leg -> the `1e308`
    row's attempt count diverges; drop the `value > 0` guard from `clampWindow`
    -> the negative rows diverge; drop the `typeof value === "number"` guard
    from either helper -> the `"5"` and bool rows diverge.
    """
    from tortoise.capture_spool import _attempts, _backoff_ms

    node = _node_that_can_strip_ts()

    ext = (REPO / "tortoise" / "pi-hooks" / "tortoise-capture.ts").as_uri()
    driver = tmp_path / "clamp-parity.mjs"
    # NaN/Infinity/undefined are pushed in CODE, not carried in the JSON: Python
    # emits bare `NaN`/`Infinity` which `JSON.parse` rejects, and `undefined`
    # has no JSON spelling at all. The JSON half carries only what really can
    # sit in a spool meta file.
    driver.write_text(
        f'import {{ clampAttempts, clampWindow }} from "{ext}";\n'
        "const vals = JSON.parse(process.argv[2]);\n"
        "const out = vals.map((v) => [clampAttempts(v), clampWindow(v)]);\n"
        "out.push([clampAttempts(NaN), clampWindow(NaN)]);\n"
        "out.push([clampAttempts(Infinity), clampWindow(Infinity)]);\n"
        "out.push([clampAttempts(undefined), clampWindow(undefined)]);\n"
        "console.log(JSON.stringify(out));\n",
        encoding="utf-8",
    )
    # `10**400` is the important row: Python holds it exactly, JS `JSON.parse`
    # reads the same bytes as Infinity — which is why `_attempts` goes through a
    # float, so both legs land on 0 (30 s) instead of Python's 64 (6 h).
    matrix = [None, True, False, 0, -0.0, -0.5, 3.7, 30_000, 1e308,
              10 ** 400, "5"]
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(driver), json.dumps(matrix)],
        capture_output=True, text=True, timeout=60, cwd=str(REPO),
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    pi_rows = json.loads(proc.stdout.strip().splitlines()[-1])

    expected = [
        [_attempts({"attempts": v}), _backoff_ms({"next_attempt_at_ms": v})]
        for v in matrix
    ]
    expected += [
        [_attempts({"attempts": float("nan")}),
         _backoff_ms({"next_attempt_at_ms": float("nan")})],
        [_attempts({"attempts": float("inf")}),
         _backoff_ms({"next_attempt_at_ms": float("inf")})],
        [_attempts({"attempts": None}), _backoff_ms({"next_attempt_at_ms": None})],
    ]
    assert pi_rows == expected, (
        "the Pi and Python corrupt-input helpers disagree (row order: "
        f"{[*matrix, 'NaN', 'Infinity', 'undefined']}):\n"
        f"pi={pi_rows}\npython={expected}"
    )
    # An ABSOLUTE pin as well: a same-direction drift would pass a pure
    # comparison. A magnitude JS still parses FINITE clamps to the bound; one it
    # reads as Infinity is "retry now".
    assert pi_rows[8] == expected[8] == [64, 1e308]
    assert pi_rows[9] == expected[9] == [0, 0.0]


def test_an_unchanged_snapshot_is_not_rewritten(tmp_path):
    """MUTATION THAT REDS THIS: delete the identical-snapshot short-circuit
    (`written` stays True on the second identical write)."""
    root = tmp_path
    assert write_spool_entry(root, _snapshot("sess-same"))["written"] is True
    assert write_spool_entry(root, _snapshot("sess-same"))["written"] is False


def test_a_history_rewrite_does_not_fuse_stale_turns(tmp_path):
    """MUTATION THAT REDS THIS: trust length alone instead of the stored
    prefix → the log becomes [t0, t1, t2, t4]."""
    root = tmp_path

    def t(n):
        return {"role": "user", "content": f"t{n}"}

    write_spool_entry(root, _snapshot("sess-shift", [t(0), t(1), t(2)]))
    write_spool_entry(root, _snapshot("sess-shift", [t(1), t(2), t(3), t(4)]))
    assert [x["content"] for x in read_spool_turns(root, "sess-shift")] == \
        ["t1", "t2", "t3", "t4"]


def test_a_growing_session_appends_in_order(tmp_path):
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-grow", [{"role": "user", "content": "a"}]))
    write_spool_entry(root, _snapshot("sess-grow", [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]))
    assert [x["content"] for x in read_spool_turns(root, "sess-grow")] == ["a", "b", "c"]


# ── CLI: write-before-upload + the drain replay (execution) ────────────────


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("TORTOISE_API_KEY", "tt_test")
    monkeypatch.setenv("TORTOISE_API_URL", "https://api.example.test")
    # #3615: these tests drive the TRANSMISSION paths (`session capture` /
    # `session drain`), which now require explicit consent. The local
    # `session spool` write stays ungated, so tests that only spool do not need
    # this — setting it here is harmless for them.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")


def test_cli_capture_writes_the_spool_BEFORE_the_network(tmp_path, monkeypatch):
    """The ordering claim: the capture is durable before the transport is even
    built. Proof by execution — the injected transport RAISES, and the spool
    entry must nevertheless exist.

    MUTATION THAT REDS THIS: move the spool write after the upload → the
    exception fires first and the entry is missing.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: we decided to ship it\nAssistant: agreed\n")

    class Boom(RuntimeError):
        pass

    def exploding_post(api_key, api_url):
        def handle(payload):
            raise Boom("transport exploded before any capture attempt")
        return handle

    monkeypatch.setattr(cli, "_session_post", exploding_post)
    # An exploding transport is an UNCLASSIFIED per-entry failure: it is
    # recorded (`entry_failed`), backed off, and the entry is KEPT — a bug must
    # never delete user data and must never wedge the drain.
    assert cli.main(["session", "capture", "--file", str(transcript),
                     "--session-id", "sess-order"]) == 0
    assert read_spool_turns(tmp_path / "spool", "sess-order"), \
        "the turns must be durable BEFORE the network attempt"
    assert read_discards(tmp_path / "spool")[-1]["reason"] == "entry_failed"
    meta = read_spool_meta(tmp_path / "spool", "sess-order")
    assert meta["attempts"] == 1 and meta["next_attempt_at_ms"] > 0, \
        "it is retried later, with backoff — not dropped"


def test_cli_interrupted_capture_is_filed_by_the_next_drain(tmp_path, monkeypatch):
    """End-to-end, execution: capture with an unreachable endpoint leaves the
    session spooled; `tortoise session drain` (the SessionStart replay) files
    it exactly once.

    MUTATION THAT REDS THIS: make `session capture` fail without spooling, or
    make `drain` a no-op.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: interrupted mid-conversation\nAssistant: yes\n")

    # 1. Capture while the endpoint is unreachable → deferred, not lost.
    monkeypatch.setattr(
        cli, "_session_post",
        lambda k, u: (lambda payload: PostOutcome(ok=False, status=None, detail="ECONNREFUSED")),
    )
    assert cli.main(["session", "capture", "--file", str(transcript),
                     "--session-id", "sess-int", "--harness", "claude"]) == 0
    spooled = read_spool_meta(tmp_path / "spool", "sess-int")
    assert spooled is not None and spooled.get("filed_key") is None

    # 2. The next opportunity, AFTER the backoff window has elapsed (the drain
    #    honours the backoff rather than hammering an endpoint — advance the
    #    clock instead of sleeping).
    import time as _time
    real_time = _time.time
    monkeypatch.setattr(_time, "time", lambda: real_time() + 10_000)
    posted: list[dict] = []

    def good_post(api_key, api_url):
        def handle(payload):
            posted.append(payload)
            return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})
        return handle

    monkeypatch.setattr(cli, "_session_post", good_post)
    assert cli.main(["session", "drain"]) == 0
    assert [p["session_id"] for p in posted] == ["sess-int"]
    assert posted[0]["conversation"] == [
        {"role": "user", "content": "interrupted mid-conversation"},
        {"role": "assistant", "content": "yes"},
    ]

    # 3. A second drain must NOT re-POST (idempotent replay).
    assert cli.main(["session", "drain"]) == 0
    assert len(posted) == 1


# ── Wiring guards (source-level; the bash call site, not module behaviour) ──


def _mock_cli(tmp_path: Path, log: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    mock = bindir / "tortoise"
    mock.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    mock.chmod(mock.stat().st_mode | stat.S_IEXEC)
    return bindir


def test_session_start_executes_the_drain_replay(tmp_path):
    """Execution (not grep): the SessionStart hook really invokes
    `session drain`. The call is backgrounded (best-effort), so poll the mock's
    log rather than racing it.

    MUTATION THAT REDS THIS: delete the drain block from session-start.sh — OR
    delete the shipped session-start.sh. This repo SHIPS the hooks, so a missing
    file is a FAILURE, not a skip: a skip would let the replay opportunity vanish
    behind a green suite.
    """
    assert SESSION_START.exists(), "tortoise/claude-hooks/session-start.sh must ship"
    log = tmp_path / "calls.log"
    bindir = _mock_cli(tmp_path, log)
    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    r = subprocess.run(["bash", str(SESSION_START)], input="", capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    deadline = time.time() + 10
    calls = ""
    while time.time() < deadline:
        calls = log.read_text() if log.exists() else ""
        if "session drain" in calls:
            break
        time.sleep(0.05)
    assert "session drain" in calls, f"the replay opportunity must run (calls: {calls!r})"


def test_hooks_are_version_locked_and_syntactically_valid():
    """The Claude hooks share ONE contract version (hook_install's
    `contract_version` returns None if they disagree) and parse."""
    missing = [p.name for p in (SESSION_START, SESSION_END, SESSION_TURN)
               if not p.exists()]
    assert not missing, f"these shipped hooks are missing: {missing}"
    texts = {p: p.read_text() for p in (SESSION_START, SESSION_END, SESSION_TURN)}
    versions = set()
    for path, text in texts.items():
        line = next(ln for ln in text.splitlines() if ln.startswith("# tortoise-hook-version:"))
        versions.add(line.split(":", 1)[1].strip())
        assert subprocess.run(["bash", "-n", str(path)], capture_output=True).returncode == 0
    assert len(versions) == 1, f"hooks must share one contract version: {versions}"


# ── (13) Review-hardening regressions (#3963 review P0/P1/P2) ───────────────


def test_backoff_window_is_milliseconds_shared_with_the_pi_leg(tmp_path):
    """The Pi and CLI legs share ONE spool directory, so the backoff unit must
    match. A seconds value written by one leg reads as "in backoff until the
    year 57000" to the other — the capture is never retried again.

    MUTATION THAT REDS THIS: write `now_s + delay` (seconds) into
    `next_attempt_at_ms` → the entry at `next_attempt_at_ms=5_000` (5 seconds
    after the epoch, i.e. long past) is judged still-in-backoff forever.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("leg"))
    meta = read_spool_meta(root, "leg")
    assert "next_attempt_at_ms" in meta, "the field must carry its unit"
    assert "next_attempt_at" not in meta

    # A hand-written Pi-leg-shaped backoff: 5_000 ms after the epoch.
    from tortoise.capture_spool import _meta_path
    meta["next_attempt_at_ms"] = 5_000
    _meta_path(root, "leg").write_text(json.dumps(meta), encoding="utf-8")
    server = _Server(PostOutcome(ok=True))
    assert flush_spool(root, server.post, now=4_999).attempted == 0, "still inside the window"
    assert flush_spool(root, server.post, now=5_000).attempted == 1, "the window is milliseconds"


def test_a_live_turn_written_during_the_post_is_not_marked_filed(tmp_path):
    """The P0. A `turn_end` (or a resumed session) can grow the entry while a
    drain's POST is in flight. Stamping `filed_key` from the pre-POST snapshot
    marks the NEWER turns filed under the OLD key — the next flush skips them
    and they are lost forever.

    MUTATION THAT REDS THIS: write the stale `meta` back (drop the
    compare-and-swap) → `filed_key` is set and `turns_count` reverts to 2; the
    next flush posts nothing and the third turn is gone.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("race", turns=TURNS[:1]))

    def racing_post(payload):
        # A concurrent capture appends a turn while the POST is in flight.
        write_spool_entry(root, _snapshot("race", turns=TURNS[:2]))
        return PostOutcome(ok=True)

    first = flush_spool(root, racing_post, now=1000.0)
    assert first.filed == 1
    after = read_spool_meta(root, "race")
    assert after.get("filed_key") is None, "the newer on-disk content must stay unfiled"
    assert after["turns_count"] == 2, "the newer turn must survive on disk"

    server = _Server()
    second = flush_spool(root, server.post, now=2000.0)
    assert second.attempted == 1, "the grown conversation is filed at the next opportunity"
    assert server.sessions["race"]["conversation"] == TURNS[:2]


def test_a_transient_failure_preserves_turns_written_during_the_post(tmp_path):
    """The P0's sibling: the backoff write-back must not clobber newer turns.

    MUTATION THAT REDS THIS: write the stale `meta` back on a 5xx → the entry
    reverts to its pre-POST turn count and the new turn is lost.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("race2", turns=TURNS[:1]))

    def racing_post(payload):
        write_spool_entry(root, _snapshot("race2", turns=TURNS[:2]))
        return PostOutcome(ok=False, status=503, detail="upstream is down")

    flush_spool(root, racing_post, now=1000.0)
    after = read_spool_meta(root, "race2")
    assert after["turns_count"] == 2, "the newer turn must survive a failed POST"
    assert len(read_spool_turns(root, "race2")) == 2


def test_a_server_legal_maximum_session_is_spooled_not_discarded(tmp_path):
    """The P1: 500 turns x 5000 chars of non-ASCII text is ~10 MB of JSON — the
    server accepts it. A 4 MiB per-entry cap silently discarded legal CJK
    sessions whole.

    MUTATION THAT REDS THIS: restore `SPOOL_MAX_ENTRY_BYTES = 4 MiB`.
    """
    from tortoise.capture_spool import DEFAULT_BOUNDS
    cjk = "漢" * 5000  # 3 UTF-8 bytes per char
    turns = [{"role": "assistant", "content": cjk} for _ in range(500)]
    res = write_spool_entry(tmp_path, _snapshot("big", turns=turns), DEFAULT_BOUNDS)
    assert res["written"] is True, "a legal server-max session must fit the per-entry cap"
    assert res["discards"] == []


def test_a_filesystem_write_failure_is_discarded_with_a_reason(tmp_path, monkeypatch):
    """The P2: a full disk / read-only home must not make the capture vanish
    with an empty discard list — the one silent loss path left.

    MUTATION THAT REDS THIS: return ``discards`` unchanged on the OSError path.
    """
    import tortoise.capture_spool as sp

    def boom(path, text):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sp, "_atomic_write", boom)
    res = write_spool_entry(tmp_path, _snapshot("full-disk"))
    assert res["written"] is False
    assert len(res["discards"]) == 1
    assert res["discards"][0]["reason"] == "spool_write_failed"
    assert read_discards(tmp_path)[-1]["reason"] == "spool_write_failed"


def test_a_corrupt_meta_takes_its_orphaned_turn_log_with_it(tmp_path):
    """MUTATION THAT REDS THIS: unlink only the meta → the orphaned turns.jsonl
    stays on disk, invisible to the meta scan and never counted or pruned."""
    entries = tmp_path / "entries"
    entries.mkdir(parents=True)
    (entries / "dead.meta.json").write_text("{not json", encoding="utf-8")
    (entries / "dead.turns.jsonl").write_text("{}\n", encoding="utf-8")

    from tortoise.capture_spool import list_spool_metas
    metas, discards = list_spool_metas(tmp_path)
    assert metas == []
    assert len(discards) == 1
    assert discards[0]["reason"] == "corrupt_entry"
    assert not (entries / "dead.turns.jsonl").exists(), "the orphan log is removed with its meta"


def test_a_meta_without_a_usable_session_id_is_corrupt_not_a_crash(tmp_path):
    """MUTATION THAT REDS THIS: accept any parsable JSON → the drain dies on
    `meta["session_id"]` instead of recording the corruption."""
    entries = tmp_path / "entries"
    entries.mkdir(parents=True)
    (entries / "shape.meta.json").write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    from tortoise.capture_spool import list_spool_metas
    metas, discards = list_spool_metas(tmp_path)
    assert metas == []
    assert discards[0]["reason"] == "corrupt_entry"


def test_a_spool_directory_that_cannot_be_created_is_discarded_with_a_reason(tmp_path):
    """The spool path's parent is a regular FILE, so `mkdir` raises ENOTDIR.

    MUTATION THAT REDS THIS: return from the mkdir `except` without recording a
    discard → the capture vanishes with no recorded reason.
    """
    as_file = tmp_path / "not-a-dir"
    as_file.write_text("x", encoding="utf-8")
    res = write_spool_entry(as_file, _snapshot("sess-nowrite"))
    assert res["written"] is False
    assert len(res["discards"]) == 1, "a capture that cannot be spooled is recorded"
    assert res["discards"][0]["reason"] == "spool_write_failed"


# ── (14) Cycle-2 review regressions (torn append, live-session, transport) ──


def test_a_torn_append_is_repaired_by_a_full_rewrite(tmp_path):
    """A process killed mid-append leaves a partial line. Appending to it FUSES
    the next turn into the garbage tail; the tail is skipped on read-back while
    the meta digest still covers the lost turn — a silent, unrecoverable loss.

    MUTATION THAT REDS THIS: drop `_log_has_complete_records(log_path)` (and/or
    the `turns_count` check) from the `extends` condition → the log reads back
    one turn short.
    """
    from tortoise.capture_spool import _log_path

    first = [
        {"role": "user", "content": "t0"},
        {"role": "assistant", "content": "t1"},
        {"role": "user", "content": "t2"},
    ]
    write_spool_entry(tmp_path, _snapshot("sess-torn", turns=first))

    # A kill mid-append: half a record, no trailing newline.
    with _log_path(tmp_path, "sess-torn").open("a", encoding="utf-8") as fh:
        fh.write('{"role":"assistant","content":"t3')

    grown = [
        *first,
        {"role": "assistant", "content": "t3"},
        {"role": "user", "content": "t4"},
    ]
    write_spool_entry(tmp_path, _snapshot("sess-torn", turns=grown))

    assert read_spool_turns(tmp_path, "sess-torn") == grown, (
        "the torn tail must not survive; the full conversation must be readable")

    server = _Server()
    flush_spool(tmp_path, server.post, now=1000.0)
    assert server.sessions["sess-torn"]["conversation"] == grown, (
        "the filed conversation must contain every turn")


def test_read_spool_turns_returns_every_stored_role(tmp_path):
    """The store IS the record: the meta digest covers it. Filtering a role on
    read-back would POST fewer turns than the entry marks filed.

    MUTATION THAT REDS THIS: restore the `role in ("user","assistant")` filter
    in `read_spool_turns`.
    """
    from tortoise.capture_spool import _log_path

    write_spool_entry(tmp_path, _snapshot("sess-role", turns=[{"role": "user", "content": "hi"}]))
    with _log_path(tmp_path, "sess-role").open("a", encoding="utf-8") as fh:
        fh.write('{"role": "system", "content": "policy"}\n')
    assert [t["role"] for t in read_spool_turns(tmp_path, "sess-role")] == ["user", "system"]


def test_spool_dir_never_touches_the_real_spool_under_pytest(monkeypatch):
    """A test — or a PROCESS a test spawns — must never write the developer
    machine's real captures. Under pytest the path is derived from the test id.

    MUTATION THAT REDS THIS: delete the `PYTEST_CURRENT_TEST` branch → the
    default `~/.tortoise/capture-spool` is returned.
    """
    from tortoise.capture_spool import spool_dir

    monkeypatch.delenv("TORTOISE_CAPTURE_SPOOL_DIR", raising=False)
    d = spool_dir()
    assert "tortoise-capture-spool-tests" in str(d), f"expected a test spool, got {d}"
    assert str(Path.home() / ".tortoise") not in str(d)

    # Deterministic: a subprocess of the same test shares the same spool.
    assert spool_dir() == d


def test_the_transport_defers_every_unwrapped_failure(monkeypatch):
    """urllib does not wrap `TimeoutError` (a read timeout after the headers),
    `http.client.RemoteDisconnected` (from getresponse()) or a non-JSON 2xx
    body. Each must DEFER (status=None), never crash the capture path.

    MUTATION THAT REDS THIS: remove the `except OSError` / `except ValueError`
    arms in `_session_post` → the exception escapes and the capture no longer
    defers. (The transport is patched BEFORE `_session_post` is built: the
    function imports `urlopen` at call time, so a later patch would not bind —
    and the test would silently make a real network call.)
    """
    import http.client
    import io

    from tortoise.__main__ import _session_post

    class _Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _TimeoutResp(_Resp):
        def read(self, *a):
            raise TimeoutError("timed out after the headers")

    class _BadJson(_Resp):
        def read(self, *a):
            return b"<html>not json</html>"

    def handle_with(mock_urlopen):
        monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
        return _session_post("k", "https://api.example")

    out = handle_with(lambda *a, **k: _TimeoutResp())({"session_id": "s"})
    assert out.ok is False and out.status is None, "a read timeout must defer"

    out = handle_with(
        lambda *a, **k: (_ for _ in ()).throw(http.client.RemoteDisconnected("closed"))
    )({"session_id": "s"})
    assert out.ok is False and out.status is None, "RemoteDisconnected must defer"

    out = handle_with(lambda *a, **k: _BadJson())({"session_id": "s"})
    assert out.ok is False and out.status is None, "an unparseable 2xx must defer"



def test_a_refused_post_that_committed_is_not_reported_as_uncommitted(monkeypatch):
    """#4675: the spool's terminality rule must not be "a retryable status
    means nothing committed".

    The transport bound ABANDONS its handler rather than cancelling it, so a
    504 routinely arrives AFTER the server stored the session and its turns.
    Reporting that as a failure parks an already-durable session in the spool
    and `session drain` can never reach `filed N, deferred 0`.

    MUTATION THAT REDS THIS: drop `_refused(...)` from the HTTPError arm — the
    outcome stays `ok=False` and the entry defers forever.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _session_post

    payload = {"session_id": "s-committed", "harness": "codex",
               "conversation": [{"role": "user", "content": "hi"},
                                {"role": "assistant", "content": "yo"}]}

    class _Detail(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    methods: list[str] = []

    def _open(req, timeout=None):
        methods.append(req.get_method())
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, 504, "refused", None,
                            io.BytesIO(b'{"detail":"wait budget exceeded"}'))
        sid = req.full_url.rsplit("/", 1)[-1]
        detail = {"id": sid, "extracted": 3,
                  "turn_points": [
                      {"id": f"{sid}_t{i}", "role": t["role"],
                       "content": t["content"]}
                      for i, t in enumerate(payload["conversation"])]}
        return _Detail(json.dumps(detail).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    out = _session_post("k", "https://api.example")(payload)

    assert out.ok is True, out
    assert out.status == 200, out
    assert methods == ["POST", "GET"], methods
    assert out.body == {"confirmed": "filed"}, out.body


def test_a_refused_post_whose_turns_are_absent_still_defers(monkeypatch):
    """The mirror guard: a genuinely pre-commit refusal must keep deferring.

    MUTATION THAT REDS THIS: treat a 404 from the confirming read as filed.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _session_post

    payload = {"session_id": "s-uncommitted", "harness": "codex",
               "conversation": [{"role": "user", "content": "hi"}]}

    def _open(req, timeout=None):
        raise HTTPError(req.full_url,
                        504 if req.get_method() == "POST" else 404,
                        "refused", None, io.BytesIO(b'{}'))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    out = _session_post("k", "https://api.example")(payload)
    assert out.ok is False and out.status == 504, out


def test_a_confirmed_refusal_reaches_the_drain_as_FILED(tmp_path, monkeypatch):
    """End-to-end on the spool contract: a post-commit refusal must leave the
    entry FILED, not deferred forever.

    `_flush_one` stamps `filed_key`/`filed_at` through its own compare-and-swap
    only on an `ok` outcome, so this is the assertion that the confirming read
    actually converges a drain — `filed 1, deferred 0` on a session that is
    already durable.

    MUTATION THAT REDS THIS: drop `_refused(...)` from the HTTPError arm of
    `_session_post` — the entry defers on every drain.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _session_post
    from tortoise.capture_spool import (
        Snapshot,
        flush_spool,
        read_spool_meta,
        spool_dir,
        write_spool_entry,
    )

    monkeypatch.setenv("TORTOISE_CAPTURE_SPOOL_DIR", str(tmp_path / "spool"))
    monkeypatch.setenv("HOME", str(tmp_path))
    root = spool_dir()
    turns = [{"role": "user", "content": "hi"},
             {"role": "assistant", "content": "yo"}]
    entry = write_spool_entry(root, Snapshot(
        session_id="s-drain", turns=turns, source="probe",
        machine_id="m", model=None, harness="codex"))
    assert entry.get("written") or entry.get("bytes"), entry

    class _Detail(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, 504, "refused", None,
                            io.BytesIO(b'{"detail":"wait budget exceeded"}'))
        sid = req.full_url.rsplit("/", 1)[-1]
        detail = {"id": sid, "extracted": 2,
                  "turn_points": [
                      {"id": f"{sid}_t{i}", "role": t["role"],
                       "content": t["content"]}
                      for i, t in enumerate(turns)]}
        return _Detail(json.dumps(detail).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    summary = flush_spool(root, _session_post("k", "https://api.example"))

    assert summary.filed == 1, summary
    assert summary.deferred == 0, summary
    meta = read_spool_meta(root, "s-drain")
    assert meta["filed_key"], meta
    assert meta["filed_at"], meta


def test_a_503_stays_deferred_even_when_the_session_is_durable(monkeypatch):
    """503 is `retry` to the classifier but is in the RECORDED no-receipt set,
    and the drain applies the same exclusion as `_confirm_already_captured` so
    the two paths cannot drift. The cost is a deferral, never a loss — the
    entry is kept and re-posted (see #4925 for the standing question).

    MUTATION THAT REDS THIS: drop `outcome.status == 503` from `_refused` —
    the drain files a 503 it is supposed to keep deferring.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _session_post

    payload = {"session_id": "s-503", "harness": "codex",
               "conversation": [{"role": "user", "content": "hi"}]}

    class _Detail(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, 503, "unavailable", None,
                            io.BytesIO(b'{"detail":"Service Unavailable"}'))
        sid = req.full_url.rsplit("/", 1)[-1]
        detail = {"id": sid, "extracted": 2, "turn_points": [
            {"id": f"{sid}_t{i}", "role": t["role"], "content": t["content"]}
            for i, t in enumerate(payload["conversation"])]}
        return _Detail(json.dumps(detail).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    out = _session_post("k", "https://api.example")(payload)

    assert out.ok is False and out.status == 503, out


def test_an_unextracted_confirmation_is_reported_not_silent(monkeypatch):
    """#4188/#1529: a confirmed capture whose memory points were never minted
    must not print an unqualified success. The confirming outcome carries the
    keyless-mode marker so the caller's existing disclosure fires.

    MUTATION THAT REDS THIS: return `body={"confirmed": verdict}` without
    `extraction_mode` — the information is lost before the caller sees it.
    """
    import io
    from urllib.error import HTTPError

    from tortoise.__main__ import _session_post
    from tortoise.sdk import _CAPTURE_NO_PROVIDER_MODE

    payload = {"session_id": "s-keyless", "harness": "codex",
               "conversation": [{"role": "user", "content": "hi"}]}

    class _Detail(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=None):
        if req.get_method() == "POST":
            raise HTTPError(req.full_url, 504, "refused", None,
                            io.BytesIO(b'{"detail":"wait budget exceeded"}'))
        sid = req.full_url.rsplit("/", 1)[-1]
        detail = {"id": sid, "extracted": 0,
                  "turn_points": [
                      {"id": f"{sid}_t{i}", "role": t["role"],
                       "content": t["content"]}
                      for i, t in enumerate(payload["conversation"])]}
        return _Detail(json.dumps(detail).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", _open)
    out = _session_post("k", "https://api.example")(payload)

    assert out.ok is True, out
    assert out.body["confirmed"] == "unextracted", out.body
    assert out.body["extraction_mode"] == _CAPTURE_NO_PROVIDER_MODE, out.body


def test_cli_reports_and_excludes_non_conversational_turns(tmp_path, monkeypatch, capsys):
    """`System:` lines are excluded by POLICY (the Pi leg does the same at
    capture) — and the exclusion is REPORTED. Silently posting fewer turns than
    the spool digest covers is the loss this issue removes.

    MUTATION THAT REDS THIS: drop the role filter+note → a system turn is
    posted (and counted) as if it were conversational.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text(
        "System: you are a helpful agent\nUser: hi\nAssistant: hello\n", encoding="utf-8")
    posted: list[dict] = []

    def good_post(api_key, api_url):
        def handle(payload):
            posted.append(payload)
            return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})
        return handle

    monkeypatch.setattr(cli, "_session_post", good_post)
    assert cli.main(["session", "capture", "--file", str(transcript),
                     "--session-id", "sess-sys"]) == 0

    roles = [t["role"] for t in posted[0]["conversation"]]
    assert "system" not in roles, "non-conversational roles are not filed"
    assert roles == ["user", "assistant"]
    _out, err = capsys.readouterr()
    assert "non-conversational" in err, "the exclusion must be reported, not silent"


def test_the_derived_session_id_fits_the_server_max_length(tmp_path, monkeypatch):
    """The legacy no-`--session-id` path derives `<stem>-<digest12>`. The server
    declares `session_id: ... max_length=256`; an over-long id was a 422 → a
    PERMANENT discard of the whole capture. The stem is truncated to fit.

    MUTATION THAT REDS THIS: remove the `[:240]` truncation → the id is 262
    chars.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    long_stem = "x" * 249            # 249 + len(".jsonl") = 255: the fs maximum
    transcript = tmp_path / f"{long_stem}.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    posted: list[dict] = []

    def good_post(api_key, api_url):
        def handle(payload):
            posted.append(payload)
            return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})
        return handle

    monkeypatch.setattr(cli, "_session_post", good_post)
    assert cli.main(["session", "capture", "--file", str(transcript)]) == 0
    sid = posted[0]["session_id"]
    assert len(sid) <= 256, f"session_id {len(sid)} chars exceeds the server's max_length=256"
    assert sid.startswith("x" * 240) and len(sid) == 253


def test_cli_reports_a_backoff_skip_as_deferred_not_already_filed(tmp_path, monkeypatch, capsys):
    """A capture skipped because it is inside its backoff window is REPORTED as
    deferred. Calling it "already filed" is a lie that hides a pending retry.

    MUTATION THAT REDS THIS: drop the meta inspection → prints "already filed".
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: yo\n", encoding="utf-8")
    monkeypatch.setattr(
        cli, "_session_post",
        lambda k, u: (lambda payload: PostOutcome(ok=False, status=503, detail="upstream down")),
    )
    assert cli.main(["session", "capture", "--file", str(transcript),
                     "--session-id", "sess-b"]) == 0
    capsys.readouterr()  # discard the first report

    assert cli.main(["session", "capture", "--file", str(transcript),
                     "--session-id", "sess-b"]) == 0
    out, err = capsys.readouterr()
    assert "already filed" not in out, "a pending retry is not 'already filed'"
    assert "backoff" in err, "the reason for the skip must be named"


def test_abandoned_temp_files_are_swept(tmp_path):
    """A killed process leaves `*.tmp-<pid>` scratch files that match neither the
    meta glob nor the entry byte count — they would never be pruned and could
    grow the spool past its ceiling forever.

    MUTATION THAT REDS THIS: remove the `_sweep_stale_temp_files(root)` call.
    """
    entries = tmp_path / "entries"
    entries.mkdir(parents=True, exist_ok=True)
    stale = entries / "x.meta.json.tmp-99999"
    stale.write_text("{", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(stale, (old, old))

    write_spool_entry(tmp_path, _snapshot("sweep"))
    assert not stale.exists(), "an abandoned temp file must be swept"


def test_cli_spool_writes_the_spool_and_NEVER_touches_the_network(tmp_path, monkeypatch):
    """`session spool` is the per-turn CHEAP capture: durable, no network.

    MUTATION THAT REDS THIS: make `_cmd_session_spool` file the entry (call
    flush) → the exploding transport fires.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")

    def exploding_post(api_key, api_url):
        def handle(payload):  # pragma: no cover - must never be called
            raise AssertionError("session spool must not touch the network")
        return handle

    monkeypatch.setattr(cli, "_session_post", exploding_post)
    assert cli.main(["session", "spool", "--file", str(transcript),
                     "--session-id", "sess-turn"]) == 0

    meta = read_spool_meta(tmp_path / "spool", "sess-turn")
    assert meta is not None, "the turn must be durable in the spool"
    assert meta.get("filed_key") is None, "spooling must not mark the entry filed"
    assert read_spool_turns(tmp_path / "spool", "sess-turn") == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_an_interrupted_claude_session_is_recovered_without_session_end(tmp_path, monkeypatch):
    """The Claude-leg acceptance, WITHOUT assuming `session capture` ever ran.

    An interrupted / killed session runs NO SessionEnd. What DID run is the
    per-turn `UserPromptSubmit` hook → `session spool`. This proves that path
    alone leaves a recoverable session: the next opportunity drains it.

    MUTATION THAT REDS THIS: remove the spool write from `_cmd_session_spool`
    (nothing to replay) → the drain files zero.
    """
    import tortoise.__main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text(
        "User: we decided to ship the spool\nAssistant: agreed\n", encoding="utf-8")

    # 1. The per-turn hook fires while the session is live (no network).
    monkeypatch.setattr(
        cli, "_session_post",
        lambda k, u: (lambda p: (_ for _ in ()).throw(AssertionError("no network per turn"))),
    )
    assert cli.main(["session", "spool", "--file", str(transcript),
                     "--session-id", "sess-interrupted"]) == 0

    # 2. The process dies. No SessionEnd, no capture — only the spool.
    assert read_spool_meta(tmp_path / "spool", "sess-interrupted").get("filed_key") is None

    # 3. The next opportunity (SessionStart drain) files exactly once.
    posted: list[dict] = []

    def good_post(api_key, api_url):
        def handle(payload):
            posted.append(payload)
            return PostOutcome(ok=True, status=200, body={"session_id": payload["session_id"]})
        return handle

    monkeypatch.setattr(cli, "_session_post", good_post)
    assert cli.main(["session", "drain"]) == 0
    assert [p["session_id"] for p in posted] == ["sess-interrupted"]
    assert posted[0]["conversation"] == [
        {"role": "user", "content": "we decided to ship the spool"},
        {"role": "assistant", "content": "agreed"},
    ]


def test_the_turn_hook_really_invokes_session_spool(tmp_path):
    """Execution, not grep: `session-turn.sh` (the UserPromptSubmit hook) calls
    `session spool` with the transcript and the harness session id, and writes
    NOTHING to stdout (UserPromptSubmit stdout is injected into the prompt).

    MUTATION THAT REDS THIS: delete the `session spool` call from
    session-turn.sh — OR delete the shipped session-turn.sh itself. This repo
    SHIPS the hook, so a missing file is a FAILURE, not a skip.
    """
    assert SESSION_TURN.exists(), (
        "tortoise/claude-hooks/session-turn.sh must ship — it is the per-turn "
        "capture mechanism of record")
    log = tmp_path / "calls.log"
    bindir = _mock_cli(tmp_path, log)
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(
        '{"message": {"role": "user", "content": "hi"}}\n'
        '{"message": {"role": "assistant", "content": "hello"}}\n', encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    hook_input = json.dumps({"session_id": "sess-hook", "transcript_path": str(transcript)})
    r = subprocess.run(["bash", str(SESSION_TURN)], input=hook_input, capture_output=True,
                       text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "", "UserPromptSubmit stdout is injected into the prompt — keep it empty"

    calls = log.read_text() if log.exists() else ""
    assert "session spool" in calls, f"the per-turn capture must spool (calls: {calls!r})"
    assert "--harness claude" in calls
    assert "sess-hook" in calls, "the harness session id is the idempotency key"


def test_the_turn_hook_is_registered_for_UserPromptSubmit(tmp_path):
    """The installer must register the third hook, or the cheap capture never
    runs on a fresh install.

    MUTATION THAT REDS THIS: drop the session-turn.sh HookScriptSpec.
    """
    from tortoise.hook_install import get_layout

    specs = {s.name: s for s in get_layout("claude").scripts}
    assert "session-turn.sh" in specs, "the per-turn hook must ship in the layout"
    assert specs["session-turn.sh"].event == "UserPromptSubmit"


def test_the_cas_compares_the_POSTED_turns_not_the_stale_meta(tmp_path):
    """A writer killed between its log append and its meta write leaves the meta
    digest STALE. Comparing the on-disk meta to the pre-POST meta (the old
    behaviour) stamped `filed_key` even though the appended turn had never been
    posted — and every later flush short-circuits on the filed key, so its
    filing was lost forever with no discard record.

    MUTATION THAT REDS THIS: compare
    `on_disk.get("content_digest") == meta.get("content_digest")`.
    """
    from tortoise.capture_spool import _log_path

    write_spool_entry(tmp_path, _snapshot("sess-cas", turns=TURNS[:1]))
    posted: list[list] = []

    def racing_post(payload):
        posted.append(payload["conversation"])
        # The concurrent writer appends to the LOG only, then dies.
        with _log_path(tmp_path, "sess-cas").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(TURNS[1], ensure_ascii=False) + "\n")
        return PostOutcome(ok=True)

    flush_spool(tmp_path, racing_post, now=1000.0)
    assert posted == [TURNS[:1]], "first opportunity posts what it read"
    after = read_spool_meta(tmp_path, "sess-cas")
    assert after.get("filed_key") is None, (
        "content that was never posted must not be marked filed")
    assert len(read_spool_turns(tmp_path, "sess-cas")) == 2

    # The next opportunity posts the FULL conversation, exactly once.
    server = _Server()
    flush_spool(tmp_path, server.post, now=2000.0)
    assert server.sessions["sess-cas"]["conversation"] == TURNS[:2]


def test_an_unreadable_turn_log_discards_with_a_reason_instead_of_crashing(tmp_path):
    """`session drain` documents "always exits 0"; an unreadable log (EACCES, a
    directory in its place) must become a RECORDED discard, not an exception
    escaping the hook.

    MUTATION THAT REDS THIS: drop the `except OSError` around the log read.
    """
    from tortoise.capture_spool import _log_path

    write_spool_entry(tmp_path, _snapshot("sess-dir"))
    log = _log_path(tmp_path, "sess-dir")
    log.unlink()
    log.mkdir()  # IsADirectoryError on read

    summary = flush_spool(tmp_path, _Server().post, now=1000.0)
    assert summary.attempted == 0, "a log that cannot be read is not attempted"
    assert summary.discarded[-1]["reason"] == "transcript_empty"
    assert read_discards(tmp_path)[-1]["reason"] == "transcript_empty"


# ── (14) Cycle-4 review P1/P2 fixes ────────────────────────────────────────


def test_drain_never_files_the_live_session(tmp_path):
    """`session drain` runs at SessionStart — the session that is LIVE right
    now must not be filed. `--resume` / `/clear` / a compaction reuses the
    session id, so a mid-conversation capture of the live session is already on
    the spool; filing it makes the server REPLAY it (extraction is skipped once
    a session exists), storing the session while permanently losing its
    extraction. OTHER sessions on the spool are still filed.

    MUTATION THAT REDS THIS: ignore `exclude_session_id` in `flush_spool`.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("live-resumed"))
    write_spool_entry(root, _snapshot("interrupted-other"))
    server = _Server()
    summary = flush_spool(root, server.post, now=1000.0,
                          exclude_session_id="live-resumed")
    assert sorted(server.sessions) == ["interrupted-other"], (
        "the live session must be left for its own final flush")
    assert summary.held_back == 1, "the hold-back must be counted, not silent"
    assert summary.filed == 1
    # The live entry is untouched — still unfiled, still on the spool.
    assert read_spool_meta(root, "live-resumed").get("filed_key") is None
    assert read_spool_turns(root, "live-resumed") == TURNS

    # ...and it IS filed once its own final flush runs without the exclusion.
    summary = flush_spool(root, server.post, now=2000.0)
    assert sorted(server.sessions) == ["interrupted-other", "live-resumed"]
    assert summary.filed == 1


def test_cli_drain_accepts_exclude_session_id(tmp_path, monkeypatch, capsys):
    """The hook reaches the exclusion through the CLI flag.

    MUTATION THAT REDS THIS: drop `--exclude-session-id` from the parser (or
    stop forwarding it to `_cmd_session_drain`) → the live session is filed.
    """
    from tortoise import __main__ as cli

    # #3615: the drain TRANSMITS, so it requires explicit consent.
    monkeypatch.setenv("TORTOISE_CAPTURE", "1")
    write_spool_entry(tmp_path, _snapshot("live-resumed"))
    write_spool_entry(tmp_path, _snapshot("interrupted-other"))
    server = _Server()
    monkeypatch.setattr("tortoise.capture_spool.spool_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_resolve_config_path",
                        lambda *a, **k: (None, {}, "test-key", "http://test"))
    monkeypatch.setattr(cli, "_session_post", lambda *a, **k: server.post)
    assert cli.main(["session", "drain", "--exclude-session-id", "live-resumed"]) == 0
    assert sorted(server.sessions) == ["interrupted-other"]


def test_session_start_passes_the_live_session_id_to_the_drain(tmp_path):
    """Execution, not grep: the hook reads `session_id` from the hook JSON on
    stdin and forwards it to the backgrounded drain.

    MUTATION THAT REDS THIS: drop the `(cat)` stdin read (or the
    `--exclude-session-id` argument) from session-start.sh.
    """
    if not SESSION_START.exists():
        pytest.fail("session-start.sh must ship with the repo")
    log = tmp_path / "calls.log"
    env = dict(os.environ)
    env["PATH"] = f"{_mock_cli(tmp_path, log)}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    r = subprocess.run(
        ["bash", str(SESSION_START)],
        input=json.dumps({"session_id": "live-from-stdin",
                          "transcript_path": "/tmp/t.jsonl"}),
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, r.stderr
    deadline = time.time() + 10
    calls = ""
    while time.time() < deadline:
        calls = log.read_text() if log.exists() else ""
        if "session drain" in calls:
            break
        time.sleep(0.05)
    assert "session drain --exclude-session-id live-from-stdin" in calls, (
        f"the drain must be told which session is LIVE (calls: {calls!r})")


def test_spool_works_without_any_credentials(tmp_path, monkeypatch):
    """The per-turn cheap capture is LOCAL: it must work on an install with no
    hosted credentials, or the mechanism of record is a no-op for every
    local-only user (the Pi leg needs no key either).

    MUTATION THAT REDS THIS: move the `session_cmd == "spool"` dispatch back
    below the `api_key is None` gate.
    """
    from tortoise import __main__ as cli

    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "_resolve_config_path",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("spool must not resolve credentials")))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    assert cli.main(["session", "spool", "--file", str(transcript)]) == 0
    entries = list((tmp_path / "spool" / "entries").glob("*.meta.json"))
    assert len(entries) == 1, "the per-turn capture really wrote an entry"


def test_per_turn_snapshots_accumulate_in_one_entry(tmp_path, monkeypatch):
    """A growing transcript snapshotted on every turn must address ONE spool
    entry, not one per snapshot (which files as N sessions = N extractions).

    MUTATION THAT REDS THIS: derive the fallback id from the CONTENT
    (`content_digest(turns)`) instead of the transcript path.
    """
    from tortoise import __main__ as cli
    from tortoise.capture_spool import list_spool_metas, read_spool_turns

    root = tmp_path / "spool"
    transcript = tmp_path / "abc-123.claude.jsonl"
    _isolate(monkeypatch, tmp_path)

    def spool(text: str) -> None:
        transcript.write_text(text, encoding="utf-8")
        assert cli.main(["session", "spool", "--file", str(transcript)]) == 0

    short = "User: hi\nAssistant: yo\n"
    spool(short)
    metas, _ = list_spool_metas(root)
    assert len(metas) == 1
    sid = metas[0]["session_id"]
    assert sid.startswith("abc-123.claude-"), (
        "the derived id is anchored to the TRANSCRIPT, so it is stable")

    spool(short + "User: and one more turn\nAssistant: ok\n")
    metas2, _ = list_spool_metas(root)
    assert [m["session_id"] for m in metas2] == [sid], (
        f"two snapshots of one conversation must share ONE entry: {metas2}")
    assert read_spool_turns(root, sid) == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
        {"role": "user", "content": "and one more turn"},
        {"role": "assistant", "content": "ok"},
    ], "and the entry must hold the LATEST, longer snapshot"


def test_a_discard_records_its_reason_before_removing_the_files(tmp_path):
    """The ledger line is the contract. `record_discard` must be called BEFORE
    the entry files are unlinked, so a kill between the two can never lose an
    unfiled capture silently.

    MUTATION THAT REDS THIS: swap the two statements in `_discard_entry`
    (unlink first) → the observed sequence has no record at unlink time.
    """
    from tortoise import capture_spool as sp

    order: list[str] = []
    real_record, real_remove = sp.record_discard, sp._remove_entry_files
    sp.record_discard = lambda root, rec: (order.append("record"),
                                           real_record(root, rec))[1]
    sp._remove_entry_files = lambda root, sid: (order.append("remove"),
                                                real_remove(root, sid))[1]
    try:
        write_spool_entry(tmp_path, _snapshot("sess-oversize"))
        meta = read_spool_meta(tmp_path, "sess-oversize")
        sp._discard_entry(tmp_path, meta, "oversize", "bounded by bytes")
    finally:
        sp.record_discard, sp._remove_entry_files = real_record, real_remove
    assert order == ["record", "remove"], (
        f"the reason must be durable BEFORE the data is removed: {order}")


def test_a_malformed_http_response_defers_instead_of_crashing_the_drain(
        tmp_path, monkeypatch):
    """`http.client.HTTPException` (BadStatusLine / IncompleteRead / LineTooLong
    / InvalidURL) derives from Exception, NOT OSError: a proxy or LB with a
    malformed status line must DEFER the capture, not abort the drain loop and
    strand every entry sorted after it.

    MUTATION THAT REDS THIS: drop the `except HTTPException` arm.
    """
    from http.client import BadStatusLine

    from tortoise import __main__ as cli

    def boom(*a, **k):
        raise BadStatusLine("garbage")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    outcome = cli._session_post("k", "http://x")("payload")
    assert outcome.ok is False and outcome.status is None, (
        "a malformed response is a transient/unknown failure → defer")
    assert "malformed response" in outcome.detail


def test_pi_leg_flush_survives_an_unreadable_turn_log():
    """The Pi leg's ledger must record, not throw: an unreadable turn log (a
    DIRECTORY in the log's place) previously escaped `readFileSync` and aborted
    `flushSpool`, permanently wedging every later session's filing.

    MUTATION THAT REDS THIS: restore the unguarded `readFileSync` in
    `readSpoolTurns` (or `existsSync`-only guard).
    """
    script = r'''
import { mkdirSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { entryKey, flushSpool, readDiscards, writeSpoolEntry } from "./tortoise/pi-hooks/tortoise-capture.ts";
const dir = mkdtempSync(join(tmpdir(), "spool-red-"));
const snap = (sessionId, content) => ({
  sessionId,
  source: "t.jsonl",
  machineId: "m",
  turns: [{ role: "user", content }],
});
writeSpoolEntry(dir, snap("aaa-broken", "first"));
// Replace the turn log with a DIRECTORY: readFileSync throws EISDIR.
const brokenLog = join(dir, "entries", `${entryKey("aaa-broken")}.turns.jsonl`);
rmSync(brokenLog);
mkdirSync(brokenLog);
writeSpoolEntry(dir, snap("zzz-good", "second"));
const posted = [];
const doFetch = (_url, init) => {
  posted.push(JSON.parse(init.body).session_id);
  return Promise.resolve({ ok: true, status: 200, json: async () => ({}) });
};
await flushSpool({ apiUrl: "http://x", apiKey: "k" }, { dir, fetchImpl: doFetch });
console.log(JSON.stringify({ posted, discards: readDiscards(dir).map(d => d.reason) }));
'''
    node = _node_that_can_strip_ts()
    proc = subprocess.run([node, "--experimental-strip-types", "--input-type=module",
                           "-e", script], cwd=str(REPO), capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, f"flushSpool must not throw: {proc.stderr}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert "zzz-good" in out["posted"], (
        f"a corrupt entry must not wedge later sessions: {out}")
    assert "transcript_empty" in out["discards"], (
        f"the unreadable entry must be recorded, not dropped: {out}")


# ── (15) Cycle-5 review fixes (P2/P3) ──────────────────────────────────────


def test_an_unlistable_spool_is_recorded_not_reported_as_empty(tmp_path):
    """A mode-000 `entries/` directory is `is_dir() is True` and `Path.glob`
    SWALLOWS the EACCES its scandir hits, so the listing looked empty: the drain
    reported "nothing to file" forever for a spool it could not read, with no
    ledger line. An unreadable spool must be RECORDED.

    MUTATION THAT REDS THIS: restore `if not d.is_dir(): return ...` +
    `d.glob(...)` (drop the `os.scandir` guard).
    """
    write_spool_entry(tmp_path, _snapshot("sess-hidden"))
    entries = tmp_path / "entries"
    entries.chmod(0o000)
    try:
        summary = flush_spool(tmp_path, _Server().post, now=1000.0)
    finally:
        entries.chmod(0o700)
    assert [d["reason"] for d in summary.discarded] == ["spool_unreadable"], (
        "a spool we cannot read is not an empty spool")
    assert read_discards(tmp_path)[-1]["reason"] == "spool_unreadable", (
        "and the reason is durable in the ledger, not just a console line")


def test_an_unreadable_transcript_is_recorded_and_spool_still_exits_0(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """The per-turn capture is the mechanism of record: a non-UTF-8 (or
    unreadable) transcript must not raise a traceback out of `session spool`,
    and the failure must be recorded — not merely printed.

    MUTATIONS THAT RED THIS: drop the `(OSError, UnicodeDecodeError)` guard →
    the traceback escapes (rc != 0); `return 1` instead of 0 → the contract
    "always exits 0" is broken.
    """
    from tortoise import __main__ as cli

    _isolate(monkeypatch, tmp_path)
    transcript = tmp_path / "bad.jsonl"
    transcript.write_bytes(b"User: hi\n\xff\xfe not utf-8\n")
    assert cli.main(["session", "spool", "--file", str(transcript)]) == 0
    err = capsys.readouterr().err
    assert "unreadable" in err, "the failure must be reported on stderr"
    assert read_discards(tmp_path / "spool")[-1]["reason"] == "transcript_unreadable", (
        "and recorded in the ledger")


def test_session_spool_exits_zero_when_the_entry_cannot_be_written(tmp_path,
                                                                  monkeypatch):
    """A PERMANENT failure (the entry cannot be written / is discarded WITH a
    reason) is reported and recorded but STILL exits 0: this runs on every user
    prompt, and a non-zero exit would let a caller treat a completed capture as a
    failure.

    MUTATION THAT REDS THIS: `return 1` on the not-written branch.
    """
    from tortoise import __main__ as cli
    from tortoise import capture_spool as sp

    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        sp, "write_spool_entry",
        lambda root, snapshot, bounds=None: {
            "written": False, "bytes": 0,
            "discards": [{"reason": "entry_too_large", "detail": "bound"}],
        })
    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    assert cli.main(["session", "spool", "--file", str(transcript)]) == 0, (
        "a capture that could not be written is not a non-zero exit")


def test_session_drain_exits_zero_with_no_config_and_with_a_corrupt_config(
        tmp_path, monkeypatch):
    """`session drain` is backgrounded from SessionStart, so it must exit 0 when
    there is nothing it can do — no config, or a CORRUPT one.

    MUTATION THAT REDS THIS: drop the `_cmd_session_drain_best_effort` hoist so
    the drain falls through to the `api_key is None` gate (exit 1) / the
    `_ConfigError` handler (exit 1).
    """
    from tortoise import __main__ as cli

    monkeypatch.setattr(cli, "_resolve_config_path",
                        lambda *a, **k: (None, {}, None, None))
    assert cli.main(["session", "drain"]) == 0, "no config is not an error"

    def corrupt(*a, **k):
        raise cli._ConfigError(str(tmp_path / "credentials.json"))

    monkeypatch.setattr(cli, "_resolve_config_path", corrupt)
    assert cli.main(["session", "drain"]) == 0, "a corrupt config is not an error"


def test_the_entry_too_large_branch_records_before_removing(tmp_path):
    """`entry_too_large` is the ONE branch that deletes a PREVIOUSLY SPOOLED
    entry — so it is the branch where the record-before-remove order matters
    most. It must go through `_discard_entry`, not unlink-then-record.

    MUTATION THAT REDS THIS: restore the inline
    `_remove_entry_files(...)` + `record_discard(...)` pair.
    """
    from tortoise import capture_spool as sp

    write_spool_entry(tmp_path, _snapshot("sess-big"))
    order: list[str] = []
    real_record, real_remove = sp.record_discard, sp._remove_entry_files
    sp.record_discard = lambda root, rec: (order.append("record"),
                                           real_record(root, rec))[1]
    sp._remove_entry_files = lambda root, sid: (order.append("remove"),
                                                real_remove(root, sid))[1]
    try:
        result = sp.write_spool_entry(
            tmp_path,
            _snapshot("sess-big", turns=[*TURNS, {"role": "user", "content": "more"}]),
            bounds=sp.Bounds(max_entry_bytes=1))
    finally:
        sp.record_discard, sp._remove_entry_files = real_record, real_remove
    assert order == ["record", "remove"], (
        f"the reason must be durable BEFORE the previously spooled entry is "
        f"removed: {order}")
    assert result["discards"][-1]["reason"] == "entry_too_large"


def test_the_turn_hook_exposes_stderr_instead_of_swallowing_it(tmp_path):
    """Execution, not grep: the per-turn hook keeps stdout EMPTY (it is injected
    into the user's prompt) but must NOT swallow stderr — a silently failing
    capture (a wrong interpreter, an unreadable transcript) IS the "losing
    sessions silently" failure #3963 removes.

    MUTATION THAT REDS THIS: restore `>/dev/null 2>&1` on the spool call.
    """
    assert SESSION_TURN.exists(), "the per-turn hook must ship"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    failing = bindir / "tortoise"
    failing.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'capture exploded' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    failing.chmod(failing.stat().st_mode | stat.S_IEXEC)
    transcript = tmp_path / "s.jsonl"
    transcript.write_text('{"message": {"role": "user", "content": "hi"}}\n',
                          encoding="utf-8")

    env = dict(os.environ)
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    r = subprocess.run(
        ["bash", str(SESSION_TURN)],
        input=json.dumps({"session_id": "sess-x", "transcript_path": str(transcript)}),
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, "the hook must never block a turn"
    assert r.stdout == "", "stdout is injected into the user's prompt — keep it empty"
    assert "capture exploded" in r.stderr, (
        "a failing capture must be VISIBLE, not swallowed")


def test_an_unreadable_spool_ROOT_is_recorded_and_drain_exits_0(tmp_path,
                                                               monkeypatch):
    """A mode-000 spool ROOT is the nastier half of the unreadable-spool class:
    `Path.exists()` itself raises `PermissionError` when a parent lacks traverse
    permission, so the pre-check escaped `flush_spool` and `session drain` exited
    1 with a traceback — while the Pi leg's `existsSync` returned false and
    reported an EMPTY spool with no ledger line.

    MUTATION THAT REDS THIS: restore the `if not d.exists(): return ...`
    pre-check (PermissionError escapes) — or, on the Pi leg, the
    `if (!existsSync(root)) return ...` pre-check (silent empty).
    """
    from tortoise import __main__ as cli

    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "spool"
    (root / "entries").mkdir(parents=True)
    write_spool_entry(root, _snapshot("sess-behind-root"))
    root.chmod(0o000)
    try:
        summary = flush_spool(root, _Server().post, now=1000.0)
        assert [d["reason"] for d in summary.discarded] == ["spool_unreadable"], (
            "an unreadable ROOT is not an empty spool")
        monkeypatch.setattr(cli, "_resolve_config_path",
                            lambda *a, **k: (None, {}, "k", "http://x"))
        assert cli.main(["session", "drain"]) == 0, (
            "the backgrounded drain must exit 0 even then")
    finally:
        root.chmod(0o700)


def test_session_drain_never_crashes_on_an_unexpected_failure(monkeypatch):
    """`session drain` is backgrounded from the SessionStart hook, so its
    contract is "always exit 0" for ANY failure — including a bug inside the
    drain itself that no foreseeable guard covers.

    MUTATION THAT REDS THIS: drop the `except Exception` belt around
    `_cmd_session_drain` in `_cmd_session_drain_best_effort`.
    """
    from tortoise import __main__ as cli

    monkeypatch.setattr(cli, "_resolve_config_path",
                        lambda *a, **k: (None, {}, "k", "http://x"))

    def boom(*a, **k):
        raise RuntimeError("a bug in a new transport")

    monkeypatch.setattr(cli, "_cmd_session_drain", boom)
    assert cli.main(["session", "drain"]) == 0, "a crashed drain is still exit 0"


# ── (16) Cycle-7 review fixes (P2) ─────────────────────────────────────────


def test_spool_exits_zero_when_the_transcript_parent_is_unreadable(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """`Path.exists()` RAISES `PermissionError` when a PARENT directory lacks
    traverse permission — the same class as the spool-ROOT bug. The old
    `if not transcript_path.exists(): ...` pre-check therefore escaped as a
    traceback (exit 1, no record) instead of being classified, contradicting the
    per-turn capture's "always exits 0" contract.

    MUTATION THAT REDS THIS: restore the `transcript_path.exists()` pre-check.
    """
    from tortoise import __main__ as cli

    _isolate(monkeypatch, tmp_path)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "t.jsonl").write_text("User: hi\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        assert cli.main(["session", "spool", "--file",
                         str(locked / "t.jsonl")]) == 0, (
            "an unreadable parent must not break the exit-0 contract")
    finally:
        locked.chmod(0o700)
    assert "unreadable" in capsys.readouterr().err
    assert read_discards(tmp_path / "spool")[-1]["reason"] == "transcript_unreadable"


def test_the_spool_command_reports_not_found_for_a_missing_transcript(tmp_path,
                                                                    monkeypatch,
                                                                    capsys):
    """The ENOENT case still gets its own message (the classification moved into
    the read, but "not found" must not be reported as "unreadable").

    MUTATION THAT REDS THIS: drop the `except FileNotFoundError` arm.
    """
    from tortoise import __main__ as cli

    _isolate(monkeypatch, tmp_path)
    assert cli.main(["session", "spool", "--file",
                     str(tmp_path / "nope.jsonl")]) == 0
    assert "not found" in capsys.readouterr().err


def test_session_drain_exits_zero_when_the_config_resolver_itself_raises(
        monkeypatch):
    """The config resolver can RAISE something that is not `_ConfigError`:
    `_resolve_config_path()` calls `Path.is_file()`, which raises
    `PermissionError` when `~/.tortoise` is unreadable. The resolver therefore
    has to sit INSIDE the blanket try, or the backgrounded drain exits 1 with a
    traceback.

    MUTATION THAT REDS THIS: move `_resolve_config_path()` above the
    `except Exception` belt (catch only `_ConfigError`).
    """
    from tortoise import __main__ as cli

    def unreadable(*a, **k):
        raise PermissionError(13, "Permission denied", "/home/u/.tortoise")

    monkeypatch.setattr(cli, "_resolve_config_path", unreadable)
    assert cli.main(["session", "drain"]) == 0, (
        "the backgrounded drain must exit 0 even when the resolver raises")


# ── (17) Cycle-8 review fixes (P1/P2/P3) ───────────────────────────────────


def test_a_non_utf8_turn_log_is_recorded_not_a_crash(tmp_path, monkeypatch, capsys):
    """A torn multi-byte character in the turn log is EXACTLY what a killed
    partial append leaves (turns are written raw UTF-8 for CJK/emoji).
    `UnicodeDecodeError` is a `ValueError`, NOT an `OSError` — it escaped the
    read guard, so the per-turn `session spool` crashed with a traceback and the
    drain wedged with NO ledger line, and the OTHER session's filing was lost.

    MUTATION THAT REDS THIS: drop `UnicodeDecodeError` from the
    `read_spool_turns` except clause.
    """
    from tortoise import __main__ as cli
    from tortoise.capture_spool import _log_path

    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "spool"
    # A healthy entry that must still be filed, and a corrupt one sorted first.
    write_spool_entry(root, _snapshot("aaa-corrupt"))
    write_spool_entry(root, _snapshot("zzz-healthy"))
    _log_path(root, "aaa-corrupt").write_bytes(
        b'{"role":"user","content":"\xe4\xb8')  # a torn 3-byte codepoint

    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    assert cli.main(["session", "spool", "--file", str(transcript),
                     "--session-id", "sess-turn"]) == 0, (
        "a corrupt log must not crash the per-turn capture")
    assert "Traceback" not in capsys.readouterr().err

    server = _Server()
    monkeypatch.setattr(cli, "_session_post", lambda *a, **k: server.post)
    assert cli.main(["session", "drain"]) == 0, "the drain must not wedge"
    assert "zzz-healthy" in server.sessions, (
        "a corrupt entry must not stop the sessions sorted after it from filing")
    reasons = [d["reason"] for d in read_discards(root)]
    assert "transcript_empty" in reasons, "the corrupt log is RECORDED, not silent"


def test_a_corrupt_typed_meta_does_not_crash_or_wedge(tmp_path):
    """A hand-edited/corrupt meta field must not raise out of the ordering or
    the backoff read: a non-string `updated_at` raised `TypeError` out of
    `session spool`, and a non-numeric `next_attempt_at_ms` raised `ValueError`
    out of `flush_spool` (wedging the drain).

    MUTATIONS THAT RED THIS: keep the raw `_age_key` tuple; drop the
    `(TypeError, ValueError)` guard in `_backoff_ms`.
    """
    from tortoise.capture_spool import _age_key, _backoff_ms, _meta_path

    write_spool_entry(tmp_path, _snapshot("sess-typed"))
    meta_path = _meta_path(tmp_path, "sess-typed")
    meta = read_spool_meta(tmp_path, "sess-typed")
    meta["updated_at"] = 5            # int, not a string
    meta["next_attempt_at_ms"] = "soon"  # not a number
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    assert _age_key(meta) == ("5", "5", "sess-typed") or _age_key(meta)[0] == "5"
    assert _backoff_ms(meta) == 0.0, "an unusable backoff means 'retry now'"

    server = _Server()
    summary = flush_spool(tmp_path, server.post, now=1000.0)
    assert summary.filed == 1, "the entry is still filed, not wedged"


def test_a_failure_racing_a_successful_filing_does_not_re_arm_the_window(tmp_path):
    """#4714 cycle-7 review. `_flush_one`'s failure write-back re-reads the meta
    but never checked whether the content it failed to POST had since been FILED
    by a concurrent flush of the same `capture_key`. Re-arming the backoff there
    attaches a window to content that was never refused — and because
    `write_spool_entry` now CARRIES the window, the next turn's NEW content
    inherits it and waits up to RETRY_MAX (6 h) with no attempt behind it. Old
    code wrote (0, 0) here, so its reset cleared the resurrected window; this
    propagation is the one state the carry-forward makes reachable that the
    previous behaviour could not.

    MUTATION THAT REDS THIS: drop the `filed_key` guard in the failure
    write-back -> the resurrected window survives and the next turn inherits it.
    """
    from tortoise.capture_spool import _meta_path

    root = tmp_path
    write_spool_entry(root, _snapshot("sess-race"))

    def post_filed_underneath(payload):
        # Simulate a CONCURRENT flush completing a 2xx for this capture_key
        # while our own POST is still in flight.
        on_disk = read_spool_meta(root, "sess-race")
        on_disk["filed_key"] = on_disk["capture_key"]
        on_disk["attempts"] = 0
        on_disk["next_attempt_at_ms"] = 0
        _meta_path(root, "sess-race").write_text(
            json.dumps(on_disk), encoding="utf-8"
        )
        return PostOutcome(ok=False, status=402, detail="quota")

    summary = flush_spool(root, post_filed_underneath, now=1000.0)
    assert summary.attempted == 1
    assert summary.skipped == 1, "a failure for superseded content is not a retry"
    assert summary.deferred == 0

    after = read_spool_meta(root, "sess-race")
    assert after["attempts"] == 0, "the failure re-armed a window on filed content"
    assert after["next_attempt_at_ms"] == 0, "a resurrected window was attached"

    # And the NEXT turn must not inherit a window it never earned.
    write_spool_entry(root, _snapshot("sess-race", TURNS + TURNS[:1]))
    grown = read_spool_meta(root, "sess-race")
    assert grown["next_attempt_at_ms"] == 0, (
        "new content inherited a backoff window it never earned"
    )


def test_a_deferred_only_drain_is_counted_not_silent(tmp_path):
    """#4714 review. Moving 402 from "discard" to "defer" removed the drain's
    only signal for a quota-blocked spool: a window-held entry is neither
    attempted nor discarded, so the summary line's trigger
    (`attempted or discarded or probe_refusals`) was false and the drain printed
    NOTHING while captures sat unfiled — the old behaviour at least named a
    discard. `held_by_backoff` is that missing signal.

    MUTATION THAT REDS THIS: drop the `held_by_backoff` increment.
    """
    root = tmp_path
    write_spool_entry(root, _snapshot("sess-wait"))
    flush_spool(
        root,
        _Server(PostOutcome(ok=False, status=402, detail="quota")).post,
        now=1000.0,
    )

    again = _Server()
    summary = flush_spool(root, again.post, now=1000.0 + 1)
    assert summary.attempted == 0
    assert summary.skipped == 1
    assert summary.held_by_backoff == 1, (
        "a window-held entry is invisible to the drain and to the operator"
    )
    assert again.posts == 0


def test_an_absurd_finite_window_is_treated_as_corrupt_not_honoured(tmp_path):
    """#4714 review. Non-finite was closed, but ANY finite value passed: a window
    ~258 years out is equally not a window, and because the write path now
    CARRIES it, it was re-written on every turn — stranding the entry forever
    while every surface reported nothing wrong. A legitimately written window is
    at most `written_at + RETRY_MAX`.

    MUTATIONS THAT RED THIS: honour a window beyond now + RETRY_MAX; drop the
    `_carried_window` bound.
    """
    from tortoise.capture_spool import _carried_window, _meta_path

    assert _carried_window({"next_attempt_at_ms": 9.9e15}) == 0.0
    assert _carried_window({"next_attempt_at_ms": 31_000.0}) == 31_000.0

    write_spool_entry(tmp_path, _snapshot("sess-far"))
    meta_path = _meta_path(tmp_path, "sess-far")
    meta = read_spool_meta(tmp_path, "sess-far")
    meta["next_attempt_at_ms"] = 9.9e15
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    server = _Server()
    summary = flush_spool(tmp_path, server.post, now=1000.0)
    assert summary.attempted == 1, "an absurd finite window stranded the entry"
    assert summary.filed == 1


def test_a_non_finite_backoff_cannot_make_an_entry_unfileable(tmp_path):
    """#4714 review. The backoff is CARRIED across turns now, so a stored
    `inf` would be preserved on every write: `inf > now_ms` is true forever, the
    entry would never be POSTed, and every surface would still report "will
    retry" — a promise that can never be kept. `float(10**400)` raises
    `OverflowError` rather than returning `inf`, and that helper runs on the
    WRITE path, so an uncaught one would break `session spool`'s exit 0.

    MUTATIONS THAT RED THIS: return the raw float from `_backoff_ms`; drop
    `OverflowError` from either guard; use `int()` unguarded where the backoff
    is armed.
    """
    from tortoise.capture_spool import _attempts, _backoff_ms, _meta_path

    assert _backoff_ms({"next_attempt_at_ms": float("inf")}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": float("nan")}) == 0.0
    assert _backoff_ms({"next_attempt_at_ms": 10**400}) == 0.0
    assert _attempts({"attempts": float("inf")}) == 0
    assert _attempts({"attempts": 10**400}) <= 64, (
        "an absurd attempt count must saturate, not exponentiate"
    )

    write_spool_entry(tmp_path, _snapshot("sess-inf"))
    meta_path = _meta_path(tmp_path, "sess-inf")
    meta = read_spool_meta(tmp_path, "sess-inf")
    meta["next_attempt_at_ms"] = 1e400  # inf
    meta["attempts"] = 10**400
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    # The WRITE path must not raise (session spool always exits 0)...
    write_spool_entry(tmp_path, _snapshot("sess-inf", TURNS + TURNS[:1]))
    # ...and the entry must still be POSTed, not skipped forever.
    server = _Server()
    summary = flush_spool(tmp_path, server.post, now=1000.0)
    assert summary.attempted == 1, "a non-finite backoff made the entry un-fileable"
    assert summary.filed == 1


def test_the_turn_hook_records_an_unreadable_transcript(tmp_path):
    """Execution, not grep: the per-turn hook must not skip an unreadable
    transcript SILENTLY. The `[ -f ]` pre-check is false for EACCES/ENOTDIR, so
    the turn (and every later turn) was dropped with no spool write and no
    ledger line — the exact failure #3963 removes.

    MUTATION THAT REDS THIS: restore the `[ -f "$TRANSCRIPT_PATH" ]` pre-check
    (or swallow the conversion's failure).
    """
    assert SESSION_TURN.exists(), "the per-turn hook must ship"
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "t.jsonl").write_text(
        '{"message": {"role": "user", "content": "hi"}}\n', encoding="utf-8")
    locked.chmod(0o000)

    spool = tmp_path / "spool"
    env = dict(os.environ)
    env["TORTOISE_CAPTURE_SPOOL_DIR"] = str(spool)
    env["PATH"] = f"{_mock_cli(tmp_path, tmp_path / 'calls.log')}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    try:
        r = subprocess.run(
            ["bash", str(SESSION_TURN)],
            input=json.dumps({"session_id": "sess-locked",
                              "transcript_path": str(locked / "t.jsonl")}),
            capture_output=True, text=True, env=env, timeout=60,
        )
    finally:
        locked.chmod(0o700)
    assert r.returncode == 0, "the hook must never block a turn"
    assert r.stdout == "", "stdout is injected into the user's prompt"
    assert "unreadable" in r.stderr, "the loss must be visible on stderr"
    reasons = [d["reason"] for d in read_discards(spool)]
    assert "transcript_unreadable" in reasons, (
        "an unreadable transcript must be RECORDED, never silently skipped")


def test_the_hooks_record_a_TORN_BYTE_transcript(tmp_path):
    """`UnicodeDecodeError` is a `ValueError`, NOT an `OSError` — so the hooks'
    `except OSError` around the conversion did NOT catch a torn multi-byte
    character (exactly what a kill mid-append leaves in a real Claude
    transcript, which stores raw UTF-8). The traceback escaped, the
    `transcript_unreadable` record never fired, and the turn (and every later
    turn of the session) was dropped with no spool write and no ledger line.

    MUTATION THAT REDS THIS: narrow the hooks' conversion catch back to
    `except OSError`.
    """
    for hook in (SESSION_TURN, SESSION_END):
        assert hook.exists(), f"{hook.name} must ship"
        transcript = tmp_path / f"torn-{hook.name}.jsonl"
        # A valid record, then a torn 3-byte codepoint (killed mid-append).
        transcript.write_bytes(
            b'{"message": {"role": "user", "content": "hi"}}\n'
            b'{"message": {"role": "assistant", "content": "\xe4\xb8')
        spool = tmp_path / f"spool-{hook.name}"
        env = dict(os.environ)
        env["TORTOISE_CAPTURE_SPOOL_DIR"] = str(spool)
        env["PATH"] = f"{_mock_cli(tmp_path, tmp_path / 'calls.log')}:{env.get('PATH', '')}"
        env.pop("TORTOISE_SRC_DIR", None)
        r = subprocess.run(
            ["bash", str(hook)],
            input=json.dumps({"session_id": "sess-torn",
                              "transcript_path": str(transcript)}),
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert r.returncode == 0, f"{hook.name} must never block"
        assert r.stdout == "", "stdout is injected into the user's prompt"
        assert "Traceback" not in r.stderr, f"{hook.name}: {r.stderr}"
        reasons = [d["reason"] for d in read_discards(spool)]
        assert "transcript_unreadable" in reasons, (
            f"{hook.name} dropped a torn-byte transcript silently")


def test_the_session_end_hook_records_an_unreadable_transcript(tmp_path):
    """The SessionEnd twin has the same silent-skip path (`[ -f ]` false on
    EACCES), so the FINAL FLUSH would silently capture nothing.

    MUTATION THAT REDS THIS: restore the `[ -f "$TRANSCRIPT_PATH" ]` pre-check
    in session-end.sh.
    """
    assert SESSION_END.exists(), "session-end.sh must ship"
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "t.jsonl").write_text(
        '{"message": {"role": "user", "content": "hi"}}\n', encoding="utf-8")
    locked.chmod(0o000)

    spool = tmp_path / "spool"
    env = dict(os.environ)
    env["TORTOISE_CAPTURE_SPOOL_DIR"] = str(spool)
    env["PATH"] = f"{_mock_cli(tmp_path, tmp_path / 'calls.log')}:{env.get('PATH', '')}"
    env.pop("TORTOISE_SRC_DIR", None)
    try:
        r = subprocess.run(
            ["bash", str(SESSION_END)],
            input=json.dumps({"session_id": "sess-locked",
                              "transcript_path": str(locked / "t.jsonl")}),
            capture_output=True, text=True, env=env, timeout=60,
        )
    finally:
        locked.chmod(0o700)
    assert r.returncode == 0, "the final flush must never block session end"
    assert "unreadable" in r.stderr
    reasons = [d["reason"] for d in read_discards(spool)]
    assert "transcript_unreadable" in reasons


def test_a_non_utf8_meta_is_a_recorded_corrupt_entry(tmp_path):
    """`UnicodeDecodeError` is a `ValueError`, so a non-UTF-8 meta escaped the
    meta-scan guard and crashed the drain instead of being classified.

    MUTATION THAT REDS THIS: drop `UnicodeDecodeError` from
    `list_spool_metas`'s except clause.
    """
    from tortoise.capture_spool import _meta_path

    write_spool_entry(tmp_path, _snapshot("sess-badmeta"))
    _meta_path(tmp_path, "sess-badmeta").write_bytes(b"\xff\xfe not utf-8")
    summary = flush_spool(tmp_path, _Server().post, now=1000.0)
    assert [d["reason"] for d in summary.discarded] == ["corrupt_entry"]
    assert read_discards(tmp_path)[-1]["reason"] == "corrupt_entry"


def test_per_turn_spool_repairs_a_non_utf8_meta_instead_of_crashing(tmp_path,
                                                                  monkeypatch,
                                                                  capsys):
    """The per-turn path reads the prior meta through `read_spool_meta`; a
    non-UTF-8 meta must be treated as "no prior meta" (the snapshot then REPAIRS
    it), not crash `session spool`.

    MUTATION THAT REDS THIS: drop `UnicodeDecodeError` from `read_spool_meta`'s
    except clause.
    """
    from tortoise import __main__ as cli
    from tortoise.capture_spool import _meta_path

    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "spool"
    write_spool_entry(root, _snapshot("sess-fixmeta"))
    _meta_path(root, "sess-fixmeta").write_bytes(b"\xff\xfe not utf-8")

    transcript = tmp_path / "t.claude.jsonl"
    transcript.write_text("User: hi\nAssistant: hello\n", encoding="utf-8")
    assert cli.main(["session", "spool", "--file", str(transcript),
                     "--session-id", "sess-fixmeta"]) == 0
    assert "Traceback" not in capsys.readouterr().err
    assert read_spool_meta(root, "sess-fixmeta") is not None, "the meta is repaired"
