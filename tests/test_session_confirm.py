"""#4675 — confirming a capture that was REFUSED but had already committed.

``POST /v1/sessions`` can write the Session and its turns and still answer with
a retryable refusal: the transport wait bound ABANDONS its handler rather than
cancelling it, so the work completes after the 504 is sent. The client's
terminality rule therefore cannot be "the status was retryable ⇒ nothing
committed" — it must ask the server, and it must ask the right question.

These tests pin the QUESTION (the posted rows, by id AND by stored text — not
the Session row, and not the id set alone) and the ANSWER for every
inconclusive read (defer, never file, never discard).
"""
from __future__ import annotations

import itertools

import pytest

from tortoise.session_confirm import (
    FILED,
    TURNS_MISSING,
    UNEXTRACTED,
    UNKNOWN,
    confirm_capture,
    expected_turns,
    session_extracted,
    turn_point_id,
    turn_points,
)
from tortoise.session_verify import _ApiError

TURNS = [{"role": "user", "content": "ship it"},
         {"role": "assistant", "content": "on it"},
         {"role": "user", "content": "unrelated"}]

SESSION = "s1"


def _detail(turns: list[dict] = TURNS, extracted: int = 2, *,
            session_id: str = SESSION, contents: list[str] | None = None,
            roles: list[str] | None = None) -> dict:
    """A ``GET /v1/sessions/<id>`` payload in the shape the SERVER RETURNS.

    The server serves the role as its own field and the content with the
    ``[role] `` prefix STRIPPED (``hosted_api.get_session_detail``), so this
    fake builds its rows from the ORIGINAL turn dicts and never echoes the
    writer's ``"[role] text"`` string. A fake that echoes the writer makes a
    format-mismatched comparison look correct while the production path never
    matches — the defect this shape exists to catch.

    ``contents`` overrides the BODY (already stripped) and ``roles`` the role
    field, for mismatch cases.
    """
    bodies = (contents if contents is not None
              else [t["content"] for t in turns])
    row_roles = (roles if roles is not None
                 else [t["role"] for t in turns])
    rows = [{"id": turn_point_id(session_id, i), "role": row_roles[i],
             "content": bodies[i]} for i in range(len(turns))]
    return {"id": session_id, "turns": len(turns), "extracted": extracted,
            "turn_points": rows}


def _reader(responses):
    """A reader yielding the given responses in order; an entry may be an
    exception INSTANCE to raise, ``None`` for a 404, or a detail dict."""
    seq = list(responses)
    calls: list[dict] = []

    def read(api_url, api_key, session_id, *, timeout=None):
        calls.append({"api_url": api_url, "session_id": session_id,
                      "timeout": timeout})
        item = seq.pop(0) if seq else None
        if isinstance(item, BaseException):
            raise item
        return item

    read.calls = calls
    return read


def _confirm(reader, turns=TURNS, **kw):
    kw.setdefault("sleep", lambda _s: None)
    return confirm_capture(reader, "u", "k", SESSION, turns, **kw)


def test_the_posted_rows_and_extraction_is_filed():
    assert _confirm(_reader([_detail()])) == FILED


def test_durable_turns_without_extraction_is_unextracted():
    """Extraction runs AFTER the turn write, so the confirmation can prove the
    turns while the extraction the bound abandoned is still running. The SPOOL
    may terminalise on that; the import RECEIPT may not (#4188)."""
    assert _confirm(_reader([_detail(extracted=0)])) == UNEXTRACTED


def test_the_same_ids_with_DIFFERENT_TEXT_are_not_filed():
    """The P0 this guards: the ids are POSITIONAL, so a session id reused for
    different content with the same number of turns — a compaction, a branch, a
    MAX_SESSION_TURNS window shift — matches on ids while the new text is
    nowhere on the server. Filing on that loses the only copy.

    MUTATION THAT REDS THIS: compare `set(turn_points)` to `set(expected)`
    instead of the whole `{id: (role, text)}` mapping.
    """
    other = ["a completely different turn", "and another", "and a third"]
    assert _confirm(_reader([_detail(contents=other)])) == TURNS_MISSING


