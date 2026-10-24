import os
from collections.abc import AsyncGenerator
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
    )


@pytest.fixture(scope="function")
async def isolated_bucket(e2e_config: S3CacheConfig) -> AsyncGenerator[str, None]:
    """
    Session / Function fixture creating a dynamic isolated S3 bucket for testing,
    and strictly tearing it down (deleting objects, aborting multipart uploads, deleting bucket,
    verifying 404 via head_bucket) in a finally block.
    """
    endpoint_url = os.getenv("S3_ENDPOINT_URL", "").strip()
    if not endpoint_url:
        pytest.skip("S3_ENDPOINT_URL environment variable is not set.")

    prefix = os.getenv("S3_TEST_BUCKET_PREFIX", "test-jules")
    bucket_name = f"{prefix}-{uuid4().hex[:8]}"

    client = await create_aiobotocore_client(e2e_config)
    await client.create_bucket(Bucket=bucket_name)

    try:
        yield bucket_name
    finally:
        try:
            # 1. Paginate and delete all objects in chunks of 1000
            continuation_token = None
            while True:
                kwargs = {"Bucket": bucket_name}
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

            # 2. Abort any incomplete multipart uploads
            mp_resp = await client.list_multipart_uploads(Bucket=bucket_name)
            for upload in mp_resp.get("Uploads", []):
                key = upload.get("Key")
                upload_id = upload.get("UploadId")
                if key and upload_id:
                    await client.abort_multipart_upload(
                        Bucket=bucket_name, Key=key, UploadId=upload_id
                    )

            # 3. Delete bucket
            await client.delete_bucket(Bucket=bucket_name)

            # 4. Verify 404 storage leak check
            with pytest.raises(S3BucketNotFoundError):
                await client.head_bucket(Bucket=bucket_name)
        finally:
            await client.close()


@pytest.fixture(scope="function")
async def e2e_manager(
    e2e_config: S3CacheConfig, isolated_bucket: str
) -> AsyncGenerator[S3CacheManager, None]:
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
        S3_DEFAULT_BUCKET=isolated_bucket,
    )
    async with S3CacheManager(config=config) as mgr:
        yield mgr
