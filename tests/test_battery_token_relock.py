"""#2292 Task 6 — measured token re-lock + token_table_hash reviewable-change
machinery (coordination n1: this issue owns formula + machinery +
PROVISIONAL values; #2284 Task 8 finalizes over the SAME machinery)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from battery.config.arms import load_arms, token_table_hash

CONFIG = Path(__file__).resolve().parents[1] / "battery/config"

#: ceil(probe per-episode deliberation 95th-pct (8803) x 3x headroom).
MEASURED_26409 = 26409


def test_token_table_hash_roundtrip_and_drift():
    arms = load_arms(CONFIG / "arms.yaml")
    h1 = token_table_hash(arms)
    assert len(h1) == 16
    # identical table -> identical hash (stable serialization)
    assert token_table_hash(load_arms(CONFIG / "arms.yaml")) == h1
    # a re-lock drifts the hash (reviewable-change: never a silent re-lock)
    bumped = {aid: replace(a, expected_tokens_per_episode=(
        a.expected_tokens_per_episode + 1)) for aid, a in arms.items()}
    assert token_table_hash(bumped) != h1


def test_probe_measured_arms_relocked_a0_a4():
    arms = load_arms(CONFIG / "arms.yaml")
    assert arms["a0"].expected_tokens_per_episode == MEASURED_26409
    assert arms["a4"].expected_tokens_per_episode == MEASURED_26409
    # never the stale 800 tok/ep guess
    assert arms["a4"].expected_tokens_per_episode != 800


def test_unmeasured_arms_keep_authored_rows_tbd_annotated():
    arms = load_arms(CONFIG / "arms.yaml")
    authored = {"a1": 600, "a2": 700, "a2b": 700, "a3": 400}
    for aid, guess in authored.items():
        assert arms[aid].expected_tokens_per_episode == guess, \
            "unmeasured arms keep their authored rows"
    raw = (CONFIG / "arms.yaml").read_text(encoding="utf-8")
    assert raw.count("TBD(EXPOSURE)") >= 4   # never stale-guess-as-measured
    # the probe-measured rows name their basis (p95 x headroom + scaffold
    # scope) so the measured claim is auditable
    assert "8803" in raw and "26409" in raw


def test_calibrate_print_shows_token_hash(capsys):
    from battery.cli import ExitCode, main
    rc = main(["calibrate", "--print", "--config", str(CONFIG)])
    out = capsys.readouterr().out
    assert rc is ExitCode.OK
    assert "token table hash: " in out
    assert "cal table hash: " in out
