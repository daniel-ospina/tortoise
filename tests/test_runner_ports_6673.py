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

import os
import socket
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import _live_utils  # noqa: E402

PYTHON_CI = ROOT / ".github" / "workflows" / "python-ci.yml"
PMV = ROOT / ".github" / "workflows" / "post-merge-validation.yml"
ACTION_DIR = ROOT / ".github" / "actions"

# (workflow, job, uri_graph expected on the provision step['with'], legacy)
# `legacy` is the EFFECTIVE value: the fast/slow/validate jobs replace a
# two-service `services:` block (passworded + passwordless legacy), while
# test-concurrency-falkor and test-track-b replaced a ONE-service block, so
# they must pass legacy: "false" to keep that shape exactly.
PROVISIONED_JOBS = [
    (PYTHON_CI, "test", None, "true"),
    (PYTHON_CI, "test-slow", None, "true"),
    (PYTHON_CI, "test-concurrency-falkor", "tortoise", "false"),
    (PYTHON_CI, "test-track-b", "tortoise_test_matrix", "false"),
    (PMV, "validate", "tortoise_test_matrix", "true"),
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


def test_the_product_host_var_cannot_move_the_service_host(monkeypatch):
    """Pins the P2 fix. Honouring the product's FALKORDB_HOST made the PROBE
    follow the override while this lane's hardcoded client constructions (the
    `FalkorProjection(host="localhost", …)` sites) did not: the probe passed,
    the docker leg was selected, and the client dialled a dead localhost. The
    product var is therefore not consulted; only the opt-in seam var moves it.
    """
    monkeypatch.delenv("TORTOISE_TEST_DOCKER_HOST", raising=False)
    monkeypatch.setenv("FALKORDB_HOST", "10.1.2.3")
    assert _live_utils.service_host() == "localhost"


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
    ("wf_path", "job_name", "uri_graph", "legacy"), PROVISIONED_JOBS,
    ids=[f"{p.name}:{j}" for p, j, _u, _l in PROVISIONED_JOBS])
def test_services_bearing_job_provisions_ephemerally(wf_path, job_name, uri_graph,
                                                     legacy):
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
    assert str(with_inputs.get("legacy", "true")) == legacy, (
        f"{job_name}: the legacy service must match the `services:` block this "
        f"replaces — expected legacy={legacy!r}, got "
        f"{with_inputs.get('legacy', 'true')!r}. Starting a legacy container a "
        "job never had (or dropping one it did) silently changes which tests "
        "run there."
    )


def test_python_ci_jobs_keep_the_v4206_image_pin():
    """Each caller keeps its own image pin — the provision step must not
    silently switch the shared services jobs to another FalkorDB build."""
    for _wf, job_name, _g, _l in PROVISIONED_JOBS:
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
    assert "TORTOISE_CI_FALKORDB_LABEL" in script, (
        "teardown must select this job's containers by the label provision "
        "exported — never by a run/job/attempt prefix shared by every matrix "
        "shard"
    )
    assert "docker rm -f" in script
    assert "exit 0" in script
    assert "set -e" not in script, "a teardown must not red the job"


def test_ownership_label_is_unique_per_JOB_INSTANCE_not_per_job_id():
    """GITHUB_JOB is the job_id, so a 9-shard matrix shares one
    `run-job-attempt`. Keying the container name/label on that triple alone made
    each shard's `docker rm -f` (and the teardown's label sweep) destroy a PEER
    SHARD's live server — the very collision #6673 removes, moved from the port
    namespace into the name namespace. The instance token must be generated in
    the provision step and exported for the teardown.
    """
    provision = (ACTION_DIR / "falkordb-provision" / "provision.sh").read_text()
    suffix = [ln for ln in provision.splitlines() if ln.startswith("SUFFIX=")]
    assert len(suffix) == 1, "exactly one SUFFIX assignment"
    # an instance token: the shell pid and/or $RANDOM, not just the CI ids
    assert ("$$" in suffix[0]) or ("$RANDOM" in suffix[0]), (
        "the ownership key must include a per-instance token; "
        f"got {suffix[0]!r}"
    )
    assert "TORTOISE_CI_FALKORDB_LABEL=" in provision, (
        "provision must EXPORT the label so teardown can select exactly this "
        "job's containers"
    )
    assert "trap cleanup_own EXIT" in provision, (
        "a provision that fails after `docker run` must remove what it started"
    )
    assert "trap - EXIT" in provision, (
        "and the success path must DISARM that trap — it fires on a normal exit "
        "too, which would delete the containers just started"
    )
    # POSITION matters, and text presence alone does not pin it: a disarm
    # placed BEFORE the second start would leave that container's failure
    # leaking the already-started first one.
    lines = provision.splitlines()
    disarm = next(i for i, ln in enumerate(lines, 1) if ln.strip() == "trap - EXIT")
    starts = [i for i, ln in enumerate(lines, 1) if ln.strip().startswith("start ")]
    assert len(starts) == 2, f"expected two start calls, got {starts}"
    assert disarm > max(starts), (
        f"the disarm (line {disarm}) must come AFTER every start "
        f"({starts}) so a later start's failure still cleans up"
    )

    cleanup = (ACTION_DIR / "falkordb-teardown" / "cleanup.sh").read_text()
    assert "${TORTOISE_CI_FALKORDB_LABEL:-}" in cleanup, (
        "teardown must read the exported per-instance label, never re-derive a "
        "shared run/job/attempt prefix"
    )


