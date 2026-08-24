# tests/test_helpers.py — epic #1647 cycle-4 P1-4
"""Tiny test_-prefixed shared helper pinning _caller_test_stem()'s NEAREST-frame
semantics (cycle-3 P2-18): a construction made through THIS module resolves to
"test_helpers" — the helper's own stem — never the calling file's. A shared
test_-prefixed helper must therefore NEVER be listed in TEST_NO_REDIRECT_STEMS
(its exemption would exempt every caller), and no carve-out file may import it
(a carve-out constructing through it would lose its own stem's exemption). The
P2-9 stem-registry guard (Task 5 Step 1) enforces both."""
from tortoise.projection import _caller_test_stem


def construct_via_helper() -> str:
    return _caller_test_stem()
