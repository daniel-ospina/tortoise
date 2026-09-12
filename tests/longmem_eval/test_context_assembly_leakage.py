"""#3011 Track F — gold-leakage tests (HARD STOP: a leaked arm invalidates the run).

Spec (frozen, authoritative):
``docs/experiments/2026-09-11-abc-context-assembly-experiment.md`` §3
("Labeling rule (hard)" + "Leakage test") and §10 ("Gold-field leakage
test").

Track F owns the two mandatory pre-run checks:

1. **Gold-field perturbation run (§10.1).** Perturb the gold fields
   (``has_answer`` → False on every Point, one fixed permutation of
   ``lme_session_index``) and assert the re-rendered arm-B/arm-C text is
   **byte-identical** to the unperturbed render. The perturbation detects
   *use*, not just printing: a selector that ranks by ``has_answer`` emits
   clean text and still leaks. This file exercises the invariant hermetically
   (no DB, no model) over the real traversal + render functions — the live
   docker-lane scratch-namespace run is Track E's driver, which consumes the
   same helpers from :mod:`tools.longmem_eval.leakage_guard`.
2. **Static reference assertion (§10.2).** A static (AST + tokenize) check
   that the seed, traversal, ranking and B/C render code paths never
   reference ``has_answer``, ``answer_session_ids``,
   ``lme_session_index``, or the gold-evidence claim list — neither its
   loader symbol nor its artifact path. See :mod:`tools.longmem_eval.leakage_guard`.

Any failure here is a hard stop: fix the leaking code path before running an
arm — never run an arm that can rank on gold.
"""

from __future__ import annotations

import contextlib
import copy
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import tortoise.subgraph as subgraph  # noqa: E402
from tools.longmem_eval import leakage_guard as lg  # noqa: E402
from tortoise.subgraph import (  # noqa: E402
    Candidate,
    Relation,
    Subgraph,
    build_subgraph_from_seeds,
)
from tortoise.subgraph_render import (  # noqa: E402
    EMPTY_CONTEXT_SENTINEL,
    render_arm_b,
    render_arm_c,
)

#: The four gold artifacts the §10 static-reference assertion names.
_FORBIDDEN_TOKENS = (
    "has_answer",
    "answer_session_ids",
    "lme_session_index",
)


# ── fake graph (hermetic traversal seam, mirrors Track A's test fixture) ──


class _Result:
    """Minimal FalkorDB ``QueryResult`` stand-in (only ``result_set`` used)."""

    __slots__ = ("result_set",)

    def __init__(self, rows: list):
        self.result_set = rows


class FakeGraph:
    """In-memory claim/operator/entity graph with gold props on every Point."""

    def __init__(self, *, has_answer: bool = True, session_index: int = 3) -> None:
        self.points: dict[str, dict] = {}
        self.operators: dict[str, dict] = {}
        self.objects: dict[str, str] = {}
        self.about: dict[str, list[str]] = {}
        self.corrects: list[tuple[str, str]] = []
        self.object_degree: dict[str, int] = {}
        self.issued: list[tuple[str, dict]] = []
        self._has_answer = has_answer
        self._session_index = session_index

    def add_point(self, pid: str, content: str | None, **props) -> None:
        self.points[pid] = {
            "id": pid,
            "content": content,
            "is_operator": False,
            # Gold fields — exactly the shape the eval ingest writes.
            "has_answer": self._has_answer,
            "lme_session_index": self._session_index,
            **props,
        }

    def _prop_values(self, pid: str) -> list:
        point = self.points.get(pid)
        if point is None:
            return [None] * len(subgraph._POINT_PROP_KEYS)
        return [pid if key == "id" else point.get(key) for key in subgraph._POINT_PROP_KEYS]

    def query(self, cypher: str, params: dict | None = None) -> _Result:
        params = dict(params or {})
        self.issued.append((cypher, params))

        if "WHERE p.id IN $ids" in cypher:
            return _Result(
                [self._prop_values(pid) for pid in params.get("ids", []) if pid in self.points]
            )
        if "count(r) AS hub_degree" in cypher:
            rows = []
            for oid in params.get("ids", []):
                degree = self.object_degree.get(
                    oid, sum(1 for links in self.about.values() if oid in links)
                )
                rows.append([oid, degree])
            return _Result(rows)
        if "sib.id AS sib_id" in cypher:
            nid = params["id"]
            rows = []
            for oid in self.about.get(nid, []):
                for other, links in self.about.items():
                    if other == nid or oid not in links:
                        continue
                    rows.append([oid, self.objects.get(oid, ""), *self._prop_values(other)])
            return _Result(rows)
        if "o.id AS hub_id, o.name AS hub_name" in cypher:
            nid = params["id"]
            return _Result([[oid, self.objects.get(oid, "")] for oid in self.about.get(nid, [])])
        if "type(r2) AS edge_type" in cypher:
            nid = params["id"]
            rows = []
            for op in self.operators.values():
                for pid, idx in op["endpoints"]:
                    if pid != nid:
                        continue
                    for other, other_idx in op["endpoints"]:
                        if other == nid:
                            continue
                        rows.append(
                            [op["type"], idx, other_idx, op["id"], *self._prop_values(other)]
                        )
            return _Result(rows)
        if "CORRECTS" in cypher:
            nid = params["id"]
            if "(other:Point)-[:CORRECTS]->(n)" in cypher:
                return _Result([self._prop_values(new) for new, old in self.corrects if old == nid])
            return _Result([self._prop_values(old) for new, old in self.corrects if new == nid])
        raise AssertionError(f"unrecognized Cypher in fake graph: {cypher!r}")


