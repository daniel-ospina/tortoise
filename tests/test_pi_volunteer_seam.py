"""#2119 — the Pi per-turn READ seam (epic #2080 end-state platform).

Pi is the one harness in the seam family whose hook surface is an EXTENSION
rather than a shell command, and until this issue it had only the capture half
(`tortoise/pi-hooks/tortoise-capture.ts`, #3575). The read half is
`tortoise/pi-hooks/tortoise-volunteer.ts`: it registers Pi's per-turn
`before_agent_start` hook and returns the documented
`{message: {customType, content, display}}` result carrying the shared reflex's
why-context block.

The re-measurement this issue turns on, since the matrix row that said the seam
was unreachable is what kept it unbuilt:

* the installed pi-coding-agent documents `before_agent_start` as "fired after
  user submits prompt, before agent loop — can inject a message and/or modify
  the system prompt" (`docs/extensions.md`), typed in
  `dist/core/extensions/types.d.ts` as `BeforeAgentStartUserEvent`
  (`messageType: "user"` + `prompt`) → `BeforeAgentStartEventResult`
  (`message` / `systemPrompt`);
* `docs/research/2026-09-01-gbrain-learnings/platform-seams.md` claimed the
  opposite ("no per-turn prompt hook in the extension API today"). The row is
  corrected by this change; this file is the evidence it was wrong.

What is graded here, in order of how hard it is to fake:

1. the INSTALL — `install_capture("pi")` writes BOTH extension files, byte for
   byte from the shipped sources, idempotently, and refuses a foreign file at
   the read seam's path (never a silent replace of another extension);
2. the DRIFT verdict — an installed read seam that is stale reads as
   `stale-artifact`, while its ABSENCE is not a finding at all (a capture-only
   install never opted into injection, so grading absence would false-block a
   healthy machine);
3. `--uninstall` — removes the READ half only, and says what it left behind;
4. THE REAL SEAM END TO END — the INSTALLED extension, loaded by node through
   Pi's own handler contract, against the REAL `tortoise volunteer` reflex over
   a SEEDED embedded graph: the injected message carries the seeded claim's
   label. This is the anti-gaming grade the issue asks for ("grade the real
   seam, not a sim");
5. the extension's own `node --test` suite.

Self-contained embedded lane: `TORTOISE_DB_URI` is cleared and the graph is a
per-test temp db.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "tortoise" / "pi-hooks"
CAPTURE_EXTENSION = HOOKS / "tortoise-capture.ts"
VOLUNTEER_EXTENSION = HOOKS / "tortoise-volunteer.ts"
VOLUNTEER_TEST = HOOKS / "tortoise-volunteer.test.ts"

_PY = sys.executable

#: The same seeded graph `tests/test_platform_seams.py` uses: the reflex only
#: surfaces EP-MEASURED points (a neutral Beta(1,1) prior sits below the 0.7
#: gate), so the evidence→claim IMPL edge and the posteriors are wired
#: directly. Copied deliberately rather than imported — a shared fixture would
#: make one file's seeding change silently re-baseline the other's assertions.
_SEED = """
import os
from tortoise.sdk import TortoiseSDK
sdk = TortoiseSDK(db_path=os.environ["TORTOISE_DB_PATH"])
proj = sdk._get_proj()
ev = sdk.create_point(
    "evidence",
    "CI deploys on main merge via GitHub Actions [supporting record]")
claim = sdk.create_point(
    "statement",
    "The CI pipeline deploys via GitHub Actions on main merge.")
sdk.create_operator("IMPL", ev["id"], [claim["id"]])
for pid, a, b in ((claim["id"], 12.0, 1.0), (ev["id"], 12.0, 1.0)):
    m = round(a / (a + b), 4)
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.confidence=$c, "
        "n.posterior_alpha=$a, n.posterior_beta=$b",
        params={"id": pid, "a": a, "b": b, "c": m})
