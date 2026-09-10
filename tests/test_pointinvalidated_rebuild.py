"""#2488 (issue #2488) — PointInvalidated rebuild-parity suite.

Live ``invalidate_point`` writes outdated=true + validTo/expiredAt/updatedAt
stamps + a CORRECTS edge (status untouched) but emitted ZERO events — a JSONL
wipe+rebuild replayed the pre-invalidate PointAdded and RESURRECTED the
invalidated claim to EP voting/reads (the #2488 ghost). Fix shape (mirrors
#2423's supersede machinery): kwargs-style PointInvalidated emission (ts=now)
in ``invalidate_point`` + a pass-1b trailing-sweep fold
(``_fold_point_invalidated`` — outdated flag + stamps + CORRECTS, NO status
write) governed by a cross-family (supersede+invalidate) survivor rule that
drops pre-re-creation folds and orders survivors in journal-append order.

Pinned contracts here:
  - core parity: invalidate → rebuild reproduces the live node EXACTLY
    (updatedAt == journaled invalidate ts — the sweep fold is the id's last
    journal writer and writes the journaled ts unconditionally), including
    NO status change;
  - EP no-resurrection after rebuild (#2422 ghost assertions);
  - idempotency across rebuild → rebuild;
  - mixed supersede+invalidate in BOTH orders converges to live;
  - double-invalidate folds every survivor (live-legal) — distinct
    corrected_by → 2 CORRECTS, identical → 1;
  - id-reuse (raw hard-delete lane) drops pre-recreation folds;
  - raw same-id PointAdded re-emission after invalidate resurrects the
    rebuilt node live while the SDK-live node stays outdated (journal
    authoritative — ambiguity pinned);
  - invalidate-on-draft → promote: the pre-promote PointInvalidated fold
    SURVIVES (PointPromoted is NOT a drop boundary) and skip_updated_at fires
    (rebuilt updatedAt is the promote fold's rebuild-now stamp, NOT the older
    journaled invalidate ts);
  - corrected_by-terminalized / missing endpoint: the CORRECTS MERGE silently
    skips, the fold still applies (matched ≥ 1), no fold-miss warning.

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_pointinvalidated_rebuild.py -q
"""
from __future__ import annotations

