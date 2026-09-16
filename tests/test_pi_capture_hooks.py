"""#3575 — the in-repo Pi capture extension (`tortoise/pi-hooks/`).

The extension is the artifact `HARNESS_INSTALL.pi` installs. These tests pin
the shipped artifact itself: it is self-contained (no `agent-infra`
dependency), it carries no `autoCapture`-style default-false flag (installing
it IS the opt-in — the #3575 trap was a capture extension that defaulted off),
and it talks to both hosted capture endpoints.

The behavioral assertions run the extension's own `node --test` suite when the
local Node supports native TypeScript type stripping (Node >= 22.6); the
source-level assertions always run, so the surface stays pinned even where
Node is older or absent.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
EXTENSION = HOOKS / "tortoise-capture.ts"
EXTENSION_TEST = HOOKS / "tortoise-capture.test.ts"


def _src() -> str:
    return EXTENSION.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Drop `//` comments so prose that NAMES a forbidden pattern (e.g. "no
    autoCapture flag") is not mistaken for the pattern itself. Preserves the
    `://` in URLs."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        idx = line.find("//")
        if idx != -1 and (idx == 0 or line[idx - 1] != ":"):
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def test_extension_artifact_is_committed():
    assert EXTENSION.is_file(), f"missing capture extension: {EXTENSION}"
    # A stub would satisfy exists(); the shipped seam is a real implementation.
    assert len(_src()) > 2000, "extension is suspiciously small — is it a stub?"
    assert EXTENSION_TEST.is_file(), f"missing behavioral test: {EXTENSION_TEST}"


def test_extension_has_no_agent_infra_dependency():
    """agent-infra is not shipped to users — the seam must stand alone."""
    code = _code_only(_src())
    assert "agent-infra" not in code, "extension must not depend on agent-infra"
    assert "capture-attribution" not in code, (
        "extension must not import agent-infra shared modules"
    )
    # it must not reach into an agent-infra path at runtime either
    assert not re.search(r"['\"][^'\"]*agent-infra", code)


def test_capture_is_not_gated_behind_a_default_false_flag():
    """The false-PASS trap: an extension that installs but defaults capture
    OFF. Installing the extension must be the opt-in."""
    assert "autoCapture" not in _code_only(_src()), (
        "no autoCapture flag — installing the extension is the opt-in"
    )


def test_extension_posts_both_capture_endpoints():
    src = _src()
    # install-probe on load (server-visible install signal) …
    assert '"/v1/sessions/install-probe"' in src
    # … and session filing on shutdown, tagged harness=pi (the receipt key).
    assert '"/v1/sessions"' in src
    assert re.search(r'HARNESS\s*=\s*"pi"', src), "harness constant must be 'pi'"
    # the probe payload must be harness-only — zero conversation content
    assert "postInstallProbe" in src


def _node_supports_ts(node: str) -> bool:
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"v(\d+)\.(\d+)", out)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return major > 22 or (major == 22 and minor >= 6)


def test_extension_behavioral_suite():
    """Run `node --test tortoise/pi-hooks/tortoise-capture.test.ts` (probe
    payload, turn extraction, capture payload, reload skip). Skipped only when
    the local Node cannot run TypeScript — the source pins above still run."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — extension source pins above still ran")
    if not _node_supports_ts(node):
        pytest.skip("node < 22.6 cannot strip TypeScript types — source pins still ran")
    proc = subprocess.run(
        [node, "--test", str(EXTENSION_TEST)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
