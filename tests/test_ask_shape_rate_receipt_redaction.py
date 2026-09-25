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

This file is also the D3 instrument's regression home for its READER PIN
(#4582): ``pinned_reader_env`` narrows the non-pinned provider keys out of
the build env, and ``tortoise.mcp_server``'s import-time ``_load_dotenv()``
used to re-arm them mid-run (see the ``#4582`` section at the bottom).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_shape_rate import (
    NARROWED_PROVIDER_KEYS,
    PINNED_MODEL,
    PINNED_PROVIDER,
    _fresh_db,
    _install_redacting_excepthook,
    _redact_substrate_text,
    _substrate_label,
    _write_receipt,
    assert_reader_pin,
    pinned_reader_env,
)
from tortoise.mcp_server import _load_dotenv
from tortoise.model_adapters import resolve_reader_provider

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
    netloc, hence a password) must never reach the caller. Each cause is
    pinned by its OWN expected text, so a guard that reports the wrong cause
    fails here."""
    cases = [
        # (value, must-not-appear, must-appear)
        ("docker://:pw@host:hunter2secret/g", ["hunter2secret"], "port"),
        ("docker://:pw@host:99999/g", [], "out-of-range"),
        ("docker://:p\uff20ssword@host:6379/g", ["ssword"], "malformed"),
        ("docker://:pw@[hunter2secret]:6379/g", ["hunter2secret"],
         "malformed"),
        ("Bogus-Scheme://:pw@host:6379/g", ["bogus-scheme"],
         "unsupported"),
    ]
    for bad, forbidden, expected in cases:
        monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", bad)
        with pytest.raises(SystemExit) as exc:
            _substrate_label()
        text = str(exc.value)
        assert expected in text.lower(), (bad, text)
        for frag in forbidden:
            assert frag not in text.lower(), (bad, text)


def test_fresh_db_shares_the_guard(monkeypatch):
    """``--mode seed-timing`` reaches ``_fresh_db`` WITHOUT building a
    receipt, so the guard must live on the shared parser rather than on the
    label alone (round-4 P1: the same two eager-parse forms leaked there)."""
    for bad, frag in (("docker://:p\uff20ssword@host:6379/g", "ssword"),
                      ("docker://:pw@[hunter2secret]:6379/g",
                       "hunter2secret"),
                      ("docker://:@S3cret-Pa55w0rd?@host:6379/g",
                       "s3cret-pa55w0rd")):
        monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", bad)
        with pytest.raises(SystemExit) as exc:
            _fresh_db("phase")
        assert frag not in str(exc.value).lower(), (bad, str(exc.value))


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


@pytest.mark.parametrize("secret", LEAKY)
def test_no_credential_reaches_the_committed_receipt_body(monkeypatch,
                                                          tmp_path, secret):
    """Round-5: the LABEL was receipt-safe, but the FAULT channel was not.

    A ``TORTOISE_ASK_SHAPE_DB_URI`` whose password lands in the HOST slot
    (``docker://:@<password>/graph`` — ``urlparse`` splits userinfo at the
    LAST ``@``, so ``hostname`` IS the password) passes the parser whenever a
    graph segment follows. The SDK then dials a host literally named after the
    password, and its connection error NAMES that host — which is written
    verbatim into ``receipt[...]["error"]``, a TRACKED file. Redacting only the
    label left this channel open.

    Pinned over the WHOLE written file (not just the label), so every nested
    carrier — per-question error, handler envelope, ``substrate_errors``,
    movement exclusion — is covered by construction.
    """
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    # The SDK names the endpoint it could not reach; here that endpoint is
    # the password, lower-cased by ``urlparse``.
    fault = (f"ConnectionError: Error 8 connecting to {secret.lower()}:16379. "
             "nodename nor servname provided, or not known")
    out = tmp_path / "receipt.json"
    receipt = {
        "instrument": "tools/ask_shape_rate.py",
        "substrate": _substrate_label(),
        "live": {"per_question": [{"question_id": "q1", "error": fault}],
                 "substrate_errors": [{"question_id": "q1", "source": "run",
                                       "error": fault}]},
        "movement": {"M1": {"per_question": [{"question_id": "q1",
                                              "error": fault}]}},
    }
    _write_receipt(SimpleNamespace(receipt=str(out)), receipt)
    written = out.read_text()
    assert secret not in written, f"{secret!r} leaked into the receipt body"
    assert secret.lower() not in written.lower(), (
        f"{secret!r} leaked (case-insensitive) into the receipt body")
    # The receipt stays a real, readable receipt — the redaction must not
    # have destroyed it.
    assert json.loads(written)["instrument"] == "tools/ask_shape_rate.py"
    assert str(out) in written


def _decoded_strings(value):
    """Every string reachable in a decoded JSON tree (keys included)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            if isinstance(k, str):
                yield k
            yield from _decoded_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _decoded_strings(v)