import datetime
import json
import os

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sup(tmp_path):
    """(db, events_dir, sdk) with the journal wired."""
    db = os.path.join(str(tmp_path), "inv.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _rebuild(sdk, events_dir) -> None:
    sdk._get_proj().rebuild_all(str(events_dir))


def _corr(proj, old_id: str, new_id: str) -> int:
    return proj.g.query(
        "MATCH (a:Point {id:$new})-[r:CORRECTS]->(b:Point {id:$old}) "
        "RETURN count(r)",
        params={"new": new_id, "old": old_id}).result_set[0][0]


def _corr_total(proj, old_id: str) -> int:
    return proj.g.query(
        "MATCH (a:Point)-[r:CORRECTS]->(b:Point {id:$old}) RETURN count(r)",
        params={"old": old_id}).result_set[0][0]


def _point_state(sdk, pid: str) -> dict:
    p = sdk.get_point(pid) or {}
    return {k: p.get(k) for k in
            ("status", "outdated", "validTo", "expiredAt", "updatedAt")}


def _raw_append(events, sdk, type_: str, **fields) -> None:
    """Append a raw producer JSONL line (the way an unjournaled/raw producer
    would — the SDK-live graph never sees it; only rebuild replays it)."""
    line = {
        "event_id": sdk.ulid(),
        "ts": datetime.datetime.now(datetime.UTC).isoformat(),
        "type": type_,
        "initiated_by": "raw-producer",
        "projection_version": 2,
    }
    line.update(fields)
    with open(events / "events.jsonl", "a") as fh:
        fh.write(json.dumps(line) + "\n")


# ═══════════════════════════════════════════════════════════════════════
# Indicator 1 (core): invalidate → rebuild → query cycle returns IDENTICAL
# state (outdated flag + journaled stamps + CORRECTS, status unchanged —
# no resurrection)
# ═══════════════════════════════════════════════════════════════════════

def test_invalidated_point_stays_outdated_after_rebuild(sup):
    """The core P1: an invalidated Point must NOT resurrect as clean on
    rebuild. outdated=true + validTo/expiredAt/updatedAt (the journaled
    invalidate ts — invalidate is the id's last journal writer, so the sweep
    fold writes the ts unconditionally → exact live parity) + CORRECTS edge
    survive the JSONL wipe+replay, AND status stays 'live' (invalidate is a
    flag, not a terminal transition)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "old A", status="live")["id"]
    corr = sdk.create_point("statement", "corrector B", status="live")["id"]
    sdk.invalidate_point(a, corr)
    proj = sdk._get_proj()
    pre = _point_state(sdk, a)
    assert pre["outdated"] is True
    assert pre["status"] == "live", "invalidate_point must NOT change status"
    assert pre["validTo"] and pre["expiredAt"] and pre["updatedAt"]
    assert _corr(proj, a, corr) == 1
    _rebuild(sdk, events)
    post = _point_state(sdk, a)
    assert post == pre, (
        f"invalidated point state drifted across rebuild: {pre} != {post}")
    assert _corr(proj, a, corr) == 1, "CORRECTS edge lost on rebuild"


# ═══════════════════════════════════════════════════════════════════════
# EP no-resurrection (#2422 ghost): after rebuild the outdated claim must not
# re-enter EP participation
# ═══════════════════════════════════════════════════════════════════════

def posterior_mean(sdk: TortoiseSDK, pid: str) -> float:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
        "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
        params={"id": pid},
    ).result_set
    a, b = float(rows[0][0]), float(rows[0][1])
    return a / (a + b)


def _chain_ids(sdk: TortoiseSDK) -> dict[str, str]:
    """A --IMPL(op1)--> B --IMPL(op2)--> C chain (the ghost-voting canary)."""
    a = sdk.create_point("statement", "strong source", status="live")["id"]
    b = sdk.create_point("statement", "middle claim", status="live")["id"]
    c = sdk.create_point("statement", "leaf claim", status="live")["id"]
    sdk.set_point_baseline(a, 10.0, 1.0)
    sdk.set_point_baseline(b, 1.0, 1.0)
    sdk.set_point_baseline(c, 1.0, 1.0)
    op1 = sdk.create_operator("IMPL", a, [b])["id"]
    op2 = sdk.create_operator("IMPL", b, [c])["id"]
    return {"a": a, "b": b, "c": c, "op1": op1, "op2": op2}


def _run_ep(sdk: TortoiseSDK, seeds: list[str]) -> None:
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in rows} if rows else {}
    sdk._get_ep().run(seeds, max_hops=2, evidence=evidence)


def test_rebuilt_invalidated_claim_does_not_vote_in_ep(sup, tmp_path):
    """#2488 ghost leg: the pre-fix rebuild resurrected A to EP voting (its
    PointAdded snapshot has no outdated flag) — C climbed back toward the
    live (pre-invalidate) mean. Post-fix, rebuild replays the invalidate
    fold, and re-running EP must move C to the deletion reference (A never
    re-enters the affected set)."""
    _, events, sdk = sup
    ids = _chain_ids(sdk)
    _run_ep(sdk, [ids["op1"], ids["op2"]])
    live_mean = posterior_mean(sdk, ids["c"])
    assert live_mean > 0.51, f"chain must carry A's strength, got {live_mean}"

    succ = sdk.create_point("statement", "corrected source",
                            status="live")["id"]
    sdk.invalidate_point(ids["a"], succ)
    assert sdk.get_point(ids["a"])["outdated"] is True

    # Control: fresh graph WITHOUT A's operator edge (the #689-clean ref).
    ctrl = TortoiseSDK(db_path=os.path.join(str(tmp_path), "ctrl.db"))
    try:
        ctrl_ids = _chain_ids(ctrl)
        ctrl._get_proj().g.query(
            "MATCH (o:Point {id:$id})-[r]->() DELETE r",
            params={"id": ctrl_ids["op1"]},
        )
        _run_ep(ctrl, [ctrl_ids["op1"], ctrl_ids["op2"]])
        deletion_ref = posterior_mean(ctrl, ctrl_ids["c"])
    finally:
        ctrl.close()

    _rebuild(sdk, events)
    # EP bookkeeping (ep_alpha/baseline_set) is node state, not journaled —
    # re-establish baselines on the rebuilt graph before the EP run.
    for pid, (al, be) in ((ids["a"], (10.0, 1.0)), (ids["b"], (1.0, 1.0)),
                          (ids["c"], (1.0, 1.0))):
        sdk.set_point_baseline(pid, al, be)
    _run_ep(sdk, [ids["op1"], ids["op2"]])
    rebuilt_mean = posterior_mean(sdk, ids["c"])
    # P6.3 tolerance (damping residue only — see test_ep_terminal_ghost.py).
    assert rebuilt_mean == pytest.approx(deletion_ref, abs=0.005), (
        "rebuilt invalidated claim's ghost must not vote: "
        f"got C={rebuilt_mean}, deletion reference={deletion_ref}")
    assert rebuilt_mean < live_mean - 0.04, (
        "rebuild must not resurrect A's EP influence: "
        f"live={live_mean}, rebuilt={rebuilt_mean}")
    assert sdk.get_point(ids["a"])["outdated"] is True, (
        "outdated flag must survive rebuild for the EP exclusion to hold")


# ═══════════════════════════════════════════════════════════════════════
# Idempotency: rebuild → rebuild converges (the sweep fold rewrites the same
# journaled ts every time — stable across rebuilds, unlike pass-1a's
# rebuild-now stamps which the fold overwrites)
# ═══════════════════════════════════════════════════════════════════════

def test_rebuild_is_idempotent_for_invalidate_state(sup):
    """Indicator 4: rebuild → rebuild leaves the graph identical — the fold
    re-runs and writes the SAME journaled invalidate ts (not rebuild-now), so
    updatedAt is stable across rebuilds too."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    corr = sdk.create_point("statement", "B", status="live")["id"]
    sdk.invalidate_point(a, corr)
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    snapshot1 = {
        "a": _point_state(sdk, a),   # fold-stamped → updatedAt stable too
        "corr_edge": _corr(proj, a, corr),
    }
    _rebuild(sdk, events)
    snapshot2 = {
        "a": _point_state(sdk, a),
        "corr_edge": _corr(proj, a, corr),
    }
    assert snapshot2 == snapshot1, "second rebuild drifted from first"


