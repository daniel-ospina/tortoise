"""#9754 — re-mitigating onto an id-less mitigation Point must REFUSE, not no-op.

The defect (measured on ``origin/main``): ``mitigate_operator``'s idempotency
read is ``MATCH (op)-[:mitigated_by]->(m) RETURN m.id``. A hand-written /
imported mitigation ``:Point`` carries no ``id``, so ``mid`` is ``None`` and
EVERY write in the branch is id-keyed (``update_point``, the baseline writer,
the word stamp) — all of them match zero rows. The call then returned
``self.get_point(None)`` → ``{}``, an empty success a caller cannot distinguish
from "there is no mitigation": the reason/strength update did not land, no
baseline was written, and EP's calibration gate then refuses the operator while
``calibrate_summary``'s advice (``set_point_baseline(None, …)``) cannot be
executed.

#9262 guarded this branch (``if mid is None: return self.get_point(mid)``) to
avoid turning the silent drop into an abort mid-``commit_ops`` write loop. That
guard kept the FALSE PASS; this is the actual repair, option 1 of the issue:
**refuse with the real cause** — naming the MITIGATION, not the operator the
caller passed — and make ``apply_payload_operators`` classify it per item so one
unaddressable mitigation cannot abort a whole commit's write loop.
"""
import os  # noqa: I001
import shutil

import pytest
from tortoise.exceptions import UnaddressableClaimError
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """Isolated embedded SDK (falkordblite, no Docker) — #398 pattern."""
    import tempfile
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_9754_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _impl_chain_with_idless_mitigation(sdk, label="op"):
    """A REAL IMPL chain whose operator carries an id-less mitigation :Point.

    Returns ``(op_id, src_id, dst_id)``. The chain is a genuine
    ``create_operator`` pair so ``apply_payload_operators`` resolves it through
    its own MITIGATES target probe rather than dropping the record before it
    ever reaches ``mitigate_operator``.
    """
    g = sdk._get_proj().g
    src = sdk.create_point("statement", f"{label} source", status="live")
    dst = sdk.create_point("statement", f"{label} claim", status="live")
    op_id = sdk.create_operator("IMPL", src["id"], [dst["id"]])["id"]
    g.query(
        "MATCH (op:Point {id:$id}) CREATE (m:Point {pointKind:'statement', "
        "content:'[MITIGATION] legacy', status:'live', is_operator:false})"
        "-[:IMPL]->(op), (op)-[:mitigated_by]->(m)", params={"id": op_id})
    return op_id, src["id"], dst["id"]


def test_remitigation_onto_idless_mitigation_refuses_and_names_the_mitigation(sdk):
    """The call must raise, naming the MITIGATION — never return a silent ``{}``."""
    op_id, _src, _dst = _impl_chain_with_idless_mitigation(sdk)
    g = sdk._get_proj().g
    assert g.query(
        "MATCH (op:Point {id:$id})-[:mitigated_by]->(m:Point) RETURN m.id",
        params={"id": op_id}).result_set == [[None]]

    with pytest.raises(UnaddressableClaimError) as excinfo:
        sdk.mitigate_operator(op_id, "second reason", strength=0.5)

    msg = str(excinfo.value)
    # The remedy must diagnose the node that is actually unaddressable — the
    # mitigation — because the caller named the OPERATOR and cannot act on a
    # message about it. #9262's failure mode was a message that read as "the
    # operator (or a node named `None`) is wrong".
    assert "mitigation Point with no id" in msg, msg
    assert "not found" not in msg and "is not an operator" not in msg, (
        f"the refusal reads as a bad OPERATOR rather than an unaddressable "
        f"MITIGATION: {msg}")

    # And nothing was written under the false success the guard used to return.
    assert g.query(
        "MATCH (op:Point {id:$id})-[:mitigated_by]->(m:Point) "
        "RETURN count(m)", params={"id": op_id}).result_set == [[1]]
    assert g.query(
        "MATCH (m:Point) WHERE m.content = '[MITIGATION] second reason' "
        "RETURN count(m)").result_set == [[0]]


def test_commit_ops_skips_an_unaddressable_mitigation_per_item(sdk):
    """One unaddressable mitigation must not abort the whole write loop.

    The owner's prescription for #9754 option 1 is explicit: refuse, "with
    ``commit_ops`` classifying it per item rather than aborting the loop". The
    second MITIGATES record in the same payload must still land.
    """
    from tortoise.commit_ops import apply_payload_operators

    broken_op, broken_src, broken_dst = _impl_chain_with_idless_mitigation(
        sdk, label="broken")
    # A second, clean IMPL chain for the second MITIGATES record.
    src = sdk.create_point("statement", "clean source", status="live")
    claim = sdk.create_point("statement", "clean claim", status="live")
    clean_op = sdk.create_operator("IMPL", src["id"], [claim["id"]])["id"]
    g = sdk._get_proj().g

    ops = [
        {"op_type": "MITIGATES", "src": "pt_reason_broken", "strength": 0.5,
         "target": {"op_type": "IMPL", "src": broken_src,
                    "dst": broken_dst}},
        {"op_type": "MITIGATES", "src": "pt_reason_clean", "strength": 0.5,
         "target": {"op_type": "IMPL", "src": src["id"],
                    "dst": claim["id"]}},
    ]
    # Must NOT raise: the unaddressable item is a per-item skip.
    apply_payload_operators(
        sdk._get_proj(), sdk, ops,
        point_content_by_id=lambda pid: f"reason for {pid}")

    # The second mitigation landed (its own CURRENT mitigation exists)…
    assert g.query(
        "MATCH (o:Point {id:$id})-[:mitigated_by]->(m:Point) "
        "RETURN count(m)", params={"id": clean_op}).result_set == [[1]], (
        "the unaddressable first item aborted the loop — the second MITIGATES "
        "record never landed")
    # …and the broken operator still carries exactly its one legacy node (no
    # duplicate was minted; a skip is a skip).
    assert g.query(
        "MATCH (o:Point {id:$id})-[:mitigated_by]->(m:Point) "
        "RETURN count(m)", params={"id": broken_op}).result_set == [[1]]
