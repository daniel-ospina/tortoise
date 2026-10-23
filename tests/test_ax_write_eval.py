"""The write-pipeline eval instrument's scoring rules (#9675 Step 1).

These pin the RULES, not a run's outcome: the committed per-input records are
evidence and must stay editable-by-reason (a Step-3 re-run replaces them), while
the scoring semantics are what the next lane relies on to read a delta. Each
test states the write failure it exists to catch, because the instrument is only
as good as the failures it can still see.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GOLD = REPO_ROOT / "eval" / "how-to-use-tortoise" / "step1" / "inputs.json"


def _load_tool():
    spec = importlib.util.spec_from_file_location(
        "ax_write_eval", REPO_ROOT / "tools" / "ax_write_eval.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


axe = _load_tool()


def _load_setup():
    spec = importlib.util.spec_from_file_location(
        "ax_step1_setup", GOLD.parent / "setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


step1_setup = _load_setup()


def _record(nodes=(), edges=(), operators=(), sources=(), mitigations=()):
    nodes = list(nodes)
    counts = {layer: 0 for layer in axe.LAYERS}
    for node in nodes:
        counts[node["layer"]] += 1
    return {
        "nodes": nodes,
        "edges": list(edges),
        "counts": counts,
        "operators": list(operators),
        "sources": list(sources),
        "relations": sorted({e["relation"] for e in edges}),
        "mitigations": list(mitigations),
    }


def _node(layer, text, kind="", status="", is_operator=False, op_type="",
          kind_prop=None, foreign_kinds=None):
    return {
        "layer": layer,
        "labels": [layer],
        "text": text,
        "kind": kind,
        "kind_prop": kind_prop if kind_prop is not None else axe.KIND_PROP.get(layer, ""),
        "foreign_kinds": dict(foreign_kinds or {}),
        "is_operator": is_operator,
        "op_type": op_type,
        "status": status,
        "url": "",
    }


def _gold(entry):
    """A one-input gold document — the frozen file is not mutated by tests."""
    return {"inputs": [entry]}


def test_same_layer_preference_does_not_report_confusion_when_the_item_is_also_right():
    """Catch the instrument's own worst false positive.

    An agent that writes the claim AND an Object naming the same figure filed
    the item on the expected layer. Reporting "layer confusion" because the
    shorter mention sorted first would bury the real finding and make the
    document teach a rule nobody broke.

    The fixture is DISCRIMINATING on purpose (#9695 review P1): the wrong-layer
    Point is an EXACT match (1.0) and the correct-layer Subject only a
    containment match (0.867), so the Point ranks first on score alone and the
    same-layer preference is the only thing that can produce `OK`. The previous
    fixture put both nodes at 0.8 — a tie broken by list order — so the whole
    preference could be DELETED and this test still passed.
    """
    nodes = [
        _node("Point", "Marta Reyes", kind="statement"),
        _node("Subject", "Marta Reyes, CFO", kind="naturalPerson"),
    ]
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Marta Reyes", "layer": "subject", "kind": ["naturalPerson"]}],
        }
    )

    # Non-vacuity: asked for a layer NEITHER node is on, `_match` returns the
    # node the raw ranking put first — the wrong-layer Point, at 1.0. Only the
    # preference can turn that into the Subject.
    ranked_first, score, _ = axe._match("Marta Reyes", nodes, "Event")
    assert (ranked_first["layer"], score) == ("Point", 1.0)
    assert axe._match("Marta Reyes", nodes, "Subject")[0]["layer"] == "Subject"

    result = axe.score(_record(nodes=nodes), gold, "x")
    assert not [
        f for f in result["findings"] if "LAYER confusion" in f["finding"]
    ], result["findings"]
    assert result["item_rows"][0]["verdict"] == "OK"


def test_same_layer_preference_has_a_floor_so_a_weak_mention_cannot_outrank_the_real_match():
    """The bug a fresh-context review found in this instrument (fixed here).

    A same-layer node that shares ONE token must not outrank a different-layer
    containment match: doing so accuses a node the agent never wrote the item as
    and hides the real layer failure. Measured before the floor: the goal
    "sub-5-minute ingestion latency" was attributed to the Object "Ingestion
    pipeline" (1 of 6 tokens) and the layer P0 disappeared.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "sub 5 minute ingestion latency",
                    "layer": "object",
                    "kind": ["goal", "target"],
                }
            ],
        }
    )
    doc = _record(
        nodes=[
            _node("Object", "Ingestion pipeline", kind="workflow"),
            _node(
                "Point",
                "Platform team goal: sub-5-minute end-to-end ingestion latency by 2027-03-31",
                kind="target",
            ),
        ]
    )
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "LAYER"
    assert result["severity_counts"]["P0"] == 1


def test_layer_confusion_is_reported_when_the_item_exists_only_on_another_layer():
    """The failure #9675 exists for: an actor filed as a claim."""
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Marta Reyes", "layer": "subject", "kind": ["naturalPerson"]}],
        }
    )
    doc = _record(nodes=[_node("Point", "Marta Reyes is the new CFO", kind="statement")])
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "LAYER"
    assert result["severity_counts"]["P0"] == 1


def test_pack_declared_kind_is_not_an_undeclared_kind():
    """`disbursement`/`tranche` are declared by the venture pack, not invented.

    The first draft of the instrument imported only the core §5 sets, so every
    write that followed a loaded pack was reported as a failure — the false
    failure that inflated the first draft's undeclared-kind count.
    """
    assert axe.kind_status("Event", "disbursement") == "PACK"
    assert axe.kind_status("Object", "tranche") == "PACK"
    assert axe.kind_status("Point", "statement") == "CORE"
    # `issue` is a LEGACY point spelling the product's own registry accepts (see
    # the test below), so it is NOT the undeclared-kind example any more.
    assert axe.kind_status("Point", "guesstimate") == "UNDECLARED"


def test_the_products_own_registry_is_a_vocabulary_source():
    """`option`/`criterion` are DECLARED — by `tortoise.sdk`'s own register_kind.

    `_vocab()` imported only the §5 canonical sets, so the decide flow's kinds —
    the ones `register_kind("option")` / `register_kind("criterion")` declare and
    `domain_loader.known_kinds("pointKind")` (the hosted write validator) accepts
    — read as UNDECLARED. The document then taught that a decision request's own
    kinds were invented, and the census billed them as failures (#9695 review
    P1, measured on `decision_request`: `option`, `criterion` ×2).

    The answer comes from the product's predicate, never from a second list here.
    """
    from tortoise.domain_loader import known_kinds

    for kind in ("option", "criterion"):
        assert axe.kind_status("Point", kind) == "LEGACY", kind
        assert known_kinds("pointKind") >= {kind}, kind
    # …and the census must not bill them: one Point per declared kind, nothing else.
    gold = _gold({"id": "x", "expect": []})
    doc = _record(
        nodes=[
            _node("Point", "ship on 2026-11-01 or later", kind="option"),
            _node("Point", "cost under 40k", kind="criterion"),
        ]
    )
    result = axe.score(doc, gold, "x")
    assert result["kind_census"] == {}
    assert result["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}, result["findings"]

    # CONTROL — the residue the registry answer must NOT swallow. The legacy
    # bucket is FLAT, so it also accepts kinds that belong to another axis: an
    # occurrence filed as a belief is the failure #9675 quotes, and blessing it
    # as "declared" would blind the census to it.
    assert axe.kind_status("Point", "meeting") == "OTHER-AXIS"
    assert axe.other_axes_declaring("Point", "meeting") == ["Event"]
    assert axe.kind_status("Point", "issue") == "OTHER-AXIS"
    wrong_axis = axe.score(
        _record(nodes=[_node("Point", "pricing review held on tuesday", kind="meeting")]),
        gold,
        "x",
    )
    assert wrong_axis["wrong_axis_kinds"] == [
        "Point 'meeting' is declared for Event"
    ], wrong_axis["wrong_axis_kinds"]
    census_finding = next(
        f for f in wrong_axis["findings"] if f["finding"].startswith("graph-wide")
    )
    assert "declared for another axis" in census_finding["finding"]
    assert "Point 'meeting' is declared for Event" in census_finding["finding"]


def test_the_declared_subject_spelling_is_not_a_failure():
    """`core:organization` is the DECLARED §5 key; the bare form is its alias.

    The first draft hardcoded the bare forms and reported the canonical spelling
    as "an invented namespace prefix" — a rule the product's own vocabulary
    contradicts, taught to the next agent by this very document.
    """
    assert axe.kind_status("Subject", "core:organization") == "CORE"
    assert axe.kind_status("Subject", "organization") == "CORE"
    assert axe.kind_status("Subject", "person") == "UNDECLARED"
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "Acme", "layer": "subject", "kind": ["organization"]}],
        }
    )
    doc = _record(nodes=[_node("Subject", "Acme", kind="core:organization")])
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "OK"


