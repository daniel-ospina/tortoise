"""W2-b blind judges + verbatim control lane (issue #2098).

Two judge arms, both pinned and deterministic-by-construction:

1. **Mechanical arm** (authoritative, always on, BPRE default) — the 6-metric
   graders in ``grading.py`` are deterministic checks against the sealed gold
   (anchor grounding, provenance presence, quote grounding, leakage, emit
   counts).  Mechanical checks are authoritative OVER judge output (per the
   issue's grading hierarchy).  The BPRE judge pin is
   ``JUDGE_PIN_MECHANICAL`` — a run that only ever runs the mechanical arm
   records that pin, which is what makes its numbers publishable (a baseline
   with non-empty metrics requires a pinned judge).

2. **Salience arm** (LLM, ``full`` mode only, cost-tracked) — the gbrain-style
   BLIND salience judge.  The planted gold deliberately carries NO paraphrase
   statements (W2-a corpus README), so W2-b synthesizes them with a pinned
   paraphrase stage, then grades memory coverage with a pinned judge stage:

   * ``paraphrase_stage`` — GOLD-SEEING by construction (the synthesis step
     the corpus README tells W2-b to supply).  Takes the planted unit
     (kind + verbatim anchor + planted turn context) and emits ONE neutral
     paraphrase probe whose wording the judge cannot reverse back to the
     anchor.  Prompt pinned by ``PARAPHRASE_PROMPT_VERSION``.
   * ``salience_stage`` — BLIND by construction: its prompt is built ONLY
     from the paraphrase probes + the memory point contents.  It NEVER sees
     anchors, the gold, or the fixtures (the blindness test asserts the
     built prompt contains no anchor substring and the runner fail-closes if
     ``prompt_leaks_anchor`` fires before the call).  Prompt pinned by
     ``SALIENCE_PROMPT_VERSION``.  Labels: FULL / PARTIAL / ABSENT.

   Judge_pin for a full lane = ``JUDGE_PIN_FULL`` (both stage versions
   concatenated — a bump to either stage re-pins and re-synthesizes).

3. **Verbatim control lane** — the calibration ceiling for BOTH arms.  The
   control memory is the fixture conversation written back VERBATIM (every
   turn content as a memory point).  Mechanical macro survival on the control
   is 1.0 by construction — a corpus/grader drift (anchor not recoverable
   even from the verbatim transcript) shows up here, not as a silent
   pipeline miss.  When the salience arm runs, its coverage over the control
   memory is the judge's ceiling (a judge that cannot see coverage on a
   perfect memory is miscalibrated, not the pipeline).

LLM invocation follows the retrieval-eval precedent (``tests/eval/retrieval/
judge.py``): a model from the ``tests/model_adapters.py`` MODELS registry,
``.complete(system=..., user=...)``.  No ambient key ⇒ ``judge_available``
False and the run records the mechanical pin (numbers stay publishable
against the mechanical arm only; the LLM arm is never silently assumed).

Hermetic default: importing this module costs no DB/network/LLM; the model
path is lazy.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable

from tests.eval.write_path import grading, schema

# ── Pinned judge prompts ────────────────────────────────────────────────────
# Version bumps invalidate prior judge_pin baselines (schema requires a pin on
# every published refresh); a bump means "the judge protocol changed", so a
# fix-wave re-run that keeps the SAME pin is comparable and one that bumps
# re-synthesizes probes + re-grades from scratch.

# v1 -> v2 (#2405): the mechanical survival rule gained the paraphrase leg
# (grading.survival_match — anchor-coverage band gated on
# accepts_rephrase_linked + shared-token floor + polarity gate). Runs under
# v2 are NOT comparable to v1 baselines (main.json 0.25 / m2.json 0.9722
# were graded verbatim-only): both must protocol-re-bless under v2 before
# any further compare/bless (runner judge-pin mismatch => inconclusive).
JUDGE_PIN_MECHANICAL = "w2-write-path-mechanical-v2"
PARAPHRASE_PROMPT_VERSION = "w2-salience-paraphrase-v1"
SALIENCE_PROMPT_VERSION = "w2-salience-blind-v1"
# Full-lane pin: both stage versions — judge_pin naming EVERY prompt that
# produced the graded labels (the schema's non-null judge_pin discipline).
JUDGE_PIN_FULL = f"{SALIENCE_PROMPT_VERSION}+{PARAPHRASE_PROMPT_VERSION}"

_COVERAGE_LABELS = ("FULL", "PARTIAL", "ABSENT")

_PARAPHRASE_SYSTEM = (
    "You write paraphrase probes for a memory-coverage judge (epic #2080 "
    "W2-b, Tortoise write-path eval). A planted claim is turned into ONE "
    "neutral paraphrase that preserves its meaning but uses different words, "
    "so the judge cannot recover the original wording from the probe.\n"
    "\n"
    "## Rules\n"
    "- Keep every claim-critical content: numbers, dates, ownership, "
    "decisions, and causal facts must survive the paraphrase unchanged in "
    "MEANING (never in wording).\n"
    "- Do not quote the input or echo its sentence structure.\n"
    "- Write one declarative sentence.\n"
    "\n"
    '## Output\n'
    'Return ONLY a JSON object: {"paraphrase": "<one sentence>"} — no '
    "markdown fences, no extra keys."
)

_SALIENCE_SYSTEM = (
    "You are a blind memory-coverage judge for Tortoise, an epistemic memory "
    "graph engine (epic #2080 W2-b write-path eval). You receive PARAPHRASE "
    "probes describing claims a session was expected to retain, and MEMORY "
    "notes the write path actually retained. You decide, per probe, whether "
    "the memory preserves the probe's meaning.\n"
    "\n"
    "## Graded coverage (3 labels — closed vocabulary)\n"
    "FULL = the memory states the probe's claim explicitly (same meaning, "
    "wording may differ).\n"
    "PARTIAL = the memory has related content but the claim is not fully "
    "preserved (missing the decision, the number, the owner, or the causal "
    "link).\n"
    "ABSENT = no memory note addresses the probe.\n"
    "\n"
    "## Rules\n"
    "- Judge MEANING, not wording: a paraphrase of the claim is FULL.\n"
    "- A note that merely shares a topic with the probe but does not state "
    "its claim is PARTIAL at most.\n"
    "- Grade EVERY probe exactly once.\n"
    "\n"
    '## Output\n'
    'Return ONLY a JSON object: {"<probe_id>": "FULL"|"PARTIAL"|"ABSENT", '
    "…} — no markdown fences, no extra keys."
)


def paraphrase_stage_prompt(unit: dict, kind_label: str) -> str:
    """Build the paraphrase-stage user prompt for one planted unit.

    Gold-seeing by construction (this stage synthesizes the probes the blind
    judge consumes) — it may name the anchor freely; the blind boundary sits
    BETWEEN this stage and the salience stage.
    """
    anchor = unit.get("verbatim_anchor") or unit.get("survival", {}).get("via_anchor") or ""
    return (
        f"Planted claim kind: {kind_label} (kind of {anchor!r}).\n"
        f'Plant the claim: "{anchor}"\n'
        "Write the paraphrase probe."
    )


def build_salience_prompt(probes: dict[str, str], memory_contents: list[str]) -> str:
    """Build the BLIND judge user prompt from paraphrase probes + memory only.

    ``memory_contents`` are the graded memory point contents (the same
    content the mechanical arm grades).  This prompt must never contain a
    verbatim anchor — the runner fail-closes via ``prompt_leaks_anchor``
    before any call.
    """
    memory_block = "\n".join(
        f"{i + 1}. {content}" for i, content in enumerate(memory_contents)
    ) or "(no memory notes)"
    probe_block = "\n".join(f"{pid}: {probe}" for pid, probe in probes.items())
    return (
        f"## MEMORY NOTES\n{memory_block}\n\n"
        f"## PROBES\n{probe_block}"
    )


def prompt_leaks_anchor(prompt: str, anchors: list[str]) -> str | None:
    """Return the first anchor whose normalized text appears in ``prompt``.

    The blindness guarantee is an ASSERTED property of the built prompt, not
    a hope: the runner checks before every salience-stage call and raises
    when an anchor leaked into what the judge would see.
    """
    prompt_norm = schema.normalize_text(prompt)
    for anchor in anchors:
        if schema.normalize_text(anchor) in prompt_norm:
            return anchor
    return None


# ── Response parsing ────────────────────────────────────────────────────────


class JudgeProtocolError(ValueError):
    """A judge/paraphrase response violates the pinned protocol."""


def _strip_fences(raw: str) -> str:
    raw = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, flags=re.DOTALL)
    return m.group(1) if m else raw


def parse_paraphrase(raw: str, unit_id: str) -> str:
    try:
        doc = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        raise JudgeProtocolError(
            f"paraphrase stage returned non-JSON for {unit_id}: {raw[:200]!r}"
        ) from e
    if not isinstance(doc, dict):
        raise JudgeProtocolError(f"paraphrase stage for {unit_id} returned a non-object")
    text = doc.get("paraphrase")
    if not isinstance(text, str) or not text.strip():
        raise JudgeProtocolError(f"paraphrase stage for {unit_id} has no 'paraphrase' string")
    return text.strip()


def parse_salience(raw: str, probe_ids: list[str]) -> dict[str, str]:
    try:
        doc = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        raise JudgeProtocolError(
            f"salience judge returned non-JSON: {raw[:200]!r}"
        ) from e
    if not isinstance(doc, dict):
        raise JudgeProtocolError("salience judge returned a non-object")
    out: dict[str, str] = {}
    for pid in probe_ids:
        label = doc.get(pid)
        if label not in _COVERAGE_LABELS:
            raise JudgeProtocolError(
                f"salience judge label for {pid!r}: expected one of "
                f"{_COVERAGE_LABELS}, got {label!r}"
            )
        out[pid] = label
    return out


# ── LLM runner (lazy; follows the retrieval-eval judge precedent) ──────────


def _default_model_factory(model_name: str) -> object:
    from tests.model_adapters import MODELS  # lazy: no import cost when unused

    try:
        return MODELS[model_name]()
    except KeyError:
        raise ValueError(
            f"unknown model {model_name!r} — choose from {', '.join(sorted(MODELS))}"
        ) from None


class SalienceJudge:
    """Pinned two-stage blind salience judge over one session's gold + memory.

    ``model_name`` resolves against ``tests/model_adapters.py`` MODELS (the
    retrieval-eval convention).  ``model_factory`` is injectable for tests
    (a fake model records prompts and returns canned JSON).
    """

    def __init__(
        self,
        *,
        model_name: str = "deepseek-v4-flash",
        model_factory: Callable[[str], object] | None = None,
        paraphrase_model_name: str | None = None,
    ) -> None:
        factory = model_factory or _default_model_factory
        self._judge_model = factory(model_name)
        self._paraphrase_model = factory(paraphrase_model_name or model_name)
        self.cost_usd = 0.0
        self.prompt_count = 0
        self._judge_prompts: list[str] = []
        self._paraphrase_prompts: list[str] = []

    # Cost bookkeeping — the OpenRouter-family adapters expose per-call usage
    # (prompt/completion tokens); other adapters expose nothing, in which case
    # the run records cost 0 with a note (never a fabricated number).
    def _complete(self, model: object, *, system: str, user: str) -> str:
        raw = model.complete(system=system, user=user)  # type: ignore[attr-defined]
        self.prompt_count += 1
        return raw

    def record_usage(self, model: object) -> None:
        """Fold one model's per-call usage into the judge's cost snapshot."""
        if hasattr(model, "last_cost") and isinstance(model.last_cost, (int, float)):
            self.cost_usd += float(model.last_cost or 0.0)

    def synthesize_probes(self, gold: dict, session: dict) -> dict[str, str]:
        """Paraphrase-stage: gold's salient units → {unit_id: probe}."""
        kind_by_id = {
            u.get("id"): u.get("kind", "fact")
            for u in gold.get("planted_units", [])
            if isinstance(u, dict)
        }
        probes: dict[str, str] = {}
        units = gold.get("salient_units", [])
        for entry in units:
            if not isinstance(entry, dict):
                continue
            unit_id = entry.get("id")
            planted = next(
                (
                    u
                    for u in gold.get("planted_units", [])
                    if isinstance(u, dict) and u.get("id") == unit_id
                ),
                {"verbatim_anchor": entry.get("survival", {}).get("via_anchor", "")},
            )
            user = paraphrase_stage_prompt(
                planted, kind_by_id.get(unit_id, "fact")
            )
            self._paraphrase_prompts.append(user)
            raw = self._complete(
                self._paraphrase_model,
                system=_PARAPHRASE_SYSTEM,
                user=user,
            )
            self.record_usage(self._paraphrase_model)
            probes[str(unit_id)] = parse_paraphrase(raw, str(unit_id))
        return probes

    def grade_coverage(self, probes: dict[str, str], memory: list[str]) -> dict[str, str]:
        """Salience-stage: blind coverage labels over the memory layer."""
        if not probes:
            return {}
        user = build_salience_prompt(probes, memory)
        self._judge_prompts.append(user)
        raw = self._complete(
            self._judge_model, system=_SALIENCE_SYSTEM, user=user
        )
        self.record_usage(self._judge_model)
        return parse_salience(raw, sorted(probes))

    @property
    def judge_prompts(self) -> list[str]:
        return list(self._judge_prompts)

    @property
    def paraphrase_prompts(self) -> list[str]:
        return list(self._paraphrase_prompts)


