"""Coverage for the self-hosted onboarding prompt reference (#544)."""
from __future__ import annotations

import os
import sys
from unittest import mock

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
    assert "https://premiselabs.co/onboarding-prompt.md" in captured.out