def _fixture_graph(has_answer: bool = True, session_index: int = 3) -> FakeGraph:
    """A small graph exercising 1-hop IMPL, aboutObject and supersession."""
    g = FakeGraph(has_answer=has_answer, session_index=session_index)
    for pid, content in (
        ("A1", "anchor one"),
        ("A2", "anchor two"),
        ("P1", "implied claim"),
        ("P2", "superseded claim"),
    ):
        g.add_point(pid, content, session_id="s-3", createdAt="2023-05-06T10:00:00Z")
    g.operators["OP1"] = {"id": "OP1", "type": "IMPL", "endpoints": (("A1", 0), ("P1", 1))}
    g.operators["OP2"] = {"id": "OP2", "type": "NAND", "endpoints": (("A2", 0), ("P1", 1))}
    g.objects["E1"] = "Rovo"
    g.about["A1"] = ["E1"]
    g.about["P2"] = ["E1"]
    g.object_degree["E1"] = 4
    g.corrects.append(("P2", "A2"))
    return g


def _assert_subgraphs_equal(left: Subgraph, right: Subgraph) -> None:
    """Every observable traverse/rank output must be identical."""
    assert left.seeds == right.seeds
    assert left.anchors == right.anchors
    assert left.zero_seed == right.zero_seed
    assert left.seed_fn == right.seed_fn
    assert [c.point_id for c in left.candidates] == [c.point_id for c in right.candidates]
    assert [c.score for c in left.candidates] == [c.score for c in right.candidates]
    assert left.relations == right.relations
    assert left.content_by_id == right.content_by_id
    assert left.point_props == right.point_props


# ══════════════════════════════════════════════════════════════════════════
# CHECK 1 — static reference assertion (§10.2, hermetic)
# ══════════════════════════════════════════════════════════════════════════


def test_static_reference_assertion_code_paths_are_clean():
    """The seed/traversal/ranking/render paths reference no gold artifact."""
    report = lg.assert_code_paths_clean()
    assert report.clean
    assert report.visited, "the scanner visited no functions — the guard is vacuous"


