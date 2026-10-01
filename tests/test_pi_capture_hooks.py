"""#3575 — the in-repo Pi capture extension (`tortoise/pi-hooks/`).

The extension is the artifact `HARNESS_INSTALL.pi` installs. These tests pin
the shipped artifact itself: it is self-contained (no `agent-infra`
dependency), it carries no `autoCapture`-style default-false flag (installing
it IS the opt-in — the #3575 trap was a capture extension that defaulted off),
and it talks to both hosted capture endpoints.

The behavioral assertions run the extension's own `node --test` suite when the
local Node enables native TypeScript type stripping AND module-syntax
detection BY DEFAULT (Node >= 22.18 — the `.ts` is typeless with no
`package.json`); the source-level assertions always run, so the surface stays
pinned even where Node is older or absent.

The installed-artifact tests at the bottom load and fire the file
``capture_install`` actually writes, so the seam is verified at its INSTALL
location, not only in the source tree.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tortoise import capture_install
from tortoise.capture_install import install_capture

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
EXTENSION = HOOKS / "tortoise-capture.ts"
EXTENSION_TEST = HOOKS / "tortoise-capture.test.ts"


def _installed_seam_path(tmp_home: Path) -> Path:
    """Where the installer puts the Pi seam — taken from the installer itself.

    Both halves come from ``capture_install``, the module that WRITES the
    artifact: the directory from ``capture_install.pi_home`` and the filename
    from ``capture_install.PI_EXTENSION_NAME``. Re-typing either here would be a
    hand-maintained copy of the install layout, so a correct relocation in the
    shipping code would redden this gate with a message that blames the
    installer for a consistent change — and a test that re-typed the directory
    could load a stale path while the installer wrote elsewhere.
    """
    return capture_install.pi_home(tmp_home) / capture_install.PI_EXTENSION_NAME


def _src() -> str:
    return EXTENSION.read_text(encoding="utf-8")


def _code_only(src: str) -> str:
    """Drop `//` comments so prose that NAMES a forbidden pattern (e.g. "no
    autoCapture flag") is not mistaken for the pattern itself. Preserves the
    `://` in URLs."""
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        idx = line.find("//")
        if idx != -1 and (idx == 0 or line[idx - 1] != ":"):
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def test_extension_artifact_is_committed():
    assert EXTENSION.is_file(), f"missing capture extension: {EXTENSION}"
    # A stub would satisfy exists(); the shipped seam is a real implementation.
    assert len(_src()) > 2000, "extension is suspiciously small — is it a stub?"
    assert EXTENSION_TEST.is_file(), f"missing behavioral test: {EXTENSION_TEST}"


def test_extension_has_no_agent_infra_dependency():
    """agent-infra is not shipped to users — the seam must stand alone."""
    code = _code_only(_src())
    assert "agent-infra" not in code, "extension must not depend on agent-infra"
    assert "capture-attribution" not in code, (
        "extension must not import agent-infra shared modules"
    )
    # it must not reach into an agent-infra path at runtime either
    assert not re.search(r"['\"][^'\"]*agent-infra", code)


def test_capture_is_not_gated_behind_a_default_false_flag():
    """The false-PASS trap: an extension that installs but defaults capture
    OFF. Installing the extension must be the opt-in."""
    assert "autoCapture" not in _code_only(_src()), (
        "no autoCapture flag — installing the extension is the opt-in"
    )


def test_extension_turn_cap_matches_the_server_handler_cap():
    """#3575 P1-A: the shipped extension's `MAX_TURNS` must equal the bound the
    HANDLER enforces (`tortoise/quota.py::MAX_SESSION_TURNS`), never the
    Pydantic `SessionRequest.conversation` max_length.

    The extension is shipped standalone (it cannot import Python), so this
    parity guard is what makes drift impossible: if the server cap moves, the
    literal here must move with it or CI goes red — a 501+-turn Pi session
    would otherwise POST >cap, get HTTP 400, and log "capture FAILED" while
    the backfill leg (which derives from the same constant) still succeeded.
    """
    from tortoise.quota import MAX_SESSION_TURNS

    m = re.search(r"^export const MAX_TURNS = (\d+);", _src(), re.M)
    assert m, "extension must export a literal MAX_TURNS"
    assert int(m.group(1)) == MAX_SESSION_TURNS, (
        f"extension MAX_TURNS={m.group(1)} != handler cap MAX_SESSION_TURNS="
        f"{MAX_SESSION_TURNS} — a >cap session would 400 the live capture"
    )


def test_extension_turn_max_chars_matches_the_sdk_stored_window():
    """#4897 criterion 4: the per-turn cap is ONE number.

    The shipped extension is a standalone TypeScript artifact — it CANNOT
    import the Python constant (``tortoise/sdk.py::_CAPTURE_TURN_CAP``), which
    is the source of truth for the server's stored window. Without this pin the
    two declarations drift SILENTLY: the client would clip to one width and the
    server cap to another, and a turn between them would be cut by whichever
    ran second. This is the same shape as the ``MAX_TURNS`` parity guard above,
    for the same reason (a shipped artifact cannot import Python).

    The truncation sentinel is pinned too: the server stores a client-clipped
    turn VERBATIM, so a reader on either side must recognise the other's marker
    (#4897).
    """
    from tortoise.sdk import (
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _capture_truncation_marker,
    )

    m = re.search(r"^export const TURN_MAX_CHARS = (\d+);", _src(), re.M)
    assert m, "extension must export a literal TURN_MAX_CHARS"
    assert int(m.group(1)) == _CAPTURE_TURN_CAP, (
        f"extension TURN_MAX_CHARS={m.group(1)} != sdk._CAPTURE_TURN_CAP="
        f"{_CAPTURE_TURN_CAP} — the client and the server would store "
        "different windows")

    # The marker vocabulary must match: the sentinel literal and the marker's
    # fixed wording both live in the TS source, exactly as Python builds them.
    assert f'"{_CAPTURE_TRUNCATION_SENTINEL}"' in _src(), (
        "the extension's TRUNCATION_SENTINEL literal diverged from "
        "sdk._CAPTURE_TRUNCATION_SENTINEL — a reader could no longer detect a "
        "cut turn written by the other side")
    sample = _capture_truncation_marker(12345)
    assert "original length 12345 chars]" in sample  # sanity on the wording
    assert "${total}" in _src() and "original length" in _src(), (
        "the extension's marker template diverged from "
        "sdk._capture_truncation_marker")


def test_extension_posts_both_capture_endpoints():
    src = _src()
    # install-probe on load (server-visible install signal) …
    assert '"/v1/sessions/install-probe"' in src
    # … and session filing on shutdown, tagged harness=pi (the receipt key).
    assert '"/v1/sessions"' in src
    assert re.search(r'HARNESS\s*=\s*"pi"', src), "harness constant must be 'pi'"
    # the probe payload must be harness-only — zero conversation content
    assert "postInstallProbe" in src


def node_supports_ts_version(major: int, minor: int) -> bool:
    """Can THIS suite's invocation run the extension: `node --test <file>.ts`?

    22.18, not 22.7 — and the difference is the FLAG, not the feature. Node 22.7
    added `--experimental-strip-types`; enabling it BY DEFAULT (which is what a
    bare `node --test <file>.ts` depends on) arrived in 22.18.0. The extension
    also needs ambient module-syntax DETECTION, which is why 22.6 is not enough
    even with the flag.

    The parity test in `test_capture_spool.py` keeps a 22.7 floor BY DESIGN: it
    invokes node as `node --experimental-strip-types <driver>`. The two floors
    describe two different commands, so they are deliberately NOT in step — the
    defect this replaces was a floor copied from that file onto an invocation
    that does not pass the flag, which made 22.7-22.17 RED (the module fails to
    load) instead of SKIP.

    Pure over its inputs so the boundary is testable without a second Node.
    """
    return major >= 24 or (major == 23 and minor >= 6) \
        or (major == 22 and minor >= 18)


def _node_supports_ts(node: str) -> bool:
    """`node_supports_ts_version` applied to the node on PATH."""
    try:
        out = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"v(\d+)\.(\d+)", out)
    if not m:
        return False
    return node_supports_ts_version(int(m.group(1)), int(m.group(2)))


def _scrubbed_env(tmpdir: str) -> dict[str, str]:
    """The environment the suite runs in: NO ambient capture credential.

    #3721: the extension resolves its key from `TORTOISE_API_KEY` or
    ``~/.pi/agent/tortoise-config.json``. On a developer machine that has
    either, the suite passed while asserting the MACHINE — and on a clean CI
    runner the empty key short-circuited the POST before the injected fetch
    was called (`0 !== 1`). Scrubbing HOME + the TORTOISE credentials makes a
    local run reproduce CI, so that coupling cannot silently return: if the
    suite ever depends on the machine again, this test REDs locally too.
    """
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("TORTOISE_API_KEY", "TORTOISE_API_URL")
    }
    env["HOME"] = tmpdir  # no `~/.pi/agent/tortoise-config.json`
    env["USERPROFILE"] = tmpdir  # node's os.homedir() on Windows
    return env


def test_the_node_floor_matches_the_invocation_the_suite_actually_makes():
    """The floor must name what THIS file's invocation needs: `node --test
    <file>.ts`, with NO `--experimental-strip-types` — because the flag may be
    removed in a future major, the floor tracks its DEFAULT-ON version instead.

    A too-low floor is worse than a high one: on 22.7-22.17 `_node_supports_ts`
    would return True, the module would fail to load, and
    `test_extension_behavioral_suite` would RED on a machine that simply cannot
    run it — instead of skipping. 22.7 is correct for the SIBLING parity test
    only, because that driver passes the flag.

    Mutation: revert the floor to 22.7, drop the 23.x band, or use a bare
    `major > 22` -> the corresponding row REDs.
    """
    assert node_supports_ts_version(22, 6) is False
    assert node_supports_ts_version(22, 7) is False, (
        "22.7 has the flag, not the DEFAULT — this suite passes no flag")
    assert node_supports_ts_version(22, 17) is False
    assert node_supports_ts_version(22, 18) is True
    assert node_supports_ts_version(23, 5) is False, (
        "default-on landed in v23.6.0, not v23.0.0 — a bare `major > 22` says "
        "'supported' on 23.0-23.5, where the module still fails to load")
    assert node_supports_ts_version(23, 6) is True
    assert node_supports_ts_version(24, 0) is True


def test_extension_behavioral_suite():
    """Run `node --test tortoise/pi-hooks/tortoise-capture.test.ts` (probe
    payload, turn extraction, capture payload, reload skip) in a SCRUBBED
    environment — see `_scrubbed_env`. Skipped only when the local Node cannot
    run TypeScript — the source pins above still run."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available — extension source pins above still ran")
    if not _node_supports_ts(node):
        pytest.skip("node < 22.18 does not enable TypeScript type stripping by "
                    "default — source pins still ran")
    with tempfile.TemporaryDirectory() as fake_home:
        proc = subprocess.run(
            [node, "--test", str(EXTENSION_TEST)],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=120,
            env=_scrubbed_env(fake_home),
        )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


# ── the INSTALLED artifact ("#4620 outcome (1) at installed fidelity") ──
#
# Everything above exercises the seam at its SOURCE path
# (`tortoise/pi-hooks/tortoise-capture.ts`). The probe below imports the file
# `capture_install` actually writes, from a bare temp HOME with NO sibling
# files — proving the artifact is self-contained and firable where it is
# installed. It is not a second copy of the 51-test suite: the marginal claim
# is module resolution from the install location (see the anti-vacuity test).

_PROBE_SOURCE = r'''
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.env.PROBE_SEAM).href);
const handlers = {};
const pi = { on(e, f) { handlers[e] = f; } };
const calls = [];
const fetchImpl = async (url, init) => {
  calls.push({ url, body: JSON.parse(String(init?.body ?? "{}")) });
  return { ok: true, status: 200, json: async () => ({}) };
};
mod.default(pi, {
  fetchImpl,
  env: { TORTOISE_API_KEY: "tt_test", TORTOISE_API_URL: "https://h" },
  configPath: "/nonexistent/tortoise-config.json",
  spoolDir: process.env.PROBE_SPOOL,
});
const ctx = {
  sessionManager: {
    getEntries: () => [
      { type: "message", message: { role: "user", content: [{ type: "text", text: "installed seam probe" }] } },
      { type: "message", message: { role: "assistant", content: [{ type: "text", text: "receipt" }] } },
    ],
    getSessionId: () => "sess-installed-1",
    getSessionFile: () => "/tmp/sessions/2026-01-01_abc.jsonl",
  },
  model: { provider: "deepseek", id: "deepseek-v4-flash" },
};
await handlers.session_shutdown({ reason: "quit" }, ctx);
await new Promise((r) => setImmediate(r));
console.log("PROBE_JSON:" + JSON.stringify({ handlers: Object.keys(handlers), calls }));
'''


def _require_node() -> str:
    """The `node` binary — a local skip, but a FAILURE under CI.

    ⛔ #4620 review: the installed-artifact checks below are the only
    EXECUTABLE proof that the seam works as installed, so in CI a missing/old
    Node must fail by name instead of silently dropping them and reporting
    green.  The shape used here (``pytest.fail`` when ``CI`` is set, else
    ``pytest.skip``) is the one ``tests/test_pack_shipping_wheel.py`` already
    uses for a toolchain-gated pack gate; the fail-never-skip ruling it serves
    is ``bff_test_helpers.require_toolchain`` (#3501), which always fails and
    takes an explicit named opt-out (``AUTH_ALLOW_NO_TOOLCHAIN``) rather than a
    CI branch.  This gate is SELF-ENFORCING — it fails whenever ``CI`` is set,
    so no CI lane can execute this file without a usable Node and still report
    green — which is why the provisioning (``actions/setup-node@v4``, Node 22)
    is not restated here as a claim that has to be kept in sync.  Locally the
    skip is kept, because the source-level pins above still ran.

    The pre-existing ``test_extension_behavioral_suite`` above deliberately
    keeps its own skip (it is not escalated to a CI failure): this gate is for
    the checks this PR ADDS, and widening it to the older suite is a separate
    change against that suite's contract (recorded in the scoping artifact).
    """
    in_ci = bool(os.environ.get("CI"))
    node = shutil.which("node")
    if node is None:
        if in_ci:
            pytest.fail(
                "node is REQUIRED in CI: it is the only way the installed Pi "
                "capture seam is loaded and fired (#4620). Install node >= 22.18 "
                "in this lane rather than letting the check vanish silently."
            )
        pytest.skip("node not available — the source pins above still ran")
    if not _node_supports_ts(node):
        if in_ci:
            pytest.fail(
                f"node at {node} is older than 22.18 and does not enable "
                "TypeScript type stripping by default, so the installed-seam "
                "check would be skipped — it FAILS in CI instead (#4620)."
            )
        pytest.skip(
            "node < 22.18 does not enable TypeScript type stripping by default "
            "— source pins still ran"
        )
    return node


def _run_installed_probe(tmp_home: Path, installed: Path, node: str):
    """Install the probe beside the installed seam and run it, hermetically.

    `_scrubbed_env` strips the capture credential but NOT
    `TORTOISE_CAPTURE_SPOOL_DIR`, so `PROBE_SPOOL` is passed explicitly —
    otherwise a real spool could be written.
    """
    probe = tmp_home / "probe.mjs"
    probe.write_text(_PROBE_SOURCE, encoding="utf-8")
    return subprocess.run(
        [node, "probe.mjs"],
        capture_output=True,
        text=True,
        cwd=str(tmp_home),
        timeout=120,
        env={
            **_scrubbed_env(str(tmp_home)),
            "PROBE_SEAM": str(installed),
            "PROBE_SPOOL": str(tmp_home / "spool"),
        },
    )


def _guard_outcome() -> str:
    """Call `_require_node()` and RETURN what it did.

    The outcome is returned rather than raised on purpose: `pytest.skip` raises
    a BaseException that pytest turns into a SKIP, so a `pytest.raises`-based pin
    cannot tell "the gate failed closed" from "the gate skipped" — it would let
    a mutated skip read as green. Catching both sides makes the difference an
    assertion.
    """
    try:
        _require_node()
    except pytest.fail.Exception as exc:
        return f"fail: {exc}"
    except pytest.skip.Exception as exc:
        return f"skip: {exc}"
    return "no-exception"


def test_require_node_fails_closed_when_node_is_absent(monkeypatch):
    """#4620: the node gate is a FAILURE under CI, a skip locally.

    Without this pin the fail-closed branch had no test at all — reverting both
    ``pytest.fail`` calls to ``pytest.skip`` left the suite green on any
    Node-22 host, which is a mutation hole in the exact guarantee this PR
    installs.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)

    monkeypatch.setenv("CI", "1")
    assert _guard_outcome().startswith("fail:"), "CI must fail closed, not skip"

    monkeypatch.delenv("CI", raising=False)
    assert _guard_outcome().startswith("skip:"), "a local run still skips"


def test_require_node_fails_closed_on_a_pre_strip_types_node(monkeypatch):
    """The other half of the gate: node present but below the 22.18 floor."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/node")
    monkeypatch.setitem(_require_node.__globals__, "_node_supports_ts", lambda _n: False)

    monkeypatch.setenv("CI", "1")
    assert _guard_outcome().startswith("fail:"), "CI must fail closed, not skip"

    monkeypatch.delenv("CI", raising=False)
    assert _guard_outcome().startswith("skip:"), "a local run still skips"


def test_installed_seam_loads_and_fires():
    """#4620 outcome (1): load and fire the artifact AS INSTALLED, assert the
    capture receipt.

    The source-located suite above proves the seam's LOGIC; this proves the
    file `install_capture` writes is self-contained at its install location —
    it registers both handlers and POSTs harness=pi with the session's turns.
    """
    node = _require_node()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_home = Path(tmp)
        result = install_capture("pi", home=tmp_home)
        assert result.ok, result.error or "install refused"
        installed = _installed_seam_path(tmp_home)
        assert installed.is_file(), f"installer wrote no seam at {installed}"
        proc = _run_installed_probe(tmp_home, installed, node)
        assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        m = re.search(r"^PROBE_JSON:(.*)$", proc.stdout, re.M)
        assert m, f"no PROBE_JSON line — stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        payload = json.loads(m.group(1))
        assert {"session_start", "session_shutdown"} <= set(payload["handlers"]), payload["handlers"]
        calls = payload["calls"]
        assert len(calls) == 1, calls
        assert re.search(r"/v1/sessions$", calls[0]["url"]), calls[0]["url"]
        body = calls[0]["body"]
        assert body["harness"] == "pi"
        assert body["session_id"] == "sess-installed-1"
        assert body["conversation"] == [
            {"role": "user", "content": "installed seam probe"},
            {"role": "assistant", "content": "receipt"},
        ]


def test_installed_seam_probe_fails_when_the_artifact_is_not_self_contained():
    """Anti-vacuity for `test_installed_seam_loads_and_fires`.

    Mutation: append a relative runtime import to the INSTALLED seam, with no
    sibling file beside it. The source-located suite still passes (a sibling
    would resolve); the installed single-file probe must FAIL with
    ERR_MODULE_NOT_FOUND — proving the check exercises the install location,
    not the source tree. Without this, a probe that silently imported the
    source (or that swallowed a load error) would pass vacuously.
    """
    node = _require_node()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_home = Path(tmp)
        result = install_capture("pi", home=tmp_home)
        assert result.ok, result.error or "install refused"
        installed = _installed_seam_path(tmp_home)
        installed.write_text(
            installed.read_text(encoding="utf-8")
            + '\nimport { __x } from "./helper.ts";\n',
            encoding="utf-8",
        )
        proc = _run_installed_probe(tmp_home, installed, node)
        combined = proc.stdout + proc.stderr
        assert proc.returncode != 0, combined
        assert "ERR_MODULE_NOT_FOUND" in combined, combined
        assert "helper.ts" in combined, combined


# ── #4897: the two-language truncation-marker contract ────────────────────
#
# The number pin above is source-level (it catches a changed literal even if
# nothing runs the TS). This one is BEHAVIORAL: it runs the shipped TS clip and
# compares its output, character for character, with the Python marker the
# server writes into the same stored turn. A source regex cannot see a template
# that renders differently (a swapped word, a wrong separator), and the marker
# is the only thing that lets a reader on either side detect the other's cut.

_MARKER_PROBE = r'''
import { clipTurnContent, truncationMarker } from {{EXT_URI}};
const total = Number(process.env.PROBE_TOTAL);
const clipped = clipTurnContent("a".repeat(total));
console.log("PROBE_JSON:" + JSON.stringify({
  marker: truncationMarker(total),
  length: Array.from(clipped).length,
  tail: Array.from(clipped).slice(-64).join(""),
  sentinelIn: clipped.includes("\u2026[truncated:"),
}));
'''


def test_extension_marker_output_matches_the_python_marker(tmp_path):
    """The client's marker and the server's marker must be the SAME string.

    The server stores a client-clipped turn verbatim (it does not re-mark a
    turn already at or under the cap), so a reader has to recognise a marker
    the OTHER language wrote. A template that renders differently is invisible
    to the source-level pin above — this executes both and compares.
    """
    node = _require_node()
    from tortoise.sdk import _CAPTURE_TURN_CAP, _capture_truncation_marker

    total = _CAPTURE_TURN_CAP + 1234
    probe = tmp_path / "marker-probe.mjs"
    probe.write_text(
        _MARKER_PROBE.replace("{{EXT_URI}}", json.dumps(EXTENSION.as_uri())),
        encoding="utf-8",
    )
    env = _scrubbed_env(str(tmp_path))
    env["PROBE_TOTAL"] = str(total)
    proc = subprocess.run(
        [node, "marker-probe.mjs"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=120,
        env=env,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE_JSON:")][-1]
    out = json.loads(line[len("PROBE_JSON:"):])
    expected_marker = _capture_truncation_marker(total)
    assert out["marker"] == expected_marker, (
        f"TS marker {out['marker']!r} != Python marker {expected_marker!r} — a "
        "reader could not detect a cut written by the other side")
    assert out["length"] == _CAPTURE_TURN_CAP
    assert out["sentinelIn"] is True
    assert out["tail"].endswith(expected_marker)


_BLANK_PROBE = r'''
import { clipTurnContent, BLANK_CHARS, TRUNCATION_SENTINEL } from {{EXT_URI}};
const inputs = JSON.parse(process.env.PROBE_INPUTS);
const out = inputs.map((s) => {
  const clipped = clipTurnContent(s);
  return {
    marked: clipped.includes(TRUNCATION_SENTINEL),
    length: Array.from(clipped).length,
  };
});
console.log("PROBE_JSON:" + JSON.stringify({
  out,
  blankChars: Array.from(BLANK_CHARS).map((c) => c.codePointAt(0)),
}));
'''


def test_extension_clipper_blankness_matches_the_python_clipper(tmp_path):
    """#4897 review round 15, P3: the two clippers must agree on BLANK.

    ``_clip_capture_turn_content`` tested ``not content.strip()`` while the
    shipped TS clipper tested ``!content.trim()``. Those builtins disagree on
    exactly five code points — U+001C-U+001F and U+0085 (Python strips, JS keeps)
    and U+FEFF (JS strips, Python keeps) — so a client-clipped blank turn could be
    stored MARKER-ONLY on one side and blank on the other, and the module's claim
    that both paths "produce byte-identical stored turns" was false for them. Both
    sides now use the same explicit code-point set (the union of the two).

    Mutation that REDs this: revert EITHER clipper to its builtin (``.strip()`` /
    ``.trim()``) — the divergent code points then flip on one side only — or
    delete the TS blank branch, when the marker appears on a blank input. The
    exported ``BLANK_CHARS`` comparison additionally reds if the two exported
    sets drift even while behavior happens to agree.
    """
    node = _require_node()
    from tortoise.sdk import (
        _CAPTURE_BLANK_CHARS,
        _CAPTURE_TRUNCATION_SENTINEL,
        _CAPTURE_TURN_CAP,
        _clip_capture_turn_content,
    )

    over = _CAPTURE_TURN_CAP + 1
    fixtures = [
        " " * over,          # the plain blank case
        "\u001c" * over,     # Python blank, JS trim() NOT blank
        "\u001f" * over,
        "\u0085" * over,
        "\ufeff" * over,     # JS trim() blank, Python strip() NOT blank
        "\u200b" * over,     # neither builtin calls it blank — still marked
        "x" * over,          # non-blank control
    ]
    probe = tmp_path / "blank-probe.mjs"
    probe.write_text(
        _BLANK_PROBE.replace("{{EXT_URI}}", json.dumps(EXTENSION.as_uri())),
        encoding="utf-8",
    )
    env = _scrubbed_env(str(tmp_path))
    env["PROBE_INPUTS"] = json.dumps(fixtures)
    proc = subprocess.run(
        [node, "blank-probe.mjs"],
        capture_output=True, text=True, cwd=str(tmp_path), timeout=120, env=env,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    line = [ln for ln in proc.stdout.splitlines()
            if ln.startswith("PROBE_JSON:")][-1]
    payload = json.loads(line[len("PROBE_JSON:"):])
    ts = payload["out"]
    assert len(ts) == len(fixtures), (ts, fixtures)

    # The exported TS set is character-for-character the Python set.
    assert "".join(chr(cp) for cp in payload["blankChars"]) == _CAPTURE_BLANK_CHARS

    for src, got in zip(fixtures, ts, strict=True):
        py = _clip_capture_turn_content(src)
        py_marked = _CAPTURE_TRUNCATION_SENTINEL in py
        assert got["marked"] == py_marked, (
            f"clippers disagree on {src[:1]!r}: TS marked={got['marked']}, "
            f"Python marked={py_marked}")
        assert got["length"] == len(py), (
            f"clippers disagree on length for {src[:1]!r}: "
            f"{got['length']} != {len(py)}")
        if not py_marked:
            assert got["length"] == _CAPTURE_TURN_CAP
