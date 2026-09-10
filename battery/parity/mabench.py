"""MemoryAgentBench Conflict Resolution — data + official metric (#2800).

The lead standard comparable for the battery's R1/R4 families (contradiction
surfacing / defeat). This module owns two things and NOTHING else:

1. **Getting the published dataset, pinned.** The file is fetched from
   HuggingFace and verified against its published sha256 before it is parsed
   — the same content-addressed rule our LongMemEval leg already follows
   (`tools/longmem_eval/dataset.py`). No converted copy is ever committed as
   the data source: a copy drifts silently from the publisher's file and
   breaks the comparability the benchmark exists to provide.
2. **The benchmark's OWN metric, ported verbatim.** ``normalize_answer``,
   ``substring_exact_match_score``, ``drqa_metric_max_over_ground_truths``
   and ``parse_output`` are faithful ports of
   ``utils/eval_other_utils.py`` in ``HUST-AI-HYZ/MemoryAgentBench``
   (repo README: "accuracy" maps to ``substring_exact_match`` for the
   Conflict Resolution tasks ``fact_sh``/``fact_mh``). Compared against the
   published implementation line by line; the citation is in each docstring.
   This module does NOT invent a metric, and it does not re-derive one.

The task shape (verified by reading the published parquet, 2026-09-10):
8 configs = ``factconsolidation_{sh,mh}_{6k,32k,64k,262k}``, 100 QA pairs
each; every row carries a flat fact list in ``context`` (the rewritten
contradictory fact appears AFTER the original — the conflict), a
``questions`` list, and ``answers`` with the accepted answer strings per
question.

Absent pieces are explicit, never defaulted: a missing ``pyarrow``, a digest
mismatch, or an unknown config all raise, and the caller records a
not-measured cell.
"""
from __future__ import annotations

import hashlib
import os
import re
import string
import urllib.request
from dataclasses import dataclass
from pathlib import Path

HUGGINGFACE_DATASET = "ai-hyz/MemoryAgentBench"
#: The published Conflict Resolution file (1,491,588 bytes).
CR_FILE = "data/Conflict_Resolution-00000-of-00001.parquet"
#: Its published sha256 (the Hub's LFS object id) — the pin. Verified by
#: re-downloading on 2026-09-10: the bytes hash to exactly this value.
CR_SHA256 = "24d5c3f09ce0ce15625cb9f8a98f44f0d864ca6c94d7b4ad04eb697ca3a5ff45"

#: The eight pinned CR configurations (verified against the dataset).
CR_CONFIGS: tuple[str, ...] = tuple(
    f"factconsolidation_{kind}_{size}"
    for kind in ("sh", "mh")
    for size in ("6k", "32k", "64k", "262k")
)

DEFAULT_CACHE_DIR = "~/.cache/tortoise-mabench"


class MabenchError(RuntimeError):
    """Base class for MemoryAgentBench data/metric refusals."""


class DatasetDigestError(MabenchError):
    """The fetched dataset's sha256 does not match the pinned digest."""


class PyarrowUnavailable(MabenchError):
    """The parquet reader is not installed (``[parity]`` extra)."""


