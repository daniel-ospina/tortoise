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
4. **The acceptance probe cannot silently take another door** — the lane, the
   commit-dispatch switch (`TORTOISE_EXTRACTOR`, defensive: it is NOT on the
   capture path) and the render diagnostic (`TORTOISE_LABEL_ORDER`) are pinned
   and READ BACK inside ``measured_arm``, and a leak refuses the run
   (``capstone_measure``).
5. **The negative control, measured and DIFFERENT** — the same corpus session
   through both lanes. The measurement is recorded verbatim in the assertion
   message because its *result* is the finding: under the shipped offline mock
   seam the two lanes are **structurally different** (`v2` mints 1 `:Object` and
   27 `:Point`s on `wp01_quarry_debug`; `m2` mints **no `:Object` at all** and
   149 `:Point`s), so the mock is NOT lane-blind and a number measured on one
   lane is not a number about the other. §1.6.4 of the registration carries the
   same table. ⚠️ This item said the opposite until the #10983 review: a
   lane-blind mock was *believed* during the first pass, and repeating that in
   the suite's own header contradicted the test below, which measures the
   difference.

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


def _body(path: Path, name: str) -> str:
    """The def's source segment WITHOUT its docstring.

    ⛔ A substring test over the whole segment is satisfied by prose, not by
    behaviour: `assert "extract_session_v2" in _resolve(SDK, "_extract_session_v2")`
    stayed GREEN with the real call deleted, because the docstring and the
    `from tortoise.extractor_v2 import extract_session_v2` line above the call
    both contain the name (found in the #10983 review — the same defect this
    file's CONNECT pin already records for a name grep). Callers assert a CALL
    SHAPE on this, never a bare name.
    """
    src = _source(path)
    node = _funcs(path)[name]
    seg = ast.get_source_segment(src, node) or ""
    first = node.body[0] if node.body else None
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)):
        seg = seg.replace(ast.get_source_segment(src, first) or "", "", 1)
    return seg


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
    # The polarity is the SPECIFIC form: m2 is the opt-in, v2 the FALL-THROUGH.
    #
    # ⛔ Read off the AST, not out of the text. A regex with a `m2... if `
    # alternative stayed green with the fall-through rewritten to `else "v3"`
    # (the #10983 review ran that mutation): the `if` clause matched and nobody
    # read the `else`. The IfExp's own `orelse` IS the fall-through, so it is
    # asserted directly — and the probe's DOOR_LANE is bound to it, because that
    # literal is what every acceptance log prints.
    node = _funcs(HOSTED)["_capture_lane"]
    rets = [n for n in ast.walk(node) if isinstance(n, ast.Return)]
    assert len(rets) == 1, (
        f"_capture_lane at {_cite(HOSTED, '_capture_lane')} no longer a single "
        f"return — the lane predicate is now a branch, so this pin must be "
        f"re-derived (#10983)")
    expr = rets[0].value
    assert isinstance(expr, ast.IfExp), (
        f"the selector's return is no longer an inline `m2 if … else …` "
        f"expression (#10983): {body!r}")
    assert isinstance(expr.body, ast.Constant) and expr.body.value == "m2", (
        f"the selector's OPT-IN branch is no longer the literal 'm2' at "
        f"{_cite(HOSTED, '_capture_lane')}: {body!r}")
    assert isinstance(expr.orelse, ast.Constant) and expr.orelse.value == "v2", (
        f"the selector's FALL-THROUGH is {ast.unparse(expr.orelse)!r}, not 'v2' "
        f"— v2 must be the default lane (#10983): {body!r}")
    assert isinstance(expr.test, ast.Compare), (
        f"the selector's test is no longer a comparison (#10983): "
        f"{ast.unparse(expr.test)!r}")
    # Compared STRUCTURALLY: ``ast.unparse`` normalises quotes (it renders 'm2'),
    # so a substring test on the rendered text is a trap — read the Constant.
    assert any(isinstance(c, ast.Constant) and c.value == "m2"
               for c in expr.test.comparators), (
        f"the selector's test no longer compares against 'm2' (#10983): "
        f"{ast.unparse(expr.test)!r}")
    assert "TORTOISE_SESSION_EXTRACTOR" in ast.unparse(expr.test), (
        f"the selector's test no longer reads TORTOISE_SESSION_EXTRACTOR "
        f"(#10983): {ast.unparse(expr.test)!r}")

    probe = _load_probe()
    assert probe.DOOR_LANE == expr.orelse.value, (
        f"the probe pins DOOR_LANE={probe.DOOR_LANE!r} but the selector's "
        f"fall-through is {expr.orelse.value!r} — every acceptance log would "
        f"declare a lane the code does not run (#10983)")


