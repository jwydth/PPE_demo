from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.routers.testing import storage_health
from app.storage import StorageConnectionError


def test_storage_health_returns_bucket():
    storage = Mock()
    storage.ensure_ready.return_value = "evidence"

    assert storage_health(storage) == {
        "storage": "connected",
        "bucket": "evidence",
    }


def test_storage_health_returns_503_for_storage_failure():
    storage = Mock()
    storage.ensure_ready.side_effect = StorageConnectionError(
        "MinIO unavailable."
    )

    with pytest.raises(HTTPException) as exc_info:
        storage_health(storage)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "MinIO unavailable."
