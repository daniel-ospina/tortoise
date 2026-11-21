"""#14186 — the local embed WORKER process behind ``TORTOISE_EMBED_BACKING=worker``.

WHAT THIS IS. A tiny server that owns one ``EmbeddingModel`` in its OWN process
and answers newline-delimited JSON requests over a UNIX socket::

    →  {"texts": ["why did the retention window change", "…"]}
    ←  {"vectors": [[0.013, …], …]}          # one row per text, in order
    ←  {"error": "load_failed: …"}           # the encoder could not answer

WHY A PROCESS RATHER THAN A THREAD (the reason it is worth a whole module).
The serving process pays for the embedder in three ways that a worker removes:
its RSS (432 MB, and 3.66 GB when two loads overlapped — #13674), its cold
import (156.5 s, paid once per boot instead of once per process incarnation),
and the forward pass's own GIL traffic on the event loop's cores. A worker makes
the API tier **disposable** — the property SCALING-ROADMAP §2.5 takes from
Hindsight's Memlake ("servers are disposable caches") and which Track S1 needs
before replicas are cheap.

⛔ IT IS NOT A FALLBACK PATH, AND IT DOES NOT FALL BACK. The client
(:class:`tortoise.embed_seam.WorkerBacking`) records a decline and the caller
degrades; nothing re-encodes in-process. A worker that quietly stepped aside
would make "the embedder is out of the request" false while every surface said
ok — the #7795 class.

LIFECYCLE — it cannot outlive its parent. ``stdin`` is a pipe held open by the
parent; the reader thread below blocks on it and exits the process on EOF (or on
the parent-died heartbeat), so an OOM-kill or a SIGKILL of the lane cannot leave
an orphaned worker holding 432 MB. It also removes its socket on exit, so a
stale path cannot make a later start-up look healthy when it is not.

THE PIN IS ASSERTED IN THIS PROCESS, ON THE ENCODING THREAD. ``EmbeddingModel``'s
``ensure_embed_threads()`` runs before every forward pass, exactly as it does
in-process (#10296): ``torch.set_num_threads`` follows the CALLING thread's own
context, so the pin has to be re-asserted where the pass runs. This module does
not go looking for a different number — the pin's value is not its business.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time

_logger = logging.getLogger(__name__)

#: How often the parent-liveness check runs when no request is in flight. The
#: stdin reader is the primary mechanism (it returns the instant the pipe
#: closes); this is the belt for a parent that was killed in a way that left the
#: write end open in a reparented grandchild, which the pipe alone cannot see.
_HEARTBEAT_S = 1.0


def _parent_alive(parent_pid: int) -> bool:
    """Whether ``parent_pid`` still exists. Total: an unreadable probe is ALIVE.

    ⛔ Fail-safe direction: a probe that raises (a platform without ``kill``, a
    permission error) must not make the worker exit and take the embedder with
    it. The pipe is the primary signal; this only asserts a death it can prove.
    """
    if parent_pid <= 0:
        return True
    try:
        os.kill(parent_pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _watch_stdin(stop: threading.Event) -> None:
    """Exit the process when the parent closes our stdin (i.e. when it dies)."""
    try:
        while not stop.is_set():
            chunk = sys.stdin.buffer.read(1)
            if not chunk:
                break
    except Exception:
        pass
    _logger.info("embed worker: parent closed stdin — exiting")
    # ``os._exit`` rather than ``sys.exit``: this runs on a daemon thread whose
    # exception would only be printed, and a worker that outlives its parent is
    # the one failure this process must not have.
    os._exit(0)


def _watch_parent(pid: int, stop: threading.Event) -> None:
    """Belt for the pipe: exit when the parent pid is provably gone."""
    while not stop.is_set():
        if not _parent_alive(pid):
            _logger.info("embed worker: parent pid %s is gone — exiting", pid)
            os._exit(0)
        stop.wait(_HEARTBEAT_S)


def _encode(texts: list[str]) -> list[list[float]]:
    """One batch through the in-process model. Raises with a named reason."""
    from tortoise.embeddings import EmbeddingModel, ensure_embed_threads

    model = EmbeddingModel.get()
    if model is None:
        # ⛔ A DECLINE, not an empty answer: the caller must be able to tell
        # "the encoder is unavailable" from "these texts embed to nothing", and
        # an empty ``vectors`` list would make the client's length check raise
        # the far less informative "returned 0 rows".
        raise RuntimeError(
            f"embedder unavailable in the worker ({EmbeddingModel.absent_reason()})")
    ensure_embed_threads()
    vecs = model.encode(list(texts))
    if vecs is None:
        raise RuntimeError("embedder returned no vectors in the worker")
    rows = [
        list(row.tolist()) if hasattr(row, "tolist") else [float(x) for x in row]
        for row in vecs
    ]
    if len(rows) != len(texts):
        raise ValueError(
            f"worker encoder returned {len(rows)} row(s) for {len(texts)} text(s)")
    return rows


def _serve_connection(conn: socket.socket) -> None:
    """Answer one request on ``conn``, then close it.

    One request per connection (the seam's client opens a connection per
    request). Reading until a newline rather than until EOF is what lets the
    client keep the connection half-open for the response; a request larger than
    the cap is refused rather than buffered without limit.
    """
    buffer = b""
    with conn:
        while b"\n" not in buffer:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buffer += chunk
            if len(buffer) > 64 * 1024 * 1024:
                conn.sendall(
                    b'{"error": "request exceeded 64 MiB without a newline"}\n')
                return
        line, _, _rest = buffer.partition(b"\n")
        try:
            request = json.loads(line.decode("utf-8"))
            texts = request.get("texts")
            if not isinstance(texts, list) or not all(
                    isinstance(t, str) for t in texts):
                raise ValueError("`texts` must be a list of strings")
            started = time.monotonic()
            answer = {"vectors": _encode(texts)}
            _logger.info(
                "embed worker: encoded %d text(s) in %.0f ms",
                len(texts), (time.monotonic() - started) * 1000.0)
        except Exception as exc:
            # ⛔ The message goes back on the wire AND is not re-raised: one bad
            # batch must not take the worker down, or a single malformed request
            # would silently move every later read to a decline.
            answer = {"error": f"{type(exc).__name__}: {exc}"[:400]}
        conn.sendall((json.dumps(answer) + "\n").encode("utf-8"))


def serve(socket_path: str, *, parent_pid: int) -> None:
    """Run the worker until the parent dies. Blocks."""
    stop = threading.Event()
    threading.Thread(
        target=_watch_stdin, args=(stop,), name="embed-worker-stdin",
        daemon=True).start()
    threading.Thread(
        target=_watch_parent, args=(parent_pid,), name="embed-worker-parent",
        daemon=True).start()

    if os.path.exists(socket_path):
        # A stale path from a killed predecessor: bind would refuse, and a
        # refusal here looks exactly like "the worker is broken".
        try:
            os.unlink(socket_path)
        except OSError as exc:
            _logger.warning("embed worker: could not clear %s: %s", socket_path, exc)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(socket_path)
        server.listen(16)
        # Create the path BEFORE announcing: the client polls for the file, and
        # an existence check that wins the race against a bind would look like a
        # healthy socket that refuses connections.
        for _attempt in range(500):
            if os.path.exists(socket_path):
                break
            time.sleep(0.01)
        _logger.info(
            "embed worker: listening on %s (parent pid %s)", socket_path, parent_pid)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except OSError:
                break
            try:
                _serve_connection(conn)
            except Exception:
                _logger.warning("embed worker: connection failed", exc_info=True)
    finally:
        server.close()
        with contextlib.suppress(OSError):
            os.unlink(socket_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tortoise.embed_worker",
        description="Local embed worker for #14186's embedder seam.")
    parser.add_argument("--socket", required=True,
                        help="UNIX socket path to listen on")
    parser.add_argument("--parent-pid", type=int, default=os.getppid(),
                        help="exit when this pid is gone (default: the spawner)")
    parser.add_argument("--log-level", default=os.environ.get(
        "TORTOISE_EMBED_WORKER_LOG_LEVEL", "INFO"))
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        serve(args.socket, parent_pid=args.parent_pid)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":  # pragma: no cover — exercised as a subprocess
    raise SystemExit(main())
