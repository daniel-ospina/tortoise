"""lookup_hash — instant key-lookup digest for the Supabase control plane
(#669 plan P1-1, issue #770 Task 2).

lookup_hash := SHA-256(pepper + key), lowercase hex. The same vectors are
locked on the TS side in supabase/tests/lookup_parity.test.mjs (which ALSO
cross-checks the live Python implementation against the live TS mirror via
`node`) — any construction drift between the two languages fails there.

NOTE — construction order is "pepper FIRST, then key": the plan spells the
scheme "SHA-256(pepper + key)" and Task 3 (#767) resolves keys against these
hashes. Do not reorder without updating the TS mirror + parity vectors.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise.auth import hash_api_key, lookup_hash

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Authoritative vectors — independently computed with hashlib.sha256
# (identical copies in supabase/tests/lookup_parity.test.mjs).
VECTORS = [
    ("tt_test_key_vector_1", "test-pepper-vector-1",
     "6425fad49ef4d3200dedf1fcb29bbb82cfa09a3c269ff4ed7efb5cbc911bdb23"),
    ("tt_", "",
     "da3be08582a6cc5a51661a4be5483393d777f8e48858fdde0d750e5ac0c62468"),
    ("tt_abcdef0123456789abcdef0123456789", "another-pepper",
     "8d75c03bd1c61b114afc8371d2de8cc896bd1bd0da50581046b3a27663c658c0"),
]


def test_lookup_hash_matches_authoritative_vectors(monkeypatch):
    """The Python helper must produce the exact SHA-256(pepper + key) hex."""
    monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-pepper-vector-1")
    mod = sys.modules["tortoise.auth"]
    mod._PEPPER_BYTES = os.environ["TORTOISE_SECRET_PEPPER"].encode()
    try:
        for key, pepper, expected in VECTORS:
            monkeypatch.setenv("TORTOISE_SECRET_PEPPER", pepper)
            mod._PEPPER_BYTES = pepper.encode()
            assert lookup_hash(key) == expected, f"vector mismatch for {key!r}"
    finally:
        monkeypatch.setenv("TORTOISE_SECRET_PEPPER", "test-static-pepper")
        mod._PEPPER_BYTES = b"test-static-pepper"


def test_lookup_hash_format_and_determinism():
    """64 lowercase hex chars; deterministic for the same key."""
    h1 = lookup_hash("tt_deterministic_1")
    h2 = lookup_hash("tt_deterministic_1")
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)


def test_lookup_hash_pepper_sensitive():
    """A different pepper must yield a different digest (pepper is the secret)."""
    a = lookup_hash("tt_pepper_sensitive")
    assert a != hashlib.sha256(b"wrong-pepper" + b"tt_pepper_sensitive").hexdigest()


def test_lookup_hash_is_instant_lookup_not_salted_pbkdf2():
    """lookup_hash is NOT hash_api_key: deterministic digest (no salt), while
    the salted PBKDF2 key_hash keeps verification-only semantics. Both are
    stored on provisioned rows (0009/0010) — lookup_hash for index lookups,
    key_hash for continuity."""
    h = lookup_hash("tt_lookup_vs_salted")
    k = hash_api_key("tt_lookup_vs_salted")
    assert h != k
    # salted PBKDF2 embeds a per-key salt (salt_hex:hash_hex)
    assert ":" in k and ":" not in h
    # hash_api_key is salted → two runs differ; lookup_hash is deterministic
    assert hash_api_key("tt_lookup_vs_salted") != k


def test_lookup_hash_parity_with_ts_mirror():
    """The TS mirror (supabase/functions/_shared/lookup.ts) must produce the
    identical digests — run the node parity test (skips if node is absent)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — TS↔Python parity test skipped")  # noqa: F821
    parity = _REPO_ROOT / "supabase" / "tests" / "lookup_parity.test.mjs"
    result = subprocess.run(
        [node, str(parity)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"TS↔Python parity test failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "TS↔Python parity confirmed" in result.stdout
