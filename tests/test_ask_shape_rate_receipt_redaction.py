"""#4105 review fix — the D3 instrument's receipt label must NEVER leak the
DB password.

``_substrate_label()`` (``tools/ask_shape_rate.py``) writes its value into the
``substrate`` field of a receipt that ``_write_receipt`` commits to the
TRACKED file ``docs/runbook/ask-shape-rate-<date>.json``.

The first version of that label masked on ``u.username or u.password`` and
echoed ``u.hostname``. That is not a redaction: ``urlparse`` splits userinfo
at the LAST ``@``, so a password containing ``@``/``?``/``#``/``/`` lands in
``hostname`` while BOTH of those fields parse EMPTY. Measured on the old
code:

  ``docker://:@S3cret-Pa55w0rd?@host:6379/askshape``
      -> ``docker://s3cret-pa55w0rd/<graph>``   (the FULL password)
  ``docker://:P@ssw0rd?x@host:6379/askshape``
      -> ``docker://***@ssw0rd/<graph>``        (a fragment, after a mask)

and a password fragment landing in the port slot surfaced in a raw
``ValueError`` message.

These tests pin the fix by VALUE — the returned label (and any refusal
message) must contain no character of the credential — not by grepping the
source.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_shape_rate import _substrate_label

#: Distinctive secrets that do NOT appear in the function's static example
#: text (``docker://:pw@host:6379/<graph>``), so a match is a real leak.
LEAKY = [
    "S3cret-Pa55w0rd",
    "tomato",
    "hunter2",
    "correcthorsebatterystaple",
]


@pytest.mark.parametrize("secret", LEAKY)
def test_no_credential_reaches_the_receipt_label(monkeypatch, secret):
    """Every malformed placement of a password must be refused or redacted —
    none may be echoed into the label that gets committed."""
    forms = [
        f"docker://:{secret}@host:6379/g",
        f"docker://:@ {secret}".replace(" ", "") + "?@host:6379/g",
        f"docker://:{secret}@host:6379/g?x=1",
        f"docker://:{secret}#frag@host:6379/g",
        f"docker://user:{secret}@host:6379/g",
        f"docker://:{secret}@host:6379/g/../x",
        # The parser itself raises EAGERLY on these two, and its message
        # embeds the netloc — the credential must not ride that out either.
        f"docker://:{secret}\uff20tail@host:6379/g",
        f"docker://:pw@[{secret}]:6379/g",
        # a credential occupying the SCHEME slot would be echoed by a blind
        # ``u.scheme`` — the label validates the scheme against the product's
        # own set instead.
        f"{secret}://:pw@host:6379/g",
        f"docker://:{secret}@host:99999/g",
        f"docker://:{secret}@host:notaport/g",
    ]
    for uri in forms:
        monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", uri)
        try:
            label = _substrate_label()
        except SystemExit as exc:  # fail-LOUD is allowed; leaking is not
            label = str(exc)
        assert secret not in label, f"{uri!r} leaked into {label!r}"
        assert secret.lower() not in label.lower(), (
            f"{uri!r} leaked (case-insensitive) into {label!r}")


def test_the_documented_form_redacts_the_authority(monkeypatch):
    """The normal documented form still yields a USEFUL label — the scheme
    (substrate class) and the validated numeric port — with the host and
    userinfo redacted."""
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       "docker://:pw@falkor.internal:16379/askshape")
    label = _substrate_label()
    assert label == "docker://<redacted>:16379/<graph>"
    assert "falkor.internal" not in label
    assert "askshape" not in label


def test_unset_names_the_embedded_lane(monkeypatch):
    monkeypatch.delenv("TORTOISE_ASK_SHAPE_DB_URI", raising=False)
    assert _substrate_label() == "embedded"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", "   ")
    assert _substrate_label() == "embedded"


def test_a_bad_port_or_a_non_ask_scheme_is_refused_by_name(monkeypatch):
    """Every refusal is OUR named ``SystemExit`` and quotes nothing — in
    particular the library's eager ``ValueError`` (whose message carries the
    netloc, hence a password) must never reach the caller."""
    for bad in ("docker://:pw@host:hunter2secret/g",
                "docker://:pw@host:99999/g",
                "docker://:p\uff20ssword@host:6379/g",
                "docker://:pw@[hunter2secret]:6379/g"):
        monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", bad)
        with pytest.raises(SystemExit) as exc:
            _substrate_label()
        assert "hunter2secret" not in str(exc.value).lower()
        assert "ssword" not in str(exc.value).lower()


def test_a_credential_in_the_scheme_slot_is_refused(monkeypatch):
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       "S3cret-Pa55w0rd://:pw@host:6379/g")
    with pytest.raises(SystemExit) as exc:
        _substrate_label()
    assert "unsupported" in str(exc.value).lower()
    assert "s3cret-pa55w0rd" not in str(exc.value).lower()


def test_a_uri_without_a_host_is_refused_by_name(monkeypatch):
    bad = "docker:/:pw@host:6379/g"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", bad)
    with pytest.raises(SystemExit) as exc:
        _substrate_label()
    assert "no host" in str(exc.value)
    # the malformed VALUE itself is not echoed back
    assert bad not in str(exc.value)
