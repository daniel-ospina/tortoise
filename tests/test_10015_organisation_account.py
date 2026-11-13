"""#10015 — SDK row 28: `get_organisation_account`, the account read with its plan.

The reply has THREE states, not two, and every cheap implementation of this read
collapses two of them:

* a **number** — a bounded allowance;
* ``None`` — **unlimited** (``resolve_org_limits``: a key PRESENT with no value);
* the key **absent** — we could not read it, which is the state the write gate
  also treats as fail-closed (``enforce_org_limit``: a MISSING key refuses).

``dict.get()`` merges the third into the second, i.e. it tells a capped org it has
no cap. So both directions are pinned here:

(a) a bounded plan reports its limit **and** used; an unlimited plan reports
    ``limit: None`` (never a number, never an omission); an unreadable plan omits
    the key (never ``None``);
(b) an account **at or over** its cap reads as at-cap IN THE REPLY — not as an
    exception and not as a 402 — and a caller bound to one account cannot read
    another's.

Everything runs on the embedded store (``tmp_path``); nothing here needs a
database on ``localhost:6379``.
"""

from __future__ import annotations

import inspect

import pytest

from tortoise.exceptions import ControlPlaneError
from tortoise.quota import QuotaCheckError, encode_plan_block
from tortoise.record_writer import use_record_tenant
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """An SDK whose control plane is the one `resolve_org_limits` will read.

    The resolver resolves the registry through `quota._make_sdk(namespace="registry")`
    (`TORTOISE_DB_URI` / `TORTOISE_DB_PATH`), never a caller's handle — so the
    test points it at THIS store and uses the same namespace. One database, as a
    single-node deployment has; a test SDK pointed at another graph would make
    the account row invisible to the resolver for reasons unrelated to the code
    under test.
    """
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "test_10015.db"))
    return TortoiseSDK(db_path=str(tmp_path / "test_10015.db"), namespace="registry")


@pytest.fixture
def team(sdk):
    return sdk.org_create("acme-10015")


def _bind(sdk, team, graph_id="g-10015"):
    """Bind the SDK's tenant the way the hosted REST lane carries it."""
    sdk._record_tenant = (team["id"], graph_id)
    return sdk


def _set_points_override(sdk, team, value):
    """Set the stored `max_points` override (the field `org_update` accepts)."""
    sdk._get_registry().query(
        "MATCH (t:Team {id:$id}) SET t.max_points=$mp",
        params={"id": team["id"], "mp": value},
    )


def _add_points(sdk, n: int):
    """Mint `n` non-episodic Points — the label `count_org_usage('points')` counts."""
    proj = sdk._get_proj()
    for i in range(n):
        proj.g.query(
            "CREATE (p:Point {id:$pid, statement:$s})",
            params={"pid": f"10015-p{i}", "s": f"claim {i}"},
        )


# ─────────────────────────────────────────────────────────────────────────────
# (a) the encoding: three states, none of them collapsed
# ─────────────────────────────────────────────────────────────────────────────


class TestPlanBlockEncoding:
    """`quota.encode_plan_block` — the ONE encoding of used-vs-limit (#10015)."""

    def test_bounded_limit_is_reported_with_used(self):
        block = encode_plan_block({"max_points": 10000}, "points", used=42)
        assert block == {
            "resource": "points", "used": 42, "limit": 10000, "at_cap": False,
        }

    def test_unlimited_is_null_not_a_number_and_not_absent(self):
        block = encode_plan_block({"max_points": None}, "points", used=42)
        # The key IS present, and its value is None: "unlimited", resolved.
        assert "limit" in block
        assert block["limit"] is None
        # No cap exists, so "not at cap" is a TRUE answer rather than a guess.
        assert block["at_cap"] is False

    def test_a_missing_key_is_absent_not_unlimited(self):
        """The fail-open this closes.

        `.get()` would write `limit: None` here, which READS as unlimited — the
        exact lie `enforce_org_limit` refuses to tell a write.
        """
        block = encode_plan_block({}, "points", used=42)
        assert "limit" not in block, "a missing limit must not become `null` (= unlimited)"
        assert "at_cap" not in block

    @pytest.mark.parametrize("unreadable", ["many", "1000.0", float("inf")])
    def test_an_unreadable_limit_is_absent(self, unreadable):
        block = encode_plan_block({"max_points": unreadable}, "points", used=1)
        assert "limit" not in block, (
            f"{unreadable!r} is not a quantity the gate can coerce — it coerces with "
            "`int()` and refuses what that cannot read, so the read must publish no "
            "number rather than coerce or default one"
        )
        assert "at_cap" not in block

    def test_a_bool_mirrors_the_gate_rather_than_inventing_a_state(self):
        """`True` is an `int` subclass, and the GATE coerces it to a cap of 1.

        Measured: `enforce_org_limit({"max_points": True}, "points")` raises
        `QuotaExceededError … limit reached (1)`. So the read must encode 1 rather than
        report "unknown" — a read that says nothing about an account the gate is
        already refusing is the inverse of the fail-open this helper prevents.
        """
        block = encode_plan_block({"max_points": True}, "points", used=3)
        assert block["limit"] == 1
        assert block["at_cap"] is True

    def test_an_integer_formatted_string_is_honoured_like_the_gate(self):
        # `enforce_org_limit` coerces `"1000"` -> 1000 and enforces it; the read
        # must agree with the gate it mirrors.
        assert encode_plan_block({"max_points": "1000"}, "points", used=5)["limit"] == 1000

    def test_unknown_used_omits_at_cap_under_a_bound(self):
        """A failed count must never read as `at_cap: false` (#4331's lesson)."""
        block = encode_plan_block({"max_points": 10}, "points", used=None)
        assert block["used"] is None
        assert block["limit"] == 10
        assert "at_cap" not in block

    def test_at_cap_is_the_gates_own_predicate(self):
        # `enforce_org_limit` refuses on `used >= limit`.
        assert encode_plan_block({"max_points": 10}, "points", used=9)["at_cap"] is False
        assert encode_plan_block({"max_points": 10}, "points", used=10)["at_cap"] is True
        assert encode_plan_block({"max_points": 10}, "points", used=99)["at_cap"] is True

    def test_no_account_context_claims_nothing(self):
        block = encode_plan_block(None, "points", used=1)
        assert "limit" not in block and "at_cap" not in block

    def test_an_unknown_resource_is_refused_not_guessed(self):
        with pytest.raises(QuotaCheckError):
            encode_plan_block({"max_points": 1}, "not_a_resource", used=0)


