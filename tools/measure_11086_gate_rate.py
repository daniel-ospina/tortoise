"""`#11086` — the measured write-rate effect of the two gates, **per gate**.

**What this measures, and what it deliberately does not.** `STORAGE-ARCHITECTURE.md`
§12.2a decides that ONE rule set (`value_gate.RULES`) is enforced at TWO points,
each asking its own question:

===================================  ==========================================  =========================
gate                                 question                                    counted here as
===================================  ==========================================  =========================
``TORTOISE_EMBED_GATE``              does the **written row** carry a vector?    ``encode_for_row`` returns a vector
``TORTOISE_VALUE_GATE``              does the row **exist** at all?              candidate survives the S2.2b pass
===================================  ==========================================  =========================

⛔ **They are reported SEPARATELY and never summed.** A combined number would
answer a question neither gate asks, and it is exactly the collapse §12.2a's
"two questions, one rule set" split exists to prevent.

**The ordering this measurement is taken in — post-arming, and said out loud.**
`#5008` chose *"the rate effect is measurable before it is trusted"*. PR #11445
armed `TORTOISE_EMBED_GATE` in `.env.example` **before** that measurement was
taken, so the graph was already no longer unchanged when this ran. This tool
therefore measures **after** the change. That is worse than the ordered
measurement and it is what is still possible; the record says so.

**Deterministic by construction.** The embedder is stubbed with a
content-addressed vector, so the tool makes **zero model calls** and its output
is byte-stable — which is what lets a test re-run it and compare against the
committed record.

**The corpus carries both directions.** Identifier-only rows (a UUID, a commit
hash, a file path, an `#<n>` reference) are the class the gates are meant to
drop. Prose rows — including the two **semantic** members of the declared
ephemeral class, `"tool workarounds"` and `"sprint mechanics"` — are the class
they must keep (§12.2a: the retention declaration is not a meaning predicate, so
a workaround earns a vector). A corpus of only identifiers would make any gate
look effective; a corpus of only prose would make any gate look safe.

Run (both commands are the record; see `docs/evidence/11086/README.md`)::

    uv run python tools/measure_11086_gate_rate.py --out docs/evidence/11086/gate-rate.json
    uv run python tools/measure_11086_gate_rate.py            # human table

**Exit codes:** ``0`` the measurement ran (whatever it found, including a null
delta — a stated null is a result); ``2`` the instrument could not measure
(import failure, or an arm that produced no population at all). A refusal
writes **no** artifact, so a file left at ``--out`` after a refusal is the
previous run's.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The corpus. ``classes`` is what this row is HERE FOR — the arm that fires and
#: the arm that must not, in one list, so a gate that drops everything and a gate
#: that drops nothing both show up as a wrong number rather than as a clean one.
CORPUS: tuple[dict, str] = (
    # ── the identifier-only class: the gates are meant to drop these ─────────
    {"section": "entities", "text": "7f3a9c2e-1b4d-4a6f-9c81-2de5b7a04f19",
     "why": "a bare UUID"},
    {"section": "events", "text": "a41f0c9d8e2b7f6a5c4d3e2f1a0b9c8d7e6f5a4b",
     "why": "a bare commit hash"},
    {"section": "points", "text": "tortoise/projection/entities.py",
     "why": "a bare file path"},
    {"section": "points", "text": "#11086", "why": "a bare issue reference"},
    # ── prose: the gates must KEEP these ────────────────────────────────────
    {"section": "points",
     "text": "The owner ruled that the rate effect is measurable before it is "
             "trusted, so the gate ships behind a flag until the measurement lands.",
     "why": "a decision turned on a claim"},
    {"section": "points",
     "text": "The tool workaround is to pin the checkout to the recorded sha, "
             "because the floating tag resolves differently on a cold cache.",
     "why": "an ephemeral but SEMANTIC member of the declared class"},
    {"section": "points",
     "text": "Sprint mechanics: the cycle-4 review moved the fix from lane A to "
             "lane B after the metrics showed the first attempt stalled.",
     "why": "an ephemeral but SEMANTIC member of the declared class"},
    {"section": "entities", "text": "the admin-merge rail",
     "why": "a bare definite description (NOT identifier-only — VET's question)"},
    {"section": "events",
     "text": "PR #11086 landed the step-list home for the EMBED decision on "
             "2026-11-09, after the arming had already gone out.",
     "why": "an identifier merely PRESENT inside a claim"},
)

_EMBED_FLAG = "TORTOISE_EMBED_GATE"
_VALUE_FLAG = "TORTOISE_VALUE_GATE"


def _stub_vector(text: str, dim: int = 8) -> list[float]:
    """A content-addressed vector — no model, no network, byte-stable."""
    digest = hashlib.sha256(str(text).encode("utf-8")).digest()
    return [b / 255.0 for b in digest[:dim]]


class _Arms:
    """Context manager: pin an env var for one arm and restore it exactly.

    Restores the PREVIOUS value (including "was unset"), because a measurement
    that leaves the process env changed would make the second arm depend on the
    first.
    """

    def __init__(self, name: str, value: str | None):
        self.name = name
        self.value = value
        self._prior: str | None = None
        self._had = False

    def __enter__(self):
        self._had = self.name in os.environ
        self._prior = os.environ.get(self.name)
        if self.value is None:
            os.environ.pop(self.name, None)
        else:
            os.environ[self.name] = self.value
        return self

    def __exit__(self, *exc):
        if self._had and self._prior is not None:
            os.environ[self.name] = self._prior
        else:
            os.environ.pop(self.name, None)
        return False


def _embed_arm(armed: bool) -> dict:
    """Rows written through ``encode_for_row`` that carry a vector.

    ⚠️ **It records the SAME two witnesses the VALUE arm does** — whether the
    predicate was *enabled at the enforcement point*, and how many times it
    *fired* — instead of leaving `NO-EMBED` to be inferred from `vec is None`.
    The count is taken from the seam's own decision function
    (``embed_gate.embedding_verdict``), which is the call ``encode_for_row``
    makes, so "the gate ran and refused 4 rows" is read here rather than
    reconstructed from arithmetic on the result.
    """
    from tortoise import embed_gate as _gate
    from tortoise import embeddings as _emb

    # The seam is a module global on purpose (`encode_for_row` -> `encode_for_store`
    # -> `compute_embedding`), so an installed double still intercepts. Patch both
    # the single and the batched form for symmetry; only the single is used here.
    prior_single, prior_batch = _emb.compute_embedding, _emb.compute_embeddings
    _emb.compute_embedding = lambda text, max_tokens=512: _stub_vector(text)  # type: ignore[assignment]
    _emb.compute_embeddings = (  # type: ignore[assignment]
        lambda texts, max_tokens=512: [_stub_vector(t) for t in texts])
    prior_verdict = _gate.embedding_verdict
    fired = {"calls": 0, "refusals": 0}

    def counting_verdict(content):
        """The seam's own decision, counted — a witness, not a re-derivation."""
        fired["calls"] += 1
        verdict = prior_verdict(content)
        if verdict is not None:
            fired["refusals"] += 1
        return verdict

    _gate.embedding_verdict = counting_verdict  # type: ignore[assignment]
    try:
        with _Arms(_EMBED_FLAG, "1" if armed else "0"):
            # Read the SAME predicate `encode_for_row` reads, while this arm's
            # flag value is in the environment: "was it armed here" is measured
            # at the enforcement point, not assumed from the flag argument.
            enabled = _gate.embed_gate_enabled()
            rows = []
            for row in CORPUS:
                vec = _emb.encode_for_row(row["text"], None)
                rows.append({"text": row["text"], "why": row["why"],
                             "section": row["section"],
                             "carries_vector": vec is not None})
    finally:
        _emb.compute_embedding, _emb.compute_embeddings = prior_single, prior_batch
        _gate.embedding_verdict = prior_verdict  # type: ignore[assignment]
    return {
        "rows": rows,
        "rows_written": len(rows),
        "rows_carrying_a_vector": sum(1 for r in rows if r["carries_vector"]),
        "rows_without_a_vector": sum(1 for r in rows if not r["carries_vector"]),
        # The arming witness, symmetric with the VALUE arm's pair below. A
        # "never armed" arm records `false`/`0`, which cannot be read as "the
        # gate let everything through".
        "predicate_enabled_at_the_enforcement_point": bool(enabled),
        "predicate_fired": fired["refusals"],
        "predicate_calls": fired["calls"],
        # …and the rows it suppressed, named, so "the gate refused 4" is a list of
        # texts rather than an inference from `vec is None`.
        "suppressed_texts": [r["text"] for r in rows if not r["carries_vector"]],
    }