def test_a_SERVED_row_is_matched_across_the_stripped_role_prefix():
    """The format boundary, end to end — the defect that made this whole
    confirmation inert on the first attempt.

    The writer stores `"[user] ship it"`, but `GET /v1/sessions/<id>` serves
    `role="user", content="ship it"`. A comparison built on the WRITER's string
    never matches a real response, so every post-commit refusal would keep
    deferring while tests whose fakes echoed the writer stayed green.

    MUTATION THAT REDS THIS: build `expected_turns` from
    `_capture_turn_texts` without splitting through the server's own inverse.
    """
    assert _confirm(_reader([_detail()])) == FILED
    # The fake's rows really are the served shape (role separate, prefix gone).
    rows = _detail()["turn_points"]
    assert rows[0] == {"id": f"{SESSION}_t0", "role": "user",
                       "content": "ship it"}, rows[0]


def test_a_role_only_change_is_not_filed():
    """The role is served SEPARATELY, so the comparison must carry it too — an
    id+body match with the wrong speaker is a different transcript.

    MUTATION THAT REDS THIS: drop `role` from `turn_points` (compare only the
    stripped body).
    """
    roles = ["assistant", "assistant", "user"]
    assert _confirm(_reader([_detail(roles=roles)])) == TURNS_MISSING


def test_one_changed_turn_is_not_filed():
    """Content equality is per row, not per session."""
    swapped = ["ship it", "on it", "edited"]
    assert _confirm(_reader([_detail(contents=swapped)])) == TURNS_MISSING


def test_more_turns_than_posted_is_not_filed():
    grown = [*TURNS, {"role": "assistant", "content": "fourth"}]
    reader = _reader([_detail(grown)])
    assert _confirm(reader, turns=TURNS) == TURNS_MISSING
    # The grown set is a SUPERSET — accepting it would mean a concurrent
    # capture's rows satisfy our POST.
    assert len(reader.calls) == 1


def test_a_partial_turn_set_is_not_filed():
    assert _confirm(_reader([_detail(turns=TURNS[:1])])) == TURNS_MISSING


@pytest.mark.parametrize("response", [
    None,                                       # 404: not MERGEd yet
    _ApiError("HTTP 504", status=504),          # a refused READ
    _ApiError("HTTP 429", status=429),
    TimeoutError("read timed out"),             # transport
])
def test_every_inconclusive_read_defers(response):
    """UNKNOWN never files. The abandoned handler may simply not have written
    yet, and a read we could not complete is not evidence either way."""
    reader = _reader([response, response])
    assert _confirm(reader) == UNKNOWN
    assert len(reader.calls) == 2


def test_a_404_that_appears_is_filed_within_the_window():
    """The bounded re-read exists for exactly this: the write is racing us."""
    reader = _reader([None, _detail()])
    assert _confirm(reader) == FILED
    assert len(reader.calls) == 2


def test_a_transient_read_failure_then_success_is_filed():
    reader = _reader([TimeoutError("read timed out"), _detail()])
    assert _confirm(reader) == FILED


def test_no_turns_posted_is_never_confirmed():
    reader = _reader([_detail(turns=[])])
    assert _confirm(reader, turns=[]) == UNKNOWN
    assert reader.calls == []


def test_a_missing_session_id_is_never_confirmed():
    reader = _reader([_detail()])
    assert confirm_capture(reader, "u", "k", "", TURNS,
                           sleep=lambda _s: None) == UNKNOWN
    assert reader.calls == []


@pytest.mark.parametrize("bad", [
    {"turn_points": None},
    {"turn_points": "not-a-list"},
    {"turn_points": [{"id": 7}, {}, None, "x"]},
    {},
    "not-a-dict",
    None,
])
def test_a_malformed_detail_is_unknown_not_filed(bad):
    reader = _reader([bad, bad])
    assert _confirm(reader) == UNKNOWN


def test_a_row_whose_content_is_unreadable_cannot_match():
    """A row we cannot read the text of must never satisfy the comparison."""
    rows = [{"id": turn_point_id(SESSION, i), "role": "user",
             "content": None} for i in range(len(TURNS))]
    assert turn_points({"turn_points": rows})[turn_point_id(SESSION, 0)] != \
        expected_turns(SESSION, TURNS)[turn_point_id(SESSION, 0)]
    assert _confirm(_reader([{"turn_points": rows}])) == TURNS_MISSING


def test_the_sleep_runs_between_attempts_only():
    slept: list[float] = []
    reader = _reader([None, None])
    _confirm(reader, attempts=2, delay_s=0.25, sleep=slept.append)
    assert slept == [0.25], slept


