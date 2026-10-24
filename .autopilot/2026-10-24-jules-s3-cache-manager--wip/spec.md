# Jules S3 Cache Manager — Spec & Implementation Plan

> [!IMPORTANT]
> Jules works in a sandbox with **no network access** to `10.20.0.6:3900`. It must deliver green `tests/unit/` only. `tests/e2e/` must be **written** by Jules but **run** by Hermes QA after the PR is opened. E2E tests must auto-skip when `S3_ENDPOINT_URL` is unset, so Jules's `pytest` run stays green.

## 1. Tech Stack (pinned decisions)

| Concern | Choice | Rationale |
|---|---|---|
| Python | `>=3.11` | `asyncio.timeout`, `X | Y` unions |
| S3 client | `aiobotocore>=2.13` | Native async, botocore retry/timeouts, works with Garage (path-style) |
| Config/models | `pydantic>=2.6`, `pydantic-settings>=2.2` | Required |
| Tests | `pytest>=8`, `pytest-asyncio>=0.23` (`asyncio_mode=auto`), `pytest-mock`, `pytest-cov` | |
| Lint/type | `ruff`, `mypy --strict` | |
| Build | `hatchling`, src layout | |

**No `moto` server** for unit tests. Use an in-memory `FakeS3Client` instead: it is deterministic, fast, and avoids any socket or network use.

## 2. Project Layout

```
pyproject.toml
README.md
.env.example
src/s3_cache/
  __init__.py          # public exports + __version__
  config.py            # S3CacheConfig (BaseSettings)
  models.py            # CacheMetadata, CacheEntry
  exceptions.py        # S3CacheError hierarchy
  serialization.py     # encode/decode value <-> bytes + content_type
  retry.py             # async retry w/ exponential backoff + jitter
  _client.py           # S3ClientProtocol + aiobotocore factory
  manager.py           # S3CacheManager
tests/
  conftest.py
  unit/
    fakes.py           # FakeS3Client (in-memory, implements S3ClientProtocol)
    test_config.py  test_models.py  test_serialization.py
    test_retry.py   test_manager.py test_exceptions.py
  e2e/
    conftest.py        # env gating, isolated bucket fixture with strict teardown
    test_e2e_cache.py
```

## 3. Storage Format (decision)

A **single object per key**. Metadata is stored in S3 user metadata headers (`x-amz-meta-*`), not in a sidecar object:
- `exists()` → `HEAD` only (no payload download).
- `invalidate()` → one `DELETE`.
- `cleanup()` → `ListObjectsV2` + `HEAD`, then batch `DeleteObjects` (≤1000 per call).

Object key: `{key_prefix}{key}`, where `key_prefix` defaults to `"cache/"`. User metadata, all strings:

| meta key | value |
|---|---|
| `s3c-created-at` | ISO-8601 UTC |
| `s3c-expires-at` | ISO-8601 UTC or `""` (no TTL) |
| `s3c-ttl` | int seconds or `""` |
| `s3c-kind` | `bytes` \| `str` \| `json` |
| `s3c-checksum` | sha256 hex of payload |
| `s3c-version` | `"1"` |

Keys are validated: non-empty, ≤ 1024 bytes UTF-8, no leading `/`, no `..` segments. A bad key raises `ValueError`.

## 4. API Specification

### 4.1 `config.py`
```python
class S3CacheConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="S3_", env_file=".env", extra="ignore")
    endpoint_url: str | None = None
    aws_access_key_id: SecretStr | None = Field(None, validation_alias=AliasChoices("S3_ACCESS_KEY_ID","AWS_ACCESS_KEY_ID"))
    aws_secret_access_key: SecretStr | None = Field(None, validation_alias=AliasChoices("S3_SECRET_ACCESS_KEY","AWS_SECRET_ACCESS_KEY"))
    region_name: str = Field("garage", validation_alias=AliasChoices("S3_REGION","S3_REGION_NAME"))
    default_bucket: str = "cache"
    key_prefix: str = "cache/"
    default_ttl: int | None = 3600        # None/0 => no expiry; must be >= 0
    connect_timeout: float = 5.0          # > 0
    read_timeout: float = 30.0            # > 0
    max_retries: int = 3                  # 0..10
    retry_base_delay: float = 0.2
    retry_max_delay: float = 5.0
    addressing_style: Literal["path","virtual","auto"] = "path"
```

