"""Deterministic generator for the sealed ask-lane corpus (issue #9097).

Single authored source for ``fixtures/`` + ``gold/`` + ``_manifest.json`` +
``baselines/main.json``. Re-running it is byte-deterministic (sorted keys, fixed
indent, no timestamps), so a hand edit to a committed fixture or to the sealed
gold shows up as a ``--check`` drift, and every anchor is verified against the
turn it was planted in at render time.

⛔ **Generator-owned.** Never hand-edit ``fixtures/`` or ``gold/``. Add or
change a case in ``_CASES`` below and re-run. That rule is the one #7947 paid
for on the write-path corpus: a hand-added section in the sealed gold reddened
18 tests and had to be reverted.

Regeneration protocol:

    uv run python -m tests.eval.ask.generate_corpus            # idempotent write
    uv run python -m tests.eval.ask.generate_corpus --check    # exit 1 on drift
    uv run python -m tests.eval.ask.generate_corpus --validate # full committed-dir validation
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

if __package__ in (None, ""):  # pragma: no cover - script-style invocation
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tests.eval.ask import corpus

# ── the authored corpus ───────────────────────────────────────────────────
#
# Every case is one fact the reader must commit, held in TWO arms of the same
# invocation (D4: extend, never a parallel benchmark):
#
#   raw        — the capture-shaped TURN STORE only (`haystack_*`), the shape
#                the pre-D7 spot-check instrument had;
#   distilled  — the same turn store PLUS the distilled claims written the way
#                the capture extraction writer writes them
#                (`create_point(is_episodic=False, validFrom=…, session_id=…)`)
#                and, for the knowledge-update class, the planted supersession
#                written through the product's own `supersede_point`.
#
# ⛔ INVARIANT (enforced in `_issues`): every distilled claim's text appears
# VERBATIM in a turn of its own session. That is what makes the two arms a
# paired read of the SAME fact — the arms differ in FORM (narrative turn vs
# distilled claim), never in CONTENT — so a distilled/raw difference measures
# the distillation, not a corpus difference.
_CASES: tuple[dict, ...] = (
    # ── knowledge-update class: a planted supersession (the D7/D8 class) ──
    {
        "case_id": "ask01_residence",
        "class": corpus.CLASS_KNOWLEDGE_UPDATE,
        "question_type": "knowledge-update",
        "question": "Where do I live?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("residence-old", "residence-new"),
            "dates": ("2023/03/02 (Thu) 10:00", "2023/05/20 (Sat) 10:00"),
            "sessions": (
                ({"role": "user", "content": "I live in Lisbon."},),
                ({"role": "user", "content": "I live in Porto."},),
            ),
        },
        "claims": (
            {"text": "I live in Lisbon.", "session_id": "residence-old",
             "valid_from": "2023-03-02"},
            {"text": "I live in Porto.", "session_id": "residence-new",
             "valid_from": "2023-05-20"},
        ),
        "supersessions": (
            {"old": "I live in Lisbon.", "new": "I live in Porto."},
        ),
        "judge_template": "knowledge-update",
        "gold": {"answer": "I live in Porto."},
    },
    {
        "case_id": "ask02_diet",
        "class": corpus.CLASS_KNOWLEDGE_UPDATE,
        "question_type": "knowledge-update",
        "question": "What diet am I on?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("diet-old", "diet-new"),
            "dates": ("2023/04/04 (Tue) 08:30", "2023/05/26 (Fri) 08:30"),
            "sessions": (
                ({"role": "user", "content": "I eat a vegetarian diet."},),
                ({"role": "user", "content": "I eat a vegan diet."},),
            ),
        },
        "claims": (
            {"text": "I eat a vegetarian diet.", "session_id": "diet-old",
             "valid_from": "2023-04-04"},
            {"text": "I eat a vegan diet.", "session_id": "diet-new",
             "valid_from": "2023-05-26"},
        ),
        "supersessions": (
            {"old": "I eat a vegetarian diet.", "new": "I eat a vegan diet."},
        ),
        "judge_template": "knowledge-update",
        "gold": {"answer": "I eat a vegan diet."},
    },
    {
        "case_id": "ask03_employer",
        "class": corpus.CLASS_KNOWLEDGE_UPDATE,
        "question_type": "knowledge-update",
        "question": "Who do I work for?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("employer-old", "employer-new"),
            "dates": ("2023/01/09 (Mon) 09:15", "2023/05/15 (Mon) 09:15"),
            "sessions": (
                ({"role": "user", "content": "I work at Contoso Ltd."},),
                ({"role": "user",
                  "content": "I work at Northwind Traders."},),
            ),
        },
        "claims": (
            {"text": "I work at Contoso Ltd.", "session_id": "employer-old",
             "valid_from": "2023-01-09"},
            {"text": "I work at Northwind Traders.",
             "session_id": "employer-new", "valid_from": "2023-05-15"},
        ),
        "supersessions": (
            {"old": "I work at Contoso Ltd.",
             "new": "I work at Northwind Traders."},
        ),
        "judge_template": "knowledge-update",
        "gold": {"answer": "I work at Northwind Traders."},
    },
    # ── verbatim class: the class the exit bar must NOT regress ──────────
    #
    # No supersession, ONE distilled claim: this is the plain "the fact is in
    # the turn verbatim" shape. The exit bar reads it PAIRED — the same case in
    # both arms of one run — so a distilled loss against the raw arm is a FAIL,
    # and no pre-set number is needed (D8 forbids inventing one).
    {
        "case_id": "ask04_badge",
        "class": corpus.CLASS_VERBATIM,
        # The ask lane's supported reader types are
        # temporal-reasoning|knowledge-update|multi-session|
        # single-session-preference — there is no plain look-up type, and
        # forcing `knowledge-update` would tell the reader to expect an update
        # that does not exist. `None` gets the product's hardened GENERIC
        # prompt, which is the honest one for a plain fact question. The
        # (sealed) judge_template below is what grades it.
        "question_type": None,
        "judge_template": "single-session-user",
        "question": "What is my badge number?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("badge-2023",),
            "dates": ("2023/04/12 (Wed) 09:40",),
            "sessions": (
                ({"role": "user",
                  "content": "Facilities reissued my card today — my badge "
                             "number is QX-4417."},),
            ),
        },
        "claims": (
            {"text": "my badge number is QX-4417", "session_id": "badge-2023",
             "valid_from": "2023-04-12"},
        ),
        "supersessions": (),
        "gold": {"answer": "QX-4417"},
    },
    {
        "case_id": "ask05_ferry",
        "class": corpus.CLASS_VERBATIM,
        "question_type": None,
        "judge_template": "single-session-user",
        "question": "Which ferry do I take to the office?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("commute-2023",),
            "dates": ("2023/03/21 (Tue) 07:05",),
            "sessions": (
                ({"role": "user",
                  "content": "On weekdays I take the 07:15 ferry to the "
                             "office."},),
            ),
        },
        "claims": (
            {"text": "I take the 07:15 ferry to the office.",
             "session_id": "commute-2023", "valid_from": "2023-03-21"},
        ),
        "supersessions": (),
        "gold": {"answer": "The 07:15 ferry"},
    },
    {
        "case_id": "ask06_allergy",
        "class": corpus.CLASS_VERBATIM,
        "question_type": None,
        "judge_template": "single-session-user",
        "question": "What am I allergic to?",
        "question_date": "2023/06/01 (Thu) 10:00",
        "turn_store": {
            "session_ids": ("dinner-2023",),
            "dates": ("2023/05/02 (Tue) 20:10",),
            "sessions": (
                ({"role": "user",
                  "content": "Dinner was a problem: I am allergic to "
                             "shellfish."},),
            ),
        },
        "claims": (
            {"text": "I am allergic to shellfish.", "session_id": "dinner-2023",
             "valid_from": "2023-05-02"},
        ),
        "supersessions": (),
        "gold": {"answer": "Shellfish"},
    },
)


#: The ask lane's supported READER types (``tortoise/ask_lane.py`` →
#: ``ASK_QUESTION_TYPES``, enforced by ``_ask_validate``). ``None`` is also
#: accepted: the lane then uses its generic prompt + deterministic detection.
ASK_LANE_QUESTION_TYPES: tuple[str, ...] = (
    "temporal-reasoning", "knowledge-update", "multi-session",
    "single-session-preference")

#: The official anscheck templates (``judge.get_anscheck_prompt``).
JUDGE_TEMPLATES: tuple[str, ...] = (
    "single-session-user", "single-session-assistant",
    "single-session-preference", "multi-session", "temporal-reasoning",
    "knowledge-update")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _fixture_doc(case: dict) -> dict:
    """The RUNNER-visible half of a case: ingest inputs + the question."""
    return {
        "schema": corpus.FIXTURE_SCHEMA,
        "case_id": case["case_id"],
        "class": case["class"],
        "question_type": case["question_type"],
        "question": case["question"],
        "question_date": case["question_date"],
        "haystack_session_ids": list(case["turn_store"]["session_ids"]),
        "haystack_dates": list(case["turn_store"]["dates"]),
        "haystack_sessions": [
            [dict(turn) for turn in session]
            for session in case["turn_store"]["sessions"]],
        "distilled": {
            "claims": [dict(claim) for claim in case["claims"]],
            "supersessions": [dict(sup) for sup in case["supersessions"]],
        },
    }


def _gold_doc(case: dict) -> dict:
    """The SEALED half: the answer the judge grades against."""
    return {
        "schema": corpus.GOLD_SCHEMA,
        "case_id": case["case_id"],
        "class": case["class"],
        "answer": case["gold"]["answer"],
        # The OFFICIAL LongMemEval anscheck template this case is graded with
        # (tools/longmem_eval/judge.py::get_anscheck_prompt). Sealed with the
        # key because it is grading configuration: the KU class uses the
        # knowledge-update template, a plain look-up uses single-session-user.
        "judge_template": case["judge_template"],
    }


def _issues(case: dict) -> list[str]:
    """Cross-check ONE authored case. Returns issues (empty = valid).

    Checks the ways a corpus goes wrong silently rather than loudly: an
    ungrounded anchor, a supersession naming a claim that does not exist, a
    window that does not move forward, a class with the wrong shape.
    """
    out: list[str] = []
    cid = case.get("case_id") or "<missing case_id>"
    if case.get("class") not in corpus.CLASSES:
        out.append(f"{cid}: unknown class {case.get('class')!r}")
    if case.get("question_type") not in (None, *ASK_LANE_QUESTION_TYPES):
        out.append(f"{cid}: question_type {case.get('question_type')!r} is "
                   f"not a type the ask lane accepts "
                   f"({ASK_LANE_QUESTION_TYPES}) — run_ask_lane refuses it")
    if case.get("judge_template") not in JUDGE_TEMPLATES:
        out.append(f"{cid}: judge_template "
                   f"{case.get('judge_template')!r} is not an official "
                   f"anscheck template")
    store = case["turn_store"]
    sessions = store["sessions"]
    if not (len(store["session_ids"]) == len(store["dates"])
            == len(sessions)):
        out.append(f"{cid}: haystack_session_ids/dates/sessions length "
                   f"mismatch")
    claims = case["claims"]
    if not claims:
        out.append(f"{cid}: no distilled claim — a case with no memory cannot "
                   f"be asked about")
    by_session: dict[str, list[str]] = {}
    for sid, turns in zip(store["session_ids"], sessions, strict=True):
        by_session[sid] = [_normalize(t.get("content", "")) for t in turns]
    for claim in claims:
        sid = claim.get("session_id")
        if sid not in by_session:
            out.append(f"{cid}: claim session_id {sid!r} is not in the turn "
                       f"store")
            continue
        # INVARIANT: the claim's words are in a turn of its own session —
        # the pairing that makes raw vs distilled a difference of FORM only.
        if not any(_normalize(claim["text"]) in turn
                   for turn in by_session[sid]):
            out.append(
                f"{cid}: claim {claim['text']!r} is not verbatim in any "
                f"{sid!r} turn — the two arms would measure different content")
        try:
            date.fromisoformat(claim["valid_from"])
        except (KeyError, ValueError):
            out.append(f"{cid}: claim valid_from {claim.get('valid_from')!r} "
                       f"is not an ISO date")
    texts = {claim["text"] for claim in claims}
    for sup in case["supersessions"]:
        if sup.get("old") not in texts or sup.get("new") not in texts:
            out.append(f"{cid}: supersession {sup!r} names a claim that is "
                       f"not in the distilled corpus")
            continue
        old = next(c for c in claims if c["text"] == sup["old"])
        new = next(c for c in claims if c["text"] == sup["new"])
        if not old["valid_from"] < new["valid_from"]:
            out.append(f"{cid}: supersession window does not move forward "
                       f"({old['valid_from']} → {new['valid_from']})")
    if case["class"] == corpus.CLASS_KNOWLEDGE_UPDATE and not case["supersessions"]:
        out.append(f"{cid}: knowledge-update class with no planted "
                   f"supersession — the D8 gate has nothing to observe")
    if case["class"] == corpus.CLASS_VERBATIM:
        if case["supersessions"]:
            out.append(f"{cid}: verbatim class must not plant a supersession")
        if len(claims) != 1:
            out.append(f"{cid}: verbatim class carries exactly one claim "
                       f"(got {len(claims)})")
    if not str(case["gold"].get("answer") or "").strip():
        out.append(f"{cid}: empty gold answer")
    leaked = corpus.find_grading_keys(_fixture_doc(case))
    if leaked:
        out.append(f"{cid}: fixture carries grading key(s) {leaked}")
    return out


def validate_cases(cases: tuple[dict, ...] = _CASES) -> list[str]:
    """Every authored case, plus the corpus-level invariants."""
    issues: list[str] = []
    seen: set[str] = set()
    for case in cases:
        if case["case_id"] in seen:
            issues.append(f"duplicate case_id {case['case_id']!r}")
        seen.add(case["case_id"])
        issues.extend(_issues(case))
    classes = {case["class"] for case in cases}
    for required in corpus.CLASSES:
        if required not in classes:
            issues.append(f"corpus has no {required!r} case — the exit bar "
                          f"cannot cover the class it must not regress")
    return issues


def render(cases: tuple[dict, ...] = _CASES) -> dict[str, str]:
    """``{relative path: canonical bytes}`` for the whole corpus."""
    files: dict[str, str] = {}
    for case in cases:
        files[f"fixtures/{case['case_id']}.json"] = corpus.dump_json(
            _fixture_doc(case))
        files[f"gold/{case['case_id']}.gold.json"] = corpus.dump_json(
            _gold_doc(case))
    # The manifest covers fixtures AND gold (a gold-only edit changes it).
    digests = {}
    for rel, payload in files.items():
        digests[rel] = corpus.sha256_bytes(payload.encode("utf-8"))
    joined = "\n".join(f"{rel}:{digest}"
                       for rel, digest in sorted(digests.items()))
    manifest = {
        "schema": corpus.MANIFEST_SCHEMA,
        "files": digests,
        "corpus_hash": corpus.sha256_bytes(joined.encode("utf-8")),
        "sealed": {
            "gold_dir": "gold/",
            "rule": "the runner's inputs are fixtures/*.json only — the "
                    "answer key is never read by the run step",
        },
    }
    files["_manifest.json"] = corpus.dump_json(manifest)
    # The pending baseline pins the corpus hash it belongs to; it is
    # deliberately OUTSIDE the hash scope (it changes when a run is blessed).
    baseline = {
        "schema": corpus.BASELINE_SCHEMA,
        "corpus_hash": manifest["corpus_hash"],
        "judge_pin": None,
        "justification": None,
        "metrics": {},
        "history": [],
    }
    files["baselines/main.json"] = corpus.dump_json(baseline)
    return files


def write_corpus(root: Path = corpus.ASK_DIR,
                 cases: tuple[dict, ...] = _CASES) -> list[str]:
    """Write the corpus. Returns the relative paths written."""
    issues = validate_cases(cases)
    if issues:
        raise ValueError("authored corpus is invalid:\n  " + "\n  ".join(issues))
    written: list[str] = []
    for rel, payload in sorted(render(cases).items()):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        written.append(rel)
    return written


def drift(root: Path = corpus.ASK_DIR,
          cases: tuple[dict, ...] = _CASES) -> list[str]:
    """Relative paths whose committed bytes differ from a fresh render."""
    out: list[str] = []
    for rel, payload in sorted(render(cases).items()):
        path = root / rel
        if not path.exists() or path.read_text(encoding="utf-8") != payload:
            out.append(rel)
    return out


def _main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m tests.eval.ask.generate_corpus",
        description="Render / verify the sealed ask-lane corpus (#9097).")
    p.add_argument("--check", action="store_true",
                   help="exit 1 when the committed corpus drifted")
    p.add_argument("--validate", action="store_true",
                   help="validate the authored cases AND the committed tree")
    args = p.parse_args(argv)

    authoured_issues = validate_cases()
    if authoured_issues:
        print("authored corpus is invalid:", file=sys.stderr)
        for issue in authoured_issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1
    if args.check:
        drifted = drift()
        for rel in drifted:
            print(f"DRIFT {rel}", file=sys.stderr)
        return 1 if drifted else 0
    if args.validate:
        report = corpus.verify_manifest()
        if not report["ok"]:
            print(f"committed corpus invalid: {report}", file=sys.stderr)
            return 1
        print(f"committed corpus OK ({len(corpus.case_ids())} cases, "
              f"{report['corpus_hash']})")
        return 0
    for rel in write_corpus():
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
