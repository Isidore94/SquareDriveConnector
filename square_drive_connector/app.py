from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .publisher import publish_pending
from .service import ImportService


def _filename(value: str | None) -> str:
    if not value: raise HTTPException(400, detail="X-Filename is required")
    decoded = unquote(value)
    if not decoded or decoded != Path(decoded).name or "/" in decoded or "\\" in decoded or decoded in {".", ".."}:
        raise HTTPException(400, detail="X-Filename must be a basename without path traversal")
    return decoded


def create_app(settings: Settings) -> FastAPI:
    service = ImportService(settings)
    app = FastAPI(title="Square Drive Connector", docs_url=None, redoc_url=None)

    def authenticated(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer ") or not __import__("secrets").compare_digest(authorization[7:], settings.upload_token):
            raise HTTPException(401, detail="Unauthorized", headers={"WWW-Authenticate": "Bearer"})

    @app.on_event("startup")
    def recover_publication(): publish_pending(service.db, settings.drive_output_dir, settings.local_data_dir)

    @app.get("/api/status")
    def status(authorization: str | None = Header(default=None)):
        authenticated(authorization)
        result = service.db.status()
        result.update({"ok": True, "drive_output_configured": settings.drive_output_dir is not None, "drive_status": "pending" if result["pending_publications"] else "written_to_sync_folder"})
        return result

    @app.post("/api/square/upload")
    async def upload(request: Request, authorization: str | None = Header(default=None), x_filename: str | None = Header(default=None)):
        authenticated(authorization)
        filename = _filename(x_filename)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in {"text/csv", "application/octet-stream"}: raise HTTPException(415, detail="Content-Type must be text/csv (or compatible application/octet-stream)")
        try:
            length = int(request.headers.get("content-length", "0"))
            if length > settings.max_upload_bytes: raise HTTPException(413, detail="Upload exceeds configured size limit")
        except ValueError: pass
        fd, temporary = tempfile.mkstemp(prefix="upload-", suffix=".csv", dir=settings.local_data_dir / "tmp")
        total = 0
        try:
            with os.fdopen(fd, "wb") as f:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > settings.max_upload_bytes:
                        raise HTTPException(413, detail="Upload exceeds configured size limit")
                    f.write(chunk)
                f.flush(); os.fsync(f.fileno())
            response = service.import_temp(Path(temporary), filename)
            if content_type == "application/octet-stream" and response["status"] == "rejected":
                return JSONResponse(response, status_code=422)
            return JSONResponse(response, status_code=200 if response["ok"] else 422)
        except HTTPException: raise
        finally:
            Path(temporary).unlink(missing_ok=True)
    return app
