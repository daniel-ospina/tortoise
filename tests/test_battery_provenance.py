"""#2291 I-5 provenance + regression hardening tests.

Seeded-vs-agent provenance distinction on the real path; event-log
correlation (per-scenario, ns:seq event ids per #2293 — correlation only,
never a second write channel); excluded-residue/accumulation policy on the
clean seed_mode graph (re-setup accumulates; stale pre-fix refuses).
"""
from __future__ import annotations

from pathlib import Path

from battery.arms.base import AgentContext, Memory
from battery.runner.setup import scenario_namespace, seed_manifest_content
from battery.testing.seeds import seed_full_legacy, setup_seed_mode
from battery.exceptions import ConfigError

import pytest


def _store(tmp_path, sid: str = "ct-001"):
    return setup_seed_mode(tmp_path, sid)


def test_seeded_vs_agent_provenance_distinct(tmp_path) -> None:
    """Seed points carry seed:true + harness provenance; agent-filed
    evidence carries its own provenance (source_harness/source_session) and
    never seed:true — the two populations are distinguishable on the real
    path (Task-9 executor tags ride this seam)."""
    store = _store(tmp_path)
    try:
        sdk = store._arm._sdk(store._scenario)
        mems = store.retrieve("")
        seed_pts = [m for m in mems]
        assert seed_pts
        for m in seed_pts:
            pt = sdk.get_point(m.id)
            if pt.get("seed") is True:
                assert pt.get("source_harness") == "battery"
                assert pt.get("source_session") == store._scenario.id
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=tuple(mems), user_message="go"),
            Memory(id="e1", content="agent filed finding", kind="nand"))
        # find the agent evidence by content
        rows = sdk.tortoise_fts_query("agent filed finding", limit=5)
        agent = [p for p in rows if p.get("content") == "agent filed finding"]
        assert agent
        pt = sdk.get_point(agent[0]["id"])
        assert pt.get("seed") is not True
        assert pt.get("source_harness") == "battery"
        assert pt.get("source_session") == store._scenario.id
    finally:
        store.close()


def test_event_log_correlation_per_scenario(tmp_path) -> None:
    """The SDK event log is per-scenario under the run dir and records the
    scenario's operations (PointAdded/OperatorAdded + provenance) — a
    correlation channel only (#2293 alignment; never a second write
    channel). Event ordering determinism is owned by #2293; this test locks
    the per-scenario file + that agent ops ride the SAME stream."""
    store = _store(tmp_path)
    try:
        sdk = store._arm._sdk(store._scenario)
        assert sdk._event_log_path  # noqa: SLF001
        log = Path(sdk._event_log_path)  # noqa: SLF001
        assert log.exists(), "per-scenario event log missing"
        before = log.read_text(errors="replace")
        assert "PointAdded" in before
        # an agent write appends to the SAME stream (correlation, not a
        # parallel write channel)
        mems = store.retrieve("")
        store._arm.record(
            AgentContext(scenario=store._scenario, episode_seed=0,
                         prior_memories=tuple(mems), user_message="go"),
            Memory(id="e1", content="event log finding", kind="nand"))
        after = log.read_text(errors="replace")
        assert "event log finding" in after or len(after) > len(before)
    finally:
        store.close()


def test_clean_graph_resetup_accumulates_not_duplicates(tmp_path) -> None:
    """Re-setup over a CLEAN seed_mode graph accumulates (no refuse, no
    duplicate claim_a) — the Task-10 stream default + excluded-residue
    policy (stale pre-fix refuses; agent content follows a marker)."""
    from battery.config.corpus import load_corpus
    corpus = load_corpus("battery/config/corpus.yaml")
    sc = next(s for s in corpus if s.id == "ct-001")
    ns = tmp_path / "acc"
    store1 = setup_seed_mode(ns, "ct-001")
    surface1 = store1.surface_text()
    store1.close()
    store2 = setup_seed_mode(ns, "ct-001")  # clean graph → accumulate
    try:
        surface2 = store2.surface_text()
        pair = sc.contradiction_pairs[0]
        assert pair.claim_a[:40] in surface2
        assert pair.claim_b[:40] not in surface2  # ¬A still never seeded
        assert surface2.count(pair.claim_a[:40]) == surface1.count(
            pair.claim_a[:40])  # no duplicate
    finally:
        store2.close()


def test_stale_pre_fix_still_refuses_after_accumulate_workflow(tmp_path
                                                               ) -> None:
    """End-to-end residue policy: seed_full_legacy (stale PRE-FIX) then a
    seed_mode setup in the SAME namespace refuses — the ingest-lane warm
    guard keys on the marker, never raw content presence."""
    ns = tmp_path / "residue"
    seed_full_legacy(ns, "ct-001")
    with pytest.raises(ConfigError, match="warm guard"):
        setup_seed_mode(ns, "ct-001", purge=False)
