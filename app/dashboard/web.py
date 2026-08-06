"""Flask application for the LIM dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from flask import (
    Flask,
    abort,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from app.composition import (
    ApplicationServices,
    CompositionError,
    build_application_services,
)
from app.dashboard.checks import build_system_checks
from app.dashboard.overview import build_server_overview
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
        raise RuntimeError(
            f"dashboard initialization failed during {exc.stage}"
        ) from None

    app.config["LIM_DASHBOARD_STATE"] = state
    app.config["LIM_LOG_ANALYSIS_CACHE"] = {}
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

    @app.get("/onboarding")
    def onboarding():
        return render_template(
            "onboarding.html",
            server_name=request.args.get("server_name", ""),
            server_ip=request.args.get("server_ip", ""),
            admin_user=request.args.get("admin_user", "deployer"),
            fingerprint=request.args.get("fingerprint", ""),
        )

    @app.route("/collector-upgrades", methods=["GET", "POST"])
    def collector_upgrades():
        configured_version = state.services.bootstrap_service.settings.collector_version
        results = ()
        error = None

        if request.method == "POST":
            version = request.form.get("version", "").strip()
            try:
                concurrency = int(request.form.get("concurrency", "10"))
            except ValueError:
                concurrency = 10
            concurrency = max(1, min(concurrency, 32))
            dry_run = request.form.get("dry_run") == "1"
            selected_values = request.form.getlist("server_uuid")
            selected_server_uuids = None
            if selected_values:
                try:
                    selected_server_uuids = {UUID(value) for value in selected_values}
                except ValueError:
                    error = "One or more selected server IDs are invalid."

            if version != configured_version:
                error = "Target version must match the configured collector version."
            elif error is None:
                try:
                    results = state.services.collector_upgrade_service.upgrade_all(
                        version=version,
                        concurrency=concurrency,
                        dry_run=dry_run,
                        artifact_base_url=request.url_root,
                        server_uuids=selected_server_uuids,
                    )
                except Exception:
                    app.logger.exception("Bulk collector upgrade failed")
                    error = "Bulk collector upgrade failed. Check LIM logs."

        return render_template(
            "collector_upgrades.html",
            configured_version=configured_version,
            results=results,
            error=error,
        )

    @app.get("/internal/collector")
    def collector_artifact():
        artifact = Path(app.root_path).parent / "bootstrap/artifacts/remote_health.py"
        payload = artifact.read_bytes()
        configured_version = state.services.bootstrap_service.settings.collector_version
        expected_sha = sha256(payload).hexdigest()

        if request.args.get("version") != configured_version:
            abort(404)
        if request.args.get("sha256") != expected_sha:
            abort(404)

        return send_file(
            artifact,
            mimetype="text/x-python",
            as_attachment=True,
            download_name="remote-health-json",
            conditional=False,
            etag=expected_sha,
            max_age=0,
        )

    @app.post("/servers/<server_uuid>/analyze-logs")
    def analyze_server_logs(server_uuid: str):
        server = _find_server_or_404(state, server_uuid)
        try:
            result = state.services.log_analysis_service.analyze(server.uuid)
        except Exception:
            app.logger.exception("Server log analysis failed")
            return redirect(
                url_for(
                    "server_detail",
                    server_uuid=server.uuid,
                    error="Log analysis failed. Check LIM logs.",
                )
            )
        app.config["LIM_LOG_ANALYSIS_CACHE"][str(server.uuid)] = result
        return redirect(
            url_for(
                "server_detail",
                server_uuid=server.uuid,
                notice="Log analysis completed.",
            )
        )

    @app.get("/servers/<server_uuid>")
    def server_detail(server_uuid: str):
        server = _find_server_or_404(state, server_uuid)
        latest = state.services.discovery_service.retrieve_latest(server.uuid)
        system_checks = build_system_checks(latest)
        return render_template(
            "server_detail.html",
            server=server,
            latest=latest,
            system_checks=system_checks,
            overview=build_server_overview(server, latest, system_checks),
            log_analysis=app.config["LIM_LOG_ANALYSIS_CACHE"].get(str(server.uuid)),
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
        observation_state = _enum_value(getattr(result, "observation_state", None))
        observation_uuid = getattr(result, "observation_uuid", None)

        if status == "succeeded":
            detail = "Poll completed successfully."
            if observation_state:
                detail = f"Poll completed: {observation_state} observation."
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
        return (
            render_template(
                "error.html",
                title="Not found",
                message="Server not found.",
            ),
            404,
        )

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
