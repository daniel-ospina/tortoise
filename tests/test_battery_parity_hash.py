"""#2284 Task 6 — parity protocol hash.

methodology_hashes gains a THIRD element — a protocol hash over
{seed, model_pin, temperature, event_schema (SCHEMA_VERSION), tool_surface}
— so decide-loop/protocol changes (schema bump, model change, temp/seed
change, tool-surface change) trip the #1414 methodology-unchanged check
instead of being invisible to parity. Old 2-tuple baseline records
(reader_prompt_hash + judge_rubric_id_hash only) keep matching (back-compat)
but the run is marked protocol-unknown so the #1144 baseline re-record is
forced rather than leaving drift invisible.

Migration contract locked here: old 2-tuple records match when
protocol_hash is absent-or-matching AND a protocol change trips
match=False (runner + end-to-end through the CLI parity record).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.cli import main
from battery.enums import ExitCode
from battery.parity.runner import (
    TOOL_SURFACE_IDS,
    methodology_hashes,
    protocol_hash,
    run_parity,
)

CONFIG = Path(__file__).resolve().parent.parent / "battery" / "config"


def _model(pin: str = "flash-class-placeholder", temp: float = 0.0) -> dict:
    return {"model_id": pin, "temperature": temp}


def _baseline(reader_prompt="rp", judge_rubric_id="jr", *, protocol=None):
    rp_h, jr_h, _ = methodology_hashes(reader_prompt, judge_rubric_id)
    bl = {"reader_prompt_hash": rp_h, "judge_rubric_id_hash": jr_h}
    if protocol is not None:
        bl["protocol_hash"] = protocol
    return bl


# ── protocol_hash: composition + sensitivity ──────────────────────────────
class TestProtocolHash:
    def test_16hex_stable_and_seed_sensitive(self):
        h1 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                           tool_surface=TOOL_SURFACE_IDS)
        h2 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                           tool_surface=TOOL_SURFACE_IDS)
        h3 = protocol_hash(seed=8, model=_model(), event_schema="1.1",
                           tool_surface=TOOL_SURFACE_IDS)
        assert h1 == h2 and len(h1) == 16
        assert all(c in "0123456789abcdef" for c in h1)
        assert h1 != h3  # seed change trips

    def test_schema_bump_trips(self):
        v10 = protocol_hash(seed=7, model=_model(), event_schema="1.0",
                            tool_surface=TOOL_SURFACE_IDS)
        v11 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                            tool_surface=TOOL_SURFACE_IDS)
        assert v10 != v11

    def test_model_change_trips(self):
        h_a = protocol_hash(seed=7, model=_model(pin="flash-class-placeholder"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        h_b = protocol_hash(seed=7, model=_model(pin="other-pin"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        assert h_a != h_b  # model pin change trips

    def test_temperature_change_trips(self):
        h0 = protocol_hash(seed=7, model=_model(temp=0.0), event_schema="1.1",
                           tool_surface=TOOL_SURFACE_IDS)
        h1 = protocol_hash(seed=7, model=_model(temp=0.7), event_schema="1.1",
                           tool_surface=TOOL_SURFACE_IDS)
        assert h0 != h1

    def test_tool_surface_membership_sensitive_order_insensitive(self):
        surface = ("file_nand", "register_conflict", "mitigate")
        h1 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                           tool_surface=("mitigate", "file_nand",
                                         "register_conflict"))
        h2 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                           tool_surface=surface)
        assert h1 == h2  # same ids, different order → identical
        h3 = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                           tool_surface=(*surface, "supersede"))
        assert h2 != h3  # membership change trips

    def test_tool_surface_defined(self):
        # the pinned tool surface is the schema-v1.1 tool_event verb set —
        # a decide-loop tool-surface change must update this tuple and trip
        # parity (that is the point of the protocol hash).
        assert TOOL_SURFACE_IDS == (
            "create_point", "create_operator", "file_nand",
            "register_conflict", "mitigate", "supersede")
        # single-source lock (#2284 review P2): the parity-pinned tuple must
        # stay EXACTLY the schema-v1.1 tool_event verb registry — two
        # drifting copies would make a tool-surface change invisible to the
        # protocol hash unless BOTH move together.
        from battery.runner.emit import _SUBTYPE_OK
        assert set(TOOL_SURFACE_IDS) == set(_SUBTYPE_OK["tool_event"])


# ── methodology_hashes: 3-tuple fixed order ──────────────────────────────
class TestMethodologyHashes3Tuple:
    def test_returns_3tuple_fixed_order(self):
        protocol = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                                 tool_surface=TOOL_SURFACE_IDS)
        rp, jr, ph = methodology_hashes("rp", "jr", protocol=protocol)
        assert len(rp) == 16 and len(jr) == 16 and len(ph) == 16
        # fixed element order: reader_prompt, judge_rubric_id, protocol
        assert (rp, jr, ph) == (methodology_hashes("rp", "jr",
                                                   protocol=protocol))
        # independent compares: only the protocol element tracks the pin
        rp2, jr2, ph2 = methodology_hashes("rp-CHANGED", "jr",
                                           protocol=protocol)
        assert rp2 != rp and jr2 == jr and ph2 == protocol

    def test_protocol_optional_none(self):
        rp, jr, ph = methodology_hashes("rp", "jr")
        assert ph is None and len(rp) == 16 and len(jr) == 16


# ── run_parity: 3-way compare + 2-tuple back-compat ───────────────────────
class TestRunParityProtocol:
    def test_full_3tuple_match(self):
        protocol = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                                 tool_surface=TOOL_SURFACE_IDS)
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(protocol=protocol),
                       accuracy=0.66, samples=10, protocol=protocol)
        assert r.methodology_matched
        assert r.protocol_unknown is False
        assert r.protocol_hash == protocol

    def test_schema_bump_trips_match_false(self):
        # baseline recorded under schema 1.1; the CURRENT protocol is 1.0
        old = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                            tool_surface=TOOL_SURFACE_IDS)
        new = protocol_hash(seed=7, model=_model(), event_schema="1.0",
                            tool_surface=TOOL_SURFACE_IDS)
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(protocol=old),
                       protocol=new)
        assert not r.methodology_matched
        assert r.protocol_unknown is False  # compared, and it drifted

    def test_model_change_trips_match_false(self):
        old = protocol_hash(seed=7, model=_model(pin="measured-pin-v1"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        new = protocol_hash(seed=7, model=_model(pin="measured-pin-v2"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        r = run_parity("locomo", "locomo-v1", "a4",
                       "rp", "jr", _baseline(protocol=old),
                       protocol=new)
        assert not r.methodology_matched

    def test_backcompat_2tuple_baseline_matches_warn(self):
        """Migration: an OLD 2-tuple baseline record (no protocol_hash)
        still matches on reader_prompt+rubric — compare-2 back-compat — but
        the run is protocol-unknown (the protocol leg was NOT verifiable)."""
        protocol = protocol_hash(seed=7, model=_model(), event_schema="1.1",
                                 tool_surface=TOOL_SURFACE_IDS)
        bl = _baseline()  # 2-tuple only
        assert "protocol_hash" not in bl
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", bl, accuracy=0.5, samples=0,
                       protocol=protocol)
        assert r.methodology_matched  # old record still matches (2-tuple)
        assert r.protocol_unknown is True  # protocol leg unverified → warn
        assert r.protocol_hash == protocol  # current protocol backfilled

    def test_old_record_without_current_protocol_matches(self):
        """Old callers (no protocol computed) keep compare-2 semantics."""
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", _baseline(), accuracy=0.5)
        assert r.methodology_matched
        assert r.protocol_unknown is True and r.protocol_hash is None

    def test_protocol_drift_without_reader_drift_is_not_invisible(self):
        # reader_prompt + rubric UNCHANGED, protocol changed → the unchanged
        # check must STILL trip (this is the #1414 invisibility hole closed).
        old = protocol_hash(seed=7, model=_model(pin="measured-pin-v1"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        new = protocol_hash(seed=7, model=_model(pin="measured-pin-v2"),
                            event_schema="1.1", tool_surface=TOOL_SURFACE_IDS)
        bl = _baseline("rp", "jr", protocol=old)
        r = run_parity("longmemeval", "longmemeval-2025.3", "a4",
                       "rp", "jr", bl, protocol=new)
        assert not r.methodology_matched


# ── end-to-end: cli derives protocol from the pinned arm config ───────────
def _config_dir(tmp_path, *, baseline_3tuple=False,
                model_pin="flash-class-placeholder") -> Path:
    d = tmp_path / "cfg"
    d.mkdir(parents=True, exist_ok=True)
    (d / "arms.yaml").write_text(f"""\
