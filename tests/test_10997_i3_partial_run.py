"""#10997's second half — **I3 is ABSENT**, measured: a failed capture still
commits everything it wrote before the failure.

THE DEFECT THIS PINS
--------------------
`#10997`'s title carries two halves. The first — *"an unresolved identity is
HELD, never minted"* — landed across `#11212`, `#11288` and `#11693`, and
`tests/test_10997_identity_hold.py` pins it. The second — *"a run commits as one
transaction"* — did not: `#11212`'s own body records that *"the S3/S5/S6
transaction boundary (I3) is untouched"*, and the S5-Job-2 sibling `#10998`'s
approved scoping disclaims it in the same words (*"does **not** touch the I3
transaction boundary"*). §4.4's **I3** says the opposite of what runs:

    A committed run is ALL-OR-NOTHING … Leave the journal, and therefore the
    derived graph, unchanged. A partial run is not an observable state.

`_extract_session_v2` (the capture door, `tortoise/sdk.py`) writes a run in
PHASES — entities, then points, then operators — and each write journals its
own record *at the moment it succeeds*. Nothing buffers the earlier phases and
nothing rolls them back, so a failure in a LATER phase leaves the EARLIER ones
committed in the graph and in the journal. A "failed" capture is therefore
observable, partially applied — exactly what I3 forbids.

WHAT IS MEASURED HERE (both directions, so the pin cannot pass vacuously)
------------------------------------------------------------------------
* A capture whose **point** write fails returns `extracted == []` with a
  `point write failed` error — and its journal has GAINED `ObjectRegistered`
  and `EventRecorded`, the two phases that ran BEFORE the failure. **That is
  the I3 violation**, and it is what `test_a_failed_capture…` pins.
* The same capture **without** the injection journals `PointAdded` as well
  (and `EntityLinked`), while still carrying `ObjectRegistered` — so the
  difference the pin reads is the injected failure and the phase it lands in,
  not a property of the fixture.
* An injected **entity** write failure removes `ObjectRegistered` from the
  journal — the injection seam is a real write, not a no-op stub.

WHY A PIN OF THE DEFECT RATHER THAN ITS FIX
-------------------------------------------
Making one run atomic is not a local change: FalkorDB has no multi-statement
transaction (`tortoise/shared_state/event_log.py::append_event` appends one
record per call under `flock`), so it needs either a staging write path or a
compensating rollback spanning the S3-attach / S5-connect / S6-commit phases
that `docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md` §4.4 names as ONE
transaction. That is the epic's (`#5087`) architectural work, not a lane's, and
`config/v4-mechanisms.yml` already records the absence — inside the
`failure_policy` row's note (*"the S3-attach/S5-connect/S6-commit transaction
boundary (I3) is untouched"*), not as a row of its own.

This file is the node `#10998`'s plan requires before a `commit_transaction`
row can be added — *"`pins:` naming a node that reds when S3-attach/S5-connect/
S6-commit share one transaction"* — because **a register whose absence claim no
call can see is the drift the register exists to prevent** (`#5095` review A3,
the same rule `tests/test_5095_s3_identity_policy.py::TestTheRemainingAbsence`
follows).

MUTATION: make the run atomic (buffer or roll back the phases that preceded a
later failure) → the journal delta in `test_a_failed_capture…` becomes empty →
this file REDS, which is the signal that I3 has landed and the registry must be
updated. That red is the deliverable, not a regression.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.config import is_db_uri  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

#: One turn pair that the deterministic offline extractor
#: (`TORTOISE_SESSION_LLM_MOCK=1` → `sdk._V2SessionMock`) turns into exactly one
#: entity, one event and one point — the minimum population that has a phase
#: BEFORE the failure and a phase AFTER it.
_CONV = [
    {"role": "user",
     "content": "we decided the new strategy is durable and the budget is 40k"},
    {"role": "assistant",
     "content": "agreed, the launch plan follows the new strategy"},
]


@pytest.fixture(autouse=True)
def _offline_extractor(monkeypatch, force_sparse_tfidf):
    """The deterministic mock seam — no provider key, no network, no real model.

    Set here rather than inherited: the pin must not silently become a live
    model call on a box that happens to carry a key (which would make both the
    population AND the assertion non-deterministic).

    `force_sparse_tfidf` (conftest) pins the EMBEDDER off too: without it every
    `TortoiseSDK(...)` in this file cold-loads the dense model (~40 s each, all
    of it in teardown-independent warm-up) and the file costs two minutes for
    three assertions. The pinned records (`ObjectRegistered`, `PointAdded`,
    `EventRecorded`) are write-phase records, not retrieval outcomes, so the
    retrieval leg's mode does not move them — and pinning it also keeps this
    file honest in a lane with no HF cache, where `python-ci.yml` degrades to
    sparse anyway.
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _journal_types(path: Path) -> list[str]:
    """The journal's event types, in order — the run's committed record."""
    if not path.exists():
        return []
    out: list[str] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:  # a torn line is not evidence of nothing
            out.append("<unparsable>")
            continue
        out.append(str(record.get("event_type") or record.get("type") or "<untyped>"))
    return out


