"""Extraction pipeline — document → frontmatter → classify → enrich → ingest.

Orchestrates the three tools into a single pipeline:
  1. Derive structural frontmatter from path (adapted from operations/memory/derive_frontmatter.py)
  2. Classify documentKind from path heuristics (tortoise/tortoise/doc_classify.py)
  3. Optionally enrich via LLM summary+tags (operations/memory/enrich_frontmatter.py)
  4. Ingest into FalkorDB via existing extractor pipeline

Usage:
    from tortoise.extraction_pipeline import ExtractionPipeline
    pipeline = ExtractionPipeline()
    pipeline.process(doc_path, api, enrich=False)

The pipeline is idempotent: re-running a document with the same content is a no-op.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from tortoise.doc_classify import classify as classify_kind

# ponytail: adapted from operations/memory/derive_frontmatter.py —
# frontmatter derivation for any path, not just docs/teams/
_FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)
_DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})')

# Subdirectory → domain mapping (extended for Tortoise doc store paths)
_DIR_DOMAIN_MAP: dict[str, str] = {
    "conversations": "operations",
    "sessions": "operations",
    "transcripts": "operations",
    "research": "growth",
    "entities": "data",
    "decisions": "capability",
    "adrs": "capability",
    "operations": "operations",
    "experiments": "product",
    "meetings": "operations",
    "strategy": "strategy",
    "vision": "strategy",
    "plans": "capability",
    "planning": "capability",
    "roadmap": "product",
    "postmortems": "operations",
    "reflect": "operations",
    # Extended for arbitrary repo paths (GitHub doc indexer)
    "docs": "data",
    "src": "engineering",
    "lib": "engineering",
    "tests": "engineering",
    "test": "engineering",
    "spec": "engineering",
    "api": "engineering",
    "components": "ux",
    "pages": "ux",
    "layouts": "ux",
    "hooks": "engineering",
    "utils": "engineering",
    "scripts": "operations",
    "migrations": "engineering",
    "content": "growth",
    "blog": "growth",
    "guides": "growth",
    "tutorials": "growth",
    "skills": "capability",
    "workflows": "capability",
    "policies": "legal",
    "legal": "legal",
    "adrs": "capability",
    "decisions": "capability",
    "epics": "capability",
    "data": "data",
    "schemas": "data",
    "ontology": "data",
}


def _parse_frontmatter(text: str) -> dict[str, str]:
    """Extract YAML-like frontmatter as a flat dict."""
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fm: dict[str, str] = {}
    for line in m.group(1).split('\n'):
        kv = line.split(':', 1)
        if len(kv) != 2:
            continue
        k = kv[0].strip()
        v = kv[1].strip().strip('"').strip("'")
        if v.endswith('#'):
            v = v.rsplit('#', 1)[0].strip()
        if k and v:
            fm[k] = v
    return fm


def _extract_h1(text: str) -> str:
    """Extract the first H1 heading."""
    for line in text.split('\n'):
        if line.startswith('# ') and not line.startswith('## '):
            return line[2:].strip()
    return "Untitled"


def _derive_date(filename: str) -> str:
    """Extract date from filename. Returns '' if not found."""
    m = _DATE_RE.match(filename)
    if m:
        return m.group(1)
    m = re.search(r'(\d{4}-\d{2}-\d{2})', filename)
    return m.group(1) if m else ''


def _derive_domain(path_parts: tuple[str, ...]) -> str:
    """Derive domain from path subdirectories (deepest match wins)."""
    for part in reversed(path_parts):
        if part in _DIR_DOMAIN_MAP:
            return _DIR_DOMAIN_MAP[part]
    return ""


def build_frontmatter(filepath: Path, doc_text: str) -> str:
    """Build complete frontmatter block from path heuristics.

    Returns a YAML-like frontmatter string suitable for prepending or
    replacing existing frontmatter. Derives: title (H1), type (documentKind),
    domain, created (filename date), status.
    """
    parts = filepath.parts
    filename = filepath.name

    title = _extract_h1(doc_text)
    doc_kind = classify_kind(filepath)
    domain = _derive_domain(parts)
    created = _derive_date(filename)
    now_iso = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    lines = [
        "---",
        f'title: "{title}"',
        f'type: {doc_kind}',
        f'domain: {domain}',
        'status: seedling',
        'tags: []',
        'summary: ""',
        f'created: {created or now_iso}',
        f'updated: {created or now_iso}',
        "---",
    ]
    return "\n".join(lines)


def apply_frontmatter(doc_text: str, filepath: Path) -> str:
    """Ensure document has frontmatter. Derives if missing, preserves existing."""
    existing = _parse_frontmatter(doc_text)

    # If already has title + type, keep existing (don't overwrite)
    if existing.get("title") and existing.get("type"):
        return doc_text

    new_fm = build_frontmatter(filepath, doc_text)

    if _FRONTMATTER_RE.match(doc_text):
        # Replace existing (incomplete) frontmatter
        end_idx = doc_text.index("---", doc_text.index("---") + 3) + 3
        return new_fm + "\n" + doc_text[end_idx:]
    else:
        # Prepend frontmatter
        return new_fm + "\n\n" + doc_text.lstrip("\n")


class ExtractionPipeline:
    """Document → frontmatter → classify → (enrich) → ingest pipeline."""

    def __init__(self, enrich: bool = False):
        self.enrich = enrich

    def process_file(self, filepath: str | Path, api, *,
                     enrich: bool | None = None) -> dict:
        """Process a single document file through the pipeline.

        1. Read document
        2. Derive + apply frontmatter
        3. Classify documentKind
        4. Optionally enrich via LLM
        5. Ingest into FalkorDB via existing extractor

        Returns stats dict: {points, operators, sections, documentKind, enriched}
        """
        fp = Path(filepath).resolve()
        text = fp.read_text(encoding="utf-8")

        # Step 1-2: Derive + apply frontmatter
        updated_text = apply_frontmatter(text, fp)
        if updated_text != text:
            fp.write_text(updated_text, encoding="utf-8")

        # Step 3: Classify (already done in apply_frontmatter, but re-derive for stats)
        doc_kind = classify_kind(fp)

        # Step 4: Optionally enrich via LLM
        do_enrich = enrich if enrich is not None else self.enrich
        enriched = False
        if do_enrich:
            enriched = self._run_enrich(fp)

        # Step 5: Ingest — use existing extract_from_document
        source_id = fp.name
        fm = _parse_frontmatter(updated_text)

        # Create DocumentCreated event
        api.add_document(
            doc_id=source_id,
            title=fm.get("title", fp.stem),
            document_kind=fm.get("type", doc_kind),
            document_knowledge_domain=fm.get("domain", ""),
            authored_by="extraction-pipeline",
            owned_by=fm.get("ownedBy", ""),
            managed_by=fm.get("managedBy", ""),
            governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
            doc_status=fm.get("status", fm.get("doc_status", "draft")),
            format="markdown",
            version=fm.get("version", ""),
            createdAt=fm.get("created", None),
            updatedAt=fm.get("updated", None),
        )

        # Extract entities and claims
        from tortoise.extractor import extract_from_document as doc_extract
        from tortoise.ingest import build_model

        point_spec = "mock:cheap"
        rel_spec = "mock:reason"

        stats = doc_extract(
            updated_text, source_id, api,
            point_model=build_model(point_spec),
            relation_model=build_model(rel_spec, reasoning=True),
            authored_by="extraction-pipeline",
        )

        # Propagate confidence via EP
        self._run_ep(api)

        return {
            "documentKind": doc_kind,
            "enriched": enriched,
            "points": stats.get("points", 0),
            "operators": stats.get("operators", 0),
            "sections": stats.get("sections", 0),
        }

    def process_text(self, text: str, source_id: str, api, *,
                     doc_kind: str = "transcript",
                     enrich: bool | None = None) -> dict:
        """Process raw text (no file) through the pipeline.

        Used by auto-capture when conversation text is in memory, not on disk.
        Skips frontmatter derivation (no path to derive from).
        """
        # Step 3: Classify already provided
        # Step 4: Optionally enrich — skipped (no file to modify)

        # Step 5: Create DocumentCreated + extract
        api.add_document(
            doc_id=source_id,
            title=source_id,
            document_kind=doc_kind,
            document_knowledge_domain="operations",
            authored_by="auto-capture",
            doc_status="captured",
            format="markdown",
            createdAt=datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        )

        from tortoise.extractor import extract_from_document as doc_extract
        from tortoise.ingest import build_model

        point_spec = "mock:cheap"
        rel_spec = "mock:reason"

        stats = doc_extract(
            text, source_id, api,
            point_model=build_model(point_spec),
            relation_model=build_model(rel_spec, reasoning=True),
            authored_by="auto-capture",
        )

        self._run_ep(api)

        return {
            "documentKind": doc_kind,
            "enriched": False,
            "points": stats.get("points", 0),
            "operators": stats.get("operators", 0),
            "sections": stats.get("sections", 0),
        }

    def _run_enrich(self, filepath: Path) -> bool:
        """Run LLM enrichment on a file. Returns True if enriched."""
        import subprocess
        import sys

        enrich_script = Path(__file__).resolve().parents[3] / "operations" / "memory" / "enrich_frontmatter.py"
        if not enrich_script.exists():
            return False

        try:
            result = subprocess.run(
                [sys.executable, str(enrich_script), str(filepath),
                 "--limit", "1", "--delay", "0"],
                capture_output=True, timeout=30,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_ep(self, api) -> None:
        """Run EP confidence propagation if projection is available."""
        try:
            proj = api.projection
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
            evidence = {cid: TortoiseEP.confidence_to_prior(float(conf))
                        for cid, conf in claim_rows}
            ep = proj.get_ep() if hasattr(proj, 'get_ep') else TortoiseEP(proj)
            ep.run(op_ids, max_hops=3, evidence=evidence)
        except Exception:
            pass  # ponytail: EP is best-effort
