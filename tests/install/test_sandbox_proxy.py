from __future__ import annotations

import importlib.util
import ssl
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "scripts" / "sandbox" / "proxy.py"


def _load_proxy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["proxy.py", str(tmp_path), str(tmp_path), str(tmp_path / "ca.pem")],
    )
    spec = importlib.util.spec_from_file_location("sandbox_proxy_under_test", PROXY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _ContextManager:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


def test_https_get_retries_only_before_response_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy = _load_proxy(monkeypatch, tmp_path)
    first = MagicMock()
    first.recv.side_effect = ssl.SSLEOFError("transient")
    second = MagicMock()
    second.recv.side_effect = [b"HTTP/1.1 200 OK\r\n\r\nbody", b""]
    upstreams = iter([first, second])
    context = MagicMock()
    context.wrap_socket.side_effect = lambda *_args, **_kwargs: _ContextManager(
        next(upstreams)
    )
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda **_kwargs: context)
    create_connection = MagicMock(side_effect=lambda *_args, **_kwargs: _ContextManager(MagicMock()))
    monkeypatch.setattr(proxy.socket, "create_connection", create_connection)
    sleep = MagicMock()
    monkeypatch.setattr(proxy.time, "sleep", sleep)
    client = MagicMock()

    proxy.forward_https(client, "registry.npmjs.org", 443, b"GET /pkg HTTP/1.1\r\n\r\n")

    assert create_connection.call_count == 2
    client.sendall.assert_called_once_with(b"HTTP/1.1 200 OK\r\n\r\nbody")
    sleep.assert_called_once_with(0.25)


def test_https_post_never_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy = _load_proxy(monkeypatch, tmp_path)
    upstream = MagicMock()
    upstream.recv.side_effect = ssl.SSLEOFError("terminal")
    context = MagicMock()
    context.wrap_socket.return_value = _ContextManager(upstream)
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda **_kwargs: context)
    create_connection = MagicMock(return_value=_ContextManager(MagicMock()))
    monkeypatch.setattr(proxy.socket, "create_connection", create_connection)

    with pytest.raises(ssl.SSLEOFError):
        proxy.forward_https(
            MagicMock(),
            "registry.npmjs.org",
            443,
            b"POST /pkg HTTP/1.1\r\n\r\n",
        )

    assert create_connection.call_count == 1


def test_https_get_retries_clean_eof_before_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy = _load_proxy(monkeypatch, tmp_path)
    first = MagicMock()
    first.recv.return_value = b""
    second = MagicMock()
    second.recv.side_effect = [b"HTTP/1.1 200 OK\r\n\r\nbody", b""]
    upstreams = iter([first, second])
    context = MagicMock()
    context.wrap_socket.side_effect = lambda *_args, **_kwargs: _ContextManager(
        next(upstreams)
    )
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda **_kwargs: context)
    create_connection = MagicMock(
        side_effect=lambda *_args, **_kwargs: _ContextManager(MagicMock())
    )
    monkeypatch.setattr(proxy.socket, "create_connection", create_connection)
    monkeypatch.setattr(proxy.time, "sleep", MagicMock())
    client = MagicMock()

    proxy.forward_https(client, "registry.npmjs.org", 443, b"GET /pkg HTTP/1.1\r\n\r\n")

    assert create_connection.call_count == 2
    client.sendall.assert_called_once_with(b"HTTP/1.1 200 OK\r\n\r\nbody")


def test_https_get_clean_eof_exhaustion_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy = _load_proxy(monkeypatch, tmp_path)
    upstreams = []
    for _ in range(3):
        upstream = MagicMock()
        upstream.recv.return_value = b""
        upstreams.append(upstream)
    upstream_iter = iter(upstreams)
    context = MagicMock()
    context.wrap_socket.side_effect = lambda *_args, **_kwargs: _ContextManager(
        next(upstream_iter)
    )
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda **_kwargs: context)
    create_connection = MagicMock(
        side_effect=lambda *_args, **_kwargs: _ContextManager(MagicMock())
    )
    monkeypatch.setattr(proxy.socket, "create_connection", create_connection)
    sleep = MagicMock()
    monkeypatch.setattr(proxy.time, "sleep", sleep)

    with pytest.raises(ConnectionError, match="before sending an HTTP response"):
        proxy.forward_https(
            MagicMock(),
            "registry.npmjs.org",
            443,
            b"GET /pkg HTTP/1.1\r\n\r\n",
        )

    assert create_connection.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [0.25, 0.5]


def test_https_get_does_not_retry_after_response_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proxy = _load_proxy(monkeypatch, tmp_path)
    upstream = MagicMock()
    upstream.recv.side_effect = [b"partial", ssl.SSLEOFError("terminal")]
    context = MagicMock()
    context.wrap_socket.return_value = _ContextManager(upstream)
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda **_kwargs: context)
    create_connection = MagicMock(return_value=_ContextManager(MagicMock()))
    monkeypatch.setattr(proxy.socket, "create_connection", create_connection)
    client = MagicMock()

    with pytest.raises(ssl.SSLEOFError):
        proxy.forward_https(
            client,
            "registry.npmjs.org",
            443,
            b"GET /pkg HTTP/1.1\r\n\r\n",
        )

    assert create_connection.call_count == 1
    client.sendall.assert_called_once_with(b"partial")
