"""#12926 — a shipped deployment config that omits the embedding switches must be REFUSED.

`#11445` put the "what earns an embedding" guarantee in the **shipped configuration**, not in a
code default, and `#12926` measured that nothing enforced it: `docker compose config -q` proves a
compose file *parses* and says nothing about any environment key, while the two switches read
**opposite** ways when unset (`value_gate` ON, `embed_gate` OFF — both modules record that
changing either is a reopen of `#11445`).

Each test below is a falsifier for one claim the check makes:

| test | claim it falsifies | it goes RED when … |
|---|---|---|
| `test_neither_code_default_moved` | ⛔ the PR does not change a default | unset stops reading `embed_gate=OFF` · `value_gate=ON` — the reopen `#12926` forbids |
| `test_the_switch_names_are_the_resolvers_own` | one spelling of the names | the check hard-codes a name the gate modules no longer read |
| `test_a_channel_that_omits_either_switch_is_refused` | the Objective, both directions | an omission passes, or a config stating both is refused |
| `test_the_refusal_names_the_switch_and_the_channel` | the failure is actionable | the message drops the switch or the file, so a reader cannot act |
| `test_a_file_that_was_read_and_omits_a_switch_is_not_called_unreadable` | `#12549` defect 1's wording | "read fine, omits it" is reported with the word a permissions problem would need |
| `test_a_channel_that_could_not_be_read_is_refused` | "a channel you could not read FAILS" | an unreadable/unresolvable channel passes quietly |
| `test_a_compose_patch_is_refused_by_name` | the `#12070` item 8 direction | an override file is dropped silently, or half-read as if it were a base |
| `test_nothing_discovered_is_not_a_pass` | "nothing compared is not a pass" | an empty tree passes |
| `test_a_new_compose_file_is_discovered` | discovery, not a list | a second channel lands and the check does not walk it |
| `test_a_service_this_repo_does_not_build_is_reported_not_required` | the switch is read by TORTOISE | a third-party sidecar is required to set a Python-app switch, or a Tortoise-carrying pulled service is skipped |
| `test_the_ambient_environment_cannot_arm_a_channel` | `#10996`'s doctrine | a lane's exported flag resolves a `${…}`/value-less entry and the channel reads armed |
| `test_the_resolution_is_per_service` | the resolver's own scope | one service arming another's switch |
| `test_the_dockerfile_reader_reads_both_forms_and_fails_closed` | the image channel's reader | `ENV K V` is missed, or a shape mentioning a switch passes unread |
| `test_the_entrypoint_reader_reads_defaults_and_ignores_a_valueless_export` | the entrypoint channel's reader | `${K:-1}` is unread, or a bare `export K` counts as stating it |
| `test_the_startup_line_states_both_effective_values` | the issue's Visibility target | a value is missing, or the line disagrees with the resolvers |
| `test_both_daemons_report_at_boot` | the wiring | the line exists but nothing calls it — the silence it exists to end |
| `test_the_pre_merge_check_runs_in_ci` | the enforcement | the tool exists and CI never runs it |
| `test_the_real_tree_states_both_switches` | this repository, resolved | a real channel regresses; also pins the population so a new channel is a visible change |
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import embed_switch_gate as gate  # noqa: E402

from tortoise import embed_gate, gate_visibility, value_gate  # noqa: E402

SWITCHES = ("TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE")
DOCKER = shutil.which("docker") is not None
needs_docker = pytest.mark.skipif(
    not DOCKER, reason="`docker compose config` is the compose resolution; no docker CLI here"
)

COMPOSE_TEMPLATE = """\
services:
  app:
    build: .
    environment:
{env}
"""


@pytest.fixture(autouse=True)
def _no_ambient_switches():
    """A lane's own exported flag must never make a channel read armed (#10996)."""
    keys = ("TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE")
    before = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _compose(root: Path, *, value: str | None = "1", embed: str | None = "1") -> None:
    lines = []
    if value is not None:
        lines.append(f'      TORTOISE_VALUE_GATE: "{value}"')
    if embed is not None:
        lines.append(f'      TORTOISE_EMBED_GATE: "{embed}"')
    (root / "docker-compose.yml").write_text(COMPOSE_TEMPLATE.format(env="\n".join(lines) or "      {}"))


