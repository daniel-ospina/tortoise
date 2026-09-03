"""Extractor — transcript segment(s) → events, via the shared EventAPI.

M0 ships a deterministic, offline `MockExtractor` so the spine runs with no API
key: one utterance-level point each (Option A), plus asserted-only {NAND, IMPL}
operators inferred from discourse connectives. The real LLM extractor (M2) will
implement the same `Extractor` interface — segment in, events out — so nothing
downstream changes when it's swapped in.

Builder capability catalog note (#2004 W8 / epic #1976 DM-5): this module is
referenced in the builder capability catalog (onboarding) — catalog module
'Session extractor' — tortoise/tool_registry.py CAPABILITY_CATALOG. If you
add or rename an extractor/indexer, update the catalog reference.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Protocol

from .api import EventAPI, provenance

# Asserted-relation cues (README: speaker-asserted operators only, for now).
# ponytail: single-word cues use \b word boundaries (#8); multi-word phrases use substring.
_SUPPORT_SINGLE_RE = re.compile(r'\b(because|since|therefore|thus|hence|so)\b', re.IGNORECASE)
_SUPPORT_PHRASES = ("given that",)
_REFUTE_SINGLE_RE = re.compile(r'\b(but|however|although)\b', re.IGNORECASE)
_REFUTE_PHRASES = ("not relevant", "doesn't follow", "on the contrary", "except that")
_PUNC = re.compile(r'[,.!?;:\'\"()\[\]{}]')  # strip before phrase-matching (#8)

def _classify_point_kind(text: str) -> str:
    """Keyword-based pointKind classifier (#784 — the rule extractor must
    carry kinds so content dedup (pointKind-scoped, DE2E-N11) and the
    temporal post-pass can operate on decision points). Mirrors the
    document-mode classifier; order matters — decision wins."""
    kind = "statement"
    low = text.lower()
    if any(w in low for w in ("decided", "chosen", "chose", "select", "will adopt")):
        kind = "decision"
    elif any(w in low for w in ("vision", "future state", "aspir")):
        kind = "vision"
    elif any(w in low for w in ("plan", "step", "implement", "build")):
        kind = "plan"
    elif any(w in low for w in ("goal", "outcome", "achieve")):
        kind = "goal"
    elif any(w in low for w in ("found", "observe", "measure", "data shows")):
        kind = "observation"
    elif any(w in low for w in ("hypothes", "might", "could be", "possibly")):
        kind = "hypothesis"
    return kind

logger = logging.getLogger(__name__)

def _has_cue(text: str, single_re: re.Pattern, phrases: tuple[str, ...]) -> bool:
    return bool(single_re.search(text)) or any(p in text for p in phrases)

_SPEAKER = re.compile(r"^\s*([A-Z][\w .'-]{0,40}):\s*(.*)$")
_SENT = re.compile(r"[^.?!]+[.?!]?")


class Extractor(Protocol):
    def run(self, transcript: str, source_id: str, api: EventAPI,
            *, multi_source: bool = False) -> None: ...


def _utterances(transcript: str):
    """Yield (speaker, text, span) at sentence granularity with char offsets."""
    base = 0
    for line in transcript.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = _SPEAKER.match(stripped)
        if not m:  # skip preamble / non-"Speaker: text" lines
            base += len(line)
            continue
        speaker, body = m.group(1), m.group(2)
        body_off = m.start(2)
        for sm in _SENT.finditer(body):
            text = sm.group(0).strip()
            if len(text) >= 3:
                start = base + body_off + sm.start()
                yield speaker, text, [start, start + len(sm.group(0))]
        base += len(line)


def _strip_frontmatter(text: str) -> str:
    """Strip YAML frontmatter only if at document start and before first ## header."""
    m = re.match(r'^---\n.*?\n---\n', text, re.DOTALL)
    if m:
        after = text[m.end():]
        first_header = after.find('## ')
        if first_header >= 0:
            return after
    return text


# -- Document mode: split markdown on ## headers, not conversation turns -------------
_DOC_HEADER = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _document_sections(transcript: str):
    """Split markdown on ## section headers for document-mode ingestion.

    Returns (is_document, sections) where sections is a list of
    (title, text, span). Returns (False, []) if no ## headers found.
    """
    text = _strip_frontmatter(transcript)
    if "\n## " not in text and not text.startswith("## "):
        return False, []

    # ponytail: strip fenced code blocks to avoid splitting on ## inside them.
    # Replace code block content with placeholder to keep section offsets correct.
    in_code = False
    fence_char = ""
    processed: list[str] = []
    i = 0
    while i < len(text):
        if text[i:i+3] in ("```", "~~~"):
            if not in_code:
                fence_char = text[i:i+3]
                in_code = True
                processed.append(f"\n\n{text[i:i+3]}\n")
                i += 3
            elif text[i:i+3] == fence_char:
                in_code = False
                processed.append(f"{fence_char}\n\n")
                i += 3
            else:
                processed.append(text[i])
                i += 1
        elif in_code:
            # Keep code content as literal text (preserve newlines for offset alignment)
            # but ensure ## on its own line doesn't match as a section header
            if text[i:i+3] == "## " and (i == 0 or text[i-1] == '\n'):
                processed.append("#_# ")  # ponytail: mangle section-like headers in code
            else:
                processed.append(text[i])
            i += 1
        else:
            processed.append(text[i])
            i += 1
    clean = "".join(processed)

    sections = []
    for m in _DOC_HEADER.finditer(clean):
        title = m.group(1).strip()
        start = m.start()
        sections.append((title, start, m.end()))

    results = []
    first_start = sections[0][1]
    if first_start > 0:
        preamble = clean[:first_start].strip()
        if preamble:
            results.append(("preamble", preamble, [0, first_start]))

    for i, (title, start, body_start) in enumerate(sections):  # noqa: B007
        content_start = body_start
        if content_start < len(clean) and clean[content_start] == '\n':
            content_start += 1

        content_end = sections[i + 1][1] if i + 1 < len(sections) else len(clean)
        body = clean[content_start:content_end].strip()
        if len(body) >= 20:  # skip empty/short sections
            span = [content_start, content_end]
            results.append((title, body, span))

    return True, results


