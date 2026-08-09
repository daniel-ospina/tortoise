"""Live ingest CLI — two-tier extractor → log → FalkorDB → grid render.

    python -m tortoise.ingest <transcript.txt> \
        --point-model    ollama:llama3.2:3b \
        --relation-model deepseek:deepseek-chat

Model specs are `provider:model` (the model part may itself contain ':', e.g.
ollama tags). Providers:
    ollama:MODEL    → http://localhost:11434/v1        (no key)
    deepseek:MODEL  → https://api.deepseek.com/v1       (DEEPSEEK_API_KEY)
    openai:MODEL    → https://api.openai.com/v1         (OPENAI_API_KEY)
    gemini:MODEL    → .../v1beta/openai                 (GEMINI_API_KEY)
    mock:NAME       → offline MockModel                 (for testing the wiring)

Idempotent: re-running the same file at the same extractor version is a no-op.
"""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from .api import EventAPI, provenance
from .extractor import LLMExtractor, MockModel, _document_sections, extract_from_document
from .idempotency import document_key
from .log import EventLog
from .models import OllamaModel, OpenAICompatModel
from .projection import FalkorProjection, fold, split
from .ids import ulid
from .render import render

# OpenAI-compatible providers (Ollama is handled separately via its native API).
_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}


# -- Frontmatter parsing for S8 Document Indexer (#6890) ------------------------
_FM_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML-like frontmatter as a flat dict. No PyYAML dependency."""
    m = _FM_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str | bool] = {}
    for line in m.group(1).split('\n'):
        # ponytail: key: value / key: "value" / key: 'value'
        kv = line.split(':', 1)
        if len(kv) != 2:
            continue
        k = kv[0].strip()
        v = kv[1].strip().strip('"').strip("'")
        if v.endswith('#'):  # trailing comment
            v = v.rsplit('#', 1)[0].strip()
        if k and v:
            # #133: parse well-known boolean frontmatter fields
            if k == "needs_extraction":
                fm[k] = v.lower() == "true"
            else:
                fm[k] = v
    return fm


def _infer_format(filepath: Path) -> str:
    """Map file extension to format field."""
    ext = filepath.suffix.lower()
    return {".md": "markdown", ".jsonl": "jsonl", ".yaml": "yaml",
            ".yml": "yaml", ".cypher": "cypher"}.get(ext, "other")


# ── #133 upgrade helpers ────────────────────────────────────────────

def _run_ep_propagation(proj):
    """#133: Run EP confidence propagation after upgrade.

    Lazy (on-demand) — called only after an upgrade, not on every ingest.
    Note: EP requires the full factor graph for correct belief propagation,
    so this iterates all operators (the graph is small at this scale).
    """
    try:
        op_ids = [r[0] for r in proj.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
        ).result_set]
        if not op_ids:
            return
        from tortoise.ep import TortoiseEP
        claim_rows = proj.g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id, coalesce(n.confidence, 0.5)"
        ).result_set
        evidence = {}
        for cid, conf_raw in claim_rows:
            conf = float(conf_raw)
            evidence[cid] = TortoiseEP.confidence_to_prior(conf)
        ep = proj.get_ep() if hasattr(proj, 'get_ep') else TortoiseEP(proj)
        n_iter, converged = ep.run(op_ids, max_hops=3, evidence=evidence)
        print(f"  EP: {'converged' if converged else 'max iter'} in {n_iter} iterations"
              f" ({len(evidence)} priors)")
    except Exception as e:
        msg = str(e).lower()
        if any(kw in msg for kw in ("connection refused", "connection reset",
              "errno 61", "errno 111", "cannot connect", "no route to host")):
            print(f"  EP propagation skipped (DB unavailable: {e})")
        else:
            print(f"  EP propagation failed: {e}")
            raise

def _do_upgrade(transcript, text, source_id, proj, api, args):
    """Upgrade a single Document: re-run extraction + SET doc_status='extracted'.

    Guards:
    - Already-extracted → no-op "doc already extracted, skipped"
    - Non-Document transcript → graceful "not a Document" error
    - Uses begin_ingest for idempotency (content-hash + extractor version)
    - doc_status flip via raw Cypher SET, NOT add_document (P0 — coalesce
      would overwrite 'captured' because add_document always passes non-null)
    """
    # 1. Check Document exists + current doc_status
    rows = proj.g.query(
        "MATCH (d:Document {id: $id}) "
        "RETURN coalesce(d.doc_status, 'captured') AS status",
        params={"id": source_id},
    ).result_set
    if not rows:
        print(f"not a Document: {source_id} (no Document node with this id)")
        return
    current_status = rows[0][0]
    if current_status == "extracted":
        print(f"doc already extracted, skipped: {source_id}")
        return

    # 2. Idempotency gate — re-extract only if not already processed
    #    at this extractor version (begin_ingest key: content-hash + version)
    result = api.begin_ingest(source_id, api.agent_id or "extractor@0",
                              document_key(text), force=args.force)
    if result and result.skip:
        print(f"upgrade skip: {result.reason} (run {result.run_id}); use --force to reprocess")
        return

    print(f"upgrading {source_id} (doc_status={current_status}) …")

    # 3. Re-run full extraction (the same path as normal full ingest)
    from .extractor import extract_from_document
    from .ids import ulid
    from .domain_loader import resolve_domain_from_path

    # Emit fresh DocumentCreated for metadata (idempotent via MERGE)
    is_doc, _ = _document_sections(text)
    if is_doc:
        fm = _parse_frontmatter(text)
        domain = fm.get("domain", fm.get("documentKnowledgeDomain", ""))
        if not domain:
            domain = resolve_domain_from_path(str(transcript))
        topics_raw = fm.get("topics", "")
        topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
        default_doc_status = fm.get("doc_status", "captured")
        api.add_document(
            doc_id=source_id,
            title=fm.get("title", transcript.stem),
            document_kind=fm.get("type", fm.get("document_kind", "")),
            document_knowledge_domain=domain,
            authored_by=api.agent_id or "extractor@0",
            owned_by=fm.get("ownedBy", ""),
            managed_by=fm.get("managedBy", ""),
            governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
            doc_status=default_doc_status,
            format=_infer_format(transcript),
            version=fm.get("version", ""),
            createdAt=fm.get("created", None),
            updatedAt=fm.get("updated", None),
            topics=topics,
            summary=fm.get("summary", ""),
            session_id=fm.get("sessionId", ""),
            event_id=str(ulid()),
            source_path=str(transcript),
            needs_extraction=fm.get("needs_extraction", False),
        )

        stats = extract_from_document(
            text, source_id, api,
            point_model=build_model(args.point_model),
            relation_model=build_model(args.relation_model, reasoning=True),
            authored_by="pi-agent",
            max_sections=args.max_utterances,
            domain=args.domain,
        )
        print(f"  extracted {stats['points']} Points, {stats['operators']} operators "
              f"from {stats['sections']} sections")
        if stats.get("failed_sections"):
            print(f"  warning: {len(stats['failed_sections'])} sections failed extraction")
    else:
        print(f"  warning: {source_id} is not a Document (no ## headers) — extraction skipped")

    # 4. CRITICAL P0: flip doc_status via raw Cypher SET, NOT add_document.
    #    add_document always passes non-null doc_status (default 'draft'),
    #    and coalesce($ds, d.doc_status, 'draft') would OVERWRITE 'captured'.
    proj.g.query(
        "MATCH (d:Document {id: $id}) SET d.doc_status = 'extracted'",
        params={"id": source_id},
    )
    print(f"  doc_status: {current_status} → extracted")

    # 5. Lazy EP re-propagation on affected subgraph (#133 Task 3)
    _run_ep_propagation(proj)


def _resolve_ingest_base() -> str | None:
    """#329: base-dir for ingest file reads (TORTOISE_INGEST_BASE_DIR).

    Returns None when unset — callers then FAIL CLOSED (skip reads that are
    not provably under a configured base). One-time hint is emitted by callers.
    """
    raw = os.environ.get("TORTOISE_INGEST_BASE_DIR")
    if not raw:
        return None
    return os.path.realpath(os.path.expanduser(raw))


def _do_upgrade_all(proj, api, args):
    """Discover captured/needs_extraction Documents and upgrade each.

    Uses inline Cypher via proj.g.query (no SDK method needed — plan §Task 2).
    Loop-safe: each attempt gated by begin_ingest key (content-hash + extractor
    version) so identical content is a no-op on re-run.

    #329 containment: the file read path (d.sourcePath OR d.id — both are
    tenant-mutable graph state) is resolved strictly under TORTOISE_INGEST_BASE_DIR;
    anything not provably under base is SKIPPED (fail-closed), never read.
    """
    from .security import resolve_under_base
    ingest_base = _resolve_ingest_base()
    if ingest_base is None:
        print("  warning: TORTOISE_INGEST_BASE_DIR not set — upgrade-all is fail-closed; "
              "documents are skipped unless their path resolves under a configured base. "
              "Set TORTOISE_INGEST_BASE_DIR to your corpus root to enable re-upgrade.")

    # Discover matching Documents
    rows = proj.g.query(
        "MATCH (d:Document) "
        "WHERE d.doc_status = 'captured' OR d.needs_extraction = true "
        "RETURN d.id, coalesce(d.sourcePath, d.id) AS sourcePath, "
        "coalesce(d.doc_status, 'captured') AS status, "
        "coalesce(d.needs_extraction, false) AS needs_extraction"
    ).result_set

    if not rows:
        print("upgrade-all: no documents found with doc_status='captured' or needs_extraction=true")
        return

    print(f"upgrade-all: {len(rows)} document(s) to upgrade")

    upgraded = 0
    skipped = 0
    for doc_id, source_path, status, needs_ext in rows:
        # Already extracted → no-op
        if status == "extracted":
            print(f"  skip {doc_id}: already extracted")
            skipped += 1
            continue

        # #329: resolve the candidate (sourcePath OR d.id — BOTH are
        # tenant-mutable) strictly under the configured base. Fail-closed:
        # anything not provably under base is skipped, never read.
        candidate = source_path or doc_id
        filepath = resolve_under_base(candidate, ingest_base)
        if filepath is None:
            print(f"  skip {doc_id}: path {candidate!r} not under TORTOISE_INGEST_BASE_DIR "
                  f"({ingest_base or '<unset>'}); refusing to read (fail-closed)")
            skipped += 1
            continue
        if not filepath.exists():
            print(f"  skip {doc_id}: file not found ({filepath})")
            skipped += 1
            continue

        text = filepath.read_text(encoding="utf-8")
        source_id = doc_id  # doc_id IS the source id (filename)

        # Idempotency gate
        from .idempotency import document_key
        extractor_version = api.agent_id or "extractor@0"
        result = api.begin_ingest(source_id, extractor_version,
                                  document_key(text), force=args.force)
        if result and result.skip:
            print(f"  skip {doc_id}: {result.reason}")
            skipped += 1
            continue

        print(f"  upgrading {doc_id} (doc_status={status}, needs_extraction={needs_ext}) …")

        # Emit DocumentCreated for metadata (idempotent via MERGE)
        from .extractor import extract_from_document
        from .ids import ulid
        from .domain_loader import resolve_domain_from_path

        is_doc, _ = _document_sections(text)
        if is_doc:
            fm = _parse_frontmatter(text)
            domain = fm.get("domain", fm.get("documentKnowledgeDomain", ""))
            if not domain:
                domain = resolve_domain_from_path(str(filepath))
            topics_raw = fm.get("topics", "")
            topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
            api.add_document(
                doc_id=source_id,
                title=fm.get("title", filepath.stem),
                document_kind=fm.get("type", fm.get("document_kind", "")),
                document_knowledge_domain=domain,
                authored_by=api.agent_id or "extractor@0",
                owned_by=fm.get("ownedBy", ""),
                managed_by=fm.get("managedBy", ""),
                governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
                doc_status=fm.get("doc_status", "captured"),
                format=_infer_format(filepath),
                version=fm.get("version", ""),
                createdAt=fm.get("created", None),
                updatedAt=fm.get("updated", None),
                topics=topics,
                summary=fm.get("summary", ""),
                session_id=fm.get("sessionId", ""),
                event_id=str(ulid()),
                source_path=str(filepath),
                needs_extraction=fm.get("needs_extraction", False),
            )

            stats = extract_from_document(
                text, source_id, api,
                point_model=build_model(args.point_model),
                relation_model=build_model(args.relation_model, reasoning=True),
                authored_by="pi-agent",
                max_sections=args.max_utterances,
                domain=args.domain,
            )
            print(f"    extracted {stats['points']} Points, {stats['operators']} operators")
            if stats.get("failed_sections"):
                print(f"    warning: {len(stats['failed_sections'])} sections failed extraction")
        else:
            print(f"    warning: not a Document (no ## headers) — extraction skipped")
            skipped += 1
            continue

        # doc_status flip via raw Cypher SET (not add_document — P0)
        proj.g.query(
            "MATCH (d:Document {id: $id}) SET d.doc_status = 'extracted'",
            params={"id": source_id},
        )
        print(f"    doc_status: {status} → extracted")
        upgraded += 1

    # Lazy EP re-propagation ONCE after all upgrades (#133 Task 3 — review P2:
    # N× full-graph EP is wasteful; one run after the loop is equivalent).
    if upgraded:
        _run_ep_propagation(proj)

    print(f"upgrade-all complete: {upgraded} upgraded, {skipped} skipped")


def build_model(spec: str, *, reasoning: bool = False):
    provider, _, model = spec.partition(":")
    if not model:
        raise SystemExit(f"bad model spec {spec!r}; expected provider:model")
    if provider == "mock":
        return MockModel(model)
    if provider == "ollama":
        # relation tier needs reasoning (think on); point tier is mechanical (off)
        return OllamaModel(id=model, think=reasoning)
    if provider not in _PROVIDERS:
        raise SystemExit(f"unknown provider {provider!r}; choose from "
                         f"{sorted(_PROVIDERS) + ['ollama', 'mock']}")
    base_url, api_key_env = _PROVIDERS[provider]
    return OpenAICompatModel(id=model, base_url=base_url, api_key_env=api_key_env)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tortoise live ingest")
    ap.add_argument("transcript", type=Path, nargs='?', default=None,
                    help="transcript file (not needed with --upgrade-all)")
    ap.add_argument("--point-model", default="mock:cheap")
    ap.add_argument("--relation-model", default="mock:reason")
    ap.add_argument("--db", type=str, required=True,
                    help="Docker URI (docker://:pass@host:port/graph) or file path")
    ap.add_argument("--log", type=Path, default=Path("events.jsonl"))
    ap.add_argument("--out", type=Path, default=Path("graph.html"))
    ap.add_argument("--resolution", action="store_true",
                    help="mark this ingest as a resolution event (auto-grounds)")
    ap.add_argument("--force", action="store_true", help="reprocess even if seen")
    ap.add_argument("--max-utterances", type=int, default=0,
                    help="cap utterances (0=all) — for exploration; relations don't scale yet")
    ap.add_argument("--domain", type=str, default=None,
                    help="domain ontology key for domain-specific kind values (e.g. product-strategy)")
    ap.add_argument("--semantic-extract", action="store_true",
                    help="run S7 semantic extraction (Subjects + Objects + aboutEntities) after points")
    ap.add_argument("--capture-metadata", action="store_true",
                    help="#125 metadata-only capture: emit Document + sessionCaptured Event, "
                         "SKIP LLM point extraction (topics/summary from frontmatter)")
    ap.add_argument("--upgrade", action="store_true",
                    help="#133: re-run full extraction on captured Document, "
                         "SET doc_status=extracted (requires transcript positional arg)")
    ap.add_argument("--upgrade-all", action="store_true",
                    help="#133: discover captured/needs_extraction Documents and upgrade each")
    args = ap.parse_args(argv)

    # #133: --upgrade requires a transcript file
    if args.upgrade and not args.transcript:
        raise SystemExit("--upgrade requires a transcript file to upgrade")
    # Normal ingest (no upgrade flags) → transcript is required
    if not args.upgrade and not args.upgrade_all and not args.transcript:
        raise SystemExit("the following arguments are required: transcript")

    text = None
    source_id = None
    if args.transcript:
        text = args.transcript.read_text(encoding="utf-8")
        source_id = args.transcript.name
    extractor = LLMExtractor(build_model(args.point_model),
                             build_model(args.relation_model, reasoning=True))

    log = EventLog(args.log)
    # Check DB accessibility before trying to connect.
    # Hard-reject relative paths cleanly (issue #176): a relative --db would
    # otherwise fall through to 'Docker unreachable' instead of the clear
    # hard-reject error (shared RELATIVE_PATH_ERROR).
    import os as _os
    if args.db and not _os.path.isabs(args.db) and not args.db.startswith("docker://"):
        from tortoise.config import RELATIVE_PATH_ERROR
        raise ValueError(RELATIVE_PATH_ERROR.format(path=args.db))
    if args.db.startswith("docker://"):
        proj = FalkorProjection.from_uri(args.db)
    elif args.db:
        # Embedded mode — FalkorDBLite creates the file on first connect.
        # (#493: the #176 migration added a "file must exist" gate here that
        # broke fresh embedded DBs by falling through to the Docker default.)
        proj = FalkorProjection(args.db)
    else:
        # No --db given — try Docker default as fallback
        # (Docker mode doesn't use a file, data lives in the container)
        import os
        docker_host = os.environ.get("FALKORDB_HOST", "localhost")
        docker_port = int(os.environ.get("FALKORDB_PORT", "16379"))
        docker_pass = os.environ.get("FALKORDB_PASSWORD") or None
        try:
            proj = FalkorProjection(host=docker_host, port=docker_port, password=docker_pass)
        except Exception:
            print(f"tortoise.ingest: No DB found at {args.db} and Docker unreachable. Run tortoise init first.")
            import sys
            sys.exit(0)
    api = EventAPI(log, initiated_by="extractor", agent_id=extractor.version,
                   projection=proj)
    try:
        # #133 --upgrade: re-run extraction on captured Document, then
        # SET doc_status='extracted' via raw Cypher (NOT add_document, which
        # always passes non-null doc_status and would overwrite 'captured').
        if args.upgrade:
            _do_upgrade(args.transcript, text, source_id, proj, api, args)
            # --upgrade is terminal: do NOT fall through to full ingest
            # (which would re-run begin_ingest + add_document and overwrite
            # the doc_status flip — review cycle 3 caught this).
            return

        # #133 --upgrade-all: discover captured/needs_extraction Documents
        # and upgrade each. No single transcript — early return after.
        if args.upgrade_all:
            _do_upgrade_all(proj, api, args)
            return

        # #125 capture-metadata skips begin_ingest — it is a DIFFERENT operation
        # from full extraction. Using begin_ingest here would write an
        # IngestStarted with the content-hash key, and a later full `tortoise
        # ingest` on the same file would see it as already-processed and SKIP
        # (the idempotency gotcha). Capture dedup is event-level (MERGE on
        # doc_id/sessionCaptured).
        if args.capture_metadata:
            result = None
        else:
            result = api.begin_ingest(source_id, extractor.version,
                                      document_key(text), force=args.force)
        if result and result.skip:
            print(f"skip: {result.reason} (run {result.run_id}); use --force to reprocess")
        else:
            if result:
                print(f"ingesting with {extractor.version} …")

            # S8: Extract document metadata from frontmatter → DocumentCreated event
            is_doc, _ = _document_sections(text)
            if is_doc:
                fm = _parse_frontmatter(text)
                # Determine domain: frontmatter first, then directory_map fallback (#6883)
                from .domain_loader import resolve_domain_from_path
                domain = fm.get("domain", fm.get("documentKnowledgeDomain", ""))
                if not domain:
                    domain = resolve_domain_from_path(str(args.transcript))
                # #125 capture metadata (topics/summary from frontmatter)
                topics_raw = fm.get("topics", "")
                topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
                summary = fm.get("summary", "")
                session_id = fm.get("sessionId", "")
                event_id = str(ulid())
                # #133: --capture-metadata defaults doc_status to 'captured'
                # (not 'draft') so captured vs extracted is queryable.
                # Frontmatter doc_status is authoritative on creation only.
                default_doc_status = "captured" if args.capture_metadata else "draft"
                api.add_document(
                    doc_id=source_id,
                    title=fm.get("title", args.transcript.stem),
                    document_kind=fm.get("type", fm.get("document_kind", "")),
                    document_knowledge_domain=domain,
                    authored_by=extractor.version,
                    owned_by=fm.get("ownedBy", ""),
                    managed_by=fm.get("managedBy", ""),
                    governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
                    doc_status=fm.get("doc_status", default_doc_status),
                    format=_infer_format(args.transcript),
                    version=fm.get("version", ""),
                    createdAt=fm.get("created", None),
                    updatedAt=fm.get("updated", None),
                    topics=topics,
                    summary=summary,
                    session_id=session_id,
                    event_id=event_id,
                    source_path=str(args.transcript),
                    needs_extraction=fm.get("needs_extraction", False),
                )
                if args.capture_metadata:
                    # #125 metadata-only: emit sessionCaptured Event with uses→Skill,
                    # SKIP LLM point extraction entirely
                    api.add_event(
                        event_id, "sessionCaptured",
                        subject="pi-agent",
                        object_name=source_id,
                        object_type="Document",
                        uses=[{"name": "tortoise-capture", "kind": "skill"}],
                    )
                    print(f"[capture-metadata] Document {source_id} + sessionCaptured {event_id} "
                          f"(topics={topics}, summary={summary[:40]!r}); extraction skipped")

            if is_doc and not args.capture_metadata:
                # Document mode: extract Points + IMPL/NAND via LLM, then propagate
                stats = extract_from_document(
                    text, source_id, api,
                    point_model=build_model(args.point_model),
                    relation_model=build_model(args.relation_model, reasoning=True),
                    authored_by="pi-agent",
                    max_sections=args.max_utterances,
                    domain=args.domain,
                )
                print(f"extracted {stats['points']} Points, {stats['operators']} operators "
                      f"from {stats['sections']} sections")
                if stats.get("failed_sections"):
                    print(f"warning: {len(stats['failed_sections'])} sections failed extraction")
                # Post-extraction: propagate confidence using factor-graph EP
                # (replaces BFS propagate_shock — bidirectional, quadrature-based)
                try:
                    op_ids = [r[0] for r in proj.g.query(
                        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
                    ).result_set]
                    if op_ids:
                        from tortoise.ep import TortoiseEP
                        # Build evidence priors from extractor confidence values
                        claim_rows = proj.g.query(
                            "MATCH (n:Point) "
                            "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
                            "RETURN n.id, coalesce(n.confidence, 0.5)"
                        ).result_set
                        evidence = {}
                        for cid, conf_raw in claim_rows:
                            conf = float(conf_raw)
                            evidence[cid] = TortoiseEP.confidence_to_prior(conf)
                        ep = proj.get_ep() if hasattr(proj, 'get_ep') else TortoiseEP(proj)
                        n_iter, converged = ep.run(op_ids, max_hops=3, evidence=evidence)
                        print(f"EP: {'converged' if converged else 'max iter'} in {n_iter} iterations"
                              f" ({len(evidence)} priors from extractor confidence)")
                except Exception as e:
                    # Only suppress DB-availability errors; let logic errors propagate.
                    msg = str(e).lower()
                    if any(kw in msg for kw in ("connection refused", "connection reset",
                          "errno 61", "errno 111", "cannot connect", "no route to host")):
                        print(f"tortoise.ingest: EP propagation skipped (DB unavailable: {e})")
                    else:
                        print(f"tortoise.ingest: EP propagation failed: {e}")
                        raise

                # S7: Semantic extraction (Subjects + Objects + aboutEntities)
                if args.semantic_extract and is_doc:
                    ent_stats = extractor.extract_entities(
                        text, source_id, api,
                        domain=args.domain,
                    )
                    print(f"entities: {ent_stats['subjects']} Subjects, "
                          f"{ent_stats['objects']} Objects")
            else:
                extractor.run(text, source_id, api, max_utterances=args.max_utterances)

        if args.resolution:
            api.add_point(f"Resolved: {source_id}",
                          provenance(source_id, None, None, speaker="system",
                                     extracted_by=extractor.version),
                          pointKind="resolution-event")
        points = fold(log.read_all())
        statements, operators = split(points)
        args.out.write_text(render(points, title=f"Tortoise — {source_id}"),
                            encoding="utf-8")
        print(f"points : {len(statements)} statements, {len(operators)} operators")
        print(f"graph  : {args.db}  (Cypher-queryable)")
        print(f"render : {args.out}")
    finally:
        proj.close()


if __name__ == "__main__":
    main()
