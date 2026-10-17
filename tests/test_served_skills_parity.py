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
  HEAD are still the pinned bytes). A pin-only gate is vacuous — pin any old
  commit and "byte-identical" becomes true of history — so these tests prove the
  currency arm is wired to the pin rather than the other way round. They
  monkeypatch the two network functions.

⛔ WHAT IS NOT TESTED HERE, BECAUSE IT IS NOT IN THIS TREE. The wiring that would
RUN `--check-upstream` — a step in `deploy-pages.yml` before the build stages
`public/`, and a scheduled `skills-parity.yml` that auto-files — is a
`.github/workflows/*` change this lane's credential cannot push (#8129). So those
assertions live with that change, and this file asserts the *honest limit*
instead (`test_4643_the_ownership_statement_names_one_repo_per_artifact`): §7.1
must name #8129 as the pending blocker rather than describe the arm as running.
When the wiring lands, that assertion flips to requiring the two workflows — the
handoff is a red test, not a stale sentence.

The pin itself carries no assertion here: `test_4643_the_pin_covers_exactly_the_
installed_set` ties it to the installer's `SKILLS=(...)` array, so a fourth
served-and-installed skill cannot appear without a pin (and a pinned skill
cannot be dropped from the installer) — the same ratchet shape
`tests/test_served_skills.py` uses for the served set.

"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
                  head_payloads: dict[str, bytes] | None = None):
    """Patch the two network seams.

    ``payloads`` are the bytes at the PINNED commit; ``head_payloads`` (default:
    the same) are the bytes at ``head``. The currency arm fetches BOTH, so a
    test that only moves the pointer is not exercising a drift.
    """
    at_head = payloads if head_payloads is None else head_payloads
    monkeypatch.setattr(sp, "remote_head", lambda *a, **k: head)
    monkeypatch.setattr(
        sp, "fetch_raw",
        lambda repo, commit, path, *a, **k: (at_head if commit == head else payloads)[path])


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

    The MIT repo also takes commits that cannot change what we serve — a README
    edit, a new unrelated skill, CI config (`6ebf0c9` is itself such a commit).
    Keying currency on `HEAD == pinned` reds the dashboard deploy for a reason
    unrelated to the artifact, auto-files a FALSE "the served mirror drifted"
    P2, and diagnoses the cause as "a skill changed there" — which did not
    happen. The pinned bytes still guard the arm: they are compared against what
    upstream has at HEAD, so a real skill edit reds (the test above) and an
    unrelated commit is green.
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


# ── the ownership record (target 3) ───────────────────────────────────────


def test_4643_the_ownership_statement_names_one_repo_per_artifact():
    """Target 3. The two channels disagreed about who authors what — the served
    installer claimed the canonical sources live in `daniel-ospina/tortoise`
    while the MIT repo claimed itself — and that ambiguity is what produced the
    duplicate authoring in the first place."""
    installer = INSTALLER.read_text(encoding="utf-8")
    assert "tortoise-skills-and-integrations" in installer, (
        "the served installer's header must name the MIT repo as the authored "
        "source of the three reusable capabilities (#4643 target 3)")
    assert "tortoise/onboarding/SKILL.md" in installer, (
        "…and name this repo for the artifact it DOES author (the onboarding "
        "instructions), so a contributor knows which repo to edit")
    notes = (REPO / "docs" / "license-notes.md").read_text(encoding="utf-8")
    # ⛔ §7.1, not §7: the ownership RULE this issue adds is §7.1, and §7 alone
    # matched before this PR — so "## 7." would still pass with §7.1 deleted.
    assert "### 7.1" in notes and "tortoise-skills-and-integrations" in notes
    assert "config/served-skills-upstream.yml" in notes, (
        "the ownership record must name the pin that enforces it")
    # CLAIM-MISMATCH GUARD (review finding, P1/P2): §7.1 must not describe the
    # upstream arm as RUNNING while no workflow invokes it. The runner is a
    # `.github/workflows/*` change this lane cannot push (#8129), so the record
    # names that blocker — and this assertion makes the pending state
    # load-bearing instead of prose. ⛔ When the wiring lands, flip this to
    # require `deploy-pages.yml` + `skills-parity.yml` to exist AND run
    # `--check`; the red test is the handoff.
    if not (REPO / ".github" / "workflows" / "skills-parity.yml").is_file():
        assert "#8129" in notes, (
            "§7.1 names the upstream arm without a workflow that runs it — until "
            "the wiring lands it must name #8129 as the pending blocker (docs "
            "must not promise enforcement nothing runs)")
        assert "not yet wired" in notes, (
            "§7.1 must say the arm is not yet wired, in the tense a reader can "
            "rely on")
    else:
        # The wiring IS in the tree (the authorized lane pushed #8129): §7.1 then
        # owes the present-tense claim, and the runners it names must exist.
        for runner in ("deploy-pages.yml", "skills-parity.yml"):
            assert runner in notes, (
                f"§7.1 must name {runner} as a runner of the gate now that it exists")
            assert (REPO / ".github" / "workflows" / runner).is_file()
