"""#10368 — a `create_point(dedup=True)` content fold must not mutate a TERMINAL point.

A terminal point is the graph's record of a SETTLED belief. `create_point`
resolves a dedup hit by CONTENT, so the caller never named the point they are
about to write: a content coincidence is enough to select a dead claim as the
target of an unaddressed write. #7819 refused that fold only when it carried a
temporal prop (`_TEMPORAL_RESTAMP_PROPS`), so a fold carrying a GENERIC prop
reached the write leg and rewrote the terminal node anyway — the same #8400
"terminal point mutated" class #9819 closed for `ingest`, reached through the
direct SDK door.

What is pinned here:

* the issue's own reproduction — `create_point(content, dedup=True, prop)` on a
  retracted point leaves `updatedAt` unmoved and stores no prop, and SAYS so
  with the `terminal_fold_refused` advisory rather than silently no-op'ing;
* the authored-word leg (`credibility=` → `set_point_baseline` +
  `_stamp_authored_word`) is refused by the same guard, because it writes
  server-owned baseline props BEFORE the `update_point` leg would run;
* the scope boundaries the guard must NOT cross:
    - an ADDRESSED revision (`update_point(terminal_id, prop)`) still applies —
      #3305's recorded post-terminalization revision capability, pinned by
      `test_pointinvalidated_rebuild.py::test_revise_after_invalidate_skip_updated_at`
      and `test_pointsuperseded_rebuild.py::test_supersede_then_revision_does_not_end_at_the_supersede_ts`;
    - a LIVE target still folds the same props;
    - the journaled provenance retrofit (#7704, OWNER RULING 2026-10-25: "a
      dedup hit KEEPS the new evidence") still folds onto a terminal point —
      it is evidence ABOUT the claim, not a rewrite of what the claim says.

And the read the guard is built on is a SNAPSHOT, so the WRITE point
re-verifies: a point that goes terminal between that read and the fold's
`update_point` call is refused there, not written (`test_a_fold_that_races_a_
terminalization_at_the_write_point_writes_nothing`). The authored-word leg's
`set_point_baseline` write is the disclosed residual that window does not cover.
"""
from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture
def sdk(sdk_factory):
    return sdk_factory()


def _advisory_codes(reply: dict) -> list[str]:
    return [a.get("code") for a in (reply.get("advisories") or [])]


def _fresh_content(tag: str) -> str:
    return f"terminal fold {tag} {uuid.uuid4().hex[:10]}"


def test_a_content_fold_with_a_generic_prop_does_not_mutate_a_terminal_point(sdk):
    """#10368's reproduction, verbatim.

    Pre-fix (measured on `main` at `a6e20f05b05d`): the second `create_point`
    reported the dedup HIT, stored `custom_probe`, and moved `updatedAt` on a
    `retracted` point — the dead claim's record was rewritten by a caller who
    never addressed it.
    """
    content = _fresh_content("generic-prop")
    pid = sdk.create_point("statement", content, dedup=True)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)
    assert before["status"] == "retracted"

    again = sdk.create_point("statement", content, dedup=True,
                             custom_probe="x")
    after = sdk.get_point(pid)

    assert again["id"] == pid, "a dedup HIT is still reported"
    assert after["status"] == "retracted", "the point must stay terminal"
    assert after["updatedAt"] == before["updatedAt"], (
        "a content coincidence moved a settled claim's updatedAt")
    assert "custom_probe" not in after, (
        "a content coincidence wrote a prop onto a settled claim")
    assert "terminal_fold_refused" in _advisory_codes(again), (
        "the drop must be OBSERVABLE in the result, not only in the log: "
        f"{again.get('advisories')}")
    assert "update_point" in str(again.get("advisories")), (
        "the advisory must name the ADDRESSED route that revises a dead "
        "claim deliberately")


def test_an_authored_word_fold_does_not_touch_a_terminal_points_baseline(sdk):
    """The fold's SECOND write leg — server-owned baseline, not `update_point`.

    `credibility=` on a dedup hit writes `ep_alpha`/`ep_beta` via
    `set_point_baseline` and the label via `_stamp_authored_word`, both BEFORE
    the props leg. A guard placed only in front of `update_point` would leak
    this one (#7819's own "all-or-nothing" reasoning).
    """
    content = _fresh_content("authored-word")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)

    again = sdk.create_point("statement", content, dedup=True,
                             credibility="gold")
    after = sdk.get_point(pid)

    assert again["id"] == pid
    assert after["status"] == "retracted"
    assert after["updatedAt"] == before["updatedAt"]
    assert after.get("credibility") == before.get("credibility")
    assert after.get("ep_alpha") == before.get("ep_alpha")
    assert after.get("ep_beta") == before.get("ep_beta")
    assert "terminal_fold_refused" in _advisory_codes(again), again


