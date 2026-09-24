"""CLI selection/replay contracts without a database or official-source requests."""

import json
import sys
from contextlib import contextmanager
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.intelligence import cli, editorial
from app.services.workflows import ConflictError


@pytest.fixture(scope="session", autouse=True)
def database():
    """Override the integration suite's database reset for these offline tests."""
    yield


@pytest.fixture
def draft_cli(monkeypatch):
    tenant, influencer, mission, audience, run_id = [uuid4() for _ in range(5)]
    opportunities = [
        {"id": uuid4(), "canonical_external_id": "ES:BDNS:100001"},
        {"id": uuid4(), "canonical_external_id": "ES:BDNS:100002"},
    ]
    saved_run = {
        "id": run_id,
        "influencer_id": influencer,
        "mission_id": mission,
        "state": "AWAITING_APPROVAL",
    }
    binding = {
        "opportunity_id": opportunities[0]["id"],
        "audience_segment_id": audience,
    }
    state = {"existing": True, "bound": True, "roles": []}
    credentials = {
        "tenant_id": str(tenant),
        "influencer_id": str(influencer),
        "mission_id": str(mission),
        "tokens": {"OPERATOR": "offline-test-token"},
    }

    class CredentialFile:
        def __truediv__(self, name):
            assert name == ".local/credentials.json"
            return self

        def read_text(self, *, encoding):
            assert encoding == "utf-8"
            return json.dumps(credentials)

    monkeypatch.setattr(cli, "REPO_ROOT", CredentialFile())

    class Repo:
        def require(self, role):
            state["roles"].append(role)

        def one(self, table, **filters):
            if table == "audience_segments":
                assert filters == {"code": "GENERIC_SMB"}
                return {"id": audience}
            assert table == "opportunities"
            return next(row for row in opportunities if row["id"] == filters["id"])

        def all(self, table, **filters):
            if table == "workflow_runs":
                assert filters == {"idempotency_key": "repeatable-draft"}
                return [saved_run] if state["existing"] else []
            if table == "workflow_opportunities":
                assert filters == {"workflow_run_id": run_id}
                return [binding] if state["bound"] else []
            assert table == "opportunities"
            return [row for row in opportunities if not filters or row["id"] == filters["id"]]

    @contextmanager
    def transaction(actual_tenant, token):
        assert actual_tenant == tenant
        assert token == credentials["tokens"]["OPERATOR"]
        yield Repo()

    monkeypatch.setattr(cli, "transaction", transaction)
    monkeypatch.setattr(editorial, "transaction", transaction)
    # Replays must reach the real service guard without re-evaluating history,
    # generating artifacts or running any skill/provider.
    evaluate = Mock(side_effect=AssertionError("Replay must not re-evaluate"))
    monkeypatch.setattr(cli, "evaluate", evaluate)
    monkeypatch.setattr(editorial, "evaluate", evaluate)
    monkeypatch.setattr(
        editorial, "Runner", Mock(side_effect=AssertionError("Replay must not execute"))
    )
    start = Mock(wraps=editorial.start_opportunity_workflow)
    monkeypatch.setattr(cli, "start_opportunity_workflow", start)

    def invoke(opportunity=None, key="repeatable-draft"):
        args = ["intelligence", "draft"]
        if key is not None:
            args += ["--key", key]
        if opportunity is not None:
            args += ["--opportunity", str(opportunity)]
        monkeypatch.setattr(sys, "argv", args)
        cli.main()

    return {
        "invoke": invoke,
        "state": state,
        "opportunities": opportunities,
        "run": saved_run,
        "binding": binding,
        "evaluate": evaluate,
        "start": start,
        "audience": audience,
    }


@pytest.mark.parametrize("explicit", [False, True])
def test_existing_draft_key_replays_before_editorial_evaluation(draft_cli, capsys, explicit):
    opportunity = draft_cli["opportunities"][0]
    draft_cli["invoke"](opportunity["id"] if explicit else None)

    assert json.loads(capsys.readouterr().out) == {
        "workflow_run_id": str(draft_cli["run"]["id"]),
        "opportunity": opportunity["canonical_external_id"],
        "state": "AWAITING_APPROVAL",
    }
    draft_cli["evaluate"].assert_not_called()
    draft_cli["start"].assert_called_once()
    assert draft_cli["start"].call_args.args[2] == opportunity["id"]
    assert draft_cli["state"]["roles"] == ["OPERATOR", "OPERATOR"]


def test_existing_key_with_changed_explicit_opportunity_preserves_service_conflict(draft_cli):
    different_id = draft_cli["opportunities"][1]["id"]
    with pytest.raises(ConflictError, match="different editorial request"):
        draft_cli["invoke"](different_id)

    draft_cli["evaluate"].assert_not_called()
    draft_cli["start"].assert_called_once()
    assert draft_cli["start"].call_args.args[2] == different_id


@pytest.mark.parametrize("field", ["influencer_id", "mission_id"])
def test_existing_key_preserves_service_configuration_conflict(draft_cli, field):
    draft_cli["run"][field] = uuid4()
    with pytest.raises(ConflictError, match="different editorial request"):
        draft_cli["invoke"]()
    draft_cli["evaluate"].assert_not_called()
    draft_cli["start"].assert_called_once()


def test_existing_key_bound_to_non_editorial_run_conflicts(draft_cli):
    draft_cli["state"]["bound"] = False
    with pytest.raises(ConflictError, match="different editorial request"):
        draft_cli["invoke"]()
    draft_cli["evaluate"].assert_not_called()
    draft_cli["start"].assert_not_called()


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("key", [None, "repeatable-draft"])
def test_new_draft_keeps_qualified_selection_and_service_path(draft_cli, explicit, key):
    draft_cli["state"]["existing"] = False
    chosen = draft_cli["opportunities"][1]
    draft_cli["evaluate"].side_effect = lambda repo, opportunity, mission: [
        {
            "audience_segment": {"id": draft_cli["audience"]},
            "decision": {"decision": "CREATE_CONTENT" if opportunity == chosen else "WATCH"},
        }
    ]
    draft_cli["start"].return_value = draft_cli["run"]

    draft_cli["invoke"](chosen["id"] if explicit else None, key=key)

    assert draft_cli["evaluate"].call_count == (1 if explicit else 2)
    draft_cli["start"].assert_called_once()
    call = draft_cli["start"].call_args.args
    assert call[2] == chosen["id"]
    assert call[3].audience_segment_id == draft_cli["audience"]
    if key:
        assert call[3].idempotency_key == key
    else:
        assert call[3].idempotency_key


def test_new_draft_without_qualified_opportunity_still_stops(draft_cli):
    draft_cli["state"]["existing"] = False
    draft_cli["evaluate"].side_effect = None
    draft_cli["evaluate"].return_value = [
        {"audience_segment": {"id": draft_cli["audience"]}, "decision": {"decision": "WATCH"}}
    ]
    with pytest.raises(SystemExit, match="No opportunity qualified"):
        draft_cli["invoke"]()
    assert draft_cli["evaluate"].call_count == 2
    draft_cli["start"].assert_not_called()
