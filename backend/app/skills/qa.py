from sqlalchemy import text

from app.models.schemas import QAReport


def validate(repository, run_id):
    # The database recomputes checks over persisted data, never trusts a caller-supplied PASS.
    qid = repository.connection.execute(
        text("SELECT finish_qa(:rid)"), {"rid": run_id}
    ).scalar_one()
    record = repository.one("qa_reports", id=qid)
    return qid, QAReport.model_validate(record["payload"], strict=True)
