"""Evaluate whether a LIM-managed server is operationally ready."""

from __future__ import annotations

from .models import ReadinessCheck, ReadinessState, ServerReadiness


class ReadinessService:
    def assess(
        self,
        *,
        server,
        latest,
        log_analysis=None,
        health_security=None,
    ) -> ServerReadiness:
        checks: list[ReadinessCheck] = []

        checks.append(
            ReadinessCheck(
                "Inventory",
                ReadinessState.READY,
                "Server is present in LIM inventory.",
            )
        )

        if server.last_bootstrap_at is not None:
            checks.append(
                ReadinessCheck(
                    "Bootstrap",
                    ReadinessState.READY,
                    f"Last completed {server.last_bootstrap_at}.",
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    "Bootstrap",
                    ReadinessState.NOT_READY,
                    "Bootstrap has not been recorded.",
                )
            )

        if latest is not None:
            checks.append(
                ReadinessCheck(
                    "Polling",
                    ReadinessState.READY,
                    "LIM has a collected observation.",
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    "Polling",
                    ReadinessState.NOT_READY,
                    "No observation has been collected.",
                )
            )

        if log_analysis is not None:
            checks.append(
                ReadinessCheck(
                    "Log analysis",
                    ReadinessState.READY,
                    "Log analysis has completed successfully.",
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    "Log analysis",
                    ReadinessState.UNKNOWN,
                    "Log analysis has not been verified yet.",
                )
            )

        if health_security is not None:
            checks.append(
                ReadinessCheck(
                    "Health & Security",
                    ReadinessState.READY,
                    "Health and security assessment is available.",
                )
            )
        else:
            checks.append(
                ReadinessCheck(
                    "Health & Security",
                    ReadinessState.UNKNOWN,
                    "Health and security assessment has not been verified yet.",
                )
            )

        if any(check.state is ReadinessState.NOT_READY for check in checks):
            state = ReadinessState.NOT_READY
        elif any(check.state is ReadinessState.UNKNOWN for check in checks):
            state = ReadinessState.ATTENTION
        else:
            state = ReadinessState.READY

        return ServerReadiness(state=state, checks=tuple(checks))