def _value_arm(armed: bool) -> dict:
    """Candidates that still EXIST after the S2.2b pass.

    Measured at the enforcement point itself — ``vet_gate.vet_candidates`` +
    ``apply_vet`` — which is what ``extractor_v2._run_vet_pass`` calls and what
    removes a `DISCARD`ed candidate from the list every later step sees. With the
    flag OFF the pass produced no discards at all, which is the same population as
    "the pass never ran": the caller only arms it when
    ``vet_enabled`` is true, and with no arbiter and no ``TORTOISE_VET`` that is
    the VALUE flag alone.
    """
    from tortoise import vet_gate

    embed_list: dict = {"entities": [], "events": [], "points": []}
    for row in CORPUS:
        item = ({"name": row["text"]} if row["section"] == "entities"
                else {"content": row["text"]})
        embed_list[row["section"]].append(item)

    with _Arms(_VALUE_FLAG, "1" if armed else "0"):
        out = vet_gate.vet_candidates(embed_list, narrative="", arbiter=None)
    kept_list, _warnings = vet_gate.apply_vet(embed_list, out["decisions"])

    kept_texts = set()
    for section in ("entities", "events", "points"):
        for item in kept_list.get(section) or []:
            kept_texts.add(str(item.get("name") or item.get("content") or ""))

    rows = []
    for row in CORPUS:
        rows.append({"text": row["text"], "why": row["why"],
                     "section": row["section"],
                     "still_exists": row["text"] in kept_texts})
    mechanical = (out.get("stats") or {}).get("mechanical") or {}
    #: The population the gate ACTUALLY judged — one decision per candidate whose
    #: text it could read. This is the number the refusals below compare, and it is
    #: deliberately NOT `len(CORPUS)`: a comparison of the module constant with
    #: itself can never fire, so it would advertise a refusal the tool cannot reach
    #: (measured against a broken `_item_text`: `decisions == {}`, every count zero,
    #: and a plausible-looking "this gate does nothing" record instead of an error).
    judged = len(out.get("decisions") or {})
    return {
        "rows": rows,
        "candidates": len(rows),
        "candidates_judged": judged,
        "candidates_still_existing": sum(1 for r in rows if r["still_exists"]),
        "candidates_removed": sum(1 for r in rows if not r["still_exists"]),
        "predicate_enabled_at_the_enforcement_point": bool(mechanical.get("enabled")),
        "predicate_fired": int(mechanical.get("fired") or 0),
    }


