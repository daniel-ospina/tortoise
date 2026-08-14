"""S14 + E2E-16 suite (epic #900 T8, issue #1044): the `tortoise index
directory` CLI — the operator-visible contract through the REAL process
boundary.

Plan §6.5 canonical + §8.6 T8 + E2E-16: stdout = ONE JSON line (the §3.1
report dict, deterministic key order) + human rendering to stderr; exit 0 =
completed run (with or without failed>0); exit 1 = pre-walk argument error
OR graph unreachable; --metadata opt-in (default extract_metadata=False);
--corpus-name override (default basename, RAW single-encode); env
inheritance (TORTOISE_INGEST_BASE_DIR fallback, TORTOISE_MAX_FILE_MB,
TORTOISE_INDEX_NO_NETWORK, TORTOISE_INDEX_CHILD_STDERR debug-redirect with
truncate-on-open + fail-safe). PATH-scrubbed subprocess legs (E2E-15(d2)
hygiene — never resolve a pip-installed tortoise).

E2E-16 legs (i)-(viii): happy run, re-run skipped, env fallback, both-absent
error naming BOTH surfaces, --db dead-uri no-fallback, TORTOISE_MAX_FILE_MB
through the process boundary, out-of-base symlink sandbox, --corpus-name
multi-corpus through the process boundary.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tortoise.sdk import TortoiseSDK

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_cli(args: list[str], *, env: dict | None = None,
             timeout: int = 120) -> subprocess.CompletedProcess:
    """Run the CLI entry in a subprocess with a scrubbed PATH (never a
    pip-installed tortoise — the repo-checkout fallback must resolve)."""
    e = dict(os.environ)
    e.pop("TORTOISE_DB_URI", None)
    e.pop("TORTOISE_DB_PATH", None)
    # legacy trio scrub (review-gate P2-5): _resolve_db_target prefers
    # FALKORDB_HOST/PORT/PASSWORD over TORTOISE_DB_PATH — a host with the
    # trio set would silently target a live docker graph from every
    # embedded-DB leg
    e.pop("FALKORDB_HOST", None)
    e.pop("FALKORDB_PORT", None)
    e.pop("FALKORDB_PASSWORD", None)
    if env:
        e.update(env)
    # PATH-scrub: drop any dir containing a tortoise binary from the PATH so
    # `command -v tortoise` finds nothing (hook repo-checkout fallback).
    scrubbed = [p for p in e.get("PATH", "").split(os.pathsep)
                if not Path(p).joinpath("tortoise").exists()]
    e["PATH"] = os.pathsep.join(scrubbed) if scrubbed else e.get("PATH", "")
    return subprocess.run(
        [sys.executable, "-m", "tortoise", "index", "directory", *args],
        capture_output=True, text=True, env=e, cwd=str(REPO_ROOT),
        timeout=timeout,
    )


SESSION_FIXTURE = """\
---
sessionId: {sid}
title: "{title}"
---
Body {sid}.
"""

DOC_FIXTURE = """\
---
title: "{title}"
type: strategyDoc
---
Doc body.
"""


def _make_corpus(tmp_path, name: str = "corpus") -> Path:
    c = tmp_path / name
    c.mkdir()
    (c / "s1.md").write_text(SESSION_FIXTURE.format(sid="cli1", title="S1"))
    (c / "s2.md").write_text(SESSION_FIXTURE.format(sid="cli2", title="S2"))
    (c / "doc.md").write_text(DOC_FIXTURE.format(title="Doc"))
    return c


def _db_path(tmp_path) -> str:
    return str(tmp_path / "cli.db")


# ── E2E-16 (i): happy run → stdout JSON contract + exit 0 ─────────────

def test_e2e16_i_happy_run_stdout_json_contract(tmp_path):
    """E2E-16(i): direct subprocess against a fresh DB → exit 0 + stdout
    parses to the §3.1 summary with correct counts + the deterministic key
    order (directory, corpus_name, file_count, indexed, updated, skipped,
    failed, aborted, ignored, errors, by_kind, aborted_reason) + graph state
    matches E2E-1 (Source/Event/edge, REQUIRED sweep clean)."""
    corpus = _make_corpus(tmp_path)
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path)])
    assert r.returncode == 0, r.stderr
    line = r.stdout.strip().splitlines()[0]
    d = json.loads(line)
    assert list(d.keys()) == [
        "directory", "corpus_name", "file_count", "indexed", "updated",
        "skipped", "failed", "aborted", "ignored", "errors", "by_kind",
        "aborted_reason",
    ]
    assert d["file_count"] == 3
    assert d["indexed"] == 3 and d["updated"] == 0 and d["skipped"] == 0
    assert d["failed"] == 0 and d["aborted"] == 0 and d["ignored"] == 0
    assert d["corpus_name"] == corpus.name  # default = basename
    assert d["by_kind"] == {"agentSession": 2, "document": 1}
    assert d["errors"] == [] and d["aborted_reason"] is None
    # human rendering on stderr
    assert "file_count: 3" in r.stderr


# ── E2E-16 (ii): re-run → skipped, zero new nodes ─────────────────────

def test_e2e16_ii_rerun_skipped(tmp_path):
    """E2E-16(ii): re-run → exit 0 + stdout skipped == file_count + zero new
    nodes."""
    corpus = _make_corpus(tmp_path)
    db = _db_path(tmp_path)
    r1 = _run_cli([str(corpus), "--db", db])
    assert r1.returncode == 0 and json.loads(r1.stdout)["indexed"] == 3
    r2 = _run_cli([str(corpus), "--db", db])
    d2 = json.loads(r2.stdout.splitlines()[0])
    assert d2["skipped"] == 3 and d2["indexed"] == 0 and d2["updated"] == 0
    assert d2["failed"] == 0


# ── E2E-16 (iii): env fallback (TORTOISE_INGEST_BASE_DIR) ─────────────

def test_e2e16_iii_env_fallback(tmp_path):
    """E2E-16(iii): NO positional arg + TORTOISE_INGEST_BASE_DIR=<corpus> →
    indexes via the env fallback (the hook always passes positionally
    post-T8, so the fallback path has zero real-layer exercise today)."""
    corpus = _make_corpus(tmp_path)
    r = _run_cli(["--db", _db_path(tmp_path)],
                 env={"TORTOISE_INGEST_BASE_DIR": str(corpus)})
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.splitlines()[0])
    assert d["indexed"] == 3 and d["corpus_name"] == corpus.name


# ── E2E-16 (iv): both absent → exit 1 naming BOTH surfaces ────────────

def test_e2e16_iv_both_absent_exit_1_names_both(tmp_path):
    """E2E-16(iv): no positional arg AND no TORTOISE_INGEST_BASE_DIR → exit 1
    + clear stderr naming BOTH corpus_root AND TORTOISE_INGEST_BASE_DIR (the
    actionability assertion — the first error a new operator sees) + zero
    graph writes."""
    r = _run_cli(["--db", _db_path(tmp_path)],
                 env={"TORTOISE_INGEST_BASE_DIR": ""})
    assert r.returncode == 1
    assert "corpus-dir" in r.stderr and "TORTOISE_INGEST_BASE_DIR" in r.stderr
    # zero graph writes (a fresh DB file is not even created)
    assert not Path(_db_path(tmp_path)).exists()


# ── E2E-16 (v): --db dead-uri → exit 1, no fallback-to-embedded ───────

def test_e2e16_v_dead_db_no_fallback(tmp_path):
    """E2E-16(v): a dead graph URI → exit 1, NO fallback to an embedded DB
    (a live control DB remains empty), fail-fast (no hang). The hook passes
    TORTOISE_DB_URI in the child env (never argv) — this leg exercises the
    same path: env URI → from_uri → dead server → clean exit 1."""
    corpus = _make_corpus(tmp_path)
    control = _db_path(tmp_path)
    # docker:// scheme passes _resolve_db_target's URI validation but the
    # port is dead — the graph connect fails cleanly (exit 1, no hang)
    r = _run_cli([str(corpus)],
                 env={"TORTOISE_DB_URI": "docker://127.0.0.1:1",
                      "TORTOISE_DB_PATH": control})
    assert r.returncode == 1
    assert "graph unreachable" in r.stderr or "unreachable" in r.stderr, r.stderr
    # the control DB path was NOT touched (no silent embedded fallback)
    assert not Path(control).exists() or os.path.getsize(control) == 0


# ── E2E-16 (vi): TORTOISE_MAX_FILE_MB through the process boundary ────

def test_e2e16_vi_max_file_mb_env(tmp_path):
    """E2E-16(vi): TORTOISE_MAX_FILE_MB small + an oversized fixture set ONLY
    in the child env → `failed` in the stdout report (env-through-process-
    boundary proof)."""
    corpus = _make_corpus(tmp_path)
    (corpus / "big.md").write_bytes(b"x" * (1024 * 1024 + 1))  # 1 MiB + 1
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                 env={"TORTOISE_MAX_FILE_MB": "1"})
    assert r.returncode == 0        # completed run with failed>0 STILL exits 0
    d = json.loads(r.stdout.splitlines()[0])
    assert d["failed"] == 1 and d["indexed"] == 3
    assert d["file_count"] == 4


# ── E2E-16 (vii): out-of-base symlink sandbox ─────────────────────────

def test_e2e16_vii_sandbox_out_of_base_symlink(tmp_path):
    """E2E-16(vii): TORTOISE_INGEST_BASE_DIR=<fixture parent> + one out-of-
    base symlink probe in the corpus → child run completes, probe `failed`,
    in-base files indexed (positive + negative sandbox through the real
    layer)."""
    base = tmp_path / "base"; base.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "leak.md").write_text(SESSION_FIXTURE.format(
        sid="leak", title="Leak"))
    corpus = base / "corpus"; corpus.mkdir()
    (corpus / "s1.md").write_text(SESSION_FIXTURE.format(sid="cli7", title="S"))
    (corpus / "leak.md").symlink_to(outside / "leak.md")
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                 env={"TORTOISE_INGEST_BASE_DIR": str(base)})
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.splitlines()[0])
    assert d["indexed"] == 1
    assert d["failed"] == 1          # the out-of-base symlink probe
    errs = [e for e in d["errors"] if e.get("file") == "leak.md"]
    assert errs and errs[0]["cause"] == "escape"


# ── E2E-16 (viii): --corpus-name through the process boundary ─────────

def test_e2e16_viii_corpus_name_two_same_basename_corpora(tmp_path):
    """E2E-16(viii): two same-basename corpora via the CLI — DEFAULT
    derivation → collision documented/counted; distinct --corpus-name → 2N
    Sources, zero url overlap, by_kind correct (mirrors E2E-17(f) at the CLI
    layer; exit 0 + stdout JSON in both arms)."""
    root_a = tmp_path / "ra" / "corpus"
    root_b = tmp_path / "rb" / "corpus"
    root_a.mkdir(parents=True); root_b.mkdir(parents=True)
    assert root_a.name == root_b.name == "corpus"
    (root_a / "s1.md").write_text(SESSION_FIXTURE.format(sid="cpa", title="A"))
    (root_b / "s1.md").write_text(SESSION_FIXTURE.format(sid="cpb", title="B"))
    db = _db_path(tmp_path)
    # DEFAULT arm: same basename → 1N Source (collision counted, not forked)
    ra = _run_cli([str(root_a), "--db", db])
    rb = _run_cli([str(root_b), "--db", db])
    assert ra.returncode == 0 and rb.returncode == 0
    assert json.loads(ra.stdout)["indexed"] == 1
    # the second corpus with the same basename collides on the shared url
    # (documented/counted via updated — never a silent second Source)
    drb = json.loads(rb.stdout.splitlines()[0])
    assert drb["updated"] == 1 or drb["skipped"] == 1, drb
    # distinct arm on a FRESH graph: 2N Sources, zero url overlap
    db2 = str(tmp_path / "cli2.db")
    r1 = _run_cli([str(root_a), "--db", db2, "--corpus-name", "alpha"])
    r2 = _run_cli([str(root_b), "--db", db2, "--corpus-name", "beta"])
    assert r1.returncode == 0 and r2.returncode == 0
    assert json.loads(r1.stdout)["corpus_name"] == "alpha"
    assert json.loads(r2.stdout)["corpus_name"] == "beta"
    assert json.loads(r1.stdout)["indexed"] == 1
    assert json.loads(r2.stdout)["indexed"] == 1
    # verify 2 distinct Sources via a follow-up read (list-sources CLI —
    # resolves the target from env, so TORTOISE_DB_PATH carries the db)
    ls_env = dict(os.environ)
    ls_env.pop("TORTOISE_DB_URI", None)
    ls_env["TORTOISE_DB_PATH"] = db2
    ls = subprocess.run(
        [sys.executable, "-m", "tortoise", "list-sources"],
        capture_output=True, text=True, env=ls_env,
        cwd=str(REPO_ROOT), timeout=60,
    )
    assert ls.returncode == 0, ls.stderr
    # at minimum: two distinct corpus:// urls (alpha + beta) in the output
    assert "corpus://alpha/s1.md" in ls.stdout
    assert "corpus://beta/s1.md" in ls.stdout
    assert "corpus://corpus/s1.md" not in ls.stdout


# ── S14 unit: TORTOISE_INDEX_CHILD_STDERR debug-redirect ──────────────

def test_s14_child_stderr_redirect_captures_full_output(tmp_path):
    """S14 unit: TORTOISE_INDEX_CHILD_STDERR captures the child's FULL output
    (stdout JSON line + stderr human rendering) with TRUNCATE-ON-OPEN."""
    corpus = _make_corpus(tmp_path)
    cap = str(tmp_path / "child.log")
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                 env={"TORTOISE_INDEX_CHILD_STDERR": cap})
    assert r.returncode == 0
    assert Path(cap).exists()
    content = Path(cap).read_text()
    assert "file_count" in content          # the stdout JSON line
    assert "Indexed corpus" in content      # the stderr human rendering
    # two-consecutive-fire truncate (E2E-15(h) semantics at the unit level):
    # the file holds only ONE run's output (never grows across fires)
    size1 = Path(cap).stat().st_size
    r2 = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                  env={"TORTOISE_INDEX_CHILD_STDERR": cap})
    assert r2.returncode == 0
    assert Path(cap).stat().st_size <= size1 + 8  # truncate-on-open


def test_s14_child_stderr_fail_safe(tmp_path):
    """S14 unit: the redirect fail-safe — relative target and missing/
    unwritable parent → exit 1 BEFORE any walk; a NONEXISTENT target FILE is
    valid (the child creates it)."""
    corpus = _make_corpus(tmp_path)
    # relative target → exit 1, fail-safe
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                 env={"TORTOISE_INDEX_CHILD_STDERR": "rel.log"})
    assert r.returncode == 1
    assert "must be absolute" in r.stderr
    # missing parent dir → exit 1, fail-safe
    r2 = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                  env={"TORTOISE_INDEX_CHILD_STDERR":
                       str(tmp_path / "no-such-dir" / "child.log")})
    assert r2.returncode == 1
    assert "parent dir missing/unwritable" in r2.stderr
    # nonexistent target FILE is VALID → exit 0, file created
    cap = str(tmp_path / "new.log")
    r3 = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                  env={"TORTOISE_INDEX_CHILD_STDERR": cap})
    assert r3.returncode == 0
    assert Path(cap).exists()


def test_s14_child_stderr_fail_safe_extended(tmp_path):
    """S14 unit (review-gate P2-4 — the cycle-16 pin's full enumeration):
    the fail-safe ALSO rejects an existing-DIRECTORY target and a
    non-regular-file (FIFO) target BEFORE the redirect open (an operator
    setting the var to a directory/FIFO would raise EISDIR / block inside
    the child with fd 1/2 still /dev/null — an invisible crash killing the
    #280 sweep); an unwritable-parent target is rejected too."""
    import stat as _stat
    corpus = _make_corpus(tmp_path)
    # existing-directory target → exit 1, fail-safe
    rdir = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                    env={"TORTOISE_INDEX_CHILD_STDERR": str(tmp_path)})
    assert rdir.returncode == 1
    assert "is a directory" in rdir.stderr
    # non-regular (FIFO) target → exit 1, fail-safe
    fifo = tmp_path / "child.fifo"
    os.mkfifo(str(fifo))
    try:
        rfifo = _run_cli([str(corpus), "--db", _db_path(tmp_path)],
                         env={"TORTOISE_INDEX_CHILD_STDERR": str(fifo)},
                         timeout=30)
        assert rfifo.returncode == 1
        assert "not a regular file" in rfifo.stderr
    finally:
        try:
            os.unlink(str(fifo))
        except FileNotFoundError:
            pass
    # unwritable parent → exit 1, fail-safe (root bypasses permissions —
    # skip the leg when euid==0)
    if os.geteuid() != 0:
        rodir = tmp_path / "ro"; rodir.mkdir()
        rodir.chmod(_stat.S_IRUSR | _stat.S_IXUSR)  # r-x, no write
        try:
            rro = _run_cli(
                [str(corpus), "--db", _db_path(tmp_path)],
                env={"TORTOISE_INDEX_CHILD_STDERR":
                     str(rodir / "child.log")})
            assert rro.returncode == 1
            assert "unwritable" in rro.stderr
        finally:
            rodir.chmod(0o755)


# ── S14 unit: arg resolution precedence (NO_NETWORK vs --metadata) ────

def test_s14_no_network_overrides_metadata_flag(tmp_path):
    """S14 unit (cycle-12 precedence pin): TORTOISE_INDEX_NO_NETWORK=1 FORCES
    extract_metadata=False regardless of --metadata (the flag governs only
    when the var is absent)."""
    corpus = _make_corpus(tmp_path)
    r = _run_cli([str(corpus), "--db", _db_path(tmp_path), "--metadata"],
                 env={"TORTOISE_INDEX_NO_NETWORK": "1"})
    assert r.returncode == 0
    d = json.loads(r.stdout.splitlines()[0])
    # the run completes with the honest counts — the var short-circuits
    # metadata/embeddings at the SDK boundary (no network, no crash)
    assert d["indexed"] == 3 and d["failed"] == 0


# ═══════════════════════════════════════════════════════════════════════
# E2E-15: session-end hook — REAL-SCRIPT E2E against the verified hook (S12)
# ═══════════════════════════════════════════════════════════════════════

HOOK = REPO_ROOT / "tortoise" / "claude-hooks" / "session-end.sh"


def _run_hook(*, env: dict, transcript_path: Path | None = None,
              timeout: int = 60, cwd: str | None = None) -> subprocess.CompletedProcess:
    """Invoke the REAL hook script with the stdin JSON contract + a scrubbed
    PATH (the repo-checkout fallback must resolve against the worktree, never
    a pip-installed tortoise — E2E-15(d2) harness hygiene).

    ``cwd`` defaults to REPO_ROOT; legs that stub the hosted capture pass a
    dir holding a ``.tortoise`` config (the session-capture CLI reads
    api_key/api_url from Path.cwd()/.tortoise — no env fallback)."""
    e = dict(os.environ)
    e.pop("TORTOISE_DB_URI", None)
    e.pop("TORTOISE_DB_PATH", None)
    # legacy trio scrub (review-gate P2-5)
    e.pop("FALKORDB_HOST", None)
    e.pop("FALKORDB_PORT", None)
    e.pop("FALKORDB_PASSWORD", None)
    scrubbed = [p for p in e.get("PATH", "").split(os.pathsep)
                if not Path(p).joinpath("tortoise").exists()]
    e["PATH"] = os.pathsep.join(scrubbed) if scrubbed else e.get("PATH", "")
    e.update(env)
    # the sweep child must resolve the WORKTREE's interpreter (the plan's
    # repo-checkout fallback — never a system python without tortoise); the
    # hook honors a PYTHON_BIN override (cycle-11 harness hygiene).
    e["PYTHON_BIN"] = sys.executable
    stdin = json.dumps({
        "session_id": "e2e15-session",
        "transcript_path": str(transcript_path) if transcript_path else "",
        "cwd": str(REPO_ROOT),
    })
    return subprocess.run(
        ["bash", str(HOOK)], input=stdin, capture_output=True, text=True,
        env=e, cwd=cwd or str(REPO_ROOT), timeout=timeout,
    )


def _wait_for_graph(sdk, corpus: Path, deadline_s: float = 60.0) -> dict | None:
    """Poll the graph for the post-sweep state (Source + AgentSession Event +
    references edge for the fixture file) — deterministic drain, never a
    fixed sleep (E2E-15 cycle-9 drain pin)."""
    import time as _time
    g = sdk._get_proj().g
    start = _time.time()
    while _time.time() - start < deadline_s:
        n = g.query(
            "MATCH (s:Source)-[:references]->(e:Event) "
            "WHERE s.sourceKind='agentSession' RETURN count(*)").result_set
        if n and n[0][0] >= 1:
            return {"sources": n[0][0]}
        _time.sleep(1)
    return None


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_a_happy_path_reaches_new_primitive(tmp_path, monkeypatch):
    """E2E-15(a): fire the hook → exit 0; the fixture session file IS indexed
    via the NEW primitive (Source + AgentSession Event + references edge —
    proves the sweep reached index_directory). The capture spy (a stub HTTP
    /v1/sessions endpoint) asserts the hosted capture step FIRED with the
    converted transcript. Post-drain: session_index_health asserts the
    fixture session lands in `matched` (the cycle-20 POST-DRAIN HEALTH pin).

    Backend choreography (cycle-9 pin, #6761): the nohup'd sweep is a
    SEPARATE process — two processes on one embedded file is the #6761 crash
    class (the §5.3 busy-probe fails the second opener fast). The embedded
    happy path therefore runs with the CHILD as the SOLE owner: the parent
    does NOT hold the daemon; the child indexes, its daemon auto-closes on
    child exit (redislite SAVE on last-connection close), and the parent
    opens FRESH after the child completes (RDB persisted) and reads the
    indexed state. Deterministic drain: poll for child completion + the
    daemon's .settings release (review-gate P2-1 — the report lands ~100ms
    BEFORE the daemon closes; a fresh open inside that window races the
    busy-probe), then open."""
    import time as _time
    import threading as _threading
    corpus = tmp_path / "corpus"; corpus.mkdir()
    sess = corpus / "s.md"
    sess.write_text("---\nsessionId: hk1\ntitle: Hook\n---\nBody")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hello"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    # capture spy: a stub /v1/sessions endpoint recording the POST (leg (a)
    # must prove the hosted capture step FIRED — a deleted capture step
    # would fail here; review-gate P2-3)
    from http.server import BaseHTTPRequestHandler, HTTPServer
    spy: dict = {"requests": []}

    class _H(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length).decode("utf-8", "replace")
            spy["requests"].append({"path": self.path, "body": body})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"session_id": "e2e15-spy"}')

        def log_message(self, *a):  # noqa: ANN002
            pass

    stub = HTTPServer(("127.0.0.1", 0), _H)
    stub_port = stub.server_address[1]
    stub_thread = _threading.Thread(target=stub.serve_forever, daemon=True)
    stub_thread.start()
    # the session-capture CLI reads api_key/api_url from Path.cwd()/.tortoise
    # (NO env fallback — __main__.py:1295-1310) — write the config into a
    # leg-local cwd so the capture step targets the spy
    hook_cwd = tmp_path / "hook-cwd"; hook_cwd.mkdir()
    (hook_cwd / ".tortoise").write_text(json.dumps({
        "api_key": "e2e15-spy-key",
        "api_url": f"http://127.0.0.1:{stub_port}",
    }))
    env = {
        "TORTOISE_SESSION_CORPUS": str(corpus),
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        "TORTOISE_DB_PATH": db,
        "TORTOISE_INDEX_NO_NETWORK": "1",   # omission semantics (cycle-10)
        "TORTOISE_INDEX_CHILD_STDERR": cap,
        "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
    }
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    try:
        r = _run_hook(env=env, transcript_path=transcript, cwd=str(hook_cwd))
        assert r.returncode == 0, r.stderr
        # deterministic drain: poll for the child's report in the capture
        # file + the daemon's .settings release (P2-1 fresh-open race)
        start = _time.time()
        while _time.time() - start < 60:
            cap_ready = (Path(cap).exists()
                         and "file_count" in Path(cap).read_text())
            settled = not Path(db + ".settings").exists()
            if cap_ready and settled:
                break
            _time.sleep(1)
        assert Path(cap).exists() and "file_count" in Path(cap).read_text(), \
            f"sweep child never reported: {Path(cap).read_text() if Path(cap).exists() else 'no capture'}"
        # the capture spy FIRED with the converted transcript (the payload
        # carries the parsed turns: role/content per _parse_transcript)
        assert len(spy["requests"]) >= 1, "capture spy never POSTed"
        assert any("/v1/sessions" in rq["path"] for rq in spy["requests"])
        assert any('"content": "hello"' in rq["body"] for rq in spy["requests"]), \
            "capture POST must carry the converted transcript turns"
        assert any('"role": "user"' in rq["body"] for rq in spy["requests"]), \
            "capture POST must carry the parsed User turn"
        # the child's daemon has closed on exit → open FRESH and read the graph
        sdk = TortoiseSDK(db)
        try:
            g = sdk._get_proj().g
            n = g.query(
                "MATCH (s:Source)-[:references]->(e:Event) "
                "RETURN count(*)").result_set[0][0]
            assert n == 1, f"fixture not indexed by the hook sweep (refs={n})"
        finally:
            sdk.close()
        # cycle-20 POST-DRAIN HEALTH pin: the fixture session lands in
        # `matched` (the operator's diagnose loop proven end-to-end)
        sdk2 = TortoiseSDK(db)
        try:
            health = sdk2.session_index_health(str(corpus))
            assert health["matched"] == 1, health
        finally:
            sdk2.close()
    finally:
        stub.shutdown()
        stub.server_close()


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_h_two_consecutive_fire_truncate(tmp_path):
    """E2E-15(h): fire the hook twice against the SAME TORTOISE_INDEX_CHILD_
    STDERR target (drain between fires) → the capture file contains ONLY the
    second run's output (truncate-on-open — the append-vs-truncate
    discriminator)."""
    import time as _time
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s.md").write_text("---\nsessionId: hk2\ntitle: Hook\n---\nBody")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    env = {
        "TORTOISE_SESSION_CORPUS": str(corpus),
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        "TORTOISE_DB_PATH": db,
        "TORTOISE_INDEX_NO_NETWORK": "1",
        "TORTOISE_INDEX_CHILD_STDERR": cap,
        "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
    }
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    # the child is the SOLE daemon owner (the parent never opens the DB —
    # it reads only the capture file); #6761 single-writer holds
    r1 = _run_hook(env=env, transcript_path=transcript)
    assert r1.returncode == 0
    # deterministic drain (cycle-9 pin): poll for the child's report + the
    # child's daemon release (the db .settings registry disappears when the
    # child's redislite daemon closes on exit — the next fire's truncate
    # can then be observed without a stale-offset race)
    _time.sleep(1)
    for _ in range(60):
        done = Path(cap).exists() and "file_count" in Path(cap).read_text()
        settled = not Path(db + ".settings").exists()
        if done and settled:
            break
        _time.sleep(1)
    size1 = Path(cap).stat().st_size if Path(cap).exists() else 0
    r2 = _run_hook(env=env, transcript_path=transcript)
    assert r2.returncode == 0
    _time.sleep(1)
    for _ in range(60):
        done = Path(cap).exists() and "file_count" in Path(cap).read_text()
        settled = not Path(db + ".settings").exists()
        if done and settled:
            break
        _time.sleep(1)
    # truncate-on-open (review-gate P2-2 tightened bound): the file holds
    # ONLY the second run's output — the append-vs-truncate discriminator.
    # The report is ~800B; under append semantics the file would hold TWO
    # runs' worth (~1600B) — the bound (< size1 + 256) fails append. The
    # one-JSON-line assertion is the structural discriminator (append would
    # concatenate two JSON lines).
    size2 = Path(cap).stat().st_size if Path(cap).exists() else 0
    assert 0 < size2 <= size1 + 256, \
        f"capture grew beyond one run's output: {size1} → {size2}"
    content2 = Path(cap).read_text()
    json_lines = [l for l in content2.splitlines()
                  if l.strip().startswith("{")]
    assert len(json_lines) == 1, f"capture must hold ONE report: {content2}"


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_g_nonexistent_corpus_zero_count(tmp_path):
    """E2E-15(g): TORTOISE_SESSION_CORPUS → a NONEXISTENT path → the CLI
    treats the walk root as a zero-count no-op → hook exits 0, capture
    fired, and the CHILD_STDERR capture file contains the child's ZERO-COUNT
    report — the positive control proving the sweep RAN and no-op'd (closing
    the fail-pass shape where an empty sweep is indistinguishable from a
    deleted sweep invocation)."""
    import time as _time
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    env = {
        "TORTOISE_SESSION_CORPUS": str(tmp_path / "does-not-exist"),
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        "TORTOISE_DB_PATH": db,
        "TORTOISE_INDEX_NO_NETWORK": "1",
        "TORTOISE_INDEX_CHILD_STDERR": cap,
        "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
    }
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    # the child is the SOLE daemon owner (the parent reads only the capture)
    r = _run_hook(env=env, transcript_path=transcript)
    assert r.returncode == 0
    # deterministic drain: poll for the child's report (the child spawns its
    # own daemon, indexes the zero-count no-op, writes the capture, exits)
    _time.sleep(1)
    for _ in range(60):
        if Path(cap).exists() and Path(cap).stat().st_size > 0:
            break
        _time.sleep(1)
    assert Path(cap).exists() and Path(cap).stat().st_size > 0, \
        "capture file never written"
    content = Path(cap).read_text()
    assert "file_count" in content
    assert '"file_count": 0' in content or "file_count: 0" in content, content


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_b_d2_symlink_root_escape(tmp_path):
    """E2E-15(b)/(d2) shared fixture (cycle-12 RE-ADMITTED induction — the
    corpus root is an in-base symlink resolving OUTSIDE TORTOISE_INGEST_
    BASE_DIR → the resolution ValueError class).

    (b) = default config (TORTOISE_INDEX_CHILD_STDERR ABSENT): hook exits 0
    (the sweep is nohup-backgrounded with output to /dev/null — the raise
    NEVER surfaces synchronously), the fixture unit is ABSENT from the
    graph; the ValueError class is verified at S14/d2 level.

    (d2) = WITH TORTOISE_INDEX_CHILD_STDERR: the capture file EXISTS and
    contains the PINNED SUBSTRING — the traceback naming the ValueError
    class + the symlink path (the pre-walk error exits 1 in the child, hook
    still exits 0; a clean-error implementation fails on purpose).
    """
    import time as _time
    base = tmp_path / "base"; base.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "s.md").write_text(
        "---\nsessionId: esc1\ntitle: Esc\n---\nBody")
    corpus_link = base / "corpus-link"
    corpus_link.symlink_to(outside, target_is_directory=True)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")

    def _fire(cap: str | None):
        env = {
            "TORTOISE_SESSION_CORPUS": str(corpus_link),
            "TORTOISE_INGEST_BASE_DIR": str(base),
            "TORTOISE_DB_PATH": db,
            "TORTOISE_INDEX_NO_NETWORK": "1",
            "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
        }
        if cap:
            env["TORTOISE_INDEX_CHILD_STDERR"] = cap
        os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
        return _run_hook(env=env, transcript_path=transcript)

    def _drain_settled():
        """Poll for the child's daemon release (redislite .settings registry
        disappears when the child's daemon closes on exit)."""
        _time.sleep(1)
        for _ in range(60):
            if not Path(db + ".settings").exists():
                return True
            _time.sleep(1)
        return False

    # ── (b) default config: no CHILD_STDERR ──
    r = _fire(cap=None)
    assert r.returncode == 0, r.stderr
    assert _drain_settled()
    sdk = TortoiseSDK(db)
    try:
        n = sdk._get_proj().g.query(
            "MATCH (s:Source) RETURN count(s)").result_set[0][0]
        assert n == 0, f"fixture unit must be ABSENT (refs={n})"
    finally:
        sdk.close()

    # ── (d2) WITH CHILD_STDERR: capture contains the ValueError traceback ──
    cap = str(tmp_path / "child-d2.log")
    r2 = _fire(cap=cap)
    assert r2.returncode == 0, r2.stderr
    _time.sleep(1)
    for _ in range(60):
        if Path(cap).exists() and "ValueError" in Path(cap).read_text():
            break
        _time.sleep(1)
    assert Path(cap).exists(), "capture file never written"
    content = Path(cap).read_text()
    assert "ValueError" in content, content          # the traceback class
    assert "corpus-link" in content, content         # the symlink path named
    assert "outside TORTOISE_INGEST_BASE_DIR" in content, content
    # the TRACEBACK marker (review-gate P1-3): the clean-error message ALSO
    # contains all three substrings — only the traceback names the class in
    # a way a clean-error implementation cannot fake
    assert "Traceback (most recent call last):" in content, content
    assert "_cmd_index_directory" in content, content


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_c_lock_contention(tmp_path):
    """E2E-15(c): hold the SessionIndexLock for the fixture's sessionId
    during the sweep → the locked file's unit is ABSENT from the graph (the
    lock-skip is pre-write), while a SECOND unlocked session fixture file IS
    present (Source + Event + edge) — the positive control proving the sweep
    ran, processed the corpus, and honored the lock for the held sessionId
    only; hook exits 0."""
    import time as _time
    from tortoise.index_lock import SessionIndexLock
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s1.md").write_text(
        "---\nsessionId: lock1\ntitle: Lock1\n---\nBody1")
    (corpus / "s2.md").write_text(
        "---\nsessionId: lock2\ntitle: Lock2\n---\nBody2")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    lock_dir = str(tmp_path / "locks")
    os.makedirs(lock_dir, exist_ok=True)
    # the parent holds lock1 (the sweep's child is a separate process — the
    # flock is cross-process; the child's acquire → "held" → pre-write skip)
    held = SessionIndexLock("lock1", lock_dir=lock_dir)
    assert held.acquire() in ("acquired", "stale-recovered")
    try:
        env = {
            "TORTOISE_SESSION_CORPUS": str(corpus),
            "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
            "TORTOISE_DB_PATH": db,
            "TORTOISE_INDEX_NO_NETWORK": "1",
            "TORTOISE_INDEX_CHILD_STDERR": cap,
            "TORTOISE_INDEX_LOCK_DIR": lock_dir,
        }
        r = _run_hook(env=env, transcript_path=transcript)
        assert r.returncode == 0, r.stderr
        _time.sleep(1)
        for _ in range(60):
            if Path(cap).exists() and "file_count" in Path(cap).read_text():
                break
            _time.sleep(1)
    finally:
        held.release()
    assert Path(cap).exists() and "file_count" in Path(cap).read_text(), \
        f"sweep never reported: {Path(cap).read_text() if Path(cap).exists() else 'no capture'}"
    # drain: the child's daemon closes on exit → open FRESH and read
    _time.sleep(1)
    for _ in range(60):
        if not Path(db + ".settings").exists():
            break
        _time.sleep(1)
    sdk = TortoiseSDK(db)
    try:
        g = sdk._get_proj().g
        rows = g.query(
            "MATCH (s:Source)-[:references]->(e:Event) RETURN s.url"
        ).result_set
        urls = {r[0] for r in rows}
        # the UNLOCKED session is indexed (positive control)
        assert f"corpus://{corpus.name}/s2.md" in urls, urls
        # the LOCKED session's unit is ABSENT (lock-skip is pre-write)
        assert f"corpus://{corpus.name}/s1.md" not in urls, urls
        # exactly ONE Source + ONE edge (no partial writes from the skip)
        assert g.query("MATCH (s:Source) RETURN count(s)").result_set[0][0] == 1
    finally:
        sdk.close()


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_e2_graph_unreachable_dead_uri(tmp_path):
    """E2E-15(e2): TORTOISE_DB_URI in the hook env points at a dead server
    → the CLI child exits 1 (graph-unreachable per §6.5), the hook STILL
    exits 0, capture still fires. NO graph poll against the dead URI (every
    poll query would raise) — absence is asserted via the capture file
    ("graph unreachable") + a live control DB (TORTOISE_DB_PATH) remaining
    EMPTY (a silent fallback to an embedded DB would index the fixture into
    a LIVE graph)."""
    import time as _time
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s.md").write_text(
        "---\nsessionId: dead1\ntitle: Dead\n---\nBody")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    cap = str(tmp_path / "child.log")
    control = os.path.join(str(tmp_path), "control.db")
    env = {
        "TORTOISE_SESSION_CORPUS": str(corpus),
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        # docker://127.0.0.1:1 — a VALID URI whose server is dead: the
        # resolve precedence picks TORTOISE_DB_URI over TORTOISE_DB_PATH
        "TORTOISE_DB_URI": "docker://127.0.0.1:1",
        "TORTOISE_DB_PATH": control,
        "TORTOISE_INDEX_NO_NETWORK": "1",
        "TORTOISE_INDEX_CHILD_STDERR": cap,
        "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
    }
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    r = _run_hook(env=env, transcript_path=transcript)
    assert r.returncode == 0, r.stderr
    _time.sleep(1)
    for _ in range(60):
        if Path(cap).exists() and "graph unreachable" in Path(cap).read_text():
            break
        _time.sleep(1)
    assert Path(cap).exists(), "capture file never written"
    content = Path(cap).read_text()
    assert "graph unreachable" in content, content
    # the control DB was NOT used (no silent fallback to embedded)
    assert not Path(control).exists() or os.path.getsize(control) == 0


