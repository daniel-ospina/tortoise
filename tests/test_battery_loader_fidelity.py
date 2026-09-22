# tests/test_battery_loader_fidelity.py
"""Loader fidelity — per-field survival yaml→json→Scenario + single render
rule (issue #2284 T2). TWO legs: (a) sealed-JSON leg over corpus_loader
dicts (the reader surface stays dict-typed — NO conversion), (b) Scenario
leg over the run-path YAML loader on a tmp-authored no-gold mini corpus."""

from __future__ import annotations

import yaml

from battery.config.corpus import load_corpus as load_yaml_scenarios
from battery.config.corpus_loader import load_corpus as load_sealed_json
from battery.config.corpus_loader import render_reader_prompt


def _mini(tmp_path, *, extra_meta=None):
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    corpus = {
        "meta": {"corpus_version": "1.1", "sealed": True},
        "scenarios": [
            {
                "id": "cal-mini",
                "tier": "probe",
                "family": "R3",
                "task_type": "calibration",
                "attack_type": "cal",
                "split": "train",
                "evidence_tiers": [
                    {"tier": "T1", "claim": "green canary sings", "valence": "supports"}
                ],
                "prompt": {"system": "sys", "question": "is the canary green?", "turns": []},
            },
            {
                "id": "xs-mini",
                "tier": "stream",
                "family": "L4",
                "task_type": "cross_session_contradiction",
                "attack_type": "xs",
                "split": "train",
                "k": 2,
                "prompt": {
                    "system": "sys",
                    "session_scripts": [
                        {
                            "session": 1,
                            "question": "s1 question late-probe",
                            "turns": [{"role": "user", "content": "s1 turn"}],
                        },
                        {
                            "session": 2,
                            "question": "s2 question LATE-S2",
                            "turns": [{"role": "user", "content": "s2 turn"}],
                        },
                    ],
                },
            },
            {
                "id": "drift-mini",
                "tier": "stream",
                "family": "L5",
                "task_type": "decision_drift",
                "attack_type": "drift",
                "split": "train",
                "drift": {"decision": "opt", "offsets": ["7d", "21d"]},
                "prompt": {"system": "sys", "question": "still hold?", "turns": []},
            },
            {
                "id": "ret-mini",
                "tier": "probe",
                "family": "R5",
                "task_type": "retraction",
                "attack_type": "ret",
                "split": "train",
                "retraction": {"k": 2, "claim": "retracted claim"},
                "prompt": {"system": "sys", "question": "retracted?", "turns": []},
            },
            {
                "id": "lp-mini",
                "tier": "probe",
                "family": "R3",
                "task_type": "loopy_contested",
                "attack_type": "lp",
                "split": "train",
                "graph_script": {
                    "nodes": [
                        {"id": "p", "claim_or_turn_ref": 0},
                        {"id": "q", "claim_or_turn_ref": 1},
                        {"id": "r", "claim_or_turn_ref": 2},
                    ],
                    "nand_edges": [["p", "q"], ["q", "r"], ["r", "p"]],
                    "contested_pair": {
                        "a": "claim-a text",
                        "neg_a": "claim-b text",
                        "a_ref": "p",
                        "neg_a_ref": "q",
                    },
                },
                "prompt": {"system": "sys", "question": "loopy?", "turns": []},
            },
        ],
    }
    (cfg / "corpus.yaml").write_text(yaml.safe_dump(corpus), encoding="utf-8")
    return load_yaml_scenarios(cfg / "corpus.yaml"), corpus


def test_json_leg_fields_survive_seal():
    """Authored keys survive yaml -> sealed corpus.json (allowlist = ONLY
    gold redaction). corpus_loader stays dict-typed; no conversion."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    authored = yaml.safe_load((root / "battery/config/corpus.yaml").read_text())
    sealed = {sc["id"]: sc for sc in load_sealed_json().scenarios}
    for raw in authored["scenarios"]:
        sid = raw["id"]
        sc = sealed[sid]
        for key in raw:
            if key in ("gold",):  # gold -> gold_sha256 (intentional)
                continue
            assert key in sc, f"{sid}: authored key {key!r} dropped by seal"
        if sid.startswith(("cal-", "lp-", "xs-", "int-")):
            # semantic content survives (not just key presence)
            if sid.startswith("lp-"):
                assert sc["graph_script"].get("nand_edges") is not None
            if sid.startswith(("xs-", "int-")):
                assert (sc.get("prompt") or {}).get("session_scripts")


def test_scenario_leg_preserves_fields(tmp_path):
    scs, _ = _mini(tmp_path)
    by = {sc.id: sc for sc in scs}
    cal, xs, lp, drift, ret = (
        by["cal-mini"],
        by["xs-mini"],
        by["lp-mini"],
        by["drift-mini"],
        by["ret-mini"],
    )
    assert cal.evidence_tiers and cal.evidence_tiers[0]["claim"] == "green canary sings"
    assert cal.question == "is the canary green?"  # never reconstructed by position
    assert xs.session_scripts and len(xs.session_scripts) == 2
    assert xs.session_scripts[1]["question"] == "s2 question LATE-S2"
    assert drift.drift == {"decision": "opt", "offsets": ["7d", "21d"]}
    assert ret.retraction["k"] == 2 and "retraction" in ret.to_render_dict()
    # graph_script: REAL lp-* sub-shape (1-char node ids, INT turn refs,
    # contested_pair carries claim TEXT + node-id refs — corpus lp-001..012)
    assert lp.graph_script["nodes"][0] == {"id": "p", "claim_or_turn_ref": 0}
    assert lp.graph_script["nand_edges"] == [["p", "q"], ["q", "r"], ["r", "p"]]
    assert lp.graph_script["contested_pair"]["a_ref"] == "p"
    assert isinstance(lp.graph_script["contested_pair"]["a"], str)


def test_multi_session_render_by_session(tmp_path):
    scs, _ = _mini(tmp_path)
    xs = next(sc for sc in scs if sc.id == "xs-mini")
    # via Scenario.to_render_dict — sessions are 1-based (authored session
    # ids 1..N; real xs-* are 1..6; render(session=0) KeyErrors on real data)
    s1r = render_reader_prompt(xs.to_render_dict(), session=1)
    s2r = render_reader_prompt(xs.to_render_dict(), session=2)
    assert "LATE-S2" in s2r and "LATE-S2" not in s1r
    assert "s1 turn" in s1r


def test_render_emits_authored_evidence(tmp_path):
    scs, _ = _mini(tmp_path)
    cal = next(sc for sc in scs if sc.id == "cal-mini")
    out = render_reader_prompt(cal.to_render_dict())
    assert "green canary sings" in out and "Evidence" in out
    # question appears exactly once (de-dup: flattened question REMOVED from
    # prompt_pack turns) and never as an empty "question: " line
    assert out.count("is the canary green?") == 1
    assert not any(line.strip() == "question:" for line in out.splitlines())


def test_graph_script_never_rendered(tmp_path):
    scs, _ = _mini(tmp_path)
    lp = next(sc for sc in scs if sc.id == "lp-mini")
    out = render_reader_prompt(lp.to_render_dict())
    assert "nand_edges" not in out and "contested_pair" not in out


def test_single_render_rule_projection(tmp_path):
    scs, _ = _mini(tmp_path)
    ctx = scs[0].to_episode_context()
    assert "gold" not in ctx and "contradiction_pairs" not in ctx
    assert "render" in ctx and "retrieved" in ctx
