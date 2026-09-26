"""W2-b blind judges + verbatim control lane (issue #2098).

Two judge arms, both pinned and deterministic-by-construction, plus the
banded semantic arm added by #5085:

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

import contextlib
import json
import math
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
        # Registry KEY (tests/model_adapters.py MODELS), not a raw model id —
        # the prior default "deepseek-v4-flash" is an OpenRouter model id with
        # no MODELS entry, so building the documented default raised
        # ValueError (found while wiring #5085; the class was never invoked by
        # the runner, which is why it was never exercised).
        model_name: str = "deepseek-flash",
        model_factory: Callable[[str], object] | None = None,
        paraphrase_model_name: str | None = None,
    ) -> None:
        factory = model_factory or _default_model_factory
        self._judge_model = factory(model_name)
        self._paraphrase_model = factory(paraphrase_model_name or model_name)
        self.cost_usd = 0.0
        self.cost_priced_calls = 0
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
        """Fold one model's per-call cost into the judge's cost snapshot.

        ``last_cost_usd`` is the ONLY dollar figure the adapters report
        (``tortoise/model_adapters.py`` sets it from the provider's
        ``usage.cost`` — and to ``None``, not 0.0, when the route reports no
        charge).  The same adapter's legacy ``last_cost`` attribute carries a
        TOKEN count (``usage.total_tokens``), so reading it as USD fabricates
        a spend figure — exactly the failure this method must not commit.
        A route that reports no charge is recorded as a GAP
        (``cost_priced_calls`` does not move), never as a made-up number.
        """
        value = getattr(model, "last_cost_usd", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.cost_usd += float(value)
            self.cost_priced_calls += 1

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


# ── Banded semantic judge (issue #5085) ─────────────────────────────────────
#
# The owner-decided additive leg (the D14 resolution on #5085 — "the
# two-number split"): mechanical anchor fidelity stays authoritative for the
# gated ``metrics`` vocabulary, and a JUDGED knowledge-preservation number is
# published BESIDE it — never replacing it.  The judged numbers never enter
# ``metrics`` (a semantic key there would shrink/re-type the committed
# baseline compare set); they ride in the receipt's additive
# ``semantic_judge`` block.
#
# The probability is EARNED, never asserted (arXiv 2512.22245, 2508.06225):
# the blind judge answers ONE binary preservation question per probe, N
# independent samples (sampling temperature > 0 — the adapter seam's own
# default is 0.0, which would make every sample identical and collapse the
# probability to 0/1), and the unit's probability IS the agreement fraction.
# Each sample is asked in BOTH prompt orders (probes-first / memory-first)
# and the two orders are averaged — the documented systematic position-bias
# mitigation (arXiv 2406.07791).  No verbalized confidence is requested or
# trusted; if an adapter ever exposes token logprobs they are recorded as a
# coarse cross-check only.
#
# Protocol pin: ``SEMANTIC_JUDGE_PIN`` names EVERY prompt that produced the
# graded labels (the paraphrase stage that supplies the probe surface AND
# the banded blind judge), so the fix-wave protocol stays reproducible — a
# bump re-synthesizes probes + re-judges from scratch.  It sits ALONGSIDE
# ``JUDGE_PIN_MECHANICAL``; the receipt's ``judge_pin`` stays mechanical, so
# the committed baselines' comparability surface is untouched.

SEMANTIC_PROMPT_VERSION = "w2-semantic-banded-v1"
SEMANTIC_JUDGE_PIN = f"{SEMANTIC_PROMPT_VERSION}+{PARAPHRASE_PROMPT_VERSION}"

# Owner-specified bands (D14 resolution, #5085) as INCLUSIVE lower bounds.
BAND_SAME_FACT = "same_fact"
BAND_LIKELY = "likely"
BAND_MAYBE = "maybe"
BAND_LIKELY_NOT = "likely_not"
BAND_ORDER = (BAND_SAME_FACT, BAND_LIKELY, BAND_MAYBE, BAND_LIKELY_NOT)
# (lower_bound, band), highest first.  The thresholds are the owner's numbers;
# nothing here re-derives or softens them.
BAND_THRESHOLDS: tuple[tuple[float, str], ...] = (
    (0.80, BAND_SAME_FACT),
    (0.70, BAND_LIKELY),
    (0.50, BAND_MAYBE),
    (0.0, BAND_LIKELY_NOT),
)
# Band representatives for the band-weighted score = the MIDPOINT of each
# band's probability interval, with the two open ends capped at 1.0 (top) and
# 0.0 (bottom): pure arithmetic on the thresholds above, no invented number.
# same_fact [0.80, 1.0] -> 0.90; likely [0.70, 0.80) -> 0.75;
# maybe [0.50, 0.70) -> 0.60; likely_not [0.0, 0.50) -> 0.25.
BAND_WEIGHTS: dict[str, float] = {
    BAND_SAME_FACT: 0.90,
    BAND_LIKELY: 0.75,
    BAND_MAYBE: 0.60,
    BAND_LIKELY_NOT: 0.25,
}

# A unit at/above the MAYBE threshold is "judged preserved" — the
# semantic analogue of the mechanical macro dimension (meaning present, not
# verbatim wording present).
SEMANTIC_SURVIVAL_THRESHOLD = 0.50

# Sampling defaults.  ``samples`` >= 3 (owner floor) and the 1/(2*samples)
# probability grid must be fine enough that all four bands are REACHABLE:
# with the 2-order swap, samples=5 gives a 0.1 grid, so 0.80 / 0.70 / 0.50
# are all hit; samples=3 (a 1/6 grid) could never land in [0.70, 0.80).
DEFAULT_JUDGE_SAMPLES = 5
DEFAULT_JUDGE_TEMPERATURE = 0.7
# The judge default is deliberately a DIFFERENT model from the extractor's
# registry default (``deepseek-flash`` -> deepseek/deepseek-v4-flash):
# self-preference bias is documented for a model grading its own output, so
# the judged arm is served by a different provider family (upstage
# solar-pro4).  The run records both and notes any residual family overlap.
DEFAULT_JUDGE_MODEL = "solar-pro4"
PROMPT_ORDERS = ("probes_first", "memory_first")
# A paraphrase that ECHOES the anchor is rejected and re-synthesized this many
# times before the run fails.  The guard still gates every judge call — this
# only stops one echoing paraphrase from failing a whole run.  MEASURED need:
# a full-corpus real run (#5085 evidence) failed with `probe leaked a verbatim
# gold anchor ('Maya is the natural driver for the flip')`, so the paraphrase
# model does echo sometimes despite the prompt's "do not quote" rule.
DEFAULT_PARAPHRASE_LEAK_RETRIES = 2

_BANDED_LABELS = ("PRESERVED", "NOT_PRESERVED")

_BANDED_SYSTEM = (
    "You are a blind memory-coverage judge for Tortoise, an epistemic memory "
    "graph engine (epic #2080 W2-b write-path eval). You receive PARAPHRASE "
    "probes describing claims a session was expected to retain, and MEMORY "
    "notes the write path actually retained. You decide, per probe, whether "
    "the memory preserves the probe's claim.\n"
    "\n"
    "## Verdicts (2 labels — closed vocabulary)\n"
    "PRESERVED = the memory states the probe's claim, possibly in different "
    "words, and every claim-critical element (number, date, owner, decision, "
    "causal link) is retained.\n"
    "NOT_PRESERVED = the memory does not state the probe's claim: it omits "
    "it, retains only related topical content, or states something that "
    "CONTRADICTS the probe.\n"
    "\n"
    "## Rules\n"
    "- Judge MEANING, not wording: a paraphrase of the claim is PRESERVED.\n"
    "- A note that merely shares a topic with the probe is NOT_PRESERVED.\n"
    "- A note stating the OPPOSITE of the probe is NOT_PRESERVED.\n"
    "- Grade EVERY probe exactly once.\n"
    "\n"
    '## Output\n'
    'Return ONLY a JSON object: {"<probe_id>": "PRESERVED"|"NOT_PRESERVED", '
    "…} — no markdown fences, no extra keys."
)


def band_for_probability(probability: float) -> str:
    """Map an agreement fraction onto the owner's four bands."""
    p = float(probability)
    for lower, band in BAND_THRESHOLDS:
        if p >= lower:
            return band
    return BAND_LIKELY_NOT


