"""Карта расширение → язык + грамматика tree-sitter (общая для scan.py и chunker.py).

ВНИМАНИЕ по cpp: в текущей связке tree_sitter 0.23 / language-pack 1.12.5 cpp-грамматика
несовместима по ABI (требует 15, поддержка ≤14). Поэтому cpp здесь помечен tree_sitter=None
— файлы .cpp/.hpp индексируются построчным fallback'ом (chunker), а не по AST.
"""

# ext -> (lang-метка, имя грамматики tree-sitter или None если только line-based fallback)
_EXT_LANG: dict[str, tuple[str, str | None]] = {
    ".py": ("python", "python"),
    ".js": ("javascript", "javascript"),
    ".jsx": ("javascript", "javascript"),
    ".mjs": ("javascript", "javascript"),
    ".cjs": ("javascript", "javascript"),
    ".ts": ("typescript", "typescript"),
    ".tsx": ("tsx", "tsx"),
    ".java": ("java", "java"),
    ".go": ("go", "go"),
    ".rs": ("rust", "rust"),
    ".c": ("c", "c"),
    ".h": ("c", "c"),
    ".rb": ("ruby", "ruby"),
    ".php": ("php", "php"),
    # cpp: грамматика недоступна по ABI → только line-based fallback
    ".cpp": ("cpp", None),
    ".cc": ("cpp", None),
    ".cxx": ("cpp", None),
    ".hpp": ("cpp", None),
    ".hh": ("cpp", None),
}


def lang_for(path: str) -> str | None:
    """Язык по расширению пути (или None, если не распознан — тогда fallback построчно)."""
    dot = path.rfind(".")
    if dot < 0:
        return None
    entry = _EXT_LANG.get(path[dot:].lower())
    return entry[0] if entry else None


def grammar_for(lang: str | None) -> str | None:
    """Имя грамматики tree-sitter для языка (или None → line-based fallback)."""
    if not lang:
        return None
    for _, (lbl, grammar) in _EXT_LANG.items():
        if lbl == lang:
            return grammar
    return None
