"""Session indexer — metadata extraction for agent session files.

Extracts narrative_arc, summary, keywords, topics, issues, PRs, and
critical_decisions from session markdown files via LLM or keyword fallback.

Model specs follow the `provider:model` convention from ingest.build_model:
    ollama:MODEL    → local Ollama endpoint (http://localhost:11434, no key)
                      — PII-safe: content never leaves the machine
    gpt-5-mini / gpt-4o-mini / gpt-4o → OpenAI (OPENAI_API_KEY)

Usage:
    python -m tortoise.session_indexer <file_path>
    python -m tortoise.session_indexer --model ollama:llama3.2:3b <file_path>
    python -m tortoise.session_indexer --dir <directory> --batch
"""
from __future__ import annotations

import json
import os
import re
import sys
import hashlib
from pathlib import Path
from typing import Any

# ── YAML frontmatter parsing ──────────────────────────────────────

_FM_RE = re.compile(r'^---\s*\n(.*?)\n---', re.DOTALL)


def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content.

    Uses the canonical boundary regex (this module's ``_FM_RE``), imported by
    ``ingest_corpus`` so both sides
    agree on what constitutes frontmatter (review round 5 P2): a file starting
    ``---sessionId: foo\n---`` must parse as NO frontmatter here, exactly as
    ingest sees it — otherwise health derives a different event_id and the
    sweep never converges. Non-dict roots (list/scalar YAML) return {} —
    a malformed corpus file must degrade to the file-stem fallback, never
    crash the health check / doctor / sweep (review round 5 P2).
    """
    import re as _re
    m = _FM_RE.match(content)
    if not m:
        return {}
    try:
        import yaml
        parsed = yaml.safe_load(m.group(1))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}

# ── Keyword extraction (TF-IDF + graph entities, no LLM) ──────────

# Stopwords filtered before TF-IDF scoring
_STOPWORDS = frozenset({
    'the','a','an','is','was','are','were','be','been','has','have','had',
    'do','does','did','will','would','could','should','can','may','might',
    'i','you','he','she','it','we','they','me','him','her','us','them',
    'my','your','his','its','our','their','this','that','these','those',
    'and','or','but','not','no','yes','if','then','else','when','where',
    'what','why','how','in','on','at','to','of','for','with','from','by',
    'as','so','just','also','very','really','too','only','about','like',
    'all','some','any','each','every','more','most','here','there','now',
    'up','out','one','two','go','get','see','know','think','say','make',
    'use','take','come','look','need','let','find','want','work','well',
    'ok','okay','yeah','yes','right','good','great','done','got','going',
    'new','old','first','last','next','still','even','way','thing','much',
    'many','back','into','over','after','before','between','through',
    'user','assistant','agent','session','file','data','time','run',
    'code','error','fix','change','add','set','try','call','return',
})

# Cached IDF model — built once across the corpus
_idf_model: dict[str, float] | None = None
_corpus_size: int = 0


def _tokenize(text: str) -> list[str]:
    """Lowercase, extract alphanumeric tokens, filter stopwords and short terms."""
    import re as _re
    tokens = _re.findall(r'[a-z0-9][a-z0-9_./-]*[a-z0-9]', text.lower())
    return [t for t in tokens if len(t) > 2 and t not in _STOPWORDS]


def _build_idf(corpus_paths: list[str] | None = None, force: bool = False) -> dict[str, float]:
    """Build IDF model from session files. Cached globally, rebuilt when force=True."""
    global _idf_model, _corpus_size
    if _idf_model is not None and not force:
        return _idf_model
    
    import math
    from pathlib import Path
    
    if corpus_paths is None:
        corpus_paths = [str(p) for p in session_corpus_dir().glob("*.md")]
    
    df = {}  # document frequency: term → how many docs contain it
    doc_count = 0
    
    for path in corpus_paths:
        try:
            with open(path) as f:
                text = f.read()
        except Exception:
            continue
        doc_count += 1
        unique_terms = set(_tokenize(text))
        for term in unique_terms:
            df[term] = df.get(term, 0) + 1
    
    _corpus_size = max(doc_count, 1)
    _idf_model = {term: math.log(_corpus_size / (count + 1)) + 1 for term, count in df.items()}
    return _idf_model


def _tfidf_keywords(content: str, top_n: int = 8) -> list[str]:
    """Extract top TF-IDF keywords from a single document."""
    idf = _build_idf()
    tokens = _tokenize(content)
    if not tokens:
        return []
    
    from collections import Counter
    tf = Counter(tokens)
    max_tf = max(tf.values()) if tf else 1
    
    scores = {}
    for term, count in tf.items():
        tf_norm = count / max_tf
        scores[term] = tf_norm * idf.get(term, 1.0)
    
    return [t for t, _ in sorted(scores.items(), key=lambda x: -x[1])[:top_n]]


# Cached FalkorDB connection for graph entity lookups
_graph_db = None

def _graph_entity_keywords(content: str) -> list[str]:
    """Find Object and Subject names from the graph mentioned in content."""
    global _graph_db
    content_lower = content.lower()
    matches = []
    try:
        import os as _os
        uri = _os.environ.get('TORTOISE_DB_URI', '')
        if not uri:
            return []
        if _graph_db is None:
            from falkordb import FalkorDB
            from urllib.parse import urlparse
            parsed = urlparse(uri)
            host = parsed.hostname or 'localhost'
            port = parsed.port or 16379
            _graph_db = FalkorDB(host=host, port=port)
        g = _graph_db.select_graph('tortoise')
        rows = g.query('MATCH (n) WHERE (n:Object OR n:Subject) AND n.name IS NOT NULL RETURN DISTINCT n.name').result_set
        for row in rows:
            name = str(row[0])
            if len(name) > 3 and name.lower() in content_lower:
                matches.append(name)
    except Exception:
        pass
    return matches


def extract_keywords_from_frontmatter(content: str) -> dict:
    """Extract metadata from session content — no LLM required.
    
    Uses TF-IDF over the full corpus to find distinctive terms, plus
    graph entity matching for high-precision Object/Subject references.
    
    Returns dict with keys: summary, narrative_arc, keywords, topics, issues, prs.
    """
    fm = _parse_frontmatter(content)
    
    # Summary from title or first substantive line
    summary = fm.get("title", "")
    if not summary:
        body = content.split("---", 2)[-1] if "---" in content else content
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 10:
                summary = stripped[:200]
                break
    
    # Keywords: frontmatter tags + TF-IDF terms + graph entity matches
    keywords = fm.get("tags", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    
    tfidf_terms = _tfidf_keywords(content)
    graph_terms = _graph_entity_keywords(content)
    
    # Combine: tags first (explicit), then graph entities (high precision), then TF-IDF (fill)
    keywords = list(dict.fromkeys(keywords + graph_terms + tfidf_terms))[:10]
    
    # Topics from frontmatter domain or inferred from keyword clusters
    topics = []
    domain = fm.get("domain", "")
    if domain:
        topics.append(domain)
    infra_terms = {"docker","container","volume","port","deploy","config","server","host","network"}
    data_terms = {"graph","database","index","search","query","falkordb","tortoise","redis","sql"}
    eng_terms = {"api","sdk","mcp","extension","test","e2e","typecheck","pipeline","workflow",
                  "commit","pr","github","issue","python","typescript"}
    kw_set = set(keywords)
    if kw_set & infra_terms:
        topics.append("infrastructure")
    if kw_set & data_terms:
        topics.append("data")
    if kw_set & eng_terms:
        topics.append("engineering")
    topics = list(dict.fromkeys(topics))[:3]
    
    # Issues from frontmatter or content regex
    issues = fm.get("issues", [])
    if isinstance(issues, str):
        issues = [issues]
    # Also scan content for repo#NNN patterns
    issue_refs = re.findall(r'([a-zA-Z0-9_-]+)#(\d+)', content)
    for repo, num in issue_refs:
        ref = f"{repo}#{num}"
        if ref not in issues:
            issues.append(ref)
    issues = issues[:20]
    
    # PRs from content
    prs = fm.get("prs", [])
    if isinstance(prs, str):
        prs = [prs]
    
    # Narrative arc from frontmatter phases or basic generation
    narrative_arc = fm.get("narrative_arc", [])
    if not narrative_arc:
        narrative_arc = [{
            "phase": "Session",
            "topic": summary[:100] or "Untitled",
            "decisions": [],
            "message_range": [1, max(1, _count_messages(content))]
        }]
    
    # Critical decisions from frontmatter
    critical_decisions = fm.get("critical_decisions", [])
    
    return {
        "summary": summary or "Untitled session",
        "narrative_arc": narrative_arc,
        "keywords": keywords,
        "topics": topics[:3],
        "issues": issues,
        "prs": prs,
        "critical_decisions": critical_decisions,
    }

def _count_messages(content: str) -> int:
    """Count ## User / ## Assistant blocks in markdown."""
    return len(re.findall(r'^## (?:User|Assistant)$', content, re.MULTILINE))

# ── LLM extraction ────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured metadata from agent conversations. "
    "Return only valid JSON, no markdown fences."
)

