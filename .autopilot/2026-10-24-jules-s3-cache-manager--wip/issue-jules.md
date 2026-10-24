# Implement Async S3 Cache Manager (aiobotocore + Pydantic v2) with Two-Tier Testing

## Objective
Implement a production-grade, asynchronous S3-compatible caching client in Python 3.11+ using `aiobotocore` and `pydantic` v2, designed with a **Two-Tier Testing** architecture.

---

## Testing & Execution Rules for Jules (Two-Tier Architecture)

> [!IMPORTANT]
> Google Jules runs in a sandbox with **no external network access**. You are required to pass all **Tier 1 (Unit)** tests inside the sandbox before opening a PR.

- **Tier 1 — Unit Tests (`tests/unit/`):**
  - Must run 100% in-memory using an internal `FakeS3Client` (`tests/unit/fakes.py`) and a controllable `FakeClock`.
  - A socket connection guard must be placed in `tests/unit/conftest.py` (monkeypatching `socket.socket.connect` to raise) so any inadvertent network attempt fails immediately.
  - Acceptance for Jules: `pytest tests/unit/ -v --cov=s3_cache --cov-report=term-missing --cov-fail-under=90` must pass with 100% GREEN.
- **Tier 2 — E2E Integration Tests (`tests/e2e/`):**
  - Jules must write the complete E2E test suite (`tests/e2e/conftest.py`, `tests/e2e/test_e2e_cache.py`).
  - **Auto-skip in Sandbox:** If `S3_ENDPOINT_URL` environment variable is unset or empty, all tests in `tests/e2e/` must automatically skip (`pytestmark = pytest.mark.e2e`, check in fixture / session). This ensures `pytest` runs clean in your sandbox.
  - **Strict Teardown & Isolation Invariant:** The session fixture must create a dynamically named bucket: `f"{os.getenv('S3_TEST_BUCKET_PREFIX', 'test-jules')}-{uuid4().hex[:8]}"`. In a strict `finally:` block, it must paginate and delete all objects (using `DeleteObjects` in chunks of 1000), abort any incomplete multipart uploads, delete the bucket, and verify via `head_bucket` (404) that no storage leaks remain on the cluster.

---

## Technical Stack & Packaging

- **Python:** `>=3.11`
- **Build tool:** `hatchling` (configured in `pyproject.toml`, `src` layout)
- **Dependencies:**
  - `aiobotocore>=2.13.0`
  - `pydantic>=2.6.0`
  - `pydantic-settings>=2.2.0`
- **Dev Dependencies:**
  - `pytest>=8.0.0`
  - `pytest-asyncio>=0.23.0` (`asyncio_mode = "auto"`)
  - `pytest-mock`
  - `pytest-cov`
  - `ruff`
  - `mypy`

---

## Project Structure

```text
pyproject.toml
README.md
.env.example
.gitignore
src/s3_cache/
  __init__.py          # Public exports + __version__ + py.typed
  config.py            # S3CacheConfig (BaseSettings with S3_ prefix)
  models.py            # CacheMetadata, CacheEntry
  exceptions.py        # S3CacheError hierarchy
  serialization.py     # Value <-> bytes encode/decode, content-type, sha256 checksum
  retry.py             # Async retry with exponential backoff & jitter (injectable sleep)
  _client.py           # S3ClientProtocol & aiobotocore factory with error translation
  manager.py           # S3CacheManager main client class
tests/
  conftest.py
  unit/
    fakes.py           # In-memory FakeS3Client & FakeClock
    test_config.py
    test_models.py
    test_serialization.py
    test_retry.py
    test_exceptions.py
    test_manager.py
  e2e/
    conftest.py        # S3_ENDPOINT_URL gating, isolated bucket fixture with strict teardown
    test_e2e_cache.py  # Live roundtrip, TTL expiry, cleanup, leak audit
```

---

## Architecture & Storage Format

1. **Single Object per Key:**
   - Store metadata directly in S3 user metadata headers (`x-amz-meta-s3c-*`), not in a sidecar object.
   - Headers:
     - `s3c-created-at`: ISO-8601 UTC
     - `s3c-expires-at`: ISO-8601 UTC or `""` (no TTL)
     - `s3c-ttl`: integer seconds or `""`
     - `s3c-kind`: `bytes` | `str` | `json`
     - `s3c-checksum`: sha256 hex of raw payload
     - `s3c-version`: `"1"`
   - Benefits: `exists()` is a fast `HEAD` call without payload download; `invalidate()` is a single atomic `DELETE`.
