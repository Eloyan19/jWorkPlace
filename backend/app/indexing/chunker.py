"""Code-aware чанкинг: tree-sitter по top-level символам + построчный fallback.

Дизайн rag-indexing-engineer. Чанк = top-level символ (функция/класс/метод). Неизвестный язык
или недоступная грамматика (напр. cpp по ABI) → построчный fallback с overlap. Символ длиннее
лимита → режем тело окнами с overlap, сохраняя symbol. Строки с секретами (secret_ranges из
scan.py) пропускаем — они не эмбеддятся и не кэшируются.
"""
from dataclasses import dataclass

from app.indexing.langs import grammar_for

# Узлы верхнего уровня, которые считаем символами (по грамматикам tree-sitter).
_SYMBOL_NODE_TYPES = {
    "function_definition", "function_declaration", "method_definition",
    "class_definition", "class_declaration", "function_item", "impl_item",
    "struct_item", "enum_item", "trait_item", "method_declaration",
    "interface_declaration", "type_alias_declaration", "arrow_function",
    "lexical_declaration", "export_statement", "module", "constructor_declaration",
}
_MAX_CHUNK_LINES = 120          # символ длиннее — режем окнами
_WINDOW_LINES = 60              # fallback-окно
_OVERLAP_LINES = 15


@dataclass
class Chunk:
    file: str
    lang: str | None
    symbol: str | None
    symbol_kind: str | None
    start_line: int   # 1-based, включительно
    end_line: int     # 1-based, включительно
    text: str


def _overlaps_secret(start: int, end: int, secret_ranges: list[tuple[int, int]]) -> bool:
    return any(not (end < s or start > e) for s, e in secret_ranges)


def chunk_file(
    path: str,
    lang: str | None,
    source: str,
    secret_ranges: list[tuple[int, int]] | None = None,
) -> list[Chunk]:
    """Разбить исходник файла на чанки. secret_ranges — диапазоны строк, которые нужно пропустить."""
    secret_ranges = secret_ranges or []
    lines = source.splitlines()
    if not lines:
        return []
    grammar = grammar_for(lang)
    chunks: list[Chunk] = []
    if grammar:
        try:
            chunks = _chunk_treesitter(path, lang, grammar, source, lines)
        except Exception:
            chunks = []  # любой сбой парсера → fallback, индексацию не роняем
    if not chunks:
        chunks = _chunk_lines(path, lang, lines)

    # Отсев чанков, пересекающих строки с секретами.
    return [c for c in chunks if not _overlaps_secret(c.start_line, c.end_line, secret_ranges)]


def _chunk_treesitter(path, lang, grammar, source, lines) -> list[Chunk]:
    from tree_sitter_language_pack import get_parser

    parser = get_parser(grammar)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    chunks: list[Chunk] = []
    covered_end = 0
    for node in tree.root_node.children:
        if node.type not in _SYMBOL_NODE_TYPES:
            continue
        start = node.start_point[0] + 1
        end = node.end_point[0] + 1
        symbol = _node_symbol(node, source_bytes)
        body = "\n".join(lines[start - 1:end])
        if end - start + 1 > _MAX_CHUNK_LINES:
            chunks.extend(_split_window(path, lang, symbol, node.type, lines, start, end))
        else:
            chunks.append(Chunk(path, lang, symbol, node.type, start, end, body))
        covered_end = max(covered_end, end)
    # Файл без распознанных символов (скрипт из выражений) → построчный fallback.
    if not chunks:
        return _chunk_lines(path, lang, lines)
    return chunks


def _node_symbol(node, source_bytes: bytes) -> str | None:
    """Имя символа из дочернего узла-идентификатора. Срез по БАЙТАМ (offset'ы tree-sitter байтовые)."""
    def _slice(n) -> str:
        return source_bytes[n.start_byte:n.end_byte].decode("utf-8", errors="ignore")

    # у большинства грамматик имя доступно через поле "name"
    name = node.child_by_field_name("name") if hasattr(node, "child_by_field_name") else None
    if name is not None:
        return _slice(name)
    for child in node.children:
        if child.type in ("identifier", "name", "type_identifier", "field_identifier",
                          "constant", "property_identifier"):
            return _slice(child)
    return None


def _split_window(path, lang, symbol, kind, lines, start, end) -> list[Chunk]:
    """Крупный символ → окна с overlap, symbol сохраняем."""
    out: list[Chunk] = []
    i = start
    while i <= end:
        w_end = min(i + _WINDOW_LINES - 1, end)
        out.append(Chunk(path, lang, symbol, kind, i, w_end, "\n".join(lines[i - 1:w_end])))
        if w_end >= end:
            break
        i = w_end - _OVERLAP_LINES + 1
    return out


def _chunk_lines(path, lang, lines) -> list[Chunk]:
    """Построчный fallback окнами с overlap (для неизвестных языков / сбоя парсера)."""
    out: list[Chunk] = []
    n = len(lines)
    i = 1
    while i <= n:
        w_end = min(i + _WINDOW_LINES - 1, n)
        out.append(Chunk(path, lang, None, None, i, w_end, "\n".join(lines[i - 1:w_end])))
        if w_end >= n:
            break
        i = w_end - _OVERLAP_LINES + 1
    return out
