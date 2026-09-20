from app.models.schemas import SourceInput
from app.workflows.sofia_demo import run_sofia_demo


def test_sofia_demo_closes_loop() -> None:
    source = SourceInput(
        source_id="test-source",
        title="Test official update",
        source_type="official",
        market="ES",
        raw_text="Official test evidence for a Spanish SMB support programme.",
    )
    run = run_sofia_demo(source)

    assert run.tenant_id == "growie"
    assert run.influencer_id == "sofia_es"
    assert run.qa.status == "APPROVE"
    assert run.status.value == "APPROVED"
    assert len(run.draft.slides) >= 5
    assert "IA" in run.draft.disclosure
