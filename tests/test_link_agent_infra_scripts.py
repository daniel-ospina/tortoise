"""#9475 — the `scripts` pointer must be machine-local, not a committed absolute symlink.

The tracked blob was mode `120000` with target
`/Users/danielospina/Documents/GitHub/agent-infra/scripts`, so on any machine whose agent-infra
lives elsewhere `scripts/` dangled and every documented `scripts/<tool>.sh` invocation died with
`No such file or directory`. A git symlink blob cannot hold `$AGENT_INFRA_PATH`, so the pointer
cannot be both tracked and correct: it is now gitignored and materialised per machine by
`tools/link_agent_infra_scripts.py`.

Two halves are tested, and they fail for different reasons:

* the REPO half (``TestScriptsPointerIsNotTracked``) — no tracked `scripts` entry, and the path
  is covered by ``.gitignore``. Both assertions fail on the pre-fix tree, which is the point.
* the MECHANISM half (``TestMaterialise``) — the tool creates, repairs and reports the pointer,
  and distinguishes "wrong pointer" (exit 1) from "no agent-infra on this machine" (exit 2).
  That distinction is load-bearing: this issue's own symptom had both causes at once (dangling
  link AND an `$AGENT_INFRA_PATH` checkout 1341 commits behind `origin/main`).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "link_agent_infra_scripts.py"
#: The CI wrapper `.github/workflows/ci.yml`'s `drift-check` job passes as `check-command`
#: (PR #9541 review round 6 P1) — it materialises the pointer, then runs agent-infra's check.
DRIFT_TOOL = REPO_ROOT / "tools" / "agent_infra_drift_check.py"

# The trees a reader consults to learn what the `scripts` pointer IS. `docs/` is deliberately
# OUT of scope: it holds dated records (`docs/scoping/2026-09-17-3863-…` says the pointer "is a
# tracked symlink") that were true when written — history, not guidance.
_CLAIM_SCAN_ROOTS = ("AGENTS.md", ".husky", ".github", "tests", "tools")
_CLAIM_SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".sh", ""}
#: This guard's own message strings quote the claim they forbid, so it skips itself.
_CLAIM_SELF_NAMERS = frozenset({"tests/test_link_agent_infra_scripts.py"})
#: `scripts` asserted to BE (or to have been) a tracked symlink — the claim #9475 falsified.
#: Deliberately anchored on `scripts` first: an aside that only MENTIONS scripts while claiming
#: something about `skills` (e.g. "`skills/` is a tracked symlink … `scripts/` was the same
#: until #9475") is correct and must stay allowed.
_STALE_TRACKED_CLAIM = re.compile(
    r"\bscripts?/?\b[^.;]{0,60}\b(?:is|are|was|were|stays?|remains?)\b[^.;]{0,30}"
    r"\btracked\b[^.;]{0,40}\b(?:symlinks?|120000)\b",
    re.I | re.S,
)
#: The CORRECT shape, which stays allowed: "gitignored, not tracked", "untracked".
_NOT_TRACKED = re.compile(
    r"\b(?:not|never|no\s+longer)\s+(?:\*\*)?tracked\b|\buntracked\b", re.I
)


def run_tool(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the tool as the shell would — exit codes are part of its contract.

    ``cwd`` is explicit for the tests that care about the difference between the CWD and
    ``--repo``: the tool validates the agent-infra candidate against the CWD but writes the
    symlink target under ``--repo``, so a relative ``--infra`` is only caught by a test that
    puts those two in different places.
    """
    # Asserted, not assumed: without this a missing tool makes python exit 2, which every
    # ConfigError assertion below would read as a PASS for the wrong reason (measured on the
    # pre-fix tree, where the tool did not exist yet and one such test passed vacuously).
    assert TOOL.is_file(), f"missing {TOOL} — the materialiser this fix ships"
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=None if cwd is None else str(cwd),
    )


