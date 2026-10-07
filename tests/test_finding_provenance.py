"""Hermetic tests for tools/finding_provenance.py (#4290).

No network, no Docker, no FalkorDB. Every case runs against a REAL temp git
repo with a known shape — a base commit, a fix commit, a post-fix commit, and a
remote-tracking ``origin/main`` ref — so the gate's ancestry tests are exercised
against actual git objects, not mocks.

Run standalone:      python3 tests/test_finding_provenance.py
Run under pytest:    TORTOISE_TEST_CARVE_OUT=1 python3 -m pytest tests/test_finding_provenance.py -q
                     (the file opens no graph, but the #1647 P4 session gate
                     still demands a DB URI or the carve-out opt-in — see
                     AGENTS.md's testing section for the docker lane)

The two falsification directions the gate must satisfy (#4290 ask 4):

  (a) IT FLAGS a finding measured against a tree that predates the fix —
      ``PREDATES_FIX`` for the exact B2 shape, ``STALE`` for the B5 shape, and
      ``UNKNOWN_PROVENANCE`` when the finding carries no date at all.
  (b) IT DOES NOT FLAG a correct, current finding — including the case that
      makes EQUALITY the wrong check: a measured SHA that is neither the fix
      nor the base, but CONTAINS both, must pass (``test_containing_tree...``).

The live-incident tests at the bottom replay B2 against this repository's own
history (``65b26f6c2``) when the commit is present, and skip otherwise.

The COMMENT path (#4732) has its own falsification pair, because the gate's
trigger now covers ``issue_comment`` and the failure mode is the opposite one —
an over-eager gate that flags every comment becomes noise. So the comment tests
assert BOTH directions of the CLASSIFIER: a finding comment (with a stale
``Measured at:`` line, with a malformed one, or with no line at all but the
``<!-- finding -->`` marker) is selected and flagged, and an ordinary
discussion comment is routed away without ever being validated.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "finding_provenance.py"
WORKFLOW = ROOT / ".github" / "workflows" / "finding-provenance.yml"
# The GitHub Actions expression opener (`${{`), assembled from parts so this
# module can assert on its ABSENCE in a `run:` block without the literal
# opener appearing here as something a reader has to brace-count.
ACTIONS_EXPR = "$" + "{" + "{"
PYTHON = sys.executable

# The B2 fix: "re-cut the MCP/SDK baseline with main's move-invariant
# fingerprint". A finding measured before it that claims `co_firstlineno` is
# still in the fingerprint is the incident #4290 describes.
B2_FIX = "65b26f6c2"


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )


def _commit(cwd: Path, filename: str, content: str) -> str:
    (cwd / filename).write_text(content, encoding="utf-8")
    _run(cwd, "add", filename)
    r = subprocess.run(
        ["git", "-C", str(cwd),
         "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false",
         "commit", "-m", f"add {filename}"],
        capture_output=True, text=True, check=False,
    )
    assert r.returncode == 0, r.stderr
    return _run(cwd, "rev-parse", "HEAD").stdout.strip()


def _provenance(ref: str, sha: str) -> str:
    return f"Measured at: {ref}@{sha} on 2026-09-20\n"


class FindingProvenanceTestBase(unittest.TestCase):
    """A temp repo with: BASE <- FIX <- TIP, and origin/main -> TIP."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="finding-provenance.")
        cls.repo = Path(cls._tmp.name)
        r = _run(cls.repo, "init", "-b", "main")
        assert r.returncode == 0, r.stderr
        cls.base = _commit(cls.repo, "base.txt", "base\n")
        cls.fix = _commit(cls.repo, "fix.txt", "the fix\n")
        cls.tip = _commit(cls.repo, "later.txt", "later work\n")
        # origin/main is a REMOTE-TRACKING ref, created locally — no network.
        _run(cls.repo, "update-ref", "refs/remotes/origin/main", cls.tip)
        # A fix that exists but is NOT on the measured tree's line — an
        # unmerged fix-branch commit. This is the ONLY shape in which the
        # fix-ancestry test is reachable independently of the base test: if the
        # fix is on origin/main, a fresh measurement already contains it.
        _run(cls.repo, "checkout", "-q", "-b", "pending-fix", cls.tip)
        cls.pending_fix = _commit(cls.repo, "pending.txt", "pending fix\n")
        _run(cls.repo, "checkout", "-q", "main")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def invoke(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(TOOL), "--repo", str(self.repo), *args],
            capture_output=True, text=True, input=stdin, check=False,
        )

    def route_comment(self, body: str) -> int | None:
        """Replay the workflow's COMMENT routing (#4732).

        Mirrors the two steps exactly: ``--is-finding`` selects the body (exit
        0) or does not (exit 1); only a SELECTED body reaches ``--validate``,
        whose exit code is the gate's. ``None`` means the comment was never
        gated — it passes untouched and unannotated. The classifier assertion
        is what makes the noise arm real: a gate that flagged every comment
        would return an int here for an ordinary comment.
        """
        selected = self.invoke("--is-finding", "-", stdin=body)
        self.assertIn(selected.returncode, (0, 1),
                      f"classifier must answer yes/no: {selected.stdout}{selected.stderr}")
        if selected.returncode == 1:
            return None
        return self.invoke("--validate", "-", stdin=body).returncode

    # ── (a) IT FLAGS a finding measured against a tree that predates the fix

    def test_predates_fix_is_flagged(self) -> None:
        """B2's exact shape: a fix exists off the measured line, and the
        measurement predates it. The measured tree is CURRENT against the base
        (so the base test does not fire) yet does not contain the fix -> FAIL.

        This isolates the fix-ancestry test: an unfixed-but-current tree is
        not enough when a fix is known to exist elsewhere."""
        r = self.invoke("--validate", "-", "--fix", self.pending_fix,
                        stdin=_provenance("origin/main", self.tip))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PREDATES_FIX", r.stdout)
        self.assertIn(self.pending_fix[:12], r.stdout)

    def test_predates_fix_on_an_older_base_is_flagged(self) -> None:
        """Measured exactly at the fix's parent, with the base pinned there
        too: the freshness test passes (A is its own ancestor) and the fix
        test is the one that refuses."""
        r = self.invoke("--validate", "-", "--base", self.base,
                        "--fix", self.fix,
                        stdin=_provenance("stale-branch", self.base))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PREDATES_FIX", r.stdout)

    def test_stale_against_current_base_is_flagged(self) -> None:
        """B5's shape: measured on a tree that never took origin/main -> FAIL."""
        r = self.invoke("--validate", "-",
                        stdin=_provenance("main", self.base))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("STALE", r.stdout)
        # the gap is reported, not just the verdict
        self.assertIn("behind by 2 commit(s)", r.stdout)

    def test_missing_provenance_is_not_a_pass(self) -> None:
        """A free-text-less finding cannot be dated -> FAIL, never exit 0."""
        r = self.invoke("--validate", "-",
                        stdin="A finding with no provenance at all.\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("UNKNOWN_PROVENANCE", r.stdout)

    def test_unresolvable_measured_sha_is_not_a_pass(self) -> None:
        """A well-formed line naming a commit this repo does not have -> FAIL."""
        ghost = "0" * 40
        r = self.invoke("--validate", "-",
                        stdin=_provenance("main", ghost))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("UNKNOWN_PROVENANCE", r.stdout)

    def test_malformed_line_is_not_a_pass(self) -> None:
        for body in (
            "Measured at: main@abc123 on 2026-09-20\n",  # short SHA
            f"Measured at: main@{self.tip}\n",  # no date
            f"measured at: main@{self.tip} on 2026-09-20\n",  # wrong case
        ):
            with self.subTest(body=body):
                r = self.invoke("--validate", "-", stdin=body)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertIn("UNKNOWN_PROVENANCE", r.stdout)

    def test_multiple_provenance_lines_is_not_a_pass(self) -> None:
        """An appended second line must not leave a stale FIRST line silently
        authoritative, and a quoted older finding must not be mistaken for this
        one. Ambiguity is a FAIL, never a first-match."""
        body = (_provenance("main", self.base)
                + "\nquoted from an older report:\n"
                + _provenance("ancestor", self.tip))
        r = self.invoke("--validate", "-", stdin=body)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("AMBIGUOUS_PROVENANCE", r.stdout)

    def test_a_backtick_label_parses_and_is_defended_at_reflection(self) -> None:
        """Round-2 P1: the ref label must stay permissive (legal refs contain
        `#`, `+`, …), so a fence-looking label PARSES — the injection defence
        moved to the workflow, which indents the verdict instead of fencing it.
        Pinned by test_workflow_indents_the_verdict_block."""
        r = self.invoke("--validate", "-",
                        stdin=f"Measured at: ```@{self.tip} on 2026-09-20\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)

    # ── (b) IT DOES NOT FLAG a correct, current finding

    def test_measured_at_current_base_passes(self) -> None:
        """A finding measured AT origin/main (== base) passes with a --fix the
        tree contains. Measured == base here, so this case pins the
        base-equality/reflexive path; the anti-equality case is the
        descendant test below, where measured equals neither."""
        r = self.invoke("--validate", "-", "--fix", self.fix,
                        stdin=_provenance("origin/main", self.tip))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)
        self.assertEqual(
            _run(self.repo, "rev-parse", "origin/main").stdout.strip(), self.tip,
            "this case is only meaningful while measured == the base ref")
        self.assertNotEqual(self.tip, self.fix)

    def test_descendant_of_base_passes(self) -> None:
        """THE anti-equality case. A finding measured on a feature branch
        rebased onto current main is a commit that CONTAINS origin/main and the
        fix but EQUALS neither — an equality check would flag it stale; the
        ancestry check must not."""
        _run(self.repo, "checkout", "-q", "-b", "rebased-branch", self.tip)
        try:
            feat = _commit(self.repo, "feature-on-main.txt", "feature\n")
        finally:
            _run(self.repo, "checkout", "-q", "main")
        r = self.invoke("--validate", "-", "--fix", self.fix,
                        stdin=_provenance("rebased-branch", feat))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)
        # measured != base and measured != fix, yet it contains both — the
        # three SHAs are pairwise distinct, which is the point of the case.
        self.assertNotEqual(feat, self.tip)
        self.assertNotEqual(feat, self.fix)
        self.assertNotEqual(self.tip, self.fix)

    def test_measured_equal_to_fix_is_reflexively_current(self) -> None:
        """`merge-base --is-ancestor A A` is 0 — measuring exactly at the fix
        is current (reflexive ancestry; a strict `<` would wrongly exclude the
        fix commit itself from its own fix)."""
        r = self.invoke("--validate", "-", "--fix", self.fix, "--base", self.fix,
                        stdin=_provenance("fix", self.fix))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)

    # ── the COMMENT path (#4732)

    def test_is_finding_classifier_contract(self) -> None:
        """``--is-finding`` is a CLASSIFIER, not a verdict: 0 = the body claims
        to be a finding (validate it), 1 = ordinary discussion (leave it), 2 =
        the body could not be read. Frozen here so the comment gate's selector
        and its verdict can never be conflated."""
        for body in (
            _provenance("main", self.base),
            "**Measured at:** `2b8ad274f` on my branch\n",
            "> Measured at: main@" + self.tip + " on 2026-09-20\n",
            "- Measured at 2b8ad274f\n",
            "<!-- finding -->\n\nPosting this without a line.\n",
            "<!-- finding-provenance -->\n",
        ):
            with self.subTest(body=body):
                r = self.invoke("--is-finding", "-", stdin=body)
                self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for body in (
            "Thanks — merging now.",
            "## Rail verdict: refused (not merged)\n\nThe refusal is correct.",
            # Prose, not a claim: line-anchored matching is what keeps a
            # discussion comment from being gated on the word "measured".
            "As measured at runtime, the loop is hot, but that is by design.",
            "",
        ):
            with self.subTest(body=body):
                r = self.invoke("--is-finding", "-", stdin=body)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        missing = self.invoke("--is-finding", str(self.repo / "no-such-comment.md"))
        self.assertEqual(missing.returncode, 2, missing.stdout + missing.stderr)

    def test_comment_path_flags_a_stale_finding_comment(self) -> None:
        """(a) STALE arm: a finding comment measured on a tree that never took
        origin/main is selected and flagged with the gate's exit 1 — the code
        that annotates the comment."""
        body = "## Finding: the rename table drifted\n\n" + _provenance("main", self.base)
        self.assertEqual(self.route_comment(body), 1,
                         "a stale finding comment must be annotated")
        self.assertIn("STALE", self.invoke("--validate", "-", stdin=body).stdout)

    def test_comment_path_flags_a_finding_comment_missing_the_line(self) -> None:
        """(a) ABSENT arm — the shape that actually occurred (#4732): findings
        posted as comments carrying NO `Measured at:` line at all. The explicit
        `<!-- finding -->` marker selects it, so it is flagged
        UNKNOWN_PROVENANCE instead of slipping through as discussion."""
        body = "<!-- finding -->\n\nThe rename table's line numbers are stale.\n"
        self.assertEqual(self.route_comment(body), 1)
        self.assertIn("UNKNOWN_PROVENANCE",
                      self.invoke("--validate", "-", stdin=body).stdout)

    def test_comment_path_flags_a_loose_provenance_claim(self) -> None:
        """A bolded/quoted `Measured at` line (the form this repo's own
        scoping docs use) is still a CLAIM — selected, then refused as UNKNOWN
        by the strict parser, rather than treated as discussion."""
        body = "**Measured at:** `2b8ad274f` on my branch fix/4732\n"
        self.assertEqual(self.route_comment(body), 1)
        self.assertIn("UNKNOWN_PROVENANCE",
                      self.invoke("--validate", "-", stdin=body).stdout)

    def test_comment_path_skips_an_ordinary_discussion_comment(self) -> None:
        """(b) THE NOISE ARM. An ordinary comment must be routed AWAY WITHOUT
        VALIDATION. The assertion is on the classifier, so this test fails on a
        gate that flags every comment (such a gate would return an int here)."""
        for body in (
            "Thanks — merging now.",
            "## Rail verdict: refused (not merged)\n\nRefusal is correct; recorded.",
            "As measured at runtime, the loop is hot, but that is by design.",
            "",
        ):
            with self.subTest(body=body):
                self.assertIsNone(self.route_comment(body),
                                  f"discussion comment must never be gated: {body!r}")

    def test_comment_path_passes_a_current_finding_comment(self) -> None:
        """A claiming comment that IS current returns 0 — no annotation, so
        the live-state behaviour carries over to the new path."""
        self.assertEqual(self.route_comment(_provenance("origin/main", self.tip)), 0)

    # ── the emit / checkout verbs (ask 1 + ask 2)

    def test_emit_round_trips_through_validate(self) -> None:
        emitted = self.invoke("--emit")
        self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
        self.assertRegex(emitted.stdout, r"Measured at: \S+@[0-9a-f]{40} on \d{4}-\d{2}-\d{2}")
        r = self.invoke("--validate", "-", stdin=emitted.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_checkout_reports_the_gap(self) -> None:
        _run(self.repo, "checkout", "-q", "-b", "behind-gap", self.base)
        self.addCleanup(_run, self.repo, "checkout", "-q", "main")
        stale = self.invoke("--checkout")
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        self.assertIn("STALE", stale.stdout)
        self.assertIn("2 behind origin/main", stale.stdout)

        _run(self.repo, "checkout", "-q", "main")
        current = self.invoke("--checkout")
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertIn("CURRENT", current.stdout)

    def test_emit_warns_when_checkout_is_behind(self) -> None:
        """Self-contained (round-2 P2: the earlier version relied on a sibling
        test having created the `behind` branch — it failed in isolation)."""
        _run(self.repo, "checkout", "-q", "-b", "behind-emit", self.base)
        self.addCleanup(_run, self.repo, "checkout", "-q", "main")
        r = self.invoke("--emit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("behind origin/main", r.stderr)

    def test_emit_round_trips_on_a_special_character_branch(self) -> None:
        """Round-2 P1: a legal ref may contain `#`, `+`, … — an over-narrow
        label class made --emit print a line its own --validate then rejected,
        reddening a CURRENT finding on such a branch."""
        branch = "fix/#4290+prov"
        _run(self.repo, "checkout", "-q", "-b", branch, self.tip)
        self.addCleanup(_run, self.repo, "checkout", "-q", "main")
        emitted = self.invoke("--emit")
        self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
        self.assertIn(f"Measured at: {branch}@", emitted.stdout)
        r = self.invoke("--validate", "-", stdin=emitted.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)

    def test_emit_marks_a_dirty_worktree_in_the_line(self) -> None:
        """Round-2 P2: a stderr warning only reaches the reporter. The DIRTY
        marker rides IN the line so it travels to the reader, and --validate
        reports it without refusing (a finding about uncommitted state is
        legitimate — it just cannot be dated to a clean commit)."""
        dirty = self.repo / "uncommitted.txt"
        dirty.write_text("dirty\n", encoding="utf-8")
        self.addCleanup(dirty.unlink)
        emitted = self.invoke("--emit")
        self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
        self.assertIn("(DIRTY)", emitted.stdout)
        self.assertIn("DIRTY", emitted.stderr)
        r = self.invoke("--validate", "-", stdin=emitted.stdout)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)
        self.assertIn("DIRTY", r.stderr)
        import json as _json
        j = self.invoke("--validate", "-", "--json", stdin=emitted.stdout)
        self.assertEqual(j.returncode, 0, j.stdout + j.stderr)
        self.assertTrue(_json.loads(j.stdout)["dirty"])

    def test_workflow_gate_cannot_be_a_no_op(self) -> None:
        """#4290 review P0/P2: a piped gate (`cmd | tee`) returns tee's status,
        so the step could never fail and the whole workflow was a no-op. Pin
        that the verdict is captured with an explicit rc and PROPAGATED, that
        no bare pipe is used, and that annotation is bound to the tool's exit
        code (1 = stale finding) rather than to any job failure — exit 2 is an
        environment fault and must NOT stamp the stale label."""
        wf = (ROOT / ".github" / "workflows" / "finding-provenance.yml").read_text(
            encoding="utf-8")
        self.assertIn("shell: bash", wf)
        self.assertIn("rc=$?", wf)
        self.assertIn('exit "$rc"', wf)
        # Every step that branches on the gate's rc MUST carry a status
        # function (`always()`): Actions wraps a bare `if:` in an implicit
        # success(), and the gate step exits non-zero — so without `always()`
        # those steps are skipped exactly when they must fire (round-3 P1,
        # the #2189 / e2e-live-reconcile.yml:79 trap).
        rc_ifs = [ln.strip() for ln in wf.splitlines()
                  if "steps.gate.outputs.rc" in ln and ln.strip().startswith("if:")]
        self.assertEqual(len(rc_ifs), 3, f"expected 3 rc-branches, got {rc_ifs}")
        for cond in rc_ifs:
            self.assertIn("always()", cond,
                          f"rc branch lacks always(): {cond}")
        self.assertIn("always() && steps.gate.outputs.rc == '1'", wf)
        self.assertIn("always() && steps.gate.outputs.rc == '2'", wf)
        self.assertIn("always() && steps.gate.outputs.rc == '0'", wf)
        # The gate step's `run:` must not pipe the validator (a pipe's status
        # is the last command's). Slice from the step to the next step.
        gate = wf.split("name: Gate the finding's provenance", 1)[1]
        gate = gate.split("- name: Annotate a stale", 1)[0]
        code_lines = [ln for ln in gate.splitlines()
                      if not ln.strip().startswith("#")]
        self.assertFalse([ln for ln in code_lines if "| tee" in ln],
                         "the gate step must not pipe the validator into tee")
        tool_lines = [ln for ln in gate.splitlines()
                      if "finding_provenance.py --validate" in ln]
        self.assertTrue(tool_lines, "the gate step must invoke the validator")
        self.assertNotIn("|", tool_lines[0],
                         "the validator must not be piped; its own exit code is the gate")

    def test_workflow_indents_the_verdict_block(self) -> None:
        """Round-2 P1: the ref label is untrusted and echoed into a bot
        comment. The defence is at the REFLECTION POINT — the verdict is
        4-space INDENTED (a fence can be closed by content; an indented block
        cannot) — not by over-restricting the ref."""
        wf = (ROOT / ".github" / "workflows" / "finding-provenance.yml").read_text(
            encoding="utf-8")
        self.assertIn("sed 's/^/    /'", wf)
        annotate = wf.split("name: Annotate a stale", 1)[1]
        annotate = annotate.split("name: Report an unmeasurable", 1)[0]
        self.assertIn("sed 's/^/    /' \"$RUNNER_TEMP/verdict.txt\"", annotate)

    def test_workflow_gates_a_claim_before_exempting_beta_feedback(self) -> None:
        """Round-2 P1: testing `beta-feedback` FIRST exempted every issue filed
        through bug_report.yml (which stamps that label), making the form's
        measured_at field unreachable and giving an internal finding a
        one-token escape. A body that CLAIMS a measurement is gated whatever
        its labels; the exemption applies only to a report with no line."""
        wf = (ROOT / ".github" / "workflows" / "finding-provenance.yml").read_text(
            encoding="utf-8")
        decide = wf.split("Decide whether this issue is a finding", 1)[1]
        decide = decide.split("Gate the finding's provenance", 1)[0]
        self.assertLess(decide.index("Measured at:"), decide.index("beta-feedback"),
                        "the provenance check must precede the beta-feedback exemption")

    # ── the COMMENT path's workflow shape (#4732)

    def test_workflow_has_the_issue_comment_trigger(self) -> None:
        """The gate must FIRE on a comment, not only on `opened`. `on:` parses
        as the boolean True key in YAML 1.1, which is why it is looked up both
        ways rather than assumed."""
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        triggers = wf["on"] if "on" in wf else wf[True]
        self.assertIn("issue_comment", triggers)
        self.assertEqual(triggers["issue_comment"]["types"], ["created"])
        # the body path is unchanged — this is an ADDITION, not a replacement
        self.assertEqual(triggers["issues"]["types"], ["opened"])

    def test_workflow_never_interpolates_event_text_into_a_run_block(self) -> None:
        """#4732 SECURITY. The comment trigger runs the gate on text ANYONE can
        write. `${{ github.event.comment.body }}` (or any event field) inside a
        `run:` block is the classic script-injection sink: `$(…)`, backticks and
        quotes in the comment become shell. So NO expression at all may appear
        in run source — the body reaches the script as a FILE fetched by its
        numeric id, which carries no shell metacharacters."""
        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for job_name, job in wf["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run") or ""
                if ACTIONS_EXPR in run:
                    offenders.append(
                        f"{job_name}: step {step.get('name')!r} interpolates "
                        f"an expression into run source")
        self.assertFalse(offenders, offenders)
        # The body must reach the tool from a FILE, fetched by id — not from
        # the event context. Numeric ids carry no shell metacharacters, so the
        # only untrusted text in play is DATA read off disk.
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("issues/comments/$COMMENT_ID", text)
        code = "\n".join(ln for ln in text.splitlines()
                         if not ln.lstrip().startswith("#"))
        self.assertNotIn("comment.body", code,
                         "the comment body must never be referenced from YAML code "
                         "— it reaches the tool only as a file fetched by id")

    def test_workflow_comment_path_classifies_before_it_gates(self) -> None:
        """The comment branch must SELECT with `--is-finding` (so discussion is
        never validated), and it must come before the label-based exemptions —
        a comment carries no labels to read."""
        decide = WORKFLOW.read_text(encoding="utf-8").split(
            "Decide whether this issue is a finding", 1)[1]
        decide = decide.split("Gate the finding's provenance", 1)[0]
        self.assertIn("--is-finding", decide)
        self.assertLess(decide.index("--is-finding"), decide.index("beta-feedback"))
        self.assertLess(decide.index('"$KIND" = "comment"'),
                        decide.index("beta-feedback"))

    # ── environment errors are exit 2, never a silent pass

    def test_unresolvable_base_is_an_environment_error(self) -> None:
        r = self.invoke("--validate", "-", "--base", "origin/nope",
                        stdin=_provenance("main", self.tip))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_unresolvable_fix_is_an_environment_error(self) -> None:
        r = self.invoke("--validate", "-", "--fix", "deadbeefdeadbeef",
                        stdin=_provenance("origin/main", self.tip))
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)

    def test_not_a_git_repo_is_an_environment_error(self) -> None:
        """Exit 2, never exit 1: a caller branching on the code must not read
        'could not measure' as 'measured and stale' (#4290 review P2)."""
        with tempfile.TemporaryDirectory(prefix="finding-provenance-plain.") as d:
            r = subprocess.run(
                [PYTHON, str(TOOL), "--repo", d, "--checkout"],
                capture_output=True, text=True, check=False)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("not a git repository", r.stderr)

    def test_json_output_carries_the_verdict(self) -> None:
        import json as _json
        stale = self.invoke("--validate", "-", "--json",
                            stdin=_provenance("main", self.base))
        self.assertEqual(stale.returncode, 1, stale.stdout + stale.stderr)
        payload = _json.loads(stale.stdout)
        self.assertEqual(payload["status"], "STALE")
        self.assertEqual(payload["behind"], 2)

        current = self.invoke("--validate", "-", "--json",
                              stdin=_provenance("origin/main", self.tip))
        self.assertEqual(current.returncode, 0, current.stdout + current.stderr)
        self.assertEqual(_json.loads(current.stdout)["status"], "CURRENT")


