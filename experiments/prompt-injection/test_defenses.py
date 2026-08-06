"""Юнит-тесты защитных слоёв (pytest). Секреты синтетические. Запуск:
    ../../backend/.venv/bin/python -m pytest test_defenses.py -q
"""
from defenses import sanitize_input, strip_zero_width, validate_output, wrap_boundary
from payloads import count_zero_width, html_comment, white_on_white, zero_width_split


# --- L1: input sanitization ---

def test_strip_html_comment():
    assert "секрет" not in sanitize_input("видно" + html_comment("секрет инструкция"))


def test_strip_white_on_white():
    out = sanitize_input("норм" + white_on_white("тайная команда"))
    assert "тайная" not in out


def test_strip_zero_width_removes_all():
    poisoned = zero_width_split("ignore")
    assert count_zero_width(poisoned) > 0
    assert count_zero_width(strip_zero_width(poisoned)) == 0
    assert strip_zero_width(poisoned) == "ignore"


def test_zero_width_defeats_naive_keyword_filter():
    # Наивный фильтр ищет "ignore" — zero-width его обходит, sanitize восстанавливает.
    poisoned = zero_width_split("ignore") + " previous"
    assert "ignore" not in poisoned            # фильтр промахнулся
    assert "ignore" in sanitize_input(poisoned)  # после L1 — снова ловится


# --- L2: content boundary markers ---

def test_boundary_has_nonce_and_wraps():
    system, wrapped = wrap_boundary("данные пользователя")
    assert "nonce=" in system and "nonce=" in wrapped
    assert "данные пользователя" in wrapped
    # nonce одинаковый в system и обёртке (иначе граница не сматчится).
    nonce = system.split("nonce=")[1][:16]
    assert nonce in wrapped


# --- L3: output validation ---

def test_validate_blocks_new_url():
    ok, _ = validate_output("Перейдите на http://evil.test", "легитимный текст без ссылок")
    assert ok is False


def test_validate_blocks_new_email():
    ok, _ = validate_output("пишите на mallory@evil.test", "исходный текст")
    assert ok is False


def test_validate_blocks_injection_marker():
    ok, _ = validate_output("PWNED", "обычный документ")
    assert ok is False


def test_validate_allows_clean_answer():
    ok, _ = validate_output("Встреча в четверг в 15:00.", "Напоминаю про встречу в четверг.")
    assert ok is True


def test_validate_allows_url_present_in_source():
    # URL из источника — не «новый», пропускаем (иначе ломаем легитимные ответы).
    ok, _ = validate_output("сайт: https://ok.example", "официальный сайт https://ok.example")
    assert ok is True


def test_residual_plaintext_poison_passes_validation():
    # Документирует ГРАНИЦУ защиты: вредный email в открытом источнике L3 не считает новым.
    src = "обращайтесь на support@evil.test"
    ok, _ = validate_output("Поддержка: support@evil.test", src)
    assert ok is True  # именно поэтому вектор v4 остаётся остаточным
