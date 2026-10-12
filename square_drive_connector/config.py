from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    listen_address: str
    port: int
    upload_token: str
    local_data_dir: Path
    drive_output_dir: Path | None
    timezone: str
    max_upload_bytes: int

    @property
    def database_path(self) -> Path:
        return self.local_data_dir / "square.sqlite3"


def load_settings(env_file: str | Path = ".env", require_token: bool = True) -> Settings:
    load_dotenv(env_file, override=False)
    token = os.getenv("UPLOAD_TOKEN", "").strip()
    if require_token and not token:
        raise ValueError("UPLOAD_TOKEN is required; run square-drive init and edit .env")
    drive = os.getenv("DRIVE_OUTPUT_DIR", "").strip()
    tz = os.getenv("TIMEZONE", "America/Vancouver")
    try:
        ZoneInfo(tz)
    except ZoneInfoNotFoundError:
        raise ValueError(f"TIMEZONE '{tz}' is not a known IANA zone name") from None
    return Settings(
        listen_address=os.getenv("LISTEN_ADDRESS", "0.0.0.0"),
        port=int(os.getenv("PORT", "8765")),
        upload_token=token,
        local_data_dir=Path(os.getenv("LOCAL_DATA_DIR", "C:\\SquareDriveConnectorData")).expanduser(),
        drive_output_dir=Path(drive).expanduser() if drive else None,
        timezone=tz,
        max_upload_bytes=int(float(os.getenv("MAX_UPLOAD_MIB", "20")) * 1024 * 1024),
    )


def initialize_env(path: Path) -> None:
    """Create/update only a blank UPLOAD_TOKEN; do not expose it to stdout."""
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if "UPLOAD_TOKEN=" in content:
        lines = [
            ("UPLOAD_TOKEN=" + secrets.token_urlsafe(32)) if line.strip() == "UPLOAD_TOKEN=" else line
            for line in content.splitlines()
        ]
        content = "\n".join(lines) + "\n"
    else:
        content += ("\n" if content and not content.endswith("\n") else "") + "UPLOAD_TOKEN=" + secrets.token_urlsafe(32) + "\n"
    path.write_text(content, encoding="utf-8")
