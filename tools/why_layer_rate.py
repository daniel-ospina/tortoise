#!/usr/bin/env python3
"""#3990 — the WHY-LAYER rate instrument (objective 4's "with its reasons" half).

ONE job: measure whether the product's REAL read path returns the **reason** a
claim is believed — the operator that mediates it (id + type + Layer-3 label)
and any mitigation on that operator — over a FIXED eval set, with legs that are
deterministic reads of product-returned fields.

WHY THIS IS A SEPARATE INSTRUMENT FROM ``tools/ask_shape_rate.py``
  That tool measures the ANSWER shape (L1 abstention / L2 provenance / L3
  verbatim grounding) over a sha-pinned fixture, and its own docstring states
  that a changed fixture is a NEW instrument, not a moved rate. The why layer
  needs a different eval set (every gold claim operator-mediated, each carrying
  a known operator id and a mitigation or an explicit null), so folding it in
  would silently re-baseline an existing pre-registered rate. Two instruments,
  two eval sets, one objective.

THE RATE (pre-registered in #3990's T section, frozen before any run)
  why_rate = |{q in N : W1 and W2 and W3}| / N     (denominator N, no
  exclusions; a per-question exception is a FAIL, never dropped).

  W0 — operator id: the returned evidence carries ANY operator id. This is the
       empty-set guard's key (see ``observed_null``): W1 folds the kind and
       label legs in, so it cannot tell "no operator" from "operator named,
       reason missing" — and ``0/N`` is the honest rate for the latter.
  W1 — operator present: the returned evidence names the mediating operator
       with its ID AND ITS KIND (``operator_id`` and ``operator_type`` both
       non-null) for every question, since every question's gold claim is
       operator-mediated by construction — plus the Layer-3 ``operator_label``
       on the rows whose gold operator has one (a nulled label is not a named
       operator).
  W2 — operator correct: the named operator IS the gold operator — identity
       compared against the id the seeder minted, never a text match.
  W3 — mitigation surfaced: where the gold operator carries a mitigation, that
       mitigation is present on the returned support entry; where it does not,
       none is fabricated. The KEY MUST BE PRESENT: the contract's
       present-and-null rule exists so "no mitigation" is a measured value, so
       an absent key is a FAIL here, not a pass.

  NO LLM: all three legs read fields the product returns. The eval set is
  deliberately ALL operator-mediated — abstention and grounding stay with
  ``tools/ask_shape_rate.py`` (L1/L3), so no question is in the denominator
  while being out of scope for the legs (a vacuous pass is not a measurement).

SHAPE REACH — stated here, not only in the fixture: every eval row is the SAME
shape (one evidence -> one IMPL operator -> one claim). Mixed-edge claims (the
direct-IMPL leg) and claims mediated by MORE THAN ONE operator are outside this
set's reach — `tortoise/why.py` resolves the multi-operator case to the lowest
operator id, and `tests/test_why_operator_mediation.py` pins both shapes. This
instrument measures the shipped shape, not every shape.

MOVEMENT CONTROL (the §32 test the lane already uses) — before the rate means
anything, each leg must be able to RED:
    M4 delete the operator bridge ((sup)-[:INPUT]->(op)-[:IMPL]->(claim))
       -> W0, W1 and W2 must fall to 0;
    M6 strip the operator's Layer-3 label (REMOVE op.label)
       -> W1's LABELLED subset must fall to 0, and the unlabelled subset must
          keep passing (an unlabelled gold row is not expected to move);
    M5 delete (op)-[:mitigated_by]->(m)
       -> W3 must fall to 0, and no entry may fabricate a mitigation.
  FLAT => the instrument is decoration => the run is VOID, reported as VOID,
  never as "no effect". Every movement is judged AGAINST THE HEALTHY BASELINE:
  a mutant reading of 0 is a movement only if the healthy run was > 0 first,
  otherwise the legs were dead all along (the pre-#3990 product) and a
  ``why_rate 0.0`` would be published as a real zero.

STRUCTURAL-NULL GUARD (#3990's own warning): the run REFUSES to report a rate
when the seeded graph has no operators — a rate over an empty operator set is
the same failure the C2 entity arm hit with Object = 0, and it would read as a
legitimate 0.

Usage:
  uv run python tools/why_layer_rate.py [--json]
"""
from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import would raise ImportError before a later guard could run
# (D9 shape; tests/test_entry_point_python_guard.py pins this position).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/why_layer_rate.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/why_layer_rate.py`"
    )

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

