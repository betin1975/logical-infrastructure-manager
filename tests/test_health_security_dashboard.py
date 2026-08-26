from uuid import uuid4

from app.health_security.service import HealthSecurityService


def test_health_security_classification():
    document = {
        "generated_at": "2026-08-26T00:00:00+00:00",
        "collector_version": "1.2.0",
        "updates": {
            "available_total": 20,
            "security_total": 0,
            "security_packages": [],
            "attention_security_packages": [],
            "reboot_required": True,
            "apt_lists_age_seconds": 1000,
            "apt_lists_stale": False,
        },
        "systemd": {
            "failed_units": [
                {"unit": "fwupd-refresh.service"}
            ]
        },
        "logs": {
            "critical_count": 0,
            "error_count": 1,
            "warning_count": 23,
            "security_count": 0,
            "findings": [],
        },
    }

    assessment = HealthSecurityService._parse(
        uuid4(),
        "analyticsdb",
        document,
    )

    assert assessment.overall_status == "warning"
    assert assessment.available_updates == 20
    assert assessment.reboot_required is True
    assert assessment.failed_units == ("fwupd-refresh.service",)


def test_security_update_is_critical():
    document = {
        "generated_at": "2026-08-26T00:00:00+00:00",
        "collector_version": "1.2.0",
        "updates": {
            "available_total": 1,
            "security_total": 1,
            "security_packages": ["openssl"],
            "attention_security_packages": ["openssl"],
            "reboot_required": False,
            "apt_lists_age_seconds": 100,
            "apt_lists_stale": False,
        },
        "systemd": {"failed_units": []},
        "logs": {
            "critical_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "security_count": 0,
            "findings": [],
        },
    }

    assessment = HealthSecurityService._parse(
        uuid4(),
        "server1",
        document,
    )

    assert assessment.overall_status == "critical"
    assert assessment.security_updates == 1
