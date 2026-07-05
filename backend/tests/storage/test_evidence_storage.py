from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
from uuid import UUID

import pytest

from app.storage import EvidenceStorageError
from app.storage.evidence_storage import EvidenceStorage, StorageObject

FIXED_TIME = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
FIXED_UUID = UUID("12345678-1234-5678-1234-567812345678")


def test_build_typed_object_keys():
    storage = EvidenceStorage(client=Mock(), bucket_name="evidence")

    assert storage.build_ppe_object_key(
        occurred_at=FIXED_TIME,
        object_id=FIXED_UUID,
    ) == (
        "ppe-violations/2026/06/05/"
        "12345678123456781234567812345678.jpg"
    )
    assert storage.build_zone_object_key(
        occurred_at=FIXED_TIME,
        object_id=FIXED_UUID,
    ) == (
        "zone-violations/2026/06/05/"
        "12345678123456781234567812345678.jpg"
    )
    assert storage.build_report_object_key(
        created_at=FIXED_TIME,
        object_id=FIXED_UUID,
    ) == (
        "reports/2026/06/"
        "12345678123456781234567812345678.pdf"
    )


@pytest.mark.parametrize(
    ("method_name", "key_builder", "expected_prefix"),
    [
        (
            "upload_ppe_snapshot",
            "build_ppe_object_key",
            "ppe-violations/",
        ),
        (
            "upload_zone_snapshot",
            "build_zone_object_key",
            "zone-violations/",
        ),
    ],
)
def test_snapshot_upload_flow(
    tmp_path,
    monkeypatch,
    method_name,
    key_builder,
    expected_prefix,
):
    snapshot = tmp_path / "worker.jpg"
    snapshot.write_bytes(b"image-data")
    object_key = f"{expected_prefix}2026/06/05/{FIXED_UUID.hex}.jpg"
    client = Mock()
    client.bucket_exists.return_value = True
    client.presigned_get_object.return_value = (
        f"http://localhost:9000/evidence/{object_key}?signature=test"
    )
    storage = EvidenceStorage(client=client, bucket_name="evidence")
    monkeypatch.setattr(storage, key_builder, lambda: object_key)

    result = getattr(storage, method_name)(snapshot)

    assert result == StorageObject(
        object_key=object_key,
        object_url=(
            f"http://localhost:9000/evidence/{object_key}?signature=test"
        ),
        bucket_name="evidence",
    )
    client.bucket_exists.assert_called_once_with("evidence")
    client.fput_object.assert_called_once_with(
        "evidence",
        object_key,
        str(snapshot),
        content_type="image/jpeg",
    )


def test_report_upload_flow(tmp_path, monkeypatch):
    report = tmp_path / "monthly-report.pdf"
    report.write_bytes(b"%PDF")
    object_key = f"reports/2026/06/{FIXED_UUID.hex}.pdf"
    client = Mock()
    client.bucket_exists.return_value = True
    client.presigned_get_object.return_value = (
        f"http://localhost:9000/evidence/{object_key}?signature=test"
    )
    storage = EvidenceStorage(client=client, bucket_name="evidence")
    monkeypatch.setattr(
        storage,
        "build_report_object_key",
        lambda: object_key,
    )

    result = storage.upload_report(report)

    assert result.object_key == object_key
    assert result.bucket_name == "evidence"
    client.fput_object.assert_called_once_with(
        "evidence",
        object_key,
        str(report),
        content_type="application/pdf",
    )


def test_upload_snapshot_delegates_to_ppe_upload(monkeypatch, tmp_path):
    snapshot = tmp_path / "worker.jpg"
    snapshot.write_bytes(b"image-data")
    expected = StorageObject(
        object_key="ppe-violations/test.jpg",
        object_url="http://localhost/test.jpg",
        bucket_name="evidence",
    )
    storage = EvidenceStorage(client=Mock(), bucket_name="evidence")
    upload_ppe_snapshot = Mock(return_value=expected)
    monkeypatch.setattr(
        storage,
        "upload_ppe_snapshot",
        upload_ppe_snapshot,
    )

    assert storage.upload_snapshot(snapshot) == expected
    upload_ppe_snapshot.assert_called_once_with(snapshot)


def test_upload_rejects_missing_and_unsupported_files(tmp_path):
    storage = EvidenceStorage(client=Mock(), bucket_name="evidence")

    with pytest.raises(EvidenceStorageError, match="does not exist"):
        storage.upload_ppe_snapshot(tmp_path / "missing.jpg")

    unsupported_snapshot = tmp_path / "snapshot.png"
    unsupported_snapshot.write_bytes(b"image-data")
    with pytest.raises(EvidenceStorageError, match="Unsupported"):
        storage.upload_zone_snapshot(unsupported_snapshot)

    unsupported_report = tmp_path / "report.txt"
    unsupported_report.write_text("report")
    with pytest.raises(EvidenceStorageError, match="Unsupported"):
        storage.upload_report(unsupported_report)


def test_get_object_url_returns_presigned_url():
    client = Mock()
    client.bucket_exists.return_value = True
    client.presigned_get_object.return_value = (
        "http://localhost:9000/evidence/ppe-violations/test.jpg?signature=test"
    )
    storage = EvidenceStorage(client=client, bucket_name="evidence")

    result = storage.get_object_url("ppe-violations/test.jpg")

    assert result == (
        "http://localhost:9000/evidence/"
        "ppe-violations/test.jpg?signature=test"
    )
    client.presigned_get_object.assert_called_once_with(
        "evidence",
        "ppe-violations/test.jpg",
        expires=timedelta(hours=1),
    )


def test_object_exists_and_delete_object():
    client = Mock()
    client.bucket_exists.return_value = True
    storage = EvidenceStorage(client=client, bucket_name="evidence")

    assert storage.object_exists("ppe-violations/test.jpg") is True
    storage.delete_object("ppe-violations/test.jpg")

    client.stat_object.assert_called_once_with(
        "evidence",
        "ppe-violations/test.jpg",
    )
    client.remove_object.assert_called_once_with(
        "evidence",
        "ppe-violations/test.jpg",
    )
