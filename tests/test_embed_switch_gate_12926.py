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
