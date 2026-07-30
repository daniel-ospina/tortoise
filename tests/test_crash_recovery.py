"""GAP-14b #7002: Crash recovery — minimal restart test.

Creates 10 Points → checkpoints → simulates crash (close/reopen) →
verifies state recovery from persisted graph.
"""
import os
import tempfile

from tortoise.sdk import TortoiseSDK


def test_crash_recovery_restart():
    db = os.path.join(tempfile.mkdtemp(), "test.db")
    sdk = TortoiseSDK(db)

    points = [sdk.create_point("statement", f"point-{i}") for i in range(10)]
    sdk.checkpoint([{"content": p["content"], "wing": "test", "room": "crash"}
                    for p in points])
    sdk.close()

    # ── simulate crash: reopen same db ──
    sdk2 = TortoiseSDK(db)
    recovered = sdk2.query(kind="statement")
    assert len(recovered) == 10
    recovered_ids = {r["id"] for r in recovered}
    original_ids = {p["id"] for p in points}
    assert recovered_ids == original_ids
    sdk2.close()