def test_every_named_code_path_is_actually_scanned():
    """Guard against a silent scope drift (file rename ⇒ vacuous pass)."""
    report = lg.scan_code_paths()
    visited = set(report.visited)
    required = {
        "tortoise/subgraph.py::build_subgraph_from_seeds",
        "tortoise/subgraph.py::select_seeds",
        "tortoise/subgraph.py::rank_score",
        "tortoise/subgraph.py::_fetch_seeds",
        "tortoise/subgraph_render.py::render_arm_b",
        "tortoise/subgraph_render.py::render_arm_c",
        # P1: the module that actually builds the B/C context must be scanned
        # (it holds the full dataset row + calls render_arm_b/render_arm_c).
        "tools/longmem_eval/context_assembly_arms.py::build_context_arm",
        "tools/longmem_eval/context_assembly_arms.py::turns_by_point",
        "tools/longmem_eval/retrieve.py::vector_search",
        "tortoise/search_engine.py::run_vector_query",
        "tortoise/search_engine.py::run_fts_query",
        "tools/longmem_eval/ep_activation.py::read_confidence",
    }
    missing = sorted(required - visited)
    assert not missing, f"declared code paths not scanned: {missing}"


def test_forbidden_constants_match_the_frozen_spec():
    """The guard names exactly what §3/§10 name."""
    assert lg.GOLD_PROPERTY_TOKENS == _FORBIDDEN_TOKENS
    assert (
        lg.GOLD_EVIDENCE_ARTIFACT_PATH
        == "docs/experiments/artifacts/2026-09-11-abc-context-assembly/"
        "gold-evidence-claims.json"
    )
    assert lg.GOLD_EVIDENCE_ARTIFACT_FILENAME in lg.GOLD_EVIDENCE_ARTIFACT_PATH
    assert lg.GOLD_EVIDENCE_ARTIFACT_DIR in lg.GOLD_EVIDENCE_ARTIFACT_PATH
    assert "load_gold_evidence_claims" in lg.GOLD_EVIDENCE_LOADER_SYMBOLS


@pytest.mark.parametrize(
    "token",
    [
        "has_answer",
        "answer_session_ids",
        "lme_session_index",
    ],
)
def test_scanner_detects_property_identifier_reference(token):
    """Non-vacuous: a property identifier reference is flagged."""
    findings = lg.scan_source(f"def seed(x):\n    return x.{token}\n")
    assert any(token in f.detail or token in f.detail.lower() for f in findings)
    assert any(f.kind == "identifier" for f in findings)


def test_scanner_detects_property_string_reference():
    findings = lg.scan_source('def seed(x):\n    return x.get("has_answer")\n')
    assert any(f.kind == "string" for f in findings)


def test_scanner_detects_loader_symbol_by_identifier():
    findings = lg.scan_source(
        "def seed():\n"
        "    from tools.longmem_eval.gold_evidence import load_gold_evidence_claims\n"
        "    return load_gold_evidence_claims()\n"
    )
    assert any("gold" in f.detail.lower() for f in findings)


def test_scanner_detects_aliased_loader_symbol():
    """An aliased gold import must not evade the scan."""
    findings = lg.scan_source("from tools.x import load_gold_evidence_claims as g\n")
    assert any("gold" in f.detail.lower() for f in findings)
    findings = lg.scan_source("import tools.longmem_eval.gold_evidence as ge\n")
    assert any("gold" in f.detail.lower() for f in findings)


def test_scanner_detects_artifact_path_by_string():
    findings = lg.scan_source(
        'GOLD_PATH = "docs/experiments/artifacts/2026-09-11-abc-context-assembly/'
        'gold-evidence-claims.json"\n'
    )
    assert any(f.kind == "string" for f in findings)


def test_scanner_detects_comment_reference():
    findings = lg.scan_source("def seed(x):\n    # rank by has_answer (leak!)\n    return x\n")
    assert any(f.kind == "comment" for f in findings)


def test_scanner_ignores_docstrings_that_document_the_rule():
    """Prose that names the rule is not a code reference (spec convention)."""
    findings = lg.scan_source(
        '"""We never read the gold annotation or the gold-evidence claim artifact."""\n'
        "def seed(x):\n"
        '    """has_answer is forbidden here."""\n'
        "    return x\n"
    )
    assert findings == ()


def test_scanner_does_not_flag_unrelated_gold_free_code():
    findings = lg.scan_source(
        "def seed(items):\n"
        "    best = {}\n"
        "    for pid, score in items:\n"
        "        best[pid] = max(best.get(pid, score), score)\n"
        "    return sorted(best.items())\n"
    )
    assert findings == ()