# ─────────────────────────────────────────────────────────────────────────────
# (a)+(b) the reply, end to end
# ─────────────────────────────────────────────────────────────────────────────


class TestReply:
    def test_the_reply_carries_the_account_tier_and_plan(self, sdk, team):
        reply = _bind(sdk, team).get_organisation_account()
        assert reply["org_id"] == team["id"]
        assert reply["tier"] == "free"
        assert reply["plan"]["resource"] == "points"
        assert isinstance(reply["plan"]["limit"], int)
        assert isinstance(reply["plan"]["used"], int)

    def test_a_bounded_plan_reports_used_and_limit(self, sdk, team):
        _add_points(sdk, 3)
        reply = _bind(sdk, team).get_organisation_account()
        assert reply["plan"]["used"] == 3
        assert reply["plan"]["limit"] == 10000  # free tier's max_points
        assert reply["plan"]["at_cap"] is False

    def test_at_cap_reads_as_at_cap_not_as_an_exception(self, sdk, team):
        _set_points_override(sdk, team, 2)
        _add_points(sdk, 2)
        reply = _bind(sdk, team).get_organisation_account()   # must not raise
        assert reply["plan"]["used"] == 2
        assert reply["plan"]["limit"] == 2
        assert reply["plan"]["at_cap"] is True

    def test_over_cap_reads_as_at_cap(self, sdk, team):
        """A cap lowered under an existing count leaves the account OVER it."""
        _set_points_override(sdk, team, 1)
        _add_points(sdk, 3)
        reply = _bind(sdk, team).get_organisation_account()
        assert reply["plan"]["used"] == 3
        assert reply["plan"]["limit"] == 1
        assert reply["plan"]["at_cap"] is True

    def test_an_unlimited_plan_serialises_as_null(self, sdk, team, monkeypatch):
        """`None` survives to the reply — it is never replaced by a number.

        Not reachable through today's resolver for `points` (every tier's
        `max_graph_nodes` is finite, so `resolve_org_limits` always emits a
        number). It is pinned at the seam because it is a CONTRACT: a plan with
        no cap must answer `null`, and the day a tier is sold that way the reply
        says so instead of inventing a floor.
        """
        monkeypatch.setattr(
            "tortoise.quota.resolve_org_limits",
            lambda org_id: {"org_id": org_id, "tier": "unlimited", "max_points": None},
        )
        reply = _bind(sdk, team).get_organisation_account()
        assert reply["plan"]["limit"] is None
        assert reply["plan"]["at_cap"] is False

    def test_a_missing_limit_key_is_absent_in_the_reply(self, sdk, team, monkeypatch):
        monkeypatch.setattr(
            "tortoise.quota.resolve_org_limits",
            lambda org_id: {"org_id": org_id, "tier": "free"},
        )
        reply = _bind(sdk, team).get_organisation_account()
        assert "limit" not in reply["plan"], (
            "an unreadable plan must not answer `null`, which means unlimited"
        )
        assert "at_cap" not in reply["plan"]

    def test_a_failed_count_is_null_never_zero(self, sdk, team, monkeypatch):
        def boom(*_a, **_k):
            raise QuotaCheckError("store unavailable")

        monkeypatch.setattr("tortoise.quota.count_org_usage", boom)
        reply = _bind(sdk, team).get_organisation_account()
        assert reply["plan"]["used"] is None, "a failed read is not a genuine 0"
        assert reply["plan"]["limit"] == 10000
        assert "at_cap" not in reply["plan"]

    def test_a_bound_tenant_with_no_account_row_returns_none(self, sdk):
        sdk._record_tenant = ("no-such-org-id", "g-10015")
        assert sdk.get_organisation_account() is None

    def test_an_account_the_gates_cannot_see_is_refused_not_reported_absent(
        self, tmp_path, monkeypatch,
    ):
        """The two-control-plane case: a row `org_get` finds but the gates never read.

        `resolve_org_limits` resolves the registry through
        `_make_sdk(namespace="registry")` and the ambient env, never this handle — so an
        SDK opened on its own `db_path=` holds Team rows the plan gates cannot see.
        Reporting `None` there would claim the caller's account does not exist, and
        publishing the OTHER store's cap for the same id would be a money-surface
        fail-open. It must refuse and name the mismatch.
        """
        # The gates read a DIFFERENT store from the one this SDK is opened on.
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.setenv("TORTOISE_DB_PATH", str(tmp_path / "the-gates-store.db"))
        own = TortoiseSDK(db_path=str(tmp_path / "this-handles-store.db"),
                          namespace="registry")
        team = own.org_create("split-store-10015")
        assert own.org_get(team["id"]) is not None   # the caller CAN see it
        own._record_tenant = (team["id"], "g-10015")
        with pytest.raises(ControlPlaneError) as exc:
            own.get_organisation_account()
        assert "control plane" in str(exc.value)
        assert team["id"] in str(exc.value)


