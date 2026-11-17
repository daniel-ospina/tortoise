"""#13084 — the census tool's refusals and its rebuild, pinned without a store.

The measurement halves are covered by `test_13084_demand_census.py` (the ledger
fold) and by the run recorded on the PR (the restore probe against a lane
container). What must not drift here is the set of things the tool REFUSES to do
(a union of tenants, an unconfirmed graph rebuild, a census whose ledger does not
exist — an unmeasurable census must never exit 0, #13102 F5) and the ONE
destructive step it takes: `--restore-probe` must never DELETE the graph's key,
and a payload the engine rejects must leave that key intact (#13102 F2).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO))
if str(REPO / "tools") not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(REPO / "tools"))

import ram_window_census as census  # noqa: E402


def test_a_ledger_without_a_tenant_is_a_usage_error(capsys):
    """`f` and the mix are PER TENANT; a pooled read is a wrong number."""
    assert census.main(["--ledger", "/tmp/whatever.jsonl"]) == census.EXIT_USAGE
    err = capsys.readouterr().err
    assert "PER TENANT" in err


def test_the_restore_probe_refuses_without_an_exact_confirm(tmp_path, capsys):
    """The probe REWRITES the measured graph's key: a dry run names its confirm."""
    out = tmp_path / "r.json"
    rc = census.main(["--restore-probe", "--graph-uri", "docker://h:1/g",
                      "--graph", "g", "--json"])
    assert rc == census.EXIT_UNMEASURABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload["cold_restore"]["dry_run"] is True
    assert "--confirm-graph 'g'" in payload["cold_restore"]["error"]
    # and the recorded reference is quoted as CONTEXT, never as this graph's figure
    assert (payload["cold_restore"]["recorded_reference"]["index_dropped_s"]
            == 0.03)
    assert not out.exists()


def test_a_restore_probe_without_a_graph_uri_is_a_usage_error(capsys):
    assert census.main(["--restore-probe", "--graph", "g"]) == census.EXIT_USAGE
    assert "requires --graph-uri" in capsys.readouterr().err


def test_a_missing_ledger_is_unmeasurable_not_a_silent_success(tmp_path, capsys):
    """#13102 F5: a census that measured NOTHING must not exit 0 and print nothing.

    ``_ledger_section`` returns ``{"error": ...}`` for a path that does not
    exist; it was merged into the report and then only ``cold_restore`` was
    inspected, so ``--ledger /nope.jsonl`` exited ``EXIT_OK`` with ZERO stdout
    lines in human mode — the same "a missing file is not a zero reading" rule
    the fold asserts, broken one layer up.

    Mutation this REDs: restore the cold-restore-only check (or drop the error
    print) — the exit code becomes 0 and/or the render is empty.
    """
    missing = tmp_path / "nope.jsonl"
    assert census.main(["--ledger", str(missing), "--tenant", "t1"]) == \
        census.EXIT_UNMEASURABLE
    out = capsys.readouterr().out
    assert "no ledger at" in out, out

    # the same error is reported (and the exit code unchanged) under --json
    assert census.main(["--ledger", str(missing), "--tenant", "t1",
                        "--json"]) == census.EXIT_UNMEASURABLE
    payload = json.loads(capsys.readouterr().out)
    assert "no ledger at" in payload["error"]


def test_the_human_render_prints_the_seed_holes_beside_n_a(tmp_path, capsys):
    """`n/a` alone cannot be told from "no traversals recorded yet" (#13102 F).

    On this tree `n/a` is the EXPECTED reading — no shipped door records a
    start-key seed (see the tool's own limits) — so the operator running the
    census must see WHY the rate is missing. The hole count used to be
    json-only, while the docstring promised it beside the rate.

    Mutation this REDs: drop the `seed holes` print from the human branch (it
    was json-only until this fix) — the assertion below finds a bare `n/a`.
    """
    ledger = tmp_path / "l.jsonl"
    ledger.write_text(
        json.dumps({"event": "query", "kind": "traversal", "label": "Point",
                    "returned": 2, "seed": None,
                    "at": "2026-11-16T10:00:00+00:00", "tenant": "t1"}) + "\n",
        encoding="utf-8")

    assert census.main(["--ledger", str(ledger), "--tenant", "t1"]) == 0
    out = capsys.readouterr().out
    assert "seed hit rate   : n/a" in out
    assert "seed holes" in out, out
    assert "1 traversal(s) with no seed key" in out, out


# ── the destructive branch: NO DEL, and a rejected payload leaves the key ──

class _RestoreProbeClient:
    """The probe's client, faked: records every command that touches the key.

    ``restore_error`` makes the engine's ``RESTORE`` raise, which is the case the
    pre-fix probe turned into DATA LOSS: it had already issued ``DEL`` and the
    payload lived only in the discarded stack frame.
    """

    def __init__(self, *, restore_error: Exception | None = None,
                 payload: object = b"PAYLOAD") -> None:
        self.calls: list[tuple] = []
        self._restore_error = restore_error
        self._payload = payload
        self.closed = False

    def execute_command(self, *args):
        self.calls.append(args)
        return [b"g"]  # GRAPH.LIST -> the graph exists

    def dump(self, graph):
        self.calls.append(("DUMP", graph))
        return self._payload

    def delete(self, graph):  # recorded, then asserted ABSENT
        self.calls.append(("DEL", graph))

    def restore(self, graph, ttl, payload, **kwargs):
        self.calls.append(("RESTORE", graph, ttl, payload, kwargs))
        if self._restore_error is not None:
            raise self._restore_error

    def close(self):
        self.closed = True


