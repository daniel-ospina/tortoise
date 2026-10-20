"""Sealed ask-lane corpus access + hash verification (issue #9097, epic #2281).

Runner-facing API for the committed sealed corpus under ``tests/eval/ask/``:
canonical paths, corpus hash coverage (fixture AND gold files — a gold-only
edit invalidates the pre-registration), manifest verification, and the
first-run-pending baseline factory the generator commits.

⛔ **Two loaders, two audiences.** :func:`load_fixtures` is what the RUNNER
consumes: it globs ``fixtures/*.json`` only, so the answer key is not merely
"not read" by convention, it is not reachable through the API the runner is
handed. :func:`load_gold` / :func:`load_golds` are the SCORER's — the step
that applies the judge and evaluates the exit bar.

Deliberately a SEPARATE tree from ``tests/eval/write_path/``: that corpus's
``corpus.py::verify_manifest`` derives ``on_disk`` from its own
``FIXTURE_GLOB``/``GOLD_GLOB`` and requires ``not extra``, so any fixture
dropped into it fails the write-path benchmark's own integrity gate and
changes its single tree-wide ``fixtures_hash`` (invalidating its committed
baselines). Two benchmarks, two trees, two hashes.

Hermetic: pure stdlib.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ASK_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = ASK_DIR / "fixtures"
GOLD_DIR = ASK_DIR / "gold"
BASELINES_DIR = ASK_DIR / "baselines"
MANIFEST_PATH = ASK_DIR / "_manifest.json"
PREREGISTRATION_PATH = ASK_DIR / "preregistration.json"
BASELINE_PATH = BASELINES_DIR / "main.json"

FIXTURE_SCHEMA = "ask-sealed-fixture/v1"
GOLD_SCHEMA = "ask-sealed-gold/v1"
MANIFEST_SCHEMA = "ask-sealed-manifest/v1"
BASELINE_SCHEMA = "ask-sealed-baseline/v1"

#: The two graded classes the exit bar spans. ``knowledge-update`` is the
#: class the epic's exit bar names; ``verbatim`` is the raw/verbatim class the
#: bar must NOT regress. Both run in the SAME invocation (paired read, D4).
CLASS_KNOWLEDGE_UPDATE = "knowledge-update"
CLASS_VERBATIM = "verbatim"
CLASSES: tuple[str, ...] = (CLASS_KNOWLEDGE_UPDATE, CLASS_VERBATIM)

#: Keys that belong to the sealed grading key ONLY. A fixture carrying one is
#: a validation error: the answer key's only on-disk home is ``gold/``.
#:
#: Scanned RECURSIVELY (:func:`find_grading_keys`): a top-level-only check is
#: defeated by ``{"distilled": {"claims": [{"answer": "…"}]}}``, which is the
#: shape a future case would most plausibly grow into.
GRADING_KEYS = frozenset({"answer", "gold", "grading", "expected"})

FIXTURE_GLOB = "fixtures/*.json"
GOLD_GLOB = "gold/*.gold.json"


# ── small JSON/sha helpers (stdlib only, deterministic bytes) ──────────────

def read_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(obj) -> str:
    """Canonical committed bytes: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def sha256_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def sha256_file(path: str | Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


# ── paths / inventory ─────────────────────────────────────────────────────

def fixture_path(case_id: str, root: Path = ASK_DIR) -> Path:
    return root / "fixtures" / f"{case_id}.json"


def gold_path(case_id: str, root: Path = ASK_DIR) -> Path:
    return root / "gold" / f"{case_id}.gold.json"


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def corpus_file_paths(root: Path = ASK_DIR) -> list[Path]:
    """Every fixture + gold file in the corpus (the corpus-hash scope)."""
    return sorted(list((root / "fixtures").glob("*.json"))
                  + list((root / "gold").glob("*.gold.json")))


def case_ids(root: Path = ASK_DIR) -> list[str]:
    """Case ids from the FIXTURE files (sorted, stable)."""
    return sorted(p.stem for p in (root / "fixtures").glob("*.json"))


def compute_fixtures_hash(root: Path = ASK_DIR) -> str:
    """sha256 over the FIXTURE files only (``fixtures/*.json``).

    The RUN step records this, never the full corpus hash: the full hash covers
    the sealed gold, and handing the runner a digest of the answer key would be
    a (weak) oracle — ``gold/<case>.gold.json`` is fully determined by
    ``{schema, case_id, class, answer, judge_template}`` with a known rendering,
    so everything but the answer is known or guessable. The full hash is
    computed by the SCORE step, which is allowed to open the key.
    """
    digests = [(rel, sha256_file(path))
               for rel, path in ((_relative(p, root), p)
                                 for p in sorted((root / "fixtures").glob("*.json")))]
    payload = "\n".join(f"{rel}:{digest}"
                        for rel, digest in sorted(digests)).encode("utf-8")
    return sha256_bytes(payload)


def compute_corpus_hash(root: Path = ASK_DIR) -> str:
    """sha256 over every fixture + gold file.

    Deterministic and independent of filesystem ordering: each file's raw
    bytes are digested with its relative path prefix, sorted by relative path.
    A gold-only edit changes the hash ⇒ the pre-registration no longer
    describes this corpus ⇒ a run over it is ``inconclusive`` (never a silent
    pass on a re-keyed answer key).
    """
    digests = [(rel, sha256_file(path))
               for rel, path in ((_relative(p, root), p)
                                 for p in corpus_file_paths(root))]
    payload = "\n".join(f"{rel}:{digest}"
                        for rel, digest in sorted(digests)).encode("utf-8")
    return sha256_bytes(payload)


# ── sealed loaders ────────────────────────────────────────────────────────

def find_grading_keys(node, path: str = "") -> list[str]:
    """Recursively find grading-key paths in a document (empty = clean).

    A top-level ``intersection`` is a one-depth guard: nesting the key one level
    down (``{"distilled": {"claims": [{"answer": …}]}}``) walked straight past
    the loader, the generator check and the corpus test, all three of which
    claimed the answer key had exactly one home. One predicate, three callers.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key in GRADING_KEYS:
                found.append(here)
            found.extend(find_grading_keys(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(find_grading_keys(value, f"{path}[{index}]"))
    return found


def load_fixtures(root: Path = ASK_DIR) -> list[dict]:
    """The RUNNER's input: the fixtures ONLY, in case-id order.

    Refuses (loudly) any fixture carrying a grading key ANYWHERE in its
    document — the sealed-key rule is enforced at the boundary the runner
    crosses, not by convention.
    """
    out: list[dict] = []
    for path in sorted((root / "fixtures").glob("*.json")):
        fixture = read_json(path)
        leaked = find_grading_keys(fixture)
        if leaked:
            raise ValueError(
                f"sealed corpus violation: {_relative(path, root)} carries "
                f"grading key(s) {leaked} — the answer key lives only in gold/")
        out.append(fixture)
    return out


def load_gold(case_id: str, root: Path = ASK_DIR) -> dict:
    return read_json(gold_path(case_id, root))


def load_golds(root: Path = ASK_DIR) -> dict[str, dict]:
    """The SCORER's key, keyed by case id. Never handed to the runner."""
    return {cid: load_gold(cid, root) for cid in case_ids(root)}


# ── manifest ──────────────────────────────────────────────────────────────

def load_manifest(root: Path = ASK_DIR) -> dict:
    return read_json(root / "_manifest.json")


def verify_manifest(root: Path = ASK_DIR) -> dict:
    """Verify ``_manifest.json`` against the on-disk corpus.

    Returns ``{"ok", "missing", "extra", "mismatched", "malformed",
    "corpus_hash"}``. ``ok`` is True only when the manifest's per-file sha256
    entries cover exactly the fixture + gold files and every digest matches the
    current bytes. ``malformed`` is reported, never raised (a broken manifest
    is a verdict, not a crash).
    """
    try:
        manifest = load_manifest(root)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "missing": [], "extra": [], "mismatched": [],
                "malformed": f"manifest unreadable: {type(exc).__name__}",
                "corpus_hash": compute_corpus_hash(root)}
    if not isinstance(manifest, dict):
        return {"ok": False, "missing": [], "extra": [], "mismatched": [],
                "malformed": (f"manifest document is not an object (got "
                              f"{type(manifest).__name__})"),
                "corpus_hash": compute_corpus_hash(root)}
    files = manifest.get("files")
    if not isinstance(files, dict):
        return {"ok": False, "missing": [], "extra": [], "mismatched": [],
                "malformed": "manifest.files is not an object mapping rel "
                             "paths to digests",
                "corpus_hash": compute_corpus_hash(root)}
    on_disk = {_relative(p, root): p for p in corpus_file_paths(root)}
    missing = [rel for rel in files if rel not in on_disk]
    extra = [rel for rel in on_disk if rel not in files]
    mismatched = [rel for rel, path in on_disk.items()
                  if rel in files and files[rel] != sha256_file(path)]
    expected = manifest.get("corpus_hash")
    actual = compute_corpus_hash(root)
    return {
        "ok": bool(not missing and not extra and not mismatched
                   and expected and expected == actual),
        "missing": sorted(missing),
        "extra": sorted(extra),
        "mismatched": sorted(mismatched),
        "malformed": None,
        "corpus_hash": actual,
    }


# ── baseline ──────────────────────────────────────────────────────────────

def first_run_pending_baseline(root: Path = ASK_DIR) -> dict:
    """The first-run-pending baseline (benchmark-first state).

    Empty ``metrics``/``history``, null ``judge_pin``/``justification``: NO
    quality bar is preset. D8 is explicit — the benchmark-side X set comes from
    the first published run. The only pre-registered bar is the one that needs
    no number: the paired verbatim/raw comparison (a distilled loss against
    the raw arm of the SAME run is a FAIL) and the D8 gate (zero superseded
    claims rendered live).
    """
    return {
        "schema": BASELINE_SCHEMA,
        "corpus_hash": compute_corpus_hash(root),
        "judge_pin": None,
        "justification": None,
        "metrics": {},
        "history": [],
    }
