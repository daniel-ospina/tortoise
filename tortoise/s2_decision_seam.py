"""S2.2 VET / S2.3 CLASSIFY — the typed-decision seam (#5087).

**What this module is.** The adapter, the prompts and the parser that
``EXTRACTOR-V4-ARCHITECTURE.md`` §4.2 names as missing work — *"swapping Jev in
requires an adapter, a prompt, and a parser … it is work, not a config flag."*
It is the seam **between** a decision-only model and the two steps that need
one. It is not a step: it holds no pipeline vocabulary of its own beyond the
verdict words ``vet_gate`` already declares (``KEEP`` / ``NOOP`` / ``DISCARD``).

**The division of labour, which is the whole reason this is safe.**
The MODEL labels. This module does only mechanics: request construction,
batching, the caller-owned threshold bands, schema validation, and the cost
record. A returned probability is **never** the verdict — :func:`vet_verdict`
reads it through :class:`Bands`, whose defaults are written down below and
which the caller may override. The model cannot invent a kind, because
:func:`_choice` only accepts a member of the set the caller offered.

**The request shape is copied from a working caller, never from prose — and
where it could not be, it says so.** ``questions`` is a **map** (name →
question), not an array, and every question carries ``instructions``; a
``score`` question also carries a 2–10 entry ``criteria`` array, low→high. Those
are the three shapes that 400 if you copy the documentation instead of a caller
(``#7866``: the array form returns ``questions must be a map``; a question
without ``instructions`` returns ``needs instructions``), and the ``noul`` /
``score`` shapes are the ones that caller exercised live. The response is
``{model, answers, usage}`` with one entry per question name, each echoing its
own ``type`` — which is what :func:`_noul` / :func:`_score` / :func:`_choice`
validate against, so an answer of the wrong primitive fails closed rather than
being coerced.

Two shapes are **not** previously-exercised and are taken from the vendor's own
reference page as fetched on 2026-10-26: the ``choice`` question's ``criteria``
**map** (kind → description) and the ``model``-echo pin in :func:`_decide`. The
deleted in-repo caller (``tools/collision_preflight.py``, #8175) used ``noul``
only, so if either shape is wrong the first armed call returns 400 →
:class:`Unavailable` → the declared fallback, and that first call is what
settles it. Nothing here decides a label on an unverified shape.

**Failure policies — both belong to the STEPS (§4.4), not to this module.**
This module never decides which way a step fails; it only refuses to invent a
label, which is the one direction that is unsafe under every policy:

===========================  =========================================  ==========================================
step                         capability unavailable (no key / 429 /      answer unparseable or out of schema
                             timeout / 5xx / model-pin mismatch)
===========================  =========================================  ==========================================
**S2.2 VET**                 **no verdict** for the candidate — it keeps    **no verdict**, and the failing
                             whatever the gate already decided           question is NAMED in
                             (mechanical DISCARD included); the failure  ``stats["seam"]["degraded"]``, both in
                             is counted in ``stats["seam"]``             this step's own stats and in the gate's
                                                                         (``result["vet"][*]["stats"]["seam"]``)
**S2.3 CLASSIFY**            no assignment → the caller's kNN top-1       same; the kind is never minted and
                             fallback, the design's named mechanical      the failing question is NAMED in
                             fast path; the failure is counted in         ``stats["seam"]["degraded"]`` too, so
                             ``stats["seam"]``                            the ITEMS an outage left unassigned
                                                                         are readable, not just the count
===========================  =========================================  ==========================================

**A degraded VET says nothing, and that is the only safe word.** An earlier
shape of this module emitted a ``NOOP`` for each degraded candidate; that is a
*verdict*, and ``vet_gate`` applies arbiter verdicts over the mechanical pass
(``decisions[iid].update(verdict)``), so a ``NOOP`` would have **rescinded the
``#4899`` identifier-only DISCARD** on a key outage — an outage silently
keeping what a working predicate had dropped, and erasing the rule id that
explains it. Omitting the verdict instead leaves every candidate exactly where
the gate put it, and the failure is recorded once, by name, in
``stats["seam"]`` — the gate's ``stats``, which is what reaches the artifact as
``result["vet"][*]["stats"]["seam"]``. Nothing in this module can emit
``DISCARD`` without a
parsed, in-schema, in-band answer — *a wrong keep is noise; a wrong drop is
memory loss* (§S2.2).

**Deliberately NOT here** (each is a recorded decision, not an omission):
``SPLIT`` (absent from O4's vocabulary, so atomicity can only *veto a drop*,
never ask for a split); the Level-2 coverage question / ``RENARRATE`` (a
*batch* verdict, and §S2.2 bounds that loop — "once, then accept"); ``MERGE``
(near-duplicate detection is ``#5006``'s mechanical ladder; the arbiter that
decides a flagged pair is a different question with a different evidence bar).

**Flag.** ``TORTOISE_S2_DECISION_SEAM`` — see :func:`seam_enabled`.
"""
from __future__ import annotations