class LiveIncidentTest(unittest.TestCase):
    """Replay B2 against THIS repository's history, when the fix is present.

    The finding text is the incident's own claim (surface-guard._fingerprint
    still uses co_firstlineno). Measured one commit before 65b26f6c2 it must be
    flagged PREDATES_FIX; measured AT the fix it must be CURRENT. Skipped on a
    shallow checkout that lacks the commit — the temp-repo suite above covers
    the same logic hermetically.
    """

    def setUp(self) -> None:
        def resolves(rev: str) -> bool:
            r = subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "--verify", "--quiet",
                 f"{rev}^{{commit}}"],
                capture_output=True, text=True, check=False,
            )
            return r.returncode == 0
        if not resolves(B2_FIX) or not resolves(f"{B2_FIX}^"):
            self.skipTest(f"{B2_FIX} not in this checkout (shallow clone)")

    def invoke(self, body: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(TOOL), "--repo", str(ROOT), "--validate", "-",
             "--base", f"{B2_FIX}^", "--fix", B2_FIX],
            capture_output=True, text=True, input=body, check=False,
        )

    def test_live_prefix_measurement_is_flagged(self) -> None:
        prefix = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", f"{B2_FIX}^"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        r = self.invoke(f"Measured at: main@{prefix} on 2026-09-19\n")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("PREDATES_FIX", r.stdout)

    def test_live_fixed_measurement_is_current(self) -> None:
        fix_sha = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", B2_FIX],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        r = self.invoke(f"Measured at: origin/main@{fix_sha} on 2026-09-19\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CURRENT", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