def test_the_same_fold_still_applies_to_a_live_target(sdk):
    """Scope boundary: the guard is the TERMINAL case only.

    A blanket refusal (or one that ran before the terminal verdict) would red
    here — a live point is exactly what a dedup fold is FOR (idempotent
    re-ingest, #7704's "one claim, N sources").
    """
    content = _fresh_content("live-target")
    pid = sdk.create_point("statement", content)["id"]
    sdk.create_point("statement", content, dedup=True, custom_probe="x")
    stored = sdk.get_point(pid)
    assert stored.get("custom_probe") == "x"
    assert stored["status"] == "live"


def test_an_addressed_revision_still_applies_to_a_terminal_point(sdk):
    """Counter-test: #3305's capability must survive this guard.

    `update_point` is the ADDRESSED door — the caller names the point — and a
    post-terminalization revision is a recorded capability (`PointRevised`
    later than the terminalization, folded by replay via `max_inline_seq`). If
    a later change widens #10368's guard into `update_point`, this reds.
    """
    content = _fresh_content("addressed")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)

    sdk.update_point(pid, custom_probe="y")

    after = sdk.get_point(pid)
    assert after.get("custom_probe") == "y", (
        "an addressed revision of a dead claim is a recorded capability "
        "(#3305) — #10368 scopes the CREATE fold only")
    assert after["status"] == "retracted"


def test_a_key_resolved_fold_is_refused_on_a_terminal_point_too(sdk):
    """A `key` decides IDENTITY (#6110) — it is not a licence to rewrite a
    settled record. The refusal covers both resolution routes, so the class is
    closed at this door rather than left reachable through the key leg.

    (`ingest` refuses the same fold in Phase 1 since #10377, so the two doors
    agree on what a terminal point accepts.)
    """
    content = _fresh_content("key-hit")
    key = f"key-10368-{uuid.uuid4().hex[:10]}"
    pid = sdk.create_point("statement", content, key=key)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)

    again = sdk.create_point("statement", content, key=key, dedup=True,
                             custom_probe="x")
    after = sdk.get_point(pid)

    assert again["id"] == pid, "a key dedup HIT is still reported"
    assert after["status"] == "retracted"
    assert after["updatedAt"] == before["updatedAt"]
    assert "custom_probe" not in after, (
        "a key-resolved fold wrote a prop onto a settled claim")
    assert "terminal_fold_refused" in _advisory_codes(again), again


def test_the_journaled_provenance_fold_still_applies_to_a_terminal_point(sdk):
    """#7704's OWNER RULING (2026-10-25) is untouched by #10368.

    "A dedup hit KEEPS the new evidence — one claim, N sources." Evidence ABOUT
    a claim is not a rewrite of what the claim says, so the provenance retrofit
    still folds onto a terminal point while the caller's own props and word are
    refused. If a later change widens the refusal over `extractedFrom`, this
    reds.
    """
    content = _fresh_content("provenance")
    ref = f"https://example.com/10368/{uuid.uuid4().hex[:8]}"
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)

    again = sdk.create_point("statement", content, dedup=True,
                             extractedFrom=ref)

    after = sdk.get_point(pid)
    urls = {row[0] for row in sdk._get_proj().g.query(
        "MATCH (p:Point {id:$id})-[:extractedFrom]->(s:Source) "
        "RETURN s.url", params={"id": pid}).result_set}
    assert ref in urls, (
        "the journaled provenance fold was refused with the caller's props — "
        "#7704's owner ruling says a dedup hit KEEPS the new evidence")
    assert after["status"] == "retracted"
    assert after["updatedAt"] == before["updatedAt"]
    assert "terminal_fold_refused" not in _advisory_codes(again), (
        "nothing the caller asked for was refused here — only provenance "
        f"folded: {again.get('advisories')}")