EVAL_REL = os.path.join("tests", "fixtures", "why_layer_eval.json")
EVAL_SHA256 = "288d4908e9ede9f8c794ad690e1d983e2544eba0375fef652a25fb6df2821123"
EVAL_N = 8
EVAL_MITIGATED = 4
EVAL_LABELLED = 4

EXIT_OK = 0
EXIT_VOID = 3
EXIT_FIXTURE = 4


# ── the fixed eval set ─────────────────────────────────────────────────────
def _tree_pin() -> str:
    """The tree the numbers came from.

    A receipt that records only the EVAL sha cannot be compared with another
    receipt: the eval set can be identical while the product that produced the
    rate is not (the sibling instrument pins its tree for the same reason —
    ``tools/ask_shape_rate.py --pin-sha``).

    ⛔ ``HEAD`` alone is a lie on a dirty worktree — a pre-merge run records the
    BASE sha, whose ``tortoise/why.py`` has no operator leg, so the receipt names
    a tree that did NOT produce the numbers. The dirty marker (and the count)
    says so explicitly instead.
    """
    try:
        head = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "status", "--porcelain"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        if not head:
            return "unknown"
        if not status:
            return head
        return f"{head}-dirty({len(status.splitlines())} file(s))"
    except Exception:
        return "unknown"


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def load_eval(path: str | None = None) -> list[dict]:
    """Load the COMMITTED eval set and assert its sha + shape.

    There is no override: a substituted eval set is a different instrument.
    """
    full = path or os.path.join(_REPO_ROOT, EVAL_REL)
    actual = _sha256_file(full)
    if actual != EVAL_SHA256:
        print(f"why_layer_rate: EVAL SHA MISMATCH — {full}\n  expected "
              f"{EVAL_SHA256}\n  actual   {actual}\n"
              "A changed eval set is a NEW instrument, not a moved rate. "
              "ABORT.", file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    with open(full) as f:
        data = json.load(f)
    questions = data["questions"] if isinstance(data, dict) else data
    if len(questions) != EVAL_N:
        print(f"why_layer_rate: EVAL SHAPE MISMATCH — expected {EVAL_N} "
              f"questions, got {len(questions)}. ABORT.", file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    mitigated = sum(1 for q in questions if q.get("mitigation"))
    if mitigated != EVAL_MITIGATED:
        print(f"why_layer_rate: EVAL SHAPE MISMATCH — expected "
              f"{EVAL_MITIGATED} mitigated questions, got {mitigated}. ABORT.",
              file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    labelled = sum(1 for q in questions if q.get("operator_label"))
    if labelled != EVAL_LABELLED:
        print(f"why_layer_rate: EVAL SHAPE MISMATCH — expected "
              f"{EVAL_LABELLED} labelled questions, got {labelled}. ABORT.",
              file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    # DECORRELATION: if "labelled" and "mitigated" were the same rows, a W1
    # failure and a W3 failure could never be attributed to the right
    # dimension — the set would move as one bit. Require at least one row of
    # each pure kind.
    label_only = sum(1 for q in questions if q.get("operator_label")
                     and not q.get("mitigation"))
    mitigation_only = sum(1 for q in questions if q.get("mitigation")
                          and not q.get("operator_label"))
    if not label_only or not mitigation_only:
        print("why_layer_rate: EVAL SHAPE MISMATCH — the operator_label and "
              f"mitigation dimensions are correlated (label_only={label_only}, "
              f"mitigation_only={mitigation_only}); a W1 and a W3 failure would "
              "be indistinguishable. ABORT.", file=sys.stderr)
        raise SystemExit(EXIT_FIXTURE)
    for q in questions:
        for key in ("question_id", "question", "token", "claim", "support",
                    "operator_type"):
            if not q.get(key):
                print(f"why_layer_rate: EVAL ROW MISSING {key!r} — {q!r}. "
                      "ABORT.", file=sys.stderr)
                raise SystemExit(EXIT_FIXTURE)
    return questions


def _fresh_sdk():
    """A private, throwaway embedded graph — no shared state, no docker."""
    from tortoise.sdk import TortoiseSDK

    db_path = os.path.join(tempfile.mkdtemp(prefix="why_layer_rate_"), "g.db")
    sdk = TortoiseSDK(db_path)
    try:  # noqa: SIM105
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


def seed(sdk, questions: list[dict]) -> dict[str, dict]:
    """Seed the eval set through the PUBLIC write path.

    The gold operator id is the id ``create_operator`` RETURNS — identity, not
    a text match — so W2 compares ids the product itself minted.
    """
    planted: dict[str, dict] = {}
    for q in questions:
        claim = sdk.create_point("statement", q["claim"])
        support = sdk.create_point("evidence", q["support"])
        op = sdk.create_operator(
            q["operator_type"], support["id"], [claim["id"]],
            label=q.get("operator_label"),
        )
        if q.get("mitigation"):
            sdk.mitigate_operator(op["id"], q["mitigation"])
        planted[q["question_id"]] = {
            "claim": claim["id"],
            "support": support["id"],
            "operator": op["id"],
        }
    return planted


# ── the legs ───────────────────────────────────────────────────────────────
def _support_entry(sdk, claim_id: str, support_id: str, token: str) -> dict | None:
    """Read the claim back through the SHIPPING read path and return the
    support entry that names ``support_id`` (None when the product returned no
    such support — that is the FAIL shape, not an exception)."""
    hits = sdk.tortoise_fts_query(token, limit=25)
    item = next((h for h in hits if h.get("id") == claim_id), None)
    if item is None:
        return None
    why = item.get("why") or {}
    for entry in why.get("support_chain") or []:
        if entry.get("point_id") == support_id:
            return entry
    return None


def measure(sdk, questions: list[dict], planted: dict[str, dict]) -> dict:
    """Score W1/W2/W3 per question. Never raises: an exception is a FAIL."""
    rows: list[dict] = []
    for q in questions:
        qid = q["question_id"]
        p = planted[qid]
        row = {"question_id": qid, "w_id": False, "w1": False, "w2": False,
               "w3": False, "mitigated": bool(q.get("mitigation")),
               "labelled": bool(q.get("operator_label")), "error": None}
        try:
            entry = _support_entry(sdk, p["claim"], p["support"], q["token"])
            if entry is not None:
                named = entry.get("operator_id")
                # W1 is "names the mediating operator (id + KIND)" — so the
                # kind must be named too. Requiring it here is what stops a read
                # path that returns the id but nulls the type from passing.
                row["w_id"] = bool(named)
                # The KIND leg COMPARES against the gold mechanism — a
                # presence-only check would be a false PASS for a read path
                # reporting the wrong mechanism (a wrong column, the claim's own
                # point_kind). LIMIT, stated rather than implied: every eval row's
                # gold mechanism is IMPL (this leg only traverses -[:IMPL]->), so
                # a HARDCODED "IMPL" in the payload is indistinguishable here —
                # the eval set cannot vary the kind it plants.
                row["w1"] = bool(named) and \
                    entry.get("operator_type") == q["operator_type"]
                row["w2"] = named == p["operator"]
                if q.get("operator_label"):
                    # The Layer-3 predicate is part of "names it": a nulled
                    # label is not a named operator. Only asserted where the
                    # gold operator HAS a label, so an unlabelled gold row is
                    # not forced to invent one.
                    row["w1"] = row["w1"] and \
                        entry.get("operator_label") == q["operator_label"]
                got = entry.get("mitigation")
                # `mitigation` MUST be present (present-and-null is the
                # contract): a MISSING key is the unread-dimension shape the
                # contract exists to distinguish, so `got is None` alone would
                # pass on an absent key.
                if "mitigation" not in entry:
                    row["w3"] = False
                elif q.get("mitigation"):
                    row["w3"] = got == q["mitigation"]
                else:
                    row["w3"] = got is None
                row["named_operator"] = named
        except Exception as e:  # a per-question exception is a FAIL
            row["error"] = f"{type(e).__name__}: {e}"
        row["passed"] = row["w1"] and row["w2"] and row["w3"]
        rows.append(row)
    passed = sum(1 for r in rows if r["passed"])
    return {
        "rows": rows,
        "n": len(rows),
        "w_id": sum(1 for r in rows if r["w_id"]),
        "w1": sum(1 for r in rows if r["w1"]),
        "w2": sum(1 for r in rows if r["w2"]),
        "w3": sum(1 for r in rows if r["w3"]),
        # W3 splits by gold-mitigation, because M5 can only move the MITIGATED
        # subset: a run where every unmitigated question still reports
        # ``mitigation: None`` is the PASS shape for that half, so scoring the
        # whole denominator would call a correctly-firing M5 "FLAT".
        # W1 splits by gold-LABEL for the same reason W3 splits by gold
        # mitigation: M6 can only move the labelled subset, so scoring the whole
        # column would call a correctly-firing M6 "FLAT".
        "w1_labelled": sum(1 for r in rows if r["labelled"] and r["w1"]),
        "n_labelled": sum(1 for r in rows if r["labelled"]),
        "w3_mitigated": sum(1 for r in rows if r["mitigated"] and r["w3"]),
        "w3_unmitigated": sum(1 for r in rows if not r["mitigated"] and r["w3"]),
        "n_mitigated": sum(1 for r in rows if r["mitigated"]),
        "passed": passed,
        "rate": (passed / len(rows)) if rows else 0.0,
    }


# ── movement control ───────────────────────────────────────────────────────
def mutate_operator(sdk, questions: list[dict], planted: dict[str, dict]) -> None:
    """M4 — delete the operator bridge so the mediation cannot be read."""
    for q in questions:
        p = planted[q["question_id"]]
        sdk._get_proj().g.query(
            "MATCH (sup:Point {id:$s})-[ri:INPUT]->(op:Point {is_operator:true})"
            "-[i:IMPL]->(n:Point {id:$n}) DELETE i, ri",
            params={"s": p["support"], "n": p["claim"]},
        )


def mutate_label(sdk, questions: list[dict], planted: dict[str, dict]) -> None:
    """M6 — strip the Layer-3 predicate off every labelled gold operator.

    W1 folds the label in ("names the mediating operator"), so the label leg
    needs its own movement: M4 deletes the operator entirely and therefore
    cannot isolate it.
    """
    for q in questions:
        if not q.get("operator_label"):
            continue
        sdk._get_proj().g.query(
            "MATCH (op:Point {id:$o}) REMOVE op.label",
            params={"o": planted[q["question_id"]]["operator"]},
        )


def mutate_mitigation(sdk, questions: list[dict],
                      planted: dict[str, dict]) -> None:
    """M5 — delete the mitigation edges so no mitigation can be surfaced."""
    for q in questions:
        if not q.get("mitigation"):
            continue
        sdk._get_proj().g.query(
            "MATCH (op:Point {id:$o})-[m:mitigated_by]->(:Point) DELETE m",
            params={"o": planted[q["question_id"]]["operator"]},
        )


def structural_null(planted: dict[str, dict]) -> str:
    """A rate over an EMPTY operator set is a structural null, not a 0.

    TWO checks, because they catch different states:
      (a) PLANTING — an eval row whose operator id came back empty. Cheap, but
          in practice it fires only if the write path changes shape;
      (b) OBSERVABILITY (``observed_null`` below) — the state that actually
          matters: operators were MINTED and the read path names none of them.
          That is the pre-#3990 product verbatim, and reporting it as
          ``why_rate 0.0`` is the C2 Object=0 mistake (#3990's own warning).
    """
    missing = [qid for qid, p in planted.items() if not p.get("operator")]
    if missing:
        return ("the seeded graph has no operator for "
                f"{len(missing)} question(s) ({', '.join(sorted(missing))}) — "
                "a rate over an empty operator set is a structural null "
                "(#3990), not a zero")
    return ""


def observed_null(healthy: dict) -> str:
    """NO question's entry carried an operator ID ⇒ the operator set is empty.

    This is the guard that can actually fire (``structural_null``'s planting
    check cannot, once the writes succeeded): an empty OBSERVED operator set
    measures nothing, so the run is refused instead of publishing a zero.

    ⛔ Keyed on the ID leg (``w_id``), NOT on W1. W1 folds in the operator's
    KIND and Layer-3 label, so a payload that names every operator correctly
    but nulls its kind has ``w1 == 0`` with ``w_id == N`` — reporting THAT as
    "the read path named NO operator" would be factually wrong about the state
    and would send the reader to the eval set instead of the payload field.
    It is also the case where ``0/N`` is the honest rate: the ids were read,
    the reason was not. The empty-set refusal is for a graph with no readable
    operators (the pre-#3990 product), which is what the issue's "never run it
    against an empty operator set and report that as a rate" means.
    """
    if healthy.get("n") and healthy.get("w_id", 0) == 0:
        return ("the read path returned NO operator id for any of the "
                f"{healthy['n']} questions — an empty operator set is a "
                "structural null (#3990), not a rate of 0")
    return ""


def run(json_out: bool = False) -> int:
    """Healthy run + both movements. Returns the process exit code."""
    # The why block only exists on the W4-enriched read path, so this
    # instrument's whole scope is the flag-ON shape. The flag is defaulted ON
    # here (a runnable instrument that measures nothing by default is a trap)
    # and its PROVENANCE is recorded in the receipt, so a reader can tell an
    # ambient flag from one this instrument set.
    ambient_flag = os.environ.get("TORTOISE_W4_ENRICHMENT")
    os.environ.setdefault("TORTOISE_W4_ENRICHMENT", "1")
    from tortoise.why import w4_enrichment_enabled

    if not w4_enrichment_enabled():
        print("why_layer_rate: TORTOISE_W4_ENRICHMENT is OFF — the why block is "
              "not emitted at all, so nothing here is measurable. VOID.",
              file=sys.stderr)
        return EXIT_VOID
    flag_provenance = (f"ambient={ambient_flag!r}" if ambient_flag is not None
                       else "set by this instrument (unset in the environment)")

    questions = load_eval()

    sdk = _fresh_sdk()
    planted = seed(sdk, questions)
    null = structural_null(planted)
    if null:
        print(f"why_layer_rate: VOID — {null}", file=sys.stderr)
        return EXIT_VOID
    healthy = measure(sdk, questions, planted)
    null = observed_null(healthy)
    if null:
        print(f"why_layer_rate: VOID — {null}", file=sys.stderr)
        return EXIT_VOID

    sdk4 = _fresh_sdk()
    p4 = seed(sdk4, questions)
    mutate_operator(sdk4, questions, p4)
    m4 = measure(sdk4, questions, p4)

    sdk5 = _fresh_sdk()
    p5 = seed(sdk5, questions)
    mutate_mitigation(sdk5, questions, p5)
    m5 = measure(sdk5, questions, p5)

    sdk6 = _fresh_sdk()
    p6 = seed(sdk6, questions)
    mutate_label(sdk6, questions, p6)
    m6 = measure(sdk6, questions, p6)

    # A MUTANT READING OF 0 IS NOT A MOVEMENT. "Each leg must be able to RED"
    # means the leg was GREEN and then went red — so every movement is judged
    # against the HEALTHY baseline. Without this, an instrument whose legs were
    # ALWAYS 0 (the exact pre-#3990 product: no operator in the payload at all)
    # reports M4/M5 as MOVED and publishes `why_rate 0.0000` as a RATE, which is
    # the "reads as a real zero" trap this file's docstring forbids.
    m4_moved = (healthy["w1"] > 0 and healthy["w2"] > 0
                and m4["w1"] == 0 and m4["w2"] == 0)
    # M5 can only move the MITIGATED subset — the unmitigated half must KEEP
    # reporting ``mitigation: None`` (no fabrication) and so is not expected to
    # fall. Requiring the whole W3 column to hit 0 would make M5 un-fireable.
    healthy_w3 = (healthy["n_mitigated"] > 0
                  and healthy["w3_mitigated"] == healthy["n_mitigated"])
    m5_moved = (healthy_w3 and m5["w3_mitigated"] == 0
                and m5["w3_unmitigated"] == m5["n"] - m5["n_mitigated"])
    # M6: the Layer-3 label is part of W1, so its leg needs its own movement —
    # scored on the labelled subset (an unlabelled gold row must NOT be expected
    # to fall).
    healthy_w1_label = (healthy["n_labelled"] > 0
                        and healthy["w1_labelled"] == healthy["n_labelled"])
    m6_moved = (healthy_w1_label
                and m6["w1_labelled"] == 0
                and m6["w1"] == healthy["n"] - healthy["n_labelled"])
    verdict = "MOVED" if (m4_moved and m5_moved and m6_moved) else "FLAT"
    # WHY a movement did not fire matters: a leg that is ALREADY red on the
    # healthy run is a PAYLOAD result (the shipped read path lost that leg),
    # not proof that the instrument is decoration. Collapsing the two sends the
    # reader to the wrong artifact, and it contradicts observed_null's own
    # statement that "id named, kind missing" is an honest 0/N rate.
    baseline_red = []
    if healthy["w1"] == 0 or healthy["w2"] == 0:
        baseline_red.append(
            f"W1/W2 operator {healthy['w1']}/{healthy['n']}")
    if not healthy_w3:
        baseline_red.append(
            f"W3 mitigation {healthy['w3_mitigated']}/{healthy['n_mitigated']}")
    if not healthy_w1_label:
        # Name the RATIO: this leg's trigger is "not fully green", not "zero",
        # so a partly-green baseline (2/4) must not be reported as 0.
        baseline_red.append(
            f"W1 Layer-3 label {healthy['w1_labelled']}/{healthy['n_labelled']}")

    receipt = {
        "instrument": "why_layer_rate",
        "tree": _tree_pin(),
        "w4_enrichment": flag_provenance,
        "eval": {"path": EVAL_REL, "sha256": EVAL_SHA256, "n": EVAL_N,
                 "mitigated": EVAL_MITIGATED},
        "healthy": healthy,
        "baseline_for_movement": {
            "n": healthy["n"],
            "w_id": healthy["w_id"], "w1": healthy["w1"], "w2": healthy["w2"],
            "w1_labelled": healthy["w1_labelled"],
            "n_labelled": healthy["n_labelled"],
            "w3_mitigated": healthy["w3_mitigated"],
            "n_mitigated": healthy["n_mitigated"],
        },
        "m4_delete_operator_bridge": {
            "w1": m4["w1"], "w2": m4["w2"], "w3": m4["w3"], "rate": m4["rate"],
            "moved": m4_moved,
        },
        "m6_strip_layer3_label": {
            "w1": m6["w1"], "w1_labelled": m6["w1_labelled"],
            "w1_unlabelled": m6["w1"] - m6["w1_labelled"],
            "rate": m6["rate"], "moved": m6_moved,
        },
        "m5_delete_mitigation": {
            "w1": m5["w1"], "w2": m5["w2"], "w3": m5["w3"],
            "w3_mitigated": m5["w3_mitigated"],
            "w3_unmitigated": m5["w3_unmitigated"],
            "rate": m5["rate"], "moved": m5_moved,
        },
        "movement": verdict,
        "baseline_legs_already_red": baseline_red,
        "verdict": "RATE" if verdict == "MOVED" else "VOID",
    }

    if json_out:
        print(json.dumps(receipt, indent=2))
    else:
        print(f"why_layer_rate — eval {EVAL_REL} @ {EVAL_SHA256[:12]} "
              f"(n={EVAL_N}, mitigated={EVAL_MITIGATED})")
        print(f"  W0 operator id      : {healthy['w_id']}/{healthy['n']}")
        print(f"  W1 operator present : {healthy['w1']}/{healthy['n']} "
              f"(id + kind + label-where-gold-has-one)")
        print(f"  W2 operator correct : {healthy['w2']}/{healthy['n']}")
        print(f"  W3 mitigation       : {healthy['w3']}/{healthy['n']}")
        print(f"  why_rate            : {healthy['rate']:.4f} "
              f"({healthy['passed']}/{healthy['n']})")
        for r in healthy["rows"]:
            if not r["passed"]:
                print(f"    FAIL {r['question_id']}: "
                      f"w1={r['w1']} w2={r['w2']} w3={r['w3']} "
                      f"named={r.get('named_operator')!r} err={r['error']}")
        print(f"  movement M4 (operator bridge deleted) -> W1 {m4['w1']}, "
              f"W2 {m4['w2']} [{'MOVED' if m4_moved else 'FLAT'}]")
        print(f"  movement M6 (label stripped)          -> W1 labelled "
              f"{m6['w1_labelled']}/{healthy['n_labelled']}, unlabelled "
              f"{m6['w1'] - m6['w1_labelled']}/"
              f"{healthy['n'] - healthy['n_labelled']} "
              f"[{'MOVED' if m6_moved else 'FLAT'}]")
        print(f"  movement M5 (mitigation deleted)      -> W3 mitigated "
              f"{m5['w3_mitigated']}/{m5['n_mitigated']}, unmitigated "
              f"{m5['w3_unmitigated']}/{m5['n'] - m5['n_mitigated']} "
              f"[{'MOVED' if m5_moved else 'FLAT'}]")
        if verdict == "MOVED":
            print(f"  VERDICT: {receipt['verdict']}")
        elif baseline_red:
            print("  VERDICT: VOID — FLAT, but the baseline was already RED "
                  f"for {', '.join(baseline_red)}: that is a PAYLOAD result (the "
                  "shipped read path lost the leg), NOT proof the instrument is "
                  "decoration — the movement cannot be observed. Fix the leg "
                  "above first, then re-run for a certifiable movement.")
        else:
            print("  VERDICT: VOID — FLAT: the instrument is decoration, so "
                  "this run is VOID, not a low rate")

    return EXIT_OK if verdict == "MOVED" else EXIT_VOID


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="#3990 — the why-layer rate instrument (W1/W2/W3)")
    ap.add_argument("--json", action="store_true", help="machine-readable receipt")
    args = ap.parse_args(argv)
    return run(json_out=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
