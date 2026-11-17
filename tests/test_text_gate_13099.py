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
import json
import re
import subprocess
from pathlib import Path

import pytest

from benchmarks.synthetic_corpus import build_topic_oracle
from tools.text_gate_13099 import (
    GRAPH_NAME,
    N_POINTS,
    QUERY_CLASSES,
    SEED,
    TOKENIZER_FALLBACKS,
    build_corpus,
    build_query_classes,
    committed_queries_digest,
    labels_for,
    load_committed_queries,
    oov_evidence,
    or_tokens,
    seed_graph,
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
    # ⛔ AND THE PUBLISHED NUMBER IS PINNED, NOT ONLY THE COMPUTED ONE. §4 states
    # these as "measured, not merely asserted (`question_class_evidence` in the
    # artifact)" — a pointer nothing dereferenced. Measured: changing the
    # artifact's `question_class_evidence` AND §4's matching sentence together left
    # this file GREEN, because the asserts above look only at what the code
    # computes. That is the defect class this file fixes for §5's tables, §0/§6's
    # savings and §6's prose facts, one surface over: an assertion on the code
    # protects the code, not the record.
    art = _artifact()
    assert art["question_class_evidence"] == ev, (
        "artifact.json's question_class_evidence is stale relative to "
        "oov_evidence() — §4's figure would describe a corpus this code no "
        "longer builds")
    sec4 = _readme_section(
        "4. The three query classes, and the one that cannot be read as evidence")
    # ⛔ ONE CONTIGUOUS PHRASE, NOT TWO NUMBERS. Pinning `96.46 %` and `650` as
    # independent substrings left the sentence's POLARITY unguarded: rewriting
    # "are absent from the corpus token pool" to "are present in" keeps both
    # figures and stayed GREEN (measured) — the record would then publish the
    # inverse of the fact that makes the class degenerate. The phrase carries the
    # direction, so the direction is what is pinned.
    assert (
        f"**{ev['oov_fraction'] * 100:.2f} %** of the "
        f"{ev['added_scaffold_tokens']} scaffold tokens it adds are absent from "
        "the corpus token pool") in sec4, (
        "§4 does not carry the measured degeneracy claim — expected "
        f"\"**{ev['oov_fraction'] * 100:.2f} %** of the "
        f"{ev['added_scaffold_tokens']} scaffold tokens it adds are absent from "
        "the corpus token pool\"")


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

    ⛔ AND A MODULE-SCOPE BINDING IS CHECKED IN BOTH FILES. An attribute-only
    shim installed AFTER the import cannot see a binding created at import time:
    `from falkordb import FalkorDB` at module scope resolves before this test
    runs, and the later call then reaches the real class without touching the
    shim (measured). Two arms close that:

      * `benchmarks.synthetic_corpus` is RELOADED under the shim, so its body
        re-executes against it;
      * BOTH files are scanned at MODULE SCOPE for a `falkordb` import, because
        the file that actually names `falkordb` is the HARNESS
        (`tools/text_gate_13099.py` — its two sites are function-local today) and
        reloading a module does not rebind the names a caller imported from it.
        ⛔ The harness is NOT reloaded instead: reload re-executes its body and
        rebinds module-level state other pins depend on (measured —
        `TOKENIZER_FALLBACKS` came back as a fresh list, and
        `test_a_tokenizer_fallback_is_recorded_once_per_query_text` went red
        because it holds the pre-reload object). Scanning is side-effect free.
        (Measured: the harness escape passed GREEN before this scan existed and
        fails after.)
    """
    import ast
    import importlib
    import pathlib
    import sys
    import types

    class _NoGraph(types.ModuleType):
        def __getattr__(self, name: str):  # only reached if someone connects
            raise AssertionError(
                f"the corpus build imported falkordb.{name} — it must need no graph")

    monkeypatch.setitem(sys.modules, "falkordb", _NoGraph("falkordb"))
    import benchmarks.synthetic_corpus as corpus_mod
    importlib.reload(corpus_mod)  # the corpus BODY now runs against the shim
    import tools.text_gate_13099 as harness

    # module-scope imports only — a function-local `from falkordb import …` is the
    # shape this harness uses on purpose, and the shim above catches it at call time
    offenders: list[str] = []
    for mod in (corpus_mod, harness):
        tree = ast.parse(pathlib.Path(mod.__file__).read_text())
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.split(".")[0] == "falkordb" for n in names):
                offenders.append(f"{pathlib.Path(mod.__file__).name}:{node.lineno}")
    assert offenders == [], (
        "a MODULE-SCOPE falkordb import escapes the `_NoGraph` shim (the binding is "
        f"created before this test runs): {offenders} — keep it function-local")

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
            f"{graph['graph_memory_usage']['used_memory_human'].rstrip('M')} MiB",
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
    # ── THE PROSE FACTS, BY ROW ─────────────────────────────────────────────
    # §6's adoption-cost argument is not only numbers: the licence, the extension
    # version, the `requires`/relocatability cell and the exact preload list are
    # what the PREFERENCE between `ts_rank` and `pg_search` turns on ("no preload,
    # no AGPL"). Until this loop existed the README said these were
    # "presence-checked" and NOTHING checked them — measured at the head that added
    # this pin: `grep -c 'AGPL\|preload\|0.26.6\|copy_s' tests/test_text_gate_13099.py`
    # → 0, 0, 0, 0 — so `AGPL-3.0-or-later`→`MIT` or the preload list rewritten to
    # one extension redded nothing. That is the defect class this PR's own
    # `config/ci-surfaces.yml` rows cite by name ("claimed coverage that did not
    # exist"). `copy_s` is deliberately NOT required here: §6 prints no such cell,
    # and the record states that it is pinned by nothing rather than implying it is.
    per_row_prose = {
        "in-graph": ["| FalkorDB |"],
        "ts_rank": ["| PostgreSQL |"],
        "pg_search": [
            "**AGPL-3.0-or-later**",
            "0.26.6",
            "requires = 'vector'",
            "not relocatable",
            "`shared_preload_libraries` change required",
            "`pg_search,pg_cron,pg_stat_statements`",
        ],
    }
    for label, expected in per_row_prose.items():
        row = _sec6_row(label)
        for e in expected:
            if e not in row:
                drift.append(
                    f"§6 row {label!r} is missing its prose fact {e!r} "
                    f"(row: {row.strip()[:160]})")
    # …and the REPEAT-run figures printed in the same cells. They come from the
    # other artifact, so they were unguarded until now: mutating every one of the
    # four left the pin green.
    repeat = json.loads(ARTIFACT_REPEAT.read_text())
    r_graph = repeat["corpus"]["graph_seed"]
    r_pg = repeat["corpus"]["postgres_seed"]
    repeat_expected = {
        "in-graph": [
            f"{r_graph['seed_s']:.3f} s",
            f"{r_graph['graph_memory_usage']['used_memory_human'].rstrip('M')} MiB",
        ],
        "ts_rank": [f"{r_pg['gin_index_build_s']:.3f} s"],
        "pg_search": [f"{r_pg['bm25_index_build_s']:.3f} s"],
    }
    for label, expected in repeat_expected.items():
        row = _sec6_row(label)
        for e in expected:
            if e not in row:
                drift.append(f"§6 row {label!r} is missing its repeat-run figure {e!r}")
    assert drift == [], "\n".join(drift)


def _postgres_table_bytes(art: dict) -> int:
    """The table's OWN bytes: `sizes_bytes.tg_docs` MINUS every index on it.

    ⛔ `tg_docs` is `pg_total_relation_size` (= heap + TOAST + indexes, per
    docs/ops/postgres-storage-measurement.md), so reading it as "the store"
    charges the 3.13 MB BM25 index to the `ts_rank` candidate. The identity
    `pg_total_relation_size = pg_table_size + pg_indexes_size` makes the
    subtraction exactly `pg_table_size` — heap **plus TOAST plus FSM/VM**, not the
    bare main fork. That is deliberately the SAME quantity the harness now records
    as `tg_docs_table` (and NOT `pg_relation_size`, which is the main fork alone:
    the two agree only while TOAST is empty, and diverging silently on the first
    run whose content TOASTs is exactly the kind of stale field this record
    exists not to have).
    """
    s = art["corpus"]["postgres_seed"]["sizes_bytes"]
    indexes = (s["tg_docs_tsv_en"] + s["tg_docs_tsv_simple"]
               + s["tg_docs_bm25"] + s["tg_docs_pkey"])
    return s["tg_docs"] - indexes


def _readme_section(heading: str) -> str:
    """One README section, from its `## <heading>` line to the next `## `."""
    text = README.read_text()
    start = text.index(f"## {heading}")
    return text[start:].split("\n## ", 1)[0]


def test_the_like_for_like_store_comparison_is_quoted():
    """The adoption-cost claim compares INDEX to INDEX and STORE to STORE.

    §0's "cheapest" comparison used to set a 64 KB GIN index against the whole
    FalkorDB store and then quote `sizes_bytes.tg_docs` (`pg_total_relation_size`:
    heap + EVERY index) as if that were the `ts_rank` store — charging ParadeDB's
    3.13 MB BM25 index to an option that does not use it. Both halves are pinned
    here, and ⛔ **in §0 SPECIFICALLY**: §6's prose repeats every one of these
    figures, so a whole-document presence check was blind to §0 going entirely
    wrong (measured: corrupting §0's figures left it green). §0 is the headline a
    reader acts on, so it is the section that has to carry them.
    """
    art = _artifact()
    sizes = art["corpus"]["postgres_seed"]["sizes_bytes"]
    table_mb = _postgres_table_bytes(art) / 1e6
    ts_rank_mb = (_postgres_table_bytes(art) + sizes["tg_docs_tsv_en"]) / 1e6
    # ⛔ Redis prints `used_memory_human` in 1024-BASED units, so `7.73M` is
    # 7.73 MiB = 8.11 MB in the decimal units the Postgres figures use. Reading it
    # as 7.73 MB made the quoted saving 44 % instead of 47 % — comparing MiB
    # against MB while the paragraph tells the reader to compare like with like.
    graph_mib = float(
        art["corpus"]["graph_seed"]["graph_memory_usage"]["used_memory_human"].rstrip("M"))
    graph_mb = graph_mib * 1024 * 1024 / 1e6
    saving_pct = (1 - ts_rank_mb / graph_mb) * 100
    sec0 = _readme_section("0. Verdict")

    assert abs(table_mb - 4.23) < 0.02, "the table derivation moved — recheck the relation"
    expected = {
        "the table's own bytes": f"{table_mb:.2f} MB",
        "the ts_rank store (table + its GIN)": f"{ts_rank_mb:.1f} MB",
        "the graph's reading in its own (1024-based) unit": f"{graph_mib:.2f} MiB",
        "the graph figure in decimal MB": f"{graph_mb:.2f} MB",
        "the store-to-store saving": f"{saving_pct:.0f} %",
        "the pg_search store (table + BM25 index)": f"{table_mb + sizes['tg_docs_bm25'] / 1e6:.1f} MB",
    }
    missing = [f"{what} ({val})" for what, val in expected.items() if val not in sec0]
    assert missing == [], (
        "§0 does not carry these adoption-cost figures, so the like-for-like claim "
        "cannot be checked where it is made: " + repr(missing))

    # ⛔ AND THE SAME FIGURES IN §6, PER SECTION. The saving is stated in TWO
    # places (§0's verdict and §6's adoption paragraph). Checking §0 alone let the
    # two disagree: measured, flipping §6's "~47 %" to "~44 %" stayed GREEN while
    # §0 still said 47 — and §6 is the section the record calls "the part that
    # actually decides adoption". Both sections must carry the same figures.
    sec6 = _readme_section("6. Operational facts")
    missing6 = [f"{what} ({val})" for what, val in expected.items() if val not in sec6]
    assert missing6 == [], (
        "§6 does not carry these adoption-cost figures, so §0 and §6 can disagree "
        "without anything going red: " + repr(missing6))


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



def test_the_fusion_control_is_not_offered_as_a_postgres_option():
    """`hybrid_baseline` is the CONTROL, not an adoption candidate.

    It is RRF(in-graph leg, pgvector) — it contains no Postgres lexical leg at
    all, so listing it among the "Postgres options that do not lose" counts the
    baseline as evidence for replacing the baseline, and makes the emitted
    decision line name a candidate nobody could adopt. It must stay REPORTED
    (the arm does not lose; that is what a control is for) and be excluded from
    the candidates NAMED — named separately, not dropped silently.
    """
    v = verdict(_results({
        "hybrid_baseline": {"short_keyword": (3.0, -0.5, 7.0),
                            "entity_name": (3.0, -0.5, 7.0)},
        "hybrid_bm25": {"short_keyword": (3.0, -0.5, 7.0),
                        "entity_name": (3.0, -0.5, 7.0)},
    }))
    assert "hybrid_baseline" in v["qualifying_arms"]       # reported, not hidden
    assert "hybrid_baseline" not in v["qualifying_candidates"]
    assert v["control_arms_not_candidates"] == ["hybrid_baseline"]
    assert "hybrid_bm25" in v["qualifying_candidates"]
    assert "hybrid_baseline" not in v["decision"]

    # …and the committed record agrees with the code
    art = _artifact()
    assert art["verdict"]["control_arms_not_candidates"] == ["hybrid_baseline"]
    assert "hybrid_baseline" not in art["verdict"]["qualifying_candidates"]


def test_a_tokenizer_fallback_is_recorded_once_per_query_text(monkeypatch):
    """A silent tokenizer swap would falsify the record's own claim.

    `or_tokens` promises the SHARED product tokenizer ("it keeps the comparison
    about the scorer"), and it is invoked once per lexical arm — four times per
    query. A raw append therefore published one entry per CALL, so the artifact's
    count read 4× the affected queries. This pins one entry per text, the reason
    being recorded, and NO entry on the normal path.
    """
    import tortoise.sparse as sparse

    # the normal path records nothing
    TOKENIZER_FALLBACKS.clear()
    assert or_tokens("operator point flag")
    assert TOKENIZER_FALLBACKS == []

    # one text, four invocations — exactly one entry, and it says why
    monkeypatch.setattr(sparse, "tokenize_sparse_query", lambda *a, **k: [])
    TOKENIZER_FALLBACKS.clear()
    for _ in range(4):
        assert or_tokens("operator point flag") == ["operator", "point", "flag"]
    assert len(TOKENIZER_FALLBACKS) == 1, TOKENIZER_FALLBACKS
    assert TOKENIZER_FALLBACKS[0]["text"] == "operator point flag"
    assert "no tokens" in TOKENIZER_FALLBACKS[0]["reason"]

    # a raising tokenizer is recorded with the exception, and still falls back
    def _boom(*a, **k):
        raise RuntimeError("tokenizer exploded")

    monkeypatch.setattr(sparse, "tokenize_sparse_query", _boom)
    TOKENIZER_FALLBACKS.clear()
    for _ in range(4):
        assert or_tokens("operator point flag") == ["operator", "point", "flag"]
    assert len(TOKENIZER_FALLBACKS) == 1
    assert "RuntimeError: tokenizer exploded" in TOKENIZER_FALLBACKS[0]["reason"]
    TOKENIZER_FALLBACKS.clear()


# ── the seed's DROP must not swallow a BOUNDED read failure (#3065/#8118) ────
#
# `seed_graph` resets `GRAPH_NAME` (fixed, NOT per-run) before seeding. That
# reset used to be wrapped in `contextlib.suppress(Exception)`, which was
# harmless while the client was unbounded — a stall meant the run never
# finished, so there was no verdict to be wrong. Once the client gained a socket
# read bound, the SAME stall raises `redis.TimeoutError` at the DROP, and a
# blanket suppress turns it into a verdict: `seed_corpus` MERGEs by id, so the
# previous run's points survive the failed reset and the arms score a population
# nothing reset — a wrong published number, which is the one failure this tool
# exists to not have. The suppress is therefore narrowed to the ABSENT-graph
# class, via the product's own classifier (`projection.is_missing_graph_error`).
#
# The pair pins the CLASS BOUNDARY from both sides — it is NOT "both fail on the
# pre-fix text", which an earlier version of this comment claimed and which is
# false: a blanket `suppress(Exception)` excused the absent-graph family too, so
# the second arm passed there. Measured, each mutant in a scratch worktree at
# the same base:
#   * pre-fix text (`with contextlib.suppress(Exception): g.delete()`) -> the
#     TIMEOUT arm reds: the seed runs over a graph the failed reset left
#     populated. (Counts are deliberately NOT restated here — this file gains
#     arms, and a number beside the set it counts goes stale on arrival.)
#   * no handler at all (bare `g.delete()`) -> the ABSENT-GRAPH arm reds: a first
#     run against a graph that was never created would abort, which is the
#     over-correction this arm exists to stop.
# Too wide reds the first; too narrow reds the second.


class _FakeGraph:
    """Minimal stand-in for `Graph`: only the three calls `seed_graph` makes."""

    def __init__(self, delete_exc: Exception | None) -> None:
        self.delete_exc = delete_exc
        self.deletes = 0
        self.queries = 0

    def delete(self) -> None:
        self.deletes += 1
        if self.delete_exc is not None:
            raise self.delete_exc

    def query(self, *args: object, **kwargs: object) -> None:
        self.queries += 1
        return None


#: Every `subprocess.run` argv the stubbed probe received, in order (#13163).
#: The stub is the ONLY thing holding the durations row's "no docker" claim, so
#: an arm ASSERTS it was reached rather than trusting that the stub is installed:
#: with the stub deleted the arms stay green, and `seed_graph` then runs `docker
#: ps` / `docker exec … redis-cli INFO memory` against the ambient daemon — into
#: another lane's container at `fg_port=6379` (measured, pre-fix).
_PROBE_CALLS: list[list[str]] = []


def _record_probe_call(*args: object, **_kwargs: object) -> subprocess.CompletedProcess:
    """Stub for `subprocess.run`: records the argv, answers with empty stdout."""
    _PROBE_CALLS.append(list(args[0]) if args else [])
    return subprocess.CompletedProcess(args, 0, stdout="", stderr="")


def _patch_seed(monkeypatch, graph: _FakeGraph) -> list:
    """Point `seed_graph`'s three real dependencies at fakes — hermetic, no docker.

    ⛔ `subprocess.run` IS one of them, and stubbing it is not tidiness. `seed_graph`
    ends with a docker MEMORY PROBE (`docker ps` / `docker exec … redis-cli INFO
    memory`), and `config/ci-surfaces.yml`'s `durations:` row for this file declares
    it *hermetic — no docker, no graph, no network, no model call*, a claim a verifier
    reads INSTEAD of running the test. Leaving the probe real broke that claim the
    moment an arm called `seed_graph`, and it did worse: the arms pass
    `fg_port=6379`, so on any box where a container publishes 6379 the probe's
    second leg would `docker exec` into ANOTHER LANE's container. The arm that pins
    the probe's bound overrides this stub with its own `subprocess.run` after
    calling this helper, which is why the override must come second.
    """
    import falkordb

    import benchmarks.synthetic_corpus as corpus

    _PROBE_CALLS.clear()

    seeded: list = []

    class _FakeDB:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def select_graph(self, name: str) -> _FakeGraph:
            assert name == GRAPH_NAME
            return graph

    monkeypatch.setattr(falkordb, "FalkorDB", _FakeDB)
    monkeypatch.setattr(corpus, "seed_corpus", lambda g, points: seeded.append(points) or len(points))
    monkeypatch.setattr(
        subprocess, "run",
        _record_probe_call,
    )
    return seeded


def test_a_bounded_timeout_at_the_drop_aborts_the_seed_instead_of_scoring_stale_points(monkeypatch):
    """A read timeout is NOT "the graph was absent" — it must propagate."""
    import redis

    graph = _FakeGraph(redis.exceptions.TimeoutError("Timeout reading from socket"))
    seeded = _patch_seed(monkeypatch, graph)

    with pytest.raises(redis.exceptions.TimeoutError):
        seed_graph("127.0.0.1", 6379, [{"id": "p1"}])

    assert graph.deletes == 1
    assert seeded == [], (
        "the DROP timed out and the seed ran anyway — `seed_corpus` MERGEs by id "
        "into a fixed graph name, so the previous run's points survive and the "
        "arms score a population nothing reset"
    )
    assert graph.queries == 0, "the fulltext index was built over an un-reset graph"


def test_an_absent_graph_is_excused_so_a_first_run_still_seeds(monkeypatch):
    """The absent-graph class only — a fresh graph has nothing to delete."""
    import redis

    graph = _FakeGraph(redis.exceptions.ResponseError("Invalid graph operation on empty key"))
    seeded = _patch_seed(monkeypatch, graph)

    out = seed_graph("127.0.0.1", 6379, [{"id": "p1"}, {"id": "p2"}])

    assert graph.deletes == 1
    assert seeded == [[{"id": "p1"}, {"id": "p2"}]], "the seed must still run"
    assert out["points"] == 2
    assert _PROBE_CALLS == [["docker", "ps", "--filter", "publish=6379",
                             "--format", "{{.Names}}"]], (
        "the memory probe did not reach the stub — this run reached a real docker "
        f"daemon: {_PROBE_CALLS}"
    )


def test_a_wedged_docker_daemon_costs_a_recorded_error_not_the_run(monkeypatch):
    """The memory probe is BOUNDED: a hang there must not cost the measurement.

    `seed_graph` shells out to `docker ps` / `docker exec … redis-cli INFO memory`
    for the graph's memory figure — a SIDE READING, not the seed. Without a
    `timeout=` a wedged daemon parks the call forever, which loses the run and
    hangs this suite. What a missing
    bound actually costs, measured on the pre-fix text: the tool's own
    `except …: mem = {"error": …}` RECORDS the failure, so the seed's result
    survives — but only because `docker ps` is what hung. `seed_graph` makes TWO
    calls, and this arm must pin BOTH: `_hang` below therefore SUCCEEDS `docker
    ps` (so the `docker exec … INFO memory` leg is reached) and hangs there. A
    pin that hangs the FIRST call leaves the memory read unpinned, and a
    regression on that leg is invisible (reviewer-measured: removing only the
    second call's `timeout=` left this suite green).

    ⛔ `_patch_seed` stubs `subprocess.run` for hermeticity; this arm OVERRIDES
    that stub, so the order (patch, then override) is load-bearing.
    """
    import subprocess

    graph = _FakeGraph(None)
    _patch_seed(monkeypatch, graph)

    def _hang(*args, **kwargs):
        assert kwargs.get("timeout"), "the docker probe must carry a bound"
        # `docker ps` must SUCCEED so the `docker exec … INFO memory` leg is
        # actually reached — that read is the one this arm exists to pin, and a
        # hang on the FIRST call would leave it unexercised.
        if list(args[0])[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(args[0], 0, stdout="fdb-x\n", stderr="")
        raise subprocess.TimeoutExpired(cmd="docker", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", _hang)

    out = seed_graph("127.0.0.1", 6379, [{"id": "p1"}])

    assert out["points"] == 1, "the seed's own result survives a failed memory probe"
    assert "TimeoutExpired" in out["graph_memory_usage"]["error"], out
