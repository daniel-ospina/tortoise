"""#4911 — the capture path redacts credentials before they are persisted.

The defect: turn text was stored VERBATIM, so a credential pasted into a
session landed in the hosted multi-tenant graph as a ``Point{pointKind:'event'}``
and was thereafter readable through the MCP/SDK surface and re-indexed into
extraction and search. Nothing had leaked yet only because capture is
session-end and the count was a coincidence of timing — the control was simply
absent.

What these tests pin, one acceptance criterion each:

1. ``test_every_credential_shape_is_redacted_end_to_end`` — a turn containing
   each anchored shape is captured through ``sdk.capture_session`` and the row
   read back from the graph carries ``[REDACTED:<kind>]``, never the value.
2. ``test_redaction_is_visible_not_a_silent_truncation`` — the marker is
   present AND the surrounding text survives, so a reader can tell the text is
   incomplete. (The same silent-loss class as #4897, inverted.)
3. ``test_redaction_count_is_recorded_per_session_and_surfaced`` — the count is
   on the ``:Session`` node (``capture_redactions``) AND on the capture receipt.
4. ``test_control_lives_at_the_single_stored_text_chokepoint`` — exactly ONE
   place in the capture paths applies the scrubber (``_redact_turn_contents``)
   and every capture consumer derives its text through it — the stored turns,
   the session Source and the extractor — so no lane can drift and no
   persisting sink can be forgotten.
5. ``test_local_spool_keeps_the_raw_turn_by_decision`` — the deliberate scope
   decision (see that test's docstring): the LOCAL raw store is out of scope.

Plus the invariants a naive implementation breaks: the prose false positives
the issue itself records (``risk-``/``disk-``/``task-`` — a naive
``CONTAINS 'sk-'`` census returned 193 of them and every one was prose),
idempotency under the capture path's own double-pass, and the #4194
``content_hash``/stored-text agreement (the hash must describe the REDACTED
text, or a reader recomputing it would disagree with the node).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tortoise import capture_spool
from tortoise.sdk import (
    TortoiseSDK,
    _capture_turn_role_text,
    _capture_turn_texts,
    _content_hash,
    _redact_summary_strings,
    _redact_turn_contents,
)
from tortoise.security import CREDENTIAL_KINDS, redact_secrets

_REPO = Path(__file__).resolve().parent.parent

_ALNUM = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _fill(n: int) -> str:
    return (_ALNUM * (n // len(_ALNUM) + 1))[:n]


def _synth(*parts: str) -> str:
    """Assemble a SYNTHETIC credential from parts at RUNTIME.

    A redaction test necessarily contains credential-SHAPED values, but a
    literal token must not appear contiguously in the SOURCE: GitHub push
    protection refuses the push over it — observed on the first push of this
    file, which was rejected on two ``xoxb-`` fixtures — and a repo secret
    scanner would flag the file on every future push. Joining at runtime keeps
    the value under test byte-identical while leaving no matchable token in the
    file.
    """
    return "".join(parts)


def _pem(label: str) -> str:
    """``-----BEGIN <label>-----`` assembled at runtime (see ``_synth``)."""
    return _synth("-----", "BEGIN ", label, "-----")


def _pem_end(label: str) -> str:
    """``-----END <label>-----`` assembled at runtime (see ``_synth``)."""
    return _synth("-----", "END ", label, "-----")


def _webhook(*parts: str) -> str:
    """A Slack webhook URL assembled at runtime (see ``_synth``)."""
    return _synth("hooks.", "slack.com/", *parts)


#: ``(case, kind, value)`` — one row per PATTERN (not just per kind: GitHub has
#: two, classic and fine-grained). ``test_..._end_to_end`` asserts this covers
#: every kind the scrubber can emit.
CASES: tuple[tuple[str, str, str], ...] = (
    ("anthropic", "anthropic_api_key", "sk-ant-api03-" + _fill(93) + "AA"),
    ("openai", "openai_api_key", "sk-proj-" + _fill(64)),
    # #4911 review: DeepSeek's key is `sk-` + EXACTLY 32 lowercase alnum —
    # below the generic `sk-` rule's 40 floor, so it needs its own row or the
    # floor can silently regress. Assembled at runtime (see ``_synth``).
    ("deepseek", "deepseek_api_key",
     _synth("sk-", "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6")),
    ("jev", "jev_api_key", "jv_live_" + _fill(24)),
    ("github_classic", "github_token", "ghp_" + _fill(36)),
    ("github_fine_grained", "github_token", "github_pat_" + _fill(60)),
    ("aws", "aws_access_key_id", "AKIA" + "ABCDEFGHIJKLMNOP"),
    ("aws_secret", "aws_secret_access_key",
     "aws_secret_access_key = "
     + _synth("wJalrXUtnFEMI", "/K7MDENG", "/bPxRfiCYEXAMPLEKEY")),
    ("google", "google_api_key", "AIza" + _fill(35)),
    ("slack", "slack_token", _synth("xox", "b-", _fill(12), "-", _fill(16))),
    ("slack_webhook", "slack_webhook_url",
     _webhook("services/T00000000/B00000000/", _fill(24))),
    ("supabase", "supabase_secret_key", "sb_secret_" + _fill(40)),
    ("gitlab", "gitlab_token", "glpat-" + _fill(24)),
    ("npm", "npm_token", "npm_" + _fill(36)),
    ("huggingface", "huggingface_token", "hf_" + _fill(34)),
    ("huggingface_org", "huggingface_token", "api_org_" + _fill(34)),
    ("supabase_pat", "supabase_secret_key", "sbp_" + _fill(40)),
    ("slack_workflow_webhook", "slack_webhook_url",
     _synth("https://", _webhook(
         "workflows/T00000000/B00000000/1234567890/", _fill(16)))),
    ("slack_trigger_webhook", "slack_webhook_url",
     _synth("https://", _webhook(
         "triggers/T00000000/B00000000/1234567890/", _fill(16)))),
    ("stripe_prod", "stripe_secret_key", "sk_prod_" + _fill(24)),
    ("stripe_restricted_prod", "stripe_secret_key", "rk_prod_" + _fill(24)),
    ("stripe_secret", "stripe_secret_key", "sk_live_" + _fill(24)),
    ("stripe_webhook", "stripe_webhook_secret", "whsec_" + _fill(24)),
    ("jwt", "jwt",
     "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
     ".eyJzdWIiOiIxMjM0NTY3ODkwIn0." + _fill(43)),
    ("private_key", "private_key",
     _pem("RSA PRIVATE KEY") + "\nMIIEowIBAAKCAQEA\n"
     + _pem_end("RSA PRIVATE KEY")),
    # #4911 review: the two PEM shapes the first cut missed — a PGP block (the
    # label does not END in `PRIVATE KEY`) and the lowercase form.
    ("private_key_pgp", "private_key",
     _pem("PGP PRIVATE KEY BLOCK") + "\nmQENBGA\n"
     + _pem_end("PGP PRIVATE KEY BLOCK")),
    ("private_key_lowercase", "private_key",
     _synth("-----", "begin rsa private key-----") + "\nMIIEowIBAAKCAQEA\n"
     + _synth("-----", "end rsa private key-----")),
    ("bearer", "bearer_token", "Authorization: Bearer " + _fill(32)),
)


def _keyless(monkeypatch) -> None:
    """No LLM extraction: the turns are written by the mechanical loop under
    test, and a keyless capture cannot make a network call or silently run a
    mock (same seam as tests/test_turn_embedding_write_path_4194.py)."""
    monkeypatch.delenv("TORTOISE_SESSION_LLM_MOCK", raising=False)
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from tortoise.sdk import _build_session_llm_extractor
    assert _build_session_llm_extractor() is None, "keys leaked into the test"


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(str(tmp_path / "t.db"))
    try:
        yield s
    finally:
        s.close()


def _stored(sdk: TortoiseSDK, session_id: str) -> dict[str, str]:
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p "
        "RETURN t.id, t.content, t.content_hash ORDER BY t.id",
        params={"p": f"{session_id}_t"}).result_set
    return {r[0]: (r[1], r[2]) for r in rows}


def _session_source_blob(sdk: TortoiseSDK, session_id: str) -> str:
    """Every turn-derived string the capture persists on the session `:Source`.

    The Source's `summary`/`topics` ARE turn text — the first "substantive
    utterance" and the six most frequent content words — so a scrubber that
    only covered the turn `:Point`s left the same credential in the same graph
    one property over.
    """
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.summary, s.topics",
        params={"u": f"session:{session_id}"}).result_set
    assert rows, f"no :Source materialized for session:{session_id}"
    summary, topics = rows[0]
    return (summary or "") + " " + " ".join(topics or [])


def _session_redactions(sdk: TortoiseSDK, session_id: str):
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_redactions",
        params={"sid": session_id}).result_set
    return rows[0][0] if rows else None


# ── AC1: every shape, end to end, marker in / value out ────────────────────

def test_every_credential_shape_is_redacted_end_to_end(sdk, monkeypatch):
    """One captured turn per shape; the GRAPH ROW carries the marker only."""
    _keyless(monkeypatch)
    assert {kind for _case, kind, _v in CASES} == set(CREDENTIAL_KINDS), (
        "the sample table no longer covers every kind the scrubber emits — "
        "a new shape needs a sample row here")

    conv = [{"role": "user", "content": f"leaked value: {value} (please rotate)"}
            for _case, _kind, value in CASES]
    sid = "sess-4911-shapes"
    res = sdk.capture_session(conv, session_id=sid)

    stored = _stored(sdk, sid)
    assert len(stored) == len(CASES)
    for i, (case, kind, value) in enumerate(CASES):
        content, _hash = stored[f"{sid}_t{i}"]
        assert value not in content, (
            f"{case}: the {kind} VALUE survived into the stored turn: {content!r}")
        assert f"[REDACTED:{kind}]" in content, (
            f"{case}: expected the visible marker for {kind}, got {content!r}")
        # The rest of the turn is untouched — this is a replacement, not a cut.
        assert "leaked value: " in content and "(please rotate)" in content
        # The OTHER persistence sink on the same write: the session `:Source`
        # derives its summary/topics from the same turn text.
        assert value not in _session_source_blob(sdk, sid), (
            f"{case}: the {kind} VALUE survived into the session :Source")
    assert res["capture_redactions"] == len(CASES)


def test_a_multipart_credential_is_not_left_in_the_source_as_fragments(
        sdk, monkeypatch):
    """The sentence segmenter splits a JWT and rejoins it with SPACES.

    ``_session_llm_transcript`` runs the conversation through ``extractor._SENT``
    and flattens newlines before the Source derives its summary/topics, so a
    scrubber applied to the ASSEMBLED transcript can never see a JWT: the
    segmenter splits it on its dots and the three fragments — still a usable
    key when concatenated — land in ``Source.summary``/``topics`` while the
    receipt reports a redaction. Redacting per turn, before assembly, is what
    closes it; this asserts the FRAGMENTS, not just the contiguous value.
    """
    _keyless(monkeypatch)
    value = next(v for _c, k, v in CASES if k == "jwt")
    segments = value.split(".")
    assert all(len(s) >= 10 for s in segments)
    sid = "sess-4911-source-fragments"
    sdk.capture_session(
        [{"role": "user", "content": f"my token is {value} rotate it"}],
        session_id=sid)

    blob = _session_source_blob(sdk, sid)
    assert value not in blob, blob
    for segment in segments:
        assert segment not in blob, (
            "a JWT segment survived in the session :Source — the credential is "
            f"reconstructable from the graph: {blob!r}")
    # The space-rejoined form is what the segmenter produces; it must not be
    # present either (i.e. the value was never there to be split).
    assert " ".join(segments) not in blob, blob
    assert _stored(sdk, sid)[f"{sid}_t0"][0].count("[REDACTED:jwt]") == 1


def test_redaction_is_visible_not_a_silent_truncation(sdk, monkeypatch):
    """The marker is present and the surrounding span survives verbatim.

    A ``***``-style scrub or a truncation would pass "the secret is gone" while
    destroying the record. The contract is: the fact is gone, the SPACE it
    occupied says so, and everything else is byte-identical.
    """
    _keyless(monkeypatch)
    secret = "sk_live_" + _fill(24)
    prefix, suffix = "preceding context ", " trailing context"
    sid = "sess-4911-visible"
    sdk.capture_session([{"role": "user", "content": prefix + secret + suffix}],
                        session_id=sid)

    content, text_hash = _stored(sdk, sid)[f"{sid}_t0"]
    assert content == f"[user] {prefix}[REDACTED:stripe_secret_key]{suffix}"
    # Visible, not truncated: nothing was silently dropped.
    assert len(content) == len(f"[user] {prefix}{secret}{suffix}") - len(secret) \
        + len("[REDACTED:stripe_secret_key]")
    # #4194: the stored hash describes the REDACTED text (a hash of the raw
    # text would disagree with every reader that recomputes it from content).
    assert text_hash == _content_hash(content)
    assert text_hash != _content_hash(f"[user] {prefix}{secret}{suffix}")


def test_the_extraction_leg_writes_the_marker_not_the_credential(
        tmp_path, monkeypatch):
    """The capture's OTHER write path: the extractor→``create_point`` leg.

    Redacting only the turn store left a live hole: the extractor was handed the
    RAW conversation, and when a model echoes the pasted value into a claim the
    Point was written by ``create_point`` — a different sink with no scrubber —
    so the capture reported a redaction while storing the credential verbatim
    in a non-episodic Point. Both extractor lanes now take the scrubbed
    conversation (this exercises the M2 lane, the deterministic CI seam).
    """
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    sdk = TortoiseSDK(str(tmp_path / "ext.db"))
    try:
        secret = "sk-proj-" + _fill(64)
        sid = "sess-4911-extract"
        res = sdk.capture_session(
            [{"role": "user",
              "content": f"My OpenAI API key is {secret} — keep it safe."}],
            session_id=sid)
        assert res["capture_redactions"] == 1
        assert res["extraction_mode"] == "llm", res
        assert res["extracted"] >= 1, "the extractor did not run — nothing proven"

        # EVERY node, not just the turns: a leak in any of them is the defect.
        rows = sdk._get_proj().g.query(
            "MATCH (n) RETURN n.id, labels(n), n.content, n.summary").result_set
        assert rows
        for node_id, _labels, content, summary in rows:
            blob = f"{content or ''}{summary or ''}"
            assert secret not in blob, f"{node_id} stored the raw credential"
        # And the extracted claim carries the visible marker, so a reader can
        # tell a credential was removed from it.
        claims = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.is_episodic IS NULL "
            "RETURN p.content").result_set
        assert claims, "no extracted claim landed"
        assert any("[REDACTED:openai_api_key]" in (c or "") for c, in claims)
    finally:
        sdk.close()


def test_an_over_long_turn_matches_between_client_and_server(sdk, monkeypatch):
    """The #4675 confirmation must compare like with like on a cut turn.

    The server CAPS then SCRUBS (its capture windows the conversation before the
    writer runs); if the client instead scrubs the RAW turn and only then cuts,
    any turn over 5,000 chars containing a credential produces a different
    string on each side — the confirmation never matches, and the spool entry
    defers forever. Both sides now window first.
    """
    from tortoise.session_confirm import expected_turns

    _keyless(monkeypatch)
    secret = "sk_live_" + _fill(24)
    long_turn = "x" * 4989 + " " + secret + " tail"
    assert len(long_turn) > 5000
    sid = "sess-4911-cut"
    sdk.capture_session([{"role": "user", "content": long_turn}], session_id=sid)

    stored, _hash = _stored(sdk, sid)[f"{sid}_t0"]
    client = expected_turns(sid, [{"role": "user", "content": long_turn}])
    _role, expected_text = client[f"{sid}_t0"]
    _role2, stored_text = _capture_turn_role_text(stored)
    assert expected_text == stored_text, (
        "client and server computed different stored text for a cut turn — the "
        "spool confirmation would defer forever")
    assert secret not in stored_text, (
        "the whole credential survived the cut, so this test proves nothing")


def test_the_source_sink_scans_a_bounded_window(sdk, monkeypatch):
    """The Source scrub is bounded, and a non-str content cannot dodge it.

    Both halves are the same defect seen from two directions: the Source
    transcript builder receives the RAW, client-controlled conversation, so an
    unbounded scan is seconds of CPU per capture (the hosted caller runs it off
    the event loop on ``_CAPTURE_EXECUTOR`` — the bound is window parity and
    cost, not loop protection, #4911); and a ``content`` that is not a str used
    to be skipped by the scrubber and then stringified by that same builder —
    the credential landed in ``Source.summary`` while the receipt said 0.

    ⛔ The assertion on the non-str turn is the marker's PRESENCE, not merely the
    secret's ABSENCE. Absence alone is satisfied VACUOUSLY by any arrangement in
    which the turn never reaches the summary, and it therefore did not bind the
    control: neutralising ``redact_secrets`` left this test GREEN. The non-str
    turn is placed FIRST and kept short enough to be the first substantive
    utterance, so it IS the summary — which makes the marker check load-bearing.
    """
    _keyless(monkeypatch)
    secret = "sk_live_" + _fill(24)
    beyond = "sk-proj-" + _fill(64)
    sid = "sess-4911-source-bound"
    # A turn far larger than the stored window (its credential sits past the
    # 5,000-char cap), plus a structurally odd first turn carrying the secret.
    sdk.capture_session(
        [{"role": "user", "content": {"note": f"my key is {secret}"}},
         {"role": "user", "content": "pad " * 200_000 + beyond}],
        session_id=sid)

    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.summary, s.topics",
        params={"u": f"session:{sid}"}).result_set
    assert rows
    blob = (rows[0][0] or "") + " " + " ".join(rows[0][1] or [])
    # (a) the non-str turn's credential was COERCED, SCANNED and MARKED. This is
    #     the assertion that binds: with the scrubber neutralised the raw value
    #     is here instead of the marker and this reds.
    assert "[REDACTED:stripe_secret_key]" in blob, (
        "a credential was dropped rather than marked — or the non-str turn was "
        f"never scanned: {blob!r}")
    assert secret not in blob, (
        "a credential reached the session :Source — check the coercion path "
        "and the per-turn ordering")
    # (b) the turn beyond the 5,000-char cap was never persisted, so its
    #     credential cannot reach this sink (the documented residual is a
    #     PREFIX of a value cut mid-body in the stored TURN, not this sink).
    assert beyond not in blob, blob


# ── AC3: count recorded per session AND surfaced ───────────────────────────

def test_redaction_count_is_recorded_per_session_and_surfaced(sdk, monkeypatch):
    """The count is on the Session node and on the receipt, with a warning."""
    _keyless(monkeypatch)
    sid = "sess-4911-count"
    conv = [
        {"role": "user", "content": "two here: sk_live_" + _fill(24)
         + " and whsec_" + _fill(24)},
        {"role": "assistant", "content": "nothing sensitive in this one"},
    ]
    res = sdk.capture_session(conv, session_id=sid)

    assert res["capture_redactions"] == 2
    assert any("redacted" in w for w in res["warnings"]), res["warnings"]
    assert _session_redactions(sdk, sid) == 2

    clean_sid = "sess-4911-clean"
    clean = sdk.capture_session(
        [{"role": "user", "content": "a session with no credentials in it"}],
        session_id=clean_sid)
    assert clean["capture_redactions"] == 0
    assert not any("redacted" in w for w in clean["warnings"])
    assert _session_redactions(sdk, clean_sid) == 0


def test_capture_redactions_survives_a_journal_only_replay(tmp_path, monkeypatch):
    """The Session property has a journal carrier, so a rebuild restores it.

    The live write is a raw ``SET`` inside ``_write_capture_turns``; without a
    trailing ``SessionRecorded`` a journal-only rebuild restored the field null
    — the same live/replay divergence #3664/#3722 removed for ``capture_ok``.
    Exercised through the apply()-based engine (wipe + ``recover_from_log``),
    the pattern ``tests/test_capture_entity_attachment_3664.py`` established.
    """
    _keyless(monkeypatch)
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "g.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        sid = "sess-4911-journal"
        sdk.capture_session(
            [{"role": "user", "content": "key sk_live_" + _fill(24)}],
            session_id=sid)
        proj = sdk._get_proj()
        assert _session_redactions(sdk, sid) == 1

        proj.g.query("MATCH (n) DETACH DELETE n")
        from tortoise.consistency import recover_from_log
        result = recover_from_log(str(events), proj)
        assert result["recovered"] is True, result
        assert _session_redactions(sdk, sid) == 1
    finally:
        sdk.close()


# ── AC4: one control site, at the shared stored-text definition ────────────

def _functions_calling(path: Path, name: str) -> set[str]:
    """Enclosing function names of every call to ``name`` in ``path``."""
    tree = ast.parse(path.read_text())
    owners: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == name):
                owners.add(node.name)
    return owners


def _redact_call_owners(path: Path) -> set[str]:
    """Names of the functions that call `redact_secrets` in `path`.

    Attributed per enclosing `def`, so a DUPLICATE control introduced in a new
    function shows up as an unexpected owner rather than being counted as the
    same site.
    """
    return _functions_calling(path, "redact_secrets")


def test_control_lives_at_the_single_stored_text_chokepoint():
    """ONE function applies the scrubber; every capture consumer routes to it.

    ``_redact_turn_contents`` is where the scrubber is applied on the capture
    paths, and the consumers that must be covered are enumerated by
    name — so adding a persisting sink without routing it through the control
    reds here, and duplicating the control reds here too. ``_capture_turn_texts``
    (the shared stored-text definition, and what the #4675 client-side
    confirmation compares against) delegates to it.
    """
    assert _redact_call_owners(_REPO / "tortoise" / "sdk.py") == {
        # TWO adapters, and deliberately no third: ``_redact_turn_contents``
        # scrubs a conversation (the capture path's input shape) and
        # ``_redact_summary_strings`` scrubs an arbitrary payload (the v1
        # caller-supplied ``summary=``, which never passes through a
        # conversation). A new sink that calls the scrubber itself reds here.
        "_redact_turn_contents", "_redact_summary_strings"}
    assert _redact_call_owners(_REPO / "tortoise" / "hosted_api.py") == set(), (
        "the hosted lane must get the control through sdk (the capture helpers "
        "and the extractor), not by calling the scrubber itself")
    consumers = _functions_calling(_REPO / "tortoise" / "sdk.py",
                                  "_redact_turn_contents")
    assert consumers == {
        "_capture_turn_texts_with_redactions",   # the stored turn text
        "_materialize_session_source",           # the session :Source sink
        "_extract_session_llm",                  # the M2 extraction leg
        "_extract_session_v2",                   # the v2 extraction leg
        "_commit_session_v2",                    # the public commit sibling
        "_commit_session_v1",                    # ... and its v1 sibling
    }, consumers

    secret = "sk-proj-" + _fill(64)
    assert secret not in _capture_turn_texts(
        [{"role": "user", "content": secret}])[0]


# ── The local-spool decision, pinned ───────────────────────────────────────

def test_local_spool_keeps_the_raw_turn_by_decision(tmp_path):
    """DECISION (recorded on #4911): the LOCAL raw spool is OUT of scope.

    ``capture_spool`` is the user's own write-ahead log on the user's own
    machine, and its INPUT is the harness's session transcript
    (``~/.pi/agent/sessions/…/*.jsonl``, ``~/.claude/projects/…/*.jsonl``),
    which we do not own and which retains the bytes verbatim. Redacting only
    our copy would be a fidelity loss with no security gain, and it would place
    a cross-trust-domain control inside the domain it does not protect — while
    the acceptance criterion asks for the control at the SINGLE turn-write
    chokepoint. The boundary that matters is local -> hosted multi-tenant, and
    that is where the redaction sits.

    This test fails if the spool is redacted, on purpose: that is a scope
    change, and it must move with the decision recorded on the issue and in the
    PR body, not silently.
    """
    secret = "ghp_" + _fill(36)
    root = tmp_path / "spool"
    capture_spool.write_spool_entry(
        root,
        capture_spool.Snapshot(
            session_id="sess-4911-spool",
            turns=[{"role": "user", "content": f"token {secret}"}],
            source="test",
            machine_id="m1",
        ))
    stored_turns = capture_spool.read_spool_turns(root, "sess-4911-spool")
    blob = "\n".join(t.get("content", "") for t in stored_turns)
    assert secret in blob, (
        "the local spool was redacted — that reverses the recorded decision "
        "that the local raw store is out of scope for #4911; update the "
        "decision on the issue and in the PR body first")
    assert _redact_call_owners(_REPO / "tortoise" / "capture_spool.py") == set()


def test_unterminated_and_partial_shapes_do_not_fail_open():
    """The shapes a matcher that only knows well-formed input gets wrong.

    * A PEM header whose ``-----END …-----`` line was DELETED (one editor
      keystroke) must not store the key body verbatim — otherwise the whole
      rule is bypassable by the person pasting the key.
    * A PEM block whose END falls past the per-turn 5,000-char cut is the same
      case arriving through the cap.
    * The label grammar is wider than ``[A-Z ]``: the ssh.com/Tectia and DH
      headers are real private keys too.
    * Slack's app-level (``xapp-``) and config (``xoxe``) tokens are in the
      same credential class as ``xoxb-``.
    """
    body = "MIIEowIBAAKCAQEA" + _fill(200)
    unterminated = _pem("RSA PRIVATE KEY") + f"\n{body}"
    out, counts = redact_secrets(unterminated)
    assert body not in out, "an END-less PEM header stored the key body"
    assert counts.get("private_key") == 1

    # Truncated by the turn cap: the END line never reaches the scrubber.
    long_block = (_pem("OPENSSH PRIVATE KEY") + "\n" + _fill(6000)
                  + "\n" + _pem_end("OPENSSH PRIVATE KEY"))
    out2, counts2 = redact_secrets(long_block[:5000])
    assert counts2.get("private_key") == 1, "a cap-truncated PEM block escaped"
    # The whole header+body span is REPLACED (not merely prefixed): the marker
    # is the entire output, so none of the 5,000 stored characters survive.
    assert out2 == "[REDACTED:private_key]", out2[:80]

    for value in ("xapp-1-A0123456789-9876543210987-" + _fill(40),
                  "xoxe.xoxp-1-" + _fill(60)):
        out3, counts3 = redact_secrets(f"token {value}")
        assert value not in out3, value[:8]
        assert counts3.get("slack_token") == 1

    # Label families outside `[A-Z ]` — both are real PEM private keys.
    for label in ("SSH2 ENCRYPTED PRIVATE KEY", "X9.42 DH PRIVATE KEY"):
        out4, counts4 = redact_secrets(
            f"{_pem(label)}\n{body}\n{_pem_end(label)}")
        assert body not in out4, label
        assert counts4.get("private_key") == 1, label

    # A dangling header followed by ORDINARY text must not leak: the
    # fail-closed branch redacts from the header onward (over-redaction is the
    # safe direction), which is why it cannot be dropped from the rule.
    out5, counts5 = redact_secrets(f"key {_pem('EC PRIVATE KEY')}\n{body}")
    assert body not in out5
    assert counts5.get("private_key") == 1


def test_a_credential_touching_a_word_character_is_still_redacted():
    """The boundary is ``(?![A-Za-z0-9])``, not ``\b`` — ``_`` is a word char.

    Two distinct failures wore the same ``\b``: (a) a real token sitting
    against an underscore did not match at all, and (b) a greedy body could
    BACKTRACK to an internal ``-`` and replace only the token's prefix —
    leaving the secret body in cleartext while the count said it was redacted.
    Both are false assurances from a control whose whole job is to be trusted.
    """
    for case, kind, value in CASES:
        out, counts = redact_secrets(f"key {value}_suffix")
        assert value not in out, (
            f"{case}: still stored against an underscore — a word-boundary "
            "terminator let a greedy body backtrack to an internal `-`")
        assert counts.get(kind), f"{case}: not counted"

        # #4911 cycle 2: the boundary must hold on the LEADING side too. `_`
        # and `-` are body characters, so a real token can be glued straight
        # after one. Narrowing the lookbehind to exclude them was tried to buy
        # scan speed and silently stopped matching these — a LEAK, not a
        # tightening (measured `pre='_' -> {}` where the old rule gave
        # `{'jwt': 1}`). Pin both directions so the trade cannot be re-made.
        for pre in ("_", "-"):
            lead_out, lead_counts = redact_secrets(f"key {pre}{value}")
            assert value not in lead_out, (
                f"{case}: still stored after a leading {pre!r} — the "
                "lookbehind was narrowed past a real body character")
            assert lead_counts.get(kind), f"{case}: not counted after {pre!r}"

    # Context-anchored form: the JSON/YAML shape a pasted config actually has,
    # where the key's closing quote sits between the name and the separator.
    secret = _synth("wJalrXUtnFEMI", "/K7MDENG", "/bPxRfiCYEXAMPLEKEY")
    for text in (f'{{"aws_secret_access_key":"{secret}"}}',
                 f'aws_secret_access_key: "{secret}"',
                 f'{{"AWS_SECRET_ACCESS_KEY":"{secret}"}}'):
        out, counts = redact_secrets(text)
        assert secret not in out, text
        assert counts.get("aws_secret_access_key") == 1, text
        assert "aws_secret_access_key" in out.lower(), (
            "the anchor name is kept so the record stays diagnostic")

    # The exact backtracking case: the token continues past an internal `-`.
    slack_body = _fill(16)
    slack = _synth("xox", "b-", _fill(12), "-", slack_body, "_suffix")
    out, counts = redact_secrets(f"token {slack}")
    assert slack_body not in out, out
    assert counts == {"slack_token": 1}


# ── The false positives the issue recorded, and idempotency ────────────────

def test_anchored_shapes_do_not_redact_prose():
    """The measured #4911 false-positive class stays untouched.

    A naive ``CONTAINS 'sk-'`` census over production returned 193 nodes and
    every one was prose: ``risk-``, ``disk-``, ``task-`` all contain ``sk-``.
    The token-start anchor is what separates a shape match from a substring
    match; a regression to an un-anchored ``sk-[A-Za-z0-9_-]{16,}`` reds here.
    """
    prose = [
        "The risk-assessment of the disk-utilisation-report is on the "
        "task-list-of-long-items, and the disk-usage metrics look fine.",
        "A risk-free task-oriented disk-backed pipeline.",
        "AKIA and AIza are prefixes; so are ghp_, xoxb- and jv_live_.",
        "the bearer of bad news in a long-standing dispute",
        # Measured false positive of an earlier revision: at a 20-char body
        # floor the generic `sk-` rule matched this scikit-learn abbreviation.
        "Run the sk-learn-pipeline-version-2 experiment after the rerun.",
        # The production false-positive class that got the (now removed)
        # `connection_url` rule its first rewrite: 167 nodes of this shape in the
        # live graph, every one this repo's own documented dev URI.
        "docker://:falkordb@localhost:6379/tortoise_test_matrix",
        "'https://@','https://:443','https://host?','https://user:pass@'",
        # A password-bearing connection URL is NOT covered — the rule was removed
        # because no anchor made it both precise AND complete (see
        # tortoise/security.py and the issue). Pinned as a DECISION, so turning
        # this red is a deliberate change to that decision, not a surprise.
        "postgres://admin:s3cr3tpassword@db.internal:5432/prod",
    ]
    for text in prose:
        assert redact_secrets(text) == (text, {}), text
    for bare in ("AKIA", "AIza", "ghp_", "xoxb-", "eyJhbGci"):
        assert redact_secrets(bare) == (bare, {})


def test_a_capped_scan_truncates_the_text_it_returns():
    """``cap`` must bound the RESULT, not just the text that was scanned.

    The bug this pins: with the credential past the cap, the scanned prefix
    matched nothing and the ORIGINAL uncapped turn was returned — so the
    credential was forwarded un-scanned to whoever asked for a capped scan
    (at the ``commit_session`` call site, straight into the extractor prompt
    and off to the provider). A cap that does not cap is a leak, not a
    performance knob.
    """
    secret = "sk-proj-" + _fill(64)
    long_turn = "x" * 5500 + " " + secret + " tail"
    out, counts = _redact_turn_contents(
        [{"role": "user", "content": long_turn}], cap=5000)
    assert len(out[0]["content"]) <= 5000, "the cap did not bound the result"
    assert secret not in out[0]["content"]
    assert counts == {}
    # ... for EVERY input type, not just str: a non-str turn is coerced for the
    # scan, so the cut has to bound the coerced result too (a 100k-item list
    # came back whole, with the credential still in it).
    out, counts = _redact_turn_contents(
        [{"role": "user", "content": ["z"] * 100_000 + [secret]}], cap=5000)
    assert isinstance(out[0]["content"], str)
    assert len(out[0]["content"]) <= 5000
    assert secret not in out[0]["content"]
    # A turn that needs no cut is passed through untouched (same object).
    turn = {"role": "user", "content": 5}
    out, counts = _redact_turn_contents([turn], cap=10)
    assert out[0] is turn and counts == {}
    # A match inside the window still redacts and still counts.
    out, counts = _redact_turn_contents(
        [{"role": "user", "content": secret + "\n" + "x" * 6000}], cap=5000)
    assert secret not in out[0]["content"]
    assert counts == {"openai_api_key": 1}


def test_a_caller_supplied_summary_is_scrubbed():
    """The v1 ``summary=`` argument never meets the conversation scrub.

    It is rendered into ``construct_graph``'s prompt AND POSTed to
    ``/v1/sessions/commit`` (whose writes have no scrubber), so the credential
    has the same route to the graph as a pasted one — pinned here, including
    that the caller's own object is not mutated and the shape is preserved.
    """
    secret = "glpat-" + _fill(24)
    caller = {"points": [{"content": f"token {secret}"}],
              "operators": ("plain",), "n": 3, "flag": True, "none": None}
    scrubbed = _redact_summary_strings(caller)
    assert secret not in str(scrubbed)
    assert "[REDACTED:gitlab_token]" in scrubbed["points"][0]["content"]
    assert scrubbed["n"] == 3 and scrubbed["flag"] is True
    assert scrubbed["none"] is None
    assert isinstance(scrubbed["operators"], tuple)
    assert secret in caller["points"][0]["content"], "caller object mutated"

    # KEYS are rendered into the prompt by json.dumps, so they are scrubbed too.
    keyed = _redact_summary_strings({"session": {"summary": "hi"},
                                     secret: "innocuous"})
    assert secret not in str(keyed)
    assert "[REDACTED:gitlab_token]" in list(keyed)

    # A cyclic or very deep summary MUST NOT turn into an unhandled
    # RecursionError: before this delta such a payload reached construct_graph,
    # whose json.dumps ValueError was swallowed. A public method may not crash.
    cyclic: dict = {"a": 1}
    cyclic["self"] = cyclic
    assert isinstance(_redact_summary_strings(cyclic), dict)
    deep: dict = {"a": 1}
    for _ in range(200):
        deep = {"a": deep}
    assert isinstance(_redact_summary_strings(deep), dict)


def test_every_rule_scans_linearly_on_adversarial_input():
    """No rule may re-scan the tail from every candidate start (T7, #5296).

    ``test_the_jwt_rule_scans_linearly_on_adversarial_input`` was the original
    name; it is generalised because TWO rules shipped superlinear the same way
    and only one of them was covered (#4911 cycles 1 and 2). Three families,
    each a run whose candidate cannot complete its required delimiter:

      * ``("eyJ" + "A"*10) * n`` — the shipped input. Every ``eyJ`` after the
        first is preceded by ``A``, so the lookbehind rejects it before any body
        work: it CANNOT discriminate, which is why the historical test passed
        with the guard reverted.
      * ``("_eyJ" + "A"*50 + "_") * n`` — the ``jwt`` first segment is what
        made this quadratic: unbounded, every candidate consumed the whole run
        (0.85 s @55k → 2.83 s @110k → 10.50 s @220k). Bounding that segment at
        512 characters — NOT narrowing the lookbehind, which dropped recall —
        makes it linear.
      * ``"-----BEGIN " * n`` — the ``private_key`` label class in front of a
        REQUIRED ``PRIVATE KEY-----`` suffix: unbounded it consumed the tail and
        backtracked for the suffix at every start (0.018 s @11k → 1.276 s @44k
        → 5.752 s @88k). Bounded at 40 characters it is linear.

    Scaling, not just a wall-clock threshold: a generous absolute bound alone
    cannot certify linearity (and did not — the reverted `jwt` rule passed the
    shipped 5.0 s bound). 2x input may not cost more than 3x time.
    """
    import time

    families = (
        ("shipped eyJ", lambda n: ("eyJ" + "A" * 10) * n),
        ("glued _eyJ", lambda n: ("_eyJ" + "A" * 50 + "_") * n),
        ("PEM label, no suffix", lambda n: "-----BEGIN " * n),
    )
    def scan(name: str, build, n: int) -> float:
        text = build(n)
        started = time.perf_counter()
        redacted, counts = redact_secrets(text)
        elapsed = time.perf_counter() - started
        # None of these runs contains a complete credential: nothing may be
        # redacted and the text must come back byte-identical. That also
        # keeps the timing about the SCAN, not about replacement work.
        assert counts == {}, f"{name}: redacted a non-credential"
        assert redacted == text, f"{name}: text was modified"
        return elapsed

    for name, build in families:
        one = scan(name, build, 20_000)
        two = scan(name, build, 40_000)
        assert two < max(one * 3.0, 1.0), (
            f"{name}: scan does not scale linearly — {one:.3f}s for 20k units "
            f"→ {two:.3f}s for 2x input (a rule is re-scanning the tail from "
            "every candidate start)")

    # And the recall half of the same trade: bounding per-candidate work must
    # NOT be bought by narrowing a lookbehind past a real body character. A JWT
    # glued after `_`/`-` is a real credential (cycle 1 dropped it).
    jwt = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
           ".eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "A" * 43)
    for pre in ("_", "-", " ", "=", '"'):
        out, counts = redact_secrets(f"token {pre}{jwt}")
        assert jwt not in out, (
            f"a JWT glued after {pre!r} leaked — the lookbehind was narrowed "
            "past a real base64url body character to buy scan speed")
        assert counts.get("jwt") == 1, pre


def test_redaction_is_idempotent_under_the_capture_double_pass():
    """The capture path scrubs the same turn twice (embedding batch + write).

    A marker must not be re-matched or double-counted, or the stored count and
    the text would depend on how many times the helper ran.
    """
    turn = ("[" + "user" + "] use sk-proj-" + _fill(64)
            + " and Authorization: Bearer " + _fill(32))
    once, c1 = redact_secrets(turn)
    twice, c2 = redact_secrets(once)
    assert once == twice
    assert c1 == {"openai_api_key": 1, "bearer_token": 1}
    # Second pass: unchanged text AND no new counts — the marker is not a
    # re-match, so the writer's recomputation cannot inflate the recorded count.
    assert c2 == {}
    assert redact_secrets(once)[0].count("[REDACTED:") == 2
