from datetime import UTC, datetime, timedelta

import pytest

from s3_cache.models import CacheEntry, CacheMetadata


def test_cache_metadata_expiration() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    expires = now + timedelta(seconds=60)

    meta = CacheMetadata(
        created_at=now,
        expires_at=expires,
        ttl=60,
        kind="str",
        checksum="abc123hash",
    )

    assert not meta.is_expired(now)
    assert not meta.is_expired(now + timedelta(seconds=30))
    assert meta.is_expired(now + timedelta(seconds=60))
    assert meta.is_expired(now + timedelta(seconds=120))


def test_cache_metadata_naive_expiration() -> None:
    now_naive = datetime(2026, 10, 24, 12, 0, 0)
    expires_naive = now_naive + timedelta(seconds=60)

    meta = CacheMetadata(
        created_at=now_naive,
        expires_at=expires_naive,
        ttl=60,
        kind="str",
        checksum="abc123hash",
    )

    now_utc = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    assert not meta.is_expired(now_utc)
    assert meta.is_expired(now_utc + timedelta(seconds=120))


def test_cache_metadata_no_ttl() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    meta = CacheMetadata(
        created_at=now,
        expires_at=None,
        ttl=None,
        kind="bytes",
        checksum="hash",
    )
    assert not meta.is_expired(now)
    assert not meta.is_expired(now + timedelta(days=365))


def test_cache_metadata_to_and_from_s3() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    expires = now + timedelta(seconds=300)

    meta = CacheMetadata(
        created_at=now,
        expires_at=expires,
        ttl=300,
        kind="json",
        checksum="sha256hex",
        version="1",
    )

    s3_dict = meta.to_s3_metadata()
    assert s3_dict["s3c-created-at"] == now.isoformat()
    assert s3_dict["s3c-expires-at"] == expires.isoformat()
    assert s3_dict["s3c-ttl"] == "300"
    assert s3_dict["s3c-kind"] == "json"
    assert s3_dict["s3c-checksum"] == "sha256hex"
    assert s3_dict["s3c-version"] == "1"

    reconstructed = CacheMetadata.from_s3_head(s3_dict)
    assert reconstructed.created_at == now
    assert reconstructed.expires_at == expires
    assert reconstructed.ttl == 300
    assert reconstructed.kind == "json"
    assert reconstructed.checksum == "sha256hex"
    assert reconstructed.version == "1"


def test_cache_metadata_invalid_kind_fallback() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    s3_dict = {
        "s3c-created-at": now.isoformat(),
        "s3c-kind": "unknown_kind",
    }
    meta = CacheMetadata.from_s3_head(s3_dict)
    assert meta.kind == "bytes"


def test_cache_metadata_missing_created_at() -> None:
    with pytest.raises(ValueError, match="Missing s3c-created-at"):
        CacheMetadata.from_s3_head({})


def test_cache_entry_model() -> None:
    now = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
    meta = CacheMetadata(
        created_at=now,
        kind="str",
        checksum="hash",
    )
    entry = CacheEntry(key="my-key", value="hello", metadata=meta)
    assert entry.key == "my-key"
    assert entry.value == "hello"
    assert entry.metadata.kind == "str"
