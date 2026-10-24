import asyncio

import pytest
from pydantic import BaseModel

from s3_cache.manager import S3CacheManager

pytestmark = pytest.mark.e2e


class UserSession(BaseModel):
    user_id: str
    roles: list[str]


@pytest.mark.asyncio
async def test_e2e_roundtrip_data_types(e2e_manager: S3CacheManager) -> None:
    # String
    await e2e_manager.set("str_key", "hello e2e")
    assert await e2e_manager.get("str_key") == "hello e2e"

    # Bytes
    raw = b"\x01\x02\x03\x04\x05"
    await e2e_manager.set("bytes_key", raw)
    assert await e2e_manager.get("bytes_key") == raw

    # Dict
    d = {"user": "alice", "active": True, "count": 10}
    await e2e_manager.set("dict_key", d)
    assert await e2e_manager.get("dict_key") == d

    # Pydantic BaseModel
    session = UserSession(user_id="u123", roles=["admin", "editor"])
    await e2e_manager.set("model_key", session)

    res_model = await e2e_manager.get("model_key", target_type=UserSession)
    assert isinstance(res_model, UserSession)
    assert res_model.user_id == "u123"
    assert res_model.roles == ["admin", "editor"]


@pytest.mark.asyncio
async def test_e2e_exists_and_invalidate(e2e_manager: S3CacheManager) -> None:
    await e2e_manager.set("temp", "value")
    assert await e2e_manager.exists("temp") is True

    await e2e_manager.invalidate("temp")
    assert await e2e_manager.exists("temp") is False
    assert await e2e_manager.get("temp") is None


@pytest.mark.asyncio
async def test_e2e_ttl_expiry_and_cleanup(e2e_manager: S3CacheManager) -> None:
    # Set key with 1 sec TTL
    await e2e_manager.set("ttl_key", "expiring_val", ttl=1)
    assert await e2e_manager.exists("ttl_key") is True

    # Sleep 1.2s to expire
    await asyncio.sleep(1.2)

    assert await e2e_manager.exists("ttl_key") is False
    assert await e2e_manager.get("ttl_key") is None

    # Test cleanup
    await e2e_manager.set("cleanup/k1", "val1", ttl=1)
    await e2e_manager.set("cleanup/k2", "val2", ttl=100)
    await asyncio.sleep(1.2)

    cleaned = await e2e_manager.cleanup(prefix="cleanup/")
    assert cleaned == 1
    assert await e2e_manager.exists("cleanup/k1") is False
    assert await e2e_manager.exists("cleanup/k2") is True