import http.client
import json
import math
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .env_truthy import is_truthy

# ── The capability (pinned, exactly as the previous in-repo caller pinned it) ──

#: The pinned model. The endpoint resolves and returns a ``model`` on every
#: response; a response that omits it or names another one is rejected
#: (:func:`_decide`) — otherwise a drifting service would be trusted under this
#: pin's identity, forever, in whatever artifact recorded the decision.
MODEL = "jev-1.13.0"
ENDPOINT = "https://jevtypesafeai.com/api/v1/decide"

#: OFF by default — and the reason: the seam puts a NETWORK CALL and a SPEND on
#: the write path, so the deployed pipeline must keep exactly today's behaviour
#: (VET keeps-all with no arbiter; the classify tail is the caller's own kNN
#: fallback) until an operator arms it and re-measures. ``is_truthy`` is the
#: declared truthy contract (#4097), the same one every extractor flag uses.
ENV_FLAG = "TORTOISE_S2_DECISION_SEAM"

#: The validated batch shape (30–40 items per call). Bounds the per-request
#: token cost and the 64k-token input budget; it does NOT contain a failure to
#: one batch for VET (see :func:`vet_verdicts`).
BATCH_MAX = 40

#: One round-trip budget. A hung write-path call is worse than a degraded one.
TIMEOUT = 30.0

#: Prompt versions. A version is part of every seam result — ``stats["prompt"]``
#: from :func:`vet_verdicts` / :func:`classify_tail`, which the VET gate folds
#: into its own ``stats["seam"]["prompt"]`` and the classifier keeps under
#: ``stats["seam"]["prompt"]`` — and ``vet-v1`` is stamped into each rejected
#: verdict's reason, so an artifact can name the exact question wording that
#: produced a classification. Bump on any wording change.
VET_PROMPT = "vet-v1"
CLASSIFY_PROMPT = "classify-v1"

#: Rule id on a real verdict — the seam's answers are recognisable in
#: ``vet_gate``'s decisions, so a reader can tell what decided an item.
RULE_SEAM = "seam.arbiter"
#: Prefixes of the two degraded records in ``stats["degraded"]``. A degraded
#: item gets NO verdict (see the module docstring): these name the way it
#: degraded and, for a rejected answer, which question failed.
RULE_UNAVAILABLE = "seam.unavailable"
RULE_SCHEMA_REJECT = "seam.schema_reject"

#: Per-item and narrative caps. Every byte is a billed input token, and the
#: decision does not need more: VET's question is about the item's own stance,
#: and one long session narrative would crowd out the batch.
ITEM_MAX = 600
NARRATIVE_MAX = 6000

#: The atomicity scale — a ``score`` question needs 2–10 concrete levels,
#: low→high, so a 4-level scale returns 0–3 (a FRACTIONAL value is normal: the
#: docs' own example returns 2.97, which is why the band compares against a
#: threshold rather than requiring an integer).
ATOMICITY_LEVELS: tuple[str, ...] = (
    "a bundle of distinct claims fused together",
    "mostly one claim with a second claim embedded in it",
    "one claim carrying a modifier or a qualifier",
    "exactly one atomic, self-contained claim",
)


