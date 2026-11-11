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
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

HOSTED = REPO / "tortoise/hosted_api.py"
SDK = REPO / "tortoise/sdk.py"
EXTRACTOR = REPO / "tortoise/extractor_v2.py"
VALUE_GATE = REPO / "tortoise/value_gate.py"
SEAM = REPO / "tortoise/s2_decision_seam.py"
REFERENTIALITY = REPO / "tortoise/referentiality.py"
#: Round 7 of the #10983 review: the closure was still short of the DOOR. Both of
#: these are consulted on the capture path and were in no ledger, no pin and no
#: closure — `context_enrich` via `sdk._capture_session(enrich_fn=_capture_enriched_
#: texts)` (it REWRITES the text each turn embeds) and `model_adapters` via
#: `sdk._extract_session_v2` -> `_model_adapter` (it decides the request's
#: `response_format`). A new read inserted in `context_enrich.enrich_enabled()`
#: left the closure pin GREEN, which is the same claim-outruns-its-derivation
#: defect the whole PR exists to close, in its third round.
CONTEXT_ENRICH = REPO / "tortoise/context_enrich.py"
MODEL_ADAPTERS = REPO / "tortoise/model_adapters.py"
CHANGE_FEED = REPO / "tortoise/change_feed.py"
#: A WHOLE env-var name, so a message string that merely starts with a flag
#: (``"TORTOISE_EXTRACTOR_PROVIDER=openrouter but …"``) is not counted as a read.
_ENV_NAME = re.compile(r"TORTOISE_[A-Z0-9_]+")

#: Round 9 of the #10983 review, and the reason this list keeps growing: an
#: wide: `sparse.py:226` reads ``TORTOISE_IDENTIFIER_TOKENS`` (default ON) and
#: `projection/entities.py:1478` gates the alias derivation on it
#: (`point_identifier_aliases`), reached from the write path the acceptance
#: takes (`sdk.capture_session` -> `_extract_session_v2` -> `create_point`), so
#: it REWRITES `search_keys` on every ``:Point`` the run writes. MEASURED on the
#: registered corpus at the previous head: not in `SEAM_LEDGER`, not in
#: `DOOR_PINNED_OFF`/`ON`, not in either acknowledgement list, and `sparse.py` in
#: `path_modules` was False — an unclassified write-path switch under a door the
#: log called pinned. Its effect on the registered numbers is nil (0 of 27 points
#: yield an alias; 0 turns across the 7 fixtures), which is why it is a closure
#: defect and not a wrong figure.
SPARSE = REPO / "tortoise/sparse.py"
#: Round 10: `subject_binding.py` reads two `TORTOISE_*` variables through a
#: PARAMETER (`:210-211 _pick(tau_hi, "TORTOISE_SUBJECT_TAU_HI", …)` -> `:189
#: os.environ.get(env_name)`), reached from the write path the acceptance takes
#: (`sdk.py:17089 bind_point_subjects` inside `_extract_session_v2`, on a genuine
#: create), and it gates whether the `:aboutSubject`/`:aboutObject` edge is
#: written at all (`:408 decide_binding` -> `if outcome != BOUND: continue` at
#: `:409-416`; `:405` is the `kind_not_subject` refusal, not this gate).
#: MEASURED: `resolve_thresholds()` -> `(0.7, 0.4)` and `decide_binding(0.90)` ->
#: `bound`; with `TORTOISE_SUBJECT_TAU_HI=0.99` -> `(0.99, 0.4)` and `suspected`,
#: i.e. the edge is not written. `_env_reads` returns the EMPTY SET for that
#: module because the literal is passed to a helper as an argument — which is
#: why the census below reads LITERALS as well as resolved reads.
SUBJECT_BINDING = REPO / "tortoise/subject_binding.py"
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
    assert expr.orelse.value == probe.DOOR_LANE, (
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
    # worker. ⛔ Requiring both is NOT what catches a SWAPPED arm — a swap leaves
    # both targets present, so these two assertions stay GREEN through it and the
    # swap is caught by `test_the_sdk_lane_dispatch_and_the_hosted_mirror_agree`,
    # whose `_llm_call_branch()` resolves which ARM holds the m2 target (#10983
    # review, cycle 9: the previous wording credited a swap-detection this
    # assertion set does not have, and an editor simplifying the mapping check on
    # the strength of it would turn a swap into a silent false PASS). What these
    # two DO catch is a lost call: an arm rewired to a callee, or one deleted.
    targets = _hosted_capture_targets()
    assert "sdk._extract_session_v2" in targets, (
        f"the hosted capture no longer CALLS sdk._extract_session_v2 — it hands "
        f"{targets} to the worker, so the v2 arm of _capture_lane would name a "
        f"lane nothing runs (#10983)")
    assert "sdk._extract_session_llm" in targets, (
        f"the hosted capture no longer CALLS sdk._extract_session_llm for the "
        f"m2 arm — it hands {targets} to the worker, so 'm2' would route to a "
        f"lane nobody dispatches to (#10983)")
    # ⛔ Resolve the CALL, not the text. `_body()` strips the docstring but NOT
    # COMMENTS, so `# out = extract_session_v2(...)` satisfied a regex over the
    # segment and left this GREEN with the real hop deleted (MEASURED at the
    # #10983 review, cycle 7 — the same defect the CONNECT pin had, one sibling
    # assertion over). The `out = …` shape is kept: the hop assigns the
    # extractor's return to the caller's variable.
    hop = [node for node in ast.walk(_funcs(SDK)["_extract_session_v2"])
           if isinstance(node, ast.Assign)
           and isinstance(node.value, ast.Call)
           and ast.unparse(node.value.func) == "extract_session_v2"]
    assert hop, (
        f"sdk._extract_session_v2 at {_cite(SDK, '_extract_session_v2')} no "
        "longer CALLS extractor_v2.extract_session_v2 — its docstring, its "
        "import and a bare COMMENT all name the function, so only a resolved "
        "call proves the hop (#10983)")
    assert any(any(kw.arg == "sdk" for kw in node.value.keywords)
               for node in hop), (
        "sdk._extract_session_v2 calls extractor_v2.extract_session_v2 WITHOUT "
        "passing `sdk=` — the extractor would run without the SDK surface the "
        "door's record names (#10983)")
    # §1.6.1 also names the OTHER lane's module and class in prose, and the
    # sentence above promises the suite resolves every citation. Resolve them:
    # relocating tortoise/extractor.py (move + fix the import) left all 18 tests
    # green while the record's path rotted — the #8506 class (#10983 review,
    # cycle 4).
    m2_module = REPO / "tortoise/extractor.py"
    classes = {node.name for node in ast.walk(_tree(m2_module))
               if isinstance(node, ast.ClassDef)}
    assert "LLMExtractor" in classes, (
        "tortoise/extractor.py no longer defines LLMExtractor — §1.6.1 and "
        "door.not_the_door cite it as the m2 lane's extractor (#10983)")
    # …resolved as a CALL for the same reason: a comment naming the builder
    # satisfied the substring form (MEASURED, #10983 review cycle 7).
    built = [node for node in ast.walk(_funcs(SDK)["_extract_session_llm"])
             if isinstance(node, ast.Call)
             and ast.unparse(node.func).endswith("_build_session_llm_extractor")]
    assert built, (
        "sdk._extract_session_llm no longer CALLS _build_session_llm_extractor — "
        "the non-default lane has no reachable implementation (#10983); prose "
        "or a comment naming the builder cannot satisfy this")

    assert "extract_session_v2" in _funcs(EXTRACTOR), (
        f"extractor_v2.extract_session_v2 is GONE from "
        f"{EXTRACTOR.relative_to(REPO)} — the door's last hop does not exist")


def _sdk_lane_branch() -> ast.If | None:
    """The ``if`` in ``capture_session`` whose BODY calls the m2 extractor.

    The SDK's shape is a direct call (``self._extract_session_llm(...)``), not the
    hosted path's ``_run_capture_bounded(slot, sdk._extract_session_llm, ...)``,
    so this resolves the branch by the CALL it makes. Module-wide matching is
    what an earlier revision did and it was satisfied by the capture LABEL
    (``_capture_extractor_record``), not by the dispatch (#10983 review).
    """
    def _calls_llm(node: ast.AST) -> bool:
        return (isinstance(node, ast.Call)
                and ast.unparse(node.func).endswith("_extract_session_llm"))

    for node in ast.walk(_funcs(SDK)["_capture_session"]):
        if not isinstance(node, ast.If):
            continue
        if any(_calls_llm(c) for stmt in node.body for c in ast.walk(stmt)):
            return node
    return None


def _extraction_bindings(func: ast.AST) -> list[ast.Assign]:
    """Every assignment in ``func`` that BINDS ``extracted`` — the extractor's output.

    ⛔ Anchored on the VALUE FLOW, never on a callee spelling. Whatever a lane is
    called, its result has to reach ``extracted`` to be used for anything, so a
    third dispatch is visible even when its name shares no prefix with the two
    published ones. MEASURED at the #10983 review (cycle 8): the previous
    predicate matched ``ast.unparse(node.func).startswith("self._extract")``, so
    an ``elif`` dispatching ``self._dispatch_lane("v3", ...)`` — which of course
    assigns to ``extracted`` — left ``len(capture_ifs) == 1`` GREEN while it
    rerouted every capture. A callee this test cannot name must red, not pass.
    """
    def _targets(node: ast.AST) -> list[ast.AST]:
        if isinstance(node, ast.Assign):
            return list(node.targets)
        if isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            return [node.target]
        return []

    def _binds(node: ast.AST) -> bool:
        if getattr(node, "value", None) is None:      # a bare annotation binds nothing
            return False
        return any(isinstance(n, ast.Name) and n.id == "extracted"
                   for target in _targets(node) for n in ast.walk(target))

    return [node for node in ast.walk(func)
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign,
                                 ast.NamedExpr)) and _binds(node)]


def _lane_dispatch_ifs(func: ast.AST) -> list[ast.If]:
    """Every ``if`` in ``capture_session`` whose BODY dispatches an SDK lane.

    A "dispatch" is an assignment that binds ``extracted`` from a CALL (see
    ``_extraction_bindings``) — a structural fact about the value flow, not a
    name prefix, so a renamed second lane is counted like any other.

    Why the SET and not just the resolved arm (``_sdk_lane_branch``): that helper
    returns the FIRST If whose body calls the m2 extractor — the INNERMOST arm —
    so an ``elif`` added ABOVE it becomes a PARENT If and every arm-level
    assertion in the dispatch test stays green while the door widens. MEASURED
    at the #10983 review, cycle 7: ``elif os.environ.get("TORTOISE_MUTANT_ARM")
    == "1": extracted, meta = self._extract_session_llm_variant(...)`` inserted
    above the m2 arm left BOTH door tests passing.
    """
    dispatched = {id(binding) for binding in _extraction_bindings(func)
                  if isinstance(binding.value, ast.Call)}

    def _dispatches(node: ast.If) -> bool:
        return any(id(stmt) in dispatched for stmt in node.body)

    return [node for node in ast.walk(func)
            if isinstance(node, ast.If) and _dispatches(node)]