def run_wrapper(
    *args: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the drift-gate wrapper (`tools/agent_infra_drift_check.py`) as the shell would."""
    assert DRIFT_TOOL.is_file(), f"missing {DRIFT_TOOL} — the drift gate's CI entry point"
    return subprocess.run(
        [sys.executable, str(DRIFT_TOOL), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=None if cwd is None else str(cwd),
    )


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


@pytest.fixture()
def fake_infra(tmp_path: Path) -> Path:
    """An agent-infra checkout with the three tools this repo's docs name, plus its CLI."""
    scripts = tmp_path / "agent-infra" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("admin-merge.sh", "record-review.sh", "ci-failure-set.sh"):
        body = scripts / name
        body.write_text(f"#!/bin/sh\necho ran-{name}\n")
        body.chmod(0o755)
    # agent-infra's drift CLI — the wrapper execs it (`check . --ci`). Never actually run by
    # these tests: the `node` on PATH is a recording shim (see `node_shim`).
    bin_dir = tmp_path / "agent-infra" / "bin"
    bin_dir.mkdir()
    (bin_dir / "agent-infra.js").write_text("// agent-infra's CLI\n")
    return tmp_path / "agent-infra"


@pytest.fixture()
def node_shim(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A `node` on PATH that records its argv and exits with `$FAKE_NODE_EXIT`.

    The wrapper must run the checker UNCHANGED (`<infra>/bin/agent-infra.js check . --ci`)
    and must return the checker's verdict; a real node/agent-infra is neither available nor
    wanted in a hermetic test.
    """
    shim_dir = tmp_path / "node-shim"
    shim_dir.mkdir()
    node = shim_dir / "node"
    node.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$FAKE_NODE_ARGS"\nexit "${FAKE_NODE_EXIT:-0}"\n')
    node.chmod(0o755)
    return shim_dir, {
        "FAKE_NODE_ARGS": str(tmp_path / "node-args.txt"),
        "FAKE_NODE_EXIT": "0",
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A throwaway repo root — the tool takes ``--repo``, so nothing touches this checkout."""
    root = tmp_path / "consumer"
    root.mkdir()
    return root


class TestScriptsPointerIsNotTracked:
    """The regression guard: the absolute blob must not come back (#9475)."""

    def test_scripts_is_not_tracked(self) -> None:
        """FAILS before the fix: `git ls-files -s scripts` returned the 120000 blob."""
        tracked = git("ls-files", "--", "scripts").stdout.strip()
        assert tracked == "", (
            "`scripts` is tracked again. It is a MACHINE-LOCAL pointer: a committed symlink "
            "blob cannot hold $AGENT_INFRA_PATH, so it dangles on every other machine and every "
            "documented scripts/<tool>.sh dies with 'No such file or directory' (#9475). "
            "Materialise it instead: `uv run python tools/link_agent_infra_scripts.py`."
        )

    def test_scripts_path_is_gitignored(self) -> None:
        """FAILS before the fix: no ignore rule existed, so the link could be re-added."""
        result = git("check-ignore", "-v", "--", "scripts")
        assert result.returncode == 0, (
            "`scripts` is no longer covered by .gitignore — without the rule the materialised "
            "machine-local symlink gets committed again, which is exactly how #9475 shipped."
        )
        # The rule must be scoped to the repo root, not shadow a nested scripts/ dir elsewhere.
        # `check-ignore -v` prints `<source>:<line>:<rule>\t<path>`.
        rule = result.stdout.split("\t", 1)[0].rsplit(":", 1)[-1]
        assert rule == "/scripts", result.stdout

    def test_documented_tools_are_resolved_through_the_pointer(self) -> None:
        """Whatever the pointer's state, the repo must not claim the tools live in-repo.

        This is the semantic half of the untracking: the tools are agent-infra's, reached
        through `$AGENT_INFRA_PATH`, so a checkout that has no `scripts/` must not be told the
        tool is missing *from the repo* — it must be pointed at the materialiser.
        """
        assert "tools/link_agent_infra_scripts.py" in (REPO_ROOT / "AGENTS.md").read_text(), (
            "AGENTS.md no longer names tools/link_agent_infra_scripts.py. #9475's accepted fix is "
            "'materialise per machine', so the docs must tell an agent how — the pre-fix table "
            "claimed a tracked symlink that resolved on one machine."
        )

    def test_no_comment_still_claims_the_pointer_is_tracked(self) -> None:
        """PR #9541 review ISSUE 2 (P3): a claim that outlives its fact gets re-derived as fact.

        This PR swept the stale "re-link `scripts/`" guidance out of `.husky/pre-commit` and the
        workflow headers, then left the same claim, in the same words, at five sites in three
        files the next lane actually reads:

        * `tests/test_ci_selection.py` — `_readers_of` ("`scripts` is a tracked SYMLINK"),
          `_content_readers_of` (same), and the `_NON_CODE_DOC_ROOTS` comment ("`scripts/` is a
          tracked SYMLINK, so it must be walked on disk").
        * `tests/test_8066_served_skill_relay_ratchet.py` — "`skills` and `scripts` are tracked
          symlinks (mode 120000)".
        * `tests/test_docs_lint_baseline.py` — "`skills/` and `scripts/` are tracked symlinks".

        RED before the fix (measured here: 5 claim matches, in those 3 files). After #9475
        `git ls-files -- scripts` is EMPTY and `/scripts` is gitignored, so "tracked" sends a
        reader looking for a blob that does not exist — and it is precisely the premise the
        tracked blob would need in order to come back. The repo's own standard is that a
        duplicated claim re-stales; the untracked shape ("gitignored, not tracked; materialised
        per machine by `tools/link_agent_infra_scripts.py`") is what must be stated instead.

        SCOPE: a claim-shaped scan of `AGENTS.md`, `.husky/`, `.github/`, `tests/` and `tools/`.
        Prose in `docs/` is dated history and is out of scope; a claim phrased some way this
        pattern does not recognise is not caught here.
        """
        offenders: list[str] = []
        for scan_root in _CLAIM_SCAN_ROOTS:
            base = REPO_ROOT / scan_root
            if base.is_file():
                paths = [base]
            else:
                paths = sorted(
                    p for p in base.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts
                    and p.suffix in _CLAIM_SCAN_SUFFIXES
                )
            for path in paths:
                rel = path.relative_to(REPO_ROOT).as_posix()
                if rel in _CLAIM_SELF_NAMERS:
                    continue
                try:
                    text = path.read_text()
                except (OSError, UnicodeDecodeError):  # binary or unreadable: not a claim
                    continue
                for match in _STALE_TRACKED_CLAIM.finditer(text):
                    if _NOT_TRACKED.search(match.group(0)):
                        continue
                    line = text.count("\n", 0, match.start()) + 1
                    offenders.append(f"{rel}:{line}: {' '.join(match.group(0).split())}")

        assert not offenders, (
            "these still call the `scripts` pointer a TRACKED symlink, which #9475 made false "
            "(`git ls-files -- scripts` is empty and `/scripts` is gitignored — the pointer is "
            "materialised per machine by tools/link_agent_infra_scripts.py):\n  "
            + "\n  ".join(offenders)
            + "\nReword to the machine-local shape — \"gitignored, not tracked; materialised "
            "from $AGENT_INFRA_PATH\" — because a stale claim is how the tracked blob gets "
            "re-derived and re-added."
        )


class TestMaterialise:
    def test_creates_a_pointer_that_resolves_and_runs(
        self, repo: Path, fake_infra: Path
    ) -> None:
        result = run_tool("--repo", str(repo), "--infra", str(fake_infra))
        assert result.returncode == 0, result.stderr

        link = repo / "scripts"
        assert link.is_symlink()
        assert link.resolve() == (fake_infra / "scripts").resolve()
        # The point of the pointer: a documented invocation actually runs.
        ran = subprocess.run(
            ["sh", str(link / "admin-merge.sh")], capture_output=True, text=True
        )
        assert ran.stdout.strip() == "ran-admin-merge.sh"

    def test_is_idempotent(self, repo: Path, fake_infra: Path) -> None:
        run_tool("--repo", str(repo), "--infra", str(fake_infra))
        second = run_tool("--repo", str(repo), "--infra", str(fake_infra))
        assert second.returncode == 0
        assert second.stdout.startswith("ok:"), second.stdout

    def test_repairs_a_dangling_pointer(self, repo: Path, fake_infra: Path) -> None:
        """The exact pre-fix state: a link to a path that does not exist on this machine."""
        (repo / "scripts").symlink_to("/nonexistent/agent-infra/scripts")

        checked = run_tool("--check", "--repo", str(repo), "--infra", str(fake_infra))
        assert checked.returncode == 1, checked.stdout
        assert checked.stdout.startswith("dangling:"), checked.stdout

        repaired = run_tool("--repo", str(repo), "--infra", str(fake_infra))
        assert repaired.returncode == 0
        assert (repo / "scripts" / "record-review.sh").exists()
        assert run_tool("--check", "--repo", str(repo), "--infra", str(fake_infra)).returncode == 0

    def test_repoints_a_wrong_target(self, repo: Path, fake_infra: Path, tmp_path: Path) -> None:
        other = tmp_path / "other-infra" / "scripts"
        other.mkdir(parents=True)
        (repo / "scripts").symlink_to(other)

        checked = run_tool("--check", "--repo", str(repo), "--infra", str(fake_infra))
        assert checked.returncode == 1
        assert checked.stdout.startswith("wrong-target:"), checked.stdout

        assert run_tool("--repo", str(repo), "--infra", str(fake_infra)).returncode == 0
        assert (repo / "scripts").resolve() == (fake_infra / "scripts").resolve()

    def test_relative_infra_writes_a_pointer_that_RESOLVES(
        self, repo: Path, fake_infra: Path, tmp_path: Path
    ) -> None:
        """PR #9541 review P1: a relative target dangles, yet the tool said `relinked`/exit 0.

        `os.symlink` stores its target VERBATIM and a link's target is resolved relative to the
        LINK's own directory, never to the CWD — but `resolve_infra` validated `--infra` against
        the CWD. So `--infra agent-infra` run from ``tmp_path`` wrote the relative target
        ``agent-infra/scripts`` under ``--repo``, where it points at
        ``<repo>/agent-infra/scripts`` and does not exist: the #9475 symptom, reinstated by its
        own fix and reported as a healthy link (and unrepairable — a re-run replaced one dangling
        link with another and still exited 0). RED before the fix at `link.exists()`; the
        `--check` re-read below keeps it red even if the write path changes shape.
        """
        # CWD == tmp_path, --repo == tmp_path/consumer, --infra given RELATIVE to the CWD.
        result = run_tool(
            "--repo", str(repo), "--infra", fake_infra.name, cwd=tmp_path
        )
        assert result.returncode == 0, (result.stdout, result.stderr)
        link = repo / "scripts"
        assert link.is_symlink()
        assert link.exists(), (
            f"the pointer {link} -> {os.readlink(link)} DANGLES: a relative --infra was written "
            "as a relative symlink target, which resolves under the repo, not the CWD. The "
            "tool reported success over a pointer no documented scripts/<tool>.sh can follow."
        )
        assert link.resolve() == (fake_infra / "scripts").resolve()
        # The tool's own assertion mode must agree — this is the half a lane or a gate calls.
        checked = run_tool(
            "--check", "--repo", str(repo), "--infra", fake_infra.name, cwd=tmp_path
        )
        assert checked.returncode == 0, (checked.stdout, checked.stderr)
        assert checked.stdout.startswith("ok:"), checked.stdout

    def test_explicit_infra_is_authoritative_never_a_first_try(
        self, repo: Path, fake_infra: Path, tmp_path: Path
    ) -> None:
        """PR #9541 review P2: an explicit `--infra` must not fall through to the environment.

        `--infra <X>` with no `<X>/scripts` used to be silently DISCARDED in favour of
        `$AGENT_INFRA_PATH`, so `--check --infra <X>` exited 0 having never consulted ``X`` — a
        false green in the assertion mode. RED before the fix: this run resolved to
        ``fake_infra`` and exited 0.
        """
        typo = tmp_path / "typo-not-here"
        typo.mkdir()
        env = {**os.environ, "AGENT_INFRA_PATH": str(fake_infra)}

        checked = run_tool(
            "--check", "--repo", str(repo), "--infra", str(typo), env=env
        )
        assert checked.returncode == 2, (
            checked.returncode,
            checked.stdout,
            checked.stderr,
            "--infra was passed explicitly and has no scripts/ dir, so the answer is a CONFIG "
            "error (exit 2). Exit 0 means the named checkout was discarded and some other one "
            "was used silently.",
        )
        assert str(typo) in checked.stderr, checked.stderr

        # The write path must refuse it too, and change nothing.
        wrote = run_tool("--repo", str(repo), "--infra", str(typo), env=env)
        assert wrote.returncode == 2, (wrote.returncode, wrote.stdout, wrote.stderr)
        assert not os.path.lexists(repo / "scripts"), (
            f"a refused --infra still materialised a pointer: {repo / 'scripts'}"
        )


class TestDiagnostics:
    def test_explicit_infra_without_a_scripts_dir_is_exit_2(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """'Nothing to point at' is a config error, never a link failure.

        Formerly `test_missing_agent_infra_is_exit_2_not_1`, which passed `--infra <empty>` and
        so exercised the EXPLICIT-`--infra` error branch, not the "nothing on this machine"
        fallback its name described (PR #9541 review round 6, minor 1). The fallback branch is
        now covered by `test_nothing_on_this_machine_is_exit_2_and_names_both_candidates`.
        """
        empty = tmp_path / "no-infra-here"
        empty.mkdir()
        # HOME is redirected too: the tool's documented fallback is `~/agent-infra`, so a real
        # checkout there would legitimately answer this question with exit 1 and hide the case.
        home = tmp_path / "home"
        home.mkdir()
        result = run_tool(
            "--check",
            "--repo",
            str(repo),
            "--infra",
            str(empty),
            env={**os.environ, "AGENT_INFRA_PATH": "", "HOME": str(home)},
        )
        assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
        assert "AGENT_INFRA_PATH" in result.stderr

    def test_nothing_on_this_machine_is_exit_2_and_names_both_candidates(
        self, repo: Path, tmp_path: Path
    ) -> None:
        """PR #9541 review round 6, minor 1: the FALLBACK branch had no assertion at all.

        Every other `exit 2` test passes `--infra`, so the "nothing passed explicitly → try
        `$AGENT_INFRA_PATH`, then `~/agent-infra`" loop — the branch a fresh lane with no
        agent-infra actually hits — was only ever reached as a side effect. This asserts it
        directly, including that the message names BOTH candidates, so a regression that drops
        the `~/agent-infra` fallback (or reports it as a link failure, exit 1) is caught.
        """
        home = tmp_path / "home"
        home.mkdir()
        result = run_tool(
            "--repo",
            str(repo),
            env={**os.environ, "AGENT_INFRA_PATH": "", "HOME": str(home)},
        )
        assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
        assert "no agent-infra checkout with a scripts/ directory" in result.stderr, result.stderr
        assert str(home / "agent-infra") in result.stderr, (
            "the fallback's `~/agent-infra` candidate is not named, so the operator cannot tell "
            f"what was tried: {result.stderr}"
        )
        assert not os.path.lexists(repo / "scripts"), (
            "a config error still materialised a pointer"
        )

    def test_stale_checkout_is_named_not_hidden(
        self, repo: Path, fake_infra: Path
    ) -> None:
        """A correct link over a stale checkout must not read as 'the tools are there'.

        Measured before the fix on this lane: `$AGENT_INFRA_PATH` was 1341 commits behind
        `origin/main` (16 of 182 scripts) and had none of the three tools the issue names — so
        repairing the link alone would not have made `scripts/admin-merge.sh` resolve.
        """
        (fake_infra / "scripts" / "admin-merge.sh").unlink()
        result = run_tool("--check", "--repo", str(repo), "--infra", str(fake_infra))
        if result.returncode != 0:  # pointer not materialised yet by this test
            run_tool("--repo", str(repo), "--infra", str(fake_infra))
            result = run_tool("--check", "--repo", str(repo), "--infra", str(fake_infra))

        assert result.returncode == 0, result.stdout
        assert "stale" in result.stderr, result.stderr
        assert "admin-merge.sh" in result.stderr, result.stderr

    def test_refuses_to_replace_a_nonempty_real_directory(
        self, repo: Path, fake_infra: Path
    ) -> None:
        """A vendored copy is a deliberate choice — never silently undone."""
        (repo / "scripts").mkdir()
        (repo / "scripts" / "local-tool.sh").write_text("#!/bin/sh\n")

        result = run_tool("--repo", str(repo), "--infra", str(fake_infra))
        assert result.returncode == 2
        assert (repo / "scripts" / "local-tool.sh").exists()

    def test_replaces_an_empty_real_directory(self, repo: Path, fake_infra: Path) -> None:
        """An empty dir is the residue of a failed setup, not a decision."""
        (repo / "scripts").mkdir()
        result = run_tool("--repo", str(repo), "--infra", str(fake_infra))
        assert result.returncode == 0, result.stderr
        assert (repo / "scripts").is_symlink()

    def test_infra_path_env_is_honoured(self, repo: Path, fake_infra: Path) -> None:
        result = run_tool(
            "--repo", str(repo), env={**os.environ, "AGENT_INFRA_PATH": str(fake_infra)}
        )
        assert result.returncode == 0, result.stderr
        assert (repo / "scripts").resolve() == (fake_infra / "scripts").resolve()
        assert fake_infra.name in result.stdout


class TestPreCommitWiring:
    """PR #9541 review (advisory P2): NOTHING invoked the materialiser.

    Merging #9475 deletes a pointer that resolves on an existing developer host, so every
    existing checkout and lane loses `scripts/` — `scripts/admin-merge.sh`, the documented
    merge rail, included — until someone runs the tool by hand. `agent-infra init/update`
    is not installed on every lane, and the hook only PRINTED the command (and only when
    the SIGPIPE guard was missing or too old). So the hook now materialises the pointer
    itself when it is absent and there is a checkout to point at.

    Pinned by EXTRACTION from the real hook, the `.github/scripts/sigpipe-hook.test.sh`
    precedent: a copied block can drift from the one that actually runs. RED on the
    pre-fix hook, where no such block exists (the extraction below finds nothing).
    """

    HOOK = REPO_ROOT / ".husky" / "pre-commit"

    def _block(self) -> str:
        match = re.search(r"^if \[ ! -e scripts \].*?^fi$", self.HOOK.read_text(), re.S | re.M)
        assert match, (
            f"{self.HOOK.name} no longer materialises the machine-local `scripts` pointer "
            "(#9475: it is gitignored and untracked, so a fresh clone or lane has none). "
            "Nothing else reliably creates it — without this block, merging deletes "
            "`scripts/` from every existing checkout and `scripts/admin-merge.sh` stays dead "
            "until someone runs `uv run python tools/link_agent_infra_scripts.py` by hand."
        )
        return match.group(0)

    def _workspace(self, tmp_path: Path) -> Path:
        """A throwaway repo root holding the hook's block, the tool, and a python shim."""
        root = tmp_path / "hook-workspace"
        (root / "tools").mkdir(parents=True)
        shutil.copy(TOOL, root / "tools" / TOOL.name)
        bindir = root / ".venv" / "bin"
        bindir.mkdir(parents=True)
        shim = bindir / "python"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        shim.chmod(0o755)
        (root / "block.sh").write_text(self._block() + "\n")
        return root

    def test_an_absent_pointer_is_materialised_by_the_hook(
        self, tmp_path: Path, fake_infra: Path
    ) -> None:
        root = self._workspace(tmp_path)
        ran = subprocess.run(
            ["sh", "block.sh"],
            cwd=root,
            capture_output=True,
            text=True,
            env={"AGENT_INFRA_PATH": str(fake_infra), "PATH": "/usr/bin:/bin"},
        )
        assert ran.returncode == 0, (ran.stdout, ran.stderr)
        link = root / "scripts"
        assert link.is_symlink(), (
            f"the hook left `scripts` unmaterialised (stdout={ran.stdout!r}, "
            f"stderr={ran.stderr!r}) — a fresh clone would commit without the merge rail"
        )
        assert link.exists(), f"{link} -> {os.readlink(link)} dangles after the hook ran"
        assert link.resolve() == (fake_infra / "scripts").resolve()

    def test_the_hook_never_blocks_when_there_is_nothing_to_point_at(
        self, tmp_path: Path
    ) -> None:
        """No agent-infra checkout on this machine is an ENVIRONMENT fact, not a commit block."""
        root = self._workspace(tmp_path)
        ran = subprocess.run(
            ["sh", "block.sh"],
            cwd=root,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path / "home")},
        )
        assert ran.returncode == 0, (ran.stdout, ran.stderr)
        assert not os.path.lexists(root / "scripts")


class TestDriftGateCheckCommand:
    """PR #9541 review (round 6, P1): the drift gate's override was invalid SHELL.

    `.github/workflows/ci.yml`'s `drift-check` job calls agent-infra's reusable
    `drift-check.yml` with a `check-command` override (an inline two-statement script that
    materialised the pointer and then ran `node … check . --ci`). The reusable workflow
    consumes that input as `cmd="${{ inputs.check-command }}"` … `bash -c "$cmd"`, and
    `${{ }}` substitution is TEXTUAL — so the override's own `"` closed the assignment early
    and the surviving `(`, in `(#9475)`, was parsed as a subshell: `bash -n` on the composed
    step exits 2 with `syntax error near unexpected token '('`. The step then dies at PARSE
    time, `drift_exit` is never recorded, and the gate takes its no-result branch — red
    forever, on every drift-gated PR, having verified nothing.

    The fix is a single quote-free command (this file's sibling tool), so the ratchet below
    recomposes the step EXACTLY as the reusable workflow does and asserts it parses. It is
    RED on the pre-fix override and GREEN on the single command.
    """

    WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

    def _check_command(self) -> str:
        import yaml  # local: only this class needs to parse a workflow

        workflow = yaml.safe_load(self.WORKFLOW.read_text())
        return workflow["jobs"]["drift-check"]["with"]["check-command"]

    def test_check_command_survives_the_reusable_workflows_interpolation(
        self, tmp_path: Path
    ) -> None:
        """RED before the fix: the composed step was a bash syntax error (exit 2)."""
        command = self._check_command()
        # agent-infra's drift-check.yml@main, verbatim: the input is interpolated inside a
        # double-quoted assignment, then executed with `bash -c "$cmd"`.
        composed = tmp_path / "composed-step.sh"
        composed.write_text(f'cmd="{command}"\nbash -c "$cmd"\n')

        parsed = subprocess.run(["bash", "-n", str(composed)], capture_output=True, text=True)
        assert parsed.returncode == 0, (
            "the `check-command` override does not survive the reusable workflow's textual "
            "`${{ }}` interpolation: a `\"`, `(`, `)` or backtick in it closes "
            '`cmd="${{ inputs.check-command }}"` early, and the step dies at PARSE time. The '
            "gate then reports \"the drift check produced NO result\" forever — red on every "
            "drift-gated PR while verifying nothing. Keep the override a SINGLE quote-free "
            "command (tools/agent_infra_drift_check.py holds the logic).\n"
            f"composed step:\n{composed.read_text()}\nbash -n stderr:\n{parsed.stderr}"
        )

    def test_the_override_is_one_line_and_quote_free(self) -> None:
        """The property that makes the parse above robust, asserted directly."""
        command = self._check_command().strip()
        assert "\n" not in command, (
            f"the drift gate's `check-command` must be ONE line, not a script: {command!r}"
        )
        offenders = sorted({ch for ch in command if ch in "\"'`(){}"})
        assert not offenders, (
            f"the drift gate's `check-command` contains shell quoting characters {offenders} "
            f"({command!r}) — each one can terminate the reusable workflow's `cmd=\"…\"` "
            "assignment early and turn the step into a parse error"
        )
        assert "tools/agent_infra_drift_check.py" in command, (
            f"the override no longer names the wrapper that materialises the pointer and runs "
            f"the check: {command!r}"
        )

    def _env(self, shim: Path, extra: dict[str, str]) -> dict[str, str]:
        return {
            **os.environ,
            "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            **extra,
        }

    def test_the_wrapper_materialises_the_pointer_then_runs_the_check_unchanged(
        self, repo: Path, fake_infra: Path, node_shim: tuple[Path, dict[str, str]]
    ) -> None:
        """The gate keeps its coverage: the pointer is written AND the checker still runs.

        The checker invocation must be the reusable workflow's own default
        (`node <infra>/bin/agent-infra.js check . --ci`) — a wrapper that quietly changed the
        arguments would be the gate bypassing itself.
        """
        shim, extra = node_shim
        env = self._env(shim, extra)
        result = run_wrapper("--repo", str(repo), "--infra", str(fake_infra), env=env)
        assert result.returncode == 0, (result.stdout, result.stderr)

        link = repo / "scripts"
        assert link.is_symlink(), result.stdout
        assert link.resolve() == (fake_infra / "scripts").resolve()

        argv = Path(extra["FAKE_NODE_ARGS"]).read_text().split("\n")[:-1]
        assert argv == [str(fake_infra / "bin" / "agent-infra.js"), "check", ".", "--ci"], argv

    def test_the_checkers_verdict_is_returned_unchanged(
        self, repo: Path, fake_infra: Path, node_shim: tuple[Path, dict[str, str]]
    ) -> None:
        """A drifting repo must still fail the gate — the wrapper never masks the verdict."""
        shim, extra = node_shim
        env = self._env(shim, {**extra, "FAKE_NODE_EXIT": "3"})
        result = run_wrapper("--repo", str(repo), "--infra", str(fake_infra), env=env)
        assert result.returncode == 3, (result.returncode, result.stdout, result.stderr)

    def test_a_failed_materialisation_still_runs_the_check_and_never_reads_green(
        self, repo: Path, fake_infra: Path, node_shim: tuple[Path, dict[str, str]]
    ) -> None:
        """FAIL CLOSED: a vendored copy is refused, the check still runs, and exit is non-zero.

        The pointer is a surface this job gates, so "the checker passed" must not be reported
        as success over a pointer the wrapper failed to write — that is the #9475 symptom
        (a healthy-looking exit over a broken link) one layer up.
        """
        (repo / "scripts").mkdir()
        (repo / "scripts" / "local-tool.sh").write_text("#!/bin/sh\n")
        shim, extra = node_shim
        env = self._env(shim, extra)

        result = run_wrapper("--repo", str(repo), "--infra", str(fake_infra), env=env)
        assert result.returncode != 0, (
            "the wrapper reported success although the `scripts` pointer could not be "
            f"materialised: {result.stdout}"
        )
        # The check still ran — a broken setup must not silently skip the gate.
        assert Path(extra["FAKE_NODE_ARGS"]).exists(), (
            "the wrapper skipped agent-infra's drift check when materialisation failed, so a "
            "broken setup reads as 'no result' instead of naming the problem"
        )
        # And the vendored copy was respected, not deleted.
        assert (repo / "scripts" / "local-tool.sh").exists()