# ═══════════════════════════════════════════════════════════════════════
# Mixed supersede + invalidate on one old id, BOTH orders → live parity
# ═══════════════════════════════════════════════════════════════════════

def _assert_mixed_parity(sup, do_sup_first: bool) -> None:
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B (successor)", status="live")["id"]
    c = sdk.create_point("statement", "C (corrector)", status="live")["id"]
    if do_sup_first:
        sdk.supersede_point(a, b)
        sdk.invalidate_point(a, c)   # live-legal: invalidate has no terminal guard
    else:
        sdk.invalidate_point(a, c)
        sdk.supersede_point(a, b)    # live-legal: invalidate left status='live'
    proj = sdk._get_proj()
    pre = _point_state(sdk, a)
    # Both folds are live-truth: A is superseded (status from the supersede
    # fold) AND outdated, stamps = the LAST event's journaled ts, and the
    # CORRECTS from BOTH the supersede and the invalidate survive (live
    # supersede only MERGEs its own CORRECTS — it never deletes a prior one).
    assert pre["status"] == "superseded"
    assert pre["outdated"] is True
    assert _corr(proj, a, b) == 1
    assert _corr(proj, a, c) == 1
    _rebuild(sdk, events)
    # updatedAt is deliberately NOT in the equality: when the SUPERSEDE fold
    # is the id's last writer its updatedAt = the JSONL line's own ts (the
    # pre-existing #2164-P4 µs drift — supersede's emit passes no ts=now),
    # not the live SET clock. #2488's ts=now guarantees EXACT updatedAt
    # parity only when the invalidate fold is the last writer (core test).
    post = _point_state(sdk, a)
    for k in ("status", "outdated", "validTo", "expiredAt"):
        assert post[k] == pre[k], (
            f"mixed supersede+invalidate (sup_first={do_sup_first}) drifted "
            f"across rebuild: {pre} != {post}")
    assert _corr(proj, a, b) == 1 and _corr(proj, a, c) == 1, (
        "both CORRECTS edges must survive rebuild")


def test_supersede_then_invalidate_live_parity(sup):
    """supersede(A,B) → invalidate(A,C): rebuild reproduces live A
    (superseded + outdated + stamps of the LAST event (the invalidate) + both
    CORRECTS)."""
    _assert_mixed_parity(sup, do_sup_first=True)