arms:
  - arm_id: a4
    adapter: battery.arms.a4_tortoise
    config: {{}}
    price_per_1k_usd: 0.5
    expected_tokens_per_episode: 800
    model_pin: {model_pin}
    temperature: 0.0
""", encoding="utf-8")
    (d / "reader_prompt.md").write_text("rp-content", encoding="utf-8")
    rp, jr, _ = methodology_hashes("rp-content", "longmemeval-official")
    bl: dict = {"reader_prompt_hash": rp, "judge_rubric_id_hash": jr}
    if baseline_3tuple:
        # baseline protocol computed over the fixture's arm config
        from battery.parity.runner import TOOL_SURFACE_IDS
        from battery.runner.artifacts import SCHEMA_VERSION
        model = {"model_id": model_pin, "temperature": 0.0}
        bl["protocol_hash"] = protocol_hash(
            seed=0, model=model, event_schema=SCHEMA_VERSION,
            tool_surface=TOOL_SURFACE_IDS)
    (d / "parity_baseline.json").write_text(
        json.dumps(bl), encoding="utf-8")
    return d


class TestCliParityProtocol:
    def test_2tuple_baseline_warn_surfaced_and_persisted(self, tmp_path,
                                                         capsys):
        """Locked warn path: an old 2-tuple baseline runs the parity leg →
        the warn is SURFACED on stdout and the parity record PERSISTS the
        protocol-unknown state (forcing the #1144 re-record — drift can
        never stay invisible)."""
        cfg = _config_dir(tmp_path)
        out = tmp_path / "out"
        code = main(["parity", "--config", str(cfg), "--out", str(out)])
        assert code is ExitCode.OK
        captured = capsys.readouterr().out
        assert "protocol" in captured.lower()
        assert "re-record" in captured.lower()  # #1144 forced re-record
        rec = json.loads((out / "parity_record.json").read_text())
        assert rec["protocol_unknown"] is True
        assert len(rec["protocol_hash"]) == 16  # backfilled on read
        # methodology still matched on the 2-tuple compare (migration)
        assert all(b["methodology_matched"] for b in rec["benchmarks"].values())

    def test_protocol_derived_from_pinned_arm_config(self, tmp_path):
        """_cmd_parity loads arms.yaml (no more hardcoded config-less arm):
        the protocol hash in the parity record must equal the hash computed
        from the pinned arm's model_pin + temperature + SCHEMA_VERSION +
        tool-surface ids (same derivation as the cli)."""
        cfg = _config_dir(tmp_path, baseline_3tuple=True)
        out = tmp_path / "out"
        code = main(["parity", "--config", str(cfg), "--out", str(out)])
        assert code is ExitCode.OK
        rec = json.loads((out / "parity_record.json").read_text())
        assert rec["arm"] == "a4"  # pinned arm (default; --arms overrides)
        # Placeholder pin => protocol leg UNVERIFIED (no real model measured
        # under flash-class-placeholder) — never a certified protocol.
        assert rec["protocol_unknown"] is True
        # methodology (reader-prompt + rubric 2-tuple) still compared
        assert all(b["methodology_matched"]
                   for b in rec["benchmarks"].values())
        # recompute the protocol from the fixture config independently
        from battery.runner.artifacts import SCHEMA_VERSION
        expect = protocol_hash(
            seed=0,
            model={"model_id": "flash-class-placeholder", "temperature": 0.0},
            event_schema=SCHEMA_VERSION, tool_surface=TOOL_SURFACE_IDS)
        assert rec["protocol_hash"] == expect

    def test_model_change_trips_end_to_end(self, tmp_path, capsys):
        """Baseline (3-tuple) recorded with model pin X; arms.yaml now pins
        Y (a decide-loop model change) → unchanged-check trips across every
        benchmark in the parity record."""
        cfg = _config_dir(tmp_path, baseline_3tuple=True,
                          model_pin="measured-pin-v1")
        (cfg / "arms.yaml").write_text((cfg / "arms.yaml")
                                       .read_text(encoding="utf-8")
                                       .replace("measured-pin-v1",
                                                "measured-pin-v2"),
                                       encoding="utf-8")
        out = tmp_path / "out"
        code = main(["parity", "--config", str(cfg), "--out", str(out)])
        assert code is ExitCode.OK
        rec = json.loads((out / "parity_record.json").read_text())
        assert rec["protocol_unknown"] is False  # real measured pins
        assert all(b["methodology_matched"] is False
                   for b in rec["benchmarks"].values())  # …and it drifted