def _fly(root: Path, *, value: str | None = "1", embed: str | None = "1") -> None:
    lines = []
    if value is not None:
        lines.append(f'TORTOISE_VALUE_GATE = "{value}"')
    if embed is not None:
        lines.append(f'TORTOISE_EMBED_GATE = "{embed}"')
    (root / "fly.toml").write_text('app = "t"\n\n[env]\n' + "\n".join(lines) + "\n")


def _dockerfile(root: Path, *, value: bool = True, embed: bool = True) -> None:
    lines = ["FROM python:3.12-slim"]
    if value:
        lines.append("ENV TORTOISE_VALUE_GATE=1")
    if embed:
        lines.append("ENV TORTOISE_EMBED_GATE=1")
    (root / "Dockerfile").write_text("\n".join(lines) + "\n")


def _entrypoint(root: Path, *, value: bool = True, embed: bool = True) -> None:
    lines = ["#!/bin/sh", "set -eu"]
    if value:
        lines.append('export TORTOISE_VALUE_GATE="${TORTOISE_VALUE_GATE:-1}"')
    if embed:
        lines.append('export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"')
    (root / "entrypoint.sh").write_text("\n".join(lines) + "\n")


# ── the defaults that must NOT move ──────────────────────────────────────────────────────────


def test_neither_code_default_moved():
    """⛔ The falsifier `#12926` states outright: unset still yields today's two values.

    They are OPPOSITE on purpose, and `#11445` decided the guarantee is the shipped config, not
    these defaults. A "unification" here reverses that decision without saying so, which is a
    reopen — so this pin is not a style preference.
    """
    assert "TORTOISE_EMBED_GATE" not in os.environ
    assert "TORTOISE_VALUE_GATE" not in os.environ
    assert embed_gate.embed_gate_enabled() is False, "unset must still read embed_gate=OFF"
    assert value_gate.value_gate_enabled() is True, "unset must still read value_gate=ON"


@pytest.mark.parametrize("raw", [None, "", "  ", "1", "true", "yes", "on", "0", "false", "no", "off", "garbage"])
def test_the_one_default_site_agrees_with_each_gates_own_read(raw, monkeypatch):
    """One resolution order per switch: `resolve_enabled(raw)` IS what `*_gate_enabled()` does.

    The startup line reports through `resolve_enabled`, so this is what makes "the log cannot
    say ON while the gate behaves OFF" a measured fact rather than an intention.
    """
    for env_flag, resolve, enabled in (
        (embed_gate.ENV_FLAG, embed_gate.resolve_enabled, embed_gate.embed_gate_enabled),
        (value_gate.ENV_FLAG, value_gate.resolve_enabled, value_gate.value_gate_enabled),
    ):
        if raw is None:
            monkeypatch.delenv(env_flag, raising=False)
        else:
            monkeypatch.setenv(env_flag, raw)
        assert resolve(os.environ.get(env_flag)) is enabled(), (env_flag, raw)


def test_the_switch_names_are_the_resolvers_own():
    """The check's names are the gate modules' `ENV_FLAG`s — never a second spelling."""
    assert gate.SWITCHES == (value_gate.ENV_FLAG, embed_gate.ENV_FLAG)
    assert set(gate.SWITCHES) == set(SWITCHES)
    assert [switch for switch, _resolve, _default in gate_visibility.SWITCHES] == list(gate.SWITCHES)
    for switch, resolve, _default in gate_visibility.SWITCHES:
        assert resolve.__module__.startswith("tortoise."), switch


# ── the Objective: an omission is refused, both switches, both directions ────────────────────


