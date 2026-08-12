#!/usr/bin/env python3
"""judge_harness — the 2-window validation gate's judge harness (epic #909 slice 1a, #945).

Labels an extraction window via the classification-model rubric prompt (spec
docs/epics/2026-08-11-epic909-value-first-mining/spec-classification-model.md
§1-2) using the existing model adapter (tests/model_adapters.py —
OpenRouterModel + MODELS registry; NO new provider dependency).

The harness is the slice-1 tooling owned by DE2E-1 (plan §7): utterance-tagged
transcript in → labeled window JSON out. The labeled window is consumed by
tools/kappa.py (inter-judge agreement + gate decision) and tools/min_signal.py
(window-type minimum-signal assertion).

Transcript format (defined here, per plan DE2E-1 setup):
    <index>: <role>: <text>
One EDU per line. <index> is a non-negative int (the canonical edu_index used
in labels and kappa). <role> is an identifier token — the harness accepts any
token (owner/judge/agent/tool/user/assistant/system...). Blank lines and
'#'-prefixed comment lines are skipped. A malformed or duplicate-index line is
a hard error (the gate must not silently drop EDUs).

Output (labeled window JSON, written to --out or stdout):
    {
      "window_id": "...",
      "window_type": "design|operational",
      "judge": "...",
      "n_edus": <int>,                  # EDUs in the transcript
      "degenerate": <bool>,             # DE2E-1 neg (a): empty labels on a non-empty window
      "incomplete": <bool>,             # labels present but < n_edus (informational)
      "labels": [
        {"edu_index": <int>, "class": "decision|event|claim|process|none",
         "kind": <str|null>, "atomicity": <bool>, "source_ref": <str|null>,
         "relations": [{"type": "IMPL|NAND|MITIGATES", "source": <int|null>,
                        "target": <int|str>, "bias": <float|null>}]}
      ]
    }

Exit codes: 0 = labeled (degenerate or not); 2 = degenerate labeling (judge
emitted ZERO labels on a non-empty window — DE2E-1 neg (a) flag, CI-findable);
1 = operational error (bad input, model/parse failure).

Usage:
    python tools/judge_harness.py --transcript transcript.txt \\
        --window-id w1 --window-type design --model deepseek-flash --out labels.json
    python tools/judge_harness.py --transcript - --list-models
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

# ── The rubric (spec-classification-model.md §1-2) ──────────────────────────

CLASS_VOCAB = ("decision", "event", "claim", "process", "none")
RELATION_VOCAB = ("IMPL", "NAND", "MITIGATES")

RUBRIC_SYSTEM = (
    "You are the frontier judge for the Tortoise value-first mining rubric "
    "(epic #909). Label every elementary discourse unit (EDU) in the window "
    "with EXACTLY ONE class.\n"
    "\n"
    "## The two-axis model\n"
    "Axis 1 — illocutionary type (Searle): a COMMISSIVE commits to future "
    "action (decided/chose/agreed/we-will/I-will/committed); an ASSERTIVE "
    "reports the world (believe/think/shows/costs/failed).\n"
    "Axis 2 — aspect (within assertives): past-perfective accomplishment "
    "(\"fixed/repaired/shipped/completed\") → EVENT; stative/gnomic "
    "(\"costs $0.60/M\", \"fails 40% on CI\", \"is the unit-economics killer\") → CLAIM.\n"
    "\n"
    "| | Commissive | Assertive past-perfective | Assertive stative |\n"
    "|---|---|---|---|\n"
    "| Class | DECISION | EVENT | CLAIM |\n"
    "\n"
    "## The R1∧R3 conjunction (the real decision gate)\n"
    "A commissive alone is NOT a decision. DECISION = commissive ∧ "
    "product-knowledge-bearing (asserts something durable about the domain: a "
    "choice of approach, a ruling, a commitment with epistemic weight). "
    "Otherwise → event (or process if R3-routed).\n"
    "- \"should\" is a RECOMMENDATION, not a commitment — never a decision.\n"
    "- \"will\" is ambiguous (prediction vs commitment) — discriminator: "
    "subject agentivity + the R1∧R3 conjunction.\n"
    "- Process/governance commitments (\"validate on 2 windows first\", "
    "\"record this on the issue\") → PROCESS (R3-routed; never a point).\n"
    "\n"
    "## Cue tables\n"
    "DECISION cues: decided, chose, agreed to, we will, I will (with "
    "agentivity + the conjunction), we're going with, the ruling is, default "
    "to, ship X first, reject Y.\n"
    "EVENT cues: repaired, fixed, shipped, completed, merged, deployed, "
    "closed, ran, measured, filed, created (\"did X\" = event).\n"
    "CLAIM cues: stative predicates — is, costs, fails, implies, means, "
    "shows, measured, the cause is, the risk is, requires, depends on; plus "
    "quantified facts and research findings (with source_ref).\n"
    "MITIGATE cues (R9 — \"true but matters less\", targets an IMPL edge): "
    "it's an estimate, decide with real telemetry, a positioning tension not "
    "structural, the caveat is, only if, gated on, the one swing variable, "
    "only achievable because, the leading indicator is, preliminary, "
    "watch-gate not a statistical test, none would let it be built as-is, "
    "still to run before the gate, real but not transformative.\n"
    "\n"
    "## Atomicity (R2)\n"
    "Unit = EDU = minimal speech act; \"A AND B AND C\" is 3 EDUs. atomicity "
    "= true iff the label is a SINGLE commitment (no coordination cues, ≤1 "
    "commissive predicate).\n"
    "\n"
    "## Classes (closed vocabulary)\n"
    "decision | event | claim | process | none\n"
    "- none: extract-nothing EDUs — tool chatter, headers, metadata, "
    "formatting, pure description, non-informative filler.\n"
    "\n"
    "## Output format\n"
    "Return ONLY a JSON object with a \"labels\" array — ONE entry per EDU "
    "index present in the window. Each entry:\n"
    '{"edu_index": <int>, "class": "decision|event|claim|process|none", '
    '"kind": <pack kind or null>, "atomicity": <bool>, "source_ref": <source '
    'id or null>, "relations": [{"type": "IMPL|NAND|MITIGATES", "source": '
    '<int|null>, "target": <int or edge id string>, "bias": <0.10-0.50 or '
    'null>}]}\n'
    "If an EDU carries no relations, relations = []. Emit a label for EVERY "
    "EDU — including \"none\" verdicts; never omit an EDU. Do not wrap the "
    "JSON in markdown fences."
)

EDU_LINE_RE = re.compile(r"^(\d+):\s*([A-Za-z0-9_-]+):\s*(.+)$")


class TranscriptError(ValueError):
    """Malformed or inconsistent utterance-tagged transcript."""


class LabelParseError(ValueError):
    """The judge's response could not be parsed into valid window labels."""