def band_weight(band: str) -> float:
    """The band's representative probability for the band-weighted score."""
    try:
        return BAND_WEIGHTS[band]
    except KeyError:
        raise ValueError(f"unknown band {band!r} — one of {BAND_ORDER}") from None


class JudgeBlindnessError(ValueError):
    """The blind judge prompt contained a verbatim gold anchor.

    Judge-blindness is LOAD-BEARING (corpus README; plan §J4 makes a
    violation a harness failure).  This is raised, never logged: a leaky
    judge run must fail, not publish a number produced by lexical matching
    against the anchor it was supposed to be blind to.
    """


def build_banded_prompt(
    probes: dict[str, str],
    memory_contents: list[str],
    *,
    order: str = "probes_first",
) -> str:
    """Build the BLIND banded-judge user prompt in one of the two orders.

    ``order`` positions the probe block relative to the memory block.
    Position bias is documented and systematic, so every unit is judged in
    BOTH orders and the two verdict rates are averaged (the caller's job).

    Blindness is a property of the PROBE block (the claim under test): it must
    never carry a verbatim anchor.  The MEMORY block is observed data — a
    stored Point may legitimately contain the anchor's own wording — so it is
    not part of the blindness check.
    """
    if order not in PROMPT_ORDERS:
        raise ValueError(f"unknown prompt order {order!r} — one of {PROMPT_ORDERS}")
    memory_block = "\n".join(
        f"{i + 1}. {content}" for i, content in enumerate(memory_contents)
    ) or "(no memory notes)"
    probe_block = "\n".join(f"{pid}: {probe}" for pid, probe in probes.items())
    blocks = {"MEMORY NOTES": memory_block, "PROBES": probe_block}
    keys = ("PROBES", "MEMORY NOTES") if order == "probes_first" \
        else ("MEMORY NOTES", "PROBES")
    return "\n\n".join(f"## {key}\n{blocks[key]}" for key in keys)