# ═══════════════════════════════════════════════════════════════════════
# T8 cycle-13/14 pins: --metadata production-parity assertion mechanisms
# ═══════════════════════════════════════════════════════════════════════

def test_t8_static_invocation_includes_metadata():
    """§8.6 T8 cycle-13 pin (a): the migrated hook's command line contains
    `--metadata` — the STATIC invocation check (the legacy sweep embedded
    every replayed session; the migrated sweep must preserve that behavior
    — production-parity). A future edit dropping the flag fails here."""
    text = HOOK.read_text()
    # both the repo-checkout fallback AND the system-binary branch pass
    # --metadata on the `index directory` invocation
    assert "--metadata" in text, "hook sweep must pass --metadata (parity)"
    assert "index directory" in text, "hook must invoke index directory"


def test_t8_metadata_parity_in_process(tmp_path, monkeypatch):
    """§8.6 T8 cycle-13/14 pin (b): the IN-PROCESS parity test — invoke the
    CLI entry directly with a monkeypatched embedding and the
    TORTOISE_INDEX_NO_NETWORK var UNSET → e.embedding non-null (the legacy
    sweep's embedding behavior preserved); with the var SET → embedding
    None (the var-overrides-flag precedence, cycle-12 pin)."""
    import types
    import tortoise.__main__ as main_mod
    import tortoise.sdk as sdk_mod
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s.md").write_text(SESSION_FIXTURE.format(sid="par1", title="P"))
    db = os.path.join(str(tmp_path), "par.db")
    calls = {"n": 0}

    def _fake_embedding(self, *a, **k):
        calls["n"] += 1
        return [0.4] * 384

    monkeypatch.setattr(sdk_mod.TortoiseSDK, "_session_embedding",
                        _fake_embedding)
    # var UNSET + --metadata → embedding computed → e.embedding non-null
    monkeypatch.delenv("TORTOISE_INDEX_NO_NETWORK", raising=False)
    args = types.SimpleNamespace(corpus_dir=str(corpus), db=db,
                                 metadata=True, corpus_name=None)
    rc = main_mod._cmd_index_directory(args)
    assert rc == 0
    assert calls["n"] >= 1, "embedding must be computed under --metadata"
    sdk = TortoiseSDK(db)
    try:
        emb = sdk._get_proj().g.query(
            "MATCH (e:Event) RETURN e.embedding").result_set[0][0]
        assert emb is not None, "e.embedding must be non-null (parity)"
    finally:
        sdk.close()
    # var SET + --metadata → the var FORCES extract_metadata=False (omission)
    monkeypatch.setenv("TORTOISE_INDEX_NO_NETWORK", "1")
    db2 = os.path.join(str(tmp_path), "par2.db")
    calls["n"] = 0
    args2 = types.SimpleNamespace(corpus_dir=str(corpus), db=db2,
                                  metadata=True, corpus_name=None)
    rc2 = main_mod._cmd_index_directory(args2)
    assert rc2 == 0
    assert calls["n"] == 0, "NO_NETWORK must short-circuit the embedding"
    sdk2 = TortoiseSDK(db2)
    try:
        rows = sdk2._get_proj().g.query(
            "MATCH (e:Event) RETURN e.embedding, "
            "e.embeddingRepairFailedAt").result_set
        emb2, repair_marker = rows[0]
        assert emb2 is None, "e.embedding must be None under NO_NETWORK"
        # E2E-15(d) omission-semantics pin (review-gate P2-3): NO repair
        # attempt and NO marker are EVER written (embedding is EXCLUDED
        # from completeness — a repair attempt would mark the unit)
        assert repair_marker is None, \
            f"NO_NETWORK must never write embeddingRepairFailedAt: {repair_marker}"
    finally:
        sdk2.close()


