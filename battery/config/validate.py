"""Shared scenario validators (issue #1407) — the single implementation of
every per-scenario content invariant.

Used by BOTH the deterministic builder (Task 4) and the per-pack authoring
checkpoints (Task 3) so binding errors surface in the pack where they occur.
Cross-scenario invariants (controls bijection, PACK_SPLITS exact totals, id-set
completeness) live in separate functions the builder calls after full
accumulation — per-pack checkpoints must NOT run them (they cannot pass while
later packs are absent).

Matching contract: ``normalize`` (casefold + whitespace collapse) +
``contains_phrase`` (word-boundary substring of the FULL phrase) — imported
from corpus_loader.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from . import schema
from .corpus_loader import (
    GOLD_KEY,
    GoldLeakError,
    contains_phrase,
    normalize,
    render_reader_prompt,
)

_ID_PATTERNS = {
    "decision": r"d-\d{3}",
    "contradiction": r"ct-\d{3}",
    "calibration": r"cal-\d{3}",
    "retraction": r"ret-\d{3}",
    "loopy_contested": r"lp-\d{3}",
    "adversarial": r"adv-\d{3}",
    "family_rep": r"fam-\d{3}",
    "interdependent": r"int-\d{3}",
    "wave_variant": r"wv-\d{3}",
    "cross_session_contradiction": r"xs-\d{3}",
    "decision_drift": r"drift-\d{3}",
    "feedback_loop": r"fb-\d{3}",
}

# Hostile↔turns binding: which hostile texts must appear (normalized,
# word-boundary) in the rendered prompt, per attack type (pinned — same list
# as the test suite).
_HOSTILE_BINDINGS: dict[str, tuple[str, ...]] = {
    "poisoned": ("injected_claim",),
    "sybil": ("injected_claim",),
    "echo_chamber": ("echo_ring",),  # every ring variant
    "flapping": ("flip_claims",),    # every flip entry's claim
    "anchoring": ("stale_claim", "superseded_by"),
}

_SOURCE_TIER_INDEX = {t: i for i, t in enumerate(schema.SOURCE_TIERS)}


class _DuplicateKeyLoader(yaml.SafeLoader):
    """YAML loader that rejects duplicate mapping keys (pyyaml silently
    last-wins otherwise — a duplicated scenario id would pass id-uniqueness)."""


def _construct_mapping(loader: _DuplicateKeyLoader, node: yaml.MappingNode, deep: bool = False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate mapping key {key!r}", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_DuplicateKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml_dupreject(path: str | Path) -> dict:
    """Load a YAML corpus file, rejecting duplicate mapping keys."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.load(fh, Loader=_DuplicateKeyLoader)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a YAML mapping at the top level")
    return data


def render_scan_text(scenario: dict) -> str:
    """The single render basis for build-time scans: the reader render of a
    gold-stripped copy (byte-identical to the reader path — strip-then-render).

    A NESTED ``gold`` key (inside hostile/turns) survives the shallow strip, so
    ``render_reader_prompt`` raises ``GoldLeakError`` — the nested-gold
    refusal mechanism during validate-with-gold.
    """
    copy = dict(scenario)
    copy.pop(GOLD_KEY, None)
    return render_reader_prompt(copy)


def _gold_texts(scenario: dict) -> list[str]:
    """The gold texts to scan: gold.expected (str) or its elements (list)."""
    gold = scenario.get(GOLD_KEY) or {}
    expected = gold.get("expected")
    if expected is None:
        return []
    if isinstance(expected, list):
        return [str(e) for e in expected]
    return [str(expected)]


