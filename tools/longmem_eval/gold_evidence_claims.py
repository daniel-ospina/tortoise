"""Gold-evidence claim artifact builder (Track E1, #3011).

Builds the frozen, pre-registered gold-evidence claim list described in
``docs/experiments/2026-09-11-abc-context-assembly-experiment.md`` §4
("The gold-evidence claim list (frozen — derived once, here)") and §9.5
("The gold-evidence claim artifact (§4) must exist before the first
render"). The artifact is a **pre-run deliverable**, materialised outside
the eval graph: no namespace ever holds it, so the seed / traversal /
ranking / render paths cannot read it.

Derivation (verbatim from §4)
-----------------------------
**Source turns.** For each question ``q``: every turn in ``q``'s gold
sessions (``answer_session_ids``) that carries the dataset's turn-level
gold mark ``has_answer: true``, plus ``q``'s gold ``answer`` string.
Nothing else — no extracted claim, no graph Point, no turn from a session
outside the gold set — enters the list.

**Granularity.** One claim ``g`` = one sentence-level span of a source
turn: split the turn's ``content`` on ``.``/``!``/``?``/newline, strip the
role prefix, trim whitespace, drop empty spans. The gold ``answer`` string
enters as one further claim (never split — even when it contains its own
sentence delimiters, e.g. the multi-variant "7 days. 8 days … is also
acceptable." format).

**Tokenizer** (``tokenize`` — the single frozen function used by *both*
the ``MIN_GOLD_TOKENS`` test and the metric-5 ratio): lowercase → delete
every ASCII punctuation character (``string.punctuation``), **not**
replace it with a space, so ``don't`` → ``dont`` and ``12,000`` → ``12000``
as one token → split on whitespace only → drop empty strings → remove the
frozen stopword list. The split is whitespace-only, never on word
boundaries, so ``|tokens(g)|`` is deterministic across implementations.
Only ASCII punctuation is deleted; non-ASCII punctuation (``—``, ``’``)
survives — a literal reading of the frozen rule.

**Minimum token count** (``MIN_GOLD_TOKENS = 3``). A span with
``|tokens(g)| < 3`` is written to the artifact with ``"trivial": true`` and
is **never matched** (``match(p, g) = 0`` by definition). The all-stopword /
all-punctuation span is exactly the ``|tokens(g)| = 0`` case: flagged
trivial, never matched, and **no division is performed** (the metric-5
ratio is defined as 0 rather than computed).

**Non-emptiness (build prerequisite).** Every question must contribute
**≥ 1 non-trivial claim**. A question that cannot is a construction failure
raised as :class:`GoldEvidenceConstructionError` before the run — never a
reason to drop the question or change the 52-question denominator.

Frozen stopword list — choice and rationale
--------------------------------------------
§4 names the list as "the same list the metric-5 rule below uses, appended
to the run manifest before the first render" but does **not** name it. This
builder reuses **``tortoise.sparse.SPARSE_STOPWORDS``** (issue #1541 D1)
verbatim — it defines no list of its own — and re-exports it as the frozen
:data:`STOPWORDS` so the run manifest can record it. Rationale:

1. **It is the only public, exportable constant** among the repo's existing
   candidates (``tortoise/session_indexer.py``, ``tortoise/retrieval.py``,
   ``tools/longmem_eval/probe_v2.py`` and ``tools/longmem_eval/evidence.py``
   all keep theirs private/underscore-prefixed). §4 requires the list to be
   *recorded in the manifest*, so a documented public constant is the right
   provenance anchor — importing a private symbol would pin the manifest to
   an implementation detail that can be renamed without notice.
2. **It is the product's own small English set**, deliberately documented as
   "kept deliberately SMALL — a generic 200-word list would swallow domain
   vocabulary". Over-aggressive removal inflates the trivial set and erodes
   the non-emptiness guarantee, so the small set is the conservative choice
   for gold spans.
3. **The dependency direction is correct.** ``tools/longmem_eval/`` is a thin
   measurement layer over the product (``tortoise/``) — the eval consuming a
   product constant is normal. ``tortoise/retrieval.py`` only warns against
   the *reverse* (the product importing the eval's list).

Artifact
--------
``docs/experiments/artifacts/2026-09-11-abc-context-assembly/
gold-evidence-claims.json`` — an object keyed by ``qid``; each value a list
of ``{"claim": str, "source_turn_id": "lme:<qid>:s<si>:t<ti>" |
"gold_answer", "tokens": int, "trivial": bool}``. ``si`` is the positional
index of the gold session in ``haystack_session_ids``; ``ti`` the positional
index of the turn within that session. A sibling ``<name>.sha256`` holds the
sha256sum-format digest to record in the run manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import string
import sys
from pathlib import Path
from typing import Any

from tortoise.sparse import SPARSE_STOPWORDS

# ── frozen constants (recorded in the run manifest) ───────────────────────

#: Minimum number of content tokens for a claim to be matchable (spec §4).
MIN_GOLD_TOKENS = 3

#: The frozen stopword list (spec §4). Re-exported verbatim from
#: ``tortoise.sparse.SPARSE_STOPWORDS`` — this module defines no list of its
#: own. Same object, not a copy, so a change to the product constant cannot
#: silently diverge from the recorded manifest value.
STOPWORDS: frozenset[str] = SPARSE_STOPWORDS

#: The frozen artifact location (spec §4 Storage; named by the §10 static
#: reference assertion in ``tools/longmem_eval/leakage_guard.py``).
ARTIFACT_DIR = "docs/experiments/artifacts/2026-09-11-abc-context-assembly"
ARTIFACT_FILENAME = "gold-evidence-claims.json"
ARTIFACT_PATH = f"{ARTIFACT_DIR}/{ARTIFACT_FILENAME}"

#: A leading ``[role]`` bracket, mirroring ``tortoise/retrieval.py``'s
#: ``_ROLE_PREFIX_RE``. Dataset turn content carries no such prefix (verified
#: against the 55-question slice: 0/27,055 turns), but the spec requires the
#: strip, and the arm-B/C render path writes turns *with* the prefix.
_ROLE_PREFIX_RE = re.compile(r"^\[(user|assistant|system|tool|unknown)\]\s*",
                             re.IGNORECASE)

#: Sentence-level span delimiters (spec §4 "Granularity").
_SENTENCE_DELIM_RE = re.compile(r"[.!?\n]")

#: ASCII punctuation deletion table — maps every ``string.punctuation``
#: character to ``None`` (delete, never replace with a space).
_PUNCT_DELETE = str.maketrans("", "", string.punctuation)


class GoldEvidenceConstructionError(ValueError):
    """A question cannot contribute ≥1 non-trivial gold-evidence claim.

    Spec §4 "Non-emptiness (build prerequisite)": this is a **construction
    failure fixed before the run**, never a reason to drop the question or
    change the 52-question metric-5 denominator.
    """


# ── frozen tokenizer ───────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """The single frozen tokenizer (spec §4): lowercase → delete every ASCII
    punctuation character (no space substitution) → split on whitespace →
    drop empty strings → remove the frozen stopwords.

    Returns a list; duplicates are preserved, so ``len(tokenize(g))`` is the
    literal ``|tokens(g)|`` the frozen rule names for both the
    ``MIN_GOLD_TOKENS`` test and the metric-5 denominator.
    """
    return [
        tok
        for tok in text.lower().translate(_PUNCT_DELETE).split()
        if tok and tok not in STOPWORDS
    ]


# ── sentence-level spans ───────────────────────────────────────────────────


def sentence_spans(text: str) -> list[str]:
    """Split ``text`` into sentence-level spans, in spec §4 order: split on
    ``.``/``!``/``?``/newline → strip the role prefix → trim → drop empties.
    """
    spans: list[str] = []
    for raw in _SENTENCE_DELIM_RE.split(text):
        span = _ROLE_PREFIX_RE.sub("", raw, count=1).strip()
        if span:
            spans.append(span)
    return spans


def _claim(claim: str, source_turn_id: str, tokens: int) -> dict[str, Any]:
    """Build one artifact claim record in the frozen field order."""
    return {
        "claim": claim,
        "source_turn_id": source_turn_id,
        "tokens": tokens,
        "trivial": tokens < MIN_GOLD_TOKENS,
    }


# ── builder ────────────────────────────────────────────────────────────────


def build_claims_for_question(row: dict) -> list[dict]:
    """Derive one question's frozen gold-evidence claim list.

    Source turns are the ``has_answer`` turns of the question's gold sessions
    (``answer_session_ids``, resolved positionally against
    ``haystack_session_ids``) plus the gold ``answer`` string as one claim.

    Raises :class:`GoldEvidenceConstructionError` if the question contributes
    **no** non-trivial claim (spec §4 Non-emptiness), or if the row does not
    line up (missing gold session, ragged haystack arrays) — a silent drop
    would change the metric-5 denominator.
    """
    qid = row.get("question_id")
    if not isinstance(qid, str) or not qid:
        raise GoldEvidenceConstructionError(
            f"row has no usable question_id: {qid!r}"
        )

    session_ids = list(row.get("haystack_session_ids") or [])
    sessions = list(row.get("haystack_sessions") or [])
    if len(session_ids) != len(sessions):
        raise GoldEvidenceConstructionError(
            f"{qid}: haystack_session_ids ({len(session_ids)}) and "
            f"haystack_sessions ({len(sessions)}) are ragged"
        )
    session_index = {sid: i for i, sid in enumerate(session_ids)}

    claims: list[dict] = []
    for answer_sid in row.get("answer_session_ids") or []:
        if answer_sid not in session_index:
            raise GoldEvidenceConstructionError(
                f"{qid}: gold session {answer_sid!r} is absent from "
                f"haystack_session_ids"
            )
        si = session_index[answer_sid]
        for ti, turn in enumerate(sessions[si]):
            if not turn.get("has_answer"):
                continue
            for span in sentence_spans(str(turn.get("content") or "")):
                claims.append(
                    _claim(span, f"lme:{qid}:s{si}:t{ti}", len(tokenize(span)))
                )

    # The gold answer is exactly ONE further claim — never sentence-split.
    answer_text = "" if row.get("answer") is None else str(row["answer"])
    claims.append(_claim(answer_text, "gold_answer", len(tokenize(answer_text))))

    if not any(not c["trivial"] for c in claims):
        raise GoldEvidenceConstructionError(
            f"{qid}: no non-trivial gold-evidence claim "
            f"({len(claims)} claim(s), all < {MIN_GOLD_TOKENS} content tokens)"
        )
    return claims


def build_artifact(rows: list[dict]) -> dict[str, list[dict]]:
    """Build the full artifact, keyed by ``qid`` in ascending ``qid`` order.

    Deterministic output ordering keeps the artifact sha256 stable across
    input permutations. Raises (via :func:`build_claims_for_question`) rather
    than dropping any question that fails the non-emptiness rule.
    """
    artifact: dict[str, list[dict]] = {}
    for row in sorted(rows, key=lambda r: str(r.get("question_id", ""))):
        qid = str(row.get("question_id", ""))
        if qid in artifact:
            raise GoldEvidenceConstructionError(f"duplicate question_id: {qid!r}")
        artifact[qid] = build_claims_for_question(row)
    return artifact


def artifact_sha256(path: str | Path) -> str:
    """sha256 hex digest of the artifact file's bytes (spec §4/§9.5: the
    digest recorded in the run manifest)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """``--instances <json> --out <path>`` → artifact + sibling ``.sha256``."""
    parser = argparse.ArgumentParser(
        prog="gold_evidence_claims",
        description="Build the frozen gold-evidence claim artifact (§4/#3011).",
    )
    parser.add_argument("--instances", required=True,
                        help="LongMemEval instances JSON (a list of rows)")
    parser.add_argument("--out", required=True,
                        help="artifact output path (gold-evidence-claims.json)")
    args = parser.parse_args(argv)

    rows = json.loads(Path(args.instances).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        print(f"error: --instances must be a JSON list, got {type(rows).__name__}",
              file=sys.stderr)
        return 2

    try:
        artifact = build_artifact(rows)
    except GoldEvidenceConstructionError as exc:
        # Fail closed: no partial artifact, no silent denominator change.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    digest = artifact_sha256(out)
    sha_path = out.with_name(out.name + ".sha256")
    sha_path.write_text(f"{digest}  {out.name}\n", encoding="utf-8")

    claims = [c for cs in artifact.values() for c in cs]
    trivial = sum(1 for c in claims if c["trivial"])
    print(f"questions: {len(artifact)}")
    print(f"claims: {len(claims)} (trivial: {trivial})")
    print(f"sha256: {digest}")
    print(f"artifact: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover — thin CLI shim
    raise SystemExit(main())