def _install_probe(monkeypatch, client):
    monkeypatch.setattr(census, "_endpoint",
                        lambda _uri: type("EP", (), {"graph_name": None})())
    monkeypatch.setattr(census, "_connection", lambda _ep: (client, object()))


def _probe(monkeypatch, client, capsys, *, extra=()):
    _install_probe(monkeypatch, client)
    rc = census.main(["--restore-probe", "--graph-uri", "docker://h:1/g",
                      "--graph", "g", "--confirm-graph", "g", "--json",
                      *extra])
    return rc, json.loads(capsys.readouterr().out)


def test_the_restore_probe_NEVER_deletes_the_graph(monkeypatch, capsys):
    """#13102 F2 (P1): a measurement must not DEL a tenant's graph to time it.

    `RESTORE` validates the payload's RDB version and checksum BEFORE it creates
    the key, so `RESTORE … REPLACE` rebuilds in place and a rejected payload
    leaves the live graph untouched — the reason the separate `DEL` was removed.

    Mutation this REDs: put `client.delete(graph)` back before the restore (the
    pre-fix shape) — a `("DEL", "g")` call is recorded and the assertion fails.
    """
    client = _RestoreProbeClient()
    rc, report = _probe(monkeypatch, client, capsys)

    assert rc == census.EXIT_OK
    verbs = [c[0] for c in client.calls if c[0] != "GRAPH.LIST"]
    assert "DEL" not in verbs, f"the probe deleted the graph: {client.calls}"
    restore = next(c for c in client.calls if c[0] == "RESTORE")
    assert restore[4].get("replace") is True, (
        "RESTORE must carry replace=True; without it a live key makes every "
        f"rebuild fail: {restore}")
    cold = report["cold_restore"]
    assert cold["rebuild"] == "restore-replace"
    # an absent step is not a zero-second step
    assert cold["del_s"] is None
    assert cold["bytes"] == len(b"PAYLOAD")
    assert client.closed, "the probe must still close its client"


def test_the_human_render_says_why_there_is_no_del_figure(monkeypatch, capsys):
    """A bare `del None` would read as a broken measurement, not as a REPLACE."""
    client = _RestoreProbeClient()
    _install_probe(monkeypatch, client)
    assert census.main(["--restore-probe", "--graph-uri", "docker://h:1/g",
                        "--graph", "g", "--confirm-graph", "g"]) == \
        census.EXIT_OK
    out = capsys.readouterr().out
    assert "in place" in out, out
    assert "None" not in out, out


def test_a_rejected_payload_is_UNMEASURABLE_and_keeps_the_graph(monkeypatch,
                                                                capsys):
    """#13102 F2 (P1): the pre-fix probe turned this into an unrecoverable loss.

    `payload` lived only in a local, `DEL` had already run, and there was no
    `except` — the engine's error escaped `main` as a traceback (exit 1, a number
    the tool does not own) with the only copy of the graph discarded with the
    stack frame.

    Mutation this REDs: drop the `except` around `client.restore(...)` — the
    exception escapes `main` and the test raises instead of reading exit 3.
    """
    client = _RestoreProbeClient(
        restore_error=RuntimeError("Bad data format"))
    rc, report = _probe(monkeypatch, client, capsys)

    assert rc == census.EXIT_UNMEASURABLE
    assert "DEL" not in [c[0] for c in client.calls], client.calls
    cold = report["cold_restore"]
    assert "Bad data format" in cold["error"], cold
    assert cold["graph"] == "g"
    assert cold["bytes"] == len(b"PAYLOAD")
    assert client.closed
    # and the human render prints the failure rather than nothing
    client2 = _RestoreProbeClient(
        restore_error=RuntimeError("Bad data format"))
    _install_probe(monkeypatch, client2)
    assert census.main(["--restore-probe", "--graph-uri", "docker://h:1/g",
                        "--graph", "g", "--confirm-graph", "g"]) == \
        census.EXIT_UNMEASURABLE
    assert "Bad data format" in capsys.readouterr().out


def test_a_decoded_payload_is_refused_before_anything_is_written(monkeypatch,
                                                                capsys):
    """A `str` DUMP reply cannot pass `RESTORE`'s binary checksum.

    The client is built with `decode_responses=True`, so a payload that arrives
    decoded is refused BEFORE the restore call — with the live key untouched —
    instead of being sent on to fail (or, under the pre-fix `DEL`-first shape,
    to lose the graph).
    """
    client = _RestoreProbeClient(payload="decoded-not-bytes")
    rc, report = _probe(monkeypatch, client, capsys)

    assert rc == census.EXIT_UNMEASURABLE
    verbs = [c[0] for c in client.calls]
    assert "RESTORE" not in verbs and "DEL" not in verbs, client.calls
    assert "not bytes" in report["cold_restore"]["error"]
