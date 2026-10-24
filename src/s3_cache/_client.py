from typing import Any, Protocol, runtime_checkable

import aiobotocore.session  # type: ignore[import-untyped]
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from s3_cache.config import S3CacheConfig
from s3_cache.exceptions import (
    S3BucketNotFoundError,
    S3ConnectionError,
    S3TimeoutError,
)


@runtime_checkable
class AsyncStreamingBody(Protocol):
    """Protocol for S3 get_object response Body stream."""

    async def read(self) -> bytes:
        ...

    async def close(self) -> None:
        ...


@runtime_checkable
class S3ClientProtocol(Protocol):
    """Structural protocol for S3 operations required by S3CacheManager."""

    async def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        Metadata: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        ...

    async def get_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def head_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def delete_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def delete_objects(
        self, Bucket: str, Delete: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        ...

    async def list_objects_v2(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def head_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def create_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def delete_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def list_multipart_uploads(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        ...

    async def abort_multipart_upload(
        self, Bucket: str, Key: str, UploadId: str, **kwargs: Any
    ) -> dict[str, Any]:
        ...

    async def close(self) -> None:
        ...


def translate_s3_error(exc: Exception, key: str | None = None) -> Exception:
    """Translate botocore / aiobotocore exceptions into S3CacheError hierarchy."""
    if isinstance(exc, ConnectTimeoutError | ReadTimeoutError):
        return S3TimeoutError(f"S3 operation timed out: {exc}", key=key)
    if isinstance(exc, EndpointConnectionError):
        return S3ConnectionError(f"Failed to connect to S3 endpoint: {exc}", key=key)
    if isinstance(exc, ClientError):
        error_code = exc.response.get("Error", {}).get("Code", "")
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if error_code in ("NoSuchBucket", "404") or (
            status_code == 404 and "head_bucket" in str(exc)
        ):
            return S3BucketNotFoundError(f"S3 bucket not found: {exc}", key=key)
        if error_code in ("RequestTimeout", "408"):
            return S3TimeoutError(f"S3 request timeout: {exc}", key=key)
        return S3ConnectionError(f"S3 ClientError ({error_code}): {exc}", key=key)
    return exc


class AiobotocoreClientWrapper:
    """Wrapper over aiobotocore client providing error translation & standard protocol."""

    def __init__(self, raw_client: Any) -> None:
        self._raw_client = raw_client

    async def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        Metadata: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        try:
            params: dict[str, Any] = {"Bucket": Bucket, "Key": Key, "Body": Body}
            if Metadata is not None:
                params["Metadata"] = Metadata
            params.update(kwargs)
            res = await self._raw_client.put_object(**params)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e, key=Key) from e

    async def get_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.get_object(Bucket=Bucket, Key=Key, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e, key=Key) from e

    async def head_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.head_object(Bucket=Bucket, Key=Key, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e, key=Key) from e

    async def delete_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.delete_object(Bucket=Bucket, Key=Key, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e, key=Key) from e

    async def delete_objects(
        self, Bucket: str, Delete: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        try:
            res = await self._raw_client.delete_objects(Bucket=Bucket, Delete=Delete, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def list_objects_v2(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.list_objects_v2(Bucket=Bucket, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def head_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.head_bucket(Bucket=Bucket, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def create_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.create_bucket(Bucket=Bucket, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def delete_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.delete_bucket(Bucket=Bucket, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def list_multipart_uploads(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        try:
            res = await self._raw_client.list_multipart_uploads(Bucket=Bucket, **kwargs)
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e) from e

    async def abort_multipart_upload(
        self, Bucket: str, Key: str, UploadId: str, **kwargs: Any
    ) -> dict[str, Any]:
        try:
            res = await self._raw_client.abort_multipart_upload(
                Bucket=Bucket, Key=Key, UploadId=UploadId, **kwargs
            )
            return res  # type: ignore[no-any-return]
        except Exception as e:
            raise translate_s3_error(e, key=Key) from e

    async def close(self) -> None:
        try:
            if hasattr(self._raw_client, "__aexit__"):
                await self._raw_client.__aexit__(None, None, None)
            elif hasattr(self._raw_client, "close"):
                await self._raw_client.close()
        except Exception as e:
            raise translate_s3_error(e) from e


async def create_aiobotocore_client(config: S3CacheConfig) -> AiobotocoreClientWrapper:
    """Factory function to create an AiobotocoreClientWrapper using S3CacheConfig."""
    session = aiobotocore.session.get_session()
    botocore_config = Config(
        retries={"max_attempts": 0},
        connect_timeout=config.connect_timeout,
        read_timeout=config.read_timeout,
    )
    client_ctx = session.create_client(
        "s3",
        endpoint_url=config.endpoint_url,
        aws_access_key_id=(
            config.access_key_id.get_secret_value() if config.access_key_id else None
        ),
        aws_secret_access_key=(
            config.secret_access_key.get_secret_value() if config.secret_access_key else None
        ),
        region_name=config.region,
        config=botocore_config,
    )
    raw_client = await client_ctx.__aenter__()
    return AiobotocoreClientWrapper(raw_client)
