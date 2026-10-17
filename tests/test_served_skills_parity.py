"""#4643 — the served-skill mirror is gated against the MIT repo that authors it.

The defect this file closes: `daniel-ospina/tortoise-skills-and-integrations`
(MIT, public) is the authored source of `how-to-use-tortoise`,
`tortoise-decide` and `tortoise-file-finding`; the dashboard serves a **derived**
copy at `app.premiselabs.co/skills/<name>/SKILL.md`; and **nothing compared
them**. Measured 2026-09-21 and again 2026-10-16, the two channels had drifted
(632 vs 690 lines, 105 vs 148), so the artifact a customer vendors was not the
artifact we believed we served.

Two halves, and the split is deliberate:

* **offline** (`test_4643_the_served_mirror_equals_the_pinned_upstream_bytes`) —
  the served bytes equal the PIN. Deterministic, needs no network, runs per-PR
  via the `onboarding` + `core` surfaces and `tier1`.
* **non-vacuity** (the `check_upstream` tests below) — the pin is HONEST (the
  pinned commit really hashes to it) and CURRENT (the bytes at upstream `main`
  HEAD are still the pinned bytes) and has the right SCOPE (upstream `skills/`
  is exactly the upstream directories the pin's `upstream_path`s bind, so an
  upstream ADDITION/REMOVAL is reported rather
  than invisible). A pin-only gate is vacuous — pin any old commit and
  "byte-identical" becomes true of history — so these tests prove the currency
  arm is wired to the pin rather than the other way round. They monkeypatch the
  network seams (`remote_head`, `fetch_raw`, `list_upstream_skills`), so the
  suite stays offline.

WIRING (target 4). `--check-upstream` must have a runner, or the whole
upstream half is decoration: a served copy edited in place *plus* a hand-cut pin
hash used to pass everything. Two runners execute it — the `deploy-dashboard`
job of `deploy-pages.yml` and the daily `skills-parity.yml` — and the assertions
below derive that fact by EXECUTING each step's real `run:` block (recording
interpreter stubs on `PATH`, #4494's lesson) rather than by reading its text, so
`echo …`, `… || true`, `… | tee …` and `continue-on-error:` are not mistaken
for a gate. Because `_job_syncs` executes EVERY step of the owning job, the
execution is hermetic and measured to be so: PATH carries only a throwaway stub
dir, every other command is answered by `command_not_found_handle` (recorded,
exit 0) instead of the real binary, and a timeout is returned as status 124
rather than raised — otherwise a per-PR run of this suite reached the network,
slept for real, and errored out on Linux (#8862 review, P0 + P1). Both runners
are asserted, so landing only one of the two reds.
`docs/license-notes.md` §7.1 must name
whichever runners exist, and must name no runner that does not.

The pin itself carries no assertion here: `test_4643_the_pin_covers_exactly_the_
installed_set` ties it to the installer's `SKILLS=(...)` array, so a fourth
served-and-installed skill cannot appear without a pin (and a pinned skill
cannot be dropped from the installer) — the same ratchet shape
`tests/test_served_skills.py` uses for the served set.

"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import served_skills_parity as sp

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / sp.MANIFEST_REL
INSTALLER = (REPO / "website" / "apps" / "dashboard" / "public"
             / "install-tortoise-skills.sh")


def _installer_skills() -> list[str]:
    """The installer's shipped set — the artifact a customer actually gets."""
    m = re.search(r"^SKILLS=\(([^)]*)\)",
                  INSTALLER.read_text(encoding="utf-8"), re.M)
    assert m, "installer SKILLS=(...) array not found"
    return m.group(1).split()


def _mirror_root(tmp_path: Path, manifest: dict) -> Path:
    """A throwaway root holding the REAL served bytes at their real paths."""
    for entry in manifest["skills"].values():
        dest = tmp_path / entry["served_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / entry["served_path"], dest)
    return tmp_path


# ── the gate that runs per-PR (offline, deterministic) ─────────────────────

def test_4643_the_served_mirror_equals_the_pinned_upstream_bytes():
    """THE gate. Byte-identity over `read_bytes()`, fail-closed (#3673's
    contract, restated here for this family): a missing copy or an unreadable
    pin must FAIL rather than skip — a gate that quietly skips when a side is
    unreadable reports health it never observed."""
    manifest = sp.load_manifest(MANIFEST)
    problems = sp.check_local(manifest, REPO)
    assert problems == [], (
        "the served-skill mirror drifted from its pin (#4643).\n"
        + "\n".join(f"  - {p}" for p in problems)
        + "\n  The mirror is DERIVED: re-derive it with "
          "`uv run python tools/served_skills_parity.py --sync`.")


def test_4643_the_pin_covers_exactly_the_installed_set():
    """Ratchet: the pin's skills == the installer's `SKILLS=(...)` — and both
    equal the served tree. Without this, a fourth installed skill could ship
    ungated (invisible to the parity gate) or a pinned skill could be dropped
    from the installer while the pin kept checking bytes nobody downloads."""
    manifest = sp.load_manifest(MANIFEST)
    pinned = sorted(manifest["skills"])
    assert pinned == sorted(_installer_skills()), (
        f"the pin covers {pinned} but the installer ships "
        f"{sorted(_installer_skills())} — a served+installed skill with no pin "
        "is ungated (#4643 target 1)")
    for name, entry in manifest["skills"].items():
        assert (REPO / entry["served_path"]).is_file(), (
            f"{name} is pinned but not served at {entry['served_path']}")
        assert entry["served_path"] == (
            f"website/apps/dashboard/public/skills/{name}/SKILL.md"), (
            f"{name}: the served path must be the path the installer downloads")


def test_4643_the_pin_records_a_real_commit_and_content_hash():
    """Shape guard only — the LIVE honesty check is `--check-upstream` (and the
    monkeypatched arm below). Asserted here so an obvious placeholder (`HEAD`,
    `TODO`, a 7-char short sha that cannot be fetched by raw URL) fails in the
    fast suite instead of at a deploy."""
    manifest = sp.load_manifest(MANIFEST)
    commit = manifest["upstream"]["commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", commit), (
        f"the pin's commit must be a full 40-hex sha (raw URLs need the full "
        f"sha): {commit!r}")
    assert manifest["upstream"]["repo"].count("/") == 1
    for name, entry in manifest["skills"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), (
            f"{name}: sha256 must be 64 lowercase hex chars, got "
            f"{entry['sha256']!r}")


def test_4643_section_71_states_the_scope_rule_the_code_implements():
    """§7.1 is the section a contributor audits this gate against. It described
    the scope arm as an equality on the pin's KEYS while the code binds the
    upstream DIRECTORY from `upstream_path` (a served name may be shortened), so
    the two authoritative descriptions of the same check disagreed — the exact
    class this PR already fixed once (review finding, P2). Pin the sentence to
    the code so it cannot rot back."""
    rule = " ".join(_section_71().split())
    assert "upstream_path" in rule, (
        "§7.1 states the scope rule without naming `upstream_path`: the code "
        "binds the upstream DIRECTORY from `upstream_path`, and the section is "
        "what a contributor audits the gate against")
    assert "the pin's keys" not in rule, (
        "§7.1 still describes the scope arm as an equality on the pin's KEYS; "
        "the code binds by `upstream_path`, and a pin whose served name differs "
        "from the directory is legal")


def test_4643_the_generator_keeps_the_out_of_scope_declaration(tmp_path):
    """Both red messages print `--sync` as the remedy, and `sync()` rewrites the
    pin THROUGH `dump_manifest`. So a regression in the `out_of_scope:` emission
    makes `--sync` silently erase the very declaration the gate demands, and the
    next run reds again — with no test to notice (review finding, P2: the only
    dump assertion used the real pin, which declares nothing, so deleting the
    emission block left the suite green)."""
    manifest = sp.load_manifest(MANIFEST)
    manifest["out_of_scope"] = ["research-domain", "another-unserved"]
    rendered = sp.dump_manifest(manifest)
    assert "out_of_scope:" in rendered, (
        "`dump_manifest` dropped the `out_of_scope:` block — `--sync` would "
        "erase the declaration the scope arm demands, and the next run would "
        "red on a skill the operator had already recorded")
    # Sorted: a re-cut must not reorder the list, or the real change hides in
    # the diff of an unrelated `--sync`.
    assert rendered.index("  - another-unserved") < rendered.index(
        "  - research-domain"), "`out_of_scope:` must be emitted sorted"
    # Round-trip: what the generator writes must LOAD back to the same list —
    # the emission being present is not enough if it is not valid pin syntax.
    pin = tmp_path / "served-skills-upstream.yml"
    pin.write_text(rendered, encoding="utf-8")
    assert sp.load_manifest(pin).get("out_of_scope") == [
        "another-unserved", "research-domain"], (
        "the rendered `out_of_scope:` does not survive a round-trip through "
        "`load_manifest` — `--sync` would write a pin the gate cannot read")


def test_4643_an_undeclared_directory_is_not_called_an_addition():
    """A directory in neither the pin nor `out_of_scope:` reds either way, but a
    skill that has been upstream since BEFORE the pin was never "added" — the
    message must not send the operator looking for an upstream commit that does
    not exist (review finding, P2)."""
    manifest = sp.load_manifest(MANIFEST)
    upstream = manifest["upstream"]
    at_pin = sorted(set(sp._upstream_dirs(manifest)) | {"old-unserved"})

    def _listing(repo, sha, attempts, backoff):
        return list(at_pin)

    original = sp.list_upstream_skills
    sp.list_upstream_skills = _listing
    try:
        problems = sp._check_scope(manifest, upstream["commit"], 1, 0.0)
    finally:
        sp.list_upstream_skills = original
    text = "\n".join(problems)
    assert "old-unserved" in text, f"the undeclared directory went unreported: {text}"
    assert "not a new addition" in text, (
        f"a directory present at the pinned commit was reported as an "
        f"addition: {text}")


def test_4643_the_pin_is_exactly_what_the_generator_writes():
    """The pin says GENERATED by `--sync`. If it is not byte-identical to
    `dump_manifest(load_manifest(pin))`, someone hand-edited it — the residue
    that makes a later `--sync` diff reviewable garbage. It also keeps the pin's
    header honest about what the currency arm does."""
    manifest = sp.load_manifest(MANIFEST)
    assert MANIFEST.read_text(encoding="utf-8") == sp.dump_manifest(manifest), (
        "config/served-skills-upstream.yml is not what `--sync` generates — "
        "re-cut it with `uv run python tools/served_skills_parity.py --sync`")


# ── the fail-closed directions (watched failing, not asserted in prose) ────

def test_4643_a_served_copy_edited_in_place_is_caught(tmp_path):
    """The direction that needs no network: the mirror is DERIVED, so an edit
    here is a drift. This is the `#2315`-missing-from-the-served-copy shape
    (#4341), inverted."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    victim = manifest["skills"]["tortoise-decide"]["served_path"]
    (root / victim).write_bytes((root / victim).read_bytes() + b"\nedited here\n")

    problems = sp.check_local(manifest, root)
    assert len(problems) == 1, problems
    assert "tortoise-decide" in problems[0] and "DRIFTED" in problems[0]
    assert "--sync" in problems[0], (
        "a drift message must name the derivation command — the failure mode it "
        "replaces is a human hand-copying bytes between two repos")


def test_4643_a_missing_served_copy_is_a_failure_not_a_skip(tmp_path):
    """#3673 "0 fail-open paths": the served path is what the installer
    downloads, so its absence 404s the install flow — the gate must SAY that,
    not pass because it had nothing to compare."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    (root / manifest["skills"]["how-to-use-tortoise"]["served_path"]).unlink()

    problems = sp.check_local(manifest, root)
    assert len(problems) == 1, problems
    assert "MISSING" in problems[0] and "how-to-use-tortoise" in problems[0]


def _must_change(text: str, needle: str, replacement: str) -> str:
    """A mutation that silently no-ops leaves a VALID pin, so the case reds as
    `DID NOT RAISE` — opaque, and on a required `tier1` gate. Keying the needle
    on the LIVE pin (not a `--sync` output frozen into a literal) plus this
    assertion makes a stale mutation fail with its reason instead."""
    assert needle in text, (
        f"this mutation keys on {needle!r}, which is no longer in the pin — the "
        "pin was re-cut, so the replacement would mutate nothing and the case "
        "would fail as an opaque 'DID NOT RAISE'. Refresh the needle.")
    return text.replace(needle, replacement)


@pytest.mark.parametrize("mutation, expect", [
    ("absent", "is missing"),
    ("not_a_mapping", "must be a mapping"),
    ("no_upstream", "must carry `upstream:`"),
    ("no_skills", "must carry `upstream:`"),
    ("empty_skills", "must carry `upstream:`"),
    ("no_commit", "upstream.commit"),
    ("no_sha256", "skills.tortoise-decide.sha256"),
    ("bytes_not_int", "not an integer"),
    ("entry_not_mapping", "skills.tortoise-decide must be a mapping"),
    ("path_not_a_string", "upstream_path must be a non-empty string"),
    ("out_of_scope_not_a_list", "must be a list"),
    ("out_of_scope_overlaps_pin", "BOTH pinned and out_of_scope"),
])
def test_4643_an_unreadable_pin_fails_closed(tmp_path, mutation, expect):
    """Every malformed pin RAISES. The failure a lenient parse would produce is
    a gate that compares nothing and exits 0 — i.e. green where it is blind."""
    path = tmp_path / "pin.yml"
    if mutation != "absent":
        text = MANIFEST.read_text(encoding="utf-8")
        # The needle values come from the pin as it is NOW: a hardcoded
        # `sha256:`/`bytes:` pair is `--sync` output, so the day upstream edits
        # that skill the replacement no-ops and this case reds for the wrong
        # reason (review finding, P3).
        decide = sp.load_manifest(MANIFEST)["skills"]["tortoise-decide"]
        decide_bytes = f"    bytes: {int(decide['bytes'])}"
        if mutation == "not_a_mapping":
            text = "- a\n- list\n"
        elif mutation == "no_upstream":
            text = _must_change(text, "upstream:\n  repo:", "notupstream:\n  repo:")
        elif mutation == "no_skills":
            text = text.split("skills:")[0]
        elif mutation == "empty_skills":
            text = text.split("skills:")[0] + "skills: {}\n"
        elif mutation == "no_commit":
            text = _must_change(text, "  commit: ", "  commitsha: ")
        elif mutation == "no_sha256":
            text = _must_change(
                text, f"    sha256: {decide['sha256']}\n{decide_bytes}\n",
                decide_bytes + "\n")
        elif mutation == "bytes_not_int":
            text = _must_change(text, decide_bytes, "    bytes: many")
        elif mutation == "entry_not_mapping":
            # A purpose-built VALID document whose `skills.<name>` is a scalar:
            # `str(['a']).strip()` is truthy, so a truthiness-based validator
            # accepted a non-mapping entry and only failed later at a malformed
            # raw URL (the mutation caught exactly that).
            text = (
                "version: 1\n"
                "upstream:\n"
                "  repo: daniel-ospina/tortoise-skills-and-integrations\n"
                "  branch: main\n"
                '  commit: "0123456789abcdef0123456789abcdef01234567"\n'
                "skills:\n"
                "  tortoise-decide: 42\n")
        elif mutation == "path_not_a_string":
            text = _must_change(
                text, f"    upstream_path: {decide['upstream_path']}",
                f"    upstream_path: [{decide['upstream_path']}]")
        elif mutation == "out_of_scope_not_a_list":
            text = text + "out_of_scope: research-domain\n"
        elif mutation == "out_of_scope_overlaps_pin":
            # A skill cannot be both served and excluded: that declaration reads
            # as an exclusion while the pin still fetches and compares it.
            text = text + "out_of_scope:\n  - tortoise-decide\n"
        path.write_text(text, encoding="utf-8")

    with pytest.raises(sp.ParityError, match=re.escape(expect)):
        sp.load_manifest(path)


def test_4643_the_pins_byte_count_is_checked_not_decorative(monkeypatch, tmp_path):
    """FAIL-CLOSED ON THE PIN'S OWN METADATA (review finding, P3/P2).

    `bytes:` is independent pin metadata, so a hand-edited count beside a correct
    `sha256:` is internally inconsistent and must be caught — on BOTH arms. It is
    not unreachable-by-collision: the payload matches the digest, the count does
    not. Before this, `--check-upstream` never read `bytes:` at all, so a wrong
    count shipped silently and the module docstring's "a byte count that
    disagrees with the pin is a FAILURE" was an untested claim.
    """
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)

    victim = manifest["skills"]["tortoise-decide"]
    victim["bytes"] = int(victim["bytes"]) + 1  # digest still matches

    local = sp.check_local(manifest, root)
    assert len(local) == 1, local
    assert "tortoise-decide" in local[0] and "byte count disagrees" in local[0]

    upstream = sp.check_upstream(manifest, root)
    assert len(upstream) == 1, upstream
    assert "byte count is WRONG" in upstream[0], upstream


