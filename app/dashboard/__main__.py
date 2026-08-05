"""Run the LIM dashboard with the built-in Flask development server."""

from __future__ import annotations

import argparse
import os

from .web import create_dashboard


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the LIM dashboard")
    parser.add_argument(
        "--host",
        default=os.environ.get("LIM_DASHBOARD_HOST", "0.0.0.0"),
        help="listen address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LIM_DASHBOARD_PORT", "8088")),
        help="listen port (default: 8088)",
    )
    parser.add_argument("--debug", action="store_true", help="enable Flask debug mode")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if not 1 <= args.port <= 65535:
        raise SystemExit("dashboard port must be between 1 and 65535")
    app = create_dashboard()
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
