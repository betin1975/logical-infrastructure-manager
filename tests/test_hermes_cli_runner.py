import json
import subprocess
from uuid import uuid4

from app.log_analysis.hermes_cli import HermesCLIRunner, HermesCLISettings
from app.log_analysis.models import LogAnalysisResult, LogFinding, LogSeverity


def test_hermes_cli_runner_parses_json(monkeypatch):
    payload = {
        "summary": "SSH failures increased.",
        "probable_cause": "Repeated login attempts.",
        "recommendations": ["Review source IPs"],
        "confidence": 0.92,
    }

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0],
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        ),
    )

    result = LogAnalysisResult(
        server_uuid=uuid4(),
        hostname="db1",
        status=LogSeverity.WARNING,
        event_count=1,
        findings=(
            LogFinding(
                LogSeverity.WARNING,
                "sshd",
                "authentication",
                "Authentication failures.",
                "Failed password for invalid user",
                0.75,
            ),
        ),
        summary="1 finding",
    )

    insight = HermesCLIRunner(HermesCLISettings()).analyze(result)

    assert insight.confidence == 0.92
    assert insight.recommendations == ("Review source IPs",)
