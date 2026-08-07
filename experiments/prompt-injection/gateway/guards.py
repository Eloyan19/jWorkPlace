"""Input/Output Guard гейтвея: детекция секретов, маскирование, проверка ответа модели.

Всё — чистые функции без сети и без состояния, чтобы тестировать напрямую (как defenses.py трека).
Детектор построен на span-claiming: паттерны идут в порядке приоритета, каждый «застолбляет» свой
участок текста, чтобы номер карты не переловил телефон и наоборот.

Три канала обнаружения секрета во входе:
  plain  — секрет лежит открытым текстом (regex по сырому промпту);
  base64 — секрет спрятан в base64-блобе (декодируем и пере-сканируем);
  split  — секрет разбит на части конкатенацией ("sk-" + "proj-abc"): дефрагментируем и сканируем.

Output Guard ловит четыре класса утечек в ответе модели:
  секрет (модель «сгаллюцинировала»/echo ключа), утечку system-prompt (canary),
  подозрительный URL (raw-IP / не-https / userinfo@), подозрительную команду (rm -rf, curl|sh, …).
"""
from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

# zero-width / невидимые: прячут текст от человека И рвут ключевые слова в обход фильтров
# (U+200B ZWSP не является \s в Python re — defrag его не убирает). Чистим ДО любых проверок.
_ZERO_WIDTH = "​‌‍⁠﻿‎‏­"


def normalize(text: str) -> str:
    """Канонизация недоверенного входа перед сканом: NFKC (fullwidth/составные → базовые) +
    удаление zero-width. Для input-пути gateway нормализует ПРОМПТ целиком и дальше работает с
    нормализованной версией — так span'ы находок совпадают с тем, что маскируем и шлём в LLM."""
    text = unicodedata.normalize("NFKC", text)
    for ch in _ZERO_WIDTH:
        text = text.replace(ch, "")
    return text

# --------------------------------------------------------------------------- #
#  Модель находки
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    """Одна находка. `raw` — только для внутренней замены при маскировании; НАРУЖУ (лог/ответ)
    отдаём лишь `preview` (обрезанный + звёздочки), чтобы сам секрет не утёк в аудит."""

    type: str
    preview: str
    span: tuple[int, int] | None  # позиция в исходном тексте; None для split (позиция потеряна)
    channel: str  # plain | base64 | split
    severity: str  # high | medium
    label: str  # чем заменять при маскировании, напр. [REDACTED_API_KEY]
    raw: str  # сырое совпадение — НЕ логировать, только для inline-замены

    def public(self) -> dict:
        """Безопасное для логов/ответа представление (без сырого секрета)."""
        return {
            "type": self.type,
            "preview": self.preview,
            "channel": self.channel,
            "severity": self.severity,
        }


# --------------------------------------------------------------------------- #
#  Паттерны секретов (порядок = приоритет claim)
# --------------------------------------------------------------------------- #

_EMAIL_RX = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Карта: 13–19 цифр, разрешаем пробел/дефис как разделитель; валидируем Luhn отдельно.
_CARD_RX = re.compile(r"\b(?:\d[ -]?){13,19}\b")
# Телефон: международный (+7 ...) или US-стиль со скобками/дефисами. Требуем разделитель,
# чтобы не хватать произвольные 10-значные ID.
_PHONE_RX = re.compile(
    r"(?:\+\d[\d .()\-]{6,15}\d)"
    r"|(?:\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4})"
)