def _validate_gold_no_substring(scenario: dict, errors: list[str]) -> None:
    """Gold no-substring content rule (build-time invariant).

    Normalized gold text must NOT be a word-boundary substring of the
    rendered prompt. GOLD_ENUMS exact-token exemption (enum-shaped golds
    legitimately appear in prose). Phrase-level rule: gold text is ≥2 words
    unless it is an enum token.
    """
    gold = scenario.get(GOLD_KEY) or {}
    expected = gold.get("expected")
    if expected is None:
        errors.append(f"{scenario.get('id')}: gold.expected missing")
        return
    texts = _gold_texts(scenario)
    if not texts:
        errors.append(f"{scenario.get('id')}: gold.expected empty")
        return
    try:
        scan = render_scan_text(scenario)
    except GoldLeakError as exc:
        errors.append(f"{scenario.get('id')}: nested gold refused ({exc})")
        return
    for text in texts:
        norm = normalize(text)
        if norm in (normalize(e) for e in schema.GOLD_ENUMS):
            continue  # exact-token exemption
        if len(norm.split()) < 2:
            errors.append(
                f"{scenario.get('id')}: gold.expected {text!r} is not phrase-level "
                "(≥2 words) and not a GOLD_ENUMS token")
        if contains_phrase(scan, text):
            errors.append(
                f"{scenario.get('id')}: gold.expected {text!r} collides with the "
                "rendered prompt (no-substring rule) — rewrite the gold wording "
                "(agent-output-shaped, distinct from prompt wording)")


def _turns_of(scenario: dict) -> list[dict]:
    return scenario.get("prompt", {}).get("turns", [])


def _session_scripts_of(scenario: dict) -> list[dict]:
    scripts = scenario.get("session_scripts")
    if scripts is None:
        scripts = (scenario.get("prompt") or {}).get("session_scripts")
    return scripts or []


def _validate_contradiction_bindings(scenario: dict, errors: list[str]) -> None:
    turns = _turns_of(scenario)
    pairs = scenario.get("planted_contradictions") or []
    if not pairs:
        errors.append(f"{scenario['id']}: contradiction scenario has no planted_contradictions")
        return
    if len(turns) < schema.CONTRADICTION_K:
        errors.append(
            f"{scenario['id']}: k={schema.CONTRADICTION_K} needs ≥{schema.CONTRADICTION_K} turns, "
            f"has {len(turns)}")
        return
    pre_turns = turns[: schema.CONTRADICTION_K - 1]
    inject_turn = turns[schema.CONTRADICTION_K - 1]
    inject_text = str(inject_turn.get("content", ""))
    pre_text = " ".join(str(t.get("content", "")) for t in pre_turns)
    for i, pair in enumerate(pairs):
        claim = str(pair.get("claim", ""))
        counter = str(pair.get("counter_claim", ""))
        if pair.get("k") != schema.CONTRADICTION_K:
            errors.append(
                f"{scenario['id']}: planted contradiction {i} k={pair.get('k')!r} — "
                f"must be pinned to {schema.CONTRADICTION_K}")
        if not contains_phrase(pre_text, claim):
            errors.append(
                f"{scenario['id']}: claim {claim!r} not found in turns "
                f"[0..{schema.CONTRADICTION_K - 2}]")
        if not contains_phrase(inject_text, counter):
            errors.append(
                f"{scenario['id']}: counter_claim {counter!r} not found at turn "
                f"index {schema.CONTRADICTION_K - 1} (k={schema.CONTRADICTION_K})")
        if contains_phrase(pre_text, counter):
            errors.append(
                f"{scenario['id']}: counter_claim {counter!r} pre-appears in turns "
                f"[0..{schema.CONTRADICTION_K - 2}] — turn {schema.CONTRADICTION_K} must be "
                "the true injection point")