# ── the currency arm: prove it is not vacuous ──────────────────────────────

def _pin_upstream(monkeypatch, manifest, head: str, payloads: dict[str, bytes],
                  head_payloads: dict[str, bytes] | None = None,
                  pin_listing: list[str] | None = None,
                  head_listing: list[str] | None = None):
    """Patch the network seams (`remote_head`, `fetch_raw`, `list_upstream_skills`).

    ``payloads`` are the bytes at the PINNED commit; ``head_payloads`` (default:
    the same) are the bytes at ``head``. The currency arm fetches BOTH, so a
    test that only moves the pointer is not exercising a drift.

    ``pin_listing``/``head_listing`` are the upstream `skills/` DIRECTORY names
    at those commits; the default is the honest answer (exactly the pin's
    directories, see `_upstream_dirs`), so every byte-arm case below exercises
    bytes and not scope.
    """
    at_head = payloads if head_payloads is None else head_payloads
    pinned = manifest["upstream"]["commit"]
    default_listing = _upstream_dirs(manifest)
    monkeypatch.setattr(sp, "remote_head", lambda *a, **k: head)
    monkeypatch.setattr(
        sp, "fetch_raw",
        lambda repo, commit, path, *a, **k: (at_head if commit == head else payloads)[path])
    monkeypatch.setattr(
        sp, "list_upstream_skills",
        lambda repo, commit, *a, **k: (
            (pin_listing if pin_listing is not None else default_listing)
            if commit == pinned
            else (head_listing if head_listing is not None else default_listing)))


def test_4643_an_upstream_push_past_the_pin_is_the_stale_mirror(monkeypatch, tmp_path):
    """The whole point of the currency arm: without it, `--check-local` alone is
    satisfied by pinning any historical commit. Here upstream has moved AND the
    skill's bytes there changed, so the gate must refuse even though the mirror
    matches its (now stale) pin."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                for e in manifest["skills"].values()}
    moved = dict(payloads)
    decide = manifest["skills"]["tortoise-decide"]["upstream_path"]
    moved[decide] = moved[decide] + b"\n# upstream moved this skill on\n"
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, moved)

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    assert "MIRROR IS STALE" in problems[0] and "tortoise-decide" in problems[0]
    assert "--sync" in problems[0]


def test_4643_an_upstream_commit_that_cannot_change_what_we_serve_is_not_drift(
        monkeypatch, tmp_path, capsys):
    """CURRENCY IS ABOUT BYTES, NOT THE COMMIT POINTER (review finding, P2).

    The MIT repo also takes commits that change no skill we serve — a README
    edit, CI config, an edit to a skill already recorded under
    `out_of_scope:` (`6ebf0c9` is itself such a commit). Keying currency on
    `HEAD == pinned` reds the dashboard deploy for a reason unrelated to the
    artifact, auto-files a FALSE "the served mirror drifted" P2, and diagnoses
    the cause as "a skill changed there" — which did not happen. The pinned
    bytes still guard the arm: they are compared against what upstream has at
    HEAD, so a real skill edit reds (the test above) and an unrelated commit is
    green. ⛔ An ADDITION is not this case: a new upstream skill is red by the
    scope arm (see `test_4643_an_upstream_addition_is_reported_not_invisible`),
    because a skill nobody pinned is invisible to every byte arm.
    """
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                for e in manifest["skills"].values()}
    # HEAD moved; the three skill files did not.
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads))

    assert sp.check_upstream(manifest, root) == [], (
        "an upstream commit that touched none of the pinned skills is not a "
        "drift — red-ing here is a false alarm on the signal humans trust")
    assert "pinned skill bytes are unchanged" in capsys.readouterr().err, (
        "a green run should still say WHY the moved pointer is harmless, so a "
        "reader does not have to re-derive it")


def test_4643_a_fabricated_pin_fails_on_the_pinned_bytes(monkeypatch, tmp_path):
    """A pin edited by hand to match a hand-edited mirror cannot pass: the bytes
    are re-fetched BY PINNED COMMIT and re-hashed, so the claim is checked
    against the upstream object it names rather than trusted."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: b"# something else entirely\n"
                for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 3, problems
    assert all("PIN IS NOT HONEST" in p for p in problems), problems


def test_4643_a_mirror_that_differs_from_the_pinned_commit_is_caught(monkeypatch, tmp_path):
    """End-to-end in the network arm: pin honest and current, mirror stale."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)
    victim = manifest["skills"]["tortoise-file-finding"]["served_path"]
    (root / victim).write_bytes(b"# hand-edited in this repo\n")

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    assert "differs from" in problems[0] and "tortoise-file-finding" in problems[0]


def test_4643_the_network_arm_is_green_when_everything_agrees(monkeypatch, tmp_path):
    """Control: the gate can pass. Without this, every assertion above could be
    a gate that always fails and no one would know."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)
    assert sp.check_upstream(manifest, root) == []


def test_4643_an_ambiguous_or_empty_ls_remote_answer_fails_closed(monkeypatch):
    """`git ls-remote` exits 0 with NO output for a renamed branch or repo —
    reading that as "current" is the classic fail-open. Two refs is equally
    unreadable: the answer must be exactly one."""
    def _run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=_run.stdout, stderr="")

    monkeypatch.setattr(sp.subprocess, "run", _run)
    for stdout, _why in (("", "no refs"),
                         ("a\trefs/heads/main\nb\trefs/heads/main\n", "two refs")):
        _run.stdout = stdout
        with pytest.raises(sp.ParityError):
            sp.remote_head("example/repo", "main", attempts=1, backoff=0)


def test_4643_a_fetch_that_cannot_be_read_fails_closed(monkeypatch):
    """A 404/5xx (or a repo that moved) must RAISE after the retries rather than
    return empty bytes — empty bytes hash to a value that matches nothing, which
    would red for the wrong reason and hide the real one."""
    def _boom(*a, **k):
        raise OSError("HTTP 404")

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(sp.ParityError, match="failed after 1 attempts"):
        sp.fetch_raw("example/repo", "0" * 40, "skills/x/SKILL.md",
                     attempts=1, backoff=0)


# ── the derivation ────────────────────────────────────────────────────────

def test_4643_sync_re_derives_the_mirror_and_the_pin_byte_for_byte(monkeypatch, tmp_path):
    """`--sync` is the only sanctioned way the mirror changes. It must write the
    upstream bytes verbatim (not a normalised copy) and re-cut the pin to the
    commit it read — otherwise the next `--check` reds on its own output."""
    src = REPO / sp.MANIFEST_REL
    pin = tmp_path / "pin.yml"
    shutil.copyfile(src, pin)
    manifest = sp.load_manifest(pin)
    root = tmp_path / "root"
    for entry in manifest["skills"].values():
        dest = root / entry["served_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"# stale\n")

    upstream_bytes = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                      for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, "a" * 40, upstream_bytes)

    changed = sp.sync(root, pin, attempts=1, backoff=0).changed
    assert len(changed) == 3, changed
    for name, entry in manifest["skills"].items():
        assert (root / entry["served_path"]).read_bytes() == \
            upstream_bytes[entry["upstream_path"]], f"{name}: not byte-for-byte"
    re_pin = sp.load_manifest(pin)
    assert re_pin["upstream"]["commit"] == "a" * 40
    # Read the upstream path from the pin, as the assertions above do, rather
    # than re-spelling it: a bare `skills/<name>/…` literal is the shape
    # `tests/test_8066_served_skill_relay_ratchet.py` rejects, because it is
    # indistinguishable from a read through the machine-local `skills` relay
    # symlink. This upstream path is the MIT repo's, not the relay's (#8066/#4643).
    decide_upstream = manifest["skills"]["tortoise-decide"]["upstream_path"]
    assert re_pin["skills"]["tortoise-decide"]["sha256"] == \
        sp.sha256_bytes(upstream_bytes[decide_upstream])
    assert sp.check_local(re_pin, root) == [], "sync output must satisfy its own gate"


def test_4643_sync_dry_run_writes_nothing(monkeypatch, tmp_path):
    src = REPO / sp.MANIFEST_REL
    pin = tmp_path / "pin.yml"
    shutil.copyfile(src, pin)
    before_pin = pin.read_bytes()
    manifest = sp.load_manifest(pin)
    root = tmp_path / "root"
    for entry in manifest["skills"].values():
        dest = root / entry["served_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"# stale\n")
    upstream_bytes = {e["upstream_path"]: b"# fresh upstream\n"
                      for e in manifest["skills"].values()}
    _pin_upstream(monkeypatch, manifest, "b" * 40, upstream_bytes)

    changed = sp.sync(root, pin, attempts=1, backoff=0, dry_run=True).changed
    assert len(changed) == 3
    assert pin.read_bytes() == before_pin, "--dry-run must not re-cut the pin"
    for entry in manifest["skills"].values():
        assert (root / entry["served_path"]).read_bytes() == b"# stale\n"


def _sync_fixture(tmp_path, monkeypatch, head: str, upstream_bytes: dict[str, bytes]):
    """A throwaway pin + root holding `upstream_bytes` at the served paths."""
    src = REPO / sp.MANIFEST_REL
    pin = tmp_path / "pin.yml"
    shutil.copyfile(src, pin)
    manifest = sp.load_manifest(pin)
    root = tmp_path / "root"
    _pin_upstream(monkeypatch, manifest, head, upstream_bytes)
    return pin, manifest, root


def test_4643_sync_to_a_non_head_commit_is_not_a_pin_its_own_check_rejects(
        monkeypatch, tmp_path):
    """`--sync --commit <sha>` must not be a capability whose only product is a
    red gate (review finding, P2).

    Currency compares the pinned BYTES against upstream HEAD, so pinning a
    commit whose skill bytes equal HEAD's is green — the capability is usable.
    `at_head` reports the pointer difference so the caller can say so; when the
    bytes at the pinned commit DIFFER from HEAD the pin is legitimately red, and
    that is reported as a consequence rather than discovered from a red gate.
    """
    upstream_bytes = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                      for e in sp.load_manifest(MANIFEST)["skills"].values()}
    pin, manifest, root = _sync_fixture(tmp_path, monkeypatch, "f" * 40, upstream_bytes)

    result = sp.sync(root, pin, commit="a" * 40, attempts=1, backoff=0)
    assert result.target == "a" * 40
    assert result.previous == manifest["upstream"]["commit"]
    assert result.at_head is False, (
        "pinning a commit that is not HEAD must be REPORTED, not left for the "
        "operator to discover from a red gate")

    re_pin = sp.load_manifest(pin)
    assert re_pin["upstream"]["commit"] == "a" * 40
    assert sp.check_upstream(re_pin, root) == [], (
        "the skill bytes at the pinned commit equal HEAD's, so this pin is "
        "green — `--commit` must be able to produce a usable pin")


