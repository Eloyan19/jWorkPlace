"""Тесты confidence_harness.py: чистые функции (validate_constraint/majority_vote/parse_verdict)
без сети + классify_with_confidence с замоканным `_call_deepseek` (паттерн
`backend/tests/test_llm_deepseek.py` — мокируем внутренний вызов, не httpx напрямую).
"""
import pytest

import confidence_harness as ch


# --- validate_constraint ---


def test_validate_constraint_accepts_valid():
    result = ch.validate_constraint('{"category": "indexing", "priority": "high"}')
    assert result == {"category": "indexing", "priority": "high"}


def test_validate_constraint_rejects_invalid_json():
    assert ch.validate_constraint("not json") is None


def test_validate_constraint_rejects_extra_keys():
    assert ch.validate_constraint('{"category": "indexing", "priority": "high", "extra": 1}') is None


def test_validate_constraint_rejects_missing_key():
    assert ch.validate_constraint('{"category": "indexing"}') is None


def test_validate_constraint_rejects_unknown_category():
    assert ch.validate_constraint('{"category": "billing", "priority": "high"}') is None


def test_validate_constraint_rejects_unknown_priority():
    assert ch.validate_constraint('{"category": "indexing", "priority": "urgent"}') is None


def test_validate_constraint_rejects_non_object_json():
    assert ch.validate_constraint('["indexing", "high"]') is None


# --- majority_vote ---


def test_majority_vote_full_agreement():
    samples = [{"category": "indexing", "priority": "high"}] * 3
    majority, agreement, breakdown = ch.majority_vote(samples)
    assert majority == {"category": "indexing", "priority": "high"}
    assert agreement == 1.0
    assert breakdown == {"category_agreement": 1.0, "priority_agreement": 1.0}


def test_majority_vote_split_on_priority():
    samples = [
        {"category": "indexing", "priority": "high"},
        {"category": "indexing", "priority": "high"},
        {"category": "indexing", "priority": "low"},
    ]
    majority, agreement, breakdown = ch.majority_vote(samples)
    assert majority == {"category": "indexing", "priority": "high"}
    assert breakdown["category_agreement"] == 1.0
    assert breakdown["priority_agreement"] == pytest.approx(2 / 3, abs=1e-4)
    assert agreement < 1.0


def test_majority_vote_invalid_sample_counts_as_vote():
    samples = [
        {"category": "indexing", "priority": "high"},
        None,  # constraint failed on this repeat
        {"category": "indexing", "priority": "high"},
    ]
    majority, agreement, breakdown = ch.majority_vote(samples)
    assert majority == {"category": "indexing", "priority": "high"}
    assert breakdown["category_agreement"] == pytest.approx(2 / 3, abs=1e-4)


def test_majority_vote_all_invalid_returns_no_majority():
    """Все 3 сэмпла invalid -> "__invalid__" единогласно побеждает голосование (agreement=1.0
    по формуле "доля голосов за победителя"), но majority_vote() трактует победу "__invalid__"
    как отсутствие валидного ответа -> majority is None (защита от передачи мусора в self-check)."""
    majority, agreement, breakdown = ch.majority_vote([None, None, None])
    assert majority is None
    assert agreement == 1.0


def test_majority_vote_weak_plurality_still_returns_an_answer():
    # category расколота на 3 разных валидных значения -> нет большинства, но most_common
    # всё равно возвращает какое-то значение с cnt=1 (agreement=1/3) — majority_vote строит
    # answer, если оба поля дали ЛЮБОГО (пусть слабого) валидного победителя. Такой случай
    # передаётся дальше в self-check — низкий agreement уже сам по себе сигнал неуверенности.
    samples = [
        {"category": "indexing", "priority": "high"},
        {"category": "pr_edits", "priority": "high"},
        {"category": "account", "priority": "high"},
    ]
    majority, agreement, breakdown = ch.majority_vote(samples)
    assert breakdown["category_agreement"] == pytest.approx(1 / 3, abs=1e-4)
    assert breakdown["priority_agreement"] == 1.0
    assert majority is not None  # оба поля дали winner (пусть и слабый) -> answer есть


def test_majority_vote_invalid_plurality_beats_single_valid_vote():
    """1 валидный голос против 2 invalid -> invalid побеждает (2>1) -> majority is None.
    Это единственный путь к no_majority, достижимый из classify_with_confidence (первый сэмпл
    там всегда валиден — constraint проверяется сразу после него)."""
    samples = [{"category": "indexing", "priority": "high"}, None, None]
    majority, agreement, breakdown = ch.majority_vote(samples)
    assert majority is None


# --- parse_verdict ---


def test_parse_verdict_confirm():
    assert ch.parse_verdict('{"verdict": "confirm"}') == "confirm"


def test_parse_verdict_reject():
    assert ch.parse_verdict('{"verdict": "reject"}') == "reject"


def test_parse_verdict_invalid_json_is_reject():
    assert ch.parse_verdict("not json") == "reject"


def test_parse_verdict_unknown_value_is_reject():
    assert ch.parse_verdict('{"verdict": "maybe"}') == "reject"


def test_parse_verdict_missing_key_is_reject():
    assert ch.parse_verdict('{"other": "confirm"}') == "reject"


# --- classify_with_confidence (мок _call_deepseek, сигнатура (content, usage, finish_reason)) ---


def _usage(p=10, c=5):
    return {"prompt_tokens": p, "completion_tokens": c}


@pytest.mark.asyncio
async def test_classify_constraint_violated_stops_at_one_call(monkeypatch):
    """Первый вызов возвращает мусор -> FAIL, дальше НЕ идём (n_llm_calls == 1)."""
    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        return "not valid json", _usage(), "stop"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "FAIL"
    assert result["answer"] is None
    assert result["n_llm_calls"] == 1
    assert result["note"] == "constraint_violated"