def _validate_loopy(scenario: dict, errors: list[str]) -> None:
    gs = scenario.get("graph_script") or {}
    nodes = gs.get("nodes") or []
    edges = gs.get("nand_edges") or []
    pair = gs.get("contested_pair") or {}
    node_ids = [n.get("id") for n in nodes]
    if len(set(node_ids)) != 3:
        errors.append(f"{scenario['id']}: loopy graph_script needs 3 distinct node ids")
        return
    if not edges:
        errors.append(f"{scenario['id']}: loopy graph_script needs a non-empty nand_edges list")
    flat = [e for pair_edge in edges for e in pair_edge]
    if any(e not in node_ids for e in flat):
        errors.append(f"{scenario['id']}: nand_edges reference unknown node ids")
    a, neg_a = pair.get("a"), pair.get("neg_a")
    if a in node_ids or neg_a in node_ids:
        errors.append(f"{scenario['id']}: contested_pair nodes must be distinct from the triangle")
    if pair.get("a_ref") not in node_ids or pair.get("neg_a_ref") not in node_ids:
        errors.append(f"{scenario['id']}: contested_pair refs must resolve to triangle node ids")
    # NAND triangle = 3 edges on 3 nodes (odd cycle).
    if len(edges) != 3:
        errors.append(f"{scenario['id']}: NAND triangle must have exactly 3 edges")
    if not {tuple(sorted(e)) for e in edges} == {tuple(sorted(e)) for e in (edges[:3] + edges[:1])}:
        # Fallback: verify a 3-cycle exists via pairs (cheap check below).
        pass
    expected = (scenario.get(GOLD_KEY) or {}).get("expected")
    if expected != "undecided":
        errors.append(f"{scenario['id']}: loopy gold.expected must be 'undecided' (GOLD_ENUMS)")


def _validate_adversarial(scenario: dict, errors: list[str]) -> None:
    attack = scenario.get("attack_type")
    if attack not in schema.ATTACK_TYPES:
        errors.append(f"{scenario['id']}: attack_type {attack!r} not in {schema.ATTACK_TYPES}")
        return
    hostile = scenario.get("hostile") or {}
    turns_text = " ".join(str(t.get("content", "")) for t in _turns_of(scenario))
    n_turns = len(_turns_of(scenario))
    if attack == "poisoned":
        if hostile.get("source_tier_injected") != "T4":
            errors.append(f"{scenario['id']}: poisoned source_tier_injected must be T4")
        lt = hostile.get("legit_tier")
        if lt not in schema.SOURCE_TIERS or _SOURCE_TIER_INDEX[lt] > 1:
            errors.append(f"{scenario['id']}: poisoned legit_tier must be T0 or T1")
    elif attack == "sybil":
        sybils = hostile.get("sybil_sources") or []
        legits = hostile.get("legit_sources") or []
        if not any(s.get("tier") == "T4" and s.get("count") == 100 for s in sybils):
            errors.append(f"{scenario['id']}: sybil needs a 100-source T4 flood (100×T4 vs 1×T0)")
        if not any(s.get("tier") == "T0" and s.get("count") == 1 for s in legits):
            errors.append(f"{scenario['id']}: sybil needs a 1-source T0 legit (100×T4 vs 1×T0)")
        for s in sybils + legits:
            if s.get("tier") not in schema.SOURCE_TIERS:
                errors.append(f"{scenario['id']}: sybil source tier {s.get('tier')!r} invalid")
    elif attack == "echo_chamber":
        ring = hostile.get("echo_ring") or []
        if len(ring) < 3:
            errors.append(f"{scenario['id']}: echo_chamber needs ≥3 ring variants")
        if len({normalize(str(r)) for r in ring}) != len(ring):
            errors.append(f"{scenario['id']}: echo_ring variants must be pairwise distinct")
    elif attack == "flapping":
        flips = hostile.get("flip") or []
        if len(flips) < 2:
            errors.append(f"{scenario['id']}: flapping needs ≥2 flip entries")
        valences = {f.get("new_valence") for f in flips}
        if not valences.issubset(set(schema.VALENCES)) or len(valences) < 2:
            errors.append(
                f"{scenario['id']}: flapping needs ≥2 flip entries with OPPOSING valences "
                f"({schema.VALENCES})")
        for f in flips:
            idx = f.get("turn_idx")
            if not isinstance(idx, int) or not (0 <= idx < n_turns):
                errors.append(
                    f"{scenario['id']}: flapping turn_idx {idx!r} out of bounds "
                    f"[0, {n_turns - 1}]")
    elif attack == "anchoring":
        for field in ("supersession_turn", "anchoring_turn"):
            idx = hostile.get(field)
            if not isinstance(idx, int) or not (0 <= idx < n_turns):
                errors.append(
                    f"{scenario['id']}: anchoring {field} {idx!r} out of bounds "
                    f"[0, {n_turns - 1}] (0-based)")
    # Hostile↔turns binding (pinned enumeration).
    binding_texts: list[str] = []
    if attack in ("poisoned", "sybil"):
        binding_texts.append(str(hostile.get("injected_claim", "")))
    elif attack == "echo_chamber":
        binding_texts.extend(str(r) for r in (hostile.get("echo_ring") or []))
    elif attack == "flapping":
        binding_texts.extend(str(f.get("claim", "")) for f in (hostile.get("flip") or []))
    elif attack == "anchoring":
        binding_texts.extend(
            str(hostile.get(k, "")) for k in ("stale_claim", "superseded_by"))
    for text in binding_texts:
        if text and not contains_phrase(turns_text, text):
            errors.append(
                f"{scenario['id']}: hostile text {text!r} (attack_type={attack}) must "
                "appear in the rendered prompt turns — the agent must experience "
                "the attack, not be told its label")


