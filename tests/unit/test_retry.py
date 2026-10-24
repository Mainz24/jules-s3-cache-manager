import pytest
from botocore.exceptions import BotoCoreError, ClientError

from s3_cache.exceptions import S3ConnectionError, S3TimeoutError
from s3_cache.retry import is_transient_exception, retry_async


def test_is_transient_exception() -> None:
    assert is_transient_exception(S3ConnectionError("conn fail"))
    assert is_transient_exception(S3TimeoutError("timeout"))
    assert is_transient_exception(ConnectionError("socket err"))
    assert is_transient_exception(TimeoutError("timeout"))

    # BotoCoreError
    assert is_transient_exception(BotoCoreError())

    # 5xx ClientError
    err500 = ClientError(
        {"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}},
        "op",
    )
    assert is_transient_exception(err500)

    # Transient error codes in ClientError
    err_slowdown = ClientError(
        {"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 400}},
        "op",
    )
    assert is_transient_exception(err_slowdown)

    # 404 ClientError
    err404 = ClientError(
        {"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}},
        "op",
    )
    assert not is_transient_exception(err404)

    # ValueError
    assert not is_transient_exception(ValueError("bad value"))


@pytest.mark.asyncio
async def test_retry_success_first_try() -> None:
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        return "success"

    slept: list[float] = []

    async def mock_sleep(d: float) -> None:
        slept.append(d)

    res = await retry_async(_op, max_retries=3, sleep_fn=mock_sleep)
    assert res == "success"
    assert calls == 1
    assert slept == []


@pytest.mark.asyncio
async def test_retry_recovers_after_failures() -> None:
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise S3ConnectionError("temporary glitch")
        return "recovered"

    slept: list[float] = []

    async def mock_sleep(d: float) -> None:
        slept.append(d)

    res = await retry_async(_op, max_retries=3, sleep_fn=mock_sleep)
    assert res == "recovered"
    assert calls == 3
    assert len(slept) == 2


@pytest.mark.asyncio
async def test_retry_exceeds_max_retries() -> None:
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        raise S3ConnectionError("persistent glitch")

    slept: list[float] = []

    async def mock_sleep(d: float) -> None:
        slept.append(d)

    with pytest.raises(S3ConnectionError, match="persistent glitch"):
        await retry_async(_op, max_retries=2, sleep_fn=mock_sleep)

    assert calls == 3  # 1 initial + 2 retries
    assert len(slept) == 2


@pytest.mark.asyncio
async def test_non_transient_fails_immediately() -> None:
    calls = 0

    async def _op() -> str:
        nonlocal calls
        calls += 1
        raise ValueError("fatal error")

    slept: list[float] = []

    async def mock_sleep(d: float) -> None:
        slept.append(d)

    with pytest.raises(ValueError, match="fatal error"):
        await retry_async(_op, max_retries=3, sleep_fn=mock_sleep)

    assert calls == 1
    assert len(slept) == 0