def _is_claim(text: str) -> bool:
    """Filter: keep only lines that express a CLAIM (assertion, finding, tension, causal relationship)."""
    t = text.strip()
    if len(t) < 40:
        return False
    if t.startswith('**') and t.count('**') >= 2 and len(t) < 80:
        return False
    if t.startswith('|') or t.startswith('```') or t.startswith('#'):
        return False
    if t.startswith('>'):
        return False
    low = t.lower()
    # Explicit stance verbs
    stance_words = (' is ', ' are ', ' does ', ' should ', ' must ', ' can ', ' cannot ',
                    ' claim', ' assert', ' argue', ' find', ' show', ' suggests',
                    ' contradicts', ' challenges', ' disagrees', ' fails', ' never',
                    ' always', ' instead', ' however', ' therefore', ' because')
    if any(w in low for w in stance_words):
        return True
    # Arrow/causal notation: only keep if part of a complete statement (has context)
    if ('→' in t or '->' in t or '──' in t) and len(t) > 80:
        return True
    # Comparative/ranking: "better than", "more than", "primary", "dominant"
    if any(w in low for w in ('better than', 'more important', 'primary', 'dominant',
                               'key turning point', 'structural', 'fundamental')):
        return True
    # Percentage/fact with implied stance: need context AND a stance word
    if any(c.isdigit() for c in t) and '%' in t and len(t) > 60:  # noqa: SIM102
        if any(w in low for w in ('fail', 'unchanged', 'rate', 'increase', 'decrease', 'drop', 'rise', 'fall')):
            return True
    return False


def _cue_gate_pairs(pairs: list[tuple[str, str, dict]], texts: dict[str, str],
                   api: EventAPI) -> None:
    """Apply the cue-word gate over (src, dst, provenance) pairs.

    Support cues (because/since/therefore/...) → IMPL; refute cues
    (but/however/although/...) → NAND. Pairs without cues create nothing.
    Similarity gating is the CALLER's responsibility (candidates from
    find_cross_lens_matches, or none for the all-pairs fallback). #399:
    candidates never become operators from similarity alone — this is the
    deterministic verifier; the LLM relation model is the #6306 verifier.

    Direction: deterministic (src → dst as given); operators are
    bidirectional by default (ONTOLOGY §3.1), so cue-side directionality is
    left to the #6306 LLM verifier.
    """
    for src, dst, prov in pairs:
        ti_clean = _PUNC.sub('', f" {texts[src]} ")
        tj_clean = _PUNC.sub('', f" {texts[dst]} ")
        gate = None
        if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or \
           _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
            gate = "IMPL"
        elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or \
             _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
            gate = "NAND"
        if gate:
            api.add_operator(gate, inputs=[src, dst], provenance=prov)