def _validate_cross_session(scenario: dict, errors: list[str]) -> None:
    scripts = _session_scripts_of(scenario)
    sessions = {s.get("session") for s in scripts}
    if not {1, 5, 6}.issubset(sessions):
        errors.append(f"{scenario['id']}: cross-session needs sessions 1, 5, 6")
        return
    s1 = next(s for s in scripts if s.get("session") == 1)
    s5 = next(s for s in scripts if s.get("session") == 5)
    s1_text = " ".join(str(t.get("content", "")) for t in s1.get("turns", []))
    s5_text = " ".join(str(t.get("content", "")) for t in s5.get("turns", []))
    early_text = " ".join(
        " ".join(str(t.get("content", "")) for t in s.get("turns", []))
        for s in scripts if s.get("session") in (1, 2, 3, 4)
    )
    for i, pair in enumerate(scenario.get("planted_contradictions") or []):
        claim = str(pair.get("claim", ""))
        counter = str(pair.get("counter_claim", ""))
        if pair.get("k") != schema.CONTRADICTION_K:
            errors.append(
                f"{scenario['id']}: cross-session contradiction {i} k must be "
                f"{schema.CONTRADICTION_K} (session index of the ¬A plant)")
        if not contains_phrase(s1_text, claim):
            errors.append(f"{scenario['id']}: claim {claim!r} must be planted in session 1")
        if not contains_phrase(s5_text, counter):
            errors.append(f"{scenario['id']}: ¬A {counter!r} must be planted in session 5")
        if contains_phrase(early_text, counter):
            errors.append(
                f"{scenario['id']}: ¬A {counter!r} pre-appears in sessions 1–4 — "
                "session 5 must be the true ¬A plant")


def _validate_evidence_tiers(scenario: dict, errors: list[str]) -> None:
    tiers = scenario.get("evidence_tiers") or []
    if not tiers:
        errors.append(f"{scenario['id']}: calibration needs evidence_tiers")
        return
    for i, item in enumerate(tiers):
        if item.get("tier") not in schema.EVIDENCE_TIERS:
            errors.append(f"{scenario['id']}: evidence tier {item.get('tier')!r} invalid")
        if item.get("valence") not in schema.VALENCES:
            errors.append(f"{scenario['id']}: evidence valence {item.get('valence')!r} invalid")
    # No-outcome-in-evidence: evidence never states the known outcome.
    gold = scenario.get(GOLD_KEY) or {}
    expected = gold.get("expected")
    if expected is not None and any(
        normalize(str(item.get("claim", ""))) == normalize(str(expected))
        for item in tiers
    ):
        errors.append(
            f"{scenario['id']}: an evidence_tiers item states the known outcome — "
            "discloses the gold")


def _validate_feedback(scenario: dict, errors: list[str]) -> None:
    iters = (scenario.get("feedback") or {}).get("iterations") or []
    if len(iters) != 5:
        errors.append(
            f"{scenario['id']}: feedback_loop needs exactly 5 authored iterations "
            f"(E2E-3.4 monotone improvement), got {len(iters)}")
    for i, it in enumerate(iters):
        if not it.get("task") or not it.get("feedback"):
            errors.append(f"{scenario['id']}: feedback iteration {i} needs task + feedback")


