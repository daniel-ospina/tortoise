"""Arm-neutral itemized rubric specs (#2292 Task 1) — JSON-first loader,
canonical rendered judge prompt, and the neutrality lint (no tool names,
edge counts, or arm identity in ITEM phrasing or EVIDENCE renders).

Evidence renders are tool-stripped BEFORE this lint by the harness (the
probe/evidence bundle); the lint is the mechanical second guard so a
verb-availability artifact (the R2 delta measuring "did the arm call
file_nand", not "did the deliberation consider the counter-evidence") or an
arm-identity leak can never enter a graded construct. Banned-token source:
the product verb names from runner/emit._SUBTYPE_OK["tool_event"] +
arm-id identity tokens + numeric edge-count patterns (integration-surface
flag: tool names/edge counts/arm id in evidence = verb-availability
artifact + arm-identity leak).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from battery.exceptions import ConfigError

#: Product tool verbs (emit._SUBTYPE_OK["tool_event"]) — evidence that
#: names a write verb is tool-availability talk, never deliberation content.
_BANNED_VERBS = re.compile(
    r"\b(?:create_point|create_operator|file_nand|register_conflict|"
    r"supersede|mitigate)\b"
)
#: Arm identity tokens (a0/a1/a2/a2b/a3/a4/mock). "control" is excluded —
#: it is legitimate deliberation vocabulary (a control comparison), and
#: there is no arm whose id is the word "control".
_BANNED_ARMS = re.compile(r"\b(?:a0|a1|a2|a2b|a3|a4|mock)\b")
#: Graph/edge vocabulary — the graded construct is deliberation content,
#: never graph topology.
_BANNED_GRAPH = re.compile(r"\b(?:graph|edge|edges)\b")
#: Numeric edge/verb counts ("12 edges", "3 calls") — count-talk leaks the
#: mechanism surface.
_EDGE_COUNT = re.compile(r"\d+\s*(?:edges?|calls?|turns?)\b")


def _banned_hits(text: str) -> list[str]:
    hits: list[str] = []
    for label, rx in (
        ("verb", _BANNED_VERBS),
        ("arm", _BANNED_ARMS),
        ("graph", _BANNED_GRAPH),
        ("count", _EDGE_COUNT),
    ):
        if rx.search(text.lower()):
            hits.append(label)
    return hits


def lint_evidence_neutral(evidence_text: str) -> None:
    """Raise ValueError when evidence render carries tool/arm/graph leaks.

    The graded surface is the agent's DELIBERATION, rendered without tool
    names, edge counts, or arm identity (integration-surface rule). A leak
    here means the render would let a judge score mechanism availability
    instead of deliberation quality.
    """
    hits = _banned_hits(evidence_text)
    if hits:
        raise ValueError(
            f"evidence render is not arm-neutral (banned: {hits}): "
            f"{evidence_text[:200]!r}"
        )


def lint_rubric_items(items: list[dict]) -> None:
    """Raise ValueError when rubric ITEM phrasing is not arm-neutral."""
    for it in items:
        text = str(it.get("text", ""))
        hits = _banned_hits(text)
        if hits:
            raise ValueError(
                f"rubric item {it.get('id', '?')!r} is not arm-neutral "
                f"(banned: {hits}): {text[:200]!r}"
            )


@dataclass
class RubricSpec:
    """One itemized rubric: anchored yes/no items + gold anchors + the
    canonical rendered judge prompt (stable string — the checksum base)."""

    rubric_id: str
    items: list[dict] = field(default_factory=list)
    gold_anchors: list[dict] = field(default_factory=list)
    md_text: str = ""   # .md fallback raw text (legacy rubrics)
    extra: dict = field(default_factory=dict)  # rubric-file metadata

    @property
    def is_itemized(self) -> bool:
        return bool(self.items)

    def item_ids(self) -> list[str]:
        return [it["id"] for it in self.items]


def _load_json_spec(rubrics_dir: Path, rubric_id: str) -> RubricSpec | None:
    jp = rubrics_dir / f"{rubric_id}.json"
    if not jp.is_file():
        return None
    data = json.loads(jp.read_text(encoding="utf-8"))
    items = list(data.get("items", []))
    gold = list(data.get("gold_anchors", []))
    lint_rubric_items(items)
    for g in gold:
        lint_evidence_neutral(str(g.get("render", "")))
    # loader-level gold-block guard (review #2575 A-P2 + pro-gate P1-2): a
    # gold block with NO expected-no at >20% share makes the gold leg
    # vacuous (an all-yes degenerate judge passes 100% on all-yes anchors)
    # — and a rubric with NO gold block at all leaves the declarative gold
    # leg default-passing on the real path (gold_n=0 -> gold_ok=True) while
    # two identical all-yes judges clear AC1. Both refuse at load: an
    # itemized JSON rubric used for declarative validation MUST carry a
    # gold block with >=1 expected-no at >20% share.
    if not gold:
        raise ConfigError(
            f"rubric {rubric_id!r} is itemized JSON but has NO gold_anchors "
            f"block — declarative validation's gold leg would be vacuous "
            f"(gold_n=0 => gold_ok=True); add a gold block with >=1 "
            f"expected-no anchor at >20% share")
    no_renders = [g for g in gold if (g.get("expected") or "").lower()
                  in ("no", "expected-no", "expected_no")]
    if not no_renders or len(no_renders) / len(gold) <= 0.20:
        raise ConfigError(
            f"rubric {rubric_id!r} gold block needs >=1 expected-no anchor "
            f"at >20% share (found {len(no_renders)}/{len(gold)}) — an "
            f"all-yes gold block cannot catch a degenerate judge")
    extra = {k: v for k, v in data.items()
             if k not in ("rubric_id", "items", "gold_anchors")}
    return RubricSpec(rubric_id=rubric_id, items=items, gold_anchors=gold,
                      extra=extra)


def _load_md_spec(rubrics_dir: Path, rubric_id: str) -> RubricSpec | None:
    mp = rubrics_dir / f"{rubric_id}.md"
    if not mp.is_file():
        return None
    return RubricSpec(rubric_id=rubric_id,
                      md_text=mp.read_text(encoding="utf-8"))


def load_rubric_spec(config_dir: str | Path, rubric_id: str) -> RubricSpec:
    """JSON-first, .md fallback. ConfigError when neither exists."""
    rubrics = Path(config_dir) / "rubrics"
    spec = _load_json_spec(rubrics, rubric_id) or _load_md_spec(
        rubrics, rubric_id)
    if spec is None:
        raise ConfigError(
            f"no rubric {rubric_id!r}: expected rubrics/{rubric_id}.json "
            f"or rubrics/{rubric_id}.md under {Path(config_dir)}")
    return spec


_R2_PREAMBLE = (
    "You are judging a deliberation transcript against an anchored "
    "coverage rubric.\n"
    "Judge ONLY the deliberation content shown. Evidence is rendered "
    "without tool names or arm identity — never infer tooling from absence "
    "or presence.\n"
)


def render_rubric_prompt(spec: RubricSpec) -> str:
    """Canonical rendered judge prompt (stable derived string).

    Legacy .md rubrics render to their raw text (byte-identical to the
    pre-#2292 rubric_text contract). Itemized rubrics render the fixed
    preamble + items sorted by id + the YES/NO answer-vocabulary line — the
    string the gate checksums, so item edits drift the checksum and
    re-trigger validation (E2E-5.2).
    """
    if not spec.is_itemized:
        return spec.md_text
    lines = [_R2_PREAMBLE.strip(), "", "Anchored items:"]
    for it in sorted(spec.items, key=lambda x: str(x.get("id", ""))):
        lines.append(f"- {it['text']}")
    lines.append("")
    lines.append("Answer YES or NO for each anchored item. "
                 "Answer exactly one YES/NO per item.")
    return "\n".join(lines)
