#!/usr/bin/env python3
"""Start TrialMate: dashboard + API + background workers (+ Photon if configured).

    Windows:  py run.py --mock        (or double-click start.bat)
    Mac:      python3 run.py --mock

    --mock        run without an API key (rule-based stand-in model)
    --reset       wipe the local database and re-seed the demo data
    --no-browser  don't open the dashboard automatically
    --check       test every key and service, then exit (or double-click check.bat)
"""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

if sys.version_info < (3, 9):
    sys.exit("TrialMate needs Python 3.9 or newer.")

for stream in (sys.stdout, sys.stderr):  # Windows consoles can't print every character; never crash on one
    try:
        stream.reconfigure(errors="replace")
    except Exception:
        pass

from trialmate.app import App
from trialmate.config import Settings
from trialmate.server import serve


def main() -> None:
    parser = argparse.ArgumentParser(description="TrialMate server")
    parser.add_argument("--port", type=int)
    parser.add_argument("--host")
    parser.add_argument("--reset", action="store_true", help="delete the local database first")
    parser.add_argument("--mock", action="store_true", help="use the offline mock model instead of Claude")
    parser.add_argument("--no-browser", action="store_true", help="don't open the dashboard in a browser")
    parser.add_argument("--check", action="store_true", help="test every key and outside service, then exit")
    parser.add_argument("--no-droid", action="store_true", help="with --check: skip the live Droid run")
    args = parser.parse_args()

    settings = Settings()
    if args.mock:
        settings.llm_mode = "mock"
    if args.port:
        settings.port = args.port
    if args.host:
        settings.host = args.host
    if args.check:
        from trialmate import doctor
        sys.exit(doctor.run(settings, droid=not args.no_droid))
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(settings.db_path) + suffix).unlink()
            except FileNotFoundError:
                pass
            except PermissionError:
                sys.exit("The database is in use. Close the other TrialMate window first, then run --reset again.")

    app = App(settings)
    app.start_workers()
    try:
        httpd = serve(app, settings.host, settings.port)
    except OSError:
        sys.exit(f"Port {settings.port} is busy. Close the other TrialMate window, or run with --port 8001.")
    url = f"http://localhost:{settings.port}" if settings.host in ("127.0.0.1", "0.0.0.0", "localhost") \
        else f"http://{settings.host}:{settings.port}"
    llm = app.llm.name
    print("\n  TrialMate is running")
    print(f"  Dashboard : {url}")
    print(f"  Model     : {getattr(app.llm, 'label', 'Claude') + ' ' + app.llm.model if llm != 'mock' else 'MOCK (no API key; add one to .env for Claude)'}")
    print(f"  Photon    : {'on, platform ' + settings.photon_platform if settings.photon_enabled else 'off (phone simulator only)'}")
    print(f"  Droid     : {'ready' if app.builder_engine() == 'droid' else 'not configured (basic study builder)'}")
    print(f"  Database  : {settings.db_path}")
    print("  Press Ctrl+C to stop\n")
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        app.stop()


if __name__ == "__main__":
    main()