def test_invalidate_then_supersede_live_parity(sup):
    """invalidate(A,C) → supersede(A,B): rebuild reproduces live A
    (superseded + outdated + stamps of the LAST event (the supersede) + both
    CORRECTS)."""
    _assert_mixed_parity(sup, do_sup_first=False)


# ═══════════════════════════════════════════════════════════════════════
# Double-invalidate: live-legal (no terminal guard; outdated is a flag) —
# every survivor fold is live-truth
# ═══════════════════════════════════════════════════════════════════════

def test_double_invalidate_distinct_correctors_two_corrects(sup):
    """invalidate(A,B) then invalidate(A,C): both folds survive the id filter
    (no re-creation) and fold in journal order → 2 CORRECTS edges live AND
    rebuilt (distinct corrected_by). Stamps/updatedAt = the second (last)
    invalidate's journaled ts — exact live parity."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    c = sdk.create_point("statement", "C", status="live")["id"]
    sdk.invalidate_point(a, b)
    sdk.invalidate_point(a, c)
    proj = sdk._get_proj()
    pre = _point_state(sdk, a)
    assert _corr(proj, a, b) == 1 and _corr(proj, a, c) == 1
    assert _corr_total(proj, a) == 2
    _rebuild(sdk, events)
    post = _point_state(sdk, a)
    assert post == pre, f"double-invalidate drifted across rebuild: {pre} != {post}"
    assert _corr(proj, a, b) == 1 and _corr(proj, a, c) == 1
    assert _corr_total(proj, a) == 2, "both CORRECTS must survive rebuild"


def test_double_invalidate_same_corrector_single_corrects(sup):
    """invalidate(A,B) twice (re-assert, live-legal): the MERGE keeps ONE
    CORRECTS edge live and rebuilt."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    b = sdk.create_point("statement", "B", status="live")["id"]
    sdk.invalidate_point(a, b)
    sdk.invalidate_point(a, b)
    proj = sdk._get_proj()
    pre = _point_state(sdk, a)
    assert _corr_total(proj, a) == 1
    _rebuild(sdk, events)
    post = _point_state(sdk, a)
    assert post == pre, f"re-assert invalidate drifted across rebuild: {pre} != {post}"
    assert _corr_total(proj, a) == 1, "re-assert must not mint parallel CORRECTS"


# ═══════════════════════════════════════════════════════════════════════
# Id-reuse (RAW hard-delete lane): a raw producer deletes + re-creates an id
# (PointAdded re-emission) between folds — pre-recreation folds are dropped
# (their stamps/CORRECTS died with the deleted node)
# ═══════════════════════════════════════════════════════════════════════

def test_id_reuse_drops_pre_recreation_invalidate_fold(sup):
    """invalidate(A,B) → [raw producer hard-deletes + re-creates A] →
    invalidate(A,C). Only the POST-recreate fold is live-truth — rebuild must
    drop the pre-recreate fold entirely (no ghost CORRECTS B→A, no older
    stamps) and fold the post-recreate one verbatim. Uses the RAW hard-delete
    lane (NOT SDK delete_point — that emits PointRetracted, which tombstones
    the fresh incarnation pre-sweep and breaks the id-reuse premise)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A v1", status="live")["id"]
    b = sdk.create_point("statement", "B (v1 corrector)", status="live")["id"]
    c = sdk.create_point("statement", "C (v2 corrector)", status="live")["id"]
    sdk.invalidate_point(a, b)          # seq: pre-recreate fold
    t2 = "2026-09-08T12:00:00+00:00"
    # Raw producer: hard-delete + re-create the SAME id (fresh live node).
    _raw_append(events, sdk, "PointAdded", point={
        "id": a, "content": "A v2 (recreated)", "status": "live",
        "pointKind": "statement",
    })
    # Raw producer: invalidate the fresh incarnation.
    _raw_append(events, sdk, "PointInvalidated", id=a, corrected_by=c,
                ts=t2, valid_to=t2, expired_at=t2)
    proj = sdk._get_proj()
    # Live (SDK world): only the v1 invalidate applied — the raw events never
    # touched the graph. The JOURNAL (raw producer's truth) is authoritative
    # on rebuild.
    assert sdk.get_point(a)["outdated"] is True
    _rebuild(sdk, events)
    post = sdk.get_point(a) or {}
    assert post.get("outdated") is True, "post-recreate invalidate must fold"
    assert post.get("status") == "live"
    assert post.get("validTo") == t2 and post.get("expiredAt") == t2
    assert post.get("updatedAt") == t2, (
        "post-recreate fold is the id's last journal writer — journaled ts")
    assert _corr(proj, a, c) == 1, "post-recreate CORRECTS must fold"
    assert _corr(proj, a, b) == 0, (
        "pre-recreation invalidate fold must be dropped (ghost CORRECTS)")
    assert _corr_total(proj, a) == 1


def test_reemission_same_id_after_invalidate_resurrects_live(sup):
    """Ambiguity pin: a raw producer re-emits a same-id PointAdded AFTER an
    invalidate with no further invalidate. The rebuild follows the raw
    producer's re-creation (the journal is authoritative): the pre-recreation
    invalidate fold is dropped and the rebuilt node is a clean live point —
    while the SDK-live node (which only ever saw the invalidate) stays
    outdated. Divergence pinned + documented; the survivor rule treats the
    PointAdded as a re-creation boundary."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A v1", status="live")["id"]
    b = sdk.create_point("statement", "B (corrector)", status="live")["id"]
    sdk.invalidate_point(a, b)
    assert sdk.get_point(a)["outdated"] is True, "live stays outdated"
    # Raw producer re-creates the SAME id (no subsequent invalidate).
    _raw_append(events, sdk, "PointAdded", point={
        "id": a, "content": "A v2 (recreated)", "status": "live",
        "pointKind": "statement",
    })
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    post = sdk.get_point(a) or {}
    assert post.get("outdated") is not True, (
        "rebuilt follows the raw re-creation — clean live node (no outdated)")
    assert post.get("status") == "live"
    assert _corr(proj, a, b) == 0, "pre-recreation CORRECTS died with the node"


