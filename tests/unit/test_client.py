from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
)

from s3_cache._client import (
    AiobotocoreClientWrapper,
    create_aiobotocore_client,
    translate_s3_error,
)
from s3_cache.config import S3CacheConfig
from s3_cache.exceptions import (
    S3BucketNotFoundError,
    S3ConnectionError,
    S3TimeoutError,
)


def test_translate_s3_error() -> None:
    # ConnectTimeoutError
    err = translate_s3_error(ConnectTimeoutError(endpoint_url="http://test"), key="k1")
    assert isinstance(err, S3TimeoutError)
    assert err.key == "k1"

    # EndpointConnectionError
    err2 = translate_s3_error(EndpointConnectionError(endpoint_url="http://test"), key="k2")
    assert isinstance(err2, S3ConnectionError)
    assert err2.key == "k2"

    # NoSuchBucket ClientError
    client_err = ClientError(
        {"Error": {"Code": "NoSuchBucket", "Message": "Bucket missing"}},
        "HeadObject",
    )
    err3 = translate_s3_error(client_err, key="k3")
    assert isinstance(err3, S3BucketNotFoundError)
    assert err3.key == "k3"

    # RequestTimeout ClientError
    timeout_client_err = ClientError(
        {"Error": {"Code": "RequestTimeout", "Message": "Timeout"}},
        "GetObject",
    )
    err_timeout = translate_s3_error(timeout_client_err, key="kt")
    assert isinstance(err_timeout, S3TimeoutError)
    assert err_timeout.key == "kt"

    # Other ClientError
    other_client_err = ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "Forbidden"}},
        "GetObject",
    )
    err4 = translate_s3_error(other_client_err, key="k4")
    assert isinstance(err4, S3ConnectionError)
    assert err4.key == "k4"

    # Standard Exception
    std_err = ValueError("standard error")
    assert translate_s3_error(std_err) is std_err


@pytest.mark.asyncio
async def test_aiobotocore_client_wrapper_methods() -> None:
    raw_client = AsyncMock()
    raw_client.put_object.return_value = {"ResponseMetadata": {"HTTPStatusCode": 200}}
    raw_client.get_object.return_value = {"Body": AsyncMock(), "Metadata": {}}
    raw_client.head_object.return_value = {"Metadata": {}}
    raw_client.delete_object.return_value = {}
    raw_client.delete_objects.return_value = {"Deleted": []}
    raw_client.list_objects_v2.return_value = {"Contents": []}
    raw_client.head_bucket.return_value = {}
    raw_client.create_bucket.return_value = {}
    raw_client.delete_bucket.return_value = {}
    raw_client.list_multipart_uploads.return_value = {"Uploads": []}
    raw_client.abort_multipart_upload.return_value = {}

    wrapper = AiobotocoreClientWrapper(raw_client)

    assert await wrapper.put_object("b", "k", b"val", Metadata={"s3c-kind": "bytes"}) == {
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }
    assert await wrapper.get_object("b", "k") is not None
    assert await wrapper.head_object("b", "k") == {"Metadata": {}}
    assert await wrapper.delete_object("b", "k") == {}
    assert await wrapper.delete_objects("b", {"Objects": []}) == {"Deleted": []}
    assert await wrapper.list_objects_v2("b") == {"Contents": []}
    assert await wrapper.head_bucket("b") == {}
    assert await wrapper.create_bucket("b") == {}
    assert await wrapper.delete_bucket("b") == {}
    assert await wrapper.list_multipart_uploads("b") == {"Uploads": []}
    assert await wrapper.abort_multipart_upload("b", "k", "uid") == {}

    # Put object with Metadata=None
    await wrapper.put_object("b", "k", b"val", Metadata=None)

    await wrapper.close()
    assert raw_client.__aexit__.called


@pytest.mark.asyncio
async def test_aiobotocore_client_wrapper_close_alternative() -> None:
    raw_client = MagicMock(spec=["close"])
    raw_client.close = AsyncMock()

    wrapper = AiobotocoreClientWrapper(raw_client)
    await wrapper.close()
    assert raw_client.close.called


@pytest.mark.asyncio
async def test_aiobotocore_client_wrapper_all_method_exceptions() -> None:
    raw_client = AsyncMock()
    err = ClientError({"Error": {"Code": "NoSuchBucket"}}, "op")
    raw_client.put_object.side_effect = err
    raw_client.get_object.side_effect = err
    raw_client.head_object.side_effect = err
    raw_client.delete_object.side_effect = err
    raw_client.delete_objects.side_effect = err
    raw_client.list_objects_v2.side_effect = err
    raw_client.head_bucket.side_effect = err
    raw_client.create_bucket.side_effect = err
    raw_client.delete_bucket.side_effect = err
    raw_client.list_multipart_uploads.side_effect = err
    raw_client.abort_multipart_upload.side_effect = err
    raw_client.__aexit__.side_effect = err

    wrapper = AiobotocoreClientWrapper(raw_client)

    with pytest.raises(S3BucketNotFoundError):
        await wrapper.put_object("b", "k", b"v")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.get_object("b", "k")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.head_object("b", "k")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.delete_object("b", "k")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.delete_objects("b", {"Objects": []})
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.list_objects_v2("b")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.head_bucket("b")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.create_bucket("b")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.delete_bucket("b")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.list_multipart_uploads("b")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.abort_multipart_upload("b", "k", "uid")
    with pytest.raises(S3BucketNotFoundError):
        await wrapper.close()


@pytest.mark.asyncio
async def test_create_aiobotocore_client() -> None:
    config = S3CacheConfig(
        S3_ENDPOINT_URL="http://minio:9000",
        S3_ACCESS_KEY_ID="key",
        S3_SECRET_ACCESS_KEY="secret",
    )
    mock_raw_client = AsyncMock()
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__.return_value = mock_raw_client

    with patch("aiobotocore.session.get_session") as mock_get_session:
        mock_session = MagicMock()
        mock_get_session.return_value = mock_session
        mock_session.create_client.return_value = mock_client_ctx

        wrapper = await create_aiobotocore_client(config)
        assert isinstance(wrapper, AiobotocoreClientWrapper)
        assert mock_session.create_client.called
