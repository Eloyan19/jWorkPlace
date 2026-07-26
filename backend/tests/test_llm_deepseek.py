"""Тесты llm/deepseek: обработка ошибок, retry на 'length', парсинг JSON.
Мокируем _request (внутренний метод), не httpx напрямую. Фейк API key (синтетический).
"""
import pytest

from app.config import Settings
from app.llm.deepseek import DeepSeekLlmService, LlmError, get_llm


@pytest.fixture
def settings_with_key(monkeypatch):
    """Settings с фейковым (синтетическим) API-ключом."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-fakekey1234567890abcdef")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    return Settings()


@pytest.fixture
def settings_no_key(monkeypatch):
    """Settings без API-ключа."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    # Сбросить кэш get_settings
    from app.config import get_settings
    get_settings.cache_clear()
    return Settings()


@pytest.mark.asyncio
async def test_chat_no_api_key(settings_no_key):
    """chat raises LlmError если нет API-ключа."""
    llm = DeepSeekLlmService(settings_no_key)
    with pytest.raises(LlmError, match="не задан"):
        await llm.chat([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_chat_success(settings_with_key, monkeypatch):
    """chat возвращает успешный ответ на завершение 'stop'."""
    async def mock_request(*args, **kwargs):
        return (
            {"content": "Hello, world!"},
            "stop"
        )

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)
    result = await llm.chat([{"role": "user", "content": "Hello"}])
    assert result == "Hello, world!"


@pytest.mark.asyncio
async def test_chat_retry_on_length_succeeds(settings_with_key, monkeypatch):
    """chat retries и успешно завершает, если второй вызов имеет finish_reason='stop'."""
    call_count = 0

    async def mock_request(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        # Первый вызов — обрезано, второй — успех
        if call_count == 1:
            return ({"content": "Truncated"}, "length")
        else:
            return ({"content": "Complete answer"}, "stop")

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)
    result = await llm.chat([{"role": "user", "content": "Hello"}])

    assert result == "Complete answer"
    assert call_count == 2


@pytest.mark.asyncio
async def test_chat_retry_twice_on_length_fails(settings_with_key, monkeypatch):
    """chat raises LlmError если оба вызова имеют finish_reason='length'."""
    async def mock_request(*args, **kwargs):
        return ({"content": "Truncated"}, "length")

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)

    with pytest.raises(LlmError, match="обрезан.*дважды"):
        await llm.chat([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_chat_raw_no_api_key(settings_no_key):
    """chat_raw raises LlmError если нет API-ключа."""
    llm = DeepSeekLlmService(settings_no_key)
    with pytest.raises(LlmError, match="не задан"):
        await llm.chat_raw([{"role": "user", "content": "Hello"}])


@pytest.mark.asyncio
async def test_chat_raw_returns_dict(settings_with_key, monkeypatch):
    """chat_raw возвращает dict с content, tool_calls, finish_reason."""
    async def mock_request(*args, **kwargs):
        return (
            {
                "content": "Hello",
                "tool_calls": None,
            },
            "stop"
        )

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)
    result = await llm.chat_raw([{"role": "user", "content": "Hello"}])

    assert isinstance(result, dict)
    assert "content" in result
    assert "tool_calls" in result
    assert "finish_reason" in result
    assert result["content"] == "Hello"


@pytest.mark.asyncio
async def test_complete_wraps_chat(settings_with_key, monkeypatch):
    """complete() — это удобный wrapper вокруг chat()."""
    async def mock_request(*args, **kwargs):
        return ({"content": "Completion result"}, "stop")

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)
    result = await llm.complete("What is 2+2?")

    assert result == "Completion result"


@pytest.mark.asyncio
async def test_chat_empty_content_returns_empty_string(settings_with_key, monkeypatch):
    """chat возвращает пустую строку если content=None."""
    async def mock_request(*args, **kwargs):
        return ({"content": None}, "stop")

    monkeypatch.setattr(DeepSeekLlmService, "_request", mock_request)
    llm = DeepSeekLlmService(settings_with_key)
    result = await llm.chat([{"role": "user", "content": "Hello"}])

    assert result == ""


def test_get_llm_deepseek(settings_with_key, monkeypatch):
    """get_llm(deepseek) возвращает DeepSeekLlmService."""
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    settings = Settings()
    llm = get_llm(settings)
    assert isinstance(llm, DeepSeekLlmService)


def test_get_llm_unknown_provider(settings_with_key, monkeypatch):
    """get_llm(unknown) raises ValueError."""
    monkeypatch.setenv("LLM_PROVIDER", "unknown_provider")
    settings = Settings()
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        get_llm(settings)