def _lane_chain_gates(func: ast.AST, target: ast.If) -> list[ast.If]:
    """Every ``If`` between the function body and ``target``, via ``orelse`` links.

    The door is a CHAIN: ``if no_provider … elif replay … elif m2 … else v2``.
    An arm woven in ABOVE ``target`` becomes one of its ANCESTORS, so the length
    of this list is the chain's shape — the dimension a third arm that returns
    a payload without binding ``extracted`` would otherwise hide in (#10983
    review, cycle 8). Outermost last.
    """
    parent: dict[int, ast.AST] = {}
    for node in ast.walk(func):
        for child in ast.iter_child_nodes(node):
            parent[id(child)] = node
    gates: list[ast.If] = []
    cursor: ast.AST = target
    while True:
        ancestor = parent.get(id(cursor))
        if (isinstance(ancestor, ast.If) and len(ancestor.orelse) == 1
                and ancestor.orelse[0] is cursor):
            gates.append(ancestor)
            cursor = ancestor
        else:
            return gates


def _effectful_gates(func: ast.AST) -> dict[str, set[str]]:
    """Every ``If`` in ``func`` that can CHANGE what a capture returns.

    An arm is effectful when its OWN body returns, binds ``extracted``, or
    rebinds the door's input ``extraction``. ``_lane_chain_gates`` only walks
    ``orelse`` ANCESTORS of the m2 branch, so it cannot see an arm placed as a
    SIBLING above the chain, nor one nested inside another arm's body.
    MEASURED at the #10983 review (cycle 9), all green at the previous head:
    ``if os.environ.get("TORTOISE_MUTANT_ARM") == "1": return {...}`` as a
    sibling above ``if no_provider:``; the same as an early return from a callee
    of its choosing; a gate that rewrites ``extraction``; and a payload-returning
    gate nested inside the m2 arm's body. The door is the WHOLE FUNCTION the
    acceptance calls, not only the branch that happens to be its ancestor.
    """
    def _rebinds(stmt: ast.AST) -> bool:
        if getattr(stmt, "value", None) is None:
            return False
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets = [stmt.target]
        else:
            return False
        names = {n.id for target in targets for n in ast.walk(target)
                 if isinstance(n, ast.Name)}
        return bool(names & {"extracted", "extraction"})

    gates: dict[str, set[str]] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.If):
            continue
        kinds = {kind for kind, hit in (
            ("returns", any(isinstance(s, ast.Return) for s in node.body)),
            ("binds", any(_rebinds(s) for s in node.body)),
        ) if hit}
        if kinds:
            gates.setdefault(ast.unparse(node.test), set()).update(kinds)
    return gates


def _env_read_names_in(node: ast.AST) -> set[str]:
    """Every ``TORTOISE_*`` name read through a LITERAL argument under ``node``."""
    names: set[str] = set()
    for call in ast.walk(node):
        if not isinstance(call, ast.Call) or not call.args:
            continue
        if not ast.unparse(call.func).endswith(
                ("environ.get", "getenv", "env_flag", "ask_env_int")):
            continue
        arg = call.args[0]
        if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.startswith("TORTOISE_")):
            names.add(arg.value)
    return names


def _env_literals(path: Path) -> set[str]:
    """Every ``TORTOISE_*`` name SPELLED in this module, whatever reads it.

    The complement of :func:`_env_reads`: that function resolves the two read
    SHAPES it understands, this one collects the spellings. A module that hands
    its variable to a helper (``_pick(tau_hi, "TORTOISE_SUBJECT_TAU_HI", …)`` ->
    ``os.environ.get(env_name)``) is invisible to the first and obvious to this
    one — the blind spot that let `subject_binding.py` sit on the acceptance path
    unaccounted through five review rounds (#10983, cycle 10).

    Docstrings and message strings are excluded: only a WHOLE env-var name (the
    ``TORTOISE_[A-Z0-9_]+`` shape) counts, so
    ``"TORTOISE_EXTRACTOR_PROVIDER=openrouter but …"`` is prose, not a read.
    """
    tree = _tree(path)
    doc_nodes = {id(n.value) for n in ast.walk(tree)
                 if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                 and isinstance(n.value.value, str)}
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_nodes
            and _ENV_NAME.fullmatch(n.value) is not None}


