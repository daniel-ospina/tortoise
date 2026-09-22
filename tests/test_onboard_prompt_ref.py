"""Coverage for the self-hosted onboarding prompt reference (#544)."""
from __future__ import annotations

import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_onboard_completion_prints_prompt_url(capsys):
    """`tortoise onboard` must print the canonical onboarding prompt URL after
    completion so self-hosted users can paste it into their agent (#544)."""
    from tortoise import __main__ as m

    class _Args:
        path = None
        cmd = "onboard"

    with mock.patch.object(m, "_cmd_init", return_value=0), \
         mock.patch.object(m, "_cmd_demo", return_value=0), \
         mock.patch.object(m, "_cmd_doctor", return_value=0), \
         mock.patch("subprocess.run") as fake_run, \
         mock.patch("tortoise.__main__._cmd_index_github", return_value=0):
        fake_run.return_value.returncode = 1  # not a git repo → skip index
        rc = m._cmd_onboard(_Args())

    assert rc == 0
    captured = capsys.readouterr()
    assert "Onboarding complete." in captured.out
    assert "https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md" in captured.out


@pytest.mark.embedded_only  # Epic #1647: tests the EMBEDDED install guidance (falkordblite) — under a URI the CLI correctly prefers URI mode and the embedded guidance is unreachable (D14-class)
def test_init_missing_falkordblite_prints_install_guidance(capsys):
    """`tortoise init` with no embedded backend must print actionable install
    guidance naming falkordblite (anti-regression: #450 fixed it, #442's
    automated fixer reverted it, #706 re-fixed — issue #716).

    Simulates the missing-dep environment: falkordb unimportable (Docker
    branch passes) and FalkorProjection raising ImportError (falkordblite
    absent). The guidance must be reachable — not shadowed by an import-time
    crash in tortoise/__init__.py — and must name the real package.
    """
    from tortoise import __main__ as m

    class _Args:
        path = None
        api_key = None

    with mock.patch.dict(sys.modules, {"falkordb": None}), \
         mock.patch("tortoise.projection.FalkorProjection",
                    side_effect=ImportError("falkordblite missing")):
        rc = m._cmd_init(_Args())

    assert rc == 1
    out = capsys.readouterr().out
    assert "falkordblite" in out
    assert "pip install falkordb" in out
    assert "pip install falkordblite" in out
    assert "pip install redislite" not in out  # stale guidance must not regress