def parse_banded(raw: str, probe_ids: list[str]) -> dict[str, bool]:
    """Parse one banded-judge response into ``{probe_id: preserved?}``.

    The vocabulary is closed (``PRESERVED`` / ``NOT_PRESERVED``); anything
    else — a missing probe, an unknown label — is a protocol error, so a
    half-parsed sample never silently counts as agreement.
    """
    try:
        doc = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as e:
        raise JudgeProtocolError(
            f"banded judge returned non-JSON: {raw[:200]!r}"
        ) from e
    if not isinstance(doc, dict):
        raise JudgeProtocolError("banded judge returned a non-object")
    out: dict[str, bool] = {}
    for pid in probe_ids:
        label = doc.get(pid)
        if label not in _BANDED_LABELS:
            raise JudgeProtocolError(
                f"banded judge label for {pid!r}: expected one of "
                f"{_BANDED_LABELS}, got {label!r}"
            )
        out[pid] = label == "PRESERVED"
    return out


def _snapshot_logprobs(model: object) -> float | None:
    """Mean token logprob of a model's last call, when the seam exposes it.

    The production adapters (OpenRouterModel, DeepSeekDirectModel) expose no
    token logprobs today, so this returns None and the receipt records the
    cross-check as unavailable — the probability then stands on the
    repeated-judgement agreement alone, which is the protocol's primary
    signal by design.
    """
    for attr in ("last_logprobs", "last_token_logprobs"):
        raw = getattr(model, attr, None)
        if isinstance(raw, (list, tuple)) and raw:
            try:
                values = [float(v) for v in raw]
            except (TypeError, ValueError):
                return None
            return sum(values) / len(values)
    return None