def test_provision_logging_never_corrupts_the_captured_port():
    """`start` is called inside $( ) to capture the assigned port on stdout, so
    EVERY diagnostic must go to stderr — a log line on stdout becomes part of
    the port value in $GITHUB_ENV."""
    script = (ACTION_DIR / "falkordb-provision" / "provision.sh").read_text()
    log_def = [ln for ln in script.splitlines() if ln.startswith("log()")]
    assert len(log_def) == 1, "exactly one log() definition"
    assert ">&2" in log_def[0], f"log() must write to stderr: {log_def[0]!r}"
    for ln in script.splitlines():
        if "docker logs" in ln:
            assert "&2" in ln, f"diagnostic must go to stderr: {ln.strip()!r}"


# Every file that reads the #6673 seam must not ALSO hardcode a docker-lane
# port. This is the split-brain guard: migrating a file's *probe* while leaving
# its *consumer* on the dead literal makes the probe pass and the client dial
# nothing, which is worse than not migrating at all. Entries are (path, the
# literal substring), each a shape fixture that never dials.
SEAM_FILES_WITH_ALLOWED_LITERALS = {
    "tests/test_search_engine_gaps.py": [
        # a MOCKED non-local host (the probe is stubbed)
        "docker://:falkordb@test-host:6379/tortoise_test",
    ],
    "tests/test_redirect_seam.py": [
        # is_loopback_uri() classification inputs — pure string predicates
        "docker://:pw@db.internal.example.com:6379",
        "docker://:pw@:6379",
        "{_host_form}:6379",
        "{host}:6379",
    ],
    "tests/test_tripwire.py": [
        # non-loopback refusal fixture (asserted, never dialled)
        "docker://:pw@db.internal.example.com:6379",
    ],
    "tests/test_projection.py": [
        # from_uri() raises on the scheme BEFORE connecting
        'from_uri("localhost:6379")',
        # non-resolvable host: a parse fixture
        'host="example.invalid", port=6379',
    ],
    "tests/test_derived_names.py": [
        # the from_uri census names test_projection's ValueError fixture
        # (a regex string, not a connection target)
        'from_uri\\(\\"localhost:6379',
    ],
    "tests/test_pre_migration_safety.py": [
        # Every localhost literal in this file is an INPUT to
        # _docker_projection_target(), which parses the URI and refuses a
        # non-test graph name — it never constructs a client. (The one real
        # site, the `port=parsed.port or 6379` fallback, is migrated.)
        "docker://:falkordb@localhost:6379",
    ],
}


def _port_literals_in_code(path: Path) -> list[tuple[int, str]]:
    """Every port literal that is a CODE constant in `path`.

    Docstrings are documentation, and comments have no AST node at all, so
    neither counts — only a string or int constant the code actually uses.
    """
    import ast

    src = path.read_text()
    tree = ast.parse(src)
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body:
            first = body[0]
            if (isinstance(first, ast.Expr)
                    and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))

    lines = src.splitlines()
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant):
            continue
        val = node.value
        has = ((isinstance(val, str) and ("6379" in val or "16379" in val))
               or (isinstance(val, int) and val in (6379, 16379)))
        if not has or id(node) in docstrings:
            continue
        line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        hits.append((node.lineno, line))
    return hits