@pytest.mark.asyncio
async def test_classify_full_agreement_and_confirm_is_ok(monkeypatch):
    """3/3 согласие + self-check confirm -> OK, всего 4 вызова."""
    calls = {"n": 0}

    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] <= 3:
            return '{"category": "indexing", "priority": "high"}', _usage(), "stop"
        return '{"verdict": "confirm"}', _usage(), "stop"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "OK"
    assert result["answer"] == {"category": "indexing", "priority": "high"}
    assert result["agreement"] == 1.0
    assert result["self_check"] == "confirm"
    assert result["n_llm_calls"] == 4
    assert len(result["call_latencies"]) == 4


@pytest.mark.asyncio
async def test_classify_redundancy_split_is_unsure_without_self_check_call(monkeypatch):
    """Первый сэмпл валиден (проходит constraint), но оба повтора невалидны — invalid-голоса
    (2) перевешивают единственный валидный (1) -> majority is None -> UNSURE, self-check НЕ
    вызывается (нечего проверять)."""
    calls = {"n": 0}
    answers = [
        '{"category": "indexing", "priority": "high"}',
        'not valid json',
        'also not valid json',
    ]

    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        content = answers[calls["n"]]
        calls["n"] += 1
        return content, _usage(), "stop"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "UNSURE"
    assert result["self_check"] is None
    assert result["n_llm_calls"] == 3  # никакого 4-го (self-check) вызова
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_classify_self_check_reject_is_unsure(monkeypatch):
    """Redundancy согласна 3/3, но self-check reject -> UNSURE, не OK."""
    calls = {"n": 0}

    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] <= 3:
            return '{"category": "account", "priority": "low"}', _usage(), "stop"
        return '{"verdict": "reject"}', _usage(), "stop"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "UNSURE"
    assert result["self_check"] == "reject"
    assert result["agreement"] == 1.0  # redundancy сама по себе была согласна


@pytest.mark.asyncio
async def test_classify_first_call_network_error_is_fail(monkeypatch):
    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        raise ch.LlmCallError("DeepSeek API недоступен")

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "FAIL"
    assert result["n_llm_calls"] == 1  # попытка засчитана, хоть и неуспешная
    assert "llm_call_error" in result["note"]


@pytest.mark.asyncio
async def test_classify_self_check_network_error_is_fail_closed_unsure(monkeypatch):
    """Redundancy ок, но self-check вызов падает сетевой ошибкой -> reject (fail-closed) -> UNSURE."""
    calls = {"n": 0}

    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        calls["n"] += 1
        if calls["n"] <= 3:
            return '{"category": "other", "priority": "medium"}', _usage(), "stop"
        raise ch.LlmCallError("DeepSeek API недоступен")

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "UNSURE"
    assert result["self_check"] == "reject"
    assert result["n_llm_calls"] == 4


@pytest.mark.asyncio
async def test_classify_retries_once_on_length_then_succeeds(monkeypatch):
    """Эмпирически найдено реальным прогоном: deepseek-v4-flash иногда тратит max_tokens на
    скрытые reasoning_tokens и возвращает finish_reason='length' с пустым content. Первая
    попытка обрезана -> один retry с удвоенным max_tokens -> успех. Это должно стоить 2 сырых
    вызова (n_llm_calls растёт на 2 за этот сэмпл), но остаётся ОДНИМ сэмплом self-consistency
    (не съедает лишний слот K=3)."""
    raw_calls = {"n": 0}

    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        raw_calls["n"] += 1
        if raw_calls["n"] == 1:
            assert max_tokens == ch._MAX_TOKENS_CLASSIFY
            return "", _usage(c=200), "length"
        if raw_calls["n"] == 2:
            assert max_tokens == ch._MAX_TOKENS_CLASSIFY * 2  # удвоенный бюджет на retry
            return '{"category": "indexing", "priority": "high"}', _usage(), "stop"
        # редундантность (сэмплы 2 и 3) + self-check — без обрезания
        if raw_calls["n"] <= 4:
            return '{"category": "indexing", "priority": "high"}', _usage(), "stop"
        return '{"verdict": "confirm"}', _usage(), "stop"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "OK"
    assert result["n_llm_calls"] == 5  # 2 (retry на первом сэмпле) + 1 + 1 + 1
    assert len(result["call_latencies"]) == 5  # latency учтена по каждой сырой попытке
    assert raw_calls["n"] == 5


@pytest.mark.asyncio
async def test_classify_length_twice_gives_up_after_one_retry(monkeypatch):
    """Обрезано и на первой, и на второй (удвоенной) попытке -> не зацикливаемся, отдаём то,
    что есть (пустой content) -> constraint провален на этом сэмпле -> FAIL для первого сэмпла,
    ровно 2 сырых вызова потрачено."""
    async def fake_call(client, api_key, model, messages, *, temperature, max_tokens):
        return "", _usage(c=200), "length"

    monkeypatch.setattr(ch, "_call_deepseek", fake_call)
    result = await ch.classify_with_confidence(
        "тикет", "system", api_key="fake", model="fake-model", client=object()
    )
    assert result["status"] == "FAIL"
    assert result["n_llm_calls"] == 2
    assert result["note"] == "constraint_violated"


def test_estimate_cost_usd_uses_price_constants():
    cost = ch.estimate_cost_usd(1_000_000, 1_000_000)
    assert cost == pytest.approx(ch.PRICE_PROMPT_USD_PER_MTOK + ch.PRICE_COMPLETION_USD_PER_MTOK)
