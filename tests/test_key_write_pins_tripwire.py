"""#2299: server-side parity/tripwire for session key-write ?team_id= pins.

The server mirror of the client static tripwire
(website/apps/dashboard/src/keyTeamPinsTripwire.test.js, #2230): that file
guards the DASHBOARD's key-management writes (every revoke/rename/toggle/mint
URL must append the `?team_id=` pin); this file guards the SERVER's honoring
of that pin — every session-capable key-WRITE route must honor a truthy
?team_id= in session mode, or a future key-write endpoint silently ignores
the pin again (the pre-#2230 PATCH failure mode; #2297's session gates landed
in 1b22c56c and must stay intact).

Two layers, mirroring the client file:
  1. TestKeyWritePinsTripwireStatic — a source scan of tortoise/hosted_api.py
     asserting every session key-write route still resolves ?team_id= through
     a recognized seam (the get_current_team_session DI, or the shared
     _session_pinned_team / _ensure_key_in_pinned_team helpers). A route that
     drops its seam fails here loudly, like the client's whole-file sentinel.
  2. TestKeyWritePinsTripwireBehavior — a behavioral matrix driving each
     route with a multi-membership session (A first → memberships[0]=A, B
     second): the pin must govern the write (pin-B lands in B), and a
     wrong-team / non-member pin must fail closed 403 — never a silent
     memberships[0] fallback and never a cross-team write.

Route-coverage matrix (also documented above the helpers in hosted_api.py):

| Route                            | Handler               | Pin seam              |
|----------------------------------|-----------------------|-----------------------|
| POST   /v1/team/keys             | create_api_key        | DI (get_current_team  |
|                                  |                       |   _session)           |
| PATCH  /v1/team/keys/{key_id}    | toggle_api_key_enabled| inline helpers        |
| DELETE /v1/team/keys/{key_id}    | revoke_api_key        | DI + fail-closed      |
| PATCH  /v1/team/dashboard-login  | toggle_dashboard_login| inline membership gate|

POST /v1/session/key (session_key) carries its team selector in the BODY
(required when multi-membership) — a different client contract, documented in
the helper matrix comment; it is not a ?team_id= route and is not scanned.
Agent signup/recover/token-revoke are token-driven (the token IS the pin).
"""

from __future__ import annotations

import io
import os
import sys
import tokenize
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
import tortoise.supabase_control as sc
from tortoise.hosted_api import app
from tests.fake_control_plane import FakeControlPlane

_SUPABASE_URL = "https://pinparity.supabase.co"

# The session-lane key-WRITE handlers (server mirror of the client tripwire's
# WRITE_FNS). Each maps to the pin seam that MUST appear in its source body.
KEY_WRITE_HANDLERS: dict[str, tuple[str, ...]] = {
    # DI seam — get_current_team_session → _session_user_team membership-gates
    # the pin and resolves the team from it.
    "create_api_key": ("get_current_team_session",),
    # Inline seam — the shared helpers (membership gate + fail-closed).
    "toggle_api_key_enabled": ("_session_pinned_team", "_ensure_key_in_pinned_team"),
    "toggle_dashboard_login": ("_session_pinned_team", "_require_owner_admin"),
    # DI seam + the shared fail-closed helper on the key lookup.
    "revoke_api_key": ("get_current_team_session", "_ensure_key_in_pinned_team"),
}
# Route decorator paths that carry key-write semantics (GET list is a read and
# is deliberately excluded — same boundary as the client tripwire's scan of
# backtick `/v1/team/keys` URLs).
KEY_WRITE_ROUTE_PREFIXES = ("/v1/team/keys", "/v1/team/dashboard-login")
KEY_WRITE_METHODS = ("post", "patch", "delete")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", _SUPABASE_URL)
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-pin-parity")
    monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
    ha_mod._CLAIM_BUCKETS.clear()
    fake = FakeControlPlane()
    monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
    yield fake
    ha_mod._CLAIM_BUCKETS.clear()


@pytest.fixture
def fake(_env):
    return _env


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hosted_api_source() -> str:
    with open(os.path.join(_repo_root(), "tortoise", "hosted_api.py"), encoding="utf-8") as fh:
        return fh.read()


def _provision_anon(client, fake):
    """Mint an anonymous team via /v1/agent/signup (Supabase mode)."""
    r = client.post("/v1/agent/signup", json={})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["key"], data["team_id"]


def _fake_user(user_id: str) -> dict:
    return {"user_id": user_id, "email": "owner@example.com", "sub": user_id}


def _patch_session_user(monkeypatch, user_id: str):
    async def _fake(request):
        return _fake_user(user_id)

    import tortoise.session_auth as sa

    monkeypatch.setattr(sa, "verify_session_jwt", _fake)


def _claim(client, fake, key, user_id, email="owner@example.com"):
    from tortoise.auth import lookup_hash

    sc.claim_membership(fake, lookup_hash=lookup_hash(key), user_id=user_id, email=email)


def _keys_of(fake, team_id: str) -> list[dict]:
    return fake.query("api_keys", select=["id"], filters=[("team_id", "eq", team_id)])


