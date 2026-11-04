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


def _count_terminal_reads(sdk, monkeypatch) -> list[str]:
    """Record every `_lifecycle_terminal_marker` call the SDK makes.

    The verdict is the fold's only fan-out read (`LIFECYCLE_FANOUT_QUERY`,
    one statement over the id's whole fan-out, #7822), so counting the call is
    the cheapest faithful proxy for "this dedup hit paid an extra query".
    Wrapped rather than stubbed: the real verdict still runs, so a case that
    faults or refuses behaves exactly as in production.
    """
    calls: list[str] = []
    original = type(sdk)._lifecycle_terminal_marker

    def counting(self, point_id, *args, **kwargs):
        calls.append(point_id)
        return original(self, point_id, *args, **kwargs)

    monkeypatch.setattr(type(sdk), "_lifecycle_terminal_marker", counting)
    return calls


def test_a_bare_dedup_hit_costs_no_terminal_read(sdk, monkeypatch):
    """The fold's read gate is what the call could WRITE, not `props` itself.

    `content_hash` is put into `props` unconditionally at the top of
    `create_point` and only popped after the #7704 provenance retrofit, so a
    gate written as `if props` was ALWAYS true: the "a bare dedup hit costs no
    extra query" arm was unreachable and every dedup hit — the NORMAL path for
    the self-host surface, which dedups by default — paid a fan-out read.
    Measured pre-fix: this test's `calls` is `[pid]`; post-fix it is `[]`.

    Both nothing-at-all and provenance-only are pinned, because
    `extractedFrom` is the #7704 fold (left applying) and is not a reason to
    read a verdict either.
    """
    content = _fresh_content("read-cost")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)

    calls = _count_terminal_reads(sdk, monkeypatch)

    # (a) a bare hit: no props, no authored word.
    again = sdk.create_point("statement", content, dedup=True)
    assert again["id"] == pid
    assert calls == [], (
        "a bare dedup hit asked the lifecycle fan-out for a verdict it cannot "
        f"refuse anything on — {len(calls)} extra read(s): {calls}")

    # (b) an evidence-only hit: `extractedFrom` is the #7704 provenance fold,
    # deliberately NOT refused, so there is still nothing to decide.
    ref = f"https://example.com/10368/{uuid.uuid4().hex[:8]}"
    sdk.create_point("statement", content, dedup=True, extractedFrom=ref)
    assert calls == [], (
        "an evidence-only fold read the terminal verdict although #7704's "
        f"provenance retrofit is never refused — extra reads: {calls}")


def test_a_prop_bearing_dedup_hit_DOES_read_the_verdict_once(sdk, monkeypatch):
    """Positive control for the gate above — the read must not be dropped.

    A caller prop IS something the #10368 refusal can decide, so the verdict
    must be read, exactly once (ONE read serving every leg of the fold). If a
    later change over-narrows the gate, this reds — and the sibling
    `…_does_not_mutate_a_terminal_point` cases would then stop refusing.
    """
    content = _fresh_content("read-cost-positive")
    pid = sdk.create_point("statement", content)["id"]
    sdk.retract_point(pid)

    calls = _count_terminal_reads(sdk, monkeypatch)

    again = sdk.create_point("statement", content, dedup=True,
                             custom_probe="x")
    assert calls == [pid], (
        "a caller prop must be adjudicated by the terminal verdict, exactly "
        f"once — reads: {calls}")
    assert "terminal_fold_refused" in _advisory_codes(again), again


def test_a_bare_dedup_hit_spends_no_terminal_read(sdk, monkeypatch):
    """#10841 review (P3) — the read is skipped for the hit the comment names.

    The shared `_lifecycle_terminal_marker` read is guarded so that the BARE
    dedup hit ("no props, no authored word") costs no extra query. The guard
    used to be `props or cred_prior is not None`, and `props` is never empty
    there: `create_point` sets `props["content_hash"]` unconditionally (the
    hash is persisted regardless of the dedup flag, fix #80) and does not pop
    it until the fold's own write leg. So the condition admitted EVERY dedup
    hit and the "costs no extra query" promise was false on the default
    self-host path (dedup is the default and a same-word retry is normal).

    This pins the QUERY COUNT rather than a reply, because the defect is a cost
    and a contract, not a wrong answer — the refusal flag is computed from the
    post-pop props either way, which is why the sibling test above stayed green.

    MUTATION: restore `if (props or cred_prior is not None)` and arm 1 records
    one read where it asserts zero.
    """
    content = _fresh_content("bare-read")
    pid = sdk.create_point("statement", content)["id"]

    reads: list[str] = []
    real = sdk._lifecycle_terminal_marker

    def _spy(point_id, *args, **kwargs):
        reads.append(point_id)
        return real(point_id, *args, **kwargs)

    monkeypatch.setattr(sdk, "_lifecycle_terminal_marker", _spy)

    # 1. BARE dedup hit: no prop, no authored word -> no terminal read at all.
    bare = sdk.create_point("statement", content, dedup=True)
    assert bare["id"] == pid, "a dedup HIT is still reported"
    assert reads == [], (
        "a bare dedup hit spent a LIFECYCLE_FANOUT_QUERY it does not need — "
        f"the guard admitted `content_hash`, which is always set: {reads}")

    # 2. …and a hit that CARRIES a caller prop still reads it, because the
    #    #10368 verdict is what decides the refusal. The fix must not silence
    #    the read when it is load-bearing.
    sdk.retract_point(pid)
    reads.clear()
    with_prop = sdk.create_point("statement", content, dedup=True,
                                 custom_probe="x")
    assert with_prop["id"] == pid
    assert reads == [pid], (
        "the terminal verdict is still required for a fold that carries a "
        f"caller prop: {reads}")
    assert "terminal_fold_refused" in _advisory_codes(with_prop), with_prop

    # 3. The authored-word leg reads it too (no props, but `cred_prior` set).
    reads.clear()
    with_word = sdk.create_point("statement", content, dedup=True,
                                 credibility="low")
    assert with_word["id"] == pid
    assert reads == [pid], (
        f"an authored word needs the same verdict: {reads}")
