import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from botocore.exceptions import ClientError

from s3_cache._client import S3ClientProtocol, create_aiobotocore_client
from s3_cache.config import S3CacheConfig
from s3_cache.exceptions import S3CacheError
from s3_cache.models import CacheEntry, CacheMetadata
from s3_cache.retry import retry_async
from s3_cache.serialization import decode_value, encode_value

T = TypeVar("T")


def validate_key(key: str) -> None:
    """
    Validate key according to rules:
    - Non-empty string
    - <= 1024 bytes UTF-8
    - No leading '/'
    - No '..' path segments
    """
    if not key or not isinstance(key, str):
        raise ValueError("Cache key must be a non-empty string.")

    encoded = key.encode("utf-8")
    if len(encoded) > 1024:
        msg = f"Cache key exceeds maximum allowed length of 1024 bytes ({len(encoded)} bytes)."
        raise ValueError(msg)

    if key.startswith("/"):
        raise ValueError("Cache key must not start with a leading slash ('/').")

    segments = key.split("/")
    if ".." in segments:
        raise ValueError("Cache key must not contain '..' path segments.")


class S3CacheManager:
    """Async S3 Cache Manager with single-object per key and metadata in object headers."""

    def __init__(
        self,
        config: S3CacheConfig | None = None,
        client: S3ClientProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep_fn: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.config = config or S3CacheConfig()
        self._external_client = client is not None
        self._client: S3ClientProtocol | None = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep_fn = sleep_fn

    @property
    def is_connected(self) -> bool:
        """Return True if S3 client is connected and active."""
        return self._client is not None

    async def connect(self) -> None:
        """Connect and initialize S3 client if not already connected."""
        if self._client is None:
            self._client = await create_aiobotocore_client(self.config)

    async def close(self) -> None:
        """Close S3 client session if owned by manager."""
        if self._client is not None and not self._external_client:
            await self._client.close()
            self._client = None

    async def __aenter__(self) -> "S3CacheManager":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        await self.close()

    def _ensure_client(self) -> S3ClientProtocol:
        if self._client is None:
            raise RuntimeError(
                "S3CacheManager is not connected. Call connect() or use 'async with'."
            )
        return self._client

    def _get_bucket(self, bucket: str | None) -> str:
        target = bucket or self.config.default_bucket
        if not target:
            raise ValueError("No bucket specified and no default_bucket configured.")
        return target

    async def _execute_with_retry(self, func: Callable[[], Awaitable[T]]) -> T:
        return await retry_async(
            func,
            max_retries=self.config.max_retries,
            sleep_fn=self._sleep_fn,
        )

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        bucket: str | None = None,
    ) -> CacheMetadata:
        """Store a value in cache under key with optional TTL."""
        validate_key(key)
        client = self._ensure_client()
        target_bucket = self._get_bucket(bucket)

        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        effective_ttl = ttl if ttl is not None else self.config.default_ttl
        expires_at: datetime | None = None
        if effective_ttl is not None and effective_ttl > 0:
            expires_at = now + timedelta(seconds=effective_ttl)

        raw_bytes, kind, checksum = encode_value(value)

        metadata = CacheMetadata(
            created_at=now,
            expires_at=expires_at,
            ttl=effective_ttl,
            kind=kind,
            checksum=checksum,
        )

        s3_metadata = metadata.to_s3_metadata()

        async def _op() -> None:
            await client.put_object(
                Bucket=target_bucket,
                Key=key,
                Body=raw_bytes,
                Metadata=s3_metadata,
            )

        await self._execute_with_retry(_op)
        return metadata

    async def get_entry(
        self,
        key: str,
        target_type: type[T] | None = None,
        bucket: str | None = None,
    ) -> CacheEntry | None:
        """Retrieve full CacheEntry for key if exists and not expired."""
        validate_key(key)
        client = self._ensure_client()
        target_bucket = self._get_bucket(bucket)

        async def _op() -> dict[str, Any]:
            return await client.get_object(Bucket=target_bucket, Key=key)

        try:
            response = await self._execute_with_retry(_op)
        except (S3CacheError, ClientError) as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return None
            raise

        s3_metadata = response.get("Metadata", {})
        try:
            metadata = CacheMetadata.from_s3_head(s3_metadata)
        except Exception:
            return None

        now = self._clock()
        if metadata.is_expired(now):
            await self.invalidate(key, bucket=target_bucket)
            return None

        body_stream = response["Body"]
        raw_bytes = await body_stream.read()

        value = decode_value(
            raw_bytes,
            metadata.kind,
            expected_checksum=metadata.checksum,
            target_type=target_type,
            key=key,
        )

        return CacheEntry(key=key, value=value, metadata=metadata)

    async def get(
        self,
        key: str,
        target_type: type[T] | None = None,
        bucket: str | None = None,
    ) -> Any | None:
        """Retrieve value for key if exists and not expired."""
        entry = await self.get_entry(key=key, target_type=target_type, bucket=bucket)
        return entry.value if entry is not None else None

    async def exists(self, key: str, bucket: str | None = None) -> bool:
        """Check if non-expired key exists in cache via fast HEAD call."""
        validate_key(key)
        client = self._ensure_client()
        target_bucket = self._get_bucket(bucket)

        async def _op() -> dict[str, Any]:
            return await client.head_object(Bucket=target_bucket, Key=key)

        try:
            response = await self._execute_with_retry(_op)
        except (S3CacheError, ClientError) as exc:
            if "NoSuchKey" in str(exc) or "404" in str(exc):
                return False
            raise

        s3_metadata = response.get("Metadata", {})
        try:
            metadata = CacheMetadata.from_s3_head(s3_metadata)
        except Exception:
            return False

        now = self._clock()
        return not metadata.is_expired(now)

    async def invalidate(self, key: str, bucket: str | None = None) -> None:
        """Delete key from cache (idempotent)."""
        validate_key(key)
        client = self._ensure_client()
        target_bucket = self._get_bucket(bucket)

        async def _op() -> None:
            try:
                await client.delete_object(Bucket=target_bucket, Key=key)
            except (S3CacheError, ClientError) as exc:
                if "NoSuchKey" in str(exc) or "404" in str(exc):
                    return
                raise

        await self._execute_with_retry(_op)

    async def cleanup(
        self,
        bucket: str | None = None,
        prefix: str = "",
        concurrency: int = 16,
    ) -> int:
        """Garbage collect expired items in bucket matching prefix."""
        client = self._ensure_client()
        target_bucket = self._get_bucket(bucket)

        now = self._clock()
        semaphore = asyncio.Semaphore(concurrency)
        expired_keys: list[str] = []

        continuation_token: str | None = None

        while True:
            list_kwargs: dict[str, Any] = {"Bucket": target_bucket, "Prefix": prefix}
            if continuation_token:
                list_kwargs["ContinuationToken"] = continuation_token

            async def _list_op(kwargs: dict[str, Any] = list_kwargs) -> dict[str, Any]:
                return await client.list_objects_v2(**kwargs)

            resp = await self._execute_with_retry(_list_op)
            contents = resp.get("Contents", [])

            async def _check_key(k: str) -> str | None:
                async with semaphore:
                    try:
                        async def _head_op(item_key: str = k) -> dict[str, Any]:
                            return await client.head_object(Bucket=target_bucket, Key=item_key)

                        head_resp = await self._execute_with_retry(_head_op)
                        s3_meta = head_resp.get("Metadata", {})
                        meta = CacheMetadata.from_s3_head(s3_meta)
                        if meta.is_expired(now):
                            return k
                    except Exception:
                        pass
                    return None

            tasks = [_check_key(item["Key"]) for item in contents if "Key" in item]
            if tasks:
                results = await asyncio.gather(*tasks)
                expired_keys.extend([k for k in results if k is not None])

            if resp.get("IsTruncated"):
                continuation_token = resp.get("NextContinuationToken")
            else:
                break

        # Batch delete in chunks of 1000
        for i in range(0, len(expired_keys), 1000):
            chunk = expired_keys[i : i + 1000]
            del_payload = {"Objects": [{"Key": k} for k in chunk]}

            async def _del_op(payload: dict[str, Any] = del_payload) -> None:
                await client.delete_objects(Bucket=target_bucket, Delete=payload)

            await self._execute_with_retry(_del_op)

        return len(expired_keys)