def test_a_mitigation_is_not_scored_when_the_gold_says_nothing_about_it():
    """`expect_mitigation: false` is not a default — it is an assertion.

    Boilerplate `false` on nine inputs turned the research input's sanctioned
    mitigation of an under-evidenced inference into a P0.
    """
    gold = _gold({"id": "x", "expect": []})  # no expect_mitigation key
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by"}],
    )
    result = axe.score(doc, gold, "x")
    assert not [f for f in result["findings"] if "mitigation" in f["finding"]]


def test_a_duplicate_identity_is_measured_not_merely_described():
    """The gold declares `duplicate_expected`; the check must exist and count NODES.

    A claim that merely starts with the company name is a claim ABOUT it — the
    first draft counted those and reported five duplicates on a run that folded
    the alias correctly.
    """
    gold = _gold(
        {
            "id": "x",
            "duplicate_expected": {"of": "Northwind Logistics", "max_nodes": 1},
            "expect": [],
        }
    )
    one = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Point", "Northwind Logistics confirmed the renewal", kind="statement"),
            _node("Source", "Northwind Logistics — signed SOW", kind="document"),
        ]
    )
    assert not [f for f in axe.score(one, gold, "x")["findings"] if "identity" in f["finding"]]
    two = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Object", "Northwind Logistics Ltd", kind="other"),
        ]
    )
    result = axe.score(two, gold, "x")
    assert any("one identity" in f["finding"] for f in result["findings"])


def test_a_duplicate_written_as_a_point_is_still_a_duplicate():
    """The gold's OWN measurable: ">1 node named Northwind means the duplicate
    was written as a Point" (`inputs.json` `notes`).

    The rule filtered the node set to Object/Subject, so the seeded Object plus a
    same-name Point matched nothing and the input built to produce a duplicate
    scored clean — a false PASS on the one number Step 3 reads as its acceptance
    delta (#9695 review P1). `_same_identity` already keeps a claim apart from a
    duplicate, so the layer test was doing no work of its own.
    """
    gold = _gold(
        {
            "id": "x",
            "duplicate_expected": {"of": "Northwind Logistics", "max_nodes": 1},
            "expect": [],
        }
    )
    as_point = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Point", "Northwind Logistics", kind="statement"),
        ]
    )
    findings = axe.score(as_point, gold, "x")["findings"]
    assert any("one identity" in f["finding"] for f in findings), (
        "a duplicate Point is the gold's own measurable and must not read as clean"
    )

    # …and a CLAIM naming the company is still not a duplicate: the predicate, not
    # the layer filter, is what separates them.
    claim = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Point", "Northwind Logistics will renew in January", kind="statement"),
        ]
    )
    assert not [
        f for f in axe.score(claim, gold, "x")["findings"] if "one identity" in f["finding"]
    ]


def test_one_duplicate_identity_is_charged_once_not_twice():
    """ONE ACT, ONE FINDING: the item-level P1 and the graph-level count rule
    both fired for a single duplicate, so a per-input delta reported one
    regression as a P0 *and* a P1 (#9695 review P1).
    """
    gold = _gold(
        {
            "id": "x",
            "duplicate_expected": {"of": "Northwind Logistics", "max_nodes": 1},
            "expect": [
                {
                    "item": "Northwind Logistics",
                    "layer": "object",
                    "kind": ["other", "organization"],
                }
            ],
        }
    )
    doc = _record(
        nodes=[
            _node("Object", "Northwind Logistics", kind="other"),
            _node("Object", "Northwind Logistics Ltd", kind="other"),
        ]
    )
    charges = [
        f
        for f in axe.score(doc, gold, "x")["findings"]
        if "one identity" in f["finding"] or "duplicate nodes" in f["finding"]
    ]
    assert len(charges) == 1, (
        f"one duplicate write must be charged once, got {[f['finding'] for f in charges]}"
    )

    # The item-level rule is NOT dead: on an input that declares no
    # `duplicate_expected`, it is the only thing that sees a duplicate.
    no_rule = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "Northwind Logistics",
                    "layer": "object",
                    "kind": ["other", "organization"],
                }
            ],
        }
    )
    still = axe.score(doc, no_rule, "x")["findings"]
    assert any("duplicate nodes" in f["finding"] for f in still)


def test_undeclared_kind_is_distinguished_from_a_declared_but_wrong_kind():
    """"A kind nothing declares is a kind nothing routes to" (issue step 2).

    Both fail, but they teach different lessons: an invented `person` means the
    vocabulary was never consulted; `observation`-instead-of-`statement` is a
    real canonical kind, just the wrong one.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "Dana", "layer": "subject", "kind": ["naturalPerson"]},
                {"item": "churn figure", "layer": "point", "kind": ["statement"]},
            ],
        }
    )
    doc = _record(
        nodes=[
            _node("Subject", "Dana", kind="person"),
            _node("Point", "churn figure was 4.1%", kind="observation"),
        ]
    )
    result = axe.score(doc, gold, "x")
    kinds = {row["item"]: row["verdict"] for row in result["item_rows"]}
    assert kinds["Dana"] == "KIND-UNDECLARED"
    assert kinds["churn figure"] == "KIND"
    # `observation` is a DECLARED legacy point kind — the finding must say so
    message = next(f["finding"] for f in result["findings"] if "churn figure" in f["finding"])
    assert "declared, but not the kind" in message


def test_a_contradiction_handled_by_mitigation_is_a_P0_even_with_an_operator_present():
    """The pipeline's most expensive confusion: relevance dampened where truth is at issue."""
    gold = _gold({"id": "x", "expect_nand": True, "expect": []})
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by", "from": "op", "to": "claim"}],
    )
    result = axe.score(doc, gold, "x")
    p0 = [f for f in result["findings"] if f["severity"] == "P0"]
    assert any("NO NAND" in f["finding"] for f in p0)


def test_one_write_is_charged_once_when_a_contradiction_is_mitigated_instead_of_nand():
    """One act, one P0.

    A NAND-less contradiction answered with a dampener is a SINGLE mistake. It
    used to be billed twice — a P0 for the missing NAND *and* a P0 for the
    mitigation — so one write counted twice in the aggregate a Step-3 delta is
    read against (#9695 review P1). The dampener must still be named inside the
    finding, and still reported as written.
    """
    gold = _gold({"id": "x", "expect_nand": True, "expect_mitigation": False, "expect": []})
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by", "from": "op", "to": "claim"}],
    )
    result = axe.score(doc, gold, "x")
    truth = [f for f in result["findings"] if f["item"] == "(truth-vs-relevance)"]
    assert len(truth) == 1, [f["finding"] for f in truth]
    assert "mitigations: 1" in truth[0]["finding"]
    assert "relevance was dampened where truth was at issue" in truth[0]["finding"]
    assert result["severity_counts"]["P0"] == 1
    assert result["mitigations"] == ["mitigated_by: op -> claim"]