def measure() -> dict:
    """The whole record. Pure: no DB, no network, no model."""
    from tortoise import embed_gate, value_gate

    on_embed = _embed_arm(armed=True)
    off_embed = _embed_arm(armed=False)
    on_value = _value_arm(armed=True)
    off_value = _value_arm(armed=False)

    # ── the refusals ────────────────────────────────────────────────────────
    # Each one is REACHABLE: it compares a population an arm OBSERVED, never a
    # module constant with itself. A guard that cannot fire advertises an exit code
    # the tool cannot produce, which is the no-op-gate shape this repo's own rules
    # call category A.
    if off_embed["rows_carrying_a_vector"] == 0:
        raise SystemExit(
            "the unobstructed EMBED arm produced no vector for ANY row — the "
            "embedder double or the width path is broken, so a zero in the gated "
            "arm would be indistinguishable from a dead instrument")
    if on_embed["rows_carrying_a_vector"] > off_embed["rows_carrying_a_vector"]:
        raise SystemExit(
            "the gated EMBED arm carries MORE vectors than the ungated one — the "
            "gate cannot add, so the two arms are not measuring the same seam")
    if off_value["candidates_judged"] != off_embed["rows_written"]:
        raise SystemExit(
            f"the VALUE pass judged {off_value['candidates_judged']} of "
            f"{off_embed['rows_written']} corpus rows — candidate text extraction "
            "is dropping rows, so its delta would be measured on a different "
            "population than the EMBED arm's")
    if on_value["candidates_judged"] != off_value["candidates_judged"]:
        raise SystemExit(
            "the two VALUE arms judged different populations — the instrument is "
            "broken, not the gate")
    if (off_value["candidates_still_existing"]
            != off_value["candidates_judged"]):
        raise SystemExit(
            "the ungated VALUE arm removed something, so it is not the "
            "counterfactual this measurement needs")

    return {
        "issue": 11086,
        "ordering": "POST-ARMING",
        "ordering_note": (
            "#5008 asked for the rate effect to be measurable BEFORE the gate was "
            "trusted. PR #11445 armed TORTOISE_EMBED_GATE in .env.example before "
            "that measurement was taken, so this record is after the change, not "
            "before it. Stated rather than implied."
        ),
        "shared_rule_set": {
            "embed_gate.RULE_SET is value_gate.RULES":
                embed_gate.RULE_SET is value_gate.RULES,
            "rules": list(value_gate.RULES),
        },
        "embed_gate": {
            "flag": embed_gate.ENV_FLAG,
            "question": "does the written row carry a vector?",
            "enforcement_point": "tortoise/embeddings.py::encode_for_row",
            "gate_on": on_embed,
            "gate_off": off_embed,
            "delta_rows_carrying_a_vector":
                on_embed["rows_carrying_a_vector"] - off_embed["rows_carrying_a_vector"],
        },
        "value_gate": {
            "flag": value_gate.ENV_FLAG,
            "question": "does the row exist at all?",
            "enforcement_point": "tortoise/vet_gate.py::vet_candidates + apply_vet",
            "gate_on": on_value,
            "gate_off": off_value,
            "delta_candidates_still_existing":
                on_value["candidates_still_existing"]
                - off_value["candidates_still_existing"],
        },
        "audit": {
            "corpus_rows": len(CORPUS),
            "model_calls": 0,
            "network_calls": 0,
            "deterministic": True,
            "embedder": "content-addressed stub (sha256 prefix), no model",
        },
    }