# ── Transcript parsing ──────────────────────────────────────────────────────

@dataclass
class Edu:
    index: int
    role: str
    text: str


def parse_transcript(text: str) -> list[Edu]:
    """Parse the utterance-tagged transcript format defined in this module.

    Format: one EDU per line ``<index>: <role>: <text>``. Blank lines and
    ``#`` comments are skipped. Indices must be unique and non-negative.
    """
    edus: list[Edu] = []
    seen: set[int] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = EDU_LINE_RE.match(line)
        if not m:
            raise TranscriptError(
                f"line {lineno}: malformed EDU line {line!r} — expected "
                f"'<index>: <role>: <text>'"
            )
        index = int(m.group(1))
        if index in seen:
            raise TranscriptError(f"line {lineno}: duplicate EDU index {index}")
        seen.add(index)
        edus.append(Edu(index=index, role=m.group(2), text=m.group(3).strip()))
    return edus


# ── Label model (shape per plan §6.3 / issue #945 output contract) ─────────

@dataclass
class RelationLabel:
    type: str                      # IMPL | NAND | MITIGATES
    source: int | None = None      # edu_index of the source EDU (null when n/a)
    target: str | int | None = None  # edu_index (IMPL/NAND) or edge id "[X→A]" (MITIGATES)
    bias: float | None = None      # MITIGATES only; canonical range [0.10, 0.50] (ENFORCED)


@dataclass
class Label:
    edu_index: int
    class_: str                    # decision | event | claim | process | none
    kind: str | None = None        # pack kind when entity-bearing
    atomicity: bool = True         # true = single commitment
    source_ref: str | None = None
    relations: list[RelationLabel] = field(default_factory=list)


@dataclass
class LabeledWindow:
    window_id: str
    window_type: str               # design | operational
    judge: str
    n_edus: int
    labels: list[Label]
    degenerate: bool = False       # DE2E-1 neg (a): empty labels on non-empty window
    incomplete: bool = False       # labels present but < n_edus (informational)

    def to_json(self) -> dict:
        data = asdict(self)
        # "class" (issue #945 output contract) — never "class_".
        for label in data["labels"]:
            label["class"] = label.pop("class_")
        return data


# ── Prompt assembly ─────────────────────────────────────────────────────────

