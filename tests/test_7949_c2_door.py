"""#10983 / #7949-C2 — the DOOR the capstone's acceptance runs through, pinned.

WHY THIS SUITE EXISTS
---------------------
The capstone (#7949) measures E1–E5 end to end and every one of those numbers is
supposed to describe **the graph the fleet builds**. That is only true if the
run took the same code path a fleet capture takes, with the same seams armed.

Neither half was pinned. The fleet's capture lane is selected by an ordinary
environment variable (``TORTOISE_SESSION_EXTRACTOR``), several v4 seams default
OFF, one defaults **ON**, and one of the six names in the seam ledger is not a
behaviour seam at all — so "we ran the pipeline" could be true of four different
pipelines while the log said one thing. A number produced by any of the other
three says nothing about the fleet's graph, and *nothing in the tree failed*
when that happened.

WHAT THIS PINS — and the polarity that makes it worth having
------------------------------------------------------------
1. **The door is named in CODE, resolved from the tree with ``ast``** (never by
   grepping for a doc string): ``hosted_api._capture_lane`` returns ``"v2"``
   unless ``TORTOISE_SESSION_EXTRACTOR == "m2"``; the v2 arm calls
   ``sdk._extract_session_v2``, which calls
   ``extractor_v2.extract_session_v2``. The hosted endpoint and the SDK read
   the SAME predicate, so "the hosted endpoint or the SDK path" is **one door**,
   not a fork to be decided again. A renamed/relocated function reds this and
   the failure message carries the ``path:line`` it resolved at, so the record
   cannot rot silently into prose (the #8506 class).
2. **The seam ledger is the CODE's, at this head** — not the issue's, not a
   doc's. ``TORTOISE_VALUE_GATE`` became **default-ON** in ``#10996``
   (``value_gate.value_gate_enabled`` reads ``is_truthy(os.environ.get(ENV_FLAG,
   "1"))``), and because ``extractor_v2`` ORs it into ``vet_enabled`` the S2.2
   pass is armed on the fleet's default write path **even with
   ``TORTOISE_VET`` unset**. The ledger line in #10983 still says all four are
   default-off; this suite is what makes that class of drift fail a test instead
   of being believed for another two months.
3. **The stage vocabulary** — ``extractor_v2``'s ``S5`` is EMBED, never CONNECT
   (§2 R10). A log line containing "S5" is therefore NOT evidence that the v4
   CONNECT step ran; only a named call site is. Pinned so a log-string "proof"
   cannot be substituted for a call site.
4. **The acceptance probe cannot silently take another door** — the lane and the
   two prompt-rendering switches are pinned and READ BACK inside
   ``measured_arm``, and a leak refuses the run (``capstone_measure``).
5. **The negative control, measured** — the same corpus session through both
   lanes. The measurement is recorded verbatim in the assertion message because
   its *result* is the finding: under the shipped offline mock seam the two
   lanes are **indistinguishable**, so the mock is lane-blind and may never be
   the control, and no acceptance number may be produced under it.

Everything here is DB-light (one embedded capture per arm), network-free and
LLM-free (the ``TORTOISE_SESSION_LLM_MOCK=1`` offline seam).
"""
from __future__ import annotations

import ast
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

HOSTED = REPO / "tortoise/hosted_api.py"
SDK = REPO / "tortoise/sdk.py"
EXTRACTOR = REPO / "tortoise/extractor_v2.py"
VALUE_GATE = REPO / "tortoise/value_gate.py"
SEAM = REPO / "tortoise/s2_decision_seam.py"
PROBE = REPO / "docs/evidence/7949-capstone/probes/capstone_measure.py"
REGISTRATION = REPO / "docs/evidence/7949-capstone/preregistration.json"


# ── resolution helpers ────────────────────────────────────────────────────