# API-ключи. `-`/`_` входят в класс → `sk-proj-abc123` матчится целиком одним паттерном.
# Границу задаём lookbehind `(?<![A-Za-z0-9])`, а НЕ `\b`: после дефрагментации split-секрета
# перед ключом может встать кириллица (`ключsk-…`), и `\b` между двумя Unicode-word-символами
# не срабатывает — ключ бы утёк. Lookbehind запрещает лишь середину ASCII-токена.
_KEY_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("OPENAI_API_KEY", re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{6,}"), "[REDACTED_API_KEY]"),
    ("GITHUB_TOKEN", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{8,}"), "[REDACTED_GITHUB_TOKEN]"),
    ("AWS_ACCESS_KEY", re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"), "[REDACTED_AWS_KEY]"),
]

# Полный набор для plain-скана: ключи + PII. Порядок важен (card до phone).
_PLAIN_PATTERNS: list[tuple[str, re.Pattern, str, str]] = [
    *[(t, rx, lbl, "high") for t, rx, lbl in _KEY_PATTERNS],
    ("EMAIL", _EMAIL_RX, "[REDACTED_EMAIL]", "medium"),
    ("CREDIT_CARD", _CARD_RX, "[REDACTED_CARD]", "high"),
    ("PHONE", _PHONE_RX, "[REDACTED_PHONE]", "medium"),
]


def _luhn_ok(digits: str) -> bool:
    """Проверка Луна — отсекает случайные 13–19-значные числа, не являющиеся картой."""
    if not 13 <= len(digits) <= 19:
        return False
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _preview(ptype: str, raw: str) -> str:
    """Безопасный для логов огрызок: показать «форму», не значение."""
    if ptype == "EMAIL":
        local, _, domain = raw.partition("@")
        head = local[:2] if len(local) > 2 else local[:1]
        return f"{head}***@{domain}"
    compact = re.sub(r"[ -]", "", raw)
    return f"{compact[:4]}***({len(compact)} симв.)"


# --------------------------------------------------------------------------- #
#  Сканеры
# --------------------------------------------------------------------------- #


def _scan_plain(text: str) -> list[Finding]:
    """Секреты открытым текстом. Span-claiming: перекрывающиеся совпадения не дублируем."""
    findings: list[Finding] = []
    claimed: list[tuple[int, int]] = []

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= cs or s >= ce) for cs, ce in claimed)

    for ptype, rx, label, sev in _PLAIN_PATTERNS:
        for m in rx.finditer(text):
            raw = m.group(0)
            s, e = m.span()
            if ptype == "CREDIT_CARD" and not _luhn_ok(re.sub(r"\D", "", raw)):
                continue
            if overlaps(s, e):
                continue
            claimed.append((s, e))
            findings.append(Finding(ptype, _preview(ptype, raw), (s, e), "plain", sev, label, raw))
    findings.sort(key=lambda f: f.span[0])
    return findings


# Кандидат base64: стандартный (+/) ИЛИ urlsafe (-_) алфавит; whitespace внутри чистим при декоде.
_B64_BLOB_RX = re.compile(r"[A-Za-z0-9+/=]{16,}|[A-Za-z0-9_-]{16,}={0,2}")


def _b64_decode(blob: str) -> str | None:
    """Попытка декодировать кандидат и в стандартном, и в urlsafe алфавите. None при неудаче."""
    cleaned = re.sub(r"\s", "", blob).rstrip("=")
    for candidate in (cleaned, cleaned.replace("-", "+").replace("_", "/")):
        try:
            decoded = base64.b64decode(candidate + "=" * (-len(candidate) % 4), validate=True)
            return decoded.decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError):
            continue
    return None


def _scan_base64(text: str, _depth: int = 0) -> list[Finding]:
    """Секрет в base64. Декодируем и пере-сканируем; при вложенном base64 (двойное кодирование)
    рекурсируем до глубины 3. Принимаем стандартный и urlsafe алфавит."""
    if _depth > 2:
        return []
    out: list[Finding] = []
    for m in _B64_BLOB_RX.finditer(text):
        payload = _b64_decode(m.group(0))
        if payload is None:
            continue
        inner = _scan_plain(payload) or _scan_base64(payload, _depth + 1)
        if inner:
            lbl = inner[0].label
            out.append(
                Finding("BASE64_SECRET", f"base64→{inner[0].type}", m.span(), "base64", "high", lbl, m.group(0))
            )
    return out


_DEFRAG_RX = re.compile(r"[\"'+\s]+")


def _scan_split(text: str, seen_raw: set[str]) -> list[Finding]:
    """Секрет, разбитый конкатенацией: 'sk-' + 'proj-abc'. Убираем кавычки/плюсы/пробелы и
    сканируем ТОЛЬКО ключевыми паттернами (PII не дефрагментируем — иначе ложные срабатывания
    от склейки соседних чисел). Сообщаем, только если в сыром тексте такого ключа ещё не было."""
    defrag = _DEFRAG_RX.sub("", text)
    out: list[Finding] = []
    for ptype, rx, label in _KEY_PATTERNS:
        for m in rx.finditer(defrag):
            raw = m.group(0)
            if raw in text or raw in seen_raw:
                continue  # это уже поймано plain-сканом, не дубль
            seen_raw.add(raw)
            out.append(Finding(ptype, _preview(ptype, raw), None, "split", "high", label, raw))
    return out


def scan_deep(text: str) -> list[Finding]:
    """Полный вход-скан: plain + base64 + split. Возвращает все находки (возможны разные каналы)."""
    plain = _scan_plain(text)
    seen = {f.raw for f in plain}
    return plain + _scan_base64(text) + _scan_split(text, seen)


# --------------------------------------------------------------------------- #
#  Маскирование
# --------------------------------------------------------------------------- #


