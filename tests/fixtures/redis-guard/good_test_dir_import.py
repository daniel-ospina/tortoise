"""Fixture: this pattern (test-dir direct redislite import) is ALLOWED when
the file lives under tests/ (allowlisted) — proven by scanning real test
files. Run directly on this fixture path, the hook REJECTS it (fixtures are
checked); the allowlist applies to real tests/ paths."""
from redislite.falkordb_client import FalkorDB  # noqa: F401