def test_no_migrated_file_still_hardcodes_a_lane_port():
    """A file that reads the #6673 seam must not ALSO hardcode a docker-lane
    port wherever the code can use it.

    This is the split-brain guard: migrating a file's *probe* while leaving its
    *consumer* on the dead literal makes the probe pass and the client dial
    nothing — the failure mode this change exists to remove, one level up.
    """
    seam_files = sorted(
        p for p in (ROOT / "tests").rglob("*.py")
        if "import _live_utils" in p.read_text()
    )
    assert seam_files, "the seam must be imported somewhere"

    offenders: dict[str, list[str]] = {}
    for p in seam_files:
        rel = p.relative_to(ROOT).as_posix()
        if rel == "tests/test_runner_ports_6673.py":
            # the seam's OWN test — it must spell the literals to pin them
            continue
        allowed = SEAM_FILES_WITH_ALLOWED_LITERALS.get(rel, [])
        for lineno, line in _port_literals_in_code(p):
            if any(a in line for a in allowed):
                continue
            offenders.setdefault(rel, []).append(f"{lineno}: {line[:78]}")

    assert not offenders, (
        "a file that resolves ports through tests/_live_utils must not ALSO "
        "hardcode a docker-lane port — the probe would pass while the client "
        "dials a dead 6379/16379. Migrate the site, or declare it in "
        "SEAM_FILES_WITH_ALLOWED_LITERALS with its reason:\n"
        + "\n".join(f"  {k}:\n    " + "\n    ".join(v)
                    for k, v in sorted(offenders.items()))
    )


def test_action_inputs_are_declared():
    prov = yaml.safe_load((ACTION_DIR / "falkordb-provision" / "action.yml").read_text())
    assert prov["runs"]["using"] == "composite"
    assert set(prov["inputs"]) == {"image", "legacy", "uri_graph", "health_timeout"}
    assert prov["inputs"]["legacy"]["default"] == "true"
    assert prov["inputs"]["uri_graph"]["default"] == ""
    teardown = yaml.safe_load((ACTION_DIR / "falkordb-teardown" / "action.yml").read_text())
    assert teardown["runs"]["using"] == "composite"


# ── 3. the actions, executed for real against a stub `docker` ────────────
#
# The host Docker daemon cannot be assumed available on a dev box (and was
# wedged when this was written), so the provision/teardown CONTRACT is pinned
# by executing the real scripts against a stub `docker` on PATH. This is the
# behaviour test the wiring pins above cannot be: it catches a port polluted by
# a stdout log line, a lost EXIT-trap cleanup, and a label shared by two
# concurrent jobs.

STUB_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALLS"
case "$1" in
  run)  if [ "${FAKE_RUN:-ok}" = "fail" ]; then
          echo "Error response from daemon: pull access denied" >&2; exit 125
        fi
        echo "cid-stub" ;;
  port) if [ -n "${FAKE_PORT:-}" ]; then echo "$FAKE_PORT"; exit 0; fi
        case "$2" in *legacy*) echo "0.0.0.0:32769" ;; *) echo "0.0.0.0:32768" ;; esac ;;
  exec) if [ "${FAKE_PING:-PONG}" = "PONG" ]; then echo PONG; else exit 1; fi ;;
  logs) echo "stub logs" ;;
  rm)   exit 0 ;;