# ═══════════════════════════════════════════════════════════════════════
# Invalidate-on-draft → promote: PointPromoted is NOT a drop boundary (the
# pre-promote invalidate fold survives) and the updatedAt seq-gate
# (skip_updated_at) fires
# ═══════════════════════════════════════════════════════════════════════

def test_invalidate_on_draft_then_promote_preserves_fold(sup):
    """invalidate(draft A, B) then promote(A): live-legal — invalidate is a
    flag write (no status guard) and the promote CAS is draft→live only. On
    rebuild, PointAdded seeds the draft node, the PointPromoted snapshot
    re-applies inline (stamping updatedAt = rebuild-now), and the deferred
    invalidate fold must SURVIVE (promote is NOT a drop boundary — seeding
    from it would silently drop the outdated flag + CORRECTS). The seq-gate
    fires (max_inline_seq[A] > the invalidate's seq): the fold does NOT
    clobber the promote's newer stamp with the older journaled invalidate ts
    — rebuilt updatedAt is the promote fold's rebuild-now stamp. Exact
    promote-time parity is out of scope (#2488 known limitation — pass-1a
    stamps rebuilt nodes at rebuild-now)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "draft A", status="draft")["id"]
    b = sdk.create_point("statement", "B (corrector)", status="live")["id"]
    sdk.invalidate_point(a, b)
    inv_ts = (sdk.get_point(a) or {}).get("validTo")
    assert inv_ts, "invalidate must stamp the draft"
    sdk.promote_point(a)
    live = sdk.get_point(a) or {}
    assert live.get("status") == "live"
    assert live.get("outdated") is True, "promote must not clear the flag"
    assert live.get("validTo") == inv_ts
    proj = sdk._get_proj()
    assert _corr(proj, a, b) == 1
    _rebuild(sdk, events)
    post = sdk.get_point(a) or {}
    assert post.get("outdated") is True, (
        "pre-promote invalidate fold must survive (promote is not a boundary)")
    assert post.get("status") == "live"
    assert post.get("validTo") == inv_ts
    assert post.get("expiredAt") == inv_ts
    assert _corr(proj, a, b) == 1
    assert post.get("updatedAt") != inv_ts, (
        "skip_updated_at must fire: the fold must not clobber the promote "
        "stamp with the OLDER invalidate ts")
    assert post.get("updatedAt") and post.get("updatedAt") > inv_ts, (
        "rebuilt updatedAt sits in the rebuild epoch (post-invalidate)")


# ═══════════════════════════════════════════════════════════════════════
# corrected_by-terminalized / missing endpoint: CORRECTS MERGE silently
# skips; the fold still applies (matched ≥ 1); no fold-miss warning
# ═══════════════════════════════════════════════════════════════════════

def test_corrected_by_missing_endpoint_fold_still_applies(sup, caplog):
    """A journaled PointInvalidated whose corrected_by endpoint never
    re-existed (raw producer referencing a hard-deleted corrector): the fold
    MATCHes the old point (outdated + stamps apply, matched ≥ 1) and the
    CORRECTS MERGE no-ops silently — parity with #2423's missing-successor
    handling. NO 'matched no Point' fold-miss warning (that fires only for a
    0-row fold — an absent OLD point)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "A", status="live")["id"]
    t = "2026-09-08T12:00:00+00:00"
    _raw_append(events, sdk, "PointInvalidated", id=a,
                corrected_by="ghost-corrector-never-created",
                ts=t, valid_to=t, expired_at=t)
    proj = sdk._get_proj()
    _rebuild(sdk, events)
    post = sdk.get_point(a) or {}
    assert post.get("outdated") is True, "fold must still apply to the old point"
    assert post.get("validTo") == t and post.get("expiredAt") == t
    assert post.get("status") == "live"
    assert _corr_total(proj, a) == 0, "CORRECTS MERGE silently skipped"
    assert "PointInvalidated fold matched no Point" not in caplog.text, (
        "missing corrected_by endpoint is NOT a fold-miss (matched >= 1)")


