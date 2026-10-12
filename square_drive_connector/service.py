from __future__ import annotations

import hashlib
import logging
import shutil
import uuid
from pathlib import Path

from .config import Settings
from .db import Database
from .parser import ParseResult, parse_square_csv
from .publisher import publish_pending

log = logging.getLogger("square_drive_connector")


class ImportErrorResponse(Exception):
    def __init__(self, message: str, code: int = 422): self.message, self.code = message, code


class ImportService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.database_path)
        self.db.initialize()
        (settings.local_data_dir / "raw").mkdir(parents=True, exist_ok=True)
        (settings.local_data_dir / "tmp").mkdir(parents=True, exist_ok=True)

    def import_temp(self, temp_path: Path, filename: str) -> dict[str, object]:
        sha = hashlib.sha256(temp_path.read_bytes()).hexdigest()
        duplicate = self.db.file_by_hash(sha)
        if duplicate:
            temp_path.unlink(missing_ok=True)
            return {"ok": True, "status": "already_imported", "message": "Exact file hash was already received.", "file_id": duplicate["file_id"], "session_id": duplicate["session_id"], "shot_count": None, "drive_status": "not_published"}
        raw_path = self.settings.local_data_dir / "raw" / f"{sha}.csv"
        shutil.move(str(temp_path), raw_path)  # durable archive precedes parsing/database acknowledgement
        file_id = "f_" + uuid.uuid4().hex[:16]
        try:
            result = parse_square_csv(raw_path.read_bytes(), self.settings.timezone)
        except ValueError as e:
            empty = ParseResult(None, None, None, [], [], [], [str(e)])
            self.db.record(file_id, sha, filename, str(raw_path), empty, self.settings.timezone, "rejected", str(e), None)
            published = publish_pending(self.db, self.settings.drive_output_dir, self.settings.local_data_dir)
            return {"ok": False, "status": "rejected", "message": str(e), "file_id": file_id, "session_id": None, "shot_count": None, "drive_status": "written_to_sync_folder" if published else "pending"}
        overlap = self.db.overlapping_file(result)
        status = "needs_review" if overlap or not result.shots else "imported"
        session_id = "s_" + uuid.uuid4().hex[:16]
        message = "Original preserved; overlapping shot fingerprints require review and were excluded from analysis outputs." if overlap else ("Original preserved, but no valid individual shots were parsed; it was excluded from analysis outputs." if not result.shots else "Original preserved and records committed.")
        self.db.record(file_id, sha, filename, str(raw_path), result, self.settings.timezone, status, message, session_id)
        published = publish_pending(self.db, self.settings.drive_output_dir, self.settings.local_data_dir)
        log.info("Import completed: file_id=%s status=%s shot_count=%s drive_status=%s", file_id, status, len(result.shots), "written" if published else "pending")
        return {"ok": True, "status": status, "message": message, "file_id": file_id, "session_id": session_id, "shot_count": len(result.shots), "drive_status": "written_to_sync_folder" if published else "pending"}
