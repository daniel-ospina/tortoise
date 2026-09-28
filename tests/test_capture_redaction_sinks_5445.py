"""Follow-up security fixes on the #4911 capture-path redaction control.

Three defects, each a different flavour of fail-open in a control that exists to
fail closed:

* **#5445** — ``_redact_turn_contents`` scrubbed ``turn["content"]`` only, while
  the sibling ``role`` is caller-controlled (the hosted model takes
  ``conversation: list[dict]`` with no role validation) and is persisted as
  TEXT by THREE sinks: the ``[role] content`` stored turn,
  ``:Point.speaker`` and the session ``:Source`` transcript. A credential in
  ``role`` reached the multi-tenant graph while the receipt reported
  ``capture_redactions: 0``.
* **#5446** — ``_redact_summary_strings`` returned a subtree VERBATIM past depth
  64, so a deeply nested credential reached ``construct_graph``'s prompt
  (``json.dumps``) unscanned.
* **#5472** — two latent contract holes in the same helpers:
  ``_redact_turn_contents(cap=0)`` appended the ORIGINAL untruncated turn, and
  ``redact_secrets`` returned a non-``str`` first element for non-``str`` input
  against its own annotation.

Kept out of ``tests/test_capture_secret_redaction_4911.py`` deliberately: a
sibling branch edits that file, so the new coverage lives here.
"""
from __future__ import annotations

import pytest

from tortoise.sdk import (
    _CAPTURE_TURN_CAP,
    TortoiseSDK,
    _capture_turn_role_text,
    _capture_turn_window,
    _redact_summary_strings,
    _redact_turn_contents,
)
from tortoise.security import redact_secrets

_ALNUM = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"


def _fill(n: int) -> str:
    return (_ALNUM * (n // len(_ALNUM) + 1))[:n]


def _secret() -> str:
    """A GitHub-classic-shaped credential, assembled at RUNTIME.

    A literal token must not appear contiguously in the source: GitHub push
    protection refuses the push over it (observed on the sibling #4911 file),
    and a secret scanner would flag the file on every future push. Joining at
    runtime leaves the value under test byte-identical (same ``_synth`` trick
    the #4911 file documents).
    """
    return "".join(("ghp_", _fill(36)))


def _keyless(monkeypatch) -> None:
    """No LLM extraction: the turns are written by the mechanical loop under
    test, and a keyless capture cannot make a network call (same seam as
    ``tests/test_capture_secret_redaction_4911.py``)."""
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


def _stored(sdk: TortoiseSDK, session_id: str) -> dict[str, tuple[str, str]]:
    """``id -> (content, speaker)`` for the stored capture turns."""
    rows = sdk._get_proj().g.query(
        "MATCH (t:Point) WHERE t.id STARTS WITH $p "
        "RETURN t.id, t.content, t.speaker ORDER BY t.id",
        params={"p": f"{session_id}_t"}).result_set
    return {r[0]: (r[1], r[2]) for r in rows}


def _source_blob(sdk: TortoiseSDK, session_id: str) -> str:
    """Every turn-derived string the capture persists on the session ``:Source``
    (summary + topics + the FTS field)."""
    rows = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) "
        "RETURN s.summary, s.topics, s._searchText",
        params={"u": f"session:{session_id}"}).result_set
    assert rows, f"no :Source materialized for session:{session_id}"
    summary, topics, search_text = rows[0]
    return " ".join([summary or "", " ".join(topics or []), search_text or ""])


# ── #5445: a credential in `role` must not reach ANY of the three sinks ────

