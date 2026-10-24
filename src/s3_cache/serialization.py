import hashlib
import json
from typing import Any

from pydantic import BaseModel

from s3_cache.exceptions import S3SerializationError
from s3_cache.models import ValueKind


def compute_checksum(data: bytes) -> str:
    """Compute sha256 hex digest of raw payload bytes."""
    return hashlib.sha256(data).hexdigest()


def encode_value(value: Any) -> tuple[bytes, ValueKind, str]:
    """
    Encode value into (raw_bytes, kind, sha256_checksum).

    Supported value types:
    - bytes
    - str
    - pydantic BaseModel
    - dict, list, int, float, bool, None (JSON serializable)
    """
    try:
        if isinstance(value, bytes):
            raw_bytes = value
            kind: ValueKind = "bytes"
        elif isinstance(value, str):
            raw_bytes = value.encode("utf-8")
            kind = "str"
        elif isinstance(value, BaseModel):
            raw_bytes = value.model_dump_json().encode("utf-8")
            kind = "json"
        else:
            raw_bytes = json.dumps(value).encode("utf-8")
            kind = "json"
    except Exception as e:
        raise S3SerializationError(f"Failed to encode value: {e}") from e

    checksum = compute_checksum(raw_bytes)
    return raw_bytes, kind, checksum


def decode_value(
    raw_bytes: bytes,
    kind: ValueKind,
    expected_checksum: str | None = None,
    target_type: type[Any] | None = None,
    key: str | None = None,
) -> Any:
    """
    Decode raw payload bytes into Python object according to kind and optional target_type.

    Verifies checksum if expected_checksum is provided.
    Raises S3SerializationError on checksum mismatch or decoding error.
    """
    if expected_checksum:
        actual_checksum = compute_checksum(raw_bytes)
        if actual_checksum != expected_checksum:
            msg = (
                f"Checksum mismatch for key '{key}': "
                f"expected {expected_checksum}, got {actual_checksum}"
            )
            raise S3SerializationError(msg, key=key)

    try:
        if kind == "bytes":
            return raw_bytes
        if kind == "str":
            return raw_bytes.decode("utf-8")
        if kind == "json":
            json_str = raw_bytes.decode("utf-8")
            if (
                target_type is not None
                and isinstance(target_type, type)
                and issubclass(target_type, BaseModel)
            ):
                return target_type.model_validate_json(json_str)
            return json.loads(json_str)
        raise S3SerializationError(f"Unknown value kind: {kind}", key=key)
    except S3SerializationError:
        raise
    except Exception as e:
        raise S3SerializationError(f"Failed to decode value for key '{key}': {e}", key=key) from e