EXTRACTION_PROMPT = """Analyze this AI agent conversation. Return JSON only, no markdown:

{
  "narrative_arc": [
    {
      "phase": "Act N — Short Title",
      "topic": "one-line description of what was discussed",
      "decisions": ["any decisions made in this phase"],
      "message_range": [start_msg_number, end_msg_number]
    }
  ],
  "summary": "One paragraph synthesizing the narrative arc phases",
  "keywords": ["3-8", "specific", "technical", "terms"],
  "topics": ["1-3", "high-level", "categories"],
  "issues": ["repo#NNN"],
  "prs": ["repo#NNN"],
  "critical_decisions": [
    {"decision": "what was decided", "confidence": "high|medium|low", "phase": "Act N"}
  ]
}

Conversation:
---
"""


# Whitelist of known OpenAI models — llm_model flows from MCP tools / CLI and must
# not allow arbitrary model injection (could trigger expensive tiers).
# `ollama:MODEL` specs bypass this whitelist: they route to the LOCAL Ollama
# endpoint (no cost tier, content never leaves the machine) — mirroring how
# ingest.build_model() treats the ollama provider outside _PROVIDERS.
_ALLOWED_LLM_MODELS = {"gpt-5-mini", "gpt-4o-mini", "gpt-4o"}


def _parse_llm_json(text: str) -> dict | None:
    """Parse + type-validate LLM JSON output. Returns None on malformed output."""
    try:
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        text = text.strip()

        result = json.loads(text)
        if not isinstance(result, dict):
            return None
        # Validate types to prevent downstream crashes
        if not isinstance(result.get("narrative_arc"), list):
            result["narrative_arc"] = []
        if not isinstance(result.get("keywords"), list):
            result["keywords"] = []
        if not isinstance(result.get("issues"), list):
            result["issues"] = []
        if not isinstance(result.get("prs"), list):
            result["prs"] = []
        return result
    except Exception:
        return None


