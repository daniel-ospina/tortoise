"""Task 9 — Executor v1: TVDE deliberation scaffold + envelope schema.

The REAL episode executor (probe tier R1-R5). One harness-owned
deliberation scaffold, byte-identical across arms/families:

    ALIGN → (CHALLENGE → DEEPEN → REVISE) → CONVERGE

Family variation lives in SCENARIO CONTENT only (the reader prompt render),
never in the scaffold. The envelope is the ONLY scalar channel — no prose
mining — a schema-validated structured envelope per content boundary:

    {position | no_position, stated_confidence, undecided,
     defeat_conditions, intents[register_conflict…], citations}

#2702: ``no_position`` is the explicit NO-OP decision class — a benign
control surface (nothing to surface) or a later phase with no new stance
is expressible WITHOUT fabricating a position sentence, and an absent
``position`` is never silently reinterpreted as that class.

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

#: The SURFACING subset of the closed intent vocabulary — the verbs that
#: claim an existing conflict (and file the same NAND op; #2702 single
#: source for the R1 FP-control verdict and the executor's decide-write
#: routing).
SURFACING_INTENTS: frozenset[str] = frozenset(
    {"register_conflict", "file_nand"})


@dataclass(frozen=True)
class Envelope:
    """One validated per-boundary structured envelope.

    ``no_position`` is the explicit NO-OP DECISION CLASS (#2702): the arm
    declares that this turn carries no substantive position — a benign
    control with nothing to surface, or a later scaffold phase with no new
    stance. It is a typed declaration, never a prose carrier (``position``
    must be empty when it is set), and never a substitute for
    ``undecided`` (which stays the arm's own epistemic flag on a contested
    question).
    """

    position: str
    stated_confidence: float
    undecided: bool
    defeat_conditions: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    no_position: bool = False

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
            "no_position": self.no_position,
        }


#: Prompt-template tokens that must never be accepted as a position (an
#: echo of the envelope request itself, not an answer).
_TEMPLATE_POSITION_ECHOES = frozenset({
    "<one sentence>", "<one-sentence>", "<position>", "one sentence",
    "<one sentence position>",
})

#: Decision classes the envelope admits (#2702): a substantive ``position``
#: or an explicit ``no_position`` no-op. Closed set — the classes exist so a
#: benign control / non-decision phase is EXPRESSIBLE without fabricating a
#: position sentence; they are not scalars and never feed a probe.
#:
#: SINGLE SOURCE (review P2-3): ``validate_envelope`` selects the turn's
#: class by ENUMERATING this tuple through ``_DECISION_CLASS_WITNESS`` (a
#: declared class with no witness fails closed, never silently dropped) and
#: ``_validate_decision_class`` dispatches on the selected NAME (a member
#: without a branch fails closed too). The list can no longer drift from the
#: validator by being dead metadata.
ENVELOPE_DECISION_CLASSES: tuple[str, ...] = ("position", "no_position")


def _position_declared(raw: dict[str, Any], position: str) -> bool:
    """Witness for the ``position`` decision class: a non-empty position IS
    the declaration."""
    return bool(position)


def _no_position_declared(raw: dict[str, Any], position: str) -> bool:
    """Witness for the ``no_position`` decision class: the explicit typed
    ``no_position: true`` declaration (never a truthy value — the bool type
    check in ``validate_envelope`` runs first)."""
    return raw.get("no_position", False) is True


#: Per-class declaration witness, keyed BY ``ENVELOPE_DECISION_CLASSES``.
_DECISION_CLASS_WITNESS: dict[str, Any] = {
    "position": _position_declared,
    "no_position": _no_position_declared,
}


def _validate_decision_class(decision_class: str, raw: dict[str, Any],
                             position: str) -> None:
    """Enforce the SELECTED decision class's own rules (the no-op class
    carries no prose and surfaces nothing; the position class rejects a
    prompt-template echo). Exactly-one-class is enforced by the caller
    before this runs."""
    if decision_class == "position":
        # Review #2717 P2: a verbatim echo of the prompt's template token is
        # a harness artifact, never a position — reject it so the metric
        # can never score the envelope request's own placeholder.
        if position.lower() in _TEMPLATE_POSITION_ECHOES:
            raise ValueError(
                f"envelope.position is the prompt template echo "
                f"{position!r}, not a position")
        return
    if decision_class == "no_position":
        if position:
            raise ValueError(
                "envelope.no_position is true but a substantive position "
                "was also declared — the no-op class carries no prose "
                "(exactly one decision class per turn)")
        if raw.get("intents"):
            raise ValueError(
                "envelope.no_position is true but surfacing intents were "
                "also declared — a no-op turn surfaces nothing")
        return
    raise ConfigError(
        f"envelope decision class {decision_class!r} has no validator — "
        f"ENVELOPE_DECISION_CLASSES ({list(ENVELOPE_DECISION_CLASSES)}) "
        f"drifted from the validator")


def validate_envelope(raw: dict[str, Any]) -> Envelope:
    """Validate one envelope dict — TypeError/ValueError on any violation
    (unknown intent, non-numeric confidence, non-bool undecided,
    non-list defeat_conditions, out-of-range confidence). The envelope is
    the ONLY scalar channel: an invalid envelope is a hard failure, never a
    silently-mined substitute."""
    if not isinstance(raw, dict):
        raise TypeError("envelope must be a dict")
    # #2702 no-op decision class. Read FIRST: it relaxes the position
    # requirement, but only as an EXPLICIT typed declaration — an omitted
    # or empty position with no class is still a hard failure (a missing
    # field must never be silently read as a declaration the arm did not
    # make).
    no_position = raw.get("no_position", False)
    if not isinstance(no_position, bool):
        raise TypeError(
            f"envelope.no_position must be a bool, got "
            f"{type(no_position).__name__}")
    position = str(raw.get("position", "")).strip()
    # The turn's decision class is SELECTED by enumerating the closed set
    # (ENVELOPE_DECISION_CLASSES is the single source), and exactly one class
    # must be declared: a substantive position and the explicit no-op are
    # mutually exclusive, and neither is a silent default. A declared class
    # with no witness is a hard failure — the list cannot drift.
    missing_witness = [c for c in ENVELOPE_DECISION_CLASSES
                       if c not in _DECISION_CLASS_WITNESS]
    if missing_witness:
        raise ConfigError(
            f"ENVELOPE_DECISION_CLASSES declares {missing_witness} without "
            f"a witness — the decision-class list and the validator drifted")
    declared = [c for c in ENVELOPE_DECISION_CLASSES
                if _DECISION_CLASS_WITNESS[c](raw, position)]
    if len(declared) != 1:
        raise ValueError(
            f"envelope must declare exactly one decision class from "
            f"{list(ENVELOPE_DECISION_CLASSES)} — a non-empty position or "
            f"an explicit no_position no-op; declared {declared or 'none'}")
    _validate_decision_class(declared[0], raw, position)
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
                    intents=list(intents), citations=list(cites),
                    no_position=no_position)


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

#: Corrective suffix appended to the FULL phase prompt on a schema failure.
#: The repair re-asks the SAME question (the phase render + phase
#: instruction) with the schema error surfaced — a bare "emit the JSON"
#: re-prompt loses the question entirely and yields placeholder answers
#: ("I am responding correctly.") that are schema-valid but semantically
#: fabricated. The envelope stays the model's own answer to the real
#: question: never mined from prose, never invented by the harness.
_REPAIR_SCHEMA = (
    '. Answer the SAME question again and end with ONLY the envelope JSON '
    'object ({"position": "<one sentence>", "stated_confidence": <0..1 '
    'number>, "undecided": <bool>, "no_position": <true ONLY if this turn '
    'has no substantive position — then omit "position">, '
    '"defeat_conditions": [...], "intents": [...], "citations": [...]}).')

#: Envelope-request suffix appended to every phase turn (the model answers
#: deliberation prose THEN a JSON envelope — envelope is the only channel,
#: prose is never mined for scalars).
_ENVELOPE_REQ = (
    "\n\nEnd your response with ONLY a JSON object matching: "
    '{"position": "<one sentence>", "stated_confidence": <0..1 number>, '
    '"undecided": <bool>, "no_position": <true ONLY if this turn declares '
    'NO substantive position — a benign surface with nothing to surface, '
    'or no new stance this phase; then OMIT "position" entirely>, '
    '"defeat_conditions": ["<condition>", ...], '
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
    block (never a prose-mined substitute).

    Extraction (#1416 real-run): the LAST COMPLETE top-level JSON object in
    the response wins (raw_decode walk). The model is instructed to END
    with the envelope; trailing prose that itself contains braces ("I chose
    option {2}…") must never make the naive last-brace slice straddle
    garbage ("Extra data" — 36% of the E2E-1.1 real leg excluded on it).
    Envelope honesty is untouched: the object still must validate (shape +
    typed scalars); prose is never mined for any scalar.
    """
    if not text or not str(text).strip():
        raise ValueError("empty model response — no envelope")
    text = str(text).strip()
    import json
    decoder = json.JSONDecoder()
    best: tuple[dict, int] | None = None
    i = text.find("{")
    while i != -1:
        try:
            obj, _end = decoder.raw_decode(text[i:])
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            best = (obj, i)
        i = text.find("{", i + 1)
    if best is None:
        raise ValueError("no JSON envelope block in the model response")
    return validate_envelope(best[0])


