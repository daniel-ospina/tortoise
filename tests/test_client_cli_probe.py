"""The shipped `tortoise-client status` probe's exit-code contract (#3832 / D5).

The LIBRARY keeps never raising — `tortoise.mcp_client.status()` still returns
the payload so script callers skip cleanly. The CLI **probe** reports a distinct
process exit code, so a degraded probe cannot look like success to a human or
an agent harness:

    available -> 0 · empty -> 0 · degraded (can't reach it) -> 3
    unconfigured (not set up) -> 4
    1 stays a query/tool call that genuinely fails · 2 is argparse's

This supersedes #526's exit-0 clause **for the CLI probe only**.

#3805 LANDED THE STATUS WORDS AS THE RECORDED TERM SET. The pins below changed
from `ok` / `tortoise_unavailable` / `not_configured` to the four recorded terms
(roadmap §7 item 9: `available | empty | degraded | unconfigured`) because the
CONTRACT changed — the pre-#3805 words are translated by
`tortoise.status_vocabulary`, never asserted. Per #3832's own rule the pins were
NOT widened to accept both spellings: a pin that accepts both cannot catch the
regression it exists to catch.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLIENT_DIR = REPO_ROOT / "client"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Bind the CANONICAL driver FIRST, then load the client shim from `client/`.
# Inserting `client/` shadows the engine `tortoise` package while it is on the
# path (the client dir carries its own `tortoise/` namespace), so the driver has
# to be cached in sys.modules first — mirrors tests/test_client_surface.py. The
# path entry is REMOVED again so no later test in the same pytest session sees
# the client-only `tortoise`. (Without this, a sibling module that imported
# `tortoise.tortoise_client` first leaves `tortoise/` on sys.path and
# `import tortoise_client` binds that FILE instead of the client package.)
import tortoise.mcp_client  # noqa: E402, F401

sys.path.insert(0, str(CLIENT_DIR))
try:
    from tortoise_client import cli
finally:
    sys.path.remove(str(CLIENT_DIR))


def _fake_status(word: str, **extra):
    return lambda: {"status": word, "url": "http://localhost:8000/mcp", **extra}


def test_status_ok_exits_zero(monkeypatch, capsys):
    monkeypatch.setattr(cli, "status", _fake_status("ok", tools=7))
    assert cli.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "available"


def test_status_unreachable_exits_three(monkeypatch, capsys):
    """A configured-but-down endpoint is the can't-reach-it case: the recorded
    term is `degraded` (#3832 exit code 3, preserved verbatim)."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_status_not_configured_exits_four(monkeypatch, capsys):
    """No endpoint DECLARED + an unreachable probe is a SET-UP gap, not an
    outage; its recorded term is `unconfigured` (#3832 exit code 4, preserved).
    The driver has no notion of a missing ENDPOINT (it falls back to the
    self-host default), so the probe splits the state out — and it asks that
    question of the RESOLUTION, so a store's `api_url` counts as declared
    (#10605). `endpoint_declared` is therefore pinned here: without the stub this
    test reads whatever store the developer's own machine has.

    On THIS probe 'no endpoint declared' is the definition of unconfigured, and
    the rewritten payload must not contradict itself: the driver's default `url`
    is dropped and `configured: false` is added."""
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    monkeypatch.setattr(cli, "endpoint_declared", lambda: False)
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unconfigured"
    assert out["configured"] is False
    assert out["url"] is None


def test_status_down_daemon_with_declared_default_stays_degraded(monkeypatch, capsys):
    """Boundary: declaring the endpoint - even as the bare default - is what
    moves a down daemon OUT of unconfigured and into degraded. The probe never
    guesses."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://localhost:8000/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("tortoise_unavailable", error="boom"))
    assert cli.main(["status"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_status_forwards_driver_not_configured(monkeypatch, capsys):
    """Forward-compat: a `not_configured` word from the driver maps to 4 and is
    never second-guessed by the endpoint heuristic; its term is `unconfigured`."""
    monkeypatch.setenv("TORTOISE_MCP_URL", "http://127.0.0.1:1/mcp")
    monkeypatch.setattr(cli, "status", _fake_status("not_configured"))
    assert cli.main(["status"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "unconfigured"


def test_the_rewrite_rule_is_the_TERM_set_not_the_narrower_legacy_pair(monkeypatch, capsys):
    """The rewrite's rule is `raw not in CLIENT_STATUS_TERMS` — stated as code.

    This probe's docstring claimed the rewrite applies to a word in
    `LEGACY_WORDS_NEEDING_CONFIGURATION` (in practice `tortoise_unavailable`),
    which is a NARROWER rule than the code applies and differs on a reachable
    input: `not_configured` is a flat legacy word that resolves to
    `unconfigured` and is NOT in the recorded term set, so the code rewrites it
    (`url: None`, `configured: false`) where the narrower rule passes it
    through verbatim (#10790 review, P3).

    No behavioural defect was in the code — the payload is defensible for
    `not_configured` too — so this pins BOTH halves: the payload the code
    produces for that word, and the rule the docstring names. Without the
    second arm a maintainer "correcting" the code toward the narrower sentence
    would silently change a legacy-word payload and no test would red.
    """
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    monkeypatch.setattr(cli, "endpoint_declared", lambda: False)
    monkeypatch.setattr(cli, "status", _fake_status("not_configured"))

    assert cli.main(["status"]) == 4  # a SET-UP gap, not an outage
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unconfigured"
    assert out["url"] is None, (
        f"a legacy word with no declared endpoint must drop the driver's "
        f"default url, as `tortoise_unavailable` does: {out}")
    assert out["configured"] is False, out

    doc = " ".join((cli._probe_payload.__doc__ or "").split())
    assert "CLIENT_STATUS_TERMS" in doc, (
        "the docstring must state the rule the code applies — a word OUTSIDE "
        "`CLIENT_STATUS_TERMS`. Naming only "
        "`LEGACY_WORDS_NEEDING_CONFIGURATION` describes a narrower rule that "
        "does not rewrite `not_configured`, which is the word this test just "
        "rewrote")


def test_a_missing_key_refusal_keeps_the_endpoint_it_probed(monkeypatch, capsys):
    """The driver's `unconfigured` is passed through VERBATIM (#10605 review, P1).

    MEASURED BEFORE THIS PIN: the driver refusing for a MISSING KEY reports its
    refusal under the recorded `unconfigured` term — `{"status": "unconfigured",
    "url": "http://localhost:8000/mcp/", "error": "MCP-KEY-EMPTY: …"}` — and with
    `TORTOISE_MCP_URL` unset this probe rewrote it to `{"status":
    "unconfigured", "url": null, "configured": false, …}`. An endpoint WAS
    resolved and CONTACTED and the gap was the credential, so a caller branching
    on `configured` was told to declare an endpoint it already had: exactly the
    misattribution #10605 exists to remove, and the opposite of what this
    module's own docstring promised.

    The rewrite is therefore keyed on the driver's word being OUTSIDE the
    recorded term set — only the two-outcome outage word needs `configured`.
    """
    monkeypatch.delenv("TORTOISE_MCP_URL", raising=False)
    # ⛔ NON-VACUITY, stated rather than assumed (#10790 review, P3): with an endpoint still
    # DECLARED by some other input, `configured` is True and the rewrite this test forbids never
    # has a chance to fire — the assertions below would pass against the pre-fix code. Clearing
    # the env var is the setup; this is the precondition.
    monkeypatch.setattr(cli, "endpoint_declared", lambda: False)
    assert cli.endpoint_declared() is False
    refusal = {
        "status": "unconfigured",
        "url": "http://localhost:8000/mcp/",
        "error": ("MCP-KEY-EMPTY: the MCP endpoint rejected this request as "
                  "unauthenticated and NO credential resolved on the client"),
    }
    monkeypatch.setattr(cli, "status", lambda: dict(refusal))

    assert cli.main(["status"]) == 4  # still a SET-UP gap, not an outage
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "unconfigured"
    assert out["url"] == "http://localhost:8000/mcp/", (
        f"the endpoint the driver actually probed was discarded: {out}")
    assert "configured" not in out, (
        "a missing credential was re-reported as a missing endpoint: "f"{out}")
    assert "MCP-KEY-EMPTY" in out["error"]


def test_call_failure_keeps_exit_one(monkeypatch, capsys):
    """1 is KEPT for a query that genuinely fails — the supersede is narrow."""

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr(cli, "call_tool", boom)
    assert cli.main(["call", "some_tool"]) == 1
    assert "call failed" in capsys.readouterr().err


def test_call_prints_the_payload_even_with_a_trailing_block(monkeypatch, capsys):
    """`call` must emit the PAYLOAD as pretty JSON, not a join of every block.

    MEASURED REGRESSION this pins: the dispatch seam appends content blocks LAST
    (a retired-name warning, #10084's graph disclosure), so a caller that joined
    all blocks and `json.loads`-ed them got `Extra data: line 1 column 8825` for
    every write tool on every transport.

    The payload slot is `content[0]`, and the CLI takes it in preference to
    `structured_content` — NOT the other way round (`cli.py::_cmd_call`): for a
    tool declaring `x-fastmcp-wrap-result` the structured payload is the
    ENVELOPE (`{"result": …}`) while `content[0]` is the tool's own answer, so
    preferring the former would silently change this command's output shape for
    the 12 read tools that never receive a disclosure block at all. A second
    block must never be concatenated into the machine-readable output a script
    pipes to `jq`; it goes to stderr instead (#10084 review P3 — this docstring
    used to state the opposite, and a reader would have "fixed" the code to
    match it).
    """
    from types import SimpleNamespace

    payload = {"id": "pt_1", "status": "live"}
    trailing = "Graph disclosure: this write named no `graph`, so its target is …"

    def fake_call_tool(*_a, **_k):
        return SimpleNamespace(
            is_error=False,
            # DIVERGES on purpose: for a wrap-envelope tool the structured
            # payload is `{"result": …}` while `content[0]` is the tool's own
            # answer. A fixture where the two agree cannot tell the two
            # implementations apart, so it cannot catch the output-shape change.
            structured_content={"result": payload},
            content=[SimpleNamespace(text=json.dumps(payload)),
                     SimpleNamespace(text=trailing)],
        )

    monkeypatch.setattr(cli, "call_tool", fake_call_tool)
    assert cli.main(["call", "tortoise_create_point"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == payload, out


def test_call_prints_the_trailing_block_on_stderr_not_stdout(monkeypatch, capsys):
    """The appended blocks are DISCLOSURES — dropping them is the silence #10084
    refuses, and merging them into stdout breaks `| jq`.

    So: payload on stdout, every later block on stderr.
    """
    from types import SimpleNamespace

    payload = {"id": "pt_2"}

    def fake_call_tool(*_a, **_k):
        return SimpleNamespace(
            is_error=False,
            structured_content={"result": payload},   # diverges — see above
            content=[SimpleNamespace(text=json.dumps(payload)),
                     SimpleNamespace(text="trailing disclosure")],
        )

    monkeypatch.setattr(cli, "call_tool", fake_call_tool)
    assert cli.main(["call", "tortoise_create_point"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == payload
    assert "trailing disclosure" in captured.err, (
        "the appended disclosure was dropped instead of surfaced on stderr")