def _run_capture(base: Path, *, fail_phase: str | None, tag: str) -> dict:
    """Run ONE capture, optionally with a write failure injected mid-run.

    Each run gets its own graph and its own journal (`base/tag/`) so the two
    populations cannot contaminate each other: a second run against the same
    graph would resolve the entity S3 already has and journal no
    `ObjectRegistered` at all, which would make the control read as a
    difference in the fixture rather than in the injection.

    Construction mirrors `tests/conftest.py::sdk_factory` — the sanctioned
    embedded-vs-URI shape: under `TORTOISE_DB_URI` the `namespace` keeps the
    graph name deterministic on the server; with no URI the same call builds a
    throwaway embedded engine under `base`.
    """
    run_dir = base / tag
    run_dir.mkdir(parents=True, exist_ok=True)
    log = run_dir / "events.jsonl"
    namespace = None
    if is_db_uri(os.environ.get("TORTOISE_DB_URI")):
        namespace = f"test_suite_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(str(run_dir / "t.db"), namespace=namespace,
                      event_log_path=str(log))
    try:
        def _boom(*_a, **_k):
            raise RuntimeError(f"injected {fail_phase} write failure")

        if fail_phase == "point":
            sdk.create_point = _boom  # type: ignore[method-assign]
        elif fail_phase == "entity":
            sdk.create_entity = _boom  # type: ignore[method-assign]
        elif fail_phase is not None:
            raise AssertionError(f"unknown fail_phase {fail_phase!r}")

        extracted, meta = sdk._extract_session_v2(
            list(_CONV), session_id=f"i3_{tag}_{uuid.uuid4().hex[:8]}",
            now="2026-11-08T00:00:00+00:00")
        return {
            "extracted": extracted,
            "errors": list(meta.get("errors") or []),
            "journal": _journal_types(log),
        }
    finally:
        # Teardown must not mask the verdict: a close() failure on a throwaway
        # engine is noise, and the journal has already been read.
        with contextlib.suppress(Exception):
            sdk.close()


def test_a_failed_capture_still_commits_the_phases_that_preceded_the_failure(tmp_path):
    """**THE PIN.** I3 is violated: a run that FAILS has already committed.

    `§4.4 I3`: *"A committed run is ALL-OR-NOTHING … A partial run is not an
    observable state."* The run below reports its own failure — `extracted` is
    empty and `errors` carries the point-write failure — and yet the journal
    holds `ObjectRegistered` and `EventRecorded`, the two phases that ran
    before it. `derived = replay(journal)` therefore reproduces a HALF-APPLIED
    run, which is the state I3 says cannot exist.

    MUTATION: make the phases share one transaction → `journal` is empty here →
    RED, signalling that a `commit_transaction` row may be added and this
    absence pin replaced by its positive counterpart.
    """
    failed = _run_capture(tmp_path, fail_phase="point", tag="pin")

    assert failed["extracted"] == [], (
        "the run did NOT fail as the pin requires — the injected point-write "
        "failure no longer fails the capture, so this test is measuring "
        "nothing. Re-derive the injection point in "
        "`_extract_session_v2`'s points phase before trusting this file")
    assert failed["errors"], (
        "a capture whose point write raised reported no error — the failure is "
        "being swallowed, a different (and worse) defect than the one pinned "
        "here")

    assert failed["journal"], (
        "the failed run left NO journal record. If the S3-attach / S5-connect / "
        "S6-commit phases now share ONE transaction, I3 HAS LANDED: replace "
        "this absence pin with its positive counterpart and update "
        "`config/v4-mechanisms.yml` (the `failure_policy` note's I3 clause, and "
        "the `commit_transaction` row #10998's scoping plans) — do not simply "
        "delete this file")

    # The phases that precede the points loop, named individually so a partial
    # fix that only stops journaling ONE of them is still visible.
    assert "ObjectRegistered" in failed["journal"], (
        f"expected the entity phase's record in a failed run's journal, got "
        f"{failed['journal']}")
    assert "EventRecorded" in failed["journal"], (
        f"expected the event phase's record in a failed run's journal, got "
        f"{failed['journal']}")


def test_the_control__an_unfailed_capture_journals_the_phase_after_the_injection(
        tmp_path):
    """**NON-VACUITY.** The same capture, un-injected, journals MORE.

    Without this arm the pin above would still pass if the fixture journaled
    those two records for reasons unrelated to the failure. Here the identical
    conversation produces `PointAdded` — the phase the pin's injection kills —
    and still carries `ObjectRegistered`, so the two journals differ by exactly
    the failure, not by the fixture.
    """
    ok = _run_capture(tmp_path, fail_phase=None, tag="ok")

    assert ok["errors"] == [], (
        f"the control capture reported errors ({ok['errors']}) — the fixture is "
        f"not a clean run, so it cannot serve as the pin's control")
    assert ok["extracted"], "the control capture extracted nothing"
    assert "ObjectRegistered" in ok["journal"], (
        f"control journal {ok['journal']}")
    assert "PointAdded" in ok["journal"], (
        f"the control capture journals no PointAdded ({ok['journal']}) — the "
        f"pin's `extracted == []` then proves nothing about the points phase")


def test_the_injection_seam_is_a_real_write_not_a_no_op(tmp_path):
    """**NON-VACUITY, second direction.** An injected ENTITY failure removes
    `ObjectRegistered`.

    The pin's injection is a raise on `sdk.create_point`. This arm injects on a
    DIFFERENT symbol and observes the DIFFERENT record disappear, so a future
    refactor that turns the injection into a no-op (a bound method replaced on
    the instance but no longer called, say) reds here instead of quietly
    weakening the pin.
    """
    broken = _run_capture(tmp_path, fail_phase="entity", tag="entityfail")
    assert "ObjectRegistered" not in broken["journal"], (
        f"an injected `create_entity` failure still journaled "
        f"ObjectRegistered ({broken['journal']}) — the injection seam is not "
        f"intercepting the write it names, so the pin's other arm cannot be "
        f"trusted")
