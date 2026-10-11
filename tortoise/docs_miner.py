"""Deterministic docs → statement Point miner (#1724).

Follow-up to #1714 / #1726 (Slice 1 ships a remote ``docs/`` corpus as
**Sources only** — no claim extraction; ``tortoise/indexer/github_docs.py``
says so in its own module docstring). This module adds the claim-extraction
layer ON TOP of those Sources, mirroring the meeting-transcript miner's
draft→promote flow (#1198, ``tortoise/mining.py``) for the docs input shape.

WHAT IT WRITES
    For every corpus ``*.md`` classified as a **document** (``classify_file``
    → ``doc``) one **draft** ``statement`` Point per extracted claim:

    ``(Point)-[:extractedFrom]->(Source{url: doc_id})`` — the D10-folded
    Document Source the corpus pipeline (``index_directory`` →
    ``_doc_write``) already mints. ``doc_id`` is the corpus-relativized
    ``doc_<rel-path>`` (``file_indexer.derive_document_id``), NOT the
    ``corpus://`` Source the same pipeline creates for the file — the two are
    kept distinct on purpose (the ``references`` edge and the
    index-completeness gate depend on it).

    ``(Point)-[:aboutObject]->(Object)`` — the repo/project entity, resolved by
    its STABLE external anchor (``externalId`` = ``github:repo:{owner}/{name}``)
    exactly as ``tortoise/session_link.py`` resolves ``github:issue:*``: a
    supersession/status fold never changes the Object id, so the edge never
    dangles.

VOCABULARY (do not drift)
    ``claim`` is NOT a node type — ``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md``
    (§"Vocabulary") records the owner ruling: *"claim" is the sanctioned
    user-facing GLOSS for a belief Point*; the type is ``Point`` and there is
    **no** ``claim`` pointKind. Every point written here is
    ``pointKind="statement"``. ``status`` is ``draft`` (``EventAPI`` writes
    draft by construction — ``tortoise/api.py`` ''#432/#1088'') so nothing
    enters the live belief set until a human/agent promotes it with the
    EXISTING ``TortoiseSDK.promote_point``.

COST — DETERMINISTIC ONLY, AND THAT IS THE DESIGN
    The issue's cost indicator is *"deterministic paths where possible; any LLM
    path is opt-in and quota-gated (no silent per-doc token spend)"*. This
    module takes the stronger half of that: **there is no LLM path at all.**
    Extraction is cue-gated string work, ``0`` network calls, ``0`` tokens, per
    document and per run. An opt-in LLM lane for prose the cues miss is
    deliberately NOT built here — it would need a metered call budget to be
    honest, and no such meter exists on this path; adding one is a separate
    change with its own verification. (Recorded, not silently omitted: the
    verification-checklist row in #1724 is worded *"cost gate (LLM path, **if
    used**)"*, which is satisfied by not using one.)

DEDUP — TWO MECHANISMS, BOTH REQUIRED
    1. **Hash-dedup (skip the file).** A document is skipped when the CORPUS
       Source (``corpus://<name>/<rel-path>``, the node the index pass writes
       ``contentHash`` onto — the Document Source carries none) matches the
       current file hash AND at least one Point already carries
       ``extractedFrom = doc_id``. The second clause is load-bearing and is the
       DE2E-N8 lesson from the conversation miner: the hash alone conflates
       *"indexed by the corpus pass"* with *"already mined"*, so a corpus
       indexed but never mined would no-op forever. The read also runs BEFORE
       ``index_directory`` — after it, the hash would already have been
       refreshed to the file's own value and an edited doc would look
       unchanged forever (see ``mine_docs``'s ORDERING note).
    2. **Deterministic keying (idempotent write).** A claim's identity is
       ``sha256(normalized content)[:12]`` — so re-extracting the same sentence
       from a changed doc MERGEs onto the SAME Point instead of minting a
       second one, and a doc edit that leaves a claim untouched does not
       churn its id.

    Re-mining an unchanged corpus therefore reports ``new_points == 0``. Note
    what that number MEASURES: ids absent from the graph before the run. A
    run that re-writes existing ids still reports ``new_points == 0``, which
    is the issue's falsifier (*"0 new Points"*), not a claim about writes.

    A claim's identity is CONTENT-addressed on purpose: a reworded claim is a
    different claim (the old Point remains as history), so no supersession is
    performed here and no edge can dangle as a result. Lifecycle is a read.
"""
from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from tortoise.entity_identity import (  # #3633 route-then-refuse
    all_name_holder_ids,
    resolve_entity_id,
)

