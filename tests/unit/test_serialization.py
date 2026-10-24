import pytest
from pydantic import BaseModel

from s3_cache.exceptions import S3SerializationError
from s3_cache.serialization import compute_checksum, decode_value, encode_value


class UserProfile(BaseModel):
    user_id: int
    username: str


def test_encode_decode_bytes() -> None:
    data = b"raw binary content \x00\x01\x02"
    raw_bytes, kind, checksum = encode_value(data)

    assert kind == "bytes"
    assert raw_bytes == data
    assert checksum == compute_checksum(data)

    decoded = decode_value(raw_bytes, kind, expected_checksum=checksum)
    assert decoded == data


def test_encode_decode_str() -> None:
    text = "Hello, world! 🚀"
    raw_bytes, kind, checksum = encode_value(text)

    assert kind == "str"
    assert checksum == compute_checksum(raw_bytes)

    decoded = decode_value(raw_bytes, kind, expected_checksum=checksum)
    assert decoded == text


def test_encode_decode_json_dict() -> None:
    obj = {"name": "Alice", "score": 100, "tags": ["admin", "user"]}
    raw_bytes, kind, checksum = encode_value(obj)

    assert kind == "json"
    assert checksum == compute_checksum(raw_bytes)

    decoded = decode_value(raw_bytes, kind, expected_checksum=checksum)
    assert decoded == obj


def test_encode_decode_pydantic_model() -> None:
    profile = UserProfile(user_id=42, username="jules")
    raw_bytes, kind, checksum = encode_value(profile)

    assert kind == "json"

    decoded_dict = decode_value(raw_bytes, kind, expected_checksum=checksum)
    assert decoded_dict == {"user_id": 42, "username": "jules"}

    decoded_model = decode_value(
        raw_bytes, kind, expected_checksum=checksum, target_type=UserProfile
    )
    assert isinstance(decoded_model, UserProfile)
    assert decoded_model.user_id == 42
    assert decoded_model.username == "jules"


def test_encode_unserializable_raises() -> None:
    class Unserializable:
        pass

    with pytest.raises(S3SerializationError, match="Failed to encode value"):
        encode_value(Unserializable())


def test_checksum_mismatch_raises() -> None:
    data = b"content"
    raw_bytes, kind, _ = encode_value(data)

    with pytest.raises(S3SerializationError, match="Checksum mismatch"):
        decode_value(raw_bytes, kind, expected_checksum="wrong_checksum", key="test_key")


def test_decode_invalid_json_raises() -> None:
    invalid_bytes = b"not json content"
    with pytest.raises(S3SerializationError, match="Failed to decode value"):
        decode_value(invalid_bytes, "json", key="test_key")


def test_unknown_kind_raises() -> None:
    with pytest.raises(S3SerializationError, match="Unknown value kind"):
        decode_value(b"test", "invalid_kind"  # type: ignore[arg-type]
                     , key="test_key")