def build_user_prompt(edus: list[Edu]) -> str:
    """Numbered window view handed to the judge (index is the edu_index)."""
    return "\n".join(f"{e.index}. [{e.role}] {e.text}" for e in edus)


# ── Judge-response parsing ──────────────────────────────────────────────────

def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def parse_labels(raw: str, edus: list[Edu]) -> list[Label]:
    """Parse the judge's JSON response into labels validated against the window."""
    valid_indices = {e.index for e in edus}
    try:
        data = json.loads(_strip_fences(raw))
    except json.JSONDecodeError as exc:
        raise LabelParseError(
            f"judge response is not JSON (near {exc.pos}): {raw[:200]!r}"
        ) from exc
    if not isinstance(data, dict) or "labels" not in data:
        raise LabelParseError(f"judge response missing 'labels' array: {raw[:200]!r}")
    raw_labels = data["labels"]
    if not isinstance(raw_labels, list):
        raise LabelParseError("'labels' is not an array")

    labels: list[Label] = []
    emitted: set[int] = set()
    for i, item in enumerate(raw_labels):
        if not isinstance(item, dict):
            raise LabelParseError(f"labels[{i}]: not an object")
        if "edu_index" not in item:
            raise LabelParseError(f"labels[{i}]: missing 'edu_index'")
        if isinstance(item["edu_index"], bool) or not isinstance(item["edu_index"], int):
            raise LabelParseError(f"labels[{i}]: 'edu_index' must be an int (bool is not an int here)")
        idx = item["edu_index"]
        if idx not in valid_indices:
            raise LabelParseError(
                f"labels[{i}]: edu_index {idx} not in window (indices: "
                f"{sorted(valid_indices)})"
            )
        if idx in emitted:
            raise LabelParseError(f"labels[{i}]: duplicate label for edu_index {idx}")
        emitted.add(idx)

        if "class" not in item:
            raise LabelParseError(f"labels[{i}]: missing 'class'")
        class_ = str(item["class"]).strip()
        if class_ not in CLASS_VOCAB:
            raise LabelParseError(
                f"labels[{i}]: unknown class {class_!r} — must be one of "
                f"{', '.join(CLASS_VOCAB)}"
            )

        kind = item.get("kind")
        if kind is not None and not isinstance(kind, str):
            raise LabelParseError(f"labels[{i}]: 'kind' must be a string or null")

        atomicity = item.get("atomicity", True)
        if not isinstance(atomicity, bool):
            raise LabelParseError(f"labels[{i}]: 'atomicity' must be a bool")

        source_ref = item.get("source_ref")
        if source_ref is not None and not isinstance(source_ref, str):
            raise LabelParseError(f"labels[{i}]: 'source_ref' must be a string or null")

        relations: list[RelationLabel] = []
        raw_relations = item.get("relations", [])
        if not isinstance(raw_relations, list):
            raise LabelParseError(f"labels[{i}]: 'relations' must be an array")
        for j, rel in enumerate(raw_relations):
            if not isinstance(rel, dict) or "type" not in rel:
                raise LabelParseError(
                    f"labels[{i}].relations[{j}]: missing 'type'"
                )
            rtype = str(rel["type"]).strip()
            if rtype not in RELATION_VOCAB:
                raise LabelParseError(
                    f"labels[{i}].relations[{j}]: unknown relation type "
                    f"{rtype!r} — must be one of {', '.join(RELATION_VOCAB)}"
                )
            source = rel.get("source")
            if source is not None and (isinstance(source, bool) or not isinstance(source, int)):
                raise LabelParseError(
                    f"labels[{i}].relations[{j}]: 'source' must be an int or null (bool is not an int here)"
                )
            target = rel.get("target")
            if target is not None and (
                isinstance(target, bool) or not isinstance(target, (int, str))
            ):
                raise LabelParseError(
                    f"labels[{i}].relations[{j}]: 'target' must be an int, "
                    f"string, or null"
                )
            bias = rel.get("bias")
            if bias is not None:
                if not isinstance(bias, (int, float)) or isinstance(bias, bool):
                    raise LabelParseError(
                        f"labels[{i}].relations[{j}]: 'bias' must be a number or null"
                    )
                if not 0.10 <= float(bias) <= 0.50:
                    raise LabelParseError(
                        f"labels[{i}].relations[{j}]: 'bias' {bias} outside the "
                        f"canonical MITIGATES range [0.10, 0.50]"
                    )
            relations.append(
                RelationLabel(type=rtype, source=source, target=target, bias=bias)
            )

        labels.append(
            Label(
                edu_index=idx,
                class_=class_,
                kind=kind,
                atomicity=atomicity,
                source_ref=source_ref,
                relations=relations,
            )
        )
    return labels