def extract_metadata_with_llm(content: str, model: str = "gpt-5-mini") -> dict | None:
    """Extract metadata using an LLM. Returns None on failure (caller should fall back).

    Model specs follow the `provider:model` convention from ingest.build_model:
      - ``ollama:MODEL`` → local Ollama endpoint (http://localhost:11434, no API
        key, PII-safe: content never leaves the machine)
      - otherwise → OpenAI whitelist (gpt-5-mini / gpt-4o-mini / gpt-4o)
    """
    # Ollama local mode — PII-sensitive extraction. Exempt from the OpenAI
    # whitelist because there is no external cost tier to protect against.
    if model.startswith("ollama:"):
        model_id = model.split(":", 1)[1]
        if not model_id:
            return {"error": f"llm_model {model!r} missing model part "
                             f"(expected ollama:MODEL)"}
        try:
            from .models import OllamaModel
            llm = OllamaModel(id=model_id, timeout=30)  # match OpenAI-path timeout
            text = llm.complete(
                system=EXTRACTION_SYSTEM_PROMPT,
                user=EXTRACTION_PROMPT + content[:16000],  # ~12K token limit
            )
            return _parse_llm_json(text)
        except Exception as e:
            print(f"[session_indexer] Ollama extraction failed: {e}", file=sys.stderr)
            return None

    if model not in _ALLOWED_LLM_MODELS:
        return {"error": f"llm_model {model!r} not allowed "
                         f"(whitelist: {sorted(_ALLOWED_LLM_MODELS)}; use ollama:MODEL for local)"}
    try:
        # Try OpenAI-compatible API
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            return None

        import requests
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": EXTRACTION_PROMPT + content[:16000]},  # ~12K token limit
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return _parse_llm_json(result["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"[session_indexer] LLM extraction failed: {e}", file=sys.stderr)
        return None


# ── Tiered extraction (main entry point) ──────────────────────────

def extract_metadata(content: str, llm_model: str | None = "gpt-5-mini") -> dict:
    """Extract metadata from session content.
    
    Strategy:
    1. If frontmatter has keywords and narrative_arc → use them (fast path)
    2. If LLM available → LLM extraction
    3. Fallback → keyword extraction from frontmatter
    
    Returns dict suitable for passing to create_event().
    """
    fm = _parse_frontmatter(content)
    
    # Tier 1: Rich frontmatter — skip LLM
    if fm.get("keywords") and fm.get("narrative_arc"):
        issues = fm.get("issues", [])
        if isinstance(issues, str):
            issues = [issues]
        prs = fm.get("prs", [])
        if isinstance(prs, str):
            prs = [prs]
        return {
            "summary": fm.get("title", ""),
            "narrative_arc": fm["narrative_arc"],
            "keywords": fm["keywords"] if isinstance(fm["keywords"], list) else [fm["keywords"]],
            "topics": fm.get("topics", []),
            "issues": issues,
            "prs": prs,
            "critical_decisions": fm.get("critical_decisions", []),
        }
    
    # Tier 2: LLM extraction
    if llm_model:
        result = extract_metadata_with_llm(content, llm_model)
        if result:
            # Validate
            if "keywords" in result and len(result.get("keywords", [])) > 0:
                return result
    
    # Tier 3: Keyword fallback
    return extract_keywords_from_frontmatter(content)


# ── Session embeddings (#244) ──────────────────────────────────────


def session_embedding_text(name: str, summary: str = "",
                           keywords: list[str] | None = None,
                           topics: list[str] | None = None) -> str:
    """Compose the embedding text for an AgentSession Event.

    name + summary + keywords + topics, de-duplicated (preserving order) and
    whitespace-joined. Shared by the indexers (session_indexer CLI,
    ingest_corpus) and graph-scripts/backfill_embeddings.py so the semantic
    surface is identical everywhere.
    """
    parts: list[str] = []
    for p in (name or "", summary or "", *(keywords or []), *(topics or [])):
        s = str(p).strip() if p else ""
        if s and s not in parts:
            parts.append(s)
    return " ".join(parts).strip()


def compute_session_embedding(name: str, summary: str = "",
                              keywords: list[str] | None = None,
                              topics: list[str] | None = None) -> list[float] | None:
    """Compute the 384-dim embedding for an AgentSession Event.

    Degrades gracefully to None when the embedding model is unavailable or the
    composed text is empty (mirrors tortoise.embeddings.compute_embedding —
    session indexing must never depend on embeddings). Callers store the result
    as vecf32 on the Event node.
    """
    text = session_embedding_text(name, summary, keywords, topics)
    if not text:
        return None
    try:
        from tortoise.embeddings import compute_embedding
        return compute_embedding(text)
    except Exception:
        return None


# ── File utilities ────────────────────────────────────────────────

def session_corpus_dir() -> Path:
    """Canonical session corpus directory (local indexer path).

    Default: ``~/.tortoise/docs/conversations/`` — the canonical store for
    the local indexer path (align decision, #280 items 2-3). Honors
    ``TORTOISE_SESSION_CORPUS`` for non-home setups / tests.
    """
    env = os.environ.get("TORTOISE_SESSION_CORPUS", "")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".tortoise" / "docs" / "conversations"


def compute_file_hash(file_path: str) -> str | None:
    """SHA256 of file contents — text-mode (universal newlines) normalized.

    MUST match ``ingest_corpus``'s file_hash derivation exactly (both hash
    ``read_text(encoding="utf-8").encode()``): a raw-bytes read would diverge
    on CRLF files, permanently classifying them as hash-stale in the health
    check / reconciliation sweep (non-convergent). Returns None on error.
    """
    try:
        with open(file_path, encoding="utf-8") as f:
            return hashlib.sha256(f.read().encode()).hexdigest()
    except Exception:
        return None


def extract_session_id(file_path: str) -> str | None:
    """Extract session ID from frontmatter or filename."""
    try:
        with open(file_path) as f:
            content = f.read()
    except Exception:
        return None
    
    fm = _parse_frontmatter(content)
    # Check each key INDEPENDENTLY: `or` would collapse falsy-but-coercible
    # scalars (0, 0.0, false). Coerce to str to match ingest (sdk.py coerces
    # sessionId before use) so health derives the SAME event_id as
    # ingest_corpus — otherwise the sweep never converges (review round 2 P2).
    def _coerce(v):
        return str(v) if v is not None else None

    # Mirror ingest's str-coerced `or`-collapse EXACTLY (review round 4 P2):
    # sessionId or session_id or file_<stem> — an empty-string sessionId is
    # falsy and must fall through to the alternate key, not straight to the
    # file stem. Otherwise health derives a DIFFERENT event_id than ingest
    # and the sweep never converges.
    sid = _coerce(fm.get("sessionId"))
    if not sid:
        sid = _coerce(fm.get("session_id"))
    if sid:
        return sid
    # empty-string/None both keys → file fallback (matches ingest's or-collapse)
    
    # Fallback: derive from filename
    stem = Path(file_path).stem
    return f"file_{stem}"


# ── CLI ────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Extract metadata from session files")
    parser.add_argument("path", help="Session file path or directory")
    parser.add_argument("--dir", action="store_true", help="Path is a directory")
    parser.add_argument("--batch", action="store_true", help="Batch process (with --dir)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM, use keyword-only")
    parser.add_argument("--model", default="gpt-5-mini",
                        help="LLM model spec (provider:model — e.g. ollama:llama3.2:3b for local PII-safe extraction)")
    parser.add_argument("--db", default=None, help="Tortoise DB URI (docker://host:port/graph). When set, indexes into FalkorDB instead of printing to stdout.")
    args = parser.parse_args()
    
    model = None if args.no_llm else args.model
    
    if args.dir:
        dir_path = Path(args.path).resolve()
        if not dir_path.is_dir():
            print(f"Error: {args.path} is not a directory", file=sys.stderr)
            sys.exit(1)
        
        if args.db:
            # Index via SDK
            import os as _os
            _os.environ['TORTOISE_DB_URI'] = args.db
            from tortoise.sdk import TortoiseSDK
            sdk = TortoiseSDK()
            llm = None if args.no_llm else args.model
            result = sdk.index_sessions(str(dir_path), extract_metadata=True, llm_model=llm)
            print(json.dumps(result, indent=2))
        else:
            files = sorted(dir_path.glob("*.md"))
            print(f"Processing {len(files)} files...")
            model = None if args.no_llm else args.model
            for i, f in enumerate(files):
                try:
                    content = f.read_text()
                    metadata = extract_metadata(content, model)
                    print(f"[{i+1}/{len(files)}] {f.name}: {len(metadata.get('keywords',[]))} keywords, "
                          f"{len(metadata.get('narrative_arc',[]))} phases")
                except Exception as e:
                    print(f"[{i+1}/{len(files)}] {f.name}: ERROR - {e}", file=sys.stderr)
    else:
        file_path = Path(args.path).resolve()
        if args.db:
            # Index single file via SDK
            import os as _os
            _os.environ['TORTOISE_DB_URI'] = args.db
            from tortoise.sdk import TortoiseSDK
            sdk = TortoiseSDK()
            llm = None if args.no_llm else args.model
            content = file_path.read_text()
            metadata = extract_metadata(content, llm)
            session_id = extract_session_id(str(file_path))
            # #280: per-session flock — the session-end hook is fire-and-forget;
            # a concurrent hook/sweep must not race this MATCH->SET read-modify-write.
            from .index_lock import SessionIndexLock
            _lock = SessionIndexLock(session_id or f"file_{file_path.stem}")
            try:
                _lock_status = _lock.acquire()
            except (OSError, AttributeError, ImportError) as _lock_err:
                # #280 review P2 (robustness): an unusable lock path (unwritable
                # lock dir / planted symlink) must not crash the CLI — surface it
                # as a retryable failure and exit 0 (never block the caller).
                print(json.dumps({"status": "error",
                                  "reason": f"session lock unavailable: {_lock_err}"}))
                return
            if _lock_status == "held":
                # ALWAYS exit 0 — never block session close; the holder wins.
                print(json.dumps({"status": "locked",
                                  "reason": f"session lock held: {_lock.detail}"}))
                return
            try:
                file_hash = compute_file_hash(str(file_path))
                event_id = f"session_{session_id}" if session_id else f"file_{file_path.stem}"
                proj = sdk._get_proj()
                exists = proj.g.query(
                    "MATCH (e:Event {eventId: $eid}) RETURN properties(e)",
                    params={"eid": event_id}
                ).result_set
                if exists and exists[0][0].get("file_hash") == file_hash:
                    print(json.dumps({"status": "skipped", "reason": "unchanged"}))
                else:
                    # #244: compute the session embedding (name + summary + keywords
                    # + topics) and store as vecf32 — degrades to None when the
                    # model is unavailable (session indexing never depends on it).
                    embedding = compute_session_embedding(
                        metadata.get("summary", file_path.stem),
                        metadata.get("summary", ""),
                        metadata.get("keywords", []),
                        metadata.get("topics", []),
                    )
                    props = {
                        "name": metadata.get("summary", file_path.stem),
                        "eventKind": "AgentSession",
                        "session_id": session_id or f"file_{file_path.stem}",
                        "agent": "pi",
                        "source_file": str(file_path),
                        "file_hash": file_hash,
                        "keywords": metadata.get("keywords", []),
                        "topics": metadata.get("topics", []),
                        "message_count": _count_messages(content),
                        "content_metadata": json.dumps({
                            "schema_version": 1,
                            "summary": metadata.get("summary", ""),
                            "narrative_arc": metadata.get("narrative_arc", []),
                            "issues": metadata.get("issues", []),
                            "prs": metadata.get("prs", []),
                            "critical_decisions": metadata.get("critical_decisions", []),
                        }),
                        "eventStatus": "completed",
                        "classificationLevel": "internal",
                    }
                    if exists:
                        proj.g.query(
                            "MATCH (e:Event {eventId: $eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) ELSE e.embedding END",
                            params={"eid": event_id, "props": props, "embedding": embedding}
                        )
                        print(json.dumps({"status": "updated", "eventId": event_id}))
                    else:
                        props["startedAt"] = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
                        proj.g.query(
                            "CREATE (e:Event {eventId: $eid}) SET e += $props, "
                            "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) END",
                            params={"eid": event_id, "props": props, "embedding": embedding}
                        )
                        print(json.dumps({"status": "ingested", "eventId": event_id}))
                    # Wire INSTANTIATES edges to issue/PR Objects (parity with ingest_corpus)
                    try:
                        from tortoise.sdk import TortoiseSDK
                        _sdk = TortoiseSDK()
                        _sdk._connect_issue_objects(event_id, metadata)
                    except Exception:
                        pass  # non-fatal edge wiring
            finally:
                _lock.release()
        else:
            content = file_path.read_text()
            metadata = extract_metadata(content, args.model if not args.no_llm else None)
            print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