def mask_text(text: str, findings: list[Finding]) -> str:
    """Заменить секреты с известной позицией (plain/base64) на их label. Замену делаем
    справа-налево, чтобы не сдвинуть ещё не обработанные span'ы. split-находки (span=None)
    здесь не трогаем — их безопаснее блокировать (см. gateway.py)."""
    spans = sorted(
        [(f.span[0], f.span[1], f.label) for f in findings if f.span is not None],
        key=lambda x: x[0],
        reverse=True,
    )
    prev_start = len(text) + 1  # применяем справа-налево; пропускаем перекрытие с уже заменённым
    for s, e, label in spans:
        if e > prev_start:
            continue
        text = text[:s] + label + text[e:]
        prev_start = s
    return text


# --------------------------------------------------------------------------- #
#  Output Guard
# --------------------------------------------------------------------------- #

_URL_RX = re.compile(r"https?://[^\s)>\]\"']+", re.IGNORECASE)
_IP_HOST_RX = re.compile(r"^https?://(?:\d{1,3}\.){3}\d{1,3}", re.IGNORECASE)
_USERINFO_RX = re.compile(r"^https?://[^/\s]*@", re.IGNORECASE)

# Опасные команды/примитивы, которых не должно быть в ответе ассистента по коду.
_CMD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("rm -rf", re.compile(r"\brm\s+-[a-z]*[rf][a-z]*", re.I)),  # -rf, -fr, -rfv, -Rf …
    ("pipe-to-shell", re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+)?(?:ba)?sh\b", re.I)),
    ("reverse-shell", re.compile(r"(?:nc\s+-[a-z]*[le]|bash\s+-i|sh\s+-i|/dev/tcp/)", re.I)),
    ("code-exec", re.compile(r"\b(?:os\.system|subprocess\.(?:run|call|Popen)|eval\(|exec\()", re.I)),
    ("chmod-777", re.compile(r"\bchmod\s+(?:-R\s+)?777\b")),
    ("fork-bomb", re.compile(r":\(\)\s*\{\s*:\|:")),
]

_SYSPROMPT_PHRASE_RX = re.compile(r"\bsystem prompt\b|\bсистемн\w* промпт", re.I)


def output_guard(
    answer: str,
    *,
    system_canary: str | None = None,
    url_allowlist: tuple[str, ...] = (),
) -> list[Finding]:
    """Проверка ответа модели ПЕРЕД отдачей пользователю. Возвращает список находок (пусто = чисто).
    Политику (блокировать/санитизировать) решает вызывающий по severity."""
    answer = normalize(answer)  # модель тоже может отдать zero-width/fullwidth обфускацию
    out: list[Finding] = []

    # 1. Секреты в ответе (галлюцинированные/эхо ключей, PII).
    for f in scan_deep(answer):
        out.append(Finding("OUTPUT_" + f.type, f.preview, f.span, f.channel, "high", f.label, f.raw))

    # 2. Утечка system-prompt: canary-маркер или прямая фраза «system prompt».
    if system_canary and system_canary in answer:
        out.append(Finding("SYSTEM_PROMPT_LEAK", "canary в ответе", None, "leak", "high", "", ""))
    elif _SYSPROMPT_PHRASE_RX.search(answer):
        out.append(Finding("SYSTEM_PROMPT_MENTION", "упоминание system prompt", None, "phrase", "medium", "", ""))

    # 3. Подозрительные URL.
    for m in _URL_RX.finditer(answer):
        url = m.group(0)
        host = re.sub(r"^https?://", "", url).split("/")[0].split("@")[-1].split(":")[0].lower()
        if url_allowlist and host in url_allowlist:
            continue
        if _IP_HOST_RX.match(url):
            out.append(Finding("SUSPICIOUS_URL", f"raw-IP: {url[:40]}", m.span(), "url", "high", "", url))
        elif _USERINFO_RX.match(url):
            out.append(Finding("SUSPICIOUS_URL", f"userinfo@: {url[:40]}", m.span(), "url", "high", "", url))
        elif url.lower().startswith("http://"):
            out.append(Finding("SUSPICIOUS_URL", f"не-https: {url[:40]}", m.span(), "url", "medium", "", url))
        elif url_allowlist:  # включён allowlist, а хост не в нём
            out.append(Finding("SUSPICIOUS_URL", f"вне allowlist: {host}", m.span(), "url", "medium", "", url))

    # 4. Подозрительные команды.
    for name, rx in _CMD_PATTERNS:
        if rx.search(answer):
            out.append(Finding("SUSPICIOUS_COMMAND", name, None, "cmd", "high", "", ""))

    return out
