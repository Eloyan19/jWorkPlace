"""Токенизация кода для лексического канала (FTS5) hybrid search.

Дизайн rag-indexing-engineer: `unicode61` сам не бьёт camelCase и режет snake по `_`, поэтому
расщепляем токены в Python ДО подачи в FTS — одинаково для документа и запроса. Ключевой приём:
для составного идентификатора сохраняем И слитную форму, И подслова
(`getUserById → {getuserbyid, get, user, by, id}`), чтобы запрос в любом стиле
(`get_user_by_id`, `getUserById`, `user by id`) матчил один и тот же чанк.
"""
import re

# Разбиение идентификатора на слова: аббревиатура перед словом (HTMLParser→HTML|Parser),
# слово с опц. заглавной (getUser→get|User), хвост заглавных, число.
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+")
# Разделители «сырых» токенов — всё, кроме букв/цифр (пути, точки, снейк, скобки).
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")

# Мини-стоп-лист: частые слова NL-вопросов о коде («что делает», «где вызывается»).
# Держим маленьким — выкидываем только шум, не идентификаторы.
_STOPWORDS = frozenset({
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
    "be", "do", "does", "how", "what", "where", "which", "who", "why", "when",
    "this", "that", "it", "its", "with", "by", "from", "as", "at", "into",
    # русские частые
    "что", "как", "где", "это", "для", "и", "в", "на", "с", "по", "за",
})


def _split_identifier(token: str) -> list[str]:
    """Составной токен → слитная форма + подслова (lowercase, без дублей, порядок сохранён).

    `getUserById → [getuserbyid, get, user, by, id]`; `escape → [escape]`.
    """
    low = token.lower()
    out = [low]
    subs = _CAMEL.findall(token)
    if len(subs) > 1:
        for s in subs:
            sl = s.lower()
            if sl != low and sl not in out:
                out.append(sl)
    return out


def code_tokenize(text: str) -> str:
    """Текст (тело чанка / символ / путь) → поток токенов через пробел для колонки FTS5.

    Пути/точки/снейк — естественные разделители: `src/markupsafe/__init__.py`
    → `src markupsafe init py`. Дубли не убираем (FTS сам считает частоты).
    """
    tokens: list[str] = []
    for raw in _NON_ALNUM.split(text):
        if raw:
            tokens.extend(_split_identifier(raw))
    return " ".join(tokens)


def build_match_query(query: str) -> str:
    """Запрос пользователя → FTS5 MATCH-строка вида `"tok1" OR "tok2" OR ...`.

    OR (не AND): для NL-вопросов AND слишком строг — bm25 сам поднимет чанки с бОльшим
    покрытием, а редкие точные идентификаторы выигрывают за счёт idf. Каждый токен в кавычках —
    нейтрализуем случайные FTS-операторы (AND/OR/NOT/NEAR) в тексте запроса. Стоп-слова выкидываем.
    Пустая строка → нет лексических кандидатов (caller это учитывает).
    """
    seen: list[str] = []
    for raw in _NON_ALNUM.split(query):
        if not raw:
            continue
        for tok in _split_identifier(raw):
            if tok in _STOPWORDS or tok in seen:
                continue
            seen.append(tok)
    return " OR ".join(f'"{tok}"' for tok in seen)
