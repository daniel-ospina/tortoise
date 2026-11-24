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

READINESS — the socket's EXISTENCE means READY, and nothing weaker. The encoder
is imported and loaded (``_preload_encoder``) BEFORE the socket is bound, so a
client that polls for the path never talks to a process that would have to pay
the 156.5 s cold import inside its first request (#13674). That is the convergent
arrangement (vLLM's preload binds its UNIX socket only once the weights are
ready), and it is what lets the client's start-up wait be a BOOT budget rather
than a request budget.

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
import struct
import sys
import threading
import time

from tortoise.env_truthy import TRUTHY

_logger = logging.getLogger(__name__)

#: How often the parent-liveness check runs when no request is in flight. The
#: stdin reader is the primary mechanism (it returns the instant the pipe
#: closes); this is the belt for a parent that was killed in a way that left the
#: write end open in a reparented grandchild, which the pipe alone cannot see.
_HEARTBEAT_S = 1.0

#: ⛔ MAKE AN UNVERIFIABLE PEER A REFUSAL. ``_peer_allowed`` fails OPEN where the
#: kernel offers no peer credential, because the private ``0700`` socket directory
#: the CLIENT mints for the DEFAULT path is still in force (it is absent for an
#: explicit shared path — see ``_peer_allowed``) and refusing every request would
#: turn a hardening detail into an outage. That default is wrong for the one deployment where the directory does
#: NOT protect the socket — an operator who points the lever at a shared path — so
#: the posture is selectable rather than assumed (review: the peer check was
#: fail-open at two points, with the macOS gap acknowledged and unimplemented).
ENV_WORKER_REQUIRE_PEER = "TORTOISE_EMBED_WORKER_REQUIRE_PEER"


def _require_peer() -> bool:
    """Whether an unverifiable peer must be REFUSED rather than allowed.

    ⛔ THE VOCABULARY IS IMPORTED, NOT RE-DECLARED. This read shipped (#14220) with its own
    inline `{"1", "true", "yes", "on"}`, making it the FIFTH ad-hoc copy of the
    truthy/falsy contract and reddening
    `tests/test_env_truthy.py::test_no_adhoc_vocabulary_literal_outside_the_contract` on
    `main` — so the required `python-ci-gate` was red for every PR (#14453 / #14463).
    `TRUTHY` holds exactly those four members, so this is behaviour-identical; the point of
    `tortoise.env_truthy` is that two code paths parsing the same env var cannot drift.
    """
    return (os.environ.get(ENV_WORKER_REQUIRE_PEER) or "").strip().lower() in TRUTHY


def _peer_uid(conn: socket.socket) -> int | None:
    """The uid on the other end of ``conn``, or ``None`` if this kernel cannot say.

    ``SO_PEERCRED`` is the kernel's own answer — it cannot be spoofed by the
    connecting process, which is what makes it useful (filesystem permissions can
    be by design, and a path that exists is not proof of who is on the other end).
    Absent on platforms that do not offer it (macOS names its own
    ``LOCAL_PEERCRED``); ``None`` says "this kernel did not tell us", never "uid 0".
    """
    so_peercred = getattr(socket, "SO_PEERCRED", None)
    if so_peercred is None:
        return None
    try:
        raw = conn.getsockopt(
            socket.SOL_SOCKET, so_peercred, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


#: Set once, so an unsupported platform says so ONCE rather than per connection.
_peer_credential_note_emitted = False


def _peer_allowed(conn: socket.socket) -> bool:
    """Whether the peer on ``conn`` may use this worker — refuse on a foreign uid.

    ⛔ THE ATTACK THIS CLOSES (review F2). The client treats the socket path's
    first EXISTENCE as READY, and this process creates that path only after its
    preload — so for the whole boot the expected path is absent. In a shared
    temp directory any local user could bind it first, be accepted as the
    encoder, and have its vectors written as record-truth embeddings (#5004's
    class of damage: undetectable downstream).

    The PRIMARY control is the private ``0700`` directory the client mints for
    the socket (:meth:`tortoise.embed_seam.WorkerBacking.socket_path`), which no
    other user can traverse. This is the belt against a foreign CLIENT — the one
    direction this process can police. ⛔ IT DOES NOT COVER THE OTHER ONE (review
    P3, cycle 9): when an operator points ``TORTOISE_EMBED_WORKER_SOCKET`` at a
    shared path, a foreign process can bind that path FIRST and become the server,
    and then this check is the attacker's own code. Neither this lever nor
    ``TORTOISE_EMBED_WORKER_REQUIRE_PEER`` closes that direction — leaving the path unshared does. What follows
    describes the client-facing belt, which the
    directory cannot protect.

    Direction of the read is the convergent one (vLLM, and the ``myunix.org``
    guidance it follows): reject a uid that is not ours. Where the kernel offers
    no peer credential at all, the failure is REPORTED and the connection is
    allowed — rejecting every request on such a platform would turn a hardening
    detail into an outage. ⛔ WHAT REMAINS IS CONDITIONAL (review P3, cycle 10):
    the private ``0700`` directory the CLIENT mints for the DEFAULT socket path,
    which does not exist when ``TORTOISE_EMBED_WORKER_SOCKET`` points at a shared
    path. On that deployment the fail-open default protects nothing, which is
    exactly why it is selectable (``ENV_WORKER_REQUIRE_PEER``) rather than
    assumed.
    """
    global _peer_credential_note_emitted
    uid = _peer_uid(conn)
    if uid is None:
        if _require_peer():
            _logger.warning(
                "embed worker: REFUSED a connection whose uid this kernel "
                "cannot report, because %s is set — peer identity is REQUIRED "
                "(#14186)", ENV_WORKER_REQUIRE_PEER)
            with contextlib.suppress(OSError):
                conn.sendall(
                    (json.dumps({
                        "error": "peer rejected: this kernel reports no peer "
                                 "credential and " + ENV_WORKER_REQUIRE_PEER +
                                 " is set"}) + "\n").encode("utf-8"))
            return False
        if not _peer_credential_note_emitted:
            _peer_credential_note_emitted = True
            _logger.warning(
                "embed worker: this platform offers no SO_PEERCRED — peer "
                "identity is unchecked; what remains is the private directory the "
                "CLIENT mints for the DEFAULT socket path, and that directory is "
                "absent when TORTOISE_EMBED_WORKER_SOCKET points at a shared path. "
                "Set %s=1 to REFUSE every client instead (#14186)",
                ENV_WORKER_REQUIRE_PEER)
        return True
    if uid == os.getuid():
        return True
    _logger.warning(
        "embed worker: REFUSED a connection from uid %s (this worker serves uid "
        "%s) — a foreign process tried to become the embedder (#14186)",
        uid, os.getuid())
    with contextlib.suppress(OSError):
        conn.sendall(
            (json.dumps({"error": f"peer rejected: uid {uid} is not the worker's "
                                  f"own uid {os.getuid()}"}) + "\n").encode("utf-8"))
    return False


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


def _preload_encoder() -> bool:
    """Pay the cold import AND the model load BEFORE the socket exists.

    ⛔ WHY THIS IS A READINESS CONTRACT, NOT AN OPTIMISATION. The client treats
    the socket's EXISTENCE as "ready" (:meth:`tortoise.embed_seam.WorkerBacking._ensure_started`),
    which is the convergent practice — vLLM's preload daemon binds its UNIX socket
    only after the weights are ready, and a socket/port that answers before the
    model is initialised is documented there as the failure mode, not the design.
    Before this, ``serve`` bound first and the encoder was imported lazily inside
    ``_encode``, so the FIRST request after every boot paid the 156.5 s cold
    import (#13674) inside a 30 s request budget: it raised ``TimeoutError``, was
    recorded as ``last_decline``, and the runbook told the operator to REPAIR a
    worker whose only problem was that it had just been born.

    Returns ``True`` when an encoder is resident. ``False`` is NOT a failure to
    start: this process's contract is to DECLINE with a name (``_encode``
    re-derives ``EmbeddingModel.absent_reason()``), never to refuse to serve — a
    worker that exited here would make an absent encoder look like a broken
    deployment, and the client would have to guess which one it was.

    Total: any failure is logged and answered ``False``, so nothing about the
    encoder can prevent the socket from appearing.
    """
    started = time.monotonic()
    try:
        from tortoise.embeddings import EmbeddingModel

        model = EmbeddingModel.get()
        took = time.monotonic() - started
        if model is None:
            _logger.warning(
                "embed worker: no encoder at start-up (%s) after %.1fs — "
                "requests will DECLINE with this reason rather than encode",
                EmbeddingModel.absent_reason(), took)
            return False
        _logger.info(
            "embed worker: encoder preloaded in %.1fs — the socket now means "
            "READY (#14186)", took)
        return True
    except Exception as exc:  # a preload must never block bind
        _logger.warning(
            "embed worker: encoder preload failed after %.1fs (%s: %s) — "
            "requests will DECLINE rather than start late",
            time.monotonic() - started, type(exc).__name__, exc)
        return False


#: The kernel's ``sun_path`` limit is 108 bytes including the NUL on Linux, minus
#: the bytes a name needs to be useful; anything past this is refused by ``bind``.
_MAX_UNIX_PATH_BYTES = 100


def _bind_ready_path(socket_path: str) -> str:
    """A sibling path to bind, so the REAL one only ever names a LISTENING socket.

    ⛔ WHY THIS EXISTS (review F2 at ``76e58a21bb``). READINESS IS THE PATH'S
    EXISTENCE — the client polls for the file — but ``bind()`` CREATES that file
    one statement before ``listen()`` makes it connectable. A client that won the
    20 ms poll could therefore ``connect()`` to a bound-but-not-listening socket
    and get ``ECONNREFUSED`` (``unix(7)``: the target pathname is not a listening
    socket), declining a batch on a perfectly healthy worker and recording it in
    ``last_decline``. The existence poll below cannot close the gap — ``bind``
    already made the path exist, so the loop is a no-op.

    Binding beside it and renaming after ``listen()`` closes it structurally:
    ``rename(2)`` is atomic within a filesystem and does not care that the inode is
    a socket, so the real path only ever appears naming a listening socket. This
    is the idiom recommended for exactly this race (python-trio#279), and the same
    reason a preload daemon binds its socket only once it can serve.

    Returns ``""`` when no sibling name fits beside an operator-supplied
    ``TORTOISE_EMBED_WORKER_SOCKET`` — the caller then binds in place, and the
    client's bounded ``ECONNREFUSED`` retry is the belt for that shape.
    """
    directory, base = os.path.split(socket_path)
    candidate = os.path.join(directory, f".{base}.{os.getpid()}.tmp")
    if len(os.fsencode(candidate)) > _MAX_UNIX_PATH_BYTES:
        return ""
    return candidate


def serve(socket_path: str, *, parent_pid: int,
          peer_allowed=_peer_allowed,
          stop: threading.Event | None = None) -> None:
    """Run the worker until the parent dies. Blocks.

    ``peer_allowed`` and ``stop`` are injectable so the ACCEPT-LOOP WIRING is
    testable without a second uid: the pre-fix suite pinned
    :func:`_peer_allowed`'s decision but not that ``serve`` ever consulted it, so
    deleting the call left every row green (review: the belt's wiring was
    unpinned).
    """
    stop = threading.Event() if stop is None else stop
    threading.Thread(
        target=_watch_stdin, args=(stop,), name="embed-worker-stdin",
        daemon=True).start()
    threading.Thread(
        # ⛔ ``stop`` IS PASSED. It was omitted, so this belt thread died on its
        # first line with ``TypeError`` and the parent-death check never ran — an
        # enforcer that looked armed and could not fire (the pipe was still the
        # primary mechanism, which is why nothing noticed).
        target=_watch_parent, args=(parent_pid, stop),
        name="embed-worker-parent", daemon=True).start()

    # ⛔ BEFORE the bind, so the socket's existence means READY — see
    # `_preload_encoder`. The two watcher threads above start FIRST on purpose:
    # the preload is the longest quiet window in this process's life (156.5 s cold
    # import + the model load), and the parent-death belt has to be armed for it,
    # or a killed parent would leave a worker holding 432 MB through the whole
    # warm-up.
    _preload_encoder()

    if os.path.exists(socket_path):
        # A stale path from a killed predecessor: bind would refuse, and a
        # refusal here looks exactly like "the worker is broken".
        try:
            os.unlink(socket_path)
        except OSError as exc:
            _logger.warning("embed worker: could not clear %s: %s", socket_path, exc)

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    ready_path = _bind_ready_path(socket_path)
    try:
        # Bind at the sibling (if there is room), LISTEN, then move it onto the
        # real name — see `_bind_ready_path` for the race this closes.
        server.bind(ready_path or socket_path)
        server.listen(16)
        if ready_path:
            os.rename(ready_path, socket_path)
        _logger.info(
            "embed worker: listening on %s (parent pid %s)", socket_path, parent_pid)
        while not stop.is_set():
            try:
                conn, _addr = server.accept()
            except OSError:
                break
            try:
                if not peer_allowed(conn):
                    # ⛔ CLOSE a refused connection. Nothing downstream will: the
                    # accept loop only hands it to ``_serve_connection`` when the
                    # policy allows it, so without this every refusal leaked one
                    # file descriptor.
                    with contextlib.suppress(OSError):
                        conn.close()
                    continue
                _serve_connection(conn)
            except Exception:
                _logger.warning("embed worker: connection failed", exc_info=True)
    finally:
        server.close()
        for path in (ready_path, socket_path) if ready_path else (socket_path,):
            with contextlib.suppress(OSError):
                os.unlink(path)


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