def validate_scenario(
    scenario: dict,
    all_ids: set[str],
    all_ct_ids: set[str],
    *,
    complete_splits: set[str] | None = None,
) -> list[str]:
    """Per-scenario validation — the ONLY per-scenario rules.

    ``all_ids`` / ``all_ct_ids`` tolerate partial/empty sets (authoring
    checkpoints pass what is accumulated): cross-id references are FORM-checked
    when the target set is incomplete and RESOLUTION-checked when present.
    ``complete_splits``: per-pack split distribution checks run only for packs
    whose accumulated count has reached the target (Task 3 semantics).
    """
    errors: list[str] = []
    sid = scenario.get("id")
    if not isinstance(sid, str) or not sid:
        return ["scenario missing id"]

    task_type = scenario.get("task_type")
    if task_type not in schema.TASK_TYPES:
        errors.append(f"{sid}: task_type {task_type!r} not in {schema.TASK_TYPES}")
        return errors
    pattern = _ID_PATTERNS.get(task_type, "")
    if pattern and not re.fullmatch(pattern, sid):
        errors.append(f"{sid}: id does not match {pattern} for task_type={task_type}")

    if scenario.get("tier") not in schema.TIERS:
        errors.append(f"{sid}: tier {scenario.get('tier')!r} invalid")
    if scenario.get("family") not in schema.FAMILIES:
        errors.append(f"{sid}: family {scenario.get('family')!r} invalid")
    if scenario.get("split") not in schema.SPLITS:
        errors.append(f"{sid}: split {scenario.get('split')!r} invalid")

    prompt = scenario.get("prompt")
    if not isinstance(prompt, dict) or not prompt.get("system"):
        errors.append(f"{sid}: prompt.system required")
    turns = prompt.get("turns", []) if isinstance(prompt, dict) else []
    if not _session_scripts_of(scenario):
        if not turns:
            errors.append(f"{sid}: prompt.turns required (non-multi-session pack)")
        for i, t in enumerate(turns):
            if t.get("role") not in ("user", "assistant") or not str(t.get("content", "")).strip():
                errors.append(f"{sid}: turn {i} needs role user|assistant + content")
    has_scripts = bool(_session_scripts_of(scenario))
    if not prompt:
        errors.append(f"{sid}: prompt required")
    elif not has_scripts and not str(prompt.get("question", "")).strip():
        errors.append(f"{sid}: prompt.question required (single-session pack)")
    if has_scripts:
        seen_sessions: set[int] = set()
        for s in _session_scripts_of(scenario):
            sess = s.get("session")
            if not isinstance(sess, int) or sess < 1:
                errors.append(f"{sid}: session number must be a positive int")
                continue
            if sess in seen_sessions:
                errors.append(f"{sid}: duplicate session {sess}")
            seen_sessions.add(sess)
            if not str(s.get("question", "")).strip():
                errors.append(f"{sid}: session {sess} missing question")
            for i, tr in enumerate(s.get("turns", [])):
                if tr.get("role") not in ("user", "assistant") or not str(tr.get("content", "")).strip():
                    errors.append(f"{sid}: session {sess} turn {i} needs role user|assistant + content")

    _validate_gold_no_substring(scenario, errors)

    if task_type == "decision":
        if scenario.get("family") not in ("R2", "R4"):
            errors.append(f"{sid}: decision family must be R2 or R4")
        if scenario.get("family") == "R4":
            gold = scenario.get(GOLD_KEY) or {}
            dc = gold.get("expected")
            if not isinstance(dc, list) or not dc or not all(str(x).strip() for x in dc):
                errors.append(f"{sid}: R4 gold.expected must be a non-empty defeat-condition list")
        mcf = scenario.get("matched_control_for")
        if mcf is not None:
            if all_ct_ids:
                if mcf not in all_ct_ids:
                    errors.append(f"{sid}: matched_control_for {mcf!r} does not resolve")
            elif not re.fullmatch(r"ct-\d{3}", str(mcf)):
                errors.append(f"{sid}: matched_control_for {mcf!r} not ct-<nnn> form")
    elif task_type == "contradiction":
        if scenario.get("family") != "R1":
            errors.append(f"{sid}: contradiction family must be R1")
        _validate_contradiction_bindings(scenario, errors)
    elif task_type == "calibration":
        if scenario.get("family") != "R3":
            errors.append(f"{sid}: calibration family must be R3")
        _validate_evidence_tiers(scenario, errors)
    elif task_type == "retraction":
        if scenario.get("family") != "R5":
            errors.append(f"{sid}: retraction family must be R5")
        retr = scenario.get("retraction") or {}
        k = retr.get("k")
        if not isinstance(k, int) or not (1 <= k <= len(turns)):
            errors.append(f"{sid}: retraction k {k!r} must be 1..{len(turns)}")
        for field in ("claim", "supporting_evidence", "retraction_event"):
            if not str(retr.get(field, "")).strip():
                errors.append(f"{sid}: retraction.{field} required")
    elif task_type == "loopy_contested":
        if scenario.get("family") != "R3":
            errors.append(f"{sid}: loopy_contested family must be R3")
        _validate_loopy(scenario, errors)
    elif task_type == "adversarial":
        if scenario.get("family") != "D4":
            errors.append(f"{sid}: adversarial family must be D4")
        _validate_adversarial(scenario, errors)
    elif task_type == "family_rep":
        if scenario.get("family") != "L2":
            errors.append(f"{sid}: family_rep family must be L2")
        fname = scenario.get("family_name")
        if fname not in schema.FAMILY_REP_NAMES:
            errors.append(f"{sid}: family_name {fname!r} not in {schema.FAMILY_REP_NAMES}")
        if scenario.get("rep") not in schema.REP_VALUES:
            errors.append(f"{sid}: rep must be one of {schema.REP_VALUES}")
        if fname == schema.HELD_OUT_FAMILY and scenario.get("split") != "held_out":
            errors.append(
                f"{sid}: held-out family {schema.HELD_OUT_FAMILY} reps must be split held_out")
        if fname != schema.HELD_OUT_FAMILY and scenario.get("split") == "held_out":
            errors.append(f"{sid}: non-held-out family reps must not be split held_out")
    elif task_type == "interdependent":
        if scenario.get("family") != "L1":
            errors.append(f"{sid}: interdependent family must be L1")
        scripts = _session_scripts_of(scenario)
        if len(scripts) < 2:
            errors.append(f"{sid}: interdependent needs ≥2 sessions (strict causal ordering)")
    elif task_type == "wave_variant":
        if scenario.get("family") != "L3":
            errors.append(f"{sid}: wave_variant family must be L3")
        if scenario.get("split") != "held_out":
            errors.append(f"{sid}: wave_variant must be split held_out (one-shot)")
        vo = scenario.get("variant_of")
        if all_ids:
            if vo not in all_ids:
                errors.append(f"{sid}: variant_of {vo!r} does not resolve")
        elif not re.fullmatch(r"d-\d{3}", str(vo)):
            errors.append(f"{sid}: variant_of {vo!r} not d-<nnn> form")
        if not str(scenario.get("delta", "")).strip():
            errors.append(f"{sid}: wave_variant delta required (harder variant)")
    elif task_type == "cross_session_contradiction":
        if scenario.get("family") != "L4":
            errors.append(f"{sid}: cross_session_contradiction family must be L4")
        _validate_cross_session(scenario, errors)
    elif task_type == "decision_drift":
        if scenario.get("family") != "L5":
            errors.append(f"{sid}: decision_drift family must be L5")
        drift = scenario.get("drift") or {}
        if not str(drift.get("decision", "")).strip():
            errors.append(f"{sid}: drift.decision required")
        offsets = drift.get("offsets")
        if offsets != ["7d", "21d"]:
            errors.append(f"{sid}: drift.offsets must be ['7d', '21d']")
    elif task_type == "feedback_loop":
        if scenario.get("family") != "D3":
            errors.append(f"{sid}: feedback_loop family must be D3")
        _validate_feedback(scenario, errors)

    if complete_splits is not None and task_type in complete_splits:
        pass  # cross-scenario split distribution — handled by validate_pack_splits
    return errors