logger = logging.getLogger(__name__)

# ── Vocabulary (pinned) ────────────────────────────────────────────────────
POINT_KIND_STATEMENT = "statement"
POINT_STATUS_DRAFT = "draft"
EXTRACTOR_VERSION = "docs-miner@1"
EXTERNAL_ID_PREFIX = "docs:"

#: The repo entity's object kind — ``software`` is a dev-pack ``objectKind``
#: (``packs/dev/manifest.yaml`` §ontology.objectKinds).
REPO_OBJECT_KIND = "software"
REPO_ID_PREFIX = "github-repo-"
REPO_EXTERNAL_ID_PREFIX = "github:repo:"

#: Claim-identity hash width (hex chars). 48 bits — a doc's claim count is
#: bounded by ``max_claims_per_doc`` (dozens), so a collision inside ONE
#: document is not a practical risk, and the id stays readable in a graph dump.
CLAIM_KEY_LEN = 12

#: A claim must be at least this many words (after markdown stripping) — below
#: it a "sentence" is a fragment, not an assertion.
MIN_CLAIM_WORDS = 4

#: Hard cap on the quote/claim text handed to the graph (a claim is a sentence,
#: not a paragraph).
MAX_CLAIM_CHARS = 400

# ── Cue families (the WHOLE extraction rule; default-deny) ─────────────────
# A sentence becomes a claim ONLY if it matches one of these families. There is
# no "any sufficiently long sentence" fallback: an un-cued sentence is not
# extracted. That is the deterministic answer to *"which sentences are claims?"*
# and it is what keeps the draft review queue readable.
_NORMATIVE_CUES = (
    "must", "must not", "never", "always", "required", "requires",
    "shall", "should not", "do not", "does not", "cannot", "can not",
    "may not", "guarantees", "invariant", "forbidden", "mandated",
    "mandatory",
)
_DECISION_CUES = (
    "decision", "decided", "ruling", "ruled", "overrides", "policy",
    "chosen", "adopted", "we choose", "the call is",
)
_LIFECYCLE_CUES = (
    "deprecated", "retired", "superseded", "obsolete", "abandoned",
    "removed", "replaced", "dropped", "no longer",
)

CUE_FAMILIES = {
    "normative": _NORMATIVE_CUES,
    "decision": _DECISION_CUES,
    "lifecycle": _LIFECYCLE_CUES,
}

# A cue must bind as a WHOLE WORD. Without the boundaries "only" fired inside
# "only-one-thing"? (no — but "at least" fired inside "at leastitude"), and more
# importantly "do not" would fire inside "does nothing". Compiled once, used for
# every line, so the rule is one table rather than a per-call regex soup.
_CUE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    + "|".join(
        re.escape(c) for cues in CUE_FAMILIES.values() for c in cues
    )
    + r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)

# The STRONGEST cue family in a sentence wins (a sentence that is both a
# decision and normative is reported as a decision — the more specific fact).
_CUE_PRIORITY = ("decision", "lifecycle", "normative")

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_CHECKBOX_RE = re.compile(r"^\s*[-*+]\s+\[[ xX]\]")
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
_FM_BOUNDARY_RE = re.compile(r"^---\s*$")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Bold and code markers only. A single ``*``/``_`` is LEFT ALONE: snake_case
# identifiers (``mine_docs``, ``TORTOISE_ALLOW_MAIN_EDITS``) are load-bearing in
# this corpus, and stripping one would rewrite the claim's text and therefore
# its identity.
_EMPHASIS_RE = re.compile(r"\*\*|__|`")
# Real sentence ends only — NOT ``:``/``;``. A marker line (``**Decision:** we
# choose X``) is ONE sentence; splitting on the colon would cut the cue away
# from the assertion it governs and silently drop the claim.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


# ── Pure text primitives (no graph, no IO) ─────────────────────────────────

