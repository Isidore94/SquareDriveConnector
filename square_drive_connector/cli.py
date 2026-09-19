from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uvicorn

from .app import create_app
from .config import initialize_env, load_settings
from .service import ImportService
from .publisher import publish_pending


def main() -> None:
    parser = argparse.ArgumentParser(prog="square-drive")
    parser.add_argument("command", choices=["init", "serve", "doctor", "status", "rebuild-outputs", "reconcile"])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--accept")
    args = parser.parse_args()
    if args.command == "init":
        env = Path(".env")
        if not env.exists():
            example = Path(".env.example")
            env.write_text(example.read_text(encoding="utf-8") if example.exists() else "", encoding="utf-8")
        initialize_env(env)
        settings = load_settings(env)
        ImportService(settings)
        print("Initialized local data layout and .env. Set DRIVE_OUTPUT_DIR to your existing Google Drive My Drive folder, then run doctor.")
        return
    try: settings = load_settings()
    except ValueError as e: parser.error(str(e))
    service = ImportService(settings)
    if args.command == "serve":
        uvicorn.run(create_app(settings), host=settings.listen_address, port=settings.port, log_level="info", access_log=False)
    elif args.command == "doctor":
        print(json.dumps({"local_data_dir": str(settings.local_data_dir), "local_data_exists": settings.local_data_dir.is_dir(), "drive_output_dir": str(settings.drive_output_dir) if settings.drive_output_dir else None, "drive_output_exists": bool(settings.drive_output_dir and settings.drive_output_dir.is_dir()), "timezone": settings.timezone, "port": settings.port}, indent=2))
    elif args.command == "status": print(json.dumps(service.db.status(), indent=2))
    elif args.command == "rebuild-outputs":
        print("written_to_sync_folder" if publish_pending(service.db, settings.drive_output_dir, settings.local_data_dir) else "pending")
    elif args.command == "reconcile":
        with service.db.connect() as c:
            if args.accept:
                accepted = service.db.accept_review(args.accept)
                print("accepted" if accepted else "No review record found")
                if accepted: publish_pending(service.db, settings.drive_output_dir, settings.local_data_dir)
            else:
                rows = c.execute("SELECT file_id,original_filename,received_at,message FROM files WHERE status='needs_review'").fetchall()
                print(json.dumps([dict(r) for r in rows], indent=2))


if __name__ == "__main__": main()
