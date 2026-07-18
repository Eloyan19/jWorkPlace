"""Тесты токенизации кода для лексического канала (FTS5)."""
from app.indexing import lexical


def test_camel_case_split_keeps_joined_and_subwords():
    toks = lexical.code_tokenize("getUserById").split()
    assert toks == ["getuserbyid", "get", "user", "by", "id"]


def test_acronym_boundary():
    toks = lexical.code_tokenize("HTMLParser").split()
    assert toks == ["htmlparser", "html", "parser"]


def test_snake_case_and_path():
    assert lexical.code_tokenize("get_user_by_id").split() == ["get", "user", "by", "id"]
    assert lexical.code_tokenize("src/markupsafe/__init__.py").split() == [
        "src", "markupsafe", "init", "py",
    ]


def test_single_word_no_duplicate():
    assert lexical.code_tokenize("escape").split() == ["escape"]


def test_match_query_drops_stopwords_and_quotes_tokens():
    # «what/is/the/in» — стоп-слова; остаётся идентификатор + значимое слово.
    assert lexical.build_match_query("what is striptags in the module") == '"striptags" OR "module"'


def test_match_query_styles_share_subwords():
    # Запрос в любом стиле даёт пересекающиеся токены → матчит один чанк.
    assert '"get"' in lexical.build_match_query("get_user_by_id")
    assert '"get"' in lexical.build_match_query("getUserById")


def test_match_query_empty_when_all_stopwords():
    assert lexical.build_match_query("what is the") == ""