def test_revise_after_invalidate_skip_updated_at(sup):
    """code-review P2-1: the PointRevised seq-gate leg — a same-id revise
    LATER than an invalidate is the newer writer; the sweep fold must omit
    updatedAt (skip_updated_at via max_inline_seq) so the revise's rebuild
    stamp survives, while outdated/validTo/expiredAt/CORRECTS still fold."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "revise-me", status="live")["id"]
    b = sdk.create_point("statement", "corrector", status="live")["id"]
    sdk.invalidate_point(a, corrected_by_id=b)
    # Live revise AFTER the invalidate — a newer same-id writer. (Plain-prop
    # update_point does NOT re-stamp updatedAt live — the non-Object branch
    # writes per-key n += $props — so the live updatedAt still reads the
    # invalidate ts here; the revise's rebuild-now stamp is what pass-1b
    # applies, and the seq-gate must let it survive the older fold ts.)
    sdk.update_point(a, props={"note": "revised after invalidate"})
    inv_ts = (sdk.get_point(a) or {}).get("updatedAt")
    assert inv_ts, "invalidate stamps updatedAt"
    assert (sdk.get_point(a) or {}).get("outdated") is True

    _rebuild(sdk, events)
    proj = sdk._get_proj()
    post = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.outdated, n.validTo, n.expiredAt, "
        "n.updatedAt, n.status",
        params={"id": a}).result_set[0]
    assert post[0] is True, "outdated survived rebuild (always folds)"
    assert post[4] == "live", "no status change"
    assert post[3] != inv_ts, (
        "updatedAt must NOT regress to the older invalidate ts "
        "(skip_updated_at fired — the revise is the newer writer)"
    )
    assert post[3] > inv_ts, (
        "rebuilt updatedAt sits in the rebuild epoch (post-invalidate): "
        "the revise's rebuild-now stamp survived the fold"
    )
    assert _corr_total(proj, a) == 1, "CORRECTS still folded"


def test_invalidate_raw_producer_no_corrected_by_flag_still_folds(sup):
    """code-review P2-2: a RAW producer emitting PointInvalidated WITHOUT a
    corrected_by key must still get the outdated flag + stamps (the #2488 fix
    needs only oid) — only the CORRECTS arm is gated. Regression guard: the
    flag skip would resurrect the ghost."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "raw-target", status="live")["id"]
    t = "2026-09-08T12:00:00+00:00"
    # Raw producer line with NO corrected_by key at all.
    _raw_append(events, sdk, "PointInvalidated", id=a, ts=t, valid_to=t,
                expired_at=t)
    _rebuild(sdk, events)
    proj = sdk._get_proj()
    post = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.outdated, n.validTo, n.status",
        params={"id": a}).result_set[0]
    assert post[0] is True, (
        "outdated flag must fold even without corrected_by (raw producer lane)"
    )
    assert post[2] == "live", "no status write"
    assert _corr_total(proj, a) == 0, "no CORRECTS without corrected_by (by design)"
