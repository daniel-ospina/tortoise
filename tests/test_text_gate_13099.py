"""#13099 — invariants the text-gate measurement must not lose.

These pin the parts of `tools/text_gate_13099.py` that would silently make the
published number worthless if they drifted, and they need no database:

* **the corpus is deterministic**, so the figures are reproducible at all;
* **the committed queries are reused verbatim** — a re-typed or re-ordered query
  set would break comparability to the recorded figures, which is the single
  most likely way this measurement produces a wrong answer;
* **the committed grade function is untouched**, so no label is invented;
* **the question class really is degenerate**, which the README offers as
  non-evidence — if that ever stops being true the README's caveat is wrong and
  this test says so;
* **the decision rule keys on the CI's DIRECTION.** This is the one that matters
  most: a significant GAIN is not a reason to keep the in-graph index, and a
  verdict function that treated "not within noise" as "disqualify" would reverse
  the decision the issue exists to test.

Run without a graph (the repo's suite needs its DB seam, so use the lane form)::

    uv run python tools/test_lane.py run -- tests/test_text_gate_13099.py -q
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from benchmarks.synthetic_corpus import build_topic_oracle
from tools.text_gate_13099 import (
    N_POINTS,
    QUERY_CLASSES,
    SEED,
    build_corpus,
    build_query_classes,
    committed_queries_digest,
    labels_for,
    load_committed_queries,
    oov_evidence,
    verdict,
)


def _corpus_digest() -> str:
    _, points, live = build_corpus()
    h = hashlib.sha256()
    for p in points:
        h.update(f"{p['id']}\t{p['content']}\t{p['status']}\t{p['topic']}\n".encode())
    for topic in sorted(live):
        h.update(f"{topic}:{','.join(live[topic])}\n".encode())
    return h.hexdigest()


def test_corpus_is_deterministic():
    """Two builds of the committed corpus are identical, byte for byte.

    Without this the artifact is not reproducible and no comparison to the
    recorded figures means anything.
    """
    assert _corpus_digest() == _corpus_digest()


def test_corpus_matches_the_committed_shape():
    oracle, points, _ = build_corpus()
    assert len(points) == N_POINTS
    assert oracle.n == 24
    assert all(p["id"].startswith("p-") for p in points)
    # topics are round-robin from the seed — every topic is populated
    assert len({p["topic"] for p in points}) == oracle.n


def test_short_keyword_class_reuses_the_committed_queries_verbatim():
    """The anchor class must be the committed set, not a re-typed copy."""
    _, points, _ = build_corpus()
    committed = load_committed_queries()
    classes = build_query_classes(build_topic_oracle(seed=SEED), points, committed)

    got = classes["short_keyword"]
    assert len(got) == len(committed) == 100
    for c, q in zip(got, committed, strict=True):
        assert c["text"] == q["query"]
        assert c["target"] == q["oracle_target"]
        assert c["qid"] == q["id"]


# The committed query file's SHA-256, as of the artifact this evidence record
# publishes. ⛔ The comparison above is builder-vs-its-own-input, so it pins "no
# re-typing INSIDE the builder" and nothing more: editing this file's text, a
# query's `oracle_target`, or the row order moves every figure in §5 while all
# of those assertions stay green — and a drifted query set is the single most
# likely way this measurement produces a wrong answer (module docstring). The
# digest is what ties the published numbers to the input that produced them.
COMMITTED_QUERIES_SHA256 = "f39c035c291fb5c369e86b43048190a16b55423f1cdaa9a86983c9e8456dd1c1"


def test_the_committed_query_file_is_byte_identical_to_the_measured_one():
    """A changed query set must RED this file, not silently re-date every table.

    If a query set genuinely needs to change, that is a new measurement: re-run
    the harness, republish the artifact and the README tables, and update this
    digest in the same change.
    """
    assert committed_queries_digest() == COMMITTED_QUERIES_SHA256, (
        "tests/eval/retrieval/queries/oracle_queries.json changed — the §5 tables "
        "and the committed artifact were produced from a different query file. "
        "Re-run tools/text_gate_13099.py and republish, or revert the edit.")


def test_all_classes_reuse_the_committed_grade_function():
    """The grade function is unchanged: grade 2 = target, grade 1 = NEAR, rest 0.

    ⛔ Grade 1 is pinned HERE because IDCG depends on it. `ndcg_at_k` builds the
    ideal DCG from BOTH the grade-2 count (`n_rel`) and the grade-1 count
    (`n_part`), so a change to the NEAR-bridge logic in
    `oracle_grades_for_query` moves every nDCG@10/@100 in the record even while
    a grade-2-only assertion stays green. The whole PARTITION is asserted, on a
    sample of topics, and it must exhaust the live set with no extra labels.
    """
    oracle, _points, live = build_corpus()
    for topic in (0, 7, 23):
        labels = labels_for(oracle, live, topic)
        grade2 = {pid for pid, g in labels.items() if g == 2}
        grade1 = {pid for pid, g in labels.items() if g == 1}
        grade0 = {pid for pid, g in labels.items() if g == 0}
        assert grade2 == set(live[topic])
        # grade 1 is EXACTLY the union of the target's NEAR topics' live points
        near = set(oracle.near(topic))
        assert near, "the oracle must have NEAR topics, or grade 1 is empty and IDCG collapses"
        assert grade1 == {pid for t in near for pid in live.get(t, [])}
        assert not (grade1 & grade2)
        assert all(g in (0, 1, 2) for g in labels.values())
        # the partition exhausts the judged population: no live point is unlabelled
        assert grade2 | grade1 | grade0 == {pid for ids in live.values() for pid in ids}


def test_query_classes_are_populated():
    _, points, _ = build_corpus()
    classes = build_query_classes(
        build_topic_oracle(seed=SEED), points, load_committed_queries())
    assert set(classes) == set(QUERY_CLASSES)
    assert len(classes["short_keyword"]) == 100
    assert len(classes["natural_language_question"]) == 100
    assert len(classes["entity_name"]) == 96  # four documents x 24 topics


def test_question_class_is_measurably_degenerate():
    """The README offers this class as NON-evidence; that claim is measured.

    If the scaffold tokens ever became in-vocabulary, the class would start
    carrying independent signal and the README would need to promote it rather
    than warn about it. Failing here is the signal to revisit the README.
    """
    _, points, _ = build_corpus()
    classes = build_query_classes(
        build_topic_oracle(seed=SEED), points, load_committed_queries())
    ev = oov_evidence(points, classes)
    assert ev["added_scaffold_tokens"] > 500
    assert ev["oov_fraction"] > 0.90


# ── the decision rule — the invariant that protects the decision itself ─────

def _results(arm_status: dict[str, dict[str, tuple[float, float, float]]]) -> dict:
    """Build a minimal `results` shape: {arm: {class: (mean, lower, upper)}}."""
    out: dict = {}
    for cls in ("short_keyword", "entity_name"):
        arms = {
            "in_graph_fulltext": {"metrics": {}, "n_queries": 1},
        }
        for arm, per_cls in arm_status.items():
            mean, lo, hi = per_cls.get(cls, (0.0, 0.0, 0.0))
            arms[arm] = {
                "n_queries": 1,
                "metrics": {},
                "delta_vs_baseline": {
                    "ndcg@10": {"mean": mean, "lower": lo, "upper": hi, "n": 100},
                },
            }
        out[cls] = {"n_queries": 1, "arms": arms}
    return out


def test_a_significant_gain_is_not_a_disqualification():
    """+5 pts with a CI above 0 is BETTER — it must never be read as a loss.

    This is the defect the verdict function exists to avoid: treating "outside
    the noise band" as "disqualify" would reverse the decision the issue tests.
    """
    v = verdict(_results({"pg_ts_rank": {
        "short_keyword": (5.0, 1.0, 9.0),
        "entity_name": (5.0, 1.0, 9.0),
    }}))
    assert v["headline_ndcg@10"]["pg_ts_rank"]["short_keyword"]["status"] == "better"
    assert "pg_ts_rank" in v["qualifying_candidates"]


def test_a_significant_loss_disqualifies():
    v = verdict(_results({"pg_bad": {
        "short_keyword": (-9.0, -14.0, -4.0),
        "entity_name": (0.0, -2.0, 2.0),
    }}))
    assert v["headline_ndcg@10"]["pg_bad"]["short_keyword"]["status"] == "worse"
    assert "pg_bad" not in v["qualifying_candidates"]


def test_a_noise_band_result_qualifies():
    """The middle case: a CI spanning zero is neither a gain nor a loss."""
    v = verdict(_results({"pg_ts_rank": {
        "short_keyword": (2.3, -0.9, 5.5),
        "entity_name": (0.4, -1.0, 1.9),
    }}))
    assert v["headline_ndcg@10"]["pg_ts_rank"]["short_keyword"]["status"] == "within_noise"
    assert "pg_ts_rank" in v["qualifying_candidates"]


def test_the_raw_query_diagnostic_is_never_a_candidate():
    """`in_graph_fulltext_raw` exists to decompose builder-vs-scorer.

    It drops the OR-union the product builds, so it loses by construction. A
    verdict that offered it as an adoptable candidate would be nonsense.
    """
    v = verdict(_results({"in_graph_fulltext_raw": {
        "short_keyword": (40.0, 30.0, 50.0),
        "entity_name": (40.0, 30.0, 50.0),
    }}))
    assert "in_graph_fulltext_raw" not in v["qualifying_candidates"]
    assert "in_graph_fulltext_raw" in v["diagnostic_arms_not_candidates"]


def test_the_corpus_is_built_without_touching_a_graph(monkeypatch):
    """The corpus must be buildable with NO database, or the pins are not reusable.

    The property is asserted, not assumed: `falkordb` is replaced by a module
    whose every attribute access raises, so any attempt to reach a graph while
    building the corpus or the query classes fails this test.
    """
    import sys
    import types

    class _NoGraph(types.ModuleType):
        def __getattr__(self, name: str):  # only reached if someone connects
            raise AssertionError(
                f"the corpus build imported falkordb.{name} — it must need no graph")

    monkeypatch.setitem(sys.modules, "falkordb", _NoGraph("falkordb"))
    oracle, points, live = build_corpus()
    classes = build_query_classes(oracle, points, load_committed_queries())
    oov_evidence(points, classes)  # the README's degeneracy claim must need no graph
    assert len(points) == N_POINTS and live


# ── the published record must agree with the committed artifact ─────────────
#
# These are the pins that make the README's numbers auditable instead of
# asserted: the evidence record is what a reader acts on, and a table that has
# quietly drifted from `artifact.json` is indistinguishable from a correct one
# until someone recomputes it by hand (which is how the operational-facts row
# carried a figure belonging to a different backend's table, and build times
# that appear in no run at all).

DOC_DIR = Path("docs") / "evidence" / "13099-text-gate"
ARTIFACT = DOC_DIR / "artifact.json"
ARTIFACT_REPEAT = DOC_DIR / "artifact-repeat.json"
README = DOC_DIR / "README.md"

# The README's §5 tables print 4 decimal places, the artifact stores 6.
_DP = 4

_ARM_ROW = re.compile(r"^\|\s*(?:⛔\s*)?`(?P<arm>[a-z0-9_]+)`")
_SEC5_HEAD = re.compile(r"^### 5\.\d\s+`(?P<cls>[a-z_]+)`")


def _artifact() -> dict:
    import json

    return json.loads(ARTIFACT.read_text())


def _published_rows() -> dict[str, dict[str, list[str]]]:
    """Parse §5's per-class arm rows into the SIGNED numbers each row prints.

    {class: {arm: [ndcg@10, ndcg@100, recall@100, (d10 mean,lo,hi) x3, returned]}}
    for a non-baseline arm, `[ndcg@10, nDCG@100, recall@100, returned]` for the
    baseline (its Δ cells are em-dashes, which yield no number). Everything is
    normalised to ASCII: the README prints the Δ sign as U+2212.
    """
    out: dict[str, dict[str, list[str]]] = {}
    cls: str | None = None
    for line in README.read_text().splitlines():
        head = _SEC5_HEAD.match(line)
        if head:
            cls = head.group("cls")
            out.setdefault(cls, {})
            continue
        if cls is None or not line.startswith("|"):
            continue
        m = _ARM_ROW.match(line)
        if not m:
            continue
        row = line.replace("**", "").replace("\u2212", "-").replace("\u00d7", "x")
        # only the cells after the arm name, and only numeric content
        body = row.strip().strip("|").split("|")[1:]
        nums = re.findall(r"-?\d+\.\d+", " ".join(body))
        out[cls][m.group("arm")] = nums
    return out


def _same_cells(got: list[str], want: list[str]) -> bool:
    """Cellwise equality, with the `returned` column compared numerically.

    That column is the only one whose value the artifact stores pre-rounded
    (`mean_returned`, 2 dp), so `20.25` prints `20.2` or `20.3` depending on the
    formatter's tie rule — a difference of no consequence, unlike a drifted
    metric or delta, which this still compares exactly.
    """
    if len(got) != len(want):
        return False
    for i, (g, w) in enumerate(zip(got, want, strict=True)):
        if i == len(want) - 1:
            # A 1-dp tie is the whole width (20.25 prints 20.2 or 20.3 depending
            # on the formatter); 0.15 leaves for binary float noise in 20.3-20.2.
            if abs(float(g) - float(w)) > 0.15:
                return False
        elif g != w:
            return False
    return True


def test_the_published_tables_are_the_artifact_to_four_decimals():
    """EVERY §5 number is recomputed from `artifact.json` — not just the metrics.

    A table that drifts from the artifact is the evidence record going wrong in
    the one direction a reader cannot check cheaply. The record's HEADLINE number
    is a Δ (and §0 repeats it), so pinning only the three metric columns would
    leave the figure the decision rests on free to drift — which is exactly how
    the §6 row this file also pins came to quote numbers no run had produced.
    """
    art = _artifact()
    published = _published_rows()
    assert set(published) == set(art["results"]), (
        "the README's §5 classes and the artifact's classes disagree: "
        f"{sorted(published)} vs {sorted(art['results'])}")

    drift: list[str] = []
    for cls, rows in published.items():
        blk = art["results"][cls]
        assert rows, f"§5.{cls} parsed no arm rows — the parser, not the record, is wrong"
        # ARM COMPLETENESS: a row deleted from the table is drift, not a shortcut.
        if set(rows) != set(blk["arms"]):
            drift.append(f"§5 {cls}: README carries {sorted(rows)}, "
                         f"artifact carries {sorted(blk['arms'])}")
        for arm, nums in rows.items():
            if arm not in blk["arms"]:
                continue
            metrics = blk["arms"][arm]["metrics"]
            want = [f"{metrics[m]:.{_DP}f}"
                    for m in ("ndcg@10", "ndcg@100", "recall@100")]
            if arm == "in_graph_fulltext":
                want.append(f"{blk['result_depth'][arm]['mean_returned']:.1f}")
            else:
                deltas = blk["arms"][arm].get("delta_vs_baseline") or {}
                for m in ("ndcg@10", "ndcg@100", "recall@100"):
                    d = deltas[m]
                    want += [f"{d['mean']:.2f}", f"{d['lower']:.2f}", f"{d['upper']:.2f}"]
                want.append(f"{blk['result_depth'][arm]['mean_returned']:.1f}")
            if not _same_cells(nums, want):
                drift.append(f"§5 {cls}/{arm}: README {nums} != artifact {want}")
    assert drift == [], "\n".join(drift)


def _sec6_row(label: str) -> str:
    """The §6 table row whose primary cell names `label` (one row, by position)."""
    readme = README.read_text()
    sec6 = readme.split("## 6. Operational facts", 1)[1].split("\n## ", 1)[0]
    for line in sec6.splitlines():
        if line.startswith("|") and label in line.split("|")[1]:
            return line
    raise AssertionError(f"§6 has no row for {label!r} — the table or this pin is wrong")


def test_the_operational_facts_table_quotes_the_artifact():
    """§6's figures come from `artifact.json`, in the RIGHT ROW.

    This table is the adoption-cost argument and it is labelled as measured, so
    it must quote measurements the committed artifact holds — and each figure
    must sit under the backend that produced it. Both halves are pinned here
    because both have been wrong: build times that appear in NO run, and a
    7.58 MB figure that is the POSTGRES table's size (`sizes_bytes.tg_docs`)
    printed under a FalkorDB `used_memory` label. A presence-only check over the
    whole section would pass a table with the two labels swapped.
    """
    art = _artifact()
    graph = art["corpus"]["graph_seed"]
    pg = art["corpus"]["postgres_seed"]
    sizes = pg["sizes_bytes"]

    per_row = {
        "in-graph": [
            f"{graph['fulltext_index_build_s']:.3f} s",   # index build
            f"{graph['seed_s']:.3f} s",                   # seeding
            f"{graph['graph_memory_usage']['used_memory_human'].rstrip('M')} MB",
        ],
        "ts_rank": [
            f"{pg['gin_index_build_s']:.3f} s",
            f"{sizes['tg_docs_tsv_en'] / 1024:.0f} KB",
            f"{sizes['tg_docs_tsv_simple'] / 1024:.0f} KB",
        ],
        "pg_search": [
            f"{pg['bm25_index_build_s']:.3f} s",
            f"{sizes['tg_docs_bm25'] / 1e6:.2f} MB",
            f"{round(sizes['tg_docs_bm25'] / sizes['tg_docs_tsv_en'])}x",
        ],
    }
    drift: list[str] = []
    for label, expected in per_row.items():
        row = _sec6_row(label)
        for e in expected:
            # the ratio is printed as "48×"; normalise the glyph here, not in the row
            if e.replace("x", "\u00d7") in row or e in row:
                continue
            drift.append(f"§6 row {label!r} is missing {e!r} (row: {row.strip()[:120]})")
    assert drift == [], "\n".join(drift)


def test_the_like_for_like_store_comparison_is_quoted():
    """The adoption-cost claim compares INDEX to INDEX and STORE to STORE.

    §0's "cheapest by a wide margin" sets a 64 KB GIN index against the whole
    FalkorDB store (7.73 MB `used_memory`, embeddings included). The like-for-like
    Postgres figure is the table itself — content + `vector(384)` + both
    tsvectors — and the artifact holds it (`sizes_bytes.tg_docs`). Quoting only
    the index would overstate the difference by two orders of magnitude, so it is
    pinned: the two store figures must appear together.
    """
    art = _artifact()
    store_pg_mb = art["corpus"]["postgres_seed"]["sizes_bytes"]["tg_docs"] / 1e6
    store_fg_mb = float(
        art["corpus"]["graph_seed"]["graph_memory_usage"]["used_memory_human"].rstrip("M"))
    readme = README.read_text()
    sec0 = readme.split("## 0.", 1)[1].split("\n## ", 1)[0]
    assert f"{store_pg_mb:.1f} MB" in sec0 or f"{store_pg_mb:.2f} MB" in sec0, (
        f"§0 never quotes the Postgres store ({store_pg_mb:.2f} MB from "
        "sizes_bytes.tg_docs), so its index-vs-store comparison is not like-for-like")
    assert f"{store_fg_mb:.2f} MB" in sec0 or f"{store_fg_mb:.1f} MB" in sec0, (
        "§0 never quotes the FalkorDB store figure from `used_memory`")


def test_an_arm_that_raised_is_disqualified_not_scored_as_a_loss():
    """The declared rule, implemented: an uninstallable arm is NEVER a low score.

    Before this, the per-query handler recorded the exception and scored the arm
    0.0 on every query — which the verdict read as a significant LOSS, i.e. the
    decision text "the text index stays" produced by a candidate nobody managed
    to install. The direction of that failure is the whole point of the issue.
    """
    results = _results({"pg_bm25": {
        "short_keyword": (-81.7, -86.6, -76.4),   # what an empty list looks like
        "entity_name": (-95.1, -97.8, -92.5),
    }})
    errors = [{"class": "short_keyword", "qid": "kw-0", "arm": "pg_bm25",
               "error": "ModuleNotFoundError: No module named 'pg_search'"}]

    v = verdict(results, errors)

    assert v["headline_ndcg@10"]["pg_bm25"]["short_keyword"]["status"] == "disqualified"
    assert v["disqualified_arms"] == ["hybrid_bm25", "pg_bm25"], (
        "the hybrid built from the dead lexical leg must be disqualified too")
    assert "pg_bm25" not in v["qualifying_candidates"]
    assert "DISQUALIFIED" in v["decision"], (
        "the decision line must say a candidate was not scored, or the reader "
        "cannot tell a refusal-to-install from a measured loss")

    # …and the per-class block is honoured on its own, for a caller that passes
    # no error list (a re-read of an artifact).
    results["short_keyword"]["arm_errors"] = {"pg_bm25": 100}
    v2 = verdict(results)
    assert v2["headline_ndcg@10"]["pg_bm25"]["short_keyword"]["status"] == "disqualified"

    # a CLEAN arm is unaffected — the rule must not swallow real losses
    clean = verdict(_results({"pg_bad": {
        "short_keyword": (-9.0, -14.0, -4.0), "entity_name": (0.0, -2.0, 2.0)}}))
    assert clean["headline_ndcg@10"]["pg_bad"]["short_keyword"]["status"] == "worse"


def test_the_rule_checks_every_published_metric_not_just_the_head():
    """A depth-only loss is a real reason not to adopt, so it must disqualify.

    The record's claim is "no Postgres option loses significantly on any query
    class", and §5/§6 publish nDCG@10, nDCG@100 and recall@100 for every arm and
    class. A rule that read nDCG@10 alone would call an arm `qualifying` that
    lost significantly AT DEPTH — an unstated narrowing of the claim, which is
    the class of defect this record has already had to correct once.
    """
    results = _results({"pg_deep": {
        "short_keyword": (2.0, -1.0, 5.0),   # head: inside the noise band
        "entity_name": (2.0, -1.0, 5.0),
    }})
    deltas = results["short_keyword"]["arms"]["pg_deep"]["delta_vs_baseline"]
    deltas["ndcg@100"] = {"mean": -9.0, "lower": -14.0, "upper": -4.0, "n": 100}

    v = verdict(results)

    assert v["headline_ndcg@10"]["pg_deep"]["short_keyword"]["status"] == "within_noise"
    assert v["headline"]["pg_deep"]["short_keyword"]["ndcg@100"]["status"] == "worse"
    assert v["worst_status"]["pg_deep"]["short_keyword"] == "worse"
    assert "pg_deep" not in v["qualifying_candidates"], (
        "a significant loss at depth must disqualify — the record publishes "
        "depth as a metric, so it cannot then decide on the head alone")
    assert v["metrics_checked"] == ["ndcg@10", "ndcg@100", "recall@100"]


def test_both_committed_verdicts_are_what_this_code_produces():
    """Each artifact's verdict must reproduce from its OWN `results`.

    Pinned because the alternative happened: the decision rule was edited and
    the published verdict left stating the OLD rule, so the record asserted a
    conclusion its own numbers no longer supported. The repeat run is checked
    against ITS results too — copying one verdict onto the other would be
    fabricated agreement.
    """
    import json

    for path in (ARTIFACT, ARTIFACT_REPEAT):
        art = json.loads(path.read_text())
        assert art["verdict"] == verdict(art["results"], art.get("errors")), (
            f"{path.name}'s verdict is stale relative to `verdict()` — regenerate "
            "it from that file's own `results`")
        assert art["verdict"]["metrics_checked"] == [
            "ndcg@10", "ndcg@100", "recall@100"]

    first = json.loads(ARTIFACT.read_text())["verdict"]
    repeat = json.loads(ARTIFACT_REPEAT.read_text())["verdict"]
    assert first["qualifying_candidates"] == repeat["qualifying_candidates"]


def test_a_dead_baseline_produces_no_verdict():
    """If the ARM EVERYTHING IS MEASURED AGAINST raised, there is no verdict.

    The basline's empty result list makes every candidate look like a large gain,
    so the decision line would say the opposite of what the instrument knows —
    with a `disqualified` note about the baseline sitting beside it. The rule is
    the same failure direction as the uninstallable-candidate case, moved to the
    reference arm, so it gets the same treatment: refuse.
    """
    v = verdict(_results({"pg_ts_rank": {
        "short_keyword": (40.0, 30.0, 50.0), "entity_name": (40.0, 30.0, 50.0)}}),
        [{"class": "short_keyword", "qid": "kw-0",
          "arm": "in_graph_fulltext", "error": "ConnectionError: refused"}])

    assert v["baseline_disqualified"] is True
    assert v["qualifying_arms"] == [] and v["qualifying_candidates"] == []
    assert "NO VERDICT" in v["decision"]
    assert "pg_ts_rank" not in v["decision"], (
        "a dead baseline must not leave a candidate named as the answer")


def test_an_arm_built_from_a_disqualified_arm_is_disqualified_too():
    """`rrf_fusion([[], vlist])` SUCCEEDS — so a half-dead hybrid would be scored.

    Both hybrids are built from other arms: one leg empty yields a valid fused
    list, an ordinary-looking metric, and a qualifying candidate. The rule has to
    close over the dependency graph or it is defeated one level up.
    """
    results = _results({"hybrid_bm25": {
        "short_keyword": (0.5, -1.0, 2.0), "entity_name": (0.5, -1.0, 2.0)}})
    results["short_keyword"]["arms"]["pg_bm25"] = {
        "n_queries": 1, "metrics": {},
        "delta_vs_baseline": {"ndcg@10": {"mean": 0.0, "lower": -1.0,
                                          "upper": 1.0, "n": 100}}}

    v = verdict(results, [{"class": "short_keyword", "qid": "kw-0",
                           "arm": "pg_bm25", "error": "ModuleNotFoundError"}])
    assert "hybrid_bm25" in v["disqualified_arms"], (
        "the hybrid's lexical leg raised, so its numbers are not a measurement of "
        "hybrid_bm25")
    assert "hybrid_bm25" not in v["qualifying_candidates"]

    # …and via the vector leg, which has no row of its own in any table
    v2 = verdict(_results({"hybrid_baseline": {
        "short_keyword": (0.5, -1.0, 2.0), "entity_name": (0.5, -1.0, 2.0)}}),
        [{"class": "short_keyword", "qid": "kw-0", "arm": "pg_vector",
          "error": "psycopg.OperationalError"}])
    assert "hybrid_baseline" in v2["disqualified_arms"]

