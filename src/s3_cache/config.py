from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class S3CacheConfig(BaseSettings):
    """S3 Cache Manager Configuration."""

    model_config = SettingsConfigDict(
        env_prefix="S3_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")
    access_key_id: SecretStr | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    secret_access_key: SecretStr | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")
    region: str = Field(default="us-east-1", alias="S3_REGION")
    default_bucket: str = Field(default="s3-cache", alias="S3_DEFAULT_BUCKET")
    default_ttl: int | None = Field(default=None, alias="S3_DEFAULT_TTL")
    connect_timeout: float = Field(default=5.0, alias="S3_CONNECT_TIMEOUT")
    read_timeout: float = Field(default=10.0, alias="S3_READ_TIMEOUT")
    max_retries: int = Field(default=3, alias="S3_MAX_RETRIES")
    key_prefix: str = Field(default="", alias="S3_KEY_PREFIX")