def test_the_read_timeout_is_the_short_one_not_the_post_timeout():
    reader = _reader([_detail()])
    _confirm(reader, read_timeout_s=7.5)
    assert reader.calls[0]["timeout"] == 7.5


def test_the_default_budget_fits_inside_a_hook_that_already_waited_on_the_post():
    """The confirmation runs AFTER a POST that may have spent its own 30s, and
    the Claude SessionEnd hook that runs `session capture` synchronously is
    cancelled at 60s. Keep the worst case small."""
    from tortoise import session_confirm as sc
    worst = (sc.DEFAULT_ATTEMPTS * sc.DEFAULT_READ_TIMEOUT_S
             + (sc.DEFAULT_ATTEMPTS - 1) * sc.DEFAULT_DELAY_S)
    assert worst <= 15.0, worst


# ── the format contract this rests on ────────────────────────────────────


def test_the_turn_id_matches_the_servers_own_writer_format():
    """Drift guard. `{session_id}_t{i}` is the writer's per-row idempotency key;
    if the server's derivation changes, every confirmation silently defers.

    Asserted against the LIVE symbol and the writer's CODE, not the module
    text. The previous version grepped `tortoise/sdk.py` for the literal
    ``f"{session_id}_t{i}"``, which that module now carries only inside
    DOCSTRINGS — so it passed on prose and could not red when the derivation
    changed. The AST half below strips the matched function's own leading
    docstring before matching, so only its executable text can satisfy it.
    """
    import ast
    import pathlib

    from tortoise.sdk import _capture_turn_id

    # 1. The live derivation, compared value-for-value with the client's copy.
    assert turn_point_id("abc", 0) == "abc_t0"
    assert turn_point_id("abc", 12) == "abc_t12"
    for i in (0, 1, 12, 999):
        assert _capture_turn_id("abc", i) == turn_point_id("abc", i), (
            "sdk._capture_turn_id and session_confirm.turn_point_id have "
            "drifted — every confirmation would silently defer")

    # 2. The writer's CODE still formats `{session_id}_t<index>`. Docstrings are
    #    stripped, so the module's own prose cannot satisfy this (the vacuity
    #    the old grep had).
    tree = ast.parse(
        (pathlib.Path(__file__).resolve().parents[1]
         / "tortoise" / "sdk.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "_capture_turn_id")
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)
            and isinstance(fn.body[0].value.value, str)):
        fn.body = fn.body[1:] or [ast.Pass()]
    code = ast.unparse(fn)
    assert "session_id}_t" in code, (
        "sdk._capture_turn_id no longer formats `{session_id}_t<index>` — "
        "session_confirm.turn_point_id must follow it")


def test_the_stored_text_matches_the_servers_own_writer_definition():
    """The content comparison must describe what the server SERVES, so it is
    derived from the writer's shared helper AND split by the reader's shared
    inverse rather than mirrored.

    #4897 round 5: BOTH sides window first. ``expected_turns`` (the client's
    confirmation) and the server's writer both feed ``_capture_turn_texts`` a
    ``_capture_turn_window``'d conversation — the stored-text helper is
    redaction-only and does NOT window, so the raw fixture must be windowed here
    exactly as ``expected_turns`` windows it.

    MUTATION THAT REDS THIS: return the raw `"[role] text"` string (the
    pre-fix shape) — the expectation stops matching a served row.
    """
    from tortoise.sdk import (
        _capture_turn_role_text,
        _capture_turn_texts,
        _capture_turn_window,
    )
    odd = [{"role": "user", "content": "x"}, {"role": None, "content": None},
           {"role": 7, "content": 42}, {"content": "no role"},
           {"role": "assistant", "content": "y" * 6000},
           {"role": "user", "content": "  leading space"}]
    windowed = _capture_turn_window([dict(t) for t in odd])
    assert expected_turns("abc", odd) == {
        f"abc_t{i}": _capture_turn_role_text(text)
        for i, text in enumerate(_capture_turn_texts(windowed))}


