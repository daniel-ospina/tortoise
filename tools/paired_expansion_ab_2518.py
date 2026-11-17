#!/usr/bin/env python
"""A PAIRED A/B of the #2518 entity-key expansion — one frozen corpus, one sample, two arms.

WHY THIS EXISTS
---------------
`#13118` compared two INDEPENDENT eval runs of an expansion lever and published
"a wash with a regression in it" — recall@3 75.0 % → 76.3 %, recall@15 88.0 % →
85.0 %, `absent_at_any_k` 28 → 25 — from **two loose runs on a live corpus at
different loads**. The arithmetic retires the claim on its own: the @15 delta is
3.0 pp at SE 2.8 pp (z = 1.08) and the @3 delta 1.3 pp (z = 0.37). Nothing there
is distinguishable from noise, and the truth was *we do not know*.

⛔ **And that comparison was not merely underpowered — it was structurally blind,
which the call site says and this tool MEASURES.** `entity_key_expansion` arms
only when `entity_type == "point"` (`TortoiseSDK.tortoise_fts_query` in
`tortoise/sdk.py`: the sparse half and the dense half both sit behind
`entity_key_expansion and entity_type == "point"`),
while `tools/candidate_recall_11901.py` measures the **OBJECT** leg. On the object
arm the two switch states are the SAME code path, byte-identically. So the
brief's two numbers differ by corpus drift, machine load and sampling noise only.
This tool runs that arm anyway (both arms are on by default) and refuses to call
its null a result: it records **every item's ranked list on BOTH sides** and
derives the ON-vs-OFF delta from that element-wise comparison, so *the lever never
fired* is a counted identity (`expansion_fired_items: 0 of N`) rather than an
assertion. ⛔ Recording the ranked list for one arm only is how this claim was
briefly `None == None` — a constant reported as a measurement (`#13267` F1);
`_read_delta` now REFUSES an arm whose ranked lists were not recorded.

The leg-trace `provenance` count is recorded too (`expansion_provenance_entries`),
but it is **not** the firing signal: only the DENSE half tags its trace entries,
so a sparse-only run reports `0` entries on BOTH sides even when the expansion
demonstrably changed the answer. Detect firing from the READ.

WHAT IS MEASURED, IN ONE TOOL
-----------------------------
1. **A frozen corpus.** ``freeze`` copies ``--sessions-dir`` to a fixed path once
   and records every file's name, size, line count and sha256 plus an aggregate
   digest (``MANIFEST.json``). Every arm reads the snapshot; nothing reads the
   live directory. When the snapshot already exists its digests are VERIFIED, not
   re-copied — a corpus that moved under a finished run is refused, not renamed.
   This is the single change that makes two runs comparable (`#13118`'s own brief
   names it: the live corpus drifted ~11 % between a run and its re-run).
2. **One sample, evaluated twice.** The population is drawn ONCE and written to
   ``--population-file``; both arms read that file. A re-run reuses it, and any
   snapshot/population mismatch is REFUSED. `#13118` recorded that pairing was
   impossible because "pairing needs the sampled population written out
   (--population-file), which this harness does not do" — that is this file's job.
3. **Per-item outcomes.** One read per item per arm at ``--read-limit``, so every
   k is a PREFIX of the same ranked list and the arms cannot differ by read width.
   For the point arm the item is the class the mechanism targets: a same-subject
   point in ANOTHER session whose content shares no query token (plain sparse
   cannot reach it) whose E3 ``search_keys`` are its only join surface.
4. **Paired statistics.** McNemar's exact test on the discordant pairs (b = miss
   → hit, c = hit → miss), a seeded paired bootstrap CI on the recall delta, the
   measured outcome correlation and the implied variance reduction, and the
   **minimum detectable effect** for the n actually used — ⛔ REPORTED BESIDE the
   exact test, and **not** pre-stated: π is this run's own discordant share, so
   the figure cannot exist until both arms have been read (`#13267` review 4 F3;
   an earlier revision of this line claimed it was fixed in advance).
   `#13118`'s two-arms-need-755 framing is recomputed for contrast — ⛔ that
   estimate is quoted, not derived, and is published nowhere this repo can point
   to (`#13267` review 3); what this tool computes is the DERIVATION.
5. **The harm side, measured too.** "Help or hurt" needs both directions: the tool
   records how much of each arm's top-k the expansion's merged, re-ranked pass
   DISPLACES (`_entity_key_dense_pass`'s docstring names this as real behaviour,
   not a bug), so a gain in target recall cannot be reported without its cost.

WHAT THIS IS NOT
----------------
* Not a production census. The corpus supplies real capture text, real names and
  real keys; the per-item graph LAYOUT is constructed (declared in the artifact as
  ``construction``), exactly as `#11901` §4 declared its generated variants.
* Not a measurement of the DENSE half. ``TORTOISE_ENTITY_KEY_DENSE`` is OFF by
  default and stays OFF here; when it is armed the tool refuses (it would be a
  second, conflated lever).
* Not a change to retrieval. This module reads; it writes only to its own graph
  and its own artifact.

USAGE
-----
    uri="$(uv run python tools/test_lane.py uri)" || exit 1
    eval "$uri"
    uv run python tools/paired_expansion_ab_2518.py \\
        --freeze /tmp/corpus-snapshot-2518 \\
        --out /tmp/paired-ab-2518.json

``test_lane.py run`` executes PYTEST, never this tool.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import random
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

#: The windows the paired comparison reports. 3 is what the capture prior ships;
#: 15 is the widest window `#11901` measured. Every k is a prefix of ONE read at
#: ``--read-limit``, so the pair bounds the answer without a second read shape.
KS: tuple[int, ...] = (3, 5, 10, 15)

#: The provenance string the expansion's own trace entries carry
#: (`search_engine.PROVENANCE_ENTITY_KEY_EXPANSION`). Restated as a literal so a
#: change to the constant cannot silently turn the "the lever fired" counter into
#: "0" and make the structural-blindness evidence read as a clean null.
EXPANSION_PROVENANCE = "entity_key_expansion"

SNAPSHOT_SCHEMA = "paired-expansion-ab-2518.snapshot.v1"
POPULATION_SCHEMA = "paired-expansion-ab-2518.population.v1"
ARTIFACT_SCHEMA = "paired-expansion-ab-2518.artifact.v1"

#: Refusal CLASSES, so an opt-out can be scoped to the class it is about.
#: ⛔ `--allow-mixed-modes` used to excuse EVERY class, which made a "tolerate a
#: contaminated window-mode mix" flag also un-refuse a structurally-blind run and
#: an inert harvest — the exact exit-3 property the tool advertises (`#13267` F7).
MODE_REFUSAL = "window_modes"
STRUCTURAL_REFUSAL = "structurally_blind"
INERT_REFUSAL = "inert_harvest"

#: What the determinism check compares. `ranked_list_digest` compares the READ
#: this run's repeat performed against the OFF read; the retired basis compared
#: the target's RANK, which is `None` on every point-arm item, i.e. `None ==
#: None` (`#13267` review 4 F2). Recorded in the artifact so a reader can tell a
#: measured determinism from a vacuous one.
REPEAT_BASIS_DIGEST = "ranked_list_digest"
REPEAT_BASIS_LEGACY = "target_rank"


class Refused(Exception):
    """A measurement the tool will not take. Every caller returns exit 3."""


def _log(stage: str, **fields) -> None:
    """Progress to stderr, flushed. ⛔ Not decoration: the first full-scale run
    was killed at 55 minutes with no output because the only print was at the
    end, and a lane that leaves no trace of WHERE it was is a lane whose result
    cannot be resumed. Each stage line is one resumable boundary."""
    extra = "  ".join(f"{k}={v}" for k, v in fields.items())
    print(f"[paired-ab] {stage}" + (f"  {extra}" if extra else ""),
          file=sys.stderr, flush=True)


# ── the frozen corpus ────────────────────────────────────────────────────────
def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def _count_lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def snapshot_digest(manifest: dict) -> str:
    """The aggregate digest: sha256 over ``name:sha256:bytes``, sorted by name.

    ⛔ Sorting is load-bearing. ``Path.glob`` order is filesystem-dependent, so an
    unsorted digest would differ between a copy and its verification on two boxes
    holding identical bytes — and the whole point of the digest is to be a
    property of the CONTENT, not of the directory iterator.
    """
    body = "\n".join(
        f"{row['name']}:{row['sha256']}:{row['bytes']}"
        for row in sorted(manifest["files"], key=lambda r: r["name"]))
    return _sha256_bytes(body.encode())


def freeze_snapshot(src: Path, dst: Path) -> dict:
    """Copy ``src`` to ``dst`` ONCE and record it; verify instead of re-copying.

    A snapshot that already exists is verified in place. If any byte differs the
    call REFUSES — silently re-copying would produce a run whose "frozen" corpus
    is a third state neither the earlier artifact nor this run described.
    """
    src = Path(src).expanduser()
    dst = Path(dst).expanduser()
    if not src.is_dir():
        raise Refused(f"--sessions-dir {src} is not a directory")
    manifest_path = dst / "MANIFEST.json"
    if manifest_path.exists():
        recorded = json.loads(manifest_path.read_text())
        current = _build_manifest(dst, source=str(dst))
        if current["digest"] != recorded["digest"]:
            raise Refused(
                f"the snapshot at {dst} no longer matches its own manifest "
                f"({recorded['digest'][:16]} recorded, {current['digest'][:16]} "
                f"on disk) — it is not frozen. Re-freeze to a NEW path rather "
                f"than overwriting evidence.")
        return recorded
    files = sorted(src.glob("*.jsonl"))
    if not files:
        raise Refused(f"no *.jsonl session events under {src}")
    dst.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, dst / f.name)
    manifest = _build_manifest(dst, source=str(src))
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


def _build_manifest(directory: Path, *, source: str) -> dict:
    rows = []
    for f in sorted(directory.glob("*.jsonl")):
        digest, size = _sha256_file(f)
        rows.append({"name": f.name, "bytes": size,
                     "lines": _count_lines(f), "sha256": digest})
    manifest = {
        "schema": SNAPSHOT_SCHEMA,
        "source": source,
        "files": rows,
        "file_count": len(rows),
        "total_bytes": sum(r["bytes"] for r in rows),
        "total_lines": sum(r["lines"] for r in rows),
    }
    manifest["digest"] = snapshot_digest(manifest)
    return manifest


def verify_snapshot(path: Path) -> dict:
    """Load a snapshot's manifest and re-derive it; refuse on any mismatch."""
    path = Path(path).expanduser()
    manifest_path = path / "MANIFEST.json"
    if not manifest_path.exists():
        raise Refused(
            f"{path} holds no MANIFEST.json — every arm must read a FROZEN "
            f"snapshot, never the live session-events directory")
    recorded = json.loads(manifest_path.read_text())
    if recorded.get("schema") != SNAPSHOT_SCHEMA:
        raise Refused(f"{manifest_path} is not a {SNAPSHOT_SCHEMA} manifest")
    current = _build_manifest(path, source=recorded.get("source", ""))
    if current["digest"] != recorded["digest"]:
        raise Refused(
            f"the snapshot at {path} was MODIFIED after it was frozen "
            f"({recorded['digest'][:16]} → {current['digest'][:16]}) — the two "
            f"arms would not be reading one corpus")
    return recorded


