"""#5365 — a fail-open supersession skip must be VISIBLE, not just warned.

`commit_ops.apply_supersessions` and `sdk.promote_point` both deliberately
swallow a failed supersession rather than abort their caller (a bad record must
not abort a whole ingest; a promotion must not be lost to a wire that cannot be
made). #4021 made that acute: a retroactive supersession that previously
SUCCEEDED — while writing an inverted predecessor window — now RAISES into those
catches, so the record became silently unapplied and the only trace was a
callback warning that a caller not reading logs never sees.

These tests pin the two visibility channels that fix it. They deliberately do
NOT pin a fail-CLOSED posture: propagating the refusal would reverse a
documented, deliberate decision for these consumers.
"""

import pytest

from tortoise.ids import content_hash
from tortoise.live import is_terminal_status
from tortoise.sdk import TortoiseSDK


@pytest.fixture()
def sdk(tmp_path):
    return TortoiseSDK(db_path=str(tmp_path / "t.db"))


def _props(sdk, point_id):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": point_id}).result_set
    return rows[0][0] if rows else {}


def _pt(sdk, tag, valid_from):
    """Create a Point carrying a CANONICAL ``pt_``-prefixed id.

    The prefix is what routes a supersession record to the POINT lane in
    ``apply_supersessions``. A default ``create_point`` id does not carry it,
    so the record falls to the ENTITY lane and is skipped as a dangling
    successor — which would make these tests pass for the wrong reason (the
    skip would be a dangling-successor skip, not the #4021 refusal).
    """
    content = f"5365 {tag}"
    return sdk.create_point("statement", content, id=f"pt_{content_hash(content)}",
                            validFrom=valid_from)


def _inverted_pair(sdk, tag):
    """A (predecessor, successor) pair whose supersession #4021 REFUSES.

    The successor's resolved window start precedes the predecessor's, so
    ``supersede_point`` raises before any mutation. This is the exact input
    class that #4021 turned from "succeeds with a bad window" into "raises".
    """
    return (_pt(sdk, f"{tag} old", "2026-06-10"),
            _pt(sdk, f"{tag} new", "2026-06-01"))


def test_apply_supersessions_records_a_refused_supersede_as_skipped(sdk):
    """The refusal lands in ``skipped``, and the predecessor is untouched."""
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    old, new = _inverted_pair(sdk, "refused")

    skipped = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": old["id"], "supersedes_by": new["id"],
          "evidence": "retroactive successor"}],
        session_id="sess_5365_refused", skipped=skipped)

    assert applied == 0, "a refused supersession must not count as applied"
    assert len(skipped) == 1, f"the refusal was not recorded: {skipped!r}"
    assert skipped[0]["ref"] == old["id"]
    assert skipped[0]["successor"] == new["id"]
    assert "supersede refused" in skipped[0]["reason"], skipped[0]

    # The fail-open posture is PRESERVED: the record is skipped, not fatal,
    # and the predecessor keeps its window (no half-write).
    assert "validTo" not in _props(sdk, old["id"]), \
        "the refused supersession must not have written a window end"


def test_apply_supersessions_skipped_defaults_to_none_and_is_backwards_safe(sdk):
    """``skipped`` is OPT-IN: omitting it must change nothing observable.

    The return contract is an ``int`` and existing callers rely on it, so the
    new channel must not alter the default path — including that a refusal
    still does not propagate.
    """
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    old, new = _inverted_pair(sdk, "optin")

    # No `skipped=` — must not raise, must still return the int.
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": old["id"], "supersedes_by": new["id"],
          "evidence": "retroactive successor"}],
        session_id="sess_5365_optin")
    assert applied == 0
    assert isinstance(applied, int)


def test_apply_supersessions_skipped_lists_only_unapplied_records(sdk):
    """A clean record applies and is ABSENT from ``skipped``.

    The anti-vacuity companion: a channel that recorded every record would
    satisfy the refusal test above while being useless to a caller.
    """
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    good_old = _pt(sdk, "good old", "2026-06-01")
    good_new = _pt(sdk, "good new", "2026-06-10")
    bad_old, bad_new = _inverted_pair(sdk, "mixed")

    skipped = []
    applied = apply_supersessions(
        proj, sdk,
        [
            {"superseded": good_old["id"], "supersedes_by": good_new["id"],
             "evidence": "forward-dated successor"},
            {"superseded": bad_old["id"], "supersedes_by": bad_new["id"],
             "evidence": "retroactive successor"},
        ],
        session_id="sess_5365_mixed", skipped=skipped)

    assert applied == 1, "the forward-dated record must still apply"
    assert [s["ref"] for s in skipped] == [bad_old["id"]], \
        f"only the unapplied record belongs in skipped: {skipped!r}"
    # and the good one really did land
    assert _props(sdk, good_old["id"]).get("validTo") == "2026-06-10"