# ---------------------------------------------------------------------------
# Cross-scenario invariants (builder only — run after full accumulation)
# ---------------------------------------------------------------------------

def validate_id_uniqueness(scenarios: list[dict]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for sc in scenarios:
        sid = sc.get("id")
        if sid in seen:
            dupes.append(str(sid))
        seen.add(str(sid))
    return [f"duplicate scenario id {d}" for d in sorted(dupes)]


def validate_controls(scenarios: list[dict]) -> list[str]:
    """matched_control_for bijection: exactly 15 decision controls ↔ the 15
    ct- ids, each referenced exactly once (E2E-1.2 FP gate)."""
    errors: list[str] = []
    ct_ids = sorted(
        sc["id"] for sc in scenarios if sc.get("task_type") == "contradiction")
    controls = [
        sc.get("matched_control_for") for sc in scenarios
        if sc.get("task_type") == "decision" and sc.get("matched_control_for")
    ]
    if len(controls) != len(ct_ids):
        errors.append(
            f"controls bijection broken: {len(controls)} decision controls vs "
            f"{len(ct_ids)} contradiction scenarios")
    seen: dict[str, list[str]] = {}
    for sc in scenarios:
        mcf = sc.get("matched_control_for")
        if mcf:
            seen.setdefault(str(mcf), []).append(str(sc.get("id")))
    for cid, owners in sorted(seen.items()):
        if cid not in ct_ids:
            errors.append(f"matched_control_for {cid} does not resolve to a contradiction id")
        if len(owners) != 1:
            errors.append(f"matched_control_for {cid} referenced {len(owners)} times: {owners}")
    missing = [c for c in ct_ids if c not in seen]
    if missing:
        errors.append(f"contradiction scenarios without a matched control: {missing}")
    return errors


def validate_pack_splits(scenarios: list[dict]) -> list[str]:
    """Exact per-pack split distribution == schema.PACK_SPLITS."""
    errors: list[str] = []
    for task_type, expected in schema.PACK_SPLITS.items():
        actual: dict[str, int] = {}
        for sc in scenarios:
            if sc.get("task_type") == task_type:
                actual[sc.get("split")] = actual.get(sc.get("split"), 0) + 1
        if actual != expected:
            errors.append(
                f"PACK_SPLITS mismatch for {task_type}: expected {expected}, got {actual}")
    return errors


def validate_attack_distribution(scenarios: list[dict]) -> list[str]:
    errors: list[str] = []
    actual: dict[str, int] = {}
    for sc in scenarios:
        if sc.get("task_type") == "adversarial":
            at = sc.get("attack_type")
            actual[at] = actual.get(at, 0) + 1
    if actual != schema.ATTACK_DISTRIBUTION:
        errors.append(
            f"attack_type distribution mismatch: expected {schema.ATTACK_DISTRIBUTION}, "
            f"got {actual}")
    return errors


def validate_pack_counts(scenarios: list[dict]) -> list[str]:
    errors: list[str] = []
    for task_type, count in schema.PACK_COUNTS.items():
        actual = sum(1 for sc in scenarios if sc.get("task_type") == task_type)
        if actual != count:
            errors.append(
                f"pack count mismatch for {task_type}: expected {count}, got {actual} "
                "(grow a pack ⇒ bump CORPUS_VERSION)")
    return errors


def validate_held_out_family(scenarios: list[dict]) -> list[str]:
    """The L2 held-out family's 3 reps are all split held_out (E2E-2.1)."""
    held = [
        sc for sc in scenarios
        if sc.get("task_type") == "family_rep"
        and sc.get("family_name") == schema.HELD_OUT_FAMILY
    ]
    if len(held) != 3 or any(sc.get("split") != "held_out" for sc in held):
        return [
            f"held-out family {schema.HELD_OUT_FAMILY} must have exactly 3 reps, "
            f"all split held_out (got {len(held)})"]
    return []
