from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel

S3C_VERSION = "1"
ValueKind = Literal["bytes", "str", "json"]


class CacheMetadata(BaseModel):
    """Metadata associated with a cached object stored in S3 object headers."""

    created_at: datetime
    expires_at: datetime | None = None
    ttl: int | None = None
    kind: ValueKind
    checksum: str
    version: str = S3C_VERSION

    def is_expired(self, current_time: datetime | None = None) -> bool:
        """Check if the cache entry is expired relative to current_time (default now UTC)."""
        if self.expires_at is None:
            return False
        now = current_time or datetime.now(UTC)
        if self.expires_at.tzinfo is None:
            now = now.replace(tzinfo=None)
        return now >= self.expires_at

    def to_s3_metadata(self) -> dict[str, str]:
        """Convert metadata to S3 user metadata dictionary (without x-amz-meta- prefix)."""
        return {
            "s3c-created-at": self.created_at.isoformat(),
            "s3c-expires-at": self.expires_at.isoformat() if self.expires_at else "",
            "s3c-ttl": str(self.ttl) if self.ttl is not None else "",
            "s3c-kind": self.kind,
            "s3c-checksum": self.checksum,
            "s3c-version": self.version,
        }

    @classmethod
    def from_s3_head(cls, head_response_metadata: dict[str, str]) -> "CacheMetadata":
        """Reconstruct CacheMetadata from S3 Metadata dict (keys normalized by botocore)."""
        created_at_str = head_response_metadata.get("s3c-created-at")
        if not created_at_str:
            raise ValueError("Missing s3c-created-at metadata header")

        created_at = datetime.fromisoformat(created_at_str)

        expires_at_str = head_response_metadata.get("s3c-expires-at", "")
        expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None

        ttl_str = head_response_metadata.get("s3c-ttl", "")
        ttl = int(ttl_str) if ttl_str else None

        kind_raw = head_response_metadata.get("s3c-kind", "bytes")
        if kind_raw not in ("bytes", "str", "json"):
            kind_raw = "bytes"
        kind: ValueKind = kind_raw  # type: ignore[assignment]

        checksum = head_response_metadata.get("s3c-checksum", "")
        version = head_response_metadata.get("s3c-version", S3C_VERSION)

        return cls(
            created_at=created_at,
            expires_at=expires_at,
            ttl=ttl,
            kind=kind,
            checksum=checksum,
            version=version,
        )


class CacheEntry(BaseModel):
    """Full cache entry containing key, payload value, and metadata."""

    key: str
    value: Any
    metadata: CacheMetadata
