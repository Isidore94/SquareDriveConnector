from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .db import Database

EXPORT_VERSION = "1.0"


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): digest.update(block)
    return digest.hexdigest()


def _atomic_bytes(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".square-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _csv_bytes(headers: list[str], rows: list[dict[str, object]]) -> bytes:
    from io import StringIO
    out = StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=headers, extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode("utf-8")


def publish(db: Database, output: Path, local_data: Path) -> dict[str, object]:
    if not output.exists() or not output.is_dir():
        raise FileNotFoundError("DRIVE_OUTPUT_DIR does not exist or is unavailable; it was not created automatically")
    sessions, shots, issues, files = db.export_data()
    shot_rows: list[dict[str, object]] = []
    for sh in shots:
        normalized = json.loads(sh["normalized_json"])
        raw = json.loads(sh["raw_json"])
        shot_rows.append({
            "shot_id": sh["shot_id"], "session_id": sh["session_id"], "file_id": sh["file_id"], "source_row": sh["source_row"], "source_index": sh["source_index"], "club_original": sh["club_original"], "club_normalized": sh["club_normalized"], "session_time": sh["session_time"], "timezone_assumption": sh["timezone"], "place": sh["place"],
            "ball_speed_mph": normalized.get("Ball Speed(mph)"), "apex_yd": normalized.get("Apex(yd)"), "carry_yd": normalized.get("Carry(yd)"), "total_yd": normalized.get("Total(yd)"), "offline_yd_right_positive": normalized.get("Offline(yd)"), "launch_direction_right_positive": normalized.get("Launch Direction"), "spin_axis_right_positive": normalized.get("Spin Axis"), "club_path_right_positive": normalized.get("Club Path"), "face_angle_right_positive": normalized.get("Face Angle"),
            "raw_values": json.dumps(raw, sort_keys=True), "normalized_values": json.dumps(normalized, sort_keys=True), "extra_fields": json.dumps(raw, sort_keys=True), "quality_warnings": sh["warnings_json"],
        })
    shot_headers = ["shot_id", "session_id", "file_id", "source_row", "source_index", "club_original", "club_normalized", "session_time", "timezone_assumption", "place", "ball_speed_mph", "apex_yd", "carry_yd", "total_yd", "offline_yd_right_positive", "launch_direction_right_positive", "spin_axis_right_positive", "club_path_right_positive", "face_angle_right_positive", "raw_values", "normalized_values", "extra_fields", "quality_warnings"]
    session_rows = [{"session_id": s["session_id"], "file_id": s["file_id"], "session_time": s["session_time"], "original_timestamp_text": s["timestamp_text"], "timezone_assumption": s["timezone"], "place": s["place"], "shot_count": s["shot_count"], "quality_status": s["status"]} for s in sessions]
    session_headers = ["session_id", "file_id", "session_time", "original_timestamp_text", "timezone_assumption", "place", "shot_count", "quality_status"]
    summary: dict[str, list[float]] = defaultdict(list)
    for row in shot_rows:
        carry = json.loads(row["normalized_values"]).get("Carry(yd)")
        if isinstance(carry, (int, float)): summary[str(row["club_normalized"])].append(float(carry))
    club_rows = [{"club_normalized": club, "shot_count": len(values), "carry_yd_mean": round(sum(values)/len(values), 3) if values else "", "carry_yd_min": min(values) if values else "", "carry_yd_max": max(values) if values else ""} for club, values in sorted(summary.items())]
    issue_rows = [{"file_id": i["file_id"], "source_row": i["source_row"], "severity": i["severity"], "message": i["message"], "raw": i["raw_json"], "created_at": i["created_at"]} for i in issues]
    writes: dict[Path, bytes] = {
        output / "sessions.csv": _csv_bytes(session_headers, session_rows),
        output / "all_shots.csv": _csv_bytes(shot_headers, shot_rows),
        output / "club_summary.csv": _csv_bytes(["club_normalized", "shot_count", "carry_yd_mean", "carry_yd_min", "carry_yd_max"], club_rows),
        output / "quality_warnings.csv": _csv_bytes(["file_id", "source_row", "severity", "message", "raw", "created_at"], issue_rows),
        output / "README.md": b"# Square Golf data export\n\nGenerated locally from SQLite. Right is positive and left is negative for directional values. Source units and raw labels are preserved in JSON columns; units not established by Square are not guessed. Session timestamps use the configured timezone assumption. Upload time is retained in the local database separately. No per-shot timestamps are inferred. Placeholder zeros are preserved and flagged. This folder being written does not prove Google cloud synchronization.\n",
    }
    for path, data in writes.items(): _atomic_bytes(path, data)
    # Per-session files and byte-identical raw copies are generated only from archive/database records.
    for session in sessions:
        own = [r for r in shot_rows if r["session_id"] == session["session_id"]]
        session_dir = output / "sessions" / session["session_id"]
        _atomic_bytes(session_dir / "shots.csv", _csv_bytes(shot_headers, own))
        _atomic_bytes(session_dir / "metadata.json", json.dumps(dict(session), indent=2, default=str).encode("utf-8"))
    for file in files:
        source = Path(file["raw_path"])
        if source.exists():
            destination = output / "raw" / f"{file['sha256']}.csv"
            if not destination.exists() or _checksum(destination) != file["sha256"]:
                _atomic_bytes(destination, source.read_bytes())
    checksums = {str(p.relative_to(output)).replace("\\", "/"): _checksum(p) for p in writes}
    manifest = {"schema_version": EXPORT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(), "counts": {"sessions": len(sessions), "shots": len(shot_rows), "files": len(files), "issues": len(issue_rows)}, "latest_successful_import": max((f["received_at"] for f in files if f["status"] == "imported"), default=None), "latest_completed_local_publication": datetime.now(timezone.utc).isoformat(), "checksums": checksums, "cloud_sync_status": "unverified"}
    _atomic_bytes(output / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return manifest


def publish_pending(db: Database, output: Path | None, local_data: Path) -> bool:
    jobs = db.pending()
    if not jobs: return True
    if output is None:
        for job in jobs: db.publication(job["job_id"], "pending", "DRIVE_OUTPUT_DIR is not configured")
        return False
    try:
        publish(db, output, local_data)
    except (OSError, ValueError) as e:
        for job in jobs: db.publication(job["job_id"], "pending", str(e))
        return False
    for job in jobs: db.publication(job["job_id"], "written")
    return True