def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _funcs(path: Path) -> dict[str, ast.AST]:
    return {n.name: n for n in ast.walk(_tree(path))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _cite(path: Path, name: str) -> str:
    """``path:line`` of ``name``'s def — the record's own citation format."""
    node = _funcs(path)[name]
    return f"{path.relative_to(REPO)}:{node.lineno}"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve(path: Path, name: str) -> str:
    """The def's source segment, so a claim is read off the CODE that runs."""
    node = _funcs(path)[name]
    return ast.get_source_segment(_source(path), node) or ""


# ── 1. the door, in code ──────────────────────────────────────────────────

def test_the_lane_selector_defaults_to_v2_and_m2_is_the_opt_in():
    """``_capture_lane()`` is the hosted mirror of the SDK's env predicate."""
    body = _resolve(HOSTED, "_capture_lane")
    assert "TORTOISE_SESSION_EXTRACTOR" in body, (
        f"_capture_lane at {_cite(HOSTED, '_capture_lane')} no longer reads "
        f"TORTOISE_SESSION_EXTRACTOR — the door moved: {body!r}")
    assert re.search(r'== "m2"', body) or re.search(r"== 'm2'", body), (
        f"the selector no longer compares against 'm2' at "
        f"{_cite(HOSTED, '_capture_lane')}: {body!r}")
    # The polarity is the SPECIFIC form: m2 is the opt-in, v2 the fall-through.
    # (A bare `"v2" in body` was near-tautological — the previous revision of
    # this test accepted three alternatives, one of which matched the docstring.)
    assert re.search(r'<\s*"m2"\s*\n?\s*else\s+"v2"|m2.\s+if\s+', body), (
        f"the selector's polarity changed — v2 must be the FALL-THROUGH, not a "
        f"listed alternative (#10983): {body!r}")


def test_the_v2_arm_calls_the_sdk_extractor_entry():
    """The chain is a chain: hosted v2 arm → sdk method → extractor entry."""
    hosted = _source(HOSTED)
    assert "sdk._extract_session_v2" in hosted, (
        "the hosted capture no longer calls sdk._extract_session_v2 — the v2 "
        "arm of _capture_lane would then name a lane nothing runs (#10983)")
    sdk_body = _resolve(SDK, "_extract_session_v2")
    assert "extract_session_v2" in sdk_body, (
        f"sdk._extract_session_v2 at {_cite(SDK, '_extract_session_v2')} no "
        "longer reaches extractor_v2.extract_session_v2")
    assert "extract_session_v2" in _funcs(EXTRACTOR), (
        f"extractor_v2.extract_session_v2 is GONE from "
        f"{EXTRACTOR.relative_to(REPO)} — the door's last hop does not exist")


def test_the_sdk_lane_dispatch_and_the_hosted_mirror_agree():
    """Two entry points, ONE predicate — so this is one door, not a fork.

    #10983 leaves open "is the acceptance the hosted endpoint or the SDK path
    it calls?". They select the lane from the SAME variable with the SAME
    comparison, so the answer is not a decision about which door: it is a
    decision about how much HTTP boundary to put in front of one door. Pinned
    so a later edit cannot make them disagree.
    """
    sdk_src = _source(SDK)
    dispatch = re.search(
        r'if\s+os\.environ\.get\("TORTOISE_SESSION_EXTRACTOR"\)\s*==\s*"m2"',
        sdk_src)
    assert dispatch, (
        "the SDK's lane dispatch is no longer "
        "'os.environ.get(\"TORTOISE_SESSION_EXTRACTOR\") == \"m2\"' — the "
        "hosted mirror and the SDK dispatch have started to disagree about "
        "which lane is which (#10983)")
    hosted_body = _resolve(HOSTED, "_capture_lane")
    assert re.search(r'os\.environ\.get\("TORTOISE_SESSION_EXTRACTOR"\)', hosted_body)


def test_the_door_citation_is_derived_not_typed():
    """A typed ``:16975`` is the #8506 rot class. Resolve it every run."""
    cite = _cite(HOSTED, "_capture_lane")
    line = int(cite.rsplit(":", 1)[1])
    text = HOSTED.read_text(encoding="utf-8").splitlines()[line - 1]
    assert "def _capture_lane" in text, (
        f"the derived cite {cite} does not land on the def — the derivation "
        f"itself is broken")


# ── 2. the seam ledger, from the code ─────────────────────────────────────

def _default_of(resolver_module: str, resolver: str) -> bool | None:
    """Resolve a seam's DEFAULT by CALLING it with the var unset."""
    import importlib

    mod = importlib.import_module(f"tortoise.{resolver_module}")
    saved = {k: os.environ.pop(k, None)
             for k in ("TORTOISE_VET", "TORTOISE_CLASSIFY_LATER",
                       "TORTOISE_S2_DECISION_SEAM", "TORTOISE_VALUE_GATE",
                       "TORTOISE_S4_DELTA", "TORTOISE_LABEL_ORDER")}
    try:
        value = getattr(mod, resolver)("story") if resolver == "_label_order_rng" \
            else getattr(mod, resolver)()
    finally:
        for key, prior in saved.items():
            if prior is not None:
                os.environ[key] = prior
    if isinstance(value, bool):
        return value
    return None if value is None else True


def test_the_seam_ledger_is_the_codes_not_the_issues():
    """Each row of #10983's ledger, resolved against the module that owns it.

    ⚠️ THE EXPECTATIONS BELOW ARE THE CORRECTION, not a restatement. The issue
    calls all four of VET / CLASSIFY_LATER / S2_DECISION_SEAM / VALUE_GATE
    "genuinely default-off". That was true when it was written and it is FALSE
    at this head: ``#10996`` made the mechanical predicate default-ON, which is
    a *deliberate* behavioural change on the fleet's write path.
    """
    ledger = {
        ("extractor_v2", "_vet_enabled"): False,
        ("extractor_v2", "_classify_later_enabled"): False,
        ("s2_decision_seam", "seam_enabled"): False,
        # ⚠️ default-ON since #10996 — the issue's ledger says off.
        ("value_gate", "value_gate_enabled"): True,
        ("extractor_v2", "_s4_delta_enabled"): True,
        # NOT a behaviour seam: a render diagnostic, off => None.
        ("extractor_v2", "_label_order_rng"): None,
    }
    measured = {row: _default_of(*row) for row in ledger}
    assert measured == ledger, (
        "the v4 seam ledger MOVED. Resolved by calling each resolver with the "
        "variable unset: " + json.dumps(
            {f"{m}.{f}": v for (m, f), v in measured.items()}, indent=1) +
        "\n#10983's ledger and every acceptance log built on it must be "
        "re-decided, not inherited.")


def test_the_mechanical_gate_arms_the_vet_pass_on_the_default_path():
    """The consequence the ledger hid: VALUE_GATE ON ⇒ S2.2 runs by default.

    ``extractor_v2`` computes ``vet_enabled = (arbiter is not None or
    _vet_enabled() or _value_gate_enabled())``. With the gate default-ON, the
    pass runs even though ``TORTOISE_VET`` is unset — so "VET is off by
    default" is true of the FLAG and false of the PASS. Pinned so the ledger
    cannot be read as "the fleet performs no selection" (#10996 landed that
    change; #7949's epic body still says the opposite).
    """
    src = _source(EXTRACTOR)
    assert re.search(
        r"vet_enabled\s*=\s*\(vet_arbiter is not None\s*\n?\s*or _vet_enabled\(\)"
        r"\s*\n?\s*or _value_gate_enabled\(\)\)", src), (
        "the vet_enabled composition changed — re-derive what arms S2.2 before "
        "trusting either the ledger or the epic's 'zero selection' claim")

    # And the OR has a live right-hand term: the gate defaults ON.
    body = _resolve(VALUE_GATE, "value_gate_enabled")
    assert "is_truthy(os.environ.get(ENV_FLAG" in body and '"1"' in body, (
        f"value_gate.value_gate_enabled at "
        f"{_cite(VALUE_GATE, 'value_gate_enabled')} no longer defaults ON; "
        f"the S2.2 pass is now unarmed on the default path")


def test_the_seam_var_the_ledger_names_is_the_one_the_module_reads():
    """A seam name is a contract: the ledger's spelling must be the read."""
    assert 'ENV_FLAG = "TORTOISE_S2_DECISION_SEAM"' in _source(SEAM), (
        "s2_decision_seam.ENV_FLAG is no longer TORTOISE_S2_DECISION_SEAM — "
        "every acceptance log that pins that spelling is pinning nothing")
    for flag in ("TORTOISE_S4_DELTA", "TORTOISE_VET", "TORTOISE_CLASSIFY_LATER"):
        assert flag in _source(EXTRACTOR), f"{flag} is not read by extractor_v2"
    assert "TORTOISE_LABEL_ORDER" in _source(EXTRACTOR)


# ── 3. the stage vocabulary (§2 R10) ──────────────────────────────────────

def test_s5_means_embed_in_this_module_never_connect():
    """A log string containing "S5" is not evidence CONNECT ran.

    ``extractor_v2``'s own header lists ``[S1] STORY SUMMARY … [S5] EMBED``: in
    THIS module S5 is the deterministic commit/embed execution step. v4's
    ``S5 CONNECT`` is a different stage in a different document. Pinned so
    "prove S5 CONNECT ran" cannot be answered by matching the string.
    """
    doc = (_funcs(EXTRACTOR).get("extract_session_v2") is not None)
    assert doc
    header = _source(EXTRACTOR)
    stages = re.findall(r"\[S(\d)\]\s*([A-Z][A-Z \-]+)", header)
    assert ("5", "EMBED") in [(n, label.strip()) for n, label in stages], (
        "extractor_v2's stage list no longer says '[S5] EMBED' — the stage "
        f"vocabulary moved (#10983 §2 R10). Found: {stages}")
    assert not any("CONNECT" in label for _n, label in stages), (
        "extractor_v2 now claims a CONNECT stage — if that is real, the "
        "CONNECT claim can be evidenced by a call site AND this pin must move; "
        f"found {stages}")


def test_the_connect_stage_has_a_named_call_site_or_no_claim():
    """§2 R10's positive half: the v4 CONNECT step is a writer, not a label.

    The capstone's E4 row is about operator edges being *wired and persisted*.
    Pinned here as an executable fact rather than a doc claim: the embed step's
    operator path is a real function, so "CONNECT ran" is checkable at a call
    site instead of by log text.
    """
    names = set(_funcs(EXTRACTOR))
    writers = [n for n in names
               if re.search(r"apply_payload_operators|_emit_operators|"
                            r"operator", n)]
    assert writers, (
        "no operator-writing function remains in extractor_v2 — the E4 "
        "CONNECT claim would then have no call site to name (#7949 §2 R10)")


# ── 4. the acceptance probe cannot silently take another door ─────────────

def _load_probe():
    import importlib.util as ilu

    spec = ilu.spec_from_file_location("capstone_measure_door", str(PROBE))
    module = ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_probe_pins_the_lane_and_reads_it_back():
    probe = _load_probe()
    assert probe.DOOR_LANE == "v2"
    assert probe.DOOR_LANE_ENV == "TORTOISE_SESSION_EXTRACTOR"
    assert "TORTOISE_SESSION_EXTRACTOR" in probe.DOOR_PINNED_OFF, (
        "the probe no longer pins the extraction lane — an ambient "
        "TORTOISE_SESSION_EXTRACTOR=m2 would reroute every acceptance capture "
        "to the m2 lane and the log would not say so (#10983)")
    assert ("TORTOISE_S4_DELTA", "1") in probe.DOOR_PINNED_ON, (
        "TORTOISE_S4_DELTA is DEFAULT-ON; leaving it unpinned lets an ambient "
        "=0 swap S4's output contract under a declared arm")
    assert "TORTOISE_LABEL_ORDER" in probe.DOOR_PINNED_OFF, (
        "TORTOISE_LABEL_ORDER=shuffle randomises the renders per call — it "
        "must be pinned off or two runs of the arm differ for a non-arm reason")


def test_the_probe_refuses_a_leaked_lane(monkeypatch):
    """Fail closed: a lane that does not read back as unset REFUSES the run."""
    probe = _load_probe()

    class _Unpoppable(dict):
        """Accepts the pin, refuses to apply it — the lane leaks back out."""

        def pop(self, key, default=None):
            return self.get(key, default)

    env = _Unpoppable(os.environ)
    env[probe.DOOR_LANE_ENV] = "m2"
    monkeypatch.setattr(probe, "os", type(
        "os", (), {"environ": env})())
    with pytest.raises(SystemExit) as exc, probe.measured_arm():
        pass
    assert "REFUSED" in str(exc.value), exc.value
    assert "lane" in str(exc.value).lower(), exc.value


def test_the_probe_records_the_door_in_the_log_and_the_machine_record():
    """Target 1 & 3: the seam set is recorded IN THE RUN, not inferred later."""
    probe = _load_probe()
    observed = {key: None for key in probe.DOOR_PINNED_OFF}
    observed.update({key: value for key, value in probe.DOOR_PINNED_ON})
    header = probe.door_header(observed)
    assert "v2 lane" in header, header
    assert "sdk._extract_session_v2" in header, header
    assert "_capture_lane" in header, header
    for env, _where, default, _seam in probe.SEAM_LEDGER:
        assert f"{env}={default}" in header, (
            f"the door line does not state {env}'s default: {header}")

    record = probe.door_record(observed)
    assert record["lane"] == "v2"
    assert record["lane_env"] == "TORTOISE_SESSION_EXTRACTOR"
    assert {row["env"] for row in record["seams"]} == {
        env for env, _w, _d, _s in probe.SEAM_LEDGER}
    assert any(row["default"] == "on" for row in record["seams"])


def test_the_registration_records_the_door_and_the_corpus_attribution():
    """The corpus decision lives in the REGISTRATION, not in prose (#10983)."""
    record = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    door = record.get("door")
    assert isinstance(door, dict), (
        "preregistration.json carries no `door` block — the acceptance would "
        "again be scoreable by a path nobody named (#10983)")
    assert door["lane"] == "v2"
    assert "extract_session_v2" in door["entry_point"]
    assert {row["env"] for row in door["seams"]} == {
        "TORTOISE_VET", "TORTOISE_CLASSIFY_LATER", "TORTOISE_S2_DECISION_SEAM",
        "TORTOISE_VALUE_GATE", "TORTOISE_S4_DELTA", "TORTOISE_LABEL_ORDER"}

    rows = record.get("corpus_rows")
    assert isinstance(rows, dict) and rows, (
        "preregistration.json carries no corpus-row attribution — 'which "
        "corpus carries which row' is the decision #10983 exists to write down")
    for crit, entry in rows.items():
        assert entry["corpus"] in {"tests/eval/write_path", "fleet-capture"}, (
            f"row {crit} names an unknown corpus {entry['corpus']!r}")
        assert entry["claim"] and entry["not_a_proof_of"], (
            f"row {crit} states no claim / no boundary — an attribution "
            f"without a falsifier is prose again")


# ── 5. the negative control, MEASURED ─────────────────────────────────────

def _census(sdk) -> dict:
    g = sdk._get_proj().g
    counts = {}
    for label in ("Object", "Point", "Event", "Session", "Operator"):
        res = g.query(f"MATCH (n:{label}) RETURN count(n)").result_set
        counts[label] = res[0][0] if res else 0
    names = sorted(row[0] for row in (
        g.query("MATCH (n:Object) RETURN n.name").result_set or []))
    return {"counts": counts, "objects": names}


def test_the_negative_control_is_measured_and_the_two_lanes_DISAGREE():
    """The control, run for real: the same session through both lanes.

    * ``v2`` — the door (``_extract_session_v2`` → ``extract_session_v2``);
    * ``m2`` — ``_extract_session_llm``, the non-default lane.

    MEASURED at this head under the shipped offline model seam
    (``TORTOISE_SESSION_LLM_MOCK=1``):

        v2 (the door): Object=1 Point=27 Event=2 Session=1 Operator=0
                       objects=['the strategy']
        m2 (the other): Object=0 Point=149 Event=1 Session=1 Operator=0
                        objects=[]

    The control works, and it is the point of #10983: **the two doors build
    different graphs**, so a number measured on the ``m2`` lane is not a number
    about the v2 door (and vice versa). The differences are structural, not a
    matter of degree: the ``m2`` lane mints **no `:Object` at all** on this
    session and 5.5x the Points, because it is a different extractor, not a
    different seam of the same one.

    ⚠️ The assertion polarity is deliberately BOTH ways: identical output would
    mean the control had stopped being a control (the mock seam would have
    become lane-blind, as it was believed to be during #10983's first pass), so
    it reds too — with the measurement printed, because which way it moved is
    the finding.
    """
    from tests.eval.write_path.corpus import load_fixture, session_ids
    from tortoise.sdk import TortoiseSDK

    sid = session_ids()[0]
    conversation = load_fixture(sid)["conversation"]
    assert conversation, f"fixture {sid} carries no conversation"

    saved = {key: os.environ.get(key)
             for key in ("TORTOISE_SESSION_LLM_MOCK", "TORTOISE_SESSION_EXTRACTOR")}
    measured: dict[str, dict] = {}
    try:
        os.environ["TORTOISE_SESSION_LLM_MOCK"] = "1"
        for lane in ("v2", "m2"):
            os.environ.pop("TORTOISE_SESSION_EXTRACTOR", None)
            if lane == "m2":
                os.environ["TORTOISE_SESSION_EXTRACTOR"] = "m2"
            sdk = TortoiseSDK(os.path.join(tempfile.mkdtemp(), "door.db"))
            try:
                receipt = sdk.capture_session(conversation, session_id=sid)
                assert receipt.get("ok") is True, receipt.get("errors")
                measured[lane] = _census(sdk)
            finally:
                sdk.close()
    finally:
        for key, prior in saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior

    door, other = measured["v2"], measured["m2"]
    assert door["counts"]["Object"] >= 1, (
        "the door wrote no Objects at all, so the control compares two empty "
        f"graphs and proves nothing: {door}")
    assert door != other, (
        "THE NEGATIVE CONTROL SHOWS NO DIFFERENCE — the two lanes produced the "
        "same graph, so either the lane selector has been bypassed or the "
        "mock seam has gone lane-blind. A control that shows none is evidence "
        "the CONTROL is wrong (#10983):\n"
        f"  v2 (the door): {door}\n  m2 (the other): {other}")
    # The difference must be in the GRAPH, not only in a receipt field.
    assert door["counts"] != other["counts"], (door, other)
    assert door["objects"] != other["objects"], (
        "the two lanes minted the same :Object names — the control is "
        f"measuring content, not the door: {door['objects']}")
