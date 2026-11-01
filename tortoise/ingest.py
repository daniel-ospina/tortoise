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
#
# THE SESSION-EXTRACTION PROVIDER REGISTRY (#8133). A provider registered
# here becomes visible to the consumers of the KEY SET, which read this mapping
# rather than keeping a list of their own:
#   * ``hosted_api._llm_provider_keys`` (the hosted ``no_provider`` gate),
#   * ``test_session_extraction_modes.test_the_lane_gate_registry_covers_the_v2_extractor_registry``
#     and the #1197 drift guard, which demand the two entries below.
# ⚠️ THREE registration sites, not one — this mapping plus
# ``sdk._SESSION_LLM_PROVIDER_PRIORITY`` (which provider serves a capture) and
# ``sdk._SESSION_LLM_DEFAULT_MODELS`` (which model id is sent).  Adding a
# provider HERE alone registers its KEY and leaves ``_build_session_llm_extractor``
# raising ``KeyError`` on the missing default model: the #1197 guard catches the
# first omission, the second is caught only by an end-to-end venice-only test
# (tests/test_session_extraction_modes.py).
# Before #8133 ``model_adapters.resolve_extractor_provider`` (the v2 extractor's
# registry) carried ``venice`` and this mapping did not, so a venice-only
# deployment reported ``('venice', ['venice'])`` there while every lane gate said
# "no provider" and the capture stored its turns WITHOUT extracting — silently,
# while the receipt blamed a key the operator had configured.
_PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "deepseek": ("https://api.deepseek.com/v1", "DEEPSEEK_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    # venice.ai — the #1549 3-provider rotation lane, OpenAI-compatible at
    # ``/v1`` (``models.OpenAICompatModel`` appends ``/chat/completions``).
    # Registered on main by ``model_adapters.resolve_extractor_provider``, whose
    # ``if vz_key:`` branch returns venice as a STANDALONE PRIMARY — so #8133
    # was this entry missing, not a product decision to keep venice
    # fallback-only.
    "venice": ("https://api.venice.ai/api/v1", "VENICE_API_KEY"),
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


def _frontmatter_kind(fm: dict) -> str | None:
    """The document genre from frontmatter, or ``None`` when it names none.

    ``None`` rather than ``""`` DELIBERATELY (#5140 review round 5). The fold's
    genre clause is ``ELSE coalesce($dk, s.documentKind, '')``: an empty string
    is a VALUE, so it OVERWROTE a stored genre on every re-ingest — measured
    pre-fix, a re-extracted transcription became ``documentKind=''``, and
    ``quota.DOCUMENT_METER_PREDICATE`` counts ``''`` as metered while
    ``'transcript'`` is exempt, so a sweep could silently move a captured
    session INTO the ``documents`` meter (the inverse of the demotion #9536
    closed, and just as wrong: the genre is the identity key). ``None`` lets
    the coalesce preserve a stored genre, while a CREATE still lands the
    non-null ``''`` default the documents meter requires (D10 B3).
    """
    return fm.get("type") or fm.get("document_kind") or None


def _infer_format(filepath: Path) -> str:
    """Map file extension to format field."""
    ext = filepath.suffix.lower()
    return {".md": "markdown", ".jsonl": "jsonl", ".yaml": "yaml",
            ".yml": "yaml", ".cypher": "cypher"}.get(ext, "other")


# ── #133 upgrade helpers ────────────────────────────────────────────

#: The belief write-back families ``TortoiseEP`` routes through its ``emit``
#: callable and this seam therefore journals. #2884 added the node arm
#: (``ConfidenceChanged``); #5380 added the edge-message arm
#: (``EdgeMessageChanged``). A type outside this set is a foreign event and is
#: ignored — the seam is a belief write-back journal, not a general sink.
_JOURNALED_BELIEF_TYPES = ("ConfidenceChanged", "EdgeMessageChanged")


def _belief_emitter(api):
    """#2884 FIX-5 / #5380: a ``TortoiseEP`` emitter that journals belief
    write-backs — the node family (``ConfidenceChanged``) and the
    edge-message family (``EdgeMessageChanged``).

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

    #5380: ``TortoiseEP`` calls this same callable for BOTH belief families,
    so the allow-list below is load-bearing. Dropping the edge type is the
    silent half-loss #5380 removes, re-introduced on the ingest/upgrade EP
    lane: the graph still gets all four edge slots (``_flush_cache`` writes
    them live) but the journal records none, so a rebuild of that journal
    loses the edge-level belief state. The raw ``FalkorProjection`` this CLI
    holds folds the record through ``apply()``'s arm.
    """
    if api is None:
        return None

    def emit(type_: str, **payload) -> None:
        if type_ not in _JOURNALED_BELIEF_TYPES:
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

def _document_is_pending(needs_extraction, doc_status, *,
                         unmarked_is_pending: bool = True,
                         other_legacy_is_pending: bool = False) -> bool:
    """#5140 P1: is this document Source awaiting extraction? ONE definition.

    ``_do_upgrade`` (one document the operator named) READS THIS FUNCTION. The
    corpus sweep (``_do_upgrade_all``) reads its Cypher mirror
    ``_discover_pending_documents``, because it has to ask the question of the
    whole graph in one query; the two are kept arm-for-arm in step by
    ``tests/test_5140_pending_extraction.py::test_python_rule_and_cypher_twin_agree``,
    and the discipline is the one the retrieval legs follow for
    ``documentKind`` (#5140 finding 1): two paths that each spell a rule out
    drift apart. ⛔ The sweep does not call this function (review round 10) — a
    comment saying it does is how an auditor concludes the rule has one home
    when it has two.

    D10 retired ``doc_status`` and left ``needs_extraction`` as the only stored
    signal — but it also moved the DEFAULT. Pre-D10 ``_do_upgrade`` read
    ``coalesce(d.doc_status, 'captured')`` (absent ⇒ CAPTURED ⇒ pending) and
    ``_do_upgrade_all`` discovered ``doc_status='captured' OR needs_extraction
    = true``; the D10 code half became ``coalesce(s.needs_extraction, false)``
    / ``= true``, which reads absence as EXTRACTED and drops the retired marker
    from discovery entirely. The pre-D10 metadata-only population — stamped
    ``doc_status='captured'`` beside the old writer's boilerplate
    ``needs_extraction=false`` — then vanished from both paths, and
    ``--upgrade`` claimed *"already extracted"* about a document it had never
    extracted.

    So the rule is read off what the old signals SAID rather than invented. The
    pre-D10 writer stamped BOTH: ``--capture-metadata`` set
    ``doc_status='captured'`` **and** ``needs_extraction=false`` (its default),
    so the stored ``false`` on a legacy node is boilerplate, not a claim — while
    ``doc_status`` was the live authority at the time. The same code flipped
    ``doc_status`` to ``'extracted'`` on completion, and D10 replaced that flip
    with the ``needs_extraction`` clear (``_do_upgrade``'s final SET), so a
    post-D10 node has no ``doc_status`` at all.

    1. ``doc_status='captured'`` → PENDING, even beside a stored
       ``needs_extraction=false``. ⛔ WHAT THIS ARM CAN AND CANNOT REACH (measured
       at #5140 review round 3, and it is narrower than it looks): pre-D10 EVERY
       document writer MERGEd a ``:Document {id}`` node (``_upsert_document``,
       ``_event_plain_merge``, ``hosted_api``'s transcript MERGE), so the
       historical backlog carried ``doc_status`` on a node **both readers here
       exclude** (``_do_upgrade`` MATCHes ``:Source {url}``;
       ``_discover_pending_documents`` MATCHes a bare ``:Source`` and only
       *returns* ``s.url`` — which is why it can select a url-less node at all).
       This arm therefore matches a ``:Source`` document whose retired marker SURVIVED the move, and nothing
       this product writes today (the post-D10 fold REMOVEs ``doc_status``).
       ⛔ THE RELABEL IS A PRECONDITION, NOT A PRECEDENCE CHOICE — run
       ``tortoise/migrate_kinds.py``, whose ``relabel_legacy_documents`` folds
       ``:Document`` into ``:Source {url}`` (#5140 finding 8) FIRST; that is what
       puts the marker on a label this arm can read. Running ``--upgrade-all`` on
       a pre-D10 graph without it reports "no document Sources are awaiting
       extraction" while the backlog sits untouched.
    2. ``doc_status='extracted'`` → done. It is a completion record, and it
       outranks a stale ``needs_extraction=true``; without that precedence a
       sweep would re-discover a document the per-document path always skips.
    3. ``needs_extraction is True`` → pending (the live claim; checked before
       the generic retired-value arm so a ``draft`` node carrying an explicit
       ``true`` is still discovered — pre-D10 discovery was an OR over both).
    4. any other retired value (``draft``) → ``other_legacy_is_pending``. The
       sweep's mirror hard-codes ``False``: pre-D10 discovery never selected it
       (``doc_status='captured' OR needs_extraction = true``), and selecting it
       would re-enter every pre-D10 full-ingest document. ``--upgrade`` passes
       ``True`` because pre-D10 ``_do_upgrade`` skipped on ``'extracted'``
       **alone** (``coalesce(d.doc_status,'captured')``), so it upgraded a
       ``draft`` document — that is PARITY, not evidence.
       ⛔ ``draft`` does NOT mean "not extracted": pre-D10 the full-ingest path
       WROTE ``draft`` as the default and never flipped it (only
       ``_do_upgrade``/``_do_upgrade_all`` wrote ``'extracted'``), so ``draft``
       is what every successfully full-ingested pre-D10 document still carries.
       The ``begin_ingest`` key (content-hash + extractor version) is what stops
       a re-extraction; at a BUMPED version this reprocesses and retracts that
       source's earlier Points — the sweep's purpose, and a real cost.
    5. otherwise an ``is not None`` but non-``True`` signal is a stated
       "done" for the live axis: ``needs_extraction=false`` with no legacy
       marker → not pending.
    6. with no signal at all, ``unmarked_is_pending`` decides. ``_do_upgrade``
       keeps the pre-D10 default (pending — the operator named this document);
       the corpus SWEEP passes ``False``, because the two in-tree writers that
       mint a document Source without a signal are transcript writers
       (``hosted_api._execute_commit_writes``, and ``_event_plain_merge``'s
       ``objectType='Document'`` branch, which stamps
       ``documentKind='transcript'``), and selecting those would queue every
       captured session for LLM extraction. The widened set is not ONLY
       transcripts: a caller-minted Source (``create_source``/``update_entity``
       take a ``documentKind``, and ``needs_extraction`` is not caller-settable)
       is selected too. Whole-population widening is therefore an explicit
       operator choice — ``--include-unstamped`` — never a default.

    ⛔ The REBUILT-graph legacy shape is NOT covered, and that is a decision this
    PR deliberately did not take. Replaying a pre-D10 journal folds the old
    record's boilerplate ``needs_extraction=false`` onto a ``:Source`` and scrubs
    ``doc_status``, so such a document reads as arm 5 (done) although it was
    never extracted. Telling it apart from a REAL completion needs extraction
    evidence — ``contentHash`` (written only by the extracting path, #5422) or a
    journal read — and neither is a clean discriminator by itself: extractions
    predating #5422 carry no anchor, and the transcript genre (which must NOT be
    swept) carries none either. That is a product decision with a real
    re-extraction cost, recorded on #5140, not a default to flip here.

    Completion must therefore clear BOTH when it can (see ``_do_upgrade``): an
    upgrade that only cleared ``needs_extraction`` would leave
    ``doc_status='captured'`` behind and the sweep would re-select the document
    forever. On the SWEEP path, a document it cannot complete (unreadable path,
    file gone, no ``##`` sections, content already claimed at this extractor
    version) deliberately KEEPS its marker and is reported in its own bucket —
    clearing it would record "extracted" for work that never ran. A PARTIAL
    extraction (``failed_sections``) still clears, matching pre-D10 — the
    sections that succeeded are written, the document counts as ``upgraded`` (no
    bucket), and a ``warning: N sections failed extraction`` line is printed. The
    explicit ``--upgrade`` path is the pre-D10 behaviour and DOES clear after a
    "no ## sections" outcome: the operator named that document, there is nothing
    to extract, and it says so.
    """
    if doc_status == "captured":
        return True
    if doc_status == "extracted":
        # A completion record outranks a stale ``needs_extraction=true``. This is
        # the ONE cell where the sweep's selected set differs from pre-D10
        # discovery: pre-D10 SELECTED it and then skipped it inside the loop
        # (``skip …: already extracted``), so UPGRADE BEHAVIOUR IS UNCHANGED —
        # only the ``skipped`` count is (review round 9).
        return False
    if needs_extraction is True:
        return True
    if doc_status is not None:
        return other_legacy_is_pending
    if needs_extraction is not None:
        # A stated "done" (``needs_extraction=false``) with no legacy marker.
        # This is the ONE cell where ``--upgrade`` differs from pre-D10, and it
        # is DELIBERATE: pre-D10 ``coalesce(d.doc_status,'captured')`` made it
        # pending, D10's expectation is that an explicit false means done —
        # pinned by ``tests/test_ingest.py::test_upgrade_on_already_extracted_is_noop``
        # (review round 9).
        return False
    return unmarked_is_pending


def _do_upgrade(transcript, text, source_id, proj, api, args):
    """Upgrade a single Document: re-run extraction, clear needs_extraction.

    D10 (ONTOLOGY v3.15 §4.4): a document is a :Source and ``doc_status`` is
    RETIRED — liveness is a read of the extracted entities, and the only
    stored extraction signal is ``needs_extraction``.

    Guards:
    - ``doc_status='extracted'``, or a stored ``needs_extraction`` false with no
      legacy marker, → no-op (``_document_is_pending``)
    - A legacy capture (``doc_status='captured'``, even with the pre-D10
      boilerplate ``needs_extraction=false``), a document with NO signal, and a
      document carrying any other retired value (``draft``) → PENDING (#5140
      P1, the pre-D10 default restored): ``--upgrade`` names one document, so it
      is upgraded rather than reported as already done. ``draft`` is parity, not
      evidence — it is what every pre-D10 full ingest left behind; see
      ``_document_is_pending``.
    - Non-Document transcript (no ``##`` sections) → warns and, since review
      round 7, makes NO claim (``begin_ingest`` mints one and can retract a
      prior run's Points for work this call declines), then still runs the
      completion SET: there is nothing to extract, and the operator named this
      document. (The SWEEP is stricter and keeps the marker — see
      ``_document_is_pending``.)
    - Uses begin_ingest for idempotency (content-hash + extractor version)
    - On completion both signals are cleared: ``needs_extraction=false`` (the
      live signal, which ``--upgrade-all`` discovers on) and the retired
      ``doc_status`` is REMOVED. The pre-D10 code flipped that property to
      ``'extracted'``; D10 retired it, so the same transition is recorded by
      clearing it — writing a retired property back would re-enter it as a live
      signal, which is exactly what the retirement forbids for our own writes.
    """
    # 1. Check the document Source exists + its extraction signal.
    # RAW values, not coalesced: "absent" and "explicitly false" are different
    # claims, and coalescing them is exactly what inverted the default (#5140).
    rows = proj.g.query(
        "MATCH (s:Source {url: $id}) "
        "WHERE s.documentKind IS NOT NULL "
        "RETURN s.needs_extraction, s.doc_status",
        params={"id": source_id},
    ).result_set
    if not rows:
        print(f"not a Document: {source_id} (no document Source with this url)")
        return
    needs_ext, doc_status = rows[0][0], rows[0][1]
    # `other_legacy_is_pending=True`: pre-D10 `--upgrade` skipped on
    # `doc_status='extracted'` alone, so it also upgraded a `draft` document —
    # parity, not evidence that extraction never ran (`draft` is what every
    # pre-D10 full ingest left). Only the sweep treats other retired values as
    # done.
    if not _document_is_pending(needs_ext, doc_status,
                                other_legacy_is_pending=True):
        print(f"doc already extracted, skipped: {source_id}")
        return

    # 2. "Has anything to extract?" is answered BEFORE the idempotency claim
    #    (review round 7). `begin_ingest` MINTS a claim, and at a bumped
    #    extractor version it takes the reprocess path and supersedes/retracts
    #    that source's earlier Points (``api.begin_ingest``) — for work this
    #    call then declines to do. `_document_sections` is pure text parsing, so
    #    asking first changes no extraction behaviour, and it puts this path in
    #    the same order as `_do_upgrade_all` (hoisted in round 2 for the same
    #    reason). Unlike the sweep, `--upgrade` STILL records the completion
    #    transition for a no-sections document (step 4): the operator named this
    #    document, pre-D10 `--upgrade` flipped it unconditionally, and leaving
    #    the marker would re-report it on every run.
    is_doc, _ = _document_sections(text)
    if is_doc:
        from .idempotency import document_key
        result = api.begin_ingest(source_id, api.agent_id or "extractor@0",
                                  document_key(text), force=args.force)
        if result and result.skip:
            print(f"upgrade skip: {result.reason} (run {result.run_id}); use --force to reprocess")
            return

        print(f"upgrading {source_id} "
              f"(needs_extraction={needs_ext}, doc_status={doc_status}) …")

        # 3. Re-run full extraction (the same path as normal full ingest)
        from .extractor import extract_from_document  # noqa: I001
        from .ids import ulid
        from .domain_loader import resolve_domain_from_path

        # Emit fresh DocumentCreated for metadata (idempotent via MERGE)
        fm = _parse_frontmatter(text)
        domain = fm.get("domain", fm.get("documentKnowledgeDomain", ""))
        if not domain:
            domain = resolve_domain_from_path(str(transcript))
        topics_raw = fm.get("topics", "")
        topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
        api.add_document(
            doc_id=source_id,
            title=fm.get("title", transcript.stem),
            document_kind=_frontmatter_kind(fm),
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
            about_entities=_s7_document_entities(text, source_id, api, args),
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
        print(f"  warning: {source_id} is not a Document (no ## headers) — "
              f"extraction skipped (no claim made)")

    # 4. Mark extracted: clear the live signal AND retire the legacy marker.
    #    The pre-D10 code flipped doc_status to 'extracted' here; D10 retired
    #    that property, so the transition is recorded by clearing it. Leaving
    #    doc_status='captured' behind would keep the sweep re-selecting this
    #    document forever (#5140).
    proj.g.query(
        "MATCH (s:Source {url: $id}) "
        "SET s.needs_extraction = false REMOVE s.doc_status",
        params={"id": source_id},
    )
    # Print the transition that actually happened: the pre-fix line said
    # "true → false" unconditionally, but this path now also admits documents
    # whose stored value was false, absent, or a `draft` marker — so the
    # operator would read "upgrading X (needs_extraction=False)" followed by
    # "needs_extraction: true → false" (review round 3, P2).
    print(f"  needs_extraction: {needs_ext} → False")
    if doc_status is not None:
        print(f"  doc_status: {doc_status!r} → removed (retired by D10)")

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


def _discover_pending_documents(proj, *, include_unstamped: bool = False) -> list:
    """Document Sources awaiting extraction — the ONE discovery predicate.

    Mirrors the SWEEP arm of ``_document_is_pending`` in Cypher, arm for arm —
    the ``CASE`` branch order IS that function's precedence (with
    ``other_legacy_is_pending=False``, its value for the sweep), so a change to
    one that is not made to the other is a visible diff in the same commit.

    * ``doc_status = 'captured'`` — a surviving pre-D10 capture marker (a
      relabelled graph: the marker rode the retired ``:Document`` label, so a
      node that still has it on ``:Source`` was moved there deliberately).
      Pending even beside the boilerplate ``needs_extraction=false`` the old
      writer stamped. A second source of this marker is the deleted
      ``graph-scripts/backfill_doc_status.py``, which stamped it on every
      ``:Document`` with a NULL ``doc_status`` — so it means "never PROVEN
      extracted", and a node it stamps is re-selected even if it had been
      extracted. The ``begin_ingest`` key gates the work only at an UNCHANGED
      extractor version: a version bump takes the reprocess path and retracts
      that source's earlier Points before re-extracting, which is what a sweep
      is for — but it is a real re-extraction cost, not a no-op.
    * ``doc_status = 'extracted'`` — a completion record, never pending;
    * ``needs_extraction = true`` — the live pending claim;
    * any other retired value (``draft``) — not a pending claim;
    * no signal at all — only under ``include_unstamped``. The two IN-TREE
      writers that mint a document Source without a signal are transcript
      writers (``hosted_api._execute_commit_writes`` and ``_event_plain_merge``'s
      ``objectType='Document'`` branch), and sweeping those up would queue every
      captured session for LLM extraction. The widened set is not ONLY
      transcripts — a caller-minted Source (``create_source``/``update_entity``
      take a ``documentKind``; ``needs_extraction`` is not caller-settable) is
      selected too. Whole-population widening is therefore an explicit operator
      choice, never a default.

    Returns ``[(url, sourcePath, needs_extraction, doc_status), …]``.
    """
    return proj.g.query(
        "MATCH (s:Source) "
        "WHERE s.documentKind IS NOT NULL "
        "AND CASE "
        "      WHEN s.doc_status = 'captured' THEN true "
        "      WHEN s.doc_status = 'extracted' THEN false "
        "      WHEN s.needs_extraction = true THEN true "
        "      WHEN s.doc_status IS NOT NULL THEN false "
        "      WHEN s.needs_extraction IS NOT NULL THEN false "
        "      ELSE $unmarked "
        "    END "
        "RETURN s.url, coalesce(s.sourcePath, s.url) AS sourcePath, "
        "s.needs_extraction, s.doc_status",
        params={"unmarked": bool(include_unstamped)},
    ).result_set


def _do_upgrade_all(proj, api, args):
    """Discover pending document Sources and upgrade each.

    D10 (ONTOLOGY v3.15 §4.4): ``doc_status`` is retired — the extraction
    signal is ``needs_extraction``, set true by the ``--capture-metadata`` path
    and cleared here after a successful extraction. A SURVIVING
    ``doc_status='captured'`` marker is still honoured as a PENDING claim (a
    graph ``migrate_kinds.relabel_legacy_documents`` has folded ``:Document``
    into ``:Source`` — #5140 finding 8; the sweep itself never relabels); the
    rule lives in ``_discover_pending_documents`` / ``_document_is_pending``,
    never inline.

    Uses inline Cypher via proj.g.query (no SDK method needed — plan §Task 2).

    Re-run safety, stated precisely: each attempt is gated by the begin_ingest
    key (content-hash + extractor version), so identical content is a no-op. A
    document this sweep CANNOT complete is left PENDING and counted in its own
    bucket (see the summary line) — the marker is the operator's signal that
    something is still outstanding, so it is not cleared on a failure path. Only
    an attempt that RAN clears it: a PARTIAL run (``stats['failed_sections']``
    non-empty) also clears and counts as ``upgraded``, matching pre-D10, with a
    ``warning: N sections failed extraction`` line printed (review round 8 —
    this docstring said "only a completed extraction", which the code has never
    done; ``_document_is_pending`` states the real rule).

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

    include_unstamped = bool(getattr(args, "include_unstamped", False))
    rows = _discover_pending_documents(proj, include_unstamped=include_unstamped)

    if not rows:
        print("upgrade-all: no document Sources are awaiting extraction "
              "(needs_extraction=true, or the legacy doc_status='captured' marker"
              + ("; --include-unstamped widened the search to documents with no "
                 "signal at all" if include_unstamped else
                 "; pass --include-unstamped to also select documents with no "
                 "signal at all") + ")")
        return

    print(f"upgrade-all: {len(rows)} document(s) to upgrade"
          + (" (--include-unstamped: documents with no extraction signal are "
             "selected too)" if include_unstamped else ""))

    upgraded = 0
    skipped = 0
    # Skip buckets, kept apart on purpose: "unresolved" means we never got to
    # read the content (fail-closed on an unprovable path), "not a document"
    # means the content has no sections to extract, and "already claimed" means
    # this extractor version has processed this content before. All three leave
    # the pending marker in place (see the docstring), so the summary has to say
    # WHICH kind of outstanding work remains, or "0 upgraded" reads as "done".
    unresolved = 0
    not_document = 0
    already_claimed = 0
    for doc_id, source_path, needs_ext, doc_status in rows:
        # #329: resolve the candidate (sourcePath OR s.url — BOTH are
        # tenant-mutable) strictly under the configured base. Fail-closed:
        # anything not provably under base is skipped, never read.
        candidate = source_path or doc_id
        filepath = resolve_under_base(candidate, ingest_base)
        if filepath is None:
            print(f"  skip {doc_id}: path {candidate!r} not under TORTOISE_INGEST_BASE_DIR "
                  f"({ingest_base or '<unset>'}); refusing to read (fail-closed)")
            skipped += 1
            unresolved += 1
            continue
        if not filepath.exists():
            print(f"  skip {doc_id}: file not found ({filepath})")
            skipped += 1
            unresolved += 1
            continue

        text = filepath.read_text(encoding="utf-8")
        source_id = doc_id  # doc_id IS the source id (filename)

        # The "has anything to extract?" question is answered BEFORE the
        # idempotency claim (review round 2): `begin_ingest` MINTS a claim, so
        # claiming first made a no-sections document report *already processed*
        # on the second sweep (a self-inflicted, unstable bucket) and let a
        # claim supersede/retract a prior run's points for work this sweep then
        # declines to do. `_document_sections` is pure text parsing — hoisting
        # it changes no extraction behaviour.
        from .extractor import _document_sections
        is_doc, _ = _document_sections(text)
        if not is_doc:
            print(f"  skip {doc_id}: not a Document (no ## headers) — left pending")
            skipped += 1
            not_document += 1
            continue

        # Idempotency gate
        from .idempotency import document_key
        extractor_version = api.agent_id or "extractor@0"
        result = api.begin_ingest(source_id, extractor_version,
                                  document_key(text), force=args.force)
        if result and result.skip:
            print(f"  skip {doc_id}: {result.reason}")
            skipped += 1
            already_claimed += 1
            continue

        print(f"  upgrading {doc_id} "
              f"(needs_extraction={needs_ext}, doc_status={doc_status}) …")

        # Emit DocumentCreated for metadata (idempotent via MERGE)
        from .extractor import extract_from_document  # noqa: I001
        from .ids import ulid
        from .domain_loader import resolve_domain_from_path

        fm = _parse_frontmatter(text)
        domain = fm.get("domain", fm.get("documentKnowledgeDomain", ""))
        if not domain:
            domain = resolve_domain_from_path(str(filepath))
        topics_raw = fm.get("topics", "")
        topics = [t.strip() for t in topics_raw.split(",") if t.strip()] if topics_raw else []
        api.add_document(
            doc_id=source_id,
            title=fm.get("title", filepath.stem),
            document_kind=_frontmatter_kind(fm),
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
            about_entities=_s7_document_entities(text, source_id, api, args),
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
        # Mark extracted: clear the live signal AND retire the legacy marker
        # (the pre-D10 flip is recorded by clearing, not by writing a retired
        # property — see `_do_upgrade`).
        proj.g.query(
            "MATCH (s:Source {url: $id}) "
            "SET s.needs_extraction = false REMOVE s.doc_status",
            params={"id": source_id},
        )
        print(f"    needs_extraction: {needs_ext} → False"
              + (f", doc_status: {doc_status!r} → removed" if doc_status is not None else ""))
        upgraded += 1

    # Lazy EP re-propagation ONCE after all upgrades (#133 Task 3 — review P2:
    # N× full-graph EP is wasteful; one run after the loop is equivalent).
    if upgraded:
        _run_ep_propagation(proj, api)

    print(f"upgrade-all complete: {upgraded} upgraded, {skipped} skipped")
    if skipped:
        # Never let "0 upgraded" read as "nothing outstanding": the buckets name
        # the work that is still pending, and each one keeps its marker.
        print("  still pending: "
              f"{unresolved} unreadable (path outside base or file gone; "
              "fail-closed), "
              f"{not_document} not a document (no ## sections), "
              f"{already_claimed} already claimed at this extractor version "
              "(re-run with --force to reprocess)")


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


def _s7_document_entities(text, source_id, api, args, extractor=None):
    """#4938: run S7 and return the document's Subject names (wiring targets).

    The names are handed to ``add_document(about_entities=...)`` so the
    document Source gets its ``aboutSubject`` edges through the sanctioned
    ``_upsert_document → _create_about_edges`` route. Returns ``[]`` when S7 is
    opted out, in capture-metadata mode, or when the entity stage fails before
    creating any Subject (a later failure returns the partial list — below).

    FAIL-OPEN (deliberate): ``begin_ingest`` has already claimed the content
    hash when this runs, so letting an entity-stage failure escape would leave
    the document unwritten AND make a plain re-run SKIP ("already processed") —
    the document would be silently lost. Per-SECTION failures are tolerated by
    the stage itself (``skip_on_failure=True``); a NON-section failure (e.g. a
    graph/journal write during a mint) propagates, but ``extract_entities``
    attaches the names whose ``add_subject`` had already returned as
    ``partial_subject_names``, and those are still returned and wired. The
    guarantee is exactly that: returned-by-``add_subject`` names are wired; a
    call that raises after its MERGE committed is not in the list and can
    still leave a durable Subject unwired.
    """
    if not (args.semantic_extract and not args.capture_metadata):
        return []
    if extractor is None:
        extractor = LLMExtractor(build_model(args.point_model),
                                 build_model(args.relation_model, reasoning=True))
    try:
        ent_stats = extractor.extract_entities(
            text, source_id, api, domain=args.domain, skip_on_failure=True)
    except Exception as e:
        # #4938: Subjects created earlier in this run are still durable (the
        # journal is written before the projection), so wire THEM rather than
        # leaving Subjects no document points at.
        partial = list(getattr(e, "partial_subject_names", []))
        print(f"warning: S7 entity extraction failed "
              f"({type(e).__name__}: {e}) — "
              + (f"wiring the {len(partial)} Subject(s) already minted"
                 if partial else
                 "continuing without document→Subject wiring"))
        return partial
    print(f"entities: {ent_stats['subjects']} Subjects, "
          f"{ent_stats['objects']} Objects")
    failed = ent_stats.get("failed_sections") or []
    if failed:
        print(f"warning: S7 entity extraction failed for {len(failed)} "
              f"section(s) ({', '.join(failed)}) — their Subjects are "
              f"omitted; the minted Subjects are still wired")
    return ent_stats["subject_names"]


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
    ap.add_argument("--semantic-extract", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="run S7 semantic extraction (Subjects + Objects + "
                         "aboutEntities) before the document write, and wire "
                         "the document's aboutSubject edges from it; ON by "
                         "default since #4938 — the document path's only "
                         "Subject producer, and the owner's "
                         "document→Source+Subjects model requires it. Pass "
                         "--no-semantic-extract to skip it (one extra model "
                         "call per section).")
    ap.add_argument("--capture-metadata", action="store_true",
                    help="#125 metadata-only capture: emit document Source + "
                         "sessionCaptured Event, SKIP LLM point extraction "
                         "(sets needs_extraction=true; topics/summary from frontmatter)")
    ap.add_argument("--upgrade", action="store_true",
                    help="#133/#5140: re-run full extraction on a document Source "
                         "awaiting extraction — needs_extraction=true, a surviving "
                         "legacy doc_status other than 'extracted', or no signal at "
                         "all (requires the transcript positional arg). A stored "
                         "needs_extraction=false with no legacy marker still skips, and "
                         "a stored doc_status='extracted' outranks needs_extraction=true.")
    ap.add_argument("--upgrade-all", action="store_true",
                    help="#133/#5140: discover document Sources awaiting extraction "
                         "(an explicit needs_extraction=true, or a surviving legacy "
                         "doc_status='captured' marker; a stored "
                         "doc_status='extracted' outranks needs_extraction=true) and "
                         "upgrade each")
    ap.add_argument("--include-unstamped", action="store_true",
                    help="#5140: with --upgrade-all, also select document Sources "
                         "that carry no extraction signal at all (`needs_extraction` "
                         "absent AND no legacy `doc_status` marker — a surviving "
                         "`doc_status` such as 'draft' is still not selected). The "
                         "default sweep takes an explicit needs_extraction=true or a "
                         "surviving doc_status='captured' marker; the two in-tree "
                         "writers that mint a document Source with no signal are "
                         "transcript writers, so widening the sweep onto them is an "
                         "explicit choice, and a caller-minted Source "
                         "(`create_source(documentKind=…)`) is selected too. "
                         "⛔ It does NOT select a document whose stored "
                         "needs_extraction=false came from a REPLAYED pre-D10 record "
                         "(that value is indistinguishable from a real completion "
                         "without extraction evidence — see #5140), and it does NOT "
                         "see an un-migrated :Document-labelled node — fold it into "
                         "the :Source label first with tortoise/migrate_kinds.py "
                         "(#5140 finding 8). ⛔ One population needs a step before "
                         "it: _event_plain_merge's Document branch stamped NO clock "
                         "(unlike hosted_api's transcript MERGE), so the relabel "
                         "REFUSES those nodes rather than invent an ingestedAt, "
                         "and leaves them :Document. Assert the instant first "
                         "(SET d.ingestedAt='<iso>') and re-run the migration. "
                         "The WIDENING is a "
                         "read (no stamping, unlike the deleted backfill) — but the "
                         "sweep then upgrades what it selects, so this is not a dry "
                         "run.")
    args = ap.parse_args(argv)

    # #5140: --include-unstamped is a discovery MODIFIER, not a mode — it can
    # only change which documents --upgrade-all selects. Refuse rather than
    # accept a flag that would silently do nothing.
    if args.include_unstamped and not args.upgrade_all:
        raise SystemExit("--include-unstamped requires --upgrade-all")

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
                # S7 (#4938, default-ON): extract the document's Subjects +
                # Objects BEFORE the DocumentCreated event, so the Subjects it
                # names can be wired to the document through `about_entities`
                # (the sanctioned `_upsert_document` → `_create_about_edges`
                # route). Running S7 after the write — as it did behind the
                # opt-in flag — left the document's Subject producer minting
                # nodes it never connected to the document. `--capture-metadata`
                # deliberately SKIPS all LLM extraction (a metadata-only op);
                # `--no-semantic-extract` opts out of S7 alone. Fail-open —
                # see `_s7_document_entities`.
                doc_about_entities = _s7_document_entities(
                    text, source_id, api, args, extractor)
                api.add_document(
                    doc_id=source_id,
                    title=fm.get("title", args.transcript.stem),
                    document_kind=_frontmatter_kind(fm),
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
                    about_entities=doc_about_entities,
                )
                if args.capture_metadata:
                    # #125 metadata-only: emit sessionCaptured Event with uses→Skill,
                    # SKIP LLM point extraction entirely
                    #
                    # ⛔ #3590 (D1) — REGISTER the Skill this event references,
                    # BEFORE emitting it. The fold resolves an event's MENTIONS
                    # (`uses[]`, `subject`, `object`) against a single LIVE
                    # holder and otherwise drops the link
                    # (`entity_identity.resolve_fold_name(..., mention=True)`);
                    # it no longer MERGEs a stub whose id WAS the name, because
                    # a fold-time mint is not replay-deterministic and a
                    # name-derived id is the defect #3590 removes. So an
                    # attribution edge exists only when the PRODUCER registers
                    # its target — which is exactly the door D1 (RULED
                    # 2026-10-26) names as the one that grows the entity set:
                    # "the entity set grows only from an explicit registration
                    # (ObjectRegistered / SubjectAdded) that mints the id; an
                    # unresolvable mention drops its link, records the miss, and
                    # mints nothing."
                    # `add_object` is that registration and is idempotent on a
                    # live name (#452 via `_registration_id`), so a re-run
                    # resolves the same id instead of leaving a second carrier.
                    # Without it #125's own acceptance assertion —
                    # `sessionCaptured -[:uses]-> (:Object {objectKind:'skill'})`
                    # (tests/test_ingest.py) — is a silent red: the edge the
                    # capture exists to write never lands (measured on CI
                    # run 40917794096, job 123637953240).
                    # ⚠️ The `subject="pi-agent"` mention below is left
                    # unregistered ON PURPOSE — it is the agent's own label, not
                    # a referenced entity, and its dropped link is the bounded
                    # exemption `entity_identity.resolve_fold_name` records
                    # (naming this producer).
                    api.add_object("tortoise-capture", "skill")
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
