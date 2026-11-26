import sys
from pathlib import Path
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
import tortoise.hosted_api as ha
import tortoise.mcp_auth as ma

def test_probe(monkeypatch):
    import tortoise.mcp_server as ms
    written = []
    monkeypatch.setattr(ha, "_record_capture_last_error", lambda *a, **k: written.append(a))
    refusal = ha.HTTPException(status_code=409, detail=ha._capture_session_in_flight_body(),
                               headers={"Retry-After": "30"})
    async def _raise(*a, **k):
        raise refusal
    monkeypatch.setattr(ha, "_capture_session_impl", _raise)
    monkeypatch.setattr(ha, "_observed_capture_harness", lambda *a, **k: "claude-code")
    async def _no_stored(*a, **k): return None
    monkeypatch.setattr(ha, "_stored_session_harness", _no_stored)
    tok = ma._current_org_id.set("org-probe-14700")
    try:
        out = ms.tortoise_session_capture([{"role": "user", "content": "hi"}],
                                          harness="claude", session_id="s-probe")
        print("PROBE RETURNED:", str(out)[:300])
    except Exception as e:
        print("PROBE RAISED:", type(e).__name__, str(e)[:300])
    finally:
        ma._current_org_id.reset(tok)
    print("PROBE written:", written)
