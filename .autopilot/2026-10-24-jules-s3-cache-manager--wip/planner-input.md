# Brief for Planner Subagent: Jules S3 Cache Manager

## Objective
Design a comprehensive architectural specification and step-by-step implementation plan for an external coding agent (Google Jules) to implement an asynchronous S3 cache client module in Python 3.11+.

## Context & Execution Model
- Development is delegated to **Google Jules** (autonomous AI coding agent in Google Cloud Sandbox).
- Google Jules runs code inside an isolated sandbox with NO network access to our internal private cluster.
- After Jules opens a Pull Request on GitHub, our local cluster pipeline (Hermes QA/Reviewer) will checkout the PR and execute live E2E tests against our internal Garage S3 cluster (`http://10.20.0.6:3900`).

## Key Requirements & Invariants

### 1. Two-Tier Testing Architecture
- **Tier 1 (Unit Tests for Jules Sandbox):**
  - Path: `tests/unit/`
  - Fully mocked S3 interactions (using `unittest.mock` / `pytest-mock` / in-memory fake async S3 client).
  - Jules must be able to run `pytest tests/unit/ -v` and achieve 100% PASS with 0 external network dependencies before opening the PR.
- **Tier 2 (E2E Integration Tests for Internal Cluster):**
  - Path: `tests/e2e/`
  - Connects to real S3 endpoint configured via environment variables (`S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_TEST_BUCKET_PREFIX`).
  - **Strict Teardown & Isolation Invariant:** Every test run must generate an isolated bucket named `test-jules-<uuid8>` (or with timestamp), execute operations, and in a strict `finally`/pytest fixture teardown delete all objects and delete the bucket, leaving 0 leftover files or buckets on VPS storage.

### 2. Core Architecture & API Interfaces
- **Config & Settings:**
  - Pydantic v2 `BaseSettings` (`S3CacheConfig`): endpoint_url, aws_access_key_id, aws_secret_access_key, region_name, default_bucket, default_ttl, connect_timeout, read_timeout, max_retries.
- **Data Models (Pydantic v2):**
  - `CacheMetadata`: key, created_at, expires_at, ttl_seconds, content_type, size_bytes, etag/checksum.
  - `CacheEntry`: metadata + raw/deserialized payload.
- **Async Client Interface (`S3CacheManager`):**
  - Asynchronous lifecycle (`async with S3CacheManager(...) as cache:` or explicit `connect()` / `close()`).
  - `async def get(key: str) -> Optional[bytes | dict | str]`: returns None on cache miss or expired TTL.
  - `async def set(key: str, value: bytes | str | dict | BaseModel, ttl: Optional[int] = None, content_type: Optional[str] = None) -> CacheMetadata`: stores object + metadata.
  - `async def invalidate(key: str) -> bool`: deletes key and its metadata from S3.
  - `async def cleanup() -> int`: scans and purges expired items, returns count of deleted items.
  - `async def exists(key: str) -> bool`: checks presence and valid TTL without downloading entire payload.
- **Resilience & Exceptions:**
  - Custom exceptions: `S3CacheError`, `S3TimeoutError`, `S3ConnectionError`, `S3SerializationError`.
  - Proper handling of network timeouts, connection drops, and transient HTTP 5xx errors with retry policy.

### 3. Deliverables for Jules
- Project structure (`src/s3_cache/` or `jules_s3_cache/`, `tests/unit/`, `tests/e2e/`, `pyproject.toml`, `README.md`).
- Exact CLI commands for running tests.
- Clear step-by-step tasks (T01..Tnn) that will be posted as a GitHub Issue for Jules.

Generate the detailed plan and specification.