def logprob_crosscheck_from_samples(samples: list[float]) -> dict:
    """Coarse token-logprob cross-check over pooled per-call means.

    Module-level so the runner can pool samples across per-session judge
    instances (the class method only sees its own session).
    """
    if not samples:
        return {
            "available": False,
            "token_logprob_mean": None,
            "token_probability_geomean": None,
            "note": "the judge adapter exposes no token logprobs — the "
                    "probability stands on repeated-judgement agreement "
                    "alone (the protocol's primary signal)",
        }
    mean = sum(samples) / len(samples)
    return {
        "available": True,
        "token_logprob_mean": round(mean, 6),
        "token_probability_geomean": round(math.exp(mean), 6),
        "note": "mean token logprob over the verdict calls — a coarse "
                "certainty cross-check, not the answer probability",
    }


class BandedSalienceJudge:
    """Blind, banded salience judge with an EARNED probability (#5085).

    Per session: the gold-seeing paraphrase stage synthesizes one neutral
    probe per planted unit (the same boundary the ``SalienceJudge`` uses),
    then the blind stage scores every probe.  Each (order, sample) call is
    one vote; a unit's probability is ``yes_votes / total_votes`` and its
    band follows the owner's thresholds.

    ``anchors`` is the blindness GUARD's input only — it is never placed in
    a prompt.  Building with the default empty list disables the guard
    (tests do this deliberately); the runner always passes the session's
    real anchors, and a leak raises ``JudgeBlindnessError``.
    """

    def __init__(
        self,
        *,
        model_name: str = DEFAULT_JUDGE_MODEL,
        model_factory: Callable[[str], object] | None = None,
        paraphrase_model_name: str | None = None,
        samples: int = DEFAULT_JUDGE_SAMPLES,
        orders: tuple[str, ...] = PROMPT_ORDERS,
        temperature: float = DEFAULT_JUDGE_TEMPERATURE,
        anchors: list[str] | None = None,
        max_paraphrase_leak_retries: int = DEFAULT_PARAPHRASE_LEAK_RETRIES,
    ) -> None:
        if samples < 1:
            raise ValueError(f"samples must be >= 1, got {samples!r}")
        unknown = [o for o in orders if o not in PROMPT_ORDERS]
        if unknown or not orders:
            raise ValueError(f"orders must be a non-empty subset of {PROMPT_ORDERS}")
        factory = model_factory or _default_model_factory
        # The paraphrase stage stays at the adapter default (0.0) — the probe
        # surface is meant to be reproducible; only the JUDGE samples for
        # independence.
        self._judge_model = factory(model_name)
        self._paraphrase_model = factory(paraphrase_model_name or model_name)
        self._apply_temperature(self._judge_model, temperature)
        self.model_name = model_name
        self.paraphrase_model_name = paraphrase_model_name or model_name
        self.samples = int(samples)
        self.orders = tuple(orders)
        self.temperature = float(temperature)
        self._anchors = [a for a in (anchors or []) if isinstance(a, str) and a]
        self.max_paraphrase_leak_retries = max(0, int(max_paraphrase_leak_retries))
        self.leak_retries_used = 0
        self.leak_rejections = 0
        self.cost_usd = 0.0
        self.cost_priced_calls = 0
        self.prompt_count = 0
        # Role-specific call counts: ``prompt_count`` is the TOTAL (paraphrase
        # stage + judge verdicts), so a receipt field named ``judge_calls``
        # must read the verdict count, not the total.
        self.judge_call_count = 0
        self.paraphrase_call_count = 0
        self._logprob_samples: list[float] = []

    @property
    def judge_model_id(self) -> str:
        """The judge adapter's WIRE model id (not the MODELS registry key).

        Independence from the extractor is a claim about the MODEL, so it
        must be checked between two wire ids ('upstage/solar-pro4' vs
        'deepseek/deepseek-v4-flash') — comparing a registry KEY to a wire id
        is a namespace mismatch that can assert a false independence.
        """
        return str(getattr(self._judge_model, "id", "") or self.model_name)

    @property
    def logprob_samples(self) -> list[float]:
        """Every per-call token-logprob mean observed so far (cross-check)."""
        return list(self._logprob_samples)

    @staticmethod
    def _apply_temperature(model: object, temperature: float) -> None:
        """Sample at >0 so repeated judgements are independent.

        The MODELS registry pins ``temperature=0.0``; identical prompts at
        temp 0 return identical verdicts, which would make every unit's
        agreement fraction exactly 0 or 1 and defeat the protocol.  The
        adapters read ``self.temperature`` per call, so the attribute is the
        seam.  Fakes/tests without the attribute are unaffected.
        """
        if hasattr(model, "temperature"):
            with contextlib.suppress(TypeError, ValueError):
                model.temperature = float(temperature)  # type: ignore[attr-defined]

    def _complete(self, model: object, *, system: str, user: str) -> str:
        raw = model.complete(system=system, user=user)  # type: ignore[attr-defined]
        self.prompt_count += 1
        return raw

    def record_usage(self, model: object) -> None:
        """Fold one model's per-call cost into the judge's cost snapshot.

        ``last_cost_usd`` is the ONLY dollar figure the adapters report
        (``tortoise/model_adapters.py`` sets it from the provider's
        ``usage.cost`` — and to ``None``, not 0.0, when the route reports no
        charge).  The same adapter's legacy ``last_cost`` attribute carries a
        TOKEN count (``usage.total_tokens``), so reading it as USD would
        publish a spend figure in the thousands — a route with no reported
        charge is recorded as a GAP (``cost_priced_calls`` does not move),
        never as a made-up number.
        """
        value = getattr(model, "last_cost_usd", None)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            self.cost_usd += float(value)
            self.cost_priced_calls += 1

    def _guard(self, probes: dict[str, str]) -> None:
        """Fail-closed blindness check on the PROBE surface.

        Blindness is about the CLAIM the judge is asked to verify: the probe
        (paraphrase-level statement) must never carry the verbatim anchor, or
        coverage scoring degrades into lexical matching against the answer
        key.  The MEMORY block is the observed data — a stored Point may
        legitimately contain the anchor's own words, so it is deliberately NOT
        part of this check.  This is the seam the guard-removal test hits.
        """
        probe_surface = "\n".join(f"{pid}: {probe}" for pid, probe in probes.items())
        leak = prompt_leaks_anchor(_BANDED_SYSTEM + "\n" + probe_surface, self._anchors)
        if leak is not None:
            raise JudgeBlindnessError(
                "blind banded-judge probe leaked a verbatim gold anchor "
                f"({leak!r}) — judge-blindness is load-bearing (plan §J4); "
                "the run must fail, not publish a lexically-matched number"
            )

    def synthesize_probes(self, gold: dict, session: dict) -> dict[str, str]:
        """Paraphrase stage: gold's salient units → ``{unit_id: probe}``.

        An anchor-echoing paraphrase is REJECTED and re-synthesized (bounded by
        ``max_paraphrase_leak_retries``); if it still echoes, the run fails —
        the blindness guarantee is never weakened to make a run pass.
        """
        kind_by_id = {
            u.get("id"): u.get("kind", "fact")
            for u in gold.get("planted_units", [])
            if isinstance(u, dict)
        }
        probes: dict[str, str] = {}
        for entry in gold.get("salient_units", []):
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
            user = paraphrase_stage_prompt(planted, kind_by_id.get(unit_id, "fact"))
            retry_note = ""
            retries = 0
            while True:
                raw = self._complete(
                    self._paraphrase_model, system=_PARAPHRASE_SYSTEM,
                    user=user + retry_note,
                )
                self.paraphrase_call_count += 1
                self.record_usage(self._paraphrase_model)
                probe = parse_paraphrase(raw, str(unit_id))
                leak = prompt_leaks_anchor(probe, self._anchors)
                if leak is None:
                    break
                self.leak_rejections += 1
                if retries >= self.max_paraphrase_leak_retries:
                    raise JudgeBlindnessError(
                        "paraphrase stage echoed a verbatim gold anchor "
                        f"({leak!r}) for {unit_id!r} after {retries + 1} attempts "
                        "— judge-blindness is load-bearing (plan §J4); the run "
                        "must fail, not publish a lexically-matched number"
                    )
                retries += 1
                self.leak_retries_used += 1
                # The paraphrase stage is GOLD-SEEING by construction, so
                # naming the leaked span in the retry is safe — and necessary:
                # at temperature 0.0 the same prompt can reproduce the same
                # echo, so the retry must be a DIFFERENT input.
                retry_note = (
                    "\n\nYour previous attempt reused the original wording "
                    f"({leak!r}). Rewrite it without using that phrasing."
                )
            probes[str(unit_id)] = probe
        return probes

    def judge_units(
        self, probes: dict[str, str], memory: list[str]
    ) -> dict[str, dict]:
        """Blind banded judging: ``{probe_id: unit_record}``.

        ``unit_record`` = ``{probability, band, votes_yes, votes_total,
        orders: {order: {yes, total, probability}}}``.
        """
        if not probes:
            return {}
        probe_ids = sorted(probes)
        self._guard(probes)
        yes = {pid: 0 for pid in probe_ids}
        total = {pid: 0 for pid in probe_ids}
        per_order: dict[str, dict[str, dict[str, int]]] = {
            order: {pid: {"yes": 0, "total": 0} for pid in probe_ids}
            for order in self.orders
        }
        for order in self.orders:
            user = build_banded_prompt(probes, memory, order=order)
            for _ in range(self.samples):
                raw = self._complete(
                    self._judge_model, system=_BANDED_SYSTEM, user=user
                )
                self.judge_call_count += 1
                self.record_usage(self._judge_model)
                lp = _snapshot_logprobs(self._judge_model)
                if lp is not None:
                    self._logprob_samples.append(lp)
                verdicts = parse_banded(raw, probe_ids)
                for pid, preserved in verdicts.items():
                    total[pid] += 1
                    per_order[order][pid]["total"] += 1
                    if preserved:
                        yes[pid] += 1
                        per_order[order][pid]["yes"] += 1
        out: dict[str, dict] = {}
        for pid in probe_ids:
            probability = yes[pid] / total[pid] if total[pid] else 0.0
            out[pid] = {
                "probability": round(probability, 6),
                "band": band_for_probability(probability),
                "votes_yes": yes[pid],
                "votes_total": total[pid],
                "orders": {
                    order: {
                        "yes": per_order[order][pid]["yes"],
                        "total": per_order[order][pid]["total"],
                        "probability": round(
                            per_order[order][pid]["yes"]
                            / per_order[order][pid]["total"],
                            6,
                        )
                        if per_order[order][pid]["total"]
                        else 0.0,
                    }
                    for order in self.orders
                },
            }
        return out

    def logprob_crosscheck(self) -> dict:
        """Coarse token-logprob cross-check — NOT the answer probability."""
        return logprob_crosscheck_from_samples(self._logprob_samples)