def test_credential_in_role_is_redacted_in_all_three_sinks(sdk, monkeypatch):
    """The role is caller-controlled and persisted as text — scrub it too.

    Three sinks are asserted separately because a fix that only reads the
    ``[role] content`` string leaves ``:Point.speaker`` (and the session
    ``:Source``) with the raw value; a fix that only reads ``speaker`` leaves
    the other two. The receipt must not report a silent zero either.
    """
    _keyless(monkeypatch)
    secret = _secret()
    sid = "sess-5445-role"
    res = sdk.capture_session(
        [{"role": secret, "content": "please review the attached patch"},
         {"role": "user", "content": "ordinary follow-up"}],
        session_id=sid)

    stored = _stored(sdk, sid)
    assert len(stored) == 2

    # Sink 1 — the stored turn text: `[<role>] <content>`.
    content0, speaker0 = stored[f"{sid}_t0"]
    assert secret not in content0, (
        f"the role VALUE survived into the stored turn text: {content0!r}")
    assert "REDACTED:github_token" in content0, content0

    # Sink 1b — the frame must ROUND-TRIP (#5445 review). The previous assertion here was
    # `speaker0 == _capture_turn_role_text(content0)[0]`, and the writer computes `speaker` as LITERALLY
    # that expression — `f(x) == f(x)`, which could not fail. It also missed a real defect: the role scrub
    # writes `[REDACTED:github_token]`, so the stored text was `[[REDACTED:github_token]] please review…`
    # and the reader's inverse (`^\[([^\]]+)\]\s*`) parsed the speaker as `[REDACTED:github_token` and
    # handed the BODY a stray `] `. The property that actually binds is that the inverse recovers the body
    # VERBATIM, which it cannot do if the (redacted) role ends the frame early.
    role0, body0 = _capture_turn_role_text(content0)
    assert body0 == "please review the attached patch", (
        f"the stored frame does not round-trip — the role scrub wrote a `]` that ends it early: {content0!r}")

    # An EMPTY role is the other way the frame can break (review round 12):
    # ``_CAPTURE_ROLE_PREFIX`` used a ``+`` quantifier, so ``"[] hello world"`` did not match
    # and the inverse returned the WHOLE string as the body — the served turn gained a
    # stray ``[] `` prefix. The reader now accepts an empty role.
    assert _capture_turn_role_text("[] hello world") == ("", "hello world"), (
        "an empty role must round-trip, not be returned as part of the body")

    # Sink 2 — :Point.speaker. It must be the role the read path parses (not a fragment), and it carries
    # no credential.
    assert secret not in (speaker0 or ""), (
        f"the role VALUE survived into :Point.speaker: {speaker0!r}")
    assert speaker0 == role0, (
        f"the stored speaker disagrees with the reader's parse of the stored turn text (#4675 parity): "
        f"{speaker0!r} != {role0!r}")

    # Sink 3 — the session :Source (summary/topics/search text). Topics are
    # lower-cased, so compare case-insensitively or the leak hides.
    blob = _source_blob(sdk, sid)
    assert secret not in blob and secret.lower() not in blob.lower(), (
        f"the role VALUE survived into the session :Source: {blob!r}")

    # The receipt is not a silent zero.
    assert res["capture_redactions"] >= 1, res

    # The ordinary turn is untouched (no over-redaction on the same capture).
    content1, speaker1 = stored[f"{sid}_t1"]
    assert speaker1 == "user"
    assert content1.endswith("ordinary follow-up")


def test_role_is_scrubbed_and_counted_at_the_chokepoint():
    """Unit level: the role goes through the SAME chokepoint and count."""
    secret = _secret()
    out, counts = _redact_turn_contents(
        [{"role": secret, "content": "hi"}])
    assert secret not in out[0]["role"]
    assert "[REDACTED:github_token]" in out[0]["role"]
    assert counts == {"github_token": 1}


def test_credential_role_is_scrubbed_even_with_empty_content():
    """The empty-content shortcut must not skip the role.

    A turn whose ``content`` is genuinely empty still has a caller-controlled
    role, and the role is persisted. Scrubbing only the non-empty branch would
    leave this shape leaking with a zero count.
    """
    secret = _secret()
    out, counts = _redact_turn_contents(
        [{"role": secret, "content": ""}])
    assert secret not in out[0]["role"], out[0]
    assert counts == {"github_token": 1}


