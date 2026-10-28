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

import ast
import importlib
import inspect
import os
import re
import socket
import subprocess
import sys
import types
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

# The one engine pin every provisioned job resolves to (#6386). Named once so
# the guard cannot disagree with itself about which version is canon.
FALKORDB_PIN = "falkordb/falkordb-server:v4.20.6"

#: Compose files that can publish a FalkorDB. Enumerated rather than
#: `rglob`-ed: the repo root carries `.worktrees/` checkouts whose compose files
#: this guard does not own.
COMPOSE_GLOBS = (
    "docker-compose*.yml", "docker-compose*.yaml",
    "compose*.yml", "compose*.yaml",
    "apps/*/docker-compose*.yml", "apps/*/docker-compose*.yaml",
    "integrations/*/*/docker-compose*.yml",
    "integrations/*/*/docker-compose*.yaml",
)

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
    """Pins the P2 fix. Honouring the product's FALKORDB_HOST moved the PROBE
    but not the client constructions written against the historical
    `localhost`: the probe passed, the docker leg was selected, and the client
    dialled a dead localhost. The product var is therefore not consulted; only
    the opt-in seam var moves it.
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


def test_every_provisioned_job_pins_the_v4206_image():
    """#6386: EVERY provisioned job must test the SAME immutable engine pin.

    The failure this prevents is measured, not hypothetical. #6673 replaced
    this repo's `services:` blocks with `.github/actions/falkordb-provision`
    and carried each block's image over verbatim, so `post-merge-validation`
    kept `falkordb/falkordb-server:latest` while python-ci moved to `v4.20.6`.
    The tag then floated to engine 6.x, whose `reply.rs` panic killed the
    container mid-suite: on main, run 38494033413 (2026-10-14), the validate
    job failed at `114 passed, 0 failed` with `Error 111 connecting to
    localhost:32768. Connection refused`, and teardown then found ONE
    container where the action had started two. A post-merge lane running a
    different engine from the pre-merge gate reports engine drift as a
    product regression on an unchanged tree.

    Two holes are closed, because one assertion cannot see both:

    1. a CALLER must not override the pin with a floating tag — this is the
       pmv site above, and the assertion that used to exist here skipped it
       (`if _wf is not PYTHON_CI: continue`);
    2. the ACTION DEFAULT is a pin too — the four python-ci jobs pass no
       `image` at all, so a default that moved would silently move the gate's
       engine with no commit to this repo. Its value is therefore read from
       action.yml, never assumed.
    """
    prov_action = yaml.safe_load(
        (ACTION_DIR / "falkordb-provision" / "action.yml").read_text())
    action_default = prov_action["inputs"]["image"]["default"]
    assert action_default == FALKORDB_PIN, (
        "the action default is the engine every job that passes no `image` "
        f"runs against — expected {FALKORDB_PIN!r}, got {action_default!r}"
    )

    for wf_path, job_name, _g, _l in PROVISIONED_JOBS:
        job = _job(wf_path, job_name)
        prov = next(s for s in job["steps"]
                    if str(s.get("uses", "")).endswith("falkordb-provision"))
        image = (prov.get("with") or {}).get("image", action_default)
        assert image == FALKORDB_PIN, (
            f"{wf_path.name}:{job_name} provisions {image!r}, not the CI pin "
            f"{FALKORDB_PIN!r}. Every lane must test the engine the gate "
            "approved: a floating `:latest` moved to engine 6.0.0 underneath "
            "an unchanged tree and its panic killed the container mid-suite "
            "(#6386)."
        )


@pytest.mark.parametrize("wf_path", [PYTHON_CI, PMV], ids=["python-ci", "pmv"])
def test_no_fixed_port_service_mapping_anywhere(wf_path):
    text = wf_path.read_text()
    assert "    services:" not in text, f"{wf_path.name}: a services: block came back"
    for mapping in ("- 6379:6379", "- 16379:6379"):
        assert mapping not in text, (
            f"{wf_path.name}: the fixed host-port mapping {mapping!r} is the "
            "#6673 collision; the port must be Docker-assigned and "
            "loopback-bound (-p 127.0.0.1:0:6379)"
        )


def test_published_falkordb_compose_ports_bind_loopback():
    """#7637: a compose-published FalkorDB must bind 127.0.0.1, never 0.0.0.0.

    The CI provisioner's `-p 0:6379` was the reported site (fixed in
    `provision.sh`, pinned by
    `test_provision_script_assigns_and_reads_back_an_ephemeral_port` below).
    Compose files are the other place a host port is published, and a bare
    `"16379:6379"` binds 0.0.0.0 — an unauthenticated FalkorDB on the LAN of
    every lane/dev machine. Globs are enumerated rather than `rglob`-ed: the
    repo root carries `.worktrees/` checkouts whose compose files this change
    does not own.
    """
    globs = COMPOSE_GLOBS
    compose_files = sorted({p for g in globs for p in ROOT.glob(g) if p.is_file()})
    assert compose_files, f"no compose file matched {globs} — the guard is vacuous"

    checked = 0
    for path in compose_files:
        doc = yaml.safe_load(path.read_text()) or {}
        for service, svc in (doc.get("services") or {}).items():
            if "falkordb" not in str((svc or {}).get("image", "")).lower():
                continue
            for entry in (svc.get("ports") or []):
                checked += 1
                assert str(entry).startswith("127.0.0.1:"), (
                    f"{path.relative_to(ROOT)}: service {service!r} publishes "
                    f"FalkorDB port {str(entry)!r} without a `127.0.0.1` host "
                    "IP — Docker binds 0.0.0.0 and the DB is LAN-reachable "
                    "(#7637)"
                )
    assert checked, (
        "no compose file publishes a FalkorDB port — the guard no longer "
        "covers the services it exists for"
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
    # The host port must be Docker-assigned (#6673) AND loopback-bound (#7637).
    # A bare `-p 0:6379` — the pre-#7637 form — leaves the host address unset,
    # which Docker resolves to 0.0.0.0, exposing this unauthenticated (legacy
    # service: passwordless) test DB on the LAN of every lane/dev machine.
    # Read the docker publish ARGUMENTS, not the prose: a whole-file substring
    # scan would also match the explanatory comment that NAMES the bad form.
    publishes = [
        ln.strip()[len("-p "):].rstrip("\\").strip()
        for ln in script.splitlines()
        if ln.strip().startswith("-p ")
    ]
    assert publishes == ["127.0.0.1:0:6379"], (
        "the provisioner's docker publish must be the loopback-bound, "
        f"Docker-assigned form; got {publishes!r}"
    )
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
    "tests/test_wipe_server.py": [
        # Every literal here but ONE is a pure INPUT to the journal-instance
        # gate — a URI parser (`_uri_instance_key`), the sidecar text it
        # binds/reads, or a census row — and never a connection target.
        #
        # The exception is the ONE `_stale_sweep` call handed a literal URI
        # (declared below as its full statement, never a URI prefix: a sweep of
        # a DIFFERENT graph name on the same dialect IS a live target, since
        # `FalkorProjection.from_uri` folds an empty host to `localhost`). It is
        # safe on two independent counts — its journal is bound to
        # `127.0.0.1:6379` while this URI's instance key is `None`, so the
        # instance gate skips the journal before any projection, AND
        # `_proj_for_uri` is monkeypatched to `_FakeProj` on the neighbouring
        # lines. Every other sweep in the file takes its target from
        # `_live_utils.docker_base_uri` (#9359).
        #
        # They must stay literal: these assertions pin the DEFAULT-port folding
        # the instance key exists for — `localhost` → `127.0.0.1`, portless →
        # 16379 — so deriving the port from the seam would delete what they
        # test, and the seam is where a wrong fold would come from.
        "docker://:falkordb@localhost:6379/g",
        "docker://:falkordb@127.0.0.1:6379/g",
        "docker://:falkordb@127.0.0.1/g",
        "_journal_instance(str(journal))",
        r'"127.0.0.1:6379\n")',
        'write_text("127.0.0.1:16379',
        "bound to 127.0.0.1:6380, this sweep targets",
        'res = _stale_sweep("docker://:falkordb@:6379/tortoise_test_matrix")',
    ],
}


def _quoted_program(value: str) -> bool:
    """True when `value` is SOURCE CODE, not a connection target (#10218).

    A test can carry a url literal for a file it only PARSES: `test_derived_names`
    builds a `("def test_x():\n" "    proj = _FP.from_uri(\"bolt://…6379\")\n")`
    block, writes it to a tmp_path, and hands it to a census. The file never
    dials it — the port is the thing under test. Looking for the port alone
    cannot separate that from a real dial site, so ask the narrower question:
    does the WHOLE constant parse as a python module that DEFINES something?

    A dial site cannot answer yes by mistake: a URI carries `://`, which is not
    a python expression, so `ast.parse` raises. This is deliberately not a
    substring test (`"def" in value`) — a quoted program is required to DEFINE
    a function, class or import, which an identifier merely spelling "def"
    does not.

    ⛔ SCOPE, stated so this predicate is not trusted further than it goes
    (#10150 round-10 review): a fixture that WRITES such a program and then
    EXECUTES it is out of scope — this predicate cannot tell that from a
    fixture the census merely parses, and it answers "quoted program" for
    both. No site in the tree does it today (the guard's own 18-entry allow
    list was re-run: none goes inert); closing it needs the write/exec edge,
    not a better string test.
    """
    try:
        parsed = ast.parse(value)
    except (SyntaxError, ValueError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Import, ast.ImportFrom))
        for node in ast.walk(parsed)
    )


def _port_literals_in_code(path: Path) -> list[tuple[int, str]]:
    """Every port literal that is a CODE constant in `path`.

    Docstrings are documentation, and comments have no AST node at all, so
    neither counts — only a string or int constant the code actually uses.
    """
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
        if isinstance(val, str) and _quoted_program(val):
            continue  # a fixture that QUOTES source is not a file that dials
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


def test_a_quoted_program_is_not_a_dial_site(tmp_path):
    """#10218: the fix, both directions — a false positive removed, and the
    true positive it exists for still detected.

    `test_derived_names.py` quotes test source (a small module whose text carries
    a `bolt://…:6379` call) and writes it to a tmp file for a census to PARSE.
    The guard read that as "this file dials the lane" and reded `test (i)` on
    `main` for every PR. The mutation half is the point: the same literal as a
    real constant must still red, or this fix would have bought green by
    blinding the guard.
    """
    quoting = tmp_path / "test_quotes_source.py"
    quoting.write_text(
        'import _live_utils  # noqa: F401\n'
        'QUOTED = (\n'
        '    "def test_inner():\\n"\n'
        '    "    proj = _FP.from_uri(\\"bolt://localhost:6379\\")\\n"\n'
        ')\n',
        encoding="utf-8")
    assert _port_literals_in_code(quoting) == [], (
        "a fixture that quotes a program must not count as code that dials")

    dialling = tmp_path / "test_dials.py"
    dialling.write_text(
        'import _live_utils  # noqa: F401\n'
        'PROJ = _FP.from_uri(\'bolt://localhost:6379\')\n',
        encoding="utf-8")
    hits = _port_literals_in_code(dialling)
    assert [ln for ln, _ in hits] == [2], (
        f"the same literal as a REAL constant must still red, got {hits}")


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
  image) exit "${FAKE_IMAGE_RC:-0}" ;;
  pull)  if [ -n "${FAKE_PULL_ONLY_LAST:-}" ]; then
           # The pattern list is written IN SOURCE on purpose: bash does not
           # re-parse a `|` that arrives via a variable expansion, so a pattern
           # injected through the environment silently matches nothing.
           case "$2" in
             mirror.gcr.io/*|ghcr.io/*) echo "Error response from daemon: pull access denied" >&2; exit 1 ;;
           esac
         fi
         exit "${FAKE_PULL_RC:-0}" ;;
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
                   fake_run: str = "ok", fake_port: str = "",
                   fake_image_rc: str = "0", fake_pull_rc: str = "0",
                   fake_pull_only_last: str = "",
                   image: str = "falkordb/falkordb-server:v4.20.6",
                   repo_owner: str = ""
                   ) -> tuple[subprocess.CompletedProcess, Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
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
        "FALKORDB_IMAGE": image,
        "GITHUB_REPOSITORY_OWNER": repo_owner,
        "FALKORDB_LEGACY": legacy,
        "FALKORDB_URI_GRAPH": uri_graph,
        "FAKE_PING": ping,
        "FAKE_RUN": fake_run,
        "FAKE_PORT": fake_port,
        "FAKE_IMAGE_RC": fake_image_rc,
        "FAKE_PULL_RC": fake_pull_rc,
        "FAKE_PULL_ONLY_LAST": fake_pull_only_last,
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


def test_resolver_tries_mirror_then_ghcr_then_docker_hub_last(tmp_path):
    """#7867: a cold runner must resolve through mirror.gcr.io -> GHCR -> the
    Docker Hub reference AS GIVEN. The upstream tier is the point: the job must
    degrade to today's behaviour rather than fail closed on a missing mirror."""
    proc, genv, calls = _run_provision(
        tmp_path, fake_image_rc="1",
        fake_pull_only_last="1",
    )
    assert proc.returncode == 0, proc.stderr
    pulls = [ln for ln in calls.read_text().splitlines() if ln.startswith("pull ")]
    assert len(pulls) == 3, pulls
    assert pulls[0] == "pull mirror.gcr.io/falkordb/falkordb-server:v4.20.6", pulls
    assert pulls[1] == "pull ghcr.io/daniel-ospina/falkordb-server:v4.20.6", pulls
    assert pulls[2] == "pull falkordb/falkordb-server:v4.20.6", pulls
    assert "pulled falkordb/falkordb-server:v4.20.6" in proc.stderr
    assert "TORTOISE_TEST_DOCKER_PORT=32768" in genv.read_text().splitlines()


def test_warm_image_short_circuits_every_pull(tmp_path):
    """The whole defect is a quota on PULLS: a runner that already has the image
    (across ANY candidate) must not touch the network at all."""
    proc, _genv, calls = _run_provision(tmp_path)  # FAKE_IMAGE_RC defaults to warm
    assert proc.returncode == 0, proc.stderr
    invoked = calls.read_text()
    assert "pull" not in invoked, invoked
    assert "already present locally" in proc.stderr


def test_resolver_fails_closed_and_attributably_when_every_tier_is_dark(tmp_path):
    """Full exhaustion must name what it tried on stdout as a workflow command and
    start NO container — an un-attributable failure is exactly what #7867 is about."""
    proc, _genv, calls = _run_provision(tmp_path, fake_image_rc="1", fake_pull_rc="1")
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout.startswith("::error::"), proc.stdout
    assert "could not obtain" in proc.stdout
    assert "mirror.gcr.io/falkordb/falkordb-server:v4.20.6" in proc.stdout
    invoked = calls.read_text()
    assert not [ln for ln in invoked.splitlines() if ln.startswith("run ")], invoked


def test_slashless_reference_still_resolves_through_every_tier(tmp_path):
    """Docker's own rule treats a first path component as a registry host only
    when a `/` separates it, so `redis:7` is Docker Hub `redis`, tag `7`.
    Without the slash test the reference is misread as already-qualified and the
    mirror tiers are skipped — which is what mutating that guard produces."""
    proc, _genv, calls = _run_provision(
        tmp_path, image="redis:7", fake_image_rc="1", fake_pull_only_last="1",
    )
    assert proc.returncode == 0, proc.stderr
    pulls = [ln for ln in calls.read_text().splitlines() if ln.startswith("pull ")]
    assert len(pulls) == 3, pulls
    assert pulls[0] == "pull mirror.gcr.io/redis:7", pulls
    assert pulls[1] == "pull ghcr.io/daniel-ospina/redis:7", pulls
    assert pulls[2] == "pull redis:7", pulls


def test_ghcr_candidate_derives_a_lowercased_owner(tmp_path):
    """The mirror pushes under `github.repository_owner` and this resolver derives
    its own path: if the two diverge the durable GHCR home silently never serves.
    GHCR also rejects an uppercase path, so the derivation must lowercase."""
    proc, _genv, calls = _run_provision(
        tmp_path, repo_owner="Some-Org", fake_image_rc="1", fake_pull_only_last="1",
    )
    assert proc.returncode == 0, proc.stderr
    pulls = [ln for ln in calls.read_text().splitlines() if ln.startswith("pull ")]
    assert pulls[1] == "pull ghcr.io/some-org/falkordb-server:v4.20.6", pulls


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
    bindir.mkdir(parents=True, exist_ok=True)
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


# ── 4. the engine PIN reaches every docker-lane entry point (#8124) ───────
#
# #6386 pinned the CI half of the container-image class (every provisioned job
# runs FALKORDB_PIN). #8124 is the same class on the LANE-TOOLING half: tools
# and compose files that START a FalkorDB container resolved the floating
# `falkordb/falkordb:latest`, which now points at engine 6.0.x. That engine
# PANICS the server (`src/reply.rs:655` in `reply::reply_compact`, "called
# `Option::unwrap()` on a `None` value") on a query shape the suite really
# issues — `OPTIONAL MATCH (s:Session) RETURN count(s) AS n, s.harness AS h`,
# i.e. an OPTIONAL MATCH projecting an AGGREGATE column beside a PROPERTY one.
# The server process EXITS, so the lane does not fail like a bad assertion: it
# fails like `redis.exceptions.ConnectionError: Connection closed by server`,
# which the DB-unreachable session guard reports as "no TORTOISE_DB_URI
# database could be reached" (the #6073 wedge class) — and a lane pointed at a
# SHARED instance takes the DB down for every peer mid-run.
#
# MEASURED 2026-10-19, one disposable container per reading: module 60002
# (`:latest`) and module 60001 (`6.0.1`) both panic and exit (1); module 42006
# (`falkordb-server:v4.20.6`) and 42004 (`v4.20.4`) answer the same query and
# stay Up. Pinning the APP-image family was therefore NOT sufficient — every
# 6.0.x tag carries the panic.

#: Docker-lane entry points that can START a FalkorDB container, enumerated
#: (never `rglob`-ed, for the same `.worktrees/` reason as COMPOSE_GLOBS). A
#: new entry point belongs here — that is what makes "0 lane entry points
#: resolve a floating tag" a checkable statement instead of a claim.
LANE_ENTRY_POINTS = (
    ROOT / "tools" / "test_lane.py",
    ROOT / "tools" / "edge_census.py",
    ROOT / "graph-scripts" / "setup.py",
)

#: A reference whose tag is `latest*` or an `edge*` build — the floating FAMILY,
#: not an enumerated set (upstream also publishes `latest-6.0.1`, `edge-ts`,
#: `edge-x64`, `edge-rhel9-x64`, `edge-alpine`, …). `.github/workflows/
#: mirror-images.yml` deliberately mirrors upstream `:latest` into the
#: pull-through mirror — it SELECTS nothing a lane runs, so it is out of this
#: guard on purpose; every path listed in LANE_ENTRY_POINTS and every compose
#: file is in it. `tools/test_lane.py::is_floating_image` is the seam's own
#: predicate and must agree with this one
#: (pinned by test_the_seam_and_the_lane_sweep_classify_floating_tags_the_same_way).
_FLOATING_REF = re.compile(r":(?:latest|edge)[a-z0-9._-]*$", re.IGNORECASE)
_IMAGE_REF = re.compile(r"falkordb/falkordb[a-z-]*", re.IGNORECASE)


def _is_floating(ref: str) -> bool:
    """True for `:latest`, an `edge*` build, or NO tag at all.

    An untagged reference is included deliberately: Docker reads it as
    `:latest`, so `image: falkordb/falkordb` floats exactly the same and is the
    spelling a "pin" is most likely to be mistyped into.
    """
    if "@sha256:" in ref:               # a digest IS the pin
        return False
    if ":" not in ref.rsplit("/", 1)[-1]:
        return True                     # no tag = implicitly :latest
    return bool(_FLOATING_REF.search(ref))


def _code_string_constants(path: Path) -> list[str]:
    """The string constants a module can actually PASS TO DOCKER.

    Docstrings are excluded: the fix for #8124 is mostly prose that must be able
    to NAME the floating tag it forbids, and a text scan over the file would
    red on its own explanation. A docstring is a `Constant(str)` that is the
    first statement of a module/class/function body, which `ast` can tell apart
    from a value a call site uses.
    """
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        first = (getattr(node, "body", None) or [None])[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def _lane_image_references() -> list[tuple[str, str]]:
    """(location, reference) for every place a lane entry point starts a DB."""
    refs: list[tuple[str, str]] = []
    for path in LANE_ENTRY_POINTS:
        # Every entry point is Python today. A new one in another language must
        # not slip past silently — that is a loud failure, not a silent skip.
        assert path.suffix == ".py", (
            f"{path.relative_to(ROOT)}: this guard only reads .py entry points — "
            "teach it how to read this file type before adding it to "
            "LANE_ENTRY_POINTS"
        )
        for ref in _code_string_constants(path):
            if _IMAGE_REF.search(ref):
                refs.append((str(path.relative_to(ROOT)), ref))
    for path in sorted({p for g in COMPOSE_GLOBS
                        for p in ROOT.glob(g) if p.is_file()}):
        doc = yaml.safe_load(path.read_text()) or {}
        for service, svc in (doc.get("services") or {}).items():
            image = str((svc or {}).get("image", "") or "")
            if _IMAGE_REF.search(image):
                refs.append((f"{path.relative_to(ROOT)}:{service}", image))
    return refs


def _entry_point_references() -> dict[str, list[str]]:
    """{entry-point relpath: the image refs THAT file passes to docker}.

    Per FILE, not a count across sources: compose files also carry image refs,
    so a total cannot tell which entry point contributed one (#9085 review).
    """
    by_file: dict[str, list[str]] = {}
    for where, ref in _lane_image_references():
        by_file.setdefault(where, []).append(ref)
    return {str(p.relative_to(ROOT)): by_file.get(str(p.relative_to(ROOT)), [])
            for p in LANE_ENTRY_POINTS}


def _subparser_actions(parser):
    """Every argparse action of `parser` AND of each of its subparsers."""
    found = []
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            for sub in choices.values():
                found.extend(sub._actions)
                found.extend(_subparser_actions(sub))
    return found


def _load_setup_module():
    """Import `graph-scripts/setup.py` — a hyphenated dir, not importable by name.

    Module level only defines constants and functions (plus a version guard), so
    loading it runs no docker call.
    """
    import importlib.util

    path = ROOT / "graph-scripts" / "setup.py"
    spec = importlib.util.spec_from_file_location("graph_scripts_setup_9085", path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_step8(monkeypatch, *, status: str, created_image: str):
    """Run `setup.step8()` against a fake docker, returning the argv it issued.

    `socket` is replaced on the module so the readiness loop answers on the
    first attempt and no real connection is made.
    """
    setup = _load_setup_module()
    calls: list[list[str]] = []
    monkeypatch.setattr(setup, "MODE", "docker")
    monkeypatch.setattr(setup, "_container_status", lambda _n: status)
    monkeypatch.setattr(setup, "_container_image", lambda _n: created_image)
    # #7357: the reuse paths read the container's memory limit. Stubbed so this
    # helper stays HERMETIC — an unstubbed read shells out to a real docker with
    # no timeout, and the test would hang on a wedged daemon (#7357 review F9).
    monkeypatch.setattr(setup, "_container_memory_limit", lambda _n: 2 << 30)
    monkeypatch.setattr(setup, "_port_collision", lambda _p: None)
    monkeypatch.setattr(setup, "_run", lambda cmd, **kw: calls.append(cmd))

    class _Sock:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, _t):
            pass

        def connect(self, _addr):
            pass

        def close(self):
            pass

    monkeypatch.setattr(setup, "socket", types.SimpleNamespace(
        socket=_Sock, AF_INET=socket.AF_INET, SOCK_STREAM=socket.SOCK_STREAM))
    setup.step8()
    return setup, calls


def test_setup_recreates_an_existing_container_off_the_pin(monkeypatch):
    """#9085 review: the pin must reach an EXISTING container, not only a new one.

    `FALKORDB_IMAGE` is consumed by the `docker run` that happens ONLY on the
    `missing` path. A container created before the pin existed — from the then
    floating `falkordb/falkordb:latest` — keeps its engine ID for life, because
    `docker start` never re-resolves an image; under `--restart unless-stopped`
    that is the panicking 6.0.x engine restarted forever, on the container MOST
    likely to be broken (it is the one that crashed). Recreate it on the pin.
    """
    for status in ("running", "exited"):
        setup, calls = _run_step8(monkeypatch, status=status,
                                  created_image="falkordb/falkordb:latest")
        rm = [c for c in calls if c[:2] == ["docker", "rm"]]
        assert rm and rm[0][-1] == setup._FALKORDB_CONTAINER, (
            f"a {status} container created from `:latest` was not recreated "
            f"(docker calls: {calls}) — it keeps the panicking engine for life"
        )
        run = [c for c in calls if c[:2] == ["docker", "run"]]
        assert run, f"nothing recreated the container on the pin (calls: {calls})"
        assert setup.FALKORDB_IMAGE in run[-1], (
            f"the recreated container must name the pin, got {run[-1]}"
        )
        assert not [c for c in calls if c[:2] == ["docker", "start"]], (
            "`docker start` on the old container re-uses its original engine — "
            f"that is the bug, not the fix (calls: {calls})"
        )


def test_setup_keeps_an_existing_container_that_already_runs_the_pin(monkeypatch):
    """The recreation must not fire on the happy path — a pinned container is
    reused as-is (running) or restarted (exited), never removed: the pin decides,
    the status only decides which of the two reuse branches is taken."""
    for status in ("running", "exited"):
        _setup, calls = _run_step8(monkeypatch, status=status,
                                   created_image=FALKORDB_PIN)
        assert not [c for c in calls if c[:2] == ["docker", "rm"]], (
            f"a {status} container already on the pin must not be removed "
            f"(docker calls: {calls})"
        )
        assert not [c for c in calls if c[:2] == ["docker", "run"]], (
            f"a {status} container already on the pin must not be recreated "
            f"(docker calls: {calls})"
        )
        if status == "exited":
            assert [c for c in calls if c[:2] == ["docker", "start"]], (
                f"an exited container on the pin must simply be restarted "
                f"(docker calls: {calls})"
            )


def test_setup_never_reads_an_unreadable_image_as_the_pin(monkeypatch, capsys):
    """#8124 review (P3): a failed query is never read as permission.

    `_container_image` returns `""` when `docker inspect` cannot answer, and
    `""` used to fall through the truthiness test into "container already
    running" / "Restarting stopped container" — a container whose engine was
    never established, reported as if it had been confirmed on the pin. It must
    neither be reused as pinned nor DESTROYED (its provenance is unknown): it is
    left untouched, and the message says what is unknown and how to settle it.
    """
    for status in ("running", "exited"):
        _setup, calls = _run_step8(monkeypatch, status=status, created_image="")
        out = capsys.readouterr().out
        assert not [c for c in calls if c[:2] in (["docker", "rm"],
                                                 ["docker", "run"],
                                                 ["docker", "start"])], (
            f"an unreadable image is not permission to touch the container "
            f"(status={status}, docker calls: {calls})"
        )
        assert "#8124" in out and "untouched" in out, (
            f"the unreadable branch must name itself, not pass silently "
            f"(status={status}): {out!r}"
        )


def test_no_lane_entry_point_resolves_a_floating_falkordb_tag():
    """#8124: lane tooling must name an engine, never track a floating tag."""
    refs = _lane_image_references()
    assert refs, (
        "no lane entry point resolves a FalkorDB image — the guard is vacuous "
        "(a path in LANE_ENTRY_POINTS/COMPOSE_GLOBS stopped pointing at the "
        "file that starts the container)"
    )

    floating = [f"{where}: {ref}" for where, ref in refs if _is_floating(ref)]
    assert not floating, (
        "these docker-lane entry points start a FalkorDB from a FLOATING tag:\n  "
        + "\n  ".join(floating)
        + f"\n`latest` now resolves to engine 6.0.x, whose reply_compact panics"
        f" the server on `OPTIONAL MATCH ... RETURN count(s), s.harness` and"
        f" EXITS it — the lane then reports `Connection closed by server` instead"
        f" of a result, and a SHARED instance takes every peer lane down with it"
        f" (#8124). Pin {FALKORDB_PIN} (the engine the CI gate runs, #6386)."
    )
    # Non-vacuous in the OTHER direction too, and PER FILE: the scan must be
    # reading the very files where the pin lives, AND each of them must name the
    # canon engine. A COUNT across all sources cannot do the second job (#9085
    # review): while this PR also sets `apps/graph-viz/docker-compose.yml` to the
    # pin, the count is `len(LANE_ENTRY_POINTS) + 1`, so ONE entry point
    # drifting to a different EXPLICIT engine (`graph-scripts/setup.py` ->
    # `falkordb/falkordb-server:v4.20.4`) kept the count at 3 and this guard
    # GREEN — the exact drift the sibling test's docstring says it exists to
    # prevent ("a tool whose default is a different explicit version would pass
    # the sweep above").
    by_entry_point = _entry_point_references()
    for path in LANE_ENTRY_POINTS:
        rel = str(path.relative_to(ROOT))
        here = by_entry_point.get(rel, [])
        assert here, (
            f"{rel} resolves no FalkorDB image — the guard is not reading the "
            f"file that starts the container (refs found: {refs})"
        )
        assert all(ref == FALKORDB_PIN for ref in here), (
            f"{rel} resolves {here!r} instead of the canon engine "
            f"{FALKORDB_PIN!r} — a different EXPLICIT version is engine drift, "
            f"not a pin (#8124/#6386): the engine the gate never approved can "
            f"carry the same `reply_compact` panic, and the floating-tag sweep "
            f"cannot see it."
        )


def test_the_lane_tools_default_to_the_canon_engine_pin(monkeypatch):
    """The DEFAULT is the pin, not merely a refusal of `latest`.

    A tool whose default is a different explicit version would pass the sweep
    above while running an engine the gate never approved — the drift #6386
    exists to prevent, one layer down. Read from the modules themselves so the
    guard cannot be satisfied by a comment.
    """
    sys.path.insert(0, str(ROOT))

    from tools import test_lane as tl  # deliberately lazy

    # The CODE property, order-independently (#9085 review). `tl.IMAGE` is bound
    # at IMPORT time, and `tests/test_test_lane_tool.py` imports this module at
    # module level — so pytest's COLLECTION already bound it before this test
    # runs. Deleting the env var here cannot undo that, and a lane that
    # legitimately exports the seam (to compare two pinned engines) would red
    # this test for an environment reason. Reload under a deleted env var, so
    # what is asserted is the module's own default rather than the shell's.
    monkeypatch.delenv("TORTOISE_TEST_LANE_IMAGE", raising=False)
    importlib.reload(tl)

    assert tl.LANE_IMAGE_PIN == FALKORDB_PIN, (
        f"tools/test_lane.py pins {tl.LANE_IMAGE_PIN!r}, not the canon engine "
        f"{FALKORDB_PIN!r} (#8124/#6386)"
    )
    assert tl.IMAGE == tl.LANE_IMAGE_PIN, (
        "with no TORTOISE_TEST_LANE_IMAGE exported the lane must run the pin — "
        f"the module resolved {tl.IMAGE!r}"
    )

    from tools import edge_census as ec

    assert ec.PROBE_IMAGE == FALKORDB_PIN, (
        f"tools/edge_census.py's probe default is {ec.PROBE_IMAGE!r}, not "
        f"{FALKORDB_PIN!r} — a panic there kills the probe container "
        "mid-measurement and reports a partial reading (#8124)"
    )
    assert inspect.signature(ec.run_probe).parameters["image"].default == ec.PROBE_IMAGE, (
        "run_probe's default and the --image default must be ONE value"
    )
    # ...and the OTHER side of that pair is the argparse default, which neither
    # `run_probe`'s signature nor the floating sweep reads (#9085 review): a
    # drift of that one line to another EXPLICIT version kept both green.
    image_actions = [a for a in _subparser_actions(ec._build_parser())
                     if "--image" in a.option_strings]
    assert image_actions, (
        "tools/edge_census.py's parser declares no --image argument — this "
        "assertion has gone vacuous, not green"
    )
    for action in image_actions:
        assert action.default == ec.PROBE_IMAGE, (
            f"the parser default for {action.option_strings} is "
            f"{action.default!r}, not PROBE_IMAGE ({ec.PROBE_IMAGE!r}) — "
            "`probe --image` and run_probe's signature must agree on the pin"
        )

    # graph-scripts/setup.py is the third entry point, and it is checkable here
    # by VALUE rather than only by the floating sweep (#9085 review).
    setup = _load_setup_module()
    assert setup.FALKORDB_IMAGE == FALKORDB_PIN, (
        f"graph-scripts/setup.py's dev-container image is "
        f"{setup.FALKORDB_IMAGE!r}, not the canon engine {FALKORDB_PIN!r} — its "
        "container is long-lived (`--restart unless-stopped`), so a panicking "
        "engine there reads as an unreachable host (#8124)"
    )


def test_the_lane_image_seam_refuses_a_floating_override(monkeypatch):
    """A digest and an explicit version are pins; the `latest`/`edge` FAMILY is not.

    Fail-closed at the seam matters because the old default is the thing an
    operator copy-pastes: `TORTOISE_TEST_LANE_IMAGE=falkordb/falkordb:latest`
    reproduces #8124 exactly, and it would look like a deliberate comparison.
    The seam must match the FAMILY, not an enumerated set (#9085 review):
    upstream also publishes `edge-ts`, `edge-x64`, `edge-rhel9-x64`,
    `edge-alpine`, `latest-6.0.1`, and an enumerated refusal list is always one
    publication behind — the four spellings below that the earlier set did NOT
    contain were ACCEPTED by it and reached the `docker run`.
    """
    sys.path.insert(0, str(ROOT))
    from tools import test_lane as tl

    assert tl.assert_pinned_image(FALKORDB_PIN) == FALKORDB_PIN
    assert tl.assert_pinned_image("falkordb/falkordb-server:v4.20.4") == (
        "falkordb/falkordb-server:v4.20.4"
    )
    digest = "falkordb/falkordb@sha256:" + "a" * 64
    assert tl.assert_pinned_image(digest) == digest

    for floating in ("falkordb/falkordb:latest", "falkordb/falkordb",
                     "falkordb/falkordb:edge", "falkordb/falkordb-server:latest",
                     "falkordb/falkordb:edge-ts", "falkordb/falkordb:latest-6.0.1",
                     "falkordb/falkordb-server:edge-x64",
                     "falkordb/falkordb:edge-alpine",
                     "falkordb/falkordb-server:EDGE-RHEL9-X64"):
        with pytest.raises(SystemExit) as exc:
            tl.assert_pinned_image(floating)
        assert "#8124" in str(exc.value), (
            f"refusing {floating!r} must name the issue it is protecting: "
            f"{exc.value}"
        )


def test_the_seam_and_the_lane_sweep_classify_floating_tags_the_same_way():
    """ONE predicate owns the verdict (#9085 review).

    Two definitions of "floating" in the same PR is how a lane slips between
    them: `_is_floating` (this file's sweep) refuses every `edge*` spelling,
    while `tools/test_lane.py`'s seam used to accept anything outside a
    six-item set. They must agree on the whole family, and on the pins.
    """
    sys.path.insert(0, str(ROOT))
    from tools import test_lane as tl

    samples = [
        "falkordb/falkordb", "falkordb/falkordb:latest",
        "falkordb/falkordb:latest-6.0.1", "falkordb/falkordb:latest-alpine",
        "falkordb/falkordb:edge", "falkordb/falkordb:edge-c",
        "falkordb/falkordb:edge-rs", "falkordb/falkordb:edge-ts",
        "falkordb/falkordb-server:edge-x64",
        "falkordb/falkordb-server:edge-rhel9-x64",
        "falkordb/falkordb:edge-alpine-arm64v8",
        FALKORDB_PIN, "falkordb/falkordb-server:v4.20.4",
        "falkordb/falkordb-server@sha256:" + "a" * 64,
    ]
    disagreements = [
        f"{ref}: sweep={_is_floating(ref)} seam={tl.is_floating_image(ref)}"
        for ref in samples if _is_floating(ref) != tl.is_floating_image(ref)
    ]
    assert not disagreements, (
        "the lane sweep and the image seam disagree about which references "
        "float — one predicate must own the verdict:\n  "
        + "\n  ".join(disagreements)
    )
    # Both directions non-vacuous: the samples must contain pins AND floats.
    assert any(_is_floating(ref) for ref in samples)
    assert any(not _is_floating(ref) for ref in samples)


def test_the_selfhost_quickstart_hands_out_the_compose_pin_not_latest():
    """The doc's runnable example is a docker-lane entry point too (#8124).

    Option B says "Same image, standalone" — it is the bare-container form of
    `docker-compose.yml`'s sidecar, so it must name the COMPOSE pin, read from
    the compose file rather than restated here. A floating tag in a copy-paste
    block is how a reader ends up running the engine whose `reply_compact`
    panics the server; the documented default is the compose's own pin, and
    bumping the compose must carry the doc with it.
    """
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    compose_image = compose["services"]["falkordb"]["image"]
    assert ":" in compose_image.rsplit("/", 1)[-1], (
        f"docker-compose.yml's falkordb image {compose_image!r} is untagged "
        "(implicitly :latest) — pin it before relying on it here"
    )

    quick = (ROOT / "docs" / "quickstart-selfhosted.md").read_text()
    option_b = quick.split("### Option B", 1)[1].split("### Option C", 1)[0]
    assert compose_image in option_b, (
        f"docs/quickstart-selfhosted.md's Option B no longer names the compose "
        f"pin {compose_image!r} — a bare `docker run` there must hand the reader "
        "the pinned engine, not a floating tag (#8124)"
    )
    assert not re.search(r"falkordb/falkordb[a-z-]*:latest", option_b), (
        "Option B still hands out a floating `:latest` engine (#8124)"
    )


# ── #7357: every FalkorDB container this repo starts carries a KERNEL cap ──
#
# The issue's measured root cause is not one bug but one MISSING FLAG,
# repeated at every entry point that starts a FalkorDB: `HostConfig.Memory=0`.
# `--maxmemory` (where it is set at all) is a REDIS-side limit, so with no
# cgroup cap the container's real ceiling is the HOST's — and the host's
# ceiling is swap. Measured on the fleet box: `Memory=0`, `OOMKilled=false`,
# `ExitCode=0`, `RestartCount` climbing, every lane degraded with it, and the
# canonical graph instance killed and restarted by its own restart policy.
#
# So the guard is per ENTRY POINT, from the argv each one really issues: a
# source-level `grep` for `--memory` is satisfied by a comment, and a single
# count across all entry points stays green when one of them loses the flag.

MEMORY_CAP_ENV = "FALKORDB_CONTAINER_MEMORY"
MEMORY_CAP_DEFAULT = "2g"


def test_the_lane_tool_caps_the_container_memory(monkeypatch):
    """`tools/test_lane.py` — the fleet's per-lane container (#7357)."""
    monkeypatch.delenv(MEMORY_CAP_ENV, raising=False)
    from tools import test_lane as tl

    args = tl._memory_cap_args()
    assert args == ["--memory", MEMORY_CAP_DEFAULT,
                    "--memory-swap", MEMORY_CAP_DEFAULT], args
    assert tl.DEFAULT_CONTAINER_MEMORY == MEMORY_CAP_DEFAULT

    monkeypatch.setenv(MEMORY_CAP_ENV, "1536m")
    assert tl._memory_cap_args()[1] == "1536m", (
        "the cap must be read at CALL time, so one lane can raise it"
    )


def test_the_edge_census_probe_caps_the_container_memory(monkeypatch):
    """`tools/edge_census.py` — the disposable probe the issue names as a
    neighbour that can starve the canonical graph (#7357)."""
    monkeypatch.delenv(MEMORY_CAP_ENV, raising=False)
    from tools import edge_census as ec

    args = ec._probe_run_args("probe-x", ec.PROBE_IMAGE)
    assert args[:4] == ["run", "-d", "--network", "none"], args
    assert "--rm" not in args, (
        f"`--rm` removes a stopped container before its OOMKilled/ExitCode can "
        f"be read — the evidence this report is made of (#7357 review): {args}"
    )
    assert "--memory" in args and "--memory-swap" in args, args
    cap = args[args.index("--memory") + 1]
    assert args[args.index("--memory-swap") + 1] == cap, (
        "swap must EQUAL memory: docker's default is 2x, which lets the "
        "container page the host for minutes instead of dying alone"
    )
    assert cap == MEMORY_CAP_DEFAULT, cap
    assert args[-1] == ec.PROBE_IMAGE, args


def test_the_dev_installer_caps_the_memory_and_bounds_the_restart_loop(monkeypatch):
    """`graph-scripts/setup.py` — the only in-repo launcher of a 16379 container,
    and the only one with an unconditional restart policy (#7357).

    Two properties, both from the argv: the KERNEL cap (an over-consuming dev
    container must die alone, not swap the host), and a BOUNDED restart policy
    (`unless-stopped` + a container that dies on every boot is a silent infinite
    loop — `docker ps` just shows a small uptime each time).
    """
    monkeypatch.delenv(MEMORY_CAP_ENV, raising=False)
    _setup, calls = _run_step8(
        monkeypatch, status="missing", created_image=FALKORDB_PIN)
    run = next(c for c in calls if c[:2] == ["docker", "run"])
    assert "--memory" in run and "--memory-swap" in run, run
    cap = run[run.index("--memory") + 1]
    assert run[run.index("--memory-swap") + 1] == cap, run
    assert cap == MEMORY_CAP_DEFAULT, run
    assert "unless-stopped" not in run, (
        f"an unconditional restart policy is the loop #7357 records: {run}"
    )
    assert "on-failure:5" in run, run


def _run_ci_provision(tmp_path, *, mode: str, cap: str | None = None,
                      inspect_stderr: str = ""):
    """Run the REAL provision.sh with a stub `docker`, returning (result, argv log).

    A stub on PATH rather than a source-level grep: the defect is an argv that
    reaches docker, so the guard has to read the argv docker really got.
    ``mode`` is ``healthy`` (PING answers), ``killed`` (never answers, docker
    reports the kernel killed it), or ``gone`` (never answers and `docker
    inspect` FAILS — which is UNKNOWN, not absence; ``inspect_stderr`` supplies
    docker's own wording when the case is genuinely an absence).
    """
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    log = tmp_path / "docker.log"
    _write_ci_docker_stub(bindir, mode)
    gh_env = tmp_path / "gh_env"
    gh_env.write_text("")
    env = {
        **os.environ,
        "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
        "DOCKER_LOG": str(log),
        "GITHUB_ENV": str(gh_env),
        "FALKORDB_IMAGE": FALKORDB_PIN,
        "FALKORDB_LEGACY": "false",
        "FALKORDB_URI_GRAPH": "",
        "FALKORDB_HEALTH_TIMEOUT": "1",
    }
    env.pop(MEMORY_CAP_ENV, None)
    if cap is not None:
        env[MEMORY_CAP_ENV] = cap
    env["INSPECT_STDERR"] = inspect_stderr
    script = ACTION_DIR / "falkordb-provision" / "provision.sh"
    result = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, env=env,
        timeout=120,
    )
    return result, log


def _write_ci_docker_stub(bindir: Path, mode: str) -> Path:
    """Write the stub `docker` for ``mode`` into ``bindir``; return its path.

    Split out of ``_run_ci_provision`` so the stub's OWN properties (the shell it
    needs) can be asserted without executing provision.sh against it.
    """
    # ⛔ `;;` only — NEVER `;;&`. The `;;&` fall-through terminator is bash 4.0+,
    # and on a macOS dev box `bash` (from PATH) is /bin/bash 3.2.57, where it is
    # a SYNTAX ERROR (rc 2) rather than a fall-through: every `docker` call then
    # fails before doing anything, so the "healthy" runs assert a pull failure
    # and the "should refuse" runs pass for the wrong reason. CI cannot see it
    # (the ubuntu runner's bash is 5.x), which is exactly why the stub is POSIX
    # `sh` and `test_the_stubbed_docker_seams_are_POSIX_parseable` runs `sh -n`
    # over it (#9204 review, P1). Nothing here needs a fall-through: each branch
    # either exits or prints, and `image`/`port`/`exec`/`inspect` are mutually
    # exclusive first arguments.
    if mode == "healthy":
        answers = (
            '  image) exit 0 ;;\n'
            '  port) printf "127.0.0.1:32770\\n" ;;\n'
            '  exec) printf "PONG\\n" ;;\n'
            '  inspect) printf "false 0\\n" ;;\n'
        )
    elif mode == "killed":
        # The container never answers, and docker says the kernel killed it.
        answers = (
            '  image) exit 0 ;;\n'
            '  port) printf "127.0.0.1:32770\\n" ;;\n'
            '  exec) exit 1 ;;\n'
            '  inspect) printf "true 137\\n" ;;\n'
        )
    else:  # gone
        # `docker inspect` FAILS: a wedged daemon, a timeout, or a container
        # removed by hand. That is UNKNOWN, not evidence of absence (see
        # `inspect_stderr` above for the genuine-absence wording).
        answers = (
            '  image) exit 0 ;;\n'
            '  port) printf "127.0.0.1:32770\\n" ;;\n'
            '  exec) exit 1 ;;\n'
            '  inspect) printf "%s" "$INSPECT_STDERR" >&2; exit 1 ;;\n'
        )
    stub = bindir / "docker"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCKER_LOG"\n'
        'case "$1" in\n' + answers + "esac\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    return stub


def test_the_stubbed_docker_seams_are_POSIX_parseable(tmp_path):
    """The stub must run under the shell that resolves it, on EVERY platform.

    The P1 this pins (#9204 review): the CI stub was written with `;;&`, a
    bash-4.0 case terminator. CI is bash 5 (green), but a macOS dev box runs
    /bin/bash 3.2.57, where `;;&` is a SYNTAX ERROR — the stub exited 2 on
    every call, so 5 of these tests could not run locally at all. `sh -n` is
    the portable check: the stub is POSIX, and POSIX shells (dash, bash-3.2 in
    POSIX mode) reject a bash-4 terminator too, so this reds on Linux as well.
    """
    for mode in ("healthy", "killed", "gone"):
        bindir = tmp_path / mode / "bin"
        bindir.mkdir(parents=True, exist_ok=True)
        stub = _write_ci_docker_stub(bindir, mode)
        assert stub.exists(), f"{mode}: no stub was written"
        text = stub.read_text()
        assert ";;&" not in text, (
            f"{mode}: `;;&` is bash 4+ — on macOS (/bin/bash 3.2) the stub is a "
            f"syntax error, so every docker call returns 2 before running: {text}"
        )
        for shell in ("sh", "bash"):
            parsed = subprocess.run([shell, "-n", str(stub)],
                                    capture_output=True, text=True)
            assert parsed.returncode == 0, (
                f"{mode}: the stub does not parse under {shell} — "
                f"{parsed.stderr}{parsed.stdout}"
            )


def test_the_ci_provision_caps_the_container_memory(tmp_path):
    """`.github/actions` — the CI spawner, on the SAME host as the canonical
    instance (the runners share one Docker daemon), two containers per job."""
    result, log = _run_ci_provision(tmp_path, mode="healthy")
    assert result.returncode == 0, result.stdout + result.stderr
    run_lines = [ln for ln in log.read_text().splitlines() if ln.startswith("run ")]
    assert run_lines, log.read_text()
    for line in run_lines:
        cap = line.split("--memory ", 1)[1].split(" ", 1)[0]
        assert line.count("--memory") == 2, line  # --memory and --memory-swap
        assert f"--memory-swap {cap}" in line, (
            f"swap must EQUAL the memory cap, not docker's 2x default: {line}"
        )
        assert cap == MEMORY_CAP_DEFAULT, line


def test_the_ci_provision_names_a_memory_kill_instead_of_merely_unhealthy(tmp_path):
    """#7357's legibility half, at the CI gate: a container the kernel (or the
    cap) killed must be reported as KILLED FOR MEMORY, not as a server that
    "did not answer" — otherwise the operator retries into the same death."""
    result, _log = _run_ci_provision(tmp_path, mode="killed")
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "KILLED FOR MEMORY" in out, out
    assert "OOMKilled=true" in out and "137" in out, out


def test_the_ci_provision_distinguishes_absence_from_a_failed_read(tmp_path):
    """#7357 review F3: a failed `docker inspect` is NOT evidence of absence —
    it also fails when the daemon is wedged, which is this issue's condition. So
    only docker's OWN absence wording is reported as a disappearance; any other
    failure says the state is UNKNOWN."""
    result, _log = _run_ci_provision(tmp_path, mode="gone")
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "could not be READ" in out, out
    assert "UNKNOWN" in out, out
    assert "no longer EXISTS" not in out, (
        f"a failed read must not be asserted as an absence: {out}"
    )

    result, _log = _run_ci_provision(tmp_path, mode="gone", inspect_stderr="No such object: x")
    out = result.stdout + result.stderr
    assert "no longer EXISTS" in out, out
    assert "CRASHED or was KILLED" in out, out


def test_the_probe_names_a_memory_kill_and_an_unreadable_termination(monkeypatch):
    """#7357 review: the probe is started WITHOUT `--rm` so a dead container's
    facts are still readable; when they are not, the report says the state is
    UNKNOWN instead of asserting a kill or an absence. A healthy container must
    stay silent (no false alarm)."""
    from tools import edge_census as ec

    class _R:
        def __init__(self, rc, out=""):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    monkeypatch.setattr(ec, "_docker", lambda *a, **k: _R(0, "true 137 false\n"))
    assert "KILLED FOR MEMORY" in ec._memory_kill_report("probe-x")

    # inspect fails (a wedged daemon, a timeout, a container removed by hand):
    # the READ is what gets reported, and the state stays UNKNOWN — never a
    # guessed kill, never an asserted absence, never a silent "" (the #7357
    # review's dead-code finding).
    monkeypatch.setattr(ec, "_docker", lambda *a, **k: _R(1, ""))
    gone = ec._memory_kill_report("probe-x")
    assert "could not be READ" in gone, gone
    assert "UNKNOWN" in gone, (
        f"a failed read must not be asserted as an absence or a kill: {gone}"
    )

    # A `_docker` that RAISES (a timeout on an overloaded host) must not replace
    # the CensusError with a traceback (#7357 review).
    def boom(*a, **k):
        raise OSError("docker timed out")
    monkeypatch.setattr(ec, "_docker", boom)
    assert "could not be READ" in ec._memory_kill_report("probe-x")

    # Still running (or exited cleanly): nothing to report.
    monkeypatch.setattr(ec, "_docker", lambda *a, **k: _R(0, "false 0\n"))
    assert ec._memory_kill_report("probe-x") == ""


def test_the_cap_seams_refuse_a_zero_value(monkeypatch):
    """`--memory 0` is docker's spelling for NO LIMIT, so the ZERO value must be
    refused at every seam — not only malformed text (#7357 review)."""
    from tools import edge_census as ec

    for bad in ("0", "0g", "0.0m"):
        monkeypatch.setenv(MEMORY_CAP_ENV, bad)
        with pytest.raises(ec.CensusError) as e:
            ec.container_memory_cap()
        assert MEMORY_CAP_ENV in str(e.value), (bad, str(e.value))

    setup = _load_setup_module()
    for bad in ("0", "0g"):
        monkeypatch.setenv(MEMORY_CAP_ENV, bad)
        with pytest.raises(SystemExit):
            setup._container_memory()


def test_the_dev_installer_reports_an_existing_uncapped_container(monkeypatch, capsys):
    """`docker start` cannot add a limit, so an EXISTING container keeps
    `Memory=0` for life. That must be SAID, not silently reused: the same
    "an existing container keeps the bad setting forever" mechanism as #8124's
    image pin, and the reason this cap could otherwise never reach the
    container most likely to need it (#7357 review N1)."""
    setup = _load_setup_module()
    monkeypatch.setenv(MEMORY_CAP_ENV, MEMORY_CAP_DEFAULT)
    monkeypatch.setattr(setup, "MODE", "docker")
    monkeypatch.setattr(setup, "_container_status", lambda _n: "running")
    monkeypatch.setattr(setup, "_container_image", lambda _n: setup.FALKORDB_IMAGE)
    monkeypatch.setattr(setup, "_container_memory_limit", lambda _n: 0)
    setup.step8()
    out = capsys.readouterr().out
    assert "WITHOUT a kernel memory cap" in out, out
    assert "docker rm -f" in out, out

    # …and a container that IS capped says nothing (no false alarm).
    monkeypatch.setattr(setup, "_container_memory_limit", lambda _n: 2 << 30)
    setup.step8()
    assert "WITHOUT a kernel memory cap" not in capsys.readouterr().out


def test_the_dev_installer_says_when_the_memory_limit_cannot_be_READ(
        monkeypatch, capsys):
    """#7357 review F9: `None` from `_container_memory_limit` is "could not
    READ", never "it is capped" — and reporting it as SILENCE makes the two
    indistinguishable, which is how an uncapped container goes unnoticed. The
    same function already names its other unreadable fact (the image pin), so
    this arm does too, with the command that would settle it."""
    setup = _load_setup_module()
    monkeypatch.setenv(MEMORY_CAP_ENV, MEMORY_CAP_DEFAULT)
    monkeypatch.setattr(setup, "MODE", "docker")
    monkeypatch.setattr(setup, "_container_status", lambda _n: "running")
    monkeypatch.setattr(setup, "_container_image", lambda _n: setup.FALKORDB_IMAGE)
    monkeypatch.setattr(setup, "_container_memory_limit", lambda _n: None)
    setup.step8()
    out = capsys.readouterr().out
    assert "Could not read the kernel memory limit" in out, out
    assert "HostConfig.Memory" in out, (
        f"the message must name the query that failed, so the reader can run "
        f"it: {out}"
    )
    assert "WITHOUT a kernel memory cap" not in out, (
        f"an unreadable limit must NOT be reported as an absent one: {out}"
    )


def test_the_dev_installer_refuses_a_bad_cap_before_removing_anything(monkeypatch):
    """#7357 review F8: a guard is only as good as its ORDERING.

    `_container_memory()` used to be called at the `docker run` call site — i.e.
    AFTER the stale-container `docker rm -f` and after the port-collision prompt
    that can `docker kill` a NEIGHBOUR. So `FALKORDB_CONTAINER_MEMORY=0` (which
    docker reads as UNLIMITED) destroyed the stopped dev container and only then
    refused to start a replacement. Both destructive seams are booby-trapped
    here, so this test fails if the validation ever moves back below them.
    """
    setup = _load_setup_module()
    calls: list[list[str]] = []

    def _boom(*_a, **_k):  # pragma: no cover - asserted not to run
        raise AssertionError("docker was called before cap validation")

    monkeypatch.setattr(setup, "MODE", "docker")
    monkeypatch.setattr(setup, "_container_status", _boom)
    monkeypatch.setattr(setup, "_container_image", _boom)
    monkeypatch.setattr(setup, "_port_collision", _boom)
    monkeypatch.setattr(setup, "_run", lambda cmd, **kw: calls.append(cmd))
    for bad in ("0", "0g", "2 gigs"):
        monkeypatch.setenv(MEMORY_CAP_ENV, bad)
        with pytest.raises(SystemExit):
            setup.step8()
    assert calls == [], (
        f"a bad cap must refuse BEFORE any docker command (a `docker rm -f` in "
        f"here destroys the container it then refuses to replace): {calls}"
    )


def test_the_cap_grammar_is_the_SAME_at_every_seam(tmp_path, monkeypatch):
    """#7357 review F7: ONE env var, ONE grammar.

    The three Python seams read the value with `.strip().lower()`, so `2G` and
    `" 2g "` are accepted and `"2 g"` is REFUSED. The shell seam normalised with
    `tr -d '[:space:]'`, which deleted INNER whitespace too — so `"2 g"` was
    silently rewritten to `2g` in CI and refused in every lane. One env var,
    two contracts: a value that works in CI reds nowhere, which is the wrong
    half of a disagreement. Pinned at the argv docker really gets, on BOTH
    spellings, so the seams cannot drift back apart.
    """
    from tools import edge_census as ec
    from tools import test_lane as tl

    setup = _load_setup_module()
    for i, good in enumerate(("2G", " 2g ", "\t2G\t", "2gb", "2048mb",
                              "1t", "2tb", "1.5gb", "2gib", "+2g")):
        monkeypatch.setenv(MEMORY_CAP_ENV, good)
        expected = good.strip().lower()   # `.strip().lower()`, at every seam
        assert tl.container_memory_cap() == expected, good
        assert ec.container_memory_cap() == expected, good
        assert setup._container_memory() == expected, good
        result, log = _run_ci_provision(tmp_path / f"ok{i}", mode="healthy",
                                        cap=good)
        out = result.stdout + result.stderr
        assert result.returncode == 0, (good, out)
        line = next(ln for ln in log.read_text().splitlines()
                    if ln.startswith("run "))
        # `2gb`/`2048mb` are docker-valid LONG units, so they reach the argv as
        # written (docker normalises them); the short forms normalise to `2g`
        # (#7357 review). The point pinned here is that ALL FOUR seams accept
        # the same set — never that they rewrite the value to one spelling.
        expected = good.strip().lower()
        assert f"--memory {expected} --memory-swap {expected}" in line, (good, line)

    # `2g\nabc` used to be ACCEPTED by the shell seam alone: `grep -E '$'` is
    # line-anchored, so any one matching LINE accepted the whole value, while the
    # Python seams' `\Z` refused it (#7357 review). A value with a newline must
    # be refused at every seam — never silently normalised, and never passed to
    # `docker run` where the refusal is opaque.
    # BYTE positivity, not number positivity: docker truncates the parsed spec
    # to an int64, so `0.0000000001g` (0.107 B) and `0.1b` install as
    # `HostConfig.Memory=0` — the UNCAPPED container every seam exists to
    # prevent, reached through a value that reads as positive (#7357 review).
    for i, bad in enumerate(("2 g", "2\tg", "2bb", "2g\nabc", "2g\n0",
                             "0.0000000001g", "0.1b", "0.0g", "0t")):
        monkeypatch.setenv(MEMORY_CAP_ENV, bad)
        with pytest.raises(SystemExit):
            tl.container_memory_cap()
        with pytest.raises(ec.CensusError):
            ec.container_memory_cap()
        with pytest.raises(SystemExit):
            setup._container_memory()
        result, log = _run_ci_provision(tmp_path / f"bad{i}", mode="healthy",
                                        cap=bad)
        out = result.stdout + result.stderr
        assert result.returncode != 0, (bad, out)
        assert "POSITIVE docker memory spec" in out, (bad, out)
        assert "run " not in log.read_text(), (
            f"{bad!r} was silently normalised into a usable cap: "
            f"{log.read_text()}"
        )


def test_the_ci_provision_refuses_a_zero_cap(tmp_path):
    """The CI seam refuses a zero cap too — the shell validator's zero arm."""
    result, log = _run_ci_provision(tmp_path, mode="healthy", cap="0")
    out = result.stdout + result.stderr
    assert result.returncode != 0, out
    assert "POSITIVE docker memory spec" in out, out
    assert "0 means UNLIMITED" in out, (
        f"the refusal must explain WHY zero is refused; a backtick in the "
        f"annotation makes bash run the token and lose it (#7357 review "
        f"F7): {out}"
    )
    assert "command not found" not in out, out
    assert "run " not in log.read_text(), (
        f"no container may be started with a zero cap: {log.read_text()}"
    )


#: Byte multipliers for the units docker AND compose accept: `b`,
#: `k`/`kb`/`kib`, `m`/`mb`/`mib`, `g`/`gb`/`gib`, `t`/`tb`/`tib` (measured on
#: docker 29.1.3 / compose 2.40.3; `2bb` is "invalid suffix: 'bb'" to docker).
_MEMORY_UNITS = {
    "": 1, "b": 1,
    "k": 1 << 10, "kb": 1 << 10, "kib": 1 << 10,
    "m": 1 << 20, "mb": 1 << 20, "mib": 1 << 20,
    "g": 1 << 30, "gb": 1 << 30, "gib": 1 << 30,
    "t": 1 << 40, "tb": 1 << 40, "tib": 1 << 40,
}


def _memory_bytes(spec):
    """Bytes for a literal docker memory spec, or None for anything else.

    #7357 review: `2g` and `2048m` are the SAME 2 GiB (measured: `docker create
    --memory 2048m --memory-swap 2048m` -> Memory=2147483648), so comparing the
    strings turns one correct configuration into a false red whose message
    misstates the condition. A compose INTERPOLATION (`${VAR:-2g}`) is not a
    literal and returns None — that case is settled by raw equality instead, so
    the two keys must read the same expression and the seams' own remedy lever
    keeps working here.
    """
    # Docker's suffix set: `b`, `k`/`kb`/`kib`, `m`/`mb`/`mib`, `g`/`gb`/`gib`,
    # `t`/`tb`/`tib`, with an optional leading `+` — all measured against docker
    # (`2gib`, `+2g` are accepted; `2bb` is "invalid suffix: 'bb'").
    #
    # The whitespace rule is compose's OWN, measured on 2.40.3: ONE space between
    # the number and the unit is accepted (`"2 g"` -> 2147483648), while padding
    # or a doubled space is not (`" 2g "` fails with `strconv.ParseFloat:
    # parsing " 2g": invalid syntax`, `"2  g"` with `parsing "2 ": invalid
    # syntax` — each quotes ITS OWN input). A guard that trimmed
    # the value would pass a file `docker compose up` refuses; one that refused
    # the inner space would reject a file it accepts. So the value is judged as
    # written, with exactly one optional inner space and no padding.
    m = re.fullmatch(r"\+?([0-9]+(?:\.[0-9]+)?) ?([bkmgt]|[kmgt]b|[kmgt]ib)?",
                     str(spec), re.IGNORECASE)
    if not m:
        return None
    return int(float(m.group(1)) * _MEMORY_UNITS[(m.group(2) or "").lower()])


def _is_interpolation(spec) -> bool:
    """Whether *spec* is a compose interpolation rather than a literal.

    An interpolation cannot be resolved here (the value depends on the
    environment at `docker compose up` time), so it is compared AS WRITTEN: the
    two keys must read the same expression, which is what makes raising the
    shared env var move both.
    """
    return "${" in str(spec)


def _compose_falkordb_gaps(root, globs):
    """(files, examined, uncapped, swap_mismatch, bad_spec) per compose service.

    Split out so the population and the failure kinds are testable against a temp
    tree (#7357 review), not only against this checkout. FOUR distinct failure
    kinds, because one message cannot honestly describe them all:
    an absent cap, a cap of ZERO (docker's spelling for NO LIMIT — every `docker
    run` seam refuses it, so this guard must not accept it), a swap mismatch, and
    a value that is not a docker memory spec at all.
    """
    files = sorted({p for g in globs for p in root.glob(g) if p.is_file()})
    checked = 0
    uncapped = []
    swap_mismatch = []
    bad_spec = []
    for path in files:
        data = yaml.safe_load(path.read_text()) or {}
        for name, svc in (data.get("services") or {}).items():
            svc = svc or {}
            if "falkordb" not in str(svc.get("image", "")).lower():
                continue
            checked += 1
            label = f"{path.relative_to(root)}:{name}"
            limit = (((svc.get("deploy") or {}).get("resources") or {})
                     .get("limits") or {}).get("memory")
            if not limit:
                uncapped.append(label)
                continue
            limit_b = _memory_bytes(limit)
            if limit_b is None and not _is_interpolation(limit):
                bad_spec.append(f"{label} (memory={limit!r})")
                continue
            if limit_b == 0:
                # `memory: "0"` is a truthy STRING, so `if not limit` misses it
                # — and 0 is docker's spelling for NO LIMIT, i.e. exactly the
                # uncapped container this guard exists to prevent (#7357
                # review, measured: `--memory 0` -> HostConfig.Memory=0).
                uncapped.append(f"{label} (memory={limit!r} is docker's NO-LIMIT)")
                continue
            swap = svc.get("memswap_limit")
            swap_b = _memory_bytes(swap)
            if swap_b is None and not _is_interpolation(swap):
                bad_spec.append(f"{label} (memswap_limit={swap!r})")
                continue
            same = (limit_b is not None and limit_b == swap_b) or (
                _is_interpolation(limit) and str(swap).strip() == str(limit).strip()
            )
            if not same:
                swap_mismatch.append(
                    f"{label} (memswap_limit={swap!r}, memory={limit!r})"
                )
    return files, checked, uncapped, swap_mismatch, bad_spec


def test_every_compose_falkordb_has_a_memory_limit():
    """The invariant the cap comments claim: NO compose service in this repo
    starts a FalkorDB without a `memory` AND an equal `memswap_limit`.
    `deploy.resources.limits.memory` IS applied by `docker compose up`
    (measured, compose 2.40.3 — #7357).

    Swap equality is enforced ONLY on the compose files here and on the five
    `docker run` seams in this file (`--memory` + `--memory-swap`, equal); a
    service with a memory limit and docker's 2x swap default still pages the
    host for minutes, which is the mechanism the cap exists to remove.

    ANTI-VACUITY (#7357 review): without the population assertions below, a
    glob that stops matching (a compose file moves, `apps/*/` is renamed) or a
    service whose image string stops matching leaves `missing == []` and this
    guard passes having read NOTHING — the exact silent-coverage-loss the
    sibling `test_published_falkordb_compose_ports_bind_loopback` documents and
    guards against. Not `rglob`-ed, so the `.worktrees/` filter the sibling does
    not carry would be dead code HERE and, in a fleet lane (where `ROOT` IS
    `<main>/.worktrees/<lane>`), would skip EVERY file: measured, the guard was
    green in a worktree against an uncapped `apps/graph-viz`.
    """
    files, checked, uncapped, swap_mismatch, bad_spec = _compose_falkordb_gaps(
        ROOT, COMPOSE_GLOBS)
    assert files, f"no compose file matched {COMPOSE_GLOBS} — the guard is vacuous"
    assert checked, (
        f"not one FalkorDB service was examined across {len(files)} compose "
        f"file(s) — the guard no longer covers what it exists for"
    )
    assert not uncapped, (
        "these compose FalkorDB services have no kernel memory limit, so one "
        "runaway instance can swap the host and starve the canonical graph "
        f"(#7357): {uncapped}"
    )
    assert not swap_mismatch, (
        "these compose FalkorDB services pin a memory limit but NOT an equal "
        "memswap_limit, so docker's 2x swap default applies — a swapping "
        f"FalkorDB is a minutes-long RDB reload, not a fast death (#7357): "
        f"{swap_mismatch}"
    )
    assert not bad_spec, (
        "these compose FalkorDB services state a memory value that is neither a "
        f"docker memory spec nor an interpolation (#7357): {bad_spec}"
    )


def test_the_compose_guard_reads_bytes_not_spelling(tmp_path):
    """#7357 review: `memory: 2048m` with `memswap_limit: 2g` IS a correct
    configuration — the guard used to fail it as "no kernel memory limit", a
    false red that also misstated the condition. Bytes are compared, and the two
    failure kinds have their own messages: a MISSING cap is not a SWAP mismatch.
    """
    def _write(memory, swap):
        d = tmp_path / "app"
        d.mkdir(parents=True, exist_ok=True)
        body = ["services:", "  falkordb:",
                "    image: falkordb/falkordb-server:v4.20.6"]
        if memory is not None:
            body += ["    deploy:", "      resources:", "        limits:",
                     f'          memory: "{memory}"']
        if swap is not None:
            body += [f'    memswap_limit: "{swap}"']
        (d / "docker-compose.yml").write_text("\n".join(body) + "\n")

    globs = ("app/docker-compose*.yml",)

    def _gaps():
        _f, _c, unc, mism, bad = _compose_falkordb_gaps(tmp_path, globs)
        return unc, mism, bad

    _write("2048m", "2g")
    assert _gaps() == ([], [], []), (
        "2g and 2048m are the same 2 GiB (and 1t and 1099511627776 the same "
        f"TiB); a byte comparison must accept them: {_gaps()}"
    )
    _write("1t", "1099511627776")
    assert _gaps() == ([], [], []), _gaps()
    _write("2gib", "+2147483648")   # docker accepts both spellings
    assert _gaps() == ([], [], []), _gaps()

    # compose REJECTS padding (`strconv.ParseFloat: parsing " 2g": invalid
    # syntax`), so the guard must not normalise it away — reporting it is the
    # true reading (#7357 review).
    _write(" 2g ", " 2g ")
    unc, mism, bad = _gaps()
    assert bad and not unc and not mism, (
        f"a padded value is not a compose memory spec, so it is neither a cap "
        f"nor a swap mismatch: {_gaps()}"
    )

    # …while ONE inner space IS a spec compose accepts ("2 g" -> 2147483648),
    # so refusing it would be a false red on a deployable file.
    _write("2 g", " 2g ".strip())
    unc, mism, bad = _gaps()
    assert not unc and not mism and not bad, (
        f"compose accepts a single inner space between number and unit, so the "
        f"guard must too: {_gaps()}"
    )

    _write("512m", "1g")
    unc, mism, bad = _gaps()
    assert not unc and not bad and mism, _gaps()

    _write(None, "512m")
    unc, mism, bad = _gaps()
    assert unc and not mism and not bad, (
        f"an absent cap must be reported as an absent cap, not as a swap "
        f"mismatch: {_gaps()}"
    )

    # `"0"` is a TRUTHY string, so `if not limit` misses it — and 0 is docker's
    # spelling for NO LIMIT (#7357 review). An integer 0 was caught, the quoted
    # string was not.
    _write("0", "0")
    unc, mism, bad = _gaps()
    assert unc and not mism and not bad, (
        f"`memory: \"0\"` is an UNCAPPED container, which is what this guard "
        f"exists to catch: {_gaps()}"
    )

    _write("lots", "lots")
    unc, mism, bad = _gaps()
    assert bad and not unc and not mism, (
        f"a value that is not a docker memory spec is neither an absent cap nor "
        f"a swap mismatch: {_gaps()}"
    )


def test_the_compose_guard_keeps_a_shared_interpolation_seam(tmp_path):
    """The remedy these messages print is `raise FALKORDB_CONTAINER_MEMORY`, so
    a compose service must be able to take BOTH keys from that ONE env var
    (`${FALKORDB_CONTAINER_MEMORY:-2g}`). An interpolation is not a literal, so
    the comparison falls back to raw equality — two different expressions (even
    if they resolve to the same bytes) are a real mismatch, because the operator
    raising the env var would cap memory and swap differently."""
    d = tmp_path / "app"
    d.mkdir()
    (d / "docker-compose.yml").write_text(
        "services:\n"
        "  falkordb:\n"
        "    image: falkordb/falkordb-server:v4.20.6\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        '          memory: "${FALKORDB_CONTAINER_MEMORY:-2g}"\n'
        '    memswap_limit: "${FALKORDB_CONTAINER_MEMORY:-2g}"\n'
    )
    _files, _checked, uncapped, mismatch, bad = _compose_falkordb_gaps(
        tmp_path, ("app/docker-compose*.yml",))
    assert (uncapped, mismatch, bad) == ([], [], []), (uncapped, mismatch, bad)

    (d / "docker-compose.yml").write_text(
        "services:\n"
        "  falkordb:\n"
        "    image: falkordb/falkordb-server:v4.20.6\n"
        "    deploy:\n"
        "      resources:\n"
        "        limits:\n"
        '          memory: "${FALKORDB_CONTAINER_MEMORY:-2g}"\n'
        '    memswap_limit: "2g"\n'
    )
    _files, _checked, uncapped, mismatch, bad = _compose_falkordb_gaps(
        tmp_path, ("app/docker-compose*.yml",))
    assert mismatch and not bad, (
        "raising FALKORDB_CONTAINER_MEMORY must move BOTH keys, or the cap and "
        f"the swap diverge: {mismatch}"
    )


def test_a_probe_killed_MID_STAGE_is_explained_not_left_as_raw_stderr(monkeypatch):
    """#7357 review: the probe's memory-kill report was attached ONLY to the
    "never became ready" branch. The cap this module now imposes makes the likely
    failure of a large `--n` a kill AFTER readiness — mid-stage — which raised
    docker's raw "Container … is not running" with no cause at all: the legibility
    gap the issue is about, on the very path the cap creates.
    """
    from tools import edge_census as ec

    calls: list[tuple] = []

    def fake(*args, **kwargs):
        calls.append(args)
        if args[0] == "version":
            return ec.subprocess.CompletedProcess(args, 0, "8.0.0\n", "")
        if args[0] == "run":
            return ec.subprocess.CompletedProcess(args, 0, "abc123\n", "")
        if args[0] == "exec":
            return ec.subprocess.CompletedProcess(args, 0, "PONG\n", "")
        if args[0] == "inspect":
            fmt = str(args[2])
            if "HostConfig" in fmt:
                return ec.subprocess.CompletedProcess(args, 0, "2147483648\n", "")
            if "OOMKilled" in fmt:
                # dead, and killed by its own cgroup cap
                return ec.subprocess.CompletedProcess(
                    args, 0, "true 137 false\n", "")
            return ec.subprocess.CompletedProcess(args, 0, "\n", "")
        if args[0] == "rm":
            return ec.subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected docker call: {args}")

    # First query (the baseline read) succeeds; the CREATE then finds the
    # container gone, exactly what an OOM kill mid-run looks like.
    def _ping_then_die(container, *a, **k):
        calls.append(("QUERY", container))
        if len([c for c in calls if c[0] == "QUERY"]) > 1:
            raise ec.CensusError(
                f"probe query failed in {container!r}: "
                f"Error response from daemon: Container abc is not running")
        return None

    monkeypatch.setattr(ec, "_docker", fake)
    monkeypatch.setattr(ec, "_container_query", _ping_then_die)
    monkeypatch.setattr(ec, "_container_used_memory",
                        lambda _c: 1234)
    with pytest.raises(ec.CensusError) as excinfo:
        ec.run_probe(n=100, log=lambda _m: None)
    msg = str(excinfo.value)
    assert "KILLED FOR MEMORY" in msg, (
        f"a probe killed mid-stage must name the kill, not raise docker's raw "
        f"stderr on its own: {msg}"
    )
    assert "is not running" in msg, (
        f"the engine's own error must stay — the report is APPENDED, never a "
        f"replacement: {msg}"
    )


def test_the_probe_start_failure_carries_dockers_own_line(monkeypatch):
    """#7357 review: `str(CalledProcessError)` holds only the argv and the exit
    status, so a missing image, a permission problem and a wedged daemon were
    indistinguishable — and this module's stated rule is that it reports docker's
    reason, never an opaque refusal. `_container_query` in the same file already
    sets that standard."""
    from tools import edge_census as ec

    def fake(*args, **kwargs):
        if args[0] == "version":
            return ec.subprocess.CompletedProcess(args, 0, "8.0.0\n", "")
        if args[0] == "run":
            raise ec.subprocess.CalledProcessError(
                125, args,
                output="",
                stderr="docker: Error response from daemon: pull access denied "
                       "for tortoise-review/no-such-image, repository does not "
                       "exist")
        if args[0] == "rm":       # the `finally:` teardown, on the failure path
            return ec.subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected docker call: {args}")

    monkeypatch.setattr(ec, "_docker", fake)
    with pytest.raises(ec.CensusError) as excinfo:
        ec.run_probe(n=100, image="tortoise-review/no-such-image:1",
                     log=lambda _m: None)
    msg = str(excinfo.value)
    assert "pull access denied" in msg, (
        f"docker's own line must survive: a bare exit status cannot distinguish "
        f"a missing image from a permission problem or a wedged daemon: {msg}"
    )