def strip_markdown(text: str) -> str:
    """Strip the markdown that must never enter a claim's identity.

    Links become their LABEL (``[the gate](x.md)`` → ``the gate``), emphasis and
    inline-code markers are dropped, and whitespace is collapsed. Deliberately
    NOT a parser: it normalizes the two dozen constructs our corpus uses, and
    anything it does not know it leaves alone (a wrong guess here would change
    every claim's identity at once).
    """
    s = _LINK_RE.sub(r"\1", str(text or ""))
    s = _EMPHASIS_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_claim(text: str) -> str:
    """The claim's canonical text — the ONLY input to its identity.

    Order matters: markdown first, then the list/quote/heading prefixes that
    carry structure rather than content, then trailing punctuation. Two
    spellings of the same assertion (*``- X must Y.``* and *``X must Y``*)
    normalize to one string and therefore to one Point.
    """
    s = strip_markdown(text)
    s = _LIST_ITEM_RE.sub("", s)
    s = re.sub(r"^>\s*", "", s)
    s = _HEADING_RE.sub("", s)
    s = s.strip()
    return s.rstrip(".:;,-—– ").strip()


def claim_key(normalized: str) -> str:
    """The claim's content key — ``sha256(normalized)[:12]``.

    Domain-separated by prefix so a key can never be mistaken for a
    ``content_hash`` of the whole document (the #330 drift class this repo has
    eaten twice).
    """
    return hashlib.sha256(
        f"docclaim:{normalized}".encode()
    ).hexdigest()[:CLAIM_KEY_LEN]


def statement_id(doc_id: str, key: str) -> str:
    """Deterministic Point id: ``pt_doc_<doc-slug>_<key>``.

    The doc slug (not the doc id verbatim) keeps the id Cypher/SDK-safe; the
    ``pt_doc_`` prefix keeps it distinguishable from the other deterministic
    id families (``pt_gh_`` GitHub statements, ``obj_`` Objects). The cap is
    applied by ``slugify`` and the full doc id is recoverable from
    ``externalId``/``extractedFrom`` — the id is a handle, not a record.
    """
    from .file_indexer import slugify
    return f"pt_doc_{slugify(doc_id, cap=60)}_{key}"


def external_id(doc_id: str, key: str) -> str:
    """Stable external anchor: ``docs:<doc_id>#<claim key>``.

    ``session_link``'s resolve-to-current rule reads the STABLE anchor rather
    than a graph id, so supersession never dangles an edge. For a content-keyed
    claim the anchor IS the content — the two moves agree here by construction.
    """
    return f"{EXTERNAL_ID_PREFIX}{doc_id}#{key}"


def repo_object_id(repo: str) -> str:
    """Deterministic Object id for a repo/project entity (``github-repo-<repo>``).

    Same shape as the GitHub indexer's ``github-issue-{repo}-{n}`` WorkItem ids:
    derived from the STABLE external anchor, never from a mutable property, so a
    later re-mint or a status fold resolves to the same node.
    """
    return f"{REPO_ID_PREFIX}{repo}"


def repo_external_id(repo: str) -> str:
    """Canonical external anchor for a repo: ``github:repo:{owner}/{name}``."""
    return f"{REPO_EXTERNAL_ID_PREFIX}{repo}"


def cue_family(sentence: str) -> str | None:
    """The strongest cue family a sentence matches, or ``None``.

    ``None`` is the default and means "not a claim" — see the module docstring's
    default-deny rule.
    """
    if not _CUE_RE.search(sentence):
        return None
    for family in _CUE_PRIORITY:
        for cue in CUE_FAMILIES[family]:
            if re.search(
                r"(?<![A-Za-z0-9_])" + re.escape(cue) + r"(?![A-Za-z0-9_])",
                sentence,
                re.IGNORECASE,
            ):
                return family
    return None