# ── The flag ────────────────────────────────────────────────────────────────

def seam_enabled() -> bool:
    """Whether the seam may be consulted. Off unless the flag says otherwise.

    Off is the production default and the reason is in :data:`ENV_FLAG`'s
    comment: this is a network call and a spend on the write path. Arming it is
    the operator's act, and the run's own evidence is what makes the effect
    measurable: the seam's record rides ``stats["seam"]`` on BOTH halves —
    ``_run_vet_pass``'s stats for VET, the classify stats for the tail — each
    naming its ``prompt``, ``model``, ``bands``, ``calls`` and ``cost_usd``.
    """
    return is_truthy(os.environ.get(ENV_FLAG))


# ── The caller-owned bands (the model's number is NOT the verdict) ───────────

@dataclass(frozen=True)
class Bands:
    """The thresholds that turn a calibrated probability into a verdict.

    These are the CALLER's, deliberately not the model's: the model answers
    "how likely is this a durable claim?", and what to *do* at 0.6 is a policy
    this repo has argued about (``#4899``, ``#2453``), not something a
    probability can settle. Written down here, overridable per call, and
    recorded in ``stats["bands"]`` — folded into the gate's ``stats["seam"]`` —
    so a decision carries the band it was read through.

    - ``keep_min`` — ``noul >= keep_min`` ⇒ ``KEEP``.
    - ``discard_max`` — ``noul < discard_max`` ⇒ *eligible* for ``DISCARD``,
      which a low atomicity score then vetoes (see :func:`vet_verdict`).
    - ``atomic_min`` — an item scoring below this on :data:`ATOMICITY_LEVELS` is
      a fused bundle. ``SPLIT`` is not in O4's vocabulary, so the only honest
      action is to NOT drop it: a fused candidate carries more than one claim
      and discarding it loses both.
    """

    keep_min: float = 0.70
    discard_max: float = 0.30
    atomic_min: float = 2.0

    def __post_init__(self) -> None:
        for name, value in (("keep_min", self.keep_min),
                            ("discard_max", self.discard_max),
                            ("atomic_min", self.atomic_min)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(float(value)):
                raise ValueError(f"Bands.{name} must be a finite number, got {value!r}")
        if not (0.0 <= self.discard_max < self.keep_min <= 1.0):
            raise ValueError(
                "Bands must satisfy 0 <= discard_max < keep_min <= 1 "
                f"(got discard_max={self.discard_max}, keep_min={self.keep_min})")
        if not (0.0 <= self.atomic_min <= len(ATOMICITY_LEVELS) - 1):
            raise ValueError(
                f"Bands.atomic_min must be within 0..{len(ATOMICITY_LEVELS) - 1}, "
                f"got {self.atomic_min}")

    def as_record(self) -> dict:
        return {"keep_min": self.keep_min, "discard_max": self.discard_max,
                "atomic_min": self.atomic_min}


# ── Failures (two, and they are not the same kind of thing) ─────────────────

#: The default bands, as ONE frozen instance: a shared default is safe because
#: ``Bands`` is frozen, and a call in a default argument would be re-evaluated
#: on every call (and is flagged by ruff's B008 for exactly that reason).
DEFAULT_BANDS = Bands()


class Unavailable(Exception):
    """The capability could not be consulted: no key, transport error, a
    non-2xx status, a timeout, or a response from an unverified model.

    Never caught inside a *parser*: it is the signal that says "do not read an
    answer", which is a different fact from "the answer is not usable"."""


class SchemaViolation(Exception):
    """An answer that cannot be trusted: missing, the wrong primitive, or out of
    the offered set / the declared scale.

    Carries the question that failed (``.question``) so the record names it —
    "the seam produced no verdict" is not an actionable report, "``keep_c3``
    answered as ``choice`` when asked ``noul``" is.
    """

    def __init__(self, question: str, detail: str) -> None:
        self.question = question
        self.detail = detail
        super().__init__(f"{question}: {detail}")


# ── Small mechanics ─────────────────────────────────────────────────────────

def _one_line(text: object, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat[:limit]


def _finite(value: object) -> float | None:
    """A finite float from a JSON number, else None.

    ``bool`` is rejected despite being an ``int`` subclass — ``True`` is not a
    probability. ``float(10**400)`` raises ``OverflowError`` because a JSON int
    can be arbitrarily large, and that exception is NOT in the transport's
    ``except`` tuple, so it must be caught here rather than escape as a
    traceback with no verdict.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _api_key() -> str | None:
    """``$JEV_API_KEY``, else the repo-root ``.env`` — its documented home
    (``.env.example``). Returns None when neither carries one; the caller then
    reports the capability as unavailable instead of making a real, billable
    call while believing it had a key. Never logged, never returned to a
    caller that could print it."""
    key = (os.environ.get("JEV_API_KEY") or "").strip()
    if key:
        return key
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    try:
        lines = dotenv.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if line.startswith("JEV_API_KEY="):
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            return value or None
    return None


def _http_post(payload: dict, *, timeout: float) -> Any:
    """One round trip. Every failure raises :class:`Unavailable`.

    No retry ladder: the steps are fail-open and the run records the failure, so
    a retry inside the seam would spend a second round trip to turn one recorded
    degradation into another. A caller that needs backoff owns it.
    """
    key = _api_key()
    if not key:
        raise Unavailable("no JEV_API_KEY in the process env or the repo-root .env")
    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:  # a status we must not read an answer from
        raise Unavailable(f"http {exc.code}") from exc
    except (OSError, ArithmeticError, http.client.HTTPException) as exc:
        # http.client.HTTPException (BadStatusLine / IncompleteRead / LineTooLong)
        # is NOT an OSError and urllib re-raises it un-wrapped; ArithmeticError
        # covers an unrepresentable timeout. Miss either and a malformed
        # response escapes as a traceback instead of a degraded step.
        raise Unavailable(f"transport {type(exc).__name__}") from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise Unavailable("response not JSON") from exc


def _decide(payload: dict, *, transport: Callable[[dict], Any] | None,
            timeout: float) -> tuple[Mapping, dict]:
    """One decision round trip → ``(answers, usage)``.

    Validates the envelope, not the answers: the answer-level schema lives in
    the three parsers, because *which* validation applies depends on the
    question that was asked.
    """
    raw = transport(payload) if transport is not None else _http_post(
        payload, timeout=timeout)
    if not isinstance(raw, Mapping):
        raise Unavailable("response is not an object")
    served = raw.get("model")
    if not (isinstance(served, str) and served == MODEL):
        # The endpoint is a pinned-version service and its contract returns the
        # resolved model. Omitted or different ⇒ malformed ⇒ fail closed; a
        # decision from an unverified model must never be recorded as this pin's.
        raise Unavailable(f"model mismatch: {_one_line(served, 40)!r}")
    answers = raw.get("answers")
    if not isinstance(answers, Mapping):
        raise Unavailable("response has no answers map")
    usage = raw.get("usage")
    return answers, (dict(usage) if isinstance(usage, Mapping) else {})


# ── The parser: validate, never trust ──────────────────────────────────────

def _answer(answers: Mapping, question: str, expected: str) -> Mapping:
    answer = answers.get(question)
    if not isinstance(answer, Mapping):
        raise SchemaViolation(question, "missing answer")
    kind = answer.get("type")
    if kind != expected:
        raise SchemaViolation(
            question, f"answered as {kind!r} when asked {expected!r}")
    return answer


def _noul(answers: Mapping, question: str) -> float:
    """A calibrated 0–1 probability, or :class:`SchemaViolation`."""
    value = _finite(_answer(answers, question, "noul").get("noul"))
    if value is None or not (0.0 <= value <= 1.0):
        raise SchemaViolation(question, "noul is not a finite probability in [0, 1]")
    return value


def _score(answers: Mapping, question: str, levels: Sequence[str]) -> float:
    """A (possibly fractional) score on the declared scale, or a violation.

    The scale is the criteria array that was SENT — so an answer on another
    scale, or one past its top level, is rejected rather than read as "very
    atomic".
    """
    hi = float(len(levels) - 1)
    value = _finite(_answer(answers, question, "score").get("score"))
    if value is None or not (0.0 <= value <= hi):
        raise SchemaViolation(question, f"score is not on the declared 0..{hi:g} scale")
    return value


def _choice(answers: Mapping, question: str, offered: Sequence[str]) -> str:
    """A member of the OFFERED set, or a violation. Never a minted value.

    An exact member is returned as offered. A case-only variant is resolved to
    the offered spelling **only** when exactly one offered entry folds equal —
    so a case difference can never become a different kind, and an ambiguous
    fold is a violation rather than a coin flip.
    """
    value = _answer(answers, question, "choice").get("choice")
    if not isinstance(value, str):
        raise SchemaViolation(question, "choice is not a string")
    if value in offered:
        return value
    folded = [o for o in offered if o.lower() == value.lower()]
    if len(folded) == 1:
        return folded[0]
    raise SchemaViolation(
        question, f"choice {_one_line(value, 60)!r} is not in the offered set")


def vet_verdict(keep: float, atomic: float, bands: Bands) -> tuple[str, str]:
    """Map one validated answer pair to ``(outcome, reason)``.

    The rule, in full — there is no other path to ``DISCARD`` in this module:

    * ``keep >= bands.keep_min`` ⇒ ``KEEP`` (atomicity is not a *validity*
      question, and ``SPLIT`` is unavailable, so a fused-but-real claim keeps).
    * ``keep < bands.discard_max`` and ``atomic >= bands.atomic_min`` ⇒
      ``DISCARD``.
    * ``keep < bands.discard_max`` and ``atomic < bands.atomic_min`` ⇒ ``NOOP``
      (fused: dropping it loses more than one claim).
    * anything in between ⇒ ``NOOP`` (consulted, uncertain, nothing changes).
    """
    if keep >= bands.keep_min:
        return "KEEP", f"validity {keep:.2f} >= {bands.keep_min:.2f}"
    if keep < bands.discard_max:
        if atomic < bands.atomic_min:
            return "NOOP", (
                f"validity {keep:.2f} < {bands.discard_max:.2f} but atomicity "
                f"{atomic:.2f} < {bands.atomic_min:.2f} — a fused candidate is "
                "not dropped (SPLIT is not in the vocabulary)")
        return "DISCARD", (
            f"validity {keep:.2f} < {bands.discard_max:.2f}, atomicity "
            f"{atomic:.2f} >= {bands.atomic_min:.2f}")
    return "NOOP", (
        f"validity {keep:.2f} in the uncertain band "
        f"[{bands.discard_max:.2f}, {bands.keep_min:.2f})")


# ── S2.2 VET — the questions, the call, the verdicts ───────────────────────

_VET_KEEP_QUESTION = (
    "Is element id={ref} a durable, self-contained item worth remembering on its "
    "own — a real entity, claim, decision, number, date or condition that a human "
    "would want to find again? Answer YES only if it carries belief; answer NO if "
    "it is a bare reference, a pointer to something else, or process noise that "
    "means nothing without the sentence it came from."
)
_VET_ATOMIC_QUESTION = (
    "How atomic is element id={ref}? Pick the level that describes it, from a "
    "bundle of distinct claims fused together (lowest) to exactly one "
    "self-contained claim (highest)."
)


def _vet_questions(pos: int, ref: str) -> dict:
    """The two typed questions ``vet-v1`` asks about ONE candidate.

    Two, not more: §6 fixes VET's shape as **Noul + Score**, and §S2.2's Level-1
    q3 (coverage) is a *batch* verdict this seam deliberately does not emit.
    """
    return {
        f"keep_c{pos}": {
            "type": "noul",
            "instructions": _VET_KEEP_QUESTION.format(ref=ref),
        },
        f"atomic_c{pos}": {
            "type": "score",
            "instructions": _VET_ATOMIC_QUESTION.format(ref=ref),
            # A score question MUST carry 2–10 levels or the request 400s.
            "criteria": list(ATOMICITY_LEVELS),
        },
    }


def vet_verdicts(
    candidates: Sequence[Mapping],
    narrative: str = "",
    *,
    transport: Callable[[dict], Any] | None = None,
    bands: Bands = DEFAULT_BANDS,
    batch_size: int = BATCH_MAX,
    timeout: float = TIMEOUT,
) -> tuple[list[dict], dict]:
    """S2.2 VET's typed decision over one candidate batch.

    ``candidates`` is ``vet_gate``'s own shape — ``[{"id", "section", "text",
    "kind"}]``. Returns ``(verdicts, stats)`` where each verdict is
    ``{"id", "outcome", "rule_id", "reason"}`` and ``outcome`` is a member of
    ``vet_gate.OUTCOMES`` (this module never invents a verdict word either).

    **A candidate gets a verdict only from a parsed, in-schema answer.** On the
    two degraded paths it gets **none** — not a ``NOOP``: a verdict word is
    something ``vet_gate`` applies over the mechanical ``#4899`` pass, so a
    degraded ``NOOP`` would rescind an identifier-only DISCARD. The failure is
    recorded instead in ``stats["degraded"]`` (item id → what went wrong, with
    the failing question named), which :func:`vet_arbiter` carries out under
    ``seam`` and :func:`vet_gate.vet_candidates` folds into the GATE's
    ``stats`` — the artifact path is ``result["vet"][*]["stats"]["seam"]``, not
    a ``batch`` key (the arbiter returns an empty ``batch``: no ``RENARRATE``).
    """
    stats: dict = {
        "prompt": VET_PROMPT,
        "model": MODEL,
        "bands": bands.as_record(),
        "items": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "unavailable": 0,
        "schema_rejects": [],
        "degraded": {},
        "by_outcome": {},
    }
    verdicts: list[dict] = []
    rows = [c for c in candidates if isinstance(c, Mapping) and str(c.get("text") or "").strip()]
    stats["items"] = len(rows)
    try:
        step = max(1, int(batch_size))
    except (TypeError, ValueError):
        step = BATCH_MAX

    for start in range(0, len(rows), step):
        chunk = rows[start:start + step]
        state: list[dict] = [{"id": "narrative",
                              "text": _one_line(narrative, NARRATIVE_MAX)}]
        questions: dict = {}
        for pos, candidate in enumerate(chunk):
            text = _one_line(candidate.get("text"), ITEM_MAX)
            state.append({"id": f"c{pos}", "text": text})
            questions.update(_vet_questions(pos, f"c{pos}"))
        payload = {"model": MODEL, "state": state, "questions": questions}
        try:
            answers, usage = _decide(payload, transport=transport, timeout=timeout)
        except Unavailable as exc:
            # The whole chunk degrades together: the answers of a failed call do
            # not exist, so a per-item guess would be an invention — and no
            # verdict at all, so nothing the gate already decided is rescinded.
            stats["unavailable"] += 1
            for candidate in chunk:
                stats["degraded"][str(candidate.get("id") or "")] = (
                    f"{RULE_UNAVAILABLE} {VET_PROMPT} ({exc})")
            continue
        stats["calls"] += 1
        _add_cost(stats, usage)
        for pos, candidate in enumerate(chunk):
            iid = str(candidate.get("id") or "")
            try:
                keep = _noul(answers, f"keep_c{pos}")
                atomic = _score(answers, f"atomic_c{pos}", ATOMICITY_LEVELS)
            except SchemaViolation as exc:
                # FAIL CLOSED: no verdict word is derived from an unusable
                # answer. Recorded by item (not silence) with the question
                # named, so the gate's decisions stay auditable and untouched.
                stats["schema_rejects"].append(exc.question)
                stats["degraded"][iid] = f"{RULE_SCHEMA_REJECT} {VET_PROMPT} {exc}"
                continue
            outcome, reason = vet_verdict(keep, atomic, bands)
            verdicts.append({"id": iid, "outcome": outcome,
                             "rule_id": RULE_SEAM, "reason": reason})

    for verdict in verdicts:
        key = verdict["outcome"]
        stats["by_outcome"][key] = stats["by_outcome"].get(key, 0) + 1
    return verdicts, stats


def vet_arbiter(
    *,
    transport: Callable[[dict], Any] | None = None,
    bands: Bands = DEFAULT_BANDS,
    batch_size: int = BATCH_MAX,
    timeout: float = TIMEOUT,
) -> Callable[[list[dict], str], Any]:
    """The :data:`vet_gate.Arbiter` adapter — the callable ``vet_candidates``
    expects (``arbiter(candidates, narrative)``), so the seam is consumed
    through the contract that already exists rather than a new one.

    No ``batch`` verdict is returned: ``RENARRATE`` is §S2.2's once-only loop and
    is not this seam's to request.

    The seam's own record rides back under ``seam`` so the spend, the bands and
    the degraded items land in the artifact instead of dying at this boundary: an
    armed seam that makes a billable call no stat can show is the inert-enforcer
    class, and :func:`vet_gate.vet_candidates` folds the key into its ``stats``
    additively (its own counters stay its own).
    """
    def arbiter(candidates: list[dict], narrative: str) -> dict:
        verdicts, stats = vet_verdicts(
            candidates, narrative, transport=transport, bands=bands,
            batch_size=batch_size, timeout=timeout)
        return {"verdicts": verdicts, "batch": {}, "seam": stats}

    return arbiter


# ── S2.3 CLASSIFY — a choice over the kinds actually offered ───────────────

_CLASSIFY_QUESTION = (
    "Which single kind is element id={ref}? Choose one of the offered kinds for "
    "that element — nothing else. The candidate kinds come from a similarity "
    "search, so more than one may look plausible; pick the one that is TRUE of "
    "the element, not the one that merely shares vocabulary."
)


def classify_tail(
    items: Sequence[tuple[str, str, Sequence[str]]],
    *,
    descriptions: Mapping[str, str] | None = None,
    transport: Callable[[dict], Any] | None = None,
    batch_size: int = BATCH_MAX,
    timeout: float = TIMEOUT,
) -> tuple[dict[str, str], dict]:
    """S2.3's typed decision over the classifier's low-margin **tail**.

    ``items`` is ``[(item_id, text, offered_kinds)]`` — the offered set IS the
    kNN candidate list for that item, so the model discriminates among the
    plausible kinds rather than re-deriving the whole vocabulary (the kNN fast
    path already covered the easy majority; this replaces only the adjudication
    the hardcoded prompt used to do).

    Returns ``({item_id: kind}, stats)``. An item missing from the mapping gets
    **no** assignment on purpose — the caller then applies its own declared
    fallback (kNN top-1), which is the design's mechanical path. Nothing here can
    return a kind that was not offered.

    An item the seam could not assign from a **parsed, in-schema** answer is
    named in ``stats["degraded"]`` (item id → what went wrong, with the failing
    question named), which is the same shape S2.2's :func:`vet_verdicts` records
    for the same two failures. A count alone cannot answer *"which items did the
    outage leave to the kNN?"* — the audit question an armed run exists to
    answer — so the map is per item, not a tally.
    """
    assignments: dict[str, str] = {}
    stats: dict = {
        "prompt": CLASSIFY_PROMPT,
        "model": MODEL,
        "items": 0,
        "calls": 0,
        "cost_usd": 0.0,
        "unavailable": 0,
        "schema_rejects": [],
        "degraded": {},
        "skipped_no_offer": 0,
    }
    desc = dict(descriptions or {})
    # An item with an empty offered set cannot be asked a `choice` question at
    # all (an empty criteria map 400s); it falls through to the caller's
    # fallback, counted so the gap is visible rather than silent.
    rows = [(iid, text, [str(k) for k in offered])
            for iid, text, offered in items if [str(k) for k in offered]]
    stats["items"] = len(rows)
    stats["skipped_no_offer"] = len(items) - len(rows)
    try:
        step = max(1, int(batch_size))
    except (TypeError, ValueError):
        step = BATCH_MAX

    for start in range(0, len(rows), step):
        chunk = rows[start:start + step]
        state: list[dict] = []
        questions: dict = {}
        for pos, (_iid, text, offered) in enumerate(chunk):
            state.append({"id": f"c{pos}", "text": _one_line(text, ITEM_MAX)})
            questions[f"kind_c{pos}"] = {
                "type": "choice",
                "instructions": _CLASSIFY_QUESTION.format(ref=f"c{pos}"),
                "criteria": {kind: desc.get(kind) or kind for kind in offered},
            }
        payload = {"model": MODEL, "state": state, "questions": questions}
        try:
            answers, usage = _decide(payload, transport=transport, timeout=timeout)
        except Unavailable as exc:
            # The chunk degrades together (its answers do not exist), so no item
            # in it is assigned — and each one is NAMED, so the artifact can say
            # which items this outage left to the caller's kNN fallback.
            stats["unavailable"] += 1
            for iid, _text, _offered in chunk:
                stats["degraded"][iid] = (
                    f"{RULE_UNAVAILABLE} {CLASSIFY_PROMPT} ({exc})")
            continue
        stats["calls"] += 1
        _add_cost(stats, usage)
        for pos, (iid, _text, offered) in enumerate(chunk):
            try:
                assignments[iid] = _choice(answers, f"kind_c{pos}", offered)
            except SchemaViolation as exc:
                stats["schema_rejects"].append(exc.question)
                stats["degraded"][iid] = (
                    f"{RULE_SCHEMA_REJECT} {CLASSIFY_PROMPT} {exc}")
                continue
    return assignments, stats


def classify_decider(
    *,
    descriptions: Mapping[str, str] | None = None,
    transport: Callable[[dict], Any] | None = None,
    batch_size: int = BATCH_MAX,
    timeout: float = TIMEOUT,
) -> Callable[[list[tuple[str, str, Sequence[str]]]], tuple[dict[str, str], dict]]:
    """The callable ``KindClassifier`` consumes as ``tail_decider``.

    It returns ``(assignments, stats)``, not just the assignments: the seam's
    refusals name the question that failed and its cost is the endpoint's own
    reported charge, and a caller that received only a mapping would have to
    discard both. A stubbed decider for a test returns ``({id: kind}, {})``.
    """
    def decide(
        items: list[tuple[str, str, Sequence[str]]],
    ) -> tuple[dict[str, str], dict]:
        return classify_tail(
            items, descriptions=descriptions, transport=transport,
            batch_size=batch_size, timeout=timeout)

    return decide


def _add_cost(stats: dict, usage: Mapping) -> None:
    """Accumulate the endpoint's own reported charge.

    Display/report only, so an unrepresentable value (a JSON int can be
    arbitrarily large) must never break the decision — bounded and suppressed
    the same way ``collision_preflight`` had to bound it.
    """
    cost = _finite(usage.get("cost_usd"))
    if cost is not None and cost >= 0.0:
        total = stats.get("cost_usd") or 0.0
        stats["cost_usd"] = total + cost
    tokens = _finite(usage.get("input_tokens"))
    if tokens is not None and tokens >= 0.0:
        stats["input_tokens"] = (stats.get("input_tokens") or 0.0) + tokens
