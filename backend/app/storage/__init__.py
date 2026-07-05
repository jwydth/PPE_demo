class StorageError(RuntimeError):
    """Base error for object-storage operations."""


class StorageConfigurationError(StorageError):
    """Raised when MinIO configuration is missing or invalid."""


class StorageConnectionError(StorageError):
    """Raised when MinIO cannot be reached or initialized."""


class EvidenceStorageError(StorageError):
    """Raised when an evidence-object operation fails."""