def test_the_role_split_is_the_inverse_of_the_writer_and_the_servers_own():
    """The round trip, and the reason the split is shared: `get_session_detail`
    serves `(role, body)` from the stored `"[role] body"`, so the client must
    invert the SAME way — the server routes the stored text through
    `_capture_turn_role_text` itself, so the pairs below are exactly what it
    serves for this window.

    The pairs are pinned LITERALLY, not by re-applying the split here. A mirror
    that ran `_CAPTURE_ROLE_PREFIX` (or a hand-copied twin of it) over the text
    and compared would be `f(x) == f(x)` and could never fail — the anti-pattern
    already refuted in `tests/test_capture_redaction_sinks_5445.py`. Pinning the
    output instead makes drift on either side red here for real.
    """
    from tortoise.sdk import _capture_turn_role_text, _capture_turn_texts

    stored = _capture_turn_texts([
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "  padded"},
        {"role": "user", "content": "[user] nested"},
        {"role": "", "content": "empty role"},
        {"content": "no role key"},
    ])
    assert [_capture_turn_role_text(text) for text in stored] == [
        ("user", "hi"),
        ("assistant", "padded"),
        ("user", "[user] nested"),
        # ⛔ The empty role must recover as ``""``, NOT fold into the whole
        # string: a ``+`` quantifier mis-splits ``"[] empty role"`` into
        # ``("unknown", "[] empty role")`` and corrupts the served turn with a
        # stray ``[] `` prefix (round-12 P0).
        ("", "empty role"),
        ("unknown", "no role key"),
    ], stored


def test_session_extracted_is_total_over_odd_values():
    assert session_extracted({"extracted": 3}) == 3
    assert session_extracted({"extracted": -4}) == 0
    assert session_extracted({"extracted": True}) == 0
    assert session_extracted({"extracted": "3"}) == 0
    assert session_extracted({"extracted": None}) == 0
    assert session_extracted(None) == 0


def test_the_no_receipt_set_is_independent_of_the_retry_classifier():
    """The two rules answer DIFFERENT questions and both answers must hold.

    #4614 made 402 `retry` (a quota refusal is transient: its `est` is computed
    from the INCOMING capture, so the identical capture succeeds once a node is
    freed), so the only copy is KEPT in the spool. #4675's Task-15 acceptance
    line names 402 in the no-receipt set (a refusal must still exit 1 with no
    local receipt).

    Nothing in the classifier expresses the second, so the receipt gates must
    name the set themselves — deriving it from `classify_failure` is the defect
    this pins: 402 stopped being excluded the moment it became `retry`, and a
    durable 402 would then mint a receipt for a refusal.

    MUTATION THAT REDS THIS: replace `_NO_RECEIPT_REFUSAL_STATUSES` with the
    retry-classifier (any expression of the form `status not in ... and
    classify_failure(...) == "retry"` alone), or drop 402 from the set.
    """
    from tortoise.__main__ import _NO_RECEIPT_REFUSAL_STATUSES
    from tortoise.capture_spool import classify_failure

    # (1) The classifier's answer, which governs the SPOOL: keep the only copy.
    assert classify_failure(402) == "retry"
    # (2) The recorded rule's answer, which governs the RECEIPT: exit 1, none.
    assert 402 in _NO_RECEIPT_REFUSAL_STATUSES
    # Both are true at once — that is the point, not a contradiction.
    assert classify_failure(402) == "retry" and 402 in _NO_RECEIPT_REFUSAL_STATUSES

    # The recorded set is EXACTLY the rule's three statuses: 403/402/503. A
    # transient status outside it (429, 5xx, a network failure) must stay
    # confirmable, or #4675's own fix is lost.
    assert sorted(_NO_RECEIPT_REFUSAL_STATUSES) == [402, 403, 503]
    for status in (429, 500, 502, 504, None):
        assert status not in _NO_RECEIPT_REFUSAL_STATUSES, status


# ── #5394 — the client/server redaction-table skew must not defer forever ──

SECRET = "sk-proj-" + "A" * 64


def test_an_older_client_against_an_upgraded_server_still_confirms(monkeypatch):
    """#5394 — the redaction table is DEPLOYED INDEPENDENTLY on the two sides.

    Model an OLDER client (installed before a rule the server now has) by
    emptying its table. The served turn carries a marker the client would never
    have produced, so the EXACT comparison fails. The served body is still the
    client's RAW body with a redaction span, so the verdict must be TERMINAL.
    Returning TURNS_MISSING here is the defect: that verdict is retryable, so
    ``capture_spool`` defers the entry with backoff forever and ``session
    drain`` never reaches ``filed N, deferred 0``.

    MUTATION THAT REDS THIS: delete the ``_turns_survive_redaction_skew`` branch
    from ``confirm_capture`` (or restrict it to the client's own expectation).
    """
    import tortoise.sdk as sdk
    monkeypatch.setattr(sdk, "redact_secrets", lambda text: (text, {}))
    rows = [{"role": "user", "content": f"key: {SECRET}"}]
    served = _detail(turns=rows, contents=["key: [REDACTED:openai_api_key]"])
    # The client's own model really does differ (the skew is live)...
    assert expected_turns(SESSION, rows)[f"{SESSION}_t0"] == \
        ("user", f"key: {SECRET}")
    # ...and the confirmation still terminalises.
    assert _confirm(_reader([served]), turns=rows) == FILED


