"""SCRATCH PROBE (not committed) — verify the reviewer's P1 on PR #10162.

Shape: an EventRecorded mention stub creates an Object node by name; the stub is
still ALIVE at the journal's end; an ObjectSuperseded names it. Expectation to
test: rebuild_all folds it cleanly while check_consistency records a miss.
"""
from __future__ import annotations

import pytest

from tests.test_rebuild_parity_fail_loud_3585 import _raw  # noqa: E402
from tortoise.consistency import check_consistency  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402


@pytest.fixture()
def env(tmp_path):
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "l6.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def test_probe_mention_stub_alive_then_name_supersede(env):
    sdk, events = env
    _raw(events, type="EventRecorded", id="e-m1", eventKind="k",
         object="ACME-ALIVE", event_id="e-m-rec")
    _raw(events, type="ObjectSuperseded", name="ACME-ALIVE",
         supersedes_by="y", event_id="e-m-sup")
    proj = sdk._get_proj()
    accepted, err = True, None
    try:
        proj.rebuild_all(str(events), confirm_destructive=True)
    except Exception as exc:  # noqa: BLE001
        accepted, err = False, f"{type(exc).__name__}: {exc}"
    nodes = proj.g.query(
        "MATCH (o:Object) RETURN o.id, o.name, o.status").result_set
    r = check_consistency(str(events / "events.jsonl"), proj)
    print(f"\nPROBE rebuild_all accepted={accepted} err={err}")
    print(f"PROBE object nodes={nodes}")
    print(f"PROBE non_folded_refused_count={r['non_folded_refused_count']}")
    print(f"PROBE non_folded_events={r['non_folded_events']}")
    print(f"PROBE divergence={r['divergence']}")
    assert accepted, err
    assert r["non_folded_refused_count"] == 0, r["non_folded_events"]
