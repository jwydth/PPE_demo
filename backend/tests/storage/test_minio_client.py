from unittest.mock import Mock

import pytest

from app.storage import StorageConfigurationError, StorageConnectionError
from app.storage import minio_client


def test_create_minio_client_normalizes_endpoint(monkeypatch):
    constructor = Mock()
    client = Mock()
    constructor.return_value = client
    monkeypatch.setattr(minio_client, "Minio", constructor)

    result = minio_client.create_minio_client(
        endpoint="http://localhost:9000/",
        access_key="access",
        secret_key="secret",
        secure=False,
    )

    assert result is client
    constructor.assert_called_once_with(
        "localhost:9000",
        access_key="access",
        secret_key="secret",
        secure=False,
    )


def test_create_minio_client_validates_configuration():
    with pytest.raises(
        StorageConfigurationError,
        match="MINIO_ENDPOINT is not configured",
    ):
        minio_client.create_minio_client(
            endpoint="",
            access_key="access",
            secret_key="secret",
            secure=False,
        )


def test_ensure_bucket_exists_checks_connection_without_creating():
    client = Mock()
    client.bucket_exists.return_value = True

    minio_client.ensure_bucket_exists(client, "evidence")

    client.bucket_exists.assert_called_once_with("evidence")
    client.make_bucket.assert_not_called()


def test_ensure_bucket_exists_creates_missing_bucket():
    client = Mock()
    client.bucket_exists.return_value = False

    minio_client.ensure_bucket_exists(client, "evidence")

    client.make_bucket.assert_called_once_with("evidence")


def test_ensure_bucket_exists_wraps_connection_errors():
    client = Mock()
    client.bucket_exists.side_effect = OSError("connection refused")

    with pytest.raises(StorageConnectionError, match="Could not connect"):
        minio_client.ensure_bucket_exists(client, "evidence")