def iter_claim_spans(text: str) -> list[tuple[str, int, int]]:
    """Raw ``(sentence, start_line, end_line)`` candidates in document order.

    Walks the body line by line (1-based line numbers, inclusive) and yields
    the SENTENCES of each candidate line that carries a cue. Skipped outright:
    the frontmatter block, fenced code, tables, HTML comments, checkbox items,
    and anything inside an indented code block. Blockquotes, bullets and plain
    prose lines are all candidates — the cue filter, not the line shape, is
    what decides.
    """
    lines = str(text or "").splitlines()
    out: list[tuple[str, int, int]] = []
    in_fence = False
    fence_token = ""
    in_frontmatter = False
    fm_seen = False
    in_comment = False
    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        # frontmatter block (only at the very top — `_FM_RE` semantics)
        if not fm_seen and idx == 1 and _FM_BOUNDARY_RE.match(line):
            in_frontmatter = True
            continue
        if in_frontmatter:
            if _FM_BOUNDARY_RE.match(line):
                in_frontmatter = False
                fm_seen = True
            continue
        fence = _FENCE_RE.match(line)
        if fence:
            token = fence.group(1)
            if not in_fence:
                in_fence, fence_token = True, token
            elif token == fence_token:
                in_fence = False
            continue
        if in_fence:
            continue
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        if _CHECKBOX_RE.match(stripped):
            continue
        if line.startswith("    ") and not _LIST_ITEM_RE.match(stripped):
            # indented code block (a list item may legitimately be indented)
            continue
        for sentence in _SENTENCE_SPLIT_RE.split(stripped):
            sentence = sentence.strip()
            if not sentence:
                continue
            if cue_family(sentence) is None:
                continue
            out.append((sentence, idx, idx))
    return out


def extract_claims(text: str, *, doc_id: str,
                   max_claims: int = 40) -> list[dict]:
    """Deterministically extract reviewable statement claims from a doc.

    Pure: no graph, no IO, no model. Returns claim records in DOCUMENT ORDER
    (so the ``max_claims`` truncation is stable run to run), each:

    ``{id, external_id, key, content, cue, span_start, span_end, quote}``

    ``content`` is the normalized claim (what is written to the Point);
    ``quote`` is the raw source sentence (truncated) so a reviewer sees it
    verbatim. Duplicates — the same normalized claim repeated in one doc — are
    dropped, first occurrence winning.
    """
    from .file_indexer import parse_frontmatter

    fm = parse_frontmatter(text or "")
    claims: list[dict] = []
    seen: set[str] = set()

    def _add(content: str, cue: str, start: int, end: int, quote: str) -> None:
        norm = normalize_claim(content)
        if len(norm.split()) < MIN_CLAIM_WORDS:
            return
        norm = norm[:MAX_CLAIM_CHARS]
        key = claim_key(norm)
        if key in seen:
            return
        seen.add(key)
        claims.append({
            "id": statement_id(doc_id, key),
            "external_id": external_id(doc_id, key),
            "key": key,
            "content": norm,
            "cue": cue,
            "span_start": start,
            "span_end": end,
            "quote": str(quote or content).strip()[:MAX_CLAIM_CHARS],
        })

    summary = fm.get("summary")
    if summary:
        _add(str(summary), "frontmatter", 1, 1, str(summary))

    for sentence, start, end in iter_claim_spans(text or ""):
        if len(claims) >= max_claims:
            break
        _add(sentence, cue_family(sentence) or "normative", start, end, sentence)

    return claims[:max_claims]


# ── corpus / repo helpers ─────────────────────────────────────────────────

def derive_repo_from_corpus(directory: str | Path,
                            ingest_base: str | None = None) -> str | None:
    """``<org>/<repo>`` from a staged corpus path, when it is that shape.

    The hosted staging layout is ``{TORTOISE_INGEST_BASE_DIR}/{org_id}/{repo}``
    (``indexer/github_docs.py``). Returns ``None`` when the layout does not
    apply — an honest no-repo outcome, never a guessed one.
    """
    try:
        root = Path(str(directory)).resolve()
    except Exception:  # noqa: BLE001, RUF100
        return None
    if ingest_base:
        try:
            root = root.relative_to(Path(str(ingest_base)).resolve())
        except ValueError:
            return None
    parts = root.parts
    if len(parts) < 2:
        return None
    return f"{parts[-2]}/{parts[-1]}"


def _default_repo(directory: str, explicit: str | None) -> str | None:
    if explicit:
        return explicit.strip().strip("/") or None
    return derive_repo_from_corpus(directory, os_environ_ingest_base())


def os_environ_ingest_base() -> str | None:
    import os
    raw = os.environ.get("TORTOISE_INGEST_BASE_DIR")
    return os.path.realpath(os.path.expanduser(raw)) if raw else None


# ── graph-facing pass ─────────────────────────────────────────────────────