def read_records(snapshot: Path) -> list[dict]:
    """Every text-bearing session record in the frozen snapshot, in file order.

    The parse rule is `candidate_recall_11901.harvest`'s, kept in step with it on
    purpose: a record with no textual turn contributes no name and no item, and
    dropping it here without a count would make the population unrecomputable.

    ⛔ TURNS ARE KEPT, not just the joined text. The first version of this file
    joined every turn into one blob and used the blob as the query; measured at
    n=40 the anchor resolution then resolved **zero** anchors for 37 of 40 items
    (a 500-character record's OR union does not rank one Object name in a 20-row
    window), so the mechanism never fired and the run was a null about the
    QUERY SHAPE rather than about the lever. A capture question is a turn, so a
    turn is what is queried.
    """
    records: list[dict] = []
    for f in sorted(Path(snapshot).glob("*.jsonl")):
        for lineno, line in enumerate(f.open(errors="replace")):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            turns = [{"role": str(t.get("role") or ""),
                      "content": t.get("content")}
                     for t in (rec.get("conversation") or [])
                     if isinstance(t.get("content"), str)]
            if not turns:
                continue
            records.append({
                "session_id": str(rec.get("session_id") or f"file:{f.name}:{lineno}"),
                "turns": turns,
                "text": "\n".join(t["content"] for t in turns),
                "file": f.name,
                "lineno": lineno,
            })
    for r in records:
        r["text_lower"] = " ".join(r["text"].lower().split())
        for t in r["turns"]:
            t["lower"] = " ".join(t["content"].lower().split())
    return records


def _mention_turn(rec: dict, needle: str, *, query_chars: int,
                  prefer_role: str = "user") -> str | None:
    """The turn that NAMES the entity — the question a capture actually asks.

    Prefers the ``user`` role (the human's own words are what a retrieval query
    is built from), then the longest mentioning turn, then the record text. Ties
    break on turn index, so the choice is a function of the frozen bytes.
    """
    hits = [i for i, t in enumerate(rec["turns"]) if needle in t["lower"]]
    if not hits:
        return None
    user = [i for i in hits if rec["turns"][i]["role"] == prefer_role]
    pool = user or hits
    best = max(pool, key=lambda i: (len(rec["turns"][i]["content"]), -i))
    return rec["turns"][best]["content"][:query_chars]


# ── the sample, drawn ONCE ───────────────────────────────────────────────────
def _allocate_shares(sample: int, pop: list[dict]) -> dict[str, int]:
    """Largest-remainder class shares. Reused from `candidate_recall_11901` so the
    stratified sample here is the canonical one, not a second convention."""
    from tools.candidate_recall_11901 import _allocate_shares as _shared
    return _shared(sample, pop)


def draw_population(harvested: dict[str, collections.Counter], *, population: int,
                    sample: int, seed: int) -> list[dict]:
    """Population, then a SEEDED stratified sample of it — drawn exactly once.

    ⛔ The sample is drawn, not head-sliced. `candidate_recall_11901` takes the
    top-``n`` by frequency, which is deterministic but biases the items toward the
    most repeated names; a paired design wants items that represent the class, and
    a recorded seed makes the draw reproducible without that bias. The seed is in
    the population file, so a re-run does not need to be told it.
    """
    from tools.candidate_recall_11901 import build_population
    pop = build_population(harvested, population)
    if not pop:
        raise Refused("the harvest produced no names — nothing to sample")
    if sample > len(pop):
        raise Refused(f"--sample {sample} exceeds the population ({len(pop)})")
    by_cls: dict[str, list[dict]] = collections.defaultdict(list)
    for row in pop:
        by_cls[row["cls"]].append(row)
    shares = _allocate_shares(sample, pop)
    if any(shares.get(c, 0) == 0 for c in by_cls):
        raise Refused(f"--sample {sample} starves a class ({shares}) — a per-class "
                      f"rate needs at least one name per class")
    rng = random.Random(seed)
    out: list[dict] = []
    for cls in sorted(by_cls):
        pool = sorted(by_cls[cls], key=lambda r: (-r["records"], r["name"]))
        take = shares[cls]
        out.extend(rng.sample(pool, take) if take < len(pool) else pool)
    # Deterministic ITEM order: sorted by (cls, name) so the graph write order and
    # the per-item id sequence are a function of the sample, not of dict order.
    out.sort(key=lambda r: (r["cls"], r["name"]))
    for i, row in enumerate(out):
        row["item_id"] = f"item-{i:06d}"
    return out


def population_body(rows: list[dict], *, snapshot_digest_: str, population: int,
                    sample: int, seed: int) -> dict:
    names = "\n".join(r["name"] for r in rows)
    return {
        "schema": POPULATION_SCHEMA,
        "snapshot_digest": snapshot_digest_,
        "population": population,
        "sample": sample,
        "seed": seed,
        "names_sha256": _sha256_bytes(names.encode()),
        "items": rows,
    }


def load_or_make_population(path: Path, *, harvested, snapshot_digest_: str,
                            population: int, sample: int, seed: int) -> tuple[list[dict], dict]:
    """Read the drawn sample if it exists, else draw and write it.

    ⛔ The mismatch check is the falsifier "both arms use the same items". A
    population file drawn against a DIFFERENT frozen corpus would silently pair
    item ids whose names come from another corpus — so a snapshot or name digest
    that does not recompute is refused rather than reused.

    ⛔ And a file drawn at a different SAMPLE / POPULATION / SEED is refused too
    (`#13267` review 6 F5): only the snapshot and name digests used to be checked,
    so `--sample 300 --seed 20261117` pointed at a stale `population-<n>.json`
    silently measured the OTHER sample while `construction` — written from argv —
    went on saying 300/20261117. Every field a reader consults for "what did this
    run measure" then describes a run that did not happen. The file's own triple
    is what the run actually used, so a disagreement is a refusal, never a
    preference for one of the two.
    """
    path = Path(path).expanduser()
    if path.exists():
        body = json.loads(path.read_text())
        if body.get("schema") != POPULATION_SCHEMA:
            raise Refused(f"{path} is not a {POPULATION_SCHEMA} file")
        if body["snapshot_digest"] != snapshot_digest_:
            raise Refused(
                f"{path} was drawn against snapshot {body['snapshot_digest'][:16]}, "
                f"but this run's frozen corpus is {snapshot_digest_[:16]} — the "
                f"items would not be the same population")
        recomputed = _sha256_bytes(
            "\n".join(r["name"] for r in body["items"]).encode())
        if recomputed != body["names_sha256"]:
            raise Refused(f"{path} is internally inconsistent (names digest)")
        for field, wanted in (("sample", sample), ("population", population),
                              ("seed", seed)):
            if body.get(field) != wanted:
                raise Refused(
                    f"{path} was drawn with --{field.replace('sample', 'sample')} "
                    f"{body.get(field)!r}, but this run asks for {wanted!r} — the "
                    f"file's items are the ones the run would measure, so "
                    f"`construction.{field}` (written from argv) would describe a "
                    f"sample this run did not take. Re-draw to a new path.")
        return body["items"], body
    rows = draw_population(harvested, population=population, sample=sample, seed=seed)
    body = population_body(rows, snapshot_digest_=snapshot_digest_,
                           population=population, sample=sample, seed=seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True))
    return rows, body


