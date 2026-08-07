from uuid import uuid4

from app.log_analysis.models import LogAnalysisResult, LogFinding, LogSeverity
from app.log_analysis.store import LogAnalysisStore


def test_store_keeps_latest(tmp_path):
    server_uuid = uuid4()
    store = LogAnalysisStore(tmp_path / "history", history_limit=2)

    def result(message):
        return LogAnalysisResult(
            server_uuid=server_uuid,
            hostname="db1",
            status=LogSeverity.WARNING,
            event_count=1,
            findings=(
                LogFinding(
                    LogSeverity.WARNING,
                    "sshd",
                    "authentication",
                    "warning",
                    message,
                    0.75,
                ),
            ),
            summary="warning",
        )

    store.save(result("one"))
    store.save(result("two"))
    store.save(result("three"))

    assert store.latest(server_uuid).findings[0].evidence == "three"
    assert len(store.history(server_uuid, limit=10)) == 2