def test_ordinary_roles_and_text_are_not_redacted():
    """No new over-redaction: a conforming role and ordinary prose are
    byte-identical and return the SAME object (no needless copy)."""
    conv = [{"role": "moderator", "content": "risk-free task-queue disk-usage"},
            {"role": "user", "content": "nothing sensitive here"}]
    out, counts = _redact_turn_contents(conv)
    assert counts == {}
    assert out[0] is conv[0] and out[1] is conv[1]
    assert out[0]["role"] == "moderator"


# ── #5472(i): cap=0 must bound the RETURNED turn ───────────────────────────

def test_cap_zero_bounds_the_returned_turn():
    """A cap that does not cap is a leak, not a performance knob.

    At ``cap=0`` the cut produced ``""`` from non-empty input; the emptiness
    guard then treated that as "genuinely empty" and appended the ORIGINAL,
    untruncated turn — so the credential came back whole, past a cap that was
    supposed to have removed it.
    """
    secret = "AKIA" + "ABCDEFGHIJKLMNOP"
    turn = {"role": "user", "content": f"secret {secret}"}
    out, counts = _redact_turn_contents([turn], cap=0)
    assert out[0]["content"] == ""
    assert secret not in out[0]["content"]
    assert counts == {}

    # Genuinely-empty content still passes through untouched (same object).
    empty = {"role": "user", "content": ""}
    out_empty, counts_empty = _redact_turn_contents([empty])
    assert out_empty[0] is empty and counts_empty == {}

    none_turn = {"role": "user", "content": None}
    out_none, counts_none = _redact_turn_contents([none_turn])
    assert out_none[0] is none_turn and counts_none == {}


# ── #5472(ii): redact_secrets keeps its str contract ───────────────────────

def test_redact_secrets_always_returns_a_str_first_element():
    """The annotation is ``tuple[str, dict]`` — make it true for non-str input.

    All in-repo callers coerce first, so this is latent; but a direct caller
    currently receives a tuple whose first element is not a string, with no
    error — and a container's string form can itself carry a credential.
    """
    for non_str in (None, 123, 1.5, True, ["a"], {"a": 1}, ("x",)):
        redacted, counts = redact_secrets(non_str)
        assert isinstance(redacted, str), (non_str, redacted)
        assert counts == {}

    # Coercion must not skip the scan: a credential inside a non-str's string
    # form is still redacted (it would otherwise be a NEW fail-open).
    secret = _secret()
    redacted, counts = redact_secrets([f"token {secret}"])
    assert secret not in redacted
    assert counts.get("github_token") == 1


# ── #5446: the depth guard must fail CLOSED, not return the subtree raw ────

def test_summary_depth_guard_fails_closed_not_open():
    """Past the depth bound the subtree becomes a visible marker, never raw.

    Walking the structure is the point: the no-crash test in the #4911 file
    passes even while the leaf leaks, because it only asserts ``isinstance``.
    """
    secret = _secret()
    obj: dict = {"k": secret}
    for _ in range(70):
        obj = {"n": obj}

    out = _redact_summary_strings(obj)
    assert secret not in str(out), "the deep credential survived the guard"

    node = out
    depth = 0
    while isinstance(node, dict):
        node = node.get("n")
        depth += 1
    assert depth <= 65, depth
    assert isinstance(node, str), node
    assert secret not in node
    assert node.startswith("[REDACTED:"), node

    # The guard's original job still holds: a ~200-deep payload must not turn
    # into an unhandled RecursionError on the public commit path.
    deep: dict = {"a": 1}
    for _ in range(200):
        deep = {"a": deep}
    assert isinstance(_redact_summary_strings(deep), dict)


