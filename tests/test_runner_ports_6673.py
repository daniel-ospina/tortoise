# tests/test_runner_ports_6673.py
"""#6673: the docker-lane services run on EPHEMERAL host ports.

Two things are pinned here:

1. **The seam** (`tests/_live_utils.py`) — the two port env vars the workflow's
   provision step exports, their defaults, and the URI/probe helpers built on
   them. Before #6673 every live test hardcoded `localhost:6379` /
   `localhost:16379`, so the ports could not move without the suite skipping
   (and the skip-guard, correctly, redding on the availability-regression reason
   family).

2. **The wiring** — no job may go back to a `services:` block. That mechanism
   pins a fixed host port, and on a host where several self-hosted runners share
   ONE Docker daemon a fixed host port is a single global namespace: two
   overlapping services jobs made the second `docker start` fail with
   "Bind for 0.0.0.0:6379 failed: port is already allocated", which aborts the
   runner in its `initialize_containers` pre-step and reds the job in ~9s with
   ZERO tests executed (measured: `test (b)`, `test-track-b`,
   `test-concurrency-falkor`). Each job must therefore provision through
   `.github/actions/falkordb-provision` (ephemeral port + `docker port` read-back)
   and tear down through `.github/actions/falkordb-teardown`.
"""
from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _live_utils  # noqa: E402

PYTHON_CI = ROOT / ".github" / "workflows" / "python-ci.yml"
PMV = ROOT / ".github" / "workflows" / "post-merge-validation.yml"
ACTION_DIR = ROOT / ".github" / "actions"

# (workflow, job, uri_graph expected on the provision step['with'])
PROVISIONED_JOBS = [
    (PYTHON_CI, "test", None),
    (PYTHON_CI, "test-slow", None),
    (PYTHON_CI, "test-concurrency-falkor", "tortoise"),
    (PYTHON_CI, "test-track-b", "tortoise_test_matrix"),
    (PMV, "validate", "tortoise_test_matrix"),
]


def _job(wf_path: Path, job: str) -> dict:
    return yaml.safe_load(wf_path.read_text())["jobs"][job]


# ── 1. the seam ──────────────────────────────────────────────────────────

def test_port_defaults_match_the_historical_literals(monkeypatch):
    monkeypatch.delenv("TORTOISE_TEST_DOCKER_PORT", raising=False)
    monkeypatch.delenv("TORTOISE_TEST_LEGACY_PORT", raising=False)
    monkeypatch.delenv("FALKORDB_PORT", raising=False)
    monkeypatch.delenv("TORTOISE_TEST_DOCKER_HOST", raising=False)
    assert _live_utils.docker_port() == 6379
    assert _live_utils.legacy_port() == 16379
    assert _live_utils.service_host() == "localhost"


def test_provisioned_ports_win_over_the_defaults(monkeypatch):
    """What the workflow's provision step exports must be what the tests use."""
    monkeypatch.setenv("TORTOISE_TEST_DOCKER_PORT", "32768")
    monkeypatch.setenv("TORTOISE_TEST_LEGACY_PORT", "32769")
    assert _live_utils.docker_port() == 32768
    assert _live_utils.legacy_port() == 32769


def test_ports_are_read_at_call_time_not_import_time(monkeypatch):
    """A monkeypatched port must be honoured — the helpers read the env per call.

    (The module-level DOCKER_PORT/LEGACY_PORT constants are import-time
    snapshots for the module-level URI constants that were literals before
    #6673; the workflow exports the env before pytest starts, so both reads
    agree in CI.)
    """
    monkeypatch.setenv("TORTOISE_TEST_DOCKER_PORT", "41001")
    assert _live_utils.docker_port() == 41001
    monkeypatch.setenv("TORTOISE_TEST_DOCKER_PORT", "41002")
    assert _live_utils.docker_port() == 41002


def test_legacy_port_keeps_the_falkordb_port_fallback(monkeypatch):
    """FALKORDB_PORT (the product var test_ingest/test_projection read) stays
    the fallback, but the #6673 provision var wins over it."""
    monkeypatch.delenv("TORTOISE_TEST_LEGACY_PORT", raising=False)
    monkeypatch.setenv("FALKORDB_PORT", "16399")
    assert _live_utils.legacy_port() == 16399  # pre-#6673 behaviour preserved
    monkeypatch.setenv("TORTOISE_TEST_LEGACY_PORT", "32770")
    assert _live_utils.legacy_port() == 32770  # provisioned port wins


