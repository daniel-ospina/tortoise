"""Document kind classification from file path heuristics.

Used by extraction_pipeline.build_frontmatter() to populate the `type` field
in auto-generated frontmatter. Works on arbitrary repo paths, not just
Tortoise's own doc store.

ponytail: regex-based heuristics — no ML, no config, no dependencies.
Add new patterns when a path that should be classified isn't.
"""

from pathlib import Path


# (pattern, kind) — first match wins, ordered most-specific to least-specific.
# Specific patterns must come BEFORE the docs?/ catch-all so docs/epics/foo.md
# matches epic, not documentation.
_RULES: list[tuple[str, str]] = [
    # Exact filenames (highest priority)
    (r'(^|/)README\.md$', 'readme'),
    (r'(^|/)CHANGELOG(\.md)?$', 'changelog'),
    (r'(^|/)CONTRIBUTING(\.md)?$', 'guide'),
    (r'(^|/)LICENSE(\.md)?$', 'legal'),
    (r'(^|/)SECURITY(\.md)?$', 'security'),
    (r'(^|/)CODE_OF_CONDUCT(\.md)?$', 'policy'),

    # Skills
    (r'(^|/)skills?/.*SKILL\.md$', 'skill'),
    (r'(^|/)SKILL\.md$', 'skill'),

    # Index files (before docs/ catch-all so docs/00_index.md → index)
    (r'(?i)(^|/).*index\.md$', 'index'),

    # Domain-specific patterns (before docs/ catch-all)
    (r'(^|/)epics?/', 'epic'),
    (r'(^|/)research/', 'research'),
    (r'(^|/)specs?/', 'spec'),
    (r'(^|/).*\.spec\.md$', 'spec'),
    (r'(^|/)adrs?/', 'adr'),
    (r'(^|/)decisions/', 'decision'),
    (r'(^|/)postmortems?/', 'postmortem'),
    (r'(^|/)runbooks?/', 'runbook'),
    (r'(^|/)incidents?/', 'incident'),
    (r'(^|/)meetings?/', 'meeting'),
    (r'(^|/)transcripts?/', 'transcript'),
    (r'(^|/)conversations?/', 'conversation'),

    # Plans (fuzzy — after more specific patterns)
    (r'(^|/)\d{2}-plan\.md$', 'plan'),
    (r'(^|/).*plan.*\.md$', 'plan'),

    # Documentation (catch-all for docs/ paths — must be after specific patterns)
    (r'(^|/)docs?/', 'documentation'),

    # Default
    (r'\.md$', 'page'),
]


def classify(filepath: str | Path) -> str:
    """Classify a document's kind from its file path.

    Returns the first matching documentKind string, or 'page' for
    unrecognized markdown files.
    """
    path_str = str(filepath)
    import re
    for pattern, kind in _RULES:
        if re.search(pattern, path_str):
            return kind
    return 'page'
