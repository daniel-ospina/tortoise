"""#4914 — the SERVED onboarding document must be byte-checked, not marker-checked.

The onboarding copy family has three members:

  1. ``tortoise/onboarding/SKILL.md`` — the canonical reference.
  2. ``website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md`` — the
     tracked served mirror, asserted byte-identical to (1).
  3. ``https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md`` — the
     bytes an agent ACTUALLY fetches.

Copy 3 is produced by ``dist`` copying ``public/``, so it is correct *by
construction*. Construction is not observation. A deploy that fails or only
partially publishes, a CDN/cache anomaly, or a custom domain pointing at the
wrong Pages project (``deploy-pages.yml`` records ``tortoise.premiselabs.co``
previously serving ``tortoise-landing-v2``) leaves the OLD document live while
every repo-side check stays green.

The pre-existing coverage does not close this: ``welcome-e2e-monitor`` fetches
the URL but asserts CONTENT MARKERS (``text/markdown``, ``---``, ``name:``, a
few tokens), so a drift that preserves those markers is invisible — and that
monitor cannot fail a release.

These tests pin the GUARD, not the deployment: they assert the deploy step still
compares BYTES, still prints both digests, and still fails closed. Deleting or
weakening the step reds here.

# Known residual (stated, not implied): the step compares copy 3 against copy 1.
# The other three served skills (how-to-use-tortoise, tortoise-decide,
# tortoise-file-finding) are served from the same tree and are equally
# unobserved; their canonical lives in the MIT repo (#4643), so widening needs a
# different comparison source. That is #4914's stated scope boundary, not a gap
# this guard silently pretends to cover.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-pages.yml"

STEP_NAME = "The served onboarding document is byte-identical to the canonical"
SERVED_URL = "https://app.premiselabs.co/skills/tortoise-onboarding/SKILL.md"
CANONICAL = "tortoise/onboarding/SKILL.md"


def _workflow_text() -> str:
    assert WORKFLOW.is_file(), f"missing workflow: {WORKFLOW}"
    return WORKFLOW.read_text(encoding="utf-8")


def _step_block(text: str) -> str:
    """The YAML block for the served-skill step, from its name to the next step.

    Slicing on `      - name:` keeps this independent of PyYAML's `on:` ->
    ``True`` coercion and of any later reformatting that preserves the list
    shape.
    """
    start = text.find(STEP_NAME)
    assert start != -1, (
        f"the deploy no longer carries a step named {STEP_NAME!r} — #4914's "
        "third-copy guard has been removed"
    )
    nxt = text.find("\n      - ", start)
    return text[start:] if nxt == -1 else text[start:nxt]


def test_the_deploy_has_a_served_skill_byte_parity_step() -> None:
    block = _step_block(_workflow_text())
    assert SERVED_URL in block, (
        "the step must fetch the URL an agent actually follows (#4914)"
    )
    assert CANONICAL in block, (
        "the step must compare against the canonical repo copy"
    )


def test_the_step_compares_bytes_not_markers() -> None:
    """The whole point: digests, not substring markers.

    Markers are what the pre-existing monitor already checks, and a drift that
    preserves them is exactly the class this guard exists to catch.
    """
    block = _step_block(_workflow_text())
    assert "sha256sum" in block, (
        "the step must hash the served bytes — marker assertions cannot see a "
        "drift that preserves the markers (#4914)"
    )
    # It must hash BOTH sides, not just the payload.
    assert block.count("sha256sum") >= 2, (
        "both the canonical and the served document must be hashed"
    )


def test_the_step_prints_both_digests_so_green_proves_the_bytes() -> None:
    """A green run must PROVE the served bytes, not assert it in prose."""
    block = _step_block(_workflow_text())
    assert re.search(r'echo[^\n]*canonical[^\n]*\$EXPECTED', block), (
        "the step must print the canonical digest"
    )
    assert re.search(r'echo[^\n]*served[^\n]*\$SERVED', block), (
        "the step must print the served digest"
    )


def test_the_step_is_fail_closed_with_the_sibling_bundle_polls_policy() -> None:
    """Identical failure semantics to the sibling bundle poll in the same job."""
    block = _step_block(_workflow_text())
    assert re.search(r"^\s*MAX_ATTEMPTS=(\d+)", block, re.MULTILINE), (
        "the retry bound must be stated explicitly"
    )
    bound = int(
        re.search(r"^\s*MAX_ATTEMPTS=(\d+)", block, re.MULTILINE).group(1)
    )
    assert bound == 20, f"expected the sibling's 20 attempts, got {bound}"
    assert "sleep 30" in block, "expected the sibling's 30s backoff"
    assert re.search(r"^\s*exit 1", block, re.MULTILINE), (
        "a mismatch must RED THE DEPLOY, not warn — a monitor that cannot fail "
        "a release is the gap #4914 filed"
    )


def test_the_step_has_no_cache_buster_branch() -> None:
    """The 2026-09-23 ruling rejects an 'edge stale vs not published' branch.

    It is unimplementable (no per-deployment origin is captured; the ``pages.dev``
    project origin returns the SAME ETag) and unnecessary (the served path
    measures ``cf-cache-status: DYNAMIC``). A cache-buster reappearing here would
    be re-litigating a settled ruling, which is why it is pinned.
    """
    block = _step_block(_workflow_text())
    forbidden = [
        r"\?cb=",
        r"\?t=",
        r"cache[-_ ]?bust",
        r"cache-control:\s*no-cache",
        r"-H ['\"]Cache-Control",
    ]
    hits = [p for p in forbidden if re.search(p, block, re.IGNORECASE)]
    assert not hits, (
        "the 2026-09-23 ruling rejects a cache-buster branch in this step; "
        f"found {hits}"
    )


def test_the_step_runs_after_the_bundle_poll_in_the_same_job() -> None:
    """Ordering is the contract: it is a POST-deploy check.

    A copy of this comparison placed before the upload would assert the build,
    not the published artifact — the precise gap #4914 describes.
    """
    text = _workflow_text()
    poll = text.find("dashboard bundle not live after")
    served = text.find(STEP_NAME)
    assert poll != -1, "the sibling bundle poll is gone — ordering cannot be checked"
    assert served != -1
    assert served > poll, (
        "the served-skill check must run AFTER the post-deploy bundle poll; "
        "placed earlier it cannot observe the published artifact"
    )


@pytest.mark.parametrize(
    "skill",
    [
        "how-to-use-tortoise",
        "tortoise-decide",
        "tortoise-file-finding",
    ],
)
def test_the_uncovered_served_skills_are_named_not_silently_ignored(skill: str) -> None:
    """#4914's scope boundary, made explicit rather than implied.

    This guard covers tortoise-onboarding only. If someone widens it, this test
    is where they should flip the expectation — the point is that the omission
    is VISIBLE, not that it is permanent.
    """
    block = _step_block(_workflow_text())
    assert skill not in block, (
        f"{skill!r} is now covered by the served-byte guard — update #4914's "
        "residual note (and this test) rather than leaving the record stale"
    )
