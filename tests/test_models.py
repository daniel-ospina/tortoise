"""Model backend tests — build_request, parse_response, _headers, __init__.

No network calls.   .venv/bin/python tests/test_models.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.models import OpenAICompatModel, OllamaModel  # noqa: E402, I001, RUF100

# ---------------------------------------------------------------------------
# OpenAICompatModel
# ---------------------------------------------------------------------------


def test_openai_init_defaults():
    m = OpenAICompatModel(id="deepseek-chat", base_url="https://api.deepseek.com")
    assert m.id == "deepseek-chat"
    assert m.base_url == "https://api.deepseek.com"
    assert m.api_key_env == "OPENAI_API_KEY"
    assert m.temperature == 0.0
    assert m.timeout == 60
    print("PASS test_openai_init_defaults")


def test_openai_init_custom():
    m = OpenAICompatModel(id="gemini-flash", base_url="https://generativelanguage.googleapis.com/v1beta/",
                          api_key_env="GEMINI_API_KEY", temperature=0.7, timeout=120)
    assert m.id == "gemini-flash"
    assert m.api_key_env == "GEMINI_API_KEY"
    assert m.temperature == 0.7
    assert m.timeout == 120
    print("PASS test_openai_init_custom")


def test_openai_base_url_strips_trailing_slash():
    m = OpenAICompatModel(id="x", base_url="http://localhost:8080/v1//")
    assert m.base_url == "http://localhost:8080/v1"
    print("PASS test_openai_base_url_strips_trailing_slash")


def test_openai_build_request():
    m = OpenAICompatModel(id="deepseek-chat", base_url="https://api.deepseek.com",
                          temperature=0.3)
    req = m.build_request(system="You are helpful.", user="Hello!")
    assert req["model"] == "deepseek-chat"
    assert req["temperature"] == 0.3
    assert req["response_format"] == {"type": "json_object"}
    msgs = req["messages"]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "You are helpful."}
    assert msgs[1] == {"role": "user", "content": "Hello!"}
    print("PASS test_openai_build_request")


def test_openai_parse_response():
    data = {"choices": [{"message": {"content": "{\"x\": 1}"}}]}
    assert OpenAICompatModel.parse_response(data) == "{\"x\": 1}"
    print("PASS test_openai_parse_response")


def test_openai_headers_no_key():
    m = OpenAICompatModel(id="x", base_url="http://localhost", api_key_env=None)
    h = m._headers()
    assert h == {"Content-Type": "application/json"}
    print("PASS test_openai_headers_no_key")


def test_openai_headers_missing_env():
    m = OpenAICompatModel(id="deepseek-chat", base_url="http://localhost",
                          api_key_env="OPENAI_API_KEY")
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        err = None
        try:
            m._headers()
        except RuntimeError as e:
            err = str(e)
        assert err is not None
        assert "OPENAI_API_KEY" in err
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved
    print("PASS test_openai_headers_missing_env")


def test_openai_headers_present():
    m = OpenAICompatModel(id="x", base_url="http://localhost",
                          api_key_env="TORT_TEST_KEY")
    os.environ["TORT_TEST_KEY"] = "sk-fake"
    try:
        h = m._headers()
        assert h["Content-Type"] == "application/json"
        assert h["Authorization"] == "Bearer sk-fake"
    finally:
        del os.environ["TORT_TEST_KEY"]
    print("PASS test_openai_headers_present")


# ---------------------------------------------------------------------------
# OllamaModel
# ---------------------------------------------------------------------------


def test_ollama_init_defaults():
    m = OllamaModel(id="qwen3:0.6b")
    assert m.id == "qwen3:0.6b"
    assert m.base_url == "http://localhost:11434"
    assert m.timeout == 300
    assert m.think is False
    print("PASS test_ollama_init_defaults")


def test_ollama_init_custom():
    m = OllamaModel(id="qwen3:14b", base_url="http://192.168.1.50:11434/",
                    timeout=600, think=True)
    assert m.id == "qwen3:14b"
    assert m.base_url == "http://192.168.1.50:11434"
    assert m.timeout == 600
    assert m.think is True
    print("PASS test_ollama_init_custom")


def test_ollama_base_url_strips_trailing_slash():
    m = OllamaModel(id="x", base_url="http://localhost:11434///")
    assert m.base_url == "http://localhost:11434"
    print("PASS test_ollama_base_url_strips_trailing_slash")


def test_ollama_build_request_no_think():
    m = OllamaModel(id="qwen3:0.6b")
    req = m.build_request(system="Be concise.", user="What is 2+2?")
    assert req["model"] == "qwen3:0.6b"
    assert req["think"] is False
    assert req["stream"] is False
    assert req["format"] == "json"
    msgs = req["messages"]
    assert len(msgs) == 2
    assert msgs[0] == {"role": "system", "content": "Be concise."}
    assert msgs[1] == {"role": "user", "content": "What is 2+2?"}
    print("PASS test_ollama_build_request_no_think")


def test_ollama_build_request_think():
    m = OllamaModel(id="qwen3:14b", think=True)
    req = m.build_request(system="Reason carefully.", user="Explain gravity.")
    assert req["model"] == "qwen3:14b"
    assert req["think"] is True
    assert req["stream"] is False
    assert req["format"] == "json"
    assert req["messages"][0]["role"] == "system"
    assert req["messages"][1]["role"] == "user"
    print("PASS test_ollama_build_request_think")


def test_ollama_parse_response():
    data = {"message": {"content": "the answer is 4"}}
    assert OllamaModel.parse_response(data) == "the answer is 4"
    print("PASS test_ollama_parse_response")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall model tests passed")


if __name__ == "__main__":
    _run_all()
