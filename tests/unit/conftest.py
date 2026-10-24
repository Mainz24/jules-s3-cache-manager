import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch socket.socket.connect to prevent network calls in unit tests."""

    def _guarded_connect(self: socket.socket, *args: object, **kwargs: object) -> None:
        raise RuntimeError("Network access blocked in Tier 1 unit tests!")

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
