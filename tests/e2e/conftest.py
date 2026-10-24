import os
from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import pytest

from s3_cache._client import create_aiobotocore_client
from s3_cache.config import S3CacheConfig
from s3_cache.exceptions import S3BucketNotFoundError
from s3_cache.manager import S3CacheManager

pytestmark = pytest.mark.e2e


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Automatically skip tests in tests/e2e if S3_ENDPOINT_URL is not set or empty."""
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()
    if not endpoint_url:
        skip_e2e = pytest.mark.skip(
            reason="S3_ENDPOINT_URL environment variable is not set; skipping E2E tests."
        )
        for item in items:
            if "e2e" in str(item.fspath):
                item.add_marker(skip_e2e)


@pytest.fixture(scope="session")
def e2e_config() -> S3CacheConfig:
    return S3CacheConfig(
        S3_ENDPOINT_URL=os.getenv("S3_ENDPOINT_URL", ""),
        S3_ACCESS_KEY_ID=os.getenv("S3_ACCESS_KEY_ID", "minioadmin"),
        S3_SECRET_ACCESS_KEY=os.getenv("S3_SECRET_ACCESS_KEY", "minioadmin"),
        S3_REGION=os.getenv("S3_REGION", "us-east-1"),
        S3_DEFAULT_BUCKET=os.getenv("S3_DEFAULT_BUCKET", "s3-cache"),
    )


@pytest.fixture(scope="function")
async def isolated_bucket(
    e2e_config: S3CacheConfig,
) -> AsyncGenerator[tuple[str, str], None]:
    """
    Fixture providing an isolated bucket and key prefix for E2E testing.
    Attempts CreateBucket first; if forbidden/AccessDenied or if S3_TEST_BUCKET / S3_DEFAULT_BUCKET
    is specified, falls back to pre-existing bucket with an isolated test run key prefix.
    """
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()
    if not endpoint_url:
        pytest.skip("S3_ENDPOINT_URL environment variable is not set.")

    prefix_setting = os.getenv("S3_TEST_BUCKET_PREFIX", "test-jules")
    test_bucket_override = (
        os.getenv("S3_TEST_BUCKET", "").strip() or os.getenv("S3_DEFAULT_BUCKET", "").strip()
    )

    client = await create_aiobotocore_client(e2e_config)

    created_bucket = False
    bucket_name = f"{prefix_setting}-{uuid4().hex[:8]}"
    key_prefix = ""

    if test_bucket_override:
        bucket_name = test_bucket_override
        key_prefix = f"t-jules-{uuid4().hex[:8]}/"
    else:
        try:
            await client.create_bucket(Bucket=bucket_name)
            created_bucket = True
        except Exception:
            bucket_name = e2e_config.default_bucket or "s3-cache"
            key_prefix = f"t-jules-{uuid4().hex[:8]}/"

    try:
        yield bucket_name, key_prefix
    finally:
        try:
            continuation_token = None
            while True:
                kwargs: dict[str, Any] = {"Bucket": bucket_name}
                if key_prefix:
                    kwargs["Prefix"] = key_prefix
                if continuation_token:
                    kwargs["ContinuationToken"] = continuation_token

                resp = await client.list_objects_v2(**kwargs)
                contents = resp.get("Contents", [])

                if contents:
                    delete_objects = [{"Key": obj["Key"]} for obj in contents if "Key" in obj]
                    for i in range(0, len(delete_objects), 1000):
                        chunk = delete_objects[i : i + 1000]
                        await client.delete_objects(
                            Bucket=bucket_name, Delete={"Objects": chunk}
                        )

                if resp.get("IsTruncated"):
                    continuation_token = resp.get("NextContinuationToken")
                else:
                    break

            mp_resp = await client.list_multipart_uploads(Bucket=bucket_name)
            for upload in mp_resp.get("Uploads", []):
                key = upload.get("Key", "")
                upload_id = upload.get("UploadId")
                if upload_id and (not key_prefix or key.startswith(key_prefix)):
                    await client.abort_multipart_upload(
                        Bucket=bucket_name, Key=key, UploadId=upload_id
                    )

            if created_bucket:
                await client.delete_bucket(Bucket=bucket_name)
                with pytest.raises(S3BucketNotFoundError):
                    await client.head_bucket(Bucket=bucket_name)
        finally:
            await client.close()


@pytest.fixture(scope="function")
async def e2e_manager(
    e2e_config: S3CacheConfig, isolated_bucket: tuple[str, str]
) -> AsyncGenerator[S3CacheManager, None]:
    bucket_name, key_prefix = isolated_bucket
    config = S3CacheConfig(
        S3_ENDPOINT_URL=e2e_config.endpoint_url,
        S3_ACCESS_KEY_ID=(
            e2e_config.access_key_id.get_secret_value() if e2e_config.access_key_id else None
        ),
        S3_SECRET_ACCESS_KEY=(
            e2e_config.secret_access_key.get_secret_value()
            if e2e_config.secret_access_key
            else None
        ),
        S3_REGION=e2e_config.region,
        S3_DEFAULT_BUCKET=bucket_name,
        S3_KEY_PREFIX=key_prefix,
    )
    async with S3CacheManager(config=config) as mgr:
        yield mgr