2. **Key Validation:**
   - Keys must be non-empty strings, ≤ 1024 bytes UTF-8, no leading `/`, no `..` segments. Invalid keys raise `ValueError`.
3. **Resilience & Retries:**
   - Disable botocore's internal retries (`retries={"max_attempts": 0}`).
   - Implement custom async retry decorator/function in `retry.py` for transient 5xx errors, connection drops, and network timeouts.
   - Injectable `sleep` parameter so unit tests take zero wall-clock time.

---

## Detailed Task Breakdown

- [ ] **T01 — Project Scaffolding:** Create `pyproject.toml` (hatchling, dependencies, mypy/ruff/pytest settings with `asyncio_mode = "auto"`), `.gitignore`, `.env.example`, and directory tree.
- [ ] **T02 — Exceptions Hierarchy (`exceptions.py`):** Define `S3CacheError(Exception)` base with `.key` attribute and subclasses: `S3ConnectionError`, `S3TimeoutError`, `S3SerializationError`, `S3BucketNotFoundError`.
- [ ] **T03 — Configuration (`config.py`):** Define `S3CacheConfig(BaseSettings)` supporting `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `default_bucket`, `default_ttl`, `connect_timeout`, `read_timeout`, `max_retries`. Mask credentials in `repr` with `SecretStr`.
- [ ] **T04 — Data Models (`models.py`):** Implement Pydantic v2 `CacheMetadata` (with `is_expired()`, `to_s3_metadata()`, `from_s3_head()`) and `CacheEntry`.
- [ ] **T05 — Serialization (`serialization.py`):** Implement `encode()` / `decode()` supporting `bytes`, `str`, `dict`, `list`, and Pydantic `BaseModel`. Verify sha256 checksums on read.
- [ ] **T06 — Retry Logic (`retry.py`):** Implement `with_retry` with exponential backoff and jitter for transient errors. Support custom `sleep` coroutine for instant testing.
- [ ] **T07 — S3 Client Adapter (`_client.py`):** Define `S3ClientProtocol` and an aiobotocore client context manager with error translation into `S3CacheError` hierarchy.
- [ ] **T08 — Unit Test Fakes (`tests/unit/fakes.py`):** Build an in-memory `FakeS3Client` implementing `S3ClientProtocol` (with pagination and fault injection) and `FakeClock`. Add socket block in `tests/unit/conftest.py`.
- [ ] **T09 — Manager Lifecycle (`manager.py`):** Implement `S3CacheManager` `connect()`, `close()`, `async with` context manager, and `is_connected` guard.
- [ ] **T10 — Core Operations (`manager.py`):** Implement `set()`, `get()`, `get_entry()` with automatic TTL handling and lazy eviction on expired reads.
- [ ] **T11 — Query & Invalidation (`manager.py`):** Implement `exists()` (HEAD only, returns False if expired or missing) and `invalidate()` (idempotent DELETE).
- [ ] **T12 — Garbage Collection (`manager.py`):** Implement `cleanup()` with pagination (`list_objects_v2`), bounded concurrency (`asyncio.Semaphore(16)`), and chunked `delete_objects`.
- [ ] **T13 — Public Package Exports:** Export main classes in `s3_cache/__init__.py` and include `py.typed`.
- [ ] **T14 — E2E Test Suite (`tests/e2e/`):** Implement `conftest.py` (auto-skip if `S3_ENDPOINT_URL` is unset, isolated bucket fixture with strict teardown) and `test_e2e_cache.py`.
- [ ] **T15 — Documentation:** Comprehensive `README.md` with usage examples and commands for running unit vs E2E tests.
- [ ] **T16 — Quality Gate & PR:** Run `ruff check .`, `mypy --strict src`, verify unit tests pass with ≥ 90% coverage. Open PR with completed checklist.

---

## Verification Commands for Jules

```bash
pip install -e ".[dev]"
ruff check .
mypy --strict src
pytest tests/unit/ -v --cov=s3_cache --cov-report=term-missing --cov-fail-under=90
pytest tests/e2e/ -v  # Must show all skipped when S3_ENDPOINT_URL is not set
```
