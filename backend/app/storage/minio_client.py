from functools import lru_cache
from urllib.parse import urlparse

from minio import Minio

from app.core.config import settings
from app.storage import StorageConfigurationError, StorageConnectionError


def create_minio_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    secure: bool,
) -> Minio:
    normalized_endpoint = _normalize_endpoint(endpoint)
    normalized_access_key = _require_value(access_key, "MINIO_ACCESS_KEY")
    normalized_secret_key = _require_value(secret_key, "MINIO_SECRET_KEY")

    try:
        return Minio(
            normalized_endpoint,
            access_key=normalized_access_key,
            secret_key=normalized_secret_key,
            secure=secure,
        )
    except Exception as exc:
        raise StorageConfigurationError(
            "MinIO client configuration is invalid."
        ) from exc


@lru_cache
def get_minio_client() -> Minio:
    return create_minio_client(
        endpoint=settings.MINIO_ENDPOINT or "",
        access_key=settings.MINIO_ACCESS_KEY or "",
        secret_key=settings.MINIO_SECRET_KEY or "",
        secure=settings.MINIO_SECURE,
    )


def get_bucket_name() -> str:
    bucket_name = _require_value(
        settings.MINIO_BUCKET_NAME or "",
        "MINIO_BUCKET_NAME",
    )
    if len(bucket_name) < 3 or len(bucket_name) > 63:
        raise StorageConfigurationError(
            "MINIO_BUCKET_NAME must contain between 3 and 63 characters."
        )
    return bucket_name


def ensure_bucket_exists(client: Minio, bucket_name: str) -> None:
    try:
        if not client.bucket_exists(bucket_name):
            client.make_bucket(bucket_name)
    except Exception as exc:
        raise StorageConnectionError(
            f"Could not connect to MinIO bucket '{bucket_name}'."
        ) from exc


def _normalize_endpoint(endpoint: str) -> str:
    normalized = _require_value(endpoint, "MINIO_ENDPOINT").rstrip("/")
    if "://" not in normalized:
        return normalized

    parsed = urlparse(normalized)
    if not parsed.netloc or parsed.path not in {"", "/"}:
        raise StorageConfigurationError(
            "MINIO_ENDPOINT must contain only a host and optional port."
        )
    return parsed.netloc


def _require_value(value: str, setting_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise StorageConfigurationError(f"{setting_name} is not configured.")
    return normalized