#: Credentials that ALSO collide with JSON syntax or with non-ASCII
#: serialization — the shapes that a post-serialization ``re.sub`` corrupts or
#: misses (``null`` rewrote the literal ``null``; ``false``/``true`` rewrote
#: booleans; ``pä55w0rd`` survived as ``\u00e4``).
SYNTAX_COLLIDING = [*LEAKY, "null", "false", "true", "2024", "pä55w0rd"]


@pytest.mark.parametrize("secret", SYNTAX_COLLIDING)
def test_the_receipt_is_redacted_AND_stays_valid_json(monkeypatch, tmp_path,
                                                      secret):
    """The redaction walks the OBJECT TREE, so it cannot corrupt the document.

    Measured before the fix: a credential of ``null``/``false``/``true`` (or a
    non-ASCII one, which ``json.dumps`` escapes) was substituted into the
    SERIALIZED text, producing an unparseable receipt the tool still reported
    as written — silent loss of the evidence artifact.
    """
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    fault = (f"ConnectionError: Error 8 connecting to {secret.lower()}:16379. "
             "nodename nor servname provided, or not known")
    out = tmp_path / "receipt.json"
    receipt = {
        "instrument": "tools/ask_shape_rate.py",
        "substrate": _substrate_label(),
        "movement_control_required": True,
        "measured_latency": None,
        "n_questions": 21,
        "live": {"per_question": [{"question_id": "q1", "error": fault,
                                   "l1_abstain": False}]},
        "movement": {"M1": {"per_question": [{"question_id": "q1",
                                              "error": fault}]}},
    }
    _write_receipt(SimpleNamespace(receipt=str(out)), receipt)
    written = out.read_text()
    parsed = json.loads(written)          # <-- must still be VALID JSON
    assert parsed["instrument"] == "tools/ask_shape_rate.py"
    # non-string leaves are untouched: a credential is never a literal
    assert parsed["movement_control_required"] is True
    assert parsed["measured_latency"] is None
    assert parsed["n_questions"] == 21
    for text in _decoded_strings(parsed):
        assert secret not in text, f"{secret!r} leaked into {text!r}"
        assert secret.lower() not in text.lower(), (
            f"{secret!r} leaked (case-insensitive) into {text!r}")
    if secret.lower() not in ("null", "true", "false"):
        # and for a non-colliding token, not in the raw document either
        assert secret not in written
        assert secret.lower() not in written.lower()


def test_the_excepthook_redacts_an_uncaught_traceback(monkeypatch, capsys):
    """``seed_timing()`` has no ``try`` and ``main()`` has only a ``finally``,
    so an SDK connection failure propagates and the interpreter prints the
    traceback — whose last line NAMES the endpoint, i.e. the password when the
    substrate URI put it in the host slot (round-5 finding; measured on the
    real CLI as a stderr leak into CI logs)."""
    secret = "S3cret-Pa55w0rd"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    saved = sys.excepthook
    try:
        _install_redacting_excepthook()
        try:
            raise ConnectionError(
                f"Error 8 connecting to {secret.lower()}:16379. "
                "nodename nor servname provided, or not known")
        except ConnectionError:
            sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = saved
    err = capsys.readouterr().err
    assert secret not in err
    assert secret.lower() not in err.lower()
    # a redaction, not a suppression: the diagnostic survives
    assert "ConnectionError" in err
    assert "Error 8 connecting to" in err
    assert "Traceback (most recent call last)" in err