# ── P0 regression: assembled references must not evade the scan ───────────


@pytest.mark.parametrize(
    ("label", "source"),
    [
        # The four demonstrated evasions from the review finding — each
        # returned ZERO findings before constant folding was added.
        (
            "concat_two_fragments",
            'def seed(x):\n    k = "answer" + "_session_ids"\n    return x.get(k)\n',
        ),
        ("concat_has_answer", 'def seed(x):\n    k = "has_" + "answer"\n    return x.get(k)\n'),
        (
            "concat_three_fragments",
            'def seed(x):\n    k = "lme_" + "session" + "_index"\n    return x.get(k)\n',
        ),
        (
            "getattr_concatenated",
            'def seed(props):\n    return getattr(props, "answer" + "_session_ids")\n',
        ),
        # Other constant-assembly routes the resolver closes.
        ("fstring_constant", "def seed(x):\n    k = f\"has_{'answer'}\"\n    return x.get(k)\n"),
        (
            "join_constants",
            'def seed(x):\n    k = "_".join(["answer", "session", "ids"])\n    return x.get(k)\n',
        ),
        (
            "format_constant",
            'def seed(x):\n    k = "has_{}".format("answer")\n    return x.get(k)\n',
        ),
        ("percent_constant", 'def seed(x):\n    k = "has_%s" % "answer"\n    return x.get(k)\n'),
        ("bytes_literal", 'def seed(x):\n    return x.get(b"has_answer")\n'),
        (
            "bytes_decode",
            'def seed(x):\n    return x.get(b"answer_session_ids".decode())\n',
        ),
        (
            "subscript_key_concat",
            'def seed(x):\n    return x["lme_" + "session" + "_index"]\n',
        ),
    ],
)
def test_scanner_detects_assembled_gold_reference(label, source):
    """A gold marker assembled from constant fragments is still caught."""
    findings = lg.scan_source(source)
    assert findings, f"{label}: assembled gold reference evaded the scan"
    assert any(f.kind == "string" for f in findings), (label, findings)


def test_scanner_detects_assembled_artifact_path():
    """The gold artifact path assembled from fragments is caught."""
    findings = lg.scan_source(
        'P = "docs/experiments/artifacts/2026-09-11-abc" '
        '+ "-context-assembly/gold-evidence-claims.json"\n'
    )
    assert any(f.kind == "string" for f in findings)


def test_scanner_still_flags_direct_reference_after_folding():
    """Constant folding did not weaken the direct-literal channel."""
    assert lg.scan_source('def seed(x):\n    return x.get("has_answer")\n')
    assert lg.scan_source("def seed(x):\n    return x.answer_session_ids\n")


# ── P1 regression: B/C builder is in scope, arm A/D carve-out is explicit ──


_BC_BUILDER_WITH_ARM_A_GOLD_READ = """
def build_context_arm(arm, qctx):
    if arm == "A":
        # arm A is the gold-verbatim oracle ceiling (spec §3)
        return qctx.answer_session_ids
    if arm == "D":
        return render_context([], question_date=qctx.question_date)
    if arm in ("B", "C"):
        return build_subgraph(qctx.question_text)
"""


def _arm_exclusion(value: str) -> lg.BranchExclusion:
    return lg.BranchExclusion(
        function="build_context_arm", parameter="arm", value=value, reason="test carve-out"
    )


def test_bc_builder_and_turn_helper_are_scanned():
    """P1: the guard covers the module that builds the B/C context."""
    report = lg.scan_code_paths()
    visited = set(report.visited)
    assert "tools/longmem_eval/context_assembly_arms.py::build_context_arm" in visited
    assert "tools/longmem_eval/context_assembly_arms.py::turns_by_point" in visited
    # Reached through the builder's B/C branch — proves the closure is live.
    assert "tortoise/subgraph_render.py::render_arm_b" in visited
    assert "tortoise/subgraph_render.py::render_arm_c" in visited