def test_4643_sync_reports_the_commit_it_pinned_not_the_previous_one(
        monkeypatch, tmp_path, capsys):
    """The single line an operator reads after `--sync` used to name the PREVIOUS
    commit: `sync()` re-loads the pin into its own dict, so the caller's copy
    still holds the old one (review finding, P3). The one fact `--sync` exists
    to report must be the commit it read."""
    upstream_bytes = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                      for e in sp.load_manifest(MANIFEST)["skills"].values()}
    pin, manifest, root = _sync_fixture(tmp_path, monkeypatch, "b" * 40, upstream_bytes)
    previous = manifest["upstream"]["commit"]

    rc = sp.main(["--sync", "--dry-run", "--root", str(root),
                  "--manifest", str(pin), "--attempts", "1", "--backoff", "0"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "b" * 12 in out, (
        f"the sync summary must name the commit it pinned ({'b' * 12}), not the "
        f"previous one ({previous[:12]}): {out!r}")
    assert f"pin moved {previous[:12]}" in out


def test_4643_sync_combined_with_a_check_arm_is_refused(tmp_path, monkeypatch, capsys):
    """`--sync --check` exited 0 having run NO gate: the `if args.sync:` branch
    returned before `do_local`/`do_upstream` were consulted, so a caller who
    asked for a check got a green exit and no check — a fail-open path in a tool
    whose whole purpose is fail-closed (review finding, P2)."""
    upstream_bytes = {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
                      for e in sp.load_manifest(MANIFEST)["skills"].values()}
    pin, _manifest, root = _sync_fixture(tmp_path, monkeypatch, "c" * 40, upstream_bytes)

    for arm in ("--check", "--check-local", "--check-upstream"):
        with pytest.raises(SystemExit) as exc:
            sp.main(["--sync", arm, "--dry-run", "--root", str(root),
                     "--manifest", str(pin), "--attempts", "1", "--backoff", "0"])
        assert exc.value.code == 2, (
            f"--sync {arm} must refuse (argparse usage error), not exit 0")
    assert "--sync cannot be combined with" in capsys.readouterr().err


def test_4643_json_is_one_document_on_the_green_path(capsys):
    """`--json` is a MACHINE-READABLE flag, and the run a monitor reads most is
    the green one. It used to print the JSON object followed by `1 + len(skills)`
    human lines on stdout, so `json.loads(stdout)` raised exactly when the gate
    passed — green read as a crash (review finding, P2)."""
    import json

    rc = sp.main(["--root", str(REPO), "--check-local", "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)  # ⛔ must not raise
    assert payload["ok"] is True
    assert payload["checked_local"] is True
    assert "parity OK" in captured.err, (
        "the human summary still has to be reported — on stderr, so stdout "
        "stays a single JSON document")


# ── the pin's SCOPE: an upstream addition/removal is not invisible ────────
#
# Both byte arms iterate the PIN's keys, so on their own a new upstream
# `skills/<name>/SKILL.md` is never fetched, never mirrored and never reported —
# the pin would describe a smaller set than the repo it claims to mirror.

def _payloads(manifest: dict) -> dict[str, bytes]:
    return {e["upstream_path"]: (REPO / e["served_path"]).read_bytes()
            for e in manifest["skills"].values()}


def _upstream_dirs(manifest: dict) -> list[str]:
    """The upstream DIRECTORY names the pin binds.

    Derived from `upstream_path`, never from the pin's KEYS: the key is the
    SERVED name and the schema deliberately lets the two differ (review finding,
    P3 — comparing keys reds an honest pin whose served name is shortened).
    """
    return sorted({Path(e["upstream_path"]).parent.name
                   for e in manifest["skills"].values()})


def test_4643_an_upstream_addition_is_reported_not_invisible(monkeypatch, tmp_path):
    """Review finding (P2), measured: add an upstream `skills/newcapability/`
    and NOTHING reds — not the mirror arm (it iterates the pin), not the
    currency arm (it fetches only the pinned paths), not any test."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads),
                  head_listing=[*sorted(manifest["skills"]), "newcapability"])

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    assert "upstream added 'skills/newcapability'" in problems[0], problems
    assert "out of scope" in problems[0], (
        "the message must name BOTH ways out — pin it, or record it as "
        "out of scope — or the only legal answer reads as 'mirror it'")


def test_4643_an_upstream_removal_is_reported_as_stale_not_as_a_404(
        monkeypatch, tmp_path):
    """A pinned skill deleted upstream: without the scope check the byte arm
    fetches a 404, which the CLI reports as *cannot verify* — a real, actionable
    drift misclassified as a network problem."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    listing = [n for n in sorted(manifest["skills"]) if n != "tortoise-file-finding"]
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads),
                  head_listing=listing)

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    # Read the path from the pin rather than spelling it: a bare
    # `skills/<served-name>` literal is the shape
    # `tests/test_8066_served_skill_relay_ratchet.py` rejects, because it is
    # indistinguishable from a read through the machine-local relay (#8066).
    removed = Path(
        manifest["skills"]["tortoise-file-finding"]["upstream_path"]).parent.as_posix()
    assert f"upstream removed '{removed}'" in problems[0], problems
    assert "STALE" in problems[0], problems


def test_4643_a_pin_that_names_a_skill_its_own_commit_lacks_reds(
        monkeypatch, tmp_path):
    """The scope is checked at the PINNED commit too: the pin lying about what
    its own commit contains is a pin defect, not a 404 to be retried."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    listing = [n for n in sorted(manifest["skills"]) if n != "tortoise-decide"]
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads),
                  pin_listing=listing, head_listing=listing)

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    assert "NAMES a skill its own commit does not contain" in problems[0], problems
    named = Path(
        manifest["skills"]["tortoise-decide"]["upstream_path"]).parent.as_posix()
    assert named in problems[0]


def test_4643_the_scope_arm_binds_by_upstream_path_not_the_pin_key(
        monkeypatch, tmp_path):
    """Review finding (P3): the scope arm compared the pin's KEYS to upstream
    DIRECTORY names. `skills.<key>` is the SERVED name and `upstream_path` is the
    binding, so an honest pin whose served name is shortened was a PERMANENT
    false red whose printed remedy (`--sync`) re-cuts the same keys and cannot
    clear it.
    """
    manifest = sp.load_manifest(MANIFEST)
    entry = manifest["skills"].pop("tortoise-file-finding")
    manifest["skills"]["file-finding"] = entry  # served name ≠ upstream dir
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)

    assert sp.check_upstream(manifest, root) == [], (
        "the pin binds upstream directories through `upstream_path`; keying the "
        "scope arm on the pin's keys invents a missing skill and prints a "
        "remedy that cannot fix it")


def test_4643_out_of_scope_records_a_deliberate_exclusion(monkeypatch, tmp_path):
    """The escape hatch the message names must WORK, or the only options for a
    converged-but-unserved upstream skill are a false pin or a permanently red
    gate."""
    manifest = sp.load_manifest(MANIFEST)
    manifest["out_of_scope"] = ["research-domain"]
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    listing = [*sorted(manifest["skills"]), "research-domain"]
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads,
                  pin_listing=listing)

    assert sp.check_upstream(manifest, root) == [], (
        "a declared out_of_scope upstream skill is not a drift — the declaration "
        "is the recorded decision the gate is asking for")


def test_4643_out_of_scope_covers_a_skill_added_after_the_pin(monkeypatch, tmp_path):
    """THE case the escape hatch exists for, and the one it used to reject.

    Review finding (P2): `declared_out` was validated against the PINNED commit,
    but a skill that needs excluding is one added upstream AFTER the pin — so it
    is by definition absent from the pinned commit. The gate printed "pin it or
    record it as out of scope", and recording it printed "drop the stale
    declaration": the two messages prescribed each other, so no sequence of the
    documented actions reached green, and the action that would have (a pin
    re-cut) was named by neither.

    This test is the state the first message produces (`head_listing` has the
    addition, `pin_listing` does not), which is exactly what the old code reds.
    """
    manifest = sp.load_manifest(MANIFEST)
    manifest["out_of_scope"] = ["research-domain"]
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads),
                  head_listing=[*_upstream_dirs(manifest), "research-domain"])

    assert sp.check_upstream(manifest, root) == [], (
        "a declared out_of_scope skill that appeared upstream AFTER the pin is a "
        "recorded decision, not a stale declaration — rejecting it closed the "
        "hatch for the only case that needs it")


def test_4643_a_stale_out_of_scope_declaration_reds(monkeypatch, tmp_path):
    """The converse: `out_of_scope:` naming an upstream skill that no longer
    exists is a stale declaration — it must be cleaned up, not carried forever
    as a line that silently widens the expected set."""
    manifest = sp.load_manifest(MANIFEST)
    manifest["out_of_scope"] = ["gone-skill"]
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    _pin_upstream(monkeypatch, manifest, manifest["upstream"]["commit"], payloads)

    problems = sp.check_upstream(manifest, root)
    assert len(problems) == 1, problems
    assert "out_of_scope" in problems[0] and "gone-skill" in problems[0], problems

    # …and at HEAD: a skill once excluded that upstream has since DELETED must
    # be dropped from the declaration, or `out_of_scope:` widens the expected
    # set with names nothing can match.
    _pin_upstream(monkeypatch, manifest, "f" * 40, payloads, dict(payloads),
                  pin_listing=[*sorted(manifest["skills"]), "gone-skill"])
    head_problems = sp.check_upstream(manifest, root)
    assert len(head_problems) == 1, head_problems
    assert "gone-skill" in head_problems[0] and "HEAD" in head_problems[0]


# ── the CLI exit code: the only thing a workflow step reads ───────────────

def test_4643_the_cli_exits_nonzero_on_drift(tmp_path, capsys):
    """Review finding (P2), measured: changing the problems branch's `return 1`
    to `return 0` left 31/31 green. A gate whose failing branch returns 0 is a
    gate that never gates — the runner is `uv run python … --check-upstream`,
    so the EXIT CODE is the whole verdict."""
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    victim = manifest["skills"]["tortoise-decide"]["served_path"]
    (root / victim).write_bytes(b"# edited in place, not re-derived\n")

    rc = sp.main(["--check-local", "--root", str(root),
                  "--manifest", str(MANIFEST), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, "a drift MUST exit non-zero — that is what reds the step"
    assert payload["ok"] is False, payload
    assert payload["problems"], payload
    assert "DRIFTED" in payload["problems"][0], payload
    assert payload["cannot_verify"] == [], (
        "a byte drift is not an unreadable side — the two must not be conflated")


def test_4643_the_cli_exits_nonzero_when_it_cannot_verify(monkeypatch, capsys):
    """A side that cannot be READ must fail the step (never silently pass) and
    must NOT be reported as drift: a monitor keying on `problems` would
    auto-file a false 'the served mirror drifted' P2 for a network blip."""
    def _boom(*a, **k):
        raise sp.ParityError("upstream unreachable: connection reset (3 attempts)")

    monkeypatch.setattr(sp, "remote_head", _boom)
    rc = sp.main(["--check-upstream", "--root", str(REPO),
                  "--manifest", str(MANIFEST), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, "cannot-verify must FAIL — a gate that passes blind is #3673"
    assert payload["ok"] is False
    assert payload["problems"] == [], (
        "…and it is NOT drift: reporting it as drift auto-files a false alarm "
        "about bytes nobody compared")
    assert payload["cannot_verify"] and "unreachable" in payload["cannot_verify"][0]


def test_4643_a_drift_survives_an_unreadable_side(monkeypatch, tmp_path, capsys):
    """Review finding (P2): an observed drift was DISCARDED by a later failure.

    `check_upstream` kept its findings in a local list, so when a later fetch
    exhausted its retries it raised out of the function and the caller never got
    the drift it had already found — the run still exited 1, but it reported
    "could not verify — this is NOT a drift" and the report a human reads for the
    red said `### Drift (0)`. Both answers must survive in ONE run.
    """
    manifest = sp.load_manifest(MANIFEST)
    root = _mirror_root(tmp_path, manifest)
    payloads = _payloads(manifest)
    names = sorted(manifest["skills"])
    unreadable = manifest["skills"][names[0]]["upstream_path"]
    drifted = manifest["skills"][names[-1]]["upstream_path"]
    moved = dict(payloads)
    moved[drifted] = moved[drifted] + b"\n# upstream edited this one\n"
    pinned = manifest["upstream"]["commit"]

    def _fetch(repo, commit, path, *a, **k):
        if commit != pinned and path == unreadable:
            raise sp.ParityError(f"{path}: timed out after 3 attempts")
        return (payloads if commit == pinned else moved)[path]

    monkeypatch.setattr(sp, "remote_head", lambda *a, **k: "f" * 40)
    monkeypatch.setattr(sp, "fetch_raw", _fetch)
    monkeypatch.setattr(sp, "list_upstream_skills",
                        lambda *a, **k: _upstream_dirs(manifest))

    report = tmp_path / "report.md"
    rc = sp.main(["--check-upstream", "--root", str(root),
                  "--manifest", str(MANIFEST), "--json",
                  "--report", str(report)])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["problems"] and "MIRROR IS STALE" in payload["problems"][0], payload
    assert payload["cannot_verify"] and "timed out" in payload["cannot_verify"][0], (
        f"the unreadable skill must be reported as could-not-verify: {payload}")
    text = report.read_text(encoding="utf-8")
    assert "### Drift (1)" in text, (
        "the uploaded report is the diagnostic for the red run — it must carry "
        f"the drift that WAS found: {text!r}")
    assert "### Could not verify (1)" in text, text


def test_4643_a_defective_pin_is_a_defect_not_a_could_not_verify(tmp_path, capsys):
    """Review finding (P2): `main()` wrapped `load_manifest` in the same
    `except ParityError` as the network reads, so a missing or malformed pin —
    deterministic, and the thing the operator must fix FIRST — was reported as
    "could not verify … re-run once the network/credential is sane", advice that
    can never clear it."""
    bad = tmp_path / "pin.yml"
    bad.write_text("upstream:\n  repo: x\nskills: {}\n", encoding="utf-8")

    rc = sp.main(["--check-upstream", "--root", str(REPO),
                  "--manifest", str(bad), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1, "a pin this tool cannot read must FAIL (fail-closed)"
    assert payload["cannot_verify"] == [], (
        "a malformed pin is not a transient read failure — filing it under "
        "'re-run once the network is sane' hides an actionable defect")
    assert payload["problems"] and "UNUSABLE" in payload["problems"][0], payload


# ── the ownership record (target 3) + the wiring (target 4) ───────────────

WORKFLOWS = REPO / ".github" / "workflows"


def _section_71() -> str:
    """§7.1's body — the ownership RULE, the table a reader acts on, AND the
    wiring sentence the docs/runner assertions read (so the cut is on the next
    HEADING, not on the next bold paragraph)."""
    notes = (REPO / "docs" / "license-notes.md").read_text(encoding="utf-8")
    start = notes.index("### 7.1")
    nxt = notes.find("\n### ", start + 1)
    return notes[start:] if nxt == -1 else notes[start:nxt]


def _table_rows(section: str) -> list[list[str]]:
    """The §7.1 markdown table's data rows, cells stripped."""
    rows: list[list[str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):  # the |---|---| rule
            continue
        if cells[0] == "Artifact":  # header
            continue
        rows.append(cells)
    return rows


def _artifacts(cell: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", cell))


def test_4643_the_ownership_table_states_the_rule_per_artifact():
    """Target 3, and it must be the TABLE — not a sentence that happens to sit
    near one.

    Review finding (P2), measured: replacing `### 7.1`→EOF with a four-token
    line still left 31/31 green, because the old assertion grepped two
    substrings. The rule is a MAPPING (artifact set → authoring repo), so the
    assertion is over the parsed rows: a deleted or inverted table must red.
    """
    section = _section_71()
    rows = _table_rows(section)
    assert rows, (
        "§7.1 must carry the ownership TABLE (`| Artifact | Authored in | "
        "Enforced by |`) — prose is not a rule a contributor can act on")
    pinned = set(sp.load_manifest(MANIFEST)["skills"])

    # 1. The three served-and-pinned capabilities are authored in the MIT repo.
    capabilities = [r for r in rows if _artifacts(r[0]) == pinned]
    assert len(capabilities) == 1, (
        f"§7.1 needs exactly ONE row whose artifact set is the pin's {sorted(pinned)}; "
        f"found {[r[0] for r in rows]}")
    row = capabilities[0]
    assert "daniel-ospina/tortoise-skills-and-integrations" in row[1], (
        "that row's `Authored in` must name the MIT repo — inverted, it is the "
        "duplicate-authoring defect this rule exists to end: " + row[1])
    assert "config/served-skills-upstream.yml" in row[2], (
        "…and its `Enforced by` must name the pin: " + row[2])

    # 2. The installer is authored HERE (#3324 moved its source of truth out of
    # the MIT repo because that copy went stale) — the mirror image of row 1.
    installer = [r for r in rows if _artifacts(r[0]) == {"install-tortoise-skills.sh"}]
    assert len(installer) == 1, (
        f"§7.1 needs a row for `install-tortoise-skills.sh`; rows: "
        f"{[r[0] for r in rows]}")
    assert "daniel-ospina/tortoise" in installer[0][1], (
        "the installer is authored in daniel-ospina/tortoise: " + installer[0][1])
    assert "tortoise-skills-and-integrations" not in installer[0][1], (
        "the installer's `Authored in` must NOT name the MIT repo — that is the "
        "inversion #3324 corrected")

    # 3. Onboarding: authored here, gated by the byte-identity test + cmp.
    onboarding = [r for r in rows if _artifacts(r[0]) == {"tortoise-onboarding"}]
    assert len(onboarding) == 1, f"§7.1 needs a row for tortoise-onboarding: {rows}"
    assert "daniel-ospina/tortoise" in onboarding[0][1], onboarding[0][1]

    # 4. The installer's OWN header — the artifact a customer's `curl … | bash`
    # actually reads, and the one that carried the wrong claim (#4358/#3324).
    # Review finding (P2), measured: the deleted
    # `test_4643_the_ownership_statement_names_one_repo_per_artifact` was the
    # ONLY assertion in the tree tying that header to the MIT repo, and §7.1's
    # "the rule, now stated in both repos" is a claim about this side too — so
    # the table alone left the header unguarded.
    header = INSTALLER.read_text(encoding="utf-8")
    assert "tortoise-skills-and-integrations" in header, (
        "the served installer's header must name the MIT repo as the author of "
        "the skills it installs — it is the statement a contributor reads: "
        f"{INSTALLER.relative_to(REPO)}")
    assert "tortoise/onboarding/SKILL.md" in header, (
        "…and must name the in-repo artifact it does NOT install, so the "
        "boundary reads as a rule rather than an omission")


# ── "is the arm WIRED" is decided by EXECUTION, not by reading the YAML ────
#
# Review finding (P2), measured: the previous predicate declared a workflow a
# runner iff a step's `run:` TEXT contained `served_skills_parity` and
# `--check-upstream`. Every one of these therefore still counted as a wired
# gate, while the docs row, the workflow header and this file all claimed a
# fail-closed runner:
#
#     run: echo "uv run python tools/served_skills_parity.py --check-upstream"
#     run: uv run python tools/served_skills_parity.py --check-upstream || true
#     run: set +e; uv run python tools/… --check-upstream; true
#     - run: uv run … --check-upstream
#       continue-on-error: true
#
# That is the SAME defect the P1 was ("a gate that reads as a gate and gates
# nothing"), one level up — and `tests/test_ci_guard_invocation.py` is the
# repo's settled answer to it (#4494, after thirteen review cycles on this exact
# class): *a text scanner cannot be completed by more text rules*. So the step
# is RUN, under the flags `_shell_flags` derives for THAT step, with recording
# interpreter stubs on PATH, and a runner must satisfy BOTH:
#
#   * the recorded argv is the arm itself — `served_skills_parity.py` at the
#     interpreter's SCRIPT operand, with `--check-upstream` after it (so an
#     `echo`, a `printf`, a quoted string or a `python -c` lookalike records
#     nothing and is rejected);
#   * when the STUBBED ARM exits 1, the STEP exits non-zero (so `|| true`,
#     `|| :`, a `set +e` with a masking last command, and a `trap`/subshell that
#     swallows the status — all of which still execute the arm — are rejected on
#     their status, not on their spelling). Note that a `set -e` step needs no
#     help: a bare failing line already aborts it, so `; exit 0` is NOT a
#     disarmament there and is not claimed as one.
#
# `continue-on-error:` and `if:` are judged statically, at BOTH levels, because
# they are workflow semantics no shell can see: a unit whose failure the runner
# is told to swallow cannot be where a failing-closed gate lives. The STEP is
# judged by `_is_arm_step`; the JOB that owns it is judged by `_can_fail(job)`
# in `_upstream_runners`, because GitHub reports a job with
# `continue-on-error: true` — or one skipped by `if:` — as GREEN, so the same
# silent-green-with-no-gate shape survives one level up (review finding, P2,
# measured: each left 49/49 green while the dashboard deployed nothing).
#
# Declared bounds (what this does NOT decide, and why that is safe): reachability
# of the workflows THEMSELVES is not decided here, but the two facts that make
# them reachable are asserted below — the deploy step's placement before
# `npm run build` (same test) and the daily workflow's `on.schedule` cron
# (`test_4643_the_daily_backstop_is_actually_scheduled`), since
# `workflow_dispatch` alone needs a human and `repository_dispatch` has no
# sender yet (#8748).

#: One JSON-free record per interpreter call: argv NUL-separated, then an RS.
_STUB = """#!/usr/bin/env bash
printf '%s\\0' "${0##*/}" "$@" >> "$PARITY_STUB_RECORD"
printf '\\036' >> "$PARITY_STUB_RECORD"
if [ "${PARITY_STUB_FAIL_ARM:-0}" = "1" ]; then
  # The real tool's semantics (`main()`) are CUMULATIVE, not first-flag-wins:
  # `--help` short-circuits (exit 0); `--sync` alone re-derives the mirror and
  # runs no arm, and `--sync` COMBINED with a check flag is refused (exit 2); an
  # arm flag ANYWHERE runs the arm, even alongside `--check-local`; a bare
  # invocation (or one that only adds `--json`/`--report`) runs BOTH arms.
  # A stub that disagreed with the predicate about any of these made a `--sync`
  # step look fail-closed (review findings, P1/P2).
  has_arm=0; has_sync=0; has_suppress=0
  for a in "$@"; do
    case "$a" in
      --help|-h) exit 0 ;;
      --check-upstream|--check) has_arm=1 ;;
      --sync) has_sync=1 ;;
      --check-local) has_suppress=1 ;;
    esac
  done
  if [ "$has_sync" = "1" ]; then
    if [ "$has_arm" = "1" ]; then exit 2; fi
    exit 0
  fi
  if [ "$has_arm" = "1" ]; then exit 1; fi
  if [ "$has_suppress" = "1" ]; then exit 0; fi
  exit 1
fi
exit 0
"""

#: Shadowed so the step's real command shape is what runs, never the real tool.
_STUBBED = ("uv", "uvx", "python", "python3")

#: The real shells a body may spawn (`bash -c …`, `sh -c …`), linked INTO the
#: hermetic bin dir. A nested shell then behaves like the runner's while no
#: system directory is on PATH — resolving them from the parent's PATH instead
#: is what put `/usr/bin:/bin` on the child's PATH (review finding, P1).
_SHELLS = ("bash", "sh")

#: Answer EVERY other command a body invokes, instead of leaving it to a real
#: binary. Three measured reasons (review findings P0 + P1):
#:
#:   * with `/usr/bin:/bin` on PATH a step reached the network
#:     (`api.cloudflare.com`, prod `app.premiselabs.co`) and slept for real
#:     (`sleep 5`, then 19 × `sleep 30` in the served-onboarding poll), so the
#:     suite's green depended on a live third party;
#:   * that poll never converged in the throwaway cwd (`sha256sum` of a missing
#:     file is empty, the served hash is not), so `subprocess.run` hit its bound
#:     and `TimeoutExpired` escaped as a test ERROR — the P0;
#:   * an unresolvable command makes `bash -e` abort the STEP, which is
#:     fail-OPEN for the `--sync` hazard this harness exists to catch: a `--sync`
#:     further down the same body was never reached, and an env-spelled
#:     `"$TOOL"` is invisible to the text fallback.
#:
#: Recording and returning 0 fixes all three at once: nothing outside the stub
#: dir is executable, nothing sleeps, and the body always runs to its end. The
#: record is what makes the hermeticity assertable rather than claimed.
_NOT_FOUND_HANDLE = """command_not_found_handle() {
  printf '%s\\0' "$@" >> "$PARITY_STUB_EXTERNAL"
  printf '\\036' >> "$PARITY_STUB_EXTERNAL"
  return 0
}
export -f command_not_found_handle
"""

#: A step that outlives this is rejected, never raised. Everything above makes a
#: legitimate step finish in milliseconds (bash startup, stubs, no I/O), so the
#: bound only ever fires on a body the hermetic env cannot satisfy.
_STEP_TIMEOUT_SECONDS = 20


def _shell_flags(step: object) -> tuple[str, ...]:
    """The flags GitHub actually runs this step with.

    GitHub's implied default on Linux is `bash -e {file}`; `shell: bash` is
    `bash --noprofile --norc -eo pipefail {file}`. The distinction is load-bearing
    here: WITHOUT `pipefail` a trailing `| tee` makes the step exit 0 while the
    arm fails, so such a gate is disarmed in CI — and a harness that always ran
    `-o pipefail` would certify it anyway (review finding, P2).
    """
    if not isinstance(step, dict):
        return ("-e",)
    shell = str(step.get("shell") or "").strip()
    if shell == "bash" or "pipefail" in shell:
        return ("-e", "-o", "pipefail")
    return ("-e",)


def _read_record(path: Path) -> list[list[str]]:
    """Decode one stub record: argv NUL-separated, records RS-separated."""
    if not path.exists():
        return []
    return [[p.decode() for p in chunk.split(b"\0") if p]
            for chunk in path.read_bytes().split(b"\x1e") if chunk]


def _execute_step(body: str, *, fail_arm: bool = False,
                  flags: tuple[str, ...] = ("-e",),
                  extra_env: dict | None = None) -> tuple[int, list[list[str]]]:
    """RUN a step's real `run:` body; return (exit status, recorded argvs)."""
    rc, calls, _external = _execute_step_detail(
        body, fail_arm=fail_arm, flags=flags, extra_env=extra_env)
    return rc, calls


def _execute_step_detail(
        body: str, *, fail_arm: bool = False, flags: tuple[str, ...] = ("-e",),
        extra_env: dict | None = None) -> tuple[int, list[list[str]],
                                                list[list[str]]]:
    """Like `_execute_step`, plus the commands the body TRIED to run.

    `flags` must be the shell THIS step gets on the runner (`_shell_flags`), not
    the strictest shell available — see that function for why the difference is a
    real disarmament and not a detail.

    Hermetic by construction, and measured rather than asserted: PATH contains
    the throwaway bin dir and NOTHING else (no `/usr/bin`, no `/bin`), so the
    interpreter stubs are the only interpreters, every other command is answered
    by `command_not_found_handle` (recorded, exit 0) instead of a real binary,
    the cwd is a throwaway directory, and the environment is an allowlist. A body
    therefore cannot execute a real gate, sync a real project, reach the network,
    or sleep — and cannot abort early on an unresolvable command either, which is
    what kept a late `--sync` invisible (review findings, P0 + P1).

    The status is always an int: a body that outlives the bound is reported as
    `124` (the `timeout(1)` convention) with whatever argvs it recorded first,
    never raised, because both predicates this feeds are total functions over
    workflow content.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        shell = shutil.which("bash") or "/bin/bash"
        for name in _STUBBED:
            stub = bin_dir / name
            stub.write_text(_STUB, encoding="utf-8")
            stub.chmod(0o755)
        for name in _SHELLS:
            target = shutil.which(name) or shell
            link = bin_dir / name
            if link.exists():
                continue
            try:
                link.symlink_to(target)
            except OSError:  # pragma: no cover - filesystem refuses symlinks
                continue
        record = root / "argv.bin"
        external_record = root / "external.bin"
        script = root / "step.sh"
        script.write_text(_NOT_FOUND_HANDLE + _interpolate(body, extra_env),
                          encoding="utf-8")
        env = {
            # ⛔ NOTHING but the stub dir: `:/usr/bin:/bin` here put every real
            # binary (curl, sleep, sha256sum, npm, git) in reach of a step that
            # runs on every PR (review finding, P1 — measured reaching
            # api.cloudflare.com and prod app.premiselabs.co).
            "PATH": str(bin_dir),
            "HOME": str(root),
            "TMPDIR": str(root),
            "RUNNER_TEMP": str(root),
            "PARITY_STUB_RECORD": str(record),
            "PARITY_STUB_EXTERNAL": str(external_record),
            "PARITY_STUB_FAIL_ARM": "1" if fail_arm else "0",
            "GITHUB_WORKSPACE": str(REPO),
        }
        # A step's own `env:` is part of what it RUNS: without it, a step that
        # spells the tool `"$TOOL"` records an argv the guard cannot identify,
        # and the `--sync` hazard walked straight through (review finding, P2).
        for key, value in (extra_env or {}).items():
            if isinstance(key, str) and isinstance(value, (str, int, float)):
                env[key] = str(value)
        status = 124
        try:
            # `shell` is ABSOLUTE on purpose: it must not be looked up on the
            # child's PATH, which no longer carries a system directory.
            proc = subprocess.run(
                [shell, *flags, str(script)],
                cwd=root, env=env, capture_output=True, text=True,
                timeout=_STEP_TIMEOUT_SECONDS,
            )
            status = proc.returncode
        except subprocess.TimeoutExpired:
            # `subprocess.run` has already killed the child, so the partial
            # record on disk is readable and the callers stay total.
            status = 124
        return status, _read_record(record), _read_record(external_record)


#: Flags that make the tool run the upstream arm. `--check` runs BOTH arms and a
#: bare invocation runs both too (`main()`: `do_upstream = args.check or …`), so
#: the predicate decides on the ARM SEMANTICS, not on one spelling: §7.1's Gate
#: cell names `--check`, and a maintainer who aligns the step with the doc must
#: not be told "no step executes the arm" (review finding, P3).
_ARM_FLAGS = ("--check", "--check-upstream")

#: The tool this tree ships. The script operand must RESOLVE here — not merely
#: end in the right filename. `tests/test_ci_guard_invocation.py` (#4494), the
#: model this harness cites, makes exactly this check for `tools/skip-guard.py`;
#: omitting it left a `scripts/served_skills_parity.py` that prints OK accepted as
#: a runner (review finding, P3).
THE_TOOL = (REPO / "tools" / "served_skills_parity.py").resolve()


def _resolves_to_the_tool(operand: str) -> bool:
    """The script operand is THIS tree's tool, not a lookalike."""
    candidate = Path(operand)
    if not candidate.is_absolute():
        candidate = REPO / candidate
    try:
        return candidate.resolve() == THE_TOOL
    except OSError:  # pragma: no cover - unresolvable path
        return False


def _is_the_arm(args: list[str]) -> bool:
    """True iff THIS recorded command runs the tool's upstream arm.

    One record is one interpreter invocation, so the flag cannot belong to a
    different command in the same `run:` body: it has to be in the argv the TOOL
    receives, after the script operand — and the script operand has to be the
    file this tree ships.
    """
    operand = next((i for i, a in enumerate(args)
                    if a.endswith("served_skills_parity.py")
                    or a.replace("/", ".").endswith(
                        "tools.served_skills_parity")), None)
    if operand is None:
        return False
    # `python -c 'pass' tools/served_skills_parity.py --check-upstream` runs no
    # gate: the script is an argument to nothing.
    if "-c" in args[:operand]:
        return False
    # `-m tools.served_skills_parity` is NOT that lookalike: `-m` puts the MODULE
    # in the script slot, so `python3 -m tools.served_skills_parity
    # --check-upstream` runs the arm on a real runner (`_runs_sync` already
    # models the same form). Treating it as "an argument to nothing" was an
    # instrument false positive: a working gate reported as no gate at all
    # (review finding, P2).
    if args[operand].endswith("served_skills_parity.py"):
        if not _resolves_to_the_tool(args[operand]):
            return False
    # ONLY a bare dotted module name is the `-m` form. A PATH that merely ends
    # in `tools/served_skills_parity` is not the tool either:
    # `/tmp/evil/tools/served_skills_parity` is a `.`-separated tail too, and
    # certifying it while rejecting the identical `.py` shim was a fail-open
    # (review finding, P2).
    # EXACT module name: a suffix test also certified a DIFFERENT package
    # (`evil.tools.served_skills_parity`, `mytools.served_skills_parity`) as this
    # tool — the same lookalike class, one spelling over (review finding, P2).
    elif args[operand] != "tools.served_skills_parity":
        return False
    tail = args[operand + 1:]
    # `main()`'s semantics are CUMULATIVE, not first-flag-wins:
    #   * `--help` short-circuits argparse with exit 0 — no arm runs;
    #   * `--sync` returns 0 before either arm, and COMBINED with a check flag
    #     the tool refuses the invocation outright (exit 2), so it is never the
    #     arm (review finding, P2);
    #   * an arm flag ANYWHERE wins over `--check-local` — `--check-local
    #     --check-upstream` runs the upstream arm and is a legitimate spelling.
    if any(a in ("--help", "-h") for a in tail):
        return False
    if "--sync" in tail:
        return False
    if any(flag in tail for flag in _ARM_FLAGS):
        return True
    # A bare invocation (or one that only adds `--json`/`--report`/`--root`)
    # runs BOTH arms; `--check-local` on its own is the remaining spelling that
    # does not.
    return "--check-local" not in tail


def _can_fail(unit: dict) -> bool:
    """A STEP or JOB the runner will not let fail cannot gate anything.

    The same rule applies at both levels, so it is deliberately one function: a
    step that swallows its status cannot gate, and neither can a job doing the
    same — `continue-on-error: true` on the job leaves the run green while the
    gate fails, and `if:` other than `always()` skips the gate entirely.
    """
    coe = unit.get("continue-on-error")
    if coe is not None and not (coe is False or str(coe).strip().lower() == "false"):
        return False
    if "if" in unit:
        # `success()` and `!cancelled()` mask NOTHING — they are what the runner
        # does when there is no `if:` at all, so a step carrying one still runs
        # and its failure still reds the run. Keeping only `always()` dropped a
        # correct gate and blamed a `continue-on-error:` that was not there
        # (review finding, P2). Everything else stays fail-closed.
        cond = str(unit["if"]).strip()
        if cond.startswith("${{") and cond.endswith("}}"):
            cond = cond[3:-2].strip()
        return cond in {"always()", "success()", "!cancelled()"}
    return True


def _is_arm_step(step: object) -> bool:
    """True iff this step EXECUTES the arm and a failing arm fails the job."""
    if not isinstance(step, dict):
        return False
    body = step.get("run")
    if not isinstance(body, str) or "served_skills_parity" not in body:
        # Cheap pre-filter only — the VERDICT is `_execute_step`. Without it
        # every step of every workflow would be executed to build the runner
        # set; text alone never decides, so a spelling that hides the command
        # from the pre-filter is rejected (fail-closed), never accepted.
        return False
    if not _can_fail(step):
        return False
    rc, calls = _execute_step(body, flags=_shell_flags(step))
    if rc != 0 or not any(_is_the_arm(a) for a in calls):
        return False
    # ⛔ A step that SYNCS **and** CHECKS is not a gate (review finding, P1).
    # `--sync` re-derives the mirror and re-cuts the pin, so the check that
    # follows grades bytes the same step just wrote from upstream — and those
    # unreviewed bytes are exactly what the build stages. The tool refuses the
    # combination inside one invocation for this reason ("a check in the same
    # invocation would grade bytes it just overwritten"); certifying the
    # two-invocation equivalent is the same hole one `&&` away.
    if any(_runs_sync(a) for a in calls):
        return False
    fail_rc, _ = _execute_step(body, fail_arm=True, flags=_shell_flags(step))
    return fail_rc != 0


def _runs_sync(args: list[str]) -> bool:
    """True iff this recorded command would run `--sync` on THIS tree's tool.

    Two spellings, because `-m` never names a file: the script operand resolves
    to the shipped tool, or the interpreter was given the module path
    `tools.served_skills_parity` (which IS this file — `python -m tools.…` is the
    repo's own idiom for `tools/longmem_eval/*`).
    """
    for index, arg in enumerate(args):
        if arg.endswith("served_skills_parity.py"):
            if _resolves_to_the_tool(arg) and "--sync" in args[index + 1:]:
                return True
            continue
        if arg == "tools.served_skills_parity":
            return "--sync" in args[index + 1:]
    return False


def _arm_steps(doc: dict) -> list[tuple[str, dict, dict]]:
    """`(job_id, job, step)` for every step that EXECUTES the arm — the job is
    carried so its own `if:`/`continue-on-error:` can be judged with it."""
    return [(job_id, job, step)
            for job_id, job in (doc.get("jobs") or {}).items()
            if isinstance(job, dict)
            for step in (job.get("steps") or [])
            if _is_arm_step(step)]


def _interpolate(body: str, env: dict | None = None) -> str:
    """Expand the GitHub contexts a runner expands BEFORE the shell sees them.

    bash cannot: `${{ … }}` dies with "bad substitution", so a step spelled that
    way recorded NO argv and was judged disarmed — a false red on a gate that
    works on the real runner (review finding, P2). Unhandled contexts are left in
    place on purpose: `_body_mentions_sync_on_the_tool` treats a residual `${{`
    next to a tool operand as UNVERIFIABLE and fails closed, so the class is
    closed without enumerating every context and access syntax.
    """
    text = re.sub(r"\$\{\{\s*github\.workspace\s*\}\}", str(REPO), body)
    for name, value in (env or {}).items():
        text = re.sub(r"\$\{\{\s*env\." + re.escape(str(name)) + r"\s*\}\}",
                      str(value), text)
    return text.replace("$GITHUB_WORKSPACE", str(REPO))


def _body_mentions_sync_on_the_tool(body: str, env: dict | None = None) -> bool:
    """Fail-closed fallback for spellings the hermetic run cannot expand.

    Must decide the way `_runs_sync` does — per COMMAND, after interpolation —
    not by substring. Measured false rejects: an inline comment mentioning
    `--sync`, and the tool's OWN remedy string (`… || { echo "re-derive with
    --sync"; exit 1; }`) echoed by an honest gate, which drops a fully armed job
    from the runner set and blames `continue-on-error` (review findings, P2).
    Measured escapes: `${{ env.X }}` and unspaced `${{github.workspace}}` — bash
    never expands either, so the executor records nothing at all.
    """
    lines = []
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line.split(" #", 1)[0])
    text = _interpolate("\n".join(lines), env)
    text = (text.replace("${PWD}", str(REPO)).replace("$PWD", str(REPO)))
    # Per COMMAND, like `_runs_sync`: `--sync` must belong to the same segment.
    for segment in re.split(r"&&|\|\||;|\n", text):
        words = segment.replace("'", " ").replace('"', " ").split()
        # Printing is not invoking: the tool's own remedy is echoed verbatim
        # (`|| { echo "uv run python tools/served_skills_parity.py --sync"; }`),
        # and that must not disarm an honest gate. Checked BEFORE the context
        # rule, so an echoed remedy in an expression-bearing step is still just
        # printing (review finding, P2).
        if any(w in ("echo", "printf") or w.endswith(("/echo", "/printf"))
               for w in words):
            continue
        # Unverifiable CONTEXT: `--sync` next to a `${{ ... }}` we do not model
        # (`env['X']`, `vars.X`, ...) in the SAME command — that spelling names
        # the tool without naming its path, so no argv is recorded. Fail closed.
        # Scoped per SEGMENT: a body-wide test dropped honest gates that merely
        # carried an expression elsewhere in the step (review finding, P2).
        if "--sync" in segment and "$ {{".replace(" ", "") in segment:
            return True
        named = [w for w in words
                 if w.endswith("served_skills_parity.py") and _resolves_to_the_tool(w)]
        if not named:
            continue
        for index, word in enumerate(words):
            if (word.endswith("served_skills_parity.py")
                    and _resolves_to_the_tool(word)
                    and "--sync" in words[index + 1:]):
                return True
    return False


def _job_syncs(job: dict, inherited_env: dict | None = None) -> bool:
    """True iff ANY step of the job runs `--sync` on THIS tree's tool.

    The hazard is JOB-scoped, not step-scoped: step 1 re-derives the mirror and
    re-cuts the pin, step 2 certifies precisely the bytes step 1 just wrote, and
    the build stages them. The tool refuses the flag combination inside ONE
    invocation for this reason, so the two-step form must not be certified
    either (review finding, P2).
    """
    for step in (job.get("steps") or []):
        if not isinstance(step, dict):
            continue
        body = step.get("run")
        if not isinstance(body, str):
            continue
        # ⛔ NO text pre-filter here (review finding, P2). `_is_arm_step` may
        # cheap-prefilter because a spelling it cannot see is fail-closed for the
        # ARM (nothing is certified). For the SYNC hazard a hidden spelling is
        # fail-OPEN: `env: {TOOL: tools/served_skills_parity.py}` with
        # `run: … python3 "$TOOL" --sync` hides the name from any text test while
        # GitHub runs it. So EVERY step is executed, with the step's own `env:`.
        env = step.get("env") if isinstance(step.get("env"), dict) else None
        merged = {**(inherited_env or {}), **(env or {})} or None
        _rc, calls = _execute_step(body, flags=_shell_flags(step), extra_env=merged)
        if any(_runs_sync(a) for a in calls):
            return True
        # The unmodelled-context rule lives in `_body_mentions_sync_on_the_tool`
        # and is scoped per COMMAND — a body-wide version here disarmed honest
        # gates that merely carried an expression elsewhere in the step.
        # Fail-closed text fallback for spellings the hermetic run cannot expand.
        if _body_mentions_sync_on_the_tool(body, merged):
            return True
    return False


def _upstream_runners(workflows: Path | None = None) -> dict[str, dict]:
    """Workflows that EXECUTE `--check-upstream`, derived by executing their
    steps — the definition of "wired" that survives a rename, and that does not
    force a lane landing only the deploy-time step to lie about it (review
    finding, P2)."""
    root = WORKFLOWS if workflows is None else workflows
    runners: dict[str, dict] = {}
    for path in sorted({*root.glob("*.yml"), *root.glob("*.yaml")}):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        # The OWNING JOB must be allowed to fail too: with `continue-on-error:
        # true` the job is torn down and GitHub still reports the run green,
        # and `if: false` skips it — either way the arm never gates a deploy
        # and every surface here still says "failing closed" (P2, measured:
        # both left 49/49 green).
        # `env:` is inherited from the workflow and the job too, and GitHub
        # expands all three levels — probing only the step's own left the sync
        # hazard reachable one level up (review finding, P2).
        if any(_can_fail(job) and not _job_syncs(
                job, {**(doc.get("env") or {}), **(job.get("env") or {})})
               for _, job, _ in _arm_steps(doc)):
            runners[path.name] = doc
    return runners


# ── the harness itself must DO nothing — it runs on every PR ─────────────────
#
# Review findings (P0 + P1), measured on Linux (the ubuntu-latest shape):
# `_job_syncs` executes every step of the arm's job to catch a `--sync` hidden
# behind `env:`/`$PWD`, and PATH carried `/usr/bin:/bin`. So a per-PR test run
# executed the REAL post-deploy steps of `deploy-dashboard`: it curled
# `api.cloudflare.com` and prod `app.premiselabs.co`, slept 5 s, then 19 × 30 s
# in the served-onboarding poll — a poll whose `EXPECTED` hash is empty in the
# throwaway cwd, so it can never converge. `subprocess.run`'s 60 s bound then
# fired and `TimeoutExpired` escaped as an ERROR through `_upstream_runners` into
# ~25 test cases (~27 min of shard time, each paying ~65 s before erroring), and
# the run's green depended on a live third party. On macOS the same poll exits 0
# in under a second (`sha256sum` is absent, so `"" == ""`), which is why the
# local "41 passed" could be true while CI was red.
#
# The fix is the three assertions below: nothing outside the stub dir is on PATH,
# nothing a body invokes is executed for real, and a timed-out step is REPORTED
# rather than raised. Each is measured against the same helper, so none of them
# can be satisfied by prose.

def test_4643_the_step_runner_puts_nothing_but_the_stubs_on_path():
    """A body sees the stub dir and nothing else — `/usr/bin:/bin` is what made
    real `curl`/`sleep`/`sha256sum` reachable from a per-PR test run (P1)."""
    rc, calls = _execute_step('python3 "$PATH"')
    assert rc == 0 and calls, "the interpreter stub did not record its own PATH"
    entries = calls[0][1].split(":")
    assert len(entries) == 1, (
        f"PATH must carry ONLY the throwaway stub dir, got {entries} — every "
        "other entry puts a real binary (curl, sleep, npm, git) in reach of a "
        "step body that runs on every PR, and the network/sleep follow from it")
    stub_dir = Path(entries[0])
    assert stub_dir.name == "bin" and not any(
        str(stub_dir).startswith(system) for system in ("/usr", "/bin", "/opt")), (
        f"the only PATH entry must be the temp stub dir, got {stub_dir}")


def test_4643_an_executed_step_cannot_curl_or_sleep():
    """The real binaries a body would reach are answered by the not-found
    handler: recorded, never executed. `curl` here would have been a live
    request to a production host, and `sleep 30` real shard time."""
    body = ("curl -fsSL --max-time 20 https://app.premiselabs.co/x -o f\n"
            "sleep 30\n"
            "sha256sum f\n")
    start = time.monotonic()
    rc, _calls, external = _execute_step_detail(body)
    elapsed = time.monotonic() - start
    assert rc == 0, (
        "a body whose `curl`/`sleep`/`sha256sum` are hermetically answered must "
        f"still run to completion (exit {rc}) — an abort here is fail-OPEN for a "
        "`--sync` further down the same step")
    assert [argv[0] for argv in external] == ["curl", "sleep", "sha256sum"], (
        f"expected the not-found handler to answer all three, recorded {external}"
        " — a name missing from this list was resolved to a REAL binary")
    assert elapsed < 5, (
        f"the body took {elapsed:.1f}s — `sleep 30` ran for real; the stub env "
        "must make it free")


def test_4643_executing_the_real_deploy_job_stays_off_the_network():
    """The regression itself: the SHIPPED `deploy-dashboard` job, executed by
    the sync probe, must return quickly. Pre-fix on Linux this raised
    `TimeoutExpired` after ~65 s (the onboarding poll never converges), so the
    assertion is on both the verdict and the wall clock."""
    doc = yaml.safe_load(
        (WORKFLOWS / "deploy-pages.yml").read_text(encoding="utf-8")) or {}
    job = doc["jobs"]["deploy-dashboard"]
    start = time.monotonic()
    assert _job_syncs(job) is False, (
        "the shipped deploy job runs `--check-upstream`, not `--sync`; a True "
        "here means the probe misread a step")
    elapsed = time.monotonic() - start
    assert elapsed < _STEP_TIMEOUT_SECONDS, (
        f"executing `deploy-dashboard` took {elapsed:.1f}s — its post-deploy "
        "steps (curl to prod, `sleep 30` polls) are running for real")


def test_4643_a_body_that_cannot_resolve_a_command_still_reaches_its_sync():
    """Fail-OPEN closed: `bash -e` aborts on `command not found`, so before the
    handler a `--sync` further down the same body was never reached — and this
    spelling is invisible to the text fallback, because the tool is named by
    `$TOOL` (the very spelling the `env:` probe exists for)."""
    body = ('no-such-binary-4643 --nowhere\n'
            'python3 "$TOOL" --sync\n')
    job = {"steps": [{"env": {"TOOL": "tools/served_skills_parity.py"},
                      "run": body}]}
    assert _job_syncs(job) is True, (
        "a step whose first command is unresolvable must still be executed to "
        "the end — otherwise a `--sync` hidden behind it is certified as clean, "
        "and the build stages bytes an unreviewed step just wrote")


def test_4643_a_timed_out_step_is_reported_not_raised(monkeypatch):
    """The P0's escape hatch: `subprocess.run`'s bound must surface as a status,
    never as an exception out of a predicate that both `_upstream_runners` and
    `_is_arm_step` call."""
    def _boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["bash"], timeout=1)

    monkeypatch.setattr(subprocess, "run", _boom)
    rc, calls = _execute_step("echo hello")
    assert rc == 124, (
        f"a timed-out step must report 124 (the `timeout(1)` convention), got {rc}")
    assert calls == [], (
        "nothing was recorded before the timeout, so the predicate must see no "
        "argv rather than stale ones")


def test_4643_the_upstream_arm_has_a_runner_and_the_docs_name_it():
    """Target 4 — the P1: the gate never compared against the MIT repo, because
    NO RUNNER executed `--check-upstream`. Both arms then lived in this repo, so
    an in-place served edit plus a hand-cut pin hash passed everything.

    Asserted from workflow CONTENTS, in both directions: every runner that
    exists is named in §7.1, and every workflow §7.1 names exists. A doc that
    promises a runner nobody wrote, or a runner the doc does not mention, is the
    same claim-mismatch class."""
    runners = _upstream_runners()
    assert runners, (
        "no workflow step executes `served_skills_parity.py --check-upstream` — "
        "the upstream half is then decoration: a served copy edited in place "
        "PLUS a hand-cut pin hash passes every test (#4643, P1)")

    # The deploy-time gate must live in the workflow that BUILDS the dashboard
    # (a content fact — the `deploy-dashboard` job), which is what places the
    # check before the build stages `public/`.
    deploy_doc = next((doc for doc in runners.values()
                       if "deploy-dashboard" in (doc.get("jobs") or {})), None)
    assert deploy_doc is not None, (
        f"the arm must run in the workflow whose `deploy-dashboard` job builds "
        f"and uploads the dashboard — otherwise a stale mirror deploys green. "
        f"A workflow whose `deploy-dashboard` job carries `continue-on-error:` "
        f"or an `if:` other than `always()` does not count: GitHub reports that "
        f"run GREEN while the arm fails or is skipped, so nothing deploys and "
        f"nothing reds. Runners found: {sorted(runners)}")
    # …and ORDER matters: the claim is "before the build stages public/", so the
    # arm's step must precede the dashboard build in that job. A moved step is a
    # check that gates nothing that ships.
    steps = deploy_doc["jobs"]["deploy-dashboard"]["steps"]
    arm = next(i for i, s in enumerate(steps) if _is_arm_step(s))
    build = next(i for i, s in enumerate(steps)
                 if isinstance(s.get("run"), str) and "npm run build" in s["run"])
    assert arm < build, (
        f"the upstream arm (step {arm}) must run BEFORE the dashboard build "
        f"(step {build}) stages public/ into dist/ — after it, it gates nothing")

    section = _section_71()
    for name in runners:
        assert name in section, (
            f"§7.1 must name {name} as a runner of the gate — it executes "
            "`--check-upstream`, and a record that omits it reads as 'nothing "
            "runs the upstream arm'")
    # …and the Gate cell's own promise is checked: a workflow §7.1 names THERE
    # must actually execute the arm. A step whose command was commented out (or
    # replaced by an `echo`) leaves the file, the step name and the doc all
    # claiming a gate that no longer runs — the exact class the P1 was.
    rows = _table_rows(section)
    pinned = set(sp.load_manifest(MANIFEST)["skills"])
    gate_cell = next((r[2] for r in rows if _artifacts(r[0]) == pinned), "")
    promised = {m for m in re.findall(r"[A-Za-z0-9_./-]+\.ya?ml", gate_cell)
                if (WORKFLOWS / m).is_file()}
    assert promised, (
        "§7.1's Gate cell must name the workflow(s) that RUN the gate: " + gate_cell)
    assert promised <= set(runners), (
        f"§7.1's Gate cell claims {sorted(promised - set(runners))} run the gate, "
        "but no step there executes `served_skills_parity.py "
        "--check-upstream` — the promise and the runner disagree")
    # The converse: no workflow may be PROMISED that does not exist. Basenames
    # are resolved against the workflows dir AND against the repo, so a config
    # path (`config/served-skills-upstream.yml`) is not misread as a workflow.
    for mention in re.findall(r"[A-Za-z0-9_./-]+\.ya?ml", section):
        assert (WORKFLOWS / mention).is_file() or (REPO / mention).is_file(), (
            f"§7.1 names {mention!r}, which does not exist — a record that "
            "promises a runner nobody wrote is the same false claim in reverse")


# ── "the daily backstop" is a TRIGGER, and a trigger can be deleted ────────
#
# Review finding (P2), measured: deleting `schedule:`/`cron:` from
# `skills-parity.yml` left 49/49 green. Nothing read the triggers — the runner
# predicate reads `jobs:` only — yet `schedule:` is the single thing that makes
# this a *second* runner. `workflow_dispatch` needs a human and
# `repository_dispatch` has no sender (#8748), so without the cron the workflow
# runs only when someone clicks it: the exact P1 shape (a gate that reads as a
# gate and gates nothing) while every prose claim stays true — the header's "the
# daily backstop", "bounds staleness to a day", §7.1's "caught by the next
# morning". A comment claiming that coverage without it is the claim-mismatch
# class this file polices.

def _triggers(doc: dict) -> dict:
    """A workflow doc's `on:` block.

    PyYAML parses the bare `on:` key as the boolean ``True`` (YAML 1.1
    truthiness) — the repo pins that behaviour in
    `tests/test_required_set_sync.py::test_a_workflow_parse_uses_the_boolean_on_key`
    — and the string form is read too, for a quoted ``"on":``. A non-mapping
    form (``on: [push]``) carries no schedule, so it is reported as an empty
    mapping rather than crashing.
    """
    trig = doc.get("on")
    if trig is None:
        trig = doc.get(True)
    return trig if isinstance(trig, dict) else {}


def _scheduled_crons(doc: dict) -> list[str]:
    """The usable cron entries under `on.schedule` — `[]` if unscheduled."""
    schedule = _triggers(doc).get("schedule")
    if not isinstance(schedule, list):
        return []
    return [str(entry.get("cron") or "").strip()
            for entry in schedule
            if isinstance(entry, dict) and str(entry.get("cron") or "").strip()]


def test_4643_the_daily_backstop_is_actually_scheduled():
    """The trigger is the gate's other half: an arm that only runs when a human
    clicks it bounds nothing, and no amount of fail-closed logic inside it
    changes that. Asserted from the workflow DOC (parsed, not grepped) so
    commenting the block out is caught too."""
    doc = yaml.safe_load(
        (WORKFLOWS / "skills-parity.yml").read_text(encoding="utf-8")) or {}
    crons = _scheduled_crons(doc)
    assert crons, (
        "`skills-parity.yml` must carry `on.schedule` with a cron entry — it is "
        "the only thing that makes this a SECOND runner: `workflow_dispatch` "
        "needs a human, and `repository_dispatch` has no sender yet (#8748). "
        "Without it, drift between deploys is invisible until the next release "
        "— the P1 shape, with the header's 'daily backstop' still claiming "
        "otherwise")
    for cron in crons:
        assert len(cron.split()) == 5, (
            f"{cron!r} is not a five-field cron spec — GitHub rejects the "
            "workflow outright, which looks like 'scheduled' in review and "
            "runs never")
    assert "workflow_dispatch" in _triggers(doc), (
        "the daily backstop must also be runnable on demand: a human diagnosing "
        "a drift needs to re-run it without waiting for 06:17")


#: Each keeps every prose claim and removes the only runner that needs no human.
#: The control is the test above: it asserts the REAL file does carry a cron, so
#: a predicate that returned `[]` for everything cannot pass both halves.
_TRIGGER_DISARMAMENTS = [
    ("schedule_trigger_removed", lambda t: t.pop("schedule", None)),
    ("schedule_entry_emptied", lambda t: t.update(schedule=[])),
    ("cron_expression_blanked",
     lambda t: t.update(schedule=[{**e, "cron": ""} for e in t["schedule"]])),
]


@pytest.mark.parametrize("name,mutate", _TRIGGER_DISARMAMENTS,
                         ids=[n for n, _ in _TRIGGER_DISARMAMENTS])
def test_4643_an_unscheduled_backstop_is_not_a_backstop(name, mutate):
    """Review finding (P2), measured on the pre-fix tree: removing
    `schedule:`/`cron:` from `skills-parity.yml:35-37` left **49 passed**.

    Each case below is a way to keep the file, the step, the stubbed arm and
    every sentence about daily protection — and lose the schedule. The mutation
    is applied to the PARSED doc read from the real file, so a case that no
    longer removes the schedule reds here instead of passing silently."""
    doc = yaml.safe_load(
        (WORKFLOWS / "skills-parity.yml").read_text(encoding="utf-8")) or {}
    assert _scheduled_crons(doc), (
        f"{name}: the real `skills-parity.yml` carries no cron to remove — a "
        "mutation that no-ops proves nothing. The schedule guard above is the "
        "one that must red here")
    mutate(_triggers(doc))
    assert _scheduled_crons(doc) == [], (
        f"{name}: the mutation left a usable cron ({_scheduled_crons(doc)}) — "
        "this case is dead and proves nothing")


# ── the wiring guard cannot be disarmed (mutation-proved) ─────────────────

def _gate_step_in(workflows: Path) -> dict:
    """The gate step of the (possibly mutated) deploy workflow, regardless of
    whether it still qualifies as a runner — the disarmament cases need to read
    the step's own `shell:` to know what the mutation MEANS."""
    doc = yaml.safe_load((workflows / "deploy-pages.yml").read_text(encoding="utf-8"))
    for job in (doc.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            if isinstance(step, dict) and "served_skills_parity" in str(
                    step.get("run") or ""):
                return step
    raise AssertionError("no gate step in the mutated deploy workflow")


def test_4643_the_executor_models_the_shell_github_actually_uses():
    """`| tee` is a disarmament ONLY without pipefail, and `shell: bash` is the
    standard hardening a maintainer reaches for. The model is load-bearing in
    both directions, so assert it directly: reverting `_shell_flags` to always
    `-e` (re-introducing the escaped P2) must red here rather than pass
    (review finding, P2)."""
    assert _shell_flags({}) == ("-e",), (
        "GitHub's implied default is `bash -e <file>` — no pipefail")
    assert _shell_flags({"shell": "bash"}) == ("-e", "-o", "pipefail")
    assert _shell_flags({"shell": "bash -eo pipefail {0}"}) == (
        "-e", "-o", "pipefail")
    assert _shell_flags({"shell": "sh"}) == ("-e",)


def test_4643_a_piped_gate_under_the_pipefail_shell_is_still_a_runner(tmp_path):
    """The other half of the model: piped through `tee`, but with `shell: bash`
    (pipefail), the step still exits non-zero on a failing arm — so it IS a
    fail-closed runner, and calling it disarmed would be a false red that tells
    a maintainer to remove the hardening."""
    def mutate(step):
        step.update({"shell": "bash", "run": step["run"] + " | tee arm.log"})

    workflows = _workflows_with_mutation(tmp_path, mutate)
    assert "deploy-pages.yml" in _upstream_runners(workflows), (
        "a piped gate under `shell: bash` fails the step, so it is a runner")


def _arm_workflows(tmp_path: Path, name: str, mutate) -> Path:
    """The two real workflows, with `mutate` applied to deploy's arm step."""
    workflows = tmp_path / name
    workflows.mkdir()
    for workflow in ("deploy-pages.yml", "skills-parity.yml"):
        shutil.copyfile(WORKFLOWS / workflow, workflows / workflow)
    path = workflows / "deploy-pages.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    arm = next(s for s in doc["jobs"]["deploy-dashboard"]["steps"]
               if isinstance(s.get("run"), str)
               and "served_skills_parity" in s["run"])
    mutate(arm)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return workflows


def test_4643_a_shim_and_a_noop_if_are_judged_correctly(tmp_path):
    """Two verdict rules, both measured (review findings, P2).

    (a) The `-m` fix let ANY extensionless operand through: a `.`-separated PATH
        tail matched a module name, so `/tmp/evil/tools/served_skills_parity` was
        certified as a fail-closed gate while the identical `.py` shim was
        rejected.
    (b) `_can_fail` kept only `always()`, so `if: success()` and
        `if: ${{ !cancelled() }}` — which mask nothing and are what the runner
        does with no `if:` at all — dropped a correct gate and blamed a
        `continue-on-error:` that was not there.
    """
    base = "uv run --no-project --with pyyaml==6.0.3 python3"

    for index, shim in enumerate(("/tmp/evil/tools/served_skills_parity",
                                  "../evil/tools/served_skills_parity",
                                  "-m evil.tools.served_skills_parity",
                                  "-m mytools.served_skills_parity")):
        workflows = _arm_workflows(
            tmp_path, f"shim-{index}",
            lambda arm, s=shim: arm.update(run=f"{base} {s} --check-upstream"))
        assert "deploy-pages.yml" not in _upstream_runners(workflows), (
            f"an extensionless PATH {shim!r} was certified as the shipped tool")

    for index, cond in enumerate(("success()", "${{ success() }}",
                                  "!cancelled()", "${{ !cancelled() }}")):
        workflows = _arm_workflows(
            tmp_path, f"noopif-{index}",
            lambda arm, c=cond: arm.update({"if": c}))
        assert "deploy-pages.yml" in _upstream_runners(workflows), (
            f"`if: {cond}` masks nothing — the arm still runs and still fails "
            f"the step, so the job is a runner")

    # …while a condition that DOES mask the gate stays disqualifying.
    for index, cond in enumerate(("false", "failure()")):
        workflows = _arm_workflows(
            tmp_path, f"maskif-{index}",
            lambda arm, c=cond: arm.update({"if": c}))
        assert "deploy-pages.yml" not in _upstream_runners(workflows), (
            f"`if: {cond}` does mask the gate — it must not count as a runner")


def test_4643_the_arm_is_recognised_through_every_legitimate_spelling(tmp_path):
    """The harness must not call a gate that WORKS on the real runner "not a
    runner". `$GITHUB_WORKSPACE`, `${{ github.workspace }}` (spaced and
    unspaced) and the repo's own `-m` module idiom all execute the arm, and bash
    cannot expand any of them here — so `_interpolate` (applied before bash sees
    the script) and the `-m` operand rule are what keep this honest. Unguarded,
    reverting either left the suite fully green (review finding, P2)."""
    base = "uv run --no-project --with pyyaml==6.0.3 python3"
    spellings = [
        "tools/served_skills_parity.py --check-upstream",
        '"$GITHUB_WORKSPACE/tools/served_skills_parity.py" --check-upstream',
        # Braced form: `_interpolate` handles only the unbraced one, so this case
        # is the ONLY thing that makes the hermetic `GITHUB_WORKSPACE` entry
        # load-bearing (review finding, P2 — reverting it left 85 green).
        '"${GITHUB_WORKSPACE}/tools/served_skills_parity.py" --check-upstream',
        '"${{ github.workspace }}/tools/served_skills_parity.py" --check-upstream',
        '"${{github.workspace}}/tools/served_skills_parity.py" --check-upstream',
        "-m tools.served_skills_parity --check-upstream",
    ]
    for index, spelling in enumerate(spellings):
        workflows = tmp_path / f"arm-{index}"
        workflows.mkdir()
        for name in ("deploy-pages.yml", "skills-parity.yml"):
            shutil.copyfile(WORKFLOWS / name, workflows / name)
        path = workflows / "deploy-pages.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        arm = next(s for s in doc["jobs"]["deploy-dashboard"]["steps"]
                   if isinstance(s.get("run"), str)
                   and "served_skills_parity" in s["run"])
        arm["run"] = f"{base} {spelling}"
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        assert "deploy-pages.yml" in _upstream_runners(workflows), (
            f"a gate spelled {spelling!r} works on the real runner but was "
            f"reported as NOT a runner — an instrument false positive")


def test_4643_an_expression_or_a_printed_remedy_does_not_disarm_a_gate(tmp_path):
    """Two false rejects, both measured: (a) the unmodelled-context rule was
    body-wide, so ANY `${{ … }}` in a step plus a `--sync` mention ANYWHERE in it
    dropped an honest gate — including the `${{ github.workspace }}` spelling the
    ARM half must recognise; (b) the tool's own remedy echoed verbatim
    (`… || { echo "uv run python tools/served_skills_parity.py --sync"; }`) did
    the same. Both must keep the runner; `${{ env['X'] }} --sync` and
    `${{ vars.X }} --sync` must still drop it (review finding, P2)."""
    base = "uv run --no-project --with pyyaml==6.0.3 python3"
    ws = '"${{ github.workspace }}/tools/served_skills_parity.py"'
    keep = [
        (f"{base} {ws} --check-upstream  # never run --sync before this", None),
        (f"{base} {ws} --check-upstream || {{ echo "
         '\"uv run python tools/served_skills_parity.py --sync\"; exit 1; }', None),
        (f"{base} {ws} --check-upstream || {{ echo "
         '\"Fix: `uv run python tools/served_skills_parity.py --sync`\"; exit 1; }',
         None),
    ]
    for index, (run, _) in enumerate(keep):
        workflows = tmp_path / f"keep-{index}"
        workflows.mkdir()
        for name in ("deploy-pages.yml", "skills-parity.yml"):
            shutil.copyfile(WORKFLOWS / name, workflows / name)
        path = workflows / "deploy-pages.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        arm = next(s for s in doc["jobs"]["deploy-dashboard"]["steps"]
                   if isinstance(s.get("run"), str)
                   and "served_skills_parity" in s["run"])
        arm["run"] = run
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        assert "deploy-pages.yml" in _upstream_runners(workflows), (
            f"an honest gate was disarmed by {run!r}")

    for index, spelling in enumerate(("${{ env['PARITY_TOOL'] }}",
                                      "${{ vars.PARITY_TOOL }}")):
        workflows = tmp_path / f"escape-{index}"
        workflows.mkdir()
        for name in ("deploy-pages.yml", "skills-parity.yml"):
            shutil.copyfile(WORKFLOWS / name, workflows / name)
        path = workflows / "deploy-pages.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = doc["jobs"]["deploy-dashboard"]["steps"]
        at = next(i for i, s in enumerate(steps)
                  if isinstance(s.get("run"), str)
                  and "served_skills_parity" in s["run"])
        steps.insert(at, {"name": "sync", "run": f'{base} "{spelling}" --sync'})
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        assert "deploy-pages.yml" not in _upstream_runners(workflows), (
            f"a --sync spelled through {spelling!r} still certified the job")


