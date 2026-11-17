"""#13084 — the census tool's refusals, pinned without a store.

The measurement halves are covered by `test_13084_demand_census.py` (the ledger
fold) and by the run recorded on the PR (the restore probe against a lane
container). What must not drift here is the set of things the tool REFUSES to do:
a union of tenants, and a destructive restore probe that was not confirmed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO / "tools"))

import ram_window_census as census  # noqa: E402


def test_a_ledger_without_a_tenant_is_a_usage_error(capsys):
    """`f` and the mix are PER TENANT; a pooled read is a wrong number."""
    assert census.main(["--ledger", "/tmp/whatever.jsonl"]) == census.EXIT_USAGE
    err = capsys.readouterr().err
    assert "PER TENANT" in err


def test_the_restore_probe_refuses_without_an_exact_confirm(tmp_path, capsys):
    """DESTRUCTIVE between DEL and RESTORE: a dry run names the confirm it needs."""
    out = tmp_path / "r.json"
    rc = census.main(["--restore-probe", "--graph-uri", "docker://h:1/g",
                      "--graph", "g", "--json"])
    assert rc == census.EXIT_UNMEASURABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload["cold_restore"]["dry_run"] is True
    assert "--confirm-graph 'g'" in payload["cold_restore"]["error"]
    # and the recorded reference is quoted as CONTEXT, never as this graph's figure
    assert (payload["cold_restore"]["recorded_reference"]["index_dropped_s"]
            == 0.03)
    assert not out.exists()


def test_a_restore_probe_without_a_graph_uri_is_a_usage_error(capsys):
    assert census.main(["--restore-probe", "--graph", "g"]) == census.EXIT_USAGE
    assert "requires --graph-uri" in capsys.readouterr().err
