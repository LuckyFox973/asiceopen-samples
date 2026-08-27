"""Google Drive client for backup archives.

Uses the ``drive.file`` scope, which grants access **only to files this
application itself creates**.  Nothing else in the Drive is visible to it —
an important property when the same Google account holds client documents.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.logging import get_logger

log = get_logger(__name__)

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_MIME = "application/vnd.google-apps.folder"
ARCHIVE_MIME = "application/octet-stream"
# Resumable uploads survive a dropped connection; worth it above a few MB.
RESUMABLE_THRESHOLD = 5 * 1024 * 1024

drive_retry = retry(
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(4),
    reraise=True,
)


@dataclass(slots=True)
class DriveFile:
    id: str
    name: str
    size_bytes: int
    created_at: datetime | None

    @property
    def is_archive(self) -> bool:
        return self.name.endswith(".eaabk")


class DriveClient:
    def __init__(self, credentials: Any) -> None:
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    @drive_retry
    def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        """Find or create a folder, returning its id.

        Only folders this application created are visible under
        ``drive.file``, so this will not collide with the user's own folders.
        """
        query = f"mimeType = '{FOLDER_MIME}' and name = '{name}' and trashed = false"
        if parent_id:
            query += f" and '{parent_id}' in parents"
        response = (
            self._service.files()
            .list(q=query, spaces="drive", fields="files(id, name)", pageSize=10)
            .execute()
        )
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        metadata: dict[str, Any] = {"name": name, "mimeType": FOLDER_MIME}
        if parent_id:
            metadata["parents"] = [parent_id]
        created = self._service.files().create(body=metadata, fields="id").execute()
        log.info("drive.folder_created", name=name, id=created["id"])
        return created["id"]

    @drive_retry
    def upload(self, path: Path, folder_id: str, name: str | None = None) -> DriveFile:
        size = path.stat().st_size
        media = MediaFileUpload(
            str(path),
            mimetype=ARCHIVE_MIME,
            resumable=size > RESUMABLE_THRESHOLD,
        )
        created = (
            self._service.files()
            .create(
                body={"name": name or path.name, "parents": [folder_id]},
                media_body=media,
                fields="id, name, size, createdTime",
            )
            .execute()
        )
        return _to_drive_file(created)

    @drive_retry
    def list_folder(self, folder_id: str) -> list[DriveFile]:
        files: list[DriveFile] = []
        page_token = None
        while True:
            response = (
                self._service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    spaces="drive",
                    fields="nextPageToken, files(id, name, size, createdTime)",
                    orderBy="createdTime desc",
                    pageToken=page_token,
                    pageSize=100,
                )
                .execute()
            )
            files.extend(_to_drive_file(f) for f in response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return files

    @drive_retry
    def download(self, file_id: str, destination: Path) -> Path:
        request = self._service.files().get_media(fileId=file_id)
        with destination.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return destination

    @drive_retry
    def delete(self, file_id: str) -> None:
        self._service.files().delete(fileId=file_id).execute()

    @drive_retry
    def about(self) -> dict[str, Any]:
        """Storage quota — so a backup can refuse to start rather than fail late."""
        return self._service.about().get(fields="storageQuota, user").execute()


def _to_drive_file(payload: dict[str, Any]) -> DriveFile:
    created = payload.get("createdTime")
    parsed = None
    if created:
        try:
            parsed = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:  # pragma: no cover - Drive always sends RFC 3339
            parsed = None
    return DriveFile(
        id=payload["id"],
        name=payload.get("name", ""),
        size_bytes=int(payload.get("size") or 0),
        created_at=parsed,
    )