def test_4643_a_sync_in_a_separate_step_still_disarms_the_job(tmp_path):
    """Step-level rejection is not enough: `--sync` as its OWN step, followed by
    the arm, grades exactly the bytes the sync just wrote — the hazard the tool
    refuses inside one invocation. The job must lose its runner status
    (review finding, P2)."""
    workflows = tmp_path / "workflows"
    workflows.mkdir()
    for name in ("deploy-pages.yml", "skills-parity.yml"):
        shutil.copyfile(WORKFLOWS / name, workflows / name)
    path = workflows / "deploy-pages.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = doc["jobs"]["deploy-dashboard"]["steps"]
    index = next(i for i, s in enumerate(steps)
                 if isinstance(s.get("run"), str)
                 and "served_skills_parity" in s["run"])
    steps.insert(index, {
        "name": "Re-derive the mirror from upstream",
        "run": "uv run --no-project --with pyyaml==6.0.3 python3 "
               "tools/served_skills_parity.py --sync",
    })
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert "deploy-pages.yml" not in _upstream_runners(workflows), (
        "a job whose first step RE-DERIVES the mirror certifies the bytes it "
        "just wrote — it is not a fail-closed runner")


def test_4643_json_is_refused_with_sync_rather_than_silently_ignored(tmp_path, capsys):
    """The `--json` contract is unconditional (ONE document on stdout, so
    `json.loads(stdout)` works). The `--sync` branch returns BEFORE the JSON
    block, so accepting the flag printed human lines and no document on a run
    that exited 0 — the one combination a monitor would use. Refused, like
    `--sync --check*` (review finding, P2)."""
    with pytest.raises(SystemExit) as exc:
        sp.main(["--sync", "--json", "--root", str(tmp_path),
                 "--manifest", str(MANIFEST)])
    out = capsys.readouterr()
    assert exc.value.code not in (0, None), (
        "`--sync --json` must be refused, not accepted")
    assert "cannot be combined" in out.err or "cannot be combined" in out.out
    assert "would re-derive" not in out.out, (
        "the refusal must not print the human sync lines on stdout")

    # `--report` is its sibling in the same branch and had the same defect: the
    # sync branch returns before the report is written, so the operator got exit
    # 0 and no file. Same refusal, and specifically NO file left behind.
    report = tmp_path / "skills-parity-report.md"
    with pytest.raises(SystemExit) as exc:
        sp.main(["--sync", "--dry-run", "--report", str(report),
                 "--root", str(tmp_path), "--manifest", str(MANIFEST)])
    capsys.readouterr()
    assert exc.value.code not in (0, None), (
        "`--sync --report` must be refused, not silently ignored")
    assert not report.exists(), (
        "a refused `--sync --report` must not leave a report file")


