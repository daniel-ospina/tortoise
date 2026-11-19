"""Pins what #13732's measurement rests on — the half that runs with no store.

The lane's question is *"does the record's dense leg lift claim recall?"*, and the
instrument (`tools/claim_dense_leg_13732.py`) cannot answer it on a box with no
corpus, no embedder and no Postgres. What CAN be pinned everywhere is the part a
later run must not be free to choose after it sees the numbers:

1. **the pre-registered statistic** — the paired McNemar and the DETECTABLE
   EFFECT at the population's n. A lane that discovers its delta is 2 points and
   then picks a test that calls that significant is the failure this file makes
   impossible: the numbers are constants here, and the tool's own
   `--preregister` output is compared against them;
2. **the arms' own claims about themselves** — `record_dense` must be served by
   `mechanism=pgvector` and the lexical arm must show a dense leg that did not
   run. A `scan_fallback` on the record arm is REFUSED here, because a graph
   answer wearing the record arm's name is the one way this measurement could
   be reported and be about the wrong store;
3. **the coverage gate's zero trap** — `coverage` compares
   `record_vectors >= graph_vectors`, and **0 >= 0 is verified**, so an unseeded
   population PASSES it and serves an empty cohort as `pgvector/ok`. That is
   exactly the shape M2's population had (written with no embedding), so the
   guard is asserted rather than assumed;
4. **the contradiction test, as a structural pin** — the dense leg comes FROM
   THE RECORD (`#11481` V1: Postgres is the record, FalkorDB is the projection).
   The instrument must therefore never create a graph vector index nor seed
   record rows; an AST scan refuses either. A future lane that "fixes" the
   measurement by indexing `:Object` on the graph would be reversing a landed
   decision, and this test is where that shows up.

⛔ Nothing here runs the measurement. The live arms need a capture corpus, an
embedder (`huggingface` reachable) and a pgvector record — the instrument refuses
by NAME (`no_corpus` / `no_embedder` / `no_record_dsn` / `coverage_unverified`)
rather than publishing a fixture's number, and those refusals are asserted below
so a silent skip cannot take their place.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "claim_dense_leg_13732.py"

#: M2's built claim population (`--points 900` -> 786 built), the n the lane's
#: pre-registration is stated for.
M2_N = 786


@pytest.fixture(scope="module")
def tool_mod():
    assert TOOL.exists(), f"missing instrument {TOOL}"
    spec = importlib.util.spec_from_file_location("claim_dense_leg_13732", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── 1. the pre-registered statistic ────────────────────────────────────────


class TestPreregistration:
    """The thresholds exist BEFORE the numbers, and are constants, not choices."""

    #: Computed by hand from `delta = (z_{0.975} + z_{0.80})·sqrt(p_d/n)` at
    #: n = 786 — the same expression the tool implements, pinned independently so
    #: a "tuned" formula reds here.
    EXPECTED_PP: ClassVar[dict[float, float]] = {
        0.05: 2.23,
        0.10: 3.16,
        0.20: 4.47,
        0.30: 5.47,
    }

    def test_detectable_effect_at_the_m2_population(self, tool_mod):
        for discordance, expected in self.EXPECTED_PP.items():
            got = tool_mod.detectable_effect_pp(M2_N, discordance)
            assert abs(got - expected) < 0.06, (discordance, got, expected)

    def test_detectable_effect_is_monotone_in_discordance(self, tool_mod):
        vals = [tool_mod.detectable_effect_pp(M2_N, d) for d in (0.05, 0.10, 0.20, 0.30)]
        assert vals == sorted(vals)

    def test_detectable_effect_falls_as_n_grows(self, tool_mod):
        small = tool_mod.detectable_effect_pp(786, 0.2)
        big = tool_mod.detectable_effect_pp(786 * 4, 0.2)
        assert big < small

    def test_erfinv_inverts_erf(self, tool_mod):
        for x in (-0.9, -0.5, 0.0, 0.3, 0.75):
            assert abs(math.erf(tool_mod._erfinv(x)) - x) < 1e-9

    def test_preregistration_block_is_runnable_and_carries_the_numbers(self, tool_mod):
        block = tool_mod.preregistration(M2_N)
        assert block["population_n"] == M2_N
        assert block["primary_k"] == 3
        assert set(block["detectable_delta_pp"]) == {"0.05", "0.10", "0.20", "0.30"}
        assert block["statistic"].startswith("exact two-sided McNemar")
        assert block["arms"] == ["lexical", "graph_dense", "record_dense"]
        assert block["declared_mechanisms"]["record_dense"] == ["pgvector"]

    def test_committed_preregistration_matches_the_instrument(self, tool_mod):
        """The committed block and the tool's own output are the SAME numbers.

        ⛔ A COMPOSITION check, not provenance: it says the artifact was not
        edited independently of the code that emits it, so an edit to one that
        stales the other reds. It cannot show the thresholds existed before the
        run — only the run's own ordering does that, which is why the file is
        committed WITH the instrument rather than after a measurement.
        """
        committed = json.loads(
            (
                REPO_ROOT / "docs" / "evidence" / "13732-claim-dense-leg" / "preregistration.json"
            ).read_text()
        )
        assert committed == tool_mod.preregistration(M2_N)

    def test_preregister_needs_no_store_no_embedder_no_corpus(self):
        """The one mode THIS box can run — so the thresholds precede the run.

        Run in a SUBPROCESS with an empty environment so an embedder, a DSN or a
        live FalkorDB in the parent process cannot make this pass by accident.
        """
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--preregister", "--population", str(M2_N)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"},
        )
        assert proc.returncode == 0, proc.stderr
        block = json.loads(proc.stdout)
        assert block["detectable_delta_pp"]["0.20"] == pytest.approx(4.47, abs=0.06)


# ── 2. the paired analysis ─────────────────────────────────────────────────


class TestPaired:
    def _ranks(self, spec: dict[str, int | None]) -> dict[str, int | None]:
        return dict(spec)

    def test_rank_of_absent_is_none_not_zero(self, tool_mod):
        assert tool_mod.rank_of(["a", "b"], "b") == 2
        assert tool_mod.rank_of(["a", "b"], "z") is None
        assert tool_mod.hit(None, 10) is False
        assert tool_mod.hit(0, 1) is False  # a 0 rank is not a rank

    def test_paired_counts_discordant_pairs_only(self, tool_mod):
        # b gains 3, loses 1 -> net +2 of 10 queries; the 6 concordant carry no
        # information and must not enter the test's denominator.
        a = {
            "q0": 1,
            "q1": 1,
            "q2": None,
            "q3": None,
            "q4": 9,
            "q5": None,
            "q6": 1,
            "q7": 1,
            "q8": None,
            "q9": None,
        }
        b = {
            "q0": 1,
            "q1": 1,
            "q2": 2,
            "q3": 3,
            "q4": 9,
            "q5": 1,
            "q6": 1,
            "q7": 5,
            "q8": None,
            "q9": None,
        }
        cmp = tool_mod.paired(a, b, 3)
        assert cmp["only_a"] == 1  # q7 (rank 5) lost
        assert cmp["only_b"] == 3  # q2, q3, q5 gained
        assert cmp["discordant"] == 4
        assert cmp["delta_pp"] == pytest.approx(20.0)
        assert cmp["found_deep_a"] == 5  # q0,q1,q4,q6,q7 at any rank
        assert cmp["found_deep_b"] == 8  # every query but q8,q9

    def test_paired_refuses_different_id_sets(self, tool_mod):
        with pytest.raises(ValueError):
            tool_mod.paired({"a": 1}, {"b": 1}, 3)

    def test_mcnemar_is_exact_and_matches_scipy(self, tool_mod):
        binomtest = pytest.importorskip("scipy.stats").binomtest
        for only_b, n_disc in ((3, 4), (10, 20), (0, 7), (25, 40)):
            got = tool_mod._mcnemar_p(only_b, n_disc)
            want = binomtest(only_b, n_disc, 0.5, alternative="two-sided").pvalue
            assert got == pytest.approx(min(1.0, want), abs=1e-6), (only_b, n_disc)

    def test_unresolved_when_the_delta_is_below_the_detectable_effect(self, tool_mod):
        # n = 786, 20 % discordance -> detectable 4.47 pp. A 2 pp move with a
        # small p is still UNRESOLVED: the test can see an ordering, not a size.
        cmp = {
            "delta_pp": 2.0,
            "mcnemar_p_two_sided": 0.01,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(cmp) == "UNRESOLVED"

    def test_trade_is_not_a_gain(self, tool_mod):
        cmp = {
            "delta_pp": 8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": -2.5,
        }
        assert tool_mod.classify(cmp) == "TRADE"

    def test_gain_and_loss(self, tool_mod):
        up = {
            "delta_pp": 8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 1.0,
        }
        down = {
            "delta_pp": -8.0,
            "mcnemar_p_two_sided": 1e-5,
            "detectable_pp": 4.47,
            "found_deep_delta_pp": 0.0,
        }
        assert tool_mod.classify(up) == "GAIN"
        assert tool_mod.classify(down) == "LOSS"


# ── 3. the arms' claims about themselves ───────────────────────────────────


def _trace(
    mechanism: str | None, *, ran: bool = True, degraded: bool = False, reason: str | None = "ok"
) -> list[dict]:
    entry = {"leg": "vector", "ran": ran, "degraded": degraded, "reason": reason, "count": 1}
    if mechanism is not None:
        entry["mechanism"] = mechanism
    return [entry, {"leg": "fts", "ran": True, "degraded": False, "reason": "ok", "count": 5}]


class TestArmAssertions:
    def test_record_arm_accepts_pgvector(self, tool_mod):
        got = tool_mod.check_arm("record_dense", _trace("pgvector"))
        assert got == {"arm": "record_dense", "ran": True, "mechanisms": ["pgvector"]}

    def test_record_arm_refuses_scan_fallback(self, tool_mod):
        """A graph answer wearing the record's name — the falsifier's whole point."""
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", _trace("scan_fallback"))
        assert ei.value.code == "record_dense_not_pgvector"
        assert ei.value.exit_code == 3

    def test_record_arm_refuses_a_mixed_run(self, tool_mod):
        mixed = _trace("pgvector") + _trace("scan_fallback")
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", mixed)
        assert ei.value.code == "record_dense_mixed_mechanism"

    def test_record_arm_refuses_when_the_dense_leg_did_not_run(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm(
                "record_dense", _trace("pgvector", ran=False, degraded=True, reason="query_failed")
            )
        assert ei.value.code == "dense_arm_did_not_run"

    def test_lexical_arm_accepts_a_dense_leg_that_did_not_run(self, tool_mod):
        got = tool_mod.check_arm(
            "lexical", _trace(None, ran=False, degraded=True, reason="no_embedder")
        )
        assert got["ran"] is False

    def test_lexical_arm_refuses_a_hybrid_read(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("lexical", _trace("index"))
        assert ei.value.code == "lexical_arm_ran_dense"

    def test_graph_arm_refuses_the_record_mechanism(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("graph_dense", _trace("pgvector"))
        assert ei.value.code == "graph_arm_answered_from_record"

    def test_graph_arm_accepts_the_index_and_the_scan(self, tool_mod):
        assert tool_mod.check_arm("graph_dense", _trace("index"))["mechanisms"] == ["index"]
        assert tool_mod.check_arm("graph_dense", _trace("scan_fallback"))["mechanisms"] == [
            "scan_fallback"
        ]

    def test_a_trace_with_no_vector_entry_is_refused_not_passed(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.check_arm("record_dense", [{"leg": "fts", "ran": True}])
        assert ei.value.code == "trace_missing_vector_leg"


# ── 4. the preconditions refuse BY NAME ────────────────────────────────────


class TestRefusals:
    def test_missing_corpus_names_the_directory(self, tool_mod, tmp_path):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_corpus(tmp_path)
        assert ei.value.code == "no_corpus"
        assert str(tmp_path) in ei.value.message
        assert "fixture" in ei.value.message

    def test_no_record_dsn(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_record(None)
        assert ei.value.code == "no_record_dsn"

    def test_seeded_guard_refuses_the_zero_zero_coverage_trap(self, tool_mod):
        """`0 >= 0` is VERIFIED — so seeding is guarded, not inferred."""
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_seeded(0, 0)
        assert ei.value.code == "graph_unseeded"

    def test_seeded_guard_refuses_an_empty_record(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_seeded(786, 0)
        assert ei.value.code == "record_unseeded"
        assert "13210" in ei.value.message  # the backfill is named, not implied

    def test_seeded_guard_refuses_an_unmeasurable_record(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_seeded(786, None)
        assert ei.value.code == "record_unmeasurable"

    def test_seeded_guard_passes_a_seeded_pair(self, tool_mod):
        tool_mod.require_seeded(786, 800)  # no raise

    def test_unverified_coverage_is_refused(self, tool_mod):
        class V:
            verified = False
            reason = "the record holds 0 vector(s) for `point`"

        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_coverage(V())
        assert ei.value.code == "coverage_unverified"

    def test_main_refuses_a_k_set_without_the_primary_width(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--ks", "1,5"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 4
        assert "primary k=3" in proc.stderr

    def test_main_refuses_a_depth_below_the_widest_k(self):
        proc = subprocess.run(
            [sys.executable, str(TOOL), "--deep", "3"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 4
        assert "found@deep" in proc.stderr


# ── 5. the contradiction test, structurally ───────────────────────────────


class TestContradictionTest:
    """Postgres is the record; FalkorDB is the projection (`#11481` V1).

    ⛔ The dense leg must come FROM THE RECORD. The two ways an instrument could
    quietly reverse that are a VECTOR index on the graph and record rows written
    from the harness — both are refused here, by AST, so the refusal is about the
    code and not about a reviewer's memory.
    """

    #: Cypher/DDL this instrument may never contain.
    FORBIDDEN_SNIPPETS: ClassVar[tuple[str, ...]] = (
        "db.idx.vector.queryNodes",  # a graph ANN read would bypass the record
        "CREATE VECTOR INDEX",  # the index that must stay on the record
        "CREATE INDEX FOR",  # ditto, engine-native spelling
        "INSERT INTO",  # the instrument does not seed record rows
        "CREATE TABLE",  # nor the record's schema
    )

    def test_the_instrument_creates_no_graph_vector_index_and_seeds_no_rows(self):
        source = TOOL.read_text()
        ast.parse(source)  # it must at least be a module
        for snippet in self.FORBIDDEN_SNIPPETS:
            assert snippet not in source, (
                f"{snippet!r} appears in the instrument — the dense leg comes from "
                f"the RECORD (#11481 V1), and a graph vector index here would "
                f"reverse a landed decision inside a measurement tool"
            )

    def test_it_reads_the_record_leg_through_its_own_seam(self):
        """The arm is switched by the module that owns the switch, not re-spelt.

        ⛔ The literal is checked OUTSIDE docstrings: the prose is allowed to
        name the switch (a reader needs it), the CODE is not — one owner of the
        name, so a rename cannot leave this tool flipping a variable nothing
        reads.
        """
        source = TOOL.read_text()
        tree = ast.parse(source)
        assert "record_vector.ARMING_ENV" in source
        docstrings = {
            ast.get_docstring(node, clean=False)
            for node in ast.walk(tree)
            if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        }
        literals = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value not in docstrings
        ]
        assert not [lit for lit in literals if "TORTOISE_RECORD_VECTOR_LEG" in lit], (
            "the env var is spelled in CODE as well as in prose — it must be read "
            "through record_vector.ARMING_ENV only"
        )

    def test_it_reuses_the_m3_statistic_rather_than_copying_it(self):
        source = TOOL.read_text()
        assert "from tools.m3_fts_leg_recall import _binom_two_sided" in source
