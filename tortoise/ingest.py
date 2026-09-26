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

Builder capability catalog note (#2004 W8 / epic #1976 DM-5): this module is
referenced in the builder capability catalog (onboarding) — catalog module
'Document indexer' (corpus ingestion) — tortoise/tool_registry.py
CAPABILITY_CATALOG. If you add or rename an extractor/indexer, update the
catalog reference.
"""
from __future__ import annotations  # noqa: I001

import argparse
import os
from pathlib import Path

from .api import EventAPI, provenance
from .extractor import LLMExtractor, MockModel, _document_sections, extract_from_document
from .idempotency import document_key
from .log import EventLog
from .models import OllamaModel, OpenAICompatModel
from .projection import FalkorProjection, fold, split
from .ids import content_hash, ulid
from .render import render

# OpenAI-compatible providers (Ollama is handled separately via its native API).
_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}


# -- Frontmatter parsing for S8 Document Indexer (#6890) ------------------------
# Canonical boundary regex lives in tortoise.file_indexer (canonical home —
# this module keeps the module-level ``_FM_RE`` name as a back-compat alias).
# NOTE: this module's ``_parse_frontmatter`` is the deliberate line-by-line
# parser (no PyYAML dependency) — a DIFFERENT function from
# file_indexer.parse_frontmatter; only the boundary regex is shared.
from .file_indexer import _FM_RE  # noqa: E402


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

def _belief_emitter(api):
    """#2884 FIX-5: a ``TortoiseEP`` emitter that journals belief write-backs.

    ``_run_ep_propagation`` builds a bare ``TortoiseEP`` with NO emitter, so
    every posterior/confidence it wrote was durable only until the next JSONL
    wipe+rebuild — the exact silent-loss class #2884 fixes, on the
    ingest-propagation path. This CLI builds a raw ``FalkorProjection`` (no SDK
    handle), but it holds the SAME ``EventAPI``/``EventLog`` the SDK emitter
    writes and ``rebuild_all`` replays. #2884 A6: the record is appended
    through ``EventAPI.emit_belief`` — the ONE ingest envelope (``_emit``),
    already guarded so a log-write failure cannot crash the CLI after the
    graph write committed. An earlier revision hand-built a THIRD envelope
    here; routing through the api removes that second implementation of the
    record shape.
    """
    if api is None:
        return None

    def emit(type_: str, **payload) -> None:
        if type_ != "ConfidenceChanged":
            return  # this seam journals belief write-backs ONLY
        api.emit_belief(type_, **payload)

    return emit


def _run_ep_propagation(proj, api=None, *, label: str = "EP"):
    """#133: Run EP confidence propagation after ingest/upgrade.

    Lazy (on-demand) — called only after an upgrade/ingest, not on every
    ingest. Note: EP requires the full factor graph for correct belief
    propagation, so this iterates all operators (the graph is small at this
    scale).

    #1157 calibration posture: evidence priors are synthesized ONLY from
    claims with a STORED confidence (extractor confidence or a prior EP
    posterior) — never the silent ``coalesce(n.confidence, 0.5)`` default.
    Beta(1,1) uniform priors on uncalibrated claims are the exact silent
    uncalibrated-EP pattern #7478 targets. Claims without stored confidence
    are excluded and counted in the output line. When NO claim has stored
    confidence the run is refused (skipped with a loud flag) — no EP on a
    fully uncalibrated graph.
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
            "WHERE n.is_operator = false "
            "RETURN n.id, n.confidence"
        ).result_set
        evidence = {}
        uncalibrated = 0
        for cid, conf_raw in claim_rows:
            if conf_raw is None:
                uncalibrated += 1
                continue
            try:
                conf = float(conf_raw)
            except (TypeError, ValueError):
                uncalibrated += 1
                continue
            evidence[cid] = TortoiseEP.confidence_to_prior(conf)
        if not evidence:
            print(f"  {label}: skipped — no stored confidence on any claim "
                  f"(graph uncalibrated); set credibility / baselines first")
            return
        ep = (proj.get_ep() if hasattr(proj, 'get_ep')
              else TortoiseEP(proj, emit=_belief_emitter(api)))
        n_iter, converged = ep.run(op_ids, max_hops=3, evidence=evidence)
        suffix = (f" ({uncalibrated} uncalibrated claims excluded)"
                  if uncalibrated else "")
        print(f"  {label}: {'converged' if converged else 'max iter'} in "
              f"{n_iter} iterations ({len(evidence)} priors){suffix}")
    except Exception as e:
        msg = str(e).lower()
        if any(kw in msg for kw in ("connection refused", "connection reset",
              "errno 61", "errno 111", "cannot connect", "no route to host")):
            print(f"  {label}: propagation skipped (DB unavailable: {e})")
        else:
            print(f"  {label}: propagation failed: {e}")
            raise

