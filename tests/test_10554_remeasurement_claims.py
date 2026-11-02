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
5. The per-session percentage swings that DO exceed the drift are stated, and every percentage
   the record attributes to a session is one the logs' own per-session tables support.
6. The recorded log is LOADED from either side of the withdrawal the #10463 ruling orders, so
   neither merge order (this PR first, or #10544 first) collects zero pins.
7. The bundle README scopes that withdrawal to the RECORDED run's log — the ordering half reads
   no log of that run, and it does read this run's log, measured from the guard's own glob.

Every number is re-derived from `s3-ablation-a1075dfc.log` and `s3-ablation-a83c23a8.log`; none
is hardcoded, so a future edit to either log reds these pins instead of silently re-basing them.
Hermetic: file reads and regex, no DB, no graph, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

EVIDENCE = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "5087-hypothesis"

#: The recorded run's log, by name. The #10463 ruling (item 3) orders this file **moved** to
#: `withdrawn/` (carried by #10544), so it is resolved rather than pinned to one path: a single
#: hardcoded path dies `FileNotFoundError` inside the module-scoped `logs` fixture the moment the
#: withdrawal lands, i.e. all five pins ERROR instead of running — the guard stops guarding — and
#: #10544 cannot carry this file, because on its branch this module does not exist.
RECORDED_LOG_NAME = "s3-ablation-a1075dfc.log"


def _resolve_recorded_log(evidence: Path) -> Path:
    """Locate the recorded run's log in **either** place the ruling can leave it.

    `withdrawn/` is preferred: once #10544 lands, the withdrawn copy is the live one and the
    top-level file is gone. The top-level entry keeps this module green on a tree where the
    withdrawal has not landed yet, so neither merge order can red the other lane.
    """
    candidates = (
        evidence / "withdrawn" / RECORDED_LOG_NAME,
        evidence / RECORDED_LOG_NAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"the recorded run's log is at neither location the #10463 ruling leaves it in: "
        f"{candidates[0]} or {candidates[1]} — a moved evidence log must be re-pointed, "
        f"not deleted"
    )


RECORDED_LOG = _resolve_recorded_log(EVIDENCE)
THIS_LOG = EVIDENCE / "s3-ablation-a83c23a8.log"
RECORD = EVIDENCE / "REMEASUREMENT-a83c23a8.md"
README = EVIDENCE / "README.md"

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
def readme_text() -> str:
    """The bundle README's prose with runs of whitespace collapsed, as `record_text` does."""
    text = README.read_text()
    assert text, f"{README.name} is empty"
    return re.sub(r"\s+", " ", text)


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


def test_the_recorded_log_loads_from_either_side_of_the_ordered_withdrawal(
    tmp_path: Path,
) -> None:
    """The guard must LOAD its own evidence under both trees the #10463 ruling can leave.

    Ruling item 3 orders `s3-ablation-a1075dfc.log` moved into `withdrawn/` (#10544), and #10544
    cannot re-point this module (it does not exist on that branch). A module that pins one path
    therefore goes from five green pins to five ERRORS on whichever tree lands second — the
    instrument stops guarding rather than refusing, and `main` goes red on the one check this
    PR adds. Both trees are simulated here from the real log's bytes, so the pin cannot be
    satisfied by a path helper that never reads anything.
    """
    body = RECORDED_LOG.read_text()
    decoy = body.replace("OFF=284 ON=247", "OFF=999 ON=247")

    def tree(name: str, *, withdrawn: bool, also_top_level: bool = False) -> Path:
        root = tmp_path / name
        placed = root / "withdrawn" if withdrawn else root
        placed.mkdir(parents=True, exist_ok=True)
        (placed / RECORDED_LOG_NAME).write_text(body)
        if also_top_level:
            (root / RECORDED_LOG_NAME).write_text(decoy)
        return root

    before_withdrawal = tree("before", withdrawn=False)
    after_withdrawal = tree("after", withdrawn=True)
    both = tree("both", withdrawn=True, also_top_level=True)

    assert _resolve_recorded_log(before_withdrawal) == before_withdrawal / RECORDED_LOG_NAME
    assert (
        _resolve_recorded_log(after_withdrawal)
        == after_withdrawal / "withdrawn" / RECORDED_LOG_NAME
    )
    # With both present the WITHDRAWN copy wins — the decoy's different COUNTS prove the
    # resolution is by location, not by whichever path was tried first and happened to parse.
    assert _resolve_recorded_log(both) == both / "withdrawn" / RECORDED_LOG_NAME
    assert _counts(_resolve_recorded_log(both)) == _counts(RECORDED_LOG)
    assert _counts(both / RECORDED_LOG_NAME) != _counts(RECORDED_LOG)

    # Reading from the moved location works end to end: this is the call that raises
    # FileNotFoundError in the fixture on a pinned single path.
    assert _counts(_resolve_recorded_log(after_withdrawal)) == _counts(RECORDED_LOG)
    assert _per_session_deltas(_resolve_recorded_log(after_withdrawal))

    with pytest.raises(FileNotFoundError, match="withdrawn"):
        _resolve_recorded_log(tmp_path / "absent")


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
    assert abs(float(m.group(2)) - pct) < 0.05, (
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
        r"(?:about|roughly)\s+\*{0,2}\s*(\w+)\s+times\s*\*{0,2}\s+the\s+(\d+)-node ON"
        + MINUS
        + r"OFF difference",
        record_text,
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
    # `roughly` + one-decimal rounding is the tolerance this pin allows.
    assert abs(stated - multiplier) <= 1.5, (
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

    assert re.search(r"larger than every per-session change \*\*in nodes\*\*", record_text), (
        "the comparison must be qualified 'in nodes' — unqualified, it is false as a share "
        "of each session's own denominator (the sibling test derives the counter-examples)"
    )
    short = str(worst_session).split("_")[0]
    assert re.search(
        rf"the largest within-run ON{MINUS}OFF delta in either run is `{worst}` nodes \(`{short}`",
        record_text,
    ), (
        f"the record must name the largest within-run node delta, which the logs put at "
        f"{worst} ({worst_session!r} in the {worst_run})"
    )


def test_every_session_percentage_the_record_states_is_the_logs_arithmetic(
    logs: dict[str, object], record_text: str
) -> None:
    """Defect 3, second half: the per-session swings that DO exceed the 10.9 % drift.

    These are the counter-examples that make the "in nodes" qualification necessary rather
    than pedantic. Two pins, both derived here rather than hardcoded: the record must state at
    least one (pre-fix it stated none, which is why the unqualified sentence survived), and
    every percentage it does state must be one the logs' own per-session tables support — a
    fabricated or mis-transcribed swing is the failure this catches.
    """
    stated = re.findall(r"`(wp\d+)` `([+\-\u2212])(\d+\.\d) %`", record_text)
    assert stated, (
        "the record must state at least one per-session percentage swing. Without one, the "
        "\"in nodes\" qualification reads as pedantry when it is in fact load-bearing: as a "
        "share of its own denominator a single session out-swings the whole-run drift."
    )

    # The record names sessions by their short id (`wp06`), so key by the prefix.
    known: dict[str, dict[str, float]] = {}
    for run, key in (("recorded", "recorded_sessions"), ("this run", "this_sessions")):
        for session, (off, _on, delta) in dict(logs[key]).items():  # type: ignore[arg-type]
            known.setdefault(session.split("_")[0], {})[run] = delta / off * 100

    for session, sign, number in stated:
        assert session in known, f"the record names {session!r}, which no log's table carries"
        value = float(number) * (1.0 if sign == "+" else -1.0)
        # 0.05 = half of the one decimal place the record prints.
        assert any(abs(candidate - value) < 0.05 for candidate in known[session].values()), (
            f"the record states {session} at {sign}{number} %; the logs' per-session tables "
            f"give {known[session]} — a stated swing must be one the logs support"
        )

    drift_pct = float(logs["drift_pct"])
    assert any(abs(float(number)) > drift_pct for _s, _sign, number in stated), (
        f"at least one stated per-session swing must exceed the {drift_pct:.1f} % drift, or "
        f"the record's qualification has no counter-example behind it"
    )


def _ordering_glob() -> str:
    """The ordering half's own glob, read off the guard instead of copied from it.

    Same loader shape as the sibling module's `_checker()`: a second copy of the literal here
    would be the two-copies-that-can-disagree shape this bundle refuses for its bars.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_capstone_preregistration_check_for_10554",
        EVIDENCE.parent.parent.parent / "tools" / "capstone_preregistration_check.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.EVIDENCE_LOG_GLOB


def test_the_ordering_half_reads_this_log_and_not_the_withdrawn_one(
    readme_text: str,
) -> None:
    """The README's claim about WHICH logs the ordering half reads must be the true one.

    The #10463 ruling withdraws the RECORDED run's log (item 3), so the bundle README (rewritten
    by #10544) originally said the ordering half "now examines **no** log for this bundle — the
    log is under `withdrawn/`, outside its non-recursive glob". That is true of the recorded run
    and false as a statement about the bundle the moment THIS PR adds the re-measurement's log at
    the top level: the guard's non-recursive glob does see `s3-ablation-a83c23a8.log`, and that is
    the log whose add-commit carries the ordering this record claims. A README that says "no log"
    overstates the withdrawal in the direction that hides the re-measurement's own provenance.

    Both halves are pinned against the guard's own `EVIDENCE_LOG_GLOB`, so the sentence cannot
    drift from what the guard reads: the re-measurement's log IS in the globbed set, the withdrawn
    copy is NOT, and the README must still say the withdrawal covers the recorded run specifically
    (the pre-fix universal is refused by name).
    """
    glob = _ordering_glob()
    scanned = {path.name for path in EVIDENCE.glob(glob)}

    assert THIS_LOG.name in scanned, (
        f"the ordering half's glob {glob!r} no longer reaches {THIS_LOG.name}, which is the log "
        f"the record's ordering claim is made from; it globs {sorted(scanned)}"
    )
    assert RECORDED_LOG_NAME not in scanned, (
        f"the withdrawn log is back inside the globbed set {sorted(scanned)} — it must stay "
        f"under `withdrawn/` (#10463 item 3), which the non-recursive glob excludes"
    )

    assert re.search(r"\*\*no\*\* log of the recorded run", readme_text), (
        "the README must scope the withdrawal to the RECORDED run's log: the ordering half reads "
        f"no log of that run ({RECORDED_LOG_NAME} is under `withdrawn/`), but it does read "
        f"{THIS_LOG.name}, so an unscoped \"no log\" is a false claim about the instrument"
    )
    assert not re.search(r"\*\*no\*\* log (?:for|of) this bundle", readme_text), (
        "the README says the ordering half examines no log for THIS BUNDLE, and it examines "
        f"{THIS_LOG.name} (measured from the guard's own glob above) — say which run's log is "
        "withdrawn instead of overstating the withdrawal to the whole bundle"
    )