def test_a_non_json_native_leaf_is_redacted_too(monkeypatch, tmp_path):
    """The encoder's ``default`` fallback runs AFTER the tree walk, so a leaf
    whose ``str()`` carries the credential used to be written verbatim."""
    secret = "hunter2"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")

    class Opaque:
        def __str__(self):
            return f"connection to {secret} failed"

    out = tmp_path / "receipt.json"
    receipt = {"instrument": "x", "nested": {"leaf": Opaque()}}
    _write_receipt(SimpleNamespace(receipt=str(out)), receipt)
    written = out.read_text()
    assert secret not in written
    assert "***" in written            # redacted, not dropped
    assert json.loads(written)["nested"]["leaf"].startswith("connection to")


def test_a_cyclic_receipt_neither_hangs_nor_truncates(monkeypatch, tmp_path):
    """A self-referential receipt used to raise RecursionError — and because
    the destination was opened first, that left a pre-existing receipt
    TRUNCATED to 0 bytes (evidence lost while reporting the fault)."""
    secret = "hunter2"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    out = tmp_path / "receipt.json"
    out.write_text('{"previous": "run evidence"}')

    receipt: dict = {"instrument": "x", "note": secret}
    receipt["self"] = receipt          # a true cycle
    _write_receipt(SimpleNamespace(receipt=str(out)), receipt)

    written = out.read_text()
    assert written, "the pre-existing receipt was truncated"
    parsed = json.loads(written)       # still valid JSON
    assert parsed["self"] == "<circular reference>"
    assert secret not in written
    assert not list(tmp_path.glob("*.tmp.*")), "temp file was left behind"


def test_a_component_that_raises_does_not_drop_the_others(monkeypatch):
    """``.hostname``/``.port`` raise EAGERLY on the documented malformations.

    Registering the components through a lazy generator aborted on the first
    raise and silently dropped the rest (measured at review: only the raw value
    and netloc were registered for ``docker://:pw@[<secret>]:6379/g``), so the
    redactor was weakest exactly where the netloc was most suspicious.
    """
    secret = "S3cret-Pa55w0rd"
    for uri in (f"docker://:pw@[{secret}]:6379/g",
                f"docker://:pw@[{secret}]:notaport/g"):
        monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", uri)
        text = f"connecting to {secret} now"
        assert secret not in _redact_substrate_text(text), uri
        assert secret.lower() not in _redact_substrate_text(text).lower(), uri


def test_the_hook_covers_threads_and_unraisable_exceptions(monkeypatch,
                                                           capsys):
    """CPython does NOT route a thread's uncaught exception or an atexit
    callback's exception through ``sys.excepthook`` — their default hooks print
    the traceback themselves, so installing only ``sys.excepthook`` left the
    credential on stderr (measured at review)."""
    secret = "S3cret-Pa55w0rd"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    saved = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    try:
        _install_redacting_excepthook()
        # (a) a THREAD's uncaught exception
        def _boom():
            raise ConnectionError(
                f"Error 8 connecting to {secret.lower()}:16379.")
        thread = threading.Thread(target=_boom, name="redaction-probe")
        thread.start()
        thread.join()
        # (b) an UNRAISABLE (the atexit-callback path)
        class _Unraisable:
            exc_type = ConnectionError
            exc_value = ConnectionError(
                f"Error 8 connecting to {secret.lower()}:16379.")
            exc_traceback = None
            err_msg = "Exception ignored in atexit callback"

        sys.unraisablehook(_Unraisable())
    finally:
        (sys.excepthook, threading.excepthook, sys.unraisablehook) = saved
    err = capsys.readouterr().err
    assert secret not in err
    assert secret.lower() not in err.lower()
    # a redaction, not a suppression
    assert "Error 8 connecting to" in err
    assert "Exception in thread redaction-probe" in err


def test_a_bare_relative_receipt_path_can_be_written(monkeypatch, tmp_path):
    """``os.makedirs("")`` raises, so ``--receipt receipt.json`` could not
    produce its evidence artifact (pre-existing on origin/main)."""
    monkeypatch.delenv("TORTOISE_ASK_SHAPE_DB_URI", raising=False)
    monkeypatch.chdir(tmp_path)
    _write_receipt(SimpleNamespace(receipt="receipt.json"),
                   {"instrument": "x"})
    assert json.loads((tmp_path / "receipt.json").read_text())["instrument"] == "x"