def test_4643_a_sync_hidden_behind_env_or_pwd_still_disarms_the_job(tmp_path):
    """The sync guard must not be defeated by a spelling that hides the tool NAME
    from a text test: `env: {TOOL: …}` with `"$TOOL" --sync` (expanded here
    because the step's `env:` is passed into the hermetic run), `-m
    tools.served_skills_parity`, `$PWD/…` and `${{ github.workspace }}/…` (not
    expandable, caught by the fail-closed text fallback). Measured before this
    fix: every one of them left the job certified and the suite fully green
    (review finding, P2)."""
    spellings = [
        ({"TOOL": "tools/served_skills_parity.py"},
         'uv run --no-project --with pyyaml==6.0.3 python3 "$TOOL" --sync'),
        (None, "uv run --no-project --with pyyaml==6.0.3 python3 -m "
               "tools.served_skills_parity --sync"),
        (None, "uv run --no-project --with pyyaml==6.0.3 python3 "
               '"$PWD/tools/served_skills_parity.py" --sync'),
        (None, "uv run --no-project --with pyyaml==6.0.3 python3 "
               '"${{ github.workspace }}/tools/served_skills_parity.py" --sync'),
        # Unspaced: the regex must tolerate it, and bash expands neither form.
        (None, "uv run --no-project --with pyyaml==6.0.3 python3 "
               '"${{github.workspace}}/tools/served_skills_parity.py" --sync'),
    ]
    for env, run in spellings:
        workflows = tmp_path / f"wf-{abs(hash(run))}"
        workflows.mkdir()
        for name in ("deploy-pages.yml", "skills-parity.yml"):
            shutil.copyfile(WORKFLOWS / name, workflows / name)
        path = workflows / "deploy-pages.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = doc["jobs"]["deploy-dashboard"]["steps"]
        index = next(i for i, s in enumerate(steps)
                     if isinstance(s.get("run"), str)
                     and "served_skills_parity" in s["run"])
        smuggled = {"name": "Re-derive the mirror from upstream", "run": run}
        if env:
            smuggled["env"] = env
        steps.insert(index, smuggled)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        assert "deploy-pages.yml" not in _upstream_runners(workflows), (
            f"a job that runs --sync still certified itself as fail-closed for "
            f"this spelling: {run!r}")