def test_a_pure_dedup_hit_on_a_terminal_point_stays_silent(sdk):
    """Nothing was asked for, so nothing is refused — and nothing churns.

    No props, no authored word: the pre-#10368 tree already wrote nothing here,
    so the new advisory must not fire and `updatedAt` must not move (#490
    P2-1: a pure dedup hit must not re-trigger EP dirty-marking).
    """
    content = _fresh_content("pure-hit")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)

    again = sdk.create_point("statement", content, dedup=True)

    after = sdk.get_point(pid)
    assert again["id"] == pid
    assert after["updatedAt"] == before["updatedAt"]
    assert "terminal_fold_refused" not in _advisory_codes(again), again


def test_a_bare_dedup_hit_costs_no_lifecycle_read(sdk, monkeypatch):
    """The skip the guard's own comment promises is MEASURED here, not asserted.

    `_lifecycle_terminal_marker` is a graph round-trip (`LIFECYCLE_FANOUT_QUERY`)
    whenever it is called without `rows`, and the shared gate above the fold names
    one case where it must not be called at all: a bare dedup hit (no props, no
    authored word). The predicate that decided it tested the raw `props` bag — and
    that bag ALWAYS carries the DERIVED `content_hash` by then — so the skip was
    dead code and every `create_point(dedup=True)` hit (the re-ingest and
    self-host hot path included) paid a query it had never paid before (#10368
    review).

    Two-sided on purpose: a "fix" that simply REMOVED the read reds the second
    half, because a fold carrying a caller prop must still get the terminal
    verdict that decides the refusal.
    """
    calls: list[int] = []
    real = type(sdk)._lifecycle_terminal_marker

    def counting(self, *args, **kwargs):
        calls.append(len(calls))
        return real(self, *args, **kwargs)

    content = _fresh_content("no-extra-read")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)
    monkeypatch.setattr(type(sdk), "_lifecycle_terminal_marker", counting)

    again = sdk.create_point("statement", content, dedup=True)
    assert again["id"] == pid, "a dedup HIT is still reported"
    assert calls == [], (
        f"a bare dedup hit issued {len(calls)} lifecycle read(s) — this path "
        "wrote nothing before #10368 and its own comment says it costs no extra "
        "query (#10368 review)")

    sdk.create_point("statement", content, dedup=True, custom_probe="x")
    assert len(calls) == 1, (
        "a fold carrying a caller prop must READ the terminal verdict — that "
        "read is what refuses the write onto the settled claim (#10368)")


def test_an_applied_fold_costs_the_measured_lifecycle_reads(sdk, monkeypatch):
    """The APPLIED path's cost, measured — the comment above the fold claims ONE
    read, and on the applied path that claim is not true.

    `_lifecycle_terminal_marker` is hoisted so ONE read serves both refusals
    (#10368), but when the target is LIVE the fold then calls `update_point`,
    whose own #2498/#7819 `_assert_lifecycle_guard` classifies the SAME fan-out
    again. Measured here rather than argued: the pre-#10368 tree paid that
    guard read only (`1`), so an applied fold now costs `2` — one extra
    `LIFECYCLE_FANOUT_QUERY` per dedup fold that actually writes, on the
    re-ingest / self-host commit path this PR itself calls hot.

    ⛔ Two-sided, and pinned at the NUMBER rather than at `>= 1`: dropping the
    hoist reds nothing here (`2` merely becomes the guard's `1`), so a bare
    inequality would let the refusals above lose their read and stay green.
    The refused arm is pinned alongside it, because that is the arm the hoist
    exists for: one read, no `update_point`, no write.

    Why the double read is not collapsed into one (a fixer's option the #10368
    review offered): handing this read's rows to `update_point`'s guard would
    make the guard classify a snapshot taken BEFORE the authored-word leg's
    `set_point_baseline` write and before anything else in `update_point`.
    Re-verifying at the write point is the guard's whole value — the same
    reason a database re-evaluates a predicate against the row version at
    UPDATE time instead of trusting the earlier search — so the cost is the
    price of the safety property, and it is DISCLOSED, not hidden.

    ⛔ AND THAT RE-VERIFICATION IS ARMED BY THE FOLD (#10368 review P2).
    `update_point` scopes `refuse_terminal` to `_TEMPORAL_RESTAMP_PROPS`, which
    an applied fold never carries, so its read classified and acted on nothing
    until the fold started setting `_FOLD_REFUSES_TERMINAL_WRITE` around this
    one write. The race that made the difference is pinned in
    `test_a_fold_that_races_a_terminalization_at_the_write_point_writes_nothing`.
    """
    from tortoise.live import LIFECYCLE_FANOUT_QUERY

    proj = sdk._get_proj()
    guard = type(proj.g)          # `_GuardedGraph` is __slots__-restricted
    real_query = guard.query
    reads: list[int] = []

    def counting(inner_self, cypher, *args, **kwargs):
        if cypher == LIFECYCLE_FANOUT_QUERY:
            reads.append(len(reads))
        return real_query(inner_self, cypher, *args, **kwargs)

    content = _fresh_content("applied-cost")
    pid = sdk.create_point("statement", content)["id"]
    live = sdk.create_point("statement", content, dedup=True,
                            custom_probe="x")
    assert live["id"] == pid

    monkeypatch.setattr(guard, "query", counting)

    sdk.create_point("statement", content, dedup=True, custom_probe="x")
    assert len(reads) == 2, (
        "an APPLIED dedup fold must cost exactly the measured 2 fan-out reads "
        f"(this fold's refusal read + update_point's own guard), got {len(reads)}"
    )

    sdk.retract_point(pid)
    reads.clear()
    refused = sdk.create_point("statement", content, dedup=True,
                               custom_probe="x")
    assert refused["id"] == pid
    assert "terminal_fold_refused" in _advisory_codes(refused), refused
    assert len(reads) == 1, (
        "a REFUSED fold must cost exactly 1: the hoist exists so both refusals "
        f"share one read and `update_point` is never reached, got {len(reads)}"
    )