def _hosted_capture_targets() -> list[str]:
    """The sdk extractor each hosted capture call actually hands the worker.

    Resolved from the CALL SITE, never from a substring: ``sdk._extract_session_v2``
    also appears in a COMMENT in that module, so "the string is in the file"
    stayed true with the v2 arm rewired to ``_extract_session_llm`` (#10983
    review, cycle 3).
    """
    targets = []
    for node in ast.walk(_tree(HOSTED)):
        if isinstance(node, ast.Call) and \
                ast.unparse(node.func).endswith("_run_capture_bounded") and \
                len(node.args) >= 2:
            targets.append(ast.unparse(node.args[1]))
    return targets


def test_the_v2_arm_calls_the_sdk_extractor_entry():
    """The chain is a chain: hosted v2 arm → sdk method → extractor entry.

    ⛔ Every assertion below reads a CALL SHAPE, never a bare name. The first
    revision grepped for names: `"sdk._extract_session_v2"` was satisfied by a
    COMMENT in `hosted_api.py`, and `"extract_session_v2"` by
    `_extract_session_v2`'s docstring plus its own import line — so deleting the
    real call left this test GREEN (#10983 review).
    """
    # BOTH arms, resolved from the AST: the target each hosted capture hands the
    # worker. Requiring both is what makes a SWAPPED arm red, which a single
    # call-shape match on the v2 arm cannot see.
    targets = _hosted_capture_targets()
    assert "sdk._extract_session_v2" in targets, (
        f"the hosted capture no longer CALLS sdk._extract_session_v2 — it hands "
        f"{targets} to the worker, so the v2 arm of _capture_lane would name a "
        f"lane nothing runs (#10983)")
    assert "sdk._extract_session_llm" in targets, (
        f"the hosted capture no longer CALLS sdk._extract_session_llm for the "
        f"m2 arm — it hands {targets} to the worker, so 'm2' would route to a "
        f"lane nobody dispatches to (#10983)")
    sdk_body = _body(SDK, "_extract_session_v2")
    assert re.search(r"out = extract_session_v2\(", sdk_body), (
        f"sdk._extract_session_v2 at {_cite(SDK, '_extract_session_v2')} no "
        "longer CALLS extractor_v2.extract_session_v2 (its docstring and its "
        "import both name the function, so only the call form proves the hop)")
    assert "extract_session_v2" in _funcs(EXTRACTOR), (
        f"extractor_v2.extract_session_v2 is GONE from "
        f"{EXTRACTOR.relative_to(REPO)} — the door's last hop does not exist")