def test_a_dampener_on_an_input_with_no_contradiction_is_its_own_P0():
    """The genuinely separate case: `expect_mitigation: false` with truth NOT at issue.

    The `elif` in `score()` must still fire when nothing was NAND-less —
    otherwise fixing the double charge would have deleted the rule rather than
    made it disjoint from the missing-NAND rule.
    """
    gold = _gold(
        {"id": "x", "expect_mitigation": False, "expect_nand": False, "expect": []}
    )
    doc = _record(
        nodes=[_node("Point", "", is_operator=True, op_type="IMPL")],
        operators=[{"op_type": "IMPL"}],
        mitigations=[{"relation": "mitigated_by"}],
    )
    result = axe.score(doc, gold, "x")
    truth = [f for f in result["findings"] if f["item"] == "(truth-vs-relevance)"]
    assert len(truth) == 1
    assert "INVALIDATES/mitigation" in truth[0]["finding"]


def test_a_kind_written_on_another_layer_property_is_its_own_finding():
    """`subjectKind` on an Object is invisible to every reader of an Object.

    One cross-property `coalesce` read a foreign kind back as the layer's own
    kind, so the worst version of this write (a legal word on the wrong axis —
    `eventKind: meeting` on an Object) passed, and `KIND_PROP` was declared and
    referenced nowhere (#9695 review P2).
    """
    node = axe._node_from_row(
        ["Object"], "Northwind Logistics", {"subjectKind": "organization"},
        False, "", "", "", "n1",
    )
    assert node["kind"] == "", "a foreign-axis kind must not become the layer's kind"
    assert node["kind_prop"] == "objectKind"
    assert node["foreign_kinds"] == {"subjectKind": "organization"}
    assert node["id"] == "n1"

    gold = _gold(
        {"id": "x", "expect": [{"item": "Northwind Logistics", "layer": "object"}]}
    )
    result = axe.score(_record(nodes=[node]), gold, "x")
    rows = [f for f in result["findings"] if f["item"] == "Northwind Logistics"]
    assert rows, result["findings"]
    assert "CROSS-AXIS" in rows[0]["finding"]
    assert "subjectKind='organization'" in rows[0]["finding"]
    assert result["mitigations"] == []


def test_one_cross_axis_write_is_charged_once_even_when_the_gold_declares_a_kind():
    """One act, one finding — a foreign-axis kind is not also a kind-set miss.

    When a node carries its kind ONLY on another layer's property, its own
    property is empty, so `kind_status("")` is NONE and `""` matches no allowed
    kind. The item-level checks therefore used to fire a SECOND P1 for the same
    write, whose text ("declared, but not the kind this item is", `written ''`)
    is false in its own terms — nothing was written on this layer's property at
    all, which is exactly what the cross-axis finding already said (#9695 review
    P2). The census and `unkinded` already skipped such a node for this reason.

    The gold here DECLARES a kind set (the fixture that could not catch this had
    no `kind`, so the second finding could never fire), and the item-row verdict
    is pinned too, so the double-charge cannot come back unnoticed.
    """
    node = _node("Object", "Northwind Logistics", foreign_kinds={"subjectKind": "organization"})
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "Northwind Logistics", "layer": "object", "kind": ["plan"]}
            ],
        }
    )
    result = axe.score(_record(nodes=[node]), gold, "x")
    item_findings = [f for f in result["findings"] if f["item"] == "Northwind Logistics"]
    assert len(item_findings) == 1, [f["finding"] for f in item_findings]
    assert "CROSS-AXIS" in item_findings[0]["finding"]
    assert "kind outside the frozen set" not in item_findings[0]["finding"]
    assert result["severity_counts"] == {"P0": 0, "P1": 1, "P2": 0}, result["severity_counts"]
    assert result["item_rows"][0]["verdict"] == "KIND-CROSS-AXIS"
    assert len(result["item_rows"]) == 1, "the item row must still be emitted"

    # CONTROL: the guard is scoped to the empty own-property. A node that wrote a
    # declared-but-wrong kind on its OWN property beside a foreign one is a
    # different write, and it must still be charged.
    both = _node(
        "Object", "Northwind Logistics", kind="tool",
        foreign_kinds={"subjectKind": "organization"},
    )
    control = axe.score(_record(nodes=[both]), gold, "x")
    control_findings = [f for f in control["findings"] if f["item"] == "Northwind Logistics"]
    assert len(control_findings) == 2, [f["finding"] for f in control_findings]
    assert any("kind outside the frozen set" in f["finding"] for f in control_findings)


def test_the_graph_wide_census_catches_a_kind_on_the_wrong_axis():
    """An unnamed node's foreign-axis kind is invisible unless the census says so.

    `kind` is empty on the layer's own property, so the undeclared-kind census
    skips it and the unkinded check would describe it as "no kind property" — a
    different (and less true) finding than "you wrote it on the wrong property".
    """
    gold = _gold({"id": "x", "expect": []})
    doc = _record(
        nodes=[
            _node("Object", "Renewal window", foreign_kinds={"eventKind": "meeting"}),
        ]
    )
    result = axe.score(doc, gold, "x")
    census = [f for f in result["findings"] if "cross-axis" in f["finding"]]
    assert census, result["findings"]
    assert "eventKind='meeting'" in census[0]["finding"]
    assert "carry no kind property" not in census[0]["finding"]
    assert result["cross_axis_kinds"] == ["Object 'Renewal window' carries eventKind='meeting'"]


def test_every_layer_reads_its_own_kind_property():
    """The record must say WHICH property carried the kind, per layer."""
    assert set(axe.KIND_PROPS) == set(axe.KIND_PROP.values())
    derived = tuple(axe.KIND_PROP[layer] for layer in axe.LAYERS)
    assert derived == axe.KIND_PROPS, "KIND_PROPS must be derived from KIND_PROP"
    for layer, prop in axe.KIND_PROP.items():
        node = axe._node_from_row([layer], "t", {prop: "x"}, False, "", "", "")
        read = node["kind_prop"], node["kind"], node["foreign_kinds"]
        assert read == (prop, "x", {}), f"{layer} did not read {prop}"
    # infrastructure labels are not knowledge nodes
    assert axe._node_from_row(["Meta"], "t", {}, False, "", "", "") is None


def test_record_reads_the_kind_columns_in_KIND_PROPS_order(monkeypatch):
    """Pin `record()`'s positional column order — a silent mis-zip swaps kinds.

    The five kind properties come back as separate columns now, so one off-by-one
    in the slice would report another layer's kind as this node's. No database:
    the seam is `_rows`.
    """

    class _SDK:
        closed = False

        def close(self):
            self.closed = True

    sdk = _SDK()
    monkeypatch.setattr(axe, "_open", lambda uri: (sdk, object()))
    node_row = (
        ["Object", "Node"], "Northwind ARR", "", "", "other", "", "",
        False, "", "live", "https://example.invalid/nw", "n7",
    )
    edge_row = ("aboutObject", "p", "Northwind ARR", ["Point"], ["Object"], "p1", "n7")
    seen: list[str] = []

    def fake_rows(proj, cypher):
        seen.append(cypher)
        return [edge_row] if "-[e]->" in cypher else [node_row]

    monkeypatch.setattr(axe, "_rows", fake_rows)
    doc = axe.record("memory://x")

    assert sdk.closed, "record() must close the SDK it opened"
    node = doc["nodes"][0]
    assert (node["layer"], node["kind"], node["kind_prop"]) == ("Object", "other", "objectKind")
    assert node["foreign_kinds"] == {}
    assert (node["status"], node["url"], node["id"]) == (
        "live", "https://example.invalid/nw", "n7",
    )
    assert doc["counts"]["Object"] == 1
    assert doc["edges"][0] == {
        "relation": "aboutObject",
        "from": "p",
        "to": "Northwind ARR",
        "from_layer": "Point",
        "to_layer": "Object",
        "from_id": "p1",
        "to_id": "n7",
    }
    for prop in axe.KIND_PROPS:
        assert f"coalesce(n.{prop}" in seen[0], f"{prop} is not a column of the node query"


