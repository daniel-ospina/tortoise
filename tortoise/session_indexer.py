"""Session indexer — metadata extraction for agent session files.

Extracts narrative_arc, summary, keywords, topics, issues, PRs, and
critical_decisions from session markdown files via LLM or keyword fallback.

Usage:
    python -m tortoise.session_indexer <file_path>
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

def _parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter from markdown content."""
    if not content.startswith("---"):
        return {}
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        import yaml
        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}

# ── Keyword extraction (fallback, no LLM) ─────────────────────────

def extract_keywords_from_frontmatter(content: str) -> dict:
    """Fallback: extract metadata from YAML frontmatter + basic heuristics.
    
    Returns dict with keys: summary, narrative_arc, keywords, topics, issues, prs.
    """
    fm = _parse_frontmatter(content)
    
    # Summary from title or first substantive line
    summary = fm.get("title", "")
    if not summary:
        # Try first non-empty, non-header, non-frontmatter line
        body = content.split("---", 2)[-1] if "---" in content else content
        for line in body.split("\n"):
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and len(stripped) > 10:
                summary = stripped[:200]
                break
    
    # Keywords from frontmatter tags + basic extraction
    keywords = fm.get("tags", [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(",")]
    
    # Add keywords from common patterns in content
    content_lower = content.lower()
    tech_terms = {
        "docker", "falkordb", "tortoise", "redis", "port", "migration",
        "graph", "api", "mcp", "sdk", "extension", "symlink", "python",
        "typescript", "github", "issue", "pr", "commit", "deploy",
        "config", "volume", "container", "database", "index", "search",
        "test", "e2e", "typecheck", "pipeline", "workflow"
    }
    found_terms = {t for t in tech_terms if t in content_lower}
    keywords = list(set(keywords) | found_terms)[:8]
    
    # Topics from frontmatter domain or inference
    topics = []
    domain = fm.get("domain", "")
    if domain:
        topics.append(domain)
    topic_map = {
        "docker": "infrastructure", "falkordb": "data",
        "port": "infrastructure", "volume": "infrastructure",
        "tortoise": "data", "api": "engineering", "mcp": "engineering",
        "extension": "engineering", "test": "engineering",
        "issue": "engineering", "pr": "engineering"
    }
    for kw in keywords:
        if kw in topic_map and topic_map[kw] not in topics:
            topics.append(topic_map[kw])
    
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
            "message_range": [1, _count_messages(content)]
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


def extract_metadata_with_llm(content: str, model: str = "gpt-5-mini") -> dict | None:
    """Extract metadata using an LLM. Returns None on failure (caller should fall back)."""
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
                    {"role": "system", "content": "You extract structured metadata from agent conversations. Return only valid JSON, no markdown fences."},
                    {"role": "user", "content": EXTRACTION_PROMPT + content[:16000]},  # ~12K token limit
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        text = result["choices"][0]["message"]["content"]
        
        # Strip markdown fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
        text = text.strip()
        
        return json.loads(text)
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
        return {
            "summary": fm.get("title", ""),
            "narrative_arc": fm["narrative_arc"],
            "keywords": fm["keywords"] if isinstance(fm["keywords"], list) else [fm["keywords"]],
            "topics": fm.get("topics", []),
            "issues": fm.get("issues", []),
            "prs": fm.get("prs", []),
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


# ── File utilities ────────────────────────────────────────────────

def compute_file_hash(file_path: str) -> str:
    """SHA256 hash of file contents."""
    with open(file_path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def extract_session_id(file_path: str) -> str | None:
    """Extract session ID from frontmatter or filename."""
    try:
        with open(file_path) as f:
            content = f.read()
    except Exception:
        return None
    
    fm = _parse_frontmatter(content)
    sid = fm.get("sessionId") or fm.get("session_id")
    if sid:
        return sid
    
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
    parser.add_argument("--model", default="gpt-5-mini", help="LLM model name")
    args = parser.parse_args()
    
    model = None if args.no_llm else args.model
    
    if args.dir:
        dir_path = Path(args.path)
        if not dir_path.is_dir():
            print(f"Error: {args.path} is not a directory", file=sys.stderr)
            sys.exit(1)
        
        files = sorted(dir_path.glob("*.md"))
        print(f"Processing {len(files)} files...")
        
        for i, f in enumerate(files):
            try:
                content = f.read_text()
                metadata = extract_metadata(content, model)
                print(f"[{i+1}/{len(files)}] {f.name}: {len(metadata.get('keywords',[]))} keywords, "
                      f"{len(metadata.get('narrative_arc',[]))} phases")
            except Exception as e:
                print(f"[{i+1}/{len(files)}] {f.name}: ERROR - {e}", file=sys.stderr)
    else:
        file_path = args.path
        content = Path(file_path).read_text()
        metadata = extract_metadata(content, model)
        print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