@dataclass(frozen=True)
class TvdeEpisode:
    """One executed TVDE episode (harness-side record)."""

    scenario_id: str
    turns: tuple[dict[str, Any], ...]  # {phase, content}
    envelopes: tuple[Envelope, ...]
    decide_cycles: int  # harness-side counter — reported, not scored
    converged_early: bool
    turn_calls: tuple[int, ...] = ()  # #1416: caller calls per turn (1 +
    # repairs) so run.py token attribution stays aligned with caller.rows

    def declared_revision(self) -> bool:
        """Did the LAST envelope differ in its decision class/position from
        the ALIGN baseline? (R1/R3 read the ep_contested marker + envelope,
        not this — this is the scaffold's own early-exit signal.) #2702:
        the comparison is on ``(no_position, position)`` so a no-op turn
        never compares equal to a substantive stance by accident."""
        if len(self.envelopes) < 2:
            return False
        return ((self.envelopes[-1].no_position,
                 self.envelopes[-1].position)
                != (self.envelopes[0].no_position,
                    self.envelopes[0].position))


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
    calls_per_turn: list[int] = []
    _caller_rows = getattr(caller, "rows", None)

    def _turn(phase: str) -> Envelope:
        # Per-turn repair budget (review P1): each turn may make ONE repair
        # attempt of its own — a later non-conforming turn in the same
        # episode is not silently starved by an earlier turn's repair, and
        # the trace's "repaired" flag reflects ONLY this turn.
        _repaired = False
        _repair_text = ""
        _before = len(_caller_rows) if _caller_rows is not None else 0
        text = caller.call(prompt=tvde_prompt(scenario_render, phase))
        text = str(text or "").strip()
        if not text:
            raise ValueError(
                f"TVDE {phase} turn returned EMPTY content — zero "
                f"fabricated turns permitted (realism gate)")
        try:
            env = envelope_from_response(text)
        except (ValueError, TypeError) as first:
            # #1416 corrective-repair loop: a schema violation (malformed
            # envelope, empty position, null confidence — TypeError or
            # ValueError alike) is surfaced back to the model, as it would
            # be in production, with a bounded repair budget. The repair
            # re-asks the SAME question with the error appended: the
            # accepted envelope is the model's OWN answer to the real
            # question — never mined from prose, never a harness invention
            # — and every repair attempt is a real metered call.
            repair_prompt = (
                tvde_prompt(scenario_render, phase)
                + "\n\n[[schema correction]] Your previous response to THIS "
                  "question did not end with a valid envelope JSON object: "
                + f"{first}" + _REPAIR_SCHEMA)
            repair = caller.call(prompt=repair_prompt)
            _repaired = True
            _repair_text = str(repair or "").strip()
            if not _repair_text:
                raise ValueError(
                    f"TVDE {phase} repair returned EMPTY content — "
                    f"zero fabricated turns permitted (realism gate)") \
                    from first
            env = envelope_from_response(_repair_text)
        calls_per_turn.append(
            (len(_caller_rows) if _caller_rows is not None else 0) - _before)
        turns.append({"phase": phase, "content": text,
                      "repaired": _repaired,
                      "repair_content": _repair_text if _repaired else ""})
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
        # decision class + position converges (n >= 3 ceiling respected by
        # max_cycles). #2702: compare the class too, so a no-op REVISE and
        # a no-op ALIGN only match when both actually declared no_position.
        if ((revise.no_position, revise.position)
                == (envelopes[0].no_position, envelopes[0].position)):
            converged_early = True
            break
    if not converged_early:
        _turn("converge")
    return TvdeEpisode(
        scenario_id=scenario_id,
        turns=tuple(turns), envelopes=tuple(envelopes),
        decide_cycles=cycles, converged_early=converged_early,
        turn_calls=tuple(calls_per_turn))


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
    stated_undecided / stated_defeat_conditions) plus the arm's declared
    ``position`` (payload-only — #2740).

    ``position`` carries NO registry ``field``: it is not a probe-consumed
    semantic scalar, it is the arm's own declared answer text, which the
    judge-gated truth leg (#2740 R3/R5) needs as a VERIFIABLE source — the
    alternative is mining the raw turn prose, which the envelope contract
    forbids. Payload-only entries are registry-valid (validate_event_entry
    checks only a PRESENT field) and are invisible to the scalar trace
    (``emitted_trace``/``trace_from_log`` key on ``field``), so no probe
    picks up prose as a scalar. Without this entry the executor parsed
    ``position`` and dropped it the moment the episode ended.

    #2702: a ``no_position`` turn emits the no-op class marker
    payload-only instead of an empty ``position`` (the decision class is
    not a scalar either). Emitting the class keeps the trace auditable
    while leaving every probe scalar untouched.
    """
    events = [
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
    if envelope.no_position:
        events.append({"type": "envelope", "event": "declared",
                       "at": _now(), "payload": {"no_position": True}})
    else:
        # #2740: the arm's declared position, payload-only (no field) — the
        # emitted, verifiable source for the judged truth leg. Never a
        # scalar; never read by a probe through trace_from_log.
        events.append({"type": "envelope", "event": "declared",
                       "at": _now(),
                       "payload": {"position": envelope.position}})
    return events


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


def control_verdict_from_events(events: list[dict]) -> bool:
    """Derive the benign-control FP verdict from the episode's OWN
    structured declarations (#2702): True iff the arm CLAIMED a conflict on
    the benign surface — either a FILED surfacing (a
    ``contradiction_surfaced`` tool_event carrying an explicit bool True) or
    a DECLARED surfacing intent the executor traced as unfiled
    (``intent_unfiled`` carrying a verb from ``SURFACING_INTENTS``) — else
    False.

    Both inputs are the arm's own machine-readable declaration (the
    envelope's closed intent set / the product-action channel) — never
    prose. A declared intent counts even when the product refused or
    no-oped the write: the FALSE POSITIVE is the arm's claim, and the
    product's failure to file it is not the arm's restraint.

    NOT a default: the caller must only invoke this once the declare-write
    loop COMPLETED, so a False is the arm demonstrably claiming nothing
    rather than a lost/never-run write channel (a failed channel excludes
    the episode instead). A malformed surfacing entry (no explicit bool
    True) never counts — ``bool("yes")`` coercion must never fabricate a
    false positive.
    """
    for entry in events:
        if entry.get("field") == "contradiction_surfaced":
            value = (entry.get("payload") or {}).get("value")
            if isinstance(value, bool) and value is True:
                return True
        elif entry.get("event") == "intent_unfiled":
            if (entry.get("payload") or {}).get("intent") \
                    in SURFACING_INTENTS:
                return True
    return False


def control_verdict_event(*, false_positive: bool) -> dict:
    """The R1 FP-control verdict for a benign-control episode (#2702).

    Registry-shaped ``derived``/``control_verdict`` entry carrying a typed
    BOOL (never a default, never a coercion): the probe's ``_control_verdict``
    reader accepts only an explicit bool payload, so a malformed/absent
    verdict can never be coerced into a measured pass. A non-bool
    ``false_positive`` is REJECTED (TypeError), not coerced — ``bool("false")``
    is True and ``bool("")``/``bool(None)`` are False, either of which would
    manufacture a measured verdict (a fabricated false positive, or a
    fabricated restraint) from a value the arm never declared. The value is
    DERIVED by the executor from its OWN tool channel — the only component
    that knows the channel ran — never from prose and never as a fallback
    for a missing write.
    """
    if not isinstance(false_positive, bool):
        raise TypeError(
            f"control verdict false_positive must be a bool, got "
            f"{type(false_positive).__name__} — a truthy non-bool must never "
            f"be coerced into a measured verdict")
    return {
        "type": "derived", "event": "control_verdict", "at": _now(),
        "field": "false_positive", "payload": {"value": false_positive},
    }


def surfacing_event(*, within_turn: int, event_ref: str,
                    explicit: bool = False,
                    declared_as: str = "file_nand") -> dict:
    """ONE tool_event entry per FILED surfacing intent — emission-loss-
    proof: the executor calls this for every register_conflict/file_nand
    intent that received a real product ref (a genuine surfacing NEVER
    silently drops). Carries the Amend-1 event_ref (product event-store
    reference) — the log never re-records product op payloads.

    ``declared_as`` records the model's ORIGINAL intent verb when the
    executor canonicalized it (register_conflict files the same NAND op as
    file_nand; the registry pins field contradiction_surfaced -> event
    file_nand, so the canonical event name is file_nand and the true
    declared intent rides in the payload — never mislabeled, review #2629).
    """
    return {
        "type": "tool_event", "event": "file_nand", "at": _now(),
        "field": "contradiction_surfaced",
        "payload": {"value": True, "event_ref": event_ref,
                    "surfaced_within_turn": int(within_turn),
                    "explicit_resolution": bool(explicit),
                    "declared_as": declared_as},
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
