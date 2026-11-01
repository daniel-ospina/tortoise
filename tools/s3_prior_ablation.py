#!/usr/bin/env python3
"""The two-arm S3 prior-injection ablation (#10463 / epic #5087).

THE QUESTION. The extractor epic's central hypothesis is that *telling extraction what already
exists in the graph* (S3 prior-injection) *reduces what it creates*. This probe measures it on the
frozen ``tests/eval/write_path`` corpus as a two-arm ablation over ``TortoiseSDK.capture_session``
(the real entry point), one full replay of the 7 sessions per arm, onto a FRESH disposable graph
per arm — and "fresh" is ASSERTED, not assumed: a non-zero baseline census is REFUSED
(`assert_fresh_graph`), because a reused `--graph-prefix` (or an epoch-second collision in the
default name) makes every printed delta arithmetically correct while the E2a guard is scored over
a previous run's Points:

* **OFF arm** — ``extractor_v2.search_graph`` is REPLACED in-process by a stub returning a healthy
  zero-result search (``degraded=False``, no entities/points/events, ``queries_run=0``, a reason
  naming ``OFF-ARM``). The writer is told nothing exists. A degraded lookup would ALSO tell the
  writer nothing exists, but through a failure — the stub keeps the arm a clean counterfactual.
* **ON arm** — the REAL ``search_graph``, wrapped only by a call counter so the log can say the
  function actually executed.

ARM INTEGRITY IS ASSERTED FROM THE RUN RECEIPT, never from a flag: each session's ``search`` block
must show ``mode="real"``, ``degraded=False`` and ``queries_run>=1`` in ON, and ``queries_run==0``
with the ``OFF-ARM`` reason in OFF. A run whose ON lookup degraded is a measurement of the
degradation (#3679), so it is reported ``ARM-INVALID`` and no ratio is printed.

⚠️ ``queries_failed`` is NOT a receipt field at this head. ``search_graph`` degrades the WHOLE
search on the first query failure (``tortoise/extractor_v2.py::search_graph``), so
``queries_failed == 0`` is asserted as ``degraded == False``; the log says so beside the number
rather than implying the receipt carries a key it does not.

THE BARS ARE NOT TYPED HERE. They are parsed out of the pre-registration COMMIT
(``git show <sha>:<prereg>``), so a threshold edited after the numbers are in cannot be served:
``--prereg`` names the commit, and the record's ``source_sha`` is asserted to be an ancestor of
``HEAD`` with no non-production file differing between them — and, stricter than that, with no
METRIC-DEFINITION file differing at all (``METRIC_ARTIFACT_PREFIXES``). The prefix allowlist alone
was too wide: the three artifacts that compute every reported number live under it.

⚠️ A COMMIT DIFF CANNOT SEE THE WORKING TREE. ``assert_source_sha`` compares two commits, so an
UNCOMMITTED edit to production code, to a metric artifact or to this file was admitted silently
while the log still asserted that the tree it ran was the pinned one — and the bundle's own measured
log is an instance of it: its ``head=`` line names the commit it ran under, while a line it prints
does not exist in this probe at that commit (README item 7). ``assert_clean_tree`` therefore
REFUSES a dirty tree over exactly those paths, the log prints ``tree=`` (``git describe --dirty``)
beside the changed-file line so it can never read as a clean-tree assertion, and ``probe_sha256``
names the bytes that ran whether or not they were ever committed.

The node census is ``docs/evidence/7949-capstone/probes/capstone_measure.py::census``, imported
READ-ONLY and reused; this module does not implement a second census (#10463 forbids one).

THE LOG IS WRITTEN INCREMENTALLY (``open_log``): each line is flushed as it is logged, so a run
that aborts mid-way still leaves the sessions it already measured on disk. The pre-fix probe
buffered every line and wrote the file once at the end, which is why replay A's output does not
exist at all (README, "Disclosure — replay A").

Exit codes: 0 = both arms measured and a verdict was written; 3 = REFUSED / ARM-INVALID (no
verdict, never a 0); 2 = the invocation itself is wrong (an ``--out`` escaping the evidence bundle,
or an existing ``--out`` — ``--overwrite`` replaces an aborted, unmeasured log and never one that
carries a ``RATIO:``/``VERDICT:``).
"""
from __future__ import annotations

import sys

