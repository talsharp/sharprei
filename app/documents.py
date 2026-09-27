"""Shared file-attachment handling for deals and rental properties."""

import mimetypes
import uuid
from pathlib import Path
from typing import List, Tuple

from fastapi import Response, UploadFile
from fastapi.responses import FileResponse

from app.database import UPLOAD_ROOT

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILE_MB = MAX_FILE_BYTES // (1024 * 1024)
# Shown in the browser tab instead of downloaded; everything else downloads,
# so an uploaded HTML/SVG file can never run as a page on this site.
INLINE_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain"}


def folder_for(kind: str, owner_id: int) -> Path:
    """kind is "deals" or "properties" - also the folder name under uploads/."""
    return UPLOAD_ROOT / kind / str(owner_id)


async def save_uploads(folder: Path, uploads: List[UploadFile]) -> Tuple[List[dict], List[str]]:
    """Writes each upload under a random name; returns (saved, too_big_names)."""
    saved, too_big = [], []
    folder.mkdir(parents=True, exist_ok=True)
    for upload in uploads:
        if not upload.filename:
            continue
        content = await upload.read()
        if len(content) > MAX_FILE_BYTES:
            too_big.append(upload.filename)
            continue
        original = Path(upload.filename).name[:255]
        stored = uuid.uuid4().hex + Path(original).suffix.lower()[:10]
        (folder / stored).write_bytes(content)
        saved.append({"original_name": original, "stored_name": stored, "size_bytes": len(content)})
    return saved, too_big


def serve(folder: Path, stored_name: str, original_name: str):
    path = folder / stored_name
    if not path.is_file():
        return Response("File is missing on disk", status_code=404)
    media_type = mimetypes.guess_type(original_name)[0] or "application/octet-stream"
    inline = media_type in INLINE_TYPES
    return FileResponse(
        path,
        filename=original_name,
        media_type=media_type if inline else "application/octet-stream",
        content_disposition_type="inline" if inline else "attachment",
        headers={"X-Content-Type-Options": "nosniff"},
    )


def too_big_suffix(too_big: List[str]) -> str:
    return "?too_big=" + ",".join(too_big)[:300] if too_big else ""