# ─────────────────────────────────────────────────────────────────────────────
# (b) tenancy — scoped to the caller's own account, by construction
# ─────────────────────────────────────────────────────────────────────────────


class TestTenancy:
    def test_the_method_takes_no_account_argument(self):
        """No `org_id` parameter means no way to NAME another tenant's row."""
        params = list(inspect.signature(TortoiseSDK.get_organisation_account).parameters)
        assert params == ["self"], (
            "get_organisation_account must resolve the account from the SDK's own "
            "tenant; an org-id argument is the cross-tenant read this refuses"
        )

    def test_an_unbound_sdk_refuses_rather_than_guessing(self, sdk, monkeypatch):
        for var in ("TORTOISE_RECORD_ORG_ID", "TORTOISE_RECORD_GRAPH_ID",
                    "TORTOISE_RECORD_SINGLE_TENANT"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(ControlPlaneError) as exc:
            sdk.get_organisation_account()
        message = str(exc.value)
        # Actionable, not a dead end.
        assert "use_record_tenant" in message
        assert "TORTOISE_RECORD_ORG_ID" in message

    def test_a_caller_reads_its_own_account_and_not_another(self, sdk):
        a = sdk.org_create("tenant-a-10015")
        b = sdk.org_create("tenant-b-10015")
        _set_points_override(sdk, a, 7)
        _set_points_override(sdk, b, 11)

        sdk._record_tenant = (a["id"], "g-a")
        reply_a = sdk.get_organisation_account()
        sdk._record_tenant = (b["id"], "g-b")
        reply_b = sdk.get_organisation_account()

        assert reply_a["org_id"] == a["id"]
        assert reply_b["org_id"] == b["id"]
        assert reply_a["plan"]["limit"] == 7
        assert reply_b["plan"]["limit"] == 11
        # The decisive half: A's read carries nothing from B's account.
        assert reply_a["org_id"] != b["id"]
        assert reply_a["plan"]["limit"] != reply_b["plan"]["limit"]

    def test_the_carried_tenant_wins_over_an_ambient_scope(self, sdk):
        """The hosted lane carries its tenant BY VALUE (#9188) — that wins.

        Otherwise a scope left on the context could redirect a credential's read
        to another account, which is the same defect class on the read side.
        """
        a = sdk.org_create("carried-a-10015")
        b = sdk.org_create("carried-b-10015")
        sdk._record_tenant = (a["id"], "g-a")
        with use_record_tenant(b["id"], "g-b"):
            reply = sdk.get_organisation_account()
        assert reply["org_id"] == a["id"]

    def test_an_ambient_single_tenant_scope_is_honoured_when_nothing_is_carried(
        self, sdk,
    ):
        a = sdk.org_create("scoped-a-10015")
        with use_record_tenant(a["id"], "g-a"):
            reply = sdk.get_organisation_account()
        assert reply["org_id"] == a["id"]
