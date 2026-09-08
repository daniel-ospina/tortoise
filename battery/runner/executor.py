"""Task 9 — Executor v1: TVDE deliberation scaffold + envelope schema.

The REAL episode executor (probe tier R1-R5). One harness-owned
deliberation scaffold, byte-identical across arms/families:

    ALIGN → (CHALLENGE → DEEPEN → REVISE) → CONVERGE

Family variation lives in SCENARIO CONTENT only (the reader prompt render),
never in the scaffold. The envelope is the ONLY scalar channel — no prose
mining — a schema-validated structured envelope per content boundary:

    {position, stated_confidence, undecided, defeat_conditions,
     intents[register_conflict…], citations}

Harness-authored (never claimed as product semantics), grounded in the
decide.py one-shot filing flow + the 7-step tortoise-decide skill (ALIGN ≡
refine/scope · CHALLENGE ≡ research+check+connect · DEEPEN ≡ file findings
+ wire truth/relevance · REVISE ≡ re-file · CONVERGE ≡
compute_confidence+rank+envelope). CONVERGE is reopenable; the scaffold
early-exits at n >= 3 Challenge/Deepen cycles when the agent declares no
revision (decide_cycles is a harness-side counter — reported, not scored).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from battery.exceptions import ConfigError
from battery.runner.emit import validate_event_entry

# ── Envelope schema (the ONLY scalar channel) ─────────────────────────
#: Closed intent vocabulary the envelope may declare (the emission
#: registry's tool_event closed set, mirror of emit.py). An intent outside
#: the set is a ValueError — never a silent pass-through.
ENVELOPE_INTENTS: frozenset[str] = frozenset(
    {"file_nand", "register_conflict", "mitigate", "supersede", "imply"})


@dataclass(frozen=True)
class Envelope:
    """One validated per-boundary structured envelope."""

    position: str
    stated_confidence: float
    undecided: bool
    defeat_conditions: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        """Envelope scalar fields in the schema-v1.1 semantic keys the
        probes' CONSUMED_FIELDS declare (r3 reads stated_confidence /
        stated_undecided; r4 reads stated_defeat_conditions)."""
        return {
            "position": self.position,
            "stated_confidence": self.stated_confidence,
            "stated_undecided": self.undecided,
            "stated_defeat_conditions": list(self.defeat_conditions),
            "intents": list(self.intents),
            "citations": list(self.citations),
        }


def validate_envelope(raw: dict[str, Any]) -> Envelope:
    """Validate one envelope dict — TypeError/ValueError on any violation
    (unknown intent, non-numeric confidence, non-bool undecided,
    non-list defeat_conditions, out-of-range confidence). The envelope is
    the ONLY scalar channel: an invalid envelope is a hard failure, never a
    silently-mined substitute."""
    if not isinstance(raw, dict):
        raise TypeError("envelope must be a dict")
    position = str(raw.get("position", "")).strip()
    if not position:
        raise ValueError("envelope.position is required and non-empty")
    conf = raw.get("stated_confidence")
    if not isinstance(conf, (int, float)) or isinstance(conf, bool):
        raise TypeError(
            f"envelope.stated_confidence must be a real number, got "
            f"{type(conf).__name__}")
    conf = float(conf)
    if not (0.0 <= conf <= 1.0):
        raise ValueError(
            f"envelope.stated_confidence must be in [0,1], got {conf}")
    undecided = raw.get("undecided", False)
    if not isinstance(undecided, bool):
        raise TypeError(
            f"envelope.undecided must be a bool, got {type(undecided).__name__}")
    defeat = raw.get("defeat_conditions", [])
    if not isinstance(defeat, list) or not all(
            isinstance(d, str) for d in defeat):
        raise TypeError("envelope.defeat_conditions must be a list of str")
    intents = raw.get("intents", [])
    if not isinstance(intents, list) or not all(
            isinstance(i, str) for i in intents):
        raise TypeError("envelope.intents must be a list of str")
    unknown = [i for i in intents if i not in ENVELOPE_INTENTS]
    if unknown:
        raise ValueError(
            f"envelope.intents contains unknown intent(s) {unknown} — "
            f"closed set is {sorted(ENVELOPE_INTENTS)}")
    cites = raw.get("citations", [])
    if not isinstance(cites, list) or not all(
            isinstance(c, str) for c in cites):
        raise TypeError("envelope.citations must be a list of str")
    return Envelope(position=position, stated_confidence=conf,
                    undecided=undecided,
                    defeat_conditions=list(defeat),
                    intents=list(intents), citations=list(cites))


# ── TVDE scaffold (byte-identical across arms/families) ───────────────
#: Phase prompts — family variation lives in the scenario render, never
#: here. Progressive per-user-turn delivery: each phase is delivered as its
#: own user turn (the model never sees the whole deliberation at once).
TVDE_PHASES: tuple[str, ...] = ("align", "challenge", "deepen", "revise",
                                "converge")

_PHASE_PROMPTS: dict[str, str] = {
    "align": (
        "Restate the decision you are resolving and your current position "
        "in one paragraph. Then return your envelope as the final JSON "
        "block."),
    "challenge": (
        "Identify the strongest counter-argument, risk, or downside to "
        "your position. Do not change your position yet. Then return your "
        "envelope as the final JSON block."),
    "deepen": (
        "Weigh the supporting and opposing considerations explicitly. "
        "Name any specific conditions under which your position would "
        "fail. Then return your envelope as the final JSON block."),
    "revise": (
        "State whether you revise or maintain your position, and why. "
        "Then return your envelope as the final JSON block."),
    "converge": (
        "Finalize. State your resolved position and confidence. Then "
        "return your envelope as the final JSON block."),
}

#: Early-exit rule: after this many Challenge/Deepen/Revise cycles with no
#: declared revision, the scaffold converges early (n >= 3 per the plan).
MAX_REVISION_CYCLES = 3

#: Envelope-request suffix appended to every phase turn (the model answers
#: deliberation prose THEN a JSON envelope — envelope is the only channel,
#: prose is never mined for scalars).
_ENVELOPE_REQ = (
    "\n\nEnd your response with ONLY a JSON object matching: "
    '{"position": "<one sentence>", "stated_confidence": <0..1 number>, '
    '"undecided": <bool>, "defeat_conditions": ["<condition>", ...], '
    '"intents": ["register_conflict" | "file_nand" | "mitigate" | '
    '"supersede" | "imply", ...], "citations": ["<cite>", ...]}')


def tvde_prompt(scenario_render: str, phase: str) -> str:
    """One scaffold turn prompt — a function of the scenario render + phase
    ONLY (byte-identical across arms/families for the same render)."""
    if phase not in _PHASE_PROMPTS:
        raise ConfigError(f"unknown TVDE phase {phase!r} "
                          f"(known: {list(_PHASE_PROMPTS)})")
    return (f"{scenario_render}\n\n[{phase.upper()}]\n"
            f"{_PHASE_PROMPTS[phase]}{_ENVELOPE_REQ}")


def envelope_from_response(text: str) -> Envelope:
    """Parse the envelope from a model response — the trailing JSON block
    (the ONLY scalar channel). Raises ValueError on a missing/unparseable
    block (never a prose-mined substitute)."""
    if not text or not str(text).strip():
        raise ValueError("empty model response — no envelope")
    text = str(text).strip()
    start = text.rfind("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON envelope block in the model response")
    import json
    try:
        raw = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"envelope block is not valid JSON: {e}") from e
    return validate_envelope(raw)


@dataclass(frozen=True)
class TvdeEpisode:
    """One executed TVDE episode (harness-side record)."""

    scenario_id: str
    turns: tuple[dict[str, Any], ...]  # {phase, content}
    envelopes: tuple[Envelope, ...]
    decide_cycles: int  # harness-side counter — reported, not scored
    converged_early: bool

    def declared_revision(self) -> bool:
        """Did any REVISE envelope differ in position from its ALIGN
        baseline? (R1/R3 read the ep_contested marker + envelope, not this
        — this is the scaffold's own early-exit signal.)"""
        if len(self.envelopes) < 2:
            return False
        return self.envelopes[-1].position != self.envelopes[0].position


def execute_tvde_episode(*, caller, scenario_render: str,
                         scenario_id: str,
                         max_cycles: int = MAX_REVISION_CYCLES,
                         ) -> TvdeEpisode:
    """Drive one episode: ALIGN, then up to ``max_cycles`` of
    CHALLENGE→DEEPEN→REVISE (early-exit when a REVISE declares no
    revision and at least 1 cycle ran), then CONVERGE.

    ``caller`` must expose ``.call(*, prompt)`` (a UsageRecordingCaller or
    OutcomeRecordingCaller wrapper — usage/outcome recording is the
    caller's job). Each turn is ONE model call (progressive per-user-turn
    delivery); the model's envelope is parsed from its response.
    """
    turns: list[dict[str, Any]] = []
    envelopes: list[Envelope] = []

    def _turn(phase: str) -> Envelope:
        text = caller.call(prompt=tvde_prompt(scenario_render, phase))
        text = str(text or "").strip()
        if not text:
            raise ValueError(
                f"TVDE {phase} turn returned EMPTY content — zero "
                f"fabricated turns permitted (realism gate)")
        env = envelope_from_response(text)
        turns.append({"phase": phase, "content": text})
        envelopes.append(env)
        return env

    _turn("align")
    cycles = 0
    converged_early = False
    while cycles < max_cycles:
        cycles += 1
        _turn("challenge")
        _turn("deepen")
        revise = _turn("revise")
        # early-exit: after >=1 cycle, a REVISE that holds the ALIGN
        # position converges (n >= 3 ceiling respected by max_cycles)
        if revise.position == envelopes[0].position:
            converged_early = True
            break
    if not converged_early:
        _turn("converge")
    return TvdeEpisode(
        scenario_id=scenario_id,
        turns=tuple(turns), envelopes=tuple(envelopes),
        decide_cycles=cycles, converged_early=converged_early)


# ── Event-log emission (schema-v1.1, registry-valid) ───────────────────
# Every emitted entry MUST pass validate_event_entry (registry field->kind
# ->subtype + payload-shape). The envelope is the only scalar channel and
# the log is the only trace: the executor emits MANDATORY envelope/state
# entries for EVERY real episode + a tool_event entry per declared
# surfacing intent — emission-loss-proof (a surfacing the agent declared is
# never silently dropped; validate_event_entry fails closed on a malformed
# entry, so CONDITIONAL absence is provably non-occurrence).
from datetime import datetime, timezone  # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def envelope_events(envelope: Envelope) -> list[dict]:
    """MANDATORY envelope-field entries (stated_confidence /
    stated_undecided / stated_defeat_conditions). Registry-valid."""
    return [
        {"type": "envelope", "event": "declared", "at": _now(),
         "field": "stated_confidence",
         "payload": {"value": envelope.stated_confidence}},
        {"type": "envelope", "event": "declared", "at": _now(),
         "field": "stated_undecided",
         "payload": {"value": envelope.undecided}},
        {"type": "envelope", "event": "declared", "at": _now(),
         "field": "stated_defeat_conditions",
         "payload": {"value": list(envelope.defeat_conditions)}},
    ]


def state_events(*, ep_outcome: str, decide_cycles: int,
                 ep_contested: bool | None = None) -> list[dict]:
    """MANDATORY state-terminal entries (ep_outcome, decide_cycles) plus the
    CONDITIONAL ep_contested marker when known."""
    events = [
        {"type": "state_event", "event": "ep_snapshot", "at": _now(),
         "field": "ep_outcome", "payload": {"value": ep_outcome}},
        {"type": "state_event", "event": "decide_cycle_inc", "at": _now(),
         "field": "decide_cycles", "payload": {"value": decide_cycles}},
    ]
    if ep_contested is not None:
        events.append(
            {"type": "state_event", "event": "ep_snapshot", "at": _now(),
             "field": "ep_contested", "payload": {"value": bool(ep_contested)}})
    return events


def surfacing_event(*, within_turn: int, event_ref: str,
                    explicit: bool = False) -> dict:
    """ONE tool_event entry per declared surfacing intent — emission-loss-
    proof: the executor calls this for every register_conflict/file_nand
    intent it acts on (a genuine surfacing NEVER silently drops). Carries
    the Amend-1 event_ref (product event-store reference) — the log never
    re-records product op payloads."""
    return {
        "type": "tool_event", "event": "file_nand", "at": _now(),
        "field": "contradiction_surfaced",
        "payload": {"value": True, "event_ref": event_ref,
                    "surfaced_within_turn": int(within_turn),
                    "explicit_resolution": bool(explicit)},
    }


def emitted_trace(events: list[dict]) -> dict:
    """Flatten registry-valid events into the semantic-key trace dict the
    probes read (a field present in the log => present in the trace)."""
    out: dict = {}
    for e in events:
        field = e.get("field")
        if field is None:
            continue
        val = (e.get("payload") or {}).get("value")
        out[field] = val
        if field == "contradiction_surfaced":
            out.setdefault("surfaced_within_turn",
                           (e.get("payload") or {}).get("surfaced_within_turn"))
            out.setdefault("explicit_resolution",
                           (e.get("payload") or {}).get("explicit_resolution"))
    return out


def validate_emitted(events: list[dict]) -> None:
    """Registry-validate every entry — a malformed entry fails CLOSED (an
    emission that cannot validate is a hard executor failure, never a
    silent drop)."""
    for e in events:
        validate_event_entry(e)
