from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from s3_cache.config import S3CacheConfig
from s3_cache.exceptions import S3BucketNotFoundError, S3ConnectionError
from s3_cache.manager import S3CacheManager, validate_key
from tests.unit.fakes import FakeClock, FakeS3Client


class Item(BaseModel):
    id: int
    name: str


@pytest.fixture
def fake_client() -> FakeS3Client:
    return FakeS3Client(default_buckets=["s3-cache", "alt-bucket"])


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock(datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC))


@pytest.fixture
def manager(fake_client: FakeS3Client, fake_clock: FakeClock) -> S3CacheManager:
    config = S3CacheConfig(S3_DEFAULT_BUCKET="s3-cache", S3_MAX_RETRIES=2)
    return S3CacheManager(
        config=config,
        client=fake_client,
        clock=fake_clock.now,
    )


def test_validate_key() -> None:
    # Valid keys
    validate_key("user:123")
    validate_key("items/category/item1")
    validate_key("a" * 1024)

    # Invalid keys
    with pytest.raises(ValueError, match="non-empty string"):
        validate_key("")

    with pytest.raises(ValueError, match="maximum allowed length"):
        validate_key("a" * 1025)

    with pytest.raises(ValueError, match="leading slash"):
        validate_key("/leading/slash")

    with pytest.raises(ValueError, match="'..' path segments"):
        validate_key("foo/../bar")

    with pytest.raises(ValueError, match="'..' path segments"):
        validate_key("..")


@pytest.mark.asyncio
async def test_manager_lifecycle_owned_client() -> None:
    config = S3CacheConfig(S3_DEFAULT_BUCKET="s3-cache")
    mgr = S3CacheManager(config=config)
    assert not mgr.is_connected

    with pytest.raises(RuntimeError, match="not connected"):
        mgr._ensure_client()

    mock_wrapper = AsyncMock()
    with patch("s3_cache.manager.create_aiobotocore_client", return_value=mock_wrapper):
        await mgr.connect()
        assert mgr.is_connected

        await mgr.close()
        assert not mgr.is_connected
        assert mock_wrapper.close.called


@pytest.mark.asyncio
async def test_get_bucket_error() -> None:
    config = S3CacheConfig(S3_DEFAULT_BUCKET="")
    mgr = S3CacheManager(config=config, client=FakeS3Client())
    with pytest.raises(ValueError, match="No bucket specified"):
        mgr._get_bucket(None)


@pytest.mark.asyncio
async def test_set_naive_datetime_clock(fake_client: FakeS3Client) -> None:

    def naive_clock() -> datetime:
        return datetime(2026, 10, 24, 12, 0, 0)

    mgr = S3CacheManager(client=fake_client, clock=naive_clock)
    meta = await mgr.set("naive_key", "val", ttl=10)
    assert meta.created_at.tzinfo == UTC


@pytest.mark.asyncio
async def test_get_entry_non_404_error_and_corrupt_metadata(
    manager: S3CacheManager, fake_client: FakeS3Client
) -> None:
    # 1. Non-404 persistent error across retries
    fake_client.inject_error("get_object", S3ConnectionError("network down"))
    fake_client.inject_error("get_object", S3ConnectionError("network down"))
    fake_client.inject_error("get_object", S3ConnectionError("network down"))
    with pytest.raises(S3ConnectionError):
        await manager.get("mykey")

    # 2. Corrupt metadata header
    fake_client.buckets["s3-cache"]["corrupt_key"] = {"Body": b"data", "Metadata": {}}
    val = await manager.get("corrupt_key")
    assert val is None


@pytest.mark.asyncio
async def test_exists_non_404_error_and_corrupt_metadata(
    manager: S3CacheManager, fake_client: FakeS3Client
) -> None:
    # 1. Non-404 persistent error
    fake_client.inject_error("head_object", S3ConnectionError("network down"))
    fake_client.inject_error("head_object", S3ConnectionError("network down"))
    fake_client.inject_error("head_object", S3ConnectionError("network down"))
    with pytest.raises(S3ConnectionError):
        await manager.exists("mykey")

    # 2. Corrupt metadata
    fake_client.buckets["s3-cache"]["corrupt_key"] = {"Body": b"data", "Metadata": {}}
    assert not await manager.exists("corrupt_key")