@pytest.mark.parametrize("omitted", SWITCHES)
@needs_docker
def test_a_channel_that_omits_either_switch_is_refused(tmp_path, omitted):
    _compose(tmp_path, value="1", embed="1")
    kind = "value" if omitted == "TORTOISE_VALUE_GATE" else "embed"
    _compose(tmp_path, **{kind: None})
    channels, failures = gate.evaluate(tmp_path)

    assert failures, "a resolved config that omits a switch must be refused"
    assert any(omitted in failure and "docker-compose.yml" in failure for failure in failures), failures
    assert [c.ok for c in channels] == [False]

    _compose(tmp_path)  # both stated
    _channels, failures = gate.evaluate(tmp_path)
    assert failures == [], f"a config stating both must pass, got {failures}"


@needs_docker
def test_the_refusal_names_the_switch_and_the_channel(tmp_path):
    _compose(tmp_path, embed=None)
    _fly(tmp_path)
    _dockerfile(tmp_path)
    _entrypoint(tmp_path)
    _channels, failures = gate.evaluate(tmp_path)
    assert failures == ["docker-compose.yml: app does not state TORTOISE_EMBED_GATE "
                        "(docker compose config --format json)"]


@needs_docker
def test_a_file_that_was_read_and_omits_a_switch_is_not_called_unreadable(tmp_path):
    """`#12549` defect 1: a file the walk READ must never be reported as UNREADABLE.

    The two states are different facts — one sends a reader to fix the config, the other to
    chase a permissions/tooling problem that does not exist — so they are different words.
    """
    _compose(tmp_path, embed=None)
    _channels, failures = gate.evaluate(tmp_path)
    text = "\n".join(failures).lower()
    assert "does not state" in text
    for wrong in ("unreadable", "could not be read", "refused"):
        assert wrong not in text, f"{wrong!r} names a state this channel is not in: {failures}"


# ── "a channel you could not read makes the check fail" ─────────────────────────────────────


@needs_docker
def test_a_channel_that_could_not_be_read_is_refused(tmp_path):
    (tmp_path / "docker-compose.yml").write_text("services: [this is not a mapping\n")
    channels, failures = gate.evaluate(tmp_path)
    assert any("COULD NOT BE READ" in failure for failure in failures), failures
    assert all(not channel.ok for channel in channels)


def test_a_compose_patch_is_refused_by_name(tmp_path):
    """An override merges LAST, so it can DISARM an armed base (`#12070` item 8).

    This gate models no merge order, so it refuses the file by name instead of reading it as if
    it were a base — either direction (silent drop, or a per-file read) is a wrong verdict.
    """
    _compose(tmp_path)
    patch = tmp_path / "docker-compose.override.yml"
    patch.write_text('services:\n  app:\n    environment:\n      TORTOISE_EMBED_GATE: "0"\n')
    _channels, failures = gate.evaluate(tmp_path)
    assert any("docker-compose.override.yml" in failure and "REFUSED" in failure for failure in failures), failures


def test_nothing_discovered_is_not_a_pass(tmp_path):
    channels, failures = gate.evaluate(tmp_path)
    assert channels == []
    assert failures and "nothing was compared" in failures[0]


# ── discovery, not a list ────────────────────────────────────────────────────────────────────


@needs_docker
def test_a_new_compose_file_is_discovered(tmp_path):
    _compose(tmp_path)
    (tmp_path / "docker-compose.staging.yml").write_text("services:\n  app:\n    build: .\n")
    channels, failures = gate.evaluate(tmp_path)
    labels = [channel.channel for channel in channels]
    assert "docker-compose.yml: app" in labels, labels  # the base is still walked
    assert "docker-compose.staging.yml" in labels, (
        f"a newly added compose file must be DISCOVERED (not silently dropped): {labels}"
    )
    assert any("docker-compose.staging.yml" in failure for failure in failures), failures


