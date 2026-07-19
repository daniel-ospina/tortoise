"""Tests for agent_provisioning — 4-layer capability model + failure recovery."""
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.agent_provisioning import (
    Capabilities,
    CircuitBreaker,
    CircuitBreakerError,
    CircuitState,
    Role,
    _extract_frontmatter,
    dead_letter_drain,
    dead_letter_log,
    inject_context,
    load_role,
    main,
    validate_all_manifests,
    validate_role,
)


# ── Manifest ──────────────────────────────────────────────────────────────

VALID_MANIFEST = """---
team: test-team
role: tester
capabilities:
  tools:
    - read
    - bash
  mcp:
    - supabase
  skills:
    - issue-scoping
  memory_filter:
    team_id: test-team
    types:
      - procedural
deny:
  - write
---
"""


def test_extract_frontmatter_valid():
    fm = _extract_frontmatter(VALID_MANIFEST)
    assert fm is not None
    assert fm["team"] == "test-team"
    assert fm["role"] == "tester"
    assert fm["capabilities"]["tools"] == ["read", "bash"]


def test_extract_frontmatter_none():
    assert _extract_frontmatter("no frontmatter here") is None


def test_extract_frontmatter_dashes_not_delimiter():
    """Lines like ---- or ---content are NOT YAML delimiters."""
    assert _extract_frontmatter("---content\nkey: val\n---\n") is None
    assert _extract_frontmatter("----\nkey: val\n----\n") is None


def test_extract_frontmatter_non_dict_yaml():
    """List/int YAML frontmatter is not a valid role manifest."""
    assert _extract_frontmatter("---\n- item1\n- item2\n---\n") is None
    assert _extract_frontmatter("---\n42\n---\n") is None


def test_load_role_from_file():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(VALID_MANIFEST)
        f.flush()
        role = load_role(f.name)
    Path(f.name).unlink()
    assert role.team == "test-team"
    assert role.role == "tester"
    assert role.capabilities.tools == ["read", "bash"]
    assert role.capabilities.mcp == ["supabase"]
    assert role.deny == ["write"]


def test_load_role_missing_frontmatter():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("# Just a heading\n\nNo frontmatter.")
        f.flush()
        with pytest.raises(ValueError, match="YAML frontmatter"):
            load_role(f.name)
    Path(f.name).unlink()


# ── Validation ────────────────────────────────────────────────────────────


def test_validate_role_valid():
    role = Role(team="t", role="r", capabilities=Capabilities(tools=["read"]))
    assert validate_role(role) == []


def test_validate_role_missing_team():
    role = Role(team="", role="r")
    errors = validate_role(role)
    assert any("team" in e for e in errors)


def test_validate_role_deny_conflict():
    role = Role(team="t", role="r", capabilities=Capabilities(tools=["write"]), deny=["write"])
    errors = validate_role(role)
    assert any("write" in e for e in errors)


def test_validate_role_bad_types():
    role = Role(team="t", role="r", capabilities=Capabilities(tools="not-a-list"))  # type: ignore
    errors = validate_role(role)
    assert any("tools" in e for e in errors)


def test_validate_role_non_list_skips_deny_check():
    """When capabilities.tools is not a list, deny check should not iterate characters."""
    role = Role(team="t", role="r", capabilities=Capabilities(tools="write"), deny=["write"])  # type: ignore
    errors = validate_role(role)
    # Should have "must be a list" error, but NOT "in both capabilities.tools and deny"
    # because we skip the deny check for non-list capabilities
    assert any("must be a list" in e for e in errors)
    assert not any("in both capabilities.tools and deny" in e for e in errors)


# ── Context injection ─────────────────────────────────────────────────────


def test_inject_context():
    caps = Capabilities(
        tools=["read", "bash"],
        mcp=["supabase"],
        skills=["issue-scoping"],
        memory_filter={"team_id": "t", "types": ["procedural"]},
    )
    role = Role(team="t", role="r", capabilities=caps, deny=["write"])
    ctx = inject_context(role)
    assert ctx["role"] == "t/r"
    assert ctx["allowlist"]["tools"] == ["read", "bash"]
    assert ctx["allowlist"]["mcp_servers"] == ["supabase"]
    assert ctx["denylist"] == ["write"]
    assert ctx["memory_scope"]["team_id"] == "t"


