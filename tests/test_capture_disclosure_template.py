"""#3517 — the capture disclosure: ONE source of truth, rendered, never authored.

The disclosure is a SETTING's transparency (epic #1714 B2: a product setting,
default-ON, contract + notice — there is no consent gate and no consent
record), and it is generated from runtime structured data via a **non-LLM
template** because a wrong disclosure is a UX bug (epic #1714 R7 / decision 1).

This file pins the three properties that make that true, none of which can be
asserted by reading the SKILL alone:

* **one home** — every disclosure sentence lives in
  ``tortoise/config/capture_disclosure.json`` and NOT anywhere else, so the
  agent-prompt render and the dashboard render cannot drift (the cross-site
  half is #3513's assertion over the same file);
* **runtime-driven** — the rendered state sentence follows the organization's
  LIVE ``session_recording`` flag; an unread flag is its own wording, never the
  ON default in disguise;
* **the withdrawable contract is withdrawable** — the false-promise grep trips
  on the dead 403 consent contract, with a SEEDED-VIOLATION control proving it
  can trip.

Each test names the mutation that turns it RED.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tortoise import capture_disclosure as cd

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL = REPO_ROOT / "tortoise" / "onboarding" / "SKILL.md"
MIRROR = (REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "skills"
          / "tortoise-onboarding" / "SKILL.md")
MAIN_JSX = REPO_ROOT / "website" / "apps" / "dashboard" / "src" / "main.jsx"


def _static_runs(template: str) -> list[str]:
    """The literal (non-placeholder) runs of a template, longest first.

    Derived from the JSON rather than hand-listed on purpose: a new template
    is covered the moment it lands, and a hand list would have to be
    maintained — which is the drift this test exists to catch.
    """
    runs, current = [], ""
    i = 0
    while i < len(template):
        ch = template[i]
        if ch == "{":
            end = template.find("}", i)
            if end == -1:
                current += ch
                i += 1
                continue
            if len(current) >= 25:
                runs.append(current)
            current = ""
            i = end + 1
            continue
        current += ch
        i += 1
    if len(current) >= 25:
        runs.append(current)
    return sorted(runs, key=len, reverse=True)


class TestSingleSourceOfTruth:
    def test_every_template_has_a_static_run_to_search_for(self):
        """Guard the guard: if the templates were all placeholders, the scan
        below would pass vacuously. Mutation: make a template pure '{x}'."""
        templates = cd.source()["templates"]
        assert set(templates) == {
            "install_disclosure", "installed_confirmation",
            "declined_confirmation", "first_capture_line"}
        for name, template in templates.items():
            assert _static_runs(template), (
                f"{name} has no >=25-char literal run — the single-source scan "
                "below cannot see it")

    def test_no_disclosure_sentence_is_written_anywhere_else(self):
        """The sentences live in the JSON and nowhere else.

        Scans the surfaces that could carry a copy: the live onboarding
        document (both tracked copies — byte-identity is the parity contract)
        and the dashboard source tree. Mutation: paste a rendered sentence
        into `tortoise/onboarding/SKILL.md`.

        ONE exemption, named rather than implied: the `first_capture_line` is
        ALSO quoted in SKILL.md §8 because `test_onboarding_variants.py` pins
        that literal as the W2 copy contract (#2002). It is exempt ONLY in the
        two SKILL copies, and the test below asserts the quote is byte-equal
        to the render — so the exemption is a pinned equality, not a hole.
        """
        scanned = [SKILL, MIRROR, *sorted(
            (REPO_ROOT / "website" / "apps" / "dashboard" / "src")
            .glob("*.js")),
            *sorted((REPO_ROOT / "website" / "apps" / "dashboard" / "src")
                    .glob("*.jsx"))]
        assert len(scanned) > 5, "the scan found no dashboard sources"
        for name, template in cd.source()["templates"].items():
            for sentence in _static_runs(template):
                for path in scanned:
                    if name == "first_capture_line" and path in (SKILL, MIRROR):
                        continue  # pinned by the W2 contract; equality below
                    text = path.read_text(encoding="utf-8")
                    assert sentence not in text, (
                        f"{path.relative_to(REPO_ROOT)} duplicates the "
                        f"disclosure copy {sentence!r} — the sentence belongs "
                        f"ONLY in "
                        f"{cd.SOURCE_PATH.relative_to(REPO_ROOT)} (single "
                        "source of truth: a copy is the drift the file exists "
                        "to prevent)")

    def test_the_w2_pinned_line_in_the_skill_equals_the_template(self):
        """The one exemption above is a PINNED EQUALITY, not a hole.

        SKILL.md §8 quotes the W2 copy contract literal (#2002). If either
        side is edited alone the two diverge and this reds — so the quote
        cannot drift away from the template it is supposed to render.
        Mutation: change the template wording without editing SKILL.md §8.
        """
        rendered = cd.render_first_capture_line(
            harness="claude", hosted=True, api_host=None)
        assert f'> "{rendered}"' in SKILL.read_text(encoding="utf-8"), (
            "SKILL.md §8 no longer quotes the template's first_capture_line "
            f"verbatim (template renders: {rendered!r})")
        assert rendered in MIRROR.read_text(encoding="utf-8")

    def test_the_skill_points_at_the_renderer_for_every_branch(self):
        """The agent prompt must name the renderer, not restate the copy.

        Mutation: replace a `tortoise session disclosure` invocation with the
        sentence it prints.
        """
        text = SKILL.read_text(encoding="utf-8")
        for command in (
            "tortoise session disclosure --harness <harness>",
            "tortoise session disclosure --harness <harness> --kind confirmation",
            "tortoise session disclosure --harness <harness> --kind declined",
        ):
            assert command in text, (
                f"the onboarding prompt does not print the renderer for one of "
                f"its branches: {command!r} is missing")

    def test_the_source_ships_in_the_wheel(self):
        """`tortoise session disclosure` resolves its source from the PACKAGE.

        Mutation: drop `config/capture_disclosure.json` from
        `[tool.setuptools.package-data]` — a wheel install then fails to render
        the one surface that exists to be accurate.
        """
        import fnmatch

        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            patterns = tomllib.load(fh)["tool"]["setuptools"]["package-data"][
                "tortoise"]
        rel = str(cd.SOURCE_PATH.relative_to(REPO_ROOT / "tortoise"))
        assert any(fnmatch.fnmatch(rel, p) for p in patterns), (
            f"{rel} is not matched by any package-data pattern {patterns}")


class TestRuntimeDrivenRender:
    def test_the_state_sentence_follows_the_live_flag(self):
        """The disclosure must never claim ON for an org whose setting is OFF.

        Mutation: render `state_on` unconditionally (drop the
        `recording is False` branch) — the OFF case then reads as ON and this
        test reds.
        """
        off = cd.render_install_disclosure(
            harness="pi", hosted=True, api_host="api.example.com", recording=False)
        assert "is OFF" in off and "nothing is filed" in off, off
        assert "is ON" not in off, (
            "an org with recording disabled is told it is ON — the exact "
            "accuracy defect the non-LLM template exists to prevent")
        on = cd.render_install_disclosure(
            harness="pi", hosted=True, api_host="api.example.com", recording=True)
        assert "is ON" in on, on
        unknown = cd.render_install_disclosure(
            harness="pi", hosted=True, api_host="api.example.com", recording=None)
        assert "could not read" in unknown and "is ON by default" in unknown, (
            "an unread flag must be its OWN wording — not the ON default in "
            "disguise")

    def test_the_rendered_facts_name_where_sessions_go_and_where_to_stop(self):
        """Every fact the user needs is present: what is recorded, the beacon,
        the destination, the off-switch and the view/delete home."""
        text = cd.render_install_disclosure(
            harness="pi", hosted=True, api_host="api.example.com", recording=True)
        for fact in ("What is recorded", "Also sent at the start",
                     "Where it goes", "Turn it off", "See or delete",
                     "api.example.com"):
            assert fact in text, f"the disclosure omits {fact!r}: {text}"

    def test_the_first_capture_line_is_the_pinned_w6_wording(self):
        """The once-per-org line (§8) keeps the shipped wording while its home
        moves into the JSON. Mutation: paraphrase the template."""
        assert cd.render_first_capture_line(
            harness="pi", hosted=True, api_host="api.example.com") == (
            "Heads up: I'll remember this session so you can recall it later. "
            "View/delete in Settings → Captured sessions.")

    def test_host_of_never_leaks_userinfo_or_a_path(self):
        """A disclosure names WHERE sessions go — never the credential.

        Mutation: return `api_url` verbatim; the userinfo assertion reds.
        """
        assert cd.host_of("https://user:secret@api.example.com:8443/mcp/") == (
            "api.example.com:8443")
        assert cd.host_of("http://localhost:8000/mcp") == "localhost:8000"
        assert cd.host_of(None) is None
        assert cd.host_of("") is None


class TestOffSwitchConstantMatchesTheRealSettingsPath:
    def test_the_off_switch_anchor_exists_in_the_dashboard(self):
        """The shared constant must name a home that actually exists.

        The off-switch label is a load-bearing UX fact that can drift after a
        UI rename, so the anchor id is asserted against the dashboard source.
        Mutation: rename `id="settings-memory-heading"` in `main.jsx`.
        """
        jsx = MAIN_JSX.read_text(encoding="utf-8")
        off = cd.source()["destinations"]["off_switch"]
        view = cd.source()["destinations"]["view_delete"]
        assert f'id="{off["hosted_heading_id"]}"' in jsx, (
            f"the off-switch label {off['hosted_label']!r} names "
            f"{off['hosted_heading_id']!r}, which is not an anchor in main.jsx")
        assert f'id="{view["hosted_heading_id"]}"' in jsx, (
            f"the view/delete label {view['hosted_label']!r} names "
            f"{view['hosted_heading_id']!r}, which is not an anchor in main.jsx")
        # the label's LAST segment is the section the anchor renders.
        assert off["hosted_label"].endswith("Agent sessions")

    def test_the_two_homes_stay_distinct(self):
        """Off-switch and view/delete are SIBLING homes (#2180) — a disclosure
        that sends the user to the view/delete home for a toggle is the
        documented UX bug."""
        d = cd.source()["destinations"]
        assert d["off_switch"]["hosted_label"] != d["view_delete"]["hosted_label"]


class TestFalsePromiseGrep:
    def test_the_withdrawn_403_contract_trips_the_grep(self):
        """SEEDED VIOLATION: the withdrawn 403 consent wording must FAIL.

        This is the control that proves the live-tree assertion below can
        fail at all. Mutation: narrow the pattern until this fixture passes.
        """
        seeded = (
            "un-opted team POST /v1/sessions → 403 AND MCP tool → 403; the "
            "flag IS the consent, so the data plane refuses with a 403.")
        hits = cd.false_promise_hits(seeded)
        assert hits, "the seeded withdrawn-403 fixture did not trip the grep"
        assert any(name == "withdrawn-403-consent-gate" for _, name, _ in hits)

    def test_the_withdrawn_keys_trip_the_grep(self):
        """The dropped consent-record keys are named explicitly."""
        hits = cd.false_promise_hits(
            "seed capture_opt_in and session_recording_opted_in_at")
        assert {name for _, name, _ in hits} == {"withdrawn-consent-key"}

    def test_live_consent_gated_wording_is_NOT_a_false_promise(self):
        """#3615's client transmission authorization is LIVE and correct —
        the grep must not flag it, or the gate becomes noise nobody reads.
        Mutation: broaden the pattern to bare `consent`."""
        assert cd.false_promise_hits(
            "consent-gated paths fail closed on TORTOISE_CAPTURE=1 opt-in") == []

    def test_a_research_brief_needs_the_banner_above_the_first_hit(
            self, tmp_path):
        """A historical brief may quote the dead contract, but only BELOW the
        banner — a reader must meet the retraction first.

        Mutation: place the marker after the first hit (or drop it).
        """
        from tortoise import capture_disclosure as cdx

        research = tmp_path / "docs" / "research"
        research.mkdir(parents=True)
        live = tmp_path / "tortoise" / "onboarding"
        live.mkdir(parents=True)
        for rel in cdx.LIVE_CONTRACT_FILES:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("clean live contract\n", encoding="utf-8")
        brief = research / "1714-demo.md"
        hit = "an un-opted team gets a 403 and the flag IS the consent\n"

        brief.write_text("intro\n" + hit, encoding="utf-8")
        findings = cdx.audit_false_promises(tmp_path)
        assert any("1714-demo.md" in f and "banner above it" in f
                   for f in findings), findings

        brief.write_text(f"**{cdx.WITHDRAWAL_MARKER}**\nintro\n" + hit,
                         encoding="utf-8")
        assert cdx.audit_false_promises(tmp_path) == []

        # and the marker AFTER the hit is not coverage
        brief.write_text(hit + f"**{cdx.WITHDRAWAL_MARKER}**\n",
                         encoding="utf-8")
        assert any("banner above it" in f
                   for f in cdx.audit_false_promises(tmp_path))

    def test_a_live_contract_file_may_never_quote_it(self, tmp_path):
        """The live SKILL copies have no exemption at all."""
        from tortoise import capture_disclosure as cdx

        for rel in cdx.LIVE_CONTRACT_FILES:
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"**{cdx.WITHDRAWAL_MARKER}**\n"
                         "the flag IS the consent (403)\n", encoding="utf-8")
        findings = cdx.audit_false_promises(tmp_path)
        assert len(findings) == len(cdx.LIVE_CONTRACT_FILES), findings
        assert all("LIVE contract file" in f for f in findings), findings

    def test_the_real_tree_is_clean(self):
        """The live gate: no live contract states the withdrawn 403 contract,
        and every research brief that quotes it carries the banner."""
        assert cd.audit_false_promises(REPO_ROOT) == []

    def test_every_hit_carrying_brief_is_selected_by_ci(self):
        """A brief that quotes the dead contract must select the surface whose
        guard reads it — otherwise a docs-only PR that re-adds the contract (or
        deletes the banner) runs no test at all.

        The predicate is DERIVED from the audit, so it is a ratchet rather than
        a hand-maintained list: a new brief that starts quoting the withdrawn
        contract reds here until it is registered in `SOURCE_PATTERNS`.
        Mutation: drop the `docs/research/1714-solution-converge.md` entry.
        """
        from tools.ci_selection import SOURCE_PATTERNS

        patterns = [p for pats in SOURCE_PATTERNS.values() for p in pats]
        missed = []
        for path in sorted(REPO_ROOT.glob(cd.RESEARCH_GLOB)):
            if not cd.false_promise_hits(path.read_text(encoding="utf-8")):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if not any(rel.startswith(p) for p in patterns):
                missed.append(rel)
        assert not missed, (
            f"{missed} quote the withdrawn 403 consent contract but are not in "
            "tools/ci_selection.py's SOURCE_PATTERNS — a docs-only PR editing "
            "them selects no surface, so the false-promise gate never runs")


class TestCliRender:
    """The renderer the SKILL names must actually run in the install
    environment, which has no hosted key yet.

    One test drives the REAL CLI process (the honest end-to-end path the SKILL
    invokes); the branch cases call the handler in-process with an isolated
    HOME — four more `python -m tortoise` subprocesses would cost ~10 s each
    for no extra coverage.
    """

    @pytest.fixture()
    def keyless(self, tmp_path, monkeypatch):
        """No credential, no endpoint, no ambient config — the self-hosted
        shape an install actually has at ask time."""
        for var in ("TORTOISE_API_KEY", "TORTOISE_API_URL",
                    "TORTOISE_MCP_API_KEY"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        return tmp_path

    def _invoke(self, capsys, **argv):
        import argparse

        from tortoise.__main__ import _cmd_session_disclosure

        args = argparse.Namespace(**{"harness": "claude", "kind": "disclosure",
                                     "json": False, **argv})
        rc = _cmd_session_disclosure(args)
        out, _err = capsys.readouterr()
        return rc, out.rstrip("\n")

    def test_renders_without_a_hosted_config(self, tmp_path):
        """A self-hosted or not-yet-configured install still gets the facts.

        Mutation: gate the subcommand behind the api_key check (the pattern the
        other `session` verbs use) — the CLI then exits 1 instead of printing.
        """
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(tmp_path)}
        r = subprocess.run(
            [sys.executable, "-m", "tortoise", "session", "disclosure",
             "--harness", "pi"],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
            timeout=180)
        assert r.returncode == 0, (r.stdout, r.stderr)
        assert "What is recorded" in r.stdout
        assert "your own Tortoise store" in r.stdout, r.stdout

    @pytest.mark.parametrize("kind", ["disclosure", "confirmation", "declined"])
    def test_every_branch_kind_renders(self, keyless, capsys, kind):
        """The three branches the SKILL names are all reachable."""
        rc, out = self._invoke(capsys, harness="pi", kind=kind)
        assert rc == 0
        assert out.strip(), f"kind {kind} rendered nothing"

    def test_every_kind_matches_the_template_it_names(self, keyless, capsys):
        """`--kind` selects the template it says it does — the SKILL calls
        `--kind confirmation` to name the off-switch and `--kind declined` to
        confirm the no-branch; a crossed selector would print the wrong
        branch's sentence."""
        rendered = {}
        for kind in ("disclosure", "confirmation", "declined"):
            _rc, rendered[kind] = self._invoke(capsys, harness="pi", kind=kind)
        assert rendered["confirmation"] == cd.render_installed_confirmation(
            harness="pi", hosted=False)
        assert rendered["declined"] == cd.render_declined_confirmation(
            harness="pi", hosted=False)
        assert rendered["disclosure"] == cd.render_install_disclosure(
            harness="pi", hosted=False)
        assert len(set(rendered.values())) == 3, "two kinds rendered the same text"

    def test_json_carries_the_declared_step_accounting(self, keyless, capsys):
        """The machine-readable form states the `capture-disclosed`
        accounting, so a caller can assert it without parsing prose.

        Mutation: make `step_accounting()['writes']` True — the render's
        declaration then contradicts the capture path's single-writer claim.
        """
        rc, out = self._invoke(capsys, harness="pi", json=True)
        assert rc == 0
        body = json.loads(out)
        assert body["step_accounting"]["step"] == "capture-disclosed"
        assert body["step_accounting"]["consumes"] is False
        assert body["step_accounting"]["writes"] is False
        assert body["harness"] == "pi"
        assert body["hosted"] is False, (
            "a keyless install must not claim the hosted variant")
        assert body["kind"] == "disclosure"
        assert body["text"] == body["disclosure"]
