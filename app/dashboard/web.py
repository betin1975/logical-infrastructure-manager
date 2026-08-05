"""Flask application for the minimal LIM dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from flask import Flask, abort, redirect, render_template, request, url_for

from app.composition import ApplicationServices, CompositionError, build_application_services
from app.inventory import InventoryError


@dataclass(frozen=True, slots=True)
class DashboardState:
    """Immutable dependencies used by dashboard routes."""

    services: ApplicationServices


def create_dashboard(
    services: ApplicationServices | None = None,
) -> Flask:
    """Create the LIM dashboard without global mutable service state."""
    app = Flask(__name__)
    try:
        state = DashboardState(services or build_application_services())
    except CompositionError as exc:
        raise RuntimeError(f"dashboard initialization failed during {exc.stage}") from None

    app.config["LIM_DASHBOARD_STATE"] = state
    app.jinja_env.filters["datetime_utc"] = _format_datetime
    app.jinja_env.filters["bytesize"] = _format_bytes

    @app.get("/")
    def index():
        return redirect(url_for("servers"))

    @app.get("/healthz")
    def healthz():
        return {
            "status": "ok",
            "schema_version": state.services.migration_state.schema_version,
        }

    @app.get("/servers")
    def servers():
        page = state.services.inventory_service.list_servers(limit=1000)
        rows = [
            {
                "server": server,
                "latest": state.services.discovery_service.retrieve_latest(server.uuid),
            }
            for server in page.items
        ]
        return render_template("servers.html", rows=rows, total=page.total)

    @app.get("/servers/<server_uuid>")
    def server_detail(server_uuid: str):
        server = _find_server_or_404(state, server_uuid)
        latest = state.services.discovery_service.retrieve_latest(server.uuid)
        return render_template(
            "server_detail.html",
            server=server,
            latest=latest,
            notice=request.args.get("notice"),
            error=request.args.get("error"),
        )

    @app.post("/servers/<server_uuid>/poll")
    def poll_server(server_uuid: str):
        server = _find_server_or_404(state, server_uuid)

        try:
            result = state.services.polling_service.poll(server.uuid)
        except Exception:
            app.logger.exception("Dashboard poll failed")
            return redirect(
                url_for(
                    "server_detail",
                    server_uuid=server.uuid,
                    error="Poll failed. Check the LIM logs for details.",
                )
            )

        status = _enum_value(getattr(result, "status", None))
        observation_status = _enum_value(
            getattr(result, "observation_state", None)
        )
        observation_uuid = getattr(result, "observation_uuid", None)

        if status == "succeeded":
            detail = "Poll completed successfully."
            if observation_status:
                detail = f"Poll completed: {observation_status} observation."
            if observation_uuid:
                detail = f"{detail} Observation {observation_uuid}."
            return redirect(
                url_for(
                    "server_detail",
                    server_uuid=server.uuid,
                    notice=detail,
                )
            )

        return redirect(
            url_for(
                "server_detail",
                server_uuid=server.uuid,
                error="Poll did not complete successfully. Check the LIM logs.",
            )
        )

    @app.errorhandler(404)
    def not_found(_error: Any):
        return render_template("error.html", title="Not found", message="Server not found."), 404

    @app.errorhandler(500)
    def internal_error(_error: Any):
        return (
            render_template(
                "error.html",
                title="Dashboard error",
                message="The dashboard could not complete this request.",
            ),
            500,
        )

    return app


def _find_server_or_404(state: DashboardState, server_uuid: str):
    try:
        return state.services.inventory_service.find_server_by_id(UUID(server_uuid))
    except (ValueError, InventoryError):
        abort(404)


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw).lower()


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "Never"
    return value.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _format_bytes(value: int | None) -> str:
    if value is None:
        return "—"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024.0 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TiB"
