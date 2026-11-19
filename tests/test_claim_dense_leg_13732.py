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
3b. **and the ID-SCOPED half of that guard** — `coverage` is a LABEL-WIDE count
   by design (`#11395` step 2), so it verifies any tenant that holds *some*
   embedded `Point` rows, including one that holds NONE of THIS population's ids.
   `record_population_vectors` must ask the record for the population's OWN id
   set before an arm runs: without it `record_dense` ranks nothing and publishes
   `recall@3 = 0.0` as a finding about the leg instead of a finding about the
   seed step;
4b. **the DSN comes from the module that owns it** — `record_vector` declares the
   ARM, not the DSN (`record_writer.record_dsn` does). The shipped call read
   `record_vector.record_dsn`, i.e. an `AttributeError` one expression BEFORE the
   `no_record_dsn` refusal the artifact promises, on exactly the documented
   recipe (`--dsn` defaults to `""`, the environment carries the value);
4. **the contradiction test, as a structural pin** — the dense leg comes FROM
   THE RECORD (`#11481` V1: Postgres is the record, FalkorDB is the projection).
   The instrument must therefore never create a graph vector index nor seed
   record rows; an AST scan over its CODE literals refuses either. A future lane
   that "fixes" the
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
import re
import subprocess
import sys
import types
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

    def test_the_docstring_table_quotes_the_same_numbers(self, tool_mod):
        """ONE spelling of each pre-registered threshold, in prose and in code.

        ⛔ `test_detectable_effect_at_the_m2_population` allows ±0.06 — which
        cannot separate 2.23 from 2.24 — so the module docstring's own copy of
        the table could drift from the computation and stay green. It did: the
        docstring quoted `2.24` where the committed block, the PR body and the
        test constant all say `2.23`. A pre-registration whose quoted threshold
        exists in two spellings is the one artifact where that is not a nit, so
        the table is read OUT of the docstring and compared here.
        """
        rows = re.findall(r"^(0\.\d{2})\s+(\d+\.\d{2})$", tool_mod.__doc__ or "", flags=re.M)
        assert len(rows) == 4, f"expected the 4-row table, read {rows}"
        for discordance, quoted in rows:
            assert float(quoted) == round(
                tool_mod.detectable_effect_pp(M2_N, float(discordance)), 2
            ), (discordance, quoted)

    def test_the_preregistration_needs_no_statistics_stack(self):
        """The reason `_erfinv` is hand-rolled — the fact prose cannot carry.

        ⛔ `scipy>=1.10` IS a direct dependency (`pyproject.toml`), so an earlier
        docstring's "scipy is not in the base install" was false and is the kind
        of wrong reason the next lane copies. What must stay true is that the
        instrument imports no such stack: two fixed normal quantiles are computed
        from `math`, so the pre-registration runs wherever `python` does.
        """
        tree = ast.parse(TOOL.read_text())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "scipy" not in imported, imported
        assert "numpy" not in imported, imported

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

    def test_the_dsn_is_read_through_the_module_that_owns_it(self, tool_mod, monkeypatch):
        """`record_vector` declares the ARM; `record_writer` declares the DSN.

        ⛔ The shipped call was `record_vector.record_dsn()` — an attribute that
        does not exist, so the documented recipe (no `--dsn`, the value in
        `TORTOISE_RECORD_DSN`) raised `AttributeError` one expression BEFORE the
        `no_record_dsn` refusal, and AFTER the graph had already been seeded.
        """
        source = TOOL.read_text()
        assert "record_vector.record_dsn" not in source
        tree = ast.parse(source)
        # ⛔ ANY `<module>.record_dsn` attribute read is the defect shape: the
        # function is `record_writer`'s, and asking `record_vector` (or an alias
        # of it) for it is the AttributeError. A text pin alone missed an alias.
        off_module = [
            n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "record_dsn"
        ]
        assert not off_module, [ast.dump(n)[:80] for n in off_module]
        assert "from tortoise.record_writer import" in source
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        assert any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_resolve_dsn"
            for n in ast.walk(run)
        ), "run_measurement must resolve the DSN through `_resolve_dsn`"
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "")
        assert tool_mod._resolve_dsn("") is None  # unset/blank is UNARMED, not an error
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://u:p@record.example/db")
        assert tool_mod._resolve_dsn("") == "postgresql://u:p@record.example/db"

    def test_a_malformed_dsn_is_a_named_refusal_not_a_traceback(self, tool_mod, monkeypatch):
        """A configured-but-unusable DSN is a misconfiguration, refused BY NAME.

        The seam raises `RecordWriteError` (wrong scheme, no host, whitespace);
        `main()` catches only `Refused`, so without this conversion the operator
        would get a traceback where the artifact promises a named precondition.
        """
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "mysql://user@host/db")
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod._resolve_dsn("")
        assert ei.value.code == "record_dsn_unusable"
        assert ei.value.exit_code == 2

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

    def test_the_population_guard_refuses_a_cohort_the_record_does_not_hold(self, tool_mod):
        """The label-wide count can be VERIFIED over a population in neither store.

        786 graph vectors and 800 record vectors is `verified=True` for the
        `point` label — and if the record holds NONE of this population's ids,
        `record_dense` ranks nothing and publishes `recall@3 = 0.0` as a finding
        about the leg. That is the `0 >= 0` trap one level deeper.
        """
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(0, 786)
        assert ei.value.code == "record_population_absent"
        assert "13210" in ei.value.message  # the backfill is named, not implied

    def test_the_population_guard_refuses_a_PARTIAL_cohort(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(785, 786)
        assert ei.value.code == "record_population_absent"

    def test_the_population_guard_refuses_an_unmeasurable_membership(self, tool_mod):
        with pytest.raises(tool_mod.Refused) as ei:
            tool_mod.require_population_in_record(None, 786)
        assert ei.value.code == "record_population_unmeasurable"

    def test_the_population_guard_passes_a_seeded_population(self, tool_mod):
        tool_mod.require_population_in_record(786, 786)  # no raise

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


# ── 5. the record's membership of the population (id-scoped, not label-wide) ─


class _FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


class TestRecordMembership:
    """The population's OWN ids must be in the record — the whole finding.

    ⛔ `record_vector.coverage` answers a LABEL-WIDE question by design, so it
    cannot see this. These cases red the mutants that make the guard label-wide
    or that turn an unreadable record into an empty one.
    """

    SCOPE = types.SimpleNamespace(org_id="org-1", graph_id="graph-1")

    def _ids(self, n: int) -> list[str]:
        return [f"pt-12186{i:06d}" for i in range(n)]

    def test_the_query_is_id_scoped_and_not_a_label_count(self, tool_mod, monkeypatch):
        cur = _FakeCursor((786,))
        monkeypatch.setattr("tortoise.record_serve.open_record", lambda *a, **k: _FakeConn(cur))
        monkeypatch.setattr("tortoise.record_projection.scope_connection", lambda conn, org_id: cur)
        ids = self._ids(786)
        got = tool_mod.record_population_vectors(ids, self.SCOPE, "postgresql://u@h/db")
        assert got == 786
        sql, params = cur.executed[-1]
        assert "= ANY(%s)" in sql, sql  # the ID SET is the predicate
        assert "count(*)" in sql
        assert "embedding_vec IS NOT NULL" in sql  # the column the SERVE slices
        assert params == ("org-1", "graph-1", ids)

    def test_an_unreadable_record_is_unmeasurable_not_empty(self, tool_mod, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no route to the record")

        monkeypatch.setattr("tortoise.record_serve.open_record", boom)
        assert tool_mod.record_population_vectors(self._ids(3), self.SCOPE, "dsn") is None

    def test_no_ids_is_not_a_verdict(self, tool_mod):
        assert tool_mod.record_population_vectors([], self.SCOPE, "dsn") is None

    def test_the_run_asks_the_id_scoped_question_before_any_arm(self):
        """The guard is WIRED, over the population's own ids, before the arms.

        ⛔ A guard nothing calls is the shape this finding started as: the
        label-wide gate verified, and no assertion anywhere asked the second
        question.
        """
        tree = ast.parse(TOOL.read_text())
        run = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "run_measurement"
        )
        calls = [n for n in ast.walk(run) if isinstance(n, ast.Call)]
        named = [c for c in calls if isinstance(c.func, ast.Name)]
        membership = [c for c in named if c.func.id == "record_population_vectors"]
        assert membership, "run_measurement never asks the record for the population's ids"
        first = membership[0].args[0]
        if isinstance(first, ast.Name):
            # Bound to a local first (the run also needs its length) — follow it.
            bound = [
                n
                for n in ast.walk(run)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == first.id for t in n.targets)
            ]
            assert bound, f"{first.id} is never assigned inside run_measurement"
            first = bound[0].value
        assert isinstance(first, ast.ListComp), ast.dump(first)[:120]
        assert isinstance(first.generators[0].iter, ast.Name)
        assert first.generators[0].iter.id == "population"
        guard = [c for c in named if c.func.id == "require_population_in_record"]
        assert guard, "the id-scoped measurement is never REFUSED on"
        # …and it must precede the arm loop, or a bad cohort is scored anyway.
        arms_loop = [
            n
            for n in ast.walk(run)
            if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == "ARMS"
        ]
        assert arms_loop
        assert min(c.lineno for c in guard) < min(a.lineno for a in arms_loop)