def test_a_fold_that_races_a_terminalization_at_the_write_point_writes_nothing(sdk, monkeypatch):
    """The guard's read is a SNAPSHOT — the WRITE point must re-verify (#10368 review P2).

    The hoisted read decides both refusals, but it is taken BEFORE the fold's
    `update_point` call (with the #7704 provenance work and the journal append in
    between). A concurrent `retract_point`/`supersede_point` landing in that
    window used to slip straight through: `update_point` scopes its own
    `refuse_terminal` to `_TEMPORAL_RESTAMP_PROPS` (#7819/#3305) and an applied
    fold carries no temporal prop, so that door's read classified the fan-out and
    acted on NOTHING — the caller's props and `updatedAt` landed on a claim that
    had just been settled. The fold now arms the refusal
    (`_FOLD_REFUSES_TERMINAL_WRITE`) for its one write and turns the door's
    refusal into its own drop-with-advisory, so the class this PR closes cannot be
    re-opened by timing.

    The race is made deterministic rather than slept on: the fold's snapshot read
    is answered "live" while every later read — which is the one at the write
    point — answers truthfully.

    ⛔ Non-vacuous: without the arm, `update_point` is called with
    `refuse_terminal=False`, writes `custom_probe` and moves `updatedAt`, and this
    test reds on both assertions (measured on the pre-fix tree).
    """
    content = _fresh_content("raced-terminal")
    pid = sdk.create_point("statement", content, dedup=True)["id"]
    sdk.retract_point(pid)
    before = sdk.get_point(pid)
    assert before["status"] == "retracted"

    real = type(sdk)._lifecycle_terminal_marker
    calls: list[str] = []

    def snapshot_then_truth(self, point_id, rows=None):
        calls.append(point_id)
        if len(calls) == 1:
            return None          # the snapshot: taken before the retraction
        return real(self, point_id, rows)

    monkeypatch.setattr(type(sdk), "_lifecycle_terminal_marker",
                        snapshot_then_truth)

    again = sdk.create_point("statement", content, dedup=True,
                             custom_probe="raced")
    after = sdk.get_point(pid)

    assert again["id"] == pid, "a dedup HIT is still reported"
    assert len(calls) >= 2, (
        "the write point must take its OWN lifecycle read — the snapshot above "
        "is not a verification")
    assert after.get("custom_probe") is None, (
        "a fold that raced a terminalization still stored the caller's prop")
    assert after["updatedAt"] == before["updatedAt"], (
        "a fold that raced a terminalization still moved a settled claim's "
        "updatedAt")
    assert after["status"] == "retracted", "the point must stay terminal"
    assert "terminal_fold_refused" in _advisory_codes(again), again
