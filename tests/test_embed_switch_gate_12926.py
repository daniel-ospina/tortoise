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
| `test_a_function_header_spelling_with_parentheses_is_still_a_BODY` | a never-running helper body is not credited | ANY header spelling (`function NAME`, `function NAME()`, `NAME()`) stops remembering its Allman body |
| `test_a_FORKED_group_body_is_never_credited` | a subshell cannot state a value | a group that is piped or backgrounded (single- and multi-line) is credited, or an unforked group stops being |
| `test_a_group_closer_inside_a_BODY_does_not_end_that_body` | a `}`'s owner decides what it closes | a group's closer inside a function or control-flow body spends the BODY's depth, so its later siblings read as top level and are credited |
| `test_a_redefined_stage_name_resolves_to_the_MOST_RECENT_stage` | the image channel's stage model agrees with Docker | a repeated `AS <name>` is resolved to the FIRST definition, so a dead stage's `ENV` grades the image |
| `test_a_trailing_guard_operator_is_consumed_by_the_next_statement` | a guard applies to the next statement, not the rest of the file | `carried` leaks past the statement it guards, or stops guarding across a blank line |
"""

from __future__ import annotations

import logging
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
#: The names a daemon may call the boot report by: the function itself, or the alias each daemon
#: imports it as. `tortoise/hosted_api.py` imports it AS `_report_gates` (`#12926` Visibility), so
#: a pin that only knows the original name would call a live report "missing".
BOOT_REPORTERS = ("report_effective_switches", "_report_gates")
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


#: The behaviour, written out INDEPENDENTLY of the implementation (review P3: comparing
#: `resolve(env.get(flag))` with `*_gate_enabled()` is vacuous when the latter is DEFINED as the
#: former — no spelling could make it fail). These are the two opposite directions the issue is
#: about, over the whole truthy contract: absent ⇒ value=ON / embed=OFF, and a blank or falsy
#: statement ⇒ OFF for both, while garbage reads OFF rather than flipping a default-ON knob.
_EXPECTED = {
    # raw value: (value_gate expected, embed_gate expected)
    None: (True, False),
    "": (False, False),
    "   ": (False, False),
    "1": (True, True),
    "true": (True, True),
    "TRUE": (True, True),
    "yes": (True, True),
    "on": (True, True),
    "0": (False, False),
    "false": (False, False),
    "no": (False, False),
    "off": (False, False),
    "garbage": (False, False),
}


@pytest.mark.parametrize("raw", list(_EXPECTED))
def test_the_two_switches_resolve_as_declared_in_both_directions(raw, monkeypatch):
    """The table above, against each gate's OWN read AND against the startup line's report.

    Both arms are load-bearing: the first pins the behaviour (so "unset still means
    value=ON / embed=OFF" cannot drift), the second pins that the *line an operator reads* states
    the same value the gate acts on — a log that disagrees with the behaviour is worse than none.
    """
    expected_value, expected_embed = _EXPECTED[raw]
    for env_flag, resolve, enabled, expected in (
        (value_gate.ENV_FLAG, value_gate.resolve_enabled, value_gate.value_gate_enabled, expected_value),
        (embed_gate.ENV_FLAG, embed_gate.resolve_enabled, embed_gate.embed_gate_enabled, expected_embed),
    ):
        if raw is None:
            monkeypatch.delenv(env_flag, raising=False)
        else:
            monkeypatch.setenv(env_flag, raw)
        assert enabled() is expected, (env_flag, raw)
        assert resolve(raw) is expected, (env_flag, raw, "the one default site")

    line = gate_visibility.startup_line()
    assert f"TORTOISE_VALUE_GATE={'ON' if expected_value else 'OFF'}" in line, (raw, line)
    assert f"TORTOISE_EMBED_GATE={'ON' if expected_embed else 'OFF'}" in line, (raw, line)


def test_a_blank_statement_is_reported_as_blank_not_as_unset(monkeypatch):
    """Review P3: a blank variable is a DISTINCT fact, and it is not the code default.

    `TORTOISE_VALUE_GATE=""` reads **OFF** (the truthy contract), while an ABSENT one reads the
    default **ON** — so rendering both as "unset → code default ON" would print a value that is
    not the one in force.
    """
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "")
    monkeypatch.delenv("TORTOISE_EMBED_GATE", raising=False)
    line = gate_visibility.startup_line()
    assert "TORTOISE_VALUE_GATE=OFF (stated '' — blank reads as OFF, not as the unset default)" in line
    assert "TORTOISE_EMBED_GATE=OFF (unset → code default OFF)" in line
    assert "code default ON" not in line

    reported = {row["switch"]: row for row in gate_visibility.effective_switches()}
    assert reported["TORTOISE_VALUE_GATE"]["source"] == "blank"
    assert reported["TORTOISE_VALUE_GATE"]["code_default"] is True


def test_the_switch_names_are_the_resolvers_own():
    """The check's names are the gate modules' `ENV_FLAG`s — never a second spelling."""
    assert gate.SWITCHES == (value_gate.ENV_FLAG, embed_gate.ENV_FLAG)
    assert set(gate.SWITCHES) == set(SWITCHES)
    assert [switch for switch, _resolve in gate_visibility.SWITCHES] == list(gate.SWITCHES)
    for switch, resolve in gate_visibility.SWITCHES:
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


@needs_docker
def test_a_population_of_only_non_channels_is_not_a_pass(tmp_path):
    """Review P3: the other shape of "nothing compared" — manifests were found, none is a channel.

    A compose file left with only sidecar services (or whose Tortoise service lost every
    `TORTOISE_*` key, which is what makes it a channel) must not report `OK … (0 checked)`.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  db:\n    image: falkordb/falkordb-server:v4.20.4\n"
    )
    channels, failures = gate.evaluate(tmp_path)
    assert channels and all(channel.noted for channel in channels)
    assert failures == [
        "every discovered manifest was skipped as a non-channel, so no deployment channel was "
        "checked — 'nothing compared' is not a pass"
    ]


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
def test_a_service_behind_a_profile_is_a_channel_not_invisible(tmp_path):
    """Review P2 at `543a1b0467` — Compose drops `profiles:`-gated services **silently**.

    Measured on Compose 2.40.3: `docker compose config --format json` over an armed `app` beside
    a `profiles: ["prod"]` Tortoise `worker` returns a model containing ONLY `app` — no stderr,
    no warnings, rc 0 — so the gate printed OK over a channel it never saw. Silently absent is
    the “discovered channel passes quietly” fail-open the module docstring forbids, and a
    profiled service is one deploy flag away from running. The resolver therefore enables every
    profile, and this leg reds the hour that flag is dropped.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    environment:\n"
        '      TORTOISE_VALUE_GATE: "1"\n'
        '      TORTOISE_EMBED_GATE: "1"\n'
        "  worker:\n"
        "    build: .\n"
        '    profiles: ["prod"]\n'
        "    environment:\n"
        '      TORTOISE_DB_URI: "bolt://db:6379"\n'
    )
    channels, failures = gate.evaluate(tmp_path)
    labels = [channel.channel for channel in channels]
    assert "docker-compose.yml: worker" in labels, (
        "a service behind `profiles:` is still a deployment channel — Compose enables the profile "
        f"and runs the service — so the resolver must ask for every profile: {labels}"
    )
    assert any("worker" in failure and "does not state" in failure for failure in failures), (
        f"the profiled service omits both switches and must be REFUSED by name: {failures}"
    )


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
    assert "no evidence this service runs Tortoise" in noted[0].noted
    assert "never reads either switch" not in noted[0].noted, (
        "the skip line must claim only what was measured — 'no evidence it runs Tortoise' — and "
        "never assert what the process does or does not read"
    )

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
def test_a_pulled_service_that_runs_OUR_image_is_a_channel_not_a_sidecar(tmp_path):
    """Review round 4, P2 — the escape the first rule left open.

    A service with NO `build:` and NO `TORTOISE_*` key that pulls one of THIS TREE'S OWN images
    boots the same daemon and reads the same two code defaults (EMBED = OFF), so it must state
    them. Measured before the fix: `skipped … never reads either switch` next to an armed
    `build:` sibling, then `OK … (1 checked)`, rc 0.
    """
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    image: ghcr.io/daniel-ospina/tortoise-selfhost:local\n"
        "    environment:\n"
        '      TORTOISE_VALUE_GATE: "1"\n'
        '      TORTOISE_EMBED_GATE: "1"\n'
        "  pulled:\n"
        "    image: ghcr.io/daniel-ospina/tortoise-selfhost:latest\n"
    )
    channels, failures = gate.evaluate(tmp_path)
    pulled = [channel for channel in channels if channel.channel.endswith("pulled")]
    assert pulled and not pulled[0].noted, [c.as_dict() for c in channels]
    assert any("pulled does not state TORTOISE_EMBED_GATE" in failure for failure in failures), failures

    # The same shape in a NAMESPACE this tree does not build from: still ours by the image name,
    # which is the conservative side — a false positive only asks an image to state the switches.
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n  pulled:\n    image: ghcr.io/some-fork/tortoise-selfhost:1.2\n"
    )
    channels, failures = gate.evaluate(tmp_path)
    assert channels and not channels[0].noted
    assert failures, "a pulled Tortoise image must not pass as a sidecar"

    # ⛔ And the built-image arm ALONE, on a name that does not contain "tortoise" — otherwise the
    # name heuristic covers for it and the arm can be deleted with this test still green
    # (measured: mutant M5 survived until this case existed).
    (tmp_path / "docker-compose.yml").write_text(
        "services:\n"
        "  app:\n"
        "    build: .\n"
        "    image: ghcr.io/acme/storefront:v1\n"
        "    environment:\n"
        '      TORTOISE_VALUE_GATE: "1"\n'
        '      TORTOISE_EMBED_GATE: "1"\n'
        "  staged:\n"
        "    image: ghcr.io/acme/storefront:latest\n"
    )
    channels, failures = gate.evaluate(tmp_path)
    staged = [channel for channel in channels if channel.channel.endswith("staged")]
    assert staged and not staged[0].noted, [c.as_dict() for c in channels]
    assert any("staged does not state TORTOISE_EMBED_GATE" in f for f in failures), failures


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
    stated, ambiguous, shadowed = gate._dockerfile_env(
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
    assert shadowed == {}, "a single-stage file has no builder stage to shadow anything"
    stated, _, _ = gate._dockerfile_env("ENV TORTOISE_EMBED_GATE 0\n")
    assert stated == {"TORTOISE_EMBED_GATE": "0"}

    # a shape that MENTIONS the switch but states no value this reader reads → refused
    stated, ambiguous, _ = gate._dockerfile_env("ENV TORTOISE_VALUE_GATE\n")
    assert stated == {} and ambiguous == {"TORTOISE_VALUE_GATE"}
    channel = gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV", stated=stated,
                           ambiguous=tuple(sorted(ambiguous)))
    assert "mentions TORTOISE_VALUE_GATE but does not state a value this gate can read" in (
        channel.problems()[0]
    )


def test_a_prose_mention_does_not_override_a_readable_assignment():
    """A file that STATES the switch and also names it in a log line is fine.

    The refusal for a mention exists for the opposite case — a shape this reader does not model,
    where a silent "not stated" would be indistinguishable from a genuine omission. Precedence
    matters: reporting the mention over a working assignment would red a correct channel.
    """
    stated, ambiguous = gate._shell_assignments(
        'export TORTOISE_VALUE_GATE="${TORTOISE_VALUE_GATE:-1}"\n'
        'export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"\n'
        'echo "TORTOISE_EMBED_GATE is off"\n'
    )
    assert stated == {
        "TORTOISE_VALUE_GATE": "${TORTOISE_VALUE_GATE:-1}",
        "TORTOISE_EMBED_GATE": "${TORTOISE_EMBED_GATE:-1}",
    }
    assert ambiguous == {"TORTOISE_EMBED_GATE"}
    channel = gate.Channel("entrypoint.sh", "entrypoint", "entrypoint assignments",
                           stated=stated, ambiguous=tuple(sorted(ambiguous)))
    assert channel.problems() == [], channel.problems()


def test_an_interpolation_with_no_literal_fallback_is_refused():
    """Review P3: `ENV K=$UNSET` / `export K="${K:-}"` can resolve EMPTY, so it states nothing.

    Neither reader resolves interpolation — there is no environment it could resolve against that
    would be the SHIPPED value — so a pure interpolation survives only if it carries a non-empty
    literal fallback. `entrypoint.sh`'s own `${K:-1}` is exactly that shape.
    """
    for text, reader in (
        ("ENV TORTOISE_EMBED_GATE=$UNSET_VAR\n", gate._dockerfile_env),
        ('export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-}"\n', gate._shell_assignments),
        ("ENV TORTOISE_EMBED_GATE ${TORTOISE_EMBED_GATE:-}\n", gate._dockerfile_env),
    ):
        stated, ambiguous, *rest = reader(text)
        shadowed = rest[0] if rest else {}
        assert stated == {}, (text, stated)
        assert shadowed == {}, (text, shadowed)
        assert ambiguous == {"TORTOISE_EMBED_GATE"}, (text, ambiguous)
        channel = gate.Channel("entrypoint.sh", "entrypoint", "r", stated=stated,
                               ambiguous=tuple(sorted(ambiguous)))
        problems = channel.problems()
        assert any("does not state a value this gate can read" in p for p in problems), problems

    # ⛔ `${X:+1}` / `${X+1}` are NOT a fallback: they expand to EMPTY exactly when X is unset —
    # which is the shipped case (review round 6, P3).
    for shape in ('"${TORTOISE_EMBED_GATE:+1}"', '"${TORTOISE_EMBED_GATE+1}"'):
        stated, ambiguous = gate._shell_assignments(f"export TORTOISE_EMBED_GATE={shape}\n")
        assert stated == {} and ambiguous == {"TORTOISE_EMBED_GATE"}, shape

    # a non-empty literal fallback IS a statement
    stated, ambiguous = gate._shell_assignments('export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"\n')
    assert stated == {"TORTOISE_EMBED_GATE": "${TORTOISE_EMBED_GATE:-1}"} and ambiguous == set()


def test_a_non_scalar_fly_value_is_refused_rather_than_stringified():
    """Review P3: the Fly arm's `ambiguous` set was dead by construction; a non-scalar value is
    the shape it was reaching for — a TOML table states no string the gate can read."""
    env = {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": {"nested": "1"}}
    stated, ambiguous = gate._env_table_values(env)
    assert stated == {"TORTOISE_VALUE_GATE": "1"}
    assert ambiguous == {"TORTOISE_EMBED_GATE"}
    # booleans are normalised, not repr()d as `True`
    stated, ambiguous = gate._env_table_values({"TORTOISE_EMBED_GATE": True})
    assert stated == {"TORTOISE_EMBED_GATE": "true"} and ambiguous == set()
    # `null` states no value at all → the EMPTY arm refuses it
    stated, _ = gate._env_table_values({"TORTOISE_EMBED_GATE": None})
    assert stated == {} and gate.Channel("fly.toml", "fly", "r", stated={}).problems()[0].endswith(
        "(r)"
    )


def test_a_trailing_comment_is_not_part_of_the_value():
    """Review round 6, P3: `export K=""  # off` must still hit the EMPTY-value refusal, and a
    switch named only in a trailing comment must not be credited.

    Both readers strip a quote-aware trailing comment. In the SHELL that is right (`export K=1  # x`
    ships `1`), and the dangerous shape there is a switch named only in the trailing text. In a
    Dockerfile the `K=V` form has no trailing comment at all — Docker refuses the line — so the
    reader must not strip it; the LEGACY `ENV K V` form is the one place Docker folds the text into
    the value, and this reader reports that folded value rather than a tidied one.
    """
    stated, ambiguous = gate._shell_assignments('export TORTOISE_VALUE_GATE=""  # off for now\n')
    assert stated == {"TORTOISE_VALUE_GATE": ""} and ambiguous == set()
    channel = gate.Channel("entrypoint.sh", "entrypoint", "r",
                           stated={"TORTOISE_VALUE_GATE": "", "TORTOISE_EMBED_GATE": "1"})
    assert "EMPTY value" in channel.problems()[0]

    # ⛔ A Dockerfile has NO inline-comment form: Docker REFUSES `ENV K=1  # x` outright
    # (`Syntax error - can't find = in "#"`, measured on Docker 29.1.3), so such a line can never
    # ship. The reader therefore must not strip the comment and then report the value as STATED —
    # the line is refused instead, which is the fail-closed direction (review at `3e2b944afd`).
    stated, ambiguous, _ = gate._dockerfile_env("ENV TORTOISE_EMBED_GATE=1  # on\n")
    assert stated == {}, stated
    assert "TORTOISE_EMBED_GATE" in ambiguous, ambiguous
    # A switch named only in that trailing text is not credited either.
    stated, ambiguous, _ = gate._dockerfile_env("ENV HF_HUB_OFFLINE=1  # TORTOISE_EMBED_GATE=1\n")
    assert stated == {}, stated
    assert "TORTOISE_EMBED_GATE" in ambiguous, ambiguous
    # A whole-line comment is still just a comment (Docker ignores it), not a mention.
    stated, ambiguous, _ = gate._dockerfile_env("# ENV TORTOISE_EMBED_GATE=1\nENV TORTOISE_VALUE_GATE=1\n")
    assert stated == {"TORTOISE_VALUE_GATE": "1"} and "TORTOISE_EMBED_GATE" not in ambiguous


@pytest.mark.parametrize(
    "body",
    [
        "setup() {\n  export TORTOISE_VALUE_GATE=1\n}\n",
        "if false; then\n  export TORTOISE_VALUE_GATE=1\nfi\n",
        # ⛔ the MULTI-LINE form, and an INDENTED `if`: treating `then` as a closer dropped the
        # depth so the body read as top level (review at `69ee7b1395`, measured).
        "if false\n then\n  export TORTOISE_VALUE_GATE=1\nfi\n",
        "  if false; then\n    export TORTOISE_VALUE_GATE=1\n  fi\n",
        "exit 0\nexport TORTOISE_VALUE_GATE=1\n",
        "for f in a b; do\n  export TORTOISE_VALUE_GATE=1\ndone\n",
        # ⛔ the ONE-LINE function body (review at `6a221f9053`, measured): the `{` is
        # mid-statement, so it never opened and the body ran at depth 0 — `ok`, exit 0, over a
        # process that receives neither switch (`env -i … bash script /usr/bin/env` printed
        # neither).
        "arm() { :; export TORTOISE_VALUE_GATE=1; }\n",
        "arm() { export TORTOISE_VALUE_GATE=1; }\n",
        # ⛔ the right-hand side of `&&`/`||` (review at `6a221f9053`, measured): split into its
        # own statement with nothing recording WHY, so a guard-then-arm line was credited STATED.
        '[ -n "$MAYBE_FROM_RUNTIME" ] && export TORTOISE_VALUE_GATE=1\n',
        '[ -z "$MAYBE_FROM_RUNTIME" ] || export TORTOISE_VALUE_GATE=1\n',
        # ⛔ a FORKED statement (review at `6a221f9053`, measured): a background `&` and a
        # pipeline stage both run in a SUBSHELL, so the export never reaches the process.
        "export TORTOISE_VALUE_GATE=1 &\n",
        "export TORTOISE_VALUE_GATE=1 | tee /tmp/log\n",
    ],
)
def test_an_assignment_the_process_never_receives_is_refused(body):
    """Review at `8aa35e5d56`, P3 — the residual recorded in the docstring, now CLOSED.

    A function body, a branch, a loop body and anything after a top-level `exit`/`exec` are
    assignments the process may never receive, so none may be credited as a statement the shipped
    configuration makes. Each lands on the fail-closed `ambiguous` verdict instead — and a
    top-level assignment in the SAME file still counts.
    """
    stated, ambiguous = gate._shell_assignments(body)
    assert stated == {}, (body, stated)
    assert ambiguous == {"TORTOISE_VALUE_GATE"}, (body, ambiguous)
    stated, _ = gate._shell_assignments("export TORTOISE_EMBED_GATE=1\n" + body)
    assert stated == {"TORTOISE_EMBED_GATE": "1"}


def test_a_later_unset_or_bare_assignment_supersedes_an_earlier_export():
    """Review at `69ee7b1395`, P2: last-assignment-wins, in the direction that matters.

    `export K=1` then `unset K` (or `K=`) leaves the process with NO value, but a reader that kept
    the literal reported STATED — and `stated` outranks `ambiguous`, so the stale literal won.
    """
    for tail in ("unset TORTOISE_EMBED_GATE\n", "TORTOISE_EMBED_GATE=\n"):
        stated, ambiguous = gate._shell_assignments("export TORTOISE_EMBED_GATE=1\n" + tail)
        assert stated == {}, (tail, stated)
        assert ambiguous == {"TORTOISE_EMBED_GATE"}, (tail, ambiguous)


def test_the_shell_reader_requires_export():
    """Review P2 at `648842c31a` — a bare assignment never reaches the process.

    `entrypoint.sh` ends in `exec "$@"` (:1151), so a shell variable that is not EXPORTED is not
    inherited by uvicorn. Crediting the bare form would report `ok entrypoint.sh
    TORTOISE_EMBED_GATE=1` for a value the image never receives — the false PASS this gate exists
    to prevent — so only an `export`ed assignment is a statement. Measured both ways: with the
    optional `export` group, the bare line read `stated={'TORTOISE_EMBED_GATE': '1'}`.
    """
    stated, ambiguous = gate._shell_assignments("TORTOISE_EMBED_GATE=1\n")
    assert stated == {}, (
        "a bare `K=V` is not a statement an entrypoint can make to its process — crediting it "
        f"reports an arm the process never receives: {stated}"
    )
    assert ambiguous == {"TORTOISE_EMBED_GATE"}, (
        "a bare assignment is SEEN but not creditable, so it must be reported as ambiguous rather "
        f"than silently ignored: {ambiguous}"
    )

    # The channel-level consequence follows: a file whose only mention is bare does not STATE the
    # switch, so the gate refuses it rather than OK-ing it.
    channel = gate.Channel("entrypoint.sh", "entrypoint", "shell assignments", stated=stated,
                           ambiguous=ambiguous)
    assert any("does not state" in problem for problem in channel.problems()), channel.problems()

    stated, _ = gate._shell_assignments("export TORTOISE_EMBED_GATE=1\n")
    assert stated == {"TORTOISE_EMBED_GATE": "1"}, stated


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


def test_a_later_ambiguous_assignment_supersedes_an_earlier_literal_dockerfile():
    """Review P2: last assignment wins, so `ENV K=1` then `ENV K=${X}` arms NOTHING.

    Reporting the stale `1` is a fail-OPEN: the image resolves the switch empty (measured with
    real Docker) while the gate says STATED. `switch_problem` gives `stated` precedence over
    `ambiguous`, so the earlier literal has to be dropped rather than merely flagged.
    """
    stated, ambiguous, _shadowed = gate._dockerfile_env(
        "FROM python:3.12\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=${EMBED_FROM_BUILD_ARG}\n"
    )
    assert "TORTOISE_EMBED_GATE" not in stated, (
        f"the superseded literal survived: {stated} — the gate would report STATED for a value "
        "that resolves empty"
    )
    assert "TORTOISE_EMBED_GATE" in ambiguous
    channel = gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV",
                           stated=stated, ambiguous=ambiguous)
    problems = channel.problems()
    assert len(problems) == 1 and "does not state a value this gate can read" in problems[0], problems

    # ...and the reverse order stays STATED: a literal AFTER the ambiguous line is the value
    # Docker actually resolves, so it must not be thrown away.
    stated, ambiguous, _shadowed = gate._dockerfile_env(
        "FROM python:3.12\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=${EMBED_FROM_BUILD_ARG}\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV",
                        stated=stated, ambiguous=ambiguous).problems() == []


def test_a_later_ambiguous_assignment_supersedes_an_earlier_literal_entrypoint():
    """The same last-wins rule in the shell reader — `export K=1` then `export K=$X`."""
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'export TORTOISE_EMBED_GATE=1\n'
        'export TORTOISE_EMBED_GATE="${EMBED_FROM_CALLER}"\n'
    )
    assert "TORTOISE_EMBED_GATE" not in stated, f"the superseded literal survived: {stated}"
    assert "TORTOISE_EMBED_GATE" in ambiguous

    # a literal after the ambiguous line is the effective value again
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'export TORTOISE_EMBED_GATE="${EMBED_FROM_CALLER}"\n'
        'export TORTOISE_EMBED_GATE=1\n'
    )
    assert stated == {"TORTOISE_EMBED_GATE": "1"}, stated


def test_only_the_final_build_stage_states_a_switch():
    """Review P2: `ENV` is STAGE-SCOPED, so a builder-stage assignment arms nothing that ships.

    Crediting it would read a two-stage Dockerfile as STATED while the runtime image says
    nothing — an unmodelled shape failing OPEN, which this gate's whole doctrine forbids.
    """
    both = (
        "FROM python:3.12 AS build\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
        "FROM python:3.12-slim\n"
        "CMD [\"python\", \"-c\", \"print(1)\"]\n"
    )
    stated, ambiguous, _shadowed = gate._dockerfile_env(both)
    assert stated == {}, (
        f"a builder-stage ENV was credited to the final image: {stated} — neither switch survives "
        "into the image that ships"
    )
    assert gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV",
                        stated=stated, ambiguous=ambiguous).problems()

    # the final stage stating them IS the image stating them
    final = (
        "FROM python:3.12 AS build\n"
        "ENV TORTOISE_VALUE_GATE=0\n"
        "FROM python:3.12-slim\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
    )
    stated, ambiguous, _shadowed = gate._dockerfile_env(final)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert gate.Channel("Dockerfile", "dockerfile", "Dockerfile ENV",
                        stated=stated, ambiguous=ambiguous).problems() == []

    # a single-stage file is read whole (no boundary to honour)
    stated, _, _ = gate._dockerfile_env(
        "FROM python:3.12\nENV TORTOISE_VALUE_GATE=1\nENV TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated


def test_the_dockerfile_reader_credits_only_the_final_stage():
    """Review P2 at `648842c31a` — the reader had no build-stage model (measured both ways).

    A ``Dockerfile`` is a SEQUENCE of images and only the last one ships, so
    ``FROM … AS builder`` + ``ENV TORTOISE_EMBED_GATE=1`` + ``FROM runtime`` used to read as
    ``stated`` — a false PASS one ``FROM … AS`` away from any real deployment. It must be refused
    and must NAME the stage (a silent drop would read exactly like a genuine omission). The
    mirror is just as load-bearing: a final stage that DOES inherit (``FROM builder``) must still
    be credited, or the gate would refuse a correct file.
    """
    stated, ambiguous, shadowed = gate._dockerfile_env(
        "FROM python:3.12-slim AS builder\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        "RUN pip wheel .\n"
        "FROM python:3.12-slim\n"
        "COPY --from=builder /wheels /wheels\n"
    )
    assert stated == {}, f"a builder-stage ENV never reaches the shipped image: {stated}"
    assert shadowed == {
        "TORTOISE_VALUE_GATE": "builder",
        "TORTOISE_EMBED_GATE": "builder",
    }, shadowed
    channel = gate.Channel("Dockerfile.hosted", "dockerfile", "Dockerfile ENV", stated=stated,
                           ambiguous=tuple(sorted(ambiguous)), shadowed=shadowed)
    problems = channel.problems()
    assert len(problems) == len(SWITCHES), problems
    for problem in problems:
        assert "only in build stage 'builder'" in problem, problem
        assert "does not inherit" in problem, problem

    # ⛔ the INHERITED direction: a final stage built FROM the stage that states them is armed.
    for final in ("FROM base AS final\n", "FROM base\n"):
        stated, ambiguous, shadowed = gate._dockerfile_env(
            "FROM python:3.12-slim AS base\n"
            "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n" + final
        )
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (final, stated)
        assert shadowed == {} and ambiguous == set(), (final, shadowed, ambiguous)

    # Docker matches a stage name case-insensitively (measured: `FROM scratch AS Builder` +
    # `FROM builder` builds). A stage INDEX is not a `FROM` form — `FROM 0` asks for the image
    # `docker.io/library/0` and buildkit refuses it — so no numeric arm is modelled or tested.
    stated, _, _ = gate._dockerfile_env(
        "FROM x AS Builder\nENV TORTOISE_EMBED_GATE=1\nFROM builder\n"
    )
    assert stated == {"TORTOISE_EMBED_GATE": "1"}, stated

    # ⚠️ precedence, as everywhere else: the SHIPPED stage's own statement wins over a sibling
    # builder's differing one, and no `shadowed` verdict survives.
    stated, _, shadowed = gate._dockerfile_env(
        "FROM x AS docs\nENV TORTOISE_EMBED_GATE=0\nFROM y\nENV TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_EMBED_GATE": "1"} and shadowed == {}, (stated, shadowed)

    # an unrelated `ENV` in the builder stage is not a switch and is not reported
    _, ambiguous, shadowed = gate._dockerfile_env("FROM x AS b\nENV HF_HUB_OFFLINE=1\nFROM y\n")
    assert shadowed == {} and ambiguous == set()


def test_a_heredoc_body_is_data_not_instructions():
    """Review P2 at `648842c31a` — a heredoc body must not be read as Dockerfile instructions.

    `RUN cat <<EOF` is the documented way to write a multi-line script, so a body line that starts
    with `FROM` is ordinary text. Read as an instruction it opened a phantom stage and made the
    REAL shipped stage unreadable — measured: `FROM x` + `ENV K=1` + `RUN cat <<EOF` + `FROM
    something` + `EOF` was REFUSED, naming a stage that does not exist. The same body can carry an
    `ENV K=1` line that the image never receives; crediting that would be the mirror false PASS.
    """
    text = (
        "FROM x\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
        "RUN cat <<EOF > /tmp/notes\n"
        "FROM something-else\n"
        "ENV TORTOISE_VALUE_GATE=0\n"
        "EOF\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
    )
    stated, ambiguous, shadowed = gate._dockerfile_env(text)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert shadowed == {} and ambiguous == set(), (shadowed, ambiguous)

    # the BODY's only arm is not credited: the shipped stage states the other switch alone
    stated, _, _ = gate._dockerfile_env(
        "FROM x\nENV TORTOISE_EMBED_GATE=1\nRUN <<EOF\nENV TORTOISE_VALUE_GATE=1\nEOF\n"
    )
    assert stated == {"TORTOISE_EMBED_GATE": "1"}, stated

    # `<<-` strips leading TABS from its terminator; a quoted word is the same marker
    for opener, terminator in (("<<-EOF", "\tEOF"), ("<<'EOF'", "EOF"), ('<<"EOF"', "EOF")):
        stated, _, shadowed = gate._dockerfile_env(
            f"FROM x\nENV TORTOISE_EMBED_GATE=1\nRUN {opener}\nFROM y\n{terminator}\n"
        )
        assert stated == {"TORTOISE_EMBED_GATE": "1"}, (opener, stated)
        assert shadowed == {}, (opener, shadowed)

    # ⛔ an opener with NO terminator later in the file is NOT a heredoc: ignoring it must not
    # swallow the rest of the file (a quoted `<<` or a shell shift is not a body)
    stated, _, _ = gate._dockerfile_env(
        'FROM x\nENV TORTOISE_EMBED_GATE=1\nRUN echo "1 << 2"\nENV TORTOISE_VALUE_GATE=1\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated


def test_a_heredoc_body_is_not_SHELL_either(tmp_path):
    """Review P1 at `04dc33c1f3` — the shell reader had no heredoc model at all.

    The Dockerfile reader was given one (see the test above), but `_shell_assignments` still
    scanned the body as shell. A `fi`/`done`/`esac` at line start INSIDE the body then closed the
    ENCLOSING block, the depth leaked DOWN, and an `export` in a branch that never runs was
    credited `STATED`. Measured end-to-end: the gate printed `ok entrypoint.sh …` and exited 0
    over a process that receives neither switch.

    Not contrived: `entrypoint.sh:1124` already opens a `<<'PY'` body, and writing a config
    snippet from a heredoc is an ordinary entrypoint job.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'if [ -z "$X" ]; then\n'
        "cat > /tmp/x <<EOF\n"
        "fi\n"
        "EOF\n"
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "fi\n"
    )
    assert stated == {}, (
        f"an export inside an untaken branch was credited STATED because a `fi` in the heredoc "
        f"body closed the enclosing block: {stated}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous and "TORTOISE_EMBED_GATE" in ambiguous, ambiguous

    # ...and a heredoc's body does not swallow the statements AFTER it: the shipped shape
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "cat > /tmp/x <<'PY'\n"
        "print('hi')\n"
        "PY\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous

    # a here-string (`<<<`) is NOT a heredoc opener — it must not eat the rest of the file
    stated, _ = gate._shell_assignments(
        "#!/bin/sh\n"
        'cat <<< "$X"\n'
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated

    # ⛔ the TERMINATOR is compared the way the SHELL compares it (review P2). Bash strips leading
    # TABS only for `<<-` and compares the line EXACTLY for `<<`, so an indented `  EOF` under a
    # plain `<<` does NOT close the body: bash consumes the rest of the file and runs nothing.
    # `strip()` accepted the indented form, ending the body early and crediting exports the
    # process never reaches.
    stated, _ = gate._shell_assignments(
        "#!/bin/sh\n"
        "cat <<EOF\n"
        "    hello\n"
        "  EOF\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {}, (
        f"an INDENTED terminator closed a plain `<<` body early, so exports bash never reaches "
        f"(the heredoc runs to EOF) were credited: {stated}"
    )

    # ...and a TAB-indented terminator DOES close a `<<-` body, because the shell strips tabs there
    stated, _ = gate._shell_assignments(
        "#!/bin/sh\n"
        "cat <<-EOF\n"
        "\thello\n"
        "\tEOF\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated


def test_a_heredoc_delimiter_is_a_WORD_not_an_identifier():
    r"""Review P1 at `c858544e71` — the opener regex demanded `[A-Za-z_][A-Za-z0-9_]*`.

    bash's delimiter is a shell WORD, so `<<'END-OF-DATA'` (a hyphen), `<<E.O.F`, `<<'EO\F'` and
    `<<$'EOF'` are all openers — and none matched, so the opener was invisible, the BODY was read
    as SHELL, and a `fi` on the body's first line saturated the depth while the two body `export`s
    were credited `STATED` (exit 0). Measured end-to-end: `env -i … bash script /usr/bin/env`
    printed no `TORTOISE_*`, and the body was written to its file verbatim, proving bash took it
    as DATA. The terminator is compared against the UNQUOTED word, as bash does.
    """
    for label, delim, quoted in (
        ("a hyphenated delimiter", "END-OF-DATA", "'END-OF-DATA'"),
        ("a dotted delimiter", "E.O.F", "E.O.F"),
        ("a backslash-quoted delimiter", "EOF", "'\\EOF'"),
        ("an ANSI-C quoted delimiter", "EOF", "$'EOF'"),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/bash\n"
            f"cat > /tmp/notes.txt <<{quoted}\n"
            "fi\n"
            "export TORTOISE_VALUE_GATE=1\n"
            "export TORTOISE_EMBED_GATE=1\n"
            f"{delim}\n"
            'exec "$@"\n'
        )
        assert stated == {}, f"{label}: the body was read as shell: {stated}"

    # The terminator is the UNQUOTED word, so the body really does close there: a top-level export
    # AFTER it is a statement the process receives, and must still be credited.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\n"
        "cat > /tmp/notes.txt <<'END-OF-DATA'\n"
        "export TORTOISE_EMBED_GATE=1\n"
        "END-OF-DATA\n"
        "export TORTOISE_VALUE_GATE=1\n"
        'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous

    # ⛔ A `<<` this reader cannot model STOPS the read (fail CLOSED), rather than being credited
    # from lines that may sit inside a body nobody executes. A form the opener regex cannot match
    # is the only way to reach it, so this pins the direction with a shape it cannot model.
    stated, _ = gate._shell_assignments(
        "#!/bin/bash\n"
        "cat > /tmp/x <<\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {}, f"an unmodelled `<<` let the rest of the file be read: {stated}"


def test_a_separator_that_ends_a_LINE_still_makes_the_next_statement_conditional():
    """Review P1 at `7d356a5494` — the separator did not cross the line boundary.

    `_shell_statements` carries a separator only WITHIN one logical line, so the idiomatic
    multi-line guard handed each export a first-segment `separator == ""`:

        [ -n "${MAYBE_ARM:-}" ] &&
          export TORTOISE_VALUE_GATE=1 &&
          export TORTOISE_EMBED_GATE=1

    Both were credited `STATED` (gate `ok`, exit 0) while bash — `MAYBE_ARM` unset — ran no
    `export` at all and the process had neither switch. The verdict flipped on an invisible line
    break, which defeats the one-line pin the class already had.
    """
    for label, script in (
        (
            "a multi-line `&&` guard",
            '[ -n "${MAYBE_ARM:-}" ] &&\n  export TORTOISE_VALUE_GATE=1 &&\n  export TORTOISE_EMBED_GATE=1\n',
        ),
        ("a line-trailing `&&`", "false &&\n  export TORTOISE_VALUE_GATE=1\n"),
        ("a line-trailing `||`", "true ||\n  export TORTOISE_VALUE_GATE=1\n"),
    ):
        stated, ambiguous = gate._shell_assignments("#!/bin/bash\n" + script + 'exec "$@"\n')
        assert stated == {}, f"{label}: credited across a line boundary: {stated}"
        assert "TORTOISE_VALUE_GATE" in ambiguous, (label, ambiguous)

    # The other direction: a `;` that ends a line is an ordinary separator, not a guard.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\nexport TORTOISE_VALUE_GATE=1;\nexport TORTOISE_EMBED_GATE=1\nexec \"$@\"\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_a_control_word_before_a_compound_command_does_not_hide_the_block():
    """Review P2 at `7d356a5494` — `!`/`time` before `if` defeated the opener anchor.

    `! if false; then export K=1; fi` is valid shell (`bash -n` accepts it) and is the SAME
    never-running body the reader already refuses, but the anchor demanded the keyword immediately
    after the separator: the block never opened, its body read at depth 0, and both exports were
    credited (gate `ok`, exit 0, real process: no switch at all).
    """
    for label, prefix in (("a negation", "!"), ("the time builtin", "time")):
        stated, ambiguous = gate._shell_assignments(
            f"#!/bin/bash\n{prefix} if false; then\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\nexec \"$@\"\n"
        )
        assert stated == {}, f"{label} hid the block: {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)


def test_brace_EXPANSION_is_not_a_block_opener():
    """Review P3 at `7d356a5494` — the word-initial `{` arm over-reached onto brace expansion.

    `mkdir -p {logs,cache}` is brace EXPANSION: crediting its `{` as a block opener leaked the
    depth UP, so the honest literal `=1` exports below it were refused — with a message that
    blamed the VALUE instead of the depth. A block brace is a command word, i.e. followed by
    whitespace or end of statement, which is exactly what the lookahead now requires.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\nmkdir -p {logs,cache}\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
        'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous
    # …and the real one-line function body is still an opener (the arm this narrowed).
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\narm() { :; export TORTOISE_VALUE_GATE=1; }\nexec \"$@\"\n"
    )
    assert stated == {}, stated
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous


def test_an_env_invocation_that_strips_the_switch_retracts_it():
    """Review P3 at `7d356a5494` — `exec env -u K` / `env -i` is a retraction too.

    The reader modelled the shell BUILTINS (`unset`, `export -n`, a bare `K=`) and nothing else, so
    `export K=1; exec env -u K "$@"` was reported `STATED` while the process had no K at all — the
    same in-script retraction family, one layer out.
    """
    for label, tail in (
        ("`env -u`", 'exec env -u TORTOISE_EMBED_GATE "$@"'),
        ("`env -i`", 'exec env -i "$@"'),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/bash\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n" + tail + "\n"
        )
        assert "TORTOISE_EMBED_GATE" in ambiguous, (label, ambiguous, stated)
        assert "TORTOISE_EMBED_GATE" not in stated, (label, stated)
    # `env VAR=1 cmd` states nothing about the switches — it must not retract them.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        'exec env FOO=1 "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_a_heredoc_body_line_ending_in_a_BACKSLASH_does_not_eat_the_terminator():
    r"""Review P2 at `7d356a5494` — continuations were joined BEFORE heredoc bodies were removed.

    In a QUOTED heredoc bash does not continue a body line ending in `\`, so it ends the body at
    the terminator and runs the statements after it. Joining first swallowed the terminator line
    and everything below it, so an honest entrypoint whose two switches DO reach the process was
    refused — with a message that blamed the value. Fixed by removing bodies on PHYSICAL lines
    first, then joining continuations among the survivors.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\n"
        "cat > /tmp/n.txt <<'EOF'\n"
        "a literal backslash at end of line \\\n"
        "EOF\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
        'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_an_arithmetic_shift_is_not_a_heredoc():
    """Review P3: `<<` inside `$(( … ))` is a SHIFT, and it swallowed the rest of the file.

    `n=$(( 1 << shift ))` opened a phantom body named `shift`, so every later export was inside a
    body that never ends — the channel was refused for "not stating" both switches, a false block
    on an honest config.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "n=$(( 1 << shift ))\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"an arithmetic `<<` opened a phantom heredoc and swallowed the file: {stated}"
    )
    assert ambiguous == set(), ambiguous


def test_a_one_line_compound_statement_does_not_leak_depth_up():
    """Review P2 at `04dc33c1f3` — a closer after a statement separator is still a closer.

    `_SHELL_CLOSERS` was line-anchored while `_SHELL_OPENERS` matches at line start, so
    `if …; then …; fi` on ONE line opened a block and never closed it. The depth then leaked UP and
    every later top-level `export` was read as nested and REFUSED — a false block on an honest
    file. `entrypoint.sh` carries nine such one-liners (`:573 :723 :884 :933 :991 :993 :994 :1007`
    and the `for …; done` at `:809`), so this was one appended export away from a red.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'if [ -n "$X" ]; then echo hi; fi\n'
        "for f in a b; do echo $f; done\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a one-line compound statement left the depth open, so an honest top-level export was "
        f"refused as nested: {stated} / {ambiguous}"
    )
    assert ambiguous == set(), ambiguous

    # the multi-line form still nests: an export inside it is conditional, not a statement
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "if [ -n \"$X\" ]; then\n"
        "  export TORTOISE_VALUE_GATE=1\n"
        "fi\n"
    )
    assert stated == {}, stated
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous

    # ⛔ ...and a one-line compound statement INSIDE a block is net ZERO, not -1 (review P1
    # regression, measured). Matching its closer and `continue`-ing skipped the opener
    # accounting, so the depth dropped and the nested export AFTER it read as top level — the gate
    # printed `ok` and exited 0 over a helper that is never called. This module's own docstring
    # calls "move the export into the helper" an ordinary edit, so the shape is not contrived.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "helper() {\n"
        '  if [ -n "$X" ]; then echo hi; fi\n'
        "  export TORTOISE_VALUE_GATE=1\n"
        "}\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_EMBED_GATE": "1"}, (
        f"a one-line compound inside a function body leaked the depth down, crediting an export "
        f"the process never receives: {stated}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous


def test_a_closer_inside_a_quote_or_a_comment_is_DATA_not_a_closer():
    """Review P1 at `19afbd0865` — `_SHELL_CLOSERS` searched the RAW logical line.

    A `; fi` inside a quoted argument, or inside a trailing comment, therefore CLOSED the enclosing
    block. The depth then read 0 for a branch that never runs, and both `export`s inside it were
    credited ``STATED`` — so the gate printed `ok` and exited 0 over a process that receives
    neither switch. Measured at `19afbd0865` before the fix: both shapes below returned
    ``({'TORTOISE_VALUE_GATE': '1', 'TORTOISE_EMBED_GATE': '1'}, set())``.

    Same fail-open CLASS the heredoc fix closed (`test_a_heredoc_body_is_not_SHELL_either`),
    reached through quotes and comments instead of heredoc bodies — which is why the structural
    view this pins is applied to the closers and the openers alike, not to the assignment match.
    It includes the multi-line case: a quoted string that spans a NEWLINE hides its `fi` at
    column 0, so the quote state is carried ACROSS lines rather than reset per line.
    """
    for label, data_line in (
        ("a quoted argument", 'echo "n/a; fi"'),
        ("a trailing comment", "echo hi  # see; fi below"),
        ("a single-quoted argument", "echo 'n/a; done'"),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\n"
            "if false; then\n"
            f"  {data_line}\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\n"
        )
        assert stated == {}, (
            f"{label} closed the block, so a branch that never runs was credited STATED: "
            f"{stated}"
        )
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous

    # ⛔ And the shape that made this a per-SCRIPT verdict rather than a per-LINE one (measured):
    # a quoted string that SPANS A NEWLINE put a `fi` at column 0 — indistinguishable from a real
    # closer unless the open quote is carried across lines. Before the carry it returned
    # ``({'TORTOISE_VALUE_GATE': '1', 'TORTOISE_EMBED_GATE': '1'}, set())``, exit 0.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "if false; then\n"
        '  echo "n/a\n'
        'fi"\n'
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "fi\n"
    )
    assert stated == {}, f"a quote spanning a NEWLINE leaked a `fi`: {stated}"
    assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous

    # The MIRROR direction, which the same change must not break: a quoted `if`/`for` is data, so
    # it opens nothing — and the honest top-level exports after it are still statements.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'echo "if this; then that; fi"\n'
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a quoted `if`/`fi` leaked the depth and refused an honest file: {stated} / {ambiguous}"
    )
    assert ambiguous == set(), ambiguous

    # ⛔ And the OPENER half needs a witness that can actually fire (a reviewer measured the
    # previous one as vacuous: `echo "if …"` cannot match `^\s*(?:if|while|…)` even on the raw
    # line, so reverting the opener search to the raw line left the suite green). A multi-line
    # usage string whose continuation line STARTS with `if` can: reading it as an opener leaks the
    # depth and refuses an honest file.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'echo "usage:\n'
        "if you pass -h, then it prints help\n"
        '"\n'
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a quoted `if` at column 0 leaked the depth and refused an honest file: {stated} / {ambiguous}"
    )
    assert ambiguous == set(), ambiguous


def test_a_block_keyword_inside_an_UNEVALUATED_span_neither_opens_nor_closes():
    """Review at `19afbd0865`+ — a block keyword is DATA inside every span not evaluated.

    The quote-only structural view closed the quoted and commented shapes and left FOUR more
    reachable, each measured on a reviewer's probes: an unquoted command substitution
    (`LEVEL=$(if …; fi)`), a backtick substitution, a subshell (`( if …; fi )`) and an ANSI-C
    string (`$'don\\'t; fi'`). All four put a `; fi` into the structural view, dropped the depth,
    and credited both `export`s inside a branch that never runs — `ok`, exit 0 over a process
    that receives neither switch. A comment after a `;` (`set -x;# … fi`) was the same class one
    layer down: bash starts a comment at any WORD start, and the whitespace-only rule read it as
    code.

    A command substitution or subshell runs in its OWN shell, so its `if`/`fi` never belongs to
    the enclosing depth — which is why the span is BLANKED whole rather than keyword-skipped.
    """
    for label, data_line in (
        (
            "an unquoted command substitution",
            '  LEVEL=$(if [ "$D" = 2 ]; then echo high; else echo low; fi)',
        ),
        ("a backtick substitution", '  LEVEL=`if [ "$D" = 2 ]; then echo high; fi`'),
        ("a subshell", '  ( if [ "$D" = 1 ]; then echo hi; fi )'),
        ("an ANSI-C string", "  echo $'don\\'t; fi'"),
        ("a comment after a semicolon", "  set -x;# n/a; fi guard"),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\n"
            "if false; then\n"
            f"{data_line}\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\n"
        )
        assert stated == {}, f"{label} leaked a block keyword into the enclosing depth: {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)

    # A BACKSLASH-quoted heredoc delimiter (`<<\EOF`) is a valid spelling whose body is data; not
    # accepting it read the body as SHELL, so its `fi` closed the block (same class, measured).
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "if false; then\n"
        "  cat <<\\EOF\n"
        "fi is not a keyword here\n"
        "EOF\n"
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "fi\n"
    )
    assert stated == {}, f"a `<<\\EOF` body was read as shell: {stated}"
    assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous


def test_an_export_that_is_only_TEXT_is_never_credited():
    """Review at `19afbd0865`+ — the assignment match ran on the RAW line.

    An ``export TORTOISE_*_GATE=1`` that is merely the TEXT of a top-level multi-line string was
    credited ``STATED``: a script whose only "arm" is a line of help text inside ``echo "…"``
    printed ``ok`` with exit 0 over a process that receives neither switch. The value still comes
    from the raw line (its quotes must stay intact), so the guard is on the HEAD: `export KEY=`.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'echo "to enable embedding, export these yourself:\n'
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
        '"\n'
    )
    assert stated == {}, f"help TEXT was credited as a statement: {stated}"
    assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous

    # The honest spelling still credits, including a quoted value, whose quotes must survive the
    # structural view (the head is compared, not the value).
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\nexport TORTOISE_VALUE_GATE=\"1\"\nexport TORTOISE_EMBED_GATE='1'\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_an_escaped_quote_at_top_level_does_not_open_a_span():
    """Review P2 at `19afbd0865`+ — a FALSE REFUSAL this change must not introduce.

    ``echo \\"n/a\\"`` is a literal quote in bash, not a span delimiter. Modelling escapes only
    INSIDE a double-quoted span left this shape opening a span that never closed, which blanked
    the real ``fi`` and REFUSED an honest ``=1`` file that the previous head accepted — a refusal
    is a bug on this gate too, and it is the one direction a fail-closed bias hides.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "if false; then\n"
        '  echo \\"n/a\\"\n'
        "  echo hi\n"
        "fi\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"an escaped quote outside a span was read as a delimiter and refused an honest file: "
        f"{stated} / {ambiguous}"
    )
    assert ambiguous == set(), ambiguous


def test_a_NESTED_unset_neither_credits_nor_retracts():
    """Review P2 at `04dc33c1f3` — these two branches ignored the `conditional` guard.

    A helper's local `unset K` / bare `K=` retracted a TOP-LEVEL `export K=1` that the process
    does receive, so an honest config was refused. The test above pins the TOP-LEVEL direction
    (where last-wins really does retract); this one pins the nested direction, which must not.
    """
    for tail in (
        "helper() {\n  TORTOISE_EMBED_GATE=\n}\n",
        "helper() {\n  unset TORTOISE_EMBED_GATE\n}\n",
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n" + tail
        )
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (tail, stated)
        assert ambiguous == set(), (tail, ambiguous)


# ── the PROCESS VERDICT — the only thing CI consumes ────────────────────────────────────────


def test_the_process_exit_code_is_what_ci_consumes_and_it_refuses(tmp_path):
    """Review P1: nothing pinned `main()`/`render()`/`--json`.

    Every other test goes through `evaluate()`, so a mutant that made `main()` never refuse
    (`return 0`) left the whole suite green while CI went green over an unrefused omission —
    an INERT ENFORCER. CI reads the exit code, so the exit code is what this pins. Docker-free
    on purpose: an image-build channel is read by a pure-Python reader, so this arm runs on
    every lane rather than skipping with the docker-marked ones.
    """
    complete = tmp_path / "complete"
    complete.mkdir()
    _dockerfile(complete)
    assert gate.main(["--repo", str(complete)]) == 0, (
        "a tree stating both switches must exit 0, or the check reds every honest PR"
    )

    omitting = tmp_path / "omitting"
    omitting.mkdir()
    _dockerfile(omitting, embed=False)
    assert gate.main(["--repo", str(omitting)]) == 1, (
        "an omission must exit 1 — this is the ONLY signal CI consumes, so a 0 here is an "
        "inert enforcer however clearly the report printed the refusal"
    )

    # a path that is not a directory is a USAGE error, not a pass and not a refusal verdict
    assert gate.main(["--repo", str(tmp_path / "does-not-exist")]) == 2


def test_the_json_verdict_carries_the_failures(tmp_path, capsys):
    """`--json` is a consumer surface: it must carry the same verdict, not a prettier one."""
    import json

    omitting = tmp_path / "omitting"
    omitting.mkdir()
    _dockerfile(omitting, value=False)
    assert gate.main(["--repo", str(omitting), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["failures"], "--json emitted a green payload for a refused tree"
    assert any("TORTOISE_VALUE_GATE" in f for f in payload["failures"]), payload["failures"]
    assert payload["channels"], "--json emitted no channel rows"
    refused = [c for c in payload["channels"] if c.get("problems")]
    assert refused, payload["channels"]


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


def test_the_startup_record_survives_the_DEFAULT_root_level(caplog):
    """Review P3 at `ca57c755` (measured) — the doc promised aggregators a record; the code said INFO.

    `report_effective_switches` is documented (and follows `emit_off_box_backup_signal`) as
    "printed to stderr **and** logged so a container log aggregator gets it". The record was emitted
    at INFO, and nothing in `tortoise/` or `entrypoint.sh` sets a level for `tortoise.*` — so the
    root default (WARNING) dropped it, and the documented half of the pair did not exist. Measured:
    `_logger.isEnabledFor(logging.INFO) == False` on a default root.

    Pinned at the level the DEFAULT root keeps, so a future edit back to INFO reds this rather than
    silently re-emptying the aggregator arm. Mutation-proved: `.info` here reds it.
    """
    with caplog.at_level(logging.WARNING):
        line = gate_visibility.report_effective_switches()
    records = [r for r in caplog.records if line in r.getMessage()]
    assert records, "the boot record did not survive the default root level"
    assert all(r.levelno <= logging.WARNING for r in records), (
        f"a record the default root drops is not a record: {[r.levelname for r in records]}"
    )


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
    """The line exists AND a daemon CALLS it — silence is what makes the OFF direction dangerous.

    ⛔ Read from the AST, not from substrings (review P3 at `648842c31a`): the old check accepted
    any occurrence of ``report_effective_switches()`` in the file text, so COMMENTING THE CALL OUT
    — or deleting it and leaving a comment behind — satisfied the only wiring pin for the whole
    Visibility half, and the boot line would silently stop being printed. A ``Call`` node to the
    report (directly or through the ``_report_gates`` alias) is the fact this test is about;
    comments and strings carry no such node.
    """
    import ast

    source = (REPO_ROOT / module).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=module)

    def _reports_at_boot(statement: ast.stmt) -> bool:
        """Whether ``statement`` IS a bare top-level call to the report (directly or aliased)."""
        if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
            return False
        function = statement.value.func
        if isinstance(function, ast.Name):
            return function.id in BOOT_REPORTERS
        return isinstance(function, ast.Attribute) and function.attr in BOOT_REPORTERS

    assert any(_reports_at_boot(statement) for statement in tree.body), (
        f"{module} never FIRES the report at import (an import, a call inside an uncalled "
        "function, and a commented-out call are all not a boot report): the two switches would go "
        "unreported at boot, which is the silence this line exists to end"
    )


def test_the_pre_merge_check_runs_in_ci():
    """A check nobody runs is the defect this issue is about, one level up.

    Read from the PARSED workflow, not a substring offset, and asserted on the property that
    actually decides the guarantee: the step must run in a leg ``ci-introduced-failure`` cannot
    EXCUSE. A ``DEFERRABLE_LEGS`` member delegating a main-owned red would leave the required
    aggregate green over an unrefused omission (review round 7), so the wiring job is compared
    against the aggregate's OWN `DEFERRABLE_LEGS` value rather than named here.
    """
    yaml = pytest.importorskip("yaml")
    workflow = yaml.safe_load(
        (REPO_ROOT / ".github/workflows/python-ci.yml").read_text(encoding="utf-8")
    )
    wired = [
        (job_name, step.get("run") or "")
        for job_name, job in (workflow.get("jobs") or {}).items()
        for step in (job.get("steps") or [])
        if "embed_switch_gate.py" in str(step.get("run") or "")
    ]
    assert wired, (
        "no step RUNS tools/embed_switch_gate.py — a comment that names the check is not an "
        "invocation, and nothing would refuse an omission"
    )
    jobs = {job for job, _run in wired}
    deferrable: set[str] = set()
    for job in (workflow.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            for line in str(step.get("run") or "").splitlines():
                if "DEFERRABLE_LEGS=" in line:
                    deferrable = set(
                        line.split("DEFERRABLE_LEGS=", 1)[1].strip().strip('"').split()
                    )
    assert deferrable, (
        "the aggregate's `DEFERRABLE_LEGS=` value was not found — this pin cannot tell an "
        "excusable leg from a decisive one, so it must not pass"
    )
    assert not (jobs & deferrable), (
        f"the gate runs in a DEFERRABLE leg ({sorted(jobs & deferrable)}): `ci-introduced-failure` "
        "excuses such a leg when its failing ids are all main's, and a gate-step failure emits no "
        "test-id — so the required aggregate could read green with the omission unrefused"
    )

    # ⛔ NON-DEFERRABLE IS NOT THE SAME AS REQUIRED (review P2). `DEFERRABLE_LEGS` only names the
    # legs `ci-introduced-failure` may EXCUSE; a job outside it still gates nothing if the
    # required aggregate does not `needs:` it — the step would run, refuse, and the green
    # `python-ci-gate` would say nothing about it. So assert the wiring job is in the aggregate's
    # own `needs:` list AND that its row in the aggregate's leg table is DECISIVE (selector `-`,
    # not a `${{ … }}` selector that can decline the leg).
    aggregate = (workflow.get("jobs") or {}).get("python-ci-gate")
    assert aggregate, "`python-ci-gate` (the required aggregate) is missing from the workflow"
    needs = set(aggregate.get("needs") or [])
    assert jobs <= needs, (
        f"the gate runs in a job the REQUIRED aggregate does not `needs:` ({sorted(jobs - needs)}): "
        "it would run and refuse while `python-ci-gate` — the only required check — stays green, "
        "so the refusal gates nothing"
    )
    rows = {}
    for step in aggregate.get("steps") or []:
        for line in str(step.get("run") or "").splitlines():
            row = line.strip().split("|")
            if len(row) == 3 and row[0] in needs:
                rows[row[0]] = row[2].strip()
    assert rows, (
        "the aggregate's leg table (`name|result|selector` rows) was not found — this pin cannot "
        "tell a decisive leg from a declinable one, so it must not pass"
    )
    for job in sorted(jobs):
        assert rows.get(job) == "-", (
            f"the aggregate's row for the gate's job `{job}` carries selector "
            f"`{rows.get(job)}` instead of `-`: a selector can DECLINE the leg, which would leave "
            "the required aggregate green with the omission unrefused"
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


def test_a_case_arm_inside_a_substitution_is_not_the_substitutions_CLOSER():
    """Review P1 at `af655a8153` — the arm terminator closed the span, so `esac )` read as CODE.

    A ``)`` inside ``$( … )`` has two meanings and the reader only knew one. In
    ``mode=$( case "$MODE" in off) echo 0 ;; *) echo 1 ;; esac )`` the FIRST ``)`` ends a `case`
    PATTERN, not the substitution — so the span ended at the arm, the leftover `esac )` was read as
    code with no opener, and it decremented a REAL enclosing block (a function body, here). Both
    ``export``s inside the never-called helper were then credited ``STATED``: ``ok``, exit 0, over
    a process that receives neither switch. `case` is now counted inside a span, so its arms
    cannot close it.
    """
    for label, script in (
        (
            "a case arm inside a command substitution",
            "#!/bin/sh\n"
            "set -eu\n"
            "_configure() {\n"
            '  mode=$( case "$MODE" in\n'
            "             off) echo 0 ;;\n"
            "             *)   echo 1 ;;\n"
            "          esac )\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "}\n"
            'exec "$@"\n',
        ),
        (
            "a case arm containing a block",
            "#!/bin/sh\n"
            "if false; then\n"
            '  y=$( case x in x) if true; then :; fi;; esac )\n'
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\n",
        ),
    ):
        stated, ambiguous = gate._shell_assignments(script)
        assert stated == {}, f"{label} closed a REAL enclosing block: {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)


def test_a_line_that_closes_one_block_and_opens_another_is_NET_ZERO():
    """Review P2 at `af655a8153` — the opener was only recognised at LINE START.

    ``fi; if false; then`` closes one block and opens another. The closer matched, the opener did
    not (it is mid-line), so the line counted as a net ``-1``: the new block was invisible and the
    ``export``s in its body — a branch that never runs — were credited ``STATED``, exit 0. Openers
    now match after a statement separator, exactly like closers already did, so the line is net
    zero and the body stays conditional.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        "if true; then\n"
        "  echo a\n"
        "fi; if false; then\n"
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "fi\n"
        'exec "$@"\n'
    )
    assert stated == {}, f"a `fi; if …` line lost the block it opens: {stated}"
    assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous


def test_a_heredoc_opened_INSIDE_a_substitution_is_a_body_not_shell():
    """Review P1 at `6a221f9053` — the opener scan could not see `<<EOF` inside `$( … )`.

    The structural view BLANKS a substitution's body, so the `<<'EOF'` in
    ``usage=$(cat <<'EOF' … EOF)`` never registered as an opener and its body was read as SHELL.
    A `)` in the body (a numbered help line, ``1) Set the db uri``) then closed the substitution
    frame EARLY, and the rest of the body ran as code: the help text's `export` was credited
    ``STATED`` — ``ok``, exit 0 — over a process that receives neither switch (measured:
    ``env -i … bash script /usr/bin/env`` printed neither).
    The opener scan now uses a second view that keeps substitution bodies as CODE (strings,
    comments and arithmetic still blanked), so the body is dropped where it belongs.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\n"
        "usage=$(cat <<'EOF'\n"
        "1) Set the db uri\n"
        "export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        "EOF\n"
        ")\n"
        'exec "$@"\n'
    )
    assert stated == {}, f"a heredoc body inside a substitution was read as shell: {stated}"
    # ⛔ And the names inside the body are NOT mentions either — the body is data, so nothing in it
    # is a statement or a reference. The channel is refused for stating neither switch (measured:
    # `entrypoint.sh does not state TORTOISE_VALUE_GATE (entrypoint assignments)`), which is the
    # fail-closed direction this gate owes on a file it can see contains no arm.
    assert ambiguous == set(), ambiguous
    channel = gate.Channel(
        "entrypoint.sh", "entrypoint", "entrypoint assignments", stated=stated, ambiguous=ambiguous
    )
    assert len(channel.problems()) == 2, channel.problems()

    # ⛔ AND THE FIX MUST NOT TURN A SHIFT INTO A HEREDOC. `$(( 1 << n ))` is arithmetic, and it
    # stays blanked in BOTH views, so the `<<` never opens a phantom body that would swallow the
    # real statement below it and REFUSE an honest config.
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\n"
        "shift_by=$(( 1 << n ))\n"
        "export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_a_redirect_is_not_a_fork():
    """The other direction: `&>`/`>&2`/`>|` move a STREAM, they do not fork a subshell.

    Reading them as a fork would route an honest top-level `export` to ``ambiguous`` and REFUSE a
    config that arms both switches — a false refusal is a bug on this gate too.
    """
    for label, line in (
        ("combined redirect", "export TORTOISE_VALUE_GATE=1 &> /tmp/log"),
        ("stderr dup", "export TORTOISE_EMBED_GATE=1 2>&1"),
        ("clobber redirect", "export TORTOISE_VALUE_GATE=1 >| /tmp/log"),
    ):
        stated, ambiguous = gate._shell_assignments("#!/bin/bash\n" + line + '\nexec "$@"\n')
        assert stated, f"{label} was read as a fork and refused: {ambiguous}"
        assert ambiguous == set(), (label, ambiguous)


def test_a_heredoc_opener_inside_a_comment_or_a_string_is_NOT_a_heredoc():
    """Review P2 at `af655a8153` — the `<<` was searched on the RAW line.

    ``# … cat <<EOF > /tmp/conf`` in a trailing comment (or ``echo "… << EOF …"``) opened a
    PHANTOM body. The body then swallowed lines until it found ``EOF`` — including the real
    ``if false; then`` header — so the branch's ``export``s sat at depth 0 and were credited
    ``STATED``: ``ok``, exit 0. The opener is now located in the structural view (with span
    DELIMITERS kept), so a ``<<`` inside a string or a comment is not an opener while a quoted
    delimiter (``<<'EOF'``) still is.
    """
    for label, header in (
        ("a trailing comment", "set -eu   # to write the config use: cat <<EOF > /tmp/conf\n"),
        ("a quoted string", 'echo "operator tip: cat << EOF > /tmp/notes"\n'),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\n"
            + header
            + "if false; then\n"
            "  echo disabled\n"
            "EOF\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\n"
        )
        assert stated == {}, f"{label} opened a phantom heredoc body: {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)

    # The REAL spellings must still be bodies — including the backslash-quoted delimiter.
    for label, opener in (
        ("a quoted delimiter", "cat <<'EOF'"),
        ("a backslash delimiter", "cat <<\\EOF"),
        ("a tab-stripping body", "cat <<-EOF"),
    ):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\n"
            "if false; then\n"
            f"  {opener}\n"
            "fi is not a keyword here\n"
            "EOF\n"
            "  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n"
            "fi\n"
        )
        assert stated == {}, f"{label} body was read as shell: {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)


def test_one_export_line_may_state_SEVERAL_switches():
    """Review P2 at `af655a8153` — one `export` carrying two assignments was REFUSED, wrongly.

    ``export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1`` is ordinary shell and arms BOTH
    switches, but the single-assignment regex took the whole remainder as the first value: the
    channel was refused for not stating the second switch, and the ``ok`` line would have reported
    the fabricated value ``1 TORTOISE_EMBED_GATE=1`` for the first. Both halves are what this
    pins — the credit AND the value that gets printed.
    """
    for label, script in (
        ("one line", "export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"),
        ("a continuation", "export TORTOISE_VALUE_GATE=1 \\\n       TORTOISE_EMBED_GATE=1\n"),
        ("a semicolon", "export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1\n"),
    ):
        stated, ambiguous = gate._shell_assignments("#!/bin/sh\n" + script + 'exec "$@"\n')
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (label, stated)
        assert ambiguous == set(), (label, ambiguous)


def test_a_fallback_that_is_itself_a_variable_is_refused():
    """Review P1 at `b8e12ddb78` — `bool(fallback)` accepted a default that can resolve EMPTY.

    `${X:-$FALLBACK}` states a fallback, but that fallback is ITSELF a variable (or a command
    substitution), so with both unset the process receives nothing while the gate reported
    ``STATED`` — measured: the same export body printed an empty value under `env -i`, and the
    gate had already exited 0. A reader with no resolver may only take the fail-CLOSED direction.
    """
    for body, reader in (
        ('export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-$FALLBACK}"\n', gate._shell_assignments),
        ("export TORTOISE_EMBED_GATE=${TORTOISE_EMBED_GATE:-$FALLBACK}\n", gate._shell_assignments),
        (
            'export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-`cat /tmp/v`}"\n',
            gate._shell_assignments,
        ),
        ("ENV TORTOISE_EMBED_GATE=${TORTOISE_EMBED_GATE:-$FALLBACK}\n", gate._dockerfile_env),
    ):
        stated, ambiguous, *_rest = reader(body)
        assert stated == {}, (body, stated)
        assert "TORTOISE_EMBED_GATE" in ambiguous, (body, ambiguous)

    # ...and the honest shape still states a value, so this is not a blanket refusal of `${…:-…}`.
    stated, ambiguous = gate._shell_assignments(
        'export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"\n'
    )
    assert stated == {"TORTOISE_EMBED_GATE": "${TORTOISE_EMBED_GATE:-1}"} and ambiguous == set()


def test_a_line_that_opens_TWO_blocks_and_closes_ONE_does_not_read_as_top_level():
    """Review P1 at `b8e12ddb78` — "opens AND closes ⇒ net zero" is false when the counts differ.

    `if false; then if false; then echo x; fi` opens two blocks and closes one, so the depth must be
    1 afterwards. The per-line rule treated "matches both" as zero, the exports of a never-running
    OUTER branch were credited ``STATED``, and the gate exited 0. Measured against bash: the same
    script body prints both switches UNSET.

    ⛔ The second `if` is only visible because an opener may follow the `then` that begins the next
    statement — without that the line again balanced to zero, which is why the outer-branch shape
    is pinned as the mutation target rather than the counts alone.
    """
    for label, script in (
        (
            "an outer branch holding a one-line inner if",
            "#!/bin/sh\nif false; then if false; then echo x; fi\n"
            "export TORTOISE_VALUE_GATE=1\n"
            "export TORTOISE_EMBED_GATE=1\nfi\nexec \"$@\"\n",
        ),
        (
            "an else-if chain",
            "#!/bin/sh\nif false; then\n :\nelse if false; then\n"
            "  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\nfi\nfi\nexec \"$@\"\n",
        ),
    ):
        stated, ambiguous = gate._shell_assignments(script)
        assert stated == {}, f"{label}: a never-running branch was credited {stated}"
        assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, (label, ambiguous)

    # non-vacuity in the other direction: a BALANCED one-line compound still returns to top level
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\nif [ -n \"$X\" ]; then echo hi; fi\n"
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, stated
    assert ambiguous == set(), ambiguous


def test_a_closer_that_shares_a_line_with_a_later_assignment_still_retracts():
    """Review P2 at `b8e12ddb78` — the closer branch `continue`d past the assignment scan.

    `fi; export K="${MAYBE_EMPTY}"` closes the block and THEN assigns, but the whole line was
    skipped, so an earlier literal `export K=1` survived as ``STATED`` while the process receives
    the later value — the same last-wins rule the `unset`/bare-assignment regexes already enforce,
    through a hole in the line model. Each statement is now judged at the depth it runs at.
    """
    retracted, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n'
        'if [ -n "${X:-}" ]; then\n  echo hi\n'
        'fi; export TORTOISE_EMBED_GATE="${MAYBE_EMPTY}"\n'
        'exec "$@"\n'
    )
    assert retracted == {"TORTOISE_VALUE_GATE": "1"}, retracted
    assert "TORTOISE_EMBED_GATE" in ambiguous, ambiguous

    # non-vacuity: a LITERAL assignment sharing the closer's line IS a top-level statement
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\n"
        'if [ -n "${X:-}" ]; then\n  echo hi\n'
        "fi; export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a top-level assignment after a closer was refused as nested: {stated} / {ambiguous}"
    )
    assert ambiguous == set(), ambiguous


def test_a_do_on_its_own_line_is_not_a_second_opener():
    """Review P2 at `b8e12ddb78` — `do` was counted as an opener AND is the loop header's tail.

    A loop written the ordinary two-line way (`while …` then `do` alone) therefore netted +2 against
    one `done`, the depth never returned to 0, and an HONEST entrypoint was refused with a message
    blaming the value. `do` is the CONTINUATION of a header that already counts.
    """
    for label, script in (
        (
            "while, with do on its own line",
            "#!/bin/sh\nwhile IFS= read -r line\ndo\n  echo \"$line\"\ndone\n"
            "export TORTOISE_VALUE_GATE=1\n"
            "export TORTOISE_EMBED_GATE=1\nexec \"$@\"\n",
        ),
        (
            "for, with do on its own line",
            "#!/bin/sh\nfor f in a b\ndo\n  echo \"$f\"\ndone\n"
            "export TORTOISE_VALUE_GATE=1\n"
            "export TORTOISE_EMBED_GATE=1\nexec \"$@\"\n",
        ),
    ):
        stated, ambiguous = gate._shell_assignments(script)
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
            f"{label}: an honest top-level export was refused as nested: {stated} / {ambiguous}"
        )

    # ...and a body INSIDE the loop is still conditional — the header still opens a block
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/sh\nwhile IFS= read -r line\ndo\n"
        "  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\ndone\n"
    )
    assert stated == {}, stated
    assert {"TORTOISE_VALUE_GATE", "TORTOISE_EMBED_GATE"} <= ambiguous, ambiguous


def test_export_dash_n_retracts_like_unset():
    """Review P3 at `b8e12ddb78` — `export -n K` un-exports a value the process then never gets.

    `entrypoint.sh` is `#!/bin/bash`, where `-n` removes K from the exported environment without
    changing it; the reader kept the literal and printed `ok … =1` over a process that receives
    nothing (measured: `env -i … bash script` printed `NOT IN ENVIRONMENT`).
    """
    for tail in ("export -n TORTOISE_EMBED_GATE\n", "export -n TORTOISE_EMBED_GATE TORTOISE_OTHER\n"):
        stated, ambiguous = gate._shell_assignments(
            "#!/bin/sh\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n" + tail
        )
        assert stated == {"TORTOISE_VALUE_GATE": "1"}, (tail, stated)
        assert "TORTOISE_EMBED_GATE" in ambiguous, (tail, ambiguous)


#: The two shell controls that make a statement run SOMEWHERE ELSE. Each is a form the shipped
#: `entrypoint.sh` could plausibly grow into, and each was credited `STATED` before this pin.
_SHORT_CIRCUIT_BODIES = (
    # an `if` spelled with `&&`/`||` instead of `then`/`fi` — a BRANCH all the same
    '[ -n "$MAYBE" ] && export TORTOISE_VALUE_GATE=1',
    'true || export TORTOISE_VALUE_GATE=1',
    'false && export TORTOISE_VALUE_GATE=1',
    'command -v uvicorn >/dev/null || export TORTOISE_VALUE_GATE=1',
    # `&&` on a CONTINUATION line: the separator is what makes it conditional, not the line break
    '[ -n "$MAYBE" ] \\\n  && export TORTOISE_VALUE_GATE=1',
    # a BACKGROUNDED statement: the subshell exports, the process inherits nothing
    'export TORTOISE_VALUE_GATE=1 &',
    'export TORTOISE_VALUE_GATE=1 2>/dev/null &',
    # a FORKED statement: BOTH sides of a pipe run in their own subshell (measured 2026-11-16:
    # `bash -c 'export K=1 | cat; …'` and `bash -c 'export K=1 |& cat; …'` both print `[UNSET]`),
    # so the parent never receives the value either. `|&` is the same operator with stderr piped.
    'export TORTOISE_VALUE_GATE=1 | cat',
    'export TORTOISE_VALUE_GATE=1 |& cat',
)


@pytest.mark.parametrize("body", _SHORT_CIRCUIT_BODIES)
def test_a_statement_that_short_circuits_or_backgrounds_is_not_credited(body):
    """Review P0 (measured) — `&&`/`||`/`&` are BRANCHES, and a branch states nothing.

    `_shell_statements` splits a logical line on `;`/`&&`/`||` so a block closer sitting beside an
    assignment is seen, and the split then handed the RIGHT-hand slice to the reader as an ordinary
    top-level statement. The `conditional` test was `depth > 0 or terminated` — it had no notion of
    the separator that produced the slice — so a guarded export was credited and the gate exited 0:

        [ -n "$MAYBE" ] && export TORTOISE_VALUE_GATE=1     -> ok  TORTOISE_VALUE_GATE=1
        true || export TORTOISE_EMBED_GATE=1                -> ok  TORTOISE_EMBED_GATE=1

    while the same script under `env -i … bash` printed NEITHER switch: `MAYBE` is unset, so the
    `&&` RHS never ran, and `true` succeeded, so the `||` RHS never ran. A bare `&` is the same
    class from the other side — `export K=1 &` exports inside a SUBSHELL.

    This is the exact shape the module's contract already refuses as `if …; then export …`: the
    branch spelling is irrelevant, so the verdict must be `ambiguous` (fail CLOSED), never `STATED`.
    Mutation-proved: reverting `conditional` to `depth > 0 or terminated` reds every case here.
    """
    stated, ambiguous = gate._shell_assignments(f"#!/bin/sh\n{body}\nexec \"$@\"\n")
    assert stated == {}, f"a conditional/backgrounded export was credited as a statement: {body!r} -> {stated}"
    assert "TORTOISE_VALUE_GATE" in ambiguous, (
        f"a conditional export must land on the fail-closed verdict, not vanish: {body!r} -> {ambiguous}"
    )


def test_the_short_circuit_guard_does_not_refuse_an_honest_entrypoint():
    """The guard is about the SEPARATOR, so an unconditional statement is untouched — both sides.

    Fail-closed is only correct if it is not also false-closed: a `;`-separated second statement
    (the reason the split exists), an export on the LEFT of `&&` (it runs unconditionally), and a
    REDIRECT (`>&2`, `&>file`, `>|log` — they move a stream, they do not fork a subshell) are all
    ordinary statements and stay credited. ⛔ `|&` used to be listed here as ordinary too; it is
    NOT — it is a pipeline, every element of which runs in its own subshell (see the note at the
    end of the table, and `test_a_statement_that_short_circuits_or_backgrounds_is_not_credited`).
    Mutation-proved in the opposite direction from the test above.
    """
    for label, script, expected in (
        (
            "`;`-separated: both statements are unconditional",
            "export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1",
            {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"},
        ),
        (
            "a closer beside a later assignment (`fi; export K=1`)",
            "if false; then\n  echo x\nfi; export TORTOISE_VALUE_GATE=1\n",
            {"TORTOISE_VALUE_GATE": "1"},
        ),
        (
            "the LEFT of `&&` runs unconditionally",
            "export TORTOISE_VALUE_GATE=1 && echo armed",
            {"TORTOISE_VALUE_GATE": "1"},
        ),
        (
            "a `>&2` redirect is not a fork",
            "export TORTOISE_VALUE_GATE=1 >&2",
            {"TORTOISE_VALUE_GATE": "1"},
        ),
        (
            "an `&>file` redirect is not a fork",
            "export TORTOISE_VALUE_GATE=1 &>/dev/null",
            {"TORTOISE_VALUE_GATE": "1"},
        ),
        (
            "a `>|` clobber redirect is not a fork",
            "export TORTOISE_VALUE_GATE=1 >|/tmp/log",
            {"TORTOISE_VALUE_GATE": "1"},
        ),
        # ⛔ `|&` IS NOT ONE OF THEM (measured 2026-11-16, this box):
        #   bash -c 'export K=1 |& cat; echo "[${K:-UNSET}]"'   ->  [UNSET]
        # so a `|` — and `|&`, the same operator with stderr piped too — puts the statement in a
        # SUBSHELL and the parent never receives the value. It was pinned here as CREDITED, which
        # is a false-PASS pin: it asserted the gate may credit a value the process cannot have.
        # The refusal lives in `test_a_statement_that_short_circuits_or_backgrounds_is_not_credited`.
    ):
        stated, ambiguous = gate._shell_assignments(script + "\n")
        assert stated == expected, f"{label}: {script!r} -> {stated} / ambiguous={ambiguous}"


def test_the_real_entrypoint_statements_are_unconditional():
    """This repository's own channel must not be refused by the new guard — measured, not assumed.

    The guard added above is the kind that can turn a passing repo red, so the population it
    actually reads is pinned here: `entrypoint.sh` arms both switches with a TOP-LEVEL export, no
    `&&`/`||`/`&` in the statement. If a future edit guards that line (`[ … ] && export …`), this
    reds for the right reason: the shipped container may then never receive the value.
    """
    text = (REPO_ROOT / "entrypoint.sh").read_text(encoding="utf-8")
    stated, _ = gate._shell_assignments(text)
    assert {key: value for key, value in stated.items() if key in SWITCHES} == {
        "TORTOISE_VALUE_GATE": "${TORTOISE_VALUE_GATE:-1}",
        "TORTOISE_EMBED_GATE": "${TORTOISE_EMBED_GATE:-1}",
    }, stated


#: The three ways a LATER statement takes an earlier `export K=1` back. Each must still retract
#: when it is the right-hand side of `&&`/`||`.
_RETRACTIONS = (
    "unset TORTOISE_VALUE_GATE",
    "export -n TORTOISE_VALUE_GATE",
    "TORTOISE_VALUE_GATE=",
)


@pytest.mark.parametrize("retraction", _RETRACTIONS)
def test_a_short_circuited_retraction_still_retracts(retraction):
    """Review P0 at `a0d5b9ae4908` (measured) — crediting and retracting are DIFFERENT questions.

    The cycle-1 fix folded the `&&`/`||` separator into one `conditional` flag, and the retraction
    branches are gated on it — so once a retraction was declared conditional they did NOTHING at
    all, not even `ambiguous.add`, and the earlier literal survived in `stated`, which
    `switch_problem` PREFERS over `ambiguous`. Measured against `env -i … bash` ground truth:

        export K=1 && unset K        gate: STATED K=1   bash: K UNSET   <-- false PASS
        export K=1 && export -n K    gate: STATED K=1   bash: K UNSET   <-- false PASS
        export K=1 && K=             gate: STATED K=1   bash: K=""      <-- false PASS

    A short-circuited statement runs in the PARENT and may or may not execute, so whether the value
    survives is unknowable to this reader — and "cannot be shown to hold" fails CLOSED. It is
    retracted (and so lands on `ambiguous`, i.e. refused), never reported `STATED`.
    Mutation-proved: restoring `not conditional` on the retraction branches reds all three.
    """
    stated, ambiguous = gate._shell_assignments(
        f"export TORTOISE_VALUE_GATE=1 && {retraction}\n"
    )
    assert stated == {}, f"a short-circuited retraction was skipped as if it never ran: {retraction!r} -> {stated}"
    assert "TORTOISE_VALUE_GATE" in ambiguous, (retraction, ambiguous)


def test_a_forked_retraction_does_NOT_retract():
    r"""The other half of the split: a subshell cannot change the PARENT's environment.

    `K= | cat` and `K= &` run in a subshell, so the parent still has the exported K — retracting on
    them would be a FALSE REFUSAL of an honest config, the mirror of the fail-open above. Measured
    on this box (`env -i … bash`): `export K=1` then either statement, then `echo "[${K-UNSET}]"`,
    prints `[1]` — the value survives.

    ⛔ The BARE-ASSIGNMENT spelling is the only one that reaches this clause, and that is the reason
    the test uses it: `_SHELL_UNSET`/`_SHELL_EXPORT_UNEXPORT` anchor on `\s*$` inside a name
    character class, so `unset K | cat` never matches them at all (it falls through to the mention
    scan) — a pin written on that spelling passes for the WRONG reason and pins nothing. An earlier
    draft of this test did exactly that and survived its own mutation; this one does not.

    Mutation-proved: dropping `forked` from `retracts` (`not (depth > 0 or terminated)`) reds
    exactly this test and nothing else.
    """
    for forked_retraction in ("TORTOISE_VALUE_GATE= | cat", "TORTOISE_VALUE_GATE= &"):
        stated, _ = gate._shell_assignments(
            f"export TORTOISE_VALUE_GATE=1\n{forked_retraction}\n"
        )
        assert stated == {"TORTOISE_VALUE_GATE": "1"}, (
            f"a forked retraction cannot clear the parent's value, so the export must survive: "
            f"{forked_retraction!r} -> {stated}"
        )


@pytest.mark.parametrize(
    "delimiter", ["END-OF-DATA", "E.O.F", "'E.O.F'", '"E.O.F"', "\\EOF", "EOF"]
)
def test_the_dockerfile_heredoc_delimiter_is_a_WORD(delimiter):
    """Review P1 (measured) — the Dockerfile reader's delimiter was IDENTIFIER-only.

    `_HEREDOC_OPEN` matched `[A-Za-z_][A-Za-z0-9_]*`, so `<<END-OF-DATA` (and `<<E.O.F`,
    `<<'E.O.F'`, `<<\\EOF`) left the opener unrecognised and its BODY was read as INSTRUCTIONS.
    Measured on a Dockerfile whose only "ENV" lines were heredoc data — the gate printed
    `ok … TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1`, exit 0, while `sh` (what `RUN` executes)
    wrote those lines OUT verbatim: they are text a program prints, not statements the image gets.

    Same fix as the shell reader's heredoc word (which `532ab60251e1` landed while this reader kept
    the old grammar), and the same spelling table, so the two readers cannot drift again.
    Mutation-proved: restoring the identifier-only word reds the non-`EOF` rows.
    """
    body = (
        "FROM alpine:3.20\n"
        f"RUN cat > /tmp/notes.txt <<{delimiter}\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
        f"{delimiter.lstrip(chr(92)).strip(chr(39) + chr(34))}\n"
    )
    stated, ambiguous, _ = gate._dockerfile_env(body)
    assert stated == {}, (
        f"a heredoc BODY was read as ENV instructions for delimiter {delimiter!r}: {stated}"
    )
    # A body is DATA, not a mention: the reader steps over it, so nothing from inside it is
    # recorded either way. (Asserting `ambiguous` here would pin the wrong thing — the switches are
    # absent from the FILE'S INSTRUCTIONS, which is what the channel verdict reports.)
    assert ambiguous == set(), ambiguous


@pytest.mark.parametrize(
    "retraction",
    [
        "unset TORTOISE_EMBED_GATE 2>/dev/null",
        "unset TORTOISE_EMBED_GATE >/dev/null",
        'unset "TORTOISE_EMBED_GATE"',
        "unset -v TORTOISE_EMBED_GATE",
        "export -n TORTOISE_EMBED_GATE 2>/dev/null",
    ],
)
def test_a_retraction_survives_a_trailing_word(retraction):
    """Review P1 (measured) — a trailing redirect defeated every retraction arm.

    `_SHELL_UNSET`/`_SHELL_EXPORT_UNEXPORT` anchored on `[\\w\\s-]+\\s*$`, so one trailing token made
    the statement match NONE of the three retraction arms; it fell to the mention scan, and the
    earlier literal survived in `stated` — which `switch_problem` PREFERS. Measured: each spelling
    below printed `ok … TORTOISE_EMBED_GATE=1` while `env -i … bash script /usr/bin/env` showed the
    switch absent. `2>/dev/null` is the IDIOMATIC spelling precisely because the name may be
    absent, so this was a realistic disarm.

    Mutation-proved: restoring the `\\s*$` anchor reds the redirect/quoted rows.
    """
    stated, ambiguous = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
        f"{retraction}\n"
        'exec "$@"\n'
    )
    assert "TORTOISE_EMBED_GATE" not in stated, (
        f"a retraction with a trailing word was dropped, so a stale literal survived: "
        f"{retraction!r} -> {stated}"
    )
    assert "TORTOISE_EMBED_GATE" in ambiguous, (retraction, ambiguous)


def test_a_brace_GROUP_is_credited_and_a_function_BODY_is_not():
    """Review P1 (measured) — `{` is two different constructs, and the reader treated them alike.

    A `{ … }` GROUP runs in the CURRENT shell, so its `export` reaches the process — the gate
    refused an honest entrypoint (both switches MISSING while `env -i … bash script /usr/bin/env`
    printed both). A FUNCTION BODY never runs unless called, so it must fail closed. The two are
    told apart the only way a reader can: a body's `{` follows `)` (or `function NAME`).

    Mutation-proved in both directions: dropping the `_function_body_braces` arm credits a body
    (fail-open); counting every `{` as an opener refuses the group (false refusal).
    """
    group = (
        "#!/bin/bash\n{\n  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n}\n"
        'exec "$@"\n'
    )
    stated, _, = gate._shell_assignments(group)[:2]
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a brace GROUP runs in the current shell, so its export DOES reach the process: {stated}"
    )

    for label, body in (
        ("multi-line body", "arm() {\n  export TORTOISE_VALUE_GATE=1\n}\n"),
        ("one-line body", "arm() { export TORTOISE_VALUE_GATE=1; }\n"),
        ("`function` keyword", "function arm {\n  export TORTOISE_VALUE_GATE=1\n}\n"),
    ):
        stated, ambiguous = gate._shell_assignments(body)
        assert stated == {}, f"{label}: a function body never runs unless called: {stated}"
        assert "TORTOISE_VALUE_GATE" in ambiguous, (label, ambiguous)


@pytest.mark.parametrize(
    "statement",
    [
        'exec env -i "$@"',
        'exec env - "$@"',
        'exec env --ignore-environment "$@"',
        "exec env -u TORTOISE_EMBED_GATE \"$@\"",
        "declare -x TORTOISE_EMBED_GATE=",
        "readonly TORTOISE_EMBED_GATE=",
    ],
)
def test_an_exec_or_builtin_that_discards_the_switch_is_refused(statement):
    """Review P2 (measured) — the statement that REPLACES or re-binds the env was not modelled.

    `exec env -i "$@"` replaces the process with one whose environment is EMPTY, so every export
    above it is discarded — measured: the gate printed `ok … =1 =1` while `env -i … bash script
    /usr/bin/env` printed no `TORTOISE_*` at all. `-u NAME`/`--unset=NAME` removes one name.

    `declare -x K=` is the same class one shape over: after `export K=1` it leaves K EMPTY and
    still exported (bash prints `K=`), while the gate kept the earlier `=1` and printed `ok`.

    Both fail CLOSED (`ambiguous`), because a value run through them cannot be shown to survive.
    Mutation-proved: dropping the `_EXEC_STATEMENT` arm reds the `env` rows; dropping
    `_SHELL_BUILTIN_ASSIGN` reds the `declare`/`readonly` rows.
    """
    stated, ambiguous = gate._shell_assignments(
        "#!/bin/bash\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        f"{statement}\n"
    )
    assert "TORTOISE_EMBED_GATE" not in stated, (
        f"the switch cannot be shown to survive {statement!r}, so crediting it is a false PASS: "
        f"{stated}"
    )
    assert "TORTOISE_EMBED_GATE" in ambiguous, (statement, ambiguous)


def test_a_dockerfile_heredoc_body_survives_a_CONTINUATION_line():
    """Review P1 (measured) — bodies were located AFTER continuations were joined, so one
    backslash inside a body swallowed its own terminator and the body was read as INSTRUCTIONS.

    The A/B is ONE trailing backslash. Measured, final stage carrying NO `ENV`, body carrying a
    `FROM` and both `ENV` lines:

        cmd --flag \\      ->  ok TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1   (exit 0)
        cmd --flag        ->  REFUSED … MISSING MISSING

    while `docker inspect` showed no `TORTOISE_*` and `docker run cat /notes.txt` printed the body
    verbatim — so the gate credited statements the image never receives. The reader now walks
    PHYSICAL lines and joins continuations only for the INSTRUCTION.

    Mutation-proved: joining before locating (the previous shape) reds this test.
    """
    text = (
        "FROM alpine:3.20\n"
        "RUN cat > /notes.txt <<EOF\n"
        "example deployment:\n"
        "FROM tortoise-selfhost:latest\n"
        "ENV TORTOISE_VALUE_GATE=1\n"
        "ENV TORTOISE_EMBED_GATE=1\n"
        "cmd --flag \\\n"
        "EOF\n"
    )
    stated, _ambiguous, _ = gate._dockerfile_env(text)
    assert stated == {}, (
        f"a heredoc body was read as instructions because one body line ended in a backslash: {stated}"
    )


@pytest.mark.parametrize(
    "statement",
    [
        "exec env TORTOISE_VALUE_GATE= \"$@\"",
        "exec env TORTOISE_VALUE_GATE='' \"$@\"",
        'exec env TORTOISE_VALUE_GATE="" "$@"',
        "exec env -uTORTOISE_VALUE_GATE \"$@\"",
    ],
)
def test_an_env_that_empties_or_strips_the_switch_is_a_retraction(statement):
    """Review P1/P2 (measured) — `env` can EMPTY or strip a name, and neither was modelled.

    `env K=` sets the child's K to EMPTY, which the resolver reads OFF; `-uK` (getopt's attached
    value) removes it. Measured: each shape printed `ok … TORTOISE_VALUE_GATE=1` while
    `bash … /usr/bin/env` showed `TORTOISE_VALUE_GATE=` (empty) or nothing at all.

    Mutation-proved: dropping `_SHELL_ENV_EMPTIED` reds the `=` rows; restoring the `(?:\\s+|=)`
    separator requirement on `_SHELL_ENV_UNSET` reds the `-uK` row.
    """
    stated, ambiguous = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n" + statement + "\n"
    )
    assert "TORTOISE_VALUE_GATE" not in stated, (
        f"`env` empties or strips the switch, so crediting `=1` is a false PASS: {statement!r}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous, (statement, ambiguous)


def test_a_statement_prefix_neither_disarms_a_retraction_nor_a_group_export():
    """Review P1 (measured) — an anchored `^\\s*<verb>` reader missed every prefixed statement.

    `{ unset K; }` runs the unset in the CURRENT shell (a group is the module's own credit rule)
    yet the `{` defeated the retraction regex, so the earlier literal survived and the gate printed
    `ok` over a process without K. `command`/`builtin` are transparent for the same reason. The
    SAME prefix in the other direction REFUSED an honest one-line group, because the first
    `export` could not match `^\\s*export`.

    Mutation-proved: dropping `_strip_statement_prefix` reds both halves.
    """
    for prefix, retraction in (
        ("{ unset TORTOISE_VALUE_GATE; }", "a brace GROUP runs in the current shell"),
        ("command unset TORTOISE_VALUE_GATE", "`command` is transparent"),
        ("builtin unset TORTOISE_VALUE_GATE", "`builtin` is transparent"),
    ):
        stated, ambiguous = gate._shell_assignments(
            "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
            f"{prefix}\n" + 'exec "$@"\n'
        )
        assert "TORTOISE_VALUE_GATE" not in stated, (retraction, prefix, stated)
        assert "TORTOISE_VALUE_GATE" in ambiguous, (prefix, ambiguous)

    stated, _ = gate._shell_assignments(
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; }\n" + 'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a one-line brace GROUP arms both switches in the current shell: {stated}"
    )


def test_a_control_flow_retraction_retracts_but_a_function_body_one_does_not():
    """Review P2 (measured) — `depth > 0` lumped two different scopes together.

    `if true; then unset K; fi` at top level DOES run, so the retraction must land — it was skipped
    entirely (no retract, no `ambiguous`), and the gate printed `ok … =1` over a process without K.
    A `unset` in a FUNCTION BODY must stay INERT: a helper that is never called must not redden an
    honest config (the reason the nested arm exists at all).

    Mutation-proved: dropping `nested_retracts` reds the control-flow row; counting function bodies
    as control flow reds the function-body row.
    """
    control_flow, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        "if true; then unset TORTOISE_VALUE_GATE; fi\n" + 'exec "$@"\n'
    )
    assert "TORTOISE_VALUE_GATE" not in control_flow, (
        f"a control-flow retraction runs at top level, so it must land: {control_flow}"
    )

    in_body, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        "arm() { unset TORTOISE_VALUE_GATE; }\n" + 'exec "$@"\n'
    )
    assert in_body == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a helper that is never called must not retract the shipped value: {in_body}"
    )


@pytest.mark.parametrize(
    "body",
    [
        # the ALLMAN spelling: the body brace is on the line AFTER its header
        "arm_gates()\n{\n  export TORTOISE_VALUE_GATE=1\n}\n",
        "function arm_gates\n{\n  export TORTOISE_VALUE_GATE=1\n}\n",
        # ⛔ A BLANK line between the header and its brace must not clear the remembered header
        # (review P1 at `5260e1f7`, measured): an empty statement reset `pending_fn_header`, so the
        # lone `{` read as a group OPENER — and a group is CREDITED — reporting `ok … =1 =1`
        # (exit 0) over a process that `env -i … bash script /usr/bin/env` shows receives neither.
        "arm_gates()\n\n{\n  export TORTOISE_VALUE_GATE=1\n}\n",
        "function arm_gates()\n\n{\n  export TORTOISE_VALUE_GATE=1\n}\n",
        "function arm_gates\n\n\n{\n  export TORTOISE_VALUE_GATE=1\n}\n",
    ],
)
def test_an_ALLMAN_brace_function_body_is_refused(body):
    """Review P1 (measured) — one newline defeated every pinned function-body spelling.

    `_SHELL_FN_BRACE` needs the `)` and the `{` on ONE line, so `arm() {`, `arm() { …; }` and
    `function arm {` were all refused while `arm_gates()` / `{` / `export …` / `}` was credited
    `ok … =1` (exit 0) over a process that `env -i … bash script /usr/bin/env` shows receives
    nothing. The header is now remembered and the following lone `{` is the body's brace.

    Mutation-proved: dropping `pending_fn_header` reds both rows while the group row below stays
    green — the two are told apart by the HEADER, not by the brace.
    """
    stated, ambiguous = gate._shell_assignments(body + 'exec "$@"\n')
    assert stated == {}, f"an Allman-spelled function body never runs unless called: {stated}"
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous

    # …and the mirror stays credited: a GROUP after nothing at all is not a body
    group, _ = gate._shell_assignments(
        "{\n  export TORTOISE_VALUE_GATE=1\n}\n" + 'exec "$@"\n'
    )
    assert group == {"TORTOISE_VALUE_GATE": "1"}, group


def test_a_block_keyword_right_after_a_group_brace_is_not_invisible():
    """Review P1 (measured) — `_SHELL_OPENERS` anchors on `(?:^|[;&|])`, and a group's `{` is none.

    `{ if false; then export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1; fi; }` credited BOTH
    switches (exit 0) although bash ran neither — measured with `env -i … bash script /usr/bin/env`
    printing nothing. The same for `{ while false; do …; done; }`. The openers are now scanned on
    the PREFIX-STRIPPED view, where the keyword IS at statement start.

    ⛔ The fix is NOT "add `{` to the anchor class": that would make `echo {if,while}` open a
    phantom block and refuse an honest file (pinned by the brace-expansion row below).
    """
    for statement in (
        "{ if false; then export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1; fi; }",
        "{ while false; do export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1; done; }",
    ):
        stated, _ = gate._shell_assignments(statement + "\n" + 'exec "$@"\n')
        assert stated == {}, f"the body never runs, so neither export reaches the process: {statement}"

    expansion, _ = gate._shell_assignments(
        "mkdir -p {if,while}\nexport TORTOISE_VALUE_GATE=1\n" + 'exec "$@"\n'
    )
    assert expansion == {"TORTOISE_VALUE_GATE": "1"}, expansion


def test_a_dockerfile_comment_ending_in_backslash_does_not_swallow_the_next_line():
    """Review P1 (measured) — a Docker COMMENT ends at the newline even when it ends in `\\`.

    The physical-line join did not drop comment lines, so a comment ending in `\\` swallowed the
    NEXT instruction. Both directions measured against `docker build` + `docker inspect`:

      * a swallowed `FROM … AS final` made the gate report the BUILDER stage's `ENV` as the
        image's — `ok … =1 =1` while `docker inspect` showed no `TORTOISE_*` (fail-OPEN);
      * a swallowed honest `ENV` was reported MISSING (false refusal).

    Mutation-proved: removing the comment `continue` reds both rows.
    """
    fail_open = (
        "FROM alpine:3 AS base\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        "# the final stage is assembled separately \\\n"
        "FROM scratch AS final\n"
        'LABEL note="nothing here"\n'
    )
    stated, _ambiguous, _shadowed = gate._dockerfile_env(fail_open)
    assert stated == {}, (
        f"the final stage carries no ENV, so nothing may be credited from the builder: {stated}"
    )

    false_refusal = (
        "FROM alpine:3\n"
        "# the gates are armed here (a note) \\\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        'CMD ["true"]\n'
    )
    stated, _, _ = gate._dockerfile_env(false_refusal)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the comment ends at the newline, so the ENV below it is a real instruction: {stated}"
    )


def test_quoting_is_read_from_the_RAW_text_in_both_directions():
    """Review P1 (measured) — the retraction arms had quoting on ONE side only.

    The `env` arm matched the STRUCTURAL view (quoting blanked), so:
      * a QUOTED name was unrecognisable — `exec env -u "TORTOISE_VALUE_GATE"` printed `ok … =1`
        over a process without K (fail-OPEN), while the bare form was correctly refused;
      * a QUOTED non-empty value read as an EMPTY assignment — `exec env K="1"` was REFUSED
        although the process receives `K=1` (false refusal).
    And the `unset`/`declare` arms matched the raw text, so a `unset K` line INSIDE a multi-line
    string retracted a real export (false refusal: the process receives both).

    Every arm now reads the RAW text and requires its VERB to be CODE in the structural view.
    Mutation-proved: dropping `_statement_is_code` reds the string row; matching the view reds the
    quoted rows.
    """
    quoted_unset, ambiguous = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        'exec env -u "TORTOISE_VALUE_GATE" "$@"\n'
    )
    assert "TORTOISE_VALUE_GATE" not in quoted_unset, quoted_unset
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous

    quoted_value, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        'exec env TORTOISE_VALUE_GATE="1" "$@"\n'
    )
    assert quoted_value == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f'a QUOTED non-empty value states the switch; it is not an emptied one: {quoted_value}'
    )

    in_a_string, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        'echo "to turn it off:\nunset TORTOISE_EMBED_GATE\n"\n' + 'exec "$@"\n'
    )
    assert in_a_string == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a `unset` that is the TEXT of a string is data, not a statement: {in_a_string}"
    )


def test_a_closer_inside_a_function_body_does_not_end_the_body():
    """Review P2 (measured) — `fn_body` counted EVERY closer, including `fi`/`done`/`esac`.

    A balanced `if … fi` inside a helper dropped `fn_body` to 0 while the helper was still open, so
    the helper's `unset K` retracted a TOP-LEVEL export the process does receive — the exact
    property the nested-retraction rule exists to protect. Measured: the gate REFUSED an entrypoint
    both of whose switches reach the daemon, and deleting the `if`/`fi` made it pass.

    Mutation-proved: decrementing `fn_body` by every closer reds this row.
    """
    stated, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        "cleanup() {\n"
        '  if [ -n "${DEBUG:-}" ]; then\n'
        '    echo "debug"\n'
        "  fi\n"
        "  unset TORTOISE_EMBED_GATE\n"
        "}\n" + 'exec "$@"\n'
    )
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the helper is never called, so its `unset` cannot touch the shipped value: {stated}"
    )


# ── the sixth review's four findings (measured, both directions) ──────────────────────────────


@pytest.mark.parametrize(
    "header",
    [
        "arm_gates()",
        "function arm_gates",
        "function arm_gates()",  # review P1: the THIRD legal spelling — was credited
        "function arm_gates ()",
    ],
)
def test_a_function_header_spelling_with_parentheses_is_still_a_BODY(header):
    """Review P1 (measured) — `function NAME()` is a header, and its Allman body never runs.

    `_SHELL_FN_HEADER` had two arms (`function NAME`, `NAME()`), so the third legal spelling —
    `function arm_gates()` / `function arm_gates ()` — matched NEITHER. The lone `{` on the next
    line then read as a group OPENER, a group does not change the depth and IS credited by design,
    and the never-running body's exports were reported `STATED`:

        $ python -c "…_shell_assignments('function arm_gates()\\n{\\n  export …=1\\n…')"
        ({'TORTOISE_VALUE_GATE': '1', 'TORTOISE_EMBED_GATE': '1'}, set())     ← before
        $ env -i bash -c 'function arm_gates()…'                              ← the truth
        (no TORTOISE_* in the environment)

    End-to-end on a temp root: `ok entrypoint.sh … =1 =1`, exit 0, over a process that receives
    neither switch. Mutation-proved: dropping the `\\(\\s*\\)` arm reds the two `function` rows and
    leaves the POSIX ones green.
    """
    stated, ambiguous = gate._shell_assignments(
        f"{header}\n{{\n  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n}}\n"
    )
    assert stated == {}, f"the body of `{header}` is never called, so it states nothing: {stated}"
    assert set(SWITCHES) <= ambiguous, ambiguous


@pytest.mark.parametrize(
    "script",
    [
        # the three measured single-line forked spellings
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } | tee -a /tmp/x\n",
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } |& tee -a /tmp/x\n",
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } & wait\n",
        # …and the multi-line body, whose opener and closer are on DIFFERENT lines
        "{\n  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n} | tee -a /tmp/x\n",
    ],
)
def test_a_FORKED_group_body_is_never_credited(tmp_path, script):
    """Review P1 (measured) — a forked GROUP runs in a subshell, so nothing it exports survives.

    `forked` was computed per STATEMENT segment, and `|`/`&` are not statement separators — so in
    `{ export A; export B; } | tee` the fork sits in a LATER segment than the exports, each export
    saw `forked == False`, and both were credited. Measured against bash, the truth is the
    opposite: `env -i bash` on each of these prints no `TORTOISE_*`. The report then read
    `ok entrypoint.sh … =1 =1`, exit 0.

    The fix is an UNDO LOG per open group: a plain group's exports are still credited (it runs in
    the current shell — the mirror, below), and a forked closer restores the pre-group state.

    Mutation-proved: dropping the `_SHELL_GROUP_FORK` arm reds all four rows; crediting nothing
    from any group reds the unforked mirror.
    """
    entrypoint = tmp_path / "entrypoint.sh"
    entrypoint.write_text("#!/bin/sh\n" + script + 'exec "$@"\n')
    (tmp_path / "Dockerfile").write_text("FROM python:3.12-slim\nENV TORTOISE_VALUE_GATE=1\nENV TORTOISE_EMBED_GATE=1\n")
    _fly(tmp_path)

    stated, _ambiguous = gate._shell_assignments(script)
    assert stated == {}, f"a forked group's exports never reach the process: {stated}"

    channels, failures = gate.evaluate(tmp_path)
    shell = [c for c in channels if c.channel == "entrypoint.sh"]
    assert shell and shell[0].stated == {}, shell
    assert any("entrypoint.sh" in f for f in failures), failures


def test_an_UNFORKED_group_body_is_still_credited_and_a_fork_cannot_retract():
    """The mirror of the row above, BOTH directions — a group that runs in place is a statement.

    `{ export K=1; }` executes in the CURRENT shell, so its export DOES reach the process
    (`env -i bash` on it prints `K=1`) and refusing it would be a false refusal of an honest
    entrypoint. Only the FORK changes the verdict.

    ⛔ The second half is the retraction direction: `{ unset K; } | tee` runs in a subshell, so the
    top-level `export K=1` before it is STILL what the process receives — the unset must be undone
    with the rest of the frame, not left to retract an honest value.
    """
    plain, _ = gate._shell_assignments(
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; }\n"
    )
    assert plain == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"an unforked group runs in the current shell: {plain}"
    )

    retracted_by_a_fork, _ = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
        "{ unset TORTOISE_EMBED_GATE; } | tee -a /tmp/x\n"
    )
    assert retracted_by_a_fork == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"a `unset` inside a forked group cannot touch the parent's export: {retracted_by_a_fork}"
    )


@pytest.mark.parametrize(
    "label,script",
    [
        (
            "inside a function body",
            "arm_gates() {\n  { echo arming; }\n  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\n}\n",
        ),
        (
            "inside a control-flow body",
            "if false; then\n  { echo arming; }\n  export TORTOISE_VALUE_GATE=1\n"
            "  export TORTOISE_EMBED_GATE=1\nfi\n",
        ),
    ],
)
def test_a_group_closer_inside_a_BODY_does_not_end_that_body(label, script):
    """Review P1 at `85217121` (measured against bash before the fix).

    `_SHELL_CLOSERS` matches ANY bare `}`, so the closer of a group nested inside a function or
    control-flow body spent that BODY's depth. Every later sibling then read as top level and was
    credited:

        $ python -c "…_shell_assignments('arm_gates() {\n  { echo arming; }\n  export …=1 …')"
        ({'TORTOISE_VALUE_GATE': '1', 'TORTOISE_EMBED_GATE': '1'}, set())      <- before
        $ env -i PATH=/usr/bin:/bin bash <script> /usr/bin/env | grep TORTOISE  <- the truth
        (nothing: the helper is never called)

    The `}`'s OWNER is what decides — not `fn_body`, which is 1 inside a body and cannot tell a
    group's closer from the body's. `group_open` counts open groups at any nesting; a `}` that
    closes one is not a block closer.

    Mutation-proved: dropping the `group_open` exclusion reds both rows and leaves the plain-group
    and top-level mirrors green.
    """
    stated, ambiguous = gate._shell_assignments(script + 'exec "$@"\n')
    assert stated == {}, f"{label}: a group's `}}` ended the body it sits in: {stated}"
    assert set(SWITCHES) <= ambiguous, (label, ambiguous)

    # …and the BODY's own closer still closes it, so later top-level statements are ordinary.
    after_body, _ = gate._shell_assignments(
        "arm_gates() {\n  { echo arming; }\n}\n"
        "export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n" + 'exec "$@"\n'
    )
    assert after_body == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the body's own `}}` must still close the body: {after_body}"
    )


def test_a_redefined_stage_name_resolves_to_the_MOST_RECENT_stage():
    """Review P2 (measured against a real `docker build`) — `FROM b` means the LAST stage named b.

    `_parent_stage` walked FORWARD and returned the FIRST stage with that name, but Docker resolves
    a stage reference to the most RECENT definition. Measured here with docker 29.1.3/buildkit:

        FROM scratch AS b
        ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1
        FROM scratch AS b          # redefined, carries NO ENV
        FROM b AS final
        → docker image inspect … '{{json .Config.Env}}' → ["PATH=…"]      (no TORTOISE_*)

    so the reader credited both switches (exit 0) over an image that has neither. The mirror
    ordering (the SECOND `b` carries the `ENV`) was a false refusal. Mutation-proved: walking
    forward reds the first row and the second.
    """
    dead_stage_first = (
        "FROM scratch AS b\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        "FROM scratch AS b\n"
        "FROM b AS final\n"
    )
    stated, _ambiguous, shadowed = gate._dockerfile_env(dead_stage_first)
    assert stated == {}, f"the LATER `b` has no ENV, so the shipped image has neither: {stated}"
    assert set(shadowed) == set(SWITCHES), shadowed

    live_stage_second = (
        "FROM scratch AS b\n"
        "FROM scratch AS b\n"
        "ENV TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n"
        "FROM b AS final\n"
    )
    stated, _ambiguous, _shadowed = gate._dockerfile_env(live_stage_second)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the LAST `b` is what `FROM b` inherits: {stated}"
    )


def test_a_trailing_guard_operator_is_consumed_by_the_next_statement():
    """Review P2 (measured) — `carried` was never cleared, so ONE `&&` disarmed the whole file.

    The operator a PREVIOUS line ended with is carried to the next statement so a multi-line guard
    (`[ -n "$X" ] &&` / `  export K=1`) is refused instead of credited. It was re-assigned only
    when a line's LAST segment was blank, so a single continuation set it for every later
    statement in the file:

        [ -n "${STARTUP_LOG:-}" ] &&
          echo "startup log enabled"
        export TORTOISE_VALUE_GATE="${TORTOISE_VALUE_GATE:-1}"
        export TORTOISE_EMBED_GATE="${TORTOISE_EMBED_GATE:-1}"

    → REFUSED with a message blaming the VALUE ("does not state a value this gate can read"),
    while `env -i bash` on the same script exports both. Measured.

    The guard itself must still hold (second half), including across a BLANK line — bash continues
    the compound command there, so a blank line does not consume the operator. Mutation-proved:
    never clearing `carried` reds the first half; clearing it per line reds the blank-line row; not
    carrying it at all reds the multi-line guard.
    """
    honest, _ = gate._shell_assignments(
        '[ -n "${STARTUP_LOG:-}" ] &&\n'
        '  echo "startup log enabled"\n'
        "export TORTOISE_VALUE_GATE=1\n"
        "export TORTOISE_EMBED_GATE=1\n"
    )
    assert honest == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the `&&` guards the `echo`, not the exports two statements later: {honest}"
    )

    guarded, ambiguous = gate._shell_assignments(
        '[ -n "${X:-}" ] &&\n  export TORTOISE_VALUE_GATE=1 &&\n  export TORTOISE_EMBED_GATE=1\n'
    )
    assert guarded == {}, f"a multi-line guard is still not a statement about the value: {guarded}"
    assert set(SWITCHES) <= ambiguous, ambiguous

    across_a_blank_line, ambiguous = gate._shell_assignments(
        '[ -n "${X:-}" ] &&\n\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n'
    )
    # ⛔ Measured against bash, which agrees: the blank line does NOT consume the operator — the
    # FIRST non-blank statement after it is the guarded one (VALUE stays unset) and the next
    # statement is a statement of its own (EMBED is exported).
    assert across_a_blank_line == {"TORTOISE_EMBED_GATE": "1"}, (
        f"a BLANK line does not end the compound command: the next CODE statement is guarded, the "
        f"one after it is not — `env -i bash` prints V=[UNSET] E=[1]: {across_a_blank_line}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous


# ── the seventh review's four findings (measured against bash, both directions) ───────────────


@pytest.mark.parametrize(
    "script",
    [
        # the idiomatic multi-line spelling: the group is the RHS of the guard
        '[ -n "${ARM:-}" ] && {\n'
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "}\n",
        # …and its one-line mirror
        '[ -n "${ARM:-}" ] && { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; }\n',
        # a group inside a branch that never runs
        "if false; then {\n"
        "  export TORTOISE_VALUE_GATE=1\n"
        "  export TORTOISE_EMBED_GATE=1\n"
        "}\nfi\n",
    ],
)
def test_a_group_behind_a_guard_is_never_credited(script):
    """Review P1 (measured) — a group opened on the RHS of `&&` may never run.

    A brace group runs in the CURRENT shell, which is why an unforked group is credited — but only
    when it RUNS. The frame was pushed before the statement's guard was computed, so

        [ -n "${ARM:-}" ] &&
        {
          export TORTOISE_VALUE_GATE=1
          export TORTOISE_EMBED_GATE=1
        }

    credited both switches, exit 0, while `env -i bash` on the same script prints `V=UNSET
    E=UNSET` (ARM is unset, so the group runs zero times). The same hole one-lined credited `EMBED`
    and left `VALUE` ambiguous. The frame now carries its opener's `conditional`, and every
    statement inside a conditional frame is `ambiguous` — the fail-closed verdict.

    Mutation-proved: dropping the `group_conditional` arm reds all three rows; making EVERY group
    conditional reds the unforked mirror in `test_an_UNFORKED_group_body_is_still_credited…`.
    """
    stated, ambiguous = gate._shell_assignments(script)
    assert stated == {}, f"a guarded group may never run, so it states nothing: {stated}"
    assert set(SWITCHES) <= ambiguous, ambiguous


@pytest.mark.parametrize(
    "script",
    [
        # TWO openers on ONE statement, forked OUTER group
        "{ { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; }; } | tee -a /tmp/x\n",
        # the multi-line nesting (was already correct — pinned so it stays)
        "{\n {\n  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n }\n} | tee -a /tmp/x\n",
        # forked INNER group inside a plain outer one
        "{ { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } | tee; }\n",
    ],
)
def test_NESTED_groups_keep_their_frames(script):
    """Review P2 (measured) — the frame stack must follow the BRACES, not the statements.

    The push was a boolean (`startswith("{")` → ONE frame) while every `}` statement pops one, so
    `{ { export …=1; }; } | tee` desynced the stack: the outer group's fork was applied to a frame
    that no longer existed and was silently dropped, leaving both switches `STATED`, exit 0, while
    `env -i bash` on each of these scripts prints no `TORTOISE_*` (the piped group is a subshell).
    Openers and closers are now both COUNTED.

    Mutation-proved: pushing one frame per statement reds the first row (and only that one).
    """
    stated, _ambiguous = gate._shell_assignments(script)
    assert stated == {}, f"no export in a piped group reaches the process: {stated}"


@pytest.mark.parametrize(
    ("trailer", "credited"),
    [
        # redirections do NOT make a group a subshell — bash keeps both switches
        (">&2", True),
        ("1>&2", True),
        ("2>&1", True),
        (">&", True),
        (">| /tmp/f.log", True),
        ("2>| /tmp/f.log", True),
        ("> /tmp/f.log", True),
        ("&> /tmp/f.log", True),
        # …but a redirect followed by a PIPE does
        ("> /tmp/f.log 2>&1 | tee -a /tmp/g.log", False),
        ("2>&1 |& tee", False),
        # ⛔ the `&>`/`&>>` spellings must consume their TARGET before the fork test
        # (review round 3, measured): the arm matched the operator only, so the target was left
        # where the fork operator should be and a piped group was credited over a subshell.
        ("&> /tmp/f.log | tee", False),
        ("&>> /tmp/f.log | tee", False),
        # ⛔ the UNSPACED spellings (review P1 at `ca57c755`, measured): `_SHELL_REDIRECT` tried
        # `>>?|?` BEFORE `>&` and left the target unbounded, so `2>&1` matched as `2>` plus a
        # target that swallowed the pipe — the piped group was CREDITED while bash gave the
        # process no switch. One space before the pipe was refused, so the verdict flipped on
        # whitespace alone.
        ("2>&1|tee -a /tmp/g.log", False),
        (">&2|tee", False),
        ("2>&1|&tee", False),
        ("> /tmp/f.log 2>&1|tee", False),
        ("&>/tmp/f.log|tee", False),
        (">/tmp/f.log|tee", False),
        (">/tmp/f.log&", False),
    ],
)
def test_a_group_redirect_is_not_a_fork(trailer, credited):
    """Review P2 (measured, BOTH directions) — redirects sit between the closer and the operator.

    The first pattern spelled a redirect as `[0-9]*>>?[0-9]*\\s*`, which ate the fd digit of `N>&M`
    and left the `&` to be read as the BACKGROUND operator. Two measured consequences:

      * `{ …; } >&2` was REFUSED (`VALUE_GATE=MISSING`) although `env -i bash` keeps both switches
        — a false refusal of an honest entrypoint;
      * `{ …; } > f 2>&1 | tee` was CREDITED although bash runs the group in a subshell — the
        fail-open, one redirect away from the shape the fork arm exists to catch.

    `_group_is_forked` now strips redirection tokens (reusing `_SHELL_FORK_OPERATOR` for the
    operator test) before asking whether a fork follows. Mutation-proved: the old single regex
    reds both halves.
    """
    script = f"{{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; }} {trailer}\n"
    stated, _ambiguous = gate._shell_assignments(script)
    if credited:
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
            f"a redirection is not a subshell — bash keeps both switches: {stated}"
        )
    else:
        assert stated == {}, f"a redirect followed by a pipe IS a subshell: {stated}"


@pytest.mark.parametrize(
    "label,script,credited",
    [
        # ⛔ TWO closers on ONE statement (review P2 at `ca57c755`, measured): `_SHELL_GROUP_CLOSE`
        # is anchored `^`, so only the first `}` was seen, only one frame popped, and the outer,
        # piped group's credits stood — `ok … =1 =1`, exit 0, over an empty environment.
        (
            "two groups on one line, piped",
            "{ { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } } | tee /tmp/x\n",
            False,
        ),
        (
            "two groups on one line, backgrounded",
            "{ { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } } & wait\n",
            False,
        ),
        # …and the unforked mirror runs in the CURRENT shell, so it IS a statement
        (
            "two groups on one line, unforked",
            "{ { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } }\n",
            True,
        ),
    ],
)
def test_two_group_closers_on_one_statement_close_two_frames(label, script, credited):
    """Review P2 at `ca57c755` (measured both ways) — one statement may close TWO groups.

    Every closer now pops its OWN frame and reads the fork from its own tail, so the inner group
    closes first and the outer one owns the pipe. Mutation-proved: counting closers with the
    anchored `findall` reds the two piped/backgrounded rows and leaves the mirror green.
    """
    stated, _ambiguous = gate._shell_assignments(script + 'exec "$@"\n')
    if credited:
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
            f"{label}: an unforked group runs in the current shell: {stated}"
        )
    else:
        assert stated == {}, f"{label}: a forked outer group is a subshell: {stated}"


def test_a_trailing_PIPE_makes_the_next_statement_a_subshell():
    """Review P3 (measured) — a line ending in `|` continues the pipeline onto the next line.

    `carried` only ever received a statement SEPARATOR, so the trailing-fork case the comment
    claimed was not implemented: `echo hi |` / `  export K=1` / `export K2=1` credited BOTH, while
    `env -i bash` prints `V=UNSET E=1` — only the pipeline's right-hand statement is the subshell,
    and the next statement is a statement of its own.

    Mutation-proved: dropping the `_SHELL_TRAILING_PIPE` arm reds the first half; carrying a
    trailing `&` instead would red the second (bash: `export K=1 &` / `export K2=1` sets K2).
    """
    piped, ambiguous = gate._shell_assignments(
        "echo hi |\n  export TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
    )
    assert piped == {"TORTOISE_EMBED_GATE": "1"}, (
        f"`env -i bash` on this script prints V=UNSET E=1: {piped}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous

    backgrounded, ambiguous = gate._shell_assignments(
        "export TORTOISE_VALUE_GATE=1 &\nexport TORTOISE_EMBED_GATE=1\n"
    )
    assert backgrounded == {"TORTOISE_EMBED_GATE": "1"}, (
        f"a trailing `&` ends the statement; the NEXT statement is not a subshell: {backgrounded}"
    )
    assert "TORTOISE_VALUE_GATE" in ambiguous, ambiguous


def test_the_code_ON_the_allman_body_brace_line_is_inside_the_body():
    """Round 2's residual (measured) — the body brace shares its line with the body's first command.

    `arm_gates()` / `{ export K=1 K2=1` puts code on the brace's own statement, and it was scanned
    BEFORE `opened_fn` raised the depth, so a never-called helper stated BOTH switches on one export
    line and the channel PASSED (exit 0) — `env -i bash` on it runs nothing. The split spelling
    (`{ export K=1` then `  export K2=1`) was already refused because the second line read at
    depth 1; it is pinned in the same test so a fix cannot trade one for the other.

    Mutation-proved: dropping `allman_body` from `conditional` reds the first row only; the plain
    brace GROUP mirror stays credited (a group runs in the current shell), so the two are told
    apart by the HEADER, not by the brace.
    """
    for script in (
        "arm_gates()\n{ export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1\n}\n",
        "arm_gates()\n{ export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n}\n",
    ):
        stated, ambiguous = gate._shell_assignments(script)
        assert stated == {}, f"the helper is never called, so it states nothing: {stated}"
        assert set(SWITCHES) <= ambiguous, ambiguous

    group, _ = gate._shell_assignments(
        "{ export TORTOISE_VALUE_GATE=1 TORTOISE_EMBED_GATE=1; }\n"
    )
    assert group == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the mirror — a brace GROUP runs in the current shell: {group}"
    )


@pytest.mark.parametrize(
    "body",
    [
        # a `{` inside a QUOTED span is data
        """echo '{"status":"armed"}'""",
        # …and so is a brace EXPANSION (a group opener must be followed by a command)
        "mkdir -p {logs,cache}",
        "printf '%s\\n' {1..5}",
        # ⛔ and `{}` (review round 4, measured): an EMPTY pair is followed by `}`, which the first
        # discriminator did not exclude, so `find … -exec … {} +` inside a piped group pushed a
        # phantom frame and the group's fork was never applied.
        "echo {}",
        "find /tmp -maxdepth 0 -exec true {} +",
    ],
)
def test_a_brace_that_is_DATA_does_not_push_a_group_frame(body):
    """Review round 3 (measured) — the opener count ran on the RAW line, so data pushed a frame.

    The count was `_SHELL_GROUP_OPEN.findall(raw)`, which matches a `{` in a quoted string
    (`echo '{"status":"armed"}'`) and the head of a brace EXPANSION (`mkdir -p {logs,cache}`). Each
    pushed a PHANTOM frame on top of the real group's frame, so a forked closer popped the phantom
    and the group's fork was never applied:

        { export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; mkdir -p {logs,cache}; } | tee /tmp/x
        → reader STATED / exit 0        bash: V=UNSET E=UNSET

    The count now runs on the STRUCTURAL view (spans blanked) and requires a REAL opener — a `{`
    followed by anything but a word character, a dot or a comma, which is what a group needs and
    what an expansion never has.

    Mutation-proved: counting on `raw` reds all three rows; requiring `{` + whitespace only would
    red a group whose first command is quoted (`{ "echo" hi; }` is still a group) — the unforked
    mirror below keeps that honest.
    """
    script = (
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; "
        f"{body}; }} | tee /tmp/x\n"
    )
    stated, _ambiguous = gate._shell_assignments(script)
    assert stated == {}, (
        f"the brace in {body!r} is DATA, not a group; the real group is piped and exports nothing: "
        f"{stated}"
    )

    unforked = (
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; "
        f"{body}; }}\n"
    )
    stated, _ambiguous = gate._shell_assignments(unforked)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"the unforked mirror runs in the current shell, so bash keeps both: {stated}"
    )


@pytest.mark.parametrize(
    "prefix",
    [
        # ⛔ the boot-log idiom this repo's own entrypoint would plausibly adopt (review round 4):
        # a redirect-only `exec` does NOT replace the process, the shell continues.
        "exec > >(tee -a /var/log/tortoise-boot.log) 2>&1",
        "exec >> /var/log/tortoise-boot.log 2>&1",
        "exec 2>&1",
        # ⛔ an `exit` that runs only on a failure branch cannot terminate the file.
        '[ -x /usr/bin/env ] || { echo "no env"; exit 1; }',
        # an `exit` inside a helper body the script may never call
        "cleanup() {\n  exit 1\n}",
    ],
)
def test_a_REDIRECT_ONLY_exec_or_a_GUARDED_exit_does_not_terminate_the_file(prefix):
    """Review round 4 (measured, BOTH directions) — `terminated` was set by any `exec`/`exit`.

    `_SHELL_TERMINATES` matched the VERB, so one realistic line prepended to this repo's own
    `entrypoint.sh` turned it RED:

        exec > >(tee -a /var/log/tortoise-boot.log) 2>&1     # a redirect-only `exec`
        export TORTOISE_VALUE_GATE="${TORTOISE_VALUE_GATE:-1}"    # never reached, said the reader

    → `entrypoint.sh mentions TORTOISE_VALUE_GATE but does not state a value this gate can read …
    state it as a literal assignment` — advice the file already follows, with nothing pointing at
    the `exec` line. `env -i bash` sets both switches. The same for an `exit` guarded behind `||`
    or sitting in a helper body.

    A redirect-only `exec` now needs no command WORD (`_terminates_the_shell` strips the
    redirections and the process substitutions first, on RAW text so `exec "$@"` still counts), and
    termination is gated on `retracts` — the provably-runs predicate — so a branch, a body or a
    guarded group cannot end the file. The two true positives are pinned in the same test.

    Mutation-proved: reverting to `_SHELL_TERMINATES.match(raw)` reds all five rows; treating every
    `exec` as harmless reds the `exec "$@"` row.
    """
    script = f"{prefix}\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
    stated, ambiguous = gate._shell_assignments(script)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"bash runs the exports after {prefix!r}, so the reader must credit them: {stated} "
        f"(ambiguous={ambiguous})"
    )


@pytest.mark.parametrize(
    "prefix",
    [
        "exec /usr/bin/env",  # a COMMAND WORD replaces the process
        'exec "$@"',
        "exit 0",
        "exit",
    ],
)
def test_a_real_exec_or_exit_still_terminates_the_file(prefix):
    """The mirror of the row above — the two shapes that DO end the rest of the file.

    Without this, "nothing terminates" would pass while a script that execs a different program
    before exporting the switches was credited for exports that never run.
    """
    script = f"{prefix}\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\n"
    stated, ambiguous = gate._shell_assignments(script)
    assert stated == {}, f"nothing after {prefix!r} runs, so nothing is stated: {stated}"
    assert set(SWITCHES) <= ambiguous, ambiguous


@pytest.mark.parametrize(
    ("label", "line"),
    [
        # ⛔ EXPANSION braces are not group structure (review P1 at `7d046141`, measured): a
        # parameter expansion's `}` popped the real group's frame early, so `group_open` hit 0
        # before the group's own closer, the loop broke, the FORK was never consulted, and a
        # subshell's exports were credited — `ok … =1 =1`, exit 0, over an empty environment.
        ("a parameter default", "${TORTOISE_DB_URI:-}"),
        ("a parameter assign-default", "${X:=}"),
        ("a parameter prefix-strip", "${X#*=}"),
        # …and a brace EXPANSION's `{` pushed a phantom frame, with the same effect the other way.
        ("a find-style exec word", "find /tmp -maxdepth 1 -name '*.tmp' -exec rm {} +"),
        ("a range expansion", "echo {1..5}"),
    ],
)
def test_an_EXPANSION_brace_does_not_disturb_the_group_model(label, line):
    """Review P1 at `7d046141` (measured, both directions) — the brace BOUNDARY is the test.

    The model matched `{`/`}` as bare characters, so an expansion's brace counted as structure. A
    measured verdict flipped on it: a piped group whose body merely MENTIONS `${TORTOISE_DB_URI:-}`
    was CREDITED although bash exports nothing from a subshell, and the same file with that one
    line deleted was refused — so the expansion was the whole cause. The mirror: `find … -exec rm
    {} +` pushed a phantom opener, so a later function body's `}` was spent closing it and an
    honest file was refused.

    A group opener must be followed by WHITESPACE (a brace expansion never is) and a group closer
    must sit at a statement start or after whitespace/separator (bash never ends an expansion so).

    Mutation-proved: reverting either boundary reds this row — the body then either credits a
    piped group or leaks the depth into the following statement.
    """
    script = (
        "{\n  export TORTOISE_VALUE_GATE=1\n  export TORTOISE_EMBED_GATE=1\n"
        f"  echo {line}\n}} | tee /tmp/x\nexec \"$@\"\n"
    )
    stated, _ambiguous = gate._shell_assignments(script)
    assert stated == {}, f"{label}: a piped group exports nothing, whatever it mentions: {stated}"

    # …and the same body UNPIPED is credited, so the boundary is not a widening.
    unpiped = script.replace("} | tee /tmp/x", "}")
    stated, _ambiguous = gate._shell_assignments(unpiped)
    assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
        f"{label}: an unforked group runs in the current shell: {stated}"
    )


def test_a_function_body_closer_may_carry_ANY_redirect():
    """Review P1 at `7d046141` (measured) — `_SHELL_CLOSERS` knew only `N>&M` after a `}`.

    A function body redirected at its closer (`} 2>/dev/null`, `} >/dev/null`, `} >>/tmp/log 2>&1`)
    matched NO closer, so the body never closed, `depth`/`fn_body` stayed up, and every later
    top-level `export` was REFUSED with "mentions … but does not state a value this gate can read" —
    a false refusal whose message blamed the VALUE, and which flipped on the redirect's spelling.

    Mutation-proved: restoring the old `(?:[0-9]*>&[0-9]+)?` arm reds all but the bare row.
    """
    for redirect in ("2>/dev/null", ">/dev/null", ">>/tmp/log 2>&1", "2>&1", ""):
        stated, _ambiguous = gate._shell_assignments(
            "cleanup() {\n  rm -f /tmp/x\n}"
            + (f" {redirect}" if redirect else "")
            + "\nexport TORTOISE_VALUE_GATE=1\nexport TORTOISE_EMBED_GATE=1\nexec \"$@\"\n"
        )
        assert stated == {"TORTOISE_VALUE_GATE": "1", "TORTOISE_EMBED_GATE": "1"}, (
            f"a `}}` carrying `{redirect or 'nothing'}` still closes the body: {stated}"
        )

    # …and a `}` followed by an OPERATOR is still a GROUP's closer, not a block closer.
    piped, _ambiguous = gate._shell_assignments(
        "{ export TORTOISE_VALUE_GATE=1; export TORTOISE_EMBED_GATE=1; } | tee /tmp/x\n"
    )
    assert piped == {}, f"`}} | tee` must stay a fork, not a body close: {piped}"