esac
exit 0
"""


def _run_provision(tmp_path: Path, *, run_id: str = "42", legacy: str = "true",
                   uri_graph: str = "tortoise_test_matrix",
                   ping: str = "PONG", health_timeout: str = "1",
                   fake_run: str = "ok", fake_port: str = ""
                   ) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "docker").write_text(STUB_DOCKER)
    (bindir / "docker").chmod(0o755)
    # per-call scratch paths: a shared GITHUB_ENV would be truncated by the
    # second call and hide the first call's label
    token = uuid.uuid4().hex[:8]
    calls = tmp_path / f"calls-{token}"
    genv = tmp_path / f"env-{token}"
    genv.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bindir}:{os.environ['PATH']}",
        "DOCKER_CALLS": str(calls),
        "GITHUB_ENV": str(genv),
        "GITHUB_RUN_ID": run_id,
        "GITHUB_JOB": "test",
        "GITHUB_RUN_ATTEMPT": "1",
        "FALKORDB_IMAGE": "falkordb/falkordb-server:v4.20.6",
        "FALKORDB_LEGACY": legacy,
        "FALKORDB_URI_GRAPH": uri_graph,
        "FAKE_PING": ping,
        "FAKE_RUN": fake_run,
        "FAKE_PORT": fake_port,
        "FALKORDB_HEALTH_TIMEOUT": health_timeout,
    }
    proc = subprocess.run(
        ["bash", str(ACTION_DIR / "falkordb-provision" / "provision.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    return proc, genv, calls


def test_provision_exports_exactly_the_assigned_ports(tmp_path):
    proc, genv, _calls = _run_provision(tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "", (
        "provision's stdout must stay EMPTY on success — it is reserved for "
        "workflow commands, and anything printed there has to be assumed "
        f"capturable. Got {proc.stdout!r}"
    )
    lines = genv.read_text().splitlines()
    assert "TORTOISE_TEST_DOCKER_PORT=32768" in lines, lines
    assert "TORTOISE_TEST_LEGACY_PORT=32769" in lines, lines
    assert ("TORTOISE_DB_URI=docker://:falkordb@localhost:32768/"
            "tortoise_test_matrix") in lines, lines
    assert len([ln for ln in lines if ln.startswith("TORTOISE_CI_FALKORDB_LABEL=")]) == 1
    assert proc.stderr, "the progress log belongs on stderr"


def test_provision_omits_the_legacy_port_and_uri_when_not_asked(tmp_path):
    proc, genv, _calls = _run_provision(tmp_path, legacy="false", uri_graph="")
    assert proc.returncode == 0, proc.stderr
    lines = genv.read_text().splitlines()
    assert "TORTOISE_TEST_DOCKER_PORT=32768" in lines
    assert not [ln for ln in lines if ln.startswith("TORTOISE_TEST_LEGACY_PORT=")]
    assert not [ln for ln in lines if ln.startswith("TORTOISE_DB_URI=")]


def test_two_concurrent_jobs_get_DISTINCT_ownership_labels(tmp_path):
    """The matrix-shard defect: a shared label let one shard's `docker rm -f`
    kill a peer's live server."""
    a, genv_a, _ = _run_provision(tmp_path, run_id="42")
    b, genv_b, _ = _run_provision(tmp_path, run_id="42")
    assert a.returncode == 0 and b.returncode == 0
    labels = {
        ln for f in (genv_a, genv_b)
        for ln in f.read_text().splitlines()
        if ln.startswith("TORTOISE_CI_FALKORDB_LABEL=")
    }
    assert len(labels) == 2, f"same run+job must still differ per instance: {labels}"


def test_provision_cleans_up_its_own_containers_when_it_fails(tmp_path):
    proc, _genv, calls = _run_provision(tmp_path, ping="FAIL")
    assert proc.returncode != 0, "a failed health gate must fail the step"
    text = calls.read_text()
    trap_calls = [ln for ln in text.splitlines() if ln.startswith("rm -f ")]
    assert any("falkordb-6673-pw-" in ln and "falkordb-6673-legacy-" in ln
               for ln in trap_calls), (
        "the EXIT trap must remove BOTH containers it started before failing:\n"
        + text
    )


