# Async S3 Cache Manager (`s3-cache-manager`)

Production-grade, asynchronous S3-compatible caching client in Python 3.11+ using `aiobotocore` and `pydantic` v2, designed with a **Two-Tier Testing** architecture.

---

## Features

- **Single Object Per Key:** Store values directly in S3 with metadata encoded in S3 user metadata headers (`x-amz-meta-s3c-*`). `exists()` is a fast `HEAD` call and `invalidate()` is an atomic `DELETE`.
- **Flexible Serialization:** Built-in support for `bytes`, `str`, `dict`, `list`, and Pydantic `BaseModel` instances with sha256 checksum validation.
- **Resilient Retries:** Custom exponential backoff with jitter for transient 5xx errors and connection drops, disabling botocore internal retries.
- **Garbage Collection:** Asynchronous `cleanup()` for clearing expired cache keys using bounded concurrency.
- **Two-Tier Testing:** Tier 1 (In-Memory Unit Tests with socket guard) and Tier 2 (E2E Integration Tests with automatic sandbox skipping and strict teardown).

---

## Installation

```bash
pip install -e ".[dev]"
```

---

## Quickstart

```python
import asyncio
from pydantic import BaseModel
from s3_cache import S3CacheManager, S3CacheConfig

class UserSession(BaseModel):
    user_id: int
    username: str

async def main():
    config = S3CacheConfig(
        S3_ENDPOINT_URL="http://localhost:9000",
        S3_ACCESS_KEY_ID="minioadmin",
        S3_SECRET_ACCESS_KEY="minioadmin",
        S3_DEFAULT_BUCKET="my-cache-bucket",
    )

    async with S3CacheManager(config=config) as cache:
        # Store string with TTL of 300 seconds
        await cache.set("user:greeting", "Hello, S3 Cache!", ttl=300)

        # Retrieve value
        greeting = await cache.get("user:greeting")
        print("Greeting:", greeting)

        # Store Pydantic model
        session = UserSession(user_id=42, username="jules")
        await cache.set("user:session:42", session, ttl=3600)

        # Retrieve Pydantic model
        retrieved_session = await cache.get("user:session:42", target_type=UserSession)
        print("Session:", retrieved_session)

        # Check existence via HEAD
        if await cache.exists("user:session:42"):
            print("Session active")

        # Invalidate key
        await cache.invalidate("user:greeting")

        # Cleanup expired items
        cleaned_count = await cache.cleanup(prefix="user:")
        print(f"Cleaned {cleaned_count} expired entries.")

if __name__ == "__main__":
    asyncio.run(main())
```

---

## Running Tests

### Tier 1 — Unit Tests (In-Memory, No Network)

```bash
pytest tests/unit/ -v --cov=s3_cache --cov-report=term-missing --cov-fail-under=90
```

### Tier 2 — E2E Integration Tests

E2E tests auto-skip when `S3_ENDPOINT_URL` is unset:

```bash
# In sandbox or without endpoint configured (shows SKIPPED):
pytest tests/e2e/ -v

# With local MinIO or real S3 endpoint:
export S3_ENDPOINT_URL="http://localhost:9000"
export S3_ACCESS_KEY_ID="minioadmin"
export S3_SECRET_ACCESS_KEY="minioadmin"
pytest tests/e2e/ -v
```

### Code Style & Type Checks

```bash
ruff check .
mypy --strict src
```
