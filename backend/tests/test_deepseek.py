"""Тесты DeepSeek-адаптера (app/llm/deepseek.py): без сети — httpx.AsyncClient замокан.

Проверяем контракт из CLAUDE.md/плана: пустой ключ -> ошибка без сетевого вызова; finish_reason
"length" -> один retry с большим max_tokens, затем LlmError; ключ и детали httpx-исключения
никогда не попадают в текст LlmError (что могло бы утечь в лог/ответ клиенту).
"""
import asyncio

import httpx
import pytest

from app.config import Settings
from app.llm import deepseek as ds


def _settings(api_key: str = "test-key") -> Settings:
    return Settings(deepseek_api_key=api_key)


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", ds.DEEPSEEK_API_URL)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._payload


class _FakeAsyncClient:
    """Заменяет httpx.AsyncClient(timeout=...): та же контекстная форма `async with`."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls = 0
        self.last_headers: dict | None = None

    def __call__(self, timeout=None):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json, headers):
        self.last_headers = headers
        resp = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return resp


def test_empty_api_key_raises_without_network_call(monkeypatch):
    svc = ds.DeepSeekLlmService(_settings(api_key=""))

    def _must_not_be_called(*a, **k):
        raise AssertionError("сетевой вызов не должен произойти без ключа")

    monkeypatch.setattr(ds.httpx, "AsyncClient", _must_not_be_called)

    with pytest.raises(ds.LlmError):
        asyncio.run(svc.chat([{"role": "user", "content": "hi"}]))


def test_key_sent_only_in_authorization_header(monkeypatch):
    payload = {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
    fake = _FakeAsyncClient([_FakeResponse(payload)])
    monkeypatch.setattr(ds.httpx, "AsyncClient", fake)

    svc = ds.DeepSeekLlmService(_settings(api_key="topsecretkey123"))
    result = asyncio.run(svc.chat([{"role": "user", "content": "hi"}]))

    assert result == "ok"
    assert fake.last_headers == {"Authorization": "Bearer topsecretkey123"}


def test_length_finish_reason_retries_once_then_raises(monkeypatch):
    payload = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
    fake = _FakeAsyncClient([_FakeResponse(payload), _FakeResponse(payload)])
    monkeypatch.setattr(ds.httpx, "AsyncClient", fake)

    svc = ds.DeepSeekLlmService(_settings())
    with pytest.raises(ds.LlmError):
        asyncio.run(svc.chat([{"role": "user", "content": "hi"}], max_tokens=100))

    assert fake.calls == 2  # 1 первичный запрос + 1 retry с увеличенным max_tokens


def test_length_finish_reason_succeeds_on_retry(monkeypatch):
    truncated = {"choices": [{"message": {"content": "{"}, "finish_reason": "length"}]}
    complete = {"choices": [{"message": {"content": '{"answer": "ok"}'}, "finish_reason": "stop"}]}
    fake = _FakeAsyncClient([_FakeResponse(truncated), _FakeResponse(complete)])
    monkeypatch.setattr(ds.httpx, "AsyncClient", fake)

    svc = ds.DeepSeekLlmService(_settings())
    result = asyncio.run(svc.chat([{"role": "user", "content": "hi"}]))

    assert result == '{"answer": "ok"}'
    assert fake.calls == 2


def test_http_error_never_leaks_key_or_url(monkeypatch):
    fake = _FakeAsyncClient([_FakeResponse({}, status_code=500)])
    monkeypatch.setattr(ds.httpx, "AsyncClient", fake)

    svc = ds.DeepSeekLlmService(_settings(api_key="topsecretkey123"))
    with pytest.raises(ds.LlmError) as exc_info:
        asyncio.run(svc.chat([{"role": "user", "content": "hi"}]))

    message = str(exc_info.value)
    assert "topsecretkey123" not in message
    assert ds.DEEPSEEK_API_URL not in message