def _do_upgrade(transcript, text, source_id, proj, api, args):
    """Upgrade a single Document: re-run extraction, clear needs_extraction.

    D10 (ONTOLOGY v3.15 §4.4): a document is a :Source and ``doc_status`` is
    RETIRED — liveness is a read of the extracted entities, and the only
    stored extraction signal is ``needs_extraction``.

    Guards:
    - Already-extracted (needs_extraction false) → no-op
    - Non-Document transcript → graceful "not a Document" error
    - Uses begin_ingest for idempotency (content-hash + extractor version)
    - needs_extraction cleared via raw Cypher SET after the extraction attempt
      (``--upgrade-all`` discovers on this flag, so clearing it preserves the
      discovery loop)
    """
    # 1. Check the document Source exists + its extraction signal.
    rows = proj.g.query(
        "MATCH (s:Source {url: $id}) "
        "WHERE s.documentKind IS NOT NULL "
        "RETURN coalesce(s.needs_extraction, false) AS needs_extraction",
        params={"id": source_id},
    ).result_set
    if not rows:
        print(f"not a Document: {source_id} (no document Source with this url)")
        return
    if not rows[0][0]:
        print(f"doc already extracted, skipped: {source_id}")
        return

    # 2. Idempotency gate — re-extract only if not already processed
    #    at this extractor version (begin_ingest key: content-hash + version)
    result = api.begin_ingest(source_id, api.agent_id or "extractor@0",
                              document_key(text), force=args.force)
    if result and result.skip:
        print(f"upgrade skip: {result.reason} (run {result.run_id}); use --force to reprocess")
        return

    print(f"upgrading {source_id} …")

    # 3. Re-run full extraction (the same path as normal full ingest)
    from .extractor import extract_from_document  # noqa: I001
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
        api.add_document(
            doc_id=source_id,
            title=fm.get("title", transcript.stem),
            document_kind=fm.get("type", fm.get("document_kind", "")),
            document_knowledge_domain=domain,
            authored_by=api.agent_id or "extractor@0",
            owned_by=fm.get("ownedBy", ""),
            managed_by=fm.get("managedBy", ""),
            governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
            format=_infer_format(transcript),
            version=fm.get("version", ""),
            content_hash=content_hash(text),
            createdAt=fm.get("created", None),
            updatedAt=fm.get("updated", None),
            topics=topics,
            summary=fm.get("summary", ""),
            session_id=fm.get("sessionId", ""),
            event_id=str(ulid()),
            source_path=str(transcript),
            needs_extraction=True,
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

    # 4. Mark extracted: clear the needs_extraction signal (the D10
    #    replacement for the retired doc_status flip). add_document reads the
    #    flag freshly above, so a raw SET is correct here.
    proj.g.query(
        "MATCH (s:Source {url: $id}) SET s.needs_extraction = false",
        params={"id": source_id},
    )
    print("  needs_extraction: true → false")

    # 5. Lazy EP re-propagation on affected subgraph (#133 Task 3)
    _run_ep_propagation(proj, api)


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
    """Discover needs_extraction document Sources and upgrade each.

    D10 (ONTOLOGY v3.15 §4.4): ``doc_status`` is retired — the only stored
    extraction signal is ``needs_extraction``, set true by the
    ``--capture-metadata`` path and cleared here after a successful extraction.

    Uses inline Cypher via proj.g.query (no SDK method needed — plan §Task 2).
    Loop-safe: each attempt gated by begin_ingest key (content-hash + extractor
    version) so identical content is a no-op on re-run.

    #329 containment: the file read path (s.sourcePath OR s.url — both are
    tenant-mutable graph state) is resolved strictly under TORTOISE_INGEST_BASE_DIR;
    anything not provably under base is SKIPPED (fail-closed), never read.
    """
    from .security import resolve_under_base
    ingest_base = _resolve_ingest_base()
    if ingest_base is None:
        print("  warning: TORTOISE_INGEST_BASE_DIR not set — upgrade-all is fail-closed; "
              "documents are skipped unless their path resolves under a configured base. "
              "Set TORTOISE_INGEST_BASE_DIR to your corpus root to enable re-upgrade.")

    # Discover document Sources awaiting extraction (D10: needs_extraction is
    # the signal; documentKind IS NOT NULL restricts to documents).
    rows = proj.g.query(
        "MATCH (s:Source) "
        "WHERE s.documentKind IS NOT NULL "
        "AND coalesce(s.needs_extraction, false) = true "
        "RETURN s.url, coalesce(s.sourcePath, s.url) AS sourcePath, "
        "coalesce(s.needs_extraction, false) AS needs_extraction"
    ).result_set

    if not rows:
        print("upgrade-all: no document Sources found with needs_extraction=true")
        return

    print(f"upgrade-all: {len(rows)} document(s) to upgrade")

    upgraded = 0
    skipped = 0
    for doc_id, source_path, needs_ext in rows:
        # #329: resolve the candidate (sourcePath OR s.url — BOTH are
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

        print(f"  upgrading {doc_id} (needs_extraction={needs_ext}) …")

        # Emit DocumentCreated for metadata (idempotent via MERGE)
        from .extractor import extract_from_document  # noqa: I001
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
                format=_infer_format(filepath),
                version=fm.get("version", ""),
                content_hash=content_hash(text),
                createdAt=fm.get("created", None),
                updatedAt=fm.get("updated", None),
                topics=topics,
                summary=fm.get("summary", ""),
                session_id=fm.get("sessionId", ""),
                event_id=str(ulid()),
                source_path=str(filepath),
                needs_extraction=True,
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
            print(f"    warning: not a Document (no ## headers) — extraction skipped")  # noqa: F541
            skipped += 1
            continue

        # Mark extracted: clear the needs_extraction signal (the D10
        # replacement for the retired doc_status flip).
        proj.g.query(
            "MATCH (s:Source {url: $id}) SET s.needs_extraction = false",
            params={"id": source_id},
        )
        print("    needs_extraction: true → false")
        upgraded += 1

    # Lazy EP re-propagation ONCE after all upgrades (#133 Task 3 — review P2:
    # N× full-graph EP is wasteful; one run after the loop is equivalent).
    if upgraded:
        _run_ep_propagation(proj, api)

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
                         f"{sorted(_PROVIDERS) + ['ollama', 'mock']}")  # noqa: RUF005
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
                    help="#125 metadata-only capture: emit document Source + "
                         "sessionCaptured Event, SKIP LLM point extraction "
                         "(sets needs_extraction=true; topics/summary from frontmatter)")
    ap.add_argument("--upgrade", action="store_true",
                    help="#133: re-run full extraction on a captured document "
                         "Source (requires transcript positional arg)")
    ap.add_argument("--upgrade-all", action="store_true",
                    help="#133: discover needs_extraction document Sources and upgrade each")
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
    # Hard-reject relative paths cleanly (issue #176): a relative --db would
    # otherwise fall through to 'Docker unreachable' instead of the clear
    # hard-reject error (shared RELATIVE_PATH_ERROR).
    import os as _os
    if args.db and not _os.path.isabs(args.db) and not args.db.startswith("docker://"):
        from tortoise.config import RELATIVE_PATH_ERROR
        raise ValueError(RELATIVE_PATH_ERROR.format(path=args.db))
    if args.db.startswith("docker://"):
        proj = FalkorProjection.from_uri(args.db)
    else:
        # Embedded mode: redislite creates the DB server at the given path on
        # first use (the file materializes on close — a fresh path NEVER
        # satisfies a pre-.exists() check). The .exists() gate + Docker
        # fallback added in #26 made first-run `tortoise ingest --db <new>`
        # silently no-op (sys.exit(0)) instead of creating the DB, breaking
        # the create-if-absent contract the CLI had before #26 and that
        # `tortoise init` still honors. Restored: unconditional construction.
        proj = FalkorProjection(args.db)
    api = EventAPI(log, initiated_by="extractor", agent_id=extractor.version,
                   projection=proj)
    try:
        # #133 --upgrade: re-run extraction on a captured document Source.
        # D10 (ONTOLOGY §4.4): the extraction signal is needs_extraction (the
        # retired doc_status flip is gone).
        if args.upgrade:
            _do_upgrade(args.transcript, text, source_id, proj, api, args)
            # --upgrade is terminal: do NOT fall through to full ingest
            # (which would re-run begin_ingest + add_document and re-set the
            # needs_extraction flag — review cycle 3 caught this).
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
                # #133 / D10: --capture-metadata SKIPS extraction, so it
                # marks the document Source needs_extraction=true — the
                # --upgrade / --upgrade-all discovery signal (doc_status is
                # retired, ONTOLOGY §4.4). A normal full ingest extracts
                # immediately, so it leaves the flag false unless frontmatter
                # requests otherwise.
                needs_extraction = (True if args.capture_metadata
                                    else bool(fm.get("needs_extraction", False)))
                api.add_document(
                    doc_id=source_id,
                    title=fm.get("title", args.transcript.stem),
                    document_kind=fm.get("type", fm.get("document_kind", "")),
                    document_knowledge_domain=domain,
                    authored_by=extractor.version,
                    owned_by=fm.get("ownedBy", ""),
                    managed_by=fm.get("managedBy", ""),
                    governing_agreement=fm.get("governedBy", fm.get("governingAgreement", "")),
                    format=_infer_format(args.transcript),
                    version=fm.get("version", ""),
                    # #5422: the version anchor is written on the path that
                    # actually EXTRACTS. `--capture-metadata` deliberately
                    # skips extraction (it emits no Points to anchor), so it
                    # passes no hash and the fold's preserve gate leaves any
                    # existing anchor exactly as it was.
                    content_hash=(None if args.capture_metadata
                                  else content_hash(text)),
                    createdAt=fm.get("created", None),
                    updatedAt=fm.get("updated", None),
                    topics=topics,
                    summary=summary,
                    session_id=session_id,
                    event_id=event_id,
                    source_path=str(args.transcript),
                    needs_extraction=needs_extraction,
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
                # (replaces BFS propagate_shock — bidirectional, quadrature-based).
                # #1157: shared helper — priors from stored confidence only;
                # refuses (loud flag) when the graph has none.
                _run_ep_propagation(proj, api, label="EP")

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
