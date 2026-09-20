from conftest import artifact_data, create, decision, headers


def test_sofia_demo_closes_loop(client, identities):
    # Retains the starter's loop regression, now requiring persistence and human approval.
    identity = identities[0]
    run = create(client, identity)
    assert run["state"] == "AWAITING_APPROVAL"
    data = artifact_data(client, identity, run)
    assert data["qa_reports"][0]["status"] == "PASS"
    assert len(data["skill_runs"]) == 5
    assert all(s["provider"] == "mock" and s["cost"] == 0 for s in data["skill_runs"])
    approved = client.post(
        f"/v1/workflow-runs/{run['id']}/approve",
        headers=headers(identity, "APPROVER"),
        json=decision(run),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["workflow"]["state"] == "APPROVED"
    stored = artifact_data(client, identity, run)
    assert len(stored["approval_records"]) == 1
    audit = client.get(f"/v1/workflow-runs/{run['id']}/audit", headers=headers(identity)).json()
    states = [a["to_state"] for a in audit if a["event_type"] == "STATE_TRANSITION"]
    assert states == [
        "CREATED",
        "SOURCE_CAPTURED",
        "RESEARCHING",
        "RESEARCH_COMPLETE",
        "BRIEFING",
        "BRIEF_COMPLETE",
        "CONTENT_GENERATING",
        "CONTENT_COMPLETE",
        "QA_RUNNING",
        "AWAITING_APPROVAL",
        "APPROVED",
    ]