def test_supersession_must_leave_a_superseded_node_behind():
    gold = _gold({"id": "x", "expect_supersede": "3.2 over 4.1", "expect": []})
    doc = _record(nodes=[_node("Point", "churn 3.2%", kind="statement", status="live")])
    result = axe.score(doc, gold, "x")
    assert any("superseded" in f["finding"] for f in result["findings"])


def test_the_graph_wide_census_reports_what_the_gold_never_names():
    """Deleting the census used to leave every test green while 8 of the findings vanished.

    The census is what keeps a run from reading clean: `score()` audits the nodes
    the gold names, so a gold that happens to match says nothing about the rest of
    the graph.
    """
    gold = _gold(
        {"id": "x", "expect": [{"item": "churn", "layer": "point", "kind": ["statement"]}]}
    )
    doc = _record(
        nodes=[
            _node("Point", "churn was 3.2%", kind="statement"),
            _node("Point", "dashboard double-counted an account", kind="guesstimate"),
        ]
    )
    result = axe.score(doc, gold, "x")
    census = [f for f in result["findings"] if f["finding"].startswith("graph-wide")]
    assert len(census) == 1
    assert result["kind_census"] == {"Point": ["guesstimate"]}
    # the negative control: a kind already reported against a gold ITEM is not
    # counted a second time by the census
    gold2 = _gold(
        {"id": "x", "expect": [{"item": "churn", "layer": "point", "kind": ["statement"]}]}
    )
    doc2 = _record(nodes=[_node("Point", "churn was 3.2%", kind="incident")])
    result2 = axe.score(doc2, gold2, "x")
    assert result2["kind_census"] == {}