# ── The labeling pipeline (DE2E-1 steps 1-2) ────────────────────────────────

def label_window(
    edus: list[Edu],
    model,
    *,
    window_id: str,
    window_type: str = "design",
    judge: str = "frontier",
) -> LabeledWindow:
    """Label a window via the rubric prompt; returns the labeled window.

    ``model`` is any object exposing ``complete(*, system, user) -> str``
    (the OpenRouterModel/OllamaModel adapter interface — tuning knobs are
    constructor attributes on the adapters, applied by the CLI via
    ``_apply_tuning``). Never called with a real model in unit tests — tests
    inject a mock.
    """
    response = model.complete(system=RUBRIC_SYSTEM, user=build_user_prompt(edus))
    labels = parse_labels(response, edus)
    n_edus = len(edus)
    return LabeledWindow(
        window_id=window_id,
        window_type=window_type,
        judge=judge,
        n_edus=n_edus,
        labels=labels,
        degenerate=n_edus > 0 and len(labels) == 0,   # DE2E-1 neg (a)
        incomplete=0 < len(labels) < n_edus,
    )


# ── CLI ─────────────────────────────────────────────────────────────────────

def _default_model_factory(model_name: str):
    """Resolve a model by name via tests/model_adapters.py (lazy import)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests.model_adapters import MODELS  # noqa: PLC0415 (lazy: keeps unit tests LLM-free)

    try:
        factory = MODELS[model_name]
    except KeyError:
        raise SystemExit(
            f"unknown model {model_name!r} — choose from: {', '.join(sorted(MODELS))}"
        ) from None
    return factory()


def _apply_tuning(model, max_tokens: int, temperature: float) -> None:
    """Apply CLI tuning knobs to an adapter instance that supports them."""
    for attr, value in (("max_tokens", max_tokens), ("temperature", temperature)):
        if hasattr(model, attr):
            setattr(model, attr, value)


def main(argv: list[str] | None = None, *, model_factory: Callable[[str], object] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if "--list-models" in argv:
        # Pre-scan: --list-models must work standalone (--transcript is required
        # for a labeling run only).
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tests.model_adapters import MODELS  # noqa: PLC0415

        for name in sorted(MODELS):
            print(name)
        return 0

    parser = argparse.ArgumentParser(
        prog="judge_harness",
        description="Label an extraction window via the classification-model "
        "rubric (epic #909 DE2E-1). Transcript format: "
        "'<index>: <role>: <text>' per line.",
    )
    parser.add_argument("--transcript", required=True, help="path to the "
                        "utterance-tagged transcript, or '-' for stdin")
    parser.add_argument("--window-id", required=True, help="window identifier "
                        "(e.g. w1, w2)")
    parser.add_argument("--window-type", choices=("design", "operational"),
                        default="design", help="window type (min-signal "
                        "applies to operational windows)")
    parser.add_argument("--judge", default="frontier", help="judge name "
                        "(default: frontier)")
    parser.add_argument("--model", default="deepseek-flash",
                        help="model registry name (tests/model_adapters.py MODELS)")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--out", help="write labeled-window JSON to this file "
                        "(default: stdout)")
    parser.add_argument("--list-models", action="store_true",
                        help="list available model names and exit")
    args = parser.parse_args(argv)

    try:
        transcript = sys.stdin.read() if args.transcript == "-" else Path(args.transcript).read_text()
        edus = parse_transcript(transcript)
    except (OSError, TranscriptError) as exc:
        print(f"judge_harness: error: {exc}", file=sys.stderr)
        return 1

    if model_factory is None:
        model = _default_model_factory(args.model)
    else:
        model = model_factory(args.model)
    _apply_tuning(model, args.max_tokens, args.temperature)

    try:
        window = label_window(
            edus,
            model,
            window_id=args.window_id,
            window_type=args.window_type,
            judge=args.judge,
        )
    except Exception as exc:  # LabelParseError, model/network failures alike
        print(f"judge_harness: error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(window.to_json(), indent=2)
    try:
        if args.out:
            Path(args.out).write_text(payload + "\n")
        else:
            print(payload)
    except OSError as exc:
        print(f"judge_harness: error: {exc}", file=sys.stderr)
        return 1
    if window.degenerate:
        print(
            "judge_harness: DEGENERATE LABELING: window is non-empty "
            f"({window.n_edus} EDUs) but the judge emitted 0 labels — DE2E-1 "
            "neg (a) flag",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