class MockExtractor:
    """Heuristic, deterministic extractor for the M0 spine."""

    version = "mock@0"

    def run(self, transcript: str, source_id: str, api: EventAPI,
            *, multi_source: bool = False) -> None:
        # #399: documented #6306 integration point — always present (any mode).
        self._last_candidates: list[dict] = []
        if multi_source:
            # Multi-source mode: claim extraction → embedding pre-filter → cue-word gate typing.
            # #399: cross-vocabulary matching via lens-keyed candidates (cross_lens module);
            # the old ≥3-shared-content-words semantic-agreement gate is removed (it killed
            # zero-word-overlap pairs by design). Candidates NEVER become operators from
            # similarity alone — the cue-word gate decides IMPL vs NAND direction.
            pids = []
            for speaker, text, span in _utterances(transcript):
                if not _is_claim(text):
                    continue
                prov = provenance(source_id, span, quote=text, speaker=speaker,
                                  extracted_by=self.version)
                # #49 Phase 2: context is deprecated — use extractedFrom for provenance
                pid = api.add_point(content=text, provenance=prov,
                                    extractedFrom=source_id,
                                    pointKind=_classify_point_kind(text))
                pids.append((pid, text.lower(), prov))

            # Use embedding pre-filter if available
            try:
                from tortoise.cross_lens import find_cross_lens_matches
                # Build points dict from what we collected. #399 root-cause #2: keep the
                # LENS identity — source_id was dropped before. In multi_source mode
                # the transcript MERGES utterances from different sources, and the
                # speaker is that source discriminator ("These statements come from
                # DIFFERENT sources" — LLMExtractor multi_source prompt). So the lens
                # here is the speaker; the uniform source_id stays for provenance and
                # for #6306's multi-document fold (lens_key="source" / derivation).
                all_points = {}
                for pid_i, ti, pvi in pids:
                    sp = pvi.get('speaker', 'unknown') if isinstance(pvi, dict) else 'unknown'
                    all_points[pid_i] = {"content": ti, "speaker": sp, "source": source_id}

                candidates = find_cross_lens_matches(all_points, lens_key="speaker")
                self._last_candidates = list(candidates)
                texts_by_pid = {pid: ti for pid, ti, _ in pids}
                provs_by_pid = {pid: pv for pid, _, pv in pids}
                if candidates:
                    degraded = any(c.get("degraded") for c in candidates)
                    if degraded:
                        logger.info(
                            "multi-source: cross-lens matching degraded to TF-IDF "
                            "(%d candidates) — candidates remain similarity-gated",
                            len(candidates),
                        )
                    # Similarity-gated cue-gate: candidates (real embeddings or
                    # TF-IDF degraded) are the embedding pre-filter; cue words
                    # decide IMPL vs NAND direction. Non-cued candidates stay in
                    # _last_candidates for the #6306 LLM verifier — never
                    # operators from similarity alone.
                    pairs = [(m["src"], m["dst"], provs_by_pid[m["src"]])
                             for m in candidates]
                    _cue_gate_pairs(pairs, texts_by_pid, api)
                else:
                    # No candidates above threshold — keep the similarity gate:
                    # no operators (pre-#399 success-path semantics; only the
                    # exception fallback below is all-pairs).
                    logger.info(
                        "multi-source: no cross-lens candidates above threshold "
                        "(%d points, all same lens) — no operators",
                        len(pids),
                    )
            except Exception:
                # Fallback: cue-word only all-pairs (noisy but works)
                # (catches ImportError for missing dependencies AND runtime errors
                #  like sklearn ValueError on empty vocabulary)
                logger.warning(
                    "multi-source: cross-lens matching unavailable — "
                    "all-pairs cue-gate fallback",
                    exc_info=True,
                )
                texts_by_pid = {pid: ti for pid, ti, _ in pids}
                provs_by_pid = {pid: pv for pid, _, pv in pids}
                pairs = [(pids[i][0], pids[j][0], provs_by_pid[pids[i][0]])
                         for i in range(len(pids))
                         for j in range(i + 1, len(pids))]
                _cue_gate_pairs(pairs, texts_by_pid, api)
        else:
            # Sequential mode (original): only connect consecutive utterances
            prev_pid = None
            for speaker, text, span in _utterances(transcript):
                prov = provenance(source_id, span, quote=text, speaker=speaker,
                                  extracted_by=self.version)
                # #49 Phase 2: context is deprecated — use extractedFrom for provenance
                pid = api.add_point(content=text, provenance=prov,
                                    extractedFrom=source_id,
                                    pointKind=_classify_point_kind(text))
                low = _PUNC.sub('', f" {text.lower()} ")
                gate = None
                if _has_cue(low, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                    gate = "IMPL"
                elif _has_cue(low, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                    gate = "NAND"
                if gate and prev_pid is not None:
                    api.add_operator(gate, inputs=[prev_pid, pid],
                                     provenance=prov)
                prev_pid = pid


# ---------------------------------------------------------------------------
# Phase-2 entity stage (epic #264 plan W-1 / §5.1 / §7 — issue #782 DE2E-1).
# ---------------------------------------------------------------------------

# W-1 rules pre-filter: reuse the EXISTING issues/prs metadata regex
# (session_indexer.py:204 — repo#NNN pattern). Deliberately NOT
# _graph_entity_keywords: that is a name-substring matcher against existing
# graph entities, valid only as the known-Object pre-filter for R4 cost control.
_ISSUE_REF_RE = re.compile(r"([a-zA-Z0-9_-]+)#(\d+)")

# objectKind vocab reuse (issue #782 complexity table + plan §4.1):
# Project, WorkItem, document, tag, user, skill, tool, agent, workflow,
# agreement, standard, other.
_OBJECT_KIND_VOCAB = frozenset({
    "project", "workitem", "document", "tag", "user", "skill", "tool",
    "agent", "workflow", "agreement", "standard", "other",
})


def _normalize_object_kind(kind: str) -> str:
    """DE2E-N7: unknown objectKind values fall back to 'other'."""
    k = str(kind or "other").strip().lower()
    return k if k in _OBJECT_KIND_VOCAB else "other"


def _intersect_object_kinds(kinds: list[str] | None) -> list[str]:
    """DE2E-review (objectKind vocab): the LLM prompt vocab must not be wider
    than the validator vocab — kinds that cannot survive _normalize_object_kind
    (DE2E-N7) silently collapse to 'other' and mislead the model ("Prefer
    specific kinds" while 26 of 38 prompt kinds are dropped). Intersect the
    resolved vocab (domain_loader known_kinds/domain_kinds) with
    _OBJECT_KIND_VOCAB so the model only sees kinds that survive."""
    if not kinds:
        return sorted(_OBJECT_KIND_VOCAB)
    merged: list[str] = []
    for k in kinds:
        ks = str(k).strip().lower()
        if ks in _OBJECT_KIND_VOCAB and ks not in merged:
            merged.append(ks)
    return merged or sorted(_OBJECT_KIND_VOCAB)


def _canonical_name(name: str) -> str:
    """Plan §4.1: normalized entity name — lowercase, whitespace-collapsed
    ("port  16379" == "port 16379"), punctuation-stripped for matching; the
    display `title` preserves the original mention."""
    return _PUNC.sub("", re.sub(r"\s+", " ", name.lower().strip()))


def _normalize_entity(e) -> dict | None:
    """Normalize one raw entity dict into the Phase-2 contract shape
    {name, objectKind, canonical_candidates, span, confidence}."""
    if not isinstance(e, dict) or not e.get("name"):
        return None
    name = str(e["name"]).strip()
    if not name:
        return None
    span = e.get("span")
    if not (isinstance(span, list) and len(span) == 2
            and all(isinstance(x, int) for x in span) and span[0] <= span[1]):
        span = None
    return {
        "name": name,
        "objectKind": _normalize_object_kind(e.get("objectKind")),
        "canonical_candidates": [str(c) for c in (e.get("canonical_candidates") or [])],
        "span": span,
        "confidence": float(e.get("confidence", 0.5)),
    }


def _rule_fallback_entities(transcript: str) -> list[dict]:
    """DE2E-N2 rule/keyword fallback: extract KNOWN reference entities
    (issue/PR refs repo#NNN → objectKind workitem) with deterministic spans.
    Returns [] when no known refs are present — never raises."""
    out: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for m in _ISSUE_REF_RE.finditer(transcript):
        span = (m.start(), m.end())
        if span in seen:
            continue
        seen.add(span)
        ent = _normalize_entity({
            "name": m.group(0),
            "objectKind": "workitem",
            "canonical_candidates": [m.group(0)],
            "span": list(span),
            "confidence": 1.0,
        })
        if ent:
            out.append(ent)
    return out


class EntityStageMock:
    """Deterministic Phase-2 entity-stage fixture (plan §7 preamble).

    Same pattern as MockExtractor: offline, no LLM, no network. Maps seed
    transcript fragments (substring keys) to fixed entity sets; char spans are
    located deterministically via str.find. Optional failure injection for
    DE2E-N2 (LLM failure → rule/keyword fallback).
    """

    version = "entity-mock@0"

    def __init__(self, entities_by_key: dict[str, list[dict]] | None = None,
                 *, fail_first_call: bool = False):
        self.entities_by_key = dict(entities_by_key or {})
        self.fail_first_call = fail_first_call
        self.calls = 0

    def run(self, transcript: str, source_id: str) -> list[dict]:
        self.calls += 1
        if self.fail_first_call and self.calls == 1:
            raise RuntimeError("entity stage failure (fixture: fail_first_call)")
        out: list[dict] = []
        for key, ents in self.entities_by_key.items():
            if key not in transcript:
                continue
            for e in ents:
                name = str(e.get("name") or key)
                span = e.get("span")
                if span is None:
                    start = transcript.find(name)
                    span = [start, start + len(name)] if start >= 0 else None
                try:
                    ent = _normalize_entity({
                        "name": name,
                        "objectKind": e.get("objectKind", "other"),
                        "canonical_candidates": e.get("canonical_candidates") or [name],
                        "span": span,
                        "confidence": e.get("confidence", 1.0),
                    })
                except Exception:
                    # per-entity brittleness: skip only the bad entity
                    logger.debug("skipping malformed fixture entity %r", e,
                                 exc_info=True)
                    continue
                if ent:
                    out.append(ent)
        return out


def entity_stage_fixture(entities_by_key: dict[str, list[dict]] | None = None) -> EntityStageMock:
    """Default DE2E-1 fixture: port 16379 → other, FalkorDB → tool,
    tortoise#123 → workitem."""
    return EntityStageMock(entities_by_key or {
        "port 16379": [{"name": "port 16379", "objectKind": "other"}],
        "FalkorDB": [{"name": "FalkorDB", "objectKind": "tool"}],
        "tortoise#123": [{"name": "tortoise#123", "objectKind": "workitem"}],
    })


# ---------------------------------------------------------------------------
# M2: two-stage LLM extractor (cheap point model + reasoning relation model).
# ---------------------------------------------------------------------------

_POINTS_SYS = (
    "TASK: extract_points\n"
    "You are given numbered transcript utterances. For EACH one, return a cleaned, "
    "standalone statement (strip leading discourse connectives like but/so/however, "
    "expand contractions, keep the exact meaning; Option A: one point per utterance, "
    "do NOT split into sub-propositions). Return JSON "
    '{"points": {"<index>": "<cleaned statement>", ...}} with one entry per input '
    "index. Do not add, drop, merge, or reorder."
)
_RELATIONS_SYS = (
    "TASK: extract_relations\n"
    "Identify ONLY relations the speaker EXPLICITLY asserted between the given "
    "points — cue words like because/therefore/so (support) or but/however/not "
    "relevant (refute). Do NOT infer unstated relations. Gates: IMPL (supports) or "
    "NAND (refutes). Return JSON "
    '{"relations":[{"op_type":"IMPL|NAND","src":<point index>,"dst":<point index>}]}.'
)

_POINTS_DOC_SYS = (
    "TASK: extract_points_from_section\n"
    "You are given a markdown document section (title + body). Extract structured "
    "claims as Points. Each Point must be a standalone, self-contained statement "
    "that can be understood without the surrounding document context.\n\n"
    "For each claim, classify it into one of these pointKind values:\n"
    "{pointKind_list}\n\n"
    "Also extract:\n"
    "- aboutEntities: list of entity names the claim is about "
    "(people, teams, tools, products, competitors, etc.)\n"
    "- confidence: your confidence that this claim is correctly extracted "
    "and classified (0.0 to 1.0)\n\n"
    'Return JSON: {{"points": [{{"content": "...", "pointKind": "...", '
    '"aboutEntities": ["..."], "confidence": 0.X}}]}}\n\n'
    "Rules:\n"
    "- One claim per array element. Do not merge unrelated claims.\n"
    "- Skip boilerplate (navigation, table of contents, YAML frontmatter).\n"
    'If a section contains no extractable claims, return {{"points": []}}.\n'
    "- Claims must be standalone — resolve all pronouns and implicit references."
)

# Default pointKind descriptions (used when no domain override)
_DEFAULT_POINT_KIND_DESCRIPTIONS: list[tuple[str, str]] = [
    ("statement", "a factual claim or assertion"),
    ("decision", "a binding choice was made"),
    ("vision", "an aspirational future state"),
    ("strategy", "an approach to achieve something"),
    ("plan", "a concrete implementation approach"),
    ("goal", "a desired outcome"),
    ("target", "a measurable objective"),
    ("observation", "an empirical finding or data point"),
    ("hypothesis", "a testable proposition not yet verified"),
]


def _build_pointkind_prompt(point_kinds: list[str] | dict[str, str] | None = None) -> str:
    """Build the pointKind list for the LLM prompt.

    - None → core defaults with descriptions.
    - list[str] → bare names (legacy domain mode).
    - dict[str, str] → kind semantics (#951): ``- kind: description`` lines;
      kinds without a pack description fall back to the core defaults, then
      to the bare name (pack kindDefs are the vocabulary source, the
      defaults are the core fallback — research-r6 §5.4).
    """
    if isinstance(point_kinds, dict) and point_kinds:
        core_descs = dict(_DEFAULT_POINT_KIND_DESCRIPTIONS)
        lines = []
        for kind, desc in point_kinds.items():
            if desc:
                lines.append(f"- {kind}: {desc}")
            elif kind in core_descs:
                lines.append(f"- {kind}: {core_descs[kind]}")
            else:
                lines.append(f"- {kind}")
        return "\n".join(lines)
    if point_kinds:
        lines = [f"- {k}" for k in point_kinds]
    else:
        lines = [f"- {kind}: {desc}" for kind, desc in _DEFAULT_POINT_KIND_DESCRIPTIONS]
    return "\n".join(lines)


def _warn_unrecognized_kinds(extracted_kinds: set[str]) -> None:
    """Warn if any extracted pointKind values are not in the known registry.

    Prints warnings to stderr. Unknown kinds are accepted (open vocabulary) but
    agents should know when the LLM invents a new kind. Uses the bucket-scoped
    ``kind_is_known(kind, "pointKind")`` (#951 — previously a silent TypeError,
    research-r6 §1.2).
    """
    try:
        from tortoise.domain_loader import kind_is_known
        unknown = {k for k in extracted_kinds if not kind_is_known(k, "pointKind")}
        if unknown:
            import sys
            print(
                f"⚠ unrecognized pointKind values: {sorted(unknown)}. "
                f"These will be stored but may not match any registered ontology. "
                f"Register them via a pack manifest (or register_kind) if intentional.",
                file=sys.stderr,
            )
    except Exception:
        pass  # ponytail: validation is best-effort, don't block extraction

_RELATIONS_DOC_SYS = (
    "TASK: extract_relations\n"
    "You are given a list of Points extracted from a document. Identify logical "
    "relationships between them:\n\n"
    "- IMPL: Point A supports, implies, or provides evidence for Point B. "
    "Directional: A → B means 'A supports B'.\n"
    "- NAND: Point A contradicts or is incompatible with Point B. "
    "Symmetric: A ↔ B means 'these cannot both be true'.\n\n"
    "Only report relations that are LOGICALLY NECESSARY — where one claim's truth "
    "implies, supports, or contradicts another. Do NOT report:\n"
    "- Topical similarity (both about the same topic but logically independent)\n"
    "- Co-occurrence (both appear in the same section)\n"
    "- Hierarchical membership (A is a sub-point of B — that's composedOf, not IMPL)\n\n"
    'Return JSON: {"relations": [{"op_type": "IMPL", "src": <index>, "dst": <index>}, ...]}'
)


class MockModel:
    """Deterministic offline stand-in implementing the Model interface. Keys on the
    TASK tag in the system prompt so it can serve either stage without a network."""

    def __init__(self, id: str = "mock"):
        self.id = id

    def complete(self, *, system: str, user: str) -> str:
        payload = json.loads(user)
        if "extract_entities" in system:
            # Mock entity extraction: detect proper nouns / capitalized names
            body = payload.get("section_body", "")
            subjects, objects, entities = [], [], []
            # Detect organization/team names (multi-word capitalized)
            for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', body):
                name = match.group(1)
                if name.lower() not in ("the", "this", "that"):
                    if any(w in name.lower() for w in ("team", "org", "group", "dept")):
                        subjects.append({"name": name, "subjectKind": "team"})
                    else:
                        objects.append({"name": name, "objectKind": "product"})
            # Single-word proper nouns
            for word in re.findall(r'\b[A-Z][a-z]+\b', body):
                if len(word) < 3 or word.lower() in ("the", "this", "that", "these", "those", "each", "every", "some", "any", "all", "both", "few", "many", "most", "other", "such", "no", "nor", "not", "only", "own", "same", "so", "than", "too", "very"):
                    continue
                entities.append(word)
            return json.dumps({"subjects": subjects, "objects": objects,
                               "aboutEntities": entities[:5]})
        if "extract_points_from_section" in system:
            # Document mode: return points from section body
            body = payload.get("section_body", "")
            title = payload.get("section_title", "")  # noqa: F841
            pts = []
            # ponytail: extract sentences as mock points with keyword-based pointKind
            for sent in _SENT.finditer(body):
                text = sent.group(0).strip()
                if len(text) < 20:
                    continue
                kind = _classify_point_kind(text)
                pts.append({
                    "content": text,
                    "pointKind": kind,
                    "aboutEntities": [],
                    "confidence": 0.8,
                })
            return json.dumps({"points": pts})
        if "extract_points" in system:
            utts = payload["utterances"]
            items = utts.items() if isinstance(utts, dict) else enumerate(utts)
            return json.dumps({"points": {str(k): v for k, v in items}})
        if "extract_relations" in system:
            pts = payload["points"]
            rels = []
            for i in range(len(pts)):
                for j in range(i + 1, len(pts)):
                    low_i = _PUNC.sub('', f" {pts[i]['content'].lower()} ")
                    low_j = _PUNC.sub('', f" {pts[j]['content'].lower()} ")
                    if _has_cue(low_j, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                        rels.append({"op_type": "IMPL", "src": i, "dst": j})
                    elif _has_cue(low_j, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                        rels.append({"op_type": "NAND", "src": i, "dst": j})
                    if _has_cue(low_i, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                        rels.append({"op_type": "IMPL", "src": j, "dst": i})
                    elif _has_cue(low_i, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                        rels.append({"op_type": "NAND", "src": j, "dst": i})
            return json.dumps({"relations": rels})
        return "{}"


def _overlap(cleaned: str, source: str) -> float:
    """Fraction of source words retained. Guards against a weak point model
    mis-mapping its index→content keys: a real cleaning keeps most words; a
    cleaning of a *different* utterance does not."""
    src = set(re.findall(r"[a-z0-9]+", source.lower()))
    if not src:
        return 1.0
    return len(set(re.findall(r"[a-z0-9]+", cleaned.lower())) & src) / len(src)


def _json(text: str) -> dict:
    """Tolerant JSON parse — small/local models wrap output in ``` fences, add
    preamble, or (thinking models) emit <think>…</think>. Fall back to the
    outermost {...} span."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1] if t.count("```") >= 2 else t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    start, end = t.find("{"), t.rfind("}")
    if 0 <= start < end:
        return json.loads(t[start:end + 1])
    raise ValueError(f"no JSON object in model output: {text[:120]!r}")


class _PointStage:
    def __init__(self, model):
        self.model = model

    def run(self, utterances, llm_context) -> dict[int, str]:
        """Return {utterance_index: cleaned_content}. The caller owns identity and
        provenance; this is best-effort cleaning only. Tolerates map or list output."""
        numbered = {str(i): u for i, u in enumerate(utterances)}
        out = self.model.complete(
            system=_POINTS_SYS,
            user=json.dumps({"context": llm_context, "utterances": numbered}),
        )
        raw = _json(out).get("points", {})
        cleaned: dict[int, str] = {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                try:  # noqa: SIM105
                    cleaned[int(k)] = str(v)
                except (ValueError, TypeError):
                    pass
        elif isinstance(raw, list):  # tolerate [{src/i, content}] or [str]
            for i, item in enumerate(raw):
                if isinstance(item, dict) and "content" in item:
                    idx = item.get("src", item.get("i", i))
                    try:  # noqa: SIM105
                        cleaned[int(idx)] = str(item["content"])
                    except (ValueError, TypeError):
                        pass
                elif isinstance(item, str):
                    cleaned[i] = item
        return cleaned


class _DocumentPointStage:
    """Per-section structured output parser for document mode.
    Returns list[dict] with {content, pointKind, aboutEntities, confidence}."""

    def __init__(self, model, *, point_kinds: list[str] | None = None):
        self.model = model
        self._system = _POINTS_DOC_SYS.format(
            pointKind_list=_build_pointkind_prompt(point_kinds),
        )

    def run(self, title: str, body: str, llm_context: str) -> list[dict]:
        out = self.model.complete(
            system=self._system,
            user=json.dumps({
                "context": llm_context,
                "section_title": title,
                "section_body": body,
            }),
        )
        raw = _json(out).get("points", [])
        if isinstance(raw, list):
            return [
                {
                    "content": str(r.get("content", "")),
                    "pointKind": str(r.get("pointKind", "statement")),
                    "aboutEntities": list(r.get("aboutEntities", [])),
                    "confidence": float(r.get("confidence", 0.5)),
                }
                for r in raw
                if isinstance(r, dict) and r.get("content")
            ]
        return []


class _RelationStage:
    def __init__(self, model, *, system_prompt: str | None = None):
        self.model = model
        self._sys = system_prompt or _RELATIONS_SYS

    def run(self, point_contents, llm_context, *, multi_source: bool = False):
        extra = " These statements come from DIFFERENT sources. Identify where they agree or contradict." if multi_source else ""
        out = self.model.complete(
            system=self._sys,
            user=json.dumps({"context": llm_context + extra,
                             "points": [{"i": i, "content": c}
                                        for i, c in enumerate(point_contents)]}),
        )
        return _json(out).get("relations", [])


# -- S7 Semantic Extractor ---------------------------------------------------

_SEMANTIC_SYS = (
    "TASK: extract_entities\n"
    "Extract Subjects (organizations, teams, roles, people) and Objects "
    "(tools, products, documents, competitors, skills, etc.) from the document "
    "section.\n\n"
    "Subjects represent agents that act, own, or are responsible. Types: "
    "{subject_kinds}\n"
    "Objects represent things that are created, used, or referenced. Types: "
    "{object_kinds}\n\n"
    "Return JSON:\n"
    '{{"subjects": [{{"name": "...", "subjectKind": "..."}}], '
    '"objects": [{{"name": "...", "objectKind": "..."}}], '
    '"aboutEntities": ["entity_name", ...]}}\n\n'
    "Rules:\n"
    "- Only extract entities that appear in the text. Do not invent.\n"
    "- Prefer specific over generic kinds (e.g. 'product' over 'other').\n"
    "- aboutEntities lists all entity names this section is about.\n"
    '- If no entities found, return {{"subjects": [], "objects": [], "aboutEntities": []}}.\n'
)


class _SemanticStage:
    """LLM call for entity extraction from a document section."""

    def __init__(self, model, *, subject_kinds: list[str] | None = None,
                 object_kinds: list[str] | None = None):
        self.model = model
        self.subject_kinds = subject_kinds or ["organization", "team", "role",
                                               "legalPerson", "naturalPerson", "other"]
        self.object_kinds = object_kinds or ["document", "product", "customer",
                                             "competitor", "user", "skill",
                                             "workflow", "tool", "agent", "indicator",
                                             "database", "api", "code", "software",
                                             "infrastructure", "agreement", "standard",
                                             "epic", "project", "task", "other"]

    def run(self, title: str, body: str, llm_context: str) -> dict:
        system = _SEMANTIC_SYS.format(
            subject_kinds=", ".join(self.subject_kinds),
            object_kinds=", ".join(self.object_kinds),
        )
        out = self.model.complete(
            system=system,
            user=json.dumps({
                "context": llm_context,
                "section_title": title,
                "section_body": body,
            }),
        )
        raw = _json(out)
        return {
            "subjects": [
                {"name": str(s.get("name", "")),
                 "subjectKind": str(s.get("subjectKind", "other"))}
                for s in raw.get("subjects", [])
                if isinstance(s, dict) and s.get("name")
            ],
            "objects": [
                {"name": str(o.get("name", "")),
                 "objectKind": str(o.get("objectKind", "other"))}
                for o in raw.get("objects", [])
                if isinstance(o, dict) and o.get("name")
            ],
            "aboutEntities": list(raw.get("aboutEntities", [])),
        }


_ENTITY_CONV_SYS = (
    "TASK: extract_conversation_entities\n"
    "Extract named OBJECT entities from the conversation transcript: tools, "
    "products, documents, projects, work items (issues/PRs), skills, agents, "
    "workflows, agreements, standards, tags, users, etc. Do NOT extract "
    "speakers/subjects as entities — only the things they act on or reference. "
    "Object kinds: {object_kinds}\n\n"
    'Return JSON: {{"entities": [{{"name": "...", "objectKind": "...", '
    '"canonical_candidates": ["canonical variant", ...], '
    '"span": [start_char, end_char], "confidence": 0.9]}}}}\n\n'
    "Rules:\n"
    "- Only extract entities that appear verbatim in the transcript.\n"
    "- `name` = the exact mention (display form).\n"
    "- `canonical_candidates` = normalized/alias variants of the name (lowercase, "
    "abbreviations, issue refs without repo prefix) used for cross-session dedup.\n"
    "- `span` = zero-based character offsets of the name in the transcript.\n"
    "- Prefer specific kinds over 'other'.\n"
    '- If no entities are found, return {{"entities": []}}.\n'
)


class EntityStage(_SemanticStage):
    """Phase-2 conversation entity stage — extends _SemanticStage with span +
    canonical_candidates prompt params (plan W-1 / §5.1 / §6.1).

    Object-only output: [{name, objectKind, canonical_candidates, span,
    confidence}] — one dict per extracted entity mention. The S7
    `extract_entities` document surface (Subjects+Objects) is untouched
    (DE2E-N12: `extract_conversation_entities` is the renamed Phase-2 API).
    """

    def __init__(self, model, *, object_kinds: list[str] | None = None):
        super().__init__(model, object_kinds=_intersect_object_kinds(
            object_kinds or [
                "project", "workitem", "document", "tag", "user", "skill",
                "tool", "agent", "workflow", "agreement", "standard", "other",
            ],
        ))
        self._system = _ENTITY_CONV_SYS

    def run(self, transcript: str, source_id: str) -> list[dict]:
        """LLM call for conversation entity extraction. Raises on parse failure
        — the caller (extract_conversation_entities) applies the rule fallback."""
        system = self._system.format(object_kinds=", ".join(self.object_kinds))
        out = self.model.complete(
            system=system,
            user=json.dumps({
                "context": f"conversation:{source_id}",
                "transcript": transcript,
            }),
        )
        raw = _json(out).get("entities", [])
        if not isinstance(raw, list):
            # DE2E-review (parse brittleness): a non-list entities payload is a
            # parse failure — raise so the caller's rule fallback runs instead
            # of silently returning [] (which drops valid entities AND skips
            # the fallback).
            raise ValueError(
                f"entities must be a JSON list, got {type(raw).__name__}: "
                f"{str(raw)[:120]!r}"
            )
        entities: list[dict] = []
        for e in raw:
            try:
                ent = _normalize_entity(e)
            except Exception:
                # per-entity brittleness: skip only the bad entity, keep the rest
                logger.debug("skipping malformed entity %r", e, exc_info=True)
                continue
            if ent:
                entities.append(ent)
        return entities


class LLMExtractor:
    """Cheap point model + large relation model, same Extractor interface as the
    mock. Spans/quotes come from the deterministic segmenter, so provenance
    grounding holds regardless of model strength; the point model only cleans
    `content`."""

    def __init__(self, point_model, relation_model, *, prompt_version: str = "v2"):
        self.points = _PointStage(point_model)
        self.relations = _RelationStage(relation_model)
        self.version = f"{point_model.id}/{relation_model.id}@{prompt_version}"

    def run(self, transcript: str, source_id: str, api: EventAPI,
            *, max_utterances: int = 0, multi_source: bool = False,
            domain: str | None = None) -> None:
        # Document mode: markdown with ## headers → split on sections, not utterances
        is_doc, doc_sections = _document_sections(transcript)
        if is_doc:
            self._run_document(doc_sections, source_id, api,
                               max_utterances=max_utterances,
                               domain=domain)
            return

        segs = list(_utterances(transcript))
        if max_utterances:  # exploration cap (whole-transcript relations don't scale yet)
            segs = segs[:max_utterances]

        # Deterministic 1:1 utterance→point. The segmenter owns identity + provenance;
        # the model only supplies cleaned content, with the raw utterance as fallback,
        # so a weak point model can neither drop points nor corrupt grounding.
        cleaned = self.points.run([s[1] for s in segs], f"conversation:{source_id}")
        ids, contents = [], []
        for i, (speaker, text, span) in enumerate(segs):
            c = cleaned.get(i)
            content = c if (c and _overlap(c, text) >= 0.5) else text
            contents.append(content)
            prov = provenance(source_id, span, quote=text, speaker=speaker,
                              extracted_by=self.version)
            # #49 Phase 2: context is deprecated — use extractedFrom for provenance
            ids.append(api.add_point(content, prov, extractedFrom=source_id))

        # #1194 flood bound: the relation model can emit up to O(n²) relations
        # (the prompt says "only relations the speaker asserted", but nothing
        # enforces it — MockModel's cue-word sparsity hides the flood in tests).
        # Each relation writes an IMPL/NAND operator node counted by the points
        # quota, so a permissive model could write more operator nodes than the
        # session pre-write estimate (2 × sentences, #822) counted — bypassing
        # the 402 flood gate the estimate feeds. Dedupe and clamp operators
        # ≤ points (len(ids)) so the estimate's ×2 is a TRUE ceiling on node
        # writes.
        rels = self.relations.run(contents, f"conversation:{source_id}", multi_source=multi_source)
        seen: set[tuple] = set()
        bounded: list[dict] = []
        for r in rels:
            s, d = r.get("src"), r.get("dst")
            if s is None or d is None or not (0 <= s < len(ids)) or not (0 <= d < len(ids)):
                continue
            key = (r.get("op_type"), s, d)
            if key in seen:
                continue
            seen.add(key)
            bounded.append(r)
            if len(bounded) >= len(ids):
                break
        for r in bounded:
            s, d = r.get("src"), r.get("dst")
            # ground the operator in the utterance that asserted the relation (dst)
            speaker, text, span = segs[d]
            prov = provenance(source_id, span, quote=text, speaker=speaker,
                              extracted_by=self.version)
            api.add_operator(r["op_type"], [ids[s], ids[d]], prov)

    def _run_document(self, sections, source_id, api, *,
                      max_utterances: int = 0, max_points_per_batch: int = 40,
                      skip_on_failure: bool = False,
                      authored_by: str = "unknown",
                      domain: str | None = None):
        """Document mode: extract Points per ## section, then IMPL/NAND across all."""
        if max_utterances:
            sections = sections[:max_utterances]

        # Resolve domain pointKinds for the prompt. #951 (epic #909 slice 4c):
        # previously called the nonexistent domain_kinds() — AttributeError
        # swallowed by the ponytail, so the pack vocabulary never reached the
        # prompt (research-r6 §1.2). Now the adapter returns pack kind
        # SEMANTICS (kind → description from pack kindDefs; core defaults are
        # the fallback in _build_pointkind_prompt).
        point_kinds = None
        if domain:
            try:
                from tortoise.domain_loader import domain_kinds, domain_kind_semantics  # noqa: I001
                semantics = domain_kind_semantics(domain, "pointKind")
                if semantics:  # noqa: SIM108
                    point_kinds = semantics  # pack kind semantics
                else:
                    point_kinds = domain_kinds(domain, "pointKind")
            except Exception:
                pass  # ponytail: use defaults if loader fails

        doc_stage = _DocumentPointStage(self.points.model, point_kinds=point_kinds)
        all_point_ids: list[str] = []
        all_contents: list[str] = []
        seen_kinds: set[str] = set()
        failed: list[str] = []

        for title, body, span in sections:
            try:
                points_data = doc_stage.run(title, body, f"document:{source_id}")
            except Exception:
                if skip_on_failure:
                    failed.append(title)
                    continue
                raise

            for pd in points_data:
                # ponytail: provenance carries the section span for traceability
                prov = provenance(source_id, [span[0], span[1]], quote=body,
                                  speaker="document", extracted_by=self.version)
                pk = pd.get("pointKind", "statement")
                seen_kinds.add(pk)
                # #49 Phase 2: context is deprecated — use extractedFrom for provenance
                pid = api.add_point(
                    pd["content"], prov,
                    pointKind=pk,
                    aboutEntities=pd["aboutEntities"],
                    confidence=pd["confidence"],
                    authoredBy=authored_by,
                    extractedFrom=source_id,
                )
                all_point_ids.append(pid)
                all_contents.append(pd["content"])

        # Validate extracted pointKinds against domain registry
        _warn_unrecognized_kinds(seen_kinds)

        if not all_point_ids:
            return

        # Batch relation extraction
        rel_stage = _RelationStage(self.relations.model,
                                    system_prompt=_RELATIONS_DOC_SYS)
        seen_pairs: set[tuple[int, int]] = set()
        for batch_start in range(0, len(all_point_ids), max_points_per_batch):
            batch_ids = all_point_ids[batch_start:batch_start + max_points_per_batch]
            batch_contents = all_contents[batch_start:batch_start + max_points_per_batch]
            for r in rel_stage.run(batch_contents, f"document:{source_id}"):
                s, d = r.get("src"), r.get("dst")
                if s is None or d is None:
                    continue
                if not (0 <= s < len(batch_ids)) or not (0 <= d < len(batch_ids)):
                    continue
                if s == d:
                    continue
                # NAND: canonical pair order for dedup
                if r.get("op_type") == "NAND":
                    pair = tuple(sorted([batch_start + s, batch_start + d]))
                else:
                    pair = (batch_start + s, batch_start + d)
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                try:  # noqa: SIM105
                    api.add_operator(
                        r["op_type"],
                        [all_point_ids[pair[0]], all_point_ids[pair[1]]],
                        provenance(source_id, None, quote="",
                                       speaker="document",
                                       extracted_by=self.version),
                    )
                except Exception:
                    # ponytail: skip malformed operator, don't abort whole run
                    pass

        # Store results for post-extraction reporting
        self._last_result = {
            "points": len(all_point_ids),
            "operators": len(seen_pairs),
            "sections": len(sections),
            "point_ids": all_point_ids,
            "failed_sections": failed,
        }

    # -- public API for standalone use ---------------------------------------
    def extract_from_document(self, text: str, source_id: str, api: EventAPI, *,
                               authored_by: str = "unknown",
                               max_sections: int = 0,
                               max_points_per_batch: int = 40,
                               skip_on_failure: bool = False,
                               domain: str | None = None) -> dict:
        """Extract Points + IMPL/NAND from a markdown document.

        Returns: {"points": N, "operators": M, "sections": S,
                  "point_ids": [...], "failed_sections": [...]}
        """
        is_doc, doc_sections = _document_sections(text)
        if not is_doc:
            return {"points": 0, "operators": 0, "sections": 0,
                    "point_ids": [], "failed_sections": []}
        self._run_document(doc_sections, source_id, api,
                            max_utterances=max_sections,
                            max_points_per_batch=max_points_per_batch,
                            skip_on_failure=skip_on_failure,
                            authored_by=authored_by,
                            domain=domain)
        return getattr(self, "_last_result", {
            "points": 0, "operators": 0, "sections": 0,
            "point_ids": [], "failed_sections": [],
        })

    def extract_entities(self, text: str, source_id: str, api: EventAPI, *,
                         domain: str | None = None,
                         skip_on_failure: bool = False) -> dict:
        """S7: Extract Subjects + Objects from a markdown document.

        Emits SubjectAdded/ObjectAdded events via the API. Deduplicates by name
        within a run (idempotent across runs via IngestKey in begin_ingest).

        Returns: {"subjects": N, "objects": M, "entities": [names...]}
        """
        is_doc, sections = _document_sections(text)
        if not is_doc:
            return {"subjects": 0, "objects": 0, "entities": []}

        # Build kind vocabularies from the domain_loader adapter (#951):
        # domain_kinds()/known_kinds(bucket) previously did not exist — the
        # TypeError fell through to the defaults below.
        subject_kinds = None
        object_kinds = None
        try:
            from tortoise.domain_loader import known_kinds, domain_kinds  # noqa: I001
            if domain:
                subject_kinds = domain_kinds(domain, "subjectKind")
                object_kinds = domain_kinds(domain, "objectKind")
            else:
                subject_kinds = list(known_kinds("subjectKind"))
                object_kinds = list(known_kinds("objectKind"))
        except Exception:
            pass  # ponytail: use defaults if domain_loader not available

        stage = _SemanticStage(
            self.points.model,
            subject_kinds=subject_kinds,
            object_kinds=object_kinds,
        )
        context = f"document:{source_id}"

        seen_subjects: dict[str, str] = {}  # name → id
        seen_objects: dict[str, str] = {}   # name → id
        all_entity_names: set[str] = set()
        n_subjects, n_objects = 0, 0

        for title, body, span in sections:  # noqa: B007
            try:
                entities = stage.run(title, body, context)
            except Exception:
                if skip_on_failure:
                    continue
                raise

            for s in entities.get("subjects", []):
                name = s["name"].strip()
                if not name or name in seen_subjects:
                    continue
                sid = api.add_subject(name, s["subjectKind"])
                seen_subjects[name] = sid
                all_entity_names.add(name)
                n_subjects += 1

            for o in entities.get("objects", []):
                name = o["name"].strip()
                if not name or name in seen_objects:
                    continue
                oid = api.add_object(name, o["objectKind"])
                seen_objects[name] = oid
                all_entity_names.add(name)
                n_objects += 1

            for ename in entities.get("aboutEntities", []):
                ename = str(ename).strip()
                if ename:
                    all_entity_names.add(ename)

        return {
            "subjects": n_subjects,
            "objects": n_objects,
            "entities": sorted(all_entity_names),
        }

    def extract_conversation_entities(self, transcript: str, source_id: str, api: EventAPI, *,
                                      model=None, domain: str | None = None,
                                      entity_stage=None) -> list[dict]:
        """Phase-2: conversation entity extraction → Object-only entity dicts
        [{name, canonical_candidates, objectKind, span, confidence}].

        RENAMED API vs the S7 `extract_entities` (document Subjects+Objects) so
        the two extraction tasks never collide (DE2E-N12). `entity_stage` is an
        injectable deterministic mock (EntityStageMock) for tests; None → LLM
        EntityStage with domain-aware objectKind vocab via domain_loader.
        LLM failure → rule/keyword fallback extracting known refs (DE2E-N2).
        """
        if entity_stage is None:
            object_kinds = None
            try:
                from tortoise.domain_loader import domain_kinds, known_kinds
                if domain:
                    object_kinds = domain_kinds(domain, "objectKind")
                else:
                    object_kinds = list(known_kinds("objectKind"))
            except Exception:
                pass  # ponytail: use the default conversation vocab
            entity_stage = EntityStage(model or self.points.model,
                                       object_kinds=object_kinds)
        try:
            raw = entity_stage.run(transcript, source_id)
        except Exception:
            logger.warning(
                "extract_conversation_entities: entity stage failed for %s — "
                "rule/keyword fallback (DE2E-N2)", source_id, exc_info=True,
            )
            raw = _rule_fallback_entities(transcript)
        out: list[dict] = []
        for e in raw or []:
            try:
                ent = _normalize_entity(e)
            except Exception:
                # per-entity brittleness: skip only the bad entity
                logger.debug("skipping malformed entity %r", e, exc_info=True)
                continue
            if ent:
                out.append(ent)
        return out


# -- Module-level convenience API ------------------------------------------------

def extract_from_document(
    text: str,
    source_id: str,
    api: EventAPI,
    *,
    point_model,
    relation_model,
    authored_by: str = "unknown",
    max_sections: int = 0,
    max_points_per_batch: int = 40,
    skip_on_failure: bool = False,
    domain: str | None = None,
) -> dict:
    """Extract Points + IMPL/NAND from a markdown document.

    Returns: {"points": N, "operators": M, "sections": S,
              "point_ids": [...], "failed_sections": [...]}
    """
    extractor = LLMExtractor(point_model, relation_model)
    is_doc, doc_sections = _document_sections(text)
    if not is_doc:
        return {"points": 0, "operators": 0, "sections": 0,
                "point_ids": [], "failed_sections": []}
    extractor._run_document(
        doc_sections, source_id, api,
        max_utterances=max_sections,
        max_points_per_batch=max_points_per_batch,
        skip_on_failure=skip_on_failure,
        authored_by=authored_by,
        domain=domain,
    )
    return getattr(extractor, "_last_result", {
        "points": 0, "operators": 0, "sections": 0,
        "point_ids": [], "failed_sections": [],
    })


def extract_conversation_entities(
    transcript: str,
    source_id: str,
    api: EventAPI,
    *,
    model=None,
    entity_stage=None,
    domain: str | None = None,
) -> list[dict]:
    """Phase-2 convenience: conversation entity extraction → Object-only list
    [{name, canonical_candidates, objectKind, span, confidence}].

    ``model=None`` + ``entity_stage=None`` → rule/keyword fallback only
    (deterministic, no LLM — the safe default for MockExtractor pipelines).
    ``entity_stage`` injects a deterministic mock (EntityStageMock).
    """
    if model is not None:
        extractor = LLMExtractor(model, model)
        return extractor.extract_conversation_entities(
            transcript, source_id, api, model=model, domain=domain,
            entity_stage=entity_stage,
        )
    if entity_stage is not None:
        try:
            raw = entity_stage.run(transcript, source_id)
        except Exception:
            logger.warning(
                "extract_conversation_entities: injected stage failed — "
                "rule/keyword fallback (DE2E-N2)", exc_info=True,
            )
            raw = _rule_fallback_entities(transcript)
    else:
        raw = _rule_fallback_entities(transcript)
    out: list[dict] = []
    for e in raw or []:
        try:
            ent = _normalize_entity(e)
        except Exception:
            # per-entity brittleness: skip only the bad entity
            logger.debug("skipping malformed entity %r", e, exc_info=True)
            continue
        if ent:
            out.append(ent)
    return out