def aggregate_banded(units: list[dict]) -> dict:
    """Pool per-unit banded verdicts into the additive semantic aggregate.

    ``units`` entries must carry ``probability`` (the agreement fraction);
    the band is derived here so a caller cannot disagree with the banding
    function.  Note: the blindness guard runs on the PROBE block inside
    ``BandedSalienceJudge`` — the MEMORY block is observed data and may
    legitimately carry a stored Anchor's own wording.

    Returns the band distribution + the three headline numbers:

    * ``band_weighted_score`` — mean band representative (midpoint of the
      owner's band intervals): the stable publication scalar.
    * ``probability_mean`` — mean raw agreement fraction (finer, noisier).
    * ``semantic_survival_rate`` — fraction at/above the MAYBE threshold
      (0.50): the judged analogue of the mechanical macro dimension.
    """
    distribution = {band: 0 for band in BAND_ORDER}
    weights: list[float] = []
    probabilities: list[float] = []
    preserved = 0
    for unit in units:
        probability = float(unit.get("probability") or 0.0)
        band = band_for_probability(probability)
        unit["band"] = band
        distribution[band] += 1
        weights.append(band_weight(band))
        probabilities.append(probability)
        if probability >= SEMANTIC_SURVIVAL_THRESHOLD:
            preserved += 1
    n = len(units)
    return {
        "units_total": n,
        "band_distribution": distribution,
        "band_weighted_score": round(sum(weights) / n, 6) if n else None,
        "probability_mean": round(sum(probabilities) / n, 6) if n else None,
        "semantic_survival_rate": round(preserved / n, 6) if n else None,
        "same_fact_rate": round(distribution[BAND_SAME_FACT] / n, 6) if n else None,
    }


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
