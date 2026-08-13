"""CLI `tortoise signup` tests (#1081)."""
import json
from unittest import mock
from urllib.error import HTTPError

import tortoise.__main__ as main


def _http_error(code, body, headers=None):
    import io
    from email.message import Message
    msg = Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return HTTPError("https://api.premiselabs.co/v1/agent/signup", code,
                     "err", msg, io.BytesIO(body.encode()))


class TestSignup429:
    def test_429_prints_retry_and_support(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(
                            429, json.dumps({"detail": {"error_code": "over_signup_ip_rate_limit"}}),
                            {"Retry-After": "86399"})):  # computed remaining (P2-FIX-5)
            rc = main._cmd_signup(mock.Mock())
        err = capsys.readouterr().err
        assert rc == 1
        assert "86399" in err or "24h" in err or "later" in err
        assert "support@premiselabs.co" in err
        assert "Signup rate limit" in err

    def test_non_429_error_unchanged(self, capsys):
        with mock.patch("urllib.request.urlopen",
                        side_effect=_http_error(
                            500, json.dumps({"detail": "boom"}))):
            rc = main._cmd_signup(mock.Mock())
        err = capsys.readouterr().err
        assert rc == 1
        assert "Signup failed (500)" in err
        assert "boom" in err
