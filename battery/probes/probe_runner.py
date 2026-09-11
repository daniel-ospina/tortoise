"""#2292-owned real-model probe (Task 3) — deliberation driver + usage
capture + measured-token tables + budget sub-cap.

The validation run (Task 4) needs REAL deliberation text and the budget
needs MEASURED tokens — both from ONE minimal probe that runs the
candidate-pinned model under test on a small authored scenario set BEFORE
the #2284 executor exists. The probe is #2292-owned and standalone (never
depends on run.py's real-executor seam, which refuses real mode until Task
9's emission seam is active).

It produces:
(a) real deliberation text per scenario x arm for the validation anchors,
(b) measured per-phase tokens (model under test per phase + the judge-leg
    accounting line — judge spend is metered, never folded into the
    model-under-test row, decision (c)),
(c) a persisted evidence bundle for Task 4's validation run (rubric id ->
    per-anchor evidence renders, tool-stripped + lint-passed).

Spend discipline: probe spend is capped by budget.yaml ``probe_cap_usd``
(default $3.00 — pre-authorized inside the existing $50 dollar cap); the
probe refuses to start when OPENROUTER_API_KEY is absent (fail-closed) or
the sub-cap cannot fund a single call; never silently re-run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from battery.config.prices import RATES_PER_1M_USD
from battery.exceptions import ConfigError
from battery.judge.rubric import lint_evidence_neutral

#: Deliberation scaffold phases (4-turn envelope): position -> challenge ->
#: deepen -> revise. Every turn is a REAL model call; transcripts record the
#: recorded model-call outcome (zero fabricated turns).
_DELIB_TURNS = (
    ("position",
     "State your position on the scenario question and the single strongest "
     "consideration that supports it."),
    ("challenge",
     "Consider what could be wrong with that position: name the strongest "
     "counter-argument, risk, or downside you can identify."),
    ("deepen",
     "Weigh the supporting and opposing considerations against each other "
     "explicitly. Which side has the better case and why?"),
    ("revise",
     "Given the counter-evidence you considered, state whether you revise or "
     "maintain your earlier position, and why."),
)

#: Sub-cap floor: a probe cap below this cannot fund a single real model
#: call (nominal per-call cost >> floor) — refuse at pre-flight.
_MIN_FUNDABLE_CAP_USD = 0.01

#: Banned-token scrub for evidence renders (tool verbs / arm ids / graph +
#: edge vocabulary / numeric counts — the neutrality lint's token set,
#: mirrored here as a pre-lint STRIP so a raw model turn that mentions a
#: product verb still yields a lint-passing render where scrubbing removes
#: the leak; the lint remains the final mechanical guard).
_SCRUB = re.compile(
    r"\b(?:create_point|create_operator|file_nand|register_conflict|"
    r"supersede|mitigate|mitigation|mitigat\w*)\b|"
    r"\b(?:a0|a1|a2|a2b|a3|a4|mock)\b|\b(?:graph|edge|edges)\b|"
    r"\d+\s*(?:edges?|calls?|turns?)\b",
    flags=re.IGNORECASE,
)


#: Pinned deepseek-v4-flash (OpenRouter) per-1M-token rates — the probe's
#: spend meter rows (matches the judge reserve table's deepseek row so ONE
#: price source governs both spend legs; review #2575 B-P1).
#: Imported from the ONE declared basis (#2874) — a local copy is how the
#: previous constant drifted away from every real price.
_PROBE_RATES_PER_1M_USD: tuple[float, float] = RATES_PER_1M_USD


def _call_cost_usd(prompt_tok: int, completion_tok: int) -> float:
    p_in, p_out = _PROBE_RATES_PER_1M_USD
    return (float(prompt_tok) * p_in
            + float(completion_tok) * p_out) / 1_000_000.0


@dataclass(frozen=True)
class ProbeBudget:
    """Probe spend sub-cap (budget.yaml ``probe_cap_usd``)."""

    cap_usd: float = 3.0

    def refuse_reason(self) -> str | None:
        if self.cap_usd <= 0.0:
            return "probe sub-cap must be positive"
        if self.cap_usd < _MIN_FUNDABLE_CAP_USD:
            return (f"probe sub-cap ${self.cap_usd:.4f} cannot fund a single "
                    f"real model call (floor ${_MIN_FUNDABLE_CAP_USD:.2f})")
        return None


@dataclass
class _PhaseAccumulator:
    """Per-call usage capture into per-phase token rows (the model_adapters
    contract: last_prompt_tokens / last_completion_tokens per call)."""

    prompt_tokens: list[int] = field(default_factory=list)
    completion_tokens: list[int] = field(default_factory=list)
    calls: int = 0

    def record(self, prompt_tok: int, completion_tok: int) -> None:
        self.prompt_tokens.append(int(prompt_tok))
        self.completion_tokens.append(int(completion_tok))
        self.calls += 1

    def totals(self) -> dict:
        return {"prompt_tokens": sum(self.prompt_tokens),
                "completion_tokens": sum(self.completion_tokens),
                "calls": self.calls}


def _p95(sorted_vals: list[float]) -> float:
    """95th percentile (nearest-rank over the SORTED data; the 0.95*(n-1)
    index rule the measured-token tables use)."""
    if not sorted_vals:
        return 0.0
    vals = sorted(sorted_vals)
    return vals[int(0.95 * (len(vals) - 1))]


def token_tables(rows: dict[str, list[float]]) -> dict[str, dict]:
    """Per-phase 95th-pct + mean tables from raw token rows."""
    out: dict[str, dict] = {}
    for phase, vals in rows.items():
        vals = [float(v) for v in vals]
        out[phase] = {
            "p95": _p95(vals),
            "mean": (sum(vals) / len(vals)) if vals else 0.0,
            "n": len(vals),
        }
    return out


def _strip_tools(text: str) -> str:
    """Tool-strip an evidence render (remove tool verbs / arm ids / graph +
    edge vocabulary / numeric count-talk) so the render is arm-neutral
    before the lint. Deterministic; preserves deliberation content."""
    scrubbed = _SCRUB.sub("", text)
    scrubbed = re.sub(r"\s{2,}", " ", scrubbed).strip()
    return scrubbed


def _scenario_context(config: str | Path, sid: str) -> dict:
    """Real scenario render when the id resolves; a synthetic single-turn
    stub otherwise (hermetic probe tests feed ids outside the corpus)."""
    try:
        from battery.config.corpus import load_corpus
        scenarios = load_corpus(Path(config) / "corpus.yaml")
        by_id = {s.id: s for s in scenarios}
        sc = by_id.get(sid)
        if sc is not None:
            return {"id": sid, "render": _render(sc)}
    except Exception:  # noqa: BLE001, RUF100 — fall through to synthetic
        pass
    return {"id": sid, "render": f"Scenario {sid}: resolve the situation described."}


def _render(sc) -> str:
    from battery.config.corpus_loader import render_reader_prompt
    try:
        return render_reader_prompt(sc.to_render_dict())
    except Exception:  # noqa: BLE001, RUF100
        return f"Scenario {sc.id}: resolve the situation described."


def _make_caller():
    """Real caller from the model_adapters registry (decision (a) pin:
    deepseek-flash -> OpenRouterModel('deepseek/deepseek-v4-flash',
    max_tokens=None, temperature=0.0)) — wrapped into the probe .call
    contract (the registry's complete(*, system, user) signature)."""
    import os
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise ConfigError(
            "real probe refuses to start: OPENROUTER_API_KEY absent "
            "(fail-closed — the probe is spend-gated, never a silent mock)")
    from tortoise import model_adapters
    try:
        real = model_adapters.MODELS["deepseek-flash"]()
    except KeyError as e:  # pragma: no cover — registry drift guard
        raise ConfigError(
            "model_adapters.MODELS['deepseek-flash'] missing") from e
    return _AdapterCaller(real)


class _AdapterCaller:
    """Adapter-caller wrapper: the model_adapters registry contract is
    ``complete(*, system, user)`` with per-call usage on last_* fields;
    the probe's .call(prompt=) contract delegates and mirrors the usage
    capture seam."""

    def __init__(self, real):
        self._real = real

    @property
    def model_id(self) -> str:
        return getattr(self._real, "id", "deepseek/deepseek-v4-flash")

    @property
    def temperature(self) -> float:
        return getattr(self._real, "temperature", 0.0)

    def call(self, *, prompt: str) -> str:
        text = self._real.complete(system="", user=prompt)
        self.last_cost_usd: float | None = None
        self.last_prompt_tokens = getattr(
            self._real, "last_prompt_tokens", 0)
        self.last_completion_tokens = getattr(
            self._real, "last_completion_tokens", 0)
        # #2906: forward the provider's authoritative charge too. Without
        # this the probe's HARD STOP and its persisted ``spend`` fall back to
        # the declared price basis, which is wrong in both directions (the
        # upstream serving the call is not pinned).
        self.last_cost_usd = getattr(self._real, "last_cost_usd", None)
        return text


def _spend_cost_basis(provider_calls: int, estimated_calls: int) -> str:
    """#2906 — how a persisted spend figure was priced, derived from the calls
    that actually priced it, never asserted.

    A run that priced NOTHING reads ``estimated``: it must not claim
    provider-reported provenance it does not have (review #2915 P2). Same rule
    as ``model_calls.aggregate_cost_basis`` and the judge meter.
    """
    if not (provider_calls or estimated_calls):
        return "estimated"
    if not estimated_calls:
        return "provider_reported"
    return "mixed" if provider_calls else "estimated"


def run_probe(*, config: str | Path, arms: list[str],
              scenario_ids: list[str],
              caller=None,
              out_dir: str | Path | None = None,
              budget: ProbeBudget | None = None,
              seed: int = 7) -> dict:
    """Drive the pinned model over scenarios x arms; accumulate usage per
    phase; write the manifest + token tables + validation bundle.

    ``caller`` is injectable (hermetic tests script usage capture); when
    None the real registry caller is built (fail-closed on absent key).
    Raises ValueError on fabricated/empty turns; ConfigError on budget
    refusal or an unpinnable model.
    """
    budget = budget or ProbeBudget()
    reason = budget.refuse_reason()
    if reason:
        raise ConfigError(f"probe sub-cap refusal: {reason}")
    cfg_dir = Path(config)
    out = Path(out_dir) if out_dir else None
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)

    if caller is None:
        caller = _make_caller()
        # pin-coupling guard (review #2575 B-P2): the real probe measures
        # the decision-(a) pin; a sibling re-lock of arms.yaml to another
        # measured model must NOT leave the probe silently measuring
        # deepseek (token tables re-locking expected_tokens_per_episode
        # would be misattributed). Resolve each requested arm's pin and
        # refuse on caller/model mismatch.
        from battery.config.arms import load_arms, resolve_pinned_model
        try:
            arm_map = load_arms(Path(config) / "arms.yaml")
        except Exception:  # noqa: BLE001, RUF100 — guard is best-effort
            arm_map = {}
        for arm in arms:
            ac = arm_map.get(arm)
            if ac is None or arm == "mock" or not ac.model_pin:
                continue
            resolved = resolve_pinned_model(ac.model_pin)
            if getattr(resolved, "id", "") != caller.model_id:
                raise ConfigError(
                    f"probe pin coupling: arm {arm!r} pin "
                    f"{ac.model_pin!r} resolves to "
                    f"{getattr(resolved, 'id', '?')!r} but the probe caller "
                    f"measures {caller.model_id!r} — the probe would "
                    f"misattribute token tables; refuse")

    ctxs = [_scenario_context(cfg_dir, sid) for sid in scenario_ids]
    phases = {
        "deliberation": _PhaseAccumulator(),
        "envelope": _PhaseAccumulator(),
        "judge": _PhaseAccumulator(),
    }
    #: cumulative probe spend in USD (mid-run HARD STOP against cap)
    spent: float = 0.0
    #: #2906 — how many metered calls were priced from the provider's own
    #: reported charge vs the declared fallback basis. Persisted so a spend
    #: figure never implies provider-reported accuracy it does not have.
    metered_provider: int = 0
    metered_estimated: int = 0
    transcripts: list[dict] = []
    evidence_by_rubric: dict[str, list[dict]] = {}
    #: per-EPISODE deliberation token totals (the measured re-lock unit for
    #: arms.yaml expected_tokens_per_episode — per-CALL rows would feed a
    #: 4x-wrong basis into Task 6's p95 formula).
    per_episode_deliberation: list[int] = []
    model_block = {
        "model_id": getattr(caller, "model_id", "deepseek/deepseek-v4-flash"),
        "provider": "openrouter",
        "temperature": getattr(caller, "temperature", 0.0),
    }

    for sid, ctx in zip(scenario_ids, ctxs, strict=True):
        for arm in arms:
            episode: dict = {
                "scenario": sid, "arm": arm, "seed": seed, "turns": []}
            for phase_name, turn_text in _DELIB_TURNS:
                prompt = (f"{ctx['render']}\n\n[deliberation turn — "
                          f"{phase_name}]\n{turn_text}")
                try:
                    text = caller.call(prompt=prompt)
                except Exception as e:  # noqa: BLE001, RUF100
                    raise ConfigError(
                        f"probe model call failed (scenario {sid}, arm {arm}, "
                        f"turn {phase_name}): {e}") from e
                # spend meter (review #2575 B-P1): the probe is the only
                # path this slice runs a live model over an unbounded
                # scenarios x arms x 4 matrix — the cap HARD-STOPS mid-run,
                # not just at pre-flight. Cost rows from the per-call usage
                # capture at the pinned rates (overshoot <= one call).
                ptok = getattr(caller, "last_prompt_tokens", 0)
                ctok = getattr(caller, "last_completion_tokens", 0)
                # #2906: prefer the provider-reported charge (`is None`, never
                # truthiness — a genuine 0.0 is a value); the declared basis is
                # the fallback and the label is recorded so the persisted
                # spend never masquerades as provider-reported.
                reported_cost = getattr(caller, "last_cost_usd", None)
                if reported_cost is None:
                    spent += _call_cost_usd(int(ptok), int(ctok))
                    metered_estimated += 1
                else:
                    spent += float(reported_cost)
                    metered_provider += 1
                if spent > budget.cap_usd:
                    raise ConfigError(
                        f"probe sub-cap ${budget.cap_usd:.2f} exceeded after "
                        f"{phases['deliberation'].calls + 1} deliberation "
                        f"calls (${spent:.4f}) — HARD STOP (overshoot <= one "
                        f"call)")
                if not text or not str(text).strip():
                    raise ValueError(
                        f"probe produced an EMPTY deliberation turn "
                        f"(scenario {sid}, arm {arm}, turn {phase_name}) — "
                        f"zero fabricated turns permitted")
                text = str(text).strip()
                phases["deliberation"].record(ptok, ctok)
                episode["turns"].append(
                    {"phase": phase_name, "content": text})
            # Envelope scalars: assembled from the deliberation text
            # (positional heuristics over the real turns — no fabricated
            # field). The revise turn decides direction + revision.
            last = episode["turns"][-1]["content"].lower()
            revise_words = ("revise", "revised", "update", "change my",
                            "i now", "rather than")
            explicit_hold = any(w in last for w in
                                ("maintain my position", "no revision",
                                 "stand by my"))
            revised = any(w in last for w in revise_words) \
                and not explicit_hold
            envelope = {
                "position_clear": bool(episode["turns"][0]["content"]),
                "revised": bool(revised),
            }
            episode["envelope"] = envelope
            transcripts.append(episode)
            per_episode_deliberation.append(
                phases["deliberation"].prompt_tokens[-4:]
                and sum(p + c for p, c in zip(
                    phases["deliberation"].prompt_tokens[-4:],
                    phases["deliberation"].completion_tokens[-4:],
                    strict=True)))

            # Evidence render (arm-neutral, tool-stripped): the revise-turn
            # content + the risk/deepen turns, scrubbed + linted. Feeding
            # the r2-coverage rubric anchors (deliberation content).
            raw = " ".join(t["content"] for t in episode["turns"])
            render = _strip_tools(raw)
            try:
                lint_evidence_neutral(render)
            except ValueError as e:
                # The strip should have removed the leaks; a survivor is a
                # harness bug — surface loudly, never weaken the lint.
                raise ValueError(f"probe evidence render not arm-neutral "
                                 f"({sid}/{arm}): {e}") from e
            evidence_by_rubric.setdefault("r2-coverage", []).append({
                "scenario": sid, "arm": arm, "render": render[:2000]})

    if out is not None:
        rows = {p: a.totals() for p, a in phases.items()}
        per_phase_lists = {
            # per-EPISODE deliberation totals (the re-lock unit)
            "deliberation": per_episode_deliberation,
            "envelope": [],
            "judge": [],
        }
        tables = token_tables(per_phase_lists)
        tables["judge"]["calls"] = phases["judge"].calls
        (out / "probe_tokens.json").write_text(
            json.dumps(tables, indent=2), encoding="utf-8")
        (out / "probe_manifest.json").write_text(json.dumps({
            "scenarios": scenario_ids, "arms": arms, "seed": seed,
            "model": model_block, "spend": {
                "sub_cap_usd": budget.cap_usd,
                "cost_basis": _spend_cost_basis(metered_provider,
                                                metered_estimated),
            },
            "usage": rows,
        }, indent=2), encoding="utf-8")
        (out / "probe_validation_bundle.json").write_text(json.dumps({
            "seed": seed, "model": model_block,
            "rubrics": evidence_by_rubric,
        }, indent=2), encoding="utf-8")
        tx = out / "transcripts"
        tx.mkdir(parents=True, exist_ok=True)
        for i, ep in enumerate(transcripts):
            (tx / f"{ep['scenario']}__{ep['arm']}__{i:02d}.json").write_text(
                json.dumps(ep, indent=2), encoding="utf-8")

    return {"episodes": len(transcripts),
            "scenarios": scenario_ids, "arms": arms}
