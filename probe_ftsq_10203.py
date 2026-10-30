import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tests._assembly_graph as ag
from tests.test_assembly_sdk import Q_DATE, _install_fake, FALKORDB_AVAILABLE  # noqa
from tests import _live_utils
from tortoise.ask_lane import run_ask_lane
from tortoise.sdk import TortoiseSDK
import pytest

Q = "compare the couch and the dog bed, which should i keep?"


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="docker lane")
def test_dump(monkeypatch):
    import tortoise.sparse as sp
    calls = []
    orig = sp.build_or_query

    def wrapped(query, **kw):
        out = orig(query, **kw)
        calls.append((query, out))
        return out

    monkeypatch.setattr(sp, "build_or_query", wrapped)
    monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
    monkeypatch.setenv("TORTOISE_ASK_SEARCH_KEYS_PRF", "1")
    sdk = TortoiseSDK()
    ag.build_base_graph(sdk)
    _install_fake(sdk, monkeypatch, reply="GOLD")
    res = run_ask_lane(sdk, Q, question_date=Q_DATE,
                       question_type=None)
    print("\n=== FTS QUERIES ===")
    for q, out in calls:
        print(repr(q), "->", out)
    print("=== EVIDENCE ===")
    print(res["evidence"])