def test_the_hook_redacts_its_metadata_lines_too(monkeypatch, capsys):
    """The traceback was redacted but the METADATA around it was written raw:
    a thread name embedding a registered substrate token, or an unraisable
    ``err_msg`` carrying an object repr, reached stderr verbatim."""
    secret = "S3cret-Pa55w0rd"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    saved = (sys.excepthook, threading.excepthook, sys.unraisablehook)
    try:
        _install_redacting_excepthook()

        def _boom():
            raise ConnectionError("boom")

        thread = threading.Thread(target=_boom,
                                  name=f"graph-{secret}")  # a credential NAME
        thread.start()
        thread.join()

        class _Unraisable:
            exc_type = ConnectionError
            exc_value = ConnectionError("boom")
            exc_traceback = None
            object = None
            err_msg = f"Exception ignored in: <redis connection to {secret}>"

        sys.unraisablehook(_Unraisable())
    finally:
        (sys.excepthook, threading.excepthook, sys.unraisablehook) = saved
    err = capsys.readouterr().err
    assert secret not in err
    assert secret.lower() not in err.lower()
    # redacted, not suppressed — the metadata shape survives
    assert "Exception in thread graph-" in err
    assert "Exception ignored in:" in err


def test_the_hook_keeps_the_unraisable_object_repr_line(monkeypatch, capsys):
    """The default ``sys.unraisablehook`` has TWO metadata forms: ``err_msg``
    when set, and ``Exception ignored in: <object repr>`` otherwise (the
    ``__del__``/GC path — the teardown class the hook exists for). Emitting
    only the ``err_msg`` form silently DROPPED a whole diagnostic line, which
    is more than the "only credential tokens are substituted" claim allows."""
    secret = "S3cret-Pa55w0rd"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    saved = sys.unraisablehook
    try:
        _install_redacting_excepthook()

        class _DelProbe:
            def __repr__(self):
                return f"<redis connection to {secret}:16379>"

        class _Unraisable:
            exc_type = ConnectionError
            exc_value = ConnectionError("boom")
            exc_traceback = None
            err_msg = None                 # the __del__/GC path
            object = _DelProbe()

        sys.unraisablehook(_Unraisable())
    finally:
        sys.unraisablehook = saved
    err = capsys.readouterr().err
    assert secret not in err
    assert secret.lower() not in err.lower()
    # the default hook's line SURVIVES — redacted, not dropped
    assert "Exception ignored in: <redis connection to" in err
    assert "ConnectionError: boom" in err


def test_the_hook_emits_the_object_repr_in_BOTH_unraisable_shapes(monkeypatch,
                                                                  capsys):
    """CPython's default hook is not ``err_msg`` XOR ``object``: with
    ``err_msg`` set it prints ``f"{err_msg}: {object!r}"`` (the atexit shape —
    measured on this interpreter as
    ``Exception ignored in atexit callback: <function cb at 0x…>``), and with
    ``err_msg`` unset ``f"Exception ignored in: {object!r}"``. Treating them as
    mutually exclusive DROPPED the object repr on the atexit shape."""
    secret = "S3cret-Pa55w0rd"
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI",
                       f"docker://:@{secret}/invalid/g")
    saved = sys.unraisablehook
    try:
        _install_redacting_excepthook()

        class _Probe:
            def __repr__(self):
                return f"<function cb bound to {secret}:16379>"

        class _Unraisable:
            exc_type = ConnectionError
            exc_value = ConnectionError("boom")
            exc_traceback = None
            object = _Probe()

        # (a) the atexit shape: err_msg SET — the repr must SURVIVE
        _Unraisable.err_msg = "Exception ignored in atexit callback"
        sys.unraisablehook(_Unraisable())
        # (b) the __del__ shape: err_msg unset
        _Unraisable.err_msg = None
        sys.unraisablehook(_Unraisable())
    finally:
        sys.unraisablehook = saved
    err = capsys.readouterr().err
    assert secret not in err
    assert secret.lower() not in err.lower()
    assert "Exception ignored in atexit callback: <function cb bound to" in err
    assert "Exception ignored in: <function cb bound to" in err