# #5128 shape (D9): refuse a <3.12 interpreter BEFORE the imports below, because they are where an
# old interpreter actually dies — `from typing import …`/3.11+ stdlib names raise `ImportError` or
# `AttributeError` first, and the traceback then names the import rather than the interpreter. The
# position is load-bearing (tests/test_entry_point_python_guard.py enforces it), not cosmetic:
# `pyproject`'s `requires-python = ">=3.12"` only binds a run that goes THROUGH `uv`, and the
# documented invocation here is a bare `python3 tools/…`, whose ambient interpreter can be older.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # 2 is this tool's own "the invocation/environment is wrong" code (`main` returns 3 only for a
    # REFUSED/ARM-INVALID measurement), so an old interpreter stays legible to a runner that keys on
    # the exit code alone.
    print(
        f"tools/s3_prior_ablation.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/s3_prior_ablation.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import importlib.util
import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn, TextIO

ROOT = Path(__file__).resolve().parent.parent
# The corpus package is imported from the repo root, so the root must be on sys.path when this
# file is run as a script (`tools/` would otherwise be the only entry) — the same reason
# capstone_measure.py inserts it.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
EVIDENCE_DIR = ROOT / "docs/evidence/5087-hypothesis"
PREREG_REL = "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md"
CAPSTONE_CENSUS = ROOT / "docs/evidence/7949-capstone/probes/capstone_measure.py"
PINS_FENCE = "```yaml prereregistration-pins"
VERDICT_SUPPORTED = "HYPOTHESIS-SUPPORTED"
VERDICT_FALSIFIED = "HYPOTHESIS-FALSIFIED"
VERDICT_REFUSED = "REFUSED"
VERDICT_ARM_INVALID = "ARM-INVALID"



def refuse(message: str) -> NoReturn:
    """A refusal is exit 3, never 1: 1 is what a crash looks like.

    `raise SystemExit(str)` exits **1**, so a REFUSED run and an unhandled exception were
    indistinguishable to any caller — the module's own header promises 3 (review r1 P2).

    ⚠️ EVERY failure on the ``--prereg`` path is this code, not 2: the pins block, the pins keys and
    the ``document_sha256`` cross-check all ``refuse()``. The only exit-2s in the module are the
    ``--out`` checks (an escaping bundle, an existing measured log) — this docstring said otherwise
    and was wrong (review r5 P3).
    """
    print(message if message.startswith("s3-ablation:") else f"s3-ablation: REFUSED — {message}",
          file=sys.stderr)
    raise SystemExit(3)


# ── the pre-registration (read out of the COMMIT, never the worktree) ──────

def show(sha: str, rel: str) -> str:
    """``<sha>:<rel>`` as text — the frozen artifact, not the working copy."""
    proc = subprocess.run(["git", "show", f"{sha}:{rel}"], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        refuse(f"s3-ablation: REFUSED — {rel} is not readable at {sha}: "
                         f"{proc.stderr.strip()[:300]}")
    return proc.stdout


def parse_pins(text: str) -> dict:
    """The ``yaml prereregistration-pins`` fenced block."""
    import yaml
    start = text.find(PINS_FENCE)
    if start < 0:
        refuse(f"s3-ablation: REFUSED — the pre-registration carries no "
                         f"`{PINS_FENCE}` block; the bars cannot be read")
    body_start = text.find("\n", start)
    end = text.find("\n```", body_start)
    if body_start < 0 or end < 0:
        refuse("s3-ablation: REFUSED — the pins fence is unterminated")
    pins = yaml.safe_load(text[body_start:end])
    if not isinstance(pins, dict):
        refuse("s3-ablation: REFUSED — the pins block is not a mapping")
    for key in ("hypothesis_bars", "corpus", "source_sha", "denominator_floors"):
        if key not in pins:
            refuse(f"s3-ablation: REFUSED — the pins block carries no {key!r}; "
                             "a bar this probe had to guess is not a pre-registered bar")
    return pins


def changed_files(source_sha: str, head: str) -> list[str]:
    """Files differing between the pinned source and the head being measured."""
    proc = subprocess.run(["git", "diff", "--name-only", source_sha, head],
                          cwd=str(ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        refuse(f"s3-ablation: REFUSED — cannot diff {source_sha}..{head}: "
                         f"{proc.stderr.strip()[:300]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


#: The prefixes `is_evidence_or_tool` admits — the pin's allowlist, in ONE place, so the documented
#: admission command can be checked against the rule instead of against a second copy of it
#: (review r6 F2: the `## Reproduce` check demanded an EMPTY `source_sha..HEAD` diff, which the pin
#: never required and which is false at the only head the section declared admissible).
EVIDENCE_OR_TOOL_PREFIXES = ("docs/", "tools/", "tests/")


def is_evidence_or_tool(path: str) -> bool:
    """True when ``path`` is NOT production code under test.

    The pinned ``source_sha`` must be the tree the run executes, so a file differing between it and
    ``HEAD`` is allowed only when it cannot change what extraction does: the evidence bundles, the
    probes, the docs, and the test suite. ``tortoise/`` (and any other production tree) is refused.

    ⚠️ ``tests/eval/write_path/`` is the CORPUS and is allowed here because it is guarded
    separately and more strictly: ``corpus_state`` re-derives ``fixtures_hash`` from the tree at run
    time and REFUSES a corpus that moved. A path allowlist cannot express "same corpus" — a hash
    can, and does.

    ⚠️ This allowlist answers "is this production code?". It does NOT answer "is this the code that
    computed the number?" — see ``METRIC_ARTIFACT_PREFIXES``, which ``assert_source_sha`` applies
    ON TOP of this one (review r3 P2a).
    """
    return path.startswith(EVIDENCE_OR_TOOL_PREFIXES)


#: The artifacts that COMPUTE the reported metrics — pinned exactly, not by the allowlist above.
#:
#: Every number in the bundle comes out of these three, and all three live under a prefix
#: ``is_evidence_or_tool`` admits, so a prefix alone let the metric DEFINITION drift between the
#: pinned ``source_sha`` and ``HEAD`` without a refusal — i.e. the tuning path the pin exists to
#: close (a re-defined denominator beside an unchanged log reads as an unchanged measurement).
#: Directories, not files, on purpose: a transitive helper moving beside ``grading.py`` would move
#: the number just as surely.
METRIC_ARTIFACT_PREFIXES = (
    # census -> created_new_nodes (the primary bar) and operator_edges
    "docs/evidence/7949-capstone/probes/",
    # grading.macro_survival_counts -> real_facts; corpus.compute_fixtures_hash -> the corpus guard
    "tests/eval/write_path/",
)


def is_metric_artifact(path: str) -> bool:
    """True when ``path`` defines how a reported number is computed.

    ``assert_source_sha`` refuses ANY change to these between the pinned ``source_sha`` and ``HEAD``,
    however harmless the change looks: the pinned sha must be the code that computed the numbers,
    not merely the code that ran.
    """
    return path.startswith(METRIC_ARTIFACT_PREFIXES)


def assert_source_sha(source_sha: str, head: str) -> list[str]:
    """The pinned sha must really be the tree that runs.

    Ancestor-checked, and every differing file must be evidence or tooling — so the numbers
    describe the pinned code, not some later production edit that slipped in beside the run.

    ⚠️ This compares COMMITS only. An uncommitted edit is invisible to it — that half is
    ``assert_clean_tree``, which ``main`` runs beside this one (review r5 P1).
    """
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", source_sha, head],
                          cwd=str(ROOT), capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        refuse(f"s3-ablation: REFUSED — source_sha {source_sha} is not an "
                         f"ancestor of HEAD {head}; the pinned tree is not the tree that runs")
    changed = changed_files(source_sha, head)
    production = [path for path in changed if not is_evidence_or_tool(path)]
    if production:
        refuse(f"s3-ablation: REFUSED — {len(production)} production file(s) differ between the "
            f"pinned source_sha {source_sha} and HEAD {head}: {production[:5]} — the pinned sha "
            "must be the tree the run executes"
        )
    metrics = [path for path in changed if is_metric_artifact(path)]
    if metrics:
        refuse(f"s3-ablation: REFUSED — {len(metrics)} metric-definition file(s) differ between "
            f"the pinned source_sha {source_sha} and HEAD {head}: {metrics[:5]} — the pinned sha "
            "must be the code that COMPUTED the reported numbers, not only the code that ran"
        )
    return changed


#: The trees whose UNCOMMITTED state can change a reported number — the half of "the pinned sha is
#: the tree that runs" a COMMIT diff cannot see.
#:
#: Production extraction code, the metric artifacts (see ``METRIC_ARTIFACT_PREFIXES``), and this
#: probe itself, which computes every reported number (the ``extraction_nodes`` numerator and
#: denominator, ``real_facts``, the concentration share and the whole verdict mapping). A working
#: copy carrying an edit to any of them is refused: a later commit id cannot certify a dirty tree.
WORKTREE_GUARDED_PATHS = (
    "tortoise",
    "tests/eval/write_path",
    "docs/evidence/7949-capstone/probes",
    "tools/s3_prior_ablation.py",
)


def dirty_paths(paths: tuple[str, ...] = WORKTREE_GUARDED_PATHS) -> list[str]:
    """Uncommitted changes under ``paths``, as ``git status --porcelain`` reports them."""
    proc = subprocess.run(["git", "status", "--porcelain", "--", *paths], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        refuse(f"s3-ablation: REFUSED — cannot read the working tree state: "
               f"{proc.stderr.strip()[:300]}")
    return [line for line in proc.stdout.splitlines() if line.strip()]


def tree_state() -> str:
    """``git describe --dirty --always`` — the revision the log prints, marked when dirty."""
    proc = subprocess.run(["git", "describe", "--dirty", "--always"], cwd=str(ROOT),
                          capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        return "UNKNOWN"
    return proc.stdout.strip() or "UNKNOWN"


def probe_sha256() -> str:
    """The sha256 of the bytes that RAN — the probe's own identity, committed or not.

    ``head=`` names a commit, and a commit cannot name a working copy. This can: the log carries
    the hash of the file that computed every number, so the producing revision is checkable even
    when (as with the measured run) it was an uncommitted revision (README item 7).
    """
    import hashlib
    return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def assert_clean_tree() -> str:
    """The WORKING TREE must be the pinned tree too — not only the commit on top of it.

    ``assert_source_sha`` compares commits, so before this guard an uncommitted edit to
    ``tortoise/``, to a metric artifact or to this file was admitted silently and the log still read
    as "the pinned sha is the tree that ran" (review r5 P1). Refused at exit 3, like every other
    unmeasurable run. Returns ``tree_state()`` for the log to print.
    """
    dirty = dirty_paths()
    if dirty:
        refuse(f"s3-ablation: REFUSED — {len(dirty)} uncommitted change(s) under the guarded "
               f"paths {list(WORKTREE_GUARDED_PATHS)}: {[line.strip() for line in dirty[:5]]} — "
               "a commit id cannot certify a dirty tree, so the tree that runs would not be the "
               "tree the pin names")
    return tree_state()


# ── the corpus ─────────────────────────────────────────────────────────────

def corpus_state(pins: dict) -> tuple[list[str], str]:
    """(session ids, fixtures_hash) re-derived from the tree, refused if it moved."""
    from tests.eval.write_path.corpus import compute_fixtures_hash, session_ids

    sessions = session_ids()
    actual = compute_fixtures_hash()
    want = (pins.get("corpus") or {}).get("fixtures_hash")
    if want != actual:
        refuse(f"s3-ablation: REFUSED — the corpus in this checkout hashes to "
                         f"{actual}, the pre-registration froze {want} — a different "
                         "corpus is refused, never scored")
    frozen_n = (pins.get("corpus") or {}).get("sessions")
    if isinstance(frozen_n, int) and len(sessions) != frozen_n:
        refuse(f"s3-ablation: REFUSED — the pre-registration freezes {frozen_n} "
                         f"sessions, this corpus has {len(sessions)}")
    return sessions, actual


# ── the census (reused, never reimplemented) ───────────────────────────────

def load_census():
    """``capstone_measure.census`` — the ONE census (#10463 forbids a second)."""
    spec = importlib.util.spec_from_file_location("_capstone_census", CAPSTONE_CENSUS)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:  # pragma: no cover - import machinery guard
        refuse(f"s3-ablation: REFUSED — cannot load {CAPSTONE_CENSUS}")
    spec.loader.exec_module(module)
    census = getattr(module, "census", None)
    if not callable(census):  # pragma: no cover - the artifact moved
        refuse("s3-ablation: REFUSED — capstone_measure.census is not callable")
    return census


def created_counts(data: dict) -> dict:
    """The create metrics, read off the REUSED census.

    ``extraction_nodes`` is the primary denominator/numerator: extraction-claim Points plus the
    ``Object`` and ``Event`` nodes the run minted. Turn-row Points (``is_episodic``) and operator
    Points are EXCLUDED on purpose — both arms write them under the same rules, and counting them
    would push the ratio toward 1.0 for reasons that have nothing to do with prior-injection. The
    wider ``Σ label_counts`` delta is carried beside it as a disclosed aside.
    """
    composition = data.get("point_composition") or {}
    per_label = data.get("per_label") or {}
    claims = int(composition.get("extraction_claims") or 0)
    objects = int((per_label.get("Object") or {}).get("nodes") or 0)
    events = int((per_label.get("Event") or {}).get("nodes") or 0)
    return {
        "extraction_claims": claims,
        "objects": objects,
        "events": events,
        "extraction_nodes": claims + objects + events,
        "label_instances": sum(int(count) for count in (data.get("label_counts") or {}).values()),
        "points": int(data.get("points") or 0),
        "operator_edges": sum(int(count) for count in
                              (data.get("operator_relationships") or {}).values()),
    }


def assert_fresh_graph(before: dict, *, arm: str, graph: str, uri: str) -> None:
    """Every count in the arm's baseline census must be **0** — a reused graph is REFUSED.

    ``created`` per session is ``after - before`` and ``real_facts`` grades the arm's WHOLE graph
    (``read_point_contents``), and BOTH stay arithmetically correct over a graph that already held
    Points — which is what makes a reused graph SILENT: the deltas and the ratio look right while
    the E2a guard is scored over a previous run's Points. Two ways to reach it without meaning to:
    ``--graph-prefix`` reuses a namespace on purpose, and the default name
    ``s3ablate_<head8>_<epoch-second>`` collides when two runs start inside the same second.

    The pre-registration's "FRESH disposable graph" (§0) was a premise no code checked (review
    P2). It is checked here, before the first session is captured, and the baseline is printed so
    the log carries the evidence that it was zero rather than an assurance that it was.
    """
    dirty = {key: value for key, value in before.items() if value}
    if dirty:
        refuse(f"s3-ablation: REFUSED — the {arm} arm's graph {graph} is NOT fresh: its baseline "
               f"census is {dirty} (every count must be 0). Its per-session deltas would still "
               "add up, but the E2a guard grades the arm's WHOLE graph, so it would be scored "
               f"over a previous run's Points. Use a new --graph-prefix, or drop the graph at {uri}")


def read_point_contents(uri: str) -> list[dict]:
    """Every ``:Point`` in the arm's graph, as grading input (NOT a census).

    ``tests.eval.write_path.grading`` is the corpus's own matcher and it takes points, so the
    real-fact guard needs their contents. This is a grading read of one property; the node
    accounting above comes from the reused census.
    """
    from tortoise.projection import FalkorProjection

    projection = FalkorProjection.from_uri(uri)
    return [
        {"point_id": str(row[0]), "content": str(row[1] or "")}
        for row in projection.g.query(
            "MATCH (p:Point) RETURN p.id AS id, p.content AS content").result_set
        if row and row[0]
    ]


def real_facts(arm_gold: dict[str, dict], points: list[dict]) -> dict:
    """Planted units recovered by content, over the ARM's whole graph.

    Scored over the arm's cumulative Point set, not over the points written in the session: the
    hypothesis predicts FOLDING onto existing canonical Points, which writes no new Point, so a
    written-in-session scope would charge the ON arm for the exact behaviour under test. A unit is
    recovered when any Point in the arm's graph retains its anchor.
    """
    from tests.eval.write_path.grading import macro_survival_counts

    survived = 0
    total = 0
    per_session: dict[str, dict] = {}
    for sid, gold in arm_gold.items():
        counts = macro_survival_counts(gold, points)
        survived += int(counts["survived"])
        total += int(counts["total"])
        per_session[sid] = {"survived": int(counts["survived"]),
                            "total": int(counts["total"])}
    return {"survived": survived, "total": total, "per_session": per_session}


# ── the arms ───────────────────────────────────────────────────────────────

def graph_uri(base_uri: str, graph: str) -> str:
    """Address ``graph`` on the container ``base_uri`` points at (last segment replaced)."""
    stem, sep, query = base_uri.partition("?")
    base = stem.rsplit("/", 1)[0]
    return f"{base}/{graph}" + (f"?{query}" if sep else "")


def off_arm_stub(mode: str) -> dict:
    """What the OFF arm's S3 returns: a HEALTHY search that found nothing."""
    return {
        "mode": mode,
        "degraded": False,
        "reason": ("OFF-ARM: S3 prior-injection suppressed by the probe "
                   "(search_graph replaced; zero priors injected)"),
        "entities": [], "points": [], "events": [], "queries_run": 0,
    }


def arm_s3_state(arm: str, search: dict) -> dict:
    """The receipt's S3 state, and whether it PROVES the arm it claims to be."""
    search = search or {}
    queries_run = int(search.get("queries_run") or 0)
    reason = str(search.get("reason") or "")
    degraded = bool(search.get("degraded"))
    mode = search.get("mode")
    if arm == "ON":
        resolved = (not degraded) and queries_run >= 1 and mode == "real"
        note = "resolved" if resolved else f"NOT resolved (degraded={degraded}, queries_run={queries_run}, mode={mode!r})"
    else:
        resolved = False
        proven_off = (not degraded) and queries_run == 0 and reason.startswith("OFF-ARM")
        note = "suppressed (OFF-ARM)" if proven_off else "NOT proven suppressed"
        return {"queries_run": queries_run, "degraded": degraded, "mode": mode,
                "reason": reason, "state": note, "proven": proven_off}
    return {"queries_run": queries_run, "degraded": degraded, "mode": mode,
            "reason": reason, "state": note, "proven": resolved}


def witness_ok(arm: str, blob: dict, real_calls_this_session: int) -> bool:
    """Is the witness provable, and did the REAL function run the number of times the arm allows?

    Three review findings folded into one predicate:

    * an EMPTY witness proves nothing however healthy the blob reads — a session that never reached
      the S3 call site used to inherit the previous session's dict;
    * an ARM-TOTAL call count cannot say whether a given session made its call, so the count that
      matters is this session's own delta; and
    * ⚠️ the count must be of calls to the **REAL** function, never of calls intercepted. In the OFF
      arm the stub IS called once per session, so a counter incremented by the stub made every OFF
      session unprovable by construction — `ARM-INVALID` 0 of 7, always (review r2 P1). E3's
      registered wording is "zero REAL-function calls", and that is what is counted here.
    """
    if not blob:
        return False
    if arm == "ON":
        return real_calls_this_session >= 1
    return real_calls_this_session == 0


def run_arm(arm: str, graph: str, sessions: list[str], census, golds: dict[str, dict],
            log) -> dict:
    """One full replay of the corpus into a fresh graph, with the arm's S3 behaviour."""
    import tortoise.extractor_v2 as ev
    from tortoise.sdk import TortoiseSDK

    real_search = ev.search_graph
    # ``real`` is the arm total; ``real_session`` is THIS session's calls to the REAL function, and
    # it is the only one arm integrity may read. ``intercepted`` counts calls that reached either
    # wrapper, so a run that never reaches the call site at all is distinguishable from one whose
    # stubbed call was healthy — the two are different failures and the log says which happened.
    calls = {"real": 0, "real_session": 0, "intercepted": 0}
    witness: dict = {}

    def counting(*args, **kwargs):
        calls["real"] += 1
        calls["real_session"] += 1
        calls["intercepted"] += 1
        result = real_search(*args, **kwargs)
        witness.clear()
        witness.update(result or {})
        return result

    def off_stub(*args, **kwargs):
        calls["intercepted"] += 1
        result = off_arm_stub(ev.resolve_backend_mode())
        witness.clear()
        witness.update(result)
        return result

    ev.search_graph = counting if arm == "ON" else off_stub
    base_uri = os.environ["TORTOISE_DB_URI"]
    uri = graph_uri(base_uri, graph)
    rows: list[dict] = []
    try:
        sdk = TortoiseSDK(graph_name=graph)
        before = created_counts(census(uri))
        # "A FRESH disposable graph" is a PREMISE of the measurement (pre-registration §0), so it is
        # refused when false instead of assumed (review P2): a reused `--graph-prefix`, or two runs
        # colliding inside one epoch second on the default name, leaves every printed delta correct
        # while the E2a guard is scored over the previous run's Points. The baseline is printed for
        # the same reason the bars are: the log must carry what the run was measured against.
        assert_fresh_graph(before, arm=arm, graph=graph, uri=uri)
        log(f"  [{arm}] graph baseline before session 1: {before} — every count 0, the graph is "
            "fresh (asserted, not assumed)")
        for sid in sessions:
            started = time.time()
            # A session's witness must be THIS session's: cleared per session so a session that
            # never reaches the S3 call site cannot inherit the previous one's dict and be proven
            # from it, and the call count is read as a PER-SESSION delta — an arm total of 7 over
            # 7 sessions could otherwise be 7 in one session and 0 in six (review r1 P1).
            witness.clear()
            calls["real_session"] = 0
            calls["intercepted"] = 0
            receipt = sdk.capture_session(_turns(sid), session_id=sid)
            stats = receipt.get("stats") or {}
            # ⚠️ THE RECEIPT CARRIES NO `search` BLOCK ON THIS LANE. `capture_session` ->
            # `_extract_session_v2` never surfaces S3's report onto the meta it returns (only
            # `_commit_session_v2` does), so the arm's S3 state is witnessed from the value the
            # S3 function ACTUALLY RETURNED during this session — recorded by the wrapper above,
            # which is the real function's own output, not a flag. The receipt is used when it
            # does carry the block, so this probe cannot be accused of preferring its own copy.
            receipt_search = receipt.get("search") or {}
            witness_source = "receipt" if receipt_search else "search_graph-return"
            s3_blob = receipt_search or dict(witness)
            s3_state = arm_s3_state(arm, s3_blob)
            # The PER-SESSION call delta is part of the proof: ON must have made at least the real
            # call this session needed, OFF must have made none — and an EMPTY witness is not
            # provable at all whatever the blob says (review r1 P1: a session that never reached
            # the S3 call site must not be proven from the previous session's dict).
            bad_calls = not witness_ok(arm, s3_blob, calls["real_session"])
            if bad_calls:
                s3_state = {**s3_state, "proven": False,
                            "state": (f"NOT proven ({witness_source}; this session's REAL S3 "
                                      f"calls {calls['real_session']}, intercepted "
                                      f"{calls['intercepted']}; witness "
                                      f"{'empty' if not s3_blob else 'present'})")}
            s3_state = {**s3_state, "witness_source": witness_source,
                        "session_real_calls": calls["real_session"],
                        "session_intercepted": calls["intercepted"]}
            after = created_counts(census(uri))
            created = {key: after[key] - before[key] for key in after}
            rows.append({
                "session_id": sid,
                "ok": bool(receipt.get("ok")),
                "created": created,
                "llm_calls": int((stats.get("llm") or {}).get("calls") or 0),
                "s3": s3_state,
                "errors": list(receipt.get("errors") or [])[:3],
                "elapsed_s": round(time.time() - started, 1),
            })
            before = after
            log(f"  [{arm}] {sid}: created={created['extraction_nodes']} "
                f"(claims={created['extraction_claims']} objects={created['objects']} "
                f"events={created['events']}) s3={rows[-1]['s3']['state']} "
                f"llm_calls={rows[-1]['llm_calls']} ok={rows[-1]['ok']} "
                f"({rows[-1]['elapsed_s']}s)")
    finally:
        ev.search_graph = real_search
    final = created_counts(census(uri))
    return {
        "arm": arm,
        "graph": graph,
        "uri": uri,
        "sessions": rows,
        "real_search_calls": calls["real"],
        "real_facts": real_facts(golds, read_point_contents(uri)),
        "totals": {key: sum(row["created"][key] for row in rows) for key in final},
        "final": final,
        "llm_calls": sum(row["llm_calls"] for row in rows),
    }


def _turns(sid: str) -> list[dict]:
    from tests.eval.write_path.corpus import load_fixture

    turns = (load_fixture(sid) or {}).get("conversation")
    if not isinstance(turns, list) or not turns:
        refuse(f"s3-ablation: REFUSED — fixture {sid} carries no conversation")
    return turns


# ── the arithmetic the pre-registration fixes ──────────────────────────────

def concentration(pairs: list[tuple[int, int]]) -> dict:
    """The largest single session's share of the total create REDUCTION.

    ``pairs`` are ``(off_created, on_created)`` per session. Reductions are floored at 0 so a
    session that INCREASED cannot subsidise the shares. ``share`` is ``None`` when nothing fell at
    all — an undefined statistic, never a 0 that reads like a clean result.
    """
    reductions = [max(0, off - on) for off, on in pairs]
    total = sum(reductions)
    if total <= 0:
        return {"share": None, "largest_session_reduction": 0, "total_reduction": 0}
    return {"share": max(reductions) / total,
            "largest_session_reduction": max(reductions),
            "total_reduction": total}


def paired_sessions(off: dict, on: dict) -> list[tuple[dict, dict]]:
    """The two arms' sessions paired row by row — REFUSED, never truncated, on a mismatch.

    Every per-session statistic the probe prints (the table, the ratio range) reads these pairs,
    so a `zip` over two arms of different lengths would silently drop the tail of the longer arm
    and still print a table that looks complete. That is a reporting defect with a measurement's
    shape, so a mismatch is a refusal (exit 3), not a shorter table.

    `strict=True` is therefore unreachable — it is here so a future edit that relaxes the guard
    above cannot reintroduce the silent truncation (ruff's B905 asks for the explicit choice).
    """
    off_rows, on_rows = off["sessions"], on["sessions"]
    if len(off_rows) != len(on_rows):
        refuse(f"the arms replay {len(off_rows)}/{len(on_rows)} sessions; a per-session "
               "comparison needs both arms to replay the same set")
    return list(zip(off_rows, on_rows, strict=True))


def decide(bars: dict, floors: dict, off: dict, on: dict) -> dict:
    """The pre-registered verdict mapping, applied to the two arms.

    Order is fixed by the pre-registration: arm integrity first (no ratio from an arm whose S3 did
    not resolve), then the denominator floors, then the primary bar, then the guards.
    """
    integrity = [row["s3"]["proven"] for row in off["sessions"] + on["sessions"]]
    proven = sum(1 for flag in integrity if flag)
    required = int(bars["arm_integrity_required"])
    if proven < required:
        broken = [f"{row['session_id']}:{'ON' if row in on['sessions'] else 'OFF'}"
                  for row in off["sessions"] + on["sessions"] if not row["s3"]["proven"]]
        return {"verdict": VERDICT_ARM_INVALID,
                "why": f"E3 {proven} of {required} session-arms prove their S3 state; "
                       f"unproven: {broken[:6]}",
                "ratio": None, "qualifier": None}

    created_off = off["totals"]["extraction_nodes"]
    created_on = on["totals"]["extraction_nodes"]
    if len(off["sessions"]) != int(floors["sessions"]) or \
            len(on["sessions"]) != int(floors["sessions"]):
        return {"verdict": VERDICT_REFUSED,
                "why": (f"the arms replay {len(off['sessions'])}/{len(on['sessions'])} sessions, "
                        f"the pre-registration freezes {floors['sessions']}"),
                "ratio": None, "qualifier": None}
    if created_off < int(floors["created_nodes_off"]):
        return {"verdict": VERDICT_REFUSED,
                "why": f"the OFF arm created {created_off} extraction nodes, below the "
                       f"pre-registered floor of {floors['created_nodes_off']} — the corpus "
                       "cannot carry the bar", "ratio": None, "qualifier": None}
    if int(off["real_facts"]["survived"]) < int(floors["real_facts_off"]):
        return {"verdict": VERDICT_REFUSED,
                "why": f"the OFF arm recovered {off['real_facts']['survived']} planted units, "
                       f"below the pre-registered floor of {floors['real_facts_off']}",
                "ratio": None, "qualifier": None}

    ratio = created_on / created_off if created_off else None
    pairs = [(sum(row["created"]["extraction_nodes"] for row in off["sessions"] if row["session_id"] == sid),
              sum(row["created"]["extraction_nodes"] for row in on["sessions"] if row["session_id"] == sid))
             for sid in [row["session_id"] for row in off["sessions"]]]
    conc = concentration(pairs)
    qualifier = None
    if conc["share"] is not None and conc["share"] > float(bars["concentration_max"]):
        qualifier = "SINGLE-SESSION-DRIVEN"
    guard_floor = float(bars["guard_ratio_min"])
    off_facts = off["real_facts"]["survived"]
    off_edges = off["totals"]["operator_edges"]
    guard_facts = (on["real_facts"]["survived"] >= guard_floor * off_facts)
    guard_operators = (on["totals"]["operator_edges"] >= guard_floor * off_edges)
    primary_met = ratio is not None and ratio <= float(bars["primary_ratio_max"])
    fell = sum(1 for off_n, on_n in pairs if on_n < off_n)
    if fell < int(bars["corroboration_min_sessions"]):
        # Registered in §3 (E2b): a qualifier, never the primary bar, and never a silent omission.
        qualifier = ",".join(filter(None, [qualifier, "not-majority-of-sessions"]))
    if primary_met and guard_facts and guard_operators:
        verdict = VERDICT_SUPPORTED
    elif primary_met:
        broken = "real_facts" if not guard_facts else "operator_edges"
        verdict = VERDICT_FALSIFIED
    else:
        broken = None
        verdict = VERDICT_FALSIFIED
    payload = {"verdict": verdict, "ratio": ratio, "qualifier": qualifier,
               "primary_met": primary_met, "guard_facts": guard_facts,
               "guard_operators": guard_operators, "concentration": conc,
               "sessions_fell": fell,
               "corroboration_met": fell >= int(bars["corroboration_min_sessions"]),
               "created_off": created_off, "created_on": created_on,
               "cost_ratio": (on["llm_calls"] / off["llm_calls"]) if off["llm_calls"] else None,
               "cost_met": ((on["llm_calls"] <= float(bars["cost_ratio_max"]) * off["llm_calls"])
                            if off["llm_calls"] else None)}
    if primary_met and not (guard_facts and guard_operators):
        payload["why"] = (f"E1 met (ratio {ratio:.3f} <= {bars['primary_ratio_max']}) but "
                          f"GUARD-VIOLATED: {broken} fell — volume bought by extracting less "
                          "real content is not a confirmation")
        payload["verdict_text"] = f"HYPOTHESIS-FALSIFIED (ratio {ratio:.3f}, GUARD-VIOLATED: {broken})"
    elif primary_met:
        payload["why"] = (f"E1 met and both guards held (facts {on['real_facts']['survived']} "
                          f">= {off['real_facts']['survived']}, operator edges "
                          f"{on['totals']['operator_edges']} >= {off['totals']['operator_edges']})")
    else:
        payload["why"] = (f"E1 not met: ratio {ratio:.3f} > {bars['primary_ratio_max']} — telling "
                          "extraction what exists does not reduce what it creates")
        # Rule 5 freezes the verdict TEXT as `HYPOTHESIS-FALSIFIED (ratio X)`. Only the rule-4 branch
        # set it, so the branch that actually fired at this head logged the bare token (the committed
        # log's `VERDICT:` line) — a reader could not tell a rule-5 falsification from a bare word.
        payload["verdict_text"] = f"{VERDICT_FALSIFIED} (ratio {ratio:.3f})"
    return payload


# ── entry point ────────────────────────────────────────────────────────────

def head_sha() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                          capture_output=True, text=True, check=True).stdout.strip()


def inside_evidence(path: Path) -> bool:
    try:
        path.resolve().relative_to(EVIDENCE_DIR.resolve())
    except ValueError:
        return False
    return True


#: The log lines only a run that reached a VERDICT can have written. An aborted run cannot print
#: either, so their presence is what separates a MEASURED log from an unmeasured one.
MEASURED_LOG_MARKERS = ("RATIO:", "VERDICT:")


def out_path_refusal(out_path: Path, overwrite: bool) -> str | None:
    """The one-measured-log rule, decided by the FILE and not only by the flag.

    ``--overwrite`` exists for an aborted run that left an unmeasured log (replay A). Nothing
    inspected the existing file, so the same flag also truncated a log carrying a ``RATIO:`` /
    ``VERDICT:`` line — i.e. exactly the "re-run until it agrees" path the refusal exists to close,
    one flag away (review r5 P2). An aborted run cannot write either line, so reading the file is
    the test, and the README now states this rule instead of a stronger one the code never had.
    """
    if not out_path.exists():
        return None
    relative = out_path.relative_to(ROOT)
    if not overwrite:
        return (f"{relative} already exists. One bundle carries one measured log: a re-run writes "
                "a NEW file (and the superseded one is deleted deliberately, with the supersede "
                "chain disclosed), never an overwrite. Pass --overwrite only when you are "
                "replacing an aborted, unmeasured run.")
    prior = out_path.read_text(encoding="utf-8", errors="replace")
    if any(line.startswith(MEASURED_LOG_MARKERS) for line in prior.splitlines()):
        return (f"{relative} already carries a RATIO/VERDICT line, so it is a MEASURED log, not an "
                "aborted one — --overwrite may replace an unmeasured run and nothing else. A "
                "measured log is superseded by a NEW file with the supersede chain disclosed.")
    return None


def open_log(path: Path) -> tuple[TextIO, Callable[[str], None]]:
    """Open the log and return ``(handle, write_line)`` — the log is written INCREMENTALLY.

    The measured run's output has to be storable **without the process's cooperation**. This bundle
    is the receipt of what the alternative costs: replay A aborted mid-run on a probe defect, and
    because the pre-fix probe accumulated every line in memory and wrote the file ONCE at the end
    (`out_path.write_text`), that abort left *nothing* — no file at all — so its per-session noise
    distribution is unreproducible and claimed nowhere (README, "Disclosure — replay A").

    So each line is written and FLUSHED as it is logged: a killed process loses at most the line it
    was in the middle of, and the sessions already measured survive on disk. The durable write is
    the log's own definition of done, exactly as ``METRIC_ARTIFACT_PREFIXES`` is the code's.

    The one-measured-log refusal is unaffected: the caller creates the file once (it has already
    refused an existing ``--out``), it just no longer depends on a clean exit to have a body.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8")

    def write_line(message: str) -> None:
        handle.write(message + "\n")
        handle.flush()

    return handle, write_line


#: The frozen bars, in the pre-registration's own order. Named once here so the log header can
#: emit EVERY one of them: the pre-fix line printed five of the seven, so the two that decide the
#: corroboration qualifier (`corroboration_min_sessions`) and the session count (`sessions_total`)
#: were invisible in the only record of what a run compared against (#10468 r6 F1).
#: ⛔ Emitted is not the same as COMPARED: `decide()` reads six of these, and the session count it
#: actually gates on comes from `denominator_floors.sessions` — `sessions_total` is here because the
#: log must name every frozen number it was scored against, not because a verdict branches on it.
#: `tests/test_s3_prior_ablation.py::test_every_frozen_threshold_is_named_so_none_can_be_dropped_silently`
#: decides which is which from `decide()`'s own source, so this stays true without a second copy.
BARS_LOG_ORDER = (
    "primary_ratio_max",
    "guard_ratio_min",
    "corroboration_min_sessions",
    "sessions_total",
    "arm_integrity_required",
    "concentration_max",
    "cost_ratio_max",
)


def bars_log_line(bars: dict) -> str:
    """The log header that names every frozen bar, so the log pins what it was scored against."""
    return "bars: " + " ".join(f"{key}={bars[key]}" for key in BARS_LOG_ORDER)


def floors_log_line(floors: dict) -> str:
    """The log header that names every REFUSED floor, round-trippable by ``ast.literal_eval``."""
    return f"floors: {dict(floors)}"


def arm_integrity_line(off: dict, on: dict) -> str:
    """The ARM INTEGRITY log line, counted over the sessions that ACTUALLY ran.

    The fractions were hardcoded ``/7``, so the line carried a denominator no arm produced — the
    corpus is pinned, but the log should not be a second copy of that fact, and a denominator that
    cannot follow the run is the class of defect this bundle exists to close (review P3).
    """
    off_proven = sum(1 for row in off["sessions"] if row["s3"]["proven"])
    on_proven = sum(1 for row in on["sessions"] if row["s3"]["proven"])
    return (f"ARM INTEGRITY: OFF {off_proven}/{len(off['sessions'])} proven "
            f"(real search_graph calls {off['real_search_calls']}), "
            f"ON {on_proven}/{len(on['sessions'])} resolved "
            f"(real search_graph calls {on['real_search_calls']})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Two-arm S3 prior-injection ablation (#10463).")
    ap.add_argument("--prereg", required=True,
                    help="the commit sha the pre-registration is read from")
    ap.add_argument("--out", required=True,
                    help="log path INSIDE docs/evidence/5087-hypothesis/ (the V4 rule refuses an "
                         "escaping evidence path)")
    ap.add_argument("--arm-order", default="OFF,ON",
                    help="corpus replay order of the two arms (default: OFF,ON)")
    ap.add_argument("--graph-prefix", default=None)
    ap.add_argument("--json", action="store_true", help="also print the record as JSON")
    ap.add_argument("--overwrite", action="store_true",
                    help="replace an existing --out (only for an aborted, unmeasured run)")
    args = ap.parse_args(argv)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    if not inside_evidence(out_path):
        print(f"s3-ablation: REFUSED — --out {out_path} escapes the evidence bundle "
              f"{EVIDENCE_DIR} (the gate's V4 rule refuses an escaping evidence path)")
        return 2

    head = head_sha()
    prereg_text = show(args.prereg, PREREG_REL)
    pins = parse_pins(prereg_text)
    # The machine record pins the argument it describes; if the two disagree, the bars this run
    # parses are not the bars the bundle's record froze — the residual tuning path review r1 P2
    # named (a re-cut document beside an un-re-cut record).
    #
    # ⚠️ ABSENCE REFUSES, IT DOES NOT SKIP (review r6 F4). This was `if record_path.is_file()`, so
    # the ONLY document↔record cross-check in the module switched itself off when the file was
    # gone — deleting one file, committed or not, disabled the guard with no refusal and no line in
    # the log saying the check had not run. Everything else here refuses rather than skips ("a bar
    # this probe had to guess is not a pre-registered bar"), and a record carrying no
    # ``document_sha256`` is the same fail-open one field in, so that refuses too.
    record_path = EVIDENCE_DIR / "preregistration.json"
    try:
        record_label = str(record_path.relative_to(ROOT))
    except ValueError:  # a bundle outside the repo (tests, a restored copy)
        record_label = str(record_path)
    if not record_path.is_file():
        refuse(f"{record_label} is missing from this evidence bundle — the machine record is what "
               "pins the argument, so without it nothing checks that the bars this run obeys are "
               "the bars the bundle froze. A guard that can be switched off by deleting one file "
               "is not a guard")
    import hashlib
    try:
        record = json.loads(record_path.read_text())
    except json.JSONDecodeError as exc:
        refuse(f"{record_label} is not JSON ({exc}) — an unreadable record cannot be compared "
               "with the document, and an unchecked cross-check is not a passed one")
    if not isinstance(record, dict):
        refuse(f"{record_label} is not a JSON object; the record must pin document_sha256")
    want = record.get("document_sha256")
    got = "sha256:" + hashlib.sha256(prereg_text.encode()).hexdigest()
    if not want:
        refuse(f"{record_label} carries no document_sha256 — a record that does not pin the "
               "argument cannot refuse a re-cut one")
    if want != got:
        refuse(f"the pre-registration at {args.prereg} hashes to {got}, but "
               f"{record_label} pins {want} — the record does not describe the "
               "document this run is about to obey")
    # One bundle carries ONE measured log. Overwriting it silently is how a superseded run becomes
    # invisible, and how a re-run could replace a falsifying number with a friendlier one. The
    # refusal reads the FILE: `--overwrite` may replace an aborted run, never a log with a verdict.
    refusal = out_path_refusal(out_path, args.overwrite)
    if refusal is not None:
        print(f"s3-ablation: REFUSED — {refusal}", file=sys.stderr)
        return 2
    bars = pins["hypothesis_bars"]
    floors = pins["denominator_floors"]
    changed = assert_source_sha(str(pins["source_sha"]), head)
    # The pin above compares COMMITS; this refuses a working copy that differs from the commit it
    # claims to be. Both are needed: a dirty tree is how the measured run's log came to print a
    # line its own `head=` did not contain (README item 7).
    tree = assert_clean_tree()
    probe_hash = probe_sha256()
    sessions, corpus_hash = corpus_state(pins)

    # ⚠️ The prerequisites are checked BEFORE the log is created (review r6 F3). They used to run
    # after ``open_log`` and the eleven header lines, so a keyless or URI-less invocation exited 3
    # leaving a header-only ``*.log`` inside the evidence bundle — a file that reads as the setup of
    # a run that never started, which then tripped the one-measured-log refusal (exit 2) on the next
    # attempt, while the documented reproduce command passes no ``--overwrite``. A refused run now
    # leaves no file at all. Nothing below needs the log: every header value is already computed.
    if not (os.environ.get("DEEPSEEK_API_KEY") or "").strip():
        refuse("s3-ablation: REFUSED — DEEPSEEK_API_KEY is unset; a keyless capture "
                         "extracts nothing, which measures the absence of a key (#3892)")
    if not (os.environ.get("TORTOISE_DB_URI") or "").strip():
        refuse("s3-ablation: REFUSED — TORTOISE_DB_URI is unset; S3 needs a searchable "
                         "backend (`uv run python tools/test_lane.py uri`)")

    lines: list[str] = []
    handle, write_line = open_log(out_path)

    def log(message: str) -> None:
        lines.append(message)
        print(message, flush=True)
        write_line(message)

    log("=== s3-ablation (#10463) — does telling extraction what exists reduce what it creates? ===\n")
    log(f"prereg={args.prereg} (bars read from the COMMIT, not the worktree)")
    log(f"source_sha={pins['source_sha']} head={head}")
    log(f"source_sha..head changed files ({len(changed)}, all evidence/tooling): {changed or '(none)'}")
    log(f"tree={tree} — the working copy the run executed, `git describe --dirty`; the guarded "
        f"paths {list(WORKTREE_GUARDED_PATHS)} were ASSERTED clean before any model call, so this "
        "run was not a dirty-tree run")
    log(f"probe_sha256={probe_hash} — the bytes that computed every number below (a `head=` names "
        "a commit; this names the file the run actually executed)")
    log(f"corpus=tests/eval/write_path sessions={len(sessions)} fixtures_hash={corpus_hash}")
    log(bars_log_line(bars))
    log(floors_log_line(floors))
    log("queries_failed: the receipt carries no such field at this head; search_graph degrades "
        "the WHOLE search on the first query failure, so queries_failed==0 is asserted as "
        "degraded==False (stated here, not implied)")
    log("S3 WITNESS: the `capture_session` lane's receipt carries NO `search` block (only "
        "`_commit_session_v2` surfaces one), so each session's S3 state is asserted from the "
        "value the S3 function ACTUALLY RETURNED during that session — recorded by the probe's "
        "wrapper around the real function (ON) / stub (OFF), never from a flag. This is a "
        "disclosed deviation from the pre-registration's wording 'asserted from the run receipt' "
        "and an instrument finding for #5087 indicator 3.")

    from tests.eval.write_path.corpus import load_gold

    census = load_census()
    golds = {sid: load_gold(sid) for sid in sessions}
    order = [arm.strip() for arm in args.arm_order.split(",") if arm.strip()]
    stamp = head[:8]
    prefix = args.graph_prefix or f"s3ablate_{stamp}_{int(time.time())}"
    arms: dict[str, dict] = {}
    for arm in order:
        graph = f"{prefix}_{arm.lower()}"
        log(f"--- ARM {arm} — fresh graph {graph} ---")
        arms[arm] = run_arm(arm, graph, sessions, census, golds, log)
        data = arms[arm]
        log(f"  [{data['arm']}] real search_graph calls: {data['real_search_calls']} "
            f"(OFF arm must be 0)")
        log(f"  [{data['arm']}] created extraction nodes: {data['totals']['extraction_nodes']} "
            f"| aside Σlabel_counts delta: {data['totals']['label_instances']}")
        log(f"  [{data['arm']}] real facts: {data['real_facts']['survived']}"
            f"/{data['real_facts']['total']} planted units recovered")
        log(f"  [{data['arm']}] operator edges: {data['totals']['operator_edges']} "
            f"(graph-wide IMPL/NAND/SUPPORTS/MITIGATES/NEGATE/SUPERSEDE/INPUT)")
        log(f"  [{data['arm']}] model calls: {data['llm_calls']}\n")

    if "OFF" not in arms or "ON" not in arms:
        refuse("s3-ablation: REFUSED — both arms are required; a single-arm result is "
                         "never a verdict")
    # The per-session table and the per-session ratio range pair the two arms ROW BY ROW, so an
    # arm of a different length would be silently truncated to the shorter one by `zip` — a table
    # that looks complete while a whole session of one arm is missing from it. `paired_sessions`
    # refuses that outright (exit 3, never 1: an unmeasurable run is not a crash).
    pairs = paired_sessions(arms["OFF"], arms["ON"])

    result = decide(bars, floors, arms["OFF"], arms["ON"])
    off_recover = sum(1 for row in arms["OFF"]["sessions"] if not row["ok"])
    on_recover = sum(1 for row in arms["ON"]["sessions"] if not row["ok"])

    log("--- per-session table (created extraction nodes) ---")
    log(f"{'session':28s} {'OFF':>6s} {'ON':>6s} {'delta':>7s} {'OFF s3':>10s} {'ON s3':>10s}")
    for off_row, on_row in pairs:
        log(f"{off_row['session_id']:28s} "
            f"{off_row['created']['extraction_nodes']:6d} "
            f"{on_row['created']['extraction_nodes']:6d} "
            f"{on_row['created']['extraction_nodes'] - off_row['created']['extraction_nodes']:7d} "
            f"{off_row['s3']['state']:>10s} {on_row['s3']['state']:>10s}")
    # ⚠️ NO RATIO FROM AN UNPROVEN OR REFUSED ARM (pre-registration rule 1). The range is a ratio
    # statistic, so it is emitted only when the verdict produced one: an ARM-INVALID log used to
    # print a per-session ratio range derived from sessions whose S3 state was never proven, which
    # is exactly the number a reader would quote (review r3 P2).
    ratios = []
    for off_row, on_row in pairs:
        off_n = off_row["created"]["extraction_nodes"]
        ratios.append((on_row["created"]["extraction_nodes"] / off_n) if off_n else None)
    defined = [value for value in ratios if value is not None]
    if result.get("ratio") is not None and defined:
        log(f"per-session ratio range: {min(defined):.3f} .. {max(defined):.3f} "
            f"(over {len(defined)} of {len(ratios)} sessions with a non-zero OFF denominator)")
    elif result.get("ratio") is None:
        log("per-session ratio range: NOT REPORTED — the verdict produced no ratio "
            f"({result['verdict']}); a ratio from an unproven arm is not a measurement")
    if result.get("concentration"):
        conc = result["concentration"]
        log(f"concentration (largest session's share of the total reduction): "
            f"{conc['share'] if conc['share'] is None else round(conc['share'], 3)} "
            f"(reduction total {conc['total_reduction']}, largest {conc['largest_session_reduction']})")

    log("")
    log(f"COUNTS: created extraction nodes OFF={result.get('created_off', arms['OFF']['totals']['extraction_nodes'])} "
        f"ON={result.get('created_on', arms['ON']['totals']['extraction_nodes'])}")
    log(f"RATIO: {result['ratio'] if result['ratio'] is None else round(result['ratio'], 4)}")
    log(arm_integrity_line(arms["OFF"], arms["ON"]))
    log(f"REAL FACTS: OFF {arms['OFF']['real_facts']['survived']}/{arms['OFF']['real_facts']['total']} "
        f"ON {arms['ON']['real_facts']['survived']}/{arms['ON']['real_facts']['total']}")
    log(f"OPERATOR EDGES: OFF {arms['OFF']['totals']['operator_edges']} "
        f"ON {arms['ON']['totals']['operator_edges']}")
    log(f"COST: model calls OFF {arms['OFF']['llm_calls']} ON {arms['ON']['llm_calls']} "
        f"(ratio {result.get('cost_ratio')}; bar <= {bars['cost_ratio_max']}; "
        f"met={result.get('cost_met')})")
    log(f"SESSIONS WHOSE CREATES FELL: {result.get('sessions_fell')} of {len(sessions)} "
        f"(E2b bar >= {bars['corroboration_min_sessions']}; "
        f"met={result.get('corroboration_met')})")
    log(f"FAILED SESSIONS: OFF {off_recover} ON {on_recover}")
    verdict_line = result.get("verdict_text") or result["verdict"]
    if result["verdict"] in (VERDICT_REFUSED, VERDICT_ARM_INVALID):
        verdict_line = f"{result['verdict']}({result.get('why')})"
    elif result.get("qualifier"):
        verdict_line = f"{verdict_line} ({result['qualifier']})"
    log(f"VERDICT: {verdict_line}")
    log(f"WHY: {result.get('why')}")
    handle.close()
    print(f"s3-ablation: wrote {out_path.relative_to(ROOT)}")
    if args.json:
        print(json.dumps({"arms": arms, "result": result}, indent=2, default=str))
    return 0 if result["verdict"] not in (VERDICT_REFUSED, VERDICT_ARM_INVALID) else 3


if __name__ == "__main__":
    raise SystemExit(main())