def test_guard_report_names_which_functions_are_out_of_scope():
    """The coverage claim is auditable: exclusions are stated verbatim."""
    report = lg.scan_code_paths()
    joined = "\n".join(report.excluded)
    assert "tools/longmem_eval/context_assembly_arms.py::build_context_arm" in joined
    assert "arm == 'A'" in joined
    assert "arm == 'D'" in joined


def test_arm_a_gold_read_is_excluded_from_the_bc_scan():
    """Arm A reads answer_session_ids legitimately; the carve-out hides it."""
    exclusions = (_arm_exclusion("A"), _arm_exclusion("D"))
    excluded = lg.scan_source(_BC_BUILDER_WITH_ARM_A_GOLD_READ, branch_exclusions=exclusions)
    assert excluded == (), [str(f) for f in excluded]
    # The same source without the carve-out IS flagged — the exclusion is real.
    assert lg.scan_source(_BC_BUILDER_WITH_ARM_A_GOLD_READ) != ()


def test_bc_branch_leak_is_not_hidden_by_the_arm_a_exclusion():
    """A gold reference in the B/C branch is still a hard failure."""
    source = (
        "def build_context_arm(arm, qctx):\n"
        '    if arm == "A":\n'
        "        return qctx.answer_session_ids\n"
        '    if arm in ("B", "C"):\n'
        '        return getattr(qctx, "answer" + "_session_ids")\n'
    )
    findings = lg.scan_source(source, branch_exclusions=(_arm_exclusion("A"),))
    assert findings, "a B/C-branch gold read must still be caught"
    assert any(f.kind == "string" for f in findings)


def test_guard_reports_no_stale_scope():
    """A missing declared entry point is itself a finding (stale-scope guard)."""
    report = lg.scan_code_paths()
    assert not any(f.kind == "scope" for f in report.findings)


# ══════════════════════════════════════════════════════════════════════════
# CHECK 2 — gold-field perturbation (§10.1, hermetic)
# ══════════════════════════════════════════════════════════════════════════


def _sample_subgraph() -> Subgraph:
    """One anchor + one candidate, with gold fields present in point props."""
    session_index_map = lg.fixed_session_index_permutation(8)
    return Subgraph(
        seeds=(("A1", 0.9),),
        anchors=("A1",),
        candidates=(
            Candidate(
                point_id="P1",
                content="implied claim",
                anchor_id="A1",
                edge_type="IMPL",
                hop=1,
                s_norm=1.0,
                raw_score=0.7,
                score=0.7,
                damped=False,
                reserved=False,
            ),
        ),
        relations=(Relation(source_id="A1", relation="IMPL", target_id="P1", target_label=""),),
        zero_seed=False,
        reserved_overflow=0,
        seed_fn="vector",
        content_by_id={"A1": "anchor one", "P1": "implied claim"},
        point_props={
            "A1": {
                "session_id": "s-3",
                "createdAt": "2023-05-06T10:00:00Z",
                "source_turn_id": "lme:q1:s3:t4",
                "posterior_alpha": 4.0,
                "posterior_beta": 1.0,
                # Gold fields — must have zero effect on the render.
                "has_answer": True,
                "lme_session_index": 3,
            },
            "P1": {
                "session_id": "s-3",
                "validFrom": "2023-05-07",
                "posterior_alpha": 2.0,
                "posterior_beta": 2.0,
                "has_answer": True,
                "lme_session_index": session_index_map[3],
            },
        },
    )


_HAYSTACK = ["s-1", "s-2", "s-3", "s-4"]
_TURNS = {"A1": [{"role": "user", "content": "the primary user turn"}]}


def test_render_arm_b_byte_identical_under_gold_perturbation():
    sg = _sample_subgraph()
    session_index_map = lg.fixed_session_index_permutation(8)

    baseline = render_arm_b(sg, haystack_session_ids=_HAYSTACK, points_by_id=sg.point_props)
    perturbed = render_arm_b(
        sg,
        haystack_session_ids=_HAYSTACK,
        points_by_id={
            pid: lg.perturb_gold_props(props, session_index_map=session_index_map)
            for pid, props in sg.point_props.items()
        },
    )
    assert baseline.text == perturbed.text
    assert baseline.word_count == perturbed.word_count
    assert baseline.claims_rendered == perturbed.claims_rendered