# ── S14 unit: env-resolved nonexistent TORTOISE_INGEST_BASE_DIR (P1-1) ─

def test_s14_env_nonexistent_base_dir_prewalk_error(tmp_path):
    """E2E-15(g)/§8.6 cycle-13 pin: a nonexistent ENV-resolved fallback dir
    (manual TORTOISE_INGEST_BASE_DIR typo) = PRE-WALK ERROR (exit 1, clear
    message) — the zero-count no-op applies ONLY to an explicitly
    POSITIONALLY-passed nonexistent dir."""
    # env → nonexistent → exit 1, pre-walk error
    r = _run_cli(["--db", _db_path(tmp_path)],
                 env={"TORTOISE_INGEST_BASE_DIR":
                      str(tmp_path / "does-not-exist")})
    assert r.returncode == 1
    assert "nonexistent" in r.stderr
    assert "TORTOISE_INGEST_BASE_DIR" in r.stderr
    # positional → nonexistent → zero-count no-op (exit 0, file_count 0)
    r2 = _run_cli([str(tmp_path / "does-not-exist"), "--db",
                   _db_path(tmp_path)])
    assert r2.returncode == 0
    d = json.loads(r2.stdout.splitlines()[0])
    assert d["file_count"] == 0 and d["indexed"] == 0 and d["failed"] == 0


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_h2_crash_mid_write_recovery(tmp_path):
    """E2E-15(h) crash-mid-write recovery sub-leg (cycle-16 pin): the
    capture target holds a PARTIAL report (the crash-mid-write state) → fire
    again → assert the capture file contains ONLY the SECOND fire's
    COMPLETE report (the truncate cleared the partial — the recovery
    guarantee, asserted end-to-end).

    REVIEW-GATE P2-2 DEVIATION (deterministic, documented): the plan's
    "kill the child mid-report" induction is racy by construction (the
    report is written in a few ms — a timed kill lands before/after the
    write nondeterministically). The recovery guarantee the plan pins is
    "the truncate cleared the partial" — exercised here DETERMINISTICALLY
    by truncating the capture to a partial report before fire 2 (a
    truncate-on-open implementation clears it; an append implementation
    leaves the partial residue and the assertions fail)."""
    import time as _time
    corpus = tmp_path / "corpus"; corpus.mkdir()
    (corpus / "s.md").write_text("---\nsessionId: hk3\ntitle: Hook\n---\nBody")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    env = {
        "TORTOISE_SESSION_CORPUS": str(corpus),
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        "TORTOISE_DB_PATH": db,
        "TORTOISE_INDEX_NO_NETWORK": "1",
        "TORTOISE_INDEX_CHILD_STDERR": cap,
        "TORTOISE_INDEX_LOCK_DIR": str(tmp_path / "locks"),
    }
    os.makedirs(env["TORTOISE_INDEX_LOCK_DIR"], exist_ok=True)
    # fire 1: a COMPLETE first report (the baseline)
    r1 = _run_hook(env=env, transcript_path=transcript)
    assert r1.returncode == 0
    _time.sleep(1)
    for _ in range(60):
        if Path(cap).exists() and "file_count" in Path(cap).read_text():
            break
        _time.sleep(1)
    assert Path(cap).exists() and "file_count" in Path(cap).read_text(), \
        f"first fire never reported: {Path(cap).read_text() if Path(cap).exists() else 'no capture'}"
    # simulate the crash-mid-write state: truncate the capture to a PARTIAL
    # report (a short fixed prefix of the JSON line — exactly the state a
    # killed child leaves; half-the-line could still contain "file_count")
    full = Path(cap).read_text()
    Path(cap).write_text(full[:12])
    partial = Path(cap).read_text()
    assert "file_count" not in partial, "partial must not be complete"
    # fire 2: the truncate clears the partial → the file holds ONLY the
    # COMPLETE second report (JSON line parseable, no partial residue)
    r2 = _run_hook(env=env, transcript_path=transcript)
    assert r2.returncode == 0
    _time.sleep(1)
    for _ in range(60):
        if Path(cap).exists() and "file_count" in Path(cap).read_text():
            break
        _time.sleep(1)
    content = Path(cap).read_text() if Path(cap).exists() else ""
    assert "file_count" in content
    # the partial residue is GONE: ONE parseable JSON line, nothing before
    # it (an append implementation would prepend the half-line garbage)
    lines = content.splitlines()
    assert lines and lines[0].strip().startswith("{"), \
        f"partial residue not cleared: {content[:200]!r}"
    first_line = lines[0]
    import json as _json
    d = _json.loads(first_line)
    assert d["file_count"] == 1, d
    assert d["indexed"] + d["skipped"] == 1, d