# ── items: the class the mechanism targets ───────────────────────────────────
def _salient_tokens(text: str, limit: int) -> list[str]:
    """The record's own top-``limit`` tokens, deterministically ordered.

    This is the stand-in for the E3 keys a real capture writes (``search_keys`` =
    2–4 aliases + verbatim tokens). Frequency-then-alphabetical, so two runs over
    the frozen snapshot produce one key string.
    """
    from tortoise.sparse import tokenize_sparse_query
    toks = tokenize_sparse_query(text)
    counts = collections.Counter(toks)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [t for t, _ in ranked[:limit]]


def build_items(rows: list[dict], records: list[dict], *, query_chars: int,
                key_tokens: int, min_query_tokens: int) -> tuple[list[dict], dict]:
    """One constructed item per sampled name — a cross-session, key-only join.

    Per name N: a QUERY record (a session that mentions N) and a TARGET record in
    a DIFFERENT session that does NOT mention N at all. The target is linked to the
    anchor by ``aboutObject`` and carries ``search_keys`` — so the sparse leg finds
    it only through the harvest. That is `#2518`'s stated class verbatim
    ("a same-subject point from a session the one-shot top-k starves", proven by
    `tests/test_entity_key_expansion.py`).

    ⛔ Declared, not hidden: the target's ``search_keys`` are its own salient
    tokens, so the harvest that reaches it carries terms derived from the target
    itself. That is what the SHIPPED mechanism does (the pool is the linked
    points' keys), so the measured lift is the mechanism's ceiling under a clean
    key set — not production's noisy E3 keys. It is recorded as
    ``construction.keys_source`` so a reader cannot mistake the lift for a blind
    expansion's.
    """
    from tortoise.sparse import tokenize_sparse_query
    items: list[dict] = []
    drop = collections.Counter()
    # ⛔ PRECOMPUTE each record's token set ONCE. The first version computed it
    # inside the target-selection ``min(key=...)``, i.e. once per CANDIDATE per
    # item — 300 items x ~14,000 records = 4.2 M tokenizations of a ~1 KB span.
    # Measured: the tool sat at 100 % CPU for 55 minutes and had not yet seeded
    # its graph. One pass over the records is ~15 s; the same code is now linear
    # in the corpus instead of linear in corpus x sample.
    from tortoise.sparse import tokenize_sparse_query as _tok
    for rec in records:
        rec["tokens"] = set(_tok(rec["text"][:4000]))
    for row in rows:
        name = row["name"]
        needle = " ".join(name.lower().split())
        mentioned = [i for i, rec in enumerate(records) if needle in rec["text_lower"]]
        if not mentioned:
            drop["no_mention"] += 1
            continue
        sessions = {records[i]["session_id"] for i in mentioned}
        if len(sessions) < 2:
            drop["single_session"] += 1
            continue
        # QUERY record: the mention whose turn gives the best question — the
        # longest mentioning user turn anywhere in the record. Deterministic.
        best: tuple[int, str] | None = None
        for i in mentioned:
            cand = _mention_turn(records[i], needle, query_chars=query_chars)
            if cand is None:
                continue
            if best is None or len(cand) > len(best[1]):
                best = (i, cand)
        if best is None:
            drop["no_mention_turn"] += 1
            continue
        q_idx, query = best
        q_rec = records[q_idx]
        q_tokens = set(tokenize_sparse_query(query))
        if len(q_tokens) < min_query_tokens:
            drop["query_too_short"] += 1
            continue
        # TARGET: a different session that does NOT contain the name and shares the
        # fewest query tokens — the starved class by construction.
        candidates = [i for i, rec in enumerate(records)
                      if rec["session_id"] != q_rec["session_id"]
                      and needle not in rec["text_lower"]]
        if not candidates:
            drop["no_other_session"] += 1
            continue
        t_idx = min(candidates,
                    key=lambda i: (len(records[i]["tokens"] & q_tokens),
                                   -len(records[i]["text"]), i))
        t_rec = records[t_idx]
        target_text = t_rec["turns"][0]["content"][:query_chars]
        keys = _salient_tokens(target_text, key_tokens)
        if not keys:
            drop["target_no_tokens"] += 1
            continue
        items.append({
            "item_id": row["item_id"],
            "name": name,
            "cls": row["cls"],
            "query": query,
            "query_session": q_rec["session_id"],
            "target_text": target_text,
            "target_session": t_rec["session_id"],
            "target_keys": " ".join(keys),
            "target_overlap_with_query": len(
                set(tokenize_sparse_query(target_text)) & q_tokens),        })
    return items, dict(drop)