def test_apply_supersessions_dead_sibling_makes_the_ref_a_noop(sdk):
    """#7822: the pt_ terminal pre-check scans EVERY node carrying the ref id.

    The pre-#7822 ``{r[0]: (status, outdated)}`` comprehension was
    last-row-wins, so a duplicate ref id with one live and one terminal node
    made this pre-check disagree with the guard ``sdk.supersede`` runs (which
    refuses on ANY terminal row): the pre-check said "not terminal", attempted
    the write, and the guard's refusal turned the documented idempotent no-op
    into a warned, recorded skip.

    The dead sibling is written FIRST (a raw CREATE — a later property write
    moves a node to the end of the scan order), and the live one SECOND, so the
    LAST row the fold's statement returns is LIVE and the pre-fix fold read it
    — the failure is deterministic here, and the premise assertion below reds
    loudly if that order ever changes.
    """
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    content = "dupsib old"
    old_id = f"pt_{content_hash(content)}"
    new_id = f"pt_{content_hash(content)}_new"
    proj.g.query(
        "CREATE (n:Point {id:$id, content:'dupsib dead sibling', "
        "pointKind:'statement', status:'retracted'})", params={"id": old_id})
    sdk.create_point("statement", content, id=old_id, status="live",
                     validFrom="2026-06-01")
    sdk.create_point("statement", content + " new", id=new_id,
                     status="live", validFrom="2026-06-10")
    dup_states = [s for s in proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, n.status, coalesce(n.outdated, false)",
        params={"ids": [old_id, new_id]}).result_set if s[0] == old_id]
    assert len(dup_states) == 2, dup_states
    assert any(is_terminal_status(s[1], bool(s[2])) for s in dup_states), \
        dup_states
    # Premise, asserted rather than assumed: the LIVE duplicate is the LAST row,
    # so the pre-#7822 last-row-wins fold would have said "not terminal".
    assert not is_terminal_status(dup_states[-1][1], bool(dup_states[-1][2])), \
        dup_states

    warns: list = []
    skipped: list = []
    applied = apply_supersessions(
        proj, sdk,
        [{"superseded": old_id, "supersedes_by": new_id,
          "evidence": "duplicate id"}],
        session_id="sess_5365_dup", warn=warns.append, skipped=skipped)
    assert applied == 0
    assert skipped == [], (
        f"a dead sibling is the documented idempotent no-op, not an unapplied "
        f"record: {skipped!r}")


def test_apply_supersessions_summary_warn_sizes_the_skips(sdk):
    """ONE summary warn carries the applied/skipped counts.

    Mirrors the existing #5654 summary: the point is that a caller reading the
    warning stream sees the batch was PARTIAL, without one line per record.
    """
    from tortoise.commit_ops import apply_supersessions

    proj = sdk._get_proj()
    old, new = _inverted_pair(sdk, "warn")

    warns = []
    skipped = []
    apply_supersessions(
        proj, sdk,
        [{"superseded": old["id"], "supersedes_by": new["id"],
          "evidence": "retroactive successor"}],
        session_id="sess_5365_warn", warn=warns.append, skipped=skipped)

    joined = "\n".join(warns)
    assert "SKIPPED" in joined, f"no skip summary in the warn stream: {warns!r}"
    assert "1 SKIPPED" in joined, joined
    assert "0 applied" in joined, joined


def test_promote_point_reports_a_supersede_skipped_in_its_result(sdk):
    """``promote_point``'s response carries the skip, not just a log line.

    Same decision, second site: the promotion PROCEEDS, so the response is the
    only place a caller can learn the temporal wire did not land.
    """
    pytest.importorskip("tortoise.sdk")
    import inspect

    src = inspect.getsource(type(sdk).promote_point)
    # Pin the contract at the source level: the field is written only on the
    # skip path, so an end-to-end assertion requires a draft point carrying
    # temporal_replacement plus a live target whose window refuses — the
    # helper-level contract is what this issue needs pinned.
    assert "supersede_skipped" in src, \
        "promote_point no longer reports an unapplied supersession"
    assert "supersede_skipped_count" in src, \
        "promote_point must size the skip, not only list it"


def test_promote_point_skips_a_temporal_target_with_a_dead_sibling(sdk):
    """#7822: promote_point's temporal-target probe scans EVERY node carrying
    the target id.

    The pre-#7822 probe read ``trow[0]``, so a duplicate target id whose LIVE
    node was returned first NAND-wired a target the shared lifecycle predicate
    calls dead — and that the guard would refuse to supersede. The fixture
    writes the LIVE duplicate first, so a first-row-only read sees it: the
    failure is deterministic here, and the premise assertion below reds loudly
    if that order ever changes.
    """
    proj = sdk._get_proj()
    g = proj.g
    tgt = "pt_temporal_dup_7822"
    g.query(
        "CREATE (n:Point {id:$id, content:'live sibling', "
        "pointKind:'statement', status:'live'})", params={"id": tgt})
    g.query(
        "CREATE (n:Point {id:$id, content:'dead sibling', "
        "pointKind:'statement', status:'retracted'})", params={"id": tgt})
    rows = g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status, coalesce(n.outdated, false)",
        params={"id": tgt}).result_set
    assert len(rows) == 2, rows
    # The premise, asserted rather than assumed: a first-row-only read sees the
    # LIVE sibling.
    assert not is_terminal_status(rows[0][0], bool(rows[0][1])), rows
    draft = sdk.create_point(
        "statement", "draft candidate", status="draft",
        temporal_candidate=True, temporal_reviewed="merge",
        temporal_target_ids=[tgt])["id"]

    result = sdk.promote_point(draft)

    assert result["promoted"] is True, result
    assert "temporal_wired" not in result, result
    assert g.query(
        "MATCH (op:Point {is_operator:true})-[:NAND]->(:Point {id:$t}) "
        "RETURN count(*)", params={"t": tgt}).result_set[0][0] == 0, (
        "a target with a terminal sibling must not be NAND-wired")