def test_an_upgraded_client_against_an_older_server_still_confirms(monkeypatch):
    """The OTHER direction: the client knows a shape the server stored VERBATIM.

    A rollback, a mixed fleet, or a rule the client carries first. The client's
    expectation holds a marker the server never wrote; the served body is the
    raw text. Comparing against the client's RAW model matches — which is why
    the tolerance is built on the raw pair and not on the redacted expectation.
    """
    import tortoise.sdk as sdk

    def redact(text):
        if "TOPSEKRIT" in text:
            return (text.replace("TOPSEKRIT", "[REDACTED:test_kind]"),
                    {"test_kind": text.count("TOPSEKRIT")})
        return text, {}

    monkeypatch.setattr(sdk, "redact_secrets", redact)
    rows = [{"role": "user", "content": "note TOPSEKRIT here"}]
    served = _detail(turns=rows)                    # stored verbatim
    assert expected_turns(SESSION, rows)[f"{SESSION}_t0"] == \
        ("user", "note [REDACTED:test_kind] here")
    assert _confirm(_reader([served]), turns=rows) == FILED


def test_a_marker_cannot_excuse_a_difference_outside_its_span():
    """The loosened guard must still reject a DIFFERENT transcript.

    A marker may absorb the one span the table disagrees about; it may not
    absorb the rest of the body. The suffix differs, so this is not our turn.

    MUTATION THAT REDS THIS: make the marker wildcard unbounded on both sides
    (e.g. compare ``set(turn_points)`` or wildcard the whole body).
    """
    rows = [{"role": "user", "content": f"key: {SECRET} keep"}]
    served = _detail(turns=rows,
                     contents=["key: [REDACTED:openai_api_key] CHANGED"])
    assert _confirm(_reader([served]), turns=rows) == TURNS_MISSING


def test_a_marker_must_stand_for_at_least_one_raw_character():
    """A marker replaces a NON-EMPTY span (every ``_SECRET_SHAPES`` rule has a
    body floor of at least one character), so an empty raw body cannot be
    covered by a marker-only served body.

    MUTATION THAT REDS THIS: use ``.*`` (zero-or-more) at the marker instead of
    the non-empty span, which would let a bare ``[REDACTED:…]`` match any raw
    text — including one it never redacted.
    """
    rows = [{"role": "user", "content": ""}]
    served = _detail(turns=rows, contents=["[REDACTED:openai_api_key]"])
    assert _confirm(_reader([served]), turns=rows) == TURNS_MISSING


def test_the_skew_tolerance_still_rejects_a_grown_or_missing_set():
    """The key SET must be identical: the ids are positional, so a superset or
    subset is a different transcript (#4675). Marker tolerance is per row and
    applies only after the sets already agree."""
    rows = [{"role": "user", "content": f"key: {SECRET}"}]
    grown = _detail(turns=[*rows, {"role": "assistant", "content": "extra"}],
                    contents=["key: [REDACTED:openai_api_key]", "extra"])
    assert _confirm(_reader([grown]), turns=rows) == TURNS_MISSING
    two = [*rows, {"role": "assistant", "content": "second"}]
    partial = _detail(turns=rows,
                      contents=["key: [REDACTED:openai_api_key]"])
    assert _confirm(_reader([partial]), turns=two) == TURNS_MISSING


def test_a_redacted_ROLE_survives_the_skew_too(monkeypatch):
    """The role is scrubbed and delimiter-neutralized by the same writer, so it
    is version-sensitive too. A redacted role is SERVED as
    ``(REDACTED:<kind>)`` (square brackets never survive the neutralization),
    and that form must be recognised."""
    import tortoise.sdk as sdk
    monkeypatch.setattr(sdk, "redact_secrets", lambda text: (text, {}))
    rows = [{"role": f"sk-proj-{'A' * 64}", "content": "hi"}]
    served = _detail(turns=rows, roles=["(REDACTED:openai_api_key)"])
    assert _confirm(_reader([served]), turns=rows) == FILED


