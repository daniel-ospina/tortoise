"""#11739 — WHY the gate failed: quote IDENTITY across distinct facts.

This is the second artifact of the #11739 gate run. ``composed_gate.py`` produces the three gate
verdicts; this probe produces the ROOT-CAUSE counts quoted in the bundle README, so every number
on the issue has an artifact and a command behind it rather than a hand-run.

What it measures, all from the same cached live-graph rows and the REAL composer:

* how many distinct composed texts the live quoted population composes to (names arm empty — the
  live graph carries no neighbour material, see ``composed_gate.py``), and therefore how many
  Points share a composed text with another Point;
* for every pair inside a shared-composed-text group: whether their ``content`` is the same, and
  their RAW cosine (the graph's stored vectors) — i.e. exactly the pairs the composition turns into
  a cosine-1.000 "duplicate" that raw space did not see;
* the largest groups, and the count of pre-existing raw exact-duplicate pairs (cos >= 0.99999),
  so the README does not attribute a pre-existing raw duplicate to the composition.

Usage
-----
    uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
        --rows /tmp/11739-rows.jsonl \
        --out docs/evidence/11739-composed-gate/quote_identity.json

    # rebind ONLY the provenance of an existing artifact (no row cache needed):
    uv run python docs/evidence/11739-composed-gate/probes/quote_identity.py \
        --attest docs/evidence/11739-composed-gate/quote_identity.json

Exit codes: 0 measured · 2 usage / unwritable --out / an artifact `--attest` refused ·
3 no rows / no composed text (never a silent zero).

⚠️ ``--rows`` is the row cache written by ``composed_gate.py`` (the live, quoted+embedded subset;
re-fetch it with that probe, which is the one owner of the paging contract).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import itertools
import json
import os
import pathlib
import sys

import numpy as np

HERE = pathlib.Path(__file__).resolve().parent


def _payload_digest(report: dict, rows_source: str) -> str:
    """sha256 over the artifact's EVIDENCE — every field except ``provenance``, PLUS the row
    cache those fields are attributed to.

    Same canonical form as ``composed_gate.py``'s and the same reason: ``probe_sha256`` binds this
    file to the code that produced it, ``payload_sha256`` binds it to the root-cause numbers the
    fix (#12436) is scoped against — 2,059 Points sharing a quote, 28,994 byte-identical composed
    pairs. ``rows_source`` travels INSIDE the digest because it is the attribution those numbers
    rest on: re-pointing it at a different cache is an edit to the evidence, not a note about it.
    """
    body = {k: v for k, v in report.items() if k != "provenance"}
    canon = json.dumps({"artifact": body, "rows_source": rows_source},
                       indent=2, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _provenance(report: dict, rows_source: str) -> dict:
    """The ONE producer of the provenance block: a fresh run and ``--attest`` both call it.

    ⚠️ ``--attest`` blesses the bytes it is handed, so the guarantee is *"nothing edited this
    artifact after the attest"* — not *"these numbers came from a run"*.
    """
    return {
        "probe_path": str(pathlib.Path(__file__).relative_to(HERE)),
        "probe_sha256": hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest(),
        "payload_sha256": _payload_digest(report, rows_source),
        "rows_source": rows_source,
        "composed_encoder": "BAAI/bge-small-en-v1.5 (only via the composer's word budget; "
                            "this section encodes nothing)",
    }


def _attest(path: str) -> int:
    """(Re)bind an existing artifact's provenance to this probe's bytes and its own evidence.

    Mirrors ``composed_gate.py --attest``: a re-run needs the row cache (which is not
    committed), and the binding is a pure function of the probe's bytes and the artifact's
    own payload. It refuses an artifact that carries no measurement, and one whose provenance
    does not name a row source, so neither a truncated report nor a stripped artifact can be
    attested into evidence. Every refusal exits ``2``: the artifact's SHAPE is checked before any
    ``.get`` is called on it, so a non-object ``provenance`` is a documented refusal rather than
    an ``AttributeError`` out of ``main()`` (#12476).
    """
    try:
        report = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        _say(f"ATTEST-UNREADABLE({path}): {e}\n")
        return 2
    if not isinstance(report, dict):
        _say(f"ATTEST-REFUSED({path}): the artifact is not a JSON object "
             f"(`{type(report).__name__}`), so it carries no root-cause measurement\n")
        return 2
    if "measured_points" not in report or "shared_composed_text_groups" not in report:
        _say(f"ATTEST-REFUSED({path}): no root-cause measurement in this artifact\n")
        return 2
    # Same type discipline as `composed_gate.py::_attest` (#12476): `(report.get("provenance") or
    # {})` passes a non-object straight through to `.get`, so a hand-edit that replaced the block
    # with a list exited 1 with an AttributeError instead of the documented 2.
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        _say(f"ATTEST-REFUSED({path}): `provenance` is not an object "
             f"(`{type(provenance).__name__}`), so `rows_source` cannot be read\n")
        return 2
    rows_source = str(provenance.get("rows_source") or "")
    if not rows_source:
        _say(f"ATTEST-REFUSED({path}): no `provenance.rows_source`, so the row cache the "
             "numbers came from cannot be recorded\n")
        return 2
    report["provenance"] = _provenance(report, rows_source)
    try:
        pathlib.Path(path).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as e:
        _say(f"PROBE-WRITE({path}): {e}\n")
        return 2
    _say(f"attested {path} -> {report['provenance']['probe_sha256']}\n")
    return 0


def _say(msg: str) -> None:
    sys.stderr.write(msg)


def measure(rows: list[dict]) -> dict:
    from tortoise.embed_compose import compose_embedding_text, quote_text

    kept = [r for r in rows
            if isinstance(r.get("embedding"), list) and r["embedding"]
            and quote_text(r.get("quote"))]
    if not kept:
        _say("IDENTITY-EMPTY: no live quoted+embedded rows in the cache.\n")
        raise SystemExit(3)
    composed = [compose_embedding_text(r.get("quote"), (), content=r.get("content"))
                for r in kept]
    raw = np.asarray([r["embedding"] for r in kept], dtype=np.float32)
    raw /= np.clip(np.linalg.norm(raw, axis=1, keepdims=True), 1e-12, None)

    groups: dict[str, list[int]] = collections.defaultdict(list)
    for i, text in enumerate(composed):
        groups[text].append(i)
    shared = {t: v for t, v in groups.items() if len(v) > 1}

    pairs = 0
    same_content = 0
    below = 0
    at_or_above = 0
    examples: list[dict] = []
    for text, idx in shared.items():
        for a, b in itertools.combinations(idx, 2):
            pairs += 1
            if kept[a].get("content") == kept[b].get("content"):
                same_content += 1
            r = float(raw[a] @ raw[b])
            if r < 0.89:
                below += 1
                if len(examples) < 8:
                    examples.append({
                        "raw_similarity": round(r, 4),
                        "composed_similarity": 1.0,
                        "composed_text": text[:180],
                        "content_a": (kept[a].get("content") or "")[:160],
                        "content_b": (kept[b].get("content") or "")[:160],
                    })
            else:
                at_or_above += 1

    rr = raw @ raw.T
    np.fill_diagonal(rr, -1.0)
    exact = int(np.count_nonzero(np.triu(rr >= 0.99999, 1)))

    sizes = sorted((len(v) for v in shared.values()), reverse=True)
    return {
        "measured_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                  __import__("time").gmtime()),
        "rows_in_cache": len(rows),
        "measured_points": len(kept),
        "distinct_composed_texts": len(groups),
        "points_without_a_distinct_composed_text": len(kept) - len(groups),
        "points_without_distinct_text_pct": round(
            100.0 * (len(kept) - len(groups)) / len(kept), 2),
        "shared_composed_text_groups": len(shared),
        "points_in_shared_groups": sum(len(v) for v in shared.values()),
        "points_in_shared_groups_pct": round(
            100.0 * sum(len(v) for v in shared.values()) / len(kept), 2),
        "pairs_composing_to_identical_text": pairs,
        "of_those_with_DIFFERENT_content": pairs - same_content,
        "of_those_with_same_content": same_content,
        "of_those_raw_below_0_89": below,
        "of_those_raw_at_or_above_0_89": at_or_above,
        "largest_groups": [{"points": n} for n in sizes[:6]],
        "largest_group_points": sizes[0] if sizes else 0,
        "raw_exact_duplicate_pairs_ge_0_99999": exact,
        "examples": examples,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", default="/tmp/11739-rows.jsonl")
    ap.add_argument("--out", default="-")
    ap.add_argument("--attest", default="", metavar="PATH",
                    help="(re)write provenance into an existing artifact; no measurement runs")
    args = ap.parse_args(argv[1:])
    if args.attest:
        return _attest(args.attest)
    try:
        rows = [json.loads(line) for line in open(args.rows, encoding="utf-8")]
    except OSError as e:
        _say(f"IDENTITY-NO-ROWS({args.rows}): {e}\n")
        return 3
    report = measure(rows)
    report["provenance"] = _provenance(report, args.rows)
    text = json.dumps(report, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(text)
        sys.stdout.flush()
        return 0
    try:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
    except OSError as e:
        _say(f"PROBE-WRITE({args.out}): {e}\n")
        return 2
    _say(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
