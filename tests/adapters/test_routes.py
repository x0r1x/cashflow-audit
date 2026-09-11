from __future__ import annotations

import httpx

from cashflow_audit.adapters.routes import join_route, openai_path, rewrite_request
from cashflow_audit.errors import PortError


def test_join_route_appends_path_to_base() -> None:
    assert join_route("http://h:1234/v1", "/chat/completions") == (
        "http://h:1234/v1/chat/completions"
    )


def test_join_route_is_literal_even_if_path_repeats_v1() -> None:
    assert join_route("http://h/v1", "/v1/embeddings") == "http://h/v1/v1/embeddings"


def test_openai_path_blank_is_default() -> None:
    assert openai_path(None, "/chat/completions") == "/chat/completions"
    assert openai_path("  ", "/embeddings") == "/embeddings"


def test_openai_path_rejects_url() -> None:
    try:
        openai_path("http://evil/chat", "/chat/completions")
    except PortError:
        return
    raise AssertionError("expected PortError")


def test_openai_path_rejects_relative() -> None:
    try:
        openai_path("chat/completions", "/chat/completions")
    except PortError:
        return
    raise AssertionError("expected PortError")


def test_rewrite_request_replaces_sdk_suffix() -> None:
    request = httpx.Request("POST", "http://h:1234/v1/chat/completions", json={"m": 1})
    out = rewrite_request(
        request,
        base_url="http://h:1234/v1",
        sdk_suffix="/chat/completions",
        dest_path="/openai/chat",
    )
    assert str(out.url) == "http://h:1234/v1/openai/chat"
    assert out.method == "POST"
    assert b'"m"' in out.content


def test_rewrite_request_noop_when_path_is_default() -> None:
    request = httpx.Request("POST", "http://h:1234/v1/chat/completions")
    out = rewrite_request(
        request,
        base_url="http://h:1234/v1",
        sdk_suffix="/chat/completions",
        dest_path="/chat/completions",
    )
    assert out is request