def _llm_call_branch() -> ast.If | None:
    """The ``if`` whose BODY dispatches ``sdk._extract_session_llm``.

    The branch, not the module: ``TORTOISE_SESSION_EXTRACTOR`` is also compared
    with ``!=`` elsewhere in this module for unrelated guards, so "no ``!=``
    anywhere" would be a false alarm, and "the env name appears" is satisfied by
    the capture LABEL. This pins the one branch that chooses the other extractor.
    """
    def _is_llm_capture(call: ast.Call) -> bool:
        return (ast.unparse(call.func).endswith("_run_capture_bounded")
                and len(call.args) >= 2
                and ast.unparse(call.args[1]) == "sdk._extract_session_llm")

    for node in ast.walk(_tree(HOSTED)):
        if not isinstance(node, ast.If):
            continue
        for stmt in node.body:
            if any(isinstance(c, ast.Call) and _is_llm_capture(c)
                   for c in ast.walk(stmt)):
                return node
    return None


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
    # ⛔ The BRANCH that dispatches the other extractor, structurally. The
    # previous revision read ``_capture_lane`` here — the LABEL the hosted path
    # stamps on the capture, NOT the branch that chooses the extractor. Inverting
    # the real branch left this test green (#10983 review, cycle 3).
    branch = _llm_call_branch()
    assert branch is not None, (
        "the hosted endpoint no longer dispatches sdk._extract_session_llm from "
        "a guarded branch — the two lanes are no longer distinguishable by "
        "their call sites (#10983)")
    test_src = ast.unparse(branch.test)
    assert "os.environ.get('TORTOISE_SESSION_EXTRACTOR')" in test_src or \
        'os.environ.get("TORTOISE_SESSION_EXTRACTOR")' in test_src, (
        f"the m2 branch is no longer guarded by the extraction lane variable "
        f"(#10983): {test_src!r}")
    assert any(isinstance(op, ast.Eq) for op in branch.test.ops), (
        f"the m2 branch now tests {test_src!r} — an inverted comparison flips "
        f"every hosted capture to the other extractor while the log still "
        f"declares the v2 lane (#10983)")
    assert any(isinstance(c, ast.Constant) and c.value == "m2"
               for c in branch.test.comparators), (
        f"the m2 branch no longer compares against the literal 'm2' (#10983): "
        f"{test_src!r}")

    # …and the SDK's dispatch is the same comparison, so one door.
    sdk_dispatch = [
        ast.unparse(n.test) for n in ast.walk(_tree(SDK))
        if isinstance(n, ast.If)
        and "os.environ.get('TORTOISE_SESSION_EXTRACTOR')"
        in ast.unparse(n.test).replace('"', "'")
        and any(isinstance(op, ast.Eq) for op in n.test.ops)]
    assert sdk_dispatch, (
        "the SDK no longer has an `== 'm2'` dispatch on "
        "TORTOISE_SESSION_EXTRACTOR — the hosted mirror and the SDK have "
        "started to disagree about which lane is which (#10983)")
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
    # A non-bool is 'on' only if it is TRUTHY: `return None if value is None
    # else True` published 'on' for `0`, `''` and `'off'` alike, so a resolver
    # returning a falsy non-None would have made this gate DEMAND a false ledger
    # (found in the #10983 review). `_label_order_rng` returns None when unset,
    # which stays None.
    return None if value is None else bool(value)


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
    """§2 R10's positive half: the v4 CONNECT step is a CALL SITE, not a label.

    The capstone's E4 row is about operator edges being *wired and persisted*,
    so the claim needs a call site — not a function whose name happens to
    contain "operator". The two halves are pinned separately because they live
    in different modules: ``extractor_v2`` only *emits* the payload's operator
    list, while the PERSISTENCE happens on the SDK side, where
    ``_extract_session_v2`` hands that list to ``apply_payload_operators``.

    ⛔ The previous version of this test matched any function NAME containing
    "operator" in ``extractor_v2``. The only two matches were a pure helper
    (``_operator_endpoint_surfaces``) and a prune (``_prune_held_operators``),
    so it stayed GREEN with the operator wiring deleted — a name grep cannot
    prove a writer exists. Found in the #10983 review; fixed by resolving the
    real call site.
    """
    v2_src = _source(EXTRACTOR)
    assert re.search(r'"operators"\s*:\s*payload_operators', v2_src), (
        "extractor_v2 no longer builds the payload's operator list — the "
        "emitter half of CONNECT is gone, so E4 has no operator data to "
        "persist (#7949 §2 R10)")

    # ⛔ _body, never _resolve: the name also appears in the docstring, so a
    # substring test over the whole segment stayed GREEN with the real call
    # deleted (measured in the #10983 review — this file's own _body() helper
    # exists for exactly this class, and the E4 "wired and persisted" claim is
    # only a call-site claim if the CALL is what is read).
    body = _body(SDK, "_extract_session_v2")
    assert "apply_payload_operators(" in body, (
        "sdk._extract_session_v2 no longer CALLS apply_payload_operators — the "
        "v2 lane would emit operator edges that nothing persists, and E4's "
        "'wired and persisted' claim would have no call site to name "
        "(#7949 §2 R10)")


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
    # The three the #10983 review added: each changes what the run MEASURES
    # through this door, and none was pinned in the first revision.
    assert "TORTOISE_SESSION_LLM_MOCK" in probe.DOOR_PINNED_OFF, (
        "TORTOISE_SESSION_LLM_MOCK is the offline model seam: =1 swaps the "
        "model for _V2SessionMock and short-circuits the provider-key check, "
        "so an inherited mock makes every capture canned output while "
        "backend_ready() still passes and the log claims a real run")
    assert "TORTOISE_EXTRACTOR_PROMPT" in probe.DOOR_PINNED_OFF, (
        "TORTOISE_EXTRACTOR_PROMPT=compact renders a different master prompt "
        "(explicitly not byte-identical since #9733) — pinned off or two runs "
        "of the arm differ for a non-arm reason")
    assert "TORTOISE_EXTRACT_MODEL" in probe.DOOR_PINNED_OFF, (
        "TORTOISE_EXTRACT_MODEL selects the extractor's model AND the legacy "
        "4000-token bound — pinned off so the acceptance runs the default "
        "producer and its truncation contract")


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