def _table(record: dict) -> str:
    lines: list[str] = []
    lines.append(f"#11086 gate-rate measurement — ordering: {record['ordering']}")
    lines.append("")
    lines.append("Two gates, ONE rule set, TWO different questions. Not summed.")
    lines.append("")
    eg = record["embed_gate"]
    lines.append(f"## {eg['flag']} — {eg['question']}")
    lines.append(f"   enforcement point: {eg['enforcement_point']}")
    # ⛔ The first column is the ROW IDENTIFIER (`#1`, `#2`, …). It used to
    # repeat the gate-ON answer, so a rendered row read `False False True` and
    # the label column carried no label.
    lines.append(f"   {'#':<4} {'gate ON':>8} {'gate OFF':>9}   row")
    for i, (on, off) in enumerate(zip(eg["gate_on"]["rows"],
                                      eg["gate_off"]["rows"], strict=True), 1):
        lines.append(f"   {f'#{i}':<4} "
                     f"{on['carries_vector']!s:>8} "
                     f"{off['carries_vector']!s:>9}   "
                     f"{on['text'][:58]!r}  [{on['why']}]")
    lines.append(f"   rows carrying a vector: ON "
                 f"{eg['gate_on']['rows_carrying_a_vector']} / "
                 f"{eg['gate_on']['rows_written']}  ·  OFF "
                 f"{eg['gate_off']['rows_carrying_a_vector']} / "
                 f"{eg['gate_off']['rows_written']}  ·  "
                 f"delta {eg['delta_rows_carrying_a_vector']}")
    lines.append(f"   witness: ON armed="
                 f"{eg['gate_on']['predicate_enabled_at_the_enforcement_point']}"
                 f" fired={eg['gate_on']['predicate_fired']}"
                 f"/{eg['gate_on']['predicate_calls']}  ·  OFF armed="
                 f"{eg['gate_off']['predicate_enabled_at_the_enforcement_point']}"
                 f" fired={eg['gate_off']['predicate_fired']}"
                 f"/{eg['gate_off']['predicate_calls']}")
    lines.append("")
    vg = record["value_gate"]
    lines.append(f"## {vg['flag']} — {vg['question']}")
    lines.append(f"   enforcement point: {vg['enforcement_point']}")
    lines.append(f"   {'#':<4} {'gate ON':>8} {'gate OFF':>9}   row")
    for i, (on, off) in enumerate(zip(vg["gate_on"]["rows"],
                                      vg["gate_off"]["rows"], strict=True), 1):
        lines.append(f"   {f'#{i}':<4} "
                     f"{on['still_exists']!s:>8} "
                     f"{off['still_exists']!s:>9}   "
                     f"{on['text'][:58]!r}  [{on['why']}]")
    lines.append(f"   witness: ON enabled="
                 f"{vg['gate_on']['predicate_enabled_at_the_enforcement_point']}"
                 f" fired={vg['gate_on']['predicate_fired']}  ·  OFF enabled="
                 f"{vg['gate_off']['predicate_enabled_at_the_enforcement_point']}"
                 f" fired={vg['gate_off']['predicate_fired']}")
    lines.append(f"   candidates still existing: ON "
                 f"{vg['gate_on']['candidates_still_existing']} / "
                 f"{vg['gate_on']['candidates']}  ·  OFF "
                 f"{vg['gate_off']['candidates_still_existing']} / "
                 f"{vg['gate_off']['candidates']}  ·  "
                 f"delta {vg['delta_candidates_still_existing']}")
    lines.append("")
    prov = record.get("provenance") or {}
    if prov:
        lines.append(f"measured at {prov.get('head_sha')} on {prov.get('measured_at')}")
        lines.append("")
    return "\n".join(lines)