def _read_doc_state(proj, doc_id: str, file_url: str) -> dict:
    """``{source, content_hash, existing_points}`` for one corpus document.

    TWO nodes, deliberately: ``doc_id`` is the D10-folded Document Source the
    claims attach to (and the only thing that says "this doc was MINED"),
    while ``file_url`` is the corpus Source the index pass writes the file's
    ``contentHash`` onto. The Document Source carries NO ``contentHash`` of its
    own (measured: ``index_directory`` writes ``documentKind``/``format`` and
    leaves the version anchor to the corpus Source), so reading the hash from
    the node claims hang off would silently disable hash-dedup — and a silently
    disabled dedup reads exactly like a working one until the quota bill.
    """
    rows = proj.g.query(
        "MATCH (s:Source {url:$did}) RETURN 1",
        params={"did": doc_id},
    ).result_set
    state = {"source": bool(rows), "content_hash": None, "existing_points": 0}
    rows = proj.g.query(
        "MATCH (s:Source {url:$url}) RETURN s.contentHash",
        params={"url": file_url},
    ).result_set
    if rows:
        state["content_hash"] = rows[0][0]
    rows = proj.g.query(
        "MATCH (p:Point) WHERE p.extractedFrom = $did RETURN count(p)",
        params={"did": doc_id},
    ).result_set
    state["existing_points"] = int(rows[0][0]) if rows else 0
    return state


def _ensure_entities(api, proj, entities: list[dict]) -> list[str]:
    """Mint/merge entity Objects; return their ids (order preserved).

    Resolution is by the STABLE external anchor first (``externalId``) and by
    the ROUTED name resolver second — the ``session_link`` rule, resolved
    through #3633's ``resolve_entity_id`` rather than a raw
    ``MATCH (o:Object {name:$name})`` read. A raw read silently picks the first
    of N same-name carriers, so a name held by two live Objects would attach
    every claim to an arbitrary one; the resolver returns the single live id
    and REFUSES (:class:`~tortoise.entity_identity.AmbiguousEntityName`) on two
    or more.

    MINTING IS FOR A GENUINELY UNHELD NAME ONLY. The resolver returns ``None``
    both for a name with NO holder and for one whose holder(s) are all
    TERMINAL, so the holder set is read back before minting: the projection's
    object write MERGEs ``(o:Object {name:$name})``, so minting onto a held
    name would write the new id onto EVERY same-name holder — two terminal
    holders would end up both claiming one id (the ``duplicate-id`` corruption
    the resolver itself refuses), and a single terminal holder would be
    silently re-keyed and served as if live. A terminal-only reference is
    #3633 D2's refusal; refusing here lets ``mine_docs`` record it in
    ``result["errors"]`` instead of corrupting or resurrecting an identity.

    Re-minting is idempotent: the projection MERGEs Object by name, and the
    deterministic id is derived from the same anchor on every run, so a second
    run cannot fork the node.
    """
    ids: list[str] = []
    for ent in entities:
        name = ent["name"]
        rows = proj.g.query(
            "MATCH (o:Object) WHERE o.externalId = $ext RETURN o.id",
            params={"ext": ent["externalId"]},
        ).result_set
        if rows:
            ids.append(rows[0][0])
            continue
        resolved = resolve_entity_id(proj.g, "Object", name)
        if resolved is not None:
            ids.append(resolved)
            continue
        if all_name_holder_ids(proj.g, "Object", name):
            raise ValueError(
                f"docs miner: Object {name!r} is held only by terminal "
                f"node(s) — refusing to mint a name-keyed replacement that "
                f"the projection's MERGE would stamp onto every holder "
                f"(#3633)")
        oid = ent["id"]
        api.add_object(name, ent["object_kind"], id=oid,
                       canonical_name=name, title=name,
                       externalId=ent["externalId"])
        ids.append(oid)
    return ids


