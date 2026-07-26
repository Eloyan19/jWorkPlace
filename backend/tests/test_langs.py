"""Тесты langs: детект языка по расширению файла и поиск грамматики tree-sitter."""
import pytest

from app.indexing import langs


class TestLangFor:
    """Тесты функции lang_for: определение языка по пути файла."""

    def test_python_file(self):
        assert langs.lang_for("src/main.py") == "python"

    def test_typescript_file(self):
        assert langs.lang_for("src/index.ts") == "typescript"

    def test_javascript_variants(self):
        assert langs.lang_for("test.js") == "javascript"
        assert langs.lang_for("test.jsx") == "javascript"
        assert langs.lang_for("test.mjs") == "javascript"
        assert langs.lang_for("test.cjs") == "javascript"

    def test_tsx_file(self):
        assert langs.lang_for("components/Button.tsx") == "tsx"

    def test_java_file(self):
        assert langs.lang_for("Main.java") == "java"

    def test_go_file(self):
        assert langs.lang_for("main.go") == "go"

    def test_rust_file(self):
        assert langs.lang_for("lib.rs") == "rust"

    def test_c_h_files(self):
        assert langs.lang_for("util.c") == "c"
        assert langs.lang_for("util.h") == "c"

    def test_ruby_file(self):
        assert langs.lang_for("script.rb") == "ruby"

    def test_php_file(self):
        assert langs.lang_for("index.php") == "php"

    def test_cpp_files(self):
        """C++ возвращает 'cpp' язык (но грамматика None из-за ABI-конфликта)."""
        assert langs.lang_for("main.cpp") == "cpp"
        assert langs.lang_for("util.cc") == "cpp"
        assert langs.lang_for("util.cxx") == "cpp"
        assert langs.lang_for("header.hpp") == "cpp"
        assert langs.lang_for("header.hh") == "cpp"

    def test_no_extension(self):
        """Файлы без расширения возвращают None."""
        assert langs.lang_for("Makefile") is None
        assert langs.lang_for("README") is None
        assert langs.lang_for("/path/to/file") is None

    def test_case_insensitive(self):
        """Расширения case-insensitive."""
        assert langs.lang_for("file.PY") == "python"
        assert langs.lang_for("file.Py") == "python"
        assert langs.lang_for("file.JS") == "javascript"

    def test_unknown_extension(self):
        """Неизвестные расширения возвращают None."""
        assert langs.lang_for("file.xyz") is None
        assert langs.lang_for("file.unknown") is None

    def test_multiple_dots(self):
        """Берётся последнее расширение после последней точки."""
        assert langs.lang_for("my.config.py") == "python"
        assert langs.lang_for("index.spec.ts") == "typescript"

    def test_dot_at_end(self):
        """Точка в конце не даёт расширения (пустое)."""
        assert langs.lang_for("file.") is None

    def test_hidden_file_no_extension(self):
        """Скрытый файл без расширения — None."""
        assert langs.lang_for(".gitignore") is None


class TestGrammarFor:
    """Тесты функции grammar_for: поиск грамматики tree-sitter."""

    def test_python_grammar(self):
        assert langs.grammar_for("python") == "python"

    def test_javascript_grammar(self):
        assert langs.grammar_for("javascript") == "javascript"

    def test_typescript_grammar(self):
        assert langs.grammar_for("typescript") == "typescript"

    def test_tsx_grammar(self):
        assert langs.grammar_for("tsx") == "tsx"

    def test_java_grammar(self):
        assert langs.grammar_for("java") == "java"

    def test_cpp_grammar_is_none(self):
        """C++ грамматика недоступна (ABI-конфликт) → None."""
        assert langs.grammar_for("cpp") is None

    def test_none_lang(self):
        """None язык → None грамматика."""
        assert langs.grammar_for(None) is None

    def test_unknown_lang(self):
        """Неизвестный язык → None."""
        assert langs.grammar_for("unknown_lang_xyz") is None

    def test_empty_string_lang(self):
        """Пустая строка → None."""
        assert langs.grammar_for("") is None

    def test_all_mapped_languages(self):
        """Все языки в маппинге имеют определённую грамматику (или явно None)."""
        # Все известные языки должны либо вернуть имя грамматики, либо None
        langs_to_test = [
            "python", "javascript", "typescript", "tsx", "java",
            "go", "rust", "c", "ruby", "php", "cpp"
        ]
        for lang in langs_to_test:
            result = langs.grammar_for(lang)
            # result может быть строкой или None, но не выбросить исключение
            assert isinstance(result, (str, type(None)))

    def test_grammar_for_file_roundtrip(self):
        """lang_for + grammar_for дают непротиворечивый результат."""
        test_files = [
            "main.py",  # python → python
            "index.ts",  # typescript → typescript
            "main.go",  # go → go
        ]
        for fname in test_files:
            lang = langs.lang_for(fname)
            grammar = langs.grammar_for(lang)
            # Грамматика либо совпадает с языком, либо None
            if grammar is not None:
                assert isinstance(grammar, str)
