"""#1417 — aboutEvent untangle: capture-path provenance via eventId, not edges.

The ontology reserves ``aboutEvent`` for CONTENT ("What Event this describes",
ONTOLOGY §3.4). Before #1417 the three capture paths wrote point→source-event
with ``aboutEvent`` as PROVENANCE — silently merging "produced by X" with
"about X". Since #1417:

  - the capture path stamps the sessionCaptured Event's eventId onto the
    extracted Points (their provenance surface) and mints NO aboutEvent edges.
    ⚠️ That guarantee now lives on the HOSTED lane alone: `TortoiseSDK.capture_session`
    (the SDK half) was deleted by #4282, and its two tests went with it;
  - mining stamps the meeting Event's eventId onto its extraction Points the
    same way (DE2E-1 anchor);
  - the D10 subject fallback (fetch_point_epistemic_state) resolves the
    source-event via the point's eventId PROPERTY, so the promoted ``subject``
    keeps resolving for freshly captured points without any aboutEvent edge.

Existing aboutEvent edges in the graph are left in place (compat — semantic
reinterpretation, no migration).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK

TEST_GRAPH = "tortoise_test_1417_about_event_untangle"


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline MockModel session extractor (#822) — no provider key/network."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


@pytest.fixture()
def sdk(tmp_path):
    sdk = TortoiseSDK(str(tmp_path / "capture.db"), namespace=TEST_GRAPH)
    yield sdk
    try:
        sdk.test_guard()
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    sdk.close()


CONV = [
    {"role": "user", "content": "I think the auth dead-end is the top issue. "
                                "We decided to ship serve --http first."},
    {"role": "assistant", "content": "Agreed. Evidence suggests the website "
                                     "config is the root cause."},
    {"role": "user", "content": "ok"},
]


def _session_event(sdk):
    """Return the single sessionCaptured Event node's eventId."""
    rows = sdk._get_proj().g.query(
        "MATCH (e:Event {eventKind:'sessionCaptured'}) RETURN e.eventId"
    ).result_set
    assert len(rows) == 1, rows
    return rows[0][0]


def _scan(sdk, limit=100):
    """Full-scan query — runs the real decoration path (#1353 steps 4-9),
    including fetch_point_epistemic_state's D10 subject resolution. Full-scan
    requires a kind; the test stamps pointKind on the extracted points below."""
    return sdk.tortoise_fts_query(query=None, kind="statement",
                                  entity_type="point", limit=limit)



# ── mining: DE2E-1 session-occurrence anchor, provenance not content ──────

def test_mining_no_about_event_provenance(mining_sdk):
    """The mining path stamps the meeting Event's eventId onto its extraction
    Points and mints NO aboutEvent edge to the meeting Event (DE2E-1 anchor
    now rides the point's eventId property, not the content edge)."""
    import tortoise.mining as mining
    from tortoise.api import EventAPI
    from tortoise.log import EventLog

    sdk = mining_sdk
    proj = sdk._get_proj()
    log = EventLog(os.path.join(tempfile.mkdtemp(), "mine.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="t",
                   projection=proj)
    source_id = "session_1417_mining"
    result = mining.mine_conversation(
        "Alice: We decided to move the FalkorDB default port to 16379.\n"
        "Bob: I disagree because changing port 16379 breaks the redis config.\n"
        "Alice: But tortoise#123 tracks the migration work.\n",
        source_id, api)
    assert result["points"] > 0, "fixture transcript must produce points"
    meeting_event = f"meeting-{source_id}"

    no_edges = proj.g.query(
        "MATCH (e:Event {eventId:$eid})<-[:aboutEvent]-(p:Point) RETURN count(p)",
        params={"eid": meeting_event},
    ).result_set
    assert no_edges[0][0] == 0, \
        "mining must not mint aboutEvent-as-provenance edges"

    stamped = proj.g.query(
        "MATCH (p:Point) WHERE p.eventId = $eid RETURN count(p)",
        params={"eid": meeting_event},
    ).result_set
    assert stamped[0][0] == result["points"], \
        "every extraction Point must carry the meeting Event's eventId"


@pytest.fixture()
def mining_sdk():
    tmpdir = tempfile.mkdtemp(prefix="tortoise_mining_1417_")
    sdk = TortoiseSDK(os.path.join(tmpdir, "test.db"), namespace=TEST_GRAPH)
    yield sdk
    sdk.close()
    # #4096: reclaim this fixture's temp tree on teardown.
    shutil.rmtree(tmpdir, ignore_errors=True)