def test_the_probe_refuses_a_LEAKED_PINNED_OFF_switch(monkeypatch):
    """Every pinned-off switch is re-read and refused, not only the lane.

    ⛔ Found in the #10983 review: ``DOOR_PINNED_OFF`` pops three vars, but the
    refusal block checked only the lane, so an unpoppable ``TORTOISE_EXTRACTOR``
    or ``TORTOISE_LABEL_ORDER`` was recorded in ``observed``, printed in the
    ``door:`` line, and silently accepted — while the registration said "every
    one is written in-process, read back, and a run whose read-back disagrees is
    refused". A pin that cannot fail is a declaration, not a control, so each
    non-lane member is exercised here.
    """
    probe = _load_probe()

    class _Unpoppable(dict):
        """Accepts the pin, refuses to apply it — the value leaks back out."""

        def pop(self, key, default=None):
            return self.get(key, default)

    others = [k for k in probe.DOOR_PINNED_OFF if k != probe.DOOR_LANE_ENV]
    assert others, (
        "DOOR_PINNED_OFF pins only the lane — the other switches are back to "
        "being declarations (see the #10983 review)")
    for key in others:
        env = _Unpoppable(os.environ)
        env[key] = "leaked"
        monkeypatch.setattr(probe, "os", type(
            "os", (), {"environ": env})())
        with pytest.raises(SystemExit) as exc, probe.measured_arm():
            pass
        assert "REFUSED" in str(exc.value) and key in str(exc.value), (
            f"a leaked {key} was accepted: the run would declare a pinned-off "
            f"seam it did not actually run (got {exc.value})")


def test_an_unmeasured_door_never_claims_a_read_back():
    """Absence of a measurement is not a held pin, and the text must say so.

    ⛔ Found in the #10983 review. ``render_log``'s ``door`` defaults to ``None``
    and the repo's own fixtures call it that way, while every pinned-off target
    state is ``<unset>`` — so a header rendered from NOTHING printed
    byte-identical prose to one whose pins HELD, including "read back from
    os.environ", and ``door_record({})`` carried the measured record's own
    ``source`` sentence. A log that asserts a control ran when nothing was
    measured is exactly what ``measured_arm`` refuses in the ``arm:`` line; the
    door follows the same rule.
    """
    probe = _load_probe()
    header = probe.door_header({})
    assert "NOT MEASURED" in header, header
    assert "read back from os.environ" not in header, header
    record = probe.door_record({})
    assert record["measured"] is False, record
    assert "pinned_off" not in record and "source" not in record, record
    # A read-back of an all-unset set IS a measurement (that is the passing
    # state), so it must not be confused with the absent one.
    all_unset = {key: None for key in
                 probe.DOOR_PINNED_OFF + tuple(k for k, _ in probe.DOOR_PINNED_ON)}
    assert probe.door_record(all_unset)["measured"] is True
    assert "read back from os.environ" in probe.door_header(all_unset)


