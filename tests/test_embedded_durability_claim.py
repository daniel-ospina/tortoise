"""Drift pin for #2879 — the documented embedded durability default must equal
the code's default.

README advertised ``AOF-durable to <=1s since #915`` while
``TORTOISE_EMBEDDED_AOF`` (the flag that actually enables AOF) defaults OFF
and appeared in no ``.env.example``, no ops doc and no CLI flag. A documented
durability guarantee the code does not keep is the same defect class as a gate
that reports success without running: a user believing the claim has a real
data-loss window they do not know about.

The fix corrected the claim and documented the flag. This test pins the
*behaviour* against the *claim*, so the two cannot silently diverge again:

1. behaviour — with the flag unset no AOF artifact is written; with it set one
   is (a real on-disk measurement, not a code reading);
2. docs — the README documents the flag as opt-in and no longer states the
   unconditional <=1s guarantee;
3. discoverability — ``.env.example`` declares the flag.
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _aof_artifact_written(tmp: Path, *, enabled: bool, monkeypatch) -> bool:
    """Construct an embedded graph at ``tmp`` and report whether an AOF
    artifact (``<db>-appendonlydir``) was materialised."""
    if enabled:
        monkeypatch.setenv("TORTOISE_EMBEDDED_AOF", "1")
    else:
        monkeypatch.delenv("TORTOISE_EMBEDDED_AOF", raising=False)

    from tests._embedded import fresh_embedded_proj

    # Function-scoped seam (#3769): a FRESH server per call, so the
    # construction-time flag above is honoured per case. A session-scoped
    # server would freeze it at the first case and this helper would then
    # report the same verdict for `default-off` and `opt-in-on`.
    with fresh_embedded_proj(tmp) as proj:
        # The `*-appendonlydir` is a CONSTRUCTION-time artifact: redis-server
        # starts with `appendonly yes` when the flag is set, so the dir exists
        # BEFORE this write. The write makes journal CONTENT observable — it is
        # not what produces the artifact (measured, #3769 review P2-2).
        proj._upsert({"id": "durability-probe", "content": "x", "context": "y"})
    return any(p.name.endswith("-appendonlydir") for p in tmp.iterdir())


@pytest.mark.parametrize(
    "enabled,expect_aof", [(False, False), (True, True)], ids=["default-off", "opt-in-on"]
)
def test_embedded_aof_default_matches_documented_optin(tmp_path, monkeypatch, enabled, expect_aof):
    """#2879 behaviour: AOF is OFF by default and ON only when opted in."""
    assert _aof_artifact_written(tmp_path, enabled=enabled, monkeypatch=monkeypatch) is expect_aof


def test_readme_no_longer_claims_unconditional_aof_durability():
    """#2879 docs: the old unconditional claim must not return."""
    readme = (ROOT / "README.md").read_text()
    assert "AOF-durable to \u22641s" not in readme, (
        "README again claims unconditional AOF durability while "
        "TORTOISE_EMBEDDED_AOF defaults off (#2879)"
    )
    # The corrected row must name the flag and that it is opt-in.
    row = next(
        (ln for ln in readme.splitlines() if ln.startswith("| `TORTOISE_DB_PATH`")), ""
    )
    assert row, "README lost the TORTOISE_DB_PATH self-host row"
    assert "TORTOISE_EMBEDDED_AOF" in row and "opt-in" in row.lower(), (
        f"self-host row must document AOF as opt-in via TORTOISE_EMBEDDED_AOF: {row!r}"
    )


def test_env_example_declares_the_embedded_aof_flag():
    """#2879 discoverability: the flag must be visible to self-hosters."""
    env = (ROOT / ".env.example").read_text()
    assert "TORTOISE_EMBEDDED_AOF" in env, (
        "TORTOISE_EMBEDDED_AOF is the embedded durability switch but is not "
        "declared in .env.example (#2879)"
    )