sdk.close()
"""

_SEEDED_CLAIM = "CI pipeline deploys via GitHub Actions"


# ── env plumbing ─────────────────────────────────────────────────────────


def _base_env(home: Path) -> dict:
    return {
        **os.environ,
        "TORTOISE_DB_PATH": str(home.parent / "seams.db"),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
        "HOME": str(home),
        # The reflex resolves its identity from the user-global credentials
        # file; a real hosted key on the runner would make these local-graph
        # assertions vacuous. Nothing here is ever transmitted.
        "TORTOISE_API_KEY": "",
        "TORTOISE_API_URL": "",
    }


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An isolated HOME with a seeded embedded graph beside it."""
    h = tmp_path / "home"
    h.mkdir()
    env = _base_env(h)
    for key in ("TORTOISE_API_KEY", "TORTOISE_API_URL",
                "TORTOISE_VOLUNTEER_DISABLE", "TORTOISE_COMMAND"):
        monkeypatch.delenv(key, raising=False)
    # `PATH.home()` is what the INSTALLER and the `--uninstall` verb resolve
    # their root from, so the fixture must move it too or `_uninstall_pi_volunteer`
    # would act on the developer's real `~/.pi`.
    monkeypatch.setenv("HOME", str(h))
    subprocess.run([_PY, "-c", _SEED], env=env, check=True,
                   capture_output=True, timeout=180)
    return h


def _install(home: Path):
    from tortoise.capture_install import install_capture

    return install_capture("pi", home=home)


def _ext_dir(home: Path) -> Path:
    return home / ".pi" / "agent" / "extensions"


# ── 1. the install ───────────────────────────────────────────────────────


def test_install_writes_both_artifacts_byte_for_byte(home):
    result = _install(home)
    assert result.ok, result.error
    assert result.changed is True
    installed = _ext_dir(home)
    assert (installed / CAPTURE_EXTENSION.name).read_bytes() == CAPTURE_EXTENSION.read_bytes()
    assert (installed / VOLUNTEER_EXTENSION.name).read_bytes() == VOLUNTEER_EXTENSION.read_bytes()
    # The read seam's action is reported SEPARATELY: an install that silently
    # gained an injection seam would be an exposure change nobody asked for.
    assert any(VOLUNTEER_EXTENSION.name in action for action in result.actions)
    assert any("read seam" in action for action in result.actions)

    # Idempotent: a re-run (the upgrade path) writes nothing and says so.
    again = _install(home)
    assert again.ok and again.changed is False
    assert again.actions == ()


def test_a_dry_run_writes_neither_artifact(home):
    from tortoise.capture_install import install_capture

    result = install_capture("pi", home=home, dry_run=True)
    assert result.ok, result.error
    assert not _ext_dir(home).exists()


def test_a_foreign_file_at_the_read_seam_is_refused_not_replaced(home):
    installed = _ext_dir(home)
    installed.mkdir(parents=True)
    foreign = installed / VOLUNTEER_EXTENSION.name
    foreign.write_text("// somebody else's extension\nexport default () => {};\n")

    result = _install(home)
    assert result.ok is False
    assert VOLUNTEER_EXTENSION.name in (result.error or "")
    assert foreign.read_text().startswith("// somebody else's extension"), (
        "a file this installer cannot prove it wrote must never be replaced")
    # …and the CAPTURE half did land first (reported, not hidden) — the error
    # names the read seam without pretending the whole install was a no-op.
    assert (installed / CAPTURE_EXTENSION.name).is_file()


