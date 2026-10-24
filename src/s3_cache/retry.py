import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from botocore.exceptions import BotoCoreError, ClientError

from s3_cache.exceptions import S3ConnectionError, S3TimeoutError

T = TypeVar("T")

TRANSIENT_HTTP_STATUS_CODES = {500, 502, 503, 504}


def is_transient_exception(exc: Exception) -> bool:
    """Check if an exception is transient and eligible for retry."""
    if isinstance(exc, (S3ConnectionError, S3TimeoutError, TimeoutError, ConnectionError)):
        return True

    if isinstance(exc, ClientError):
        status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if status_code in TRANSIENT_HTTP_STATUS_CODES:
            return True
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in ("RequestTimeout", "ServiceUnavailable", "SlowDown", "InternalError"):
            return True

    if isinstance(exc, BotoCoreError):
        # General botocore connection / endpoint errors
        return True

    return False


async def retry_async(
    func: Callable[[], Awaitable[T]],
    max_retries: int = 3,
    base_delay: float = 0.1,
    max_delay: float = 2.0,
    sleep_fn: Callable[[float], Awaitable[None]] | None = None,
) -> T:
    """
    Execute func with exponential backoff and jitter on transient errors.

    If max_retries <= 0, func is executed once without retries.
    """
    if sleep_fn is None:
        sleep_fn = asyncio.sleep

    attempt = 0
    while True:
        try:
            return await func()
        except Exception as exc:
            if attempt >= max_retries or not is_transient_exception(exc):
                raise
            attempt += 1
            # Exponential backoff with jitter
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            jittered_delay = random.uniform(0, delay)
            await sleep_fn(jittered_delay)