# ── Verbatim control lane ───────────────────────────────────────────────────


def control_lane_points(conversation: list[dict[str, str]]) -> list[dict]:
    """The verbatim control memory: every turn content as a memory point.

    A perfect (verbatim) writer would retain exactly this — the mechanical
    macro survival on the control lane is 1.0 BY CONSTRUCTION when the corpus
    is sound (every planted anchor is a normalized substring of its planted
    turn, which is verbatim in the control memory).  If the control lane ever
    grades below 1.0 the CORPUS/GRADER is broken (anchor not recoverable from
    the verbatim transcript), never the pipeline — the runner's pre-flight
    self-check.
    """
    return [
        {"point_id": f"control_t{i}", "content": turn.get("content", ""),
         "provenance_present": False, "ep_updated": False}
        for i, turn in enumerate(conversation)
        if isinstance(turn.get("content"), str) and turn.get("content", "").strip()
    ]


def control_macro_counts(gold: dict, conversation: list[dict[str, str]]) -> dict:
    """Macro survival of the session's gold against the verbatim control."""
    return grading.macro_survival_counts(gold, control_lane_points(conversation))


def judge_survival(coverage: dict[str, str]) -> dict:
    """Fold salience coverage labels into survival counts.

    A probe graded FULL or PARTIAL counts as retained content (the mechanical
    macro dimension's meaning-presence analogue); ABSENT does not.  Pooled
    by the caller across sessions.
    """
    survived = sum(1 for label in coverage.values() if label != "ABSENT")
    return {"survived": survived, "total": len(coverage)}