@needs_docker
def test_a_service_this_repo_does_not_build_is_reported_not_required(tmp_path):
    """The switch is read by TORTOISE: a pulled sidecar is not asked for it; a Tortoise-carrying
    pulled service IS (otherwise "no `build:`" would be a way to escape the gate)."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    environment:\n"
        '      TORTOISE_VALUE_GATE: "1"\n'
        '      TORTOISE_EMBED_GATE: "1"\n'
        "  db:\n"
        "    image: falkordb/falkordb-server:v4.20.4\n"
        "    environment:\n"
        '      REDIS_ARGS: "--appendonly yes"\n'
    )
    channels, failures = gate.evaluate(tmp_path)
    assert failures == []
    noted = [channel for channel in channels if channel.noted]
    assert [channel.channel for channel in noted] == ["docker-compose.yml: db"]
    assert "not a channel" in noted[0].noted

    # The same pulled service, carrying Tortoise config, IS a channel.
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    image: ghcr.io/daniel-ospina/tortoise-selfhost:local\n"
        "    environment:\n"
        '      TORTOISE_API_KEY: "x"\n'
    )
    _channels, failures = gate.evaluate(tmp_path)
    assert any("does not state" in failure for failure in failures), failures


@needs_docker
def test_the_ambient_environment_cannot_arm_a_channel(tmp_path):
    """A lane's exported flag is not a shipped default (`#10996`'s doctrine).

    A value-less compose entry resolves from the environment compose runs in, so without a
    scrub a lane that exported the switch would see its own shell as the channel's arm.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  app:\n    build: .\n    environment:\n      - TORTOISE_VALUE_GATE\n      - TORTOISE_EMBED_GATE\n"
    )
    os.environ["TORTOISE_VALUE_GATE"] = "1"
    os.environ["TORTOISE_EMBED_GATE"] = "1"
    channels, failures = gate.evaluate(tmp_path)
    assert all(not channel.ok for channel in channels), [c.as_dict() for c in channels]
    assert len(failures) == 2, failures


@needs_docker
def test_the_resolution_is_per_service(tmp_path):
    """One service arming does not arm its neighbour — the resolver's own scope."""
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  armed:\n"
        "    build: .\n"
        "    environment:\n"
        '      TORTOISE_VALUE_GATE: "1"\n'
        '      TORTOISE_EMBED_GATE: "1"\n'
        "  silent:\n"
        "    build: .\n"
        "    environment:\n"
        '      TORTOISE_DB_URI: "docker://:x@db:6379/g"\n'
    )
    _channels, failures = gate.evaluate(tmp_path)
    assert failures == [
        "docker-compose.yml: silent does not state TORTOISE_VALUE_GATE (docker compose config --format json)",
        "docker-compose.yml: silent does not state TORTOISE_EMBED_GATE (docker compose config --format json)",
    ]


# ── the two readers with no external resolver ───────────────────────────────────────────────


def test_the_dockerfile_reader_reads_both_forms_and_fails_closed():
    stated, ambiguous = gate._dockerfile_env(
        "FROM x\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=on\n"
        "ENV OTHER 2\n"
        "ENV CONTINUED=1 \\\n"
        "    ALSO=2\n"
    )
    assert stated["TORTOISE_VALUE_GATE"] == "1"
    assert stated["TORTOISE_EMBED_GATE"] == "on"
    assert stated["OTHER"] == "2"
    assert stated["CONTINUED"] == "1" and stated["ALSO"] == "2"
    assert ambiguous == set()

    # legacy `ENV K V` form, alone
    stated, _ = gate._dockerfile_env("ENV TORTOISE_EMBED_GATE 0\n")
    assert stated == {"TORTOISE_EMBED_GATE": "0"}

    # a shape that MENTIONS the switch but states no value this reader reads → refused
    stated, ambiguous = gate._dockerfile_env("ENV TORTOISE_VALUE_GATE\n")
    assert stated == {} and ambiguous == {"TORTOISE_VALUE_GATE"}
    channel = gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV", stated=stated,
                           ambiguous=tuple(sorted(ambiguous)))
    assert "mentions TORTOISE_VALUE_GATE but not as an assignment" in channel.problems()[0]


def test_the_entrypoint_reader_reads_defaults_and_ignores_a_valueless_export():
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'export TORTOISE_VALUE_GATE="${TORTOISE_VALUE_GATE:-1}"\n'
        "export TORTOISE_EMBED_GATE=1\n"
        "export SOMETHING_ELSE\n"
    )
    assert stated["TORTOISE_VALUE_GATE"] == "${TORTOISE_VALUE_GATE:-1}"
    assert stated["TORTOISE_EMBED_GATE"] == "1"
    assert ambiguous == set()

    # `export NAME` states no value — it exports whatever the caller had, which is the
    # absence this gate exists to refuse.
    stated, _ = gate._shell_assignments("export TORTOISE_EMBED_GATE\n")
    assert stated == {}

    # an empty assignment is a placeholder, not an arm
    channel = gate.Channel("entrypoint.sh", "entrypoint", "shell assignments",
                           stated={"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": ""})
    problems = channel.problems()
    assert len(problems) == 1 and "EMPTY value" in problems[0], problems


