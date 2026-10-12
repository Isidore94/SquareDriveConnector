import hashlib
import json
import os

import pytest
from fastapi.testclient import TestClient

from square_drive_connector.app import create_app
from square_drive_connector.config import load_settings
from square_drive_connector.parser import parse_square_csv
from square_drive_connector.publisher import publish, publish_pending
from square_drive_connector.service import ImportService

from tests.test_service_and_api import CSV, CSV_TWO, settings, write_temp

HEAD = b"Club,Index,Carry(yd),Offline(yd),ClubSpeed,SmashFactor\n"
PLACEHOLDER_ROW = b"7i,1,80,L1.0,0,0\n"
AUTH = {"Authorization": "Bearer a-secret-token"}


def test_unknown_timezone_fails_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOAD_TOKEN", "t")
    monkeypatch.setenv("TIMEZONE", "Europe/London")
    assert load_settings(tmp_path / "missing.env").timezone == "Europe/London"
    monkeypatch.setenv("TIMEZONE", "America/Vancuver")
    with pytest.raises(ValueError, match="TIMEZONE"):
        load_settings(tmp_path / "missing.env")


def test_status_reports_written_when_nothing_is_pending(tmp_path):
    (tmp_path / "offline").mkdir()
    offline = TestClient(create_app(settings(tmp_path / "offline", drive=False)))
    offline.post("/api/square/upload", content=CSV, headers={**AUTH, "X-Filename": "a.csv", "Content-Type": "text/csv"})
    assert offline.get("/api/status", headers=AUTH).json()["drive_status"] == "pending"

    client = TestClient(create_app(settings(tmp_path)))
    reply = client.post("/api/square/upload", content=CSV, headers={**AUTH, "X-Filename": "a.csv", "Content-Type": "text/csv"})
    assert reply.status_code == 200
    assert reply.json()["status"] == "imported" and reply.json()["drive_status"] == "written_to_sync_folder"
    status = client.get("/api/status", headers=AUTH).json()
    assert status["pending_publications"] == 0
    assert status["drive_status"] == "written_to_sync_folder"


def test_rejected_upload_publishes_raw_copy_immediately(tmp_path):
    service = ImportService(settings(tmp_path))
    data = b"not,csv\n"
    result = service.import_temp(write_temp(service, data), "bad.csv")
    assert result["status"] == "rejected"
    assert result["drive_status"] == "written_to_sync_folder"
    assert service.db.status()["pending_publications"] == 0
    raw = service.settings.drive_output_dir / "raw" / f"{hashlib.sha256(data).hexdigest()}.csv"
    assert raw.read_bytes() == data


def test_publish_skips_rewriting_unchanged_files(tmp_path):
    service = ImportService(settings(tmp_path))
    session_id = service.import_temp(write_temp(service, CSV), "one.csv")["session_id"]
    output = service.settings.drive_output_dir
    shots = output / "sessions" / session_id / "shots.csv"
    os.utime(shots, ns=(1_000_000_000 * 10**9, 1_000_000_000 * 10**9))
    before = json.loads((output / "manifest.json").read_text())["generated_at"]
    publish(service.db, output, service.settings.local_data_dir)
    assert json.loads((output / "manifest.json").read_text())["generated_at"] != before  # manifest still rewritten
    assert shots.stat().st_mtime_ns == 1_000_000_000 * 10**9


def test_shared_placeholder_shot_does_not_trigger_overlap_review(tmp_path):
    service = ImportService(settings(tmp_path))
    a = b"Dates,11:50 18 Sep. 2026,Place,Range\n" + HEAD + b"Driver,0,100,R2.0,90,1.4\n" + PLACEHOLDER_ROW
    b = b"Dates,12:30 19 Sep. 2026,Place,Range\n" + HEAD + b"Driver,0,120,R3.0,95,1.45\n" + PLACEHOLDER_ROW
    assert service.import_temp(write_temp(service, a), "a.csv")["status"] == "imported"
    assert service.import_temp(write_temp(service, b), "b.csv")["status"] == "imported"
    assert service.db.status()["sessions"] == 2


def test_all_placeholder_session_imports_without_overlap_check(tmp_path):
    service = ImportService(settings(tmp_path))
    a = b"Dates,11:50 18 Sep. 2026,Place,Range\n" + HEAD + b"Driver,0,100,R2.0,90,1.4\n" + PLACEHOLDER_ROW
    only_placeholders = b"Dates,12:30 19 Sep. 2026,Place,Range\n" + HEAD + PLACEHOLDER_ROW
    service.import_temp(write_temp(service, a), "a.csv")
    result = service.import_temp(write_temp(service, only_placeholders), "p.csv")
    assert result["status"] == "imported"
    assert result["shot_count"] == 1


def test_bomless_cp1252_csv_is_not_decoded_as_utf16(tmp_path):
    bom_utf16 = "Dates,11:50 18 Sep. 2026,Place,Café\nClub,Index,Carry(yd)\nDriver,0,100\n".encode("utf-16")
    assert parse_square_csv(bom_utf16, "America/Vancouver").place == "Café"
    data = "Dates,11:50 18 Sep. 2026,Place,Café\nClub,Index,Carry(yd)\nDriver,0,100\n".encode("cp1252")
    if len(data) % 2: data += b"\n"
    assert len(data) % 2 == 0  # odd lengths fail UTF-16 by accident; the bug needs an even length
    assert parse_square_csv(data, "America/Vancouver").place == "Café"


def test_accepted_review_session_is_published(tmp_path):
    service = ImportService(settings(tmp_path))
    service.import_temp(write_temp(service, CSV), "one.csv")
    second = service.import_temp(write_temp(service, CSV_TWO), "two.csv")
    assert second["status"] == "needs_review"
    assert service.db.accept_review(second["file_id"]) is True
    assert publish_pending(service.db, service.settings.drive_output_dir, service.settings.local_data_dir) is True
    output = service.settings.drive_output_dir
    assert second["session_id"] in (output / "sessions.csv").read_text(encoding="utf-8")
    shot_lines = [l for l in (output / "all_shots.csv").read_text(encoding="utf-8").splitlines()[1:] if second["session_id"] in l]
    assert len(shot_lines) == 2
    assert service.db.status()["pending_publications"] == 0


def test_all_shots_has_no_duplicate_extra_fields_column(tmp_path):
    service = ImportService(settings(tmp_path))
    service.import_temp(write_temp(service, CSV), "one.csv")
    header = (service.settings.drive_output_dir / "all_shots.csv").read_text(encoding="utf-8").splitlines()[0].split(",")
    assert "raw_values" in header and "quality_warnings" in header
    assert "extra_fields" not in header