def test_the_probe_refuses_a_PINNED_ON_switch_that_reads_back_wrong(monkeypatch):
    """The pinned-ON half of the same rule: a pin that cannot fail is a claim.

    ⛔ Found in the #10983 review (cycle 3): the cycle-2 fix covered only the
    pinned-OFF members, so deleting the whole ``DOOR_PINNED_ON`` refusal block
    left 15 tests green while the registration states that every value is
    re-read and a mismatch REFUSES the run.
    """
    probe = _load_probe()
    assert probe.DOOR_PINNED_ON, "no pinned-ON switch is declared at all"

    class _Sticky(dict):
        """Accepts ONE assignment and ignores it — that pin does not take.

        Only the target key is swallowed: the measured arm's own writes must
        land, or the earlier ``ARM_ON`` refusal fires first and this test would
        pass for the wrong reason.
        """

        stuck: str = ""

        def __setitem__(self, key, value):
            if key == self.stuck:
                return
            super().__setitem__(key, value)

    for key, expected in probe.DOOR_PINNED_ON:
        env = _Sticky(os.environ)
        env.stuck = key
        env[key] = "0"          # the value it will READ BACK, not the pin
        monkeypatch.setattr(probe, "os", type(
            "os", (), {"environ": env})())
        with pytest.raises(SystemExit) as exc, probe.measured_arm():
            pass
        assert "REFUSED" in str(exc.value) and key in str(exc.value), (
            f"{key} did not read back as pinned {expected!r} and the run was "
            f"accepted: it would declare an arm it did not run (got "
            f"{exc.value})")


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


def test_the_PUBLISHED_ledgers_are_the_codes_not_a_second_transcription():
    """The two PUBLISHED copies of the ledger must match the CODE, row for row.

    ``test_the_seam_ledger_is_the_codes_not_the_issues`` compares the code
    against a literal in THIS file. That leaves the two artifacts a reader
    actually sees — ``probe.SEAM_LEDGER`` (what every acceptance log prints) and
    ``preregistration.json``'s ``door.seams`` (what the record states) — compared
    to nothing at all. Set either copy's ``TORTOISE_VALUE_GATE`` default back to
    ``"off"`` and all 14 tests still passed, re-publishing exactly the false
    ledger #10996 created. That is the drift this PR exists to correct, so it is
    pinned here rather than described.
    """
    probe = _load_probe()
    published_probe = {env: default
                       for env, _where, default, _seam in probe.SEAM_LEDGER}
    record = json.loads(REGISTRATION.read_text(encoding="utf-8"))
    published_json = {row["env"]: row["default"]
                      for row in record["door"]["seams"]}

    expect = {}
    for env, where, _default, _seam in probe.SEAM_LEDGER:
        module, _, resolver = where.partition(".")
        expect[env] = "on" if _default_of(module, resolver) else "off"

    assert published_probe == expect, (
        "probe.SEAM_LEDGER is not the code's ledger — every acceptance log "
        f"prints it verbatim:\n  published: {published_probe}\n  code:      "
        f"{expect}")
    assert published_json == expect, (
        "preregistration.json door.seams is not the code's ledger — the "
        f"registration states it as the record:\n  published: "
        f"{published_json}\n  code:      {expect}")

    # …and the DOCUMENT is a THIRD copy, read by humans. It was unpinned until
    # the #10983 review (cycle 3): setting its VALUE_GATE row back to off left
    # the suite green, re-publishing in §1.6.2 exactly the false ledger this PR
    # corrects — while §1.6.2 claimed "the pin suite resolves every row against
    # the modules so it cannot rot quietly".
    published_md = _md_seam_table()
    md_view = {env: published_md.get(env) for env in expect}
    assert md_view == expect, (
        f"PRE-REGISTRATION.md §1.6.2 is not the code's ledger — that table is "
        f"what a reader takes as the record:\n  document: {md_view}\n  "
        f"code:     {expect}")


