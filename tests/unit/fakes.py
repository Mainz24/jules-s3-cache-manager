from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import ClientError

from s3_cache._client import translate_s3_error


class FakeAsyncStreamingBody:
    """In-memory async streaming body simulating botocore StreamingBody."""

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._closed = False

    async def read(self) -> bytes:
        if self._closed:
            raise RuntimeError("Stream is closed")
        return self._content

    async def close(self) -> None:
        self._closed = True


class FakeClock:
    """Controllable clock for deterministic TTL testing."""

    def __init__(self, initial_time: datetime | None = None) -> None:
        if initial_time is None:
            self._current_time = datetime(2026, 10, 24, 12, 0, 0, tzinfo=UTC)
        elif initial_time.tzinfo is None:
            self._current_time = initial_time.replace(tzinfo=UTC)
        else:
            self._current_time = initial_time

    def now(self) -> datetime:
        return self._current_time

    def advance(self, seconds: float) -> None:
        self._current_time += timedelta(seconds=seconds)

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._current_time = new_time.replace(tzinfo=UTC)
        else:
            self._current_time = new_time


class FakeS3Client:
    """In-memory S3 client implementation conforming to S3ClientProtocol."""

    def __init__(self, default_buckets: list[str] | None = None) -> None:
        self.buckets: dict[str, dict[str, dict[str, Any]]] = {}
        for b in default_buckets or ["s3-cache", "test-bucket"]:
            self.buckets[b] = {}
        self.injected_errors: dict[str, list[Exception]] = {}
        self.closed = False

    def inject_error(self, method_name: str, exc: Exception) -> None:
        """Inject an exception to be raised on the next call to method_name."""
        if method_name not in self.injected_errors:
            self.injected_errors[method_name] = []
        self.injected_errors[method_name].append(exc)

    def _check_injected_error(self, method_name: str, key: str | None = None) -> None:
        if method_name in self.injected_errors and self.injected_errors[method_name]:
            exc = self.injected_errors[method_name].pop(0)
            raise translate_s3_error(exc, key=key)

    @staticmethod
    def _make_client_error(
        code: str, message: str, status_code: int = 404, key: str | None = None
    ) -> Exception:
        err = ClientError(
            {
                "Error": {"Code": code, "Message": message},
                "ResponseMetadata": {"HTTPStatusCode": status_code},
            },
            "S3Operation",
        )
        return translate_s3_error(err, key=key)

    async def put_object(
        self,
        Bucket: str,
        Key: str,
        Body: bytes,
        Metadata: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._check_injected_error("put_object", key=Key)
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404, key=Key)

        meta_copy = {k.lower(): v for k, v in (Metadata or {}).items()}
        self.buckets[Bucket][Key] = {
            "Body": bytes(Body),
            "Metadata": meta_copy,
        }
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    async def get_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("get_object", key=Key)
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404, key=Key)
        if Key not in self.buckets[Bucket]:
            msg = f"The specified key {Key} does not exist"
            raise self._make_client_error("NoSuchKey", msg, 404, key=Key)

        obj = self.buckets[Bucket][Key]
        return {
            "Body": FakeAsyncStreamingBody(obj["Body"]),
            "Metadata": dict(obj["Metadata"]),
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

    async def head_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("head_object", key=Key)
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404, key=Key)
        if Key not in self.buckets[Bucket]:
            msg = f"The specified key {Key} does not exist"
            raise self._make_client_error("NoSuchKey", msg, 404, key=Key)

        obj = self.buckets[Bucket][Key]
        return {
            "Metadata": dict(obj["Metadata"]),
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }

    async def delete_object(self, Bucket: str, Key: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("delete_object", key=Key)
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404, key=Key)

        self.buckets[Bucket].pop(Key, None)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    async def delete_objects(
        self, Bucket: str, Delete: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        self._check_injected_error("delete_objects")
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404)

        deleted = []
        for obj in Delete.get("Objects", []):
            k = obj.get("Key")
            if k and k in self.buckets[Bucket]:
                del self.buckets[Bucket][k]
                deleted.append({"Key": k})

        return {"Deleted": deleted, "ResponseMetadata": {"HTTPStatusCode": 200}}

    async def list_objects_v2(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("list_objects_v2")
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404)

        prefix = kwargs.get("Prefix", "")
        max_keys = kwargs.get("MaxKeys", 1000)
        token = kwargs.get("ContinuationToken")

        all_keys = sorted([k for k in self.buckets[Bucket].keys() if k.startswith(prefix)])

        start_idx = 0
        if token:
            try:
                start_idx = int(token)
            except ValueError:
                start_idx = 0

        page_keys = all_keys[start_idx : start_idx + max_keys]
        is_truncated = (start_idx + max_keys) < len(all_keys)
        next_token = str(start_idx + max_keys) if is_truncated else None

        contents = [{"Key": k, "Size": len(self.buckets[Bucket][k]["Body"])} for k in page_keys]

        resp: dict[str, Any] = {
            "Contents": contents,
            "IsTruncated": is_truncated,
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }
        if next_token:
            resp["NextContinuationToken"] = next_token
        return resp

    async def head_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("head_bucket")
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404)
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    async def create_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("create_bucket")
        if Bucket not in self.buckets:
            self.buckets[Bucket] = {}
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    async def delete_bucket(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("delete_bucket")
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404)
        if self.buckets[Bucket]:
            msg = f"The bucket {Bucket} is not empty"
            raise self._make_client_error("BucketNotEmpty", msg, 409)
        del self.buckets[Bucket]
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    async def list_multipart_uploads(self, Bucket: str, **kwargs: Any) -> dict[str, Any]:
        self._check_injected_error("list_multipart_uploads")
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404)
        return {"Uploads": [], "ResponseMetadata": {"HTTPStatusCode": 200}}

    async def abort_multipart_upload(
        self, Bucket: str, Key: str, UploadId: str, **kwargs: Any
    ) -> dict[str, Any]:
        self._check_injected_error("abort_multipart_upload", key=Key)
        if Bucket not in self.buckets:
            msg = f"The specified bucket {Bucket} does not exist"
            raise self._make_client_error("NoSuchBucket", msg, 404, key=Key)
        return {"ResponseMetadata": {"HTTPStatusCode": 204}}

    async def close(self) -> None:
        self.closed = True