def mine_docs(sdk, directory: str | Path, *,
              corpus_name: str | None = None,
              repo: str | None = None,
              about_entities: list[str] | None = None,
              max_claims_per_doc: int = 40,
              index_first: bool = True,
              progress_file: str | None = None) -> dict:
    """Mine a markdown corpus into DRAFT statement Points (the #1724 pipeline).

    Composes the Slice-1 corpus pass (``sdk.index_directory`` — Sources +
    D10-folded Document Sources, hash-dedup'd) with the claim pass on top,
    exactly as ``mining.mine_corpus_with_sdk`` composes ``sdk.ingest_corpus``
    with the conversation miner. ``index_first=False`` skips the composition
    for a caller that just ran it.

    Only files classified ``doc`` are mined; ``agent_session`` / ``meeting``
    files in the same tree are counted in ``skipped_not_document`` and left to
    their own miners (the corpus pipeline indexes them regardless).

    Returns ``{directory, corpus_name, repo, documents, indexed,
    skipped_unchanged, skipped_not_document, skipped_missing_source, mined,
    claims, new_points, existing_points, objects, objects_wired,
    errors:[{file, error, retryable}]}``.

    ORDERING (load-bearing, and the reason the pre-scan is a separate phase):
    the per-document mined-state read runs BEFORE ``index_directory``. Reading
    it after would compare the file hash against the hash the index pass had
    JUST refreshed to the same value — so every document would look unchanged
    and a genuinely edited doc would be skipped forever. This is the DE2E-N8
    ordering pin from ``mine_corpus_with_sdk``, restated for the docs shape.
    """
    import os as _os

    from .api import EventAPI
    from .file_indexer import (
        classify_file,
        compute_file_hash,
        derive_document_id,
        derive_source_url,
        parse_frontmatter,
    )
    from .ids import ulid
    from .log import EventLog

    directory = str(directory)
    if not _os.path.isabs(directory) or ".." in Path(directory).parts:
        raise ValueError(
            f"Unsafe directory {directory!r} — must be absolute with no '..' "
            f"components (#329)."
        )
    dir_path = Path(directory)
    if not dir_path.is_dir():
        raise ValueError(f"docs miner: not a directory: {directory!r}")
    corpus_root = Path(_os.path.realpath(str(dir_path)))
    corpus_name = corpus_name or corpus_root.name

    result = {
        "directory": str(corpus_root), "corpus_name": corpus_name,
        "documents": 0, "indexed": 0, "skipped_unchanged": 0,
        "skipped_not_document": 0, "skipped_missing_source": 0,
        "mined": 0, "claims": 0, "new_points": 0, "existing_points": 0,
        "objects": 0, "objects_wired": 0, "repo": None, "errors": [],
    }

    proj = sdk._get_proj()
    docs = [fp for fp in sorted(corpus_root.rglob("*.md"))
            if not fp.is_symlink()]

    # ── Phase A: PRE-INDEX scan — which documents are already mined at the
    # current content hash? MUST run before the index pass (see ORDERING). ──
    pre: dict[str, dict] = {}
    for fp in docs:
        rel = str(fp)
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001, RUF100
            result["errors"].append(
                {"file": rel, "error": str(e), "retryable": False})
            continue
        if classify_file(parse_frontmatter(text), fp, None) != "doc":
            result["skipped_not_document"] += 1
            continue
        result["documents"] += 1
        try:
            doc_id = derive_document_id(rel, corpus_root)
            file_url = derive_source_url(rel, corpus_root, corpus_name)
        except ValueError as e:
            result["errors"].append(
                {"file": rel, "error": str(e), "retryable": False})
            continue
        file_hash = compute_file_hash(rel)
        state = _read_doc_state(proj, doc_id, file_url)
        # Hash-dedup + the DE2E-N8 second clause (see the module docstring).
        pre[rel] = {
            "doc_id": doc_id,
            "file_url": file_url,
            "file_hash": file_hash,
            "skip": bool(
                file_hash is not None
                and state["content_hash"] == file_hash
                and state["existing_points"] > 0),
        }

    ingest = {}
    if index_first:
        ingest = sdk.index_directory(directory, corpus_name=corpus_name,
                                     progress_file=progress_file)
        result["indexed"] = ingest.get("indexed", 0)
        for err in ingest.get("errors", []):
            result["errors"].append(dict(err))

    from .api import provenance
    from .session_link import link_entity

    log = sdk._get_event_log()
    if log is None:
        db_path = getattr(sdk, "_db_path", None)
        anchor = (_os.path.dirname(_os.path.abspath(db_path)) if db_path
                  else str(corpus_root))
        log = EventLog(_os.path.join(anchor, ".mine-docs-events.jsonl"))
    api = EventAPI(log, initiated_by="extractor", agent_id="docs-miner",
                   projection=proj)
    api._ingest_cache = {}

    resolved_repo = _default_repo(directory, repo)
    entities: list[dict] = []
    if resolved_repo:
        entities.append({
            "id": repo_object_id(resolved_repo),
            "name": resolved_repo,
            "object_kind": REPO_OBJECT_KIND,
            "externalId": repo_external_id(resolved_repo),
        })
    for name in (about_entities or []):
        clean = str(name).strip()
        if not clean or any(e["name"] == clean for e in entities):
            continue
        entities.append({
            "id": repo_object_id(clean),
            "name": clean,
            "object_kind": REPO_OBJECT_KIND,
            "externalId": repo_external_id(clean),
        })

    result["repo"] = resolved_repo
    entity_ids: list[str] = []
    if entities:
        try:
            entity_ids = _ensure_entities(api, proj, entities)
            result["objects"] = len(entity_ids)
        except Exception as e:  # noqa: BLE001, RUF100
            result["errors"].append({
                "file": "(entities)",
                "error": f"entity mint failed: {e}", "retryable": True})
            entity_ids = []

    for fp in docs:
        rel = str(fp)
        info = pre.get(rel)
        if info is None:
            continue  # not a document, or its identity derivation failed above
        doc_id = info["doc_id"]
        if info["skip"]:
            # Nothing is read and nothing is written for a skipped document, so
            # it contributes to NO counter except ``skipped_unchanged``. That
            # keeps the run's accounting a closed statement —
            # ``existing_points + new_points == claims`` on every run — rather
            # than a number whose meaning depends on which docs were walked.
            result["skipped_unchanged"] += 1
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001, RUF100
            result["errors"].append(
                {"file": rel, "error": str(e), "retryable": False})
            continue
        # The Document Source is the corpus pass's product; mining a doc whose
        # Source is absent would mint an extractedFrom edge with no endpoint
        # (silently dropped) and a Point whose provenance is a phantom. Skipped
        # honestly, and retryable — the next run after a successful index mines
        # it. Read AFTER the index pass, so a first run is never refused here.
        rows = proj.g.query("MATCH (s:Source {url:$did}) RETURN 1",
                            params={"did": doc_id}).result_set
        if not rows:
            result["skipped_missing_source"] += 1
            result["errors"].append({
                "file": rel,
                "error": f"no Document Source {doc_id!r} — run the corpus "
                         f"index pass first",
                "retryable": True})
            continue

        api.current_run = ulid()
        try:
            claims = extract_claims(text, doc_id=doc_id,
                                    max_claims=max_claims_per_doc)
        except Exception as e:  # noqa: BLE001, RUF100
            result["errors"].append({
                "file": rel, "error": f"extraction failed: {e}",
                "retryable": False})
            continue

        mined = 0
        for claim in claims:
            try:
                already = bool(proj.g.query(
                    "MATCH (p:Point {id:$id}) RETURN 1",
                    params={"id": claim["id"]},
                ).result_set)
                if already:
                    result["existing_points"] += 1
                api.add_point(
                    claim["content"],
                    provenance(doc_id,
                               [claim["span_start"], claim["span_end"]],
                               claim["quote"], speaker="document",
                               extracted_by=EXTRACTOR_VERSION),
                    id=claim["id"],
                    pointKind=POINT_KIND_STATEMENT,
                    status=POINT_STATUS_DRAFT,
                    extractedFrom=doc_id,
                    externalId=claim["external_id"],
                    span_start=claim["span_start"],
                    span_end=claim["span_end"],
                    quote=claim["quote"],
                    authoredBy="docs-miner",
                )
            except Exception as e:  # noqa: BLE001, RUF100
                result["errors"].append({
                    "file": rel, "error": f"claim write failed ({e})",
                    "retryable": True})
                continue
            mined += 1
            if not already:
                result["new_points"] += 1
            for oid in entity_ids:
                try:
                    result["objects_wired"] += link_entity(
                        proj, "Point", claim["id"], oid, "aboutObject",
                        "Object", sdk=sdk)
                except Exception as e:  # noqa: BLE001, RUF100
                    result["errors"].append({
                        "file": rel,
                        "error": f"aboutObject wire failed for "
                                 f"{claim['id']}: {e}",
                        "retryable": True})
        if claims:
            result["mined"] += 1
        result["claims"] += len(claims)

    return result
