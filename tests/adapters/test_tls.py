from __future__ import annotations

import ssl
import subprocess
from pathlib import Path

import pytest

from cashflow_audit.adapters.tls import httpx_verify, log_host
from cashflow_audit.errors import PortError


def _self_signed_ca(tmp_path: Path) -> Path:
    ca = tmp_path / "ca.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(key),
            "-out",
            str(ca),
            "-days",
            "1",
            "-nodes",
            "-subj",
            "/CN=test",
        ],
        check=True,
        capture_output=True,
    )
    return ca


def test_httpx_verify_none_is_system_ca() -> None:
    assert httpx_verify(None) is True


def test_httpx_verify_uses_pem_path(tmp_path: Path) -> None:
    ctx = httpx_verify(_self_signed_ca(tmp_path))
    assert isinstance(ctx, ssl.SSLContext)


def test_httpx_verify_missing_file_is_port_error(tmp_path: Path) -> None:
    with pytest.raises(PortError) as exc:
        httpx_verify(tmp_path / "missing.pem")
    assert exc.value.port == ""


def test_log_host_strips_userinfo_and_path() -> None:
    assert log_host("https://user:secret@llm.example:8443/v1") == "llm.example:8443"
    assert log_host("http://127.0.0.1:1234/v1") == "127.0.0.1:1234"
