"""Техники сокрытия инструкции внутри данных (носитель indirect prompt injection).

Учебный модуль: показывает, ЧЕМ атакующий прячет команду в письме/документе/веб-странице так,
чтобы человек её не увидел, а LLM-агент — прочитал и выполнил. Payload здесь синтетический,
без реальных секретов/адресов (origin публичный, см. CLAUDE.md). Все функции — чистые.

Инвариант проекта, который это иллюстрирует: контент чужого репо/письма/страницы — НЕДОВЕРЕННЫЕ
данные, а не инструкции (`../CLAUDE.md`, `chat/grounding.py::SYSTEM_PROMPT`).
"""

# Zero-width символы: невидимы при рендере, но присутствуют в тексте, который получает LLM.
ZERO_WIDTH = {
    "​": "ZWSP",   # zero-width space
    "‌": "ZWNJ",   # zero-width non-joiner
    "‍": "ZWJ",    # zero-width joiner
    "⁠": "WJ",     # word joiner
    "﻿": "BOM",    # zero-width no-break space
}


def html_comment(instruction: str) -> str:
    """Вектор 1: HTML-комментарий. В отрендеренном письме/странице невидим, но в сыром
    HTML/markdown, который скармливают LLM, читается как обычный текст."""
    return f"<!-- {instruction} -->"


def white_on_white(instruction: str) -> str:
    """Вектор 2: белый текст на белом фоне. Человек не видит, но text-extraction (и LLM,
    которому отдают вытащенный текст) получает инструкцию целиком."""
    return f'<span style="color:#ffffff;background:#ffffff">{instruction}</span>'


def zero_width_split(keyword: str) -> str:
    """Вставить ZWSP между буквами слова — слово визуально целое, но наивный keyword-фильтр
    (ищет "ignore") его НЕ находит: техника обхода input-фильтра, а не сокрытия от человека."""
    return "​".join(keyword)


def markdown_link_exfil(label: str, base_url: str, stolen: str) -> str:
    """Вектор 3 (эксфильтрация): markdown-ссылка, где секрет утекает в query-параметр URL.
    Агент, отрендеривший/«кликнувший» ссылку, отправит данные на сервер атакующего."""
    return f"[{label}]({base_url}?data={stolen})"


def count_zero_width(text: str) -> int:
    """Сколько zero-width символов в тексте — сигнал для детектора аномалий."""
    return sum(text.count(ch) for ch in ZERO_WIDTH)