def test_two_homes_is_a_P1_and_absence_is_a_P0():
    """A review found three P0s accusing an absence the graph disproved.

    When the expected layer ALSO carries the item, the finding is "one fact, two
    layers" — weaker than an absence, and differently instructive — so the
    instrument must say which of the two it is.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "marta reyes joined northwind logistics", "layer": "event",
                 "kind": ["roleCreated", "occurrence"]},
                {"item": "tobias lund", "layer": "subject", "kind": ["naturalPerson"]},
            ],
        }
    )
    two_homes = _record(
        nodes=[
            _node(
                "Event",
                "Marta Reyes joins Northwind Logistics as its new Chief Financial "
                "Officer this week, the company confirmed on Tuesday",
                kind="occurrence",
            ),
            # the exact-name Point wins `_match`; the Event still carries the item
            _node("Point", "marta reyes joined northwind logistics", kind="statement"),
            _node("Point", "Tobias Lund is the CEO", kind="statement"),
        ]
    )
    result = axe.score(two_homes, gold, "x")
    verdicts = {row["item"]: row["verdict"] for row in result["item_rows"]}
    assert verdicts["marta reyes joined northwind logistics"] == "TWO-HOMES"
    assert verdicts["tobias lund"] == "LAYER"


def test_the_stemmer_is_what_keeps_tense_from_reading_as_absence():
    """Removing `_stem` used to leave every test green while three findings moved.

    With the stemmer disabled, `duplicate_entity` loses its Event ("renew" vs
    "renewal") and reads as a LAYER P0 — one of the three false P0s a review
    found. This pins the mechanism, not just the two-homes branch.
    """
    assert axe._tokens("renewal") == axe._tokens("renew")
    assert axe._tokens("joins") == axe._tokens("joined") == axe._tokens("join")
    assert axe._tokens("3 engineer-weeks") == {"engineer", "week"}
    # The `-e` noun class: `es` strips before the word's own trailing `e`, so
    # `values` stemmed to `valu` while `value` stayed `value` and the two shared
    # no token at all — a fully-written item could then fall under the 0.5 floor
    # and be reported P0 ABSENT (#9695 review P1).
    for singular, plural in (
        ("value", "values"),
        ("estimate", "estimates"),
        ("deadline", "deadlines"),
        ("slide", "slides"),
        ("principle", "principles"),
    ):
        assert axe._tokens(singular) == axe._tokens(plural), (singular, plural)
    # …and it must not inflate either side's token COUNT, which would silently
    # move every partial-overlap ratio in the report.
    assert len(axe._tokens("estimate for the quarter")) == 2
    gold = _gold(
        {
            "id": "x",
            "expect": [{"item": "renew in january", "layer": "event", "kind": ["occurrence"]}],
        }
    )
    doc = _record(
        nodes=[_node("Event", "Northwind Logistics confirmed January renewal", kind="occurrence")]
    )
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "OK", (
        "a singular/plural difference must not read as an absent item"
    )


def test_an_e_final_plural_is_not_an_absent_item():
    """`estimates` vs `Estimate` used to mint the harshest verdict there is.

    The item and the node below ARE the same fact; only the number differs. With
    the `-e` stem split, the shared content word contributed zero overlap, the
    match fell under the 0.5 acceptance floor, and `score()` emitted a P0 "no
    node represents it" — a false absence on a correct write (#9695 review P1).
    """
    gold = _gold(
        {"id": "x", "expect": [{"item": "estimates for the quarter", "layer": "point"}]}
    )
    doc = _record(
        nodes=[_node("Point", "Estimate for the quarter is 3 engineer weeks", kind="statement")]
    )
    result = axe.score(doc, gold, "x")
    assert result["item_rows"][0]["verdict"] == "OK", result["findings"]
    assert result["severity_counts"] == {"P0": 0, "P1": 0, "P2": 0}


def test_one_unkinded_gold_named_write_is_charged_once_with_honest_text():
    """One act, one finding — and the finding must not describe the opposite act.

    A gold item resolving to a node whose own kind property is EMPTY fell into
    the "kind outside the frozen set" branch, whose text says "declared, but not
    the kind this item is" — false in its own terms, since nothing was declared
    or written — AND `unkinded` charged the same node a second time. The
    cross-axis twin of this bug was fixed in round 6; this is the plain case
    (#9695 review P1/P2).
    """
    gold = _gold(
        {"id": "x", "expect": [{"item": "Dana", "layer": "subject", "kind": ["naturalPerson"]}]}
    )
    result = axe.score(_record(nodes=[_node("Subject", "Dana", kind="")]), gold, "x")
    findings = [f for f in result["findings"] if f["item"] == "Dana"]
    assert len(findings) == 1, [f["finding"] for f in findings]
    assert "no kind written on subjectKind" in findings[0]["finding"]
    assert "declared, but not the kind" not in findings[0]["finding"]
    assert not any("carry no kind property" in f["finding"] for f in result["findings"]), (
        "the same write was billed twice"
    )
    assert result["severity_counts"] == {"P0": 0, "P1": 1, "P2": 0}
    assert result["item_rows"][0]["verdict"] == "KIND-MISSING"

    # CONTROL: the graph-wide rule still sees an unkinded node the gold does NOT
    # name — the skip is keyed on the charge, not on the gold naming the node.
    control = axe.score(
        _record(nodes=[_node("Subject", "Dana", kind=""), _node("Object", "Renewal", kind="")]),
        gold,
        "x",
    )
    assert any("carry no kind property" in f["finding"] for f in control["findings"]), (
        control["findings"]
    )


def test_one_node_carrying_both_an_undeclared_and_a_foreign_kind_reports_both():
    """The census must not let a foreign-axis kind hide the node's OWN kind.

    Item level already charges both (the declared-kinds control in
    `test_one_cross_axis_write_is_charged_once…`), but the census `continue`d on
    `foreign_kinds`, so a node carrying `pointKind: guesstimate` AND
    `subjectKind: person` reported only the cross-axis half — a false negative in
    the measurement (#9695 review P2).
    """
    gold = _gold({"id": "x", "expect": []})
    node = _node(
        "Point", "the tier is being retired", kind="guesstimate",
        foreign_kinds={"subjectKind": "person"},
    )
    result = axe.score(_record(nodes=[node]), gold, "x")
    assert result["kind_census"] == {"Point": ["guesstimate"]}, result["kind_census"]
    assert len(result["cross_axis_kinds"]) == 1
    census = next(f for f in result["findings"] if f["finding"].startswith("graph-wide"))
    assert "guesstimate" in census["finding"]
    assert "subjectKind='person'" in census["finding"]


def test_a_shared_name_prefix_is_not_one_identity():
    """`Acme` and `Acme Capital` are two companies, not a duplicate-identity P1.

    `_match` tested `startswith` on the normalised name, so any two entities
    sharing a name prefix read as "duplicate nodes for one identity" — on a
    portfolio/company input that is the ordinary case. `_same_identity` already
    had the word boundary (a legal-form suffix is the only thing that may
    differ); the two halves of one instrument now agree (#9695 review P2).
    """
    gold = _gold({"id": "x", "expect": [{"item": "acme", "layer": "subject"}]})
    result = axe.score(
        _record(nodes=[_node("Subject", "Acme Capital", kind="company"),
                       _node("Subject", "Acme", kind="company")]),
        gold,
        "x",
    )
    assert not any("duplicate nodes for one identity" in f["finding"] for f in result["findings"]), (
        result["findings"]
    )

    # CONTROL: the legal-form case IS one identity, and it must still be caught.
    control = axe.score(
        _record(nodes=[_node("Subject", "Northwind Logistics", kind="company"),
                       _node("Subject", "Northwind Logistics Ltd", kind="company")]),
        _gold({"id": "x", "expect": [{"item": "northwind logistics", "layer": "subject"}]}),
        "x",
    )
    assert any("duplicate nodes for one identity" in f["finding"] for f in control["findings"]), (
        control["findings"]
    )


def test_a_tie_between_two_candidates_is_resolved_by_the_same_key_either_way():
    """A tie must not be decided by list order (the record query has no ORDER BY).

    `_match` sorted on the score alone, so two equally-scored nodes swapped the
    winner when a live run's row order changed — and the winner picks the layer
    the verdict is computed from, which injects a verdict change with no
    behaviour change behind it. Step 3's acceptance is exactly that per-input
    delta (#9695 review P2).
    """
    gold = _gold({"id": "x", "expect": [{"item": "churn", "layer": "point", "kind": ["statement"]}]})
    # Two same-layer candidates at the SAME containment score (one shared stem of
    # a two-stem node either way), carrying different kinds — so whichever wins
    # decides the verdict, and the two orders used to disagree.
    first = _node("Point", "churn alpha", kind="guesstimate")
    second = _node("Point", "churn beta", kind="statement")
    forward = axe.score(_record(nodes=[first, second]), gold, "x")
    backward = axe.score(_record(nodes=[second, first]), gold, "x")
    assert forward["item_rows"][0]["verdict"] == backward["item_rows"][0]["verdict"], (
        forward["item_rows"][0], backward["item_rows"][0]
    )
    assert forward["item_rows"][0]["verdict"] == "OK", forward["findings"]


def test_the_gold_file_does_not_carry_boilerplate_operator_rules():
    """`expect_mitigation: false` on nine inputs was the bug, not the fix.

    The code path was pinned; the ARTIFACT it was fixed in was not, so
    re-adding the boilerplate left every test green while turning the research
    input's sanctioned mitigation into a P0.
    """
    gold = json.loads(GOLD.read_text())
    forbidden = [e["id"] for e in gold["inputs"] if e.get("expect_mitigation") is False]
    assert forbidden == ["contradicting_claim"], (
        "a dampener may only be forbidden where the gold argues truth is at issue; "
        f"boilerplate found on {forbidden}"
    )
    missing_nand = [e["id"] for e in gold["inputs"] if "expect_nand" not in e]
    assert not missing_nand, f"every input states whether a NAND is expected: {missing_nand}"


def test_every_committed_run_reproduces_from_its_own_graph():
    """The single gate that keeps the report honest.

    Re-scores each committed `graph.json` and demands the committed `score.json`
    back, field for field — which pins the matcher's thresholds (including the
    two-thirds two-homes bar), the tie-breaks, the class rules, and the link
    between a record's verdicts and its own findings list. The ONE excluded field
    is `packs` (the vocabulary the run was scored under), asserted on its own
    below so a real vocabulary-driven verdict change still reds while a pure
    pack-list delta does not. Without this gate, a compensated pair of
    edits to two records leaves the totals intact while the report's per-input
    table contradicts them, and loosening the two-homes bar from two thirds to a
    third left all 25 tests green.
    """
    gold = json.loads(GOLD.read_text())
    runs = sorted((GOLD.parent / "runs").glob("*/score.json"))
    assert len(runs) == 10
    for path in runs:
        committed = json.loads(path.read_text())
        graph = json.loads((path.parent / "graph.json").read_text())
        recomputed = axe.score(graph, gold, path.parent.name)
        # `packs` is the vocabulary the run was scored UNDER, recorded as
        # provenance — a repo-global constant, not a verdict about the run. It is
        # deliberately EXCLUDED from the equality: including it meant any PR that
        # added a pack anywhere (a supported extension point, unrelated to this
        # eval) reddened this `core` test on main and forced a rewrite of ten
        # committed evidence files. That already happened once inside this PR
        # (`origin/main` added the `portfolio` pack, #9694, and every verdict was
        # byte-identical). The provenance is asserted separately, below.
        live = {k: v for k, v in recomputed.items() if k != "packs"}
        stored = {k: v for k, v in committed.items() if k != "packs"}
        if live != stored:
            # A red here has TWO possible causes and they are not the same news:
            # the instrument's rules changed (the evidence must be re-derived) or
            # the PRODUCT's kind vocabulary moved under a committed record (a pack
            # or registry change, unrelated to this eval — re-score with
            # `score --graph`, nothing was wrong before). `kind_sources` is the
            # record's own frozen answer, so the second case is detectable and
            # must not be reported as "your evidence drifted" (#9695 review P2).
            moved = {
                layer: {
                    kind: (was, axe.kind_status(layer, kind))
                    for kind, was in kinds.items()
                    if axe.kind_status(layer, kind) != was
                }
                for layer, kinds in (committed.get("kind_sources") or {}).items()
            }
            moved = {layer: kinds for layer, kinds in moved.items() if kinds}
            assert not moved, (
                f"{path.parent.name}: the product's KIND VOCABULARY moved under this "
                f"committed record — {moved} (was, now). This is not evidence drift: "
                f"re-score the ten records with `score --graph` and update the doc's "
                f"counts, or pin the vocabulary if the move is a bug"
            )
            assert live == stored, (
                f"{path.parent.name}: re-scoring its graph.json does not reproduce the "
                f"committed verdicts — the report and the record have diverged"
            )
        assert committed["packs"], f"{path.parent.name}: the score record lost its vocabulary"
        assert committed["packs"] == sorted(committed["packs"]), (
            f"{path.parent.name}: the recorded pack set is not in its canonical order"
        )
        # A record's own severity counts must follow from its own findings.
        counted = {
            sev: sum(1 for f in committed["findings"] if f["severity"] == sev)
            for sev in ("P0", "P1", "P2")
        }
        assert committed["severity_counts"] == counted, (
            f"{path.parent.name}: severity_counts disagree with its findings list"
        )


def test_the_two_homes_bar_is_two_thirds_not_a_third():
    """The cycle-3 regression as a test.

    At a third, the meeting SESSION event ("Pricing & self-serve decision
    meeting", 2 of 4 stems) stood in for the kill decision and a real absence
    became the weaker two-homes finding.
    """
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {"item": "killing the self-serve tier", "layer": "event", "kind": ["decision"]}
            ],
        }
    )
    half = _record(
        nodes=[
            _node("Event", "Pricing & self-serve decision meeting", kind="meeting"),
            _node("Point", "The self-serve tier is being killed", kind="decision"),
        ]
    )
    result = axe.score(half, gold, "x")
    assert result["item_rows"][0]["verdict"] == "LAYER", (
        "an Event sharing two of four topic stems must not count as the item's home"
    )
    two_thirds = _record(
        nodes=[
            _node(
                "Event",
                "Decision recorded by the chief executive at the weekly pricing "
                "review: kill the self-serve tier, effective Q4, with the "
                "migration comms owned by product",
                kind="decision",
            ),
            _node("Point", "The self-serve tier is being killed", kind="decision"),
        ]
    )
    result2 = axe.score(two_thirds, gold, "x")
    assert result2["item_rows"][0]["verdict"] == "TWO-HOMES"


def test_the_incremental_inputs_all_state_their_before_graph():
    """A Step-3 re-run driven from the gold alone must know what to seed.

    `product_spec_object` depends on a pre-existing plan Object and was the one
    incremental input whose gold carried no `setup` note.

    The note must also match the ONLY fixture that builds that graph: it said
    `objectKind organization` while `setup.py` seeds `other`, so a lane that
    rebuilt the before-graph from the gold would seed a different node than the
    measured run had (#9695 review P2).
    """
    gold = json.loads(GOLD.read_text())
    seeded = [e["id"] for e in gold["inputs"] if e.get("setup")]
    assert seeded == [
        "correction_existing_claim",
        "duplicate_entity",
        "contradicting_claim",
        "product_spec_object",
    ], f"incremental inputs must state their before-graph: {seeded}"
    note = next(e["setup"] for e in gold["inputs"] if e["id"] == "duplicate_entity")
    setup_src = (GOLD.parent / "setup.py").read_text()
    seeded_kind = re.search(
        r'create_object\("Northwind Logistics", objectKind="([\w:]+)"\)', setup_src
    )
    assert seeded_kind, "the duplicate_entity fixture no longer seeds that Object"
    assert f"objectKind {seeded_kind.group(1)}" in note, (
        f"the gold's before-state note disagrees with setup.py: {note!r}"
    )


def test_fresh_refuses_a_mistyped_directory_and_deletes_nothing(tmp_path):
    """#9695 P1: `--fresh` must refuse a typed path it cannot positively identify.

    An embedded graph is always a FILE (`tortoise/config.py`: TORTOISE_DB_PATH is
    "file path (canonical for embedded)"), so a directory is the mistyped-`--db`
    shape — and the doc's reproduce block shows `--db <path> --fresh` side by
    side, inviting exactly that copy-edit. Before this guard the deletion branch
    was `shutil.rmtree(p) if p.is_dir() else p.unlink()`, i.e. an operator typo
    deleted an arbitrary tree with no confirmation and no ownership check.

    This test fails on that code: the sentinel is gone and no SystemExit is
    raised, so it cannot regress into a silent `rmtree` again.
    """
    mistaken = tmp_path / "ax-eval"  # a directory: `--db` typed one level short
    mistaken.mkdir()
    sentinel = mistaken / "contradicting_claim.db"
    sentinel.write_text("a graph — or anything else its owner cannot recreate")

    with pytest.raises(SystemExit) as exc:
        step1_setup.prepare(str(mistaken), "contradicting_claim", fresh=True)

    assert "refusing --fresh" in str(exc.value), exc.value
    assert sentinel.read_text() == (
        "a graph — or anything else its owner cannot recreate"
    ), "the guard refused AFTER deleting inside the directory"
    assert not list(mistaken.glob("*" + step1_setup.MARKER_SUFFIX))


def test_fresh_refuses_a_graph_file_this_tool_did_not_create(tmp_path):
    """A file alone is not proof of ownership: the marker is the allow-list.

    Deleting on "the operator typed a path" is the class of bug that costs a
    database, so an unmarked file must survive `--fresh` untouched.
    """
    foreign = tmp_path / "torchvision-cache.db"
    foreign.write_text("someone else's artifact at a path that looks like a db")

    with pytest.raises(SystemExit) as exc:
        step1_setup.prepare(str(foreign), "duplicate_entity", fresh=True)

    assert "marker" in str(exc.value), exc.value
    assert foreign.exists(), "an unmarked file was deleted by --fresh"


def test_fresh_deletes_the_graph_this_tool_claimed(tmp_path):
    """The guard must not disable the feature: our own db is still replaceable."""
    db = tmp_path / "duplicate_entity.db"
    step1_setup.prepare(str(db), "duplicate_entity", fresh=False)
    claim = json.loads(step1_setup.marker_path(db).read_text())
    assert claim["tool"] == step1_setup.TOOL_ID
    assert claim["db"] == str(db)
    assert claim["input"] == "duplicate_entity"
    db.write_text("the seeded before-graph")

    step1_setup.prepare(str(db), "duplicate_entity", fresh=True)

    assert not db.exists(), "--fresh no longer deletes the tool's own graph"
    assert step1_setup.marker_path(db).is_file(), (
        "the re-run must re-claim the path so the next --fresh can identify it"
    )


def test_fresh_refuses_a_marker_that_names_another_artifact(tmp_path):
    """A copied marker is not ownership: it must name THIS path and THIS tool."""
    elsewhere = tmp_path / "elsewhere.db"
    elsewhere.write_text("x")
    db = tmp_path / "decision_request.db"
    db.write_text("x")
    step1_setup.marker_path(db).write_text(
        json.dumps({"tool": step1_setup.TOOL_ID, "db": str(elsewhere)})
    )

    with pytest.raises(SystemExit) as exc:
        step1_setup.prepare(str(db), "decision_request", fresh=True)

    assert "another artifact" in str(exc.value), exc.value
    assert db.exists(), "a marker naming another path authorised the deletion"


def test_fresh_refuses_a_symlink_so_the_typed_path_is_the_deleted_path(tmp_path):
    """`--db link.db` must not delete whatever the link resolves to."""
    real = tmp_path / "real.db"
    step1_setup.prepare(str(real), "duplicate_entity", fresh=False)
    real.write_text("keep me")
    link = tmp_path / "link.db"
    link.symlink_to(real)

    with pytest.raises(SystemExit) as exc:
        step1_setup.prepare(str(link), "duplicate_entity", fresh=True)

    assert "symlink" in str(exc.value), exc.value
    assert real.read_text() == "keep me"


def test_a_uri_db_never_takes_the_destructive_path(tmp_path, monkeypatch):
    """A docker:// URI names a server, not a local artifact — nothing to delete.

    "Nothing to delete" is preserved by REFUSING, not by returning silently: a
    silent return left `--fresh` a no-op that then seeded on top of the existing
    graph, so a re-run's before-graph carried the fixture's own duplicate and the
    eval charged it to the agent as its write failure (#9695 review P1).
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as refused:
        step1_setup.prepare(
            "docker://:pw@localhost:6379/eval", "duplicate_entity", fresh=True
        )
    assert "refusing --fresh" in str(refused.value)
    # the refusal must still be a pure read: no marker, no db file, nothing written
    assert list(tmp_path.iterdir()) == []


def test_a_uri_db_without_fresh_is_still_a_no_op(tmp_path, monkeypatch):
    """Only the DESTRUCTIVE flag refuses; a plain URI seed is unchanged."""
    monkeypatch.chdir(tmp_path)
    step1_setup.prepare(
        "docker://:pw@localhost:6379/eval", "duplicate_entity", fresh=False
    )
    assert list(tmp_path.iterdir()) == []


def test_the_committed_gold_file_has_ten_inputs_with_pairwise_confusions():
    """Step 1's own contract: ten representative inputs, each naming the ADJACENT pair."""
    gold = json.loads(GOLD.read_text())
    assert len(gold["inputs"]) == 10
    assert len({entry["id"] for entry in gold["inputs"]}) == 10
    cells = {entry["cell"] for entry in gold["inputs"]}
    assert {"portfolio update", "meeting transcript", "research paper"} <= cells
    for entry in gold["inputs"]:
        assert entry["confuses"], f"{entry['id']} names no adjacent-layer confusion"
        assert entry["expect"], f"{entry['id']} has no frozen gold structure"


def test_every_committed_run_record_names_its_input_and_its_writes():
    """The records are the evidence: an unreadable one silently disarms Step 3's delta."""
    runs = sorted((GOLD.parent / "runs").glob("*/score.json"))
    assert len(runs) == 10, f"expected 10 committed run records, found {len(runs)}"
    for path in runs:
        record = json.loads(path.read_text())
        assert record["input"] == path.parent.name
        assert set(record) >= {
            "counts",
            "item_rows",
            "findings",
            "severity_counts",
            "kind_census",
            "packs",
        }
        # a verdict without its evidence cannot be audited
        graph = json.loads((path.parent / "graph.json").read_text())
        assert set(graph) >= {"nodes", "edges", "counts"}
        assert graph["counts"] == record["counts"], (
            f"{path.parent.name}: the verdict's counts disagree with the graph "
            f"they were read from"
        )


def test_the_reported_totals_match_the_committed_records():
    """The report's headline must be a function of the records, not of memory.

    A reviewer found the first draft's totals wrong; this pin is what makes the
    next stale number a red test instead of a claim. The P0 count moved 14 -> 13
    when one write stopped being charged twice (#9695 review P1: a NAND-less
    contradiction answered with a dampener was a P0 for the missing NAND *and* a
    P0 for the mitigation).
    """
    totals = {"P0": 0, "P1": 0}
    with_p0 = 0
    for path in sorted((GOLD.parent / "runs").glob("*/score.json")):
        record = json.loads(path.read_text())
        totals["P0"] += record["severity_counts"]["P0"]
        totals["P1"] += record["severity_counts"]["P1"]
        with_p0 += 1 if record["severity_counts"]["P0"] else 0
    assert totals == {"P0": 13, "P1": 31}, f"report claims 13/31, records say {totals}"
    assert with_p0 == 6, f"report claims 6 of 10 inputs carry a P0, records say {with_p0}"
    doc = (REPO_ROOT / "docs" / "ax" / "how-to-use-tortoise-step1-eval.md").read_text()
    assert "13 P0 and 31 P1" in doc, "the evidence doc's headline drifted from the records"

    # PER-INPUT pin. The totals and the class aggregates cannot see a COMPENSATED
    # pair of record edits (one input gaining a finding as another loses one), so
    # the report's per-input table could contradict the records with the suite
    # green. This is the table, value by value — and each row is grepped out of the
    # doc so a stale cell is a red test rather than a reading exercise.
    per_input = {
        record["input"]: (
            record["severity_counts"]["P0"],
            record["severity_counts"]["P1"],
        )
        for record in (
            json.loads(path.read_text())
            for path in sorted((GOLD.parent / "runs").glob("*/score.json"))
        )
    }
    assert per_input == {
        "portfolio_update": (2, 5),
        "meeting_transcript": (2, 7),
        "research_paper": (0, 4),
        "decision_request": (3, 1),
        "correction_existing_claim": (0, 1),
        "duplicate_entity": (0, 3),
        "regulatory_filing": (0, 2),
        "person_appointment": (2, 4),
        "contradicting_claim": (2, 2),
        "product_spec_object": (2, 2),
    }, f"the report's per-input table drifted from the records: {per_input}"
    cells = {
        "portfolio_update": (1, "portfolio update"),
        "meeting_transcript": (2, "meeting transcript"),
        "research_paper": (3, "research paper"),
        "decision_request": (4, "decision request"),
        "correction_existing_claim": (5, "correction to a claim"),
        "duplicate_entity": (6, "duplicate entity"),
        "regulatory_filing": (7, "regulatory filing"),
        "person_appointment": (8, "person appointment"),
        "contradicting_claim": (9, "contradicting claim"),
        "product_spec_object": (10, "product spec"),
    }
    for input_id, (row_index, cell) in cells.items():
        p0, p1 = per_input[input_id]
        expected_row = f"| {row_index} | {cell} | {p0} | {p1} |"
        assert expected_row in doc, (
            f"the doc's table row for {input_id} is not {expected_row!r} — the "
            f"report and the records disagree"
        )
    assert "6 of 10 inputs" in doc
    index = (REPO_ROOT / "docs" / "00_index.md").read_text()
    assert "13 P0 / 31 P1" in index, "the docs index headline drifted from the records"


def test_the_documented_failure_classes_reconcile_with_the_committed_records():
    """The class table must be derivable from the `score.json` files alone.

    Two reviews caught the class counts contradicting their own records ("1
    input, 2 findings" for a class the records carried three of, a research-paper
    row silent about the P0 it had, an enumeration summing to 12 for a total of
    14). Counting every class here — from the committed verdicts, by the wording
    the verdicts themselves use — is what makes the next stale number a red test.
    """
    runs = [
        json.loads(path.read_text())
        for path in sorted((GOLD.parent / "runs").glob("*/score.json"))
    ]

    def count(needle: str) -> tuple[int, int]:
        hits = [
            (record["input"], f)
            for record in runs
            for f in record["findings"]
            if needle in f["finding"] or needle == f["item"]
        ]
        return len(hits), len({name for name, _ in hits})

    classes = {
        "UNDECLARED kind": (14, 5),  # a kind the product does not know, on a gold item
        "graph-wide": (7, 7),  # the census beyond the gold
        "LAYER confusion": (9, 5),
        "two homes:": (2, 2),
        "kind outside the frozen set": (7, 5),
        "no node represents it": (2, 2),
        "EDGE-LAYER confusion": (0, 0),  # an edge item is graded on both ends
        "a NAND was written where nothing contradicts": (1, 1),
        "(provenance)": (1, 1),
        "nodes carry one identity": (0, 0),
        "INVALIDATES/mitigation": (0, 0),  # truth-vs-relevance is charged once
    }
    measured = {needle: count(needle) for needle in classes}
    assert measured == classes, measured

    # One P0 for the one contradiction: the dampener is named INSIDE it, so the
    # record carries exactly one truth-vs-relevance P0 for `contradicting_claim`.
    contradicting = next(r for r in runs if r["input"] == "contradicting_claim")
    truth_p0 = [
        f
        for f in contradicting["findings"]
        if f["item"] == "(truth-vs-relevance)" and f["severity"] == "P0"
    ]
    assert len(truth_p0) == 1, [f["finding"] for f in truth_p0]
    assert "mitigations: 1" in truth_p0[0]["finding"]

    doc = (REPO_ROOT / "docs" / "ax" / "how-to-use-tortoise-step1-eval.md").read_text()
    for claim in (
        "14 scored items across 5 inputs",
        "9 items across 5 inputs",
        "2 items across 2 inputs",
        "1 input, 1 finding",
    ):
        assert claim in doc, f"the class table no longer states {claim!r}"

    # The census ENUMERATION is prose, so its `×N` multipliers drifted unpinned
    # once — `role` ×2 beside three `role` Objects in the committed graph (#9695
    # review). Pin every `kind` ×N the doc states to the node count in the
    # committed graphs, so the next stale multiplier is a red test.
    kind_totals: dict[str, int] = {}
    for graph_path in sorted((GOLD.parent / "runs").glob("*/graph.json")):
        for node in json.loads(graph_path.read_text())["nodes"]:
            kind = (node.get("kind") or "").lower()
            if kind:
                kind_totals[kind] = kind_totals.get(kind, 0) + 1
    multipliers = re.findall(r"`([\w:]+)` ×(\d+)", doc)
    assert multipliers, "the census enumeration vanished — nothing left to check"
    for kind, stated in multipliers:
        assert kind_totals.get(kind) == int(stated), (
            f"the doc states `{kind}` ×{stated}, the committed graphs carry "
            f"{kind_totals.get(kind)}"
        )


def test_the_cli_re_scores_a_committed_graph_without_a_database(tmp_path):
    """`score --graph` is the audit path the doc tells a reader to use.

    The seam is the CLI entry point itself, not `score()`: the doc prints this
    command, and an audit command that asks for a database it does not have is
    not an audit path.
    """
    run_dir = GOLD.parent / "runs" / "contradicting_claim"
    out = tmp_path / "rescore"
    assert axe.main(
        ["score", "--graph", str(run_dir / "graph.json"), "--id", "contradicting_claim",
         "--out", str(out)]
    ) == 1  # that run carries P0s, so the exit status is non-zero by contract
    rescored = json.loads((out / "score.json").read_text())
    committed = json.loads((run_dir / "score.json").read_text())
    # `packs` is the vocabulary the run was scored UNDER — a repo-global constant,
    # not a verdict about the run. Comparing it here would red this `core` test on
    # any PR that adds a pack anywhere (the same coupling
    # `test_every_committed_run_reproduces_from_its_own_graph` excludes it for, and
    # the reason ten evidence files once had to be rewritten for #9694). The
    # verdicts are what this audit path claims to reproduce; `packs` is pinned per
    # record by the reproduction gate.
    assert {k: v for k, v in rescored.items() if k != "packs"} == {
        k: v for k, v in committed.items() if k != "packs"
    }
    assert rescored["packs"] == sorted(rescored["packs"])
    assert json.loads((out / "graph.json").read_text()) == json.loads(
        (run_dir / "graph.json").read_text()
    )


def test_score_refuses_an_ambiguous_or_missing_source():
    """Exactly one of --db / --graph, or the run says so instead of guessing."""
    with pytest.raises(SystemExit):
        axe.main(["score", "--id", "contradicting_claim"])


@pytest.mark.parametrize("layer", axe.LAYERS)
def test_vocabulary_loads_for_every_layer(layer):
    """The declared vocabularies are imported, not re-spelled, so they cannot drift."""
    vocab = axe._vocab()[layer]
    assert vocab, f"no declared {layer} vocabulary — UNDECLARED detection would be blind"


@pytest.mark.parametrize("kind", ["core:organization", "core:team", "core:naturalPerson"])
def test_the_subject_vocabulary_is_the_products_own(kind):
    """Pinned against `subject_binding`, not against the six literals this tool once held."""
    from tortoise.subject_binding import is_subject_kind

    assert axe.kind_status("Subject", kind) == "CORE"
    assert is_subject_kind(kind)


def _edge(relation, from_layer, to_layer, from_text="plan v2", to_text="platform team"):
    return {
        "relation": relation,
        "from": from_text,
        "to": to_text,
        "from_layer": from_layer,
        "to_layer": to_layer,
        "from_id": "a",
        "to_id": "b",
    }


def test_an_edge_item_wired_between_the_wrong_layers_is_not_ok():
    """Catch a false PASS in the one number Step 3 reads as its delta.

    The gold item says `ownedBy` is "an EDGE to a Subject"; the graph wires it
    between two Points. Grading that by relation NAME answered `OK` for a write
    that says the opposite of what the item is about, and the whole branch had
    no fixture at all (#9695 review P1). The endpoint constraint is stated by
    the gold — its `why` always said it — so it is now scored.
    """
    record = _record(
        nodes=[_node("Point", "plan v2", kind="plan"), _node("Point", "platform team")],
        edges=[_edge("ownedBy", "Point", "Point")],
    )
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "owned by",
                    "layer": "edge",
                    "kind": ["ownedBy"],
                    "endpoints": {"to": ["Subject"]},
                    "why": "§3.5-3.6 — responsibility is an EDGE to a Subject",
                }
            ],
        }
    )
    verdict = axe.score(record, gold, "x")
    assert verdict["item_rows"][0]["verdict"] == "EDGE-LAYER", verdict["item_rows"]
    (finding,) = [f for f in verdict["findings"] if f["item"] == "owned by"]
    assert finding["severity"] == "P0"
    assert "EDGE-LAYER confusion" in finding["finding"]
    assert "Point -> Point" in finding["finding"]


def test_an_edge_item_with_the_endpoints_the_gold_names_is_ok():
    """The control: the same gold, the same relation, wired the way §3 describes."""
    record = _record(
        nodes=[_node("Object", "plan v2", kind="plan"), _node("Subject", "platform team")],
        edges=[_edge("ownedBy", "Object", "Subject")],
    )
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "owned by",
                    "layer": "edge",
                    "kind": ["ownedBy"],
                    "endpoints": {"to": ["Subject"]},
                    "why": "§3.5-3.6 — responsibility is an EDGE to a Subject",
                }
            ],
        }
    )
    verdict = axe.score(record, gold, "x")
    assert verdict["item_rows"][0]["verdict"] == "OK"
    # no item-level finding: the ONLY finding is the graph-wide census, which is
    # about the fixture's unkinded nodes and not about this edge.
    assert verdict["severity_counts"]["P0"] == 0
    assert not [f for f in verdict["findings"] if f["item"] == "owned by"]


