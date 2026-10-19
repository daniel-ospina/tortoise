#!/usr/bin/env python3
"""The extractor-v4 capstone run — S0 → S6 on the pre-registered corpus (#9080).

This is the first half of the issue's own acceptance command. It exists because
the epic owns no implementation and its closing rule is an *execution*:
*"Close this epic only when E1–E5 all have evidence attached to this issue —
not when the children close. A child closing is a claim; this epic is where the
claim is checked against the pipeline running end to end."*

⚠️ **Re-measured at `717355e67` (the issue body's figures are pinned to
`9037c7a7b`).** Two of its statements are STALE and this probe is built on the
corrected reading:

1. **A single entry point DOES exist.** The body says *"whether any single
   entry point … can drive all eight in one pass"* is an open question. It can:
   ``TortoiseSDK.capture_session`` → ``sdk._extract_session_v2`` →
   ``extractor_v2.extract_session_v2`` (``extractor_v2.py:7611``) calls
   ``_edus_from_conversation`` (S0, ``:7719``), ``run_s1`` (S1, ``:7752``),
   ``run_s2`` (S2.1, ``:7776``), ``_run_vet_pass`` (S2.2, ``:7805``),
   ``kind_classifier.classify_items``/``_apply_classify_kinds`` (S2.3,
   ``:7821``), ``search_graph`` (S3, ``:7837``), ``run_s4`` (the gap filler,
   ``:7858``), and ``execute_embed`` (S5+S6, ``:8057``). So the capstone drives
   the REAL entry point — it does not compose the step functions, which the
   issue explicitly forbids.
2. **``docs/evidence/7949-capstone/`` did not exist and still does not exist as
   a measured bundle** — this probe creates it, and the bundle ships with a
   REFUSED log rather than a green table (see below).

## The gate chain — every gate FAILS CLOSED, in §4.4's order

§4.4: S0 READ is **FAIL-CLOSED — end the run before any model call or write**.
The capstone inherits that: a run whose pre-registration does not exist must be
refused BEFORE it spends a model call, and the refusal must be recorded as a
verdict table, not as an exception. The chain:

    G0  the pre-registration commit resolves, carries the artifact, and is an
        ancestor of HEAD                                   → REFUSED (#9075)
    G1  the corpus matches the sha the pre-registration froze → REFUSED (#9075)
    G2  TORTOISE_DB_URI is a SEARCHABLE backend (not embedded/lite) — an
        embedded store never searches, so S3 resolves nothing and supersession
        records are structurally zero                           → REFUSED
    G3  a routing-usable LLM provider key exists — without one `capture_session`
        SKIPS extraction and still stores the turns, i.e. NO S1–S6 would run
                                                                 → REFUSED
    G4  the real entry point runs one pass                      → REFUSED on raise
    G5  all EIGHT steps were OBSERVED in that one pass (§4.4's step count)
                                                                 → REFUSED
    G6  the per-criterion evidence is collected

Nothing is inferred. A refused run emits 5 rows that are NOT green, each naming
the issue that blocks it — which is exactly what the issue asks for:
*"it must report a red row and name the blocking issue rather than infer green
from a closed child."*

## Why the steps are observed, never instrumented by wrapping

E4's row explicitly *"fails if … the extractor was monkeypatched"*, so the probe
must not wrap the pipeline to watch it — a wrapper is the evidence-destroying
move the row is guarding against. The probe READS the pipeline's own returned
surface; it never installs a spy.

⚠️ **That surface is only half-reachable, and the half that is not is #9116.**
`extract_session_v2` returns the full per-stage block (``vet``,
``classify_later``, ``search``, ``payload``, ``embed_list``, ``story_arc``), but
`capture_session` — the entry point this probe drives — assembles its receipt
from ``extracted`` + ``meta``, and `_extract_session_v2` forwards only
``stats``/``error_census``/``errors``/``warnings``/``mode``/``operator_ids``/
``provider``/``route``/``failover_used``/``source_change_notices``. So the four
keys in ``WITNESS_KEYS_OFF_RECEIPT`` are **structurally unobservable on the
object the probe holds**, and G5 refuses for EVERY input until the seam lands.
The refusal is attributed to that INSTRUMENT gap (#9116) — never to "the
pipeline did not run VET/classify/search/commit", which is the one wrong
reading a bundle like this must not produce. Where the surface cannot witness a
step, the step is reported **unobserved** — an honest finding for #7949, never
a guess.

Run:
    python docs/evidence/7949-capstone/probes/capstone_run.py \\
        --prereg <frozen pre-registration commit> --corpus <corpus> \\
        | tee docs/evidence/7949-capstone/capstone.log
    python tools/capstone_verdict_check.py docs/evidence/7949-capstone/capstone.log
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ``parents`` is indexed from the FILE: [0]=probes, [1]=7949-capstone,
# [2]=evidence, [3]=docs, [4]=<repo>. ``parents[3]`` is ``<repo>/docs`` — the
# off-by-one was masked only because every git call is subdirectory-safe, and
# any later ``ROOT / <repo-relative path>`` join (the shape
# ``capstone_verdict_check.check`` already uses with ``repo / ev_path``) would
# silently resolve under ``docs/``. Pinned by
# tests/test_7949_capstone_verdict_check.py::test_root_is_the_repo_root.
ROOT = Path(__file__).resolve().parents[4]          # repo root
EVIDENCE_DIR = Path("docs/evidence/7949-capstone")
DEFAULT_PREREG_ARTIFACT = EVIDENCE_DIR / "preregistration.json"

SCHEMA = "capstone-verdict-v1"
PREREG_SCHEMA = "capstone-preregistration/v1"
STEPS = ("S0", "S1", "S2.1", "S2.2", "S2.3", "S3", "S5", "S6")

#: Every key ``observe_steps`` reads off the object it is handed. Declared so a
#: witness can never be added silently: the contract test
#: ``test_every_witness_observe_steps_reads_is_on_the_receipt_or_declared``
#: derives the READ set from this module's source and the RECEIPT set from
#: ``tortoise/sdk.py``'s ``resp = {...}`` literal, then requires
#: ``WITNESS_KEYS - receipt == WITNESS_KEYS_OFF_RECEIPT`` exactly. Drop a key
#: from the receipt, or read an undeclared one, and it reds.
WITNESS_KEYS = ("stats", "story_arc", "embed_list", "vet",
                "classify_later", "search", "payload")

#: The subset of ``WITNESS_KEYS`` that ``TortoiseSDK.capture_session``'s receipt
#: does NOT carry (#9116). ``stats`` is the receipt's only pipeline surface, so
#: every step whose witness lives in here is unobservable through this entry
#: point — the run refuses at G5 and says so, attributed to the instrument.
WITNESS_KEYS_OFF_RECEIPT = ("story_arc", "embed_list", "vet",
                            "classify_later", "search", "payload")

#: The steps with NO witness on the receipt at all. S1/S2.1 survive without
#: their ``story_arc``/``embed_list`` fallbacks because ``stats.llm.by_stage``
#: carries them; these four have no other witness.
UNOBSERVABLE_STEPS = ("S2.2", "S2.3", "S3", "S6")

# Each criterion's OWNER — the issue that must land for the row to be
# measurable at all. The table's non-green rows cite these, so a red row always
# carries its cause and never reads as "not done yet".
OWNERS = {
    "E1": "#9075",   # pre-registration (thresholds) gates the measurement in #9076
    "E2": "#9075",
    "E3": "#5199",   # :Object/:Event version anchoring — the anchor stops at Points
    "E4": "#9077",   # the planted-operator corpus, real path, no monkeypatch
    "E5": "#7947",   # the falsifier (§10) run on OUR corpus
}
# Criterion-specific causes that survive the pre-registration landing: named in
# the row's note so the reader is not told "#9075" for a row #9075 cannot fix.
ALSO_BLOCKED_BY = {"E1": "#9076", "E2": "#9076"}

BEGIN = "=== CAPSTONE-VERDICT-BEGIN ==="
END = "=== CAPSTONE-VERDICT-END ==="


# ── pure helpers (pinned by tests/test_7949_capstone_verdict_check.py) ─────

def corpus_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_corpus(path: Path) -> list[dict]:
    """Read the corpus as the `capture_session` conversation shape.

    Accepts a JSON list of ``{"role", "content"}`` turns or JSONL of the same.
    A corpus that does not parse is an INPUT error (§4.4 S0: "A parse/mapping
    failure is an INPUT error — surfaced, never retried").
    """
    text = path.read_text()
    stripped = text.lstrip()
    if stripped.startswith("["):
        turns = json.loads(text)
    else:
        turns = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not isinstance(turns, list) or not turns:
        raise ValueError(f"corpus {path} is empty")
    for turn in turns:
        if not isinstance(turn, dict) or not str(turn.get("content", "")).strip():
            raise ValueError(f"corpus {path}: turn without content: {turn!r}")
    return turns


def observe_steps(result: dict) -> dict[str, str]:
    """Which of the EIGHT steps did THIS pass actually execute?

    Returns ``{step: why}`` for the steps observed, and nothing for the rest —
    absence is the signal. Each witness is read off the pipeline's own returned
    surface (never a wrapper), so the observation cannot alter what it watches.

    ⚠️ The keys in ``WITNESS_KEYS_OFF_RECEIPT`` are not on the object the
    shipped caller can pass (#9116): ``capture_session``'s receipt carries
    ``stats`` only. They are read anyway so that the day the seam lands the
    observation is already complete — and ``UNOBSERVABLE_STEPS`` names which
    steps that costs today, so G5's refusal is attributed to the instrument
    rather than to the pipeline.
    """
    stats = result.get("stats") or {}
    llm = stats.get("llm") or {}
    by_stage = llm.get("by_stage") or {}
    embed = result.get("embed_list") or {}
    items = sum(len(embed.get(k) or [])
                for k in ("entities", "events", "points", "operators"))
    vet = result.get("vet") or {}
    classify = result.get("classify_later") or {}

    seen: dict[str, str] = {}
    if stats.get("edus") or stats.get("chunks"):
        seen["S0"] = (f"reader segmented the source: edus={stats.get('edus')} "
                      f"chunks={stats.get('chunks')}")
    if by_stage.get("s1") or result.get("story_arc"):
        seen["S1"] = (f"first LLM call reached: by_stage.s1="
                      f"{bool(by_stage.get('s1'))} story_arc="
                      f"{bool(result.get('story_arc'))}")
    if by_stage.get("s2") or items:
        seen["S2.1"] = f"S2 map produced {items} candidate item(s)"
    if vet.get("s2") or vet.get("union"):
        seen["S2.2"] = "the VET gate reported a pass (result['vet'])"
    if classify.get("s2") or classify.get("union"):
        seen["S2.3"] = "a classify pass reported assignments (result['classify_later'])"
    if result.get("search"):
        seen["S3"] = "S3 search surface present (result['search'])"
    if (stats.get("s4_merge") or {}).get("merged_items") is not None:
        seen["S5"] = (f"S5 merged {stats['s4_merge'].get('merged_items')} item(s) "
                      f"into the commit list")
    if result.get("payload") is not None:
        seen["S6"] = "the Layer-1 commit payload was produced (result['payload'])"
    return seen


def e4_receipt(stats: dict) -> dict:
    """E4's receipt — the no-silent-drop proof (`_s4_merge_stats`).

    E4's row *"fails if the denominator is 4"*: the historical defect ran the
    merge over only 4 of the 15 edges reachable on the real extraction leg, so
    a receipt whose denominator is 4 is the defect re-appearing, not a
    measurement. The receipt is carried verbatim so the check reads the same
    numbers the pipeline emitted.
    """
    merge = stats.get("s4_merge") or {}
    den = int(merge.get("s2_items") or 0)
    return {
        "numerator": int(merge.get("kept_from_s2") or 0),
        "denominator": den,
        "s4_items": merge.get("s4_items"),
        "merged_items": merge.get("merged_items"),
        "corrected_by_s4": merge.get("corrected_by_s4"),
        "verbatim_reemissions": merge.get("verbatim_reemissions"),
        "added_by_s4": merge.get("added_by_s4"),
        "denominator_is_the_4_edge_defect": den == 4,
    }


def refused_rows(reason: str, *, prereg_ok: bool) -> list[dict]:
    """The 5 rows of a REFUSED run.

    A refusal cannot make a row green, so every row is non-green and names its
    cause. ``UNPREREGISTERED`` is reserved for E1/E2 when the pre-registration
    itself is the missing thing: those two rows are the ones whose thresholds
    #9075 owns, and calling them "blocked by an unimplemented mechanism" would
    misattribute the cause.
    """
    rows = []
    for cid in ("E1", "E2", "E3", "E4", "E5"):
        verdict = ("UNPREREGISTERED" if cid in ("E1", "E2") and not prereg_ok
                   else "BLOCKED")
        # Two distinct causes, both stated: WHY this run produced no number,
        # and WHICH lane owns the number. A row that named only the gate would
        # read as "blocked by the run", and the next lane would re-run instead
        # of landing the mechanism. A row that named only the owner would hide
        # that the table's own run never got off the ground.
        note = f"gate refused: {reason}; this criterion's evidence is owned by "
        note += OWNERS[cid]
        if cid in ALSO_BLOCKED_BY:
            note += f" / the measurement itself is {ALSO_BLOCKED_BY[cid]}"
        rows.append({
            "criterion": cid,
            "verdict": verdict,
            "evidence_kind": "refusal",
            "evidence": str(EVIDENCE_DIR / "capstone.log"),
            "sha": None,          # filled by emit_log with the run head
            "numerator": None,
            "denominator": None,
            "corpus_sha256": None,
            "sibling": None,
            "blocked_by": OWNERS[cid],
            "note": note,
        })
    return rows


def emit_log(*, head: str, prereg: dict, corpus: dict, run: dict,
             rows: list[dict], out: Path | None) -> str:
    """Render the run log — human record on top, machine table at the bottom."""
    for row in rows:
        if not row.get("sha"):
            row["sha"] = head
    record = {
        "schema": SCHEMA,
        "issue": 9080,
        "epic": 7949,
        "head": head,
        "prereg": prereg,
        "corpus": corpus,
        "run": run,
        "rows": rows,
    }
    lines = [
        "# extractor-v4 capstone — S0 → S6 on the pre-registered corpus (#9080)",
        f"# generated: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"# head: {head}",
        f"# pre-registration: {prereg.get('sha')} ({prereg.get('status')})",
        f"# corpus: {corpus.get('path')} sha256={corpus.get('sha256')} "
        f"turns={corpus.get('turns')}",
        f"# run: {run.get('status')} — {run.get('reason')}",
    ]
    observed = run.get("steps_observed") or []
    lines.append("# steps observed: "
                 + (" ".join(observed) if observed else "(none)")
                 + f"  required: {' '.join(STEPS)}")
    missing = [s for s in STEPS if s not in observed]
    if missing:
        lines.append("# steps MISSING: " + " ".join(missing))
    lines += ["", "## Verdict", "",
              "| criterion | verdict | evidence | blocked_by | note |",
              "|---|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['criterion']} | {row['verdict']} | "
                     f"{row['evidence']} | {row.get('blocked_by') or '—'} | "
                     f"{row['note']} |")
    lines += ["", BEGIN, json.dumps(record, indent=2, sort_keys=True), END, ""]
    text = "\n".join(lines)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
    return text


# ── git / pre-registration ────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, check=False)


def read_preregistration(sha: str, artifact: Path) -> tuple[dict | None, str]:
    """Read the pre-registration AS IT WAS at ``sha`` — never from the worktree.

    Reading it out of the frozen commit is the whole property: a threshold
    edited after the numbers are in is not a pre-registration, and a worktree
    read would happily serve that edit.
    """
    if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
        return None, f"pre-registration commit {sha!r} does not resolve in this repo"
    head = _git("rev-parse", "HEAD").stdout.strip()
    if head and _git("merge-base", "--is-ancestor", sha, head).returncode != 0:
        return None, (f"pre-registration {sha} is not an ancestor of HEAD {head} "
                      "— thresholds must be fixed before the run, not on a "
                      "branch the run cannot see")
    blob = _git("show", f"{sha}:{artifact.as_posix()}")
    if blob.returncode != 0:
        return None, (f"commit {sha} does not carry {artifact.as_posix()} — "
                      "there is no pre-registration to measure against (#9075)")
    try:
        record = json.loads(blob.stdout)
    except json.JSONDecodeError as e:
        return None, f"{artifact.as_posix()} at {sha} is not JSON: {e}"
    if record.get("schema") != PREREG_SCHEMA:
        return None, (f"{artifact.as_posix()} at {sha} declares schema "
                      f"{record.get('schema')!r}, expected {PREREG_SCHEMA!r}")
    if not record.get("written_before_any_run"):
        return None, ("the pre-registration does not assert "
                      "written_before_any_run — a threshold without that claim "
                      "may have been chosen to fit the number")
    return record, ""


def backend_is_searchable() -> tuple[bool, str]:
    """S3 needs a SEARCHABLE backend or the whole table is structurally void.

    `sdk.capture_session`'s own contract: over embedded/FalkorDBLite the S3
    search is skipped, so supersession records are ZERO structurally. A capstone
    run there would measure the embedded lane's absence of S3 and report it as
    the product's — so this refuses instead.

    The decision is the extractor's OWN gate, not a string test: `config.
    is_db_uri()` accepts only ``docker://`` / ``redis://`` / ``rediss://``, and
    `extractor_v2.resolve_backend_mode()` reports anything else as ``embedded``
    (``TORTOISE_API_URL`` set and no supported DB URI → ``hosted``). A
    hand-rolled heuristic drifts in BOTH directions (#9080 review): it ACCEPTED
    ``TORTOISE_DB_URI=/tmp/graph.db`` — a file path, which `is_db_uri` rejects
    and the SDK therefore treats as the EMBEDDED store, S3 skipped — and it
    REFUSED a hosted lane. No SDK exists yet at G2, so the env-derived label is
    the authoritative half of `_is_searchable_backend` here.
    """
    from tortoise.extractor_v2 import _is_searchable_backend, resolve_backend_mode

    uri = os.environ.get("TORTOISE_DB_URI")
    mode = resolve_backend_mode()
    if _is_searchable_backend(mode):
        return True, ""
    if not uri and not os.environ.get("TORTOISE_API_URL"):
        return False, ("neither TORTOISE_DB_URI nor TORTOISE_API_URL is set — "
                       f"the backend is {mode!r} (the embedded store), where "
                       "S3 does not search")
    return False, (f"TORTOISE_DB_URI={uri!r} resolves to the {mode!r} backend — "
                   "S3 does not search there, so supersession records would be "
                   "structurally zero (a supported scheme is docker/redis/"
                   "rediss, or set TORTOISE_API_URL for the hosted lane)")


def model_key_present() -> tuple[bool, str]:
    """Without a provider key `capture_session` SKIPS extraction and stores turns.

    That is a documented behaviour (#3892), not an error — but it means S1–S6
    never run, so measuring it would report a storage write as an extraction.

    The predicate is the SAME one `_extract_session_v2` gates on (#1530), not a
    hand-written key list (#9080 review): `resolve_extractor_provider()` reads
    DEEPSEEK/OPENROUTER/VENICE, and ``TORTOISE_SESSION_LLM_MOCK=1`` is the
    offline seam the repo's e2e lanes use (#1468). A list that also accepted
    OPENAI/ANTHROPIC/TORTOISE_LLM_API_KEY passed hosts the adapter cannot
    consume — they then died at G4 blaming the entry point — and refused a
    mock-seam host that would have run.
    """
    from tortoise.model_adapters import resolve_extractor_provider
    from tortoise.sdk import _session_llm_mock_enabled

    if _session_llm_mock_enabled():
        return True, ""
    try:
        primary = resolve_extractor_provider()[0]
    except ValueError as e:  # explicit TORTOISE_EXTRACTOR_PROVIDER, keyless
        return False, ("the extractor provider is explicit but its key is "
                       f"absent — the adapter fails closed: {e}")
    if primary:
        return True, ""
    return False, ("no routing-usable LLM provider key is set (DEEPSEEK_API_KEY "
                   "/ OPENROUTER_API_KEY / VENICE_API_KEY via "
                   "resolve_extractor_provider, or TORTOISE_SESSION_LLM_MOCK=1) "
                   "— capture_session would SKIP extraction (#3892) and S1–S6 "
                   "would not run")


# ── the run ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Run the extractor-v4 capstone (S0→S6) once (#9080).")
    ap.add_argument("--prereg", required=True,
                    help="the frozen pre-registration commit")
    ap.add_argument("--corpus", required=True, help="the pre-registered corpus")
    ap.add_argument("--prereg-artifact", default=str(DEFAULT_PREREG_ARTIFACT),
                    help=f"path of the pre-registration record (default "
                         f"{DEFAULT_PREREG_ARTIFACT})")
    ap.add_argument("--out", default=None,
                    help="also write the log here (stdout always gets it)")
    ap.add_argument("--head", default=None, help="override the measured sha")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    head = args.head or _git("rev-parse", "HEAD").stdout.strip() or "unknown"
    corpus_path = Path(args.corpus)
    out = Path(args.out) if args.out else None

    def refuse(reason: str, *, prereg_ok: bool, code: int,
               prereg: dict | None = None, corpus: dict | None = None) -> int:
        """Record a refusal as a verdict table and return the gate's exit code.

        ``prereg_ok`` is not a label for the reason — it IS "G0 read the
        pre-registration successfully". Every gate after G0 passes True, so the
        recorded ``prereg.status`` must follow the SAME fact: only the G0
        refusal may write ``ABSENT``. Writing ``ABSENT`` on a G1–G5 refusal
        would tell the next lane that `#9075` is still open when the run had
        already read the frozen thresholds. Pinned by
        tests/test_7949_capstone_verdict_check.py::
        test_post_g0_refusals_record_the_preregistration_as_read.
        """
        rows = refused_rows(reason, prereg_ok=prereg_ok)
        text = emit_log(
            head=head,
            prereg=prereg or {"sha": args.prereg,
                              "status": "OK" if prereg_ok else "ABSENT",
                              "artifact": args.prereg_artifact},
            corpus=corpus or {"path": args.corpus, "sha256": None, "turns": None},
            run={"status": "REFUSED", "reason": reason, "steps_observed": [],
                 "steps_required": list(STEPS),
                 "gate_failed": code},
            rows=rows, out=out)
        sys.stdout.write(text)
        return code

    # G0 — the pre-registration (FAIL-CLOSED, before any model call)
    prereg_record, why = read_preregistration(
        args.prereg, Path(args.prereg_artifact))
    if prereg_record is None:
        return refuse(why, prereg_ok=False, code=2)

    # G1 — the corpus is the corpus that was frozen
    if not corpus_path.is_file():
        return refuse(f"corpus {args.corpus} does not exist", prereg_ok=True,
                      code=2)
    sha = corpus_sha256(corpus_path)
    # The sha is COMPUTED, so every G1 refusal below carries it: a refusal that
    # discards a sha it already has leaves the log saying "no corpus" about a
    # corpus it read (the row's denominator would be unattributable later).
    corpus = {"path": args.corpus, "sha256": sha, "turns": None}
    frozen = prereg_record.get("corpus") or {}
    # FAIL CLOSED on a pre-registration that freezes no corpus (#9080 review):
    # both checks used to be conditional, so a record carrying thresholds but no
    # `corpus` block made G1 a no-op — the run then measured whatever corpus it
    # was handed and stamped that corpus's sha on every row as if it were the
    # pre-registered one. V7 cannot catch that: it compares a row's sha to the
    # log's own, which agree by construction.
    if not frozen.get("sha256"):
        return refuse(
            "the pre-registration freezes no corpus.sha256 — G1 would be a "
            "no-op, so the run would measure an arbitrary corpus and report it "
            "as the pre-registered one", prereg_ok=True, code=2, corpus=corpus)
    if frozen["sha256"] != sha:
        return refuse(
            f"corpus sha256 {sha} != the pre-registered {frozen['sha256']} — "
            "a run on a different corpus cannot decide a threshold frozen "
            "against this one", prereg_ok=True, code=2, corpus=corpus)
    try:
        turns = load_corpus(corpus_path)
    except (ValueError, json.JSONDecodeError) as e:
        return refuse(f"corpus is an INPUT error, not retried: {e}",
                      prereg_ok=True, code=2, corpus=corpus)
    corpus["turns"] = len(turns)
    if frozen.get("items") and int(frozen["items"]) != len(turns):
        return refuse(
            f"corpus has {len(turns)} turns, the pre-registration froze "
            f"{frozen['items']}", prereg_ok=True, code=2, corpus=corpus)

    # G2 / G3 — a run that cannot execute every step must not report a table
    ok, why = backend_is_searchable()
    if not ok:
        return refuse(why, prereg_ok=True, code=3, corpus=corpus)
    ok, why = model_key_present()
    if not ok:
        return refuse(why, prereg_ok=True, code=3, corpus=corpus)

    # G4 — ONE pass through the REAL entry point
    from tortoise.sdk import TortoiseSDK  # imported late: the gates above need no client

    sdk = TortoiseSDK()
    session_id = f"capstone_{uuid.uuid4().hex[:12]}"
    try:
        captured = sdk.capture_session(turns, session_id=session_id)
        # The receipt IS the only surface. ``capture_session`` never returns an
        # ``extraction`` key (sdk.py assembles ``resp`` from ``extracted`` +
        # ``meta``), so the old ``captured.get("extraction") or captured`` was a
        # DEAD lookup whose fallback silently hid the shape mismatch — which is
        # how four unobservable witnesses went unnoticed (#9116, review F1).
        result = captured
    except Exception as e:  # noqa: BLE001, RUF100 — a refused run is a verdict, not a crash
        return refuse(f"the entry point raised: {type(e).__name__}: {e}",
                      prereg_ok=True, code=4, corpus=corpus)
    if not (result.get("stats") or {}).get("llm"):
        return refuse(
            "the pass returned no extractor stats — capture_session did not "
            "reach extract_session_v2 (extraction_mode="
            f"{captured.get('extraction_mode')!r}); no step can be observed",
            prereg_ok=True, code=4, corpus=corpus)

    # G5 — EIGHT steps, or the table is not the epic's table
    observed = observe_steps(result)
    missing = [s for s in STEPS if s not in observed]
    if missing:
        unobservable = [s for s in missing if s in UNOBSERVABLE_STEPS]
        # Attribute an unobservable step to the INSTRUMENT. The receipt carries
        # `stats` only (#9116), so a bare "steps not observed" reads as "the
        # pipeline never ran VET/classify/search/commit" — a product finding
        # this surface cannot support, and the one wrong reading that would
        # send the epic's readers to the wrong issue.
        if unobservable:
            return refuse(
                "steps not observed in the pass: " + ", ".join(missing)
                + f" — {', '.join(unobservable)} have NO witness on "
                "capture_session's receipt (#9116: the receipt carries `stats` "
                "only, and vet/classify_later/search/payload never leave "
                "extract_session_v2), so this is an INSTRUMENT gap and NOT the "
                "pipeline skipping those steps; §4.4 forbids a table from a "
                "subset, so the epic stays open until #9116 lands",
                prereg_ok=True, code=5, corpus=corpus)
        return refuse(
            "steps not observed in the pass: " + ", ".join(missing)
            + " — §4.4 forbids a table assembled from a subset",
            prereg_ok=True, code=5, corpus=corpus)

    # G6 — per-criterion evidence
    rows: list[dict] = []
    for cid in ("E1", "E2", "E3", "E4", "E5"):
        rows.append({
            "criterion": cid, "verdict": "BLOCKED", "evidence_kind": "refusal",
            "evidence": str(EVIDENCE_DIR / "capstone.log"), "sha": head,
            "numerator": None, "denominator": None, "corpus_sha256": sha,
            "sibling": None, "blocked_by": OWNERS[cid],
            "note": "the run completed all eight steps; this criterion's "
                    "evidence artifact is still owned by " + OWNERS[cid],
        })
        if cid in ALSO_BLOCKED_BY:
            rows[-1]["note"] += f" / {ALSO_BLOCKED_BY[cid]}"
        rows[-1]["sibling"] = "E2" if cid == "E1" else ("E1" if cid == "E2"
                                                       else None)
    receipt = e4_receipt(result.get("stats") or {})
    for row in rows:
        if row["criterion"] == "E4":
            row["note"] += (f"; receipt carried: kept_from_s2="
                            f"{receipt['numerator']}/"
                            f"{receipt['denominator']}"
                            + (", denominator is the 4-edge defect"
                               if receipt["denominator_is_the_4_edge_defect"]
                               else ""))
    sys.stdout.write(emit_log(
        head=head,
        prereg={"sha": args.prereg, "status": "OK",
                "artifact": args.prereg_artifact},
        corpus=corpus,
        run={"status": "COMPLETED", "reason": "eight steps observed in one pass",
             "steps_observed": [s for s in STEPS if s in observed],
             "steps_required": list(STEPS), "gate_failed": None,
             "session_id": session_id, "e4_receipt": receipt},
        rows=rows, out=out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
