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
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOL = REPO_ROOT / "tools" / "link_agent_infra_scripts.py"


def run_tool(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the tool as the shell would — exit codes are part of its contract."""
    # Asserted, not assumed: without this a missing tool makes python exit 2, which every
    # ConfigError assertion below would read as a PASS for the wrong reason (measured on the
    # pre-fix tree, where the tool did not exist yet and one such test passed vacuously).
    assert TOOL.is_file(), f"missing {TOOL} — the materialiser this fix ships"
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


@pytest.fixture()
def fake_infra(tmp_path: Path) -> Path:
    """An agent-infra checkout with the three tools this repo's docs name."""
    scripts = tmp_path / "agent-infra" / "scripts"
    scripts.mkdir(parents=True)
    for name in ("admin-merge.sh", "record-review.sh", "ci-failure-set.sh"):
        body = scripts / name
        body.write_text(f"#!/bin/sh\necho ran-{name}\n")
        body.chmod(0o755)
    return tmp_path / "agent-infra"


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


class TestDiagnostics:
    def test_missing_agent_infra_is_exit_2_not_1(self, repo: Path, tmp_path: Path) -> None:
        """'Nothing to point at' is a config error, never a link failure."""
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
