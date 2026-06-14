class ServiceError(RuntimeError):
    """Base error for service-layer failures."""


class ServiceValidationError(ServiceError):
    """Raised when service input violates a business-facing contract."""


class ServiceNotFoundError(ServiceError):
    """Raised when a requested domain object does not exist."""