def test_a_PAREN_marker_in_a_BODY_is_literal_not_a_wildcard():
    """Only the ROLE is delimiter-neutralized; a body's marker is always
    ``[REDACTED:<kind>]``. Treating ``(REDACTED:…)`` as a marker in a body
    would wildcard text the user literally typed."""
    rows = [{"role": "user", "content": "(REDACTED:foo) secret text"}]
    served = _detail(turns=rows, contents=["(REDACTED:foo) DIFFERENT"])
    assert _confirm(_reader([served]), turns=rows) == TURNS_MISSING


def test_the_raw_model_is_the_stored_text_before_the_client_scrub():
    """Drift guard: ``raw_windowed_turns`` is the writer's frame BEFORE the
    client's scrub, and ``expected_turns`` is the same frame AFTER it. The two
    must differ exactly by redaction — which is what makes the tolerant branch
    reachable on a skew and inert otherwise."""
    from tortoise.session_confirm import raw_windowed_turns

    rows = [{"role": "user", "content": f"key: {SECRET}"}]
    assert raw_windowed_turns(SESSION, rows)[f"{SESSION}_t0"] == \
        ("user", f"key: {SECRET}")
    assert expected_turns(SESSION, rows)[f"{SESSION}_t0"] == \
        ("user", "key: [REDACTED:openai_api_key]")


@pytest.mark.parametrize("raw,stored,expected", [
    ("abc", "abc", True),                                        # identity
    ("key sk-proj-AAAA", "key [REDACTED:openai_api_key]", True),  # one span
    ("sk-proj-AAAA tail", "[REDACTED:openai_api_key] tail", True),
    ("head sk-proj-AAAA", "head [REDACTED:openai_api_key]", True),
    ("", "[REDACTED:openai_api_key]", False),                     # non-empty
    ("x", "[REDACTED:openai_api_key]", True),
    ("key sk-proj-AAAA keep", "key [REDACTED:openai_api_key] CHANGED", False),
    ("aa", "a", False),                                           # trailing raw
    ("ab", "a", False),
    ("a", "[REDACTED:x][REDACTED:y]", False),                     # two spans
    ("ab", "[REDACTED:x][REDACTED:y]", True),
    ("abc", "abd", False),                                        # no marker
])
def test_the_redaction_tolerant_predicate(raw, stored, expected):
    from tortoise.session_confirm import (
        _BODY_REDACTION_MARKER,
        _stored_is_raw_with_markers,
    )
    assert _stored_is_raw_with_markers(
        raw, stored, _BODY_REDACTION_MARKER) is expected


def test_the_tolerant_predicate_matches_its_own_property_exhaustively():
    """The hand-rolled walk is an OPTIMISATION of one property, so it is
    checked against that property's direct transcription — markers become
    ``.+``, literals are escaped, and the whole must ``fullmatch`` — over an
    exhaustive small domain. A disagreement here means the optimisation is
    wrong, and the exhaustive domain is small enough to keep the check cheap.
    """
    import re

    from tortoise.session_confirm import (
        _BODY_REDACTION_MARKER,
        _stored_is_raw_with_markers,
    )

    def reference(raw: str, stored: str) -> bool:
        if raw == stored:
            return True
        if _BODY_REDACTION_MARKER.search(stored) is None:
            return False
        parts: list[str] = []
        last = 0
        for mt in _BODY_REDACTION_MARKER.finditer(stored):
            parts.append(re.escape(stored[last:mt.start()]))
            parts.append(".+")
            last = mt.end()
        parts.append(re.escape(stored[last:]))
        return re.fullmatch("".join(parts), raw, re.DOTALL) is not None

    def strings(alpha: str, maxlen: int):
        for n in range(maxlen + 1):
            for tup in itertools.product(alpha, repeat=n):
                yield "".join(tup)

    marker = "[REDACTED:k]"
    for raw in strings("ab", 6):
        for stored in strings("ab", 6):
            assert _stored_is_raw_with_markers(
                raw, stored, _BODY_REDACTION_MARKER) is reference(raw, stored), \
                (raw, stored)
        for n in range(4):
            for tup in itertools.product([marker, "a", "", "b"], repeat=n):
                stored = "".join(tup)
                assert _stored_is_raw_with_markers(
                    raw, stored, _BODY_REDACTION_MARKER) is reference(raw, stored), \
                    (raw, stored)