def _env_reads(path: Path) -> set[str]:
    """Every ``TORTOISE_*`` name this module can READ, structurally.

    Two forms are resolved, because a source-text grep is satisfied both by prose
    and by a LONGER name that begins with the one being looked for
    (``TORTOISE_LABEL_ORDER`` ⊂ ``TORTOISE_LABEL_ORDER_SEED``,
    ``TORTOISE_EXTRACTOR`` ⊂ ``TORTOISE_EXTRACTOR_PROMPT``). Found in the #10983
    review (cycle 5): deleting a real read left the substring version green.

      * a CALL to one of the env readers with a ``TORTOISE_*`` string literal;
      * a module-level ``NAME = "TORTOISE_..."`` binding the module USES again —
        the one-indirection form ``referentiality.ENV_FLAG`` takes, which no
        literal-call grep can see.
    """
    tree = _tree(path)
    names = _env_read_names_in(tree)
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
            continue
        target = stmt.targets[0]
        if not (isinstance(target, ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
                and stmt.value.value.startswith("TORTOISE_")):
            continue
        uses = sum(1 for n in ast.walk(tree)
                   if isinstance(n, ast.Name) and n.id == target.id)
        if uses > 1:            # the binding itself, plus at least one USE
            names.add(stmt.value.value)
    return names


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

    # …and the SDK's dispatch is the same comparison, so one door — resolved to
    # the BRANCH THAT DISPATCHES the m2 extractor, not to a module-wide grep.
    # The module-wide form was satisfied by the capture LABEL
    # (`_capture_extractor_record` reads the same variable), and its list
    # comprehension required only *an* `Eq`, so swapping the comparator to
    # `"m2-legacy"` was caught by NOTHING — MEASURED in the #10983 review
    # (cycle 5): the published test file PASSED that mutant.
    sdk_branch = _sdk_lane_branch()
    assert sdk_branch is not None, (
        "nothing in sdk.capture_session dispatches _extract_session_llm from a "
        "guarded branch — the SDK has one lane, so the hosted mirror's two-lane "
        "predicate no longer describes it (#10983)")
    sdk_test = ast.unparse(sdk_branch.test)
    # ⛔ Pin the CHAIN, not the arm it resolves to. `_sdk_lane_branch()` finds the
    # first If whose body calls the m2 extractor — the innermost arm — so an arm
    # added ABOVE it is a PARENT If that no assertion below can see. MEASURED at
    # the #10983 review (cycle 7): an `elif` above the m2 arm dispatching a
    # behaviourally-identical variant rerouted every capture by ambient env while
    # both door tests stayed green. The door is the CHAIN, so require that
    # exactly one branch in capture_session dispatches an SDK extractor at all.
    capture_func = _funcs(SDK)["_capture_session"]
    capture_ifs = _lane_dispatch_ifs(capture_func)
    assert len(capture_ifs) == 1, (
        "sdk.capture_session dispatches an SDK extractor from "
        f"{len(capture_ifs)} branches — a third arm is a second door, and the "
        "acceptance pins exactly two lanes (#10983): "
        f"{[ast.unparse(node.test) for node in capture_ifs]}")
    # ⛔ The CALLEE SET, not a spelling. `len(capture_ifs) == 1` above is computed
    # from the value flow (an arm that binds `extracted` from a call), so this
    # pair is what names the two lanes the acceptance is allowed to run. A third
    # callable that feeds the extraction reds here even if it shares no prefix
    # with these two — a name the test cannot pronounce is a FAIL, never a pass
    # (#10983 review, cycle 8: `self._dispatch_lane(...)` in a third arm left the
    # previous prefix-matched predicate green).
    bindings = _extraction_bindings(capture_func)
    dispatched = [b for b in bindings if isinstance(b.value, ast.Call)]
    assert {ast.unparse(b.value.func) for b in dispatched} == {
            "self._extract_session_llm", "self._extract_session_v2"}, (
        "sdk.capture_session feeds `extracted` from "
        f"{sorted(ast.unparse(b.value) for b in dispatched)} — the acceptance "
        "pins exactly two lanes (self._extract_session_llm / "
        "self._extract_session_v2), so any other callee is a second door the "
        "record would still declare as v2 (#10983)")
    assert len(dispatched) == 2, (
        f"sdk.capture_session dispatches {len(dispatched)} extractors into "
        "`extracted` — exactly two are pinned, one per lane (#10983): "
        f"{[ast.unparse(b.value) for b in dispatched]}")
    indirect = [b for b in bindings if not isinstance(b.value, ast.Call)]
    laundered = [b for b in indirect
                 if not (isinstance(b.value, ast.List) and not b.value.elts)]
    assert not laundered, (
        "sdk.capture_session binds `extracted` without calling an extractor: "
        f"{[ast.unparse(b) for b in laundered]} — only the two empty "
        "short-circuit arms (no provider / replay) may do that; a dispatch "
        "laundered through a temporary is still a second door (#10983)")
    # ⛔ The door's own FUNCTION reads exactly ONE seam variable. `path_modules`
    # closes over the modules the capture path REACHES, and `sdk.capture_session`
    # is not one of them — so a new `TORTOISE_*` read added inside this function
    # was invisible to the closure as well as to the geometric pins (#10983
    # review, cycle 9). MEASURED: `if os.environ.get("TORTOISE_MUTANT_SEAM") ==
    # "1": extraction = <other window>` as a sibling above the chain left all 19
    # tests green while it rewrote the door's INPUT.
    door_reads = _env_read_names_in(capture_func)
    assert door_reads == {"TORTOISE_SESSION_EXTRACTOR"}, (
        "sdk.capture_session reads "
        f"{sorted(door_reads)} — the door function itself is pinned to exactly "
        "one seam variable, the one that selects the lane; a second read either "
        "reroutes the capture or rewrites its input, and `door_record()` would "
        "still print lane: v2 (#10983)")
    # ⛔ HOP 2, the middle one, not only hop 1. The door the record names is
    # `capture_session -> _extract_session_v2 -> extractor_v2.extract_session_v2`,
    # and only the FIRST function was pinned above; a new env-gated reroute
    # inserted INSIDE `_extract_session_v2` kept all 19 tests green while the
    # acceptance ran the other extractor — MEASURED at the #10983 review (cycle
    # 10): `if os.environ.get("TORTOISE_V5_EXTRACTOR") == "1": return
    # self._extract_session_llm(...)` inserted at `sdk.py:16324` left the suite
    # green, `measured_arm()` ACCEPTED the run (the variable was in no pin), and
    # the census came back as §1.6.4's m2 row while `door_record()["lane"]`
    # still read "v2". A hop is part of the door, so it is pinned like one: ONE
    # seam read, and exactly one return (the pair the pinned call produces).
    hop2 = _funcs(SDK)["_extract_session_v2"]
    hop2_reads = _env_read_names_in(hop2)
    assert hop2_reads == {"TORTOISE_EXTRACT_MODEL"}, (
        "sdk._extract_session_v2 reads "
        f"{sorted(hop2_reads)} — the door's middle hop is pinned to exactly the "
        "one seam it uses to build its model; any other read inside it can "
        "reroute the capture while `door_record()` still prints lane: v2 "
        "(#10983)")
    hop2_returns = [n for n in ast.walk(hop2) if isinstance(n, ast.Return)]
    assert len(hop2_returns) == 1 and \
        ast.unparse(hop2_returns[0].value) == "(extracted, meta)", (
        "sdk._extract_session_v2 has "
        f"{len(hop2_returns)} returns "
        f"{[ast.unparse(r.value)[:60] for r in hop2_returns]} — the middle hop "
        "must have exactly one exit, the (extracted, meta) pair the pinned "
        "extractor call produces; an early return is a reroute or a fabricated "
        "payload, and the record would still declare v2 (#10983)")
    # ⛔ The WHOLE function, not just the chain above the door. `_lane_chain_gates`
    # walks `orelse` ancestors, so an arm placed as a SIBLING above the chain — or
    # nested inside another arm's body — was invisible to every pin above.
    # MEASURED at the #10983 review (cycle 9), all green: a sibling
    # `if os.environ.get("TORTOISE_MUTANT_ARM") == "1": return {...}` above
    # `if no_provider:`; an early return from a callee of its own choosing; a gate
    # rewriting `extraction`; and a payload-returning gate nested in the m2 arm.
    effectful = _effectful_gates(capture_func)
    door_test = "os.environ.get('TORTOISE_SESSION_EXTRACTOR') == 'm2'"
    assert set(effectful) == {
        "_blank_session is not None",
        "not transcript.strip()",
        "no_provider",
        "session_existed and (not retry_failed_capture)",
        door_test,
    }, (
        "sdk.capture_session's set of arms that can change what a capture "
        f"returns is not the five pinned (got {len(effectful)}: two early-return "
        "gates, the two replay short-circuits, and the door). A sixth arm — above "
        "the chain, below it, or nested inside one of them — reroutes captures "
        "the record still declares v2 (#10983): "
        f"{sorted(effectful)}")
    assert effectful[door_test] == {"binds"}, (
        f"the m2 arm's effect changed: {sorted(effectful[door_test])} — it must be "
        "the arm that BINDS `extracted`, not one that also returns (#10983)")
    # ⛔ The CHAIN's shape above the door. An arm woven in above the m2 branch is
    # an ANCESTOR of it, whatever its body does, so the count is the door's own
    # geometry: `no_provider`, then `session_existed`, then the door. A third
    # gate here reds even if it returns a payload without binding `extracted`.
    chain_gates = _lane_chain_gates(capture_func, capture_ifs[0])
    assert len(chain_gates) == 2, (
        "sdk.capture_session's lane chain has "
        f"{len(chain_gates)} gates above the door, not the two pinned "
        "(no_provider, session_existed) — an arm woven in ABOVE the door "
        "reroutes captures the record still declares v2; re-derive this count "
        "deliberately rather than bumping it (#10983): "
        f"{[ast.unparse(g.test) for g in chain_gates]}")
    assert ast.unparse(capture_ifs[0].test) == sdk_test, (
        "the only lane-dispatching branch in sdk.capture_session tests "
        f"{ast.unparse(capture_ifs[0].test)!r}, not the m2 selector "
        f"{sdk_test!r} — an arm ABOVE the door reroutes captures the record "
        "still declares v2 (#10983)")
    assert "TORTOISE_SESSION_EXTRACTOR" in sdk_test, (
        f"the SDK's m2 branch is no longer guarded by the extraction lane "
        f"variable (#10983): {sdk_test!r}")
    assert any(isinstance(op, ast.Eq) for op in sdk_branch.test.ops), (
        f"the SDK's m2 branch now tests {sdk_test!r} — an inverted comparison "
        f"moves every session to the other extractor while the log still "
        f"declares the v2 lane (#10983)")
    assert any(isinstance(c, ast.Constant) and c.value == "m2"
               for c in sdk_branch.test.comparators), (
        f"the SDK's m2 branch no longer compares against the literal 'm2' "
        f"(#10983): {sdk_test!r}")
    # ⛔ The FALL-THROUGH arm too. Pinning only the opt-in arm leaves the arm the
    # acceptance ACTUALLY takes unpinned: MEASURED — adding a third `elif` off
    # this same chain calling a behaviourally-identical variant left every door
    # test green, and the negative control cannot see it either (the census is
    # identical). The door is the pair.
    fall_through = sdk_branch.orelse
    assert len(fall_through) == 1, (
        "the SDK's lane chain now has more than one fall-through statement "
        f"({[ast.unparse(s) for s in fall_through]}) — a second route off the "
        "door would run a capture the log still declares as v2 (#10983)")
    assert not any(isinstance(s, ast.If) for s in fall_through), (
        "the SDK's lane chain grew ANOTHER `elif` — a third arm is a second "
        "door; the acceptance pins exactly two lanes (#10983)")
    # …and the fall-through IS one call to that function, not an expression that
    # happens to contain one. An `IfExp` in the else arm
    # (`_extract_session_v2(...) if os.environ.get("TORTOISE_MUTANT_ARM") != "1"
    # else _variant(...)`) selects a second lane with the earlier shape checks
    # green — an `IfExp` is not an `ast.If` and the real call is still present
    # (MEASURED, #10983 review cycle 6). One statement, one call, named.
    assert len(fall_through) == 1 and isinstance(fall_through[0], ast.Assign), (
        f"the fall-through is {[ast.unparse(s) for s in fall_through]} — expected "
        f"a single assignment to the extractor pair (#10983)")
    fall_value = fall_through[0].value
    assert isinstance(fall_value, ast.Call) and \
        ast.unparse(fall_value.func) == "self._extract_session_v2", (
        f"the SDK's non-m2 arm calls {ast.unparse(fall_value)[:120]!r} — a "
        f"conditional expression or any other callee selects a second lane "
        f"while the door's record still declares v2 (#10983)")
    fall_calls = [c for s in fall_through for c in ast.walk(s)
                  if isinstance(c, ast.Call)]
    assert any(ast.unparse(c.func).endswith("self._extract_session_v2")
               for c in fall_calls), (
        "the SDK's non-m2 arm no longer calls self._extract_session_v2 — the "
        "capture would run some other path while the door's record still "
        f"declares v2 (#10983): {[ast.unparse(c.func) for c in fall_calls]}")
    hosted_body = _resolve(HOSTED, "_capture_lane")
    assert re.search(r'os\.environ\.get\("TORTOISE_SESSION_EXTRACTOR"\)', hosted_body)


def test_the_door_citation_is_derived_not_typed(tmp_path, monkeypatch):
    """A typed ``:16975`` is the #8506 rot class. Resolve it every run.

    ⛔ The resolution is checked against a source TEXT SCAN, not against the
    AST walk ``_cite`` is itself built from: comparing ``_cite`` to its own
    derivation is a tautology, and it stayed GREEN in the #10983 mutation pass
    when ``_cite`` was rewritten to return a typed constant — the dead-weight
    shape of #3442 rule 3, where an assertion's value cannot vary under any
    mutation of the diff.

    ⛔ And the text scan alone only catches a typed WRONG number (review at
    `7eb4a042`, P2): `scanned == [line]` is satisfied by a `_cite` rewritten to
    return a TYPED but currently-CORRECT line, so the rot class the row is
    named for — the number nobody re-derives — survives exactly as long as the
    tree has not moved yet. The second arm below shifts the source and requires
    the cite to MOVE with it: a typed constant cannot, so both halves of the
    class now red. Two mutants, two rows:

    * `_cite` → `return f"{path.relative_to(REPO)}:17469"` (typed, WRONG today)
      reds the scan;
    * `_cite` → `return f"{path.relative_to(REPO)}:17467"` (typed, RIGHT today)
      passes the scan and reds the shift.
    """
    cite = _cite(HOSTED, "_capture_lane")
    line = int(cite.rsplit(":", 1)[1])
    text = HOSTED.read_text(encoding="utf-8").splitlines()
    scanned = [n for n, ln in enumerate(text, 1)
               if ln.startswith("def _capture_lane")]
    assert scanned == [line], (
        f"the derived cite {cite} does not land on the def — _cite returned "
        f"{line} while a text scan finds `def _capture_lane` at {scanned}, so "
        f"the citation is TYPED rather than derived (or the derivation is "
        f"broken) (#8506)")
    # ⛔ No second `assert "def _capture_lane" in text[line - 1]` here. It is
    # STRICTLY SUBSUMED by the assertion above (`scanned == [line]` already
    # implies `text[line - 1].startswith("def _capture_lane")`), so it could
    # never fire on its own and its message advertised a diagnosis it could not
    # produce — the same #3442 rule-3 dead weight this test was rewritten to
    # remove, and it was caught in the row that rewrote it (review at
    # `7eb4a042`).

    # ── the SHIFT arm: a cite that cannot move is not a derivation ────────
    # Pad the same source with N leading comment lines and re-cite it. A real
    # derivation reads the def's line out of the file it is given, so it lands
    # N lines lower; a constant does not move at all. `REPO` is re-pointed at
    # the temp dir only so `_cite`'s `path.relative_to(REPO)` stays valid — no
    # file in the worktree is written.
    pad = 7
    shifted = tmp_path / "hosted_api_shifted.py"
    shifted.write_text("# shifted\n" * pad + _source(HOSTED), encoding="utf-8")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    moved = int(_cite(shifted, "_capture_lane").rsplit(":", 1)[1])
    assert moved == line + pad, (
        f"_cite returned {moved} for a copy of the module shifted down {pad} "
        f"lines, but the def is at {line + pad} there — the citation does not "
        f"follow the source it is given, so it is a TYPED number that happens "
        f"to be right today (#8506; review at 7eb4a042 P2)")


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
    """A seam name is a contract: the ledger's spelling must be the read.

    ⛔ Resolved from each RESOLVER's own body, docstring stripped — not from the
    module text. The earlier version asserted `flag in _source(EXTRACTOR)`, which
    every flag satisfies through a comment or a docstring: rewiring `_vet_enabled`
    to read `TORTOISE_VET_LEGACY` left all 18 tests green (#10983 review,
    cycle 4), so the acceptance arm's `TORTOISE_VET=1` and the ledger's spelling
    could control nothing while the log declared the pass armed.
    """
    assert 'ENV_FLAG = "TORTOISE_S2_DECISION_SEAM"' in _source(SEAM), (
        "s2_decision_seam.ENV_FLAG is no longer TORTOISE_S2_DECISION_SEAM — "
        "every acceptance log that pins that spelling is pinning nothing")

    paths = {"extractor_v2": EXTRACTOR, "value_gate": VALUE_GATE,
             "s2_decision_seam": SEAM}
    probe = _load_probe()
    for flag, where, _default, _seam in probe.SEAM_LEDGER:
        module, _, resolver = where.partition(".")
        path = paths[module]
        body = _body(path, resolver)
        reads_literal = f'"{flag}"' in body or f"'{flag}'" in body
        # Two resolvers read their flag through a module constant
        # (``os.environ.get(ENV_FLAG)``), so the literal is not in the body.
        # ⛔ But the CONSTANT has to be READ, not merely DEFINED: a name lookup
        # over the module is satisfied by `ENV_FLAG = "TORTOISE_S2_DECISION_SEAM"`
        # alone, and the cycle-9 review MEASURED that — rewriting
        # `s2_decision_seam.seam_enabled` to read `TORTOISE_VET` left all 19 tests
        # green, because the constant's DECLARATION was the whole evidence. So the
        # resolver body must pass the constant to an env reader as an argument —
        # parsed from the body, not grepped.
        reads_constant = (_module_env_flag(path) == flag
                          and _reads_the_constant(body, "ENV_FLAG"))
        assert reads_literal or reads_constant, (
            f"{module}.{resolver} does not read {flag} — the ledger names a "
            f"variable the resolver never reads, so pinning that spelling arms "
            f"nothing (#10983): {body[:220]!r}")


def _reads_the_constant(body_src: str, name: str) -> bool:
    """Does this function body pass ``name`` to an env reader as its ARGUMENT?

    ``body_src`` is a def's source segment (``_body`` drops the docstring), so it
    is re-parsed at its original column and walked structurally: a bare ``Name``
    mention is NOT enough — the constant must be the argument of a call, which is
    the only form that actually reads the environment through it.
    """
    try:
        tree = ast.parse(body_src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if not ast.unparse(node.func).endswith(
                ("environ.get", "getenv", "env_flag", "ask_env_int")):
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Name) and arg.id == name:
            return True
    return False


def _module_env_flag(path: Path) -> str | None:
    """The module-level ``ENV_FLAG`` constant, if it has one."""
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ENV_FLAG" \
                        and isinstance(node.value, ast.Constant):
                    return node.value.value
    return None


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

    # ⛔ A resolved CALL, never a substring — not `_resolve` (it includes the
    # DOCSTRING) and not `_body` with a substring either (it strips the docstring
    # but KEEPS COMMENTS, so `# apply_payload_operators(…) used to be here` was
    # enough; MEASURED in the #10983 review, cycle 6: real call deleted, comment
    # left behind, 1 passed). E4's "wired and persisted" claim is a CALL-SITE
    # claim, so the pin resolves a call.
    connect_calls = [
        node for node in ast.walk(_funcs(SDK)["_extract_session_v2"])
        if isinstance(node, ast.Call)
        and ast.unparse(node.func).endswith("apply_payload_operators")
    ]
    assert connect_calls, (
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
        "TORTOISE_EXTRACT_MODEL selects the extractor's model — pinned off so "
        "the acceptance runs the default producer")
    # Review round 2: the set must be CLOSED over what `extractor_v2` reads, or
    # the header's "every switch that changes what the run measures" is a claim
    # nothing checks. These two decide how much of each stage survives
    # truncation, and they are read at CALL time (`_stage_cap`,
    # `_extractor_escalation_tokens`), so an ambient value shrinks the measured
    # population under a door the log says is pinned.
    for key in ("TORTOISE_EXTRACTOR_MAX_TOKENS",
                "TORTOISE_EXTRACTOR_ESCALATION_TOKENS",
                "TORTOISE_LABEL_ORDER_SEED"):
        assert key in probe.DOOR_PINNED_OFF, (
            f"{key} is read on the capture path and changes what the run "
            f"measures (or can, if its sibling switch moves), and is not "
            f"pinned: an inherited value would be measured under a door whose "
            f"log claims it was pinned (#10983 review round 2)")
    # Cycle 11 (#10360): the census above would ALSO be satisfied by declaring
    # this one an acknowledged non-seam, which would be the wrong call — it is a
    # write-path guard, so its OFF arm writes a point the ON arm withholds.
    # Asserting the CLASSIFICATION, not merely that something accounts for it.
    assert ("TORTOISE_STATE_VALUE_VERBATIM", "1") in probe.DOOR_PINNED_ON, (
        "TORTOISE_STATE_VALUE_VERBATIM is #10360's write-path guard and is "
        "DEFAULT-ON (`extractor_v2._state_value_verbatim_enforced`), so its "
        "absent state is neutral and an ambient `=0` is the non-neutral state: "
        "OFF disarms `_refuse_rewritten_value`, and a point whose value the "
        "model REWROTE is then WRITTEN into the graph the run writes instead of "
        "withheld. It must be PINNED (to the shipped default), not absorbed by "
        "`acknowledged_not_a_measurement_seam` — an unclassified read is how an "
        "inherited shell changes the measured population under a door the log "
        "says is pinned (#10983 reviews)")


class _Unpoppable(dict):
    """Accepts a pin, refuses to apply it — a popped value leaks back out."""

    def pop(self, key, default=None):
        return self.get(key, default)


def _scrubbed_env(pin) -> _Unpoppable:
    """A copy of the real environment with every PINNED key removed first.

    ⛔ The two leak tests build their fake environ from ``os.environ``, so on a box
    that exports ANY pinned lever the leak they assert about is not the one under
    test: ``_Unpoppable.pop`` never deletes, so every ambient pin leaks too and
    ``measured_arm``'s refusal loop fires on the FIRST key in tuple order. MEASURED
    (round 11): `TORTOISE_EXTRACT_MODEL=deepseek/deepseek-chat` red
    ``test_the_probe_refuses_a_LEAKED_PINNED_OFF_switch`` with a refusal naming
    ``TORTOISE_EXTRACT_MODEL`` while the test was exercising
    ``TORTOISE_EXTRACTOR_MAX_TOKENS``. This is the same hazard the PINNED_ON test
    below already documents for exported levers; it is fixed once, here.
    """
    env = _Unpoppable((k, v) for k, v in os.environ.items() if k not in pin)
    return env


def test_the_probe_refuses_a_leaked_lane(monkeypatch):
    """Fail closed: a lane that does not read back as unset REFUSES the run."""
    probe = _load_probe()

    env = _scrubbed_env(tuple(probe.DOOR_KEYS))
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

    others = [k for k in probe.DOOR_PINNED_OFF if k != probe.DOOR_LANE_ENV]
    assert others, (
        "DOOR_PINNED_OFF pins only the lane — the other switches are back to "
        "being declarations (see the #10983 review)")
    for key in others:
        env = _scrubbed_env(tuple(probe.DOOR_KEYS))
        env[key] = "leaked"
        monkeypatch.setattr(probe, "os", type(
            "os", (), {"environ": env})())
        with pytest.raises(SystemExit) as exc, probe.measured_arm():
            pass
        assert "REFUSED" in str(exc.value) and key in str(exc.value), (
            f"a leaked {key} was accepted: the run would declare a pinned-off "
            f"seam it did not actually run (got {exc.value})")


def test_a_capture_that_never_ENTERED_the_door_REFUSES_the_run():
    """The door is a PATH, not only an environment fact (#10983 review r11).

    ⛔ ``measured_arm`` pins and reads back the LANE, and a leaked lane refuses —
    but neither a keyless capture (#3892) nor an ``#1727`` REPLAY is another
    lane: it is *no* lane, it reports ``ok=True``, and a re-run against an
    existing graph (``--graph``) reaches the replay for every session at zero
    cost. Until this pin, that run still wrote a table whose ``door:`` line
    asserted the v2 lane and whose record said ``measured: true``.
    """
    probe = _load_probe()
    entered = [{"session_id": "wp01",
                "extraction_mode": "llm:deepseek-direct", "ok": True}]
    assert probe.door_witness(entered) == {"entered": 1, "sessions": 1,
                                           "bypassed": []}
    assert probe.require_door_entered(entered)["entered"] == 1
    # ⛔ ``"llm"`` (bare) is the OTHER lane's spelling — ``sdk._extract_session_llm``
    # reports it with no route, pinned by
    # ``tests/test_capture_session.py::test_capture_session_zero_extraction_is_warning_not_failure``.
    # Without it here, aligning the m2 lane to ``llm:<route>`` (which the
    # ``capture_session`` docstring advertises as THE success shape) would widen
    # the door to the exact bypass ``DOOR_ENTERED_PREFIX`` exists to refuse, and
    # every other test in this file would stay green (#10983 review).
    for mode in ("replayed", "no-provider", "empty", "llm",
                 "extraction-disabled", None):
        runs = [entered[0], {"session_id": "wp02", "extraction_mode": mode,
                             "ok": True}]
        assert probe.door_witness(runs)["bypassed"] == ["wp02"]
        with pytest.raises(SystemExit) as exc:
            probe.require_door_entered(runs)
        assert "REFUSED" in str(exc.value) and "wp02" in str(exc.value), (
            f"a {mode!r} capture was accepted as door-entered: {exc.value}")


def test_a_capture_that_ENTERED_the_door_and_FAILED_is_not_a_BYPASS():
    """``error`` is a POST-dispatch outcome; a bypass it is not (#11298 r14).

    ⛔ ``error`` used to sit in the refused tuple above, and that was a measured
    defect, not a conservative default. ``sdk.capture_session`` maps a failed
    extraction onto ``extraction_mode="error"`` (its ``elif not ok:
    effective_mode = "error"`` branch) and the mode is emitted from INSIDE the
    lane — ``sdk._extract_session_v2``, i.e. THE DOOR, sets
    ``meta["mode"] = "error"`` iff its own ``errors`` is non-empty — so a
    receipt carrying it took the door and raised. (Its mode emission, and the two
    in the other lane, are cited by FUNCTION rather than by ``sdk.py:<line>``: a
    line number there re-stales on the next insertion, which is the defect class
    this whole file exists to retire. The file's TWO remaining ``sdk.py:<line>``
    cites are historical prose notes nothing resolves — ``:110``
    (``bind_point_subjects``) and ``:660`` (a cycle-10 mutation's insertion site)
    — so they are the same re-staling class, not an exception to it; this
    parenthetical is scoped to the door's own emissions and is not a claim about
    every cite in the file.)
    Refusing it did three wrong things at once: it named a cause that was false
    (``returned BEFORE sdk._extract_session_v2``), it did so AFTER the run's full
    spend, and because ``require_door_entered`` raises before the log is written
    it discarded the receipt's ``errors`` — the only thing that says what
    actually happened. The probe's own rule (``capstone_measure.py``, the
    ``REFUSED`` posture) is that a refusal naming a false reason is the one
    failure mode a refusal must not have.
    """
    probe = _load_probe()
    # The REAL shape: ``sdk.capture_session``'s ok-assembly sets ok=False
    # whenever ``extraction_errors`` is non-empty (``ok = not extraction_errors
    # and meta.get("mode") != "empty"``), so an ``error`` receipt can never carry
    # ok=True. The previous
    # version of this pin fed ``ok=True`` and was therefore asserting over an
    # input the SDK cannot produce.
    failed = {"session_id": "wp02", "extraction_mode": "error",
              "ok": False, "errors": ["llm: boom"]}
    worked = {"session_id": "wp01", "extraction_mode": "llm:deepseek-direct",
              "ok": True}
    witness = probe.door_witness([worked, failed])
    assert witness == {"entered": 2, "sessions": 2, "bypassed": []}, (
        "a capture that took the door and failed was counted as a bypass, so "
        f"the refusal would name a cause that is false: {witness}")
    # And it must not refuse: the failure is the failed-list's to report, and
    # the log it lets through is what carries the errors.
    assert probe.require_door_entered([worked, failed])["entered"] == 2
    # The line is the MODE, not ``ok``: a receipt that never entered is still a
    # bypass even if something marked it ok (the #3892 keyless shape does).
    keyless = {"session_id": "wp03", "extraction_mode": "no-provider",
               "ok": True}
    assert probe.door_witness([worked, keyless])["bypassed"] == ["wp03"]


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
    # ⛔ The lane is asserted in the UNMEASURED branch too. At the #10983
    # mutation pass, rewriting this branch's `"lane": DOOR_LANE` to a literal
    # `"unknown"` left all 22 tests GREEN: the measured branch is covered by
    # `test_the_probe_records_the_door_in_the_log_and_the_machine_record`, and
    # the test above pins `measured`/`pinned_off`/`source` but never the lane
    # (#3442 rule 3 — an unasserted field is the borrowing the docstring's
    # "lane follows the READ-BACK" rule forbids).
    assert record["lane"] == probe.DOOR_LANE == "v2", record
    assert record["lane_env"] == probe.DOOR_LANE_ENV, record
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
        # ⛔ `dict.__setitem__`, NOT `env[key] = "0"`: `__setitem__` swallows
        # every write for `stuck`, this one included, so the value the run read
        # back was whatever the OPERATOR'S shell held — the suite passed on a
        # clean box and failed on a box that exported one of the pinned-ON
        # levers (measured: `TORTOISE_JSON_MODE=1` and `TORTOISE_S4_DELTA=1`
        # each red this test at the previous head). The value read back has to
        # be OURS, or the test asserts about the environment, not the probe.
        dict.__setitem__(env, key, "0")   # the value it will READ BACK, not the pin
        monkeypatch.setattr(probe, "os", type(
            "os", (), {"environ": env})())
        with pytest.raises(SystemExit) as exc, probe.measured_arm():
            pass
        assert "REFUSED" in str(exc.value) and key in str(exc.value), (
            f"{key} did not read back as pinned {expected!r} and the run was "
            f"accepted: it would declare an arm it did not run (got "
            f"{exc.value})")


def test_every_env_var_the_capture_path_reads_is_accounted_for():
    """The comment's "the set is CLOSED" claim, MECHANISED.

    Two review rounds in a row found one more measurement switch that the pin
    set did not cover (`TORTOISE_SESSION_LLM_MOCK`, then the two truncation
    budgets), because "every switch that changes what the run measures" was
    prose. This derives the list from the module instead: every
    ``TORTOISE_*`` variable read by the modules in ``path_modules`` below must be
    EXACTLY ONE OF FOUR things — the lists are the authority, and this docstring
    deliberately does NOT restate their sizes or the module names, because the
    enumeration that used to live here was three modules and three categories
    while the code was already wider (review cycle 9):

      * an arm seam in ``SEAM_LEDGER`` (the VET/CLASSIFY_LATER/S4_DELTA flags),
      * a member of ``DOOR_PINNED_OFF`` / ``DOOR_PINNED_ON``,
      * a declared INPUT (the graph it writes, the backend label it resolves) —
        things the run needs a value FOR, not settings that change what it
        measures, or
      * a member of ``acknowledged_not_a_measurement_seam`` — a read on a derived
        module that is NOT a measurement switch, with its reason stated.

    A new ``os.environ.get("TORTOISE_...")`` in the extractor therefore reds
    this suite until someone classifies it, which is the only form of this rule
    that cannot be re-opened by the next switch.
    """
    #: The two reads that are INPUTS, not measurement settings. Both must be set
    #: for a run to happen at all (`backend_ready` gates on the first), so
    #: pinning them would pin the target rather than the seam.
    acknowledged_inputs = {"TORTOISE_DB_URI", "TORTOISE_API_URL"}

    #: Reads on the derived modules that are NOT measurement switches, each with
    #: the reason. Adding two modules (cycle 7) made their WHOLE-module reads
    #: visible, including ones no capture measurement depends on; declaring them
    #: here is the price of a mechanical closure, and the reason is stated rather
    #: than assumed.
    acknowledged_not_a_measurement_seam = {
        "TORTOISE_EXTRACTOR_PROVIDER": (
            "DELIBERATELY OBSERVED, not forced: it chooses among routes the box "
            "already has a key for and the receipt reports the result as "
            "`extraction_mode` (llm:<route>), which the run records — pinning it "
            "would pin the route instead of reporting it (probes/capstone_measure.py)"),
        "TORTOISE_EXTRACTOR_FAILOVER_COOLDOWN": (
            "operational retry timing between providers; it cannot select or "
            "rewrite what is extracted"),
        "TORTOISE_ASK_MODEL": "the ASK feature's model, not the extraction path",
        "TORTOISE_ASK_PROVIDER": "the ASK feature's provider, not the extraction path",
        "TORTOISE_SESSION_LLM_MODEL": (
            "the model spec `sdk._build_session_llm_extractor` resolves, built "
            "unconditionally at capture time — but with the m2 lane pinned OFF the "
            "built extractor is never used by the v2 door, and a spec that cannot "
            "be resolved RAISES (loud) rather than silently changing the measured "
            "population, so it is not a measurement seam"),
    }

    #: The MEASUREMENT modules the path renders with. `referentiality.py` is
    #: here because `extractor_v2` reaches it one call down
    #: (`_referentiality.enabled()` on the S2 prompt/mention path), so a
    #: derivation over `extractor_v2.py` ALONE cannot see
    #: `TORTOISE_REFERENTIALITY` — which is read on every v2 capture and was in
    #: no ledger, no pin and no input list (#10983 review, cycle 5).
    #: The modules the capture path RENDERS WITH and the two it consults for a
    #: SEAM decision. All four are reached one call down from `extractor_v2`
    #: (`_referentiality.enabled()`, `value_gate.value_gate_enabled()`,
    #: `s2_decision_seam.seam_enabled()`), so a derivation over `extractor_v2.py`
    #: alone cannot see their reads — which is how `TORTOISE_REFERENTIALITY` came
    #: to be read on every v2 capture while being in no ledger, no pin and no
    #: input list (#10983 review, cycle 5), and why an unclassified read added to
    #: `value_gate.py` was invisible to the previous two-module form (measured,
    #: cycle 6).
    #:
    #: ⛔ `CONTEXT_ENRICH` and `MODEL_ADAPTERS` were added in cycle 7, after the
    #: SAME defect recurred a third time: `sdk.capture_session` hands the worker
    #: `enrich_fn=_capture_enriched_texts`, which gates on
    #: `context_enrich.enrich_enabled()` (ON = "the per-turn rewrite the owner
    #: approved on #8932", OFF = "byte-identical to pre-#8488") — it changes the
    #: text that gets embedded — and `sdk._extract_session_v2` builds its model
    #: through `model_adapters`, whose `TORTOISE_JSON_MODE` is default-ON and read
    #: PER CALL, flipping the request's `response_format`. MEASURED: a new
    #: `os.environ.get(...)` inserted in `enrich_enabled()` left this pin green.
    #: A hand-enumerated module list is not a closure; when a module is
    #: deliberately out of scope, NAME it here with the switch it reads and the
    #: reason, rather than leaving its reads invisible.
    path_modules = (EXTRACTOR, REFERENTIALITY, VALUE_GATE, SEAM,
                    CONTEXT_ENRICH, MODEL_ADAPTERS, CHANGE_FEED, SPARSE,
                    SUBJECT_BINDING)

    read: set[str] = set()
    for module in path_modules:
        read |= _env_reads(module)
    assert read, (
        "no TORTOISE_* reads resolved from the capture path's modules — the "
        "derivation itself is broken, so this pin would pass over anything")
    #: ⛔ THE LITERAL CENSUS (cycle 10). `read` above only sees two SHAPES, so a
    #: module that passes its name to a helper (``_pick(tau_hi,
    #: "TORTOISE_SUBJECT_TAU_HI", …)`` -> ``os.environ.get(env_name)``) resolves to
    #: NOTHING — measured: `_env_reads(subject_binding.py)` -> `set()` while the
    #: file holds both spellings. So every ``TORTOISE_*`` string CONSTANT in the
    #: module's AST is collected too (message strings such as
    #: ``"TORTOISE_EXTRACTOR_PROVIDER=deepseek-direct but …"`` are excluded by the
    #: full-name shape) and asserted to be accounted for. That is what makes this
    #: a CLOSURE over the modules rather than over the two shapes the resolver
    #: happens to understand.
    literals: set[str] = set()
    for module in path_modules:
        literals |= _env_literals(module)

    probe = _load_probe()
    accounted = ({env for env, _w, _d, _s in probe.SEAM_LEDGER}
                 | set(probe.DOOR_PINNED_OFF)
                 | {env for env, _v in probe.DOOR_PINNED_ON}
                 | acknowledged_inputs
                 | set(acknowledged_not_a_measurement_seam))
    unaccounted = sorted((read | literals) - accounted)
    assert not unaccounted, (
        f"{[m.name for m in path_modules]} read {unaccounted} and the door "
        f"accounts for none of "
        f"them: either pin it (pop + refuse, like the rest) or declare it an "
        f"INPUT in this test with the reason. An unclassified read is how an "
        f"inherited shell changes the measured population under a door the log "
        f"says is pinned (#10983 reviews)")
    # And the reverse: a pinned switch nothing on the path reads is a
    # declaration, not a control.
    # ⛔ STRUCTURAL, not `env not in "".join(sources)`. A substring test over the
    # whole source is satisfied by a LONGER name that begins with the pinned one
    # and by prose: `TORTOISE_LABEL_ORDER` ⊂ `TORTOISE_LABEL_ORDER_SEED` and
    # `TORTOISE_EXTRACTOR` ⊂ `TORTOISE_EXTRACTOR_PROMPT`/`_MAX_TOKENS`/
    # `_ESCALATION_TOKENS`, so deleting the real `TORTOISE_LABEL_ORDER` read (and
    # both real `TORTOISE_EXTRACTOR` reads) left this assertion green — measured
    # in the #10983 review, cycle 5. Each pin must be a RESOLVED READ somewhere on
    # the path; the probe is included because it is the code that pops and
    # re-reads them.
    read_anywhere = (read | literals | _env_reads(SDK) | _env_reads(HOSTED)
                     | _env_reads(PROBE))
    # ⛔ And the reverse check covers the ARM and LEDGER spellings too (cycle 9):
    # it used to look only at `DOOR_PINNED_OFF`/`DOOR_PINNED_ON`, so a
    # `SEAM_LEDGER` row or an `ARM_ON`/`ARM_OFF` member naming a variable the code
    # stopped reading was a DEAD DECLARATION nothing caught — and a dead ledger
    # row is worse than a missing one, because the acceptance log prints
    # `TORTOISE_S2_DECISION_SEAM=off` from it and reads as a control.
    declared = (set(probe.DOOR_PINNED_OFF)
                | {e for e, _v in probe.DOOR_PINNED_ON}
                | {e for e, _w, _d, _s in probe.SEAM_LEDGER}
                | set(probe.ARM_ON) | set(probe.ARM_OFF))
    dead = sorted(env for env in declared if env not in read_anywhere)
    assert not dead, (
        f"{dead} are pinned or carried in the ledger but read nowhere on the "
        f"capture path — a pin that cannot matter is the declaration this "
        f"module refuses elsewhere")


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

    # The TAXONOMY column too, not only the defaults: `behaviour_seam` inverted
    # freely until the #10983 review (cycle 4), and it is the column that says
    # whether a seam changes behaviour at all.
    published_behaviour = {row["env"]: row["behaviour_seam"]
                           for row in record["door"]["seams"]}
    expect_behaviour = {env: seam
                        for env, _w, _d, seam in probe.SEAM_LEDGER}
    assert published_behaviour == expect_behaviour, (
        "preregistration.json door.seams disagrees with the probe about WHICH "
        f"seams change behaviour:\n  published: {published_behaviour}\n  "
        f"probe:     {expect_behaviour}")

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

    # The ARM is a FOURTH published copy of the pin set, and it was bound to
    # nothing until the #10983 review (cycle 4): dropping TORTOISE_EXTRACT_MODEL
    # — or TORTOISE_VALUE_GATE, the arm's decisive OFF pin — from `pinned_off`
    # left 18 tests green while §1.6.2 told the reader that every one of them is
    # written in-process, read back, and refused on a mismatch. Bind all three
    # sets to the probe that actually sets them.
    probe = _load_probe()
    arm = door["acceptance_arm"]
    assert set(arm["set_on"]) == set(probe.ARM_ON), (
        f"door.acceptance_arm.set_on is {sorted(arm['set_on'])} but the probe "
        f"arms {sorted(probe.ARM_ON)} (#10983)")
    assert set(arm["pinned_off"]) == set(probe.ARM_OFF) | set(probe.DOOR_PINNED_OFF), (
        f"door.acceptance_arm.pinned_off is {sorted(arm['pinned_off'])} but the "
        f"probe pins off {sorted(set(probe.ARM_OFF) | set(probe.DOOR_PINNED_OFF))} "
        f"— a record can omit a switch the run actually pins (#10983)")
    assert dict(arm["pinned_to"]) == dict(probe.DOOR_PINNED_ON), (
        f"door.acceptance_arm.pinned_to is {arm['pinned_to']} but the probe "
        f"pins {dict(probe.DOOR_PINNED_ON)} (#10983)")

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
    # …and BIND that lane to the ARTIFACT it describes. The assertion above is a
    # literal compared to a literal, so a later on-pin receipt from another lane
    # would become E4's scored artifact while both published copies still said
    # `v2` and nothing reddened (MEASURED at the #10983 review, cycle 7). Resolve
    # the receipt the probe itself SELECTS and read its own `extractor.qualname`.
    selected_rel, selected = probe.select_e4_receipt(
        frozen_pin=record["corpus"]["fixtures_hash"])
    selected_qualname = (selected.get("extractor") or {}).get("qualname")
    lane_of_qualname = {"extract_session_v2": "v2",
                        "extract_session_llm": "m2"}
    assert selected_qualname in lane_of_qualname, (
        f"the receipt E4 is scored from ({selected_rel}) records extractor "
        f"qualname {selected_qualname!r}, which is neither lane this door "
        f"pins — the row's number would be published under a lane that did not "
        f"measure it (#10983)")
    assert rows["E4"]["lane"] == lane_of_qualname[selected_qualname], (
        f"E4's registered lane is {rows['E4'].get('lane')!r} but the receipt "
        f"select_e4_receipt picks ({selected_rel}) records "
        f"{selected_qualname!r} — the row and its artifact disagree (#10983)")
    assert rows["E1b"]["lane"] == "m2", (
        f"E1b's lane is {rows['E1b'].get('lane')!r}; it is the m2 anti-gaming "
        f"control (#10983)")
    # …and BIND E1b to its artifacts, the way E4 is bound above. The assertion
    # before this one compares two literals, so neither artifact the row talks
    # about was read. TWO are involved and both are read here: the lane the row
    # is DEFINED on (the fixture's m2 baseline, at the frozen hash) and the
    # artifact the probe actually PUBLISHES the number from — round 11 measured
    # that the published 90/90 comes from the 9889 record's `e1b` line, a no-loss
    # control on the SCORED (v2) arm whose own note disclaims the m2 lane, while
    # the row named the m2 baseline as its carrier and nothing compared them.
    m2_base = json.loads((REPO / "tests/eval/write_path/baselines/m2.json")
                         .read_text(encoding="utf-8"))
    assert (m2_base.get("config") or {}).get("extractor_posture") == "m2", (
        f"baselines/m2.json is no longer the m2 lane's baseline: "
        f"{m2_base.get('config')}")
    assert m2_base.get("fixtures_hash") == record["corpus"]["fixtures_hash"], (
        f"baselines/m2.json is pinned at {m2_base.get('fixtures_hash')!r}, not "
        f"the frozen {record['corpus']['fixtures_hash']!r} — E1b would cite a "
        f"carrier measured on another corpus revision (#10983)")
    assert ((m2_base.get("metrics") or {}).get("salient_unit_survival_strict")
            == 1.0), (
        "baselines/m2.json no longer carries the 90/90 strict survival E1b's row "
        f"cites: {m2_base.get('metrics')}")
    published = json.loads((REPO / probe.E1_E2_SCORES).read_text(encoding="utf-8"))
    published_e1b = published.get("e1b") or {}
    assert published_e1b.get("artifact") in rows["E1b"].get(
        "published_figure", ""), (
        f"the probe publishes E1b from {published_e1b.get('artifact')!r} (the "
        f"9889 record's e1b line), and the row does not name it — a reader takes "
        f"E1b as an m2 measurement when the published figure is the SCORED "
        f"arm's control: {rows['E1b'].get('published_figure')!r}")
    assert rows["E1b"].get("carried_forward"), (
        "E1b's m2 carrier records its metrics as CARRIED FORWARD from the "
        "previous fixtures_hash, not re-measured at this one, and the row must "
        "say so (#10983 review round 11)")
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


def _census_levers(probe) -> tuple[str, ...]:
    """Every variable that can move the population the negative control measures.

    Derived from the probe's own declarations — the pinned door set plus the
    seam ledger's flags — so a lever added to the probe enters this control's
    isolation WITHOUT an edit here. A hand-kept list is how the pin set drifted
    three times in earlier rounds (#10983 review).

    ``SEAM_LEDGER``'s ``TORTOISE_S4_DELTA`` / ``TORTOISE_LABEL_ORDER`` rows are
    already in ``DOOR_KEYS``; the union is deduped.
    """
    flags = {flag for flag, _where, _default, _seam in probe.SEAM_LEDGER}
    return tuple(sorted(set(probe.DOOR_KEYS) | flags))


def _scrub_levers(monkeypatch, probe) -> tuple[str, ...]:
    """Isolate this control from the operator's shell (pytest's own guidance).

    ``os.environ`` is process-global state, so its isolation belongs to the
    fixture: the control asserts EXACT published censuses, and an inherited
    lever that moved the population would red it with a message claiming "the
    door's census moved" — a false red pointing at the wrong thing (#10983
    review, round 12 note). ``monkeypatch`` restores every key at teardown, so
    the three-branch save/restore block this replaces cannot half-apply.
    """
    levers = _census_levers(probe)
    for key in levers:
        monkeypatch.delenv(key, raising=False)
    return levers


def test_the_control_scrubs_every_lever_the_probe_declares(monkeypatch):
    """The scrub is a mechanism, not a comment (mutation-proven non-vacuous).

    With every lever exported at a sentinel value, ``_scrub_levers`` must leave
    none of them readable. Make the helper a no-op and this reds; that is the
    whole assertion — it also keeps the derived set CLOSED, so a lever added to
    the probe cannot silently fall outside the control's isolation.
    """
    probe = _load_probe()
    levers = _census_levers(probe)
    assert set(probe.DOOR_KEYS) <= set(levers), (
        "a pinned door key is missing from the control's scrub set")
    assert {flag for flag, _w, _d, _s in probe.SEAM_LEDGER} <= set(levers), (
        "a seam-ledger flag is missing from the control's scrub set")
    for key in levers:
        monkeypatch.setenv(key, "sentinel")
    _scrub_levers(monkeypatch, probe)
    leaked = sorted(key for key in levers if key in os.environ)
    assert not leaked, (
        f"these levers survived the scrub and would be measured under the "
        f"operator's values while the test asserts a published census: {leaked}")


def test_the_negative_control_is_measured_and_the_two_lanes_DISAGREE(monkeypatch):
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
    about the v2 door (and vice versa). The difference is structural IN KIND —
    the ``m2`` lane mints **no `:Object` at all** on this session — and the
    5.5x Point count is the SIZE of that same difference, not the reason it
    matters. (This docstring said "structural, not a matter of degree" beside the
    5.5x figure until the #10983 review: a ratio IS a degree, and the record's own
    ``structural_in_kind_not_only_in_size`` says so.)

    ⚠️ The assertion polarity is deliberately BOTH ways: identical output would
    mean the control had stopped being a control (the mock seam would have
    become lane-blind, as it was believed to be during #10983's first pass), so
    it reds too — with the measurement printed, because which way it moved is
    the finding.

    ⚠️ Isolated from the operator's shell: every lever the probe declares is
    scrubbed before the captures (``_scrub_levers``), so the EXACT censuses
    below describe this fixture under the shipped defaults, not under whatever
    the box happened to export. Measured at the previous head (#10983 review
    round 12 note): ten candidate levers — ``TORTOISE_EXTRACTOR_MAX_TOKENS``,
    ``_SUBJECT_TAU_HI``, ``_CONTEXT_ENRICH``, ``_REFERENTIALITY``,
    ``_CHANGE_FEED``, ``_JSON_MODE``, ``_IDENTIFIER_TOKENS``, ``_S4_DELTA``,
    ``_EXTRACTOR_PROMPT``, ``_LABEL_ORDER`` — each exported alone left this
    control green, because the offline mock seam does not consult them. The
    scrub is applied anyway: the census is a published number, and a lever that
    DOES move it later must not be able to red this with a message asserting
    the door moved.
    """
    from tests.eval.write_path.corpus import load_fixture, session_ids
    from tortoise.sdk import TortoiseSDK

    probe = _load_probe()
    _scrub_levers(monkeypatch, probe)

    sid = session_ids()[0]
    conversation = load_fixture(sid)["conversation"]
    assert conversation, f"fixture {sid} carries no conversation"

    measured: dict[str, dict] = {}
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    for lane in ("v2", "m2"):
        monkeypatch.delenv(probe.DOOR_LANE_ENV, raising=False)
        if lane == "m2":
            monkeypatch.setenv(probe.DOOR_LANE_ENV, "m2")
        sdk = TortoiseSDK(os.path.join(tempfile.mkdtemp(), "door.db"))
        try:
            receipt = sdk._capture_session(conversation, session_id=sid)
            assert receipt.get("ok") is True, receipt.get("errors")
            measured[lane] = _census(sdk)
        finally:
            sdk.close()

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


# ── 6. the guard's call site is UNCONDITIONAL, and only where it really is ─

#: The ONE line in the acceptance probe that arms the door witness. Every mutant
#: row below is a textual replacement of it, so each mutant is the REAL probe
#: with one wrapper added or removed — never a hand-built AST that could differ
#: from the tree that ships.
DOOR_GUARD_LINE = "    require_door_entered(runs)\n"

#: Exception names a handler needs in order to SWALLOW the witness's refusal.
#: `require_door_entered` raises `SystemExit`, whose MRO is
#: `(SystemExit, BaseException, object)` — it derives from `BaseException` and
#: NOT from `Exception` (Python docs, "Built-in Exceptions"; verified against
#: `SystemExit.__mro__`). So `except Exception:` lets the refusal propagate and
#: is a CORRECT implementation, while a bare `except:` or one naming
#: `SystemExit`/`BaseException` hides it.
_SWALLOWING_EXCEPTIONS = frozenset({"SystemExit", "BaseException"})

_TRY_NODES = tuple(
    node for node in (ast.Try, getattr(ast, "TryStar", None))
    if node is not None)


def _own_scope(node: ast.AST):
    """`node`'s children, never descending into a nested def/lambda/class.

    ONE walk, shared by the pin and by its mutant rows: two implementations
    would be two answers to "is this call in `main`'s own scope".
    """
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                              ast.Lambda, ast.ClassDef)):
            continue
        yield child
        yield from _own_scope(child)


def _probe_main(mutated_src: str | None = None) -> ast.FunctionDef:
    """`main` from the acceptance probe, or from a source-level mutant of it."""
    tree = ast.parse(_source(PROBE) if mutated_src is None else mutated_src)
    return {node.name: node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}["main"]


def _door_guard_call(main: ast.AST) -> tuple[ast.Call, ast.AST]:
    """`main`'s single own-scope `require_door_entered(...)` call, and its arg."""
    calls = [node for node in _own_scope(main)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "require_door_entered"]
    assert len(calls) == 1, (
        f"`main` must call require_door_entered(...) exactly once in its own "
        f"scope, called {len(calls)} time(s) — the witness exists but nothing "
        "arms it, so a bypassed capture is written as a measured one "
        "(#10983 review P2)")
    call = calls[0]
    positionals = call.args
    assert positionals or call.keywords, (
        "require_door_entered() is called with NO argument — the guard cannot "
        "see the runs it is supposed to refuse (#10983)")
    return call, (positionals[0] if positionals
                  else next(kw.value for kw in call.keywords))


def _handler_swallows_the_refusal(handler: ast.ExceptHandler) -> bool:
    """Can this handler catch the `SystemExit` the witness raises?"""
    if handler.type is None:          # bare `except:` catches BaseException
        return True
    return any(
        (isinstance(node, ast.Name) and node.id in _SWALLOWING_EXCEPTIONS)
        or (isinstance(node, ast.Attribute)
            and node.attr in _SWALLOWING_EXCEPTIONS)
        for node in ast.walk(handler.type))


def _test_still_runs_the_guard(wrapper: ast.AST, runs_arg: ast.AST) -> bool:
    """Does `wrapper.test` provably NOT skip the call?

    Only two shapes qualify: a TRUTHY literal (`if True:`, `while True:`), and
    the guard's own argument as the test — `if runs: require_door_entered(runs)`
    is behaviourally identical to calling it unconditionally, because
    `require_door_entered([])` returns `entered=0` / `bypassed=[]` and raises
    nothing. Everything else (`if False:`, `if os.environ.get(...) != "1":`,
    `if runs and also_this:`) is left UNPROVEN and blocks — a pin that guesses
    "probably fine" is the fail-open shape this row exists to prevent.
    """
    test = getattr(wrapper, "test", None)
    if isinstance(test, ast.Constant):
        return bool(test.value)
    return (isinstance(test, ast.Name) and isinstance(runs_arg, ast.Name)
            and test.id == runs_arg.id)


_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)
_SCOPE_BOUNDARY_NODES = (ast.FunctionDef, ast.AsyncFunctionDef,
                         ast.Lambda, ast.ClassDef)


def _finally_discards_the_refusal(suite: list[ast.stmt]) -> bool:
    """Does this `finally` suite DISCARD a pending exception?

    A pending exception — `SystemExit` included — is temporarily saved while
    `finally` runs and then re-raised, **unless** the suite executes `return`,
    `break` or `continue`, which discards it (language reference §8.4.4;
    corroborated by OpenSSF `pyscg-0028`). Verified by EXECUTION, not from the
    prose: a function whose `raise SystemExit(...)` is followed by
    `finally: return 0` returns `0`, and one whose `finally` runs a
    `for …: break` still raises. So `try: require_door_entered(runs)` +
    `finally: return 0` never propagates the refusal the witness exists for,
    while the shipped arm called it harmless (#10983 review at `4fa9c91d`).

    Two boundaries make this a *derivation* rather than a keyword scan:

    * `return` inside a nested `def`/`lambda`/`class` defined in the suite
      transfers nothing out of it — the same own-scope rule `_own_scope` states;
    * a `break`/`continue` only discards the exception when it LEAVES the
      suite, so one targeting a loop INSIDE the `finally` stays put (measured:
      the exception still propagates). Loop nesting is therefore tracked as
      the walk descends — `_own_scope` cannot be reused here, because that
      walk flattens exactly the nesting this rule depends on.
    """
    def walk(node: ast.AST, loops: int) -> bool:
        if isinstance(node, _SCOPE_BOUNDARY_NODES):
            return False
        if isinstance(node, ast.Return):
            return True
        if isinstance(node, (ast.Break, ast.Continue)):
            return loops == 0
        inner = loops + (1 if isinstance(node, _LOOP_NODES) else 0)
        return any(walk(child, inner) for child in ast.iter_child_nodes(node))

    return any(walk(statement, 0) for statement in suite)


def _wrapper_can_hide_the_guard(wrapper: ast.AST, child: ast.AST,
                                runs_arg: ast.AST) -> bool:
    """Can this wrapper SKIP the call, or SUPPRESS the refusal it raises?

    ⛔ The wrappers are INSPECTED, not merely named (review at `68efe7e0`,
    residual 2). An `ast.Try` says nothing on its own: `try: guard` +
    `finally:` runs the guard on every pass and lets its `SystemExit`
    propagate, and so does `except Exception:` — which cannot catch
    `SystemExit` at all. Naming the node type alone called both "guarded", so
    two CORRECT implementations red while the message asserted one "never runs
    it": over-block is fail-closed, but it is a false red with a false
    diagnosis, and it teaches the next reader to edit the probe until the pin
    is quiet.

    ⛔ And a `Try` is not only its HANDLERS (review at `4fa9c91d`): the guard's
    `SystemExit` is pending when `finally` starts, so a `finally` that reroutes
    control — `return`, or a `break`/`continue` leaving the suite — discards
    the refusal just as completely as `except SystemExit: pass`, and reading
    `handlers` alone called that a CORRECT implementation. That is the second
    question in this docstring's own first line.
    """
    if isinstance(wrapper, _TRY_NODES):
        return (any(_handler_swallows_the_refusal(handler)
                    for handler in wrapper.handlers)
                or _finally_discards_the_refusal(wrapper.finalbody))
    if isinstance(wrapper, (ast.If, ast.IfExp, ast.While)):
        return not _test_still_runs_the_guard(wrapper, runs_arg)
    return isinstance(wrapper, (ast.For, ast.AsyncFor, ast.Match,
                                ast.comprehension, ast.BoolOp))


def _door_guard_wrappers(main: ast.AST, call: ast.Call,
                         runs_arg: ast.AST) -> list[str]:
    """The wrappers above `call` that can skip it or swallow its refusal."""
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(main):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    chain: list[tuple[ast.AST, ast.AST]] = []
    child: ast.AST = call
    anchor = parents.get(id(child))
    while anchor is not None and anchor is not main:
        chain.append((anchor, child))
        child, anchor = anchor, parents.get(id(anchor))
    # ⛔ No `assert anchor is main` here (review at `68efe7e0`, residual 1). It
    # could never fire and advertised a diagnosis it could not produce: the
    # chain starts at `call`, which `_own_scope(main)` drew from `main`'s own
    # subtree, and `parents` is built from `ast.walk(main)` — the same subtree —
    # so the walk always terminates at `main`. Every mutation that breaks the
    # parent chain (a nested `def`/`lambda`/`class`) reds arm (a) first, with
    # "called 0 time(s)". Deleting dead weight is this file's own precedent
    # (`:805-810`), and `#11424` carries the rule-3 finding.
    return [type(node).__name__ for node, child in chain
            if _wrapper_can_hide_the_guard(node, child, runs_arg)]


#: The unconditional arm's discrimination matrix (review at `68efe7e0`). Each
#: row replaces `DOOR_GUARD_LINE` in the REAL probe source, so `expected` is what
#: the arm must report about a tree that really has that shape. The first block
#: is the mutant class the arm exists to RED; the second is the over-block class
#: it used to red as well — the same wrapper node types, in a CORRECT
#: implementation — which is why both directions are pinned here.
DOOR_GUARD_MUTANTS = (
    # (label, expected wrappers, the replacement for DOOR_GUARD_LINE)
    ("an `if False:` arm never runs the guard",
     ["If"], "    if False:\n        require_door_entered(runs)\n"),
    ("a conditional env arm re-creates the inert enforcer (#11424 item 1)",
     ["If"],
     "    if os.environ.get(\"TORTOISE_SKIP_DOOR_GUARD\") != \"1\":\n"
     "        require_door_entered(runs)\n"),
    ("`try` + `except SystemExit: pass` swallows the refusal",
     ["Try"],
     "    try:\n        require_door_entered(runs)\n"
     "    except SystemExit:\n        pass\n"),
    ("a bare `except:` swallows the refusal too",
     ["Try"],
     "    try:\n        require_door_entered(runs)\n"
     "    except:\n        pass\n"),
    ("a tuple handler naming SystemExit is still a swallow",
     ["Try"],
     "    try:\n        require_door_entered(runs)\n"
     "    except (ValueError, SystemExit):\n        pass\n"),
    ("a `while False:` arm never runs the guard",
     ["While"], "    while False:\n        require_door_entered(runs)\n"),
    # Review at `4fa9c91d`: the arm read a `Try`'s HANDLERS and never its
    # `finalbody`, so these two — measured on the pre-fix arm as `[]`, i.e.
    # "CORRECT implementation" — are the discriminating pair it was missing.
    ("`finally: return` discards the refusal instead of propagating it",
     ["Try"],
     "    try:\n        require_door_entered(runs)\n"
     "    finally:\n        return 0\n"),
    ("`except Exception:` + `finally: return` still discards the refusal",
     ["Try"],
     "    try:\n        require_door_entered(runs)\n"
     "    except Exception:\n        pass\n"
     "    finally:\n        return 0\n"),
    # ── the over-block class: CORRECT implementations, pinned GREEN ─────────
    ("`try/finally` runs the guard on every pass and propagates its refusal",
     [],
     "    try:\n        require_door_entered(runs)\n    finally:\n        pass\n"),
    ("`except Exception:` cannot catch SystemExit, so the refusal propagates",
     [],
     "    try:\n        require_door_entered(runs)\n"
     "    except Exception:\n        pass\n"),
    # The two boundaries `_finally_discards_the_refusal` derives rather than
    # guesses: neither transfers control OUT of the suite, and both were
    # measured to still propagate the `SystemExit`.
    ("a `break` targeting a loop INSIDE `finally` never leaves the suite",
     [],
     "    try:\n        require_door_entered(runs)\n"
     "    finally:\n        for _ in ():\n            break\n"),
    ("a `return` inside a def DEFINED in `finally` transfers nothing out",
     [],
     "    try:\n        require_door_entered(runs)\n"
     "    finally:\n        def _fmt(value):\n            return value\n"),
    ("`if runs:` is a no-op guard — an empty run list refuses nothing",
     [], "    if runs:\n        require_door_entered(runs)\n"),
    ("`while True:` runs the guard on every pass",
     [], "    while True:\n        require_door_entered(runs)\n        break\n"),
)


def test_the_unconditional_door_arm_reds_on_guards_and_not_on_no_op_wrappers():
    """Arm (d) of the call-site row must discriminate in BOTH directions.

    The pre-fix arm collected the ancestors' NODE TYPES (`ast.Try`, `ast.If`, …)
    and red on any of them. That caught the two mutants it was written for, and
    also red on `try: guard` + `finally:` and on `if runs: guard` — both correct
    (review at `68efe7e0`, residual 2). Each `DOOR_GUARD_MUTANTS` row below is a
    mutation of the real probe source, so this row is the arm's own mutation
    table: the GREEN rows fail on the pre-fix arm (they reported `['Try']` /
    `['If']` where `[]` is correct) and the RED rows pin what the arm must never
    stop catching.

    The `finally:` rows exist because the arm then read a `Try`'s HANDLERS and
    never its suite (review at `4fa9c91d`): `finally: return 0` still executes
    the guard and then DISCARDS the refusal it raised (§8.4.4), which the
    pre-fix arm called a correct implementation — measured `[]` where `['Try']`
    is right. That is the fail-open direction, so it is the pair that had to
    become RED; the two GREEN suite rows pin the boundaries the derivation must
    not over-reach to (an inner-loop `break`, a `return` in a nested def).
    """
    src = _source(PROBE)
    assert src.count(DOOR_GUARD_LINE) == 1, (
        "the mutant rows below replace exactly one line, but the probe carries "
        f"{src.count(DOOR_GUARD_LINE)} line(s) reading "
        f"{DOOR_GUARD_LINE.strip()!r} — the table cannot say which one it "
        "mutated (#10983)")

    classified = []
    for label, expected, replacement in DOOR_GUARD_MUTANTS:
        main = _probe_main(src.replace(DOOR_GUARD_LINE, replacement, 1))
        call, runs_arg = _door_guard_call(main)
        classified.append(
            (label, expected, _door_guard_wrappers(main, call, runs_arg)))
    wrong = [(label, expected, actual)
             for label, expected, actual in classified if actual != expected]
    assert not wrong, (
        "the UNCONDITIONAL arm mis-classifies the wrappers it exists for "
        "(#10983 review at `68efe7e0`, residuals 1-2):\n"
        + "\n".join(f"  {label}\n    expected {expected}, got {actual}"
                    for label, expected, actual in wrong))


def test_the_run_REFUSES_a_bypassed_capture_at_the_CALL_SITE_that_arms_the_witness():
    """`require_door_entered(runs)` is the guard; its CALL SITE is what arms it.

    #10983 review (P2 at `d410d45bbb`): every existing row asserts the witness's
    own RETURN VALUE (`:1140`, `:1202`), and nothing asserted that `main` ever
    CALLS it. Deleting the call — or moving it after `render_log(...)` — left the
    whole suite green while a run whose capture never reached
    `_extract_session_v2` went back to writing a table with a `door:` line saying
    `lane: v2`, `measured: true`, and exit 0. That is this repo's own category-A
    "inert enforcer": the condition is present in the module, can never fire, and
    the artifact says it protected something.

    Three properties, each with the mutant that reds it:

    * the call EXISTS in `main`'s OWN scope — a bare `Call`, not a name in a
      comment or a docstring (`_body()`'s docstrings record three rounds lost to
      the substring shape, so a nested `def` is not allowed to satisfy this
      either);
    * it is handed the RUN LIST — `require_door_entered([])` returns
      `{"entered": 0, "bypassed": []}` and refuses nothing, so a literal must red;
    * it runs BEFORE `render_log(...)`, so the refusal precedes the artifact it
      is refusing to write;
    * it is UNCONDITIONAL — an `if`/`try` body is still "in `main`'s own scope"
      for the walk above, so a call that can never run (`if False:`) or whose
      `SystemExit` is swallowed (`try: … except SystemExit: pass`) has to be a
      separate mutant of this same row (review at `7eb4a042`). The wrappers are
      INSPECTED rather than merely named (`_wrapper_can_hide_the_guard`), so a
      `try/finally` wrapper or an `if runs:` no-op guard — both CORRECT — no
      longer red, with their own rows in `DOOR_GUARD_MUTANTS` (review at
      `68efe7e0`, residual 2).
    """
    main = _probe_main()
    call, runs_arg = _door_guard_call(main)
    assert isinstance(runs_arg, ast.Name), (
        f"require_door_entered(...) is handed "
        f"{type(runs_arg).__name__} ({ast.dump(runs_arg)[:80]}), not the run LIST — "
        "`require_door_entered([])` returns entered=0/bypassed=[] and refuses "
        "nothing, so this call site would be green and inert (#10983)")
    bound = {n.id for n in _own_scope(main)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    assert runs_arg.id in bound or any(
        isinstance(n, ast.arg) and n.arg == runs_arg.id
        for n in _own_scope(main)), (
        f"require_door_entered({runs_arg.id}) is handed a name `main` never binds — "
        "a module-level empty list refuses nothing either (#10983)")

    # (c) the refusal precedes the artifact. ``render_log`` both BUILDS the text
    # and (at the next statement) its caller writes it, so a guard placed after
    # either one refuses nothing that has not already been produced.
    render_calls = [node for node in _own_scope(main)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "render_log"]
    assert len(render_calls) == 1, (
        f"`main` must call render_log(...) exactly once: {len(render_calls)}")
    assert call.lineno < render_calls[0].lineno, (
        f"require_door_entered(...) is at line {call.lineno} but render_log(...) is "
        f"at {render_calls[0].lineno} — the door is checked AFTER the log is built, "
        "so a bypassed capture still produces the artifact and the run exits 0 "
        "(#10983 review P2)")

    # (d) the call is UNCONDITIONAL — reached on every run, not merely PRESENT.
    # `_own_scope` skips only a nested `def`/`lambda`/`class`, so it descends
    # straight through `if` and `try` bodies: `if False: require_door_entered(
    # runs)` guards nothing, and `try: require_door_entered(runs)` +
    # `except SystemExit: pass` lets the refusal the witness exists for fall
    # through — while every assertion above stays green (review at `7eb4a042`,
    # P2). Both mutants red here; the wrapper inspection that keeps this arm
    # from ALSO reding on a correct `try/finally` or `if runs:` lives in
    # `_wrapper_can_hide_the_guard`, and its two-direction mutation table is
    # `test_the_unconditional_door_arm_reds_on_guards_and_not_on_no_op_wrappers`.
    guarded = _door_guard_wrappers(main, call, runs_arg)
    assert not guarded, (
        f"the require_door_entered(...) call at line {call.lineno} sits inside "
        f"{guarded}, so it is present but NOT unconditional — an `if False:` "
        f"arm never runs it and a `try/except SystemExit` arm swallows the very "
        f"refusal it exists to make, while the run still writes a table saying "
        f"`measured: true` and exits 0 (#10983 review P2 at 7eb4a042)")
