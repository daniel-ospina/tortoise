"""#10554 review — the re-measurement record's ARITHMETIC must be the arithmetic of its own logs.

THE DEFECTS THIS PINS
---------------------
`docs/evidence/5087-hypothesis/REMEASUREMENT-a83c23a8.md` is the document a reader quotes
the second S3 ablation from. Its headline verdict (`HYPOTHESIS-FALSIFIED`) is safe — E1 fails
either way — but the *magnitude* is not, so the document carries a warning paragraph telling a
reader how to read `0.988` without over-reading it. Three of that paragraph's load-bearing
numbers were wrong, and all three erred in the direction that makes the document's own caution
weaker than the evidence supports:

1. **The drift was understated against the effect.** The record said the untreated arm's
   between-run drift was *"roughly three times the 3-node ON−OFF difference"*. `284` vs `253`
   is `31` nodes; this run's `|250 − 253|` is `3`; the ratio is **10.3×**, not ~3×. The
   sentence exists to say the drift DROWNS the treatment difference, and it understated that
   by a factor of ~3.4 — the one direction a caution must never err in.
2. **A false resolution claim.** The record called it a *"six-decimal ratio"*. Nothing in the
   bundle prints six decimals: the log's own `RATIO:` line prints `0.9881` (four), so the
   paragraph accused the number of a precision no reader could have taken off it.
3. **An unqualified comparison that inverts under a second reading.** The record said the 11 %
   drift "is larger than every per-session delta in both runs". True in NODES (`31 > 15`, the
   largest per-session delta, which is `wp02`'s `51 → 36` in the recorded run) and FALSE as a
   share of the session's own denominator: `wp02` fell **−29.4 %** and this run's `wp03` fell
   **−24.4 %**, both larger than the 10.9 % drift. A comparison stated without its unit is a
   comparison a careful reader can refute, which is how a record loses authority.

WHAT THIS PINS
--------------
1. The drift's node count and percentage are the ones the two committed logs produce.
2. The multiplier the warning states for drift-vs-effect agrees with the logs' arithmetic.
3. The decimal resolution the record attributes to the ratio does not exceed what the log prints.
4. The drift is larger, **in nodes**, than every per-session delta in both runs — and the record
   says "in nodes" and names the true largest delta, so the claim cannot be read as a
   percentage claim it would lose.
5. The per-session percentage swings that DO exceed the drift are stated, with values derived
   here from the logs' own per-session tables.

Every number is re-derived from `s3-ablation-a1075dfc.log` and `s3-ablation-a83c23a8.log`; none
is hardcoded, so a future edit to either log reds these pins instead of silently re-basing them.
Hermetic: file reads and regex, no DB, no graph, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EVIDENCE = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "5087-hypothesis"
RECORDED_LOG = EVIDENCE / "s3-ablation-a1075dfc.log"
THIS_LOG = EVIDENCE / "s3-ablation-a83c23a8.log"
RECORD = EVIDENCE / "REMEASUREMENT-a83c23a8.md"

#: The document's minus sign for deltas is U+2212, not ASCII hyphen — match both.
MINUS = r"[-\u2212]"

_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "twenty": 20,
    "thirty": 30,
}


def _counts(path: Path) -> tuple[int, int]:
    """The log's own ``COUNTS: created extraction nodes OFF=<n> ON=<n>`` line."""
    text = path.read_text()
    m = re.search(
        r"^COUNTS: created extraction nodes OFF=(\d+) ON=(\d+)$", text, re.MULTILINE
    )
    assert m is not None, f"{path.name}: no COUNTS line — the log format moved"
    return int(m.group(1)), int(m.group(2))


def _printed_ratio_decimals(path: Path) -> int:
    """How many decimal places the log's ``RATIO:`` line actually prints."""
    text = path.read_text()
    m = re.search(r"^RATIO: ([0-9]+\.[0-9]+)$", text, re.MULTILINE)
    assert m is not None, f"{path.name}: no RATIO line — the log format moved"
    return len(m.group(1).split(".")[1])