def test_a_short_credential_cannot_rename_the_receipt_schema(monkeypatch,
                                                             tmp_path):
    """Redacting dict KEYS was fail-open in the other direction: a credential
    that is a substring of a schema key renamed it (measured with ``e``:
    ``receipt_path`` -> ``r***c***ipt_path``), so the receipt parsed, reported
    success, and had silently lost the fields it exists to carry."""
    monkeypatch.setenv("TORTOISE_ASK_SHAPE_DB_URI", "docker://:e@host:6379/g")
    out = tmp_path / "receipt.json"
    receipt = {
        "instrument": "tools/ask_shape_rate.py",
        "substrate": "embedded",
        "seeding_mode": {"mode": "embedded"},
        "live": {"per_question": [{"question_id": "q1", "error": "some error"}]},
    }
    _write_receipt(SimpleNamespace(receipt=str(out)), receipt)
    parsed = json.loads(out.read_text())
    # The SCHEMA survives, so every field is still FINDABLE.
    assert set(parsed) >= {"instrument", "substrate", "seeding_mode", "live",
                           "receipt_path"}
    assert set(parsed["live"]) == {"per_question"}
    assert isinstance(parsed["receipt_path"], str) and parsed["receipt_path"]
    # values are still redacted exhaustively — over-redaction is the
    # documented, acceptable cost for a VALUE (here every "e")
    assert "e" not in parsed["instrument"]
    assert parsed["live"]["per_question"][0]["question_id"] == "q1"


# ── #4582 — the READER PIN must be durable, not order-dependent ────────────
#
# ``pinned_reader_env`` narrows the non-pinned provider keys out of the build
# env, then ``assert_reader_pin`` requires the resolved pool to be exactly
# ``['deepseek-direct']``. That assertion runs ONCE, before the live loop.
#
# The leak: the narrowing POPPED the keys, so they were ABSENT.
# ``tortoise.mcp_server`` runs ``_load_dotenv()`` at import time, and that
# loader fills every key NOT present from the repo-root ``.env``. The
# instrument imports ``tortoise.mcp_server`` only later, inside
# ``_shipping_handlers`` on the first ask — so the first ask re-injected
# ``OPENROUTER_API_KEY`` / ``VENICE_API_KEY`` and widened the pool back to
# ``['deepseek-direct', 'openrouter', 'venice']``, re-arming the #476 failover.
# One transient deepseek error then hopped the run off the pin (measured:
# question ``1de5cff2``, provider ``openrouter``, run VOID).
#
# The tests below drive the REAL loader (``tortoise.mcp_server._load_dotenv``)
# from inside the pin, against a temp ``.env`` — the exact function the
# import-time call runs and the exact keys it fills. (pytest's ``sys.modules``
# guard only skips the import-time *call*; the loader is what re-arms the
# provider, so invoking it directly is the faithful test.)

#: The loader's default path resolves to the repo-root ``.env`` relative to
#: the ``tortoise`` package — the same file the later ``_shipping_handlers``
#: import consumes.
_PINNED_POOL = (PINNED_PROVIDER, [PINNED_PROVIDER])


def _dotenv_with_all_provider_keys(tmp_path: Path) -> Path:
    """A repo-root-shaped ``.env`` carrying every provider key — the shape
    that widens the pool when the loader re-fills a popped key."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "DEEPSEEK_API_KEY=from-dotenv-deepseek\n"
        "OPENROUTER_API_KEY=from-dotenv-openrouter\n"
        "VENICE_API_KEY=from-dotenv-venice\n",
        encoding="utf-8",
    )
    return env_file


def test_narrowed_keys_are_emptied_not_removed(monkeypatch):
    """The durability mechanism: a narrowed PROVIDER key stays PRESENT
    (empty), so the loader's ``key not in os.environ`` guard refuses to refill
    it. Popping it leaves it absent and the guard re-fills it — #4582.

    ``TORTOISE_API_URL`` is the deliberate exception: it keeps the original
    POP (it is never `.env`-sourced, and its SDK/CLI consumers read
    ``.get(name, default)``, which an empty string would poison) — so absence
    is asserted for it, not an empty value."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "present-before")
    monkeypatch.setenv("VENICE_API_KEY", "present-before")
    monkeypatch.setenv("TORTOISE_API_URL", "https://hosted.example")

    with pinned_reader_env() as info:
        for key in NARROWED_PROVIDER_KEYS:
            assert key in os.environ, (
                f"{key} was REMOVED by the pin — the loader will re-append it "
                "from .env on the next import (#4582)")
            assert os.environ[key] == "", f"{key} is still keyed"
        assert "TORTOISE_API_URL" not in os.environ
        assert info["narrowed"] == [
            "OPENROUTER_API_KEY", "VENICE_API_KEY", "TORTOISE_API_URL"]