def test_module_constants_agree_with_the_accessors():
    assert _live_utils.docker_port() == _live_utils.DOCKER_PORT
    assert _live_utils.legacy_port() == _live_utils.LEGACY_PORT
    assert _live_utils.service_host() == _live_utils.SERVICE_HOST


def test_uri_shapes(monkeypatch):
    monkeypatch.setenv("TORTOISE_TEST_DOCKER_PORT", "32768")
    monkeypatch.setenv("TORTOISE_TEST_LEGACY_PORT", "32769")
    monkeypatch.delenv("TORTOISE_TEST_DOCKER_HOST", raising=False)
    assert _live_utils.docker_uri("g") == "docker://:falkordb@localhost:32768/g"
    assert _live_utils.legacy_uri("g") == "docker://:@localhost:32769/g"
    # the legacy shape is the EMPTY-userinfo form the raw-client probes use
    assert ":@" in _live_utils.legacy_uri("g")
    # host override (the escape hatch a proxy/port-forward would need)
    assert _live_utils.docker_uri("g", host="127.0.0.1") == \
        "docker://:falkordb@127.0.0.1:32768/g"


def _listening_socket() -> tuple[socket.socket, int]:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    return srv, srv.getsockname()[1]


def test_tcp_reachable_true_for_a_listener_and_false_for_a_closed_port():
    srv, port = _listening_socket()
    try:
        assert _live_utils.tcp_reachable(port, host="127.0.0.1", timeout=1.0) is True
    finally:
        srv.close()
    free = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    free.bind(("127.0.0.1", 0))
    dead_port = free.getsockname()[1]
    free.close()
    assert _live_utils.tcp_reachable(dead_port, host="127.0.0.1", timeout=0.3) is False


def test_reachable_helpers_use_the_provisioned_port(monkeypatch):
    """The probes must follow the ephemeral port, not 6379."""
    srv, port = _listening_socket()
    try:
        monkeypatch.setenv("TORTOISE_TEST_DOCKER_PORT", str(port))
        monkeypatch.setenv("TORTOISE_TEST_DOCKER_HOST", "127.0.0.1")
        assert _live_utils.docker_reachable(timeout=1.0) is True
    finally:
        srv.close()


# ── 2. the wiring: no job may pin a fixed host port again ────────────────

@pytest.mark.parametrize(
    ("wf_path", "job_name", "uri_graph"), PROVISIONED_JOBS,
    ids=[f"{p.name}:{j}" for p, j, _ in PROVISIONED_JOBS])
def test_services_bearing_job_provisions_ephemerally(wf_path, job_name, uri_graph):
    job = _job(wf_path, job_name)
    steps = job["steps"]
    uses = [str(s.get("uses", "")) for s in steps]

    assert "services" not in job, (
        f"{job_name}: a `services:` block pins a fixed host port (6379/16379). "
        "On a host shared by several self-hosted runners that is what made "
        "`docker start` fail and the runner abort in `initialize_containers` — "
        "use .github/actions/falkordb-provision (#6673)."
    )
    assert "TORTOISE_DB_URI" not in job.get("env", {}), (
        f"{job_name}: the URI must not be a job-level literal — the host port is "
        "assigned at runtime; pass `uri_graph` to the provision step instead."
    )

    provisions = [i for i, u in enumerate(uses) if u.endswith("falkordb-provision")]
    teardowns = [i for i, u in enumerate(uses) if u.endswith("falkordb-teardown")]
    assert len(provisions) == 1, f"{job_name}: expected exactly one provision step"
    assert len(teardowns) == 1, f"{job_name}: expected exactly one teardown step"

    checkouts = [i for i, u in enumerate(uses) if u == "actions/checkout@v4"]
    assert checkouts and provisions[0] > checkouts[0], (
        f"{job_name}: a local `uses: ./.github/actions/...` needs the checkout to "
        "have run first"
    )
    assert teardowns[0] > provisions[0], f"{job_name}: teardown must follow provision"
    assert steps[teardowns[0]].get("if") == "always()", (
        f"{job_name}: the teardown must run on failure/cancel too"
    )

    with_inputs = steps[provisions[0]].get("with") or {}
    if uri_graph is None:
        assert "uri_graph" not in with_inputs, (
            f"{job_name}: this job must NOT hand the whole suite a URI (the "
            "embedded tests need the redislite default)"
        )
    else:
        assert with_inputs.get("uri_graph") == uri_graph, (
            f"{job_name}: the provision step carries the job's docker-lane URI"
        )