# ── Circuit breaker ───────────────────────────────────────────────────────


def test_circuit_breaker_closed_on_success():
    cb = CircuitBreaker(failure_threshold=2, timeout=60)
    result = cb.call(lambda x: x * 2, 21)
    assert result == 42
    assert cb.state == CircuitState.CLOSED
    assert cb.failure_count == 0


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(failure_threshold=2, timeout=60)

    def fail():
        raise RuntimeError("boom")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            cb.call(fail)

    assert cb.state == CircuitState.OPEN
    with pytest.raises(CircuitBreakerError):
        cb.call(lambda: 1)


def test_circuit_breaker_half_open_reset():
    cb = CircuitBreaker(failure_threshold=1, timeout=0.01)
    with pytest.raises(ZeroDivisionError):
        cb.call(lambda: 1 / 0)
    assert cb.state == CircuitState.OPEN

    import time
    time.sleep(0.02)

    # First call in HALF_OPEN should succeed → CLOSED
    result = cb.call(lambda x: x, 7)
    assert result == 7
    assert cb.state == CircuitState.CLOSED


# ── Dead letter ───────────────────────────────────────────────────────────


def test_dead_letter():
    dead_letter_log("test_op", "something broke", retry=3)
    entries = dead_letter_drain()
    assert len(entries) == 1
    assert entries[0]["operation"] == "test_op"
    assert entries[0]["error"] == "something broke"
    assert entries[0]["ctx"]["retry"] == 3

    # Drain clears
    assert dead_letter_drain() == []


# ── CI CLI ────────────────────────────────────────────────────────────────


def test_validate_all_manifests_clean():
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "role.md"
        md.write_text(VALID_MANIFEST)
        count, msgs = validate_all_manifests(tmp)
        assert count == 0
        assert msgs == []


def test_validate_all_manifests_errors():
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "bad.md"
        md.write_text("---\nteam: ''\nrole: ''\n---\n")
        count, msgs = validate_all_manifests(tmp)
        assert count > 0


def test_validate_all_manifests_missing_dir():
    count, msgs = validate_all_manifests("/nonexistent/dir")
    assert count == 1


def test_main_validate():
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "role.md"
        md.write_text(VALID_MANIFEST)
        rc = main(["--validate", tmp])
        assert rc == 0


# ── Retry + Timeout ─────────────────────────────────────────────────────


def test_retry_call_succeeds():
    from tortoise.agent_provisioning import retry_call
    result = retry_call(lambda x: x * 2, 21, max_retries=3, timeout=5, operation="test")
    assert result == 42


def test_retry_call_retries_and_exhausts():
    from tortoise.agent_provisioning import retry_call
    calls = []

    def flaky():
        calls.append(1)
        raise RuntimeError("fail")

    with pytest.raises(RuntimeError):
        retry_call(flaky, max_retries=3, timeout=5, operation="test_flaky")
    assert len(calls) == 3
    entries = dead_letter_drain()
    assert len(entries) == 1
    assert entries[0]["operation"] == "test_flaky"


def test_retry_call_circuit_breaker_fails_fast():
    from tortoise.agent_provisioning import retry_call
    cb = CircuitBreaker(failure_threshold=1, timeout=60)
    with pytest.raises(ZeroDivisionError):
        retry_call(lambda: 1 / 0, breaker=cb, max_retries=1, timeout=5, operation="test")
    with pytest.raises(CircuitBreakerError):
        retry_call(lambda: 42, breaker=cb, max_retries=1, timeout=5, operation="test")


def test_main_validate_errors():
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "bad.md"
        md.write_text("---\nteam: ''\n---\n")
        rc = main(["--validate", tmp])
        assert rc == 1
