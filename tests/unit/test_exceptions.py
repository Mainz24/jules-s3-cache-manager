from s3_cache.exceptions import (
    S3BucketNotFoundError,
    S3CacheError,
    S3ConnectionError,
    S3SerializationError,
    S3TimeoutError,
)


def test_exception_attributes() -> None:
    err = S3CacheError("base error", key="my-key")
    assert str(err) == "base error"
    assert err.key == "my-key"
    assert isinstance(err, Exception)


def test_subclass_exceptions() -> None:
    conn_err = S3ConnectionError("conn failed", key="key1")
    assert isinstance(conn_err, S3CacheError)
    assert conn_err.key == "key1"

    timeout_err = S3TimeoutError("timeout", key="key2")
    assert isinstance(timeout_err, S3CacheError)
    assert timeout_err.key == "key2"

    ser_err = S3SerializationError("serde failed", key="key3")
    assert isinstance(ser_err, S3CacheError)
    assert ser_err.key == "key3"

    bucket_err = S3BucketNotFoundError("bucket missing")
    assert isinstance(bucket_err, S3CacheError)
    assert bucket_err.key is None