def test_4643_a_sync_hidden_in_job_or_workflow_env_still_disarms_the_job(tmp_path):
    """`env:` is inherited from the workflow and the job, and GitHub expands all
    three levels. A probe that only merged the STEP's env left `"$PARITY_TOOL"
    --sync` expanding to empty, so the job stayed certified while it re-derived
    the mirror (review finding, P2)."""
    for level in ("job", "workflow"):
        workflows = tmp_path / f"wf-{level}"
        workflows.mkdir()
        for name in ("deploy-pages.yml", "skills-parity.yml"):
            shutil.copyfile(WORKFLOWS / name, workflows / name)
        path = workflows / "deploy-pages.yml"
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        env = {"PARITY_TOOL": "tools/served_skills_parity.py"}
        if level == "job":
            doc["jobs"]["deploy-dashboard"]["env"] = env
        else:
            doc["env"] = env
        steps = doc["jobs"]["deploy-dashboard"]["steps"]
        index = next(i for i, s in enumerate(steps)
                     if isinstance(s.get("run"), str)
                     and "served_skills_parity" in s["run"])
        steps.insert(index, {
            "name": "Re-derive the mirror from upstream",
            "run": 'uv run --no-project --with pyyaml==6.0.3 python3 '
                   '"${{ env.PARITY_TOOL }}" --sync',
        })
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        assert "deploy-pages.yml" not in _upstream_runners(workflows), (
            f"a job whose --sync is spelled through {level}-level `env:` still "
            f"certified itself as fail-closed")