@pytest.mark.asyncio
async def test_invalidate_non_404_error(
    manager: S3CacheManager, fake_client: FakeS3Client
) -> None:
    fake_client.inject_error("delete_object", S3ConnectionError("network down"))
    fake_client.inject_error("delete_object", S3ConnectionError("network down"))
    fake_client.inject_error("delete_object", S3ConnectionError("network down"))
    with pytest.raises(S3ConnectionError):
        await manager.invalidate("mykey")


@pytest.mark.asyncio
async def test_cleanup_pagination_and_head_errors(
    manager: S3CacheManager, fake_clock: FakeClock, fake_client: FakeS3Client
) -> None:
    for i in range(15):
        await manager.set(f"paged/k{i:02d}", f"val{i}", ttl=10)

    fake_clock.advance(30)

    fake_client.inject_error("head_object", S3ConnectionError("head error"))

    cleaned = await manager.cleanup(prefix="paged/", concurrency=2)
    assert cleaned >= 14


@pytest.mark.asyncio
async def test_set_and_get_str(manager: S3CacheManager) -> None:
    meta = await manager.set("greeting", "hello world", ttl=60)
    assert meta.kind == "str"
    assert meta.ttl == 60

    val = await manager.get("greeting")
    assert val == "hello world"

    entry = await manager.get_entry("greeting")
    assert entry is not None
    assert entry.key == "greeting"
    assert entry.value == "hello world"
    assert entry.metadata.ttl == 60


@pytest.mark.asyncio
async def test_set_and_get_bytes(manager: S3CacheManager) -> None:
    data = b"\x00\xff\xfe\xfd"
    await manager.set("bytes_key", data)

    val = await manager.get("bytes_key")
    assert val == data


@pytest.mark.asyncio
async def test_set_and_get_pydantic_model(manager: S3CacheManager) -> None:
    item = Item(id=1, name="widget")
    await manager.set("item:1", item)

    val = await manager.get("item:1", target_type=Item)
    assert isinstance(val, Item)
    assert val.id == 1
    assert val.name == "widget"


@pytest.mark.asyncio
async def test_get_nonexistent_key(manager: S3CacheManager) -> None:
    val = await manager.get("nonexistent")
    assert val is None

    entry = await manager.get_entry("nonexistent")
    assert entry is None


@pytest.mark.asyncio
async def test_ttl_expiration_and_lazy_eviction(
    manager: S3CacheManager, fake_clock: FakeClock, fake_client: FakeS3Client
) -> None:
    await manager.set("temp_key", "temp_value", ttl=10)

    assert await manager.exists("temp_key")
    assert "temp_key" in fake_client.buckets["s3-cache"]

    fake_clock.advance(15)

    assert not await manager.exists("temp_key")

    val = await manager.get("temp_key")
    assert val is None
    assert "temp_key" not in fake_client.buckets["s3-cache"]


@pytest.mark.asyncio
async def test_exists_and_invalidate(manager: S3CacheManager) -> None:
    await manager.set("k1", "v1")
    assert await manager.exists("k1")

    await manager.invalidate("k1")
    assert not await manager.exists("k1")

    await manager.invalidate("k1")


@pytest.mark.asyncio
async def test_custom_bucket_override(manager: S3CacheManager) -> None:
    await manager.set("alt_key", "alt_val", bucket="alt-bucket")
    val = await manager.get("alt_key", bucket="alt-bucket")
    assert val == "alt_val"

    assert await manager.get("alt_key") is None


@pytest.mark.asyncio
async def test_nonexistent_bucket_raises(manager: S3CacheManager) -> None:
    with pytest.raises(S3BucketNotFoundError):
        await manager.set("key", "val", bucket="nonexistent-bucket")
