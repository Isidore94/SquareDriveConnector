from pathlib import Path

from fastapi.testclient import TestClient

from square_drive_connector.app import create_app
from square_drive_connector.config import Settings
from square_drive_connector.service import ImportService


CSV = b"Dates,11:50 18 Sep. 2026,Place,Range\nClub,Index,Carry(yd),Offline(yd),ClubSpeed,SmashFactor\nDriver,0,100,R2.0,0,0\n"
CSV_TWO = b"Dates,11:50 18 Sep. 2026,Place,Range\nClub,Index,Carry(yd),Offline(yd),ClubSpeed,SmashFactor\nDriver,0,100,R2.0,0,0\n7i,1,80,L1.0,0,0\n"


def settings(tmp_path: Path, drive=True):
    local = tmp_path / "local"
    output = tmp_path / "drive" if drive else None
    local.mkdir()
    if output: output.mkdir()
    return Settings("127.0.0.1", 8765, "a-secret-token", local, output, "America/Vancouver", 1024)


def write_temp(service, data):
    path = service.settings.local_data_dir / "tmp" / "a.csv"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(data)
    return path


def test_import_dedup_raw_bytes_and_outputs(tmp_path):
    service = ImportService(settings(tmp_path))
    response = service.import_temp(write_temp(service, CSV), "original.csv")
    assert response["status"] == "imported"
    assert response["drive_status"] == "written_to_sync_folder"
    raw = next((service.settings.local_data_dir / "raw").glob("*.csv"))
    assert raw.read_bytes() == CSV
    assert (service.settings.drive_output_dir / "all_shots.csv").exists()
    again = service.import_temp(write_temp(service, CSV), "renamed.csv")
    assert again["status"] == "already_imported"
    assert service.db.status()["shots"] == 1
    from square_drive_connector.publisher import publish
    publish(service.db, service.settings.drive_output_dir, service.settings.local_data_dir)
    lines = (service.settings.drive_output_dir / "all_shots.csv").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # a rebuild remains derived from SQLite, never append-only


def test_overlap_needs_review_and_is_not_counted(tmp_path):
    service = ImportService(settings(tmp_path))
    service.import_temp(write_temp(service, CSV), "one.csv")
    result = service.import_temp(write_temp(service, CSV_TWO), "two.csv")
    assert result["status"] == "needs_review"
    assert service.db.status()["shots"] == 3  # preserved in DB for reconciliation
    assert "s_" in result["session_id"]
    assert "two.csv" not in (service.settings.drive_output_dir / "sessions.csv").read_text()


def test_pending_publication_survives_restart_and_retries(tmp_path):
    config = settings(tmp_path, drive=False)
    service = ImportService(config)
    result = service.import_temp(write_temp(service, CSV), "one.csv")
    assert result["drive_status"] == "pending"
    assert service.db.status()["pending_publications"] >= 1
    # Point a restarted service at an available destination using the same local data/database.
    out = tmp_path / "drive"; out.mkdir()
    restarted = ImportService(Settings(config.listen_address, config.port, config.upload_token, config.local_data_dir, out, config.timezone, config.max_upload_bytes))
    from square_drive_connector.publisher import publish_pending
    assert publish_pending(restarted.db, out, restarted.settings.local_data_dir)
    assert (out / "manifest.json").exists()


def test_api_raw_body_auth_limit_and_unsafe_filename(tmp_path):
    app = create_app(settings(tmp_path))
    client = TestClient(app)
    headers = {"Authorization": "Bearer a-secret-token", "X-Filename": "square%20file.csv", "Content-Type": "text/csv"}
    good = client.post("/api/square/upload", content=CSV, headers=headers)
    assert good.status_code == 200 and good.json()["status"] == "imported"
    assert client.get("/api/status", headers={"Authorization": "Bearer wrong"}).status_code == 401
    unsafe = dict(headers); unsafe["X-Filename"] = "..%2Fbad.csv"
    assert client.post("/api/square/upload", content=CSV, headers=unsafe).status_code == 400
    too_large = client.post("/api/square/upload", content=b"x" * 2000, headers=headers)
    assert too_large.status_code == 413


def test_rejected_csv_preserves_raw_and_octet_requires_square_format(tmp_path):
    app = create_app(settings(tmp_path))
    client = TestClient(app)
    headers = {"Authorization": "Bearer a-secret-token", "X-Filename": "bad.csv", "Content-Type": "application/octet-stream"}
    reply = client.post("/api/square/upload", content=b"not,csv\n", headers=headers)
    assert reply.status_code == 422
    assert reply.json()["status"] == "rejected"
    assert list((app.state._state if False else tmp_path / "local" / "raw").glob("*.csv"))