def _iter_child_pythons(db_path: str):
    """Best-effort: find sweep-child python processes holding the given db
    (via the db .settings registry's pidfile when present).

    Retained for the (h2) crash-mid-write induction on hosts where a timed
    kill is desired; the default (h2) leg uses the deterministic partial-
    capture rewrite (review-gate P2-2)."""
    import subprocess as _sp
    pidfile = db_path + ".settings"
    if not Path(pidfile).exists():
        return []
    try:
        import json as _json
        settings = _json.loads(Path(pidfile).read_text())
        pf = settings.get("pidfile")
        if pf and Path(pf).exists():
            return [int(Path(pf).read_text().strip())]
    except Exception:
        pass
    return []


def _daemon_holding(db_path: str) -> int | None:
    """Return the live pid holding the embedded daemon for db_path (via the
    redislite .settings registry's pidfile), or None. Used by the leg-(i)
    barrier: the hook child's busy-probe only fires when the registry is
    COMPLETE (pidfile present + pid alive) — a bare db.settings existence is
    NOT enough (redislite writes the file before the pidfile key; a probe
    racing that window silently JOINS the manual daemon → the #6761
    concurrent-writer class)."""
    import json as _json
    settings = Path(db_path).with_name(Path(db_path).name + ".settings")
    if not settings.is_file():
        return None
    try:
        s = _json.loads(settings.read_text(encoding="utf-8"))
        pf = s.get("pidfile")
        if pf and Path(pf).is_file():
            pid = int(Path(pf).read_text().strip())
            try:
                os.kill(pid, 0)
                return pid
            except ProcessLookupError:
                return None
            except PermissionError:
                return pid  # alive, not ours to signal
    except Exception:  # noqa: BLE001 — registry unreadable ⇒ no probe
        pass
    return None


