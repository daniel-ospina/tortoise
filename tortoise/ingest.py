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
import re
from pathlib import Path

from .api import EventAPI, provenance
from .extractor import LLMExtractor, MockModel, _document_sections, extract_from_document
from .idempotency import document_key
from .log import EventLog
from .models import OllamaModel, OpenAICompatModel
from .projection import FalkorProjection, fold, split
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
    fm: dict[str, str] = {}
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
            fm[k] = v
    return fm


def _infer_format(filepath: Path) -> str:
    """Map file extension to format field."""
    ext = filepath.suffix.lower()
    return {".md": "markdown", ".jsonl": "jsonl", ".yaml": "yaml",
            ".yml": "yaml", ".cypher": "cypher"}.get(ext, "other")


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
    ap.add_argument("transcript", type=Path)
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
    args = ap.parse_args(argv)

    text = args.transcript.read_text(encoding="utf-8")
    source_id = args.transcript.name
    extractor = LLMExtractor(build_model(args.point_model),
                             build_model(args.relation_model, reasoning=True))

    log = EventLog(args.log)
    # Check DB accessibility before trying to connect
    if args.db.startswith("docker://"):
        proj = FalkorProjection.from_uri(args.db)
    elif Path(args.db).exists():
        proj = FalkorProjection(args.db)
    else:
        # DB file doesn't exist — try Docker default as fallback
        # (Docker mode doesn't use a file, data lives in the container)
        import os
        docker_host = os.environ.get("FALKORDB_HOST", "localhost")
        docker_port = int(os.environ.get("FALKORDB_PORT", "6379"))
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
        result = api.begin_ingest(source_id, extractor.version,
                                  document_key(text), force=args.force)
        if result.skip:
            print(f"skip: {result.reason} (run {result.run_id}); use --force to reprocess")
        else:
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
                api.add_document(
                    doc_id=source_id,
                    title=fm.get("title", args.transcript.stem),
                    document_kind=fm.get("type", fm.get("document_kind", "")),
                    document_knowledge_domain=domain,
                    authored_by=extractor.version,
                    owned_by=fm.get("ownedBy", ""),
                    managed_by=fm.get("managedBy", ""),
                    governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
                    doc_status=fm.get("doc_status", "draft"),
                    format=_infer_format(args.transcript),
                    version=fm.get("version", ""),
                    createdAt=fm.get("created", None),
                    updatedAt=fm.get("updated", None),
                )

            if is_doc:
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
            api.add_point(f"Resolved: {source_id}", "resolution-event",
                          provenance(source_id, None, None, speaker="system",
                                     extracted_by=extractor.version))
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