# ── Visibility ───────────────────────────────────────────────────────────────────────────────


def test_the_startup_line_states_both_effective_values(capsys):
    line = gate_visibility.report_effective_switches()
    printed = capsys.readouterr().err
    assert printed.strip() == line
    for switch in SWITCHES:
        assert f"{switch}=" in line
    # the effective values ARE the resolvers' answers, not a re-derivation
    assert f"TORTOISE_VALUE_GATE={'ON' if value_gate.value_gate_enabled() else 'OFF'}" in line
    assert f"TORTOISE_EMBED_GATE={'ON' if embed_gate.embed_gate_enabled() else 'OFF'}" in line
    assert "unset → code default ON" in line and "unset → code default OFF" in line

    os.environ["TORTOISE_EMBED_GATE"] = "1"
    os.environ["TORTOISE_VALUE_GATE"] = "0"
    line = gate_visibility.startup_line()
    assert "TORTOISE_VALUE_GATE=OFF (stated '0')" in line
    assert "TORTOISE_EMBED_GATE=ON (stated '1')" in line


def test_the_startup_line_tracks_the_resolvers(monkeypatch):
    """A log that disagrees with the behaviour is worse than no log at all."""
    monkeypatch.setenv("TORTOISE_EMBED_GATE", "yes")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "off")
    reported = {row["switch"]: row for row in gate_visibility.effective_switches()}
    assert reported["TORTOISE_EMBED_GATE"]["effective"] is embed_gate.embed_gate_enabled() is True
    assert reported["TORTOISE_VALUE_GATE"]["effective"] is value_gate.value_gate_enabled() is False
    assert reported["TORTOISE_EMBED_GATE"]["source"] == "environment"


@pytest.mark.parametrize("module", ["tortoise/selfhost.py", "tortoise/hosted_api.py"])
def test_both_daemons_report_at_boot(module):
    """The line exists AND a daemon calls it — silence is what makes the OFF direction dangerous."""
    source = (REPO_ROOT / module).read_text(encoding="utf-8")
    assert "report_effective_switches" in source, f"{module} never reports the two switches"
    assert "report_effective_switches()" in source or "_report_gates()" in source, (
        f"{module} imports the report but never fires it"
    )


def test_the_pre_merge_check_runs_in_ci():
    """A check nobody runs is the defect this issue is about, one level up."""
    workflow = (REPO_ROOT / ".github/workflows/python-ci.yml").read_text(encoding="utf-8")
    assert "tools/embed_switch_gate.py" in workflow, (
        "the pre-merge check is not wired into python-ci.yml — nothing refuses an omission"
    )
    compose_step = workflow.index("docker compose config -q")
    assert workflow.index("tools/embed_switch_gate.py") > compose_step, (
        "the gate belongs beside the compose validation it extends, not in some other job"
    )


# ── this repository, resolved ───────────────────────────────────────────────────────────────


@needs_docker
def test_the_real_tree_states_both_switches():
    """The repository this PR ships. Also a POPULATION pin: a channel appearing or vanishing is
    a visible change, not a silent one (the check discovers it either way)."""
    channels, failures = gate.evaluate(REPO_ROOT)
    assert failures == [], failures
    checked = {channel.channel for channel in channels if not channel.noted}
    assert checked == {
        "docker-compose.yml: tortoise",
        "fly.toml",
        "Dockerfile.hosted",
        "Dockerfile.selfhost",
        "entrypoint.sh",
    }, checked
    for channel in channels:
        if channel.noted:
            continue
        for switch in SWITCHES:
            assert channel.stated.get(switch, "").strip(), (channel.channel, switch)