def test_python_ci_jobs_keep_the_v4206_image_pin():
    """Each caller keeps its own image pin — the provision step must not
    silently switch the shared services jobs to another FalkorDB build."""
    for _wf, job_name, _g in PROVISIONED_JOBS:
        if _wf is not PYTHON_CI:
            continue
        job = _job(PYTHON_CI, job_name)
        prov = next(s for s in job["steps"]
                    if str(s.get("uses", "")).endswith("falkordb-provision"))
        assert (prov.get("with") or {}).get("image", "falkordb/falkordb-server:v4.20.6") \
            == "falkordb/falkordb-server:v4.20.6"


@pytest.mark.parametrize("wf_path", [PYTHON_CI, PMV], ids=["python-ci", "pmv"])
def test_no_fixed_port_service_mapping_anywhere(wf_path):
    text = wf_path.read_text()
    assert "    services:" not in text, f"{wf_path.name}: a services: block came back"
    for mapping in ("- 6379:6379", "- 16379:6379"):
        assert mapping not in text, (
            f"{wf_path.name}: the fixed host-port mapping {mapping!r} is the "
            "#6673 collision; the port must be assigned by Docker (-p 0:6379)"
        )


def test_compute_docker_uri_expands_the_provisioned_port():
    """The fast/slow legs build their URI from the assigned port, and a missing
    export must red the step (not silently build `localhost:/graph`)."""
    for job_name in ("test", "test-slow"):
        job = _job(PYTHON_CI, job_name)
        compute = next(s for s in job["steps"]
                       if s.get("name", "").startswith("Compute docker URI"))
        assert "${TORTOISE_TEST_DOCKER_PORT:" in compute["run"], (
            f"{job_name}: the URI must expand the provisioned port with a "
            "fail-loud `:?` guard"
        )
        assert "localhost:6379" not in compute["run"]


# ── 3. the action contract ───────────────────────────────────────────────

def test_provision_script_assigns_and_reads_back_an_ephemeral_port():
    script = (ACTION_DIR / "falkordb-provision" / "provision.sh").read_text()
    assert "-p 0:6379" in script, "the host port must be Docker-assigned"
    assert "docker port " in script, "the assigned port must be read back"
    assert "TORTOISE_TEST_DOCKER_PORT=" in script
    assert "TORTOISE_TEST_LEGACY_PORT=" in script
    assert "TORTOISE_DB_URI=" in script  # gated on the non-empty uri_graph
    assert "redis-cli" in script and "PONG" in script, "health gate required"
    for fixed in ("-p 6379:6379", "-p 16379:6379"):
        assert fixed not in script


def test_provision_script_carries_the_requirepass_pair_and_the_legacy_lane():
    """Behaviour parity with the `services:` blocks this replaces: the same
    --requirepass/--save '' pair, and a passwordless legacy service."""
    script = (ACTION_DIR / "falkordb-provision" / "provision.sh").read_text()
    assert "--requirepass falkordb --save ''" in script
    assert "--save ''" in script
    assert "-a falkordb" in script


def test_teardown_selects_by_ownership_label_and_never_fails_the_job():
    script = (ACTION_DIR / "falkordb-teardown" / "cleanup.sh").read_text()
    assert "tortoise-ci-falkordb=" in script
    assert "docker rm -f" in script
    assert "exit 0" in script
    assert "set -e" not in script, "a teardown must not red the job"


def test_action_inputs_are_declared():
    prov = yaml.safe_load((ACTION_DIR / "falkordb-provision" / "action.yml").read_text())
    assert prov["runs"]["using"] == "composite"
    assert set(prov["inputs"]) == {"image", "legacy", "uri_graph"}
    assert prov["inputs"]["legacy"]["default"] == "true"
    assert prov["inputs"]["uri_graph"]["default"] == ""
    teardown = yaml.safe_load((ACTION_DIR / "falkordb-teardown" / "action.yml").read_text())
    assert teardown["runs"]["using"] == "composite"