def test_4643_an_honest_gate_is_not_disarmed_by_a_comment_or_a_lookalike(tmp_path):
    """The text fallback must decide like `_runs_sync`, not by substring: a
    COMMENT on the gate step (`# never run --sync`) and a lookalike
    `scripts/served_skills_parity.py --sync` both dropped a fully armed gate from
    the runner set, and the failure message blamed a disarmed step (review
    finding, P2)."""
    # (a) an inline comment mentioning --sync on the arm step itself
    workflows = tmp_path / "comment"
    workflows.mkdir()
    for name in ("deploy-pages.yml", "skills-parity.yml"):
        shutil.copyfile(WORKFLOWS / name, workflows / name)
    path = workflows / "deploy-pages.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = doc["jobs"]["deploy-dashboard"]["steps"]
    arm = next(s for s in steps if isinstance(s.get("run"), str)
               and "served_skills_parity" in s["run"])
    arm["run"] = arm["run"] + "   # never run --sync before this step"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert "deploy-pages.yml" in _upstream_runners(workflows), (
        "a comment mentioning --sync disarmed an honest gate")

    # (b) a lookalike script that is NOT this tree's tool
    workflows = tmp_path / "lookalike"
    workflows.mkdir()
    for name in ("deploy-pages.yml", "skills-parity.yml"):
        shutil.copyfile(WORKFLOWS / name, workflows / name)
    path = workflows / "deploy-pages.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = doc["jobs"]["deploy-dashboard"]["steps"]
    index = next(i for i, s in enumerate(steps)
                 if isinstance(s.get("run"), str)
                 and "served_skills_parity" in s["run"])
    steps.insert(index, {
        "name": "Lookalike",
        "run": "uv run python3 scripts/served_skills_parity.py --sync",
    })
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    assert "deploy-pages.yml" in _upstream_runners(workflows), (
        "a lookalike script path was mistaken for this tree's tool")


def test_4643_the_daily_runner_keeps_its_red_run_evidence():
    """`skills-parity.yml` promises red-run evidence: the report distinguishes
    DRIFT from COULD NOT VERIFY and is uploaded only when the gate fails, so a
    human diagnosing a red does not re-run a network gate. `if-no-files-found:
    ignore` makes losing it SILENT — deleting `--report` or the upload step left
    80/80 green (review finding, P2)."""
    doc = yaml.safe_load((WORKFLOWS / "skills-parity.yml").read_text(encoding="utf-8"))
    armed = [(job, step) for job in (doc.get("jobs") or {}).values()
             for step in (job.get("steps") or [])
             if _is_arm_step(step)]
    assert armed, "the daily runner has no arm step"
    for job, step in armed:
        body = str(step.get("run") or "")
        # Derive the report path from what the step actually RUNS, not from its
        # text: `--report` in a trailing comment writes no report, and a
        # substring test on the upload path accepted `reports/…` for a file the
        # tool writes at the repo root (review finding, P2).
        _rc, calls = _execute_step(body, flags=_shell_flags(step))
        report_paths = []
        for argv in calls:
            if "--report" in argv and _is_the_arm(argv):
                index = argv.index("--report")
                if index + 1 < len(argv):
                    report_paths.append(argv[index + 1])
        assert report_paths, (
            "the daily runner must EXECUTE --report <file> — the report is the "
            "red-run evidence its own comment promises (a `--report` in a "
            "comment writes nothing)")
        uploads = [s for s in (job.get("steps") or [])
                   if isinstance(s, dict)
                   and str(s.get("uses") or "").startswith(
                       "actions/upload-artifact")]
        assert uploads, (
            f"the report {report_paths[0]!r} must be uploaded, or no red run "
            f"has its evidence")
        for report_path in report_paths:
            wanted = report_path.removeprefix("./")
            assert any(
                str((u.get("with") or {}).get("path") or "").removeprefix("./")
                == wanted for u in uploads), (
                f"no upload carries exactly the report the gate writes "
                f"({wanted!r}); a substring match let `reports/{wanted}` pass "
                f"while upload-artifact matched nothing")


def test_4643_a_sync_step_is_not_a_gate(tmp_path):
    """`--sync` re-derives the mirror and returns 0 BEFORE either arm runs, so a
    deploy step written that way rewrites the pin at deploy time and gates
    nothing. The pre-fix predicate read it as the arm ("every tail except
    `--check-local`") and the stub's unrecognised-argv `exit 1` made it look
    fail-closed (review finding, P1)."""
    def mutate(step):
        step["run"] = step["run"].replace("--check-upstream", "--sync")

    workflows = _workflows_with_mutation(tmp_path, mutate)
    assert "deploy-pages.yml" not in _upstream_runners(workflows), (
        "a `--sync` step is certified as a fail-closed gate — it rewrites the "
        "pin instead of checking it, and could ship a mirror it just changed")


def _workflows_with_mutation(tmp_path: Path, mutate, mutate_job=None) -> Path:
    """The REAL workflows, with `mutate(step)` applied to deploy-pages.yml's arm
    step — so every assertion below is about an edit someone could actually make
    to the file that ships, not about a synthetic workflow.

    `mutate_job` is applied to that step's OWNING JOB (`deploy-dashboard`), for
    the disarmaments that take the job's teeth rather than the step's.

    Only the two workflows that carry the arm are copied: the predicate is read
    per-step, and the control test below asserts the UNMUTATED file is a runner,
    so a copy that silently lost the file cannot pass this.
    """
    out = tmp_path / "workflows"
    out.mkdir()
    for name in ("deploy-pages.yml", "skills-parity.yml"):
        shutil.copyfile(WORKFLOWS / name, out / name)
    path = out / "deploy-pages.yml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    steps = doc["jobs"]["deploy-dashboard"]["steps"]
    # Exactly ONE gate step, asserted: the head's P1 is "a step that SYNCS and
    # CHECKS is not a gate", and the two-STEP equivalent (`--sync` as its own
    # step, then the arm) grades the same bytes. A helper that silently mutated
    # the first of several tool-bearing steps turned a legitimately split job
    # into 15 misleading `cannot fail` failures — it must say what it assumes
    # (review finding, P2).
    candidates = [s for s in steps
                  if isinstance(s.get("run"), str)
                  and "served_skills_parity" in s["run"]]
    assert len(candidates) == 1, (
        f"this helper mutates exactly ONE gate step; deploy-dashboard has "
        f"{len(candidates)} tool-bearing steps. A split invocation (or a "
        f"smuggled `--sync` step) is a different question and must be asserted "
        f"deliberately, not mutated in the wrong step")
    arm = candidates[0]
    mutate(arm)
    if mutate_job is not None:
        mutate_job(doc["jobs"]["deploy-dashboard"])
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return out


