"""`tortoise signup --claim` — dashboard claim instructions (#1082, PR1).

Mint is unchanged (POST /v1/agent/signup, global ~/.tortoise/credentials.json
written since #1708); --claim additionally prints the dashboard URL + paste-key
instructions so the one-time human act (sign in with GitHub/Google, paste the
key) is discoverable.
"""
from __future__ import annotations

import io
import json
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.error import HTTPError

import pytest

from tortoise.__main__ import main


@pytest.fixture(autouse=True)
def _home_isolated(monkeypatch, tmp_path):
    """#1708 D9: never read the developer's real ~/.tortoise credentials, and
    never resolve a stray ./.tortoise file in the pytest CWD."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("TORTOISE_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)

SIGNUP_BODY = json.dumps({
    "key": "tt_claimcli_0000000000000000000000000000000000000000",
    "team_id": "team-claimcli-1",
    "team_name": "agent-claimcli",
    "graph_name": "team_team-claimcli-1",
    "identity": "anon-claimcli",
    "tier": "free",
}).encode()


def _ok_response(body: bytes) -> mock.MagicMock:
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class TestSignupClaim:
    def test_signup_claim_prints_dashboard_instructions(self, monkeypatch,
                                                        tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response(SIGNUP_BODY)) as urlopen:
            rc = main(["signup", "--claim"])
        assert rc == 0
        out = capsys.readouterr().out
        # the key is still printed (shown once at mint)
        assert "tt_claimcli_" in out
        # the claim instructions name the dashboard + the sign-in providers
        assert "Claim your team" in out
        assert "app.premiselabs.co" in out
        assert "GitHub or Google" in out
        assert "Paste this key" in out
        # config still written so the key works immediately
        cfg = json.loads((tmp_path / ".tortoise").read_text())
        assert cfg["api_key"].startswith("tt_")
        assert urlopen.call_args.args[0].full_url.endswith("/v1/agent/signup")

    def test_signup_without_claim_has_no_claim_blurb(self, monkeypatch,
                                                     tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response(SIGNUP_BODY)):
            rc = main(["signup"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Claim your team" not in out
        assert "app.premiselabs.co" not in out

    def test_signup_claim_dashboard_url_env(self, monkeypatch, tmp_path,
                                            capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("TORTOISE_DASHBOARD_URL", "https://dash.example.co")
        with mock.patch("urllib.request.urlopen",
                        return_value=_ok_response(SIGNUP_BODY)):
            rc = main(["signup", "--claim"])
        assert rc == 0
        assert "dash.example.co" in capsys.readouterr().out

    def test_signup_claim_failure_exits_1(self, monkeypatch, tmp_path,
                                          capsys):
        monkeypatch.chdir(tmp_path)
        with mock.patch("urllib.request.urlopen",
                        side_effect=HTTPError(
                            "https://api.premiselabs.co/v1/agent/signup",
                            429, "rate limited", {},
                            io.BytesIO(b"{}"))):
            rc = main(["signup", "--claim"])
        assert rc == 1