def _md_seam_table() -> dict[str, str]:
    """§1.6.2's six rows, parsed out of the registration document itself."""
    text = REGISTRATION.with_name("PRE-REGISTRATION.md").read_text(encoding="utf-8")
    rows: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(
            r"\|\s*`(TORTOISE_[A-Z0-9_]+)`\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|", line)
        if match:
            env, _resolver, default = match.groups()
            token = default.replace("*", "").replace("⚠️", "").strip().lower()
            rows[env] = "on" if token.startswith("on") else "off"
    return rows


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
    # The ROW SET is required, not merely 'some rows exist': deleting E4 (the row
    # this PR got wrong twice) left the previous revision of this test green.
    assert set(rows) == {"E1", "E1b", "E2a", "E2b", "E3", "E4", "E5",
                         "fleet-claim"}, (
        f"the registered rows moved: {sorted(rows)} — every acceptance row must "
        f"carry its own corpus attribution, or a row silently loses one (#10983)")
    # …and each row's LANE is a statement about which extractor measured it, so
    # it is pinned rather than left to prose. E4 was published as an m2 row and
    # is not one: the receipt the probe selects records extract_session_v2.
    assert rows["E4"]["lane"] == "v2", (
        f"E4's lane is {rows['E4'].get('lane')!r}; the receipt select_e4_receipt "
        f"picks on the frozen corpus_hash records extract_session_v2 (#10983 "
        f"review): {rows['E4']}")
    assert rows["E1b"]["lane"] == "m2", (
        f"E1b's lane is {rows['E1b'].get('lane')!r}; it is the m2 anti-gaming "
        f"control (#10983)")
    for crit, entry in rows.items():
        assert entry["corpus"] in {
            "tests/eval/write_path",         # the registered fixture corpus
            "fleet-capture",                 # pinned by NOBODY yet: may not be scored
            "the run's own door-written graph",  # E3: shape of whatever the run wrote
        }, (
            f"row {crit} names an unknown corpus {entry['corpus']!r} — a carrier "
            f"nobody defined is the prose this block exists to replace")
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
    # The PUBLISHED counts, pinned exactly. §1.6.4 of the registration and this
    # record's door.negative_control state these numbers; until the #10983 review
    # they appeared in the DOCUMENTS and in this docstring only, while the
    # assertions below compared the two arms to EACH OTHER — so both published
    # copies could drift from the measurement with the suite green. They are
    # deterministic under the offline mock seam, so exact equality is a fact,
    # not a flake.
    assert door["counts"] == {"Object": 1, "Point": 27, "Event": 2,
                              "Session": 1, "Operator": 0}, (
        f"the door's census moved: {door['counts']} — §1.6.4 and "
        f"door.negative_control publish Object=1 Point=27 Event=2 (#10983)")
    assert other["counts"] == {"Object": 0, "Point": 149, "Event": 1,
                               "Session": 1, "Operator": 0}, (
        f"the m2 lane's census moved: {other['counts']} — door.negative_control "
        f"publishes Object=0 Point=149 Event=1 (#10983)")
    assert door["objects"] == ["the strategy"], (
        f"the door's :Object names moved: {door['objects']} — the published "
        f"control says ['the strategy'] (#10983)")
    assert other["objects"] == [], (
        f"the m2 lane now mints :Object names: {other['objects']} — the "
        f"published control says it mints none (#10983)")
    # …and the PUBLISHED string must carry those exact cells, or the record can
    # state a census this suite never measured. The numbers below were in the
    # documents and in the docstring only until the #10983 review.
    published = json.loads(REGISTRATION.read_text(encoding="utf-8"))[
        "door"]["negative_control"]["measured"]
    for arm, census in (("v2 (the door)", door), ("m2 (the other lane)", other)):
        cells = " ".join(f"{k}={v}" for k, v in census["counts"].items())
        # Bind the CENSUS to the SEGMENT its label introduces. A bare
        # `cells in published` is satisfied by either arm's numbers (they are
        # both in one string), so swapping the two halves — inverting the
        # control's own claim — stayed green even after the label was added to
        # the match (#10983 review, cycles 3 and 4).
        segment = re.search(re.escape(arm) + r": ([^;]+)", published)
        assert segment, (
            f"door.negative_control publishes no {arm!r} segment — the record "
            f"no longer attributes its numbers to an arm (#10983): {published!r}")
        assert segment.group(1).strip().startswith(cells), (
            f"door.negative_control's {arm!r} segment reads "
            f"{segment.group(1).strip()!r}, not the {cells!r} this run "
            f"measured — the record and the control have drifted apart "
            f"(#10983): {published!r}")
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