def _per_session_deltas(path: Path) -> dict[str, tuple[int, int, int]]:
    """``{session: (off, on, delta)}`` from the log's ``--- per-session table ---`` block."""
    rows: dict[str, tuple[int, int, int]] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^(wp\d+_\w+)\s+(\d+)\s+(\d+)\s+(" + MINUS + r"?\d+)\s", line)
        if m:
            rows[m.group(1)] = (
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4).replace("\u2212", "-")),
            )
    return rows


@pytest.fixture(scope="module")
def logs() -> dict[str, object]:
    recorded_off, recorded_on = _counts(RECORDED_LOG)
    off, on = _counts(THIS_LOG)
    drift = recorded_off - off
    effect = abs(on - off)
    assert drift > 0 and effect > 0, "the drift/effect comparison needs both to be non-zero"
    return {
        "recorded": (recorded_off, recorded_on),
        "this": (off, on),
        "drift": drift,
        "drift_pct": drift / recorded_off * 100,
        "effect": effect,
        "multiplier": drift / effect,
        "ratio_decimals": _printed_ratio_decimals(THIS_LOG),
        "recorded_sessions": _per_session_deltas(RECORDED_LOG),
        "this_sessions": _per_session_deltas(THIS_LOG),
    }


@pytest.fixture(scope="module")
def record_text() -> str:
    """The record's prose with runs of whitespace collapsed to one space.

    The document is hard-wrapped at ~100 columns, so a sentence this module pins can be split
    anywhere — including between "received no" and "treatment". Matching the RAW text would
    make every pin dependent on the wrap, which is a formatting detail and not a claim. The
    pinned phrases are single sentences, so collapsing whitespace cannot merge two claims.
    """
    text = RECORD.read_text()
    assert text, f"{RECORD.name} is empty"
    return re.sub(r"\s+", " ", text)