def pick_distractors(items: list[dict], records: list[dict], n: int,
                     *, query_chars: int) -> list[dict]:
    """Real corpus records that are neither a query nor a target, as competition.

    Deterministic: evenly strided over the record list, so the pool is a spread of
    the corpus rather than its first day-file. Their content carries no
    ``aboutObject`` link, so they compete for the window exactly as unrelated
    points do.
    """
    used = {it["query"] for it in items} | {it["target_text"] for it in items}
    pool = [r for r in records
            if " ".join(r["text"][:query_chars].split()) not in used]
    if n <= 0 or not pool:
        return []
    stride = max(1, len(pool) // n)
    picked = pool[::stride][:n]
    return [{"content": " ".join(r["text"][:query_chars].split()),
             "session_id": r["session_id"]} for r in picked]


# ── the graph: written through the real projection ───────────────────────────
def _point_id(kind: str, key: str) -> str:
    """A canonical ULID-SHAPED id that is a pure function of ``key``.

    ⛔ Both halves matter. Non-canonical ids make ``create_point`` log a warning
    per write (500 distractors = 500 lines of stderr noise around the result),
    and RANDOM ids would let an id tie-break move a rank between two runs of the
    frozen corpus — which would break the ``repeat`` determinism check that this
    tool's falsifiers rest on.
    """
    return f"{0x019000000000 + int(hashlib.sha256(kind.encode()).hexdigest()[:6], 16):012x}-{hashlib.sha256((kind + ':' + key).encode()).hexdigest()[:12]}"


def seed_graph(sdk, items: list[dict], distractors: list[dict]) -> dict:
    """Write the anchors, the two linked points per item, and the competition.

    Every write goes through the SDK's production doors (``create_entity`` /
    ``create_point``), never a hand-built Cypher node, so the index text the read
    searches is the text the product would have written.
    """
    proj = sdk._get_proj()
    for it in items:
        proj._upsert_object({"id": f"o-{it['item_id']}",
                             "name": it["name"],
                             "object_kind": "core:other"})
        it["object_id"] = f"o-{it['item_id']}"
        it["query_point_id"] = _point_id("q", it["item_id"])
        it["target_point_id"] = _point_id("t", it["item_id"])
    for it in items:
        sdk.create_point("statement", it["query"],
                         id=it["query_point_id"], session_id=it["query_session"],
                         search_keys=it["name"], status="draft")
        sdk.create_point("statement", it["target_text"],
                         id=it["target_point_id"], session_id=it["target_session"],
                         search_keys=it["target_keys"], status="draft")
    for i, d in enumerate(distractors):
        sdk.create_point("statement", d["content"], id=_point_id("d", f"{i:06d}"),
                         session_id=d["session_id"], status="draft")
    # One batched edge write per item — the cross-session spine join the harvest
    # walks. MERGE, not CREATE, so a re-run onto a reused graph cannot double it.
    for it in items:
        proj.g.query(
            "MATCH (p:Point), (o:Object {id:$o}) WHERE p.id IN $ids "
            "MERGE (p)-[:aboutObject]->(o)",
            params={"o": it["object_id"],
                    "ids": [it["query_point_id"], it["target_point_id"]]})
    return {"anchors": len(items), "linked_points": 2 * len(items),
            "distractors": len(distractors),
            "points_total": 2 * len(items) + len(distractors)}


def _trace_provenance(trace: list[dict] | None) -> dict:
    """Count the expansion's own trace entries — the 'the lever fired' evidence."""
    prov = collections.Counter()
    for entry in trace or []:
        p = entry.get("provenance")
        if p:
            prov[str(p)] += 1
    return dict(prov)


def _mode_of(trace: list[dict] | None) -> str:
    """`require_hybrid_read`'s strict per-window verdict (the #2952 predicate)."""
    from tortoise.exceptions import HybridReadUnavailableError
    from tortoise.search_engine import require_hybrid_read
    try:
        require_hybrid_read(trace)
    except HybridReadUnavailableError:
        return "keyword_only"
    return "hybrid"


def _rank_of(ids: list[str], target: str) -> int | None:
    try:
        return ids.index(target)
    except ValueError:
        return None


def _ids_digest(ids: list[str] | None) -> str | None:
    """sha256 over a ranked list, or None when no list was recorded.

    Recorded per item per side so the ON-vs-OFF identity is checkable FROM THE
    ARTIFACT. ``None`` is kept distinct from the digest of an EMPTY list — a
    recorded empty read ("the query matched nothing") and an unrecorded one are
    different facts, and conflating them is how `None == None` passed for
    evidence.
    """
    if ids is None:
        return None
    return _sha256_bytes("\n".join(ids).encode())


def _read_delta(per_item_off: dict, per_item_on: dict,
                item_ids: list[str]) -> dict:
    """The ON-vs-OFF read delta, item by item — the only "did the lever fire" signal.

    ⛔ A side whose ranked list was NOT recorded is REFUSED, never scored.
    Scoring it as `(ids or [])` made `[] == []` for every item on an arm that
    recorded no ids, so the ON side was reported as producing "the identical
    ranked list for all N items" from `None == None` — a constant dressed as a
    measurement, moved by nothing the retrieval code does (`#13267` F1). The
    refusal is the fix: an arm with no ranked list has no firing evidence, and
    saying so is the only honest report.
    """
    fired: list[str] = []
    identical: list[str] = []
    gained = 0
    unmeasured: list[str] = []
    for iid in item_ids:
        off_ids = per_item_off[iid]["ids"]
        on_ids = per_item_on[iid]["ids"]
        if off_ids is None or on_ids is None:
            unmeasured.append(iid)
            continue
        if off_ids != on_ids:
            fired.append(iid)
        else:
            identical.append(iid)
        if len(on_ids) > len(off_ids):
            gained += 1
    if unmeasured:
        raise Refused(
            f"{len(unmeasured)} of {len(item_ids)} items recorded NO ranked list "
            "on one or both sides — the ON==OFF identity is UNMEASURED for this "
            "arm, not true. Record `ids` for every arm (recording them for one "
            "arm only is how this claim became `None == None`, #13267 F1)")
    return {"fired": fired, "identical": identical,
            "items_whose_read_grew": gained, "compared": len(item_ids)}


def _exit_code(refusals: list[tuple[str, str]], *,
               allow_mixed_modes: bool) -> int:
    """0 when every refusal is excused, else 3 (the artifact is already written).

    ⛔ ``--allow-mixed-modes`` excuses ONLY a mixed window-mode refusal — the
    contamination its name describes. A structurally-blind run and an inert
    harvest stay refusals under it: those are the properties the tool exists to
    refuse, and a flag about modes silently un-refusing them is how a null gets
    published as a result (`#13267` F7).
    """
    blocking = [msg for kind, msg in refusals
                if kind != MODE_REFUSAL or not allow_mixed_modes]
    if not blocking:
        return 0
    print("\nREFUSED to publish a verdict: " + "; ".join(blocking) +
          "\n  (the artifact is still written so the evidence is not lost)",
          file=sys.stderr)
    return 3


def read_arm(sdk, items: list[dict], *, arm: str, expansion: bool,
             read_limit: int, dedupe_queries: bool = True) -> dict:
    """One read per item at ``read_limit``; every k is derived as a prefix.

    ``arm`` is ``"point"`` (the leg the lever arms) or ``"object"`` (the leg the
    published comparison measured). The object arm queries the NAME as the
    capture prior does; the point arm queries the item's own capture span.
    """
    from tortoise.extractor_v2 import _sanitise_query_text
    per_item: dict[str, dict] = {}
    modes = collections.Counter()
    prov = collections.Counter()
    reads = 0
    for it in items:
        trace: list[dict] = []
        if arm == "point":
            query = it["query"]
            target = it["target_point_id"]
        else:
            query = _sanitise_query_text(it["name"])
            target = it["object_id"]
        rows = sdk.tortoise_fts_query(
            query, entity_type=arm, limit=read_limit,
            include_terminal=True, _record_reads=False,
            _valid_time_rerank=False, leg_trace=trace,
            entity_key_expansion=expansion)
        reads += 1
        ids = [str(r.get("id", "")) for r in (rows or [])]
        modes[_mode_of(trace)] += 1
        for k, v in _trace_provenance(trace).items():
            prov[k] += v
        per_item[it["item_id"]] = {
            "rank": _rank_of(ids, target),
            # ⛔ THE SEED IS REAL GOLD. The item's own query point matches the
            # question by construction (its content IS the question), so its rank
            # is the one HARM signal available on an item set whose only other
            # labelled point is the target. `_entity_key_expansion_pass`'s merge
            # contract says the pass-1 seed is never dropped but CAN be demoted,
            # and `_entity_key_dense_pass` says so explicitly; this measures it
            # rather than quoting the docstring.
            "seed_rank": _rank_of(ids, it["query_point_id"]) if arm == "point" else None,
            # ⛔ RECORDED FOR EVERY ARM. This was `ids if arm == "point" else
            # None`, so ``(ids or []) == (ids or [])`` made the object arm's
            # "identical ranked list" a `None == None` constant that no retrieval
            # behaviour could move — the one claim the tool advertised as
            # measured (`#13267` F1). A ranked list is the cheapest datum here;
            # an arm without one has no firing evidence at all.
            "ids": ids,
            "provenance_entries": _trace_provenance(trace),
        }
    return {"per_item": per_item, "window_modes": dict(sorted(modes.items())),
            "provenance_counts": dict(sorted(prov.items())), "reads": reads}


# ── paired statistics (stdlib only) ──────────────────────────────────────────
def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p for the discordant counts ``b`` and ``c``.

    Exact (binomial, p=0.5) rather than the chi-square approximation because the
    discordant total is routinely < 25 in a paired IR ablation, which is exactly
    where the approximation's error is largest. ``b == c == 0`` is p = 1.0: no
    discordant pair carries no evidence of a difference — and no evidence of none.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1))
    return min(1.0, 2.0 * (tail / (2 ** n)))