def test_capture_caps_the_role_it_scrubs():
    """The ``cap`` bounds the ROLE too — it was the one scan the cap did not reach.

    Review round 12: ``_redact_turn_contents`` truncated only ``content``, so
    ``redact_secrets`` ran over the WHOLE client-controlled role (measured ~2.7 s
    for a 500,000-char role) while the module's own "``cap`` bounds the text
    scanned per turn" claim stayed in the docstring. ``_capture_turn_window`` now
    caps the role beside the content, and ``_redact_turn_contents`` re-applies it
    for callers that pass a raw conversation with an explicit ``cap``.

    REDs on removing the ``[:cap]`` in either place.
    """
    import time

    huge = "a" * 200_000
    started = time.perf_counter()
    _redacted, _counts = _redact_turn_contents(
        [{"role": huge, "content": "x"}], cap=10)
    elapsed = time.perf_counter() - started
    # The SCAN is what `cap` promises to bound (the docstring says so), and it is the
    # [P2]: uncapped this measured ~1.1 s for this size. A generous 0.5 s threshold —
    # well clear of timing noise and far below the uncapped cost.
    assert elapsed < 0.5, f"the role scan was not bounded by cap=10: {elapsed:.2f}s"

    # ...and the WINDOW caps the role it stores, so the scanned and the persisted role
    # are the same bytes on every capture lane. (`_redact_turn_contents` deliberately
    # does not rewrite an unmatched role — it leaves the turn object untouched when
    # nothing was redacted and nothing was cut — so the bound lives at the window.)
    windowed = _capture_turn_window([{"role": huge, "content": "x"}])
    assert windowed[0]["role"] == "a" * _CAPTURE_TURN_CAP, (
        "the window must cap the role, so the scanned and persisted role are the same bytes")
    assert windowed[0]["content"] == "x"


def test_summary_depth_guard_scrubs_a_deep_str_leaf():
    """A ``str`` leaf at exactly the bound is SCRUBBED, not merely collapsed.

    ⛔ DEPTH MATTERS (review round 12). The guard is ``_depth > 64``, and at that
    depth a non-``str`` collapses to ``"[REDACTED:depth]"`` and the subtree is not
    walked. The old fixture nested SEVENTY levels, so the secret sat behind a
    container at depth 65 that was collapsed — the ``str`` branch was never
    reached, and replacing it with the bare collapse left this test GREEN (a test
    that could not fail for the reason it named). The leaf must sit at EXACTLY 65.
    """
    secret = _secret()
    obj: object = secret
    for _ in range(65):
        obj = [obj]
    out = _redact_summary_strings(obj)
    assert secret not in str(out)
    assert "[REDACTED:" in str(out), (
        "the leaf must be SCRUBBED at depth 65 — collapsing the container also hides "
        "the secret, which is why the 70-level fixture could not fail")
    assert "[REDACTED:depth]" not in str(out), (
        "depth 65 is the str-leaf branch, not the collapse branch")


def test_summary_scrubs_a_non_str_key_recursively():
    """P3 review: the docstring claims KEYS are scrubbed — make it true.

    A non-``str`` key used to be passed through verbatim, so a credential inside
    a tuple key survived ``_redact_summary_strings`` while the docstring said
    keys were scrubbed. It is latent today (``json.dumps`` rejects a tuple key
    and ``construct_graph`` is caught), but the function now recurses into every
    key, so a tuple key's ``str`` elements are redacted with it and the claim
    holds. ``bytes``/non-container keys are still passed through, but they are
    not JSON-renderable — ``json.dumps`` raises before emitting any key — so
    they cannot carry a credential into the prompt.
    """
    secret = _secret()
    out = _redact_summary_strings({("k", secret): "innocuous"})
    assert secret not in str(out), out
    (key,) = out
    assert isinstance(key, tuple), key
    assert "[REDACTED:github_token]" in key, key

    # A non-str scalar key cannot carry a credential and is preserved as-is.
    assert _redact_summary_strings({1: "v"}) == {1: "v"}
    # And a str key is still redacted (the original claim's working half).
    keyed = _redact_summary_strings({secret: "v"})
    assert secret not in str(keyed), keyed
    assert "[REDACTED:github_token]" in str(keyed), keyed
