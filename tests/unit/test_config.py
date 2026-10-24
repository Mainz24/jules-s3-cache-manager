from pydantic import SecretStr

from s3_cache.config import S3CacheConfig


def test_default_config() -> None:
    config = S3CacheConfig()
    assert config.region == "us-east-1"
    assert config.default_bucket == "s3-cache"
    assert config.default_ttl is None
    assert config.connect_timeout == 5.0
    assert config.read_timeout == 10.0
    assert config.max_retries == 3
    assert config.endpoint_url is None


def test_custom_config() -> None:
    config = S3CacheConfig(
        S3_ENDPOINT_URL="http://minio:9000",
        S3_ACCESS_KEY_ID="mykey",
        S3_SECRET_ACCESS_KEY="mysecret",
        S3_REGION="eu-west-1",
        S3_DEFAULT_BUCKET="custom-bucket",
        S3_DEFAULT_TTL=600,
        S3_CONNECT_TIMEOUT=2.0,
        S3_READ_TIMEOUT=5.0,
        S3_MAX_RETRIES=5,
    )
    assert config.endpoint_url == "http://minio:9000"
    assert isinstance(config.access_key_id, SecretStr)
    assert config.access_key_id.get_secret_value() == "mykey"
    assert isinstance(config.secret_access_key, SecretStr)
    assert config.secret_access_key.get_secret_value() == "mysecret"
    assert config.region == "eu-west-1"
    assert config.default_bucket == "custom-bucket"
    assert config.default_ttl == 600
    assert config.connect_timeout == 2.0
    assert config.read_timeout == 5.0
    assert config.max_retries == 5


def test_secret_str_repr() -> None:
    config = S3CacheConfig(
        S3_ACCESS_KEY_ID="mykey",
        S3_SECRET_ACCESS_KEY="mysecret",
    )
    repr_str = repr(config)
    assert "mykey" not in repr_str
    assert "mysecret" not in repr_str
