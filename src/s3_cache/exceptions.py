

class S3CacheError(Exception):
    """Base exception for all S3 cache manager errors."""

    def __init__(self, message: str, key: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.key = key


class S3ConnectionError(S3CacheError):
    """Raised when connecting or communicating with S3 fails due to network or endpoint issues."""


class S3TimeoutError(S3CacheError):
    """Raised when an operation times out."""


class S3SerializationError(S3CacheError):
    """Raised when value encoding or decoding fails."""


class S3BucketNotFoundError(S3CacheError):
    """Raised when the specified bucket does not exist."""