def _norm_ppf(p: float) -> float:
    """Acklam's inverse normal CDF (|rel err| < 1.15e-9 over the open unit interval)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    lo = 0.02425
    if p < lo:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p > 1 - lo:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
                ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
           (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)


def detectable_effect(n: int, discordance: float, *,
                      alpha: float = 0.05, power: float = 0.8) -> float:
    """The smallest paired recall delta this ``n`` can detect, in absolute units.

    The standard normal approximation for paired proportions (Connor 1987):

        δ = ( z_{1-α/2}·√π + z_{power}·√(π − δ²) ) / √n,     π = P(discordant)

    solved by fixed-point iteration because δ appears on both sides.

    ⛔ This is REPORTED BESIDE the exact test, and it is NOT pre-stated: π is this
    run's own discordant share, which does not exist until both arms have been
    read, so a figure computed here cannot have been fixed before the outcome.
    (`#13267` review 4 F3: the docstring said "Stated BEFORE any verdict" three
    lines above "discordance is measured, not assumed", and the brief reported
    the row as MET on that basis.) What IS fixed in advance is ``n``; δ says what
    that n could resolve at 80 % power, so a delta BELOW it is an observation the
    design was not powered to resolve and is not reported as a winner. The
    independent-design figure below is computed for the same reason: the paired
    advantage lives entirely in π, so the unpaired comparison is shown too.
    """
    if n <= 0:
        return float("nan")
    if not 0.0 <= discordance <= 1.0:
        raise ValueError("discordance must be a probability")
    # ⛔ A ZERO discordance is NOT an infinitely powerful study. The plug-in form
    # would return 0 (⟹ "any difference is detectable"), which is the opposite of
    # the truth: with no discordant pair observed the run carries no information
    # about the difference at all. Measured on the object control, where the two
    # switch states are one code path and every item agrees — the naive reading
    # was MDE = 0.0000 printed beside a null. Undefined, and said so.
    if discordance == 0.0:
        return float("nan")
    za = _norm_ppf(1 - alpha / 2)
    zb = _norm_ppf(power)
    delta = max(1e-9, math.sqrt(discordance) * (za + zb) / math.sqrt(n))
    for _ in range(200):
        inner = max(0.0, discordance - delta * delta)
        nxt = (za * math.sqrt(discordance) + zb * math.sqrt(inner)) / math.sqrt(n)
        if abs(nxt - delta) < 1e-12:
            delta = nxt
            break
        delta = nxt
    return delta


def independent_effect(n: int, p_bar: float, *,
                      alpha: float = 0.05, power: float = 0.8) -> float:
    """The same MDE for TWO INDEPENDENT arms of size ``n`` — the shape of the
    published comparison. Reported beside the paired figure so the sample-size
    argument is arithmetic rather than assertion."""
    if n <= 0:
        return float("nan")
    if p_bar <= 0.0 or p_bar >= 1.0:
        # same defect as above: a floor/ceiling rate has zero variance, and a
        # zero MDE reads as "any effect detectable" when it means "no power".
        return float("nan")
    za = _norm_ppf(1 - alpha / 2)
    zb = _norm_ppf(power)
    var = 2.0 * p_bar * (1.0 - p_bar)
    return (za + zb) * math.sqrt(var) / math.sqrt(n)


def paired_bootstrap(off: list[int], on: list[int], *, resamples: int,
                     seed: int) -> dict:
    """Seeded percentile bootstrap over ITEMS (the pairing unit)."""
    n = len(off)
    if n == 0:
        return {"delta": float("nan"), "ci95": [float("nan")] * 2}
    diffs = [o - f for o, f in zip(on, off, strict=True)]
    delta = sum(diffs) / n
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        s = 0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * resamples)]
    hi = means[min(resamples - 1, int(0.975 * resamples))]
    return {"delta": delta, "ci95": [lo, hi],
            "resamples": resamples, "seed": seed}


def outcome_correlation(off: list[int], on: list[int]) -> dict:
    """The measured correlation between the two arms' per-item outcomes.

    This is the whole reason pairing buys power: for equal-variance binary
    outcomes the variance of the paired difference is ``2σ²(1−ρ)``, so a ρ of 0.9
    cuts the standard error by ~3.2× versus two independent arms of the same size.
    Reported as MEASURED, never assumed.
    """
    n = len(off)
    if n == 0:
        return {"rho": float("nan"), "se_reduction": float("nan")}
    mo = sum(off) / n
    mn = sum(on) / n
    cov = sum((a - mo) * (b - mn) for a, b in zip(off, on, strict=True)) / n
    so = math.sqrt(sum((a - mo) ** 2 for a in off) / n)
    sn = math.sqrt(sum((b - mn) ** 2 for b in on) / n)
    if so == 0 or sn == 0:
        return {"rho": float("nan"), "se_reduction": float("nan")}
    rho = cov / (so * sn)
    return {"rho": rho,
            "se_reduction": math.sqrt(max(0.0, 1.0 - rho))}


def analyze(off_hits: list[int], on_hits: list[int], *, ks: tuple[int, ...],
            bootstrap: int, seed: int) -> dict:
    """The paired comparison at every k, with the MDE stated beside the verdict."""
    out: dict = {}
    n = len(off_hits)
    b = sum(1 for o, f in zip(off_hits, on_hits, strict=True) if o == 0 and f == 1)
    c = sum(1 for o, f in zip(off_hits, on_hits, strict=True) if o == 1 and f == 0)
    p_bar = (sum(off_hits) + sum(on_hits)) / (2 * n) if n else float("nan")
    disc = (b + c) / n if n else float("nan")
    out.update({
        "n": n,
        "off_hits": sum(off_hits),
        "on_hits": sum(on_hits),
        "off_recall": sum(off_hits) / n if n else float("nan"),
        "on_recall": sum(on_hits) / n if n else float("nan"),
        "discordant_miss_to_hit": b,
        "discordant_hit_to_miss": c,
        "discordance": disc,
        "mcnemar_exact_p": mcnemar_exact(b, c),
        "bootstrap": paired_bootstrap(off_hits, on_hits,
                                      resamples=bootstrap, seed=seed),
        "correlation": outcome_correlation(off_hits, on_hits),
        "mde_paired": detectable_effect(n, disc),
        "mde_independent": independent_effect(n, p_bar),
        "mde_note": ("" if disc else
                     "NOT ESTIMABLE: no discordant pair was observed, so this n "
                     "carries no information about the difference — an MDE of 0 "
                     "here would read as 'infinitely powered', which is the "
                     "opposite of what an all-agree run means"),
    })
    return out


def _hits_at(per_item: dict, item_ids: list[str], *, k: int) -> list[int]:
    out = []
    for iid in item_ids:
        rank = per_item[iid]["rank"]
        out.append(1 if rank is not None and rank < k else 0)
    return out


def _seed_effect(per_item_off: dict, per_item_on: dict, item_ids: list[str],
                 *, k: int) -> dict:
    """Did the merged, re-ranked pass COST the plain best match its slot?

    ``off`` and ``on`` are the two arms' per-item records. Reported for the seed
    point only, because the seed is the one id in the graph whose relevance is
    known without a qrel.
    """
    demoted = lost = top1_off = top1_on = 0
    shift = 0
    for iid in item_ids:
        ro = per_item_off[iid]["seed_rank"]
        rn = per_item_on[iid]["seed_rank"]
        if ro is not None and ro == 0:
            top1_off += 1
        if rn is not None and rn == 0:
            top1_on += 1
        if ro is not None and ro < k and (rn is None or rn >= k):
            lost += 1
        elif ro is not None and rn is not None and rn > ro:
            demoted += 1
        if ro is not None and rn is not None:
            shift += rn - ro
    return {"seed_at_top1_off": top1_off, "seed_at_top1_on": top1_on,
            "seed_lost_from_window": lost, "seed_demoted_but_kept": demoted,
            "seed_rank_shift_sum": shift}


def _displacement(per_item_off: dict, per_item_on: dict, item_ids: list[str],
                  *, k: int) -> dict:
    """How much of the OFF top-k the merged, re-ranked pass moved out of the window."""
    lost = 0
    present = 0
    rank_shift = 0
    top1_kept = 0
    for iid in item_ids:
        off_ids = (per_item_off[iid]["ids"] or [])[:k]
        on_ids = (per_item_on[iid]["ids"] or [])[:k]
        if not off_ids:
            continue
        present += 1
        if off_ids[0] in on_ids:
            top1_kept += 1
        kept = [i for i in off_ids if i in on_ids]
        lost += len(off_ids) - len(kept)
        rank_shift += sum(abs(on_ids.index(i) - off_ids.index(i)) for i in kept)
    return {"items_with_off_topk": present, "displaced_ids": lost,
            "off_top1_kept_in_window": top1_kept,
            "abs_rank_shift_of_kept": rank_shift,
            "note": ("CHURN, not harm, unless the ids carry qrels — the OFF "
                     "top-k here is mostly unrelated competition, so this counts "
                     "how much the window moved, not what was lost (see the "
                     "seed_effect block for the one labelled harm signal)")}


def _load() -> dict:
    try:
        la = os.getloadavg()
    except OSError:
        la = (float("nan"),) * 3
    return {"load_1m": la[0], "load_5m": la[1], "load_15m": la[2],
            "cpus": os.cpu_count()}


# ── the run ──────────────────────────────────────────────────────────────────
def run(args: argparse.Namespace) -> int:
    # ⛔ ONE input source, enforced HERE too — `main()`'s mutually exclusive group
    # only covers the argv path, and `run()` is the layer that actually chooses
    # which bytes the arms read. With both set, the digest/file-list came from the
    # `--freeze` target while `read_records` read the `--snapshot` directory on
    # which `verify_snapshot` never ran: an artifact attesting "this corpus, byte
    # for byte" over a corpus the run never measured (`#13267` F2). A directly
    # built Namespace must fail closed, not compose.
    if args.freeze and args.snapshot:
        raise Refused(
            "--freeze and --snapshot both select the corpus, and exactly one may: "
            "the manifest would otherwise attest the frozen copy while the arms "
            "read the reused snapshot. Pass one (they are mutually exclusive in "
            "`main()`).")

    from tortoise.sdk import _entity_key_dense_enabled

    # ⛔ BEFORE anything else: the dense half would be a SECOND, conflated lever.
    if _entity_key_dense_enabled():
        raise Refused(
            "TORTOISE_ENTITY_KEY_DENSE is armed — that is the DENSE half of the "
            "same expansion, and toggling it here would report two levers as one. "
            "Unset it and re-run.")

    uri = os.environ.get("TORTOISE_DB_URI") or ""
    if not uri.startswith(("docker://", "redis://")):
        raise Refused(
            "no live FalkorDB in TORTOISE_DB_URI — an FTS mode measured on a "
            "backend the product cannot use is not a figure.\n"
            "  uri=\"$(uv run python tools/test_lane.py uri)\" && eval \"$uri\"")

    from tools.candidate_recall_11901 import harvest
    from tortoise.sdk import TortoiseSDK

    t0 = time.time()
    src = Path(os.path.expanduser(args.sessions_dir))
    if args.freeze:
        manifest = freeze_snapshot(src, Path(args.freeze))
    else:
        if not args.snapshot:
            raise Refused("one of --freeze (copy now) or --snapshot (reuse a "
                          "frozen copy) is required — arms must not read the "
                          "live directory")
        manifest = verify_snapshot(Path(args.snapshot))
    # Exactly one of the two is set (argparse's group + the guard at the top of
    # `run`), so this is the same directory the manifest above attests.
    snapshot_path = Path(args.freeze if args.freeze else args.snapshot)
    snap_digest = manifest["digest"]

    _input: dict = {}
    harvested = harvest(snapshot_path, stats=_input)
    pop_file = Path(args.population_file or
                    (snapshot_path / f"population-{args.sample}.json"))
    rows, pop_body = load_or_make_population(
        pop_file, harvested=harvested, snapshot_digest_=snap_digest,
        population=args.population, sample=args.sample, seed=args.seed)

    _log("snapshot", digest=snap_digest[:16], files=manifest["file_count"],
         lines=manifest["total_lines"])
    _log("population", names=len(rows), file=str(pop_file))
    records = read_records(snapshot_path)
    _log("records", n=len(records))
    items, dropped = build_items(
        rows, records, query_chars=args.query_chars,
        key_tokens=args.key_tokens, min_query_tokens=args.min_query_tokens)
    if len(items) < args.min_items:
        raise Refused(
            f"only {len(items)} of {len(rows)} sampled names formed an item "
            f"(dropped: {dropped}) — --min-items {args.min_items} is the floor "
            f"below which a paired comparison cannot say anything")
    _log("items", with_item=len(items), dropped=dropped)
    distractors = pick_distractors(items, records, args.distractors,
                                   query_chars=args.query_chars)

    graph_name = args.graph or f"paired2518_{int(time.time())}_{os.getpid()}"
    load_start = _load()
    sdk = TortoiseSDK(namespace=args.namespace, graph_name=graph_name)
    artifact: dict = {}
    try:
        seeded = seed_graph(sdk, items, distractors)
        _log("seeded", **seeded)
        # One warm read so the first measured query is not paying a cold cost.
        sdk.tortoise_fts_query("warm", entity_type="point", limit=1,
                               include_terminal=True, _record_reads=False,
                               _valid_time_rerank=False)
        item_ids = [it["item_id"] for it in items]
        arms: dict[str, dict] = {}
        for arm in args.arms:
            off = read_arm(sdk, items, arm=arm, expansion=False,
                           read_limit=args.read_limit)
            _log(f"arm {arm} OFF done", modes=off["window_modes"])
            on = read_arm(sdk, items, arm=arm, expansion=True,
                          read_limit=args.read_limit)
            _log(f"arm {arm} ON done", modes=on["window_modes"])
            arms[arm] = {"off": off, "on": on}
            if args.repeat_arm == arm:
                again = read_arm(sdk, items, arm=arm, expansion=False,
                                 read_limit=args.read_limit)
                arms[arm]["repeat"] = again
        load_end = _load()
        graph_ok = int(sdk._get_proj().g.query(
            "MATCH (n) RETURN count(n)").result_set[0][0])
        artifact = _build_artifact(
            args, manifest, pop_body, items, dropped, seeded, arms, item_ids,
            load_start=load_start, load_end=load_end, graph_nodes=graph_ok,
            elapsed=time.time() - t0, pop_file=pop_file)
    finally:
        sdk.close()

    out = Path(os.path.expanduser(args.out)) if args.out else None
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(artifact, indent=2, sort_keys=True))
    _print_summary(artifact)
    # ⛔ Rebuilt from the artifact's own two parallel lists, so the exit rule reads
    # the CLASS of each refusal and `--allow-mixed-modes` can be scoped to the
    # one it names (`#13267` F7).
    return _exit_code(list(zip(artifact.get("refusal_kinds", []),
                               artifact["refusals"], strict=True)),
                      allow_mixed_modes=args.allow_mixed_modes)


def _build_artifact(args, manifest, pop_body, items, dropped, seeded, arms,
                    item_ids, *, load_start, load_end, graph_nodes, elapsed,
                    pop_file) -> dict:
    ks = [k for k in KS if k <= args.read_limit]
    out_arms: dict = {}
    # (class, message) — the class is what an opt-out is scoped to, the message is
    # what the artifact publishes.
    refusals: list[tuple[str, str]] = []
    for arm, blocks in arms.items():
        off, on = blocks["off"], blocks["on"]
        per_k: dict = {}
        for k in ks:
            off_hits = _hits_at(off["per_item"], item_ids, k=k)
            on_hits = _hits_at(on["per_item"], item_ids, k=k)
            entry = analyze(off_hits, on_hits, ks=KS, bootstrap=args.bootstrap,
                            seed=args.seed)
            if arm == "point":
                entry["off_topk_displacement"] = _displacement(
                    off["per_item"], on["per_item"], item_ids, k=k)
                entry["seed_effect"] = _seed_effect(
                    off["per_item"], on["per_item"], item_ids, k=k)
            per_k[f"k={k}"] = entry
        modes = {"off": off["window_modes"], "on": on["window_modes"]}
        for side, counter in modes.items():
            if len(counter) > 1:
                refusals.append((MODE_REFUSAL,
                                 f"arm {arm}/{side} has MIXED window modes "
                                 f"{counter} — a comparison across modes is "
                                 f"contaminated by the leg, not the lever"))
        # ⛔ The determinism check compares the READ, not the target's RANK.
        # `#13267` review 4 F2: it used to be
        #
        #     repeat["per_item"][i]["rank"] == off["per_item"][i]["rank"]
        #
        # and on the POINT arms the target's rank is `None` for every item (the
        # target is selected to be lexically unreachable), so the check was
        # `None == None` 300 times — a constant no retrieval behaviour can move,
        # published as "the repeat returned identical outcomes for all 300 items".
        # It could not see a re-ordered window or a different id set, only a
        # change in target PRESENCE. The ranked lists ARE recorded per side
        # (`_ids_digest`), so that is what is compared — and where a side has no
        # recorded list the check is UNMEASURED (`None`), never a vacuous `True`.
        repeat = blocks.get("repeat")
        repeat_identical = None
        repeat_compared = 0
        if repeat is not None:
            pairs = [(repeat["per_item"][i]["ids"], off["per_item"][i]["ids"])
                     for i in item_ids]
            if any(a is None or b is None for a, b in pairs):
                repeat_identical = None
            else:
                repeat_identical = all(
                    _ids_digest(a) == _ids_digest(b) for a, b in pairs)
                repeat_compared = len(pairs)
        prov_off = off["provenance_counts"].get(EXPANSION_PROVENANCE, 0)
        prov_on = on["provenance_counts"].get(EXPANSION_PROVENANCE, 0)
        # ⛔ "Did the lever fire?" must NOT be read off ``provenance``: ONLY the
        # DENSE half tags its trace entries (`TortoiseSDK._entity_key_dense_pass`
        # passes ``provenance=PROVENANCE_ENTITY_KEY_EXPANSION``); the SPARSE half
        # (`_entity_key_expansion_pass`) forwards `run_fts_query`'s own entries,
        # which carry `leg/ran/degraded/reason/count` and no provenance at all.
        # Measured: a point-arm run in which the expansion demonstrably changed
        # the answer reported ``provenance_entries: 0`` on BOTH sides. So the
        # firing signal is the READ ITSELF — compared, item by item, by
        # `_read_delta`, which REFUSES a side whose ranked list was not recorded.
        delta = _read_delta(off["per_item"], on["per_item"], item_ids)
        fired, identical = delta["fired"], delta["identical"]
        # The object arm is gated out of the mechanism at the call site
        # (`entity_key_expansion and entity_type == "point"`), so an element-wise
        # identity on EVERY item is structural, not an effect size. ⛔ The
        # identity must still be MEASURED — `_read_delta` guarantees that — or
        # this is a claim about the arm's name.
        structurally_blind = (arm != "point" and len(fired) == 0)
        # ⛔ Whether the identity is a MEASUREMENT or a call-site assertion, said
        # by the tool rather than inferred by a reader from a `null` count. The
        # committed artifacts had to carry this field by hand because no version
        # of this tool emitted it, which is how a re-generation came to fail the
        # doc pins that documented it (`#13267` F5).
        identity_measured = delta["compared"] > 0
        out_arms[arm] = {
            "structurally_blind": structurally_blind,
            "why": ("the two switch states produced the SAME ranked list for all "
                    f"{delta['compared']} items (element-wise, recorded ids on "
                    f"both sides), and this arm is `{arm}` — the expansion is "
                    "armed only for `entity_type == 'point'` at the call site, "
                    "so the identity is the GATE, not a null effect"
                    if structurally_blind else ""),
            "expansion_fired_items": len(fired),
            "expansion_identical_items": len(identical) if identity_measured else None,
            "items_whose_read_grew": delta["items_whose_read_grew"],
            # How many items actually carry a compared ranked list — 0 would
            # mean the field above is not a measurement (see `_read_delta`).
            "ranked_lists_compared": delta["compared"],
            "identity_measured": identity_measured,
            "identity_note": "" if identity_measured else (
                "UNMEASURED: at least one side recorded no ranked list, so "
                "`expansion_identical_items` is null — the identity is a "
                "call-site fact here, not something this run measured"),
        }
        # ⛔ What this run's `per_item` rows make RE-DERIVABLE from the artifact
        # alone. `--no-include-per-item` omits the rows entirely, so nothing is.
        has_per_item = bool(args.include_per_item) and bool(item_ids)
        _has_ids_digests = has_per_item and all(
            off["per_item"][i]["ids"] is not None
            and on["per_item"][i]["ids"] is not None for i in item_ids)
        # ⛔ key PRESENCE is not the datum: an artifact can carry `seed_rank` with
        # every value `None` (measured: the object arm declared `seed_effect`
        # recomputable while publishing no `seed_effect` cell at all and a null
        # rank on every item). Require a REAL rank on at least one item per side,
        # and gate the figure on the arm that actually publishes it.
        _has_seed_ranks = (
            has_per_item and arm == "point"
            and any(off["per_item"][i].get("seed_rank") is not None
                    for i in item_ids)
            and any(on["per_item"][i].get("seed_rank") is not None
                    for i in item_ids))
        _PER_ITEM_DERIVED = (
            "expansion_fired_items", "expansion_identical_items",
            "ranked_lists_compared", "seed_effect",
        )
        _recomputable = [name for name, datum in (
            ("expansion_fired_items", _has_ids_digests),
            ("expansion_identical_items", _has_ids_digests),
            ("ranked_lists_compared", _has_ids_digests),
            ("seed_effect", _has_seed_ranks),
        ) if datum]
        out_arms[arm].update({
            "expansion_provenance_entries": {"off": prov_off, "on": prov_on},
            "window_modes": modes,
            "repeat_identical": repeat_identical,
            # the basis is part of the record: a `true` beside zero compared
            # lists is the `None == None` constant this field used to publish
            "repeat_check": {
                "basis": REPEAT_BASIS_DIGEST,
                "compared": repeat_compared,
                "identical": repeat_identical,
                "note": "" if repeat_compared else (
                    "UNMEASURED: no repeat block, or a side recorded no ranked "
                    "list — the target's rank alone cannot show a re-ordered "
                    "window, so no determinism is claimed"),
            },
            # ⛔ WHICH derived figures this artifact makes RE-DERIVABLE, and which
            # it merely reports. A figure in neither list is a free parameter; a
            # figure in `derived_unpinned` cannot be recomputed from `per_item` and
            # is therefore not protected by recomputation (`#13267` review 5 F1).
            # ⛔ Every derived figure is classified as EITHER recomputable from
            # `per_item` OR unpinned — never NEITHER. A figure in neither list is a
            # free parameter, which is the defect this block exists to kill, and the
            # classification is built from one list so a new figure cannot be
            # forgotten in one of the two.
            "derived_recomputable": _recomputable,
            "derived_unpinned": (
                ["items_whose_read_grew", "off_topk_displacement"]
                + [n for n in _PER_ITEM_DERIVED if n not in _recomputable]),
            "derived_note": (
                "`off_topk_displacement` is NOT RE-DERIVABLE: it needs "
                "the ranked-list IDS and `per_item` records only their digests. "
                "Everything in `derived_recomputable` IS recomputed from "
                "`per_item` by the suite."),
            "per_k": per_k,
            "reads": {"off": off["reads"], "on": on["reads"]},
            "per_item": {iid: {
                "off_rank": off["per_item"][iid]["rank"],
                "on_rank": on["per_item"][iid]["rank"],
                # ⛔ The SEED's rank per side, so `seed_effect` is re-derivable
                # from the artifact instead of being a free parameter that only
                # the brief/artifact comparison can catch (`#13267` review 5 F1:
                # `seed_lost_from_window: 3 -> 0` in both files SURVIVED).
                "off_seed_rank": off["per_item"][iid]["seed_rank"],
                "on_seed_rank": on["per_item"][iid]["seed_rank"],
                # ⛔ A DIGEST of each side's ranked list, so the ON==OFF claim is
                # checkable FROM THE ARTIFACT rather than only from the tool's
                # own summary counts (`#13267` F1).
                "off_ids_digest": _ids_digest(off["per_item"][iid]["ids"]),
                "on_ids_digest": _ids_digest(on["per_item"][iid]["ids"]),
                "provenance_off": off["per_item"][iid]["provenance_entries"],
                "provenance_on": on["per_item"][iid]["provenance_entries"],
            } for iid in item_ids} if args.include_per_item else None,
        })
    if not any(not a["structurally_blind"] for a in out_arms.values()):
        refusals.append((STRUCTURAL_REFUSAL,
                         "EVERY arm is structurally blind to the lever — no arm "
                         "read the leg the expansion arms (`entity_type == "
                         "'point'`), so no verdict on 'does it help or hurt' "
                         "can be drawn from this run"))
    point = out_arms.get("point")
    if point is not None and not point["structurally_blind"] \
            and point["expansion_fired_items"] == 0:
        refusals.append((INERT_REFUSAL,
                         "the point arm reached the mechanism but the expansion "
                         "changed NO read (0 of " +
                         str(point["expansion_identical_items"]) +
                         " items) — every item's anchor resolution returned "
                         "nothing to add, so the run measures an inert "
                         "harvest, not the lever"))
    return {
        "schema": ARTIFACT_SCHEMA,
        "measured_at": datetime.now(UTC).isoformat(),
        "git_head": _git_head(),
        "snapshot": {k: manifest[k] for k in
                     ("schema", "digest", "file_count", "total_bytes",
                      "total_lines", "files")},
        "population": {k: pop_body[k] for k in
                       ("schema", "snapshot_digest", "population", "sample",
                        "seed", "names_sha256")},
        "population_file": str(pop_file),
        "items": {"sampled": pop_body["sample"], "with_item": len(items),
                  "dropped": dropped,
                  # ⛔ `id_digest` is sha256 over the SYNTHETIC ids every draw
                  # assigns (`item-000000…item-000299`), so it is a function of
                  # the sample SIZE and nothing else: `4d6899d3…` is the constant
                  # for ANY 300-item sample and `9eeb9c4f…` the constant for 60.
                  # It was cited as the "same items in both arms" evidence and
                  # could not carry it (`#13267` review 2, finding 1).
                  "id_digest": _sha256_bytes("\n".join(item_ids).encode()),
                  "id_digest_note": (f"sha256 over {len(item_ids)} synthetic "
                                     "`item-NNNNNN` ids — a function of the "
                                     "sample SIZE only, NOT pairing evidence; "
                                     "use name_digest"),
                  # The DISCRIMINATING digest: sha256 over the sampled NAMES, in
                  # item order. Two runs that drew the same names agree here and
                  # nowhere else (`#13267` F6).
                  "name_digest": _sha256_bytes(
                      "\n".join(it["name"] for it in items).encode()),
                  "median_target_overlap_with_query": _median(
                      [it["target_overlap_with_query"] for it in items])},
        # The falsifier "the same sampled items are used in both arms" is TRUE BY
        # CONSTRUCTION here (one `items` list, built once, read by every arm in
        # one process) — so it is recorded as such, with the count that IS a
        # reading, instead of as a predicate over the comprehensions that build
        # `per_item` (which was `True` for every reachable input: `#13267` F6).
        "pairing": {
            "unit": "the item",
            "items_read_per_arm": {arm: blocks["off"]["reads"]
                                   for arm, blocks in arms.items()},
            "same_item_list_is_structural": (
                "one `items` list is built once and handed to every arm in the "
                "same process, so a different sample per arm is not reachable; "
                "`items.name_digest` (and `population.names_sha256`) is the "
                "discriminating record of WHICH names those items came from"),
        },
        "construction": {
            "graph": seeded,
            "arm": "point = the leg the expansion arms; object = the leg the "
                   "published comparison measured",
            "keys_source": ("the target record's own salient tokens — the "
                            "SHIPPED harvest's own shape (the pool is the linked "
                            "points' search_keys), so the lift measured here is "
                            "the mechanism's ceiling under a clean key set"),
            "distractors": args.distractors,
            "read_limit": args.read_limit,
            # ⛔ THE TREATMENT VARIABLE, recorded. The robustness arm IS
            # `--query-chars 200`; an artifact that recorded `read_limit` and not
            # the query cap could not say what made the two 300-item point runs
            # different, and a reader was left to attribute the whole difference
            # to a knob the artifact never named (`#13267` F5 / review 2
            # finding 3).
            "query_chars": args.query_chars,
            "key_tokens": args.key_tokens,
            "min_query_tokens": args.min_query_tokens,
            "min_items": args.min_items,
            "population": args.population,
            "sample": args.sample,
            "seed": args.seed,
            "arms": list(args.arms),
            "repeat_arm": args.repeat_arm,
            "bootstrap_resamples": args.bootstrap,
            "per_k_from_one_read": True,
            "declared_not_a_production_census": True,
        },
        "arms": out_arms,
        "environment": {
            "load_start": load_start, "load_end": load_end,
            "load_delta_1m": round(load_end["load_1m"] - load_start["load_1m"], 2),
            "same_load_note": ("both arms ran back to back in ONE process on ONE "
                               "box, so the loads below bracket the whole "
                               "comparison rather than one arm — and the delta "
                               "is reported so a reader can see whether the box "
                               "moved under it"),
            "graph_nodes": graph_nodes, "elapsed_s": round(elapsed, 1),
            "graph": args.graph or "(run-scoped)",
            "db_uri_scheme": (os.environ.get("TORTOISE_DB_URI") or "").split("://")[0],
            "embedder": _has_embedder(),
        },
        # ⛔ WHICH BYTES produced this artifact, not just which commit.
        "provenance": _tool_provenance(
            git_head=_git_head(),
            git_head_note=("`git_head` is the REPO commit, not the code: a run "
                           "from a dirty tree records the commit it sits on. "
                           "`tool_sha256` is the digest of the tool's own bytes "
                           "and `tool_uncommitted` says whether those bytes "
                           "were committed (#13267 F4).")),
        "refusals": [msg for _kind, msg in refusals],
        "refusal_kinds": [kind for kind, _msg in refusals],
    }


def _median(values: list[int]) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    n = len(s)
    return float(s[n // 2]) if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _has_embedder() -> bool:
    try:
        from tortoise.embeddings import EmbeddingModel
        return EmbeddingModel.get() is not None
    except Exception:
        return False


def _git_head() -> str | None:
    try:
        import subprocess
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=10
                              ).stdout.strip() or None
    except Exception:
        return None


def _tool_provenance(*, git_head: str | None, git_head_note: str) -> dict:
    """WHICH BYTES produced the artifact — not just which commit they sat on.

    ⛔ `git_head` alone LIED here: the committed artifacts record
    `9b537d875a` while carrying fields (`seed_effect`,
    `off_topk_displacement`, `off_top1_kept_in_window`) that do not exist at that
    commit, so they were produced by a DIRTY tree and the one field a reader uses
    to locate the producing code pointed at different code (`#13267` F4). An
    EARLIER generation recorded `44a1b82` while carrying fields first committed in
    `9b537d8` — the same defect one generation back, which is why the field
    records whether the bytes were committed rather than only where HEAD sat.
    `tool_sha256` is self-identifying bytes; `tool_uncommitted` says whether those
    bytes were in the commit at all.
    """
    path = Path(__file__).resolve()
    try:
        digest = _sha256_bytes(path.read_bytes())
    except OSError:
        digest = None
    uncommitted: bool | None = None
    try:
        import subprocess
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                              capture_output=True, text=True, timeout=10
                              ).stdout.strip()
        if root:
            rel = str(path.relative_to(root))
            dirty = subprocess.run(
                ["git", "status", "--porcelain", "--", rel],
                capture_output=True, text=True, timeout=10).stdout.strip()
            uncommitted = bool(dirty)
    except Exception:
        uncommitted = None
    return {"tool_path": "tools/paired_expansion_ab_2518.py",
            "tool_sha256": digest, "tool_uncommitted": uncommitted,
            "git_head": git_head, "git_head_note": git_head_note}


def _print_summary(artifact: dict) -> None:
    snap = artifact["snapshot"]
    print(f"snapshot   {snap['digest'][:16]}  files={snap['file_count']}  "
          f"lines={snap['total_lines']}  bytes={snap['total_bytes']}")
    print(f"items      {artifact['items']['with_item']} / "
          f"{artifact['items']['sampled']} sampled  "
          f"id_digest={artifact['items']['id_digest'][:16]}  "
          f"dropped={artifact['items']['dropped']}")
    for arm, block in artifact["arms"].items():
        blind = "STRUCTURALLY BLIND" if block["structurally_blind"] else "measured"
        print(f"\narm {arm}: {blind}  window_modes={block['window_modes']}  "
              f"expansion_entries={block['expansion_provenance_entries']}  "
              f"repeat_identical={block['repeat_identical']}")
        for k, row in block["per_k"].items():
            if "off_recall" not in row:
                continue
            print(f"  {k:>5}  off={row['off_hits']:>4}/{row['n']:<4} "
                  f"on={row['on_hits']:>4}/{row['n']:<4} "
                  f"b={row['discordant_miss_to_hit']:>3} "
                  f"c={row['discordant_hit_to_miss']:>3} "
                  f"p={row['mcnemar_exact_p']:.4f} "
                  f"delta={row['bootstrap']['delta']:+.4f} "
                  f"ci95=[{row['bootstrap']['ci95'][0]:+.4f},"
                  f"{row['bootstrap']['ci95'][1]:+.4f}] "
                  f"MDE(paired)={row['mde_paired']:.4f} "
                  f"MDE(indep,n)={row['mde_independent']:.4f}")
    if artifact["refusals"]:
        print("\nrefusals:")
        for r in artifact["refusals"]:
            print(f"  - {r}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--sessions-dir", default="~/.tortoise/session-events",
                   help="the LIVE corpus — read only by --freeze")
    # ⛔ ONE input source. `--freeze` WRITES the snapshot and returns its manifest;
    # `--snapshot` VERIFIES an existing one. They are mutually exclusive because
    # the two used to compose: with both passed, the manifest (hence
    # `snapshot.digest`, every per-file sha256 and `population.snapshot_digest`)
    # came from the `--freeze` target while the records were read from the
    # `--snapshot` directory — an artifact attesting "this corpus, byte for byte"
    # over bytes the run never read (`#13267` F2). `argparse`'s documented remedy
    # for two options feeding one decision, and it refuses BEFORE any copy.
    corpus = p.add_mutually_exclusive_group()
    corpus.add_argument("--freeze", default=None,
                        help="copy the live corpus here ONCE and record its manifest")
    corpus.add_argument("--snapshot", default=None,
                        help="reuse an already-frozen snapshot (verified, never re-read live)")
    p.add_argument("--population-file", default=None,
                   help="the drawn sample; written on first run, reused after")
    p.add_argument("--population", type=int, default=1000)
    p.add_argument("--sample", type=int, default=300)
    p.add_argument("--seed", type=int, default=20261117)
    p.add_argument("--distractors", type=int, default=500,
                   help="real corpus records seeded as competition for the window")
    p.add_argument("--read-limit", type=int, default=60,
                   help="ONE read per item per arm; every k is a prefix of it")
    p.add_argument("--query-chars", type=int, default=500)
    p.add_argument("--key-tokens", type=int, default=4)
    p.add_argument("--min-query-tokens", type=int, default=3)
    p.add_argument("--min-items", type=int, default=30)
    p.add_argument("--arms", default="point,object",
                   help="comma-separated: point (the lever's own leg), object")
    p.add_argument("--repeat-arm", default="point",
                   help="re-read this arm's OFF side to prove determinism")
    p.add_argument("--bootstrap", type=int, default=2000)
    p.add_argument("--namespace", default="paired2518")
    p.add_argument("--graph", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--include-per-item", action="store_true", default=True)
    p.add_argument("--allow-mixed-modes", action="store_true")
    args = p.parse_args(argv)
    args.arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    for a in args.arms:
        if a not in ("point", "object"):
            print(f"unknown arm {a!r} (expected point|object)", file=sys.stderr)
            return 2
    try:
        return run(args)
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