def test_loader_refill_inside_the_pin_cannot_re_arm_the_provider(
        tmp_path, monkeypatch):
    """The exact leak: inside the pin, run the loader the way
    ``import tortoise.mcp_server`` runs it (repo-root ``.env``) and assert the
    resolved pool is STILL the single pinned provider."""
    env_file = _dotenv_with_all_provider_keys(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "live-deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "live-openrouter")
    monkeypatch.setenv("VENICE_API_KEY", "live-venice")

    with pinned_reader_env():
        assert resolve_reader_provider(PINNED_MODEL) == _PINNED_POOL
        _load_dotenv(path=str(env_file))
        assert resolve_reader_provider(PINNED_MODEL) == _PINNED_POOL, (
            "the .env loader re-armed a narrowed provider — the #476 failover "
            "leg is live again and the measured wire is not the pinned one")


def test_assert_reader_pin_survives_the_later_import(tmp_path, monkeypatch):
    """The instrument's own ordering, in order: assert the pin (as
    ``run_full`` does, before the live loop), THEN let the loader run (as the
    first ``_shipping_handlers`` import does), then assert again. The second
    assertion is the one the leak defeated."""
    env_file = _dotenv_with_all_provider_keys(tmp_path)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "live-deepseek")
    monkeypatch.setenv("OPENROUTER_API_KEY", "live-openrouter")
    monkeypatch.setenv("VENICE_API_KEY", "live-venice")

    with pinned_reader_env():
        assert_reader_pin()                 # run_full, pre-live-loop
        _load_dotenv(path=str(env_file))    # first _shipping_handlers import
        try:
            info = assert_reader_pin()      # the pin must still hold
        except SystemExit as e:
            pytest.fail(
                "assert_reader_pin aborted with "
                f"exit {e.code} after the import-time .env load — the pin was "
                "defeated mid-run (#4582)")

    assert info["provider"] == PINNED_PROVIDER
    assert info["reader_class"] in ("RoutingModel", "RotatingModel")


def test_the_pin_restores_the_prior_environment(monkeypatch):
    """The narrowing must not leak out of the pin: a key that was set comes
    back with its value, a key that was absent is absent again."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "live-openrouter")
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.setenv("TORTOISE_API_URL", "https://hosted.example")
    monkeypatch.setenv("TORTOISE_ASK_PROVIDER", "auto")

    with pinned_reader_env():
        assert os.environ["OPENROUTER_API_KEY"] == ""   # narrowed inside ...
        assert "TORTOISE_API_URL" not in os.environ     # ... and popped

    assert os.environ["OPENROUTER_API_KEY"] == "live-openrouter"  # ... restored
    assert "VENICE_API_KEY" not in os.environ
    assert os.environ["TORTOISE_API_URL"] == "https://hosted.example"
    assert os.environ["TORTOISE_ASK_PROVIDER"] == "auto"


def test_the_loader_never_refills_a_present_but_empty_key(tmp_path, monkeypatch):
    """The contract the empty-string narrowing rests on: ``_load_dotenv``
    treats a PRESENT key as explicit, even an empty one. If this loader ever
    switches to a truthiness test, the pin silently stops holding — this test
    is the tripwire for that."""
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    _load_dotenv(path=str(env_file))

    assert os.environ["OPENROUTER_API_KEY"] == ""