def test_a_stale_read_seam_is_reported_and_its_absence_is_not(home):
    from tortoise import hook_install

    result = _install(home)
    assert result.ok, result.error
    root = _ext_dir(home)
    assert hook_install.detect_artifact_install(root, "pi") == []

    # Absence is NOT drift: a capture-only install never opted into injection.
    (root / VOLUNTEER_EXTENSION.name).unlink()
    assert hook_install.detect_artifact_install(root, "pi") == []

    # Present but OLD is the real defect — a read seam that injects with older
    # logic is indistinguishable from an empty graph at the agent turn.
    stale = root / VOLUNTEER_EXTENSION.name
    stale.write_text("// tortoise-hook-version: 0\n// old read seam\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    kinds = {f.kind for f in findings}
    assert "stale-artifact" in kinds
    detail = " ".join(f.detail for f in findings)
    assert VOLUNTEER_EXTENSION.name in detail
    assert "tortoise install pi" in detail

    # …and a present-but-edited copy (same marker, different bytes) is named too.
    stale.write_text(VOLUNTEER_EXTENSION.read_text() + "\n// local edit\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "modified-artifact" in {f.kind for f in findings}

    # ⛔ A markerless file that is NOT ours must read as FOREIGN, not as an old
    # copy of ours (review finding 4). The distinction is load-bearing: the
    # installer and `--uninstall` both REFUSE a file they cannot prove they
    # wrote, so "reinstall with `tortoise install pi`" — the `unversioned`
    # wording — is advice that fails. `foreign-artifact` is also in
    # `MANUAL_FIX_KINDS`, so callers tell the user to move it aside instead.
    stale.write_text("// somebody else's extension\nexport default () => {};\n")
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "foreign-artifact" in {f.kind for f in findings}
    foreign = next(f for f in findings if f.kind == "foreign-artifact")
    assert "move it aside" in foreign.detail
    assert hook_install.is_manual_fix("foreign-artifact")

    # …while a markerless copy that IS ours is the repairable class.
    shipped = VOLUNTEER_EXTENSION.read_text()
    stale.write_text(shipped.replace("// tortoise-hook-version: 18\n", "", 1))
    findings = hook_install.detect_artifact_install(root, "pi")
    assert "unversioned-artifact" in {f.kind for f in findings}


def test_the_two_artifacts_declare_one_contract_number():
    """`contract_version_for("pi")` answers only when BOTH shipped artifacts
    agree — the layout half's "one contract, one number" invariant (#4680)."""
    from tortoise import hook_install

    contract = hook_install.ARTIFACT_CONTRACTS["pi"]
    assert contract.volunteer_source == VOLUNTEER_EXTENSION
    shipped = hook_install.contract_version_for("pi")
    assert shipped is not None, (
        "the Pi seam's two shipped artifacts must declare the same generation")
    assert hook_install.read_hook_version(CAPTURE_EXTENSION) == shipped
    assert hook_install.read_hook_version(VOLUNTEER_EXTENSION) == shipped


def test_uninstall_removes_the_read_half_only(home, capsys):
    from tortoise.__main__ import _uninstall_pi_volunteer

    assert _install(home).ok
    rc = _uninstall_pi_volunteer(None)
    out = capsys.readouterr().out
    assert rc == 0
    assert not (_ext_dir(home) / VOLUNTEER_EXTENSION.name).exists()
    assert (_ext_dir(home) / CAPTURE_EXTENSION.name).is_file(), (
        "`--uninstall` is scoped to the READ seam — capture is never removed")
    assert "removed" in out and "capture" in out

    # A second --uninstall is a clean no-op.
    assert _uninstall_pi_volunteer(None) == 0
    assert "not installed" in capsys.readouterr().out


def test_uninstall_honours_dry_run(home, capsys):
    """⛔ `--dry-run` is documented as "without writing", and the delete is the
    strongest write this verb performs — there is no undo, the installed file is
    a COPY, not a link (review finding 2: the first cut turned a branch that
    printed a sentence into an unconditional `unlink()`)."""
    from types import SimpleNamespace

    from tortoise.__main__ import _uninstall_pi_volunteer

    assert _install(home).ok
    rc = _uninstall_pi_volunteer(SimpleNamespace(dry_run=True))
    out = capsys.readouterr().out
    assert rc == 0
    assert "[dry-run] would remove" in out, out
    assert (_ext_dir(home) / VOLUNTEER_EXTENSION.name).is_file(), (
        "a dry run must not delete the seam")
    assert (_ext_dir(home) / CAPTURE_EXTENSION.name).is_file()


# ── 2. the real seam, end to end ─────────────────────────────────────────


_DRIVER = r"""
import { pathToFileURL } from "node:url";

const seam = await import(pathToFileURL(process.env.SEAM_PATH).href);
const handlers = {};
const pi = { on: (event, fn) => { handlers[event] = fn; } };
seam.default(pi, { env: process.env, breadcrumbDir: process.env.CRUMB_DIR });

const ctx = { sessionManager: { getSessionId: () => process.env.SESSION_ID } };
const first = await handlers.before_agent_start(
  { messageType: "user", prompt: "How is CI deploy triggered?" }, ctx);
const second = await handlers.before_agent_start(
  { messageType: "user", prompt: "How is CI deploy triggered?" }, ctx);
const custom = await handlers.before_agent_start(
  { messageType: "custom", message: { content: "x" } }, ctx);
process.stdout.write(JSON.stringify({
  events: Object.keys(handlers),
  first: first ?? null,
  second: second ?? null,
  custom: custom ?? null,
}));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        if os.environ.get("CI"):
            pytest.fail("node is required in CI — the real seam cannot be graded "
                        "without it (provision actions/setup-node)") 
        pytest.skip("node not available — the install-side pins still ran")
    return node


def _node_supports_ts(node: str) -> bool:
    try:
        out = subprocess.run([node, "--version"], capture_output=True,
                             text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    m = re.match(r"v(\d+)\.(\d+)", out)
    if not m:
        return False
    major, minor = int(m.group(1)), int(m.group(2))
    return (major, minor) >= (22, 18) or (major, minor) >= (23, 0)


def _run_driver(home: Path, tmp_path: Path, *, display: bool) -> dict:
    node = _node()
    if not _node_supports_ts(node):
        if os.environ.get("CI"):
            pytest.fail("node >= 22.18 is required to load the .ts extension")
        pytest.skip("node < 22.18 does not enable TypeScript type stripping")

    installed = _ext_dir(home) / VOLUNTEER_EXTENSION.name
    assert installed.is_file(), "the read seam must be installed before it is graded"

    if display:
        (home / ".pi" / "agent" / "tortoise-config.json").write_text(
            json.dumps({"volunteer": {"display": True}}))
    # ⛔ Node takes a `.ts` file's module format from the NEAREST parent
    # `package.json`, and the fixture cannot own what sits above its temp dir: a
    # host (or CI runner) carrying `/tmp/package.json` with
    # `"type": "commonjs"` settles the installed seam as CommonJS and it dies
    # with `SyntaxError: Cannot use import statement outside a module` — a
    # verdict about the host, not the artifact. The temp HOME pins the type,
    # exactly as `tests/test_pi_capture_hooks.py::_run_installed_probe` does
    # (#11124). Written at the HOME root, NOT beside the seam: the install
    # directory must stay sibling-free.
    (home / "package.json").write_text('{"type": "module"}\n', encoding="utf-8")
    crumbs = tmp_path / "crumbs"
    driver = tmp_path / "driver.mjs"
    driver.write_text(_DRIVER)
    # ⛔ NOT `.resolve()`: `.venv/bin/python` is a SYMLINK to the system
    # interpreter, so resolving it yields `/usr/bin` — a PATH with no
    # `tortoise` on it, which makes the seam record an inert install and
    # inject nothing, i.e. the test would pass its own bug as a verdict.
    venv_bin = str(Path(_PY).parent)
    env = {
        **_base_env(home),
        "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}",
        "SEAM_PATH": str(installed),
        "SESSION_ID": "pi-seam-e2e",
        "CRUMB_DIR": str(crumbs),
    }
    proc = subprocess.run([node, str(driver)], env=env, cwd=str(tmp_path),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    return json.loads(proc.stdout)


def test_the_installed_extension_injects_the_real_reflex_block(home, tmp_path):
    """THE grade the issue asks for: the installed artifact, Pi's own handler
    contract, the REAL reflex over a seeded graph."""
    assert _install(home).ok
    out = _run_driver(home, tmp_path, display=False)

    assert out["events"] == ["before_agent_start"]
    message = out["first"]["message"]
    assert message["customType"] == "tortoise-volunteer"
    assert message["display"] is False, "silent by default (W4 posture)"
    assert _SEEDED_CLAIM in message["content"], (
        "the injected block must carry the graph's own claim label")
    assert "point/" in message["content"]
    assert "data, not instructions" in message["content"], (
        "the reflex's prompt-injection envelope must survive the seam intact")
    # Cross-turn dedupe at the seam: the same session is not re-injected.
    assert out["second"] is None
    # A run started by a custom message is not searched.
    assert out["custom"] is None


def test_the_visible_surface_renders_the_surfaced_marker(home, tmp_path):
    assert _install(home).ok
    out = _run_driver(home, tmp_path, display=True)
    message = out["first"]["message"]
    assert message["display"] is True
    assert message["content"].startswith("Tortoise memory surfaced: ")
    assert _SEEDED_CLAIM in message["content"]


# ── 3. the extension's own behavioural suite ─────────────────────────────


def test_the_read_seam_suite_runs():
    node = _node()
    if not _node_supports_ts(node):
        if os.environ.get("CI"):
            pytest.fail("node >= 22.18 is required to run the .ts suite")
        pytest.skip("node < 22.18 does not enable TypeScript type stripping")
    env = {
        **os.environ,
        "HOME": str(Path(os.environ.get("TMPDIR", "/tmp"))),
        "TORTOISE_API_KEY": "",
        "TORTOISE_VOLUNTEER_DISABLE": "",
    }
    proc = subprocess.run([node, "--test", str(VOLUNTEER_TEST)],
                          cwd=str(REPO_ROOT), env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