class TestKeyWritePinsTripwireStatic:
    """#2299 layer 1 — source tripwire (the client keyTeamPinsTripwire's
    server mirror). Every session-lane key-write handler in hosted_api.py must
    keep resolving ?team_id= through a recognized pin seam; adding a handler
    or dropping a seam fails loudly and forces a deliberate table extension
    (same rule as WRITE_FNS in the client tripwire)."""

    def _handler_source(self, source: str, name: str) -> str:
        """Body of one route handler: from `async def <name>(` to the next
        top-level decorator/class/def or EOF (routes are contiguous here)."""
        lines = source.split("\n")
        start = None
        for i, line in enumerate(lines):
            if line.startswith(f"async def {name}("):
                start = i
                break
        assert start is not None, f"handler {name} not found in hosted_api.py"
        for j in range(start + 1, len(lines)):
            line = lines[j]
            if (
                line.startswith("@app.")
                or line.startswith("class ")
                or line.startswith("def ")
                or line.startswith("async def ")
            ):
                return "\n".join(lines[start:j])
        return "\n".join(lines[start:])

    @staticmethod
    def _code_names(fragment: str) -> set[str]:
        """NAME tokens outside comments/string literals. A helper name that
        survives only in a comment or docstring (e.g. a dropped call whose
        mention the developer left behind) does NOT count as a wired seam —
        the seam check must fail loudly on that (the pre-#2230 regression
        class)."""
        names: set[str] = set()
        try:
            for tok in tokenize.generate_tokens(io.StringIO(fragment).readline):
                if tok.type == tokenize.NAME:
                    names.add(tok.string)
        except (tokenize.TokenError, IndentationError):
            pass  # partial-fragment edge — keep whatever parsed cleanly
        return names
    def test_key_write_handlers_all_enumerated(self):
        """Whole-file sentinel: every session-lane key-write route whose
        decorator matches a key-write path prefix must be enumerated in
        KEY_WRITE_HANDLERS — a NEW key-write route fails here until someone
        deliberately extends the table (and, per the matrix, wires a seam)."""
        source = _hosted_api_source()
        lines = source.split("\n")
        found = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if not line.startswith("@app."):
                i += 1
                continue
            verb_path = line[len("@app.") :]
            method = verb_path.split("(", 1)[0].strip()
            if method not in KEY_WRITE_METHODS:
                i += 1  # non-write verb (@app.get / @app.exception_handler…) — advance
                continue
            if not any(p in verb_path for p in KEY_WRITE_ROUTE_PREFIXES):
                i += 1  # write verb on a non-key route (internal/backup/etc.) — advance
                continue
            # Consume the decorator to its balanced close (it may span
            # multiple lines) — the handler def follows on the next line.
            depth = line.count("(") - line.count(")")
            j = i
            while depth > 0 and j + 1 < len(lines):
                j += 1
                depth += lines[j].count("(") - lines[j].count(")")
            nxt = lines[j + 1] if j + 1 < len(lines) else ""
            if nxt.startswith("async def "):
                name = nxt[len("async def ") :].split("(", 1)[0].strip()
                found.append((method, name))
            i = j + 1
        assert found, "no session key-write routes scanned (source changed?)"
        for method, name in found:
            assert name in KEY_WRITE_HANDLERS, (
                f"un-enumerated session key-write route {method.upper()} "
                f"→ {name}: must honor ?team_id= through a pin seam and be "
                "added to KEY_WRITE_HANDLERS"
            )

    def test_key_write_handlers_keep_pin_seam(self):
        """Per-handler seam check: each enumerated key-write handler must keep
        the ?team_id= seam that enforces the pin (DI for create/revoke; the
        shared helpers for toggle/dashboard-login). Dropping a seam regresses
        #2230/#2248/#2297 the way the pre-#2230 PATCH did."""
        source = _hosted_api_source()
        for name, seams in KEY_WRITE_HANDLERS.items():
            code = self._code_names(self._handler_source(source, name))
            for seam in seams:
                assert seam in code, (
                    f"{name}: expected pin seam {seam!r} in handler CODE — "
                    "a session key-write that drops its ?team_id= seam "
                    "silently ignores the pin again (pre-#2230 PATCH mode); "
                    "a comment/docstring mention does not count"
                )