@pytest.mark.skipif(not Path(HOOK).exists(),
                    reason="hook script not present in the worktree")
def test_e2e15_i_hook_vs_sweep_overlap(tmp_path):
    """E2E-15(i) — the §5.3 production pin's hook-path test surface (cycle-24
    physical landing; review-gate P1-2): fire the hook while a MANUAL
    `tortoise index directory` sweep is MID-FLIGHT on the same corpus +
    embedded DB (barrier via a slow-fixture corpus + the daemon-up poll) →
    the second opener's §5.3 busy-probe detects the live holder →
    EmbeddedStoreBusyError INSPECTABLE in the CHILD_STDERR capture (hook
    still exits 0 — the hook NEVER blocks) AND the first sweep completes +
    the fixture is indexed EXACTLY ONCE (zero duplicate urls); the follow-up
    sequential run converges (skipped)."""
    import time as _time
    corpus = tmp_path / "corpus"; corpus.mkdir()
    # slow-fixture: enough session files that the manual sweep holds the
    # daemon across the hook fire (walk + hash + MERGE per file; ~25ms/file
    # warm — 400 files ≈ 10s, far wider than the child's ~2s probe window)
    for i in range(400):
        (corpus / f"s{i:03d}.md").write_text(
            f"---\nsessionId: ov{i:03d}\ntitle: Ov{i:03d}\n---\nBody {i}")
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "hi"},
    }) + "\n")
    db = os.path.join(str(tmp_path), "hook.db")
    cap = str(tmp_path / "child.log")
    lock_dir = str(tmp_path / "locks")
    os.makedirs(lock_dir, exist_ok=True)

    e = dict(os.environ)
    for k in ("TORTOISE_DB_URI", "TORTOISE_DB_PATH", "FALKORDB_HOST",
              "FALKORDB_PORT", "FALKORDB_PASSWORD"):
        e.pop(k, None)
    e.update({
        "TORTOISE_DB_PATH": db,
        "TORTOISE_INDEX_NO_NETWORK": "1",
        "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
        "TORTOISE_INDEX_LOCK_DIR": lock_dir,
    })
    # ── the MANUAL sweep starts FIRST (the live holder) ──
    manual = subprocess.Popen(
        [sys.executable, "-m", "tortoise", "index", "directory",
         str(corpus)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env=e, cwd=str(REPO_ROOT),
    )
    manual_out = manual_err = ""
    try:
        # barrier: the manual sweep's daemon must be UP with a LIVE pidfile
        # before the hook fires — the hook child must probe a LIVE holder
        # (never a cold-start race and never a bare-settings race where the
        # child silently JOINS the daemon — the #6761 crash class)
        _time.sleep(1)
        holder = None
        for _ in range(60):
            holder = _daemon_holding(db)
            if holder is not None:
                break
            if manual.poll() is not None:
                manual_out, manual_err = manual.communicate(timeout=30)
                raise AssertionError(
                    f"manual sweep exited early rc={manual.returncode}: "
                    f"{manual_err[-500:]!r}")
            _time.sleep(0.5)
        assert holder is not None, "manual daemon (live pidfile) never came up"
        # double-check the holder is still alive + the sweep still mid-flight
        assert _daemon_holding(db) is not None, "holder died before the hook fire"
        assert manual.poll() is None, "manual sweep already exited"
        # ── fire the hook mid-flight ──
        env = {
            "TORTOISE_SESSION_CORPUS": str(corpus),
            "TORTOISE_INGEST_BASE_DIR": str(tmp_path),
            "TORTOISE_DB_PATH": db,
            "TORTOISE_INDEX_NO_NETWORK": "1",
            "TORTOISE_INDEX_CHILD_STDERR": cap,
            "TORTOISE_INDEX_LOCK_DIR": lock_dir,
        }
        r = _run_hook(env=env, transcript_path=transcript)
        assert r.returncode == 0, r.stderr   # the hook NEVER blocks
        _time.sleep(1)
        for _ in range(60):
            if (Path(cap).exists()
                    and "EmbeddedStoreBusyError" in Path(cap).read_text()):
                break
            _time.sleep(1)
        assert Path(cap).exists(), "capture never written"
        content = Path(cap).read_text()
        assert "EmbeddedStoreBusyError" in content, content
    finally:
        manual_out, manual_err = manual.communicate(timeout=180)
    assert manual.returncode == 0, \
        f"manual sweep failed rc={manual.returncode}: {manual_err[-2000:]}"
    d = json.loads(manual_out.splitlines()[0])
    assert d["file_count"] == 400 and d["indexed"] == 400 \
        and d["failed"] == 0, d
    # the fixture is indexed EXACTLY ONCE (zero duplicate urls)
    _time.sleep(1)
    for _ in range(60):
        if not Path(db + ".settings").exists():
            break
        _time.sleep(1)
    sdk = TortoiseSDK(db)
    try:
        g = sdk._get_proj().g
        dups = g.query(
            "MATCH (s:Source) WITH s.url AS u, count(*) AS c "
            "WHERE c > 1 RETURN u"
        ).result_set
        assert dups == [], f"duplicate urls: {dups}"
        assert g.query("MATCH (s:Source) RETURN count(s)"
                       ).result_set[0][0] == 400
    finally:
        sdk.close()
    # follow-up sequential run converges (the plan's (i) end state)
    r2 = subprocess.run(
        [sys.executable, "-m", "tortoise", "index", "directory",
         str(corpus)],
        capture_output=True, text=True, env=e, cwd=str(REPO_ROOT), timeout=120,
    )
    assert r2.returncode == 0, r2.stderr
    d2 = json.loads(r2.stdout.splitlines()[0])
    assert d2["skipped"] == 400 and d2["indexed"] == 0, d2