def test_render_arm_c_byte_identical_under_gold_perturbation():
    sg = _sample_subgraph()
    session_index_map = lg.fixed_session_index_permutation(8)

    def render(props):
        return render_arm_c(
            sg,
            haystack_session_ids=_HAYSTACK,
            turns_by_point=_TURNS,
            points_by_id=props,
        )

    baseline, perturbed = lg.render_perturbation_pair(
        render, sg.point_props, session_index_map=session_index_map
    )
    assert baseline == perturbed
    assert "> user: the primary user turn" in baseline


def test_render_unchanged_when_gold_fields_are_stripped():
    """Dropping both gold fields changes nothing — for B and for C."""
    sg = _sample_subgraph()
    stripped = {
        pid: {k: v for k, v in props.items() if not lg.is_gold_property_key(k)}
        for pid, props in sg.point_props.items()
    }
    assert render_arm_b(sg, haystack_session_ids=_HAYSTACK, points_by_id=sg.point_props).text == (
        render_arm_b(sg, haystack_session_ids=_HAYSTACK, points_by_id=stripped).text
    )
    assert (
        render_arm_c(
            sg, haystack_session_ids=_HAYSTACK, turns_by_point=_TURNS, points_by_id=sg.point_props
        ).text
        == render_arm_c(
            sg, haystack_session_ids=_HAYSTACK, turns_by_point=_TURNS, points_by_id=stripped
        ).text
    )


def test_recording_mapping_detects_a_gold_read():
    """The recorder is non-vacuous: a synthetic gold read is recorded."""
    accessed: set[str] = set()
    recording = lg.RecordingMapping({"has_answer": True, "posterior_alpha": 1.0}, accessed)

    def reads_gold(props):
        return props.get("has_answer")

    def reads_gold_subscript(props):
        return props["lme_session_index"]

    assert reads_gold(recording) is True
    assert "has_answer" in accessed
    with contextlib.suppress(KeyError):
        reads_gold_subscript(recording)
    gold_keys_read = {key for key in accessed if lg.is_gold_property_key(key)}
    assert gold_keys_read == {"has_answer", "lme_session_index"}


def test_traversal_output_identical_under_gold_perturbation():
    """Perturb `has_answer`/`lme_session_index` in the graph → same Subgraph."""
    seeds = [("A1", 0.9), ("A2", 0.4)]
    baseline_graph = _fixture_graph(has_answer=True, session_index=3)
    baseline = build_subgraph_from_seeds(baseline_graph, list(seeds))

    perturbed_graph = copy.deepcopy(baseline_graph)
    session_index_map = lg.fixed_session_index_permutation(8)
    for point in perturbed_graph.points.values():
        point.update(
            lg.perturb_gold_props(
                {
                    "has_answer": point.get("has_answer"),
                    "lme_session_index": point.get("lme_session_index"),
                },
                session_index_map=session_index_map,
            )
        )
    perturbed = build_subgraph_from_seeds(perturbed_graph, list(seeds))

    _assert_subgraphs_equal(baseline, perturbed)
    assert baseline.candidates, "fixture produced no candidates — perturbation is vacuous"


def test_traversal_issues_no_gold_token_in_cypher_or_params():
    g = _fixture_graph()
    build_subgraph_from_seeds(g, [("A1", 0.9), ("A2", 0.4)])
    assert g.issued, "no Cypher was issued — the traversal did not run"
    for cypher, params in g.issued:
        for token in _FORBIDDEN_TOKENS:
            assert token not in cypher, cypher
            for key in params:
                assert token not in str(key)


def test_zero_seed_render_is_the_frozen_sentinel():
    sg = Subgraph(
        seeds=(),
        anchors=(),
        candidates=(),
        relations=(),
        zero_seed=True,
        reserved_overflow=0,
        seed_fn="vector",
    )
    assert render_arm_b(sg, haystack_session_ids=_HAYSTACK).text == EMPTY_CONTEXT_SENTINEL
    assert render_arm_c(sg, haystack_session_ids=_HAYSTACK).text == EMPTY_CONTEXT_SENTINEL
