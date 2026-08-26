import mimetypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
from weakref import WeakKeyDictionary

from minio import Minio
from minio.error import S3Error

from app.storage import EvidenceStorageError, StorageConfigurationError
from app.storage.minio_client import (
    ensure_bucket_exists,
    get_bucket_name,
    get_minio_client,
)

_SNAPSHOT_EXTENSIONS = {".jpg", ".jpeg"}
_REPORT_EXTENSIONS = {".pdf"}


@dataclass(frozen=True)
class StorageObject:
    object_key: str
    object_url: str
    bucket_name: str


# Buckets already confirmed to exist, per Minio client. Weak-keyed so a client
# built for a test doesn't keep its entry alive after the test drops it, and so
# a recycled id() can never be mistaken for a previously-checked client.
_READY_BUCKETS: "WeakKeyDictionary[Minio, set[str]]" = WeakKeyDictionary()


class EvidenceStorage:
    def __init__(
        self,
        client: Minio | None = None,
        bucket_name: str | None = None,
    ) -> None:
        self.client = client or get_minio_client()
        self.bucket_name = bucket_name or get_bucket_name()

    def ensure_ready(self) -> str:
        """Confirm the bucket exists, at most once per (client, bucket).

        This is a readiness check, but it used to run on *every* call — and
        get_object_url() calls it per object, so building the incident feed
        fired one bucket_exists() round-trip per row: ~1.3ms x 810 rows =
        ~1.1s of pure network wait on every analytics request, which is what
        made selecting a zone feel slow.

        A bucket that exists does not stop existing under us, so the result is
        memoized process-wide (keyed by endpoint + bucket, so tests and any
        second bucket still get their own check). Uploads still surface a
        genuine outage: fput_object fails on its own if MinIO is unreachable.
        """
        checked = _READY_BUCKETS.setdefault(self.client, set())
        if self.bucket_name not in checked:
            ensure_bucket_exists(self.client, self.bucket_name)
            checked.add(self.bucket_name)
        return self.bucket_name

    def upload_ppe_snapshot(
        self,
        local_file_path: str | Path,
    ) -> StorageObject:
        path = _validate_file(local_file_path, _SNAPSHOT_EXTENSIONS)
        return self._upload_file(
            path,
            self.build_ppe_object_key(),
        )

    def upload_zone_snapshot(
        self,
        local_file_path: str | Path,
    ) -> StorageObject:
        path = _validate_file(local_file_path, _SNAPSHOT_EXTENSIONS)
        return self._upload_file(
            path,
            self.build_zone_object_key(),
        )

    def upload_behavior_snapshot(
        self,
        local_file_path: str | Path,
    ) -> StorageObject:
        path = _validate_file(local_file_path, _SNAPSHOT_EXTENSIONS)
        return self._upload_file(
            path,
            self.build_behavior_object_key(),
        )

    def upload_report(
        self,
        local_file_path: str | Path,
    ) -> StorageObject:
        path = _validate_file(local_file_path, _REPORT_EXTENSIONS)
        return self._upload_file(
            path,
            self.build_report_object_key(),
        )

    def upload_snapshot(
        self,
        local_file_path: str | Path,
    ) -> StorageObject:
        # Deprecated: use upload_ppe_snapshot() for typed evidence storage.
        return self.upload_ppe_snapshot(local_file_path)

    def build_ppe_object_key(
        self,
        *,
        occurred_at: datetime | None = None,
        object_id: UUID | None = None,
    ) -> str:
        timestamp = _as_utc(occurred_at)
        identifier = object_id or uuid4()
        return f"ppe-violations/{timestamp:%Y/%m/%d}/{identifier.hex}.jpg"

    def build_zone_object_key(
        self,
        *,
        occurred_at: datetime | None = None,
        object_id: UUID | None = None,
    ) -> str:
        timestamp = _as_utc(occurred_at)
        identifier = object_id or uuid4()
        return f"zone-violations/{timestamp:%Y/%m/%d}/{identifier.hex}.jpg"

    def build_behavior_object_key(
        self,
        *,
        occurred_at: datetime | None = None,
        object_id: UUID | None = None,
    ) -> str:
        timestamp = _as_utc(occurred_at)
        identifier = object_id or uuid4()
        return f"behavior-incidents/{timestamp:%Y/%m/%d}/{identifier.hex}.jpg"

    def build_report_object_key(
        self,
        *,
        created_at: datetime | None = None,
        object_id: UUID | None = None,
    ) -> str:
        timestamp = _as_utc(created_at)
        identifier = object_id or uuid4()
        return f"reports/{timestamp:%Y/%m}/{identifier.hex}.pdf"

    def get_object_url(self, object_key: str) -> str:
        normalized_key = _require_object_key(object_key)
        self.ensure_ready()
        return self._presigned_url(normalized_key)

    def object_exists(self, object_key: str) -> bool:
        normalized_key = _require_object_key(object_key)
        self.ensure_ready()
        try:
            self.client.stat_object(self.bucket_name, normalized_key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NotFound"}:
                return False
            raise EvidenceStorageError(
                "Could not check evidence object."
            ) from exc
        except Exception as exc:
            raise EvidenceStorageError(
                "Could not check evidence object."
            ) from exc

    def delete_object(self, object_key: str) -> None:
        normalized_key = _require_object_key(object_key)
        self.ensure_ready()
        try:
            self.client.remove_object(self.bucket_name, normalized_key)
        except Exception as exc:
            raise EvidenceStorageError(
                "Could not delete evidence object."
            ) from exc

    def _upload_file(
        self,
        path: Path,
        object_key: str,
    ) -> StorageObject:
        self.ensure_ready()
        content_type = mimetypes.guess_type(path.name)[0] or (
            "application/octet-stream"
        )
        try:
            self.client.fput_object(
                self.bucket_name,
                object_key,
                str(path),
                content_type=content_type,
            )
            object_url = self._presigned_url(object_key)
        except Exception as exc:
            raise EvidenceStorageError("Could not upload evidence file.") from exc

        return StorageObject(
            object_key=object_key,
            object_url=object_url,
            bucket_name=self.bucket_name,
        )

    def _presigned_url(self, object_key: str) -> str:
        try:
            return self.client.presigned_get_object(
                self.bucket_name,
                object_key,
                expires=timedelta(hours=1),
            )
        except Exception as exc:
            raise EvidenceStorageError(
                "Could not generate evidence object URL."
            ) from exc


def get_evidence_storage() -> EvidenceStorage:
    return EvidenceStorage()


def _validate_file(
    local_file_path: str | Path,
    supported_extensions: set[str],
) -> Path:
    path = Path(local_file_path)
    if not path.is_file():
        raise EvidenceStorageError(f"Evidence file does not exist: {path}")
    if path.suffix.lower() not in supported_extensions:
        supported = ", ".join(sorted(supported_extensions))
        raise EvidenceStorageError(
            f"Unsupported evidence file extension '{path.suffix}'. "
            f"Supported extensions: {supported}."
        )
    return path


def _as_utc(value: datetime | None) -> datetime:
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _require_object_key(object_key: str) -> str:
    normalized = object_key.strip().lstrip("/")
    if not normalized:
        raise StorageConfigurationError("object_key must not be empty.")
    return normalized