def test_the_drift_and_its_percent_are_the_ones_the_logs_produce(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect class 1's TRUE half: the node count and percentage are the logs' own."""
    drift = int(logs["drift"])
    pct = float(logs["drift_pct"])
    m = re.search(
        r"\*\*(\d+)-node \(([\d.]+) %\) drift in the arm that received no treatment",
        record_text,
    )
    assert m is not None, (
        "the record must state the untreated arm's drift as '<N>-node (<P> %) drift in the "
        "arm that received no treatment' — that sentence is what tells a reader how far the "
        "two runs of the SAME arm disagree"
    )
    assert int(m.group(1)) == drift, (
        f"the record says the untreated arm drifted {m.group(1)} nodes; the two committed "
        f"logs say {drift} ({logs['recorded'][0]} vs {logs['this'][0]})"
    )
    assert abs(float(m.group(2)) - pct) < 0.05, (  # noqa: PLR2004 - one-decimal agreement
        f"the record says the drift is {m.group(2)} %; {drift}/{logs['recorded'][0]} is "
        f"{pct:.2f} %"
    )


def test_the_warning_does_not_understate_the_drift_against_the_effect(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect 1: the drift is ~10x the ON-OFF difference, not "roughly three times".

    The sentence exists to say the between-run drift DROWNS the treatment difference. A
    multiplier that understates the drift by a factor of three says the opposite of what the
    evidence supports, in the one paragraph written to prevent over-reading.
    """
    multiplier = float(logs["multiplier"])
    m = re.search(
        r"roughly (\w+) times the (\d+)-node ON" + MINUS + r"OFF difference", record_text
    )
    assert m is not None, (
        "the record must state the drift as a multiple of the ON-OFF difference "
        "('roughly <N> times the <M>-node ON-OFF difference')"
    )
    stated_word = m.group(1).lower()
    stated = _NUMBER_WORDS.get(stated_word)
    assert stated is not None, f"unparsed multiplier {stated_word!r} — extend _NUMBER_WORDS"
    assert int(m.group(2)) == int(logs["effect"]), (
        f"the record calls the ON-OFF difference {m.group(2)} nodes; the logs say "
        f"{logs['effect']} (|{logs['this'][0]} - {logs['this'][1]}|)"
    )
    assert abs(stated - multiplier) <= 1.5, (  # noqa: PLR2004 - "roughly" + rounding
        f"the record says the drift is roughly {stated}x the ON-OFF difference; "
        f"{logs['drift']}/{logs['effect']} is {multiplier:.2f}x. An understated multiplier "
        f"makes the drift look smaller than the effect, which is the defect this pins."
    )


def test_the_ratio_is_quoted_at_the_resolution_the_log_prints(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect 2: the log prints `RATIO: 0.9881` — four decimals, never six."""
    printed = int(logs["ratio_decimals"])
    m = re.search(r"The (\w+)-decimal ratio", record_text)
    assert m is not None, (
        "the record must attribute a decimal resolution to the ratio "
        "('The <N>-decimal ratio ...')"
    )
    word = m.group(1).lower()
    claimed = _NUMBER_WORDS.get(word)
    assert claimed is not None, f"unparsed resolution {word!r} — extend _NUMBER_WORDS"
    assert claimed <= printed, (
        f"the record calls the ratio {claimed}-decimal; the log's RATIO line prints "
        f"{printed} decimal places, so the record accuses the number of a precision no "
        f"reader could have taken off it"
    )


def test_the_drift_exceeds_every_per_session_node_delta_and_says_in_nodes(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect 3, first half: the claim is true IN NODES — so it must say so, and name the max.

    Stated without its unit, the same sentence is refutable as a percentage claim (see the
    sibling test). The largest per-session NODE delta across both runs is the recorded run's
    `wp02` (`51 -> 36`, 15 nodes), still less than the 31-node drift.
    """
    deltas: list[tuple[str, str, int]] = []
    for run, key in (("recorded", "recorded_sessions"), ("this run", "this_sessions")):
        for session, (_off, _on, delta) in dict(logs[key]).items():  # type: ignore[arg-type]
            deltas.append((run, session, abs(delta)))
    assert deltas, "no per-session rows parsed from either log — the table format moved"
    worst_run, worst_session, worst = max(deltas, key=lambda row: row[2])

    drift = int(logs["drift"])
    assert worst < drift, (
        f"the record says the drift exceeds every per-session delta; the logs show "
        f"{worst_session!r} ({worst_run}) moved {worst} nodes, and the drift is {drift}"
    )

    assert re.search(r"larger \*\*in nodes\*\* than every per-session delta", record_text), (
        "the comparison must be qualified 'in nodes' — unqualified, it is false as a share "
        "of each session's own denominator (the sibling test derives the counter-examples)"
    )
    assert re.search(rf"the largest is `{worst}`", record_text), (
        f"the record must name the largest per-session node delta, which the logs put at "
        f"{worst} ({worst_session!r} in the {worst_run})"
    )


def test_a_single_sessions_percentage_swing_exceeds_the_between_run_drift(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect 3, second half: the per-session swings that DO exceed the 10.9 % drift.

    These are the counter-examples that make the "in nodes" qualification necessary rather
    than pedantic. They are derived here, and the record must state them with these values.
    """
    drift_pct = float(logs["drift_pct"])
    over: list[tuple[str, str, float]] = []
    for run, key in (("recorded", "recorded_sessions"), ("this run", "this_sessions")):
        for session, (off, _on, _delta) in dict(logs[key]).items():  # type: ignore[arg-type]
            fell = abs(min(_delta, 0)) / off * 100
            if fell > drift_pct:
                over.append((run, session, fell))

    assert over, (
        "no per-session percentage swing exceeds the drift — if that ever changes, the "
        "'in nodes' qualification in the record becomes unnecessary and this pin should go"
    )

    # The record names exactly two: the recorded run's wp02 and this run's wp03.
    worst_recorded = max(
        (row for row in over if row[0] == "recorded"), key=lambda row: row[2], default=None
    )
    worst_this = max(
        (row for row in over if row[0] == "this run"), key=lambda row: row[2], default=None
    )
    assert worst_recorded is not None and worst_this is not None, (
        f"both runs are expected to carry a session that out-swings the drift; derived: {over}"
    )

    for run, session, pct in (worst_recorded, worst_this):
        short = session.split("_")[0]
        assert re.search(rf"`{short}` fell `\d+ → \d+` \(\*\*{MINUS}{pct:.1f} %\*\*\)", record_text), (
            f"the record must state the {run}'s worst percentage swing: "
            f"`{short}` fell **\u2212{pct:.1f} %** (derived from the logs' per-session table)"
        )
