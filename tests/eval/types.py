"""Eval types for the extraction-quality measurement core (epic #909 slice 8a, #960).

Implements the plan §6.3 dataclasses verbatim:
`docs/epics/2026-08-11-epic909-value-first-mining/04-plan.md` §6.3 (Eval interfaces).

Label/RelationLabel mirror the slice-1 tooling types (tools/judge_harness.py)
field-for-field, so tools/kappa.py and tools/min_signal.py consume these labels
via duck typing — the measurement core reuses the gate tooling instead of
duplicating it (plan §8.3 slice 8; kappa lives in tools/, merged #945).

Convention (documented in §6.3): a Window's `gold_labels` field holds the
window's label list — the GOLD labels on gold windows, the MODEL's predicted
labels on pred windows (None = unlabeled window).
"""
from __future__ import annotations

from dataclasses import dataclass, field

CLASS_VOCAB = ("decision", "event", "claim", "process", "none")
RELATION_VOCAB = ("IMPL", "NAND", "MITIGATES")
WINDOW_TYPES = ("design", "operational")


@dataclass
class RelationLabel:
    """A gold/pred relation between labels — IMPL | NAND | MITIGATES.

    For MITIGATES, `target` is the identity of the IMPL edge being mitigated
    (edge-targeted op per plan §6.1 / resolution PL1) — serialized as the edge
    id "[src→dst]" (source edu index → target edu index of the IMPL edge),
    matching the slice-1 harness convention (tools/judge_harness.py
    RelationLabel). `bias` carries the MITIGATES strength (canonical range
    [0.10, 0.50], enforced by the harness).

    The canonical mitigation probe (spec §6, DE2E-11):
        X "it's cheap" IMPL A;  Z "we can raise the price" MITIGATES [X→A];
        Y "customers aren't price-sensitive" IMPL Z
    builds as RelationLabel("IMPL", source=0, target=1) on X's label,
    RelationLabel.mitigates(source=2, edge_src=0, edge_dst=1, strength=0.3)
    on Z's label, and RelationLabel("IMPL", source=3, target=2) on Y's label.
    """

    type: str  # IMPL | NAND | MITIGATES
    source: int | None = None  # edu_index of the source EDU (null when n/a)
    target: str | int | None = None  # edu_index (IMPL/NAND) or edge id "[X→A]" (MITIGATES)
    bias: float | None = None  # MITIGATES only; canonical range [0.10, 0.50]

    @classmethod
    def mitigates(
        cls,
        source: int,
        edge_src: int,
        edge_dst: int,
        strength: float,
    ) -> "RelationLabel":
        """Canonical MITIGATES edge: Z (source) MITIGATES [X→A] (edge_src→edge_dst)."""
        return cls(
            type="MITIGATES",
            source=source,
            target=f"[{edge_src}→{edge_dst}]",
            bias=strength,
        )


@dataclass
class Label:
    """A gold/pred verdict per EDU (plan §6.3)."""

    edu_index: int
    class_: str  # decision | event | claim | process | none
    kind: str | None = None  # pack kind when entity-bearing
    atomicity: bool = True  # true = single commitment
    source_ref: str | None = None
    relations: list[RelationLabel] = field(default_factory=list)


@dataclass
class Window:
    """One extraction window (plan §6.3).

    `gold_labels` is the window's label list — gold labels on gold windows,
    the model's predicted labels on pred windows (None = unlabeled window).
    """

    session_id: str
    window_id: str
    edus: list[str]  # the EDUs to classify (transcript-derived)
    gold_labels: list[Label] | None = None


@dataclass
class MetricsReport:
    """Per-window-macro evaluation metrics (plan §6.3 + slice-8 reconciliation).

    Per-class P/R/F1 are NEVER blended (spec §6 — base rates differ wildly).
    A class with gold window-N < 12 is SKIPPED (plan §6.3 per-class N rule):
    its p/r/f1 are NaN and `per_class_n[class]` carries the actual N (the
    machine-readable flag). `ece` is NaN until pred confidences are supplied
    (c_cal telemetry, compute_metrics confidences kwarg). `mitigation_recall`
    is NaN when the mitigation class is skipped (no gold MITIGATES or N < 12).
    `process_routing` (R3) is reported at any N — its band is warn-only until
    n ≥ 20 (research-r8; thresholds.yaml row carries warn_only_until: 20).
    """

    per_class: dict[str, dict[str, float]]  # {class: {p, r, f1}} — NaN when skipped
    per_class_n: dict[str, int]  # {class: gold window-N} — N < 12 = the skip flag
    layer_correct: float
    atomicity: float
    citation_correctness: float
    kind_correctness: float
    entity_p_r: tuple[float, float]
    empty_rate: float
    ece: float
    mitigation_recall: float
    min_signal: dict[str, bool]  # {window_type: passed} — degenerate-empty defense
    r1r3_conjunction: dict[str, float | str]  # THE decision-class test stats (spec §6)
    process_routing: float
    rate_n: dict[str, int]  # per-rate sample N (true support for the band rows) —
    # {layer_correct, atomicity, citation_correctness, kind_correctness,
    #  entity (gold items), empty_rate (windows), ece (comparable EDUs),
    #  mitigation (gold windows), process_routing (gold items)}