def _provenance() -> dict:
    """The tree this record was taken on — so a quoted "5/9, −4" binds to a revision.

    ⛔ **Computed only on the WRITE path, never inside :func:`measure`.** The
    measurement stays a pure function so `tests/test_embed_decision_step_11086.py`
    can re-run it and compare against the committed numbers; a head sha or a
    timestamp inside `measure()` would make the record unreproducible by
    construction and turn the reproduction pin into a snapshot of itself. The
    test therefore compares everything EXCEPT this block, and asserts the block
    is present and well-formed here.
    """
    head = ""
    try:
        head = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        head = ""
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        # No invented hash: an unresolvable tree is recorded AS unresolved, the
        # same way an unmeasured door is never reported as zero.
        head = "unresolved (not a git checkout, or git unavailable)"
    return {
        "head_sha": head,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "instrument": "tools/measure_11086_gate_rate.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure #11086's two gates SEPARATELY, post-arming.")
    parser.add_argument("--out", type=Path, default=None,
                        help="write the JSON record here (no artifact on a refusal)")
    args = parser.parse_args(argv)

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        record = measure()
    except SystemExit as exc:  # the refusal path: no artifact written
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"REFUSED — instrument failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    # Provenance is added HERE, not in `measure()`, so the numbers stay a pure,
    # reproducible function of the tree (see `_provenance`).
    record["provenance"] = _provenance()

    print(_table(record))
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(record, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"record written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
