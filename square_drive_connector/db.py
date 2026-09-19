from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .parser import PARSER_VERSION, ParseResult


def utcnow() -> str: return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path): self.path = path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try: yield conn; conn.commit()
        except Exception: conn.rollback(); raise
        finally: conn.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS files (file_id TEXT PRIMARY KEY, sha256 TEXT UNIQUE NOT NULL, original_filename TEXT NOT NULL, raw_path TEXT NOT NULL, received_at TEXT NOT NULL, status TEXT NOT NULL, message TEXT, parser_version TEXT NOT NULL, session_id TEXT);
            CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, file_id TEXT UNIQUE NOT NULL REFERENCES files(file_id), timestamp_text TEXT, session_time TEXT, timezone TEXT NOT NULL, place TEXT, status TEXT NOT NULL, shot_count INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shots (shot_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id), source_row INTEGER NOT NULL, source_index TEXT NOT NULL, club_original TEXT NOT NULL, club_normalized TEXT NOT NULL, raw_json TEXT NOT NULL, normalized_json TEXT NOT NULL, fingerprint TEXT NOT NULL, warnings_json TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS shots_fingerprint ON shots(fingerprint);
            CREATE TABLE IF NOT EXISTS issues (issue_id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL REFERENCES files(file_id), source_row INTEGER, severity TEXT NOT NULL, message TEXT NOT NULL, raw_json TEXT, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS publications (job_id INTEGER PRIMARY KEY AUTOINCREMENT, file_id TEXT NOT NULL REFERENCES files(file_id), state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, updated_at TEXT NOT NULL);
            """)

    def file_by_hash(self, sha: str):
        with self.connect() as c: return c.execute("SELECT * FROM files WHERE sha256=?", (sha,)).fetchone()

    def overlapping_file(self, result: ParseResult):
        if not result.shots: return None
        fingerprints = [s.fingerprint for s in result.shots]
        marks = ",".join("?" * len(fingerprints))
        with self.connect() as c:
            return c.execute(f"SELECT DISTINCT s.file_id FROM sessions s JOIN shots sh ON sh.session_id=s.session_id WHERE s.status='imported' AND sh.fingerprint IN ({marks}) LIMIT 1", fingerprints).fetchone()

    def record(self, file_id: str, sha: str, filename: str, raw_path: str, result: ParseResult, tz: str, status: str, message: str, session_id: str | None) -> None:
        with self.connect() as c:
            c.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?,?,?)", (file_id, sha, filename, raw_path, utcnow(), status, message, PARSER_VERSION, session_id))
            for issue in result.malformed:
                c.execute("INSERT INTO issues(file_id,source_row,severity,message,raw_json,created_at) VALUES(?,?,?,?,?,?)", (file_id, issue["source_row"], "error", issue["reason"], json.dumps(issue["raw"]), utcnow()))
            for warning in result.warnings:
                c.execute("INSERT INTO issues(file_id,severity,message,created_at) VALUES(?,?,?,?)", (file_id, "warning", warning, utcnow()))
            if session_id:
                c.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", (session_id, file_id, result.original_timestamp_text, result.session_time_iso, tz, result.place, status, len(result.shots), utcnow()))
                # Keep parsed rows for review too, but only imported sessions are exported.
                for number, shot in enumerate(result.shots, 1):
                    shot_id = f"{session_id}-r{shot.source_row}"
                    c.execute("INSERT INTO shots VALUES (?,?,?,?,?,?,?,?,?,?)", (shot_id, session_id, shot.source_row, shot.source_index, shot.club_original, shot.club_normalized, json.dumps(shot.raw_values), json.dumps(shot.normalized), shot.fingerprint, json.dumps(shot.warnings)))
            c.execute("INSERT INTO publications(file_id,state,updated_at) VALUES(?,?,?)", (file_id, "pending", utcnow()))

    def pending(self):
        with self.connect() as c: return c.execute("SELECT * FROM publications WHERE state='pending' ORDER BY job_id").fetchall()

    def publication(self, job_id: int, state: str, error: str | None = None):
        with self.connect() as c: c.execute("UPDATE publications SET state=?, attempts=attempts+1,last_error=?,updated_at=? WHERE job_id=?", (state, error, utcnow(), job_id))

    def status(self):
        with self.connect() as c:
            return {"files": c.execute("SELECT count(*) FROM files").fetchone()[0], "sessions": c.execute("SELECT count(*) FROM sessions WHERE status='imported'").fetchone()[0], "shots": c.execute("SELECT count(*) FROM shots").fetchone()[0], "pending_publications": c.execute("SELECT count(*) FROM publications WHERE state='pending'").fetchone()[0], "needs_review": c.execute("SELECT count(*) FROM files WHERE status='needs_review'").fetchone()[0]}

    def export_data(self):
        with self.connect() as c:
            return (c.execute("SELECT * FROM sessions WHERE status='imported' ORDER BY session_time,created_at").fetchall(), c.execute("SELECT sh.*,s.file_id,s.session_time,s.timezone,s.place FROM shots sh JOIN sessions s ON s.session_id=sh.session_id WHERE s.status='imported' ORDER BY s.session_time,sh.source_row").fetchall(), c.execute("SELECT * FROM issues ORDER BY issue_id").fetchall(), c.execute("SELECT * FROM files ORDER BY received_at").fetchall())

    def accept_review(self, file_id: str) -> bool:
        with self.connect() as c:
            file = c.execute("SELECT * FROM files WHERE file_id=? AND status='needs_review'", (file_id,)).fetchone()
            session = c.execute("SELECT * FROM sessions WHERE file_id=?", (file_id,)).fetchone()
            if not file or not session: return False
            c.execute("UPDATE files SET status='imported',message='Manually reconciled and accepted.' WHERE file_id=?", (file_id,))
            c.execute("UPDATE sessions SET status='imported' WHERE file_id=?", (file_id,))
            c.execute("INSERT INTO publications(file_id,state,updated_at) VALUES(?,?,?)", (file_id, "pending", utcnow()))
            return True