### 4.2 `models.py`
```python
class CacheMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)
    key: str
    created_at: datetime            # tz-aware UTC
    expires_at: datetime | None
    ttl_seconds: int | None
    content_type: str
    size_bytes: int
    etag: str | None
    checksum: str                   # sha256 hex
    kind: Literal["bytes","str","json"]
    def is_expired(self, now: datetime | None = None) -> bool: ...
    def to_s3_metadata(self) -> dict[str, str]: ...
    @classmethod
    def from_s3_head(cls, key: str, head: dict) -> "CacheMetadata": ...   # raises S3SerializationError on malformed

class CacheEntry(BaseModel):
    metadata: CacheMetadata
    raw: bytes
    @property
    def value(self) -> bytes | str | dict | list: ...   # decoded per kind
```

### 4.3 `exceptions.py`
```
S3CacheError(Exception)                # base; .key: str|None, .__cause__ preserved
├── S3ConnectionError                  # EndpointConnectionError, ConnectionClosedError, OSError, not connected
├── S3TimeoutError                     # ConnectTimeoutError, ReadTimeoutError, asyncio.TimeoutError
├── S3SerializationError               # unsupported type, JSON fail, checksum mismatch, bad metadata
└── S3BucketNotFoundError              # NoSuchBucket
```

### 4.4 `serialization.py`
`encode(value, content_type=None) -> tuple[bytes, kind, content_type]`
- `bytes`/`bytearray`/`memoryview` → kind `bytes`, default `application/octet-stream`.
- `str` → UTF-8, kind `str`, default `text/plain; charset=utf-8`.
- `dict`/`list` → `json.dumps(separators=(",",":"), ensure_ascii=False)`, kind `json`, default `application/json`.
- `BaseModel` → `model_dump_json()`, kind `json`. `get()` returns a `dict`.
- Anything else raises `S3SerializationError`.

`decode(raw, kind) -> value` reverses this. Raises `S3SerializationError` on failure.

### 4.5 `retry.py`
`async def with_retry(fn, *, max_retries, base_delay, max_delay, sleep=asyncio.sleep)`:
- **Retryable:** `S3ConnectionError`, `S3TimeoutError`, and `ClientError` with HTTP status in {500, 502, 503, 504} or code in {`SlowDown`, `RequestTimeout`, `InternalError`, `ServiceUnavailable`}.
- **Not retried:** 4xx errors (403/404/409), `S3SerializationError`.
- **Backoff:** `min(max_delay, base_delay * 2**attempt) * uniform(0.5, 1.0)`. Total attempts are `max_retries + 1`.
- `sleep` is injectable so unit tests never really sleep.
- botocore's own retries are disabled (`retries={"max_attempts": 0}` / `total_max_attempts: 1`).

### 4.6 `_client.py`
- Defines `S3ClientProtocol` (typing.Protocol) with `put_object`, `get_object`, `head_object`, `delete_object`, `delete_objects`, `list_objects_v2` (paginated via `ContinuationToken`), `create_bucket`, `delete_bucket`, `head_bucket`.
- `create_client(config) -> AsyncContextManager[S3ClientProtocol]` builds an aiobotocore client with `AioConfig(connect_timeout, read_timeout, s3={"addressing_style": ...}, retries=...)`.
- `translate_errors()` maps botocore/aiohttp/asyncio exceptions to the §4.3 hierarchy.

