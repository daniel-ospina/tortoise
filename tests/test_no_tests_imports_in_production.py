"""Static guard: production ``tortoise/`` modules must never import ``tests.*``.

Regression pin for issue 1468 (session capture 500): ``tortoise/sdk.py``
(``_extract_session_v2``) did ``from tests.model_adapters import MODELS`` —
a TEST-ONLY module that does not exist in the production image (Fly /app) and
is not importable from the hosted server subprocess. Every
``POST /v1/sessions`` capture raised ``ModuleNotFoundError`` → HTTP 500.

Pure static text assertions over source files (no imports, no network, no
graph), mirroring the ``tests/test_cross_subdomain_cookie_sync.py`` pattern —
so the guard itself cannot be broken by packaging or import-order changes.
Anchoring to the line start keeps docstring/comment mentions (e.g.
``extractor_v2.py``'s "temperature 0.0 (via tests.model_adapters MODELS)")
from false-matching.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TORTOISE_DIR = REPO_ROOT / "tortoise"

# Anchored at line start so prose can't false-match. Covers both forms:
#   from tests.model_adapters import MODELS      (sdk.py:1926 — the #1468 bug)
#   import tests.model_adapters
_TESTS_IMPORT = re.compile(r"^\s*(?:from\s+tests\b|import\s+tests\b)")


def _production_modules() -> list[Path]:
    return sorted(TORTOISE_DIR.rglob("*.py"))


def test_no_tortoise_module_imports_tests() -> None:
    """Every tortoise/**/*.py must be free of `from tests` / `import tests`
    statements — production code cannot depend on the test-only package."""
    offenders: list[str] = []
    for path in _production_modules():
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if _TESTS_IMPORT.search(line):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()}")
    assert not offenders, (
        "production tortoise/ modules must not import the test-only `tests` "
        "package (tests/ is absent from the production image):\n"
        + "\n".join(offenders)
    )