class TestKeyWritePinsTripwireBehavior:
    """#2299 layer 2 — behavioral matrix over the FakeControlPlane harness.

    Fixture: one session user OWNING two teams (A claimed first →
    memberships[0]=A, B second). Each route is driven with a pin on the
    NON-first membership: the pin must govern the write (never a silent
    memberships[0] fallback — the pre-#2230 PATCH failure mode) and a
    wrong-team pin must fail closed 403 on a key target.
    """

    def _two_claimed_teams(self, client, fake, monkeypatch):
        """Provision A + B, claim both for one session user (A first so
        memberships[0]=A). Returns (teamA, teamB)."""
        keyA, teamA = _provision_anon(client, fake)
        keyB, teamB = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _claim(client, fake, keyA, user_id, email="ownerA@example.com")
        _claim(client, fake, keyB, user_id, email="ownerB@example.com")
        return teamA, teamB

    def _key_id(self, fake, team_id):
        rows = _keys_of(fake, team_id)
        assert rows, f"no api_keys row for {team_id}"
        return rows[0]["id"]

    # ── POST /v1/team/keys (create_api_key): the pin selects the mint team ─
    def test_create_mint_honors_pin_lands_in_pinned_team(self, client, fake, monkeypatch):
        """A session mint pinning the NON-first membership (B) must mint INTO
        B — a pin-ignoring server resolves memberships[0] (A) and would mint
        into A instead (the tripwire's ignore-regression probe)."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        beforeA = len(_keys_of(fake, teamA))
        beforeB = len(_keys_of(fake, teamB))
        r = client.post(
            f"/v1/team/keys?team_id={teamB}", headers={"Authorization": "Bearer eyJ.sess"}, json={}
        )
        assert r.status_code == 200, r.text
        minted_id = r.json()["id"]
        row = fake.query("api_keys", select=["team_id"], filters=[("id", "eq", minted_id)])
        assert row and row[0]["team_id"] == teamB, (
            "mint ignored the ?team_id= pin (landed outside team B)"
        )
        assert len(_keys_of(fake, teamA)) == beforeA, (
            "mint leaked into team A (pin ignored → memberships[0])"
        )
        assert len(_keys_of(fake, teamB)) == beforeB + 1

    def test_create_non_member_pin_403(self, client, fake, monkeypatch):
        """A session mint pinning a team the user does not belong to fails
        closed 403 "No membership in team" (the shared membership gate) —
        never mints into memberships[0]."""
        _keyA, teamA = _provision_anon(client, fake)
        _keyB, teamB = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _claim(client, fake, _keyA, user_id, email="ownerA@example.com")
        beforeA = len(_keys_of(fake, teamA))
        r = client.post(
            f"/v1/team/keys?team_id={teamB}", headers={"Authorization": "Bearer eyJ.sess"}, json={}
        )
        assert r.status_code == 403, r.text
        assert "No membership in team" in str(r.json())
        assert len(_keys_of(fake, teamA)) == beforeA, "unexpected mint in A"

    # ── PATCH /v1/team/keys/{id} (toggle): wrong-team pin fails closed ──────
    def test_toggle_wrong_team_pin_403_no_write(self, client, fake, monkeypatch):
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        kid = self._key_id(fake, teamB)  # key lives in B
        r = client.patch(
            f"/v1/team/keys/{kid}?team_id={teamA}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Not your API key"
        row = fake.query("api_keys", select=["enabled"], filters=[("id", "eq", kid)])[0]
        assert row["enabled"] is not False  # no partial write

    # ── DELETE /v1/team/keys/{id} (revoke): wrong-team pin fails closed ─────
    def test_revoke_wrong_team_pin_403_no_write(self, client, fake, monkeypatch):
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        kid = self._key_id(fake, teamB)
        r = client.delete(
            f"/v1/team/keys/{kid}?team_id={teamA}", headers={"Authorization": "Bearer eyJ.sess"}
        )
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == "Not your API key"
        row = fake.query("api_keys", select=["revoked_at"], filters=[("id", "eq", kid)])[0]
        assert row["revoked_at"] is None  # untouched

    # ── PATCH /v1/team/dashboard-login: the pin IS the write target ─────────
    def test_dashboard_login_honors_pin_flips_pinned_team(self, client, fake, monkeypatch):
        """Pinning the non-first membership (B) must flip B's
        dashboard_key_login — a pin-ignoring server flips memberships[0] (A)
        instead."""
        teamA, teamB = self._two_claimed_teams(client, fake, monkeypatch)
        r = client.patch(
            f"/v1/team/dashboard-login?team_id={teamB}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == teamB
        flagA = fake.query("teams", select=["dashboard_key_login"], filters=[("id", "eq", teamA)])[
            0
        ]
        flagB = fake.query("teams", select=["dashboard_key_login"], filters=[("id", "eq", teamB)])[
            0
        ]
        assert flagA["dashboard_key_login"] is True, (
            "dashboard-login ignored the pin (flipped team A = memberships[0])"
        )
        assert flagB["dashboard_key_login"] is False

    def test_dashboard_login_non_member_pin_403(self, client, fake, monkeypatch):
        """A non-member pin fails closed 403 via the shared membership gate —
        never a memberships[0] fallback write on the caller's own team."""
        _keyA, teamA = _provision_anon(client, fake)
        _keyC, teamC = _provision_anon(client, fake)
        user_id = str(uuid.uuid4())
        _patch_session_user(monkeypatch, user_id)
        _claim(client, fake, _keyA, user_id, email="ownerA@example.com")
        r = client.patch(
            f"/v1/team/dashboard-login?team_id={teamC}",
            headers={"Authorization": "Bearer eyJ.sess"},
            json={"enabled": False},
        )
        assert r.status_code == 403, r.text
        flagA = fake.query("teams", select=["dashboard_key_login"], filters=[("id", "eq", teamA)])[
            0
        ]
        assert flagA["dashboard_key_login"] is True  # A untouched