### 4.7 `manager.py`
```python
class S3CacheManager:
    def __init__(self, config: S3CacheConfig | None = None, *, bucket: str | None = None,
                 client_factory: Callable[[S3CacheConfig], AsyncContextManager[S3ClientProtocol]] | None = None,
                 clock: Callable[[], datetime] = utc_now): ...
    async def connect(self) -> None          # idempotent
    async def close(self) -> None            # idempotent, safe if never connected
    async def __aenter__(self) -> Self; async def __aexit__(...)
    @property
    def is_connected(self) -> bool

    async def get(self, key: str) -> bytes | str | dict | list | None
    async def get_entry(self, key: str) -> CacheEntry | None
    async def set(self, key, value, ttl: int | None = None, content_type: str | None = None) -> CacheMetadata
    async def exists(self, key: str) -> bool
    async def invalidate(self, key: str) -> bool
    async def cleanup(self) -> int
```

| Method | Behavior |
|---|---|
| op before `connect()` | raises `S3ConnectionError("not connected")` |
| `set` | `ttl=None` uses `default_ttl`. `ttl=0` means no expiry. `ttl<0` raises `ValueError`. Calls `PutObject`. Returns metadata with `etag`. |
| `get` | `GetObject`, then parse metadata. If expired, returns `None` and best-effort deletes object (lazy eviction). Checksum mismatch raises `S3SerializationError`. On 404 returns `None`. |
| `exists` | `HEAD` only. Returns `False` on 404 or if expired. Never calls `GetObject`. |
| `invalidate` | `HEAD` then `DELETE`. Returns `True` if existed, `False` otherwise. Idempotent. |
| `cleanup` | Paginates `list_objects_v2(Prefix=key_prefix)`, runs `HEAD` with bounded concurrency (`asyncio.Semaphore(16)`), batch-deletes expired keys in chunks of 1000, returns deleted count. Foreign objects without `s3c-*` meta are skipped. |

## 5. Testing Architecture

### Tier 1 — `tests/unit/` (Jules Sandbox)
- `FakeS3Client` in `tests/unit/fakes.py` dict-backed `{bucket: {key: (body, meta, content_type, etag)}}`.
- Advanceable `FakeClock`.
- Monkeypatch `socket.socket.connect` in `tests/unit/conftest.py` to raise. Accidental network use fails tests immediately.
- Required coverage: `--cov=s3_cache --cov-fail-under=90`.

### Tier 2 — `tests/e2e/` (Hermes Cluster)
- `pytestmark = pytest.mark.e2e`. Skips if `S3_ENDPOINT_URL` is unset.
- Isolated bucket fixture: `test-jules-<uuid8>` with strict teardown in `finally` deleting all objects and the bucket, asserting bucket is 404 deleted.
- Runs against Garage S3 `http://10.20.0.6:3900`.

## 6. Tasks for Jules (GitHub Issue Breakdown)

- **T01**: Scaffold: `pyproject.toml`, src layout, `.env.example`, `.gitignore`, `README.md` stub.
- **T02**: `exceptions.py` hierarchy.
- **T03**: `config.py` `S3CacheConfig` with Pydantic v2 validators.
- **T04**: `models.py` (`CacheMetadata`, `CacheEntry`, `to_s3_metadata`, `from_s3_head`, `is_expired`).
- **T05**: `serialization.py` (bytes/str/dict/list/BaseModel encoders & decoders).
- **T06**: `retry.py` (async retry with exponential backoff & jitter, injectable sleep).
- **T07**: `_client.py` (`S3ClientProtocol`, aiobotocore factory, `translate_errors`).
- **T08**: `tests/unit/fakes.py` (`FakeS3Client`, `FakeClock`, socket network guard in conftest).
- **T09**: `manager.py` lifecycle (`connect`, `close`, async context manager, guard).
- **T10**: `manager.py`: `set`, `get`, `get_entry` implementation.
- **T11**: `manager.py`: `exists`, `invalidate` implementation.
- **T12**: `manager.py`: `cleanup` with bounded concurrency and pagination.
- **T13**: Public API in `__init__.py`, `py.typed`, docstrings.
- **T14**: `tests/e2e/conftest.py` + `test_e2e_cache.py` (Tier 2 auto-skip when no `S3_ENDPOINT_URL`, teardown).
- **T15**: `README.md` documentation and test command guides.
- **T16**: Final gate: `ruff`, `mypy --strict`, unit tests 100% pass with ≥90% coverage.
