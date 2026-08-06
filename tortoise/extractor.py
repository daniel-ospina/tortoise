"""Extractor — transcript segment(s) → events, via the shared EventAPI.

M0 ships a deterministic, offline `MockExtractor` so the spine runs with no API
key: one utterance-level point each (Option A), plus asserted-only {NAND, IMPL}
operators inferred from discourse connectives. The real LLM extractor (M2) will
implement the same `Extractor` interface — segment in, events out — so nothing
downstream changes when it's swapped in.
"""
from __future__ import annotations

import json
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

    for i, (title, start, body_start) in enumerate(sections):
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
    if any(c.isdigit() for c in t) and '%' in t and len(t) > 60:
        if any(w in low for w in ('fail', 'unchanged', 'rate', 'increase', 'decrease', 'drop', 'rise', 'fall')):
            return True
    return False


class MockExtractor:
    """Heuristic, deterministic extractor for the M0 spine."""

    version = "mock@0"

    def run(self, transcript: str, source_id: str, api: EventAPI,
            *, multi_source: bool = False) -> None:
        if multi_source:
            # Multi-source mode: claim extraction → embedding pre-filter → cue-word gate typing
            pids = []
            for speaker, text, span in _utterances(transcript):
                if not _is_claim(text):
                    continue
                prov = provenance(source_id, span, quote=text, speaker=speaker,
                                  extracted_by=self.version)
                # #49 Phase 2: context is deprecated — use extractedFrom for provenance
                pid = api.add_point(content=text, provenance=prov,
                                    extractedFrom=source_id)
                pids.append((pid, text.lower(), prov))
            
            # Use embedding pre-filter if available
            try:
                from tortoise.embeddings import find_cross_source_matches
                # Build points dict from API's stored points
                # We need to reconstruct from the log — simple approach: use what we collected
                all_points = {}
                for pid_i, ti, pvi in pids:
                    sp = pvi.get('speaker', 'unknown') if isinstance(pvi, dict) else 'unknown'
                    all_points[pid_i] = {"content": ti, "speaker": sp}
                
                emb_matches = find_cross_source_matches(all_points, threshold=0.40)
                matched_pairs = set()
                for m in emb_matches:
                    matched_pairs.add((m['src'], m['dst']))
                
                # Only create operators for embedding-matched pairs with cue words
                for i in range(len(pids)):
                    pi, ti, pvi = pids[i]
                    for j in range(i + 1, len(pids)):
                        pj, tj, pvj = pids[j]
                        if (pi, pj) not in matched_pairs and (pj, pi) not in matched_pairs:
                            continue
                        # Check cue words for gate type
                        gate = None
                        ti_clean = _PUNC.sub('', f" {ti} ")
                        tj_clean = _PUNC.sub('', f" {tj} ")
                        if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                            gate = "IMPL"
                        elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                            gate = "NAND"
                        if gate:
                            api.add_operator(gate, inputs=[pi, pj], provenance=pvi)
                        elif (pi, pj) in matched_pairs:
                            # Semantic agreement: similar claims from different sources = IMPL
                            # But require shared noun phrase to avoid weak thematic connections
                            words_i = set(ti.split())
                            words_j = set(tj.split())
                            shared = words_i & words_j
                            # Need at least 3 shared content words (not just stopwords)
                            stopwords = {'the','a','an','is','are','was','were','be','been','being',
                                        'have','has','had','do','does','did','will','would','could',
                                        'should','may','might','can','shall','to','of','in','for',
                                        'on','with','at','by','from','as','into','through','during',
                                        'and','but','or','nor','not','so','yet','both','either','neither',
                                        'if','then','else','when','where','why','how','this','that','these','those',
                                        'it','its','they','them','their','we','our','i','my','you','your',
                                        'more','less','very','also','just','only','now','still','already'}
                            shared_content = shared - stopwords
                            if len(shared_content) >= 3:
                                api.add_operator("IMPL", inputs=[pi, pj], provenance=pvi)
            except Exception:
                # Fallback: cue-word only all-pairs (noisy but works)
                # (catches ImportError for missing dependencies AND runtime errors
                #  like sklearn ValueError on empty vocabulary)
                for i in range(len(pids)):
                    for j in range(i + 1, len(pids)):
                        pi, ti, pvi = pids[i]
                        pj, tj, pvj = pids[j]
                        gate = None
                        ti_clean = _PUNC.sub('', f" {ti} ")
                        tj_clean = _PUNC.sub('', f" {tj} ")
                        if _has_cue(ti_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES) or _has_cue(tj_clean, _SUPPORT_SINGLE_RE, _SUPPORT_PHRASES):
                            gate = "IMPL"
                        elif _has_cue(ti_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES) or _has_cue(tj_clean, _REFUTE_SINGLE_RE, _REFUTE_PHRASES):
                            gate = "NAND"
                        if gate:
                            api.add_operator(gate, inputs=[pi, pj], provenance=pvi)
        else:
            # Sequential mode (original): only connect consecutive utterances
            prev_pid = None
            for speaker, text, span in _utterances(transcript):
                prov = provenance(source_id, span, quote=text, speaker=speaker,
                                  extracted_by=self.version)
                # #49 Phase 2: context is deprecated — use extractedFrom for provenance
                pid = api.add_point(content=text, provenance=prov,
                                    extractedFrom=source_id)
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


def _build_pointkind_prompt(point_kinds: list[str] | None = None) -> str:
    """Build the pointKind list for the LLM prompt.

    If point_kinds is provided, use those values (bare names for domain kinds).
    Otherwise use default descriptions.
    """
    if point_kinds:
        lines = [f"- {k}" for k in point_kinds]
    else:
        lines = [f"- {kind}: {desc}" for kind, desc in _DEFAULT_POINT_KIND_DESCRIPTIONS]
    return "\n".join(lines)


def _warn_unrecognized_kinds(extracted_kinds: set[str]) -> None:
    """Warn if any extracted pointKind values are not in the known registry.

    Prints warnings to stderr. Unknown kinds are accepted (open vocabulary) but
    agents should know when the LLM invents a new kind.
    """
    try:
        from tortoise.domain_loader import kind_is_known
        unknown = {k for k in extracted_kinds if not kind_is_known(k, "pointKind")}
        if unknown:
            import sys
            print(
                f"⚠ unrecognized pointKind values: {sorted(unknown)}. "
                f"These will be stored but may not match any registered ontology. "
                f"Register them via domain manifest if intentional.",
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
            title = payload.get("section_title", "")
            pts = []
            # ponytail: extract sentences as mock points with keyword-based pointKind
            for sent in _SENT.finditer(body):
                text = sent.group(0).strip()
                if len(text) < 20:
                    continue
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
                try:
                    cleaned[int(k)] = str(v)
                except (ValueError, TypeError):
                    pass
        elif isinstance(raw, list):  # tolerate [{src/i, content}] or [str]
            for i, item in enumerate(raw):
                if isinstance(item, dict) and "content" in item:
                    idx = item.get("src", item.get("i", i))
                    try:
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

        for r in self.relations.run(contents, f"conversation:{source_id}", multi_source=multi_source):
            s, d = r.get("src"), r.get("dst")
            if s is None or d is None or not (0 <= s < len(ids)) or not (0 <= d < len(ids)):
                continue
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

        # Resolve domain pointKinds for the prompt
        point_kinds = None
        if domain:
            try:
                from tortoise.domain_loader import domain_kinds
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

                try:
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

        # Build kind vocabularies from domain loader
        subject_kinds = None
        object_kinds = None
        try:
            from tortoise.domain_loader import known_kinds, domain_kinds
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

        for title, body, span in sections:
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
