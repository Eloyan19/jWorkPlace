"""Юнит-тесты чистой логики routing (эвристики эскалации, выбор модели, честный учёт стоимости).
Без сети: сигналы синтетические. Реальный DeepSeek дёргает только run_routing.py."""
import router as rt


# --- фабрики синтетических сигналов ---


def _small(answer=None, *, content='{"category": "other", "priority": "low"}',
           truncated=False, finish_reason="stop", prompt=100, completion=20, n_calls=1):
    return {
        "answer": answer if answer is not None else {"category": "other", "priority": "low"},
        "content": content,
        "truncated": truncated,
        "finish_reason": finish_reason,
        "tokens": {"prompt": prompt, "completion": completion},
        "n_calls": n_calls,
    }


def _small_bad(finish_reason="error"):
    return {"answer": None, "content": "", "truncated": False, "finish_reason": finish_reason,
            "tokens": {"prompt": 0, "completion": 0}, "n_calls": 1}


def _conf(status="OK", agreement=1.0, answer=None, prompt=300, completion=60, n_llm_calls=4):
    return {
        "answer": answer if answer is not None else {"category": "indexing", "priority": "high"},
        "status": status,
        "agreement": agreement,
        "self_check": "confirm",
        "tokens": {"prompt": prompt, "completion": completion, "total": prompt + completion},
        "n_llm_calls": n_llm_calls,
    }


def _big(answer=None, prompt=110, completion=25, n_calls=1):
    return {
        "answer": answer if answer is not None else {"category": "pr_edits", "priority": "medium"},
        "content": "x", "truncated": False, "finish_reason": "stop",
        "tokens": {"prompt": prompt, "completion": completion}, "n_calls": n_calls,
    }


def _sig(small=None, conf=None, big=None):
    return {"small": small or _small(), "confidence": conf or _conf(), "big": big or _big()}


# --- policy_length: смотрит только на вырожденность вывода ---


def test_length_stays_on_valid_nonempty():
    assert rt.policy_length(_sig()) == (False, "length_ok")


def test_length_escalates_on_truncated():
    esc, reason = rt.policy_length(_sig(small=_small(truncated=True, finish_reason="length")))
    assert esc and reason == "truncated"


def test_length_escalates_on_empty_content():
    esc, reason = rt.policy_length(_sig(small=_small(content="")))
    assert esc and reason == "empty_output"


def test_length_escalates_on_call_error():
    esc, reason = rt.policy_length(_sig(small=_small_bad()))
    assert esc and reason == "small_error"


def test_length_ignores_semantics():
    # схема валидна и непуста, но ответ семантически неверен — length это НЕ ловит (в этом её слепота)
    assert rt.policy_length(_sig(small=_small(answer={"category": "account", "priority": "low"})))[0] is False


# --- policy_constraint: смотрит на валидность схемы ---


def test_constraint_stays_on_valid_schema():
    assert rt.policy_constraint(_sig()) == (False, "schema_ok")


def test_constraint_escalates_on_schema_violation():
    esc, reason = rt.policy_constraint(_sig(small={**_small(), "answer": None}))
    assert esc and reason == "schema_violation"


# --- policy_confidence: статус + порог agreement ---


def test_confidence_stays_on_ok_full_agreement():
    assert rt.policy_confidence(_sig(conf=_conf("OK", 1.0))) == (False, "confident_ok")


def test_confidence_escalates_on_unsure():
    esc, reason = rt.policy_confidence(_sig(conf=_conf("UNSURE", 0.83)))
    assert esc and reason == "confidence_unsure"


def test_confidence_escalates_on_fail():
    esc, reason = rt.policy_confidence(_sig(conf=_conf("FAIL", 0.0)))
    assert esc and reason == "confidence_fail"


def test_confidence_agreement_threshold_knob(monkeypatch):
    # при пороге 1.0 даже OK-статус с agreement<1.0 гипотетически эскалировал бы по low_agreement,
    # но статус UNSURE перехватывает раньше; проверяем именно ветку порога на OK+неполном согласии
    sig = _sig(conf=_conf("OK", 0.83))
    monkeypatch.setattr(rt, "AGREEMENT_ESCALATE_THRESHOLD", 1.0)
    esc, reason = rt.policy_confidence(sig)
    assert esc and reason.startswith("low_agreement")
    # понизили порог ниже фактического agreement → та же уверенность больше НЕ эскалирует
    monkeypatch.setattr(rt, "AGREEMENT_ESCALATE_THRESHOLD", 0.5)
    assert rt.policy_confidence(sig)[0] is False


# --- route: выбор модели, ответа и честный учёт стоимости ---


def test_route_stays_uses_small_answer_and_only_probe_cost():
    sig = _sig(small=_small(answer={"category": "other", "priority": "low"}))
    res = rt.route("constraint", sig)
    assert res["escalated"] is False
    assert res["model_used"] == rt.SMALL_MODEL
    assert res["answer"] == {"category": "other", "priority": "low"}
    # стоимость = только 1 проба flash, без pro
    assert res["cost_usd"] == round(rt.model_cost(rt.SMALL_MODEL, sig["small"]["tokens"]), 6)
    assert res["n_llm_calls"] == 1


def test_route_escalates_uses_big_answer_and_adds_big_cost():
    sig = _sig(small=_small_bad(), big=_big(answer={"category": "pr_edits", "priority": "high"}))
    res = rt.route("length", sig)  # small_error → эскалация
    assert res["escalated"] is True
    assert res["model_used"] == rt.BIG_MODEL
    assert res["answer"] == {"category": "pr_edits", "priority": "high"}
    expected = round(
        rt.model_cost(rt.SMALL_MODEL, sig["small"]["tokens"])
        + rt.model_cost(rt.BIG_MODEL, sig["big"]["tokens"]),
        6,
    )
    assert res["cost_usd"] == expected
    assert res["n_llm_calls"] == sig["small"]["n_calls"] + sig["big"]["n_calls"]


def test_route_confidence_probe_costs_full_pipeline():
    # confidence-политика без эскалации всё равно платит за весь свой пайплайн (n_llm_calls), а не за 1
    sig = _sig(conf=_conf("OK", 1.0, n_llm_calls=4))
    res = rt.route("confidence", sig)
    assert res["escalated"] is False
    assert res["n_llm_calls"] == 4
    assert res["cost_usd"] == round(rt.model_cost(rt.SMALL_MODEL, sig["confidence"]["tokens"]), 6)


def test_route_confidence_uses_majority_answer_when_staying():
    sig = _sig(conf=_conf("OK", 1.0, answer={"category": "indexing", "priority": "low"}))
    res = rt.route("confidence", sig)
    assert res["answer"] == {"category": "indexing", "priority": "low"}


# --- model_cost ---


def test_model_cost_pro_dearer_than_flash_same_tokens():
    tokens = {"prompt": 1000, "completion": 500}
    assert rt.model_cost(rt.BIG_MODEL, tokens) > rt.model_cost(rt.SMALL_MODEL, tokens)


def test_model_cost_handles_missing_keys():
    assert rt.model_cost(rt.SMALL_MODEL, {}) == 0.0
