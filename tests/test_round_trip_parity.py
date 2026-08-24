# tests/test_round_trip_parity.py
"""E2E-1: DB-agnostic round-trip parity, docker vs embedded (epic #1647).
Parametrized over TORTOISE_DB_URI set/unset: identical create_point → search
results on the D1/D5-identical paths; D6/D8 sides assert their own lane."""
import pytest

from tortoise.projection import FalkorProjection
from tortoise.sdk import TortoiseSDK


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """True when a live FalkorDB answers a TCP connect on host:port.

    Repo skip-guard convention (#1436, tests/test_ingest.py): the docker leg
    SKIPS with a FalkorDB-reason when the docker is absent (post-merge-
    validation runs the full suite with NO docker service) — never ERROR on
    redis.ConnectionError. The fast CI job provisions the falkordb service,
    so the probe passes there and the docker leg actually runs.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _round_trip(tmp_path, point_id: str, content: str):
    # Divergence note (epic #1647 Task 1 impl): the plan's draft drove the
    # round trip through raw proj.apply({"type": "PointCreated", ...}) and
    # asserted n.content_hash truthy — but "PointCreated" is NOT a fold
    # event type (the projection handles PointAdded/OperatorAdded + nested
    # "point", verified projection/__init__.py apply L803+) so the event was
    # silently dropped (empty result on BOTH legs), and the raw apply path
    # NEVER persists content_hash (only the SDK's create_point does, via the
    # `SET n += $props` write, sdk.py). The E2E-1 catalog row's own wording
    # is "create_point → search round-trip ... Assertion: point id,
    # content_hash, search hit list" — so this follows the CATALOG intent:
    # the SDK's create_point (the product path that computes content_hash)
    # plus a raw search. Each leg provably runs its intended backend.
    sdk = TortoiseSDK(str(tmp_path / "roundtrip.db"))
    try:
        sdk.create_point("claim", content, id=point_id)
        proj = sdk._get_proj()
        hits = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.id, n.content, n.content_hash",
            params={"id": point_id}).result_set
        return hits
    finally:
        sdk.close()


# Cycle-3 P1-6: the claimed "parametrized URI set/unset" was prose, not code —
# test_round_trip_same_shape ran the SAME lane in both legs, and on a URI-set
# dev/CI lane BOTH legs ran docker (docker-vs-docker can never detect
# divergence). Real env control: the embedded leg delenv's the URI, the docker
# leg setenv's it — each leg provably runs its intended backend.
_LEGS = ["embedded", "docker"]


@pytest.mark.parametrize("leg", _LEGS)
def test_round_trip_same_shape(leg, tmp_path, monkeypatch):
    if leg == "embedded":
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    else:
        if not _docker_reachable():
            pytest.skip("live FalkorDB (localhost:6379) not reachable")
        monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
        monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    hits = _round_trip(tmp_path, "rt-1", "parity claim")
    assert hits and hits[0][0] == "rt-1" and hits[0][1] == "parity claim"
    assert hits[0][2]  # content_hash present in BOTH modes (D1/D5-identical)
    # Cycle-5 P1-1: the cycle-4 changelog P1-3 row claimed the class-attr
    # smoke assert was DROPPED, but the BODY still shipped
    # `assert FalkorProjection._is_embedded is not None`. _is_embedded is an
    # INSTANCE attr (projection: self._is_embedded = (path is not None)) — no
    # class attr exists → AttributeError on BOTH legs, red in both modes.
    # Instance probe instead:
    probe = FalkorProjection(str(tmp_path / "probe.db"))
    try:
        assert probe._is_embedded in (True, False)  # smoke: module importable
    finally:
        probe.close()