def cache_dir() -> Path:
    """Resolve the dataset cache dir (env override or default, outside repo)."""
    raw = os.environ.get("TORTOISE_MABENCH_CACHE_DIR", "").strip()
    base = Path(raw).expanduser() if raw else Path(DEFAULT_CACHE_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


def remote_url() -> str:
    return (f"https://huggingface.co/datasets/{HUGGINGFACE_DATASET}"
            f"/resolve/main/{CR_FILE}")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def verify_digest(path: Path, *, expected: str = CR_SHA256) -> None:
    """Raise :class:`DatasetDigestError` unless ``path`` hashes to ``expected``.

    This is what makes the revision claim content-addressed: the cell records
    the digest, and a tampered/truncated file can never be parsed into a
    number.
    """
    actual = _sha256_file(path)
    if actual != expected:
        raise DatasetDigestError(
            f"MemoryAgentBench CR digest mismatch: expected {expected}, got "
            f"{actual} — the cached file is truncated or tampered; delete it "
            f"and re-download, or pin the new digest deliberately")


def _fetch_bytes(url: str, timeout: int) -> bytes:
    """Fetch the published file (single seam, so tests can substitute it)."""
    req = urllib.request.Request(
        url, headers={"User-Agent": "tortoise-mabench-parity"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_cr_parquet(*, dest_dir: Path | None = None, timeout: int = 120,
                     expected: str = CR_SHA256) -> Path:
    """Fetch the pinned CR parquet, verifying its digest before publishing it.

    The file is written to a ``.part`` sibling and only moved into the cache
    after the digest check passes, so an interrupted download can never leave
    a half-file that looks like a cached dataset. A cached file that fails
    verification is deleted and re-fetched ONCE (a truncated cache is
    recoverable); a fresh download that also fails raises — a wrong pin is a
    deliberate act, never something to paper over.
    """
    target = (dest_dir or cache_dir()) / Path(CR_FILE).name
    if target.is_file():
        try:
            verify_digest(target, expected=expected)
            return target
        except DatasetDigestError:
            target.unlink()  # corrupt cache: re-fetch once below
    part = target.with_name(target.name + ".part")
    try:
        part.write_bytes(_fetch_bytes(remote_url(), timeout))
        verify_digest(part, expected=expected)
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return target


@dataclass(frozen=True)
class CrItem:
    """One Conflict-Resolution question with its accepted answers."""

    qa_pair_id: str
    config: str
    question: str
    accepted: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.accepted:
            raise ValueError(
                f"{self.qa_pair_id}: no accepted answer — an unscored question "
                f"must never be counted as a result")


def _config_of(qa_pair_id: str) -> str:
    return re.sub(r"_no\d+$", "", qa_pair_id)


def load_cr_items(config: str, *, path: Path | None = None
                  ) -> tuple[CrItem, ...]:
    """Load one pinned CR configuration (e.g. ``factconsolidation_sh_6k``).

    Raises :class:`PyarrowUnavailable` when the parquet reader is missing
    (install the ``[parity]`` extra) and :class:`MabenchError` for an unknown
    config — the caller turns both into a not-measured cell.
    """
    if config not in CR_CONFIGS:
        raise MabenchError(
            f"unknown CR config {config!r}; pinned configs: "
            f"{', '.join(CR_CONFIGS)}")
    # Content identity FIRST, reader second: the file must be the pinned one
    # whether it came from the cache or from the caller (a caller-supplied
    # file is not exempt from the pin — an unverified corpus must never be
    # scored, however it arrived).
    src = path or fetch_cr_parquet()
    verify_digest(src)

    try:
        import pyarrow.parquet as pq
    except Exception as e:  # pragma: no cover - import guard, not a metric
        raise PyarrowUnavailable(
            f"MemoryAgentBench CR needs the parquet reader: install the "
            f"parity extra (uv sync --extra parity). ({e})") from e

    table = pq.read_table(src)
    items: list[CrItem] = []
    for i in range(table.num_rows):
        meta = table.column("metadata")[i].as_py() or {}
        qa_ids = list(meta.get("qa_pair_ids") or [])
        if not qa_ids or _config_of(str(qa_ids[0])) != config:
            continue
        questions = table.column("questions")[i].as_py() or []
        answers = table.column("answers")[i].as_py() or []
        if len(qa_ids) != len(questions) or len(qa_ids) != len(answers):
            raise MabenchError(
                f"CR config {config!r}: row {i} has {len(qa_ids)} qa ids but "
                f"{len(questions)} questions / {len(answers)} answer lists — "
                f"the pinned file changed shape; refusing to pair a question "
                f"with another question's answers")
        # strict=True is now safe: the length guard above proved they match,
        # and it keeps a future edit from silently truncating the row.
        for qa_id, question, accepted in zip(qa_ids, questions, answers,
                                             strict=True):
            flat = tuple(str(a) for a in (accepted or []))
            items.append(CrItem(qa_pair_id=str(qa_id), config=config,
                                question=str(question), accepted=flat))
    if not items:
        raise MabenchError(
            f"CR config {config!r} matched no rows in {src} — the pinned file "
            f"changed shape; refusing to report an empty measurement")
    return tuple(items)


# ── the benchmark's OWN metric, ported verbatim ───────────────────────────
def normalize_answer(answer_text: str) -> str:
    """Verbatim port of ``utils/eval_other_utils.py::normalize_answer``
    (MemoryAgentBench): lowercase, drop punctuation, drop articles a/an/the,
    collapse whitespace."""
    text = str(answer_text).lower()
    text = "".join(char for char in text if char not in string.punctuation)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def substring_exact_match_score(prediction: str, ground_truth: str) -> bool:
    """Verbatim port of the benchmark's ``substring_exact_match_score``:
    the normalized ground truth is a substring of the normalized prediction."""
    gold = normalize_answer(ground_truth)
    if not gold:
        # An empty gold would match everything; the benchmark's own corpora
        # never contain one, and a vacuous pass is a fabricated score.
        raise MabenchError(
            "empty ground truth after normalization — refusing a vacuous match")
    return gold in normalize_answer(prediction)


def score_max_over_ground_truths(prediction: str,
                                 ground_truths: str | list) -> bool:
    """Verbatim port of ``drqa_metric_max_over_ground_truths`` for the binary
    substring metric: True when ANY accepted answer scores."""
    if isinstance(ground_truths, str):
        golds = [ground_truths]
    elif ground_truths and isinstance(ground_truths[0], list):
        golds = [gt for sub in ground_truths for gt in sub]
    else:
        golds = list(ground_truths)
    if not golds:
        raise MabenchError("no ground truths supplied — refusing to score")
    return any(substring_exact_match_score(prediction, gt) for gt in golds)


def parse_output(output_text: str, answer_prefix: str = "Answer:") -> str | None:
    """Verbatim port of the benchmark's ``parse_output``: extract the model's
    answer (the ``Answer:`` line, else the first line)."""
    patterns = [
        re.compile(f"(?:{answer_prefix})(.*)(?:\\n|$)", flags=re.IGNORECASE),
        re.compile(r"(?:^)(.*)(?:\n|$)"),
    ]
    for pattern in patterns:
        match = pattern.search(str(output_text))
        if match:
            extracted = match[1].strip()
            return re.sub(f"^{re.escape(answer_prefix)}", "", extracted,
                          flags=re.IGNORECASE).strip()
    return None


def score_item(output: str, accepted: tuple[str, ...]) -> bool:
    """Score ONE model output the way the benchmark does.

    Verbatim port of ``default_post_process`` (``utils/eval_other_utils.py``):
    the metric is computed on the RAW output, then — when the parse yields
    something — on the parsed answer as well, and the best of the two wins.
    Scoring only the parsed text is NOT equivalent: an answer stated on a
    later line without the ``Answer:`` prefix fails the parse but matches the
    raw output, and the official harness counts it CORRECT.
    """
    golds = list(accepted)
    best = score_max_over_ground_truths(output, golds)
    parsed = parse_output(output)
    if parsed is not None and parsed != output:
        best = best or score_max_over_ground_truths(parsed, golds)
    return best


def score_cr(predictions: dict[str, str], items: tuple[CrItem, ...]
             ) -> tuple[float, int]:
    """Accuracy over the items, using the benchmark's own binary metric.

    Returns ``(accuracy, n)`` with ``n`` ALWAYS the full item count: an item
    the arm produced no output for is scored wrong rather than skipped, so
    the accuracy cannot be inflated by silently dropping unanswered hard
    cases. (The official harness always has an output to score; a missing one
    is our integration's failure and is charged as one.)
    """
    if not items:
        raise MabenchError("no items to score — refusing an empty measurement")
    correct = 0
    for item in items:
        raw = predictions.get(item.qa_pair_id)
        if raw is None:
            continue
        if score_item(raw, item.accepted):
            correct += 1
    return correct / len(items), len(items)