def test_an_edge_item_that_does_not_declare_its_endpoints_is_refused():
    """A gold that states only a relation name cannot be graded honestly.

    Left as a warning at most, the constraint could silently disappear the way
    it did here: the item is graded, the answer is `OK`, and the layer the item
    is ABOUT is never checked. Refusing to answer is the honest failure.
    """
    record = _record(
        nodes=[_node("Object", "plan v2", kind="plan"), _node("Subject", "platform team")],
        edges=[_edge("ownedBy", "Object", "Subject")],
    )
    gold = _gold(
        {
            "id": "x",
            "expect": [
                {
                    "item": "owned by",
                    "layer": "edge",
                    "kind": ["ownedBy"],
                    "why": "§3.5-3.6 — responsibility is an EDGE to a Subject",
                }
            ],
        }
    )
    with pytest.raises(SystemExit) as exc:
        axe.score(record, gold, "x")
    assert "endpoints" in str(exc.value)


def test_every_committed_edge_item_declares_the_endpoints_it_is_graded_on():
    """The gold is the reference standard, so an edge item states BOTH ends.

    This is the pin that keeps the refusal above from being reachable only by a
    future lane: the frozen gold's two edge items declare the shape their `why`
    already described in prose.
    """
    gold = json.loads(GOLD.read_text())
    edge_items = [
        exp
        for entry in gold["inputs"]
        for exp in entry.get("expect", [])
        if exp["layer"] == "edge"
    ]
    assert edge_items, "the gold states no edge item — nothing left to check"
    layers = {layer.lower() for layer in axe.LAYERS}
    for exp in edge_items:
        ends = exp.get("endpoints")
        assert isinstance(ends, dict) and ends, (
            f"{exp['item']!r}: an edge item must declare `endpoints` — a relation "
            f"name alone passes between the wrong layers"
        )
        for side, wanted in ends.items():
            assert side in ("from", "to"), f"{exp['item']!r}: unknown endpoint side {side!r}"
            assert wanted, f"{exp['item']!r}: {side} names no layer"
            assert {str(layer).lower() for layer in wanted} <= layers, (
                f"{exp['item']!r}: {side} names a layer that is not one of {sorted(layers)}"
            )