# ── 6. the contradiction test, structurally ───────────────────────────────


def _code_string_literals(tree: ast.AST) -> list[str]:
    """Every string LITERAL the module can hand to an engine — docstrings out.

    ⛔ THIS is the scan, not ``snippet in source``: a source-level substring
    cannot tell a Cypher read from a comment ABOUT that read, so it reds on the
    prose that explains the refusal while never distinguishing it from the
    executable string that would actually reverse the decision.

    Covered by construction: an f-string contributes its literal fragments as
    ``Constant`` nodes, and the parser folds adjacent literals into ONE constant,
    so ``f"CREATE VECTOR INDEX {name}"`` and ``"CREATE VECTOR " "INDEX"`` are
    both seen. Residual, stated rather than implied: two constants joined by
    ``+`` at RUNTIME are not folded, and no literal scan can see that.
    """
    docstrings = {
        ast.get_docstring(node, clean=False)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value not in docstrings
    ]


def _forbidden_snippets(source: str, snippets: tuple[str, ...]) -> list[str]:
    """Which forbidden snippets appear in ``source``'s CODE string literals."""
    literals = _code_string_literals(ast.parse(source))
    return [snippet for snippet in snippets if any(snippet in lit for lit in literals)]


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
        found = _forbidden_snippets(TOOL.read_text(), self.FORBIDDEN_SNIPPETS)
        assert not found, (
            f"{found} appear in the instrument's CODE — the dense leg comes from "
            f"the RECORD (#11481 V1), and a graph vector index here would "
            f"reverse a landed decision inside a measurement tool"
        )

    def test_the_scan_reads_code_literals_and_not_comments_or_prose(self):
        """The claim is "an AST scan" — pin what that buys, in BOTH directions.

        ⛔ An earlier cut asserted ``snippet not in source`` and the PR body still
        called it an AST scan: a substring over the file reds on a COMMENT or a
        docstring that explains the refusal (the prose is where a reader learns
        which index must stay on the record), which is a false block on correct
        code, and it treats prose and executables as the same event. The scan
        reads the literals the interpreter would hand to an engine.
        """
        snippet = "CREATE VECTOR INDEX"
        prose = (
            '"""Module prose: this instrument never issues CREATE VECTOR INDEX."""\n'
            "# a comment may name CREATE VECTOR INDEX too — that is not a write\n"
            "def f():\n"
            '    """Function prose: CREATE VECTOR INDEX.\n'
            '"""\n'
            "    return 1\n"
        )
        assert _forbidden_snippets(prose, (snippet,)) == []
        assert _forbidden_snippets('def f():\n    return "CREATE VECTOR INDEX "\n', (snippet,)) == [
            snippet
        ]
        assert _forbidden_snippets(
            'def f(name):\n    return f"CREATE VECTOR INDEX {name}"\n', (snippet,)
        ) == [snippet]
        assert _forbidden_snippets(
            'def f():\n    return "CREATE VECTOR " "INDEX"\n', (snippet,)
        ) == [snippet]  # adjacent literals fold into ONE constant

    def test_it_reads_the_record_leg_through_its_own_seam(self):
        """The arm is switched by the module that owns the switch, not re-spelt.

        ⛔ The literal is checked OUTSIDE docstrings: the prose is allowed to
        name the switch (a reader needs it), the CODE is not — one owner of the
        name, so a rename cannot leave this tool flipping a variable nothing
        reads.
        """
        source = TOOL.read_text()
        assert "record_vector.ARMING_ENV" in source
        literals = _code_string_literals(ast.parse(source))
        assert not [lit for lit in literals if "TORTOISE_RECORD_VECTOR_LEG" in lit], (
            "the env var is spelled in CODE as well as in prose — it must be read "
            "through record_vector.ARMING_ENV only"
        )

    def test_it_reuses_the_m3_statistic_rather_than_copying_it(self):
        source = TOOL.read_text()
        assert "from tools.m3_fts_leg_recall import _binom_two_sided" in source