#: Each is a documented way to keep a gate's TEXT and lose its TEETH. The
#: `continue-on-error` / `\|\| true` spellings are the repo's own named idioms for
#: turning a failed gate green (`tests/test_ci_selection.py::
#: test_drift_gate_cannot_skip_the_test_matrix` pins them as exactly that).
_DISARMAMENTS = [
    ("status_discarded_or_true", lambda s: s.update(run=s["run"] + " || true")),
    ("status_discarded_or_colon", lambda s: s.update(run=s["run"] + " || :")),
    # `set +e` FIRST, then the arm, then a masking last command: the arm really
    # runs and really fails, and the step still exits 0. (A `set +e` AFTER the
    # arm is not a disarmament — the runner's `bash -e` has already aborted.)
    ("set_plus_e_masking_last", lambda s: s.update(run="set +e\n" + s["run"] + "\ntrue")),
    ("wrapped_in_echo", lambda s: s.update(run='echo "' + s["run"] + '"')),
    ("python_dash_c_lookalike", lambda s: s.update(
        run="python3 -c pass " + s["run"].split("python ", 1)[-1])),
    ("continue_on_error_step", lambda s: s.update(**{"continue-on-error": True})),
    ("skipped_by_if", lambda s: s.update(**{"if": "false"})),
    # Review finding (P3): the operand is judged by NAME only, so a lookalike
    # script that always exits 0 was accepted. The stub still fails here, so
    # `_is_arm_step`'s status half passes and ONLY the tree-resolution check can
    # reject it — this case is the non-vacuity proof of that check.
    ("shim_operand_not_the_shipped_tool", lambda s: s.update(
        run=s["run"].replace("tools/served_skills_parity.py",
                             "scripts/served_skills_parity.py"))),
    # The flag is on a DIFFERENT command: the tool runs `--check-local` only.
    ("flag_on_a_different_command", lambda s: s.update(
        run="uv run python tools/served_skills_parity.py --check-local"
            " && /bin/echo --check-upstream")),
    # Executed, but under `if`: bash -e does not abort on a failing CONDITION, so
    # a red gate leaves the step green.
    ("arm_in_an_if_condition", lambda s: s.update(
        run="if " + s["run"] + "; then true; fi")),
    # Executed in the background: the step returns before the arm can fail it.
    ("arm_backgrounded", lambda s: s.update(run=s["run"] + " &")),
    # A PIPELINE hides the arm's status. Neither runner declares `shell:`, so both
    # get GitHub's implied default `bash -e` — no `pipefail` — and `| tee` exits 0
    # on a failing arm. Piping a gate's output through `tee`/`tail` for visibility
    # is the most idiomatic way to disarm it, and the pre-fix harness ran `-o
    # pipefail` regardless, so it certified a gate CI had already disarmed
    # (review finding, P2).
    ("piped_to_tee", lambda s: s.update(run=s["run"] + " | tee arm.log")),
    # `--sync` RE-DERIVES the mirror and returns 0 before either arm runs, so a
    # deploy step written that way rewrites the pin at deploy time and gates
    # nothing — while the pre-fix predicate ("every tail except `--check-local`")
    # certified it, because the stub failed on the argv it did not recognise.
    # This is the P1 class one argv away (review finding, P1).
    ("sync_instead_of_check", lambda s: s.update(
        run=s["run"].replace("--check-upstream", "--sync"))),
    # `--sync && --check-upstream` is the two-invocation form of what the tool
    # refuses inside one: the check grades the bytes `--sync` just rewrote. The
    # compositional predicate ("ANY recorded argv is the arm") certified it
    # while the deploy shipped unreviewed upstream bytes (review finding, P1).
    ("sync_then_check", lambda s: s.update(
        run="uv run --no-project --with pyyaml==6.0.3 python3 "
            "tools/served_skills_parity.py --sync && " + s["run"])),
]


@pytest.mark.parametrize("name,mutate", _DISARMAMENTS, ids=[n for n, _ in _DISARMAMENTS])
def test_4643_a_runner_that_cannot_fail_is_not_a_runner(tmp_path, name, mutate):
    """Review finding (P2), measured on the pre-fix code: all of these stayed
    GREEN through `test_4643_the_upstream_arm_has_a_runner_and_the_docs_name_it`
    — the predicates read the step's TEXT only, so `echo`, `|| true`,
    `| tee`, `|| :` and `continue-on-error:` kept every surface claiming a
    fail-closed gate that could no longer fail. `\u00a77.1`'s "two runners, both
    failing closed", `skills-parity.yml`'s header and this file all still
    passed, and only a human reading the diff would have noticed.

    `; exit 0` is deliberately NOT in this list: under `bash -e` a bare failing
    line already aborts the step, so appending `; exit 0` disarms nothing and a
    case for it could never red."""
    workflows = _workflows_with_mutation(tmp_path, mutate)
    if name == "piped_to_tee" and "pipefail" in _shell_flags(
            _gate_step_in(workflows)):
        # `| tee` disarms ONLY under the implied default shell. With
        # `shell: bash` — GitHub's pipefail shell, and the standard remedy for a
        # piped command — the step still fails closed, so this case must not
        # claim otherwise: the message it prints would tell a maintainer to
        # un-harden CI (review finding, P2).
        assert "deploy-pages.yml" in _upstream_runners(workflows), (
            "a piped gate under `shell: bash` still fails the step; the "
            "disarmament case must not call it a gate that gates nothing")
        return
    assert "deploy-pages.yml" not in _upstream_runners(workflows), (
        f"{name}: the step still reads as the gate but cannot fail — the "
        "wiring guard must stop calling it a runner (it is the P1 class: a "
        "gate that gates nothing, with every surface green)")


#: Each leaves the arm STEP intact and takes the JOB's teeth instead — the same
#: silent green one level up, where GitHub reports the run as a success.
_JOB_DISARMAMENTS = [
    ("job_continue_on_error",
     lambda job: job.update(**{"continue-on-error": True})),
    ("job_skipped_by_if", lambda job: job.update(**{"if": "false"})),
]


@pytest.mark.parametrize("name,mutate", _JOB_DISARMAMENTS,
                         ids=[n for n, _ in _JOB_DISARMAMENTS])
def test_4643_a_job_that_cannot_fail_is_not_a_runner(tmp_path, name, mutate):
    """Review finding (P2), measured on the pre-fix predicate: both of these
    left **49 passed**. The guard judged `continue-on-error`/`if:` on the STEP
    only, and the job carries neither, so the claim "the runner can fail the
    job" was false at the job level while `\u00a77.1` said "two runners, both
    failing closed".

    What it costs: `continue-on-error: true` on the job takes the dashboard
    deploy down and GitHub still reports GREEN; `if: false` skips the job
    entirely. Either way the dashboard silently STOPS DEPLOYING — no red, no
    page, and the mirror drifts with every claim here intact."""
    workflows = _workflows_with_mutation(tmp_path, lambda step: None,
                                         mutate_job=mutate)
    assert "deploy-pages.yml" not in _upstream_runners(workflows), (
        f"{name}: the job owning the arm step is not allowed to fail, so a "
        "failing upstream arm leaves the run GREEN and the dashboard deploys "
        "nothing — the wiring guard must stop calling this a runner")
    # …and the other workflow is untouched: the job rule must not be a blanket
    # "no workflow is a runner", which would pass this case for the wrong reason.
    assert "skills-parity.yml" in _upstream_runners(workflows), (
        f"{name}: only the mutated workflow lost its runner — the job-level "
        "rule leaked into a workflow whose own job is fine")


#: Spellings that DO run the upstream arm, so they must still be recognised as
#: runners. Both were rejected before the predicate decided on semantics: it
#: required the literal `--check-upstream`, while §7.1 documents `--check` and a
#: bare invocation runs both arms (review finding, P3 — a guard whose remedy is
#: "narrow the gate back" pushes in the wrong direction).
_ARM_SPELLINGS = [
    ("documented_check_flag",
     lambda s: s.update(run=s["run"].replace("--check-upstream", "--check"))),
    ("bare_invocation_runs_both_arms",
     lambda s: s.update(run=s["run"].replace(" --check-upstream", ""))),
    # `main()` is cumulative: an arm flag anywhere wins over `--check-local`, so
    # adding the upstream half to a step that already runs the offline arm is a
    # legitimate spelling — the predicate must not call it decoration and push
    # the maintainer to DROP `--check-local` (review finding, P2).
    ("explicit_both_halves",
     lambda s: s.update(run=s["run"].replace(
         "--check-upstream", "--check-local --check-upstream"))),
]


@pytest.mark.parametrize("name,mutate", _ARM_SPELLINGS,
                         ids=[n for n, _ in _ARM_SPELLINGS])
def test_4643_any_spelling_that_runs_the_arm_is_a_runner(tmp_path, name, mutate):
    """The doc names `--check`; the tool runs the upstream arm for `--check`, for
    `--check-upstream`, and for a bare invocation. A predicate that knows only one
    spelling reds a step that DOES gate, and the only way to clear it is to narrow
    the step back to the spelling the test happens to know."""
    workflows = _workflows_with_mutation(tmp_path, mutate)
    assert "deploy-pages.yml" in _upstream_runners(workflows), (
        f"{name}: this spelling runs the upstream arm, so the step is a runner")


def test_4643_the_arm_operand_must_be_the_shipped_tool():
    """The cited model (`tests/test_ci_guard_invocation.py`) resolves the operand
    into the tree; this predicate did not, so any path whose last component was
    the right filename was accepted — including a shim that always exits 0."""
    assert _is_the_arm(["uv", "run", "python",
                        "tools/served_skills_parity.py", "--check-upstream"])
    assert _is_the_arm(["uv", "run", "python",
                        "tools/served_skills_parity.py", "--check"])
    assert not _is_the_arm(["uv", "run", "python",
                            "tools/served_skills_parity.py", "--check-local"])
    assert not _is_the_arm(["uv", "run", "python",
                            "scripts/served_skills_parity.py", "--check-upstream"]), (
        "a lookalike script is not this tree's gate")
    assert not _is_the_arm(["python3", "-c", "pass",
                            "tools/served_skills_parity.py", "--check-upstream"])


def test_4643_the_predicate_and_the_stub_agree_about_the_arm():
    """The step-level verdict has two halves — the recorded argv is the arm, and
    a failing arm fails the STEP — so a disagreement between the predicate and
    the recording stub hides one of them. Reverting only the predicate, or only
    the stub, left every other test green (review finding, P2), so each side is
    pinned here directly.

    The semantics are `main()`'s, which are cumulative: `--help` exits 0;
    `--sync` alone runs no arm and combined with a check is refused (exit 2);
    an arm flag anywhere wins over `--check-local`; bare runs both arms."""
    tool = str(THE_TOOL)
    arm = f"uv run --no-project --with pyyaml python3 {tool}"

    assert _is_the_arm(["python3", tool]), "a bare invocation runs both arms"
    assert _is_the_arm(["python3", tool, "--check-upstream"])
    assert _is_the_arm(["python3", tool, "--check"])
    assert _is_the_arm(["python3", tool, "--check-local", "--check-upstream"]), (
        "an arm flag anywhere wins over --check-local: adding the upstream half "
        "to a step that already runs the offline arm is legitimate")
    assert not _is_the_arm(["python3", tool, "--check-local"])
    assert not _is_the_arm(["python3", tool, "--sync"])
    assert not _is_the_arm(["python3", tool, "--check-upstream", "--sync"]), (
        "`--sync` with a check is refused by main() (exit 2) — it never runs "
        "the arm, and it must not be certified as the gate")
    assert not _is_the_arm(["python3", tool, "--help"])

    assert _execute_step(arm, fail_arm=True)[0] != 0, (
        "a bare invocation runs the arm, so a failing arm must fail the step")
    assert _execute_step(f"{arm} --sync", fail_arm=True)[0] == 0, (
        "the stub must not fail on `--sync` — that is what made a sync step "
        "look fail-closed")
    assert _execute_step(f"{arm} --sync --check-upstream", fail_arm=True)[0] == 2
    assert _execute_step(f"{arm} --check-local --check-upstream",
                         fail_arm=True)[0] != 0, (
        "with --check-local present the arm still runs, so the step still fails")
    assert _execute_step(f"{arm} --help", fail_arm=True)[0] == 0


def test_4643_the_real_deploy_step_is_executed_and_its_failure_propagates(tmp_path):
    """The control for the mutation table: without it, a predicate that returned
    False for everything would pass every case above. Both halves are asserted —
    the recorded argv IS the arm (so `echo` is not enough), and the step's own
    status carries the stub's failure (so a status swallow is not enough)."""
    workflows = _workflows_with_mutation(tmp_path, lambda step: None)
    runners = _upstream_runners(workflows)
    assert "deploy-pages.yml" in runners, (
        "the real deploy step must be recognised as a runner — the mutations "
        "above are only meaningful if the unmutated file passes")
    assert "skills-parity.yml" in runners, (
        "…and so must the daily workflow: the predicate is not specific to one "
        "spelling of the command")

    step = next(s for s in runners["deploy-pages.yml"]["jobs"]["deploy-dashboard"]
                ["steps"] if _is_arm_step(s))
    rc, calls = _execute_step(step["run"], flags=_shell_flags(step))
    assert rc == 0, calls
    assert any(_is_the_arm(argv) for argv in calls), (
        f"the step must EXECUTE the arm; recorded calls: {calls}")
    assert _execute_step(step["run"], fail_arm=True,
                         flags=_shell_flags(step))[0] != 0, (
        "a failing arm must fail the STEP — that is what 'failing closed' means "
        "to the runner, and it is the half a text scan cannot see")
    # The `--no-project --with pyyaml` prefix is a review fix in its own right:
    # a bare `uv run` syncs the whole project + `dev` group (jax, scikit-learn,
    # playwright) from PyPI before the gate starts — minutes on every deploy,
    # including a rollback — and adds PyPI as a fourth network dependency the
    # step's "a network blip must never pass" comment does not price in. It was
    # unguarded: reverting it in BOTH workflows left the suite fully green
    # (review finding, P2). Asserted on the DERIVED runner set, so a new runner
    # cannot arrive without it.
    for name, doc in runners.items():
        for _job_id, _job, gate in _arm_steps(doc):
            body = str(gate.get("run") or "")
            assert "--no-project" in body and "--with pyyaml" in body, (
                f"{name}: the gate must run isolated (`uv run --no-project "
                f"--with pyyaml…`) — a bare `uv run` syncs the whole project "
                f"from PyPI before the gate starts")