def test_teardown_removes_only_the_recorded_containers(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "docker").write_text(STUB_DOCKER)
    (bindir / "docker").chmod(0o755)
    calls = tmp_path / "td-calls"

    # (a) no label recorded → nothing is claimed (never a shared-prefix sweep)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "DOCKER_CALLS": str(calls), "GITHUB_RUN_ID": "42", "GITHUB_JOB": "test",
           "GITHUB_RUN_ATTEMPT": "1"}
    env.pop("TORTOISE_CI_FALKORDB_LABEL", None)
    proc = subprocess.run(
        ["bash", str(ACTION_DIR / "falkordb-teardown" / "cleanup.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert not calls.exists(), f"no label → no docker call, got: {calls.read_text() if calls.exists() else ''}"

    # (b) with the label → exactly that label is selected, and the exit is 0
    calls.write_text("")
    env["TORTOISE_CI_FALKORDB_LABEL"] = "tortoise-ci-falkordb=42-test-1-999-1234"
    proc = subprocess.run(
        ["bash", str(ACTION_DIR / "falkordb-teardown" / "cleanup.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "label=tortoise-ci-falkordb=42-test-1-999-1234" in calls.read_text()


def test_provision_leaves_its_containers_RUNNING_on_success(tmp_path):
    """The reverse of the failure-path test, and the reason it exists: an EXIT
    trap fires on a NORMAL exit too, so a provision that arms one and never
    disarms it deletes the containers it just started — the step still exports
    the ports and the label, every later step dials a closed port, and the
    availability-skip guards red. Reproduced against the real script before the
    fix.
    """
    proc, genv, calls = _run_provision(tmp_path)
    assert proc.returncode == 0, proc.stderr
    text = calls.read_text()
    launches = [ln for ln in text.splitlines() if ln.startswith("run -d ")]
    removals = [ln for ln in text.splitlines() if ln.startswith("rm -f ")]
    assert len(launches) == 2, f"both services must be started:\n{text}"

    # the only removal the success path may perform is the pre-clean of each
    # name (one name per call) — never a call that removes both
    for ln in removals:
        assert not ("falkordb-6673-pw-" in ln and "falkordb-6673-legacy-" in ln), (
            "a successful provision must NOT remove both containers — the "
            "EXIT trap was left armed:\n" + text
        )
    assert [ln for ln in text.splitlines() if ln.startswith("port ")], (
        "both ports must be read back on the success path"
    )
    # and the ports the job will use are the ones that were read back
    lines = genv.read_text().splitlines()
    assert "TORTOISE_TEST_DOCKER_PORT=32768" in lines
    assert "TORTOISE_TEST_LEGACY_PORT=32769" in lines


def test_provision_emits_a_real_error_annotation_on_failure(tmp_path):
    """GitHub renders `::error::` only for a workflow command that STARTS the
    line — so it must not go through log() (prefixed) and must not be eaten by
    the port capture."""
    proc, _genv, _calls = _run_provision(tmp_path, ping="FAIL")
    assert proc.returncode != 0
    assert any(ln.startswith("::error::") for ln in proc.stdout.splitlines()), (
        "the failure must be a real annotation on stdout:\n" + proc.stdout
    )


def test_provision_refuses_an_uri_graph_that_could_inject_into_GITHUB_ENV(tmp_path):
    """$GITHUB_ENV is line-delimited, so a newline in `uri_graph` appends
    arbitrary env vars for every later step of the job. All five callers pass a
    literal today, so this is latent — but the action is reusable, and the
    refusal must happen BEFORE anything is created (nothing to clean up).
    """
    proc, genv, calls = _run_provision(tmp_path, uri_graph="evil\nINJECTED=1")
    assert proc.returncode != 0, "a non-identifier graph name must be refused"
    assert any(ln.startswith("::error::") for ln in proc.stdout.splitlines()), proc.stdout
    assert not calls.exists() or not calls.read_text().strip(), (
        "nothing may be started, so there is nothing to clean up"
    )
    assert "INJECTED=1" not in genv.read_text()
    # and the ordinary graph names the five callers use must still be accepted
    for good in ("tortoise", "tortoise_test_matrix"):
        # the helper's per-call token-scoped scratch files make a shared
        # tmp_path safe
        ok, genv2, _ = _run_provision(tmp_path, uri_graph=good)
        assert ok.returncode == 0, (good, ok.stderr)
        assert f"TORTOISE_DB_URI=docker://:falkordb@localhost:32768/{good}" in genv2.read_text()


def test_provision_annotates_a_docker_run_failure(tmp_path):
    """`set -e` would abort with docker's stderr as the only trace; the contract
    is that every failure of this step is a visible annotation."""
    proc, _genv, calls = _run_provision(tmp_path, fake_run="fail")
    assert proc.returncode != 0
    assert any(ln.startswith("::error::") for ln in proc.stdout.splitlines()), (
        proc.stdout + proc.stderr
    )
    # and it cleaned up after itself (the pre-clean of each name, then the trap)
    assert any(ln.startswith("rm -f ") for ln in calls.read_text().splitlines())


def test_provision_refuses_a_non_numeric_port(tmp_path):
    """The port is exported into $GITHUB_ENV and interpolated into a URI, so a
    truncated or error-shaped `docker port` line must not be written there."""
    proc, genv, _calls = _run_provision(tmp_path, fake_port="0.0.0.0:notaport")
    assert proc.returncode != 0
    assert any(ln.startswith("::error::") for ln in proc.stdout.splitlines()), proc.stdout
    assert "TORTOISE_TEST_DOCKER_PORT" not in genv.read_text()
